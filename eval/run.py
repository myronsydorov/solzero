"""Evaluation runner (SPEC.md section 7).

    python -m eval.run --condition oracle --seeds 1000-1002 --out runs/eval --serve --max-concurrency 3
    python -m eval.run --condition random --agent scripted --seeds 1000-1002 --out runs/eval --serve
    python -m eval.run --condition lab --seeds 1000-1002 --out runs/eval --world-url URL --agent-cmd "python -m lab.run"

For every (condition, seed) it opens a session on the world server, hands the SessionInfo
to the agent process (SPEC 5.6), enforces the wall-clock cap, then saves the admin score and
truth and grades the run. Runs go to OUT/<label>/<seed>/. Finished runs are skipped on a
rerun, so the runner resumes after a crash. A crashed agent is retried once, in a new session.
A provider rate or session limit pauses the whole batch until the provider is back, then the
paused runs continue in their own sessions with --resume (SPEC 5.6).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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


# --- provider rate limits ------------------------------------------------------

RATE_LIMIT_RE = re.compile(r"(session|rate|usage) limit|too many requests|\b429\b|overloaded|rate.?limited", re.I)
RESET_RE = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:\(([^)]+)\))?", re.I)


def rate_limit_wait(summary: dict | None, log_tail: str, default_s: float) -> float | None:
    """Seconds to pause if this run stopped on a provider limit, else None."""
    status = (summary or {}).get("status")
    text = f"{(summary or {}).get('error') or ''}\n{log_tail}"
    if status != "rate_limited" and not (status in (None, "error", "failed") and RATE_LIMIT_RE.search(text)):
        return None
    if summary and summary.get("retry_after_s"):
        return float(summary["retry_after_s"])
    m = RESET_RE.search(text)
    if m:
        hour, minute, ampm, tz = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower(), m.group(4)
        hour = hour % 12 + (12 if ampm == "pm" else 0) if ampm else hour
        try:
            zone = ZoneInfo(tz) if tz else None
        except Exception:
            zone = None
        now = datetime.now(zone)
        reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if reset <= now:
            reset += timedelta(days=1)
        return (reset - now).total_seconds() + 120  # two minutes of margin after the stated reset
    return default_s


class RateGate:
    """Shared pause: while closed, no run starts or resumes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.until = 0.0
        self.pauses = 0

    def pause(self, seconds: float, why: str) -> None:
        with self.lock:
            until = time.time() + seconds
            if until > self.until:
                self.until = until
                self.pauses += 1
                log(f"provider limit ({why}); pausing the batch for {seconds / 60:.0f} min, "
                    f"until {time.strftime('%H:%M:%S', time.localtime(until))}")

    def wait(self) -> None:
        while True:
            with self.lock:
                left = self.until - time.time()
            if left <= 0:
                return
            time.sleep(min(left, 30))


GATE = RateGate()


def _tail(path: Path, n: int = 4000) -> str:
    try:
        return path.read_text(errors="ignore")[-n:]
    except OSError:
        return ""


def run_one(args, client: WorldClient, worlds: dict[int, str], condition: str, seed: int) -> dict:
    label = label_for(condition, args.agent)
    d = Path(args.out) / label / str(seed)
    d.mkdir(parents=True, exist_ok=True)
    state_path = d / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"attempts": 0}
    if state.get("status") == "done" or (state.get("status") == "failed" and not args.retry_failed):
        return state
    if state.get("status") == "failed":
        state["attempts"] = 0
    world_id = worlds[seed]

    def save():
        state_path.write_text(json.dumps(state, indent=1))

    # A run left running or paused by an earlier runner process resumes in place if the
    # server still has its session; otherwise it starts a new attempt.
    resume = False
    if state.get("status") in ("running", "rate_limited") and state.get("attempt_dir"):
        try:
            client.score(state["session_id"])
            resume = True
        except Exception:
            resume = False

    while True:
        GATE.wait()
        if not resume:
            if state["attempts"] >= MAX_ATTEMPTS:
                state["status"] = "failed"
                break
            state["attempts"] += 1
            attempt = d / f"attempt{state['attempts']}"
            attempt.mkdir(exist_ok=True)
            info = client.session(world_id, condition)
            state.update(status="running", session_id=info.session_id, world_id=world_id, seed=seed,
                         condition=condition, label=label, attempt_dir=attempt.name, started=time.time(),
                         rate_limit_pauses=0)
            save()
            (attempt / "session_info.json").write_text(info.model_dump_json(indent=1))
            specs_path = None
            if condition == "random":
                specs_path = attempt / "specs.json"
                specs_path.write_text(json.dumps([s.model_dump() for s in random_specs(seed, info.budget)], indent=1))
            cmd = agent_command(args, condition, seed, attempt / "session_info.json", attempt / "agent", specs_path)
            (attempt / "agent").mkdir(exist_ok=True)
            (attempt / "command.json").write_text(json.dumps(cmd))
        else:
            attempt = d / state["attempt_dir"]
            cmd = json.loads((attempt / "command.json").read_text())
            if "--resume" not in cmd:
                cmd = [*cmd, "--resume"]
            state["status"] = "running"
            save()
        t0 = time.time()
        log_path = attempt / f"agent{'.resume' + str(state.get('rate_limit_pauses', 0)) if resume else ''}.log"
        with open(log_path, "w") as logf:
            try:
                rc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, timeout=args.max_wall_s + 60,
                                    env={**os.environ, "SOLZERO_WORLD_URL": args.world_url}).returncode
            except subprocess.TimeoutExpired:
                rc = "timeout"
        summary_path = attempt / "agent" / "summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else None
        state.update(returncode=rc, wall_s=state.get("wall_s", 0) * resume + time.time() - t0,
                     agent_status=summary and summary.get("status"))
        wait = rate_limit_wait(summary, _tail(log_path), args.rate_limit_wait)
        if wait is not None:
            state["rate_limit_pauses"] = state.get("rate_limit_pauses", 0) + 1
            state["status"] = "rate_limited"
            save()
            if state["rate_limit_pauses"] > args.max_pauses:
                log(f"{label} {seed}: more than {args.max_pauses} provider-limit pauses; giving up on this run")
                state["status"] = "failed"
                break
            GATE.pause(wait, f"{label} {seed}")
            resume = True
            continue
        if rc == 0 and summary is not None:
            state["status"] = "done"
            break
        log(f"{label} {seed}: attempt {state['attempts']} crashed (rc={rc})")
        state["status"] = "failed"
        resume = False
    attempt = d / state["attempt_dir"]
    (attempt / "admin_score.json").write_text(json.dumps(client.score(state["session_id"]), indent=1))
    (attempt / "truth.json").write_text(json.dumps(client.truth(world_id), indent=1))
    metrics = grade_run(attempt, label=label, seed=seed)
    metrics["run_status"] = state["status"]
    metrics["rate_limit_pauses"] = state.get("rate_limit_pauses", 0)
    (d / "metrics.json").write_text(json.dumps(metrics, indent=1))
    save()
    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True, choices=[*AGENT_CONDITIONS, *REFERENCES])
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--out", default="runs/eval")
    ap.add_argument("--agent", choices=["cli", "scripted"], default="cli",
                    help="cli: the agent CLI (lab, single, random); scripted: library-fitting stand-in (random)")
    ap.add_argument("--agent-cmd", default=DEFAULT_AGENT_CMD, help="agent CLI, e.g. 'python -m eval.agent_stub'")
    ap.add_argument("--max-concurrency", "--parallel", dest="max_concurrency", type=int, default=4,
                    help="most agent sessions running at once")
    ap.add_argument("--rate-limit-wait", type=float, default=900,
                    help="pause (s) after a provider limit that states no reset time")
    ap.add_argument("--max-pauses", type=int, default=20, help="provider-limit pauses allowed per run")
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
        with ThreadPoolExecutor(args.max_concurrency) as ex:
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
