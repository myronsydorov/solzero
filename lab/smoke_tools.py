"""Transport-only fixtures, separate from the scientific experiment tools."""
import json
import os
from pathlib import Path
import fcntl


def audit(kind: str, payload: dict) -> None:
    path = Path(os.environ["SOLZERO_SMOKE_AUDIT"])
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps({"kind": kind, "payload": payload}, allow_nan=False) + "\n")
        stream.flush()


def relay_spec(spec: dict) -> dict:
    """Return the supplied experiment record unchanged for a transport check."""
    audit("spec", spec)
    return spec


def execute_fixture(spec: dict) -> dict:
    """Return a synthetic result for a transport check; this is not a measurement."""
    result = {"experiment_id": "fixture01", "index": 1, "spec": spec,
              "observables": {"force_n": 1.0}, "noise_sd": {"force_n": 0.02},
              "status": "ok", "budget_left": 11}
    audit("result", result)
    return result


def receive_result(result: dict) -> dict:
    """Acknowledge the complete result record without changing it."""
    audit("received", result)
    return result


def blocked_operation() -> dict:
    """A policy-rejection sentinel for the transport check."""
    audit("unexpected_execution", {})
    return {"unexpected": True}


def block_sentinel(event: dict) -> dict:
    if event.get("type") == "tool_call" and str(event.get("target", "")).endswith("blocked_operation"):
        audit("policy_denied", {"tool": event["target"]})
        return {"result": "DENY", "reason": "Transport check deliberately rejects this sentinel call"}
    return {"result": "ALLOW"}
