# Status

Last updated: 2026-10-03 (lane A: calibration stages A and B). Each assistant edits only its own lane section, plus "Requests" and "Interface changes".

## Gates

| Gate | Target hour | State | Decided by |
| --- | --- | --- | --- |
| Schemas agreed (`SPEC.md` section 5) | 0.5 | In code (`schemas/`), awaiting human sign-off | Human |
| Calibration stage A: ceiling | 1.5 | Measured: 47% (reference 80%), limited by actuation noise | Human |
| Calibration stage B: headroom go or no-go | 4 | Measured: the 5 cm metric does not separate the policies (random 95%) | Human |
| One full loop, end to end | 8 | Not started | Human |
| Agents and prompts frozen | 12 | Not started | Human |
| Test seeds frozen | Before hour 12 | Not started | Human only |

## Lane A: world and science (Claude Code, branch `physics`)

**Current milestone:** calibration stages A and B done on 20 dev worlds. Waiting on a human go or no-go (see Blockers).

**Done** (2026-10-03, about 2 h)

- `schemas/`: pydantic models for every record and request body in SPEC 5.2 to 5.5, round-trip tests. Committed on its own (`76ff1d6`) so lane B can import it: `from schemas import Law, Result, SessionInfo, ...`.
- `tools/`: batched fixed-step RK4 integrator (dt 8 ms, error about 1e-6 m against `solve_ivp`, tested on 20 random cases), `simulate`, `fit_law` (scipy `least_squares`, batched finite-difference Jacobian, actuation noise propagated into the weights, LOO, covariance), `predict`/`predict_many`, `disagreement`/`disagreement_many`, `plan_shot`.
- `world/generator.py`: four families from SPEC 2. The family is `seed % 4`. Five targets per world (2 reachable at 4 m/s or less, 3 needing more), 20 hidden probes, and shot zero planned with textbook physics. Raises `PermissionError` for seeds 9000 to 9999 unless `final_eval=True`. No test seed was generated, run or inspected.
- `calibration/`: 12-form library, Stage A (true form on a fixed 12-experiment spread design), Stage B (random versus greedy disagreement, shared fitting and BIC selection), report and plot.
- `tests/test_no_leak.py`: scans `lab/` for family names, generator parameter names and hidden-package imports. It skips until `lab/` exists.

**Results** on dev seeds 1000 to 1019 (20 worlds, 5 per family). Raw JSON is in `calibration/results/dev20/`, the summary in `summary.json`, and the plot in `error_vs_experiment.png`. Hit probability means the mean over 400 draws of launcher actuation error. Nominal means no actuation error.

| Criterion (SPEC 8) | Reference | Measured | Verdict |
| --- | --- | --- | --- |
| Ceiling: true-form fit hits | at least 80% | 47% (in-range 65%, beyond 36%); nominal 88% | Fails, cause is noise |
| Headroom: greedy reaches 5 cm within 12 | at least 70% | 100% | Passes |
| Headroom: random reaches 5 cm within 12 | at most 40% | 95% | Fails, the metric is too easy |
| Separability (beyond-range, non-control) | at least 70% | 80% (nominal); beyond hit probability 36% true form vs 13% best wrong form | Passes nominally |
| Control: greedy false discovery | under 10% | 0/5 (random: 3/5) | Passes, n = 5 |

Failure diagnosis:

- **Fitting is fine.** The true form fitted to noiseless data gives a median probe error of 6 mm or less in all 20 worlds. With noise it is 2 to 55 mm, with a median of about 8 mm. BIC picks the true form in 18 of 20 worlds on the spread design.
- **The mission ceiling is limited by noise: launcher actuation error (2% speed, 0.5 degrees).** Shots planned with the exact hidden law hit 100% nominally but only 49% under actuation error (in-range 65%, beyond 38%). The miss grows with range, and low-g0 worlds put targets 5 to 12 m out. This sweep is diagnostic only; the design noise was not changed:

  | Actuation (speed frac / deg) | Exact truth | True-form fit | Greedy-selected | Random-selected |
  | --- | --- | --- | --- | --- |
  | 0.02 / 0.5 (current) | 49% | 47% | 46% | 46% |
  | 0.01 / 0.25 | 70% | 65% | 64% | 61% |
  | 0.005 / 0.1 | 85% | 77% | 77% | 70% |
  | 0 / 0 | 100% | 88% | 82% | 76% |

- **No target is truly unreachable.** Every target is hit nominally by the exact law. Three targets in two F3 worlds with negative kappa (1003, 1019) are flagged `reachable=false` by `plan_shot`, because more than 10% of its parameter-and-actuation draws fly out of the workspace. Treat this as a planner-envelope flag.
- **The headroom metric is uninformative, not the experiments.** The 5 cm median probe threshold is reached after a median of 2 experiments (random) and 3 (greedy). The paired difference in experiments to 5 cm is 0.0 (95% bootstrap CI -1.6 to 1.7). Greedy spends its first three steps on fixed weigh, drop and launch seeds. Where the policies do separate:
  - error after 12 experiments: 4.6 mm greedy vs 9.0 mm random (medians)
  - final form correct: 20/20 vs 17/20; all 3 random misses are F0 control worlds wrongly called non-ordinary
  - nominal mission hits: 82% vs 76%
- Runtime: Stage A takes 6 to 200 s per world. Stage B takes 230 to 270 s per world and policy on average, with a few worlds at 400 to 500 s. The 20-world run took 18 min wall time with 12 processes. 200 worlds would take about 3 h unless the fit loop is sped up.
- `calibration/results/dev5/` holds the first 5-world runtime run. It predates the noiseless-diagnostic fix, so its `noiseless_true_fit_chi2` values are meaningless; use dev20.

**Commands**

```
uv venv --python 3.12 && uv sync
.venv/bin/python -m pytest -q                      # 27 passed, 1 skipped (leak test, no lab/ yet), about 50 s
.venv/bin/python -m calibration.study --seeds 1000-1019 --out calibration/results/dev20 --jobs 12
.venv/bin/python -m calibration.report calibration/results/dev20
```

**Blockers** (design problems; these need a human decision, and nothing has been tuned)

1. Mission ceiling: under 2% speed and 0.5 degree actuation error, even the exact law hits 49%. Options: lower the actuation error (the table above gives the measured effect), widen the hit radius, cap the target range, or score the mission by expected miss instead of hit or miss.
2. Headroom metric: the 5 cm median probe threshold separates nothing, because random reaches it in 95% of worlds. Options: a stricter threshold such as 1 cm, a probe error computed on beyond-range probes or mission shots only, or making law recovery and false discovery the headline (these do separate in this run). SPEC 8 lists two fallbacks, cutting the budget to 8 and dropping flight time. Neither targets the issue here, since the threshold falls after 1 to 3 experiments.

**Next step**

- Get the human's decision on blockers 1 and 2. Then rerun calibration on dev worlds, logging each change under "Design changes".
- Meanwhile: build the world server (SPEC 5.2) on top of `world/generator.py` and `tools/`, and speed up the fit loop (fewer least-squares evaluations, early exit on escaping trajectories).

## Lane B: Omnigent and agents (Codex, branch `omnigent`)

**Current milestone:** Omnigent smoke test and mock world server.

**Done**

- Nothing yet.

**In progress**

- Nothing yet.

**Results** (commands, numbers, file paths)

- None yet.

**Blockers**

- None.

**Next step**

- Install Omnigent, run one of its bundled examples, then build `mock/` against `SPEC.md` section 5.2.

## Requests (one lane asking the other, or the human, for something)

- Lane A to human: decide calibration blockers 1 (actuation noise versus mission ceiling) and 2 (headroom metric). See lane A Blockers.

## Interface changes (every change to `SPEC.md` section 5)

| When | Who | What changed | Why |
| --- | --- | --- | --- |
| 2026-10-03 | Claude Code | `FitResult.cov` (optional parameter covariance) added; `loo_error` defined as RMS in noise-sd units | `predict` needs correlated parameter draws; the units were undefined |
| 2026-10-03 | Claude Code | `noise_sd` in `SessionInfo` and in launch `Result` gains `speed_frac` and `elevation_deg` (launcher actuation error) | Actuation error dominates launch noise; the fit must weight launches by it |
| 2026-10-03 | Claude Code | SPEC 5.4: analysis tools take keyword-only extras (`samples`, `noise_sd`, `seed`, `n_draws`); added `predict_many` and `disagreement_many` | Explicit seeds; batched candidate scoring |

## Design changes from calibration (dev worlds only)

| When | Change | Before | After | Reason |
| --- | --- | --- | --- | --- |
| | | | | |

## Decisions by the human

- None yet.
