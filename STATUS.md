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

**Current milestone:** installation and live transport smoke verified; mock, wrappers, ledger and stub agents implemented, awaiting shared-schema integration to execute contract tests.

**Done**

- Read SPEC.md, AGENTS.md and STATUS.md; worked only in the isolated `omnigent` worktree and authorized paths.
- Installed open-source Omnigent 0.16.0 on Python 3.12.10. Read upstream `docs/AGENT_YAML_SPEC.md` at matching tag v0.16.0 (`82a7447`). Additional dependencies are in `lab/requirements.txt`; root pyproject untouched.
- Bundled hello-world completed through the existing Claude subscription login using `claude-sdk` and `sonnet`.
- Live orchestrator + Operator + Analyst passed a JSON ExperimentSpec and Result through Python functions. A Python policy denied a sentinel call before its body executed.
- Implemented `mock/server.py`: closed-form Earth fixture with seeded sensor/actuation noise, five public routes, protected admin routes, atomic 12-experiment budget, failed-run accounting, pre-registration/spec/cycle/table-ownership checks, sample and range restrictions, and final five-shot commit. Hidden dev probes use shared analysis tools when installed; unavailable scores are marked explicitly.
- Implemented typed fixed-route HTTP client, session/pre-registration and full live-law coverage checks, shared-analysis adapters with explicit seeds, role/budget/approval policies, and durable JSONL ledger/decision diffs in `lab/`. Ambiguous mutation responses block blind retries.
- Added five role YAMLs with prompt stubs only. They require host session binding in the tool runner; no scientific discovery loop is enabled. The host must wire the single Omnigent mission approval decision into the exact-table approval callback.
- Added the human-authorized SPEC-section-2-derived leak test and `.gitignore`, contract/wrapper tests under `mock/tests/`, usage notes, and a real-loopback verification command that saves raw evidence.
- No test seeds generated, run or inspected; no calibration/design changes; no hidden vocabulary in lab code/prompts. No duplicate shared models created.

**In progress**

- Shared schemas are committed on `physics` but have not landed on `main`. Contract tests and live mock verification await that dependency; code is not claimed runtime-verified yet.

**Results** (commands, numbers, file paths)

- Two-hour box began 2026-10-03 20:56 UTC and ends 22:56 UTC. Work is blocked on the requested main merge before that deadline.
- `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r lab/requirements.txt`: 95 packages installed. Source-main install initially failed because its web UI requires Node >=22.13; the release wheel installed successfully without a Node upgrade.
- From `/tmp`: `<repo>/.venv/bin/omnigent run /tmp/solzero-omnigent-upstream/tests/resources/examples/hello_world.yaml --harness claude-sdk --model sonnet --no-session --prompt "Say exactly: SOLZERO_CONNECTION_OK" </dev/null`: returned `SOLZERO_CONNECTION_OK`, exit 0. Raw output: `runs/omnigent-smoke/bundled-example.log`.
- `.venv/bin/python -m lab.smoke`: passed, exit 0. Working handoff: declared `type: agent` tools via `sys_session_send`; complete JSON crosses child messages and Python tool arguments/results. Actual audit counts: spec 1, result 1, received 1, policy denied 1, blocked-body executions 0. Numeric JSON equality is preserved (1.0 may be reprinted as 1). Raw evidence: `runs/omnigent-smoke/{handoff.jsonl,handoff.log,summary.json}`.
- `.venv/bin/python -m pytest tests/test_no_leak.py -q`: **2 passed**. `.venv/bin/python -m compileall -q lab mock tests` and `git diff --check`: passed.
- `.venv/bin/python -m pytest tests/test_no_leak.py mock/tests -q --junitxml=runs/omnigent-smoke/pre-integration-tests.xml`: **2 collection errors**, both `ModuleNotFoundError: No module named 'schemas'`; this is a missing dependency, not evidence about endpoint correctness. Raw report saved.
- Omnigent's `spec.load` accepts `lab/agents/smoke.yaml`; the five scientific stub YAMLs cannot resolve their function imports until `schemas` is present.
- Installation/smoke milestone commit: `97a02e0`.

**Blockers**

- **Missing dependency:** schemas are not on main (last checked main: `98ee768`); schema commit `76ff1d6` and follow-up `5d0547d` are on physics. Human merge requested. No schema files copied or redefined. Shared analysis tools also need integration for actual numerical adapter verification.
- **Design problem in the current contract:** server cannot independently know every live law because section 5.2 has no live-set registration mechanism. Wrappers enforce completeness locally; server enforces all information actually provided. See Requests.
- MLflow trace integration and the first scientific loop are outside this prompt-stub milestone and remain unimplemented.

**Next step**

1. Human merges the shared schemas/setup to main. Bring main into this worktree, preserving each lane's status and authorized files; install root dependencies plus `lab/requirements.txt` into the existing `.venv`.
2. Run `.venv/bin/python -m pytest tests/test_no_leak.py mock/tests -q --junitxml=runs/omnigent-smoke/integration-tests.xml`; fix any actual failures. Run `.venv/bin/python -m mock.check --seed 1000 --output runs/mock-check` and save the raw results.
3. Validate all six YAMLs through `omnigent.spec.load`, validate the saved transport records with shared models, and exercise the numerical wrappers against the shared analysis functions. Keep the 22:56 UTC deadline; record any remaining blocker then.
4. At the next milestone, connect host session binding/approval to the complete five-agent loop and tracing; keep scientific prompts as stubs until authorized to develop them.

## Requests (one lane asking the other, or the human, for something)

- Lane B → human: merge `schemas/` to main first (initial commit `76ff1d6`; compatible follow-up `5d0547d`) so the mock/wrappers can import and test the shared models. This is the active integration blocker.
- Lane B → lane A: integrate `lab/requirements.txt` dependencies into your root pyproject: `omnigent==0.16.0`, `fastapi>=0.100,<1`, `uvicorn>=0.30,<1`, `httpx>=0.27,<1`, `pydantic>=2,<3`, and test dependency `pytest>=8,<10`. Also include `mock/tests` in the root pytest testpaths. Human authorized `.gitignore` and `tests/test_no_leak.py`; root pyproject remains lane A owned.
- Lane B → lane A/human (design gap): section 6 requires server enforcement of predictions for every live law, but section 5.2 has no live-law registration field/endpoint. Lane B wrappers enforce full current-set coverage; server can enforce table/spec/cycle/empty-list checks but cannot independently know omitted newly proposed or retired laws. Please resolve in a jointly agreed interface revision; no contract change made here.
- Lane B → lane A: mock admin scoring uses shared `tools.predict` when installed and explicitly marks a score unavailable otherwise. Mock is for API development, not scientific evaluation.
- Lane B → lane A (bug): `schemas.records.law_expr_symbols` currently passes untrusted expression strings directly to `sympy.parse_expr`. Please validate a restricted arithmetic AST before parsing; lab tool adapters now do so before model validation, but HTTP schema parsing needs the same boundary protection in your lane.

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
