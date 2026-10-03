"""Convert calibration stage B results into the SPEC 5.6 aggregate.json the viewer reads.

    .venv/bin/python -m viewer.fixtures.calibration_aggregate calibration/results/dev20_v2 --out viewer/public/eval/aggregate.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

CONDITION = {"greedy": "oracle", "random": "random"}


def _num(x):
    """None for values that never landed (inf or NaN)."""
    return x if x is not None and math.isfinite(x) else None


def row(d: dict) -> dict:
    steps = sorted(d["steps"], key=lambda s: s["step"])
    shots = d["mission_selected"]
    return {
        "world": str(d["seed"]), "family": d["truth"]["family"], "condition": CONDITION[d["policy"]],
        "within_beyond": [_num(s["probe_selected"]["within_beyond"]) for s in steps],
        "median_error_m": [_num(s["probe_selected"]["median_all"]) for s in steps],
        # Same definition as calibration/report.py: the selected form equals the true form.
        "law_recovered": d["final_form"] == d["true_form"],
        "claim": "law_identified", "claims_non_ordinary": bool(d["claims_non_ordinary"]),
        "mission_hits": sum(bool(s["realized_hit"]) for s in shots),
        "mission_hits_beyond": sum(bool(s["realized_hit"]) for s in shots if s["kind"] == "beyond"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path)
    ap.add_argument("--out", type=Path, default=Path("viewer/public/eval/aggregate.json"))
    args = ap.parse_args()
    runs = [json.loads(p.read_text()) for p in sorted(args.results.glob("stageB_*_*.json"))]
    rows = sorted((row(d) for d in runs), key=lambda r: (r["condition"], int(r["world"])))
    budget = len(runs[0]["steps"]) - 1
    agg = {
        "label": "Fixture: calibration stage B on dev worlds 1000-1019 (scripted policies, no language model)",
        "source": str(args.results), "budget": budget,
        "notes": {
            "oracle": "Scripted greedy-disagreement policy (calibration stage B), stands in for the oracle reference",
            "random": "Scripted uniform-random experiments with library fitting (calibration stage B), "
                      "not the agent random condition",
        },
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(agg, indent=1, allow_nan=False))
    print(f"{len(rows)} rows -> {args.out}")


if __name__ == "__main__":
    main()
