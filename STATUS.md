# Status

Last updated: 2026-10-04 (lane A: decisions 1 to 3, calibration rerun, world server). Each assistant edits only its own lane section, plus "Requests" and "Interface changes".

## Gates

| Gate | Target hour | State | Decided by |
| --- | --- | --- | --- |
| Schemas agreed (`SPEC.md` section 5) | 0.5 | In code (`schemas/`), awaiting human sign-off | Human |
| Calibration stage A: ceiling | 1.5 | After decision 1: exact law 95.2%, true-form fit 94.2% (2% radius); wrong-form check 49% (nesting) | Human |
| Calibration stage B: headroom go or no-go | 4 | New headline: random 95%, greedy 100%; co-headlines separate (recovery 16 vs 19 of 20, false discovery 2/5 vs 0/5) | Human |
| One full loop, end to end | 8 | Not started | Human |
| Agents and prompts frozen | 12 | Not started | Human |
| Test seeds frozen | Before hour 12 | Not started | Human only |

## Lane A: world and science (Claude Code, branch `physics`)

**Current milestone:** decisions 1 to 3 applied, calibration rerun on the same 20 dev seeds, world server built. Next is wiring an end-to-end loop with lane B.

**Done** (2026-10-04)

- Dependencies: lane B's `lab/requirements.txt` packages are now in `pyproject.toml` (`omnigent==0.16.0`, fastapi, uvicorn, httpx), and `mock/tests` is in `testpaths` (`4b71a1c`).
- Decision 1:
  - Launcher actuation error is now 0.5% speed and 0.1 degrees, for experiments and the mission.
  - `Target.hit_radius_m` = max(5 cm, 2% of target distance); see below for why 2% rather than 3%.
  - `plan_shot` reports `reachable=false` only when no nominal solution exists.
- Decision 2: there are now 40 hidden probes (20 at 1 to 4 m/s, 20 at 4 to 7 m/s). The headline is the number of experiments until 80% of beyond-range probes are within the hit radius. Co-headlines are law-form recovery and control-world false discovery.
- Decision 3: `POST /laws` replaces the live set. `/predictions` must cover exactly that set, or the server returns 422.
- World server `world/server.py` (FastAPI) implements every SPEC 5.2 endpoint plus admin:
  - `GET /admin/score/{session_id}`, `GET /admin/truth/{world_id}`, and `GET /admin/worlds`, which maps dev seeds to opaque world ids.
  - Admin endpoints need the `X-Admin-Token` header to equal `SOLZERO_ADMIN_TOKEN`; they are disabled when it is unset.
  - World ids are opaque hashes, so the seed and family cannot be read off them.
  - Only dev seeds are served. `--final-eval` reads `world/test_seeds.lock` instead (that file does not exist and was not created).
  - Every session's raw record goes to `runs/<session_id>/world_session.json`.
  - `tests/test_server.py` covers pre-registration, live-set checks, ranges, forbidden samples, budget, nominate, commit and admin auth.
- Smoke test: a live uvicorn server on port 8765 created a session and ran a drop.

**Results** on dev seeds 1000 to 1019 (the same 20 worlds). Raw JSON is in `calibration/results/dev20_v2/`, the summary in `summary.json`, and the plot in `error_vs_experiment.png`. Hit probabilities are under 0.5% / 0.1 degree actuation error, with 400 draws per shot.

Stage A (decision 1 check, made before Stage B ran):

| | Radius 3% | Radius 2% |
| --- | --- | --- |
| Exact-law hit rate (all / in-range / beyond) | 97.5 / 99.9 / 95.9% | **95.2** / 98.6 / 92.9% |
| True-form fit hit rate | 96.7% | 94.2% |
| Best wrong form, beyond-range, all worlds | 50.0% | **49.2%** |
| Best wrong form, beyond-range, non-control | 33.3% | 32.9% |
| Best form that does not contain the truth, beyond-range | 6.1% | 4.0% |

- The 3% radius fails the wrong-form check (50% > 30%), so the radius is 2%, as decided.
- **2% also fails the check:** 49% overall, 33% on non-control worlds.
- **The cause is nesting.** In F0 and F1 worlds the best "wrong" form is a superset that contains the true law, with its extra parameter fitted near 0. Examples: `height_p2` on F0, `mass_p3` on a p = 3 F1 world. These forms hit 98 to 99% of beyond-range targets.
- In F2 and F3 worlds the best wrong form (a different drag exponent) hits 0%. The best form that does not contain the truth hits 4% overall.
- Median miss as a fraction of target distance:

  | | In-range | Beyond-range |
  | --- | --- | --- |
  | Exact law | 0.62% | 0.53% |
  | True-form fit | 0.62% | 0.57% |
  | Best wrong form | 0.78% | 5.9% |
  | Best non-nesting wrong form | 1.9% | 17.9% |

- Unreachable targets under the exact law: 0. The `plan_shot` fix removed the 3 false flags.
- The slowest single `fit_law` call was 2.95 s in Stage A and 4.58 s in Stage B, under the 5 s bar, so the fit loop was not sped up.

Stage B (radius 2%, random versus greedy, n = 20 each):

| Metric | Random | Greedy |
| --- | --- | --- |
| Headline: reached 80% of beyond-range probes within 12 experiments | 95% of worlds | 100% |
| Experiments to the headline (median) | 2 | 3 |
| Paired difference, random minus greedy | 0.3 experiments, 95% CI -0.75 to 1.6 | |
| Beyond-range probes within radius after 12 experiments (mean) | 87.8% | 99.5% |
| Law-form recovery (co-headline) | 16/20 (F0 3/5, F1 4/5, F2 5/5, F3 4/5) | 19/20 (F1 4/5) |
| Control false discovery (co-headline) | 2/5 | 0/5 |
| Mission hit probability (all / in-range / beyond) | 87.8 / 95.2 / 82.9% | 93.1 / 98.4 / 89.5% |
| Paired mission hit probability, greedy minus random | | +5.3 points, 95% CI 0.7 to 11.9 |
| Median miss fraction (in / beyond) | 0.70 / 0.73% | 0.64 / 0.64% |
| Median all-probe error after 12 experiments (secondary) | 6.7 mm | 3.3 mm |

- The headline criterion, as defined, still does not separate the policies: random reaches it in 95% of worlds, against the reference of at most 40%. Random gets ahead early because greedy spends its first 3 experiments on fixed seed experiments. Greedy pulls ahead from experiment 4, and the gap at the end of the budget is large (99.5% vs 87.8%). Reported as measured; nothing was tuned.
- The previous run (`calibration/results/dev20/`, 2% / 0.5 degree launcher, 5 cm radius) is kept for the before numbers. Its summary was made by the report at commit `36bc1be`.

**Commands**

```
uv sync
.venv/bin/python -m pytest -q          # 34 passed, 1 skipped (leak test until lab/ lands), about 5 s
.venv/bin/python -m calibration.study --seeds 1000-1019 --stages A --out calibration/results/dev20_v2 --jobs 12
.venv/bin/python -m calibration.study --seeds 1000-1019 --stages B --hit-frac 0.02 --out calibration/results/dev20_v2 --jobs 12
.venv/bin/python -m calibration.report calibration/results/dev20_v2
SOLZERO_ADMIN_TOKEN=... .venv/bin/python -m world.server --port 8000
```

**Blockers**

- None blocking the build. Two calibration results need a human decision:
  1. The wrong-form check (at most 30%) fails at 2% only because nesting forms are counted as wrong. Should the check exclude forms that contain the truth? Those hit 4%.
  2. Headroom on the new headline is again small (paired difference 0.3 experiments, CI includes 0). The separation shows in final accuracy, law recovery, false discovery and mission hits.

**Next step**

- Lane B: point `lab/` at the real server (`SOLZERO_WORLD_URL`), using `/admin/worlds` to get world ids. Then run one full loop on a dev world.
- Lane A: the MuJoCo scene (SPEC 9, hours 4 to 8), and the random and oracle condition runners against the server.

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

- Lane A to human: should the wrong-form check exclude forms that contain the true law? See the lane A Blockers.
- Lane A to lane B: the real server is in `world/server.py`, with the same contract as `mock/` plus `POST /laws` and `Target.hit_radius_m`. The mock needs `/laws` and the live-set check to stay faithful to 5.2.

## Interface changes (every change to `SPEC.md` section 5)

| When | Who | What changed | Why |
| --- | --- | --- | --- |
| 2026-10-03 | Claude Code | `FitResult.cov` (optional parameter covariance) added; `loo_error` defined as RMS in noise-sd units | `predict` needs correlated parameter draws; the units were undefined |
| 2026-10-03 | Claude Code | `noise_sd` in `SessionInfo` and in launch `Result` gains `speed_frac` and `elevation_deg` (launcher actuation error) | Actuation error dominates launch noise; the fit must weight launches by it |
| 2026-10-04 | Claude Code | `POST /laws {session_id, live_laws}` replaces the live set; `/predictions` must cover exactly that set (422 otherwise); `LawsRequest` in schemas; a prediction table is single-use and is invalidated if the live set changes | Human decision 3 |
| 2026-10-04 | Claude Code | `Target.hit_radius_m` (default max(5 cm, 2% of distance)); `noise_sd` actuation values now 0.005 / 0.1; `plan_shot` `reachable=false` only without a nominal solution | Human decision 1 |
| 2026-10-04 | Claude Code | Admin `GET /admin/worlds` (dev seed to opaque world id) | Eval runners need world ids; ids are hashes so agents cannot read the seed |
| 2026-10-04 | Claude Code (viewer) | SPEC 5.5: `candidates` payload documented, with optional `disagreements` (one Disagreement per candidate). New SPEC 5.6: viewer inputs (`runs/index.json`, per-run `ledger.jsonl` + `metrics.json` + optional `video.mp4`, `eval/aggregate.json`) | The replay site needs SessionInfo, admin score and truth beside the ledger, and a flat per-world eval format |
| 2026-10-03 | Claude Code | SPEC 5.4: analysis tools take keyword-only extras (`samples`, `noise_sd`, `seed`, `n_draws`); added `predict_many` and `disagreement_many` | Explicit seeds; batched candidate scoring |

## Design changes from calibration (dev worlds only)

| When | Change | Before | After | Reason |
| --- | --- | --- | --- | --- |
| 2026-10-04 | Launcher actuation error 2% / 0.5 deg to 0.5% / 0.1 deg (experiments and mission) | Exact-law hit 49% (old 5 cm radius) | Exact-law hit 95.2% (2% radius) | Human decision 1: the ceiling was limited by actuation noise |
| 2026-10-04 | Hit radius 5 cm to max(5 cm, 2% of target distance). 3% was tried first. | True-form fit hit 47% | 94.2% (3%: 96.7%) | Decision 1. At 3% the best wrong form hit 50% of beyond-range targets (above 30%), so 2% was used; at 2% it hits 49%, caused by nesting forms |
| 2026-10-04 | Probes 20 mixed to 20 in-range plus 20 beyond-range; headline = experiments to 80% of beyond-range probes within the radius | Old 5 cm median metric: random 95%, greedy 100% reach | New headline: random 95%, greedy 100%; paired difference 0.3 (CI -0.75 to 1.6) | Human decision 2. Budget, noise and ranges unchanged |

## Decisions by the human

- None yet.
