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
from tools.analysis import compile_law

BUDGET = 12
THRESHOLD = 0.8  # share of beyond-range probes within the hit radius
DEP_TOL = 0.01  # a dependence counts when gravity changes by more than 1% over the range
P_TOL = 0.3
RHO_WAIVE = 0.05


# --- law-form recovery ---------------------------------------------------------------


def reduced_values(law: Law, fit: FitResult) -> dict[str, float]:
    """Fitted values, with every parameter within 2 sd of zero set to zero."""
    vals = {}
    for k in law.params:
        est = fit.params.get(k)
        v, sd = (est.value, est.sd) if est else (law.params[k].init, 0.0)
        vals[k] = 0.0 if abs(v) <= 2 * sd else v
    return vals


def law_dependence(law: Law, fit: FitResult) -> dict:
    """Probe the reduced law: does gravity depend on mass, on height; drag exponent."""
    cl = compile_law(law)
    vals = reduced_values(law, fit)
    P = [np.array([vals[k]]) for k in cl.param_names]

    def static_g(m, z):
        _, az = cl.accel(np.array([m]), np.array([z]), np.zeros(1), np.zeros(1), P)
        return float(-az[0])

    def drag_ax(v):
        ax, _ = cl.accel(np.array([0.1]), np.zeros(1), np.array([v]), np.zeros(1), P)
        return float(-ax[0])

    g_ref = static_g(0.1, 0.0)
    scale = max(abs(g_ref), 1e-9)
    mass_dep = abs(static_g(0.02, 0.0) - static_g(0.8, 0.0)) / scale > DEP_TOL
    height_dep = abs(static_g(0.1, 0.0) - static_g(0.1, 1.2)) / scale > DEP_TOL
    a1, a4 = drag_ax(1.0), drag_ax(4.0)
    if not (math.isfinite(a1) and math.isfinite(a4)) or (abs(a1) < 1e-9 and abs(a4) < 1e-9):
        p = None
    elif a1 <= 0 or a4 <= 0:
        p = float("nan")  # not a drag (does not oppose motion)
    else:
        p = math.log(a4 / a1) / math.log(4.0)
    return {"mass_dep": bool(mass_dep), "height_dep": bool(height_dep), "drag_p": p,
            "g_ref": g_ref, "reduced_params": vals}


def law_recovered(dep: dict, truth: dict) -> dict:
    fam = truth["family"]
    mass_ok = dep["mass_dep"] == (fam == "F2")
    height_ok = dep["height_dep"] == (fam == "F3")
    if truth["rho"] < RHO_WAIVE:
        p_ok = True
    else:
        p = dep["drag_p"]
        p_ok = p is not None and math.isfinite(p) and abs(p - truth["p"]) <= P_TOL
    return {"mass_ok": mass_ok, "height_ok": height_ok, "drag_ok": p_ok,
            "recovered": bool(mass_ok and height_ok and p_ok)}


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
        "wall_s": summary.get("wall_s"), "n_experiments": used, "flags": flags,
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
