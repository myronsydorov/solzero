"""viewer.import_eval: eval/run.py output -> SPEC 5.7 viewer layout."""

import json
import subprocess
import sys

from schemas import SessionInfo


def _eval_tree(root, session_info):
    attempt = root / "lab" / "1000" / "attempt1"
    (attempt / "agent").mkdir(parents=True)
    (root / "lab" / "1000" / "state.json").write_text(json.dumps(
        {"status": "done", "attempt_dir": "attempt1", "agent_status": "committed"}))
    (attempt / "session_info.json").write_text(session_info)
    (attempt / "admin_score.json").write_text(json.dumps({"session_id": "s_x", "nominations": [], "commit": None}))
    (attempt / "truth.json").write_text(json.dumps({"family": "F2", "p": 2.0, "g0": 7.0, "alpha": 0.3,
                                                    "kappa": 0.0, "c": 0.02, "rho": 0.3}))
    (attempt / "agent" / "ledger.jsonl").write_text(json.dumps(
        {"ts": "2026-10-04T00:00:00Z", "cycle": 0, "agent": "theorist", "kind": "law_set",
         "payload": {"laws": []}}) + "\n")
    row = {"label": "lab", "seed": 1000, "family": "F2", "condition": "lab",
           "probe_beyond_curve": [None] + [0.5] * 12, "probe_median_error_curve_m": [None] + [0.01] * 12,
           "law_form_recovered": True, "claim": "law_identified", "claims_non_ordinary": True,
           "mission_hit_rate": 0.8, "mission_hit_in_range": 1.0, "mission_hit_beyond": 2 / 3,
           "mission_median_miss_frac": {"in_range": 0.004, "beyond": float("inf")},
           "run_status": "done", "flags": []}
    (root / "all_metrics.jsonl").write_text(json.dumps(row).replace("Infinity", "1e999") + "\n")


def test_import_eval(tmp_path):
    from world.generator import make_world  # noqa: F401  (ensures dev-world SessionInfo fields exist)
    from tools.defaults import LAUNCHER, NOISE_SD, RANGES, SAMPLES

    w = make_world(1000)
    info = SessionInfo(session_id="s_x", budget=12, samples=SAMPLES, ranges={k: dict(v) for k, v in RANGES.items()},
                       noise_sd=dict(NOISE_SD), launcher=LAUNCHER, targets=w.targets, shot_zero=w.shot_zero)
    src, out = tmp_path / "eval", tmp_path / "site"
    _eval_tree(src, info.model_dump_json())
    subprocess.run([sys.executable, "-m", "viewer.import_eval", str(src), "--out", str(out), "--title", "t"],
                   check=True, capture_output=True)

    agg = json.loads((out / "eval" / "aggregate.json").read_text())
    assert agg["budget"] == 12
    (r,) = agg["rows"]
    assert r["condition"] == "lab" and r["world"] == "1000" and len(r["within_beyond"]) == 13
    assert (r["mission_hits"], r["mission_hits_in_range"], r["mission_hits_beyond"]) == (4, 2, 2)
    assert r["median_miss_frac_beyond"] is None  # non-finite becomes null

    run = out / "runs" / "lab_1000"
    m = json.loads((run / "metrics.json").read_text())
    SessionInfo.model_validate(m["session_info"])
    assert m["truth"]["family"] == "F2" and m["condition"] == "lab"
    assert (run / "ledger.jsonl").read_text().strip()
    index = json.loads((out / "runs" / "index.json").read_text())
    assert [x["run_id"] for x in index["runs"]] == ["lab_1000"]
