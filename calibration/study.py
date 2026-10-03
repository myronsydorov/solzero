"""Calibration study (SPEC.md section 8). Scripted policies only, no language model.

Stage A (ceiling): fit the true functional form to 12 well-spread experiments, plan the
five mission shots, measure hits. Diagnostics: noiseless fit, shots planned with the exact
truth, and the best wrong form on the beyond-range targets.

Stage B (headroom): random versus greedy-disagreement experiment selection, sharing the
same fitting and BIC model selection over the 12-form library.

    python -m calibration.study --seeds 1000-1004 --out calibration/results/dev5 --jobs 10

Raw per-world JSON goes to --out; summarise with calibration.report.
"""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from schemas import DropSpec, LaunchSpec, WeighSpec
from tools.analysis import disagreement_many, fit_law, plan_shot, simulate_specs, compile_law
from tools.defaults import HIT_RADIUS_M, NOISE_SD, RANGES, SAMPLES
from world.generator import DEV_SEEDS, MASSES, World, make_world

from .library import is_ordinary, library, true_form

BUDGET = 12
N_CANDIDATES = 60
N_HIT_DRAWS = 400
TOP_K = 3

REF_IDS = [s.sample_id for s in SAMPLES if s.launchable]
ALL_IDS = [s.sample_id for s in SAMPLES]

# Stage A: a hand-spread design covering every experiment type, mass and range.
SPREAD_DESIGN = [
    WeighSpec(sample_id="ref_020", height_m=0.0),
    WeighSpec(sample_id="ref_800", height_m=1.2),
    WeighSpec(sample_id="mission_300", height_m=0.6),
    DropSpec(sample_id="ref_020", height_m=1.2),
    DropSpec(sample_id="ref_800", height_m=1.2),
    DropSpec(sample_id="ref_100", height_m=0.5),
    LaunchSpec(sample_id="ref_020", speed_mps=4.0, elevation_deg=45.0),
    LaunchSpec(sample_id="ref_200", speed_mps=4.0, elevation_deg=30.0),
    LaunchSpec(sample_id="ref_800", speed_mps=3.0, elevation_deg=60.0),
    LaunchSpec(sample_id="ref_050", speed_mps=2.0, elevation_deg=30.0),
    LaunchSpec(sample_id="ref_400", speed_mps=4.0, elevation_deg=70.0),
    LaunchSpec(sample_id="ref_100", speed_mps=1.5, elevation_deg=50.0),
]

# Stage B greedy: three fixed seed experiments, one of each type.
GREEDY_SEED_DESIGN = [
    WeighSpec(sample_id="ref_100", height_m=0.6),
    DropSpec(sample_id="ref_100", height_m=1.0),
    LaunchSpec(sample_id="ref_100", speed_mps=3.0, elevation_deg=45.0),
]

ZERO_NOISE = {k: 0.0 for k in NOISE_SD}

# Diagnostic only: hit probability of the same planned shot under smaller actuation error
# (speed_frac, elevation_deg). The design noise stays NOISE_SD; see STATUS.md.
ACTUATION_SWEEP = [(0.02, 0.5), (0.01, 0.25), (0.005, 0.1), (0.0, 0.0)]


def random_spec(rng: np.random.Generator):
    """Uniform over experiment type, then uniform over that type's parameters."""
    kind = rng.choice(["weigh", "drop", "launch"])
    if kind == "weigh":
        return WeighSpec(sample_id=str(rng.choice(ALL_IDS)),
                         height_m=float(rng.uniform(*RANGES["weigh"]["height_m"])))
    if kind == "drop":
        return DropSpec(sample_id=str(rng.choice(REF_IDS)),
                        height_m=float(rng.uniform(*RANGES["drop"]["height_m"])))
    return LaunchSpec(sample_id=str(rng.choice(REF_IDS)),
                      speed_mps=float(rng.uniform(*RANGES["launch"]["speed_mps"])),
                      elevation_deg=float(rng.uniform(*RANGES["launch"]["elevation_deg"])))


def n_obs(results) -> int:
    return sum(2 if r.spec.type == "launch" else 1 for r in results if r.status == "ok")


def bic(fit, law, results) -> float:
    n = n_obs(results)
    k = len(law.params)
    chi2 = fit.chi2_dof * max(1, n - k)
    return chi2 + k * math.log(max(n, 2))


def probe_errors(w: World, law, fit) -> np.ndarray:
    theta = np.array([[fit.params[k].value for k in law.params]])
    x = simulate_specs(compile_law(law), theta, w.probes, MASSES)[0, :, 0]
    err = np.abs(x - w.probe_x)
    return np.where(np.isfinite(err), err, np.inf)


def mission(w: World, law, fit, seed: int) -> list[dict]:
    """Plan the five shots with (law, fit) and score them against the hidden law."""
    rng = np.random.default_rng([seed, 99])
    out = []
    for t in w.targets:
        plan = plan_shot(law, fit, t, "mission_300", RANGES["mission"], n_draws=100, seed=seed)
        x_nom = w.shot_x("mission_300", [plan.speed_mps], [plan.elevation_deg], t.z_m)[0, 0]
        dv = rng.normal(0, NOISE_SD["speed_frac"], (N_HIT_DRAWS, 1))
        de = rng.normal(0, NOISE_SD["elevation_deg"], (N_HIT_DRAWS, 1))
        xs = w.shot_x("mission_300", [plan.speed_mps], [plan.elevation_deg], t.z_m, dv, de)[:, 0]
        miss = np.abs(xs - t.x_m)
        miss = np.where(np.isfinite(miss), miss, np.inf)
        sweep = {}
        for sf, ang in ACTUATION_SWEEP:
            z = rng.standard_normal((2, N_HIT_DRAWS, 1))
            xa = w.shot_x("mission_300", [plan.speed_mps], [plan.elevation_deg], t.z_m,
                          sf * z[0], ang * z[1])[:, 0]
            ma = np.abs(xa - t.x_m)
            sweep[f"{sf}/{ang}"] = float(np.mean(np.where(np.isfinite(ma), ma, np.inf) <= HIT_RADIUS_M))
        out.append({
            "target_id": t.target_id, "kind": w.target_kind[t.target_id], "x_m": t.x_m, "z_m": t.z_m,
            "speed_mps": plan.speed_mps, "elevation_deg": plan.elevation_deg,
            "reachable_under_law": plan.reachable, "predicted_miss_sd_m": plan.predicted_miss_sd_m,
            "nominal_miss_m": float(abs(x_nom - t.x_m)) if np.isfinite(x_nom) else None,
            "nominal_hit": bool(np.isfinite(x_nom) and abs(x_nom - t.x_m) <= HIT_RADIUS_M),
            "hit_prob": float(np.mean(miss <= HIT_RADIUS_M)),
            "realized_hit": bool(miss[0] <= HIT_RADIUS_M),
            "median_miss_m": float(np.median(miss)),
            "hit_prob_actuation_sweep": sweep,
        })
    return out


def fit_all(laws, results, warm=None):
    fits = {}
    for fid, law in laws.items():
        x0 = {k: v.value for k, v in warm[fid].params.items()} if warm and fid in warm else None
        fits[fid] = fit_law(law, results, loo=False, x0=x0)
    return fits


# --- Stage A ----------------------------------------------------------------------


def stage_a(seed: int) -> dict:
    t0 = time.perf_counter()
    w = make_world(seed)
    laws = library()
    tf = true_form(w.family, w.p)
    rng = np.random.default_rng([seed, 1])
    results = [w.shot_zero_result] + [
        w.run_experiment(s, rng, i + 1, BUDGET - i - 1) for i, s in enumerate(SPREAD_DESIGN)]
    # Noiseless data, weighted with the nominal noise so chi2 stays interpretable.
    clean = [w.shot_zero_result] + [
        w.run_experiment(s, rng, i + 1, 0, noise=ZERO_NOISE).model_copy(update={"noise_sd": r.noise_sd})
        for i, (s, r) in enumerate(zip(SPREAD_DESIGN, results[1:]))]

    fits = fit_all(laws, results)
    fit_true = fits[tf]
    fit_clean = fit_law(laws[tf], clean, loo=False)
    scores = {fid: bic(f, laws[fid], results) for fid, f in fits.items()}
    wrong = min((fid for fid in fits if fid != tf), key=scores.get)

    from world.generator import fixed_law  # exact truth as a law, for the planning diagnostic
    tl, tfit = fixed_law("truth_form", *_truth_exprs(laws[tf]), _truth_values(w, laws[tf]))
    return {
        "seed": seed, "truth": w.truth(), "true_form": tf, "shot_zero_miss_m": w.shot_zero.miss_m,
        "fit_true": fit_true.model_dump(), "fit_true_noiseless": fit_clean.model_dump(),
        "bic": scores, "best_wrong_form": wrong, "selected_form": min(scores, key=scores.get),
        "probe_median_true_fit": float(np.median(probe_errors(w, laws[tf], fit_true))),
        "probe_median_true_fit_noiseless": float(np.median(probe_errors(w, laws[tf], fit_clean))),
        "probe_median_best_wrong": float(np.median(probe_errors(w, laws[wrong], fits[wrong]))),
        "mission_true_fit": mission(w, laws[tf], fit_true, seed),
        "mission_exact_truth": mission(w, tl, tfit, seed),
        "mission_best_wrong": mission(w, laws[wrong], fits[wrong], seed),
        "runtime_s": time.perf_counter() - t0,
    }


def _truth_exprs(law):
    return law.ax, law.az


def _truth_values(w: World, law) -> dict:
    vals = {"g0": w.g0, "c": w.c, "alpha": w.alpha, "kappa": w.kappa}
    return {k: float(vals[k]) for k in law.params}


# --- Stage B ----------------------------------------------------------------------


def stage_b(seed: int, policy: str) -> dict:
    t0 = time.perf_counter()
    w = make_world(seed)
    laws = library()
    tf = true_form(w.family, w.p)
    pid = {"random": 1, "greedy": 2}[policy]
    rng_policy = np.random.default_rng([seed, 10 + pid])
    rng_noise = np.random.default_rng([seed, 20 + pid])
    results = [w.shot_zero_result]
    fits = fit_all(laws, results)
    steps = [_record(0, None, w, laws, fits, results, tf)]
    for i in range(1, BUDGET + 1):
        if policy == "random":
            spec = random_spec(rng_policy)
        elif i <= len(GREEDY_SEED_DESIGN):
            spec = GREEDY_SEED_DESIGN[i - 1]
        else:
            scores = {fid: bic(f, laws[fid], results) for fid, f in fits.items()}
            top = sorted(scores, key=scores.get)[:TOP_K]
            cands = [random_spec(rng_policy) for _ in range(N_CANDIDATES)]
            dis = disagreement_many(cands, [(laws[f], fits[f]) for f in top])
            gaps = [sum(p.gap_sigma for p in d.pairs) for d in dis]
            spec = cands[int(np.argmax(gaps))]
        results.append(w.run_experiment(spec, rng_noise, i, BUDGET - i))
        fits = fit_all(laws, results, warm=fits)
        steps.append(_record(i, spec, w, laws, fits, results, tf))
    sel = steps[-1]["selected_form"]
    return {
        "seed": seed, "policy": policy, "truth": w.truth(), "true_form": tf, "steps": steps,
        "final_form": sel, "claims_non_ordinary": not is_ordinary(sel),
        "final_fit": fits[sel].model_dump(),
        "mission_selected": mission(w, laws[sel], fits[sel], seed),
        "mission_true_form": mission(w, laws[tf], fits[tf], seed),
        "runtime_s": time.perf_counter() - t0,
    }


def _record(i, spec, w, laws, fits, results, tf) -> dict:
    scores = {fid: bic(f, laws[fid], results) for fid, f in fits.items()}
    sel = min(scores, key=scores.get)
    probe = {fid: float(np.median(probe_errors(w, laws[fid], f))) for fid, f in fits.items()}
    r = results[-1]
    return {
        "step": i, "spec": spec.model_dump() if spec else None,
        "observables": r.observables, "status": r.status,
        "bic": scores, "probe_median": probe, "selected_form": sel,
        "probe_median_selected": probe[sel], "probe_median_true_form": probe[tf],
    }


# --- CLI --------------------------------------------------------------------------


def parse_seeds(text: str) -> list[int]:
    seeds = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        seeds += list(range(int(a), int(b or a) + 1))
    bad = [s for s in seeds if s not in DEV_SEEDS]
    if bad:
        raise SystemExit(f"calibration runs on dev seeds 1000-1999 only; refused {bad}")
    return seeds


def _run(task):
    kind, seed, policy = task
    return task, (stage_a(seed) if kind == "A" else stage_b(seed, policy))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1000-1004")
    ap.add_argument("--out", default="calibration/results/dev5")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--stages", default="AB")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    seeds = parse_seeds(args.seeds)
    tasks = []
    for s in seeds:
        if "A" in args.stages:
            tasks.append(("A", s, None))
        if "B" in args.stages:
            tasks += [("B", s, "random"), ("B", s, "greedy")]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(args.jobs) as ex:
        futs = [ex.submit(_run, t) for t in tasks]
        for f in as_completed(futs):
            (kind, seed, policy), res = f.result()
            name = f"stageA_{seed}.json" if kind == "A" else f"stageB_{seed}_{policy}.json"
            (out / name).write_text(json.dumps(res, indent=1, default=float))
            print(f"[{time.perf_counter() - t0:7.1f}s] {name} ({res['runtime_s']:.1f}s)", flush=True)
    (out / "run.json").write_text(json.dumps({
        "seeds": seeds, "stages": args.stages, "wall_s": time.perf_counter() - t0,
        "budget": BUDGET, "n_candidates": N_CANDIDATES, "noise_sd": NOISE_SD}, indent=1))


if __name__ == "__main__":
    main()
