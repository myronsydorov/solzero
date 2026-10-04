"""Durable JSONL records for one session; timestamps are always UTC."""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re

from pydantic import BaseModel
from schemas import DecisionDiff, LedgerEntry, LedgerKind, ExperimentSpec


class Ledger:
    def __init__(self, session_id: str, root: str | Path = "runs"):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            raise ValueError("Invalid session identifier")
        self.path = Path(root) / session_id / "ledger.jsonl"
        self.actor_override: str | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, cycle: int, agent: str, kind: LedgerKind, payload: dict | BaseModel) -> LedgerEntry:
        if cycle < 0:
            raise ValueError("Cycle must be nonnegative")
        if isinstance(payload, BaseModel):
            payload = payload.model_dump(mode="python")
        # Reject nonfinite values before JSON-mode model dumping can turn them into null.
        json.dumps(payload, allow_nan=False)
        entry = LedgerEntry(ts=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                            cycle=cycle, agent=self.actor_override or agent, kind=kind, payload=payload)
        encoded = json.dumps(entry.model_dump(mode="json"), allow_nan=False, separators=(",", ":")) + "\n"
        with self.path.open("a", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return entry

    def decision_diff(self, cycle: int, tentative: ExperimentSpec | None,
                      actual: ExperimentSpec, reason: str) -> LedgerEntry:
        record = DecisionDiff(tentative=tentative, actual=actual,
                              changed=tentative is not None and tentative != actual, reason=reason)
        return self.append(cycle, "experimentalist", "decision_diff", record)
