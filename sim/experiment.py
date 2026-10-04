"""Experiments in MuJoCo, in two modes with the same physics (SPEC 3 "Robot execution").

- fast: the sample is placed at its release state and the flight runs headless.
- full: the arm picks the sample from the tray and carries out the primitives; drops and
  launches then fly exactly as in fast mode, weighing reads the wrist force sensor.

Noise follows world.generator.World.run_experiment draw for draw (actuation first, then
sensor noise in observable order), so with the same rng state the two differ only by the
physics.

    .venv/bin/python -m sim.experiment --seed 1002 --mode full --spec '{"type":"drop","sample_id":"ref_100","height_m":1.0}'
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass

import mujoco
import numpy as np

from schemas import Result, parse_spec
from tools.defaults import NOISE_SD

from .arm import Arm, PrimitiveError, aim_launcher, fire
from .scene import SAMPLE_IDS, DROP_XY, MUZZLE, WEIGH_XY, Scene, scene_for_world

T_MAX = 8.0  # s, as in the shared integrator
FRAME_DT = 1 / 120  # s of sim time between on_frame calls during flights
# Flights run under MuJoCo's RK4 integrator; the law force is re-evaluated at every stage.
FLIGHT_DT = 1e-3


@dataclass
class Flight:
    """Where and when a free sample came down through z_stop, plus its sampled path."""

    x: float
    t: float
    path: np.ndarray  # (n, 4): t, x, y, z


def fly(sc: Scene, sid: str, z_stop: float = 0.0, on_frame=None, t_max: float = T_MAX,
        tail: float = 0.0) -> Flight:
    """Step until the sample crosses z_stop moving down. Crossing time and x are found with
    the same cubic Hermite interpolation the shared integrator uses. Runs `tail` seconds more
    afterwards (for video). NaN if it never comes down."""
    d = sc.data
    t0 = d.time
    a, v = sc.qadr[sid], sc.dadr[sid]
    dt_arm, integ = sc.model.opt.timestep, sc.model.opt.integrator
    sc.model.opt.timestep = h = FLIGHT_DT
    sc.model.opt.integrator = mujoco.mjtIntegrator.mjINT_RK4
    try:
        return _fly(sc, sid, z_stop, on_frame, t_max, tail, d, t0, a, v, h)
    finally:
        sc.model.opt.timestep, sc.model.opt.integrator = dt_arm, integ


def _fly(sc, sid, z_stop, on_frame, t_max, tail, d, t0, a, v, h):
    path = [(0.0, *d.qpos[a:a + 3])]
    every = max(1, int(round(FRAME_DT / h)))
    x_c = t_c = math.nan
    k = 0
    while d.time - t0 < t_max:
        p0, v0 = d.qpos[a:a + 3].copy(), d.qvel[v:v + 3].copy()
        sc.step()
        k += 1
        p1, v1 = d.qpos[a:a + 3], d.qvel[v:v + 3]
        if k % every == 0:
            path.append((d.time - t0, *p1))
            if on_frame is not None:
                on_frame(sc)
        if p0[2] >= z_stop > p1[2] and v1[2] < 0:
            s = _hermite_root(p0[2], p1[2], h * v0[2], h * v1[2], z_stop)
            x_c = float(_hermite(p0[0], p1[0], h * v0[0], h * v1[0], s))
            t_c = float(d.time - t0 - h + s * h)
            path.append((t_c, x_c, float(p1[1]), z_stop))
            break
        if abs(p1[0]) > 40 or p1[2] > 10:
            break
    t_end = d.time + tail
    while d.time < t_end:
        sc.step(every)
        path.append((d.time - t0, *d.qpos[a:a + 3]))
        if on_frame is not None:
            on_frame(sc)
    return Flight(x_c, t_c, np.array(path))


def _hermite(p0, p1, d0, d1, s):
    return ((2 * s**3 - 3 * s**2 + 1) * p0 + (s**3 - 2 * s**2 + s) * d0
            + (-2 * s**3 + 3 * s**2) * p1 + (s**3 - s**2) * d1)


def _hermite_root(p0, p1, d0, d1, target):
    s = min(max((p0 - target) / (p0 - p1), 0.0), 1.0)
    for _ in range(6):
        val = _hermite(p0, p1, d0, d1, s) - target
        der = ((6 * s**2 - 6 * s) * p0 + (3 * s**2 - 4 * s + 1) * d0
               + (-6 * s**2 + 6 * s) * p1 + (3 * s**2 - 2 * s) * d1)
        s = min(max(s - val / (der if der else -1e-12), 0.0), 1.0)
    return s


class Lab:
    """One world's scene plus the arm; runs ExperimentSpecs in fast or full mode."""

    def __init__(self, world, on_frame=None, x_end: float = 4.0):
        self.world = world
        self.sc = scene_for_world(world, x_end)
        self.on_frame = on_frame
        self.arm = Arm(self.sc, on_frame=on_frame)
        self.last_flight: Flight | None = None
        self.events: list[tuple[float, str]] = []  # (sim time, primitive), for overlays
        self.release_error: tuple[float, float] | None = None  # drop: (z - h, vz) at release

    def _event(self, name: str) -> None:
        self.events.append((self.sc.data.time, name))

    # --- noiseless physics -------------------------------------------------------------

    def measure(self, spec, mode: str = "fast", dv: float = 0.0, de: float = 0.0,
                z_stop: float = 0.0, reset: bool = True) -> dict[str, float]:
        """Noiseless observables of one experiment (actuation errors dv, de applied)."""
        sc = self.sc
        if reset:
            sc.reset()
            self.arm.reset()
            self.events.clear()
            sc.step(int(0.05 / sc.model.opt.timestep))  # settle the tray
        if mode == "fast":
            return self._fast(spec, dv, de, z_stop)
        if mode == "full":
            return self._full(spec, dv, de, z_stop)
        raise ValueError(mode)

    def _fast(self, spec, dv, de, z_stop):
        sc, sid = self.sc, spec.sample_id
        if spec.type == "weigh":
            sc.place_sample(sid, [WEIGH_XY[0], WEIGH_XY[1], spec.height_m])
            mujoco.mj_forward(sc.model, sc.data)
            fx, fz = sc.law_force()
            i = SAMPLE_IDS.index(sid)
            return {"force_n": float(math.hypot(fx[i], fz[i]))}
        if spec.type == "drop":
            sc.place_sample(sid, [DROP_XY[0], DROP_XY[1], spec.height_m])
            f = self.last_flight = fly(sc, sid, 0.0, self.on_frame)
            return {"fall_time_s": f.t}
        v, e = spec.speed_mps * (1 + dv), spec.elevation_deg + de
        sc.data.qpos[sc.elev_qadr] = math.radians(e)
        fire(sc, sid, v, e)
        f = self.last_flight = fly(sc, sid, z_stop, self.on_frame)
        return {"landing_x_m": f.x, "flight_time_s": f.t}

    def _full(self, spec, dv, de, z_stop):
        sc, arm, sid = self.sc, self.arm, spec.sample_id
        self._event("pick")
        arm.pick(sid)
        if spec.type == "weigh":
            self._event("move")
            arm.transit([WEIGH_XY[0], WEIGH_XY[1], max(spec.height_m, 0.25)])
            arm.move_sample_to([WEIGH_XY[0], WEIGH_XY[1], spec.height_m])
            self._event("hold")
            arm.hold(0.4)
            n = 20
            f = np.zeros(3)
            for _ in range(n):  # average the wrist sensor over 0.1 s
                arm.hold(0.005)
                f += sc.wrist_force()
            out = {"force_n": float(np.linalg.norm(f / n))}
            self._event("return")
            return out
        if spec.type == "drop":
            self._event("move")
            arm.transit([DROP_XY[0], DROP_XY[1], max(spec.height_m, 0.25)])
            arm.move_sample_to([DROP_XY[0], DROP_XY[1], spec.height_m])
            self._event("hold")
            arm.hold(0.3)
            self._event("release")
            self.release_error = (sc.sample_pos(sid)[2] - spec.height_m, sc.sample_vel(sid)[2])
            arm.release()
            f = self.last_flight = fly(sc, sid, 0.0, self.on_frame, tail=0.3)
            return {"fall_time_s": f.t}
        v, e = spec.speed_mps * (1 + dv), spec.elevation_deg + de
        self._event("load")
        arm.load_launcher()
        self._event("aim")
        aim_launcher(sc, spec.elevation_deg, on_tick=self.on_frame)
        self._event("fire")
        fire(sc, sid, v, e)
        f = self.last_flight = fly(sc, sid, z_stop, self.on_frame, tail=0.4)
        return {"landing_x_m": f.x, "flight_time_s": f.t}

    # --- noisy experiment, Result record ------------------------------------------------

    def run_experiment(self, spec, rng: np.random.Generator, index: int = 0, budget_left: int = 0,
                       mode: str = "full", noise: dict | None = None) -> Result:
        """Same contract and draw order as World.run_experiment. A primitive failure gives a
        status "failed" Result with NaN observables (it still costs budget)."""
        noise = noise or NOISE_SD
        dv = de = 0.0
        if spec.type == "launch":
            dv = rng.normal(0, noise["speed_frac"])
            de = rng.normal(0, noise["elevation_deg"])
        try:
            obs = self.measure(spec, mode, dv, de)
            self.failure = None
        except PrimitiveError as exc:
            obs = {"weigh": {"force_n": math.nan}, "drop": {"fall_time_s": math.nan},
                   "launch": {"landing_x_m": math.nan, "flight_time_s": math.nan}}[spec.type]
            self.failure = str(exc)
        if spec.type == "weigh":
            f = obs["force_n"]
            vals = {"force_n": f * (1 + rng.normal(0, noise["force_frac"]))}
            sd = {"force_n": noise["force_frac"] * abs(f)}
        elif spec.type == "drop":
            vals = {"fall_time_s": obs["fall_time_s"] + rng.normal(0, noise["fall_time_s"])}
            sd = {"fall_time_s": noise["fall_time_s"]}
        else:
            vals = {"landing_x_m": obs["landing_x_m"] + rng.normal(0, noise["landing_x_m"]),
                    "flight_time_s": obs["flight_time_s"] + rng.normal(0, noise["flight_time_s"])}
            sd = {k: noise[k] for k in ("landing_x_m", "flight_time_s", "speed_frac", "elevation_deg")}
        ok = all(math.isfinite(x) for x in vals.values())
        if not ok:
            vals = {k: math.nan for k in vals}
            sd = {k: (x if math.isfinite(x) else 0.0) for k, x in sd.items()}
        return Result(experiment_id=f"e{index:02d}", index=index, spec=spec,
                      observables={k: float(x) for k, x in vals.items()}, noise_sd=sd,
                      status="ok" if ok else "failed", budget_left=budget_left)


def main():
    from world.generator import DEV_SEEDS, make_world

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--mode", choices=("fast", "full"), default="full")
    ap.add_argument("--spec", required=True, help="ExperimentSpec JSON")
    args = ap.parse_args()
    if args.seed not in DEV_SEEDS:
        raise SystemExit("sim runs dev seeds 1000-1999 only")
    lab = Lab(make_world(args.seed))
    r = lab.run_experiment(parse_spec(args.spec), np.random.default_rng(args.seed), 1, 11, args.mode)
    print(r.model_dump_json(indent=1))
    if lab.failure:
        print("failure:", lab.failure)


if __name__ == "__main__":
    main()
