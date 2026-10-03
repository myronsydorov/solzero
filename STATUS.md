# Status

Last updated: 2026-10-03 (project start). Each assistant edits only its own lane section, plus "Requests" and "Interface changes".

## Gates

| Gate | Target hour | State | Decided by |
| --- | --- | --- | --- |
| Schemas agreed (`SPEC.md` section 5) | 0.5 | Draft in spec, not yet in code | Human |
| Calibration stage A: ceiling | 1.5 | Not started | Human |
| Calibration stage B: headroom go or no-go | 4 | Not started | Human |
| One full loop, end to end | 8 | Not started | Human |
| Agents and prompts frozen | 12 | Not started | Human |
| Test seeds frozen | Before hour 12 | Not started | Human only |

## Lane A: world and science (Claude Code, branch `physics`)

**Current milestone:** schemas, integrator, world server, analysis tools, calibration.

**Done**

- Nothing yet.

**In progress**

- Nothing yet.

**Results** (commands, numbers, file paths)

- None yet.

**Blockers**

- None.

**Next step**

- Implement `schemas/` from `SPEC.md` section 5.3 and commit it first, so lane B can import it.

## Lane B: Omnigent and agents (Codex, branch `omnigent`)

**Current milestone:** Omnigent installation/connection and handoff smoke, then mock and wrappers. Two-hour box: 2026-10-03 20:56–22:56 UTC.

**Done**

- Read SPEC.md, AGENTS.md and STATUS.md; confirmed isolated `omnigent` worktree.
- Installed open-source Omnigent 0.16.0 on Python 3.12.10; read upstream `docs/AGENT_YAML_SPEC.md` at tag v0.16.0 (`82a7447`).
- Bundled hello-world completed through the existing Claude subscription login, using the `claude-sdk` harness.
- Live orchestrator + two sub-agents passed JSON spec/result through Python function tools. A Python tool-call policy denied the sentinel before execution.

**In progress**

- Implementing mock HTTP API, typed wrappers, ledger and enforcement tests; production prompts remain stubs.

**Results** (commands, numbers, file paths)

- `git worktree list`: lane B is `/Users/myronsydorov/solzero-omnigent`, branch `omnigent`.
- `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r lab/requirements.txt`: installed 95 packages. Source-main install initially failed on missing Node >=22.13 for its web UI; released wheel installed successfully.
- From `/tmp`: `<repo>/.venv/bin/omnigent run /tmp/solzero-omnigent-upstream/tests/resources/examples/hello_world.yaml --harness claude-sdk --model sonnet --no-session --prompt "Say exactly: SOLZERO_CONNECTION_OK" </dev/null`; output `SOLZERO_CONNECTION_OK`, exit 0. Raw log: `runs/omnigent-smoke/bundled-example.log`.
- `.venv/bin/python -m lab.smoke`: passed, exit 0; handoff = declared `type: agent` + `sys_session_send`, with complete JSON in messages and Python function inputs/outputs. Counts: spec 1, result 1, received 1, policy denied 1, blocked body executions 0. Numeric JSON equality is preserved (1.0 can be reprinted as 1). Evidence: `runs/omnigent-smoke/{handoff.jsonl,handoff.log,summary.json}`.
- `.venv/bin/python -m pytest tests/test_no_leak.py -q`: 2 passed; terms derived from SPEC section 2.

**Blockers**

- Missing dependency: `schemas/` and root dependency configuration have not landed on main. No duplicate schema definitions will be created.

**Next step**

- Verify one bundled Omnigent example, then orchestrator/two-sub-agent function handoff and policy rejection; build against shared schemas once available.

## Requests (one lane asking the other, or the human, for something)

- Lane B → lane A/human: merge `schemas/` to main first and expose its import names; mock/wrappers must use those shared models.
- Lane B → lane A: integrate `lab/requirements.txt` dependencies into your root pyproject: `omnigent==0.16.0`, `fastapi>=0.100,<1`, `uvicorn>=0.30,<1`, `httpx>=0.27,<1`, `pydantic>=2,<3`, and test dependency `pytest>=8,<10`. Human authorized `.gitignore` and `tests/test_no_leak.py`; root pyproject remains lane A owned.
- Lane B → lane A/human (design gap): section 6 requires server enforcement of predictions for every live law, but section 5.2 has no live-law registration field/endpoint. Lane B wrappers enforce full current-set coverage; server can enforce table/spec/cycle/empty-list checks but cannot independently know omitted newly proposed or retired laws. Please resolve in a jointly agreed interface revision; no contract change made here.
- Lane B → lane A: mock admin scoring will explicitly mark fitted-law hidden-probe grading unavailable; mock is for API development, not scientific evaluation.

## Interface changes (every change to `SPEC.md` section 5)

| When | Who | What changed | Why |
| --- | --- | --- | --- |
| | | | |

## Design changes from calibration (dev worlds only)

| When | Change | Before | After | Reason |
| --- | --- | --- | --- | --- |
| | | | | |

## Decisions by the human

- None yet.
