"""Round-trip tests for schemas/, using the examples in SPEC.md section 5.3."""

import pytest
from pydantic import ValidationError

import schemas as S

SPEC_EXAMPLES = {
    "weigh": {"type": "weigh", "sample_id": "ref_100", "height_m": 0.8},
    "drop": {"type": "drop", "sample_id": "ref_400", "height_m": 1.0},
    "launch": {"type": "launch", "sample_id": "ref_050", "speed_mps": 3.5, "elevation_deg": 40.0},
}

LAW = {
    "law_id": "L3",
    "description": "quadratic drag, gravity scales with mass",
    "ax": "-c*speed*vx/m",
    "az": "-g0*(m/0.1)**alpha - c*speed*vz/m",
    "params": {
        "g0": {"init": 9.8, "lo": 1, "hi": 20},
        "alpha": {"init": 0.0, "lo": -1, "hi": 1},
        "c": {"init": 0.01, "lo": 0, "hi": 1},
    },
}

FIT = {
    "law_id": "L3",
    "params": {"g0": {"value": 6.1, "sd": 0.2}},
    "chi2_dof": 1.1,
    "loo_error": 0.031,
    "n_experiments": 7,
    "converged": True,
    "cov": [[0.04]],
}

RECORDS = [
    (S.Result, {
        "experiment_id": "e07", "index": 7, "spec": SPEC_EXAMPLES["launch"],
        "observables": {"landing_x_m": 1.92, "flight_time_s": 0.63},
        "noise_sd": {"landing_x_m": 0.01, "flight_time_s": 0.005},
        "status": "ok", "budget_left": 5,
    }),
    (S.SessionInfo, {
        "session_id": "s_ab12", "budget": 12,
        "samples": [{"sample_id": "ref_020", "mass_kg": 0.02, "launchable": True},
                    {"sample_id": "mission_300", "mass_kg": 0.3, "launchable": False}],
        "ranges": {"weigh": {"height_m": [0, 1.2]}, "drop": {"height_m": [0.1, 1.2]},
                   "launch": {"speed_mps": [1, 4], "elevation_deg": [15, 75]},
                   "mission": {"speed_mps": [1, 7], "elevation_deg": [15, 75]}},
        "noise_sd": {"force_frac": 0.02, "fall_time_s": 0.005, "landing_x_m": 0.01,
                     "flight_time_s": 0.005},
        "launcher": {"x_m": 0.0, "z_m": 0.2},
        "targets": [{"target_id": "t1", "x_m": 1.4, "z_m": 0.0}],
        "shot_zero": {"spec": SPEC_EXAMPLES["launch"], "target_id": "t0",
                      "landing_x_m": 1.1, "miss_m": 0.52},
    }),
    (S.Law, LAW),
    (S.FitResult, FIT),
    (S.Prediction, {"law_id": "L3", "observables": {
        "landing_x_m": {"mean": 1.90, "sd": 0.04}, "flight_time_s": {"mean": 0.62, "sd": 0.01}}}),
    (S.Disagreement, {"spec": SPEC_EXAMPLES["drop"],
                      "pairs": [{"a": "L1", "b": "L3", "gap_sigma": 4.2}], "max_gap_sigma": 4.2}),
    (S.ShotPlan, {"law_id": "L3", "target_id": "t4", "sample_id": "mission_300",
                  "speed_mps": 5.6, "elevation_deg": 38.0, "predicted_miss_sd_m": 0.03,
                  "reachable": True}),
    (S.Verdict, {"law_id": "L1", "experiment_id": "e07", "z": 3.4, "verdict": "rejected",
                 "note": "over-predicts range for light samples"}),
    (S.Commit, {"law_id": "L3", "claim": "law_identified", "claims_non_ordinary": True,
                "shots": [{"target_id": "t1", "speed_mps": 3.1, "elevation_deg": 42.0}]}),
    (S.LedgerEntry, {"ts": "2026-10-04T03:12:09Z", "cycle": 4, "agent": "experimentalist",
                     "kind": "decision_diff", "payload": {"changed": True}}),
    (S.DecisionDiff, {"tentative": SPEC_EXAMPLES["drop"], "actual": SPEC_EXAMPLES["weigh"],
                      "changed": True, "reason": "plan changed by evidence"}),
    (S.SessionRequest, {"world_id": "w1000", "condition": "lab"}),
    (S.PredictionsRequest, {"session_id": "s1", "spec": SPEC_EXAMPLES["weigh"],
                            "predictions": [], "tentative_followup": SPEC_EXAMPLES["drop"]}),
    (S.ExperimentRequest, {"session_id": "s1", "spec": SPEC_EXAMPLES["weigh"],
                           "prediction_table_id": "p1"}),
    (S.NominateRequest, {"session_id": "s1", "law": LAW, "fit": FIT}),
    (S.CommitRequest, {"session_id": "s1", "law_id": "L3", "claim": "insufficient_evidence",
                       "claims_non_ordinary": False, "shots": []}),
]


@pytest.mark.parametrize("model,data", RECORDS, ids=[m.__name__ for m, _ in RECORDS])
def test_round_trip(model, data):
    obj = model.model_validate(data)
    again = model.model_validate_json(obj.model_dump_json())
    assert again == obj


@pytest.mark.parametrize("kind", list(SPEC_EXAMPLES))
def test_spec_discriminator(kind):
    spec = S.parse_spec(SPEC_EXAMPLES[kind])
    assert spec.type == kind
    assert S.parse_spec(spec.model_dump_json()) == spec
    assert spec.model_dump() == SPEC_EXAMPLES[kind]


def test_spec_rejects_extra_and_wrong_type():
    with pytest.raises(ValidationError):
        S.parse_spec({"type": "weigh", "sample_id": "ref_100", "height_m": 0.8, "speed_mps": 1})
    with pytest.raises(ValidationError):
        S.parse_spec({"type": "throw", "sample_id": "ref_100"})


def test_law_rejects_unknown_names():
    bad = dict(LAW, az="-g0 - k*z")
    with pytest.raises(ValidationError, match="unknown names"):
        S.Law.model_validate(bad)
    with pytest.raises(ValidationError):
        S.Law.model_validate(dict(LAW, params={"m": {"init": 1, "lo": 0, "hi": 2}}))
    with pytest.raises(ValidationError):
        S.Law.model_validate(dict(LAW, params={"g0": {"init": 30, "lo": 1, "hi": 20}}))


def test_law_allows_sympy_reserved_param_names():
    # Names such as beta, gamma or E are sympy builtins; they must still work as parameters.
    law = S.Law(law_id="L", ax="0", az="-beta*E - gamma*speed*vz",
                params={n: {"init": 1, "lo": 0, "hi": 2} for n in ("beta", "gamma", "E")})
    assert set(law.params) == {"beta", "gamma", "E"}


def test_verdict_and_claim_values():
    with pytest.raises(ValidationError):
        S.Verdict(law_id="L", experiment_id="e1", z=0, verdict="maybe")
    with pytest.raises(ValidationError):
        S.Commit(law_id="L", claim="certain", claims_non_ordinary=False, shots=[])
