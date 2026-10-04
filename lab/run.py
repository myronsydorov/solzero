"""Isolated scientific sessions with checkpoints, traces and explicit run outcomes."""
from __future__ import annotations
import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
import fcntl
import hashlib
import inspect
import json
import os
import random
from pathlib import Path
import tempfile
import time
from typing import get_type_hints

from omnigent import ClaudeSDKExecutor, ExecutorConfig, ExecutorError, TextChunk
from pydantic import create_model
import yaml
from schemas import (Commit, ExperimentSpec, FitResult, Law, Prediction, Verdict,
                     SessionInfo, Result, PredictionsRequest, parse_spec)
from lab.client import WorldClient
from lab.policies import ROLE_TOOLS, role_policy
from lab.session import LabSession
from lab.ledger import Ledger
from lab import tool_functions
from lab.model_runtime import ObservedExecutor, ModelFailure, limit_kind, backoff_seconds


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, allow_nan=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def tool_schema(name):
    function = getattr(tool_functions, name)
    hints = get_type_hints(function)
    shared_inputs = {
        "set_laws": {"laws": list[Law]},
        "preregister": {"spec": ExperimentSpec, "predictions": list[Prediction],
                        "tentative_followup": ExperimentSpec | None},
        "fit_law": {"law": Law},
        "predict": {"law": Law, "fit": FitResult, "spec": ExperimentSpec},
        "disagreement": {"spec": ExperimentSpec,
                         "laws": list[create_model("FittedLawInput", law=(Law, ...), fit=(FitResult, ...))]},
        "plan_shot": {"law": Law, "fit": FitResult},
        "nominate": {"law": Law, "fit": FitResult},
        "record_candidates": {"candidates": list[ExperimentSpec], "chosen": ExperimentSpec},
        "record_verdicts": {"verdicts": list[Verdict]},
        "commit_mission": {"commit": Commit},
    }
    hints.update(shared_inputs.get(name, {}))
    fields = {key: (hints.get(key, str), ... if value.default is inspect.Parameter.empty else value.default)
              for key, value in inspect.signature(function).parameters.items()}
    parameters = create_model(f"{name}_input", **fields).model_json_schema()
    def omit_titles(value):
        if isinstance(value, dict):
            return {key: omit_titles(item) for key, item in value.items() if key != "title"}
        if isinstance(value, list):
            return [omit_titles(item) for item in value]
        return value
    parameters = omit_titles(parameters)
    return {"name": name, "description": inspect.getdoc(function) or name, "parameters": parameters}


class CapReached(RuntimeError):
    pass


class Runner:
    def __init__(self, session, output, workspace, options):
        self.session, self.output, self.workspace, self.options = session, output, workspace, options
        self.fits, self.fit_laws, self.memo = {}, {}, {}
        self.pending_commit = None
        self.step = 0
        self.inflight = None
        self.dispatch_count = 0
        self.usage = Counter()
        self.usage_turns = 0
        self.wall_seconds = 0.0
        self.started = time.monotonic()
        self.retry_rng = random.Random(getattr(options, "seed", 1000))
        self.schedule = json.loads(Path(options.schedule).read_text()) if options.schedule else None
        if self.schedule and "type" in self.schedule[0]:
            self.schedule = [parse_spec(item).model_dump(mode="json") for item in self.schedule]
            if len(self.schedule) != session.info.budget:
                raise ValueError("Expected exactly one supplied spec per experiment budget unit")
        self.schedule_digest = hashlib.sha256(json.dumps(self.schedule, sort_keys=True).encode()).hexdigest()
        self.trace_enabled = not options.no_tracing
        if self.trace_enabled:
            import mlflow
            mlflow.set_tracking_uri(options.tracking_uri or f"sqlite:///{output / 'mlflow.db'}")
            mlflow.set_experiment("scientific-sessions")
        if not options.resume:
            self.save()

    @contextmanager
    def span(self, name, inputs):
        if not self.trace_enabled:
            yield None
            return
        import mlflow
        with mlflow.start_span(name=name, span_type="AGENT" if name in ROLE_TOOLS else "TOOL") as span:
            span.set_inputs(inputs)
            yield span

    def trace(self, record):
        with (self.output / "tools.jsonl").open("a") as stream:
            stream.write(json.dumps(record, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def save(self):
        session = self.session
        atomic_json(self.output / "checkpoint.json", {
            "schedule_digest": self.schedule_digest, "ledger_path": str(session.ledger.path), "options": vars(self.options), "origin": str(session.client._http.base_url),
            "info": session.info.model_dump(mode="json"), "condition": session.condition,
            "results": [result.model_dump(mode="json") for result in session.results],
            "live_laws": [law.model_dump(mode="json") for law in session.live_laws.values()],
            "pending": [session.pending[0].model_dump(mode="json"), session.pending[1]] if session.pending else None,
            "tentative": session.tentative.model_dump(mode="json") if session.tentative else None,
            "budget_left": session.budget_left, "committed": session.committed, "uncertain": session.uncertain,
            "step": self.step, "inflight": self.inflight, "fits": self.fits,
            "fit_laws": {key: value.model_dump(mode="json") for key, value in self.fit_laws.items()},
            "memo": self.memo, "pending_commit": self.pending_commit,
            "tool_calls": self.dispatch_count, "usage": dict(self.usage), "usage_turns": self.usage_turns,
            "wall_seconds": self.wall_seconds + time.monotonic() - self.started})

    def context(self):
        session = self.session
        entries = self.entries()
        return {"session": session.info.model_dump(mode="json"), "budget_left": session.budget_left,
                "minimum_experiments": self.options.min_experiments,
                "results": [result.model_dump(mode="json") for result in session.results],
                "live_laws": [law.model_dump(mode="json") for law in session.live_laws.values()],
                "fits": self.fits, "coverage": tool_functions.coverage(), "recent_ledger": entries[-12:],
                "pending": session.pending[0].model_dump(mode="json") if session.pending else None}

    def entries(self):
        return [json.loads(line) for line in self.session.ledger.path.read_text().splitlines()] if self.session.ledger.path.exists() else []

    def has_record(self, kind, cycle):
        return any(item['kind'] == kind and item['cycle'] == cycle for item in self.entries())

    def token_total(self):
        return sum(self.usage[key] for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))

    async def turn(self, role, task, cycle):
        for attempt in range(6):
            try:
                return await self._turn_once(role, task, cycle)
            except ModelFailure as failure:
                category = limit_kind(str(failure))
                terminal = category != "transient" or attempt == 5 or self.inflight or self.session.uncertain
                delay = None if terminal else backoff_seconds(attempt, self.retry_rng)
                self.trace({"kind": "model_failure", "agent": role, "cycle": cycle,
                            "provider": "Anthropic Claude subscription via claude-sdk", "model": self.options.model,
                            "error": str(failure), "limit_kind": category, "retry": not terminal, "delay_s": delay})
                self.save()
                if terminal:
                    raise
                await asyncio.sleep(delay)
                if self.session.committed or self.pending_commit:
                    return

    async def _turn_once(self, role, task, cycle):
        if self.token_total() >= self.options.token_cap:
            raise CapReached("Session token cap reached")
        config = yaml.safe_load((Path(__file__).parent / "agents" / f"{role}.yaml").read_text())
        model = self.options.model
        def observe(event):
            record = {"agent": role, "cycle": cycle, "step": self.step, "observed_at": time.time(), "event": event}
            with (self.output / "model-calls.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        executor = ObservedExecutor(observer=observe, cwd=self.workspace, model=model, skills_filter="none", os_env=None)
        guard = role_policy(role, self.options.auto_approve, self.options.min_experiments)
        calls = 0
        measurement_tools = {"weigh", "drop", "launch"}
        mutations = measurement_tools | {"set_laws", "preregister", "nominate", "commit_mission"}

        dispatch_lock = asyncio.Lock()

        async def dispatch_serial(name, arguments):
            nonlocal calls
            calls += 1
            self.dispatch_count += 1
            if calls > 32:
                return {"error": "Per-turn tool limit reached"}
            key = hashlib.sha256(json.dumps([self.step, name, arguments], sort_keys=True).encode()).hexdigest()
            if name in measurement_tools | {"commit_mission"} and key in self.memo:
                return self.memo[key]
            decision = guard({"type": "tool_call", "target": name, "data": {"arguments": arguments}})
            if name in measurement_tools and len(self.session.results) >= cycle:
                decision = {"result": "DENY", "reason": "Exactly one measurement is allowed in this evidence cycle"}
            if name in measurement_tools and not self.has_record("candidates", cycle):
                decision = {"result": "DENY", "reason": "Record the candidate comparison before executing"}
            if name == "commit_mission" and not self.has_record("nomination", len(self.session.results)):
                decision = {"result": "DENY", "reason": "Complete the current evidence assessment and nomination first"}
            with self.span(name, arguments) as span:
                if decision["result"] == "DENY":
                    result = {"blocked": True, "reason": decision["reason"]}
                elif decision["result"] == "ASK":
                    self.pending_commit = arguments["commit"]
                    atomic_json(self.output / "pending-commit.json", {
                        "session_id": self.session.info.session_id,
                        "base_url": str(self.session.client._http.base_url), "commit": self.pending_commit})
                    result = {"approval_required": True, "message": "Firing table saved; no shots fired"}
                else:
                    try:
                        if name in mutations:
                            self.inflight = {"name": name, "key": key, "arguments": arguments, "cycle": len(self.session.results), "ledger_count": len(self.entries())}
                            self.save()
                        result = await asyncio.to_thread(getattr(tool_functions, name), **arguments)
                        json.dumps(result, allow_nan=False)
                        if name == "set_laws":
                            self.fits = {identifier: fit for identifier, fit in self.fits.items()
                                         if identifier in self.session.live_laws and
                                         self.fit_laws[identifier].model_dump(exclude={"description"}) ==
                                         self.session.live_laws[identifier].model_dump(exclude={"description"})}
                        if name == "fit_law":
                            self.fits[result["law_id"]] = result
                            self.fit_laws[result["law_id"]] = tool_functions.validated_law(arguments["law"])
                        if name in measurement_tools | {"commit_mission"}:
                            self.memo[key] = result
                        self.inflight = None
                    except Exception as error:
                        result = {"error": f"{type(error).__name__}: {error}"}
                        if not self.session.uncertain:
                            self.inflight = None
                if span:
                    span.set_outputs(result)
            self.trace({"cycle": len(self.session.results), "agent": role, "tool": name,
                        "arguments": arguments, "policy": decision, "result": result})
            self.save()
            return result

        async def dispatch(name, arguments):
            async with dispatch_lock:
                return await dispatch_serial(name, arguments)

        executor._tool_executor = dispatch
        prompt = task + "\n" + json.dumps(self.context(), allow_nan=False)
        transcript, turn_usage = [], None
        with self.span(role, {"cycle": cycle, "task": task, "model": model}) as span:
            try:
                async for event in executor.run_turn([{"role": "user", "content": prompt}],
                        [tool_schema(name) for name in sorted(ROLE_TOOLS[role])], config["prompt"],
                        ExecutorConfig(model=model, max_tokens=4096, extra={"max_turns": 20})):
                    if getattr(event, "usage", None):
                        turn_usage = event.usage
                    if isinstance(event, ExecutorError):
                        raise ModelFailure(event.message)
                    if isinstance(event, TextChunk):
                        transcript.append(event.text)
            except Exception as failure:
                if limit_kind(str(failure)):
                    raise ModelFailure(str(failure)) from failure
                raise
            finally:
                await executor.close()
                if turn_usage:
                    self.usage_turns += 1
                    self.usage.update({key: value for key, value in turn_usage.items()
                                       if key in {"input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"} and isinstance(value, int)})
                with (self.output / "conversation.jsonl").open("a") as stream:
                    stream.write(json.dumps({"agent": role, "cycle": cycle, "model": model,
                                             "usage": turn_usage, "resolved_model": (turn_usage or {}).get("model"), "text": "".join(transcript)}) + "\n")
                if span:
                    span.set_outputs({"text": "".join(transcript), "usage": turn_usage})
                self.save()
        print(json.dumps({"agent": role, "experiments": len(self.session.results),
                          "budget_left": self.session.budget_left, "tool_calls": calls}), flush=True)

    async def random_choice(self, cycle):
        if not self.schedule or len(self.schedule) < cycle:
            raise ValueError("The random condition requires a host-supplied shared-sampler schedule")
        pair = self.schedule[cycle - 1]
        if "type" in pair:
            chosen = pair
            following = self.schedule[cycle] if cycle < len(self.schedule) else None
            self.session.ledger.append(cycle, "experimentalist", "candidates",
                                       {"candidates": [chosen], "chosen": chosen})
        else:
            chosen = pair["chosen"]
            following = pair["tentative_followup"]
            tool_functions.record_candidates(pair["candidates"], chosen)
        forecasts = []
        for identifier, law in self.session.live_laws.items():
            if identifier not in self.fits:
                raise RuntimeError("A retained live law has no current fit")
            forecasts.append(tool_functions.predict(law.model_dump(mode="json"), self.fits[identifier], chosen))
        tool_functions.preregister(chosen, forecasts, following, "Host shared sampler selected this experiment")
        self.save()

    async def run(self):
        if self.pending_commit and self.options.auto_approve:
            self.inflight = {"name": "commit_mission", "key": "approved_pending"}
            self.save()
            self.session.commit(Commit.model_validate(self.pending_commit))
            self.inflight = None
            self.pending_commit = None
            self.save()
        # Persisted phase index makes a completed phase immune to replay on resume.
        steps = [("theorist", "Review initial information and register the initial live set, which may be empty.", 0)]
        if self.session.condition == "single":
            steps = [("single", "Complete exactly one full evidence/revision cycle. Then decide whether to continue or commit.", index)
                     for index in range(1, self.options.cycles + 1)]
        else:
            for index in range(1, self.options.cycles + 1):
                steps += [("random" if self.session.condition == "random" else "experimentalist", "Choose and pre-register the next experiment.", index),
                          ("operator", "Execute the pending pre-registered experiment once.", index),
                          ("bootstrap", "Propose the initial live laws from the observation and fit them.", index),
                          ("analyst", "Assess the latest result, record verdicts and nominate the best live fit.", index),
                          ("theorist", "Revise or retain the live set after the evidence; fit retained proposals. Do not measure.", index),
                          ("pi", "Decide whether to continue or commit. If the budget is exhausted, plan and commit now.", index)]
        while self.step < len(steps) and not self.session.committed and self.pending_commit is None:
            role, task, cycle = steps[self.step]
            if role == "bootstrap" and self.session.live_laws:
                pass
            elif role == "random":
                if self.session.pending is None:
                    await self.random_choice(cycle)
            elif role == "operator" and len(self.session.results) >= cycle:
                pass
            else:
                await self.turn("theorist" if role == "bootstrap" else role, task, cycle)
            if role in {"experimentalist", "random"} and self.session.pending is None:
                raise RuntimeError("Experiment choice did not complete pre-registration")
            if role in {"operator", "single"} and len(self.session.results) != cycle:
                raise RuntimeError("Cycle did not produce exactly one result")
            if role in {"analyst", "single"}:
                for kind in ("verdicts", "nomination"):
                    if not self.has_record(kind, cycle):
                        raise RuntimeError(f"Missing {kind} after the result")
            self.step += 1
            self.save()
        if self.session.committed:
            return "committed"
        if self.pending_commit is not None:
            return "awaiting_approval"
        return "cycle_limit"

    def summary(self, status, error=None):
        entries = self.entries()
        conversation = self.output / "conversation.jsonl"
        turns = [json.loads(line) for line in conversation.read_text().splitlines()] if conversation.exists() else []
        by_agent = {}
        for turn in turns:
            totals = by_agent.setdefault(turn["agent"], {"model_turn_attempts": 0, "tokens": 0, "usage_missing_turns": 0})
            totals["model_turn_attempts"] += 1
            totals["usage_missing_turns"] += not bool(turn.get("usage"))
            totals["tokens"] += sum((turn.get("usage") or {}).get(key, 0) for key in
                                     ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        observed = self.output / "model-calls.jsonl"
        if observed.exists():
            for line in observed.read_text().splitlines():
                record = json.loads(line)
                if record["event"]["type"] == "message_start":
                    totals = by_agent.setdefault(record["agent"], {})
                    totals["observed_model_calls"] = totals.get("observed_model_calls", 0) + 1
        return {"by_agent": by_agent, "status": status, "error": error, "session_id": self.session.info.session_id,
                "condition": self.session.condition, "cycles": len(self.session.results),
                "budget_left": self.session.budget_left, "tool_calls": self.dispatch_count,
                "stop_reason": "pi_chose_commit" if self.session.committed or self.pending_commit else status,
                "wall_seconds": self.wall_seconds + time.monotonic() - self.started,
                "usage": dict(self.usage), "tokens_including_cache": self.token_total(),
                "usage_turns": self.usage_turns, "token_cap": self.options.token_cap,
                "usage_complete": bool(turns) and all(item.get("usage") for item in turns) and status != "failed",
                "ledger": str(self.session.ledger.path), "record_counts": dict(Counter(item['kind'] for item in entries)),
                "changed_decisions": sum(item['kind']=='decision_diff' and item['payload']['changed'] for item in entries)}


def resume_session(client, saved, output, auto_approve):
    from threading import RLock
    # A durable result closes the window between ledger fsync and checkpoint save.
    # Without that acknowledgement, an in-flight measurement must never be replayed.
    if saved["inflight"] and saved["inflight"]["name"] in {"weigh", "drop", "launch"} and saved["pending"]:
        ledger_path = Path(saved.get("ledger_path", output / saved["info"]["session_id"] / "ledger.jsonl"))
        records = [json.loads(line) for line in ledger_path.read_text().splitlines()] if ledger_path.exists() else []
        later = [Result.model_validate(item["payload"]) for item in records
                 if item["kind"] == "result" and item["cycle"] > len(saved["results"])]
        if (len(later) == 1 and later[0].index == len(saved["results"]) + 1
                and later[0].spec == parse_spec(saved["pending"][0]["spec"])
                and later[0].budget_left == saved["budget_left"] - 1):
            saved["results"].append(later[0].model_dump(mode="json"))
            saved["budget_left"] = later[0].budget_left
            saved["tentative"] = saved["pending"][0]["tentative_followup"]
            saved["pending"], saved["inflight"], saved["uncertain"] = None, None, False
    intent = saved["inflight"]
    if intent and "ledger_count" in intent:
        ledger_path = Path(saved.get("ledger_path", output / saved["info"]["session_id"] / "ledger.jsonl"))
        records = [json.loads(line) for line in ledger_path.read_text().splitlines()] if ledger_path.exists() else []
        acknowledgements = records[intent["ledger_count"]:]
        kind = {"preregister": "prediction_table", "set_laws": "law_set", "nominate": "nomination", "commit_mission": "commit"}.get(intent["name"])
        matches = [item for item in acknowledgements if item["kind"] == kind]
        if len(matches) == 1:
            payload = matches[0]["payload"]
            if kind == "prediction_table":
                saved["pending"] = [PredictionsRequest.model_validate({key: value for key, value in payload.items()
                                    if key != "prediction_table_id"}).model_dump(mode="json"), payload["prediction_table_id"]]
            elif kind == "law_set":
                saved["live_laws"], saved["pending"] = payload["laws"], None
                saved["fits"], saved["fit_laws"] = {}, {}
            elif kind == "commit":
                saved["committed"], saved["pending_commit"] = True, None
            saved["inflight"], saved["uncertain"] = None, False
    if saved["inflight"] or saved["uncertain"]:
        raise RuntimeError("Ambiguous mutation checkpoint: host reconciliation is required; no replay permitted")
    if str(client._http.base_url) != saved["origin"]:
        raise ValueError("Resume requires the original service origin and live server")
    session = LabSession.__new__(LabSession)
    session.client, session.condition = client, saved["condition"]
    session.info = SessionInfo.model_validate(saved["info"])
    session.ledger = Ledger(session.info.session_id, output)
    session.ledger.path = Path(saved.get("ledger_path", session.ledger.path))
    session.approval = "auto" if auto_approve else "human"
    session.ledger.actor_override = "single" if session.condition == "single" else None
    session.results = [Result.model_validate(item) for item in saved["results"]]
    session.live_laws = {item["law_id"]: Law.model_validate(item) for item in saved["live_laws"]}
    session.pending = (PredictionsRequest.model_validate(saved["pending"][0]), saved["pending"][1]) if saved["pending"] else None
    session.tentative = parse_spec(saved["tentative"]) if saved["tentative"] else None
    session.budget_left, session.committed, session.uncertain = saved["budget_left"], saved["committed"], False
    session._approve_commit = (lambda _: True) if auto_approve else None
    session._lock = RLock()
    return session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", "--world-id", dest="world")
    parser.add_argument("--condition", choices=["lab", "random", "single"], default="lab")
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--output", "--out", default=None)
    parser.add_argument("--world-url")
    parser.add_argument("--session-info")
    parser.add_argument("--max-wall-s", type=float, default=600)
    parser.add_argument("--approval", choices=["human", "auto"], default="human")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--analyst-model")
    parser.add_argument("--cycles", type=int, default=12)
    parser.add_argument("--min-experiments", type=int, default=0)
    parser.add_argument("--token-cap", "--max-tokens", type=int, default=2000000)
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--schedule", "--specs", help="Public experiment schedule prepared by the host shared sampler")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--no-tracing", action="store_true")
    parser.add_argument("--tracking-uri")
    options = parser.parse_args()
    if options.analyst_model and options.analyst_model != options.model:
        parser.error("All scientific roles and conditions must use one model")
    options.auto_approve = options.auto_approve or options.approval == "auto"
    if not options.session_info and not options.world:
        parser.error("Supply --world or --session-info")
    if options.max_wall_s < 0 or options.token_cap < 0:
        parser.error("Caps must be nonnegative")
    if not 1000 <= options.seed <= 1999 or not 1 <= options.cycles <= 12 or not 0 <= options.min_experiments <= 12:
        parser.error("Use a dev seed and valid experiment limits")
    if options.condition == "random" and not options.schedule:
        parser.error("The random condition needs --schedule from the host shared sampler")
    output = Path(options.output or f"runs/session-{time.time_ns()}").resolve()
    output.mkdir(parents=True, exist_ok=options.resume or bool(options.session_info))
    os.environ["OMNIGENT_DISABLE_TELEMETRY"] = "true"
    os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "4096"
    with (output / ".runner.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix="solzero-scientists-") as workspace, WorldClient(options.world_url) as client:
            saved = json.loads((output / "checkpoint.json").read_text()) if options.resume else None
            if saved and any(saved['options'][key] != getattr(options, key) for key in ('world','condition','seed','model','analyst_model','cycles','schedule','min_experiments','token_cap')):
                raise ValueError("Resume must preserve the run configuration")
            session = resume_session(client, saved, output, options.auto_approve) if saved else LabSession(
                client, options.world, options.condition, runs_root=output,
                approve_commit=(lambda _: True) if options.auto_approve else None,
                info=SessionInfo.model_validate_json(Path(options.session_info).read_text()) if options.session_info else None,
                flat_ledger=bool(options.session_info), approval="auto" if options.auto_approve else "human")
            tool_functions.configure(session, seed=options.seed)
            runner = Runner(session, output, workspace, options)
            if saved:
                if saved.get("schedule_digest", runner.schedule_digest) != runner.schedule_digest:
                    raise ValueError("Resume requires the original experiment schedule")
                for attribute, key in (("step","step"),("memo","memo"),("inflight","inflight"),("fits","fits"),
                                       ("pending_commit","pending_commit"),("dispatch_count","tool_calls"),
                                       ("usage_turns","usage_turns"),("wall_seconds","wall_seconds")):
                    setattr(runner, attribute, saved[key])
                runner.usage = Counter(saved['usage'])
                runner.fit_laws = {key: Law.model_validate(value) for key,value in saved['fit_laws'].items()}
                runner.save()
            status, error = "interrupted", None
            try:
                async def bounded_run():
                    remaining = max(0, options.max_wall_s - runner.wall_seconds)
                    return await asyncio.wait_for(runner.run(), timeout=remaining)
                status = asyncio.run(bounded_run())
            except (TimeoutError, CapReached) as failure:
                status, error = "cap_reached", str(failure) or "Wall-clock cap reached"
            except Exception as failure:
                status, error = "failed", f"{type(failure).__name__}: {failure}"
            finally:
                runner.save()
                if runner.trace_enabled:
                    import mlflow
                    mlflow.flush_trace_async_logging()
                summary = runner.summary(status, error)
                if options.session_info:
                    summary.update(status={"awaiting_approval": "pending_approval", "cycle_limit": "cap_reached", "failed": "error"}.get(status, status),
                                   tokens_used=runner.token_total(), wall_s=summary["wall_seconds"],
                                   n_experiments=len(session.results))
                atomic_json(output / "summary.json", summary)
                print(json.dumps(summary, indent=2), flush=True)
            raise SystemExit(0 if options.session_info else {"committed":0,"awaiting_approval":3,"cycle_limit":5}.get(status,4))


if __name__ == "__main__":
    main()
