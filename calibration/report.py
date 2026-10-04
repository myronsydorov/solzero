"""Summarise a calibration run: criteria table, failure diagnosis, error-versus-experiment plot.

    python -m calibration.report calibration/results/dev20_v2
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


HEADLINE = 0.8  # share of beyond-range probes within the hit radius (decision 2)


def first_reaching(curve, level=HEADLINE):
    for i, v in enumerate(curve):
        if v >= level:
            return i
    return None


def _by_frac(targets, frac, key="hit_prob_by_frac", kind=None):
    vals = [float(t[key][frac]) for t in targets if kind is None or t["kind"] == kind]
    return round(float(np.mean(vals)), 3) if vals else None


def _mission_block(targets, fracs=("0.03", "0.02")) -> dict:
    out = {}
    for f in fracs:
        out[f"radius_{f}"] = {k or "all": _by_frac(targets, f, kind=k) for k in (None, "in_range", "beyond")}
        out[f"radius_{f}_nominal"] = {k or "all": _by_frac(targets, f, "nominal_hit_by_frac", k)
                                      for k in (None, "in_range", "beyond")}
    out["median_miss_frac"] = {k: round(float(np.median([t["median_miss_frac"] for t in targets if t["kind"] == k])), 4)
                               for k in ("in_range", "beyond")}
    return out


def _bootstrap(delta, seed=0):
    rng = np.random.default_rng(seed)
    boots = [rng.choice(delta, delta.size).mean() for _ in range(4000)]
    return [round(float(np.percentile(boots, 2.5)), 3), round(float(np.percentile(boots, 97.5)), 3)]


def summarise(run: Path) -> dict:
    A, B = load(run)
    s = {"run": str(run), "n_worlds_A": len(A), "n_worlds_B": len({b["seed"] for b in B})}
    if A:
        ctrl = lambda x: x["truth"]["family"] == "F0"  # noqa: E731
        sa = {
            "exact_truth": _mission_block([t for x in A for t in x["mission_exact_truth"]]),
            "true_fit": _mission_block([t for x in A for t in x["mission_true_fit"]]),
            "best_wrong_all_worlds": _mission_block([t for x in A for t in x["mission_best_wrong"]]),
            "best_wrong_non_control": _mission_block([t for x in A if not ctrl(x) for t in x["mission_best_wrong"]]),
            "best_non_nesting_wrong_all_worlds": _mission_block(
                [t for x in A for t in x["mission_best_non_nesting_wrong"]]),
            "best_wrong_beyond_by_family_0.02": {
                fam: _by_frac([t for x in A if x["truth"]["family"] == fam for t in x["mission_best_wrong"]],
                              "0.02", kind="beyond") for fam in ("F0", "F1", "F2", "F3")},
            "best_non_nesting_wrong_beyond_by_family_0.02": {
                fam: _by_frac([t for x in A if x["truth"]["family"] == fam for t in x["mission_best_non_nesting_wrong"]],
                              "0.02", kind="beyond") for fam in ("F0", "F1", "F2", "F3")},
            "exact_truth_hit_prob_by_actuation_radius_0.02": _sweep([t for x in A for t in x["mission_exact_truth"]]),
            "unreachable_under_truth": int(sum(not t["reachable_under_law"] for x in A for t in x["mission_exact_truth"])),
            "true_form_selected_by_bic": float(np.mean([x["selected_form"] == x["true_form"] for x in A])),
            "probe_median_true_fit_noiseless_m_max": round(max(x["probe_median_true_fit_noiseless"] for x in A), 5),
            "fit_time_max_s": round(max(x["fit_time_max_s"] for x in A), 2),
            "mean_runtime_s": float(np.mean([x["runtime_s"] for x in A])),
        }
        sep = []
        for x in A:
            if ctrl(x):
                continue
            tb = _by_frac(x["mission_true_fit"], "0.02", "nominal_hit_by_frac", "beyond")
            wb = _by_frac(x["mission_best_wrong"], "0.02", "nominal_hit_by_frac", "beyond")
            sep.append(tb > wb)
        sa["separability_nominal_0.02_non_control"] = float(np.mean(sep)) if sep else None
        s["stageA"] = sa
    if B:
        out = {"hit_frac": B[0].get("hit_frac")}
        for pol in ("random", "greedy"):
            runs = [b for b in B if b["policy"] == pol]
            if not runs:
                continue
            within = np.array([[st["probe_selected"]["within_beyond"] for st in b["steps"]] for b in runs])
            within_true = np.array([[st["probe_true_form"]["within_beyond"] for st in b["steps"]] for b in runs])
            med_all = np.array([[st["probe_selected"]["median_all"] for st in b["steps"]] for b in runs])
            reach = [first_reaching(c) for c in within]
            ctrl_runs = [b for b in runs if b["truth"]["family"] == "F0"]
            mission_t = sum((b["mission_selected"] for b in runs), [])
            out[pol] = {
                "n": len(runs),
                "headline_experiments_to_80pct_beyond": reach,
                "headline_reached_within_12": float(np.mean([r is not None for r in reach])),
                "mean_within_beyond_curve": [round(float(v), 3) for v in within.mean(0)],
                "mean_within_beyond_curve_true_form": [round(float(v), 3) for v in within_true.mean(0)],
                "law_form_recovery": float(np.mean([b["final_form"] == b["true_form"] for b in runs])),
                "law_form_recovery_by_family": {
                    fam: f"{sum(b['final_form'] == b['true_form'] for b in runs if b['truth']['family'] == fam)}"
                         f"/{sum(b['truth']['family'] == fam for b in runs)}" for fam in ("F0", "F1", "F2", "F3")},
                "false_discovery_control": (f"{sum(b['claims_non_ordinary'] for b in ctrl_runs)}/{len(ctrl_runs)}"
                                            if ctrl_runs else None),
                "secondary_median_all_probe_error_curve_m": [round(float(v), 4) for v in np.median(med_all, 0)],
                "mission_hit_prob": {k or "all": round(hits(mission_t, k), 3) for k in (None, "in_range", "beyond")},
                "mission_nominal_hit": {k or "all": round(hits(mission_t, k, "nominal_hit"), 3)
                                        for k in (None, "in_range", "beyond")},
                "mission_median_miss_frac": {k: round(float(np.median([t["median_miss_frac"] for t in mission_t
                                                                       if t["kind"] == k])), 4)
                                             for k in ("in_range", "beyond")},
                "fit_time_max_s": round(max(b["fit_time_max_s"] for b in runs), 2),
                "mean_runtime_s": float(np.mean([b["runtime_s"] for b in runs])),
            }
        by = {(b["seed"], b["policy"]): b for b in B}
        pairs = [sd for sd in sorted({b["seed"] for b in B}) if (sd, "random") in by and (sd, "greedy") in by]
        if pairs:
            exp = np.array([[13 if (v := first_reaching([st["probe_selected"]["within_beyond"]
                                                          for st in by[(sd, p)]["steps"]])) is None else v
                             for p in ("random", "greedy")] for sd in pairs])
            d = exp[:, 0] - exp[:, 1]
            hit = np.array([[hits(by[(sd, p)]["mission_selected"]) for p in ("random", "greedy")] for sd in pairs])
            dh = hit[:, 1] - hit[:, 0]
            out["paired"] = {
                "random_minus_greedy_experiments_to_headline": {
                    "mean": round(float(d.mean()), 3), "ci95": _bootstrap(d),
                    "note": "13 means not reached within 12 experiments"},
                "greedy_minus_random_mission_hit_prob": {"mean": round(float(dh.mean()), 3), "ci95": _bootstrap(dh)},
            }
        s["stageB"] = out
    return s


def plot(run: Path) -> Path | None:
    _, B = load(run)
    if not B:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    colors = {"random": "#d95f02", "greedy": "#1b9e77"}
    for pol in ("random", "greedy"):
        runs = [b for b in B if b["policy"] == pol]
        if not runs:
            continue
        W = np.array([[st["probe_selected"]["within_beyond"] for st in b["steps"]] for b in runs])
        M = np.clip(np.array([[st["probe_selected"]["median_all"] for st in b["steps"]] for b in runs]), 1e-4, 1e2)
        steps = np.arange(W.shape[1])
        for c in M:
            axes[1].plot(steps, c, color=colors[pol], alpha=0.12, lw=0.8)
        axes[0].plot(steps, W.mean(0), color=colors[pol], lw=2.5, label=f"{pol} (mean, n={len(runs)})")
        axes[0].fill_between(steps, np.percentile(W, 25, 0), np.percentile(W, 75, 0), color=colors[pol], alpha=0.15)
        axes[1].plot(steps, np.median(M, 0), color=colors[pol], lw=2.5, label=f"{pol} (median)")
    axes[0].axhline(HEADLINE, color="k", ls="--", lw=1, label="80% headline level")
    axes[0].set_ylim(0, 1.02)
    axes[0].set_ylabel("share of 20 beyond-range probes within hit radius")
    axes[0].set_title("headline: BIC-selected form, beyond-range probes (band: IQR)", fontsize=10)
    axes[1].axhline(0.05, color="k", ls=":", lw=1, label="5 cm")
    axes[1].set_yscale("log")
    axes[1].set_ylabel("median landing error, all 40 probes (m)")
    axes[1].set_title("secondary: all probes", fontsize=10)
    for ax in axes:
        ax.set_xlabel("experiments (after shot zero)")
        ax.grid(alpha=0.3, which="both")
        ax.legend(fontsize=8)
    fig.suptitle(f"Calibration stage B: {run.name}", fontsize=11)
    fig.tight_layout()
    path = run / "error_vs_experiment.png"
    fig.savefig(path, dpi=130)
    return path


def primary_table(run: Path) -> str:
    """SPEC 7 primary metrics for the scripted policies, with 95% bootstrap CIs and paired
    differences. Law recovery uses the eval/grade.py rule on the final fit."""
    from calibration.library import library
    from eval.lawform import claims_non_ordinary, law_dependence, law_recovered
    from schemas import FitResult

    _, B = load(run)
    laws = library()
    rows = {}
    for b in B:
        dep = law_dependence(laws[b["final_form"]], FitResult.model_validate(b["final_fit"]))
        rows[(b["policy"], b["seed"])] = {
            "probe_hit_beyond": b["steps"][-1]["probe_selected"]["within_beyond"],
            "mission_hit_rate": hits(b["mission_selected"]),
            "mission_hit_in_range": hits(b["mission_selected"], "in_range"),
            "mission_hit_beyond": hits(b["mission_selected"], "beyond"),
            "law_form_recovered": float(law_recovered(dep, b["truth"])["recovered"]),
            # As run: the claim stored in the raw file (form rule for runs before 2026-10-04 04:30).
            "false_discovery_form_rule": (float(b.get("claims_non_ordinary_form_rule", b["claims_non_ordinary"]))
                                          if b["truth"]["family"] == "F0" else None),
            # Regraded: the SPEC 7 2-sd rule applied to the saved final fit, no rerun.
            "false_discovery": float(claims_non_ordinary(dep)) if b["truth"]["family"] == "F0" else None,
            "claims_any_2sd": float(claims_non_ordinary(dep)),
        }
    keys = [("probe_hit_beyond", "Beyond-range probe hit rate (after 12)"), ("mission_hit_rate", "Mission hit rate"),
            ("mission_hit_in_range", "  in-range targets"), ("mission_hit_beyond", "  beyond-range targets"),
            ("law_form_recovered", "Law-form recovery (SPEC 7 rule)"),
            ("false_discovery_form_rule", "Control false discovery, claim by selected form (as run)"),
            ("false_discovery", "Control false discovery, claim by 2-sd rule (regraded)")]
    seeds = sorted({sd for (_, sd) in rows})
    paired_seeds = [sd for sd in seeds if ("random", sd) in rows and ("greedy", sd) in rows]

    def ci(x, seed=0):
        x = np.asarray(x, float)
        rng = np.random.default_rng(seed)
        m = rng.choice(x, (4000, x.size)).mean(1)
        return f"{x.mean():.3f} [{np.percentile(m, 2.5):.3f}, {np.percentile(m, 97.5):.3f}]"

    lines = [f"Scripted policies on {len(seeds)} dev worlds (hit radius 2%, launcher 0.5% / 0.1 deg). "
             "Means with 95% bootstrap CIs over worlds; paired = greedy minus random on the same world.", "",
             "| Metric | Random | Greedy | Paired difference (greedy - random) | n |", "| --- | --- | --- | --- | --- |"]
    for k, name in keys:
        cols = []
        for pol in ("random", "greedy"):
            v = [rows[(pol, sd)][k] for sd in seeds if (pol, sd) in rows and rows[(pol, sd)][k] is not None]
            cols.append(ci(v))
        d = [rows[("greedy", sd)][k] - rows[("random", sd)][k] for sd in paired_seeds if rows[("random", sd)][k] is not None]
        lines.append(f"| {name} | {cols[0]} | {cols[1]} | {ci(d, 1)} | {len(d)} |")
    return "\n".join(lines) + "\n"


def main():
    run = Path(sys.argv[1] if len(sys.argv) > 1 else "calibration/results/dev5")
    s = summarise(run)
    (run / "summary.json").write_text(json.dumps(s, indent=1))
    p = plot(run)
    if any(run.glob("stageB_*.json")):
        table = primary_table(run)
        (run / "primary_table.md").write_text(table)
        print(table)
    print(json.dumps(s, indent=1))
    if p:
        print("plot:", p)


if __name__ == "__main__":
    main()
