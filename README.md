# Sol Zero

A simulated robot lab that learns from experiments before committing five mission shots. An Omnigent lab proposes laws, chooses measurements, judges evidence and decides when to stop. Current code has four model-backed roles and a deterministic Operator. Every experiment leaves an auditable record of the prediction and result.

**Watch:** [two-minute demo](docs/media/demo.mp4) · [two-minute technical walkthrough](docs/media/technical.mp4) · [narration and disclosures](docs/submission-scripts.md)

**Explore:** [interactive replay viewer](https://viewer-delta-ten.vercel.app) — the deployed site currently shows **fixture data**, not the completed run below.

The videos are rendered replays of a development simulation, not a live physical robot. The lab connects to the actual project world server over HTTP; that server still implements simulated physics.

## Completed lab run

Development world 1000, session **`s_dce1ebea1d`**: the PI chose to stop after **10 of 12 available experiments**, and the server graded **5/5 mission shots as hits**, including three beyond-range targets. Its final claim was **`predictive_only`**, with `claims_non_ordinary=false`. This historical baseline used a model-backed Operator; it predates the current deterministic execution optimization.

| Target | Server outcome | Miss distance |
| --- | --- | ---: |
| t1 | Hit | 15.18 mm |
| t2 | Hit | 0.40 mm |
| t3 | Hit | 4.63 mm |
| t4 | Hit | 16.93 mm |
| t5 | Hit | 7.33 mm |

[Run evidence](docs/evidence/lab-dev1000) includes the ledger and recorded outcomes. This standalone run took about **17.1 active minutes**, with pauses and resumes. Token accounting includes an interrupted response and is incomplete. It demonstrates the workflow; it does **not** establish superiority over random experiments or a single agent, or success on held-out test worlds.

## How it works

- **Five roles:** the Theorist owns live laws, the Experimentalist chooses experiments, the Operator executes, the Analyst judges evidence, and the PI decides when to commit.
- **Pre-registration:** predictions cover every live law before an experiment. The server enforces the twelve-experiment budget, including failed measurements.
- **Shared tools:** fitting, prediction, disagreement and shot planning use the same numerical implementation across conditions.
- **Isolation:** scientific agents receive public observations through HTTP, without generator code or admin scores.
- **Records:** a JSONL ledger captures laws, candidate choices, predictions, results, verdicts, changed decisions, nominations and the mission commit.

The design is in [SPEC.md](SPEC.md), with measured results and development decisions in [STATUS.md](STATUS.md).

## Reproduce

Use Python 3.12 and [uv](https://docs.astral.sh/uv/). The default check makes no model calls:

```sh
uv venv --python 3.12 && uv sync
.venv/bin/python -m pytest -q
```

One scripted development run, also without a model:

```sh
.venv/bin/python -m eval.run --condition textbook --seeds 1000 \
  --out runs/textbook-dev1000 --serve --max-concurrency 1
```

**Optional, consumes model quota:** the agent run requires a working Claude Code subscription login and Omnigent's Claude SDK connection. Development runs reached the subscription session limit; a run can pause or stop before completing. The explicit caps below may stop it before its mission. Video duration is not model runtime.

```sh
.venv/bin/python -m eval.run --condition lab --seeds 1000 \
  --out runs/lab-dev1000 --serve --max-concurrency 1 \
  --max-tokens 2000000 --max-wall-s 600 --max-pauses 0 --approval auto \
  --agent-cmd ".venv/bin/python -m lab.run"
```

The runner saves state and artifacts. A fresh attempt can be created after a crash; ambiguous experiment outcomes are not blindly replayed. See [SPEC section 5.6](SPEC.md#56-agent-run-cli-lane-b-exposes-eval-calls) and [lab documentation](lab/README.md). Automatic approval above is explicit and for development; interactive demos retain the human gate. Test-seed generation and evaluation require a separate human freeze decision.

## Disclosure: difficulty was calibrated on dev worlds

The task's difficulty was set on development worlds (seeds 1000 to 1999) with scripted policies, before any agent saw a test world. That covers the launcher noise, the hit radius, the probe set and the target placement. The changes were:

- Launcher actuation error cut from 2% / 0.5 degrees to 0.5% / 0.1 degrees, because even the exact law could hit only 49% of targets.
- A hit radius of max(5 cm, 2% of target distance).
- A probe set of 20 in-range and 20 beyond-range launches.

Each change was decided by the human from measured numbers and is logged with before and after values in `STATUS.md` ("Design changes from calibration"). The experiment budget, the parameter ranges and the physics families were never tuned to favour any condition. The four primary metrics are fixed in SPEC section 7. Test seeds 9000 to 9999 were not generated, run or inspected during development.

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

### Agent comparison

<!-- results:start -->
No completed matched comparison is reported here. The standalone lab result above is development evidence; the scripted calibration table is not an agent comparison.
<!-- results:end -->

## Known limits

- All physics and robot footage are simulated; this is not a physical robotics deployment.
- One completed lab run is not statistical evidence of superiority. A matched lab/single/random evaluation remains incomplete.
- The demonstrated run exceeded the ten-minute runtime target; subscription quota constrained further validation.
- Token totals are incomplete for one interrupted response. Replay timing is compressed.
- The public viewer currently presents fixtures. Use the linked recorded evidence for the submitted lab run.
- World families are deliberately limited, with noisy summary observations rather than full sensor streams. See SPEC section 11.

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
