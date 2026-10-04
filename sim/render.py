"""Replay renderer (SPEC 10 demo): a session ledger played back by the MuJoCo lab.

    .venv/bin/python -m sim.render --ledger runs/<id>/ledger.jsonl --out video.mp4

Order: title and shot zero; each experiment executed by the arm in full mode with overlays
(experiment number, spec, each live law's pre-registered prediction, the measured value,
verdicts, the plan-changed flag); the five mission shots with trajectories and hit or miss;
the hidden law beside the discovered one. 1920x1080, 30 fps, offscreen.

The world comes from metrics.json next to the ledger (SPEC 5.7: truth.seed), or from
session_info.json + world_session.json (sim.oracle_ledger), or --seed. Numbers on screen
are the ledger's (the server's measurements); the arm re-executes each spec noiselessly, and
mission shots are steered to the server's graded landing points when those are known.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib
import mujoco
import numpy as np
import sympy
from PIL import Image, ImageDraw, ImageFont

from schemas import LAW_VARIABLES, LaunchSpec, SessionInfo, Target, parse_spec
from world.generator import DEV_SEEDS, make_world, textbook_law
from world.server import world_id_for

from .arm import aim_launcher, fire
from .experiment import Lab, fly
from .scene import ARM_BASE, MUZZLE

W, H, FPS = 1920, 1080, 30
FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"


def font(size: int, mono: bool = False, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSansMono" if mono else "DejaVuSans"
    if bold:
        name += "-Bold"
    return ImageFont.truetype(str(FONT_DIR / f"{name}.ttf"), size)


F_TITLE, F_H, F_B, F_S = font(40, bold=True), font(30, bold=True), font(24), font(20)
F_M, F_MS = font(22, mono=True), font(19, mono=True)
INK = (240, 240, 236)
DIM = (170, 172, 178)
ACCENT = (255, 176, 59)
GOOD, BAD, MID = (92, 214, 120), (255, 102, 92), (240, 200, 90)
VERDICT_RGB = {"supported": GOOD, "rejected": BAD, "insufficient_evidence": MID}


# --- inputs -------------------------------------------------------------------------


@dataclass
class Run:
    ledger: list[dict]
    session: SessionInfo
    seed: int
    label: str
    graded_shots: dict = field(default_factory=dict)  # target_id -> server-graded shot dict


RUN_ROOTS = (Path("runs"), Path("viewer/public/runs"))


def find_ledger(run: str | Path, roots=RUN_ROOTS) -> Path:
    """A ledger path from: a ledger file; a run directory (ledger.jsonl or agent/ledger.jsonl);
    an eval directory <label>/<seed> (the attempt named in state.json, else the latest); or a
    run id or session id searched for under `roots`."""
    p = Path(run)
    if p.is_file():
        return p
    if p.is_dir():
        for cand in (p / "ledger.jsonl", p / "agent" / "ledger.jsonl"):
            if cand.exists():
                return cand
        if (p / "state.json").exists():
            att = json.loads((p / "state.json").read_text()).get("attempt_dir")
            if att and (p / att / "agent" / "ledger.jsonl").exists():
                return p / att / "agent" / "ledger.jsonl"
        atts = sorted(p.glob("attempt*/agent/ledger.jsonl"))
        if atts:
            return atts[-1]
        raise SystemExit(f"no ledger.jsonl in {p}")
    hits = []
    for root in roots:
        for led in Path(root).rglob("ledger.jsonl") if Path(root).exists() else ():
            names = {q.name for q in led.parents}
            sid = _sidecar(led, "session_info.json")
            if str(run) in names or (sid and json.loads(sid.read_text()).get("session_id") == str(run)):
                hits.append(led)
    if not hits:
        raise SystemExit(f"no run {run!r} under {', '.join(map(str, roots))}")
    return sorted(hits)[-1]


def _sidecar(ledger_path: Path, name: str) -> Path | None:
    """name in the ledger's directory or one of the two above it (eval: attempt/agent/ledger)."""
    for d in (ledger_path.parent, ledger_path.parent.parent, ledger_path.parent.parent.parent):
        if (d / name).exists():
            return d / name
    return None


def load_run(ledger_path: Path, seed: int | None = None) -> Run:
    """Ledger plus what the renderer needs beside it. Accepted layouts:
    - viewer (SPEC 5.7): metrics.json {session_info, score, truth};
    - eval runner: attempt*/session_info.json, admin_score.json (GET /admin/score), truth.json;
    - lane B's own runs: admin-score-persisted.json (the server's /admin/score record);
    - sim.oracle_ledger: session_info.json, world_session.json;
    - a bare ledger with --seed (SessionInfo is rebuilt from the world; no server grade)."""
    ledger_path = Path(ledger_path)
    ledger = [json.loads(line) for line in ledger_path.read_text().splitlines() if line.strip()]
    read = lambda name: json.loads(f.read_text()) if (f := _sidecar(ledger_path, name)) else None
    metrics = read("metrics.json") or {}
    session = read("session_info.json") or metrics.get("session_info")
    score = (read("admin_score.json") or read("admin-score-persisted.json") or read("admin-score.json")
             or read("world_session.json") or metrics.get("score"))
    truth = read("truth.json") or metrics.get("truth") or {}
    label = metrics.get("label") or "/".join(ledger_path.parent.parts[-3:])
    if seed is None:
        seed = truth.get("seed", metrics.get("seed"))
    world_id = truth.get("world_id") or (score or {}).get("world_id")
    if seed is None and world_id:
        hit = re.fullmatch(r"w(\d+)", world_id)
        seed = int(hit.group(1)) if hit else next((s for s in DEV_SEEDS if world_id_for(s) == world_id), None)
    if seed is None:
        raise SystemExit("cannot find the world: no metrics.json, truth.json or world_session.json beside the "
                         "ledger; pass --seed")
    if not metrics.get("session_info") and metrics.get("condition"):  # eval runner metrics
        where = "dev world" if int(seed) in DEV_SEEDS else "test world"
        label = f"{metrics.get('label', metrics['condition'])} run on {where} {seed}"
    if not metrics.get("label") and (score or {}).get("condition"):  # lane B / oracle run directories
        where = "dev world" if int(seed) in DEV_SEEDS else "test world"
        label = f"{score['condition']} session {score.get('session_id', '')} on {where} {seed}"
    if session is None:  # a bare ledger: SessionInfo is a deterministic function of the world
        session = _session_from_world(seed, ledger)
    graded = {s["target_id"]: s for s in ((score or {}).get("commit") or {}).get("shots", [])}
    return Run(ledger, SessionInfo.model_validate(session), int(seed), str(label), graded)


def _session_from_world(seed: int, ledger: list[dict]) -> dict:
    from tools.defaults import LAUNCHER, NOISE_SD, RANGES, SAMPLES

    w = make_world(seed)
    sid = next((e["payload"].get("session_id") for e in ledger if e["payload"].get("session_id")), "s_unknown")
    return SessionInfo(session_id=sid, budget=12, samples=SAMPLES, ranges={k: dict(v) for k, v in RANGES.items()},
                       noise_sd=dict(NOISE_SD), launcher=LAUNCHER, targets=w.targets,
                       shot_zero=w.shot_zero).model_dump()


def cycles(ledger: list[dict]) -> list[dict]:
    """One dict per experiment: the result plus that cycle's other entries (latest of each kind)."""
    by_cycle: dict[int, dict] = {}
    for e in ledger:
        by_cycle.setdefault(e["cycle"], {})[e["kind"]] = e["payload"]
    return [by_cycle[c] | {"cycle": c} for c in sorted(by_cycle) if "result" in by_cycle[c]]


def commit_entry(ledger) -> dict | None:
    return next((e["payload"] for e in reversed(ledger) if e["kind"] == "commit"), None)


def discovered_law(ledger, law_id: str) -> tuple[dict | None, dict | None]:
    """(Law, FitResult) dicts for the committed law: the commit payload if it carries them,
    else the latest nomination of that law_id, else its latest law_set entry."""
    c = commit_entry(ledger) or {}
    if c.get("law") and c.get("fit"):
        return c["law"], c["fit"]
    for e in reversed(ledger):
        p = e["payload"]
        if e["kind"] == "nomination" and p.get("law", {}).get("law_id") == law_id:
            return p["law"], p.get("fit")
    for e in reversed(ledger):
        if e["kind"] == "law_set":
            for law in e["payload"].get("laws", []):
                if law["law_id"] == law_id:
                    return law, None
    return None, None


# --- text helpers ------------------------------------------------------------------------


def spec_text(spec) -> str:
    s = spec if isinstance(spec, dict) else spec.model_dump()
    if s["type"] == "launch":
        return f"launch {s['sample_id']} at {s['speed_mps']:.2f} m/s, {s['elevation_deg']:.1f}°"
    verb = "weigh" if s["type"] == "weigh" else "drop"
    return f"{verb} {s['sample_id']} at {s['height_m']:.2f} m"


UNITS = {"force_n": ("F", "N", 3), "fall_time_s": ("t", "s", 4), "landing_x_m": ("x", "m", 3),
         "flight_time_s": ("t", "s", 3)}


def obs_text(name: str, v) -> str:
    sym, unit, nd = UNITS.get(name, (name, "", 3))
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return f"{sym} = n/a"
    return f"{sym} = {v:.{nd}f} {unit}"


def law_formula(law: dict, values: dict[str, float] | None) -> tuple[str, str]:
    """(ax, az) strings with fitted values substituted, 3 significant figures."""
    names = list(LAW_VARIABLES) + list(law.get("params", {}))
    local = {n: sympy.Symbol(n) for n in names}
    out = []
    for key in ("ax", "az"):
        e = sympy.parse_expr(law[key], local_dict=local)
        if values:
            e = e.subs({local[k]: v for k, v in values.items() if k in local})
        e = e.evalf(3)
        out.append(str(e).replace("**", "^").replace("*", "·"))
    return out[0], out[1]


def wrap(draw, text, fnt, width) -> list[str]:
    lines, cur = [], ""
    for word in text.split(" "):
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=fnt) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def panel(img: Image.Image, box, alpha: int = 190) -> None:
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(ov).rounded_rectangle(box, radius=14, fill=(14, 14, 18, alpha))
    img.alpha_composite(ov)


def block(img: Image.Image, x: float, y: float, w: float, items, alpha: int = 190, pad: int = 20,
          gap: int = 6) -> float:
    """A panel at (x, y) of width w holding wrapped lines; returns its bottom edge.

    items: (text, font, fill) or (text, font, fill, right_text, right_fill); right_text is
    right-aligned on the same line. A None item is a small vertical gap."""
    dr = ImageDraw.Draw(img)
    lines = []
    for it in items:
        if it is None:
            lines.append(None)
            continue
        text, fnt, fill = it[:3]
        right = it[3] if len(it) > 3 else None
        avail = w - 2 * pad - (dr.textlength(right, font=fnt) + 16 if right else 0)
        for k, ln in enumerate(wrap(dr, text, fnt, avail) or [""]):
            lines.append((ln, fnt, fill, right if k == 0 else None, it[4] if right and k == 0 else None))
    h = 2 * pad - gap + sum((round(ln[1].size * 1.25) + gap) if ln else 10 for ln in lines)
    panel(img, (x, y, x + w, y + h), alpha)
    yy = y + pad
    for ln in lines:
        if ln is None:
            yy += 10
            continue
        text, fnt, fill, right, rfill = ln
        dr.text((x + pad, yy), text, font=fnt, fill=fill)
        if right:
            dr.text((x + w - pad - dr.textlength(right, font=fnt), yy), right, font=fnt, fill=rfill)
        yy += round(fnt.size * 1.25) + gap
    return y + h


def layout(img: Image.Image) -> dict:
    """Panel geometry for an overlay canvas (1920 wide normally, 1280 for large type)."""
    cw, ch = img.size
    side = round(0.3125 * cw)  # predictions panel
    board = round(0.24 * cw)  # mission scoreboard
    return {"cw": cw, "ch": ch, "m": 30, "side": side, "board": board,
            "head_w": cw - side - 90, "head_w_board": cw - board - 90}


# --- cameras and projection ------------------------------------------------------------------


class Cam(mujoco.MjvCamera):
    mirror = False


def cam(lookat, distance, azimuth, elevation) -> Cam:
    c = Cam()
    c.lookat[:] = lookat
    c.distance, c.azimuth, c.elevation = distance, azimuth, elevation
    return c


LAB_CAM = cam([-0.12, -0.32, 0.42], 3.0, 128, -20)


def side_cam(x_lo: float, x_hi: float, z_hi: float = 1.0, aspect: float = W / H) -> mujoco.MjvCamera:
    """Side view of the launch plane framing x in [x_lo, x_hi] (fovy 45 degrees)."""
    span = max(x_hi - x_lo, 1.2)
    t = math.tan(math.radians(22.5))
    dist = max(span / (2 * t * aspect), (z_hi + 0.3) / (2 * t)) * 1.12
    c = cam([(x_lo + x_hi) / 2, 0.0, max(0.25, z_hi * 0.45)], dist, -90, -8)
    c.mirror = True  # seen from the far side (the arm is not in the way), flipped so x runs left to right
    return c


MIRROR = [False]  # whether the frame being drawn is mirrored (set by Renderer.emit)


def project(scene: mujoco.MjvScene, p, width: int = W, height: int = H):
    """World point to pixel coordinates for the first scene camera (None if behind)."""
    c = scene.camera[0]
    fwd, up = np.array(c.forward), np.array(c.up)
    right = np.cross(fwd, up)
    r = np.asarray(p) - np.array(c.pos)
    z = r @ fwd
    if z <= 1e-6:
        return None
    xs, ys = c.frustum_near * (r @ right) / z, c.frustum_near * (r @ up) / z
    # frustum_width 0 means "from the viewport aspect" (MuJoCo fills it in at render time)
    hw = c.frustum_width if c.frustum_width > 0 else 0.5 * (c.frustum_top - c.frustum_bottom) * width / height
    u = (xs - (c.frustum_center - hw)) / (2 * hw) * width
    if MIRROR[0]:
        u = width - u
    v = (c.frustum_top - ys) / (c.frustum_top - c.frustum_bottom) * height
    return float(u), float(v)


def add_trail(scene: mujoco.MjvScene, pts: np.ndarray, rgba, width: float = 0.006) -> None:
    for a, b in zip(pts[:-1], pts[1:]):
        if scene.ngeom >= scene.maxgeom or np.linalg.norm(b - a) < 1e-6:
            continue
        g = scene.geoms[scene.ngeom]
        mujoco.mjv_initGeom(g, mujoco.mjtGeom.mjGEOM_CAPSULE, np.zeros(3), np.zeros(3), np.zeros(9),
                            np.array(rgba, np.float32))
        mujoco.mjv_connector(g, mujoco.mjtGeom.mjGEOM_CAPSULE, width, a, b)
        scene.ngeom += 1


# --- recording ----------------------------------------------------------------------------


@dataclass
class Shot:
    """Frames to render: sim states sampled at output times, plus how to draw them."""

    states: list = field(default_factory=list)  # (sim_t, qpos)
    camera: object = None  # MjvCamera, or callable(sim_t) -> MjvCamera
    overlay: object = None  # callable(img, scene, sim_t)
    trails: object = None  # callable(sim_t) -> list[(pts, rgba)]


class Recorder:
    def __init__(self, lab: Lab):
        self.lab = lab
        self.raw: list[tuple[float, np.ndarray]] = []

    def __call__(self, sc) -> None:
        self.raw.append((sc.data.time, sc.data.qpos.copy()))

    def take(self) -> list[tuple[float, np.ndarray]]:
        out, self.raw = self.raw, []
        return out


def resample(raw, t0: float, t1: float, seconds: float) -> list:
    """States at evenly spaced sim times in [t0, t1] that fill `seconds` of output."""
    pts = [r for r in raw if t0 - 1e-9 <= r[0] <= t1 + 1e-9]
    n = max(1, int(round(seconds * FPS)))
    if not pts:
        return []
    ts = np.array([p[0] for p in pts])
    out = []
    for k in range(n):
        t = t0 + (t1 - t0) * k / max(1, n - 1)
        j = int(np.clip(np.searchsorted(ts, t), 0, len(pts) - 1))
        out.append(pts[j])
    return out


def until_landing(flight) -> np.ndarray:
    """Flight path (t, x, y, z) cut where the sample came down (the tail is not drawn)."""
    p = flight.path
    return p[p[:, 0] <= flight.t + 1e-9] if math.isfinite(flight.t) else p


def hold(state, seconds: float) -> list:
    return [state] * max(1, int(round(seconds * FPS)))


# --- the video ---------------------------------------------------------------------------------


class Renderer:
    def __init__(self, run: Run, out: Path, final_eval: bool = False, width: int = W, height: int = H,
                 arm_seconds: float = 2.2, result_seconds: float = 1.2, style: str = "normal"):
        self.run = run
        # Overlays are drawn on this canvas and scaled to the frame: "large" draws on 1280x720,
        # so every overlay element comes out 1.5 times bigger (readable when the video is small).
        self.canvas = {"normal": (W, H), "large": (1280, 720)}[style]
        self.world = make_world(run.seed, final_eval=final_eval)
        self.out = out
        self.w, self.h = width, height
        self.arm_seconds, self.result_seconds = arm_seconds, result_seconds
        # Shot zero's practice target is drawn too (it is not one of the world's targets).
        self.t0 = Target(target_id="t0", x_m=1.2, z_m=0.0)
        x_far = max([t.x_m for t in self.world.targets] + [5.0])
        self.lab = Lab(self.world, x_end=x_far + 1.0, extra_targets=[self.t0])
        self.rec = Recorder(self.lab)
        self.lab.on_frame = self.rec
        self.lab.arm.on_frame = self.rec
        self.mr = mujoco.Renderer(self.lab.sc.model, H, W)
        self.mr.scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = True
        self.render_data = mujoco.MjData(self.lab.sc.model)
        self.n_frames = 0

    # ffmpeg sink
    def __enter__(self):
        self.ff = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{self.w}x{self.h}", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium", "-movflags", "+faststart",
             str(self.out)], stdin=subprocess.PIPE)
        return self

    def __exit__(self, *exc):
        self.ff.stdin.close()
        self.ff.wait()

    def emit(self, shot: Shot) -> None:
        m, d = self.lab.sc.model, self.render_data
        for sim_t, q in shot.states:
            d.qpos[:] = q
            mujoco.mj_kinematics(m, d)
            c = shot.camera(sim_t) if callable(shot.camera) else shot.camera
            self.mr.update_scene(d, c)
            for pts, rgba in (shot.trails(sim_t) if shot.trails else []):
                add_trail(self.mr.scene, pts, rgba)
            pix = self.mr.render()
            mirror = getattr(c, "mirror", False)
            if mirror:
                pix = pix[:, ::-1]
            img = Image.fromarray(np.ascontiguousarray(pix)).convert("RGBA")
            MIRROR[0] = mirror
            if shot.overlay:
                layer = Image.new("RGBA", self.canvas, (0, 0, 0, 0))
                shot.overlay(layer, self.mr.scene, sim_t)
                if layer.size != img.size:
                    layer = layer.resize(img.size, Image.LANCZOS)
                img.alpha_composite(layer)
            if img.size != (self.w, self.h):
                img = img.resize((self.w, self.h), Image.LANCZOS)
            self.ff.stdin.write(np.asarray(img.convert("RGB")).tobytes())
            self.n_frames += 1

    def card(self, draw_fn, seconds: float, camera=None) -> None:
        camera = camera or LAB_CAM
        state = (self.lab.sc.data.time, self.lab.sc.data.qpos.copy())
        self.emit(Shot(hold(state, seconds), camera, lambda img, sc, t: draw_fn(img)))

    # --- pieces -------------------------------------------------------------------------

    def header(self, img, title: str, sub: str = "", extra=(), board: bool = False) -> float:
        L = layout(img)
        items = [(title, F_H, ACCENT)] + ([(sub, F_B, INK)] if sub else []) + list(extra)
        return block(img, L["m"], 26, L["head_w_board" if board else "head_w"], items)

    def footer(self, img, items) -> None:
        """A panel anchored to the bottom-left corner."""
        L = layout(img)
        probe = Image.new("RGBA", img.size)
        h = block(probe, 0, 0, min(L["cw"] - 2 * L["m"], 1000), items)
        block(img, L["m"], L["ch"] - 30 - h, min(L["cw"] - 2 * L["m"], 1000), items)

    def flight_launch(self, spec: LaunchSpec, x_target: float | None, z_stop: float = 0.0,
                      dv: float = 0.0, de: float = 0.0, aim_seconds: float = 0.7, max_seconds: float = 1.6):
        """Aim and fire one launch (no arm); returns (states, flight, sim time of firing)."""
        sc = self.lab.sc
        sid = spec.sample_id
        sc.place_sample(sid, MUZZLE)
        sc.set_weld("load", sid, True)
        self.rec.take()
        t_a = sc.data.time
        aim_launcher(sc, spec.elevation_deg, on_tick=self.rec)
        aim_states = resample(self.rec.take(), t_a, sc.data.time, aim_seconds)
        t_f = sc.data.time
        fire(sc, sid, spec.speed_mps * (1 + dv), spec.elevation_deg + de)
        f = fly(sc, sid, z_stop, self.rec, tail=0.25)
        raw = self.flight_raw = self.rec.take()
        dur = (f.t if math.isfinite(f.t) else 2.0) + 0.25
        fl_states = resample(raw, t_f, t_f + dur, min(max_seconds, max(0.6, dur)))
        return aim_states, fl_states, f, t_f

    def steer(self, spec: LaunchSpec, z_stop: float, x_goal: float | None) -> float:
        """Fractional speed error dv so the MuJoCo shot lands at the server's graded point."""
        if x_goal is None or not math.isfinite(x_goal):
            return 0.0
        dv, step = 0.0, 1e-3
        for _ in range(6):
            x0 = self.lab.measure(spec, "fast", dv=dv, z_stop=z_stop)["landing_x_m"]
            x1 = self.lab.measure(spec, "fast", dv=dv + step, z_stop=z_stop)["landing_x_m"]
            if not (math.isfinite(x0) and math.isfinite(x1)) or x1 == x0:
                return dv
            if abs(x0 - x_goal) < 2e-4:
                break
            dv += (x_goal - x0) * step / (x1 - x0)
        return float(np.clip(dv, -0.05, 0.05))

    # --- segments -------------------------------------------------------------------------

    def intro(self) -> None:
        run, sz = self.run, self.run.session.shot_zero
        self.lab.sc.reset()
        self.lab.arm.reset()

        def title(img):
            L = layout(img)
            block(img, L["m"], 26, L["head_w"], [
                ("SOL ZERO", F_TITLE, ACCENT),
                ("A robot lab must find an unfamiliar force law in 12 experiments, "
                 "then hit five untouched targets, one shot each.", F_B, INK),
                (run.label, F_S, DIM)])

        self.card(title, 2.2)
        spec = sz.spec
        dv = self.steer(spec, 0.0, sz.landing_x_m)
        self.lab.sc.reset()
        aim, fl, f, t_f = self.flight_launch(spec, 1.2, 0.0, dv)
        x_hi = max(1.6, (sz.landing_x_m or 1.2) + 0.4)
        camera = side_cam(-0.3, x_hi, 0.8)
        t_land = f.t

        def overlay(img, scene, t):
            self.header(img, "Shot zero: textbook physics (g = 9.81 m/s², Earth air drag)",
                        spec_text(spec) + "  →  practice target t0 at 1.20 m")
            p = project(scene, [1.2, 0, 0.0], *img.size)
            dr = ImageDraw.Draw(img)
            if p:
                dr.line([(p[0], p[1] - 70), (p[0], p[1] - 8)], fill=ACCENT, width=4)
                dr.text((p[0] - 20, p[1] - 105), "t0", font=F_H, fill=ACCENT)
            if t - t_f >= t_land:
                self.footer(img, [(f"Landed at {sz.landing_x_m:.3f} m: missed by {sz.miss_m * 100:.1f} cm. "
                                   "Physics here is not Earth's.", F_B, BAD)])

        fp = until_landing(f)
        trails = lambda t: [(fp[fp[:, 0] <= t - t_f, 1:4], (1.0, 0.45, 0.2, 1.0))] if t >= t_f else []
        self.emit(Shot(aim + fl + hold(fl[-1], 1.4), camera, overlay, trails))

    def experiment(self, cy: dict, n_total: int) -> None:
        res = cy["result"]
        spec = parse_spec(res["spec"])
        idx = res.get("index", cy["cycle"])
        table = cy.get("prediction_table") or {}
        preds = table.get("predictions", [])
        verdicts = {v["law_id"]: v for v in (cy.get("verdicts") or {}).get("verdicts", [])}
        diff = cy.get("decision_diff")
        lab, sc = self.lab, self.lab.sc

        self.rec.take()
        try:
            lab.measure(spec, "full")
            failed = None
        except Exception as exc:  # show the attempt even if a primitive fails
            failed = str(exc)
        raw = self.rec.take()
        ev = dict((name, t) for t, name in reversed(lab.events))
        t_start = raw[0][0] if raw else sc.data.time
        if spec.type == "weigh":
            t_meas = ev.get("hold", t_start) + 0.5
            t_flight = t_meas
            t_end = raw[-1][0] if raw else t_meas
            states = resample(raw, t_start, t_meas, self.arm_seconds) + hold(raw[-1] if raw else None, self.result_seconds + 0.4)
            camera = LAB_CAM
            flight = None
        else:
            t_flight = ev.get("release", ev.get("fire", t_start))
            flight = lab.last_flight
            dur = (flight.t if flight and math.isfinite(flight.t) else 1.0)
            t_meas = t_flight + dur
            t_end = t_meas + 0.25
            fl_out = min(1.5, max(0.5, dur))
            states = (resample(raw, t_start, t_flight, self.arm_seconds)
                      + resample(raw, t_flight, t_end, fl_out))
            states += hold(states[-1], self.result_seconds)
            if spec.type == "launch":
                x = res["observables"].get("landing_x_m")
                x = x if x is not None and math.isfinite(x) else 1.0
                lab_c, rng_c = LAB_CAM, side_cam(-0.4, max(1.4, x + 0.3), 1.0)
                camera = lambda t: rng_c if t >= t_flight else lab_c
            else:
                camera = LAB_CAM
        states = [s for s in states if s is not None]
        path = until_landing(flight) if flight is not None else None

        def trails(t):
            if path is None or t < t_flight:
                return []
            return [(path[path[:, 0] <= t - t_flight, 1:4], (1.0, 0.45, 0.2, 1.0))]

        obs = res.get("observables", {})

        def overlay(img, scene, t):
            L = layout(img)
            dr = ImageDraw.Draw(img)
            extra = []
            if diff and diff.get("tentative") is not None:
                extra = [None, ("PLAN CHANGED BY EVIDENCE: tentative was " + spec_text(diff["tentative"]), F_S, ACCENT)
                         if diff.get("changed") else ("Plan kept: this was the tentative follow-up", F_S, DIM)]
            self.header(img, f"Experiment {idx} of {n_total}", spec_text(spec), extra)
            narrow = L["side"] < 500
            items = [("Pre-registered predictions", F_H, INK)]
            if not preds:
                items.append(("no live laws yet: nothing to predict", F_S, DIM))
            for p in preds:
                v = verdicts.get(p["law_id"]) if t >= t_meas else None
                right, rcol = None, None
                if v:
                    rcol = VERDICT_RGB.get(v["verdict"], DIM)
                    tag = ({"supported": "✓", "rejected": "✗", "insufficient_evidence": "?"} if narrow else
                           {"supported": "supported", "rejected": "REJECTED", "insufficient_evidence": "unclear"}
                           ).get(v["verdict"], v["verdict"])
                    right = f"{tag} z={v['z']:+.1f}"
                parts = [f"{UNITS.get(k, (k,))[0]} {e['mean']:.3f}±{e['sd']:.3f}"
                         for k, e in p["observables"].items()
                         if e.get("mean") is not None and math.isfinite(e["mean"])]
                items += [None, (p["law_id"][:22], F_M, INK, right, rcol),
                          ("   ".join(parts) or "no landing predicted", F_MS, DIM)]
            if t >= t_meas:
                meas = "   ".join(obs_text(k, v) for k, v in obs.items())
                if res.get("status") == "failed" or failed:
                    meas = "run failed"
                if narrow and not (res.get("status") == "failed" or failed):
                    items += [None, ("Measured", F_B, ACCENT)] + [(obs_text(k, v), F_B, ACCENT) for k, v in obs.items()]
                else:
                    items += [None, ("Measured: " + meas, F_B, ACCENT)]
            block(img, L["cw"] - L["m"] - L["side"], 26, L["side"], items)
            # budget bar
            step = 34 if L["cw"] >= 1600 else 26
            bx, by = L["m"], L["ch"] - 50
            label = f"budget: {n_total - idx} left"
            panel(img, (bx - 12, by - 12, bx + n_total * step + 24 + dr.textlength(label, font=F_S), by + 30))
            for k in range(n_total):
                col = ACCENT if k < idx else (60, 60, 66)
                dr.rectangle((bx + k * step, by, bx + k * step + step - 8, by + 18), fill=col)
            dr.text((bx + n_total * step + 10, by - 4), label, font=F_S, fill=INK)

        self.emit(Shot(states, camera, overlay, trails))

    def mission(self) -> list[dict]:
        c = commit_entry(self.run.ledger)
        if not c:
            return []
        targets = {t.target_id: t for t in self.run.session.targets}
        shots = [s for s in c.get("shots", []) if s["target_id"] in targets]
        x_hi = max(t.x_m for t in targets.values()) + 0.5
        z_hi = 1.0
        camera = side_cam(-0.4, x_hi, z_hi)
        done: list[tuple[np.ndarray, tuple]] = []
        results = []
        self.lab.sc.reset()
        self.lab.arm.reset()
        kind = {t.target_id: self.world.target_kind.get(t.target_id, "") for t in self.world.targets}
        for k, s in enumerate(shots):
            t = targets[s["target_id"]]
            spec = LaunchSpec(sample_id="mission_300", speed_mps=s["speed_mps"], elevation_deg=s["elevation_deg"])
            g = self.run.graded_shots.get(t.target_id)
            dv = self.steer(spec, t.z_m, g.get("x_m") if g else None)
            if g is None:  # no server grade: draw the actuation error from a seeded stream
                rng = np.random.default_rng([self.run.seed, 77, k])
                dv = rng.normal(0, self.run.session.noise_sd["speed_frac"])
            self.lab.sc.reset()
            aim, fl, f, t_f = self.flight_launch(spec, t.x_m, t.z_m, dv)
            x = g["x_m"] if g and g.get("x_m") is not None else f.x
            miss = abs(x - t.x_m) if x is not None and math.isfinite(x) else math.inf
            hit = g["hit"] if g else bool(miss <= t.hit_radius_m)
            results.append({"target_id": t.target_id, "kind": kind.get(t.target_id, ""), "hit": hit,
                            "miss_m": miss, "x_m": x})
            path = until_landing(f)
            col = (0.35, 0.9, 0.45, 1.0) if hit else (1.0, 0.35, 0.3, 1.0)
            prev = list(done)
            t_land = f.t if math.isfinite(f.t) else 1e9

            def trails(tt, path=path, col=col, prev=prev, t_f=t_f):
                cur = [(path[path[:, 0] <= tt - t_f, 1:4], (1.0, 0.6, 0.2, 1.0))] if tt >= t_f else []
                return prev + cur

            def overlay(img, scene, tt, k=k, t=t, spec=spec, hit=hit, miss=miss, t_f=t_f, t_land=t_land):
                self.header(img, f"Mission shot {k + 1} of {len(shots)}: target {t.target_id} "
                                 f"({kind.get(t.target_id, '').replace('_', ' ')})",
                            f"mission_300 at {spec.speed_mps:.2f} m/s, {spec.elevation_deg:.1f}°: "
                            f"untested sample, one shot", board=True)
                self.scoreboard(img, scene, results[:k] + ([results[k]] if tt - t_f >= t_land else []),
                                targets)
            self.emit(Shot(aim + fl + hold(fl[-1], 0.5), camera, overlay, trails))
            done.append((path[:, 1:4], col))
        self.mission_camera = camera
        self.mission_trails = done
        return results

    def scoreboard(self, img, scene, results, targets) -> float:
        """Target labels in the scene plus the hit/miss panel; returns the panel's bottom edge."""
        dr = ImageDraw.Draw(img)
        for t in (targets.values() if scene is not None else ()):
            p = project(scene, [t.x_m, 0, t.z_m], *img.size)
            if p:
                dr.text((p[0] - 14, p[1] + 14), t.target_id, font=F_S, fill=INK)
        L = layout(img)
        by = {r["target_id"]: r for r in results}
        items = [("Targets", F_H, INK), ("graded by the world server" if self.run.graded_shots
                                          else "simulated here: no server grade", F_S, DIM), None]
        for t in targets.values():
            r = by.get(t.target_id)
            right = rcol = None
            if r:
                miss = "∞" if not math.isfinite(r["miss_m"]) else f"{r['miss_m'] * 100:.1f} cm"
                right, rcol = ("HIT " if r["hit"] else "MISS ") + miss, GOOD if r["hit"] else BAD
            items.append((f"{t.target_id} {t.x_m:5.2f} m", F_M, DIM, right, rcol))
        return block(img, L["cw"] - L["m"] - L["board"], 26, L["board"], items)

    def reveal(self, results) -> None:
        c = commit_entry(self.run.ledger) or {}
        law, fit = discovered_law(self.run.ledger, c.get("law_id", ""))
        w = self.world
        truth_vals = {k: w.fit.params[k].value for k in w.law.params}
        tax, taz = law_formula(w.law.model_dump(), truth_vals)
        if law:
            vals = {k: v["value"] for k, v in (fit or {}).get("params", {}).items()} or \
                   {k: v["init"] for k, v in law.get("params", {}).items()}
            dax, daz = law_formula(law, vals)
        targets = {t.target_id: t for t in self.run.session.targets}
        n_hit = sum(r["hit"] for r in results)
        family = {"F0": "ordinary gravity, quadratic drag (control)",
                  "F1": f"drag exponent p = {w.p:g}",
                  "F2": "gravity depends on mass", "F3": "gravity depends on height"}[w.family]

        def draw(img):
            L = layout(img)
            items = [(f"Mission: {n_hit} of {len(results)} targets hit.   Claim: "
                      f"{c.get('claim', 'n/a').replace('_', ' ')}"
                      + ("  (non-ordinary physics)" if c.get("claims_non_ordinary") else ""), F_H, ACCENT), None,
                     (f"Hidden law  ({family})", F_H, INK), ("a_x = " + tax, F_M, DIM), ("a_z = " + taz, F_M, DIM),
                     None, (f"Discovered law  ({c.get('law_id', 'none')})", F_H, INK)]
            if law:
                items += [("a_x = " + dax, F_M, DIM), ("a_z = " + daz, F_M, DIM)]
                if law.get("description") and L["cw"] >= 1600:
                    items.append((law["description"][:160], F_S, DIM))
            else:
                items.append(("no law committed", F_M, DIM))
            probe = Image.new("RGBA", img.size)
            h = block(probe, 0, 0, L["cw"] - 2 * L["m"], items, 215)
            if self.scoreboard(probe, None, results, targets) < L["ch"] - 40 - h:  # only when it fits
                self.scoreboard(img, self.mr.scene, results, targets)
            block(img, L["m"], L["ch"] - 30 - h, L["cw"] - 2 * L["m"], items, 215)

        trails = self.mission_trails if hasattr(self, "mission_trails") else []
        state = (self.lab.sc.data.time, self.lab.sc.data.qpos.copy())
        camera = getattr(self, "mission_camera", LAB_CAM)
        self.emit(Shot(hold(state, 6.0), camera, lambda img, sc, t: draw(img), lambda t: trails))

    def opener(self, seconds: float = 10.0) -> dict:
        """Shot zero missing, as a stand-alone opener of exactly `seconds`: title, the textbook
        shot in slow motion, the miss measured against the practice target, a closing line."""
        sz = self.run.session.shot_zero
        start = self.n_frames
        self.lab.sc.reset()
        self.lab.arm.reset()
        spec = sz.spec
        dv = self.steer(spec, 0.0, sz.landing_x_m)
        self.lab.sc.reset()
        aim, _, f, t_f = self.flight_launch(spec, 1.2, 0.0, dv, aim_seconds=0.8)
        fl = resample(self.flight_raw, t_f, t_f + f.t + 0.05, 2.6)  # slow motion
        x_land = sz.landing_x_m
        camera = side_cam(-0.3, max(1.6, x_land + 0.4), 0.8)
        fp = until_landing(f)
        trails = lambda t: [(fp[fp[:, 0] <= t - t_f, 1:4], (1.0, 0.45, 0.2, 1.0))] if t >= t_f else []

        def title(img):
            L = layout(img)
            block(img, L["m"], 26, L["head_w"], [
                ("SOL ZERO", F_TITLE, ACCENT),
                ("Unknown physics. 12 experiments. Five targets, one shot each.", F_B, INK)])

        def overlay(img, scene, t):
            L = layout(img)
            slow = t_f <= t < t_f + f.t
            self.header(img, "Shot zero: aimed with Earth's physics",
                        "g = 9.81 m/s², Earth air drag" + ("   (slow motion)" if slow else ""))
            dr = ImageDraw.Draw(img)
            p0 = project(scene, [1.2, 0, 0.0], *img.size)
            if p0:
                dr.line([(p0[0], p0[1] - 70), (p0[0], p0[1] - 8)], fill=ACCENT, width=4)
                dr.text((p0[0] - 20, p0[1] - 105), "t0", font=F_H, fill=ACCENT)
            if t - t_f >= f.t:
                p1 = project(scene, [x_land, 0, 0.0], *img.size)
                if p0 and p1:
                    y = max(p0[1], p1[1]) + 24
                    dr.line([(p0[0], y), (p1[0], y)], fill=BAD, width=4)
                    for px in (p0[0], p1[0]):
                        dr.line([(px, y - 10), (px, y + 10)], fill=BAD, width=4)
                    dr.text(((p0[0] + p1[0]) / 2 - 40, y + 12), f"{sz.miss_m * 100:.1f} cm", font=F_H, fill=BAD)
                self.footer(img, [(f"Missed by {sz.miss_m * 100:.1f} cm. Physics here is not Earth's.", F_H, BAD),
                                  ("The lab has 12 experiments to find out why.", F_B, INK)])

        # After the landing the camera eases in on t0 so the miss is visible. Held frames get
        # tiny distinct times so the camera can move while the scene stands still.
        close = side_cam(min(x_land, 1.2) - 0.45, max(x_land, 1.2) + 0.45, 0.35)
        close.lookat[2] = 0.02  # the table plane sits mid-frame, clear of the footer
        n_total = int(round(seconds * FPS))
        n_title = int(round(2.4 * FPS))
        self.card(title, n_title / FPS, camera)
        body = aim + fl
        rest = max(0, n_total - n_title - len(body))
        t_end, q_end = body[-1]
        tail = [(t_end + 1e-3 * (k + 1), q_end) for k in range(rest)]
        n_zoom = int(0.8 * FPS)

        def cam_at(t):
            if t <= t_end:
                return camera
            a = min(1.0, (t - t_end) / 1e-3 / n_zoom)
            a = a * a * (3 - 2 * a)
            c = Cam()
            c.lookat[:] = (1 - a) * np.array(camera.lookat) + a * np.array(close.lookat)
            c.distance = (1 - a) * camera.distance + a * close.distance
            c.azimuth, c.elevation, c.mirror = camera.azimuth, camera.elevation, camera.mirror
            return c

        self.emit(Shot((body + tail)[:n_total - n_title], cam_at, overlay, trails))
        return {"frames": self.n_frames - start, "seconds": (self.n_frames - start) / FPS,
                "miss_m": sz.miss_m}

    def render(self) -> dict:
        cys = cycles(self.run.ledger)
        n_total = self.run.session.budget
        self.intro()
        for cy in cys:
            self.experiment(cy, n_total)
        results = self.mission()
        self.reveal(results)
        return {"frames": self.n_frames, "seconds": self.n_frames / FPS, "experiments": len(cys),
                "mission": results}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", required=True, help="ledger.jsonl, a run directory, or a run/session id")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--seed", type=int, help="world seed, when the run directory does not name it")
    ap.add_argument("--final-eval", action="store_true", help="allow a frozen test world (demo run only)")
    ap.add_argument("--arm-seconds", type=float, default=2.2, help="output seconds per arm sequence")
    ap.add_argument("--max-experiments", type=int, default=None, help="render only the first N (previews)")
    ap.add_argument("--scale", type=float, default=1.0, help="resolution scale (1.0 = 1920x1080)")
    ap.add_argument("--style", choices=("normal", "large"), default="normal",
                    help="large: overlay type 1.5 times bigger, for small playback")
    args = ap.parse_args()
    run = load_run(find_ledger(args.ledger), args.seed)
    if args.max_experiments is not None:
        keep = {c["cycle"] for c in cycles(run.ledger)[:args.max_experiments]}
        last = max(keep) if keep else 0
        run.ledger = [e for e in run.ledger if e["cycle"] <= last or e["kind"] == "commit"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    w, h = int(W * args.scale) // 2 * 2, int(H * args.scale) // 2 * 2
    with Renderer(run, args.out, args.final_eval, w, h, args.arm_seconds, style=args.style) as r:
        info = r.render()
    info["out"] = str(args.out)
    print(json.dumps(info, indent=1, default=float))


if __name__ == "__main__":
    main()
