"""Convert eval/run.py output into the viewer's SPEC 5.7 layout.

    .venv/bin/python -m viewer.import_eval runs/eval --out viewer/public --title "Dev worlds, agents frozen at <tag>"

Reads <root>/<label>/<seed>/<attempt>/{session_info.json, admin_score.json, truth.json, agent/ledger.jsonl,
agent/video.mp4?} (attempt from state.json) and <root>/all_metrics.jsonl. Writes
<out>/runs/<label>_<seed>/{ledger.jsonl, metrics.json, video.mp4?}, <out>/eval/aggregate.json and
<out>/runs/index.json (via the Node script when available).
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

LABELS = ("lab", "random", "single", "textbook", "oracle", "random-scripted")
N_IN_RANGE, N_BEYOND = 2, 3  # SPEC 3: five targets per world


def _count(rate, n):
    return None if rate is None else int(round(rate * n))


def aggregate_row(m: dict) -> dict:
    """One all_metrics.jsonl row -> one aggregate row."""
    miss = m.get("mission_median_miss_frac") or {}
    return {
        "world": str(m["seed"]), "family": m["family"], "condition": m["label"],
        "within_beyond": m.get("probe_beyond_curve") or [],
        "median_error_m": m.get("probe_median_error_curve_m") or [],
        "law_recovered": bool(m.get("law_form_recovered")),
        "claim": m.get("claim"), "claims_non_ordinary": bool(m.get("claims_non_ordinary")),
        "mission_hits": _count(m.get("mission_hit_rate"), N_IN_RANGE + N_BEYOND),
        "mission_hits_in_range": _count(m.get("mission_hit_in_range"), N_IN_RANGE),
        "mission_hits_beyond": _count(m.get("mission_hit_beyond"), N_BEYOND),
        "median_miss_frac_in_range": _finite(miss.get("in_range")),
        "median_miss_frac_beyond": _finite(miss.get("beyond")),
        "run_status": m.get("run_status"), "flags": m.get("flags", []),
    }


def _finite(x):
    return x if isinstance(x, (int, float)) and math.isfinite(x) else None


def import_run(run_dir: Path, label: str, seed: str, out_runs: Path, title: str) -> str | None:
    state = json.loads((run_dir / "state.json").read_text())
    attempt = run_dir / state.get("attempt_dir", "attempt1")
    ledger = attempt / "agent" / "ledger.jsonl"
    if not ledger.exists():
        return None
    run_id = f"{label}_{seed}"
    dest = out_runs / run_id
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ledger, dest / "ledger.jsonl")
    read = lambda name: json.loads((attempt / name).read_text()) if (attempt / name).exists() else None
    metrics = {
        "run_id": run_id,
        "label": f"{label} on world {seed}" + (f" ({title})" if title else ""),
        "condition": label,
        "session_info": read("session_info.json"),
        "score": read("admin_score.json"),
        "truth": read("truth.json"),
        "run_status": state.get("status"), "agent_status": state.get("agent_status"),
    }
    (dest / "metrics.json").write_text(json.dumps(metrics, indent=1))
    for v in ("video.mp4", "video.webm"):
        if (attempt / "agent" / v).exists():
            shutil.copyfile(attempt / "agent" / v, dest / v)
    return run_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, help="eval output root (contains all_metrics.jsonl)")
    ap.add_argument("--out", type=Path, default=Path("viewer/public"))
    ap.add_argument("--title", default="", help="appears in run labels and the aggregate page label")
    ap.add_argument("--labels", default=",".join(LABELS), help="comma-separated eval labels to import")
    ap.add_argument("--no-runs", action="store_true", help="only write aggregate.json")
    ap.add_argument("--remove-fixtures", action="store_true", help="delete the oracle_* fixture run directories")
    args = ap.parse_args()
    labels = [x for x in args.labels.split(",") if x]
    out_runs = args.out / "runs"

    rows = [json.loads(line) for line in (args.root / "all_metrics.jsonl").read_text().splitlines() if line.strip()]
    rows = [r for r in rows if r["label"] in labels]
    budget = max((len(r.get("probe_beyond_curve") or []) for r in rows), default=13) - 1
    agg = {
        "label": f"Eval results: {args.title}" if args.title else f"Eval results from {args.root}",
        "source": str(args.root), "budget": budget, "notes": {},
        "rows": sorted((aggregate_row(r) for r in rows), key=lambda r: (r["condition"], r["world"])),
    }
    (args.out / "eval").mkdir(parents=True, exist_ok=True)
    (args.out / "eval" / "aggregate.json").write_text(json.dumps(agg, indent=1, allow_nan=False))
    print(f"{len(agg['rows'])} aggregate rows -> {args.out / 'eval' / 'aggregate.json'}")

    if args.remove_fixtures:
        for d in out_runs.glob("oracle_*"):
            if (d / "metrics.json").exists() and "(fixture)" in (d / "metrics.json").read_text():
                shutil.rmtree(d)
    if not args.no_runs:
        n = 0
        for label in labels:
            for run_dir in sorted((args.root / label).glob("*")) if (args.root / label).is_dir() else []:
                if (run_dir / "state.json").exists() and import_run(run_dir, label, run_dir.name, out_runs, args.title):
                    n += 1
        print(f"{n} runs -> {out_runs}")
        script = Path(__file__).with_name("scripts") / "build-index.mjs"
        if shutil.which("node"):
            subprocess.run(["node", str(script), str(out_runs)], check=True)
        else:
            print("node not found: run `node viewer/scripts/build-index.mjs` to rebuild runs/index.json")


if __name__ == "__main__":
    main()
