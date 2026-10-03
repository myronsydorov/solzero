"""Analysis tools from SPEC.md section 4 and 5.4: simulate, fit_law, predict, disagreement,
plan_shot. Pure functions; none of them calls the world server.

Noise model. Weigh force has a fractional sd, drop and launch observables an absolute sd.
Launches also suffer actuation error (speed_frac, elevation_deg); its effect on the
observables is propagated by finite-difference sensitivities and added in quadrature.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import least_squares

from schemas import (
    OBSERVABLES, Disagreement, DisagreementPair, FitResult, LaunchSpec, Law,
    ObservableEstimate, ParamEstimate, Prediction, Result, Sample, ShotPlan, Target,
)

from .defaults import LAUNCHER, NOISE_SD, SAMPLES
from .integrator import CompiledLaw, integrate

_COMPILED: dict[str, CompiledLaw] = {}


def compile_law(law: Law) -> CompiledLaw:
    key = law.model_dump_json(include={"ax", "az", "params"})
    if key not in _COMPILED:
        _COMPILED[key] = CompiledLaw(law)
    return _COMPILED[key]


def _masses(samples: list[Sample] | None) -> dict[str, float]:
    return {s.sample_id: s.mass_kg for s in (samples or SAMPLES)}


# --- Forward model --------------------------------------------------------------


def simulate_specs(cl: CompiledLaw, theta: np.ndarray, specs, masses: dict[str, float],
                   dv_frac=None, dtheta_deg=None, z_stop=None) -> np.ndarray:
    """Observables for every (parameter row, spec) pair in one batched integration.

    theta: (K, P). Returns (K, n_specs, 2): weigh -> [force, nan], drop -> [fall_time, nan],
    launch -> [landing_x (or x at z_stop), flight_time]. dv_frac and dtheta_deg are optional
    (K, n_specs) actuation perturbations; z_stop optionally overrides the launch stop height.
    """
    theta = np.atleast_2d(np.asarray(theta, float))
    K, n = theta.shape[0], len(specs)
    out = np.full((K, n, 2), np.nan)
    m = np.array([masses[s.sample_id] for s in specs])
    kind = np.array([s.type for s in specs])
    zs = np.zeros(n) if z_stop is None else np.broadcast_to(np.asarray(z_stop, float), (n,))

    w = np.nonzero(kind == "weigh")[0]
    if w.size:
        h = np.array([specs[i].height_m for i in w])
        params = [np.repeat(theta[:, j:j + 1], w.size, 1) for j in range(theta.shape[1])]
        out[:, w, 0] = cl.static_force(np.tile(m[w], (K, 1)), np.tile(h, (K, 1)), params)

    t = np.nonzero(kind != "weigh")[0]
    if t.size:
        x0 = np.zeros((K, t.size))
        z0 = np.empty(t.size)
        v = np.zeros(t.size)
        el = np.zeros(t.size)
        for j, i in enumerate(t):
            s = specs[i]
            if s.type == "drop":
                z0[j] = s.height_m
            else:
                x0[:, j] = LAUNCHER.x_m
                z0[j] = LAUNCHER.z_m
                v[j], el[j] = s.speed_mps, s.elevation_deg
        V = np.tile(v, (K, 1))
        E = np.tile(el, (K, 1))
        if dv_frac is not None:
            V = V * (1 + np.asarray(dv_frac)[:, t])
        if dtheta_deg is not None:
            E = E + np.asarray(dtheta_deg)[:, t]
        E = np.radians(E)
        params = [np.repeat(theta[:, j], t.size) for j in range(theta.shape[1])]
        x, tt = integrate(cl, np.tile(m[t], K), x0.ravel(), np.tile(z0, K),
                          (V * np.cos(E)).ravel(), (V * np.sin(E)).ravel(),
                          np.tile(zs[t], K), params)
        x, tt = x.reshape(K, t.size), tt.reshape(K, t.size)
        is_drop = kind[t] == "drop"
        out[:, t, 0] = np.where(is_drop, tt, x)
        out[:, t, 1] = np.where(is_drop, np.nan, tt)
    return out


def simulate(law_fn: CompiledLaw | Law, params: dict[str, float], spec, sample: Sample) -> dict:
    """Noiseless observables of one experiment under one law (SPEC 5.4)."""
    cl = law_fn if isinstance(law_fn, CompiledLaw) else compile_law(law_fn)
    theta = np.array([[params[n] for n in cl.param_names]])
    obs = simulate_specs(cl, theta, [spec], {sample.sample_id: sample.mass_kg})[0, 0]
    return {name: float(obs[k]) for k, name in enumerate(OBSERVABLES[spec.type])}


def _sensor_sd(spec, value: np.ndarray, noise: dict) -> np.ndarray:
    """Sensor sd per observable column for one spec; value is (..., 2)."""
    if spec.type == "weigh":
        return np.stack([noise["force_frac"] * np.abs(value[..., 0]), np.full(value.shape[:-1], np.nan)], -1)
    if spec.type == "drop":
        return np.stack([np.full(value.shape[:-1], noise["fall_time_s"]), np.full(value.shape[:-1], np.nan)], -1)
    return np.stack([np.full(value.shape[:-1], noise["landing_x_m"]),
                     np.full(value.shape[:-1], noise["flight_time_s"])], -1)


def effective_sd(cl: CompiledLaw, theta: np.ndarray, specs, masses, noise: dict) -> np.ndarray:
    """Measurement sd (sensor plus linearised actuation error) per (spec, column), at theta."""
    theta = np.atleast_2d(theta)[:1]
    n = len(specs)
    base = simulate_specs(cl, theta, specs, masses)[0]
    sd = np.stack([_sensor_sd(s, base[i], noise) for i, s in enumerate(specs)])
    launch = [i for i, s in enumerate(specs) if s.type == "launch"]
    if launch and (noise.get("speed_frac", 0) or noise.get("elevation_deg", 0)):
        dv, dth = 1e-3, 0.05
        pert_v = np.zeros((2, n))
        pert_t = np.zeros((2, n))
        pert_v[0] = dv
        pert_t[1] = dth
        p = simulate_specs(cl, np.repeat(theta, 2, 0), specs, masses, pert_v, pert_t)
        dv_sens = (p[0] - base) / dv  # per unit fractional speed
        dt_sens = (p[1] - base) / dth  # per degree
        act = np.hypot(dv_sens * noise.get("speed_frac", 0), dt_sens * noise.get("elevation_deg", 0))
        act = np.where(np.isfinite(act), act, 0.0)
        sd[launch] = np.hypot(sd[launch], act[launch])
    return sd


# --- Fitting --------------------------------------------------------------------


class _Problem:
    def __init__(self, law: Law, results: list[Result], masses: dict[str, float]):
        self.law = law
        self.cl = compile_law(law)
        self.names = list(law.params)
        self.lo = np.array([law.params[k].lo for k in self.names])
        self.hi = np.array([law.params[k].hi for k in self.names])
        self.results = [r for r in results if r.status == "ok"]
        self.specs = [r.spec for r in self.results]
        self.masses = masses
        obs = np.full((len(self.specs), 2), np.nan)
        for i, r in enumerate(self.results):
            for k, name in enumerate(OBSERVABLES[r.spec.type]):
                obs[i, k] = r.observables[name]
        self.obs = obs
        self.mask = np.isfinite(obs)
        noise = dict(NOISE_SD)
        for r in self.results:  # Result.noise_sd overrides the defaults where it speaks
            for k, val in r.noise_sd.items():
                if k in ("speed_frac", "elevation_deg", "fall_time_s", "landing_x_m", "flight_time_s"):
                    noise[k] = val
                elif k == "force_n" and r.observables.get("force_n"):
                    noise["force_frac"] = val / abs(r.observables["force_n"])
        self.noise = noise
        self.sd = np.ones_like(obs)

    def set_sd(self, theta):
        sd = effective_sd(self.cl, theta, self.specs, self.masses, self.noise)
        # Weigh sd is a fraction of the measured reading, not of the prediction.
        for i, s in enumerate(self.specs):
            if s.type == "weigh":
                sd[i, 0] = self.noise["force_frac"] * abs(self.obs[i, 0])
        self.sd = np.where(self.mask, np.maximum(sd, 1e-9), 1.0)

    def residuals(self, thetas, rows=None) -> np.ndarray:
        """(K, n_obs) normalised residuals; NaN predictions become a large penalty."""
        specs = self.specs if rows is None else [self.specs[i] for i in rows]
        pred = simulate_specs(self.cl, thetas, specs, self.masses)
        obs, sd, mask = (self.obs, self.sd, self.mask) if rows is None else (
            self.obs[rows], self.sd[rows], self.mask[rows])
        r = (pred - obs) / sd
        r = np.where(np.isfinite(r), r, 1e3)
        return r[:, mask]

    def solve(self, x0, rows=None, max_nfev=60):
        cache = {}
        span = self.hi - self.lo

        def both(x):
            key = x.tobytes()
            if key not in cache:
                h = 1e-5 * np.maximum(np.abs(x), 1e-3 * np.where(np.isfinite(span), span, 1.0))
                step = np.where(x + h > self.hi, -h, h)
                thetas = np.vstack([x, x + np.diag(step)])
                R = self.residuals(thetas, rows)
                cache.clear()
                cache[key] = (R[0], ((R[1:] - R[0]) / step[:, None]).T)
            return cache[key]

        x0 = np.clip(np.asarray(x0, float), self.lo, self.hi)
        return least_squares(lambda x: both(x)[0], x0, jac=lambda x: both(x)[1],
                             bounds=(self.lo, self.hi), x_scale="jac", method="trf",
                             max_nfev=max_nfev)


def fit_law(law: Law, results: list[Result], samples: list[Sample] | None = None, *,
            loo: bool = True, x0: dict[str, float] | None = None, n_starts: int = 1,
            seed: int = 0) -> FitResult:
    """Weighted least-squares fit of a free-form law to experiment results (SPEC 4).

    x0 warm-starts the fit; n_starts > 1 adds seeded random starts inside the bounds.
    chi2_dof is the weighted chi-square over max(1, n_obs - n_params); loo_error is the RMS
    leave-one-experiment-out prediction error in units of measurement noise.
    """
    prob = _Problem(law, results, _masses(samples))
    names = prob.names
    init = np.array([(x0 or {}).get(k, law.params[k].init) for k in names], float)
    starts = [init]
    rng = np.random.default_rng(seed)
    finite = np.isfinite(prob.lo) & np.isfinite(prob.hi)
    for _ in range(n_starts - 1):
        s = init.copy()
        s[finite] = rng.uniform(prob.lo[finite], prob.hi[finite])
        starts.append(s)

    best = None
    for s in starts:
        prob.set_sd(s)
        sol = prob.solve(s)
        prob.set_sd(sol.x)  # re-linearise actuation noise at the fitted parameters
        sol = prob.solve(sol.x)
        if best is None or sol.cost < best[0].cost:
            best = (sol, prob.sd.copy())
    sol, prob.sd = best
    n_obs = int(prob.mask.sum())
    dof = max(1, n_obs - len(names))
    chi2 = float(2 * sol.cost)
    chi2_dof = chi2 / dof
    J = sol.jac
    cov = np.linalg.pinv(J.T @ J) * max(1.0, chi2_dof) if J.size else np.zeros((0, 0))
    sds = np.sqrt(np.clip(np.diag(cov), 0, None))

    loo_err = None
    n_exp = len(prob.specs)
    if loo and n_exp >= 2:
        errs = []
        for i in range(n_exp):
            rows = [j for j in range(n_exp) if j != i]
            s_i = prob.solve(sol.x, rows=rows, max_nfev=20)
            r = prob.residuals(s_i.x[None, :], rows=[i])[0]
            errs.append(float(np.mean(r**2)))
        loo_err = float(math.sqrt(np.mean(errs)))

    return FitResult(
        law_id=law.law_id,
        params={k: ParamEstimate(value=float(sol.x[j]), sd=float(sds[j])) for j, k in enumerate(names)},
        chi2_dof=chi2_dof, loo_error=loo_err, n_experiments=n_exp,
        converged=bool(sol.success and np.isfinite(chi2)), cov=cov.tolist(),
    )


# --- Prediction -----------------------------------------------------------------


def _draw_params(law: Law, fit: FitResult, n: int, rng) -> np.ndarray:
    names = list(law.params)
    mean = np.array([fit.params[k].value for k in names])
    if n <= 0:
        return mean[None, :]
    if fit.cov is not None and len(fit.cov) == len(names):
        cov = np.array(fit.cov)
    else:
        cov = np.diag([fit.params[k].sd ** 2 for k in names])
    draws = rng.multivariate_normal(mean, cov, size=n, method="eigh", check_valid="ignore")
    lo = np.array([law.params[k].lo for k in names])
    hi = np.array([law.params[k].hi for k in names])
    return np.clip(draws, lo, hi)


def predict_many(law: Law, fit: FitResult, specs, n_draws: int = 200, *,
                 samples: list[Sample] | None = None, noise_sd: dict | None = None,
                 seed: int = 0) -> list[Prediction]:
    """predict() for many specs in one batched integration."""
    cl = compile_law(law)
    masses = _masses(samples)
    noise = {**NOISE_SD, **(noise_sd or {})}
    mean_theta = _draw_params(law, fit, 0, None)
    meas_sd = effective_sd(cl, mean_theta, specs, masses, noise)
    if n_draws <= 0:
        mu = simulate_specs(cl, mean_theta, specs, masses)[0]
        param_sd = np.zeros_like(mu)
    else:
        rng = np.random.default_rng(seed)
        draws = simulate_specs(cl, _draw_params(law, fit, n_draws, rng), specs, masses)
        with np.errstate(all="ignore"):
            mu = np.nanmean(draws, 0)
            param_sd = np.nanstd(draws, 0)
        never = np.isnan(draws).mean(0) > 0.5
        mu = np.where(never, np.nan, mu)
    sd = np.hypot(meas_sd, param_sd)
    out = []
    for i, s in enumerate(specs):
        out.append(Prediction(law_id=law.law_id, observables={
            name: ObservableEstimate(mean=float(mu[i, k]), sd=float(sd[i, k]))
            for k, name in enumerate(OBSERVABLES[s.type])}))
    return out


def predict(law: Law, fit: FitResult, spec, n_draws: int = 200, *,
            samples: list[Sample] | None = None, noise_sd: dict | None = None,
            seed: int = 0) -> Prediction:
    """Predictive distribution of one experiment's observables: mean over parameter draws,
    sd combining parameter spread with measurement noise. NaN mean: sample never lands."""
    return predict_many(law, fit, [spec], n_draws, samples=samples, noise_sd=noise_sd, seed=seed)[0]


def disagreement_many(specs, laws: list[tuple[Law, FitResult]], noise_sd: dict | None = None, *,
                      samples: list[Sample] | None = None, n_draws: int = 0,
                      seed: int = 0) -> list[Disagreement]:
    """disagreement() for many specs; one batched integration per law."""
    preds = [predict_many(l, f, specs, n_draws, samples=samples, noise_sd=noise_sd, seed=seed)
             for l, f in laws]
    out = []
    for i, s in enumerate(specs):
        pairs = []
        for a in range(len(laws)):
            for b in range(a + 1, len(laws)):
                pa, pb = preds[a][i].observables, preds[b][i].observables
                gap = 0.0
                for name in pa:
                    da, db = pa[name], pb[name]
                    if not (math.isfinite(da.mean) and math.isfinite(db.mean)):
                        g = 0.0 if math.isfinite(da.mean) == math.isfinite(db.mean) else 1e3
                    else:
                        g = abs(da.mean - db.mean) / math.sqrt(0.5 * (da.sd**2 + db.sd**2))
                    gap = max(gap, g)
                pairs.append(DisagreementPair(a=laws[a][0].law_id, b=laws[b][0].law_id, gap_sigma=gap))
        out.append(Disagreement(spec=s, pairs=pairs, max_gap_sigma=max((p.gap_sigma for p in pairs), default=0.0)))
    return out


def disagreement(spec, laws: list[tuple[Law, FitResult]], noise_sd: dict | None = None, **kw) -> Disagreement:
    """Pairwise gap between the laws' predictions for one experiment, in units of the
    predictive sd (measurement noise, plus parameter spread when n_draws > 0); the largest
    gap over observables. A pair where only one law predicts a landing scores 1000."""
    return disagreement_many([spec], laws, noise_sd, **kw)[0]


# --- Shot planning --------------------------------------------------------------


def plan_shot(law: Law, fit: FitResult, target: Target, sample_id: str, limits: dict, *,
              samples: list[Sample] | None = None, noise_sd: dict | None = None,
              n_draws: int = 100, n_elevations: int = 25, seed: int = 0) -> ShotPlan:
    """Launcher setting that brings sample_id down through (x_T, z_T) under the fitted law.

    For each elevation on a grid, the lowest speed that hits is found; among those, the
    setting with the smallest predicted miss spread (parameter draws plus actuation error)
    is returned. limits: {"speed_mps": (lo, hi), "elevation_deg": (lo, hi)}.

    reachable is False only when no setting inside the limits brings the nominal trajectory
    through the target; then the returned setting is the nominal closest approach and
    predicted_miss_sd_m is that nominal shortfall. Settings where more than 10% of draws
    fail to come down are used only when no other setting exists.
    """
    cl = compile_law(law)
    masses = _masses(samples)
    noise = {**NOISE_SD, **(noise_sd or {})}
    v_lo, v_hi = limits["speed_mps"]
    e_lo, e_hi = limits["elevation_deg"]
    theta = _draw_params(law, fit, 0, None)
    els = np.linspace(e_lo, e_hi, n_elevations)
    vs = np.linspace(v_lo, v_hi, 61)

    def landing(speed, elev, th=theta, dv=None, de=None):
        specs = [LaunchSpec(sample_id="ref_100", speed_mps=float(a), elevation_deg=float(b))
                 for a, b in zip(speed, elev)]
        ms = {"ref_100": masses[sample_id]}
        return simulate_specs(cl, th, specs, ms, dv, de, z_stop=target.z_m)[..., 0]

    E, V = np.meshgrid(els, vs, indexing="ij")
    X = landing(V.ravel(), E.ravel())[0].reshape(E.shape)
    Xf = np.where(np.isnan(X), -np.inf, X)
    cand_e, cand_v = [], []
    for i in range(len(els)):
        up = np.nonzero((Xf[i, :-1] < target.x_m) & (Xf[i, 1:] >= target.x_m))[0]
        if up.size:
            j = up[0]
            x0 = Xf[i, j]
            f = 0.0 if not np.isfinite(x0) else (target.x_m - x0) / (Xf[i, j + 1] - x0)
            cand_e.append(els[i])
            cand_v.append(vs[j] + f * (vs[j + 1] - vs[j]))
    if not cand_e:
        i, j = np.unravel_index(np.argmax(Xf), Xf.shape)
        gap = abs(target.x_m - X[i, j]) if np.isfinite(X[i, j]) else abs(target.x_m)
        return ShotPlan(law_id=law.law_id, target_id=target.target_id, sample_id=sample_id,
                        speed_mps=float(vs[j]), elevation_deg=float(els[i]),
                        predicted_miss_sd_m=float(gap), reachable=False)
    ce, cv = np.array(cand_e), np.array(cand_v)
    for _ in range(5):  # secant refinement of the speed at each elevation
        dv = 1e-3
        xa = landing(cv, ce)[0]
        xb = landing(cv + dv, ce)[0]
        slope = (xb - xa) / dv
        ok = np.isfinite(xa) & np.isfinite(slope) & (slope > 0)
        cv = np.where(ok, np.clip(cv + (target.x_m - xa) / np.where(ok, slope, 1), v_lo, v_hi), cv)
    x_hit = landing(cv, ce)[0]
    err = np.where(np.isfinite(x_hit), np.abs(x_hit - target.x_m), np.inf)
    good = err < 0.005
    if not good.any():  # the grid brackets a solution; keep the closest refined settings
        good = err <= np.min(err) + 1e-9
    ce, cv = ce[good], cv[good]

    rng = np.random.default_rng(seed)
    nd = max(n_draws, 1)
    th = _draw_params(law, fit, n_draws, rng) if n_draws > 0 else np.repeat(theta, nd, 0)
    n_c = len(ce)
    dvf = rng.normal(0, noise.get("speed_frac", 0), (nd, n_c))
    dth = rng.normal(0, noise.get("elevation_deg", 0), (nd, n_c))
    xs = landing(cv, ce, th, dvf, dth)
    landed = np.isfinite(xs)
    with np.errstate(all="ignore"):
        miss = np.sqrt(np.nanmean((xs - target.x_m) ** 2, 0))
    miss = np.where(landed.any(0), miss, 1e3)
    risky = (~landed).mean(0) > 0.1
    k = int(np.lexsort((miss, risky))[0])
    return ShotPlan(law_id=law.law_id, target_id=target.target_id, sample_id=sample_id,
                    speed_mps=float(cv[k]), elevation_deg=float(ce[k]),
                    predicted_miss_sd_m=float(miss[k]), reachable=True)
