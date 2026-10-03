# Lane B: transport and tool scaffolding

Python 3.12. Run from the repository root after the shared schema package is merged.
Dependencies belong in the root `pyproject.toml`, owned by lane A. The temporary
lane requirements file has been removed. See STATUS.md if the available main
commit has not yet received its dependency follow-up.

```sh
uv sync
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

The five role YAMLs under `lab/agents/` retain brief role stubs. `lab.run` supplies
record-format and role-task instructions, then executes each role through
Omnigent's Claude SDK executor with narrow Python function tools and the role
policy. The host sequences roles; scientific choices come from the models. The
first cycle may begin with no laws, in which case the Theorist proposes the first
set after the first observation and before the Analyst nominates it. No law,
parameter bounds or experiment sequence is supplied by the runner. A role run
without a host binding fails closed.

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

The Theorist's `set_laws` calls `POST /laws` to replace the server's complete live
set. The mock requires exact prediction coverage and invalidates older tables
on any replacement, including a changed expression under an unchanged ID.

## Verification

```sh
.venv/bin/python -m mock.check --seed 1000 --output runs/mock-check-new
```

The check starts and stops its own loopback mock process, exercises the HTTP
wrappers, and saves raw responses, the ledger, server output, and a summary.
Test fixtures live under `mock/tests/`; the leak and import-isolation checks live
in the shared test directory. The transport fixture does not provide scientific hypotheses. `lab.run` saves
public model text, tool inputs/outputs and policy decisions alongside the ledger.
MLflow integration remains a subsequent milestone.

```sh
SOLZERO_WORLD_URL=http://127.0.0.1:8000 .venv/bin/python -m lab.run \
  --world-id mock-dev --seed 1000 --cycles 12 --output runs/dev-loop-new
```

Use `--cycles 1` for a single complete evidence/revision cycle. This cap does not
change the server budget. A mission request produces `pending-commit.json` for
human review and stops before firing. Empty temporary working directories, no
native OS tools, and disabled host skills prevent project context discovery.
Only session metadata, public observations, agent-authored laws and this session's
ledger are passed to the roles. The model service does not expose a sampling-seed
setting through this harness; the explicit seed controls numerical tool draws,
and raw model decisions are retained rather than claiming bitwise model replay.
