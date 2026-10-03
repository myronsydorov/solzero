"""Evaluation runner (SPEC.md section 7).

    python -m eval.run --condition oracle --seeds 1000-1002 --out runs/eval --serve
    python -m eval.run --condition random --agent scripted --seeds 1000-1002 --out runs/eval --serve
    python -m eval.run --condition lab --seeds 1000-1002 --out runs/eval --world-url URL --agent-cmd "python -m lab.run"

For every (condition, seed) it opens a session on the world server, hands the SessionInfo
to the agent process (SPEC 5.6), enforces the wall-clock cap, then saves the admin score and
truth and grades the run. Runs go to OUT/<label>/<seed>/. Finished runs are skipped on a
rerun, so the runner resumes after a crash. A crashed agent is retried once, in a new session.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from world.generator import DEV_SEEDS, TEST_SEEDS

from .client import LocalServer, WorldClient
from .grade import grade_run
from .sampler import random_specs

AGENT_CONDITIONS = ("lab", "single", "random")
REFERENCES = ("textbook", "oracle")
DEFAULT_AGENT_CMD = f"{sys.executable} -m lab.run"
MAX_ATTEMPTS = 2
_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_seeds(text: str, final_eval: bool) -> list[int]:
    seeds = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        seeds += list(range(int(a), int(b or a) + 1))
    for s in seeds:
        if s in TEST_SEEDS and not final_eval:
            raise SystemExit(f"seed {s} is a test seed; it needs --final-eval")
        if s not in DEV_SEEDS and s not in TEST_SEEDS:
            raise SystemExit(f"seed {s} is neither a dev nor a test seed")
    return seeds


def label_for(condition: str, agent: str) -> str:
    return f"{condition}-scripted" if condition == "random" and agent == "scripted" else condition


def agent_command(args, condition: str, seed: int, info_path: Path, out: Path, specs_path: Path | None) -> list[str]:
    common = ["--world-url", args.world_url, "--session-info", str(info_path), "--out", str(out),
              "--max-wall-s", str(args.max_wall_s), "--approval", args.approval]
    if args.max_tokens:
        common += ["--max-tokens", str(args.max_tokens)]
    if specs_path:
        common += ["--specs", str(specs_path)]
    if condition in REFERENCES or args.agent == "scripted":
        kind = {"textbook": "textbook", "oracle": "oracle", "random": "scripted_random"}[condition]
        return [sys.executable, "-m", "eval.scripted", "--condition", condition, "--kind", kind,
                "--rng-seed", str(seed), *common]
    return [*shlex.split(args.agent_cmd), "--condition", condition, *common]


def run_one(args, client: WorldClient, worlds: dict[int, str], condition: str, seed: int) -> dict:
    label = label_for(condition, args.agent)
    d = Path(args.out) / label / str(seed)
    d.mkdir(parents=True, exist_ok=True)
    state_path = d / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"attempts": 0}
    if state.get("status") == "done" or (state.get("status") == "failed" and not args.retry_failed):
        return state
    while state["attempts"] < MAX_ATTEMPTS:
        state["attempts"] += 1
        attempt = d / f"attempt{state['attempts']}"
        attempt.mkdir(exist_ok=True)
        world_id = worlds[seed]
        info = client.session(world_id, condition)
        state.update(status="running", session_id=info.session_id, world_id=world_id, seed=seed,
                     condition=condition, label=label, attempt_dir=attempt.name, started=time.time())
        state_path.write_text(json.dumps(state, indent=1))
        info_path = attempt / "session_info.json"
        info_path.write_text(info.model_dump_json(indent=1))
        specs_path = None
        if condition == "random":
            specs_path = attempt / "specs.json"
            specs_path.write_text(json.dumps([s.model_dump() for s in random_specs(seed, info.budget)], indent=1))
        cmd = agent_command(args, condition, seed, info_path, attempt / "agent", specs_path)
        (attempt / "agent").mkdir(exist_ok=True)
        (attempt / "command.json").write_text(json.dumps(cmd))
        t0 = time.time()
        with open(attempt / "agent.log", "w") as logf:
            try:
                rc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, timeout=args.max_wall_s + 60,
                                    env={**os.environ, "SOLZERO_WORLD_URL": args.world_url}).returncode
            except subprocess.TimeoutExpired:
                rc = "timeout"
        summary_path = attempt / "agent" / "summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else None
        (attempt / "admin_score.json").write_text(json.dumps(client.score(info.session_id), indent=1))
        (attempt / "truth.json").write_text(json.dumps(client.truth(world_id), indent=1))
        state.update(returncode=rc, wall_s=time.time() - t0, agent_status=summary and summary.get("status"))
        crashed = rc not in (0,) or summary is None
        if not crashed:
            state["status"] = "done"
            break
        log(f"{label} {seed}: attempt {state['attempts']} crashed (rc={rc})")
        state["status"] = "failed"
    metrics = grade_run(d / state["attempt_dir"], label=label, seed=seed)
    metrics["run_status"] = state["status"]
    (d / "metrics.json").write_text(json.dumps(metrics, indent=1))
    state_path.write_text(json.dumps(state, indent=1))
    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True, choices=[*AGENT_CONDITIONS, *REFERENCES])
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--out", default="runs/eval")
    ap.add_argument("--agent", choices=["cli", "scripted"], default="cli",
                    help="cli: the agent CLI (lab, single, random); scripted: library-fitting stand-in (random)")
    ap.add_argument("--agent-cmd", default=DEFAULT_AGENT_CMD, help="agent CLI, e.g. 'python -m eval.agent_stub'")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--max-wall-s", type=float, default=1800)
    ap.add_argument("--max-tokens", type=int, default=2_000_000)
    ap.add_argument("--approval", choices=["auto", "human"], default="auto")
    ap.add_argument("--world-url", default=os.environ.get("SOLZERO_WORLD_URL"))
    ap.add_argument("--admin-token", default=os.environ.get("SOLZERO_ADMIN_TOKEN"))
    ap.add_argument("--serve", action="store_true", help="start a local world server for this run")
    ap.add_argument("--final-eval", action="store_true")
    ap.add_argument("--retry-failed", action="store_true")
    args = ap.parse_args()
    if args.agent == "scripted" and args.condition in ("lab", "single"):
        raise SystemExit("--agent scripted only applies to the random condition")
    seeds = parse_seeds(args.seeds, args.final_eval)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    server = None
    if args.serve:
        server = LocalServer(out / "_server_sessions", final_eval=args.final_eval, log=out / "server.log")
        args.world_url, args.admin_token = server.url, server.token
    if not args.world_url:
        raise SystemExit("give --world-url (or SOLZERO_WORLD_URL) or --serve")
    client = WorldClient(args.world_url, args.admin_token)
    try:
        worlds = client.worlds()
        missing = [s for s in seeds if s not in worlds]
        if missing:
            raise SystemExit(f"server does not serve seeds {missing}")
        t0 = time.time()
        with ThreadPoolExecutor(args.parallel) as ex:
            futs = {ex.submit(run_one, args, client, worlds, args.condition, s): s for s in seeds}
            for f in as_completed(futs):
                st = f.result()
                log(f"{label_for(args.condition, args.agent)} {futs[f]}: {st.get('status')} "
                    f"({st.get('agent_status')}, {st.get('wall_s', 0):.0f}s)")
        log(f"done in {time.time() - t0:.0f}s")
    finally:
        if server:
            server.close()


if __name__ == "__main__":
    main()
