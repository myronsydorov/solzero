"""Grading (SPEC.md section 7), written before any test run.

    python -m eval.grade runs/eval        # re-grade every run, write metrics.json + all_metrics.jsonl

Per run it reads the admin score, the truth and the agent ledger, and writes the four
primary metrics plus the secondary ones.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

from schemas import FitResult, Law

from .lawform import law_dependence, law_recovered  # noqa: F401  (re-exported for callers)

BUDGET = 12
THRESHOLD = 0.8  # share of beyond-range probes within the hit radius


# --- ledger-derived secondary metrics ------------------------------------------------


def ledger_metrics(path: Path) -> dict:
    if not path.exists():
        return {"plan_changed_share": None, "laws_rejected": None, "initial_explanation_rejected": None}
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    diffs = [r["payload"] for r in rows if r.get("kind") == "decision_diff"]
    diffs = [d for d in diffs if d.get("tentative") is not None]  # a change needs a prior plan
    verdicts = [v for r in rows if r.get("kind") == "verdicts" for v in r["payload"].get("verdicts", [])]
    rejected = {v["law_id"] for v in verdicts if v.get("verdict") == "rejected"}
    first_laws = next((r["payload"].get("laws", []) for r in rows
                       if r.get("kind") == "law_set" and r["payload"].get("laws")), [])
    first_ids = {law.get("law_id") for law in first_laws}
    has_agent_records = any(r.get("kind") in ("law_set", "verdicts", "decision_diff") for r in rows)
    return {
        "plan_changed_share": (float(np.mean([bool(d.get("changed")) for d in diffs])) if diffs else None),
        "laws_rejected": len(rejected) if has_agent_records else None,
        "initial_explanation_rejected": (bool(first_ids & rejected) if first_ids else None),
    }


# --- per-run grading ---------------------------------------------------------------


def threshold_curve(nominations: list[dict], used: int, key: str) -> list[float | None]:
    """Value of the latest nomination made after at most k experiments, k = 0..BUDGET."""
    curve = []
    for k in range(BUDGET + 1):
        noms = [n for n in nominations if n["n_experiments"] <= k]
        curve.append(noms[-1][key] if noms else None)
    return curve


def grade_run(run_dir: Path, label: str | None = None, seed: int | None = None) -> dict:
    run_dir = Path(run_dir)
    score = json.loads((run_dir / "admin_score.json").read_text())
    truth = json.loads((run_dir / "truth.json").read_text())
    summary_path = run_dir / "agent" / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    noms = score.get("nominations", [])
    used = score.get("used", 0)
    commit = score.get("commit")
    flags = []

    final_nom = [n for n in noms if n["n_experiments"] == used] or noms[-1:]
    if not noms:
        flags.append("no_nomination")
    elif final_nom[0]["n_experiments"] != used:
        flags.append("no_nomination_after_last_experiment")
    probe_beyond = final_nom[0]["probe_within_beyond"] if final_nom else 0.0
    probe_in = final_nom[0]["probe_within_in_range"] if final_nom else 0.0

    kinds = {t["target_id"]: t["kind"] for t in score.get("targets", [])}
    shots = {s["target_id"]: s for s in (commit or {}).get("shots", [])}
    if commit is None:
        flags.append("no_commit")

    def hit_rate(kind=None):
        ids = [t for t, k in kinds.items() if kind is None or k == kind]
        return float(np.mean([bool(shots.get(t, {}).get("hit")) for t in ids])) if ids else None

    miss_frac = {k: (float(np.median([shots[t]["miss_frac"] for t in kinds if kinds[t] == k and t in shots
                                      and shots[t]["miss_frac"] is not None and math.isfinite(shots[t]["miss_frac"])]))
                     if any(kinds[t] == k and t in shots for t in kinds) else None)
                 for k in ("in_range", "beyond")}

    # Graded law: the committed law_id's latest nomination, else the last nomination.
    graded = None
    if commit:
        graded = next((n for n in reversed(noms) if n["law"]["law_id"] == commit["law_id"]), None)
        if graded is None:
            flags.append("committed_law_not_nominated")
    graded = graded or (noms[-1] if noms else None)
    if graded:
        dep = law_dependence(Law.model_validate(graded["law"]), FitResult.model_validate(graded["fit"]))
        rec = law_recovered(dep, truth)
    else:
        dep, rec = None, {"recovered": False}

    control = truth["family"] == "F0"
    claims_non_ordinary = bool(commit and commit.get("claims_non_ordinary"))
    beyond_curve = threshold_curve(noms, used, "probe_within_beyond")
    reach = next((k for k, v in enumerate(beyond_curve) if v is not None and v >= THRESHOLD), None)
    return {
        "label": label, "seed": seed if seed is not None else truth.get("seed"), "world_id": score["world_id"],
        "condition": score["condition"], "family": truth["family"], "session_id": score["session_id"],
        "agent_status": summary.get("status"), "tokens_used": summary.get("tokens_used"),
        "wall_s": summary.get("wall_s"), "n_experiments": used, "experiments_used": used, "flags": flags,
        # primary
        "probe_hit_beyond": probe_beyond,
        "mission_hit_rate": hit_rate(),
        "law_form_recovered": rec["recovered"],
        "false_discovery": (claims_non_ordinary if control else None),
        # mission detail
        "mission_hit_in_range": hit_rate("in_range"), "mission_hit_beyond": hit_rate("beyond"),
        "mission_median_miss_frac": miss_frac,
        # secondary
        "probe_hit_in_range": probe_in,
        "probe_beyond_curve": beyond_curve,
        "probe_median_error_curve_m": threshold_curve(noms, used, "probe_median_error_m"),
        "experiments_to_threshold": reach,
        "claim": (commit or {}).get("claim"),
        "abstained": bool(commit and commit.get("claim") == "insufficient_evidence"),
        "claims_non_ordinary": claims_non_ordinary,
        "law_dependence": dep, "law_recovery_detail": rec,
        **ledger_metrics(run_dir / "agent" / "ledger.jsonl"),
    }


def regrade(root: Path) -> list[dict]:
    rows = []
    for state_path in sorted(root.glob("*/*/state.json")):
        state = json.loads(state_path.read_text())
        if "attempt_dir" not in state or state.get("status") not in ("done", "failed"):
            continue
        m = grade_run(state_path.parent / state["attempt_dir"], label=state["label"], seed=state["seed"])
        m["run_status"] = state["status"]
        (state_path.parent / "metrics.json").write_text(json.dumps(m, indent=1))
        rows.append(m)
    with (root / "all_metrics.jsonl").open("w") as f:
        for m in rows:
            f.write(json.dumps(m) + "\n")
    return rows


if __name__ == "__main__":
    rows = regrade(Path(sys.argv[1] if len(sys.argv) > 1 else "runs/eval"))
    print(f"graded {len(rows)} runs")
