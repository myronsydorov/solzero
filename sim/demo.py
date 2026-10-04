"""The three demo assets for one run, in one go (SPEC 10):

    .venv/bin/python -m sim.demo --run <id | run dir | ledger> [--style normal|large|both] [--out DIR]

- replay.mp4: the full replay (sim.render): shot zero, every experiment, the mission, the reveal.
- clip.mp4: textbook shot missing beside the discovered-law shot landing (sim.clip).
- opener.mp4: exactly 10 seconds of shot zero missing.

--style large writes the variant with 1.5x overlay type (readable when the video plays small);
both writes the two sets (`*_large.mp4`). Mission hits and misses come from the server's admin
score when the run directory has it (eval runner: attempt*/admin_score.json; viewer:
metrics.json; sim.oracle_ledger: world_session.json). demo.json records which was used.
Outputs go to runs/demo/<run name>/ unless --out is given. --final-eval is for the frozen
demo run only.
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")  # renders run in parallel processes

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .render import RUN_ROOTS, Renderer, _sidecar, find_ledger, load_run


def run_name(ledger: Path) -> str:
    p = ledger.parent
    if p.name == "agent" and p.parent.name.startswith("attempt"):  # eval: <label>/<seed>/attemptN/agent
        return f"{p.parent.parent.parent.name}_{p.parent.parent.name}"
    return p.name


def _task(kind: str, ledger: str, seed, out: str, style: str, final_eval: bool, target) -> dict:
    t0 = time.perf_counter()
    run = load_run(Path(ledger), seed)
    if kind == "clip":
        from .clip import render_clip

        info = render_clip(run, Path(out), target, style, final_eval)
    else:
        with Renderer(run, Path(out), final_eval, style=style) as r:
            info = r.render() if kind == "replay" else r.opener(10.0)
        info["out"] = out
    info["kind"], info["style"], info["render_s"] = kind, style, time.perf_counter() - t0
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True, help="run id or session id (searched under runs/ and "
                    "viewer/public/runs/), a run directory, or a ledger.jsonl")
    ap.add_argument("--out", type=Path, help="output directory (default runs/demo/<run name>)")
    ap.add_argument("--style", choices=("normal", "large", "both"), default="normal")
    ap.add_argument("--seed", type=int, help="world seed, for a bare ledger")
    ap.add_argument("--target", help="clip target (default: worst textbook miss beyond range)")
    ap.add_argument("--final-eval", action="store_true", help="allow a frozen test world (demo run only)")
    ap.add_argument("--roots", nargs="*", type=Path, default=list(RUN_ROOTS))
    args = ap.parse_args()

    ledger = find_ledger(args.run, args.roots)
    run = load_run(ledger, args.seed)  # fail early, before spawning renderers
    out = args.out or Path("runs/demo") / run_name(ledger)
    out.mkdir(parents=True, exist_ok=True)
    styles = ["normal", "large"] if args.style == "both" else [args.style]
    tasks = []
    for st in styles:
        sfx = "" if st == "normal" else "_large"
        for kind in ("replay", "clip", "opener"):
            tasks.append((kind, str(ledger), args.seed, str(out / f"{kind}{sfx}.mp4"), st, args.final_eval, args.target))
    t0 = time.perf_counter()
    with ProcessPoolExecutor(len(tasks)) as ex:
        infos = list(ex.map(_task, *zip(*tasks)))
    sources = {name: str(f) if (f := _sidecar(ledger, name)) else None
               for name in ("session_info.json", "admin_score.json", "admin-score-persisted.json", "truth.json",
                            "metrics.json", "world_session.json")}
    manifest = {"run": args.run, "ledger": str(ledger), "label": run.label, "seed": run.seed,
                "session_id": run.session.session_id,
                "mission_grade": "server admin score" if run.graded_shots else "simulated (no server grade found)",
                "sources": sources, "wall_s": time.perf_counter() - t0, "assets": infos}
    (out / "demo.json").write_text(json.dumps(manifest, indent=1, default=float))
    for i in infos:
        print(f"{i['kind']:7s} {i['style']:6s} {i['seconds']:5.1f} s  {i['out']}")
    print(f"mission grade: {manifest['mission_grade']}; manifest {out / 'demo.json'}")


if __name__ == "__main__":
    main()
