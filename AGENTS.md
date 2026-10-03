# Sol Zero: shared instructions for coding assistants

Sol Zero is a hackathon project with a 24-hour clock. A robot lab discovers an unfamiliar force law in 12 experiments, then launches a sample into untouched targets. The full design is in `SPEC.md`. Read it before writing code.

## Read first, every session

1. `SPEC.md`, especially section 5 (Interfaces) and section 12 (ownership).
2. `STATUS.md`, to see what is done, who owns what, and what is blocked.

## Ownership

| Assistant | Owns | Branch |
| --- | --- | --- |
| Claude Code | `schemas/`, `tools/`, `world/`, `calibration/`, `sim/` | `physics` |
| Codex | `mock/`, `lab/` (Omnigent agents, tool wrappers, policies, ledger) | `omnigent` |
| Human | Merges to `main`, go or no-go decisions, the test-seed freeze | `main` |

- Edit only the paths you own. If you need a change elsewhere, write the request under "Requests" in `STATUS.md`.
- `eval/` and `viewer/` are shared. Claim them in `STATUS.md` before starting.

## The interface contract

- `SPEC.md` section 5 defines every record and endpoint both lanes depend on.
- Never change an interface in code alone. Edit section 5 in the same commit and add a line under "Interface changes" in `STATUS.md`.
- Shared pydantic models live in `schemas/`. Import them; do not redefine them.
- Until the real world server is merged, Codex builds against `mock/`, which must implement section 5.2 exactly.

## Scientific constraints (not negotiable)

- **Test seeds are untouchable.** Seeds 9000 to 9999 and `world/test_seeds.lock` are never generated, run, or inspected before the human declares the freeze. Use dev seeds 1000 to 1999.
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
| Run calibration | `.venv/bin/python -m calibration.study --seeds 1000-1019 --stages A --out calibration/results/dev20_v2 --jobs 12`, then the same with `--stages B --hit-frac 0.02`, then `.venv/bin/python -m calibration.report calibration/results/dev20_v2` |
| Run one lab session on a dev world | to be added |

## Working protocol

- Work in your own git worktree on your own branch. Commit small and often. Do not push to `main`.
- In `STATUS.md`, edit only your own lane's section, plus "Requests" and "Interface changes". This avoids merge conflicts.
- Update `STATUS.md` when you finish a milestone, hit a blocker, or stop working: what was done, the exact commands, the results with numbers, and the next step.
- Time-box. If a milestone runs past its box, stop, write the partial evidence and the blocker in `STATUS.md`, and ask the human.
- When something fails, say which of these it is: a bug, a numerical problem, a design problem, or a missing dependency.
- Ask the human before: changing the mission definition, changing the physics families, touching anything about test seeds, or adding a heavy dependency.
