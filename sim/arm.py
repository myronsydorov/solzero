"""Scripted arm primitives (SPEC 3 "Robot execution"): pick, move, hold, release, load
launcher, fire. Damped least-squares IK on a scratch MjData, minimum-jerk joint-space
motion tracked by the Panda's position actuators, a weld constraint for the grasp.

Every primitive returns normally or raises PrimitiveError with a reason; the experiment
runner turns that into a failed Result.
"""

from __future__ import annotations

import math

import mujoco
import numpy as np

from .scene import ARM_BASE, HOME_Q, MUZZLE, RADIUS, Scene

DOWN = np.array([[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]])  # grip frame: z points down
GRIP_OPEN, GRIP_CLOSED = 255.0, 255.0 * RADIUS / 0.04  # fingers touch the 4 cm sphere
MAX_JOINT_SPEED = 1.2  # rad/s, for timing min-jerk moves
SETTLE_TOL = 2e-3  # rad, joint tracking error at the end of a move
SETTLE_TIMEOUT = 1.5  # s


class PrimitiveError(RuntimeError):
    pass


class Arm:
    def __init__(self, sc: Scene, on_frame=None):
        self.sc = sc
        self.scratch = mujoco.MjData(sc.model)
        self.on_frame = on_frame  # callback(scene) every control tick, e.g. a video writer
        self.held: str | None = None
        self.lo = sc.model.jnt_range[[sc.model.joint(f"panda/joint{i}").id for i in range(1, 8)], 0]
        self.hi = sc.model.jnt_range[[sc.model.joint(f"panda/joint{i}").id for i in range(1, 8)], 1]

    # --- kinematics -------------------------------------------------------------

    def q(self) -> np.ndarray:
        return self.sc.data.qpos[self.sc.arm_qadr].copy()

    def grip_pos(self) -> np.ndarray:
        return self.sc.data.site_xpos[self.sc.grip_site].copy()

    def ik(self, pos, q0=None, **kw) -> np.ndarray:
        """IK from q0, then from postures turned toward the target (avoids local minima)."""
        pos = np.asarray(pos, float)
        rel = pos - ARM_BASE
        yaw = math.atan2(rel[1], rel[0]) - math.pi / 2  # base frame is turned to face +y
        starts = [self.q() if q0 is None else np.asarray(q0, float)]
        for sh, el, wr in ((0.3, -2.2, 2.4), (0.9, -1.6, 2.6), (-0.3, -2.6, 2.0), (1.2, -1.0, 2.2)):
            starts.append(np.array([yaw, sh, 0.0, el, 0.0, wr, 0.785]))
        # Gripper pointing down first; for high holds, pointing horizontally away from the
        # base (a sphere can be held in any orientation).
        out = np.array([rel[0], rel[1], 0.0]) / max(np.hypot(rel[0], rel[1]), 1e-9)
        side = np.column_stack([np.cross([0.0, 0.0, 1.0], out), [0.0, 0.0, 1.0], out])
        rots = [kw.pop("rot", DOWN)] + ([side] if kw.pop("allow_side", True) else [])
        for rot in rots:
            for q_start in starts:
                try:
                    return self._ik(pos, q_start, rot=rot, **kw)
                except PrimitiveError:
                    pass
        raise PrimitiveError(f"IK did not converge for grip position {np.round(pos, 3).tolist()}")

    def _ik(self, pos, q0, rot=DOWN, iters: int = 300, tol: float = 2e-4,
           free_yaw: bool = True) -> np.ndarray:
        """Joint angles that put the grip site at pos with orientation rot."""
        m, d, sc = self.sc.model, self.scratch, self.sc
        d.qpos[:] = self.sc.data.qpos
        q = np.clip(np.array(q0, float), self.lo + 1e-3, self.hi - 1e-3)
        jp = np.zeros((3, m.nv))
        jr = np.zeros((3, m.nv))
        cols = sc.arm_dadr
        for _ in range(iters):
            d.qpos[sc.arm_qadr] = q
            mujoco.mj_kinematics(m, d)
            mujoco.mj_comPos(m, d)
            p = d.site_xpos[sc.grip_site]
            R = d.site_xmat[sc.grip_site].reshape(3, 3)
            e_p = pos - p
            if free_yaw:  # only the pointing direction (grip z axis) is constrained
                e_r = np.cross(R[:, 2], rot[:, 2])
            else:
                e_r = 0.5 * sum(np.cross(R[:, k], rot[:, k]) for k in range(3))
            if np.linalg.norm(e_p) < tol and np.linalg.norm(e_r) < 10 * tol:
                return q
            mujoco.mj_jacSite(m, d, jp, jr, sc.grip_site)
            J = np.vstack([jp[:, cols], jr[:, cols]])
            e = np.concatenate([e_p, e_r])
            lam = 1e-2
            JJ = J @ J.T + lam**2 * np.eye(6)
            dq = J.T @ np.linalg.solve(JJ, e)
            null = np.eye(7) - np.linalg.pinv(J) @ J
            dq += null @ (0.05 * (q0 - q))
            q = np.clip(q + np.clip(dq, -0.2, 0.2), self.lo + 1e-3, self.hi - 1e-3)
        raise PrimitiveError(f"IK did not converge for grip position {np.round(pos, 3).tolist()}")

    # --- motion -----------------------------------------------------------------

    def _tick(self, n_steps: int) -> None:
        self.sc.step(n_steps)
        self.check_contacts()
        if self.on_frame is not None:
            self.on_frame(self.sc)

    def move_q(self, q1, duration: float | None = None, tick: float = 0.01) -> None:
        """Minimum-jerk joint-space move, then wait until the arm has settled."""
        sc = self.sc
        q0 = sc.data.ctrl[:7].copy()
        dist = np.max(np.abs(q1 - q0))
        T = duration if duration is not None else max(0.4, 1.875 * dist / MAX_JOINT_SPEED)
        n_tick = max(1, int(round(tick / sc.model.opt.timestep)))
        steps = int(math.ceil(T / tick))
        for k in range(1, steps + 1):
            s = k / steps
            b = 10 * s**3 - 15 * s**4 + 6 * s**5
            sc.data.ctrl[:7] = q0 + b * (q1 - q0)
            self._tick(n_tick)
        self.settle(q1, tick)

    def settle(self, q1, tick: float = 0.01) -> None:
        sc = self.sc
        n_tick = max(1, int(round(tick / sc.model.opt.timestep)))
        t = 0.0
        while t < SETTLE_TIMEOUT:
            err = np.max(np.abs(self.q() - q1))
            vel = np.max(np.abs(sc.data.qvel[sc.arm_dadr]))
            if err < SETTLE_TOL and vel < 5e-3:
                return
            self._tick(n_tick)
            t += tick
        raise PrimitiveError(f"arm did not settle (joint error {err:.4f} rad)")

    def move(self, pos, duration: float | None = None) -> None:
        """Move the grip site (gripper pointing down) to pos."""
        self.move_q(self.ik(np.asarray(pos, float), q0=self.sc.data.ctrl[:7]), duration)

    def transit(self, pos, height: float = 0.40) -> None:
        """Go up to a safe height, across, then down to pos (clears the launcher and tray)."""
        here = self.grip_pos()
        h = max(height, here[2], pos[2])
        if here[2] < h - 0.02:
            self.move([here[0], here[1], h])
        self.move([pos[0], pos[1], h])
        self.move(pos)

    def gripper(self, value: float, wait: float = 0.25) -> None:
        self.sc.data.ctrl[self.sc.grip_act] = value
        n = int(round(0.01 / self.sc.model.opt.timestep))
        for _ in range(int(wait / 0.01)):
            self._tick(n)

    def hold(self, seconds: float) -> None:
        n = int(round(0.01 / self.sc.model.opt.timestep))
        for _ in range(max(1, int(round(seconds / 0.01)))):
            self._tick(n)

    def check_contacts(self) -> None:
        """Fail if the arm touches anything but itself (table, pedestal, launcher)."""
        m, d = self.sc.model, self.sc.data
        for c in d.contact[:d.ncon]:
            b1, b2 = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
            n1, n2 = m.body(b1).name, m.body(b2).name
            if n1.startswith("panda/") != n2.startswith("panda/"):
                raise PrimitiveError(f"arm collision: {n1} with {n2}")

    # --- primitives ---------------------------------------------------------------

    def pick(self, sid: str, approach: float = 0.12) -> None:
        if self.held is not None:
            raise PrimitiveError(f"already holding {self.held}")
        p = self.sc.sample_pos(sid)
        self.gripper(GRIP_OPEN, 0.0)
        self.move(p + [0, 0, approach])
        self.move(p, duration=0.6)
        self.gripper(GRIP_CLOSED)
        self.sc.set_weld("grasp", sid, True)
        self.held = sid
        self.move(p + [0, 0, approach], duration=0.6)
        if np.linalg.norm(self.sc.sample_pos(sid) - self.grip_pos()) > 0.01:
            raise PrimitiveError(f"{sid} slipped out of the grasp")

    def move_sample_to(self, pos, duration: float | None = None, refine: int = 4) -> None:
        """Bring the held sample's centre to pos, correcting the residual offset between the
        grip site and the sample (weld compliance) with up to `refine` small moves."""
        pos = np.asarray(pos, float)
        self.move(pos, duration)
        for _ in range(refine):
            err = pos - self.sc.sample_pos(self.held)
            if np.linalg.norm(err) < 5e-5:
                break
            self.move(self.grip_pos() + err, duration=0.3)

    def release(self) -> str:
        """Open the weld and the fingers. The sample is free from this step on."""
        sid = self.held
        if sid is None:
            raise PrimitiveError("release with nothing held")
        self.sc.set_weld("grasp", sid, False)
        self.sc.data.ctrl[self.sc.grip_act] = GRIP_OPEN
        self.held = None
        return sid

    def load_launcher(self, elevation_deg: float = 75.0) -> None:
        """Place the held sample in the launcher cradle and retreat."""
        sid = self.held
        sc = self.sc
        sc.data.ctrl[sc.elev_act] = math.radians(elevation_deg)
        self.transit(MUZZLE + [0, 0, 0.12])
        self.move_sample_to(MUZZLE, duration=0.6)
        err = np.linalg.norm(sc.sample_pos(sid) - MUZZLE)
        if err > 0.005:
            raise PrimitiveError(f"misload: {sid} is {err * 1000:.1f} mm from the cradle")
        sc.set_weld("load", sid, True)
        self.release()
        self.gripper(GRIP_OPEN, 0.2)
        self.move(MUZZLE + [0, 0, 0.15], duration=0.5)
        self.move(MUZZLE + [-0.25, -0.25, 0.35])

    def home(self) -> None:
        self.move_q(HOME_Q)


def aim_launcher(sc: Scene, elevation_deg: float, on_tick=None, timeout: float = 2.0) -> None:
    """Drive the barrel to the commanded elevation and wait until it is still."""
    target = math.radians(elevation_deg)
    sc.data.ctrl[sc.elev_act] = target
    n = int(round(0.01 / sc.model.opt.timestep))
    for _ in range(int(timeout / 0.01)):
        sc.step(n)
        if on_tick is not None:
            on_tick(sc)
        q = sc.data.qpos[sc.elev_qadr]
        if abs(q - target) < 1e-3 and abs(sc.data.qvel[sc.model.jnt_dofadr[sc.model.joint("elevation").id]]) < 1e-3:
            return
    raise PrimitiveError("launcher did not reach its elevation")


def fire(sc: Scene, sid: str, speed_mps: float, elevation_deg: float) -> None:
    """Release the loaded sample at the muzzle with the given (already perturbed) speed and
    elevation. The barrel guides the sample, so the release point is the muzzle itself."""
    sc.set_weld("load", sid, False)
    e = math.radians(elevation_deg)
    sc.place_sample(sid, MUZZLE, (speed_mps * math.cos(e), 0.0, speed_mps * math.sin(e)))
    mujoco.mj_forward(sc.model, sc.data)
