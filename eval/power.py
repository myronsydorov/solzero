"""Smallest detectable difference in mission hit rate for a given number of test worlds.

    python -m eval.power calibration/results/dev60 --sizes 40,20,12 [--throughput runs/pilot/report/report.json]

Uses the 60 dev-world scripted runs as a proxy for the paired lab-versus-random comparison.
Per world, realized hits are redrawn as five Bernoulli shots from each target's hit
probability, so single-shot luck is included. Two estimates per size n:
- analytic MDE = (z_0.975 + z_0.8) * sd(paired difference) / sqrt(n)
- simulated power to detect the observed dev-world difference with the report's paired bootstrap.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

Z = 1.959964 + 0.841621  # two-sided alpha 0.05, power 0.80


def load_pairs(run: Path) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Per world, per target hit probabilities: (worlds, 5) for random and greedy."""
    by = {}
    for p in run.glob("stageB_*.json"):
        b = json.loads(p.read_text())
        by[(b["policy"], b["seed"])] = [t["hit_prob"] for t in sorted(b["mission_selected"], key=lambda t: t["target_id"])]
    seeds = sorted(s for (pol, s) in by if pol == "random" and ("greedy", s) in by)
    return (np.array([by[("random", s)] for s in seeds]), np.array([by[("greedy", s)] for s in seeds]), seeds)


def realized(p: np.ndarray, rng) -> np.ndarray:
    return (rng.random(p.shape) < p).mean(-1)


def mde_analytic(pr, pg, n, rng, draws=2000) -> dict:
    sds, means = [], []
    for _ in range(draws):
        d = realized(pg, rng) - realized(pr, rng)
        sds.append(d.std(ddof=1))
        means.append(d.mean())
    sd = float(np.mean(sds))
    return {"sd_paired_diff": round(sd, 4), "mde": round(Z * sd / math.sqrt(n), 4),
            "observed_diff": round(float(np.mean(means)), 4)}


def power_sim(pr, pg, n, rng, sims=1000, boots=1000) -> float:
    """Share of simulated n-world studies whose 95% paired bootstrap CI excludes zero."""
    hits = 0
    for _ in range(sims):
        idx = rng.integers(0, len(pr), n)
        d = realized(pg[idx], rng) - realized(pr[idx], rng)
        means = d[rng.integers(0, n, (boots, n))].mean(1)
        lo, hi = np.percentile(means, [2.5, 97.5])
        hits += lo > 0 or hi < 0
    return hits / sims


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", nargs="?", default="calibration/results/dev60")
    ap.add_argument("--sizes", default="40,20,12")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out")
    args = ap.parse_args()
    run = Path(args.run)
    pr, pg, seeds = load_pairs(run)
    rng = np.random.default_rng(args.seed)
    rows = []
    for n in [int(x) for x in args.sizes.split(",")]:
        a = mde_analytic(pr, pg, n, rng)
        rows.append({"n_worlds": n, **a, "power_at_observed_diff": round(power_sim(pr, pg, n, rng), 3)})
    result = {"source": str(run), "n_dev_worlds": len(seeds),
              "expected_hit_rate": {"random": round(float(pr.mean()), 4), "greedy": round(float(pg.mean()), 4)},
              "method": __doc__.strip().splitlines()[2:], "sizes": rows}
    out = Path(args.out or run / "power.json")
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
