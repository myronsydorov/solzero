"""Hidden world generator (SPEC.md section 2 and 3). Never visible to the scientific agents.

A world is fully determined by its integer seed. Dev seeds are 1000-1999. Test seeds
(9000-9999) are refused unless the caller passes final_eval=True, which only the
`--final-eval` command-line flag may set.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from schemas import FitResult, LaunchSpec, Law, ParamEstimate, Result, ShotZero, Target
from tools.analysis import compile_law, plan_shot, simulate_specs
from tools.defaults import NOISE_SD, RANGES, SAMPLES

FAMILIES = ("F0", "F1", "F2", "F3")
TEST_SEEDS = range(9000, 10000)
DEV_SEEDS = range(1000, 2000)

# Earth textbook physics for shot zero: g = 9.81, quadratic drag of a 2 cm sphere in air
# (0.5 * rho_air 1.2 * Cd 0.47 * pi r^2).
TEXTBOOK_C = 0.5 * 1.2 * 0.47 * math.pi * 0.02**2
TEXTBOOK_G = 9.81

N_PROBES = 20
MASSES = {s.sample_id: s.mass_kg for s in SAMPLES}


def law_expr(p: float) -> tuple[str, str]:
    """Acceleration expressions of the hidden family law, drag exponent p substituted."""
    drag = "c*vx/m" if p == 1 else f"c*speed**{p - 1:g}*vx/m"
    dragz = "c*vz/m" if p == 1 else f"c*speed**{p - 1:g}*vz/m"
    return f"-{drag}", f"-g0*(m/0.1)**alpha*(1 + kappa*z) - {dragz}"


def fixed_law(law_id: str, ax: str, az: str, values: dict[str, float]) -> tuple[Law, FitResult]:
    """A law with exact parameter values, as a (Law, FitResult) pair for the analysis tools."""
    law = Law(law_id=law_id, ax=ax, az=az,
              params={k: {"init": v, "lo": v, "hi": v} for k, v in values.items()})
    fit = FitResult(law_id=law_id, params={k: ParamEstimate(value=v, sd=0.0) for k, v in values.items()},
                    chi2_dof=0.0, n_experiments=0, converged=True)
    return law, fit


def textbook_law() -> tuple[Law, FitResult]:
    return fixed_law("textbook", "-c*speed*vx/m", "-g0 - c*speed*vz/m",
                     {"g0": TEXTBOOK_G, "c": TEXTBOOK_C})


@dataclass
class World:
    seed: int
    world_id: str
    family: str
    p: float
    g0: float
    rho: float
    alpha: float
    kappa: float
    c: float
    law: Law = field(repr=False)
    fit: FitResult = field(repr=False)
    targets: list[Target] = field(default_factory=list)
    target_kind: dict[str, str] = field(default_factory=dict)  # "in_range" | "beyond"
    probes: list[LaunchSpec] = field(default_factory=list)
    probe_x: np.ndarray | None = field(default=None, repr=False)
    shot_zero: ShotZero | None = None
    shot_zero_result: Result | None = None

    def truth(self) -> dict:
        return {"world_id": self.world_id, "family": self.family, **{
            k: float(getattr(self, k)) for k in ("p", "g0", "rho", "alpha", "kappa", "c")}}

    @property
    def theta(self) -> np.ndarray:
        return np.array([[self.fit.params[k].value for k in self.law.params]])

    # --- experiments ---------------------------------------------------------

    def run_experiment(self, spec, rng: np.random.Generator, index: int = 0,
                       budget_left: int = 0, noise: dict | None = None) -> Result:
        """Execute one experiment under the hidden law with sensor and actuation noise."""
        noise = noise or NOISE_SD
        cl = compile_law(self.law)
        dv = de = None
        if spec.type == "launch":
            dv = np.array([[rng.normal(0, noise["speed_frac"])]])
            de = np.array([[rng.normal(0, noise["elevation_deg"])]])
        obs = simulate_specs(cl, self.theta, [spec], MASSES, dv, de)[0, 0]
        if spec.type == "weigh":
            f = obs[0]
            vals = {"force_n": f * (1 + rng.normal(0, noise["force_frac"]))}
            sd = {"force_n": noise["force_frac"] * abs(f)}
        elif spec.type == "drop":
            vals = {"fall_time_s": obs[0] + rng.normal(0, noise["fall_time_s"])}
            sd = {"fall_time_s": noise["fall_time_s"]}
        else:
            vals = {"landing_x_m": obs[0] + rng.normal(0, noise["landing_x_m"]),
                    "flight_time_s": obs[1] + rng.normal(0, noise["flight_time_s"])}
            sd = {k: noise[k] for k in ("landing_x_m", "flight_time_s", "speed_frac", "elevation_deg")}
        ok = all(np.isfinite(v) for v in vals.values())
        if not ok:
            vals = {k: float("nan") for k in vals}
        return Result(experiment_id=f"e{index:02d}", index=index, spec=spec,
                      observables={k: float(v) for k, v in vals.items()}, noise_sd=sd,
                      status="ok" if ok else "failed", budget_left=budget_left)

    def shot_x(self, sample_id: str, speeds, elevs, z_stop: float, dv=None, de=None) -> np.ndarray:
        """x where shots come down through z_stop under the hidden law. speeds/elevs: (n,);
        dv, de: optional (K, n) actuation errors. Returns (K, n)."""
        specs = [LaunchSpec(sample_id=sample_id, speed_mps=float(v), elevation_deg=float(e))
                 for v, e in zip(speeds, elevs)]
        K = 1 if dv is None else np.asarray(dv).shape[0]
        return simulate_specs(compile_law(self.law), np.repeat(self.theta, K, 0), specs, MASSES,
                              dv, de, z_stop=z_stop)[..., 0]


def make_world(seed: int, *, final_eval: bool = False) -> World:
    if seed in TEST_SEEDS and not final_eval:
        raise PermissionError(f"seed {seed} is a test seed; only --final-eval may use it")
    rng = np.random.default_rng(seed)
    family = FAMILIES[seed % 4]
    g0 = rng.uniform(3, 14)
    rho = rng.uniform(0.0 if family == "F0" else 0.1, 0.8)
    p = float(rng.choice([1, 3])) if family == "F1" else 2.0
    sign = rng.choice([-1, 1])
    alpha = sign * rng.uniform(0.15, 0.5) if family == "F2" else 0.0
    kappa = sign * rng.uniform(0.25, 0.6) if family == "F3" else 0.0
    # rho = drag force / weight for the 100 g sample at 3 m/s; weight of 100 g at z = 0 is 0.1*g0.
    c = rho * 0.1 * g0 / 3**p
    ax, az = law_expr(p)
    law, fit = fixed_law("truth", ax, az, {"g0": g0, "c": c, "alpha": alpha, "kappa": kappa})
    w = World(seed=seed, world_id=f"w{seed}", family=family, p=p, g0=float(g0), rho=float(rho),
              alpha=float(alpha), kappa=float(kappa), c=float(c), law=law, fit=fit)
    _make_targets(w, rng)
    _make_probes(w, rng)
    _make_shot_zero(w, rng)
    return w


def _max_range(w: World, speed: float, z_t: float) -> float:
    els = np.arange(15, 75.1, 1.0)
    x = w.shot_x("mission_300", np.full(els.size, speed), els, z_t)[0]
    return float(np.nanmax(x)) if np.isfinite(x).any() else float("nan")


def _make_targets(w: World, rng) -> None:
    """Two targets reachable at <= 4 m/s, three needing more (up to 7 m/s), under the truth."""
    v_test = RANGES["launch"]["speed_mps"][1]
    v_mission = RANGES["mission"]["speed_mps"][1]
    for k in range(5):
        z_t = float(rng.uniform(0.0, 0.5))
        r_test = _max_range(w, v_test, z_t)
        r_max = _max_range(w, 0.97 * v_mission, z_t)
        if k < 2:
            x_t = rng.uniform(0.4, 0.9) * r_test
            kind = "in_range"
        else:
            x_t = rng.uniform(1.08 * r_test, max(1.1 * r_test, 0.95 * r_max))
            kind = "beyond"
        t = Target(target_id=f"t{k + 1}", x_m=round(float(x_t), 3), z_m=round(z_t, 3))
        w.targets.append(t)
        w.target_kind[t.target_id] = kind


def _make_probes(w: World, rng) -> None:
    """Hidden probe launches: mixed samples, speeds up to the mission limit, landing on z = 0."""
    ids = [s.sample_id for s in SAMPLES]
    v_lo, v_hi = RANGES["mission"]["speed_mps"]
    e_lo, e_hi = RANGES["mission"]["elevation_deg"]
    probes, xs = [], []
    while len(probes) < N_PROBES:
        cand = [LaunchSpec(sample_id=str(rng.choice(ids)), speed_mps=float(rng.uniform(v_lo, v_hi)),
                           elevation_deg=float(rng.uniform(e_lo, e_hi))) for _ in range(40)]
        x = simulate_specs(compile_law(w.law), w.theta, cand, MASSES)[0, :, 0]
        for s, xi in zip(cand, x):
            if np.isfinite(xi) and len(probes) < N_PROBES:
                probes.append(s)
                xs.append(xi)
    w.probes = probes
    w.probe_x = np.array(xs)


def _make_shot_zero(w: World, rng) -> None:
    """Practice shot with ref_100 at a 1.2 m table target, planned with textbook physics."""
    t0 = Target(target_id="t0", x_m=1.2, z_m=0.0)
    law, fit = textbook_law()
    plan = plan_shot(law, fit, t0, "ref_100", RANGES["launch"], n_draws=0)
    spec = LaunchSpec(sample_id="ref_100", speed_mps=round(plan.speed_mps, 3),
                      elevation_deg=round(plan.elevation_deg, 2))
    res = w.run_experiment(spec, rng, index=0, budget_left=12)
    res.experiment_id = "shot0"
    x = res.observables["landing_x_m"]
    w.shot_zero = ShotZero(spec=spec, target_id="t0", landing_x_m=x,
                           miss_m=abs(x - t0.x_m) if math.isfinite(x) else float("inf"))
    w.shot_zero_result = res
