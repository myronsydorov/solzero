"""Summarise a calibration run: criteria table, failure diagnosis, error-versus-experiment plot.

    python -m calibration.report calibration/results/dev5
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

THRESH = 0.05


def load(run: Path):
    a = [json.loads(p.read_text()) for p in sorted(run.glob("stageA_*.json"))]
    b = [json.loads(p.read_text()) for p in sorted(run.glob("stageB_*.json"))]
    return a, b


def hits(m, kind=None, key="hit_prob"):
    vals = [float(t[key]) for t in m if kind is None or t["kind"] == kind]
    return float(np.mean(vals)) if vals else float("nan")


def _sweep(targets):
    """Mean hit probability per actuation level, overall / in_range / beyond."""
    keys = targets[0]["hit_prob_actuation_sweep"].keys() if targets else []
    return {k: {kind or "all": round(float(np.mean([t["hit_prob_actuation_sweep"][k] for t in targets
                                                     if kind is None or t["kind"] == kind])), 3)
                for kind in (None, "in_range", "beyond")} for k in keys}


def first_below(curve):
    for i, v in enumerate(curve):
        if v < THRESH:
            return i
    return None


def summarise(run: Path) -> dict:
    A, B = load(run)
    s = {"run": str(run), "n_worlds_A": len(A), "n_worlds_B": len({b["seed"] for b in B})}
    if A:
        s["stageA"] = {
            "hit_prob_true_fit": hits(sum((x["mission_true_fit"] for x in A), [])),
            "hit_prob_true_fit_in_range": hits(sum((x["mission_true_fit"] for x in A), []), "in_range"),
            "hit_prob_true_fit_beyond": hits(sum((x["mission_true_fit"] for x in A), []), "beyond"),
            "nominal_hit_true_fit": hits(sum((x["mission_true_fit"] for x in A), []), key="nominal_hit"),
            "hit_prob_exact_truth": hits(sum((x["mission_exact_truth"] for x in A), [])),
            "nominal_hit_exact_truth": hits(sum((x["mission_exact_truth"] for x in A), []), key="nominal_hit"),
            "unreachable_under_truth": int(sum(not t["reachable_under_law"] for x in A for t in x["mission_exact_truth"])),
            "true_form_selected_by_bic": float(np.mean([x["selected_form"] == x["true_form"] for x in A])),
            "probe_median_true_fit_m": [round(x["probe_median_true_fit"], 4) for x in A],
            "noiseless_true_fit_chi2": [round(x["fit_true_noiseless"]["chi2_dof"], 4) for x in A],
            "probe_median_true_fit_noiseless_m": [round(x["probe_median_true_fit_noiseless"], 5) for x in A],
            "hit_prob_exact_truth_by_actuation": _sweep([t for x in A for t in x["mission_exact_truth"]]),
            "hit_prob_true_fit_by_actuation": _sweep([t for x in A for t in x["mission_true_fit"]]),
            "mean_runtime_s": float(np.mean([x["runtime_s"] for x in A])),
        }
        non_ctrl = [x for x in A if x["truth"]["family"] != "F0"]
        sep = []
        for x in non_ctrl:
            tb = [t for t in x["mission_true_fit"] if t["kind"] == "beyond"]
            wb = [t for t in x["mission_best_wrong"] if t["kind"] == "beyond"]
            sep.append(hits(tb, key="nominal_hit") > hits(wb, key="nominal_hit"))
        s["stageA"]["separability_nominal"] = float(np.mean(sep)) if sep else None
        s["stageA"]["beyond_hit_prob_best_wrong"] = hits(
            [t for x in non_ctrl for t in x["mission_best_wrong"]], "beyond") if non_ctrl else None
    if B:
        out = {}
        for pol in ("random", "greedy"):
            runs = [b for b in B if b["policy"] == pol]
            if not runs:
                continue
            curves = np.array([[st["probe_median_selected"] for st in b["steps"]] for b in runs])
            curves_true = np.array([[st["probe_median_true_form"] for st in b["steps"]] for b in runs])
            reach = [first_below(c) for c in curves]
            ctrl = [b for b in runs if b["truth"]["family"] == "F0"]
            out[pol] = {
                "n": len(runs),
                "reach_5cm_within_12": float(np.mean([r is not None for r in reach])),
                "experiments_to_5cm": reach,
                "final_probe_median_m": [round(float(c[-1]), 4) for c in curves],
                "median_curve_m": [round(float(v), 4) for v in np.median(curves, 0)],
                "median_curve_true_form_m": [round(float(v), 4) for v in np.median(curves_true, 0)],
                "final_form_correct": float(np.mean([b["final_form"] == b["true_form"] for b in runs])),
                "false_discovery_control": (float(np.mean([b["claims_non_ordinary"] for b in ctrl]))
                                            if ctrl else None),
                "hit_prob_selected": hits(sum((b["mission_selected"] for b in runs), [])),
                "hit_prob_selected_in_range": hits(sum((b["mission_selected"] for b in runs), []), "in_range"),
                "hit_prob_selected_beyond": hits(sum((b["mission_selected"] for b in runs), []), "beyond"),
                "nominal_hit_selected": hits(sum((b["mission_selected"] for b in runs), []), key="nominal_hit"),
                "hit_prob_selected_by_actuation": _sweep(sum((b["mission_selected"] for b in runs), [])),
                "mean_runtime_s": float(np.mean([b["runtime_s"] for b in runs])),
            }
        # Paired difference in experiments-to-5cm (13 = not reached within budget).
        seeds = sorted({b["seed"] for b in B})
        by = {(b["seed"], b["policy"]): b for b in B}
        diffs = []
        for sd in seeds:
            if (sd, "random") in by and (sd, "greedy") in by:
                r = [first_below([st["probe_median_selected"] for st in by[(sd, p)]["steps"]]) for p in ("random", "greedy")]
                diffs.append([13 if v is None else v for v in r])
        if diffs:
            d = np.array(diffs)
            delta = d[:, 0] - d[:, 1]
            rng = np.random.default_rng(0)
            boots = [rng.choice(delta, delta.size).mean() for _ in range(2000)]
            out["paired_random_minus_greedy_experiments"] = {
                "mean": float(delta.mean()), "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
                "note": "13 means the 5 cm threshold was not reached within 12 experiments"}
        s["stageB"] = out
    return s


def plot(run: Path) -> Path | None:
    _, B = load(run)
    if not B:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    colors = {"random": "#d95f02", "greedy": "#1b9e77"}
    for ax, key, title in [(axes[0], "probe_median_selected", "BIC-selected form"),
                           (axes[1], "probe_median_true_form", "true form (oracle model choice)")]:
        for pol in ("random", "greedy"):
            runs = [b for b in B if b["policy"] == pol]
            if not runs:
                continue
            C = np.array([[st[key] for st in b["steps"]] for b in runs])
            C = np.clip(C, 1e-4, 1e2)
            steps = np.arange(C.shape[1])
            for c in C:
                ax.plot(steps, c, color=colors[pol], alpha=0.15, lw=0.8)
            ax.plot(steps, np.median(C, 0), color=colors[pol], lw=2.5, label=f"{pol} (median, n={len(runs)})")
        ax.axhline(THRESH, color="k", ls="--", lw=1, label="5 cm threshold")
        ax.set_yscale("log")
        ax.set_xlabel("experiments (after shot zero)")
        ax.set_title(title, fontsize=10)
        ax.grid(alpha=0.3, which="both")
    axes[0].set_ylabel("median landing error on 20 hidden probes (m)")
    axes[0].legend(fontsize=8)
    fig.suptitle(f"Calibration stage B: {run.name}", fontsize=11)
    fig.tight_layout()
    path = run / "error_vs_experiment.png"
    fig.savefig(path, dpi=130)
    return path


def main():
    run = Path(sys.argv[1] if len(sys.argv) > 1 else "calibration/results/dev5")
    s = summarise(run)
    (run / "summary.json").write_text(json.dumps(s, indent=1))
    p = plot(run)
    print(json.dumps(s, indent=1))
    if p:
        print("plot:", p)


if __name__ == "__main__":
    main()
