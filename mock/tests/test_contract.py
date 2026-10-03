"""Public API contract checks; all worlds here are development fixtures."""
from concurrent.futures import ThreadPoolExecutor
import math

from fastapi.testclient import TestClient
import pytest
from schemas import Result, SessionInfo
from mock.server import create_app, flight


@pytest.fixture
def client():
    with TestClient(create_app(seed=1000, admin_token="test-only")) as client:
        yield client


def start(client, condition="lab"):
    response = client.post("/session", json={"world_id": "mock-dev", "condition": condition})
    assert response.status_code == 200
    return SessionInfo.model_validate(response.json())


def spec(kind="weigh", sample="ref_100"):
    record = {"type": kind, "sample_id": sample}
    return {**record, **({"speed_mps": 3, "elevation_deg": 45} if kind == "launch" else {"height_m": 0.8})}


def prediction(experiment):
    observables = {"weigh": {"force_n": 1}, "drop": {"fall_time_s": 0.4},
                   "launch": {"landing_x_m": 1, "flight_time_s": 0.5}}[experiment["type"]]
    return {"law_id": "L1", "observables": {name: {"mean": value, "sd": 0.1} for name, value in observables.items()}}


def register(client, session, experiment, *, empty=False, followup=None):
    return client.post("/predictions", json={"session_id": session, "spec": experiment,
        "predictions": [] if empty else [prediction(experiment)], "tentative_followup": followup})


def execute(client, session, experiment, table=None):
    return client.post("/experiment", json={"session_id": session, "spec": experiment, "prediction_table_id": table})


def firing_table(session):
    return {"session_id": session, "law_id": "L1", "claim": "insufficient_evidence", "claims_non_ordinary": False,
            "shots": [{"target_id": f"t{index}", "speed_mps": 3, "elevation_deg": 45} for index in range(1, 6)]}


def test_session_metadata_and_unknown_world(client):
    session = start(client)
    assert session.budget == 12 and len(session.samples) == 7 and len(session.targets) == 5
    assert not next(sample for sample in session.samples if sample.sample_id == "mission_300").launchable
    assert session.launcher.z_m == 0.2
    assert client.post("/session", json={"world_id": "unknown", "condition": "lab"}).status_code == 404
    assert client.post("/session", json={"world_id": "mock-dev", "condition": "invalid"}).status_code == 422
    assert SessionInfo.model_validate_json(session.model_dump_json()) == session


@pytest.mark.parametrize("condition", ["lab", "single"])
def test_preregistration_required_and_budget_not_spent_on_rejection(client, condition):
    session = start(client, condition).session_id
    experiment = spec()
    assert execute(client, session, experiment).status_code == 422
    table = register(client, session, experiment, empty=True).json()["prediction_table_id"]
    result = Result.model_validate(execute(client, session, experiment, table).json())
    assert result.index == 1 and result.budget_left == 11 and result.status == "ok"
    assert Result.model_validate_json(result.model_dump_json()) == result
    assert register(client, session, experiment, empty=True).status_code == 422
    assert execute(client, session, experiment, table).status_code == 422


def test_table_cannot_be_reused_mismatched_stale_or_cross_session(client):
    first, second = start(client).session_id, start(client).session_id
    experiment = spec()
    table = register(client, first, experiment).json()["prediction_table_id"]
    other = register(client, second, experiment).json()["prediction_table_id"]
    assert execute(client, second, experiment, table).status_code == 422
    assert execute(client, first, {**experiment, "height_m": 0.9}, table).status_code == 422
    stale = register(client, first, experiment).json()["prediction_table_id"]
    assert execute(client, first, experiment, table).status_code == 200
    assert execute(client, first, experiment, stale).status_code == 422
    assert execute(client, second, experiment, other).status_code == 200


@pytest.mark.parametrize("experiment", [spec("drop", "mission_300"), spec("launch", "mission_300"),
    spec(sample="unknown"), {**spec(), "height_m": -0.1}, {**spec(), "height_m": 1.21},
    {**spec("drop"), "height_m": 0.09}, {**spec("launch"), "speed_mps": 4.1},
    {**spec("launch"), "elevation_deg": 14.9}])
def test_forbidden_and_out_of_range_are_422(client, experiment):
    session = start(client).session_id
    assert register(client, session, experiment).status_code == 422
    assert execute(client, session, experiment).status_code == 422


def test_mission_may_be_weighed(client):
    session = start(client, "random").session_id
    assert execute(client, session, spec(sample="mission_300")).status_code == 200


def test_prediction_observable_and_duplicate_validation(client):
    session = start(client).session_id
    body = {"session_id": session, "spec": spec(), "predictions": [prediction(spec("drop"))]}
    assert client.post("/predictions", json=body).status_code == 422
    body["predictions"] = [prediction(spec())] * 2
    assert client.post("/predictions", json=body).status_code == 422
    body["predictions"] = [prediction(spec())]
    body["predictions"][0]["observables"]["force_n"]["sd"] = -1
    assert client.post("/predictions", json=body).status_code == 422


def test_concurrent_budget_enforced_atomically(client):
    session = start(client, "random").session_id
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: execute(client, session, spec()), range(16)))
    accepted = [response.json() for response in responses if response.status_code == 200]
    assert len(accepted) == 12
    assert sorted(result["index"] for result in accepted) == list(range(1, 13))
    assert sorted(result["budget_left"] for result in accepted) == list(range(12))
    assert sum(response.status_code == 409 for response in responses) == 4


def test_failed_measurement_consumes_budget():
    def fail(*_):
        raise RuntimeError("Fixture instrument failure")
    with TestClient(create_app(seed=1000, measurement=fail)) as client:
        session = start(client, "random").session_id
        result = Result.model_validate(execute(client, session, spec()).json())
        assert result.status == "failed" and result.index == 1 and result.budget_left == 11


def test_seeded_conditions_receive_identical_observations(client):
    first, second = start(client, "random"), start(client, "single")
    assert first.shot_zero == second.shot_zero
    for kind in ("weigh", "drop", "launch"):
        experiment = spec(kind)
        table = register(client, second.session_id, experiment).json()["prediction_table_id"]
        assert execute(client, first.session_id, experiment).json() == execute(client, second.session_id, experiment, table).json()


def test_ballistics_uses_degree_input():
    distance, duration = flight(3, 45)
    vertical = 3 / math.sqrt(2)
    assert duration == pytest.approx((vertical + math.sqrt(vertical ** 2 + 2 * 9.81 * 0.2)) / 9.81)
    assert distance == pytest.approx(vertical * duration)


def test_commit_is_once_five_safe_shots_and_no_score_returned(client):
    session = start(client).session_id
    body = firing_table(session)
    invalid = {**body, "shots": body["shots"][:-1]}
    assert client.post("/commit", json=invalid).status_code == 422
    invalid = {**body, "shots": [{**shot, "speed_mps": 7.01} for shot in body["shots"]]}
    assert client.post("/commit", json=invalid).status_code == 422
    assert client.post("/commit", json=body).json() == {"status": "committed"}
    assert client.post("/commit", json=body).status_code == 409
    assert execute(client, session, spec()).status_code == 409
    assert register(client, session, spec()).status_code == 409


def test_admin_requires_token_and_no_agent_response_has_scores(client):
    session = start(client).session_id
    assert client.get(f"/admin/score/{session}").status_code == 401
    assert client.get("/admin/truth/mock-dev", headers={"Authorization": "Bearer wrong"}).status_code == 401
    headers = {"Authorization": "Bearer test-only"}
    assert client.get("/admin/truth/mock-dev", headers=headers).json()["mock"] is True
    assert client.get("/admin/truth/unknown", headers=headers).status_code == 404
    client.post("/commit", json=firing_table(session))
    assert len(client.get(f"/admin/score/{session}", headers=headers).json()["mission"]) == 5


def test_unknown_session_and_admin_disabled_without_token(monkeypatch):
    monkeypatch.delenv("SOLZERO_ADMIN_TOKEN", raising=False)
    with TestClient(create_app(seed=1000)) as client:
        assert execute(client, "unknown", spec()).status_code == 404
        assert client.get("/admin/truth/mock-dev", headers={"Authorization": "Bearer anything"}).status_code == 401


def test_nomination_checks_fit_and_never_returns_hidden_scores(client):
    session = start(client, "random").session_id
    execute(client, session, spec())
    body = {"session_id": session,
            "law": {"law_id": "L1", "ax": "0", "az": "-accel",
                    "params": {"accel": {"init": 9.81, "lo": 1, "hi": 20}}},
            "fit": {"law_id": "L1", "params": {"accel": {"value": 9.81, "sd": 0.1}},
                    "chi2_dof": 1, "loo_error": None, "n_experiments": 1, "converged": True}}
    assert client.post("/nominate", json=body).json() == {"ok": True}
    body["fit"]["law_id"] = "wrong"
    assert client.post("/nominate", json=body).status_code == 422
    body["fit"]["law_id"] = "L1"
    body["fit"]["n_experiments"] = 0
    assert client.post("/nominate", json=body).status_code == 422
    score = client.get(f"/admin/score/{session}", headers={"Authorization": "Bearer test-only"}).json()
    assert len(score["nominations"]) == 1 and len(score["probe_error"]) == 1


def test_proposing_law_invalidates_an_older_empty_table(client):
    session = start(client).session_id
    old = register(client, session, spec(), empty=True).json()["prediction_table_id"]
    new = register(client, session, spec()).json()["prediction_table_id"]
    assert execute(client, session, spec(), old).status_code == 422
    assert execute(client, session, spec(), new).status_code == 200
