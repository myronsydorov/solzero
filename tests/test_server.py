"""Endpoint behaviour of the world server (SPEC.md section 5.2)."""

import pytest
from fastapi.testclient import TestClient

from schemas import Law, Result, SessionInfo
from world.generator import fixed_law
from world.server import BUDGET, create_app, world_id_for

TOKEN = "test-token"
WORLD = world_id_for(1000)
WEIGH = {"type": "weigh", "sample_id": "ref_100", "height_m": 0.5}
LAUNCH = {"type": "launch", "sample_id": "ref_200", "speed_mps": 3.0, "elevation_deg": 40.0}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLZERO_ADMIN_TOKEN", TOKEN)
    return TestClient(create_app(seeds=[1000, 1001], runs_dir=tmp_path))


def law_and_fit(law_id="L1"):
    _, fit = fixed_law(law_id, "-c*speed*vx/m", "-g0 - c*speed*vz/m", {"g0": 9.81, "c": 0.01})
    law = Law(law_id=law_id, ax="-c*speed*vx/m", az="-g0 - c*speed*vz/m",
              params={"g0": {"init": 9.81, "lo": 1, "hi": 20}, "c": {"init": 0.01, "lo": 0, "hi": 1}})
    return law.model_dump(), fit.model_dump()


def new_session(client, condition="lab"):
    r = client.post("/session", json={"world_id": WORLD, "condition": condition})
    assert r.status_code == 200, r.text
    return SessionInfo.model_validate(r.json())


def predict(client, sid, spec, preds=()):
    r = client.post("/predictions", json={"session_id": sid, "spec": spec, "predictions": list(preds)})
    return r


def run(client, sid, spec, table=None):
    return client.post("/experiment", json={"session_id": sid, "spec": spec, "prediction_table_id": table})


def test_session_info_hides_the_world(client):
    info = new_session(client)
    assert info.budget == BUDGET and len(info.targets) == 5
    assert all(t.hit_radius_m >= 0.05 for t in info.targets)
    text = client.post("/session", json={"world_id": WORLD, "condition": "lab"}).text
    for word in ("family", "kappa", "alpha", "rho", "1000"):
        assert word not in text
    assert client.post("/session", json={"world_id": "w_nope", "condition": "lab"}).status_code == 404
    assert client.post("/session", json={"world_id": world_id_for(9000), "condition": "lab"}).status_code == 404


def test_preregistration_flow(client):
    sid = new_session(client).session_id
    # Cycle 0: no live laws, empty prediction table is allowed.
    assert run(client, sid, WEIGH).status_code == 422  # lab needs a table
    t0 = predict(client, sid, WEIGH).json()["prediction_table_id"]
    assert run(client, sid, LAUNCH, t0).status_code == 422  # spec mismatch
    r = run(client, sid, WEIGH, t0)
    assert r.status_code == 200
    res = Result.model_validate(r.json())
    assert res.index == 1 and res.budget_left == BUDGET - 1 and "force_n" in res.observables
    assert run(client, sid, WEIGH, t0).status_code == 422  # table already used

    law, fit = law_and_fit("L1")
    assert client.post("/laws", json={"session_id": sid, "live_laws": [law]}).status_code == 200
    pred = {"law_id": "L1", "observables": {"force_n": {"mean": 1.0, "sd": 0.02}}}
    other = {"law_id": "L2", "observables": {"force_n": {"mean": 1.0, "sd": 0.02}}}
    assert predict(client, sid, WEIGH).status_code == 422  # must cover the live set
    assert predict(client, sid, WEIGH, [pred, other]).status_code == 422  # and nothing else
    t1 = predict(client, sid, WEIGH, [pred]).json()["prediction_table_id"]
    # Changing the live set invalidates the table.
    law2, _ = law_and_fit("L2")
    client.post("/laws", json={"session_id": sid, "live_laws": [law, law2]})
    assert run(client, sid, WEIGH, t1).status_code == 422
    t2 = predict(client, sid, WEIGH, [pred, other]).json()["prediction_table_id"]
    assert run(client, sid, WEIGH, t2).status_code == 200


def test_ranges_samples_and_budget(client):
    sid = new_session(client, "random").session_id
    assert run(client, sid, {"type": "drop", "sample_id": "mission_300", "height_m": 1.0}).status_code == 422
    assert run(client, sid, {"type": "launch", "sample_id": "mission_300", "speed_mps": 2,
                             "elevation_deg": 30}).status_code == 422
    assert run(client, sid, {**LAUNCH, "speed_mps": 5.0}).status_code == 422
    assert run(client, sid, {"type": "drop", "sample_id": "ref_050", "height_m": 0.05}).status_code == 422
    assert run(client, sid, {"type": "weigh", "sample_id": "mission_300", "height_m": 0.3}).status_code == 200
    for _ in range(BUDGET - 1):
        assert run(client, sid, LAUNCH).status_code == 200
    assert run(client, sid, LAUNCH).status_code == 409


def test_nominate_commit_and_admin(client):
    info = new_session(client, "random")
    sid = info.session_id
    run(client, sid, LAUNCH)
    law, fit = law_and_fit()
    assert client.post("/nominate", json={"session_id": sid, "law": law, "fit": fit}).json() == {"ok": True}
    bad_fit = dict(fit, law_id="other")
    assert client.post("/nominate", json={"session_id": sid, "law": law, "fit": bad_fit}).status_code == 422
    shots = [{"target_id": t.target_id, "speed_mps": 5.0, "elevation_deg": 45.0} for t in info.targets]
    body = {"session_id": sid, "law_id": "L1", "shots": shots, "claim": "predictive_only",
            "claims_non_ordinary": False}
    assert client.post("/commit", json={**body, "shots": [dict(shots[0], speed_mps=9.0)]}).status_code == 422
    assert client.post("/commit", json={**body, "shots": shots[:1] * 2}).status_code == 422
    r = client.post("/commit", json=body)
    assert r.json() == {"status": "committed"}  # no hit or miss information
    assert client.post("/commit", json=body).status_code == 409
    assert run(client, sid, LAUNCH).status_code == 409

    assert client.get(f"/admin/score/{sid}").status_code == 403
    score = client.get(f"/admin/score/{sid}", headers={"X-Admin-Token": TOKEN}).json()
    assert score["used"] == 1 and len(score["commit"]["shots"]) == 5
    assert 0 <= score["nominations"][0]["probe_within_beyond"] <= 1
    truth = client.get(f"/admin/truth/{WORLD}", headers={"X-Admin-Token": TOKEN}).json()
    assert truth["family"] in {"F0", "F1", "F2", "F3"}
