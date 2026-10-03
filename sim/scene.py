"""MuJoCo scene for Sol Zero (SPEC.md section 3, "Robot execution").

Table, Franka Panda from MuJoCo Menagerie, launcher fixture with programmable elevation,
tray with the seven spheres, target bins, wrist force sensor.

Physics frame. Planar SPEC coordinates (x downrange, z up) map to MuJoCo world x and z;
the launch plane is y = 0. A sample's z is the height of its centre. The visible table top
sits one sphere radius below z = 0, so "the sample reaches the table plane z = 0" is the
moment the sphere touches the table.

Samples. MuJoCo's built-in gravity is zero and fluid forces are off (density = viscosity =
0), so samples feel only the hidden law, applied as an external force every step (in the
passive-force callback, see Scene.step), from the law functions in tools/ and world/. The arm is a
gravity-compensated, calibrated instrument (SPEC 2: "the hidden law acts on samples").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import mujoco
import numpy as np

from schemas import Target
from tools.analysis import compile_law
from tools.defaults import LAUNCHER, SAMPLES
from tools.integrator import CompiledLaw

ASSETS = Path(__file__).with_name("assets")
PANDA_XML = ASSETS / "franka_emika_panda" / "panda.xml"

TIMESTEP = 0.0005  # s; the law force is re-evaluated every step
RADIUS = 0.02  # all spheres (SPEC 3)
TABLE_TOP = -RADIUS
TABLE_HALF_Y = 0.35
ARM_BASE = np.array([-0.35, -0.60, 0.35])  # on a pedestal beside the table, facing +y
TRAY_Y = -0.15
TRAY_X0, TRAY_DX = -0.62, 0.075
DROP_XY = np.array([-0.05, -0.25])  # over the table
WEIGH_XY = np.array([0.15, -0.75])  # beyond the table edge, so height 0 touches nothing
MUZZLE = np.array([LAUNCHER.x_m, 0.0, LAUNCHER.z_m])
BARREL_LEN = 0.19
HOME_Q = np.array([0.0, -0.3, 0.0, -2.2, 0.0, 1.9, 0.785])

# contype/conaffinity bits: 1 = arm and fixtures, 2 = samples. Samples touch the table,
# the tray, the bins and each other, never the fingers (grasping uses a weld constraint).
ARM_BIT, SAMPLE_BIT = 1, 2

SAMPLE_RGBA = {
    "ref_020": (0.62, 0.80, 0.98, 1), "ref_050": (0.45, 0.68, 0.95, 1),
    "ref_100": (0.30, 0.55, 0.90, 1), "ref_200": (0.22, 0.42, 0.80, 1),
    "ref_400": (0.16, 0.30, 0.66, 1), "ref_800": (0.10, 0.18, 0.48, 1),
    "mission_300": (0.95, 0.66, 0.12, 1),
}
MASS = {s.sample_id: s.mass_kg for s in SAMPLES}
SAMPLE_IDS = [s.sample_id for s in SAMPLES]


def tray_pos(sample_id: str) -> np.ndarray:
    i = SAMPLE_IDS.index(sample_id)
    return np.array([TRAY_X0 + i * TRAY_DX, TRAY_Y, 0.0])


def ensure_assets() -> None:
    if not PANDA_XML.exists():
        raise FileNotFoundError(
            f"{PANDA_XML} is missing; run `.venv/bin/python -m sim.fetch_assets` first")


def build_spec(targets: list[Target] | None = None, x_end: float = 4.0) -> mujoco.MjSpec:
    """The scene as an MjSpec. x_end: how far downrange the table runs (m)."""
    ensure_assets()
    targets = targets or []
    x_end = max([x_end] + [t.x_m + 1.0 for t in targets])
    s = mujoco.MjSpec()
    s.modelname = "solzero"
    s.compiler.degree = False  # every angle below is in radians
    s.option.timestep = TIMESTEP
    s.option.gravity = [0, 0, 0]
    s.option.density = 0.0
    s.option.viscosity = 0.0
    s.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    s.visual.global_.offwidth = 1920
    s.visual.global_.offheight = 1080
    s.visual.quality.shadowsize = 4096
    s.visual.map.zfar = 200
    s.stat.extent = 2.0

    s.add_texture(name="sky", type=mujoco.mjtTexture.mjTEXTURE_SKYBOX,
                  builtin=mujoco.mjtBuiltin.mjBUILTIN_GRADIENT,
                  rgb1=[0.42, 0.30, 0.26], rgb2=[0.08, 0.06, 0.07], width=512, height=512)
    s.add_texture(name="ground", type=mujoco.mjtTexture.mjTEXTURE_2D,
                  builtin=mujoco.mjtBuiltin.mjBUILTIN_CHECKER, mark=mujoco.mjtMark.mjMARK_EDGE,
                  rgb1=[0.36, 0.24, 0.18], rgb2=[0.30, 0.20, 0.15], markrgb=[0.45, 0.32, 0.24],
                  width=300, height=300)
    s.add_texture(name="bench", type=mujoco.mjtTexture.mjTEXTURE_2D,
                  builtin=mujoco.mjtBuiltin.mjBUILTIN_CHECKER, mark=mujoco.mjtMark.mjMARK_EDGE,
                  rgb1=[0.82, 0.82, 0.80], rgb2=[0.78, 0.78, 0.76], markrgb=[0.55, 0.55, 0.55],
                  width=200, height=200)
    s.add_material(name="ground", textures=["", "ground"], texrepeat=[2, 2], texuniform=True)
    s.add_material(name="bench", textures=["", "bench"], texrepeat=[10, 10], texuniform=True,
                   reflectance=0.05)

    wb = s.worldbody
    wb.add_light(pos=[1.0, -2.0, 4.0], dir=[-0.2, 0.4, -1.0], diffuse=[0.8, 0.8, 0.8],
                 castshadow=True)
    wb.add_light(pos=[x_end / 2, 0.0, 6.0], dir=[0, 0, -1], diffuse=[0.4, 0.4, 0.4],
                 castshadow=False)
    wb.add_geom(name="floor", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[0, 0, 0.05],
                pos=[0, 0, -0.8], material="ground", contype=0, conaffinity=0)
    x0 = -0.9
    wb.add_geom(name="table", type=mujoco.mjtGeom.mjGEOM_BOX, material="bench",
                size=[(x_end - x0) / 2, TABLE_HALF_Y, 0.02],
                pos=[(x_end + x0) / 2, 0, TABLE_TOP - 0.02],
                contype=ARM_BIT | SAMPLE_BIT, conaffinity=ARM_BIT | SAMPLE_BIT)
    # Distance marks every metre along the launch plane.
    for k in range(1, int(x_end) + 1):
        wb.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, size=[0.004, 0.30, 0.0005],
                    pos=[k, 0, TABLE_TOP + 0.0005], rgba=[0.35, 0.35, 0.38, 1],
                    contype=0, conaffinity=0)

    # Pedestal and arm.
    wb.add_geom(name="pedestal", type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[0.11, (ARM_BASE[2] + 0.8) / 2, 0],
                pos=[ARM_BASE[0], ARM_BASE[1], (ARM_BASE[2] - 0.8) / 2], rgba=[0.25, 0.25, 0.28, 1],
                contype=0, conaffinity=ARM_BIT)
    panda = mujoco.MjSpec.from_file(str(PANDA_XML))
    hand = panda.body("hand")
    hand.add_site(name="grip", pos=[0, 0, 0.1034], size=[0.006, 0, 0], rgba=[1, 0, 0, 0])
    hand.add_site(name="wrist", pos=[0, 0, 0], size=[0.005, 0, 0], rgba=[0, 1, 0, 0])
    for b in panda.bodies:  # gravity-compensated arm (no-op while gravity is zero)
        b.gravcomp = 1.0
    frame = wb.add_frame(pos=ARM_BASE.tolist(), quat=[math.cos(math.pi / 4), 0, 0, math.sin(math.pi / 4)])
    frame.attach_body(panda.worldbody.first_body(), "panda/", "")
    s.add_exclude(bodyname1="world", bodyname2="panda/link0")
    s.add_sensor(name="wrist_force", type=mujoco.mjtSensor.mjSENS_FORCE,
                 objtype=mujoco.mjtObj.mjOBJ_SITE, objname="panda/wrist")

    # Launcher: pedestal plus a barrel that pivots about the muzzle (the release point).
    wb.add_geom(name="launcher_post", type=mujoco.mjtGeom.mjGEOM_BOX, size=[0.05, 0.05, (MUZZLE[2] - 0.05 - TABLE_TOP) / 2],
                pos=[MUZZLE[0] - 0.09, 0, (MUZZLE[2] - 0.05 + TABLE_TOP) / 2], rgba=[0.2, 0.2, 0.22, 1],
                contype=0, conaffinity=ARM_BIT)
    lb = wb.add_body(name="launcher", pos=MUZZLE.tolist())
    lb.add_joint(name="elevation", type=mujoco.mjtJoint.mjJNT_HINGE, axis=[0, -1, 0],
                 range=[0, math.radians(90)], damping=2.0, armature=0.05)
    lb.add_geom(name="barrel", type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                fromto=[-BARREL_LEN, 0, 0, -0.07, 0, 0], size=[0.03, 0, 0],
                rgba=[0.75, 0.32, 0.18, 1], mass=1.0, contype=0, conaffinity=ARM_BIT)
    lb.add_geom(name="cradle", type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                fromto=[-0.075, 0, 0, -0.022, 0, 0], size=[0.034, 0, 0],
                rgba=[0.3, 0.3, 0.32, 1], mass=0.1, contype=0, conaffinity=0)
    s.add_exclude(bodyname1="world", bodyname2="launcher")
    lb.add_site(name="muzzle", pos=[0, 0, 0], size=[0.004, 0, 0], rgba=[1, 1, 0, 0])
    s.add_actuator(name="elevation", target="elevation", trntype=mujoco.mjtTrn.mjTRN_JOINT,
                   gaintype=mujoco.mjtGain.mjGAIN_FIXED, biastype=mujoco.mjtBias.mjBIAS_AFFINE,
                   gainprm=[60] + [0] * 9, biasprm=[0, -60, -8] + [0] * 7,
                   ctrlrange=[0, math.radians(90)], ctrllimited=True)

    # Tray.
    tray_c = (tray_pos(SAMPLE_IDS[0]) + tray_pos(SAMPLE_IDS[-1])) / 2
    half = (len(SAMPLE_IDS) - 1) * TRAY_DX / 2 + 0.05
    wb.add_geom(name="tray", type=mujoco.mjtGeom.mjGEOM_BOX, size=[half, 0.045, 0.004],
                pos=[tray_c[0], TRAY_Y, TABLE_TOP + 0.004], rgba=[0.15, 0.15, 0.17, 1],
                contype=0, conaffinity=0)
    for sid in SAMPLE_IDS:  # a seat ring per sample
        p = tray_pos(sid)
        wb.add_geom(type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[0.026, 0.006, 0],
                    pos=[p[0], p[1], TABLE_TOP + 0.010], rgba=[0.3, 0.3, 0.32, 1], contype=0, conaffinity=0)

    # Stations.
    wb.add_geom(name="drop_mark", type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[0.05, 0.0005, 0],
                pos=[DROP_XY[0], DROP_XY[1], TABLE_TOP + 0.0006], rgba=[0.9, 0.85, 0.2, 1],
                contype=0, conaffinity=0)

    # Target bins: a ring of the hit radius at the target height, on a post.
    for t in targets:
        r = t.hit_radius_m or 0.05
        top = t.z_m - RADIUS
        if top - TABLE_TOP > 0.005:
            wb.add_geom(name=f"post_{t.target_id}", type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                        size=[0.025, (top - TABLE_TOP) / 2, 0], pos=[t.x_m, 0, (top + TABLE_TOP) / 2],
                        rgba=[0.3, 0.3, 0.32, 1], contype=0, conaffinity=0)
        wb.add_geom(name=f"bin_{t.target_id}", type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                    size=[r, 0.004, 0], pos=[t.x_m, 0, top - 0.004], rgba=[0.15, 0.75, 0.35, 0.85],
                    contype=0, conaffinity=0)  # scored geometrically, never an obstacle

    # Samples, each with a weld to the hand (grasp) and to the launcher (loaded).
    for sid in SAMPLE_IDS:
        b = wb.add_body(name=sid, pos=tray_pos(sid).tolist())
        b.add_freejoint(name=sid)
        b.add_geom(name=sid, type=mujoco.mjtGeom.mjGEOM_SPHERE, size=[RADIUS, 0, 0],
                   mass=MASS[sid], rgba=list(SAMPLE_RGBA[sid]), contype=SAMPLE_BIT, conaffinity=SAMPLE_BIT,
                   condim=3, friction=[0.8, 0.02, 0.001], solref=[0.004, 1])
        for kind, other in (("grasp", "panda/hand"), ("load", "launcher")):
            s.add_equality(name=f"{kind}_{sid}", type=mujoco.mjtEq.mjEQ_WELD, name1=other, name2=sid,
                           objtype=mujoco.mjtObj.mjOBJ_BODY, active=False, solref=[0.002, 1])
    return s


@dataclass
class Scene:
    """A compiled scene with handles. One per world (targets differ per world)."""

    model: mujoco.MjModel
    data: mujoco.MjData
    law: CompiledLaw | None = None
    theta: list[float] = field(default_factory=list)
    law_on: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self):
        m = self.model
        self.arm_qadr = np.array([m.jnt_qposadr[m.joint(f"panda/joint{i}").id] for i in range(1, 8)])
        self.arm_dadr = np.array([m.jnt_dofadr[m.joint(f"panda/joint{i}").id] for i in range(1, 8)])
        self.finger_qadr = np.array([m.jnt_qposadr[m.joint(f"panda/finger_joint{i}").id] for i in (1, 2)])
        self.body = {sid: m.body(sid).id for sid in SAMPLE_IDS}
        self.qadr = {sid: m.jnt_qposadr[m.joint(sid).id] for sid in SAMPLE_IDS}
        self.dadr = {sid: m.jnt_dofadr[m.joint(sid).id] for sid in SAMPLE_IDS}
        self.eq = {(k, sid): m.equality(f"{k}_{sid}").id for k in ("grasp", "load") for sid in SAMPLE_IDS}
        self.grip_site = m.site("panda/grip").id
        self.hand_body = m.body("panda/hand").id
        self.launcher_body = m.body("launcher").id
        self.elev_qadr = m.jnt_qposadr[m.joint("elevation").id]
        self.elev_act = m.actuator("elevation").id
        self.grip_act = m.actuator("panda/actuator8").id
        self.force_adr = m.sensor_adr[m.sensor("wrist_force").id]
        self.masses = np.array([MASS[s] for s in SAMPLE_IDS])
        self._bids = np.array([self.body[s] for s in SAMPLE_IDS])
        self._qz = np.array([self.qadr[s] + 2 for s in SAMPLE_IDS])
        self._vx = np.array([self.dadr[s] for s in SAMPLE_IDS])
        self.t_hooks: list = []  # callables run after every step

    # --- the hidden law ------------------------------------------------------------

    def set_law(self, law, values: dict[str, float]) -> None:
        """law: a schemas.Law (e.g. world.law); values: its parameter values."""
        self.law = compile_law(law)
        self.theta = [float(values[k]) for k in self.law.param_names]

    def law_force(self, d: mujoco.MjData | None = None) -> tuple[np.ndarray, np.ndarray]:
        """(Fx, Fz) = m * a(m, z, vx, vz) on every sample, from the hidden law."""
        d = self.data if d is None else d
        if self.law is None:
            return np.zeros(len(self.masses)), np.zeros(len(self.masses))
        ax, az = self.law.accel(self.masses, d.qpos[self._qz], d.qvel[self._vx], d.qvel[self._vx + 2], self.theta)
        return self.masses * ax, self.masses * az

    def _passive(self, m, d) -> None:
        fx, fz = self.law_force(d)
        d.qfrc_passive[self._vx] += fx
        d.qfrc_passive[self._vx + 2] += fz

    def step(self, n: int = 1) -> None:
        """mj_step with the hidden law as an external force on the samples. The force is added
        in MuJoCo's passive-force callback, so it is re-evaluated at every integrator stage
        (all four stages under RK4, which flights use). The callback is installed only while
        stepping: model compilation also calls it, on data that is not ours."""
        mujoco.set_mjcb_passive(self._passive)
        try:
            for _ in range(n):
                mujoco.mj_step(self.model, self.data)
                for h in self.t_hooks:
                    h(self)
        finally:
            mujoco.set_mjcb_passive(None)

    # --- state helpers ------------------------------------------------------------

    def sample_pos(self, sid: str) -> np.ndarray:
        return self.data.qpos[self.qadr[sid]:self.qadr[sid] + 3].copy()

    def sample_vel(self, sid: str) -> np.ndarray:
        return self.data.qvel[self.dadr[sid]:self.dadr[sid] + 3].copy()

    def place_sample(self, sid: str, pos, vel=(0, 0, 0)) -> None:
        a, v = self.qadr[sid], self.dadr[sid]
        self.data.qpos[a:a + 3] = pos
        self.data.qpos[a + 3:a + 7] = [1, 0, 0, 0]
        self.data.qvel[v:v + 6] = 0.0
        self.data.qvel[v:v + 3] = vel

    def set_weld(self, kind: str, sid: str, active: bool) -> None:
        """Activate a weld holding the sample at its current pose relative to the hand
        (kind "grasp") or the launcher (kind "load"); or release it."""
        m, d = self.model, self.data
        i = self.eq[(kind, sid)]
        if active:
            b1 = self.hand_body if kind == "grasp" else self.launcher_body
            b2 = self.body[sid]
            mujoco.mj_kinematics(m, d)
            p1, R1 = d.xpos[b1], d.xmat[b1].reshape(3, 3)
            p2, q2 = d.xpos[b2], d.xquat[b2]
            q1inv = np.zeros(4)
            mujoco.mju_negQuat(q1inv, d.xquat[b1])
            rq = np.zeros(4)
            mujoco.mju_mulQuat(rq, q1inv, q2)
            # eq_data: anchor (3), relpos (3), relquat (4), torquescale (1)
            m.eq_data[i, 0:3] = 0.0
            m.eq_data[i, 3:6] = R1.T @ (p2 - p1)
            m.eq_data[i, 6:10] = rq
            m.eq_data[i, 10] = 1.0
        d.eq_active[i] = 1 if active else 0

    def wrist_force(self) -> np.ndarray:
        return self.data.sensordata[self.force_adr:self.force_adr + 3].copy()

    def reset(self, arm_q=HOME_Q) -> None:
        """Arm home, gripper open, launcher at 45 degrees, every sample in its tray seat."""
        m, d = self.model, self.data
        mujoco.mj_resetData(m, d)
        d.qpos[self.arm_qadr] = arm_q
        d.ctrl[:7] = arm_q
        d.qpos[self.finger_qadr] = 0.04
        d.ctrl[self.grip_act] = 255
        d.qpos[self.elev_qadr] = math.radians(45)
        d.ctrl[self.elev_act] = math.radians(45)
        d.eq_active[:] = 0
        for sid in SAMPLE_IDS:
            self.place_sample(sid, tray_pos(sid))
        mujoco.mj_forward(m, d)


def make_scene(targets: list[Target] | None = None, law=None, values: dict | None = None,
               x_end: float = 4.0) -> Scene:
    spec = build_spec(targets, x_end)
    model = spec.compile()
    sc = Scene(model, mujoco.MjData(model))
    if law is not None:
        sc.set_law(law, values)
    sc.reset()
    return sc


def scene_for_world(world, x_end: float = 4.0) -> Scene:
    """Scene with the world's targets and its hidden law (world.generator.World)."""
    values = {k: world.fit.params[k].value for k in world.law.params}
    return make_scene(world.targets, world.law, values, x_end)
