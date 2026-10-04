# Sol Zero: shared instructions for coding assistants

Sol Zero is a hackathon project with a 24-hour clock. A robot lab discovers an unfamiliar force law in 12 experiments, then launches a sample into untouched targets. The full design is in `SPEC.md`. Read it before writing code.

## Read first, every session

1. `SPEC.md`, especially section 5 (Interfaces) and section 12 (ownership).
2. `STATUS.md`, to see what is done, who owns what, and what is blocked.

## Ownership

| Lane | Owns | Branch and worktree |
| --- | --- | --- |
| physics: eval and integration (Claude Code, the integrator) | `schemas/`, `tools/`, `world/`, `calibration/`, `eval/`, `README.md`, root `pyproject.toml` and `uv.lock`, `AGENTS.md` | `physics` in `solzero-physics` |
| omnigent: lab (Codex) | `mock/`, `lab/` (Omnigent agents, tool wrappers, policies, ledger) | `omnigent` in `solzero-omnigent` |
| sim: MuJoCo | `sim/` (scene, arm primitives, launcher, fast and full mode) | `sim` in `solzero-sim` |
| viewer: site | `viewer/` (ledger and video replay site) | `viewer` in `solzero-viewer` |
| Human | Go or no-go decisions, the test-seed freeze, approval of mission and physics changes | none |

- Edit only the paths you own. If you need a change elsewhere, write the request under "Requests" in `STATUS.md`.
- `tests/` is shared: add test files for your own paths; do not edit another lane's test files.
- Dependencies: ask the integrator under "Requests". Only the physics lane edits `pyproject.toml` (with `uv add`).
- The integrator resolves cross-lane merge conflicts on `main` and records each resolution in `STATUS.md`.

## Merging and autonomy

- Run `git merge main` in your worktree before starting each milestone.
- When your full test suite passes (`.venv/bin/python -m pytest -q`), merge your branch into main yourself: `git -C /Users/myronsydorov/solzero merge <branch>`. Never leave `main` with failing tests; if a merge breaks them, fix it forward at once or revert the merge.
- Commit schema and SPEC section 5 changes first, in their own commit, before the code that uses them.
- Work through your milestones without stopping to ask. When a choice comes up, make the reasonable call, log it under "Decisions made autonomously" in `STATUS.md`, and continue.
- Stop and ask the human only for: anything touching test seeds, changing the mission or the physics families, or a blocker you cannot diagnose.
- Use parallel subagents for independent work.
- Update `STATUS.md` after each milestone with the exact commands and the numbers.

## The interface contract

- `SPEC.md` section 5 defines every record and endpoint both lanes depend on.
- Never change an interface in code alone. Edit section 5 in the same commit and add a line under "Interface changes" in `STATUS.md`.
- Shared pydantic models live in `schemas/`. Import them; do not redefine them.
- The real world server is `world/server.py`. `mock/` must keep implementing section 5.2 exactly, for tests that run without the physics.

## Scientific constraints (not negotiable)

- **Test seeds are untouchable.** Seeds 9000 to 9999 and `world/test_seeds.lock` are never generated, run, or inspected before the human declares the freeze. Use dev seeds 1000 to 1999. `python -m world.freeze` is run by the human only.
- **Do not force results.** Never tune noise, ranges, budgets, prompts, or thresholds to make the lab beat a baseline. Report what is measured, including null results.
- **Design changes happen on dev worlds only,** and each one is logged in `STATUS.md` with before and after numbers.
- **The scientific agents are isolated.** The Omnigent agents in `lab/` reach the world only through the HTTP API in section 5.2. They never import from, read, or receive anything from `world/`, `calibration/`, `sim/` or `eval/`, and they never call admin endpoints.
- **No leaks in prompts.** Agent prompts and tool descriptions must not name the physics families, the parameter ranges, or the 12-form library. They may say that physics can differ from Earth's.
- **Baselines share tools.** The random and single-agent conditions use the same `fit_law`, `predict` and `plan_shot` as the lab.
- **Abstention is valid.** "Insufficient evidence" is an allowed verdict and final claim. Do not prompt it away.
- **Do not script the discovery.** No hard-coded hypotheses, experiment sequences, or hints about a specific world.

You, the coding assistants, may read the whole repository. The isolation rules above apply to the agents you build.

## Engineering rules

- Python 3.12. One virtual environment at the repo root, dependencies in `pyproject.toml`.
- SI units everywhere. Angles are degrees in specs and radians inside the integrator.
- Every stochastic function takes an explicit seed. Runs must be reproducible from a command line.
- Keep it small. Prefer one clear module over a framework. No abstraction for a second use case that does not exist yet.
- Write a test for anything another lane depends on: schema round-trips, endpoint behaviour, budget and pre-registration enforcement.
- Keep a leak test at `tests/test_no_leak.py` that scans `lab/` for family names and parameter names from the generator and fails if it finds any.
- Save raw results before plotting. Results go to `calibration/results/` or `runs/`, never only to the terminal.
- Performance matters in the fit loop. Use the batched fixed-step integrator from `tools/`, not a per-experiment `solve_ivp` call.

## Commands

Fill these in as they come into existence, and keep them current.

| Purpose | Command |
| --- | --- |
| Set up the environment | `uv venv --python 3.12 && uv sync` |
| Run tests | `.venv/bin/python -m pytest -q` |
| Start the world server | `SOLZERO_ADMIN_TOKEN=<token> .venv/bin/python -m world.server --port 8000` (dev seeds; world ids from `GET /admin/worlds`) |
| Start the mock server | to be added |
| Run calibration | `.venv/bin/python -m calibration.study --seeds 1000-1059 --stages AB --hit-frac 0.02 --out calibration/results/dev60 --jobs 8`, then `.venv/bin/python -m calibration.report calibration/results/dev60` |
| Run one lab session on a dev world | `.venv/bin/python -m eval.run --condition lab --seeds 1000 --out runs/eval --serve --agent-cmd ".venv/bin/python -m lab.run"` (needs SPEC 5.6 in `lab.run`) |
| Run an evaluation condition | `.venv/bin/python -m eval.run --condition {lab,single,random,textbook,oracle} --seeds 1000-1059 --out runs/eval --serve [--agent scripted]` |
| Grade and report | `.venv/bin/python -m eval.grade runs/eval && .venv/bin/python -m eval.report runs/eval --readme README.md` |
| Load test the server | `.venv/bin/python -m world.loadtest --url URL --admin-token T --sessions 16 --out runs/loadtest` |
| Freeze test seeds (human only) | `.venv/bin/python -m world.freeze --entropy <string> --confirm-human-freeze` |

## Working protocol

- Work in your own git worktree on your own branch. Commit small and often. Merge to `main` only as described above; do not push to a remote unless the human asks.
- In `STATUS.md`, edit only your own lane's section, plus "Requests" and "Interface changes". This avoids merge conflicts.
- Update `STATUS.md` when you finish a milestone, hit a blocker, or stop working: what was done, the exact commands, the results with numbers, and the next step.
- Time-box. If a milestone runs past its box, write the partial evidence and the blocker in `STATUS.md`, then continue with the next milestone unless the blocker is one of the stop conditions above.
- When something fails, say which of these it is: a bug, a numerical problem, a design problem, or a missing dependency.
- Ask the human before: changing the mission definition, changing the physics families, or touching anything about test seeds. A heavy dependency is a decision you log, not a stop.
