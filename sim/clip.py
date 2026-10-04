"""Side-by-side clip: the textbook shot missing and the discovered-law shot landing, on the
same target (SPEC 10, step 6).

    .venv/bin/python -m sim.clip --ledger <ledger, run dir or id> --out clip.mp4 [--target t4] [--style large]

Left: mission_300 aimed with textbook physics (g = 9.81, Earth air drag) by the same
plan_shot every condition uses, fired into the hidden law. Right: the committed shot from
the ledger. The right landing point is the server's graded one when the run directory has
it; otherwise, like the left one, it gets an actuation error drawn from a seeded stream.
The default target is the beyond-range target where the textbook shot misses by most.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image, ImageDraw

from schemas import LaunchSpec
from tools.analysis import plan_shot
from tools.defaults import RANGES
from world.generator import textbook_law

from .render import (ACCENT, BAD, DIM, F_B, F_H, F_S, FPS, GOOD, INK, MIRROR, W, H, Renderer, Run, add_trail,
                     block, commit_entry, find_ledger, hold, load_run, project, resample, side_cam,
                     until_landing)

PANE_W = W // 2


def textbook_setting(target, sample_id: str = "mission_300"):
    law, fit = textbook_law()
    return plan_shot(law, fit, target, sample_id, RANGES["mission"], n_draws=0)


def render_clip(run: Run, out: Path, target: str | None = None, style: str = "normal",
                final_eval: bool = False, hold_seconds: float = 3.0) -> dict:
    c = commit_entry(run.ledger)
    if not c:
        raise SystemExit("the ledger has no commit")
    shots = {s["target_id"]: s for s in c["shots"]}
    r = Renderer(run, out, final_eval)
    w = r.world
    targets = {t.target_id: t for t in run.session.targets}
    noise = run.session.noise_sd

    def textbook_x(t, k):
        p = textbook_setting(t)
        rng = np.random.default_rng([run.seed, 88, k])
        dv, de = rng.normal(0, noise["speed_frac"]), rng.normal(0, noise["elevation_deg"])
        x = w.shot_x("mission_300", [p.speed_mps], [p.elevation_deg], t.z_m,
                     np.array([[dv]]), np.array([[de]]))[0, 0]
        return p, dv, de, x

    tb = {tid: textbook_x(t, k) for k, (tid, t) in enumerate(targets.items()) if tid in shots}
    if target:
        tid = target
    else:
        beyond = [tid for tid in tb if w.target_kind.get(tid) == "beyond"] or list(tb)
        miss = lambda tid: abs(tb[tid][3] - targets[tid].x_m) if math.isfinite(tb[tid][3]) else 1e9
        tid = max(beyond, key=miss)
    t = targets[tid]
    p_tb, dv_tb, de_tb, _ = tb[tid]
    s = shots[tid]
    spec_tb = LaunchSpec(sample_id="mission_300", speed_mps=p_tb.speed_mps, elevation_deg=p_tb.elevation_deg)
    spec_d = LaunchSpec(sample_id="mission_300", speed_mps=s["speed_mps"], elevation_deg=s["elevation_deg"])
    g = run.graded_shots.get(tid)
    if g is not None:
        dv_d, de_d = r.steer(spec_d, t.z_m, g.get("x_m")), 0.0
    else:
        rng = np.random.default_rng([run.seed, 89])
        dv_d, de_d = rng.normal(0, noise["speed_frac"]), rng.normal(0, noise["elevation_deg"])

    panes = []
    for spec, dv, de in ((spec_tb, dv_tb, de_tb), (spec_d, dv_d, de_d)):
        r.lab.sc.reset()
        aim, fl, f, t_f = r.flight_launch(spec, t.x_m, t.z_m, dv, de, aim_seconds=0.8, max_seconds=10)
        panes.append((aim, fl, f, t_f))
    dur = max((p[2].t if math.isfinite(p[2].t) else 2.0) for p in panes) + 0.25
    out_s = min(3.2, max(1.5, dur))
    frames = []
    for aim, fl, f, t_f in panes:
        seq = aim + resample(fl, t_f, t_f + dur, out_s)
        frames.append(seq + hold(seq[-1], hold_seconds))
    n = max(len(x) for x in frames)
    frames = [x + [x[-1]] * (n - len(x)) for x in frames]

    x_hi = max(t.x_m + 0.6, *(p[2].x + 0.3 for p in panes if math.isfinite(p[2].x)))
    camera = side_cam(-0.4, x_hi, max(1.0, t.z_m + 0.6), aspect=PANE_W / H)
    mr = mujoco.Renderer(r.lab.sc.model, H, PANE_W)
    d = mujoco.MjData(r.lab.sc.model)
    m = r.lab.sc.model
    results = []
    for (aim, fl, f, t_f) in panes:
        x = f.x if math.isfinite(f.x) else math.nan
        results.append((x, abs(x - t.x_m) if math.isfinite(x) else math.inf))
    if g is not None and g.get("x_m") is not None:
        results[1] = (g["x_m"], abs(g["x_m"] - t.x_m))
    titles = ["Textbook physics", f"Discovered law ({c.get('law_id', '')})"]
    specs = [spec_tb, spec_d]
    colors = [(1.0, 0.35, 0.3, 1.0), (0.35, 0.9, 0.45, 1.0)]
    land_t = [p[2].t if math.isfinite(p[2].t) else 1e9 for p in panes]
    paths = [until_landing(p[2]) for p in panes]
    # "large" draws the pane overlays on a 2/3-size canvas, so the type comes out 1.5x bigger.
    pane_canvas = (PANE_W, H) if style == "normal" else (PANE_W * 2 // 3, H * 2 // 3)
    kind = w.target_kind.get(tid, "").replace("_", " ")

    def pane_overlay(layer, scene, j, t_rel):
        cw, ch = layer.size
        dr = ImageDraw.Draw(layer)
        pt = project(scene, [t.x_m, 0, t.z_m], cw, ch)
        if pt:
            dr.line([(pt[0], pt[1] - 90), (pt[0], pt[1] - 10)], fill=ACCENT, width=4)
            dr.text((pt[0] - 18, pt[1] - 128), t.target_id, font=F_H, fill=ACCENT)
        block(layer, 20, 20, cw - 40, [(titles[j], F_H, INK),
                                       (f"{specs[j].speed_mps:.2f} m/s at {specs[j].elevation_deg:.1f}°", F_B, DIM)])
        if t_rel >= land_t[j]:
            x, miss = results[j]
            hit = miss <= t.hit_radius_m
            txt = ("HIT" if hit else "MISS") + (f": {miss * 100:.1f} cm from centre" if math.isfinite(miss)
                                                else ": never came down")
            items = [(txt, F_H, GOOD if hit else BAD),
                     (f"hit radius {t.hit_radius_m * 100:.1f} cm, target {t.target_id} ({kind})", F_S, DIM)]
            h = block(Image.new("RGBA", layer.size), 0, 0, cw - 40, items)
            block(layer, 20, ch - 20 - h, cw - 40, items)

    out.parent.mkdir(parents=True, exist_ok=True)
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                           "-crf", "20", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    MIRROR[0] = True
    for k in range(n):
        canvas = Image.new("RGBA", (W, H))
        for j in range(2):
            sim_t, q = frames[j][k]
            d.qpos[:] = q
            mujoco.mj_kinematics(m, d)
            mr.update_scene(d, camera)
            t_rel = sim_t - panes[j][3]
            if t_rel >= 0:
                add_trail(mr.scene, paths[j][paths[j][:, 0] <= t_rel, 1:4], colors[j])
            img = Image.fromarray(np.ascontiguousarray(mr.render()[:, ::-1])).convert("RGBA")
            layer = Image.new("RGBA", pane_canvas, (0, 0, 0, 0))
            pane_overlay(layer, mr.scene, j, t_rel)
            img.alpha_composite(layer.resize(img.size, Image.LANCZOS) if layer.size != img.size else layer)
            canvas.paste(img, (j * PANE_W, 0))
        ImageDraw.Draw(canvas).line([(PANE_W, 0), (PANE_W, H)], fill=(20, 20, 24), width=4)
        ff.stdin.write(np.asarray(canvas.convert("RGB")).tobytes())
    ff.stdin.close()
    ff.wait()
    return {"out": str(out), "target": tid, "frames": n, "seconds": n / FPS,
            "textbook": {"speed_mps": spec_tb.speed_mps, "elevation_deg": spec_tb.elevation_deg,
                         "x_m": results[0][0], "miss_m": results[0][1]},
            "discovered": {"speed_mps": spec_d.speed_mps, "elevation_deg": spec_d.elevation_deg,
                           "x_m": results[1][0], "miss_m": results[1][1]},
            "hit_radius_m": t.hit_radius_m, "graded_by_server": g is not None}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", required=True, help="ledger.jsonl, a run directory, or a run/session id")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--target", help="target id (default: beyond-range target with the worst textbook miss)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--style", choices=("normal", "large"), default="normal")
    ap.add_argument("--final-eval", action="store_true")
    args = ap.parse_args()
    run = load_run(find_ledger(args.ledger), args.seed)
    info = render_clip(run, args.out, args.target, args.style, args.final_eval)
    print(json.dumps(info, indent=1, default=float))


if __name__ == "__main__":
    main()
