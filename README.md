# Sol Zero

A robot lab that has to discover an unfamiliar force law from 12 experiments, then launch a sample into five untouched targets. Three of the targets lie beyond the speeds it was allowed to test. A multi-agent lab (Omnigent) chooses the experiments, proposes and rejects laws, and commits the firing table. The same tools run under a random-experiment baseline, a single-agent baseline, a textbook-physics floor and a scripted oracle.

The full design is in [`SPEC.md`](SPEC.md). Progress and every number we have measured are in [`STATUS.md`](STATUS.md).

## How it works

- **Hidden world** (`world/`). Each world draws one of four physics families: ordinary physics with unfamiliar constants (the control), a different drag law, mass-dependent gravity, or height-dependent gravity. The world server exposes only the experiment API (SPEC 5.2). Families, parameters and probe scores stay behind admin endpoints.
- **Experiments.** `weigh`, `drop` and `launch` return one or two noisy summary numbers each, never trajectories. Launches carry 0.5% speed and 0.1 degree actuation error.
- **Analysis tools** (`tools/`). `fit_law`, `predict`, `disagreement` and `plan_shot` work on free-form law expressions, using a batched RK4 integrator. Every condition uses the same tools.
- **Lab** (`lab/`). Five Omnigent agents (PI, Theorist, Experimentalist, Operator, Analyst) work under pre-registration: no experiment runs without a prediction from every live law.
- **Evaluation** (`eval/`). One runner covers every condition. Grading and statistics were fixed before any test world was run.

## Reproduce

Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv venv --python 3.12 && uv sync
.venv/bin/python -m pytest -q

# World server (dev seeds 1000-1999). Admin endpoints need the token.
SOLZERO_ADMIN_TOKEN=change-me .venv/bin/python -m world.server --port 8000

# Calibration on dev worlds (scripted policies, no language model)
.venv/bin/python -m calibration.study --seeds 1000-1059 --stages AB --hit-frac 0.02 --out calibration/results/dev60 --jobs 8
.venv/bin/python -m calibration.report calibration/results/dev60

# Evaluation. --serve starts a private world server; otherwise pass --world-url and --admin-token.
.venv/bin/python -m eval.run --condition textbook --seeds 1000-1002 --out runs/eval --serve
.venv/bin/python -m eval.run --condition oracle   --seeds 1000-1002 --out runs/eval --serve
.venv/bin/python -m eval.run --condition random --agent scripted --seeds 1000-1002 --out runs/eval --serve
.venv/bin/python -m eval.run --condition lab    --seeds 1000-1002 --out runs/eval --serve --agent-cmd ".venv/bin/python -m lab.run"
.venv/bin/python -m eval.run --condition single --seeds 1000-1002 --out runs/eval --serve --agent-cmd ".venv/bin/python -m lab.run"
.venv/bin/python -m eval.run --condition random --seeds 1000-1002 --out runs/eval --serve --agent-cmd ".venv/bin/python -m lab.run"
.venv/bin/python -m eval.grade runs/eval
.venv/bin/python -m eval.report runs/eval --readme README.md
```

- The runner skips finished runs, so rerunning the same command resumes after a crash.
- Without a language model, `--agent-cmd ".venv/bin/python -m eval.agent_stub"` exercises the whole pipeline.
- The agent command line is specified in SPEC 5.6.

**Final evaluation, human only.** The human freezes 40 test seeds (10 per family) with `python -m world.freeze --entropy <string> --confirm-human-freeze`. The same commands then run with `--final-eval` on the seeds in `world/test_seeds.lock`. No code path serves a test seed without that flag.

## Disclosure: difficulty was calibrated on dev worlds

The task's difficulty was set on development worlds (seeds 1000 to 1999) with scripted policies, before any agent saw a test world. That covers the launcher noise, the hit radius, the probe set and the target placement. The changes were:

- Launcher actuation error cut from 2% / 0.5 degrees to 0.5% / 0.1 degrees, because even the exact law could hit only 49% of targets.
- A hit radius of max(5 cm, 2% of target distance).
- A probe set of 20 in-range and 20 beyond-range launches.

Each change was decided by the human from measured numbers and is logged with before and after values in `STATUS.md` ("Design changes from calibration"). The experiment budget, the parameter ranges and the physics families were never tuned to favour any condition. The four primary metrics were fixed in SPEC section 7 before the test seeds were frozen. Test seeds 9000 to 9999 were not generated, run or inspected during development.

## Results

### Dev-world calibration (scripted policies, 60 worlds)

From `calibration/results/dev60/primary_table.md`: greedy disagreement versus uniform random experiment choice, with the same fitting and model selection.

| Metric | Random | Greedy | Paired difference (greedy minus random) |
| --- | --- | --- | --- |
| Beyond-range probe hit rate | 0.89 [0.83, 0.95] | 0.995 [0.99, 1.00] | +0.10 [+0.05, +0.16] |
| Mission hit rate | 0.90 [0.85, 0.94] | 0.95 [0.92, 0.97] | +0.056 [+0.024, +0.094] |
| Law-form recovery | 0.90 [0.82, 0.97] | 0.97 [0.92, 1.00] | +0.07 [-0.02, +0.17] |
| Control false discovery (15 worlds) | 0.20 [0.00, 0.40] | 0.13 [0.00, 0.33] | -0.07 [-0.33, +0.20] |

These dev worlds were used to calibrate the task, so this table is not an independent test.

### Evaluation

<!-- results:start -->
_No evaluation results yet. `python -m eval.report runs/eval --readme README.md` fills this section in._
<!-- results:end -->

## Limits

The physics is simulated and designed by us. The families are few and smooth. Sensing is Gaussian noise on summary numbers. Forty test worlds give wide intervals. SPEC section 11 has the full list.

## Repository

| Path | Contents |
| --- | --- |
| `schemas/` | Shared pydantic records (SPEC 5) |
| `tools/` | Integrator and analysis tools |
| `world/` | Hidden generator, world server, freeze script |
| `calibration/` | Scripted calibration study and its results |
| `eval/` | Runner, sampler, scripted references, grading, report |
| `lab/`, `mock/` | Omnigent agents and mock server (lane B) |
| `sim/` | MuJoCo scene (sim lane) |
| `viewer/` | Replay site (viewer lane) |
