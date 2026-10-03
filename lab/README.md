# Lane B: transport and tool scaffolding

Python 3.12. Run from the repository root after the shared schema package is merged.
The root dependency configuration belongs to lane A; the additional dependencies
for this lane are in `lab/requirements.txt`.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r lab/requirements.txt
.venv/bin/python -m pytest tests/test_no_leak.py mock/tests -q
.venv/bin/python -m mock.server --seed 1000 --port 8000
```

Set `SOLZERO_WORLD_URL=http://127.0.0.1:8000` for agent-side HTTP access. All
measurements use that service's fixed public routes. The shared analysis functions
are imported lazily from `tools`; their implementation is identical for every
condition. The host supplies an explicit analysis seed through `configure`.

## Verified Omnigent transport

The installed release is open-source Omnigent 0.16.0. The upstream
[agent YAML specification](https://github.com/omnigent-ai/omnigent/blob/v0.16.0/docs/AGENT_YAML_SPEC.md)
was read at its matching release tag.

The bundled `tests/resources/examples/hello_world.yaml` ran with `claude-sdk`,
using the existing Claude login and the `sonnet` model. It returned the requested
connection marker. The installed package needs no local web UI build.

```sh
.venv/bin/python -m lab.smoke --output runs/transport-check-new
```

This runs an orchestrator and two declared sub-agents in an empty temporary
working directory, with host skills disabled and no OS tools. It passes a supplied
experiment record through a Python function to the first worker, passes the
synthetic result through a Python function in the second worker, then tries a
sentinel rejected by a Python policy. The verified handoff is `type: agent` via
`sys_session_send`, carrying JSON through child messages and function tools.
Numeric JSON equality is checked; textual formatting may change.

`handoff.jsonl` records actual function calls and the policy rejection;
`handoff.log` is the model transcript and `summary.json` is the assertion result.
The smoke refuses to overwrite an existing audit. It is a transport fixture,
not a scientific run, and does not require shared models.

## Binding scientific tools

`LabSession` starts a service session and owns its local live-law set, pending
prediction table, observed results, budget mirror, and `Ledger`. Host code binds
it with `lab.tool_functions.configure(session, seed=...)` **inside the same
runner process that executes Python tools**. Do not expose `configure` as a tool.
Use one session per process. Separate subprocesses do not share this binding.

The five role YAMLs under `lab/agents/` deliberately contain only prompt stubs.
They declare narrow tool sets and Python role policies. They are not a discovery
loop or a standalone CLI session launcher; the host binding and orchestrator
lifecycle must be supplied in the next milestone. Running a role without a host
binding fails closed.

- Only the Operator has measurement tools.
- Pre-registration requires one prediction for each current live law. Changing
  the set invalidates the pending table.
- The service's budget is authoritative. Failed results consume a slot and are
  logged. A lost response or inconsistent result blocks further mutations until
  the host reconciles the session; there are no automatic experiment retries.
- The PI policy requests approval for a valid five-shot table. The host must wire
  the approval decision for that exact table into `approve_commit`; without it,
  `commit_mission` refuses to submit. The callback must consume the existing
  approval decision, not open another approval dialog.
- Ledger writes append one validated JSON object per line with a UTC timestamp,
  a file lock, a flush and a disk sync. A newly started session refuses to append
  to an existing populated ledger. Use a fresh run directory after restarting
  the development server.

The public API does not register the full live-law set with the server. Complete
prediction coverage is consequently enforceable locally but is not independently
verifiable by the server. The interface question is recorded in `STATUS.md`.

## Verification

```sh
.venv/bin/python -m mock.check --seed 1000 --output runs/mock-check-new
```

The check starts and stops its own loopback mock process, exercises the HTTP
wrappers, and saves raw responses, the ledger, server output, and a summary.
Test fixtures live under `mock/tests/`; the leak and import-isolation checks live
in the shared test directory. No scientific prompts or hypotheses are provided
by this scaffold. Tracing integration remains a subsequent milestone.
