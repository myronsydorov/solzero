"""Run the specialist cycle through Omnigent with a process-local tool binding."""
from __future__ import annotations
import argparse
import asyncio
import inspect
import json
import os
from pathlib import Path
import tempfile
from typing import get_type_hints

from omnigent import ClaudeSDKExecutor, ExecutorConfig, ExecutorError, TextChunk
from pydantic import create_model
import yaml
from schemas import Commit, ExperimentSpec, FitResult, Law, Prediction, Verdict

from lab.client import WorldClient
from lab.policies import ROLE_TOOLS, role_policy
from lab.session import LabSession
from lab import tool_functions

TASKS = {
    "theorist": "Review the available observations and verdicts. Propose, revise, or retain up to four laws, using only evidence available here. Call set_laws with the complete live set. Fit proposed laws with fit_law when data permit. Before any experiment you may register an empty live set; do not invent a fitted result. A law has law_id, description, ax and az arithmetic expression strings, and params mapping each chosen parameter name to init, lo and hi. Expressions can use m, z, vx, vz, speed and the named parameters. There is no supplied menu of laws. Return a concise account of uncertainty.",
    "experimentalist": "Select the next experiment. Compare at least two distinct candidates and call record_candidates. Use predict for every live law with its current fit; disagreement is available. Call preregister with the chosen spec, one complete prediction per live law, a tentative_followup spec, and the reason for this choice. Do not execute a measurement. Experiment types are weigh and drop with sample_id and height_m, or launch with sample_id, speed_mps, elevation_deg. Use the supplied instrument limits.",
    "operator": "Execute exactly the pending pre-registered experiment using its matching measurement tool, once. Return the Result. Do not choose or modify its settings.",
    "analyst": "Compare the latest result with its pre-registered predictions. Refit current live laws using fit_law. Call record_verdicts once with one verdict per live law: law_id, experiment_id, z, verdict (supported, rejected, or insufficient_evidence), and note. Describe confounding or insufficient evidence candidly. Call nominate with the current best live law and its updated FitResult. Return uncertainty and suggested questions for the next revision; do not change the law set.",
    "pi": "Decide whether more experiments are needed within the remaining budget. If continuing, say continue. If stopping or the budget is exhausted, use plan_shot for each of the five supplied targets with your selected current fitted law and mission sample. Call commit_mission with law_id, claim (law_identified, predictive_only, or insufficient_evidence), claims_non_ordinary, and exactly five shots containing target_id, speed_mps and elevation_deg. A human must approve the resulting firing table; if approval_required is returned, stop. Do not claim that shots have fired before confirmation.",
}


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


class Runner:
    def __init__(self, session, output: Path, workspace: str, model: str):
        self.session, self.output, self.workspace, self.model = session, output, workspace, model
        self.fits = {}
        self.fit_laws = {}
        self.pending_commit = None
        self.dispatch_count = 0

    def trace(self, record):
        with (self.output / "tools.jsonl").open("a") as stream:
            stream.write(json.dumps(record, allow_nan=False) + "\n")
            stream.flush()

    def context(self):
        session = self.session
        ledger = [json.loads(line) for line in session.ledger.path.read_text().splitlines()] if session.ledger.path.exists() else []
        return {"session": session.info.model_dump(mode="json"), "budget_left": session.budget_left,
                "results": [result.model_dump(mode="json") for result in session.results],
                "live_laws": [law.model_dump(mode="json") for law in session.live_laws.values()],
                "fits": self.fits, "recent_ledger": ledger[-12:],
                "pending": self.session.pending[0].model_dump(mode="json") if self.session.pending else None}

    async def turn(self, role, *, revision_only=False):
        config = yaml.safe_load((Path(__file__).parent / "agents" / f"{role}.yaml").read_text())
        executor = ClaudeSDKExecutor(cwd=self.workspace, model=self.model, skills_filter="none", os_env=None)
        guard = role_policy(role)
        calls = 0

        async def dispatch(name, arguments):
            nonlocal calls
            calls += 1
            self.dispatch_count += 1
            if calls > 32:
                return {"error": "Per-turn tool limit reached; stop and report uncertainty"}
            event = {"type": "tool_call", "target": name, "data": {"arguments": arguments}}
            decision = guard(event)
            if decision["result"] == "DENY":
                result = {"blocked": True, "reason": decision["reason"]}
            elif decision["result"] == "ASK":
                if self.pending_commit is None:
                    self.pending_commit = arguments["commit"]
                    (self.output / "pending-commit.json").write_text(json.dumps({
                        "session_id": self.session.info.session_id, "base_url": str(self.session.client._http.base_url),
                        "commit": self.pending_commit}, indent=2) + "\n")
                result = {"approval_required": True, "message": "Firing table saved for human review; no shots fired"}
            else:
                try:
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
                except Exception as error:
                    result = {"error": f"{type(error).__name__}: {error}"}
            self.trace({"cycle": len(self.session.results), "agent": role, "tool": name,
                        "arguments": arguments, "policy": decision, "result": result})
            return result

        # Omnigent 0.16's ExecutorAdapter uses this same callback binding.
        executor._tool_executor = dispatch
        prompt = TASKS[role]
        if revision_only:
            prompt += " This turn completes the evidence/revision cycle. Do not perform another experiment."
        prompt += "\nPhysics may differ from Earth's. Insufficient evidence is an acceptable conclusion.\n"
        prompt += json.dumps(self.context(), allow_nan=False)
        transcript = []
        try:
            async for event in executor.run_turn([{"role": "user", "content": prompt}],
                    [tool_schema(name) for name in sorted(ROLE_TOOLS[role])], config["prompt"],
                    ExecutorConfig(model=self.model, extra={"max_turns": 16})):
                if isinstance(event, ExecutorError):
                    raise RuntimeError(event.message)
                if isinstance(event, TextChunk):
                    transcript.append(event.text)
        finally:
            await executor.close()
            with (self.output / "conversation.jsonl").open("a") as stream:
                stream.write(json.dumps({"agent": role, "cycle": len(self.session.results),
                                         "text": "".join(transcript)}) + "\n")
        print(json.dumps({"agent": role, "experiments": len(self.session.results),
                          "budget_left": self.session.budget_left, "tool_calls": calls}), flush=True)

    async def run(self, cycles):
        await self.turn("theorist")
        for _ in range(cycles):
            previous = len(self.session.results)
            await self.turn("experimentalist")
            if self.session.pending is None:
                raise RuntimeError("Experimentalist did not complete pre-registration")
            await self.turn("operator")
            if len(self.session.results) != previous + 1:
                raise RuntimeError("Operator did not return exactly one result")
            if not self.session.live_laws:
                # Cycle zero permits no laws. Only the Theorist may propose the first set.
                await self.turn("theorist")
            await self.turn("analyst")
            latest = [json.loads(line) for line in self.session.ledger.path.read_text().splitlines()]
            for required in ("verdicts", "nomination"):
                if not any(entry["kind"] == required and entry["cycle"] == len(self.session.results) for entry in latest):
                    raise RuntimeError(f"Analyst did not record {required} after this result")
            await self.turn("theorist", revision_only=True)
            await self.turn("pi")
            if self.pending_commit is not None or self.session.budget_left == 0:
                break
        return {"session_id": self.session.info.session_id, "cycles": len(self.session.results),
                "budget_left": self.session.budget_left, "tool_calls": self.dispatch_count,
                "status": "awaiting_approval" if self.pending_commit is not None else "cycle_limit",
                "pending_commit": str(self.output / "pending-commit.json") if self.pending_commit else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world-id", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--cycles", type=int, default=12)
    args = parser.parse_args()
    if not 1000 <= args.seed <= 1999 or not 1 <= args.cycles <= 12:
        parser.error("Use a dev seed and one to twelve cycles")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["OMNIGENT_DISABLE_TELEMETRY"] = "true"
    with tempfile.TemporaryDirectory(prefix="solzero-scientists-") as workspace, WorldClient() as client:
        session = LabSession(client, args.world_id, runs_root=output)
        tool_functions.configure(session, seed=args.seed)
        runner = Runner(session, output, workspace, args.model)
        summary = {"status": "interrupted", "session_id": session.info.session_id}
        try:
            summary = asyncio.run(runner.run(args.cycles))
        except Exception as error:
            summary = {"status": "failed", "session_id": session.info.session_id,
                       "cycles": len(session.results), "error": f"{type(error).__name__}: {error}"}
            raise
        finally:
            (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
