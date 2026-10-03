"""Checks of the lane-B adapters against the public HTTP contract."""
from concurrent.futures import ThreadPoolExecutor
import json
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest
from schemas import Commit, FitResult, Law, LedgerEntry, Prediction, parse_spec

from lab.client import WorldClient
from lab.ledger import Ledger
from lab.policies import role_policy
from lab.session import LabSession
from lab import tool_functions as functions
from mock.server import create_app


@pytest.fixture
def environment(tmp_path):
    with TestClient(create_app(seed=1000)) as api:
        calls = []
        def dispatch(request):
            calls.append((request.method, request.url.path))
            response = api.request(request.method, request.url.path, content=request.content,
                                   headers={"content-type": "application/json"})
            return httpx.Response(response.status_code, content=response.content, headers=response.headers)
        with WorldClient("http://fixture", transport=httpx.MockTransport(dispatch)) as client:
            session = LabSession(client, "mock-dev", runs_root=tmp_path)
            functions.configure(session, seed=1000)
            yield session, calls, api


def experiment(height=0.8):
    return parse_spec({"type": "weigh", "sample_id": "ref_100", "height_m": height})


def law(identifier="L1"):
    return Law(law_id=identifier, ax="0", az="-accel", params={"accel": {"init": 10, "lo": 1, "hi": 20}})


def fit(identifier="L1", count=1):
    return FitResult(law_id=identifier, params={"accel": {"value": 9.81, "sd": 0.1}},
                     chi2_dof=1, loo_error=0.1, n_experiments=count, converged=True)


def forecast(identifier="L1"):
    return Prediction(law_id=identifier, observables={"force_n": {"mean": 1, "sd": 0.02}})


def mission():
    return Commit(law_id="L1", claim="insufficient_evidence", claims_non_ordinary=False,
                  shots=[{"target_id": f"t{index}", "speed_mps": 3, "elevation_deg": 45} for index in range(1, 6)])


def entries(session):
    return [LedgerEntry.model_validate_json(line) for line in session.ledger.path.read_text().splitlines()]


def test_session_flow_logs_predictions_result_nomination_and_decision_diff(environment):
    session, calls, _ = environment
    first, tentative, actual = experiment(), experiment(0.9), experiment(1.0)
    session.preregister(first, [], tentative)
    assert functions.weigh("ref_100", 0.8)["budget_left"] == 11
    session.set_laws([law()])
    session.nominate(law(), fit())
    session.preregister(actual, [forecast()], None, "Observed result changed the next choice")
    session.execute(actual)
    records = entries(session)
    diffs = [record.payload for record in records if record.kind == "decision_diff"]
    assert [record["changed"] for record in diffs] == [False, True]
    assert diffs[1]["tentative"] == tentative.model_dump() and diffs[1]["actual"] == actual.model_dump()
    assert [record.kind for record in records].count("result") == 2
    assert calls == [("POST", endpoint) for endpoint in
                     ("/session", "/predictions", "/experiment", "/laws", "/nominate", "/predictions", "/experiment")]


def test_full_live_set_coverage_and_revision_invalidates_pending(environment):
    session, calls, _ = environment
    session.set_laws([law(), law("L2")])
    for predictions in ([], [forecast()], [forecast(), forecast()]):
        with pytest.raises(ValueError):
            session.preregister(experiment(), predictions, None)
    assert calls == [("POST", "/session"), ("POST", "/laws")]
    session.preregister(experiment(), [forecast(), forecast("L2")], None)
    session.set_laws([law()])
    with pytest.raises(ValueError):
        session.execute(experiment())
    session.preregister(experiment(), [forecast()], None)
    session.execute(experiment())


def test_mismatched_spec_has_no_http_side_effect(environment):
    session, calls, _ = environment
    session.preregister(experiment(), [], None)
    previous = len(calls)
    with pytest.raises(ValueError):
        session.execute(experiment(0.9))
    assert len(calls) == previous


def test_policies_deny_role_and_budget_and_ask_for_valid_mission(environment):
    session, _, _ = environment
    event = {"type": "tool_call", "target": "weigh", "data": {"arguments": {"sample_id": "ref_100", "height_m": 0.8}}}
    assert role_policy("theorist")(event)["result"] == "DENY"
    assert role_policy("operator")(event)["result"] == "ALLOW"
    session.budget_left = 0
    assert role_policy("operator")(event)["result"] == "DENY"
    commit = {"type": "tool_call", "target": "commit_mission", "data": {"arguments": {"commit": mission().model_dump()}}}
    assert role_policy("pi")(commit)["result"] == "ASK"
    commit["data"]["arguments"]["commit"]["shots"][0]["speed_mps"] = 8
    assert role_policy("pi")(commit)["result"] == "DENY"


def test_commit_requires_exact_table_approval_and_logs_only_success(environment):
    session, calls, _ = environment
    with pytest.raises(PermissionError):
        session.commit(mission())
    assert calls == [("POST", "/session")]
    reviewed = []
    session._approve_commit = lambda record: reviewed.append(record.model_dump()) or True
    assert session.commit(mission()).status == "committed"
    assert reviewed == [mission().model_dump()]
    assert entries(session)[-1].kind == "commit"
    with pytest.raises(RuntimeError):
        session.commit(mission())
    assert calls.count(("POST", "/commit")) == 1


def test_network_ambiguity_blocks_retry(environment):
    session, _, _ = environment
    session.preregister(experiment(), [], None)
    def timeout(_):
        raise httpx.ReadTimeout("response lost")
    session.client.experiment = timeout
    with pytest.raises(httpx.ReadTimeout):
        session.execute(experiment())
    assert session.uncertain
    with pytest.raises(RuntimeError, match="unknown outcome"):
        session.execute(experiment())


def test_random_condition_uses_same_robot_wrapper_without_preregistration(environment, tmp_path):
    session, _, _ = environment
    other = LabSession(session.client, "mock-dev", "random", runs_root=tmp_path)
    functions.configure(other, seed=1000)
    assert functions.weigh("ref_100", 0.8)["index"] == 1


def test_failed_result_is_logged_and_budget_mirrored(environment):
    session, _, _ = environment
    from schemas import Result
    session.preregister(experiment(), [], None)
    session.client.experiment = lambda _: Result(experiment_id="e01", index=1, spec=experiment(),
        observables={}, noise_sd={}, status="failed", budget_left=11)
    session.execute(experiment())
    assert session.budget_left == 11 and entries(session)[-1].payload["status"] == "failed"


def test_nomination_rejects_nonlive_law(environment):
    session, calls, _ = environment
    with pytest.raises(ValueError):
        session.nominate(law(), fit())
    assert calls == [("POST", "/session")]


def test_ledger_concurrent_append_roundtrip_and_validation(tmp_path):
    ledger = Ledger("s_test", tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: ledger.append(index, "analyst", "verdicts", {"number": index}), range(32)))
    records = [LedgerEntry.model_validate_json(line) for line in ledger.path.read_text().splitlines()]
    assert len(records) == 32 and {entry.cycle for entry in records} == set(range(32))
    assert all(record.ts.endswith("Z") for record in records)
    with pytest.raises(ValueError):
        Ledger("../escape", tmp_path)
    with pytest.raises(ValueError):
        ledger.append(0, "pi", "commit", {"bad": float("nan")})
    assert len(ledger.path.read_text().splitlines()) == 32


def test_analysis_tools_delegate_to_shared_implementation_with_explicit_seed(environment, monkeypatch):
    session, _, _ = environment
    calls = []
    def capture(name, result):
        def invoke(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result
        return invoke
    from schemas import Disagreement, ShotPlan
    backend = SimpleNamespace(
        fit_law=capture("fit", fit()), predict=capture("predict", forecast()),
        disagreement=capture("disagreement", Disagreement(spec=experiment(), pairs=[], max_gap_sigma=0)),
        plan_shot=capture("plan", ShotPlan(law_id="L1", target_id="t1", sample_id="mission_300",
                                           speed_mps=3, elevation_deg=45, predicted_miss_sd_m=0.02, reachable=True)))
    monkeypatch.setitem(sys.modules, "tools", backend)
    raw_law, raw_fit = law().model_dump(), fit().model_dump()
    functions.fit_law(raw_law)
    functions.predict(raw_law, raw_fit, experiment().model_dump())
    functions.disagreement(experiment().model_dump(), [{"law": raw_law, "fit": raw_fit}])
    functions.plan_shot(raw_law, raw_fit, "t1")
    assert [name for name, _, _ in calls] == ["fit", "predict", "disagreement", "plan"]
    assert all(kwargs["seed"] == 1000 for _, _, kwargs in calls)
    assert calls[0][1][1] is session.results and calls[0][1][2] == session.info.samples


@pytest.mark.parametrize("expression", ["__import__('os').system('whoami')", "m.__class__", "[m][0]", "sum([1])", "unknown", "'text'"])
def test_law_expression_gate_runs_before_shared_parser(expression):
    record = law().model_dump()
    record["az"] = expression
    with pytest.raises((ValueError, SyntaxError)):
        functions.validated_law(record)


def test_client_only_accepts_service_origins_and_does_not_follow_redirects(monkeypatch):
    monkeypatch.delenv("SOLZERO_WORLD_URL", raising=False)
    for address in (None, "file:///etc/passwd", "http://example/path", "http://example?query=1"):
        with pytest.raises(ValueError):
            WorldClient(address)
    requests = []
    def redirect(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "http://different-service/session"})
    with WorldClient("http://fixture", transport=httpx.MockTransport(redirect)) as client:
        from schemas import SessionRequest
        with pytest.raises(httpx.HTTPStatusError):
            client.start(SessionRequest(world_id="mock-dev", condition="lab"))
    assert len(requests) == 1


def test_stub_yamls_limit_robot_tools_to_operator():
    from pathlib import Path
    import yaml
    for role in ("pi", "theorist", "experimentalist", "operator", "analyst"):
        config = yaml.safe_load((Path(__file__).resolve().parents[2] / "lab/agents" / f"{role}.yaml").read_text())
        assert "stub" in config["prompt"] and config["skills"] == "none" and "os_env" not in config
        assert bool({"weigh", "drop", "launch"} & set(config["tools"])) == (role == "operator")
