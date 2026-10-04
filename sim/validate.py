"""Validation of the MuJoCo lab on dev worlds (SPEC 3 "Robot execution").

S2 robustness: randomized experiments in full mode (arm primitives), failure rate by cause,
plus the noiseless deviation from the shared integrator.

S3 equivalence: experiments in full mode against the server path (World.run_experiment),
both fed the same random stream, so the difference is the physics alone. Reported in units
of each observable's measurement sd.

    .venv/bin/python -m sim.validate robustness --seeds 1000-1009 --per-world 5 --out sim/results/robustness.json
    .venv/bin/python -m sim.validate equivalence --seeds 1000-1003 --per-world 5 --out sim/results/equivalence.json
"""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from eval.sampler import random_spec
from schemas import OBSERVABLES
from tools.analysis import compile_law, simulate_specs
from tools.defaults import NOISE_SD
from world.generator import DEV_SEEDS, MASSES, make_world

from .experiment import Lab

SD_KEY = {"force_n": "force_frac", "fall_time_s": "fall_time_s", "landing_x_m": "landing_x_m",
          "flight_time_s": "flight_time_s"}


def _sd(name: str, value: float) -> float:
    sd = NOISE_SD[SD_KEY[name]]
    return sd * abs(value) if name == "force_n" else sd


def _specs(seed: int, n: int, stream: int):
    rng = np.random.default_rng([seed, stream])
    return [random_spec(rng) for _ in range(n)]


def robustness_world(args) -> list[dict]:
    seed, n = args
    w = make_world(seed)
    lab = Lab(w)
    cl = compile_law(w.law)
    rows = []
    for i, spec in enumerate(_specs(seed, n, 40)):
        ref = simulate_specs(cl, w.theta, [spec], MASSES)[0, 0]
        t0 = time.perf_counter()
        lab.release_error = None
        try:
            obs, failure = lab.measure(spec, "full"), None
        except Exception as exc:  # PrimitiveError, or anything else the scene throws
            obs, failure = None, f"{type(exc).__name__}: {exc}"
        dev = {}
        if obs:
            for k, name in enumerate(OBSERVABLES[spec.type]):
                dev[name] = (obs[name] - ref[k]) / _sd(name, ref[k]) if math.isfinite(ref[k]) else None
        rows.append({"seed": seed, "i": i, "spec": spec.model_dump(), "failure": failure,
                     "observables": obs, "integrator": ref[:len(OBSERVABLES[spec.type])].tolist(),
                     "deviation_sd": dev, "release_error": lab.release_error,
                     "sim_time_s": lab.sc.data.time, "wall_s": time.perf_counter() - t0})
    return rows


def equivalence_world(args) -> list[dict]:
    seed, n = args
    w = make_world(seed)
    lab = Lab(w)
    rows = []
    for i, spec in enumerate(_specs(seed, n, 50)):
        r_srv = w.run_experiment(spec, np.random.default_rng([seed, 50, i]), index=i + 1)
        r_sim = lab.run_experiment(spec, np.random.default_rng([seed, 50, i]), index=i + 1, mode="full")
        z = {}
        for name, v in r_srv.observables.items():
            u = r_sim.observables[name]
            z[name] = (u - v) / _sd(name, v) if math.isfinite(u) and math.isfinite(v) else None
        rows.append({"seed": seed, "family": w.family, "i": i, "spec": spec.model_dump(),
                     "server": r_srv.observables, "sim_full": r_sim.observables,
                     "status": [r_srv.status, r_sim.status], "failure": lab.failure, "diff_sd": z})
    return rows


def parse_seeds(text: str) -> list[int]:
    seeds = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        seeds += list(range(int(a), int(b or a) + 1))
    bad = [s for s in seeds if s not in DEV_SEEDS]
    if bad:
        raise SystemExit(f"sim validation runs on dev seeds 1000-1999 only; refused {bad}")
    return seeds


def summarize_robustness(rows) -> dict:
    fails = [r for r in rows if r["failure"]]
    by_cause: dict[str, int] = {}
    for r in fails:
        cause = r["failure"].split(":")[1].strip().split(" ")[0:3]
        by_cause[" ".join(cause)] = by_cause.get(" ".join(cause), 0) + 1
    devs = [abs(v) for r in rows for v in (r["deviation_sd"] or {}).values() if v is not None]
    by_type = {}
    for t in ("weigh", "drop", "launch"):
        rs = [r for r in rows if r["spec"]["type"] == t]
        by_type[t] = {"n": len(rs), "failed": sum(1 for r in rs if r["failure"])}
    return {"n": len(rows), "failed": len(fails), "failure_rate": len(fails) / max(1, len(rows)),
            "by_type": by_type, "by_cause": by_cause,
            "max_abs_deviation_sd": max(devs, default=None),
            "median_abs_deviation_sd": float(np.median(devs)) if devs else None,
            "mean_sim_time_s": float(np.mean([r["sim_time_s"] for r in rows])),
            "mean_wall_s": float(np.mean([r["wall_s"] for r in rows]))}


def summarize_equivalence(rows) -> dict:
    z = [abs(v) for r in rows for v in r["diff_sd"].values() if v is not None]
    per_obs = {}
    for name in SD_KEY:
        vals = [r["diff_sd"][name] for r in rows if r["diff_sd"].get(name) is not None]
        if vals:
            per_obs[name] = {"n": len(vals), "max_abs_sd": max(abs(v) for v in vals),
                             "mean_sd": float(np.mean(vals))}
    return {"n": len(rows), "failed": sum(1 for r in rows if r["status"][1] != "ok"),
            "status_mismatch": sum(1 for r in rows if r["status"][0] != r["status"][1]),
            "max_abs_diff_sd": max(z, default=None), "per_observable": per_obs,
            "within_noise": all(v < 1.0 for v in z)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("task", choices=("robustness", "equivalence"))
    ap.add_argument("--seeds", default="1000-1009")
    ap.add_argument("--per-world", type=int, default=5)
    ap.add_argument("--jobs", type=int, default=10)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    seeds = parse_seeds(args.seeds)
    fn = robustness_world if args.task == "robustness" else equivalence_world
    t0 = time.perf_counter()
    with ProcessPoolExecutor(min(args.jobs, len(seeds))) as ex:
        rows = [r for rs in ex.map(fn, [(s, args.per_world) for s in seeds]) for r in rs]
    summary = (summarize_robustness if args.task == "robustness" else summarize_equivalence)(rows)
    summary["wall_s_total"] = time.perf_counter() - t0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"task": args.task, "seeds": seeds, "summary": summary, "rows": rows},
                              indent=1, default=float))
    print(json.dumps(summary, indent=1, default=float))


if __name__ == "__main__":
    main()
