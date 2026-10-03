"""Statistics and figures for an evaluation (SPEC.md section 7).

    python -m eval.report runs/eval                 # reads all_metrics.jsonl (run eval.grade first)

Writes to runs/eval/report/: report.json, summary_table.md, summary_table.png,
learning_curves.png, mission_hits.png, false_discovery.png.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# Fixed categorical order (validated palette, light surface); colour follows the condition.
ORDER = ["lab", "single", "random", "random-scripted", "oracle", "textbook"]
COLORS = {"lab": "#2a78d6", "single": "#eb6834", "random": "#1baf7a", "random-scripted": "#eda100",
          "oracle": "#e87ba4", "textbook": "#4a3aa7"}
MARKERS = {"lab": "o", "single": "s", "random": "^", "random-scripted": "v", "oracle": "D", "textbook": "X"}
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
PAIRS = [("lab", "random"), ("lab", "single"), ("oracle", "random-scripted"), ("lab", "oracle")]
PRIMARY = [("probe_hit_beyond", "Beyond-range probe hit rate"), ("mission_hit_rate", "Mission hit rate"),
           ("law_form_recovered", "Law-form recovery"), ("false_discovery", "Control false discovery")]
N_BOOT = 4000


def load(root: Path) -> list[dict]:
    path = root / "all_metrics.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _num(v):
    return None if v is None else float(v)


def boot_ci(x: np.ndarray, seed: int = 0) -> list[float] | None:
    if x.size == 0:
        return None
    rng = np.random.default_rng(seed)
    means = rng.choice(x, (N_BOOT, x.size)).mean(1)
    return [round(float(np.percentile(means, 2.5)), 3), round(float(np.percentile(means, 97.5)), 3)]


def per_condition(rows: list[dict]) -> dict:
    out = {}
    for label in ORDER:
        rs = [r for r in rows if r["label"] == label]
        if not rs:
            continue
        ctrl = [r for r in rs if r["false_discovery"] is not None]
        d = {"n_worlds": len(rs), "n_control": len(ctrl)}
        for key, _ in PRIMARY:
            vals = np.array([_num(r[key]) for r in rs if r[key] is not None], float)
            d[key] = {"mean": round(float(vals.mean()), 3) if vals.size else None, "ci95": boot_ci(vals), "n": int(vals.size)}
        for key in ("mission_hit_in_range", "mission_hit_beyond", "probe_hit_in_range"):
            vals = np.array([r[key] for r in rs if r[key] is not None], float)
            d[key] = {"mean": round(float(vals.mean()), 3) if vals.size else None, "ci95": boot_ci(vals)}
        reach = [r["experiments_to_threshold"] for r in rs]
        d["experiments_to_threshold_median"] = (float(np.median([13 if v is None else v for v in reach])))
        d["reached_threshold"] = round(float(np.mean([v is not None for v in reach])), 3)
        d["abstention"] = round(float(np.mean([r["abstained"] for r in rs])), 3)
        pc = [r["plan_changed_share"] for r in rs if r.get("plan_changed_share") is not None]
        d["plan_changed_share_mean"] = round(float(np.mean(pc)), 3) if pc else None
        ir = [r["initial_explanation_rejected"] for r in rs if r.get("initial_explanation_rejected") is not None]
        d["initial_explanation_rejected"] = round(float(np.mean(ir)), 3) if ir else None
        d["failed_runs"] = sum(r.get("run_status") == "failed" for r in rs)
        d["flags"] = sorted({f for r in rs for f in r["flags"]})
        out[label] = d
    return out


def paired(rows: list[dict]) -> dict:
    by = {(r["label"], r["seed"]): r for r in rows}
    out = {}
    for a, b in PAIRS:
        seeds = sorted({s for (lab, s) in by if lab == a} & {s for (lab, s) in by if lab == b})
        if not seeds:
            continue
        res = {"n_worlds": len(seeds)}
        for key, _ in PRIMARY:
            diffs = np.array([_num(by[(a, s)][key]) - _num(by[(b, s)][key]) for s in seeds
                              if by[(a, s)][key] is not None and by[(b, s)][key] is not None], float)
            res[key] = {"mean_diff": round(float(diffs.mean()), 3) if diffs.size else None,
                        "ci95": boot_ci(diffs, seed=1), "n": int(diffs.size)}
        out[f"{a} - {b}"] = res
    return out


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def fig_learning(rows, path: Path):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), facecolor=SURFACE)
    for ax in axes:
        _style(ax)
    k = np.arange(13)
    for label in ORDER:
        rs = [r for r in rows if r["label"] == label]
        if not rs:
            continue
        C = np.array([[np.nan if v is None else v for v in r["probe_beyond_curve"]] for r in rs], float)
        E = np.array([[np.nan if v is None else v for v in r["probe_median_error_curve_m"]] for r in rs], float)
        m = np.nanmean(C, 0)
        axes[0].plot(k, m, color=COLORS[label], lw=2, marker=MARKERS[label], ms=5, label=f"{label} (n={len(rs)})")
        axes[0].annotate(label, (k[-1], m[-1]), xytext=(6, 0), textcoords="offset points", va="center",
                         fontsize=8, color=INK2)
        axes[1].plot(k, np.nanmedian(np.clip(E, 1e-4, 1e2), 0), color=COLORS[label], lw=2,
                     marker=MARKERS[label], ms=5, label=label)
    axes[0].axhline(0.8, color=INK2, ls="--", lw=1)
    axes[0].text(0.2, 0.81, "80% threshold (secondary metric)", fontsize=8, color=INK2, va="bottom")
    axes[0].set_ylim(0, 1.03)
    axes[0].set_xlim(0, 13.8)
    axes[0].set_ylabel("share of beyond-range probes within hit radius", color=INK)
    axes[0].set_title("Beyond-range probe hit rate vs experiments (mean over worlds)", fontsize=10, color=INK)
    axes[1].set_yscale("log")
    axes[1].set_ylabel("median landing error, all 40 probes (m)", color=INK)
    axes[1].set_title("Secondary: all-probe error (median over worlds)", fontsize=10, color=INK)
    for ax in axes:
        ax.set_xlabel("experiments after shot zero", color=INK)
        ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=140, facecolor=SURFACE)
    plt.close(fig)


def fig_mission(stats, path: Path):
    labels = [l for l in ORDER if l in stats]
    fig, ax = plt.subplots(figsize=(9, 4.2), facecolor=SURFACE)
    _style(ax)
    x = np.arange(len(labels))
    w = 0.36
    for j, (key, name, alpha) in enumerate([("mission_hit_in_range", "in-range", 0.55),
                                            ("mission_hit_beyond", "beyond-range", 1.0)]):
        for i, lab in enumerate(labels):
            d = stats[lab][key]
            if d["mean"] is None:
                continue
            xi = x[i] + (j - 0.5) * (w + 0.02)
            ax.bar(xi, d["mean"], w, color=COLORS[lab], alpha=alpha, edgecolor=SURFACE, linewidth=2,
                   hatch=None if j else "//")
            if d["ci95"]:
                ax.errorbar(xi, d["mean"], yerr=[[d["mean"] - d["ci95"][0]], [d["ci95"][1] - d["mean"]]],
                            color=INK2, lw=1, capsize=3)
            ax.text(xi, 0.02, f"{d['mean']:.2f}", ha="center", va="bottom", fontsize=7, color=INK, rotation=90)
    ax.set_xticks(x, labels)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("share of targets hit", color=INK)
    ax.set_title("Mission hits: in-range (hatched) vs beyond-range (solid), 95% bootstrap CI", fontsize=10, color=INK)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor=INK2, alpha=0.55, hatch="//", edgecolor=SURFACE, label="in-range"),
                       Patch(facecolor=INK2, label="beyond-range")], fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=140, facecolor=SURFACE)
    plt.close(fig)


def fig_false_discovery(stats, path: Path):
    labels = [l for l in ORDER if l in stats and stats[l]["n_control"]]
    fig, ax = plt.subplots(figsize=(8, 3.6), facecolor=SURFACE)
    _style(ax)
    for i, lab in enumerate(labels):
        d = stats[lab]["false_discovery"]
        ax.bar(i, d["mean"], 0.6, color=COLORS[lab], edgecolor=SURFACE, linewidth=2)
        if d["ci95"]:
            ax.errorbar(i, d["mean"], yerr=[[d["mean"] - d["ci95"][0]], [d["ci95"][1] - d["mean"]]],
                        color=INK2, lw=1, capsize=3)
        ax.text(i, d["mean"] + 0.03, f"{d['mean']:.2f} (n={d['n']})", ha="center", fontsize=8, color=INK)
    ax.set_xticks(range(len(labels)), labels)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("share of control worlds", color=INK)
    ax.set_title("Control false discovery: non-ordinary physics claimed on ordinary worlds", fontsize=10, color=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=140, facecolor=SURFACE)
    plt.close(fig)


def _fmt(d):
    if d is None or d.get("mean") is None:
        return "n/a"
    ci = d.get("ci95")
    return f"{d['mean']:.2f} [{ci[0]:.2f}, {ci[1]:.2f}]" if ci else f"{d['mean']:.2f}"


def summary_table(stats, pairs) -> tuple[list[str], list[list[str]], str]:
    head = ["condition", "worlds", "beyond probe hit", "mission hit", "in / beyond", "law recovery",
            "false disc. (ctrl)", "exp. to 80%", "abstain"]
    body = []
    for lab in ORDER:
        if lab not in stats:
            continue
        d = stats[lab]
        body.append([lab, str(d["n_worlds"]), _fmt(d["probe_hit_beyond"]), _fmt(d["mission_hit_rate"]),
                     f"{d['mission_hit_in_range']['mean']:.2f} / {d['mission_hit_beyond']['mean']:.2f}",
                     _fmt(d["law_form_recovered"]),
                     f"{_fmt(d['false_discovery'])} n={d['n_control']}",
                     f"{d['experiments_to_threshold_median']:g}", f"{d['abstention']:.2f}"])
    md = ["| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
    md += ["| " + " | ".join(r) + " |" for r in body]
    md.append("")
    md.append("Values are means over worlds with 95% bootstrap intervals. Experiments to 80% is a median, and 13 means not reached.")
    md.append("")
    if pairs:
        md.append("| paired difference | worlds | beyond probe hit | mission hit | law recovery | false disc. |")
        md.append("| --- | --- | --- | --- | --- | --- |")
        for name, p in pairs.items():
            cells = []
            for key, _ in PRIMARY:
                q = p[key]
                cells.append("n/a" if q["mean_diff"] is None else
                             f"{q['mean_diff']:+.2f} [{q['ci95'][0]:+.2f}, {q['ci95'][1]:+.2f}] (n={q['n']})")
            md.append(f"| {name} | {p['n_worlds']} | " + " | ".join(cells) + " |")
    return head, body, "\n".join(md) + "\n"


def fig_table(head, body, path: Path):
    fig, ax = plt.subplots(figsize=(14, 0.5 + 0.42 * (len(body) + 1)), facecolor=SURFACE)
    ax.axis("off")
    t = ax.table(cellText=body, colLabels=head, loc="center", cellLoc="center")
    t.auto_set_font_size(False)
    t.set_fontsize(8.5)
    t.scale(1, 1.4)
    for (r, c), cell in t.get_celld().items():
        cell.set_edgecolor(GRID)
        cell.set_text_props(color=INK)
        if r == 0:
            cell.set_text_props(weight="bold", color=INK)
            cell.set_facecolor("#f0efec")
        elif c == 0:
            cell.set_facecolor(SURFACE)
            cell.get_text().set_color(INK)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


README_START, README_END = "<!-- results:start -->", "<!-- results:end -->"


def fill_readme(readme: Path, md: str, root: Path) -> None:
    """Replace the README results block with this evaluation's summary table."""
    text = readme.read_text()
    a, b = text.index(README_START) + len(README_START), text.index(README_END)
    block = (f"\n_Generated by `python -m eval.report {root} --readme {readme}`. "
             f"Figures: `{root}/report/`._\n\n{md}\n")
    readme.write_text(text[:a] + block + text[b:])


def main():
    warnings.filterwarnings("ignore", category=RuntimeWarning)  # all-NaN curve steps before a first nomination
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="runs/eval")
    ap.add_argument("--readme", help="also write the summary table into this README's results block")
    args = ap.parse_args()
    root = Path(args.root)
    out = root / "report"
    out.mkdir(parents=True, exist_ok=True)
    rows = load(root)
    stats = per_condition(rows)
    pairs = paired(rows)
    (out / "report.json").write_text(json.dumps({"per_condition": stats, "paired": pairs}, indent=1))
    head, body, md = summary_table(stats, pairs)
    (out / "summary_table.md").write_text(md)
    fig_table(head, body, out / "summary_table.png")
    fig_learning(rows, out / "learning_curves.png")
    fig_mission(stats, out / "mission_hits.png")
    fig_false_discovery(stats, out / "false_discovery.png")
    if args.readme:
        fill_readme(Path(args.readme), md, root)
    print(md)
    print(f"figures in {out}")


if __name__ == "__main__":
    main()
