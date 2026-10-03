"""sim/: MuJoCo scene, primitives and fast/full equivalence against the shared integrator.

Skipped when mujoco or the Menagerie assets are missing (`python -m sim.fetch_assets`).
"""

import math

import numpy as np
import pytest

mujoco = pytest.importorskip("mujoco")
from sim.scene import PANDA_XML  # noqa: E402

if not PANDA_XML.exists():
    pytest.skip("Menagerie assets missing; run python -m sim.fetch_assets", allow_module_level=True)

from schemas import Result, parse_spec  # noqa: E402
from sim.experiment import Lab  # noqa: E402
from tools.analysis import compile_law, plan_shot, simulate_specs  # noqa: E402
from tools.defaults import NOISE_SD, RANGES  # noqa: E402
from world.generator import MASSES, make_world  # noqa: E402

SPECS = [
    {"type": "weigh", "sample_id": "mission_300", "height_m": 0.9},
    {"type": "drop", "sample_id": "ref_020", "height_m": 1.2},
    {"type": "launch", "sample_id": "ref_050", "speed_mps": 3.5, "elevation_deg": 40.0},
]
SD = {"force_n": None, "fall_time_s": NOISE_SD["fall_time_s"], "landing_x_m": NOISE_SD["landing_x_m"],
      "flight_time_s": NOISE_SD["flight_time_s"]}


@pytest.fixture(scope="module")
def lab():
    return Lab(make_world(1003))  # an F3 world: height-dependent gravity


def _ref(w, spec):
    obs = simulate_specs(compile_law(w.law), w.theta, [spec], MASSES)[0, 0]
    names = {"weigh": ["force_n"], "drop": ["fall_time_s"], "launch": ["landing_x_m", "flight_time_s"]}
    return dict(zip(names[spec.type], obs))


@pytest.mark.parametrize("mode", ["fast", "full"])
def test_modes_match_integrator(lab, mode):
    for d in SPECS:
        spec = parse_spec(d)
        ref = _ref(lab.world, spec)
        got = lab.measure(spec, mode)
        for k, v in ref.items():
            sd = SD[k] or NOISE_SD["force_frac"] * abs(v)
            assert abs(got[k] - v) < 0.2 * sd, (mode, d, k, got[k], v)


def test_law_force_is_applied_to_samples(lab):
    sc = lab.sc
    sc.reset()
    sc.place_sample("ref_200", [0.0, 0.0, 0.5])
    sc.step()  # the passive callback adds the law force on the sample's free-joint dofs
    i = sc.dadr["ref_200"]
    z, vz = sc.data.qpos[sc.qadr["ref_200"] + 2], sc.data.qvel[i + 2]
    ax, az = sc.law.accel(np.array([0.2]), np.array([z]), np.zeros(1), np.array([vz]), sc.theta)
    assert sc.data.qfrc_passive[i + 2] == pytest.approx(0.2 * az[0], rel=1e-6)
    assert sc.model.opt.gravity.tolist() == [0, 0, 0]
    assert sc.model.opt.density == 0 and sc.model.opt.viscosity == 0


def test_run_experiment_contract(lab):
    spec = parse_spec(SPECS[2])
    r = lab.run_experiment(spec, np.random.default_rng(0), index=3, budget_left=9, mode="fast")
    assert isinstance(r, Result) and r.status == "ok" and r.experiment_id == "e03"
    assert set(r.observables) == {"landing_x_m", "flight_time_s"}
    assert set(r.noise_sd) == {"landing_x_m", "flight_time_s", "speed_frac", "elevation_deg"}
    # Same draw order as World.run_experiment: same rng state gives the same noise.
    w = lab.world
    r_w = w.run_experiment(spec, np.random.default_rng(0), index=3, budget_left=9)
    for k in r.observables:
        assert abs(r.observables[k] - r_w.observables[k]) < 0.2 * NOISE_SD[k]


def test_mission_shot_crosses_target_height(lab):
    w = lab.world
    t = w.targets[2]  # beyond the tested range
    plan = plan_shot(w.law, w.fit, t, "mission_300", RANGES["mission"], n_draws=0)
    spec = parse_spec({"type": "launch", "sample_id": "mission_300", "speed_mps": plan.speed_mps,
                       "elevation_deg": plan.elevation_deg})
    got = lab.measure(spec, "fast", z_stop=t.z_m)["landing_x_m"]
    ref = w.shot_x("mission_300", [plan.speed_mps], [plan.elevation_deg], t.z_m)[0, 0]
    assert math.isfinite(got) and abs(got - ref) < 0.2 * NOISE_SD["landing_x_m"]
    assert abs(got - t.x_m) < t.hit_radius_m
