"""Run a real model transport check in a directory with no project context."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="runs/omnigent-smoke")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    audit = output / "handoff.jsonl"
    if audit.exists():
        raise SystemExit("Choose a fresh output directory; previous evidence is preserved")
    env = dict(os.environ, PYTHONPATH=str(root), SOLZERO_SMOKE_AUDIT=str(audit),
               OMNIGENT_DISABLE_TELEMETRY="true")
    spec = {"type": "weigh", "sample_id": "ref_100", "height_m": 0.8}
    prompt = ("This is a transport test, not a scientific session. Call relay_spec with this record: "
              + json.dumps(spec) + ". Send its complete output to the operator sub-agent. "
              "Send the operator's complete Result JSON to the analyst sub-agent. "
              "Wait for both sub-agents to finish. Then attempt blocked_operation once. "
              "Report whether the records survived and whether the policy denied the sentinel. "
              "Do not retry a denied call. Use sys_session_send for the declared sub-agents.")
    command = [str(Path(sys.executable).with_name("omnigent")), "run",
               str(root / "lab/agents/smoke.yaml"), "--no-session", "--prompt", prompt]
    with tempfile.TemporaryDirectory(prefix="solzero-agent-") as isolated:
        with (output / "handoff.log").open("w") as log:
            completed = subprocess.run(command, cwd=isolated, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, timeout=600)
    events = [json.loads(line) for line in audit.read_text().splitlines()] if audit.exists() else []
    by_kind = {kind: [row["payload"] for row in events if row["kind"] == kind]
               for kind in ("spec", "result", "received", "policy_denied", "unexpected_execution")}
    passed = (completed.returncode == 0 and by_kind["spec"] == [spec]
              and len(by_kind["result"]) == 1 and by_kind["result"][0]["spec"] == spec
              and by_kind["received"] == by_kind["result"]
              and len(by_kind["policy_denied"]) == 1 and not by_kind["unexpected_execution"])
    summary = {"passed": passed, "returncode": completed.returncode,
               "mechanism": "declared type: agent tools via sys_session_send; JSON through Python function tools",
               "counts": {kind: len(rows) for kind, rows in by_kind.items()}}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
