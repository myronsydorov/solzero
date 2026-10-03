"""Viewer inputs (SPEC 5.5 and 5.6): every run under viewer/public/runs and the aggregate
file parse with the shared schemas, and every prediction table covers the live law set."""

import json
from pathlib import Path

import pytest

from schemas import (CommitRequest, DecisionDiff, Disagreement, Law, LedgerEntry, Prediction,
                     Result, SessionInfo, Verdict, parse_spec)

PUBLIC = Path(__file__).resolve().parents[1] / "viewer" / "public"
RUNS = sorted(p for p in (PUBLIC / "runs").glob("*") if (p / "ledger.jsonl").exists())


def _prediction(d: dict) -> Prediction:
    # A null mean is allowed in the JSON ledger: the law predicts the sample never lands.
    obs = {k: {"mean": float("nan") if v["mean"] is None else v["mean"],
               "sd": float("nan") if v["sd"] is None else v["sd"]} for k, v in d["observables"].items()}
    return Prediction.model_validate({**d, "observables": obs})


@pytest.mark.skipif(not RUNS, reason="no viewer runs present")
@pytest.mark.parametrize("run", RUNS, ids=lambda p: p.name)
def test_run_files_parse(run: Path):
    live: set[str] = set()
    kinds = []
    for line in (run / "ledger.jsonl").read_text().splitlines():
        e = LedgerEntry.model_validate_json(line)
        p = e.payload
        kinds.append(e.kind)
        if e.kind == "law_set":
            live = {Law.model_validate(x).law_id for x in p["laws"]}
        elif e.kind == "candidates":
            specs = [parse_spec(s) for s in p["candidates"]]
            assert parse_spec(p["chosen"]) in specs
            if "disagreements" in p:
                assert len(p["disagreements"]) == len(specs)
                for d in p["disagreements"]:
                    Disagreement.model_validate(d)
        elif e.kind == "decision_diff":
            DecisionDiff.model_validate(p)
        elif e.kind == "prediction_table":
            preds = [_prediction(x) for x in p["predictions"]]
            assert {x.law_id for x in preds} == live
        elif e.kind == "result":
            # A failed run carries null observables in JSON (NaN is not valid JSON).
            obs = {k: float("nan") if v is None else v for k, v in p["observables"].items()}
            r = Result.model_validate({**p, "observables": obs})
            assert r.status == "failed" or None not in p["observables"].values()
        elif e.kind == "verdicts":
            for v in p["verdicts"]:
                Verdict.model_validate(v)
        elif e.kind == "nomination":
            Law.model_validate(p["law"])
        elif e.kind == "commit":
            CommitRequest.model_validate(p)
    assert "result" in kinds
    m = json.loads((run / "metrics.json").read_text())
    SessionInfo.model_validate(m["session_info"])
    assert {"run_id", "condition", "score", "truth"} <= set(m)
    assert "family" in m["truth"]


@pytest.mark.skipif(not (PUBLIC / "eval" / "aggregate.json").exists(), reason="no aggregate present")
def test_aggregate_rows():
    agg = json.loads((PUBLIC / "eval" / "aggregate.json").read_text())
    assert agg["rows"]
    for r in agg["rows"]:
        assert r["condition"] in {"lab", "random", "single", "textbook", "oracle"}
        assert len(r["within_beyond"]) == agg["budget"] + 1
        assert len(r["median_error_m"]) == agg["budget"] + 1
