"""Tests for tools/ and world/: integrator accuracy, fitting, planning, test-seed guard."""

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from schemas import DropSpec, LaunchSpec, Law, Target, WeighSpec
from tools import CompiledLaw, disagreement, fit_law, integrate, plan_shot, predict, simulate
from tools.defaults import NOISE_SD, RANGES, SAMPLES
from world.generator import fixed_law, make_world

GENERAL = Law(
    law_id="general",
    ax="-c*speed**(p-1)*vx/m",
    az="-g0*(1+kappa*z)*(m/0.1)**alpha - c*speed**(p-1)*vz/m",
    params={k: {"init": 0, "lo": -10, "hi": 10} for k in ("g0", "c", "p", "alpha", "kappa")},
)


def test_integrator_matches_solve_ivp_on_20_cases():
    cl = CompiledLaw(GENERAL)
    rng = np.random.default_rng(0)
    B = 20
    m = rng.choice([0.02, 0.05, 0.1, 0.2, 0.4, 0.8, 0.3], B)
    v = rng.uniform(1, 7, B)
    th = np.radians(rng.uniform(15, 75, B))
    g0 = rng.uniform(3, 14, B)
    p = rng.choice([1.0, 2.0, 3.0], B)
    c = rng.uniform(0, 0.8, B) * 0.1 * g0 / 3**p
    P = [g0, c, p, rng.uniform(-0.5, 0.5, B), rng.uniform(-0.6, 0.6, B)]
    zs = rng.uniform(0, 0.5, B)
    x, t = integrate(cl, m, np.zeros(B), 0.2, v * np.cos(th), v * np.sin(th), zs, P)
    for i in range(B):
        def rhs(_, y):
            ax, az = cl.accel(m[i], y[1], y[2], y[3], [q[i] for q in P])
            return [y[2], y[3], float(ax), float(az)]

        ev = lambda _, y: y[1] - zs[i]  # noqa: E731
        ev.terminal, ev.direction = True, -1
        sol = solve_ivp(rhs, (0, 8), [0, 0.2, v[i] * np.cos(th[i]), v[i] * np.sin(th[i])],
                        events=ev, rtol=1e-10, atol=1e-12)
        if len(sol.t_events[0]) == 0 or sol.y_events[0][0][1] > 9:
            continue  # escapes the workspace; the batched integrator reports NaN
        assert abs(sol.y_events[0][0][0] - x[i]) < 1e-4
        assert abs(sol.t_events[0][0] - t[i]) < 1e-4


def test_test_seeds_refused():
    with pytest.raises(PermissionError):
        make_world(9000)


def test_fit_recovers_noiseless_parameters():
    w = make_world(1002)  # a mass-dependent-gravity dev world
    rng = np.random.default_rng(0)
    zero = {k: 0.0 for k in NOISE_SD}
    specs = [WeighSpec(sample_id="ref_020", height_m=0.0), WeighSpec(sample_id="ref_800", height_m=0.5),
             DropSpec(sample_id="ref_050", height_m=1.2),
             LaunchSpec(sample_id="ref_200", speed_mps=4.0, elevation_deg=40.0),
             LaunchSpec(sample_id="ref_020", speed_mps=3.0, elevation_deg=60.0)]
    results = [w.run_experiment(s, rng, i, noise=zero) for i, s in enumerate(specs)]
    law = Law(law_id="L", ax="-c*speed*vx/m", az="-g0*(m/0.1)**alpha - c*speed*vz/m",
              params={"g0": {"init": 9.8, "lo": 1, "hi": 20}, "alpha": {"init": 0, "lo": -1, "hi": 1},
                      "c": {"init": 0.01, "lo": 0, "hi": 1}})
    fit = fit_law(law, results)
    assert fit.converged
    assert fit.params["g0"].value == pytest.approx(w.g0, rel=1e-3)
    assert fit.params["alpha"].value == pytest.approx(w.alpha, abs=1e-3)
    assert fit.params["c"].value == pytest.approx(w.c, rel=1e-2)
    assert fit.loo_error is not None and fit.cov is not None


def test_predict_disagreement_and_plan_shot_under_truth():
    w = make_world(1000)
    spec = LaunchSpec(sample_id="ref_100", speed_mps=3.0, elevation_deg=45.0)
    pred = predict(w.law, w.fit, spec, n_draws=0)
    sim = simulate(w.law, {k: v.value for k, v in w.fit.params.items()}, spec, SAMPLES[2])
    assert pred.observables["landing_x_m"].mean == pytest.approx(sim["landing_x_m"])
    tb = fixed_law("textbook", "-c*speed*vx/m", "-g0 - c*speed*vz/m", {"g0": 9.81, "c": 3.5e-4})
    d = disagreement(spec, [(w.law, w.fit), tb])
    assert d.max_gap_sigma > 3
    t = Target(target_id="t", x_m=1.5, z_m=0.2)
    plan = plan_shot(w.law, w.fit, t, "mission_300", RANGES["mission"], n_draws=0)
    assert plan.reachable
    x = w.shot_x("mission_300", [plan.speed_mps], [plan.elevation_deg], t.z_m)[0, 0]
    assert abs(x - t.x_m) < 0.005


def test_fit_ignores_missing_observables():
    """Shot zero arrives over HTTP with landing distance only; the fit must accept that."""
    w = make_world(1000)
    rng = np.random.default_rng(1)
    r0 = w.shot_zero_result.model_copy(update={"observables": {"landing_x_m": w.shot_zero.landing_x_m}})
    rest = [w.run_experiment(s, rng, i + 1) for i, s in enumerate([
        WeighSpec(sample_id="ref_100", height_m=0.3), DropSpec(sample_id="ref_100", height_m=1.0)])]
    law = Law(law_id="L", ax="-c*speed*vx/m", az="-g0 - c*speed*vz/m",
              params={"g0": {"init": 9.8, "lo": 1, "hi": 20}, "c": {"init": 0.01, "lo": 0, "hi": 1}})
    fit = fit_law(law, [r0] + rest, loo=False)
    assert fit.converged and fit.params["g0"].value == pytest.approx(w.g0, rel=0.05)
