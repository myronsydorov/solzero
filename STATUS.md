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

## Viewer: replay site (Claude Code, branch `viewer`)

**Current milestone (2026-10-04):** V1 to V3 done. The site runs on fixture data and is deployed at **https://viewer-delta-ten.vercel.app** (results page: `/results.html`). Every page is labelled "Replay of recorded runs".

**Done**

- V1, session replay (`viewer/public/index.html`). It shows:
  - a cycle timeline (play, step, arrow keys, deep links `?run=&step=`);
  - law cards with KaTeX equations rendered from the law strings, fitted parameters, and live, rejected or retired status with the last verdict;
  - the disagreement table, with the chosen candidate highlighted;
  - pre-registered predictions against each result, with z-scores;
  - the decision diff, with a "plan changed by evidence" badge and timeline marker;
  - a budget bar;
  - the mission panel: a side view with hit zones, shot zero, and fired shots with hit or miss;
  - the hidden law beside the discovered law, plus a chart of the nominated law's hidden probe score per experiment;
  - the video, when the run directory has one.
- V2, aggregate results (`viewer/public/results.html`) from `eval/aggregate.json`:
  - learning curves: beyond-range probe hit rate, and median probe error on a log scale;
  - the SPEC section 7 primary-metric table for lab, random and single agent (rows read "not run" when there are no rows), plus any reference conditions present;
  - secondary metrics, including experiments to threshold;
  - paired per-world differences with a seeded bootstrap 95% interval (lab − random, lab − single, references − random);
  - control-world false discovery, and law recovery by family.
- V3, fixtures and deployment:
  - `viewer.fixtures.oracle_session` runs the scripted greedy-disagreement oracle (12-form library, no language model) against the real `world.server.WorldServer` in-process, on dev seeds 1000 to 1003. Pre-registration and budget are enforced by the server code. It writes SPEC 5.5 ledgers and 5.7 `metrics.json`.
  - `viewer.fixtures.calibration_aggregate` turns calibration stage B (`dev20_v2`) into `aggregate.json`.
  - Real runs load by dropping a directory into `viewer/public/runs/` and running `node viewer/scripts/build-index.mjs` (Vercel runs it on every build). See `viewer/README.md`.
- `tests/test_viewer_fixtures.py` validates every fixture ledger entry against `schemas`, checks that prediction tables cover exactly the live set, and checks the `metrics.json` and `aggregate.json` shapes.
- The interface is SPEC 5.5 (`candidates` payload) and 5.7 (viewer inputs). Both were committed separately, before the code.

**Results** (fixture data; dev seeds only)

| Seed | Family | Final form | Hits | Plan changed by evidence |
| --- | --- | --- | --- | --- |
| 1000 | F0 | const_p2 | 5/5 | 6 of 12 cycles |
| 1001 | F1 (p = 3) | const_p3 | 5/5 | 3 |
| 1002 | F2 | mass_p2 | 5/5 | 5 |
| 1003 | F3 | height_p2 | 4/5 | 5 |

- Runtime is 14 to 28 s per world (29 s wall with 4 jobs).
- The aggregate page reproduces the calibration report:

  | | Random | Greedy |
  | --- | --- | --- |
  | Final beyond-range probe hit rate | 87.8% | 99.5% |
  | Law recovery | 16/20 | 19/20 |
  | Control false discovery | 2/5 | 0/5 |
  | Median experiments to threshold | 2 | 3 |

  The page also shows a paired difference of oracle − random on the beyond-range probe hit rate: 11.8 points, 95% CI 3.75 to 22.2.

**Commands**

```
.venv/bin/python -m viewer.fixtures.oracle_session --seeds 1000,1001,1002,1003 --out viewer/public/runs --jobs 4
.venv/bin/python -m viewer.fixtures.calibration_aggregate calibration/results/dev20_v2 --out viewer/public/eval/aggregate.json
node viewer/scripts/build-index.mjs
cd viewer/public && python3 -m http.server 8000          # local preview
cd viewer && npx vercel deploy --temporary --prod --yes  # redeploy (no login)
.venv/bin/python -m pytest -q                            # 49 passed, 1 skipped
```

**Blockers**

- Deployment ownership. The Vercel CLI is not logged in, so the site was deployed with `vercel deploy --temporary`, under a temporary team (`brisa6`).
  - The production URL above is live now.
  - The CLI does not say how long a temporary deployment lasts.
  - For a durable URL, the human runs `! npx vercel login`, then `cd viewer && npx vercel deploy --prod`, and puts the new URL here.

**Decisions made autonomously**

- **Stack:** a static site with no build step (plain HTML, CSS and ES modules). KaTeX renders the equations and math.js parses the law strings, both from cdnjs. The only build step is the Node run-index script, because a static host cannot list directories.
- **SPEC 5.7 input format:**
  - `metrics.json` = SessionInfo + `GET /admin/score` + `GET /admin/truth`, unchanged. The ledger alone lacks targets, shot zero and the truth.
  - `aggregate.json` is a flat list of per-world rows, and the viewer computes the metrics from them. That keeps `eval/` output simple.
- **Disagreement table:** taken from an optional `disagreements` field in the `candidates` payload. When it is absent (lane B ledgers today), gaps for the chosen spec are derived from the prediction table, using the same formula as `tools.disagreement`, and the page says so.
- **Fixture condition:** the fixture uses the `lab` server condition, because `world/server.py` does not yet accept `oracle`. `metrics.json` labels it `condition: "oracle"`.
- **Fixture verdicts:**
  - The z-score is the signed z of the worst observable.
  - A law is rejected above |z| 3, supported at |z| 2 or below, and insufficient evidence in between.
  - A failed run gives "insufficient evidence", z = 0. The first version wrongly rejected laws on failed runs with a placeholder z.
- **Tentative follow-up in the fixture:** the next cycle's candidate pool is drawn before the result and ranked with the current fits. After the result it is re-ranked with the new fits. "Changed" means the argmax moved.
- **Aggregate fixture, two caveats** (stated in its notes):
  - Mission hits count one realised shot per target, not the calibration report's hit probabilities.
  - Law recovery uses calibration's definition (the selected form equals the true form), not `eval/grade.py`.
- **Results page after the merge with main:** reorganised around the section 7 primary metrics; experiments to threshold moved to secondary. Two numbering changes:
  - main added its own SPEC 5.6 (agent run CLI), so viewer inputs became 5.7;
  - aggregate rows gained `mission_hits_in_range` and the median-miss fields.
- **Law-card status:** a law is "live" while it is in the current law set, even if its last verdict was "rejected". The oracle keeps the best three by BIC, so this happens, and both badges are shown. It is "rejected" if it left the set after a rejection, and "retired" otherwise.
- **Hidden law:** revealed only at the last step, or with a "Reveal now" button, matching the demo order.

**Next step**

- Load the first real lab, random and single-agent runs as soon as `eval/` produces `metrics.json` and `aggregate.json` (requests below).
- Add the demo video when the sim lane renders it.

## Requests (one lane asking the other, or the human, for something)

- Lane A to human: should the wrong-form check exclude forms that contain the true law? See the lane A Blockers.
- Lane A to lane B: the real server is in `world/server.py`, with the same contract as `mock/` plus `POST /laws` and `Target.hit_radius_m`. The mock needs `/laws` and the live-set check to stay faithful to 5.2.
- Viewer to lane B: please add `disagreements` (one `Disagreement` per candidate, from the `disagreement` tool) to the `candidates` ledger payload (SPEC 5.5). The replay shows it as the disagreement table; without it the viewer can only derive gaps for the chosen spec.
- Viewer to physics/eval: after each session, write `metrics.json` (SPEC 5.7: SessionInfo + admin score + admin truth) beside the ledger, and write `aggregate.json` rows in the 5.7 format, so runs can be dropped into `viewer/public/runs/` unchanged.
- Viewer to physics, a design observation from the fixtures (dev seed 1003, F3, kappa = -0.481):
  - Gravity g0 * (1 + kappa * z) reaches zero at z = 1/|kappa| = 2.08 m.
  - Steep launches of heavy samples at about 3.2 to 3.6 m/s and 62 to 69 degrees climb past that height and never land. `World.shot_x` returns NaN, and the run is reported `failed`.
  - With kappa down to -0.6 the zero-gravity height is 1.67 m, inside the reach of a 4 m/s launch.
  - The greedy oracle picked 4 such launches in 12, because a law that predicts no landing scores a 1000-sigma gap.
  - This is a design problem (the family range), not an integrator bug. Diagnosed by an energy check: the vertical speed needed to reach 2.08 m is about 2.93 m/s, against about 3.0 m/s launched.


## Interface changes (every change to `SPEC.md` section 5)

| When | Who | What changed | Why |
| --- | --- | --- | --- |
| 2026-10-03 | Claude Code | `FitResult.cov` (optional parameter covariance) added; `loo_error` defined as RMS in noise-sd units | `predict` needs correlated parameter draws; the units were undefined |
| 2026-10-03 | Claude Code | `noise_sd` in `SessionInfo` and in launch `Result` gains `speed_frac` and `elevation_deg` (launcher actuation error) | Actuation error dominates launch noise; the fit must weight launches by it |
| 2026-10-04 | Claude Code | Law expressions limited to a whitelisted arithmetic grammar (`schemas.check_expr`), checked before sympy parses them | Lane B bug report: untrusted strings reached `sympy.parse_expr` (eval) over HTTP |
| 2026-10-04 | Claude Code | `Condition` adds `textbook` and `oracle` (scripted references); `/commit` requires exactly one shot per target; prediction tables are pinned to full law content | Human instruction E1; lane B bug reports |
| 2026-10-04 | Claude Code | SPEC 5.6: agent run CLI contract (`lab.run --session-info --condition --specs --max-tokens --max-wall-s --approval`, `summary.json`). Lane B to confirm or amend | E1 needs a fixed handoff between `eval/` and `lab/` |
| 2026-10-04 | Claude Code | SPEC 7: four primary metrics fixed; experiments to threshold is secondary; law-grading rule; 40 test seeds (10 per family). SPEC 8: wrong-form check excludes forms that contain the true law | Human decisions, 2026-10-04 |
| 2026-10-04 | Claude Code | `POST /laws {session_id, live_laws}` replaces the live set; `/predictions` must cover exactly that set (422 otherwise); `LawsRequest` in schemas; a prediction table is single-use and is invalidated if the live set changes | Human decision 3 |
| 2026-10-04 | Claude Code | `Target.hit_radius_m` (default max(5 cm, 2% of distance)); `noise_sd` actuation values now 0.005 / 0.1; `plan_shot` `reachable=false` only without a nominal solution | Human decision 1 |
| 2026-10-04 | Claude Code | Admin `GET /admin/worlds` (dev seed to opaque world id) | Eval runners need world ids; ids are hashes so agents cannot read the seed |
| 2026-10-04 | Claude Code (viewer) | SPEC 5.5: `candidates` payload documented, with optional `disagreements` (one Disagreement per candidate). New SPEC 5.7 (numbered 5.6 before the merge with main): viewer inputs (`runs/index.json`, per-run `ledger.jsonl` + `metrics.json` + optional `video.mp4`, `eval/aggregate.json`) | The replay site needs SessionInfo, admin score and truth beside the ledger, and a flat per-world eval format |
| 2026-10-04 | Claude Code (viewer) | SPEC 5.7 aggregate rows gain `mission_hits_in_range` and optional `median_miss_frac_in_range` / `median_miss_frac_beyond`; the viewer reports the section 7 primary metrics | Section 7 primary metrics (merged from main) split mission hits into in-range and beyond-range with median miss fraction |
| 2026-10-03 | Claude Code | SPEC 5.4: analysis tools take keyword-only extras (`samples`, `noise_sd`, `seed`, `n_draws`); added `predict_many` and `disagreement_many` | Explicit seeds; batched candidate scoring |

## Design changes from calibration (dev worlds only)

| When | Change | Before | After | Reason |
| --- | --- | --- | --- | --- |
| 2026-10-04 | Launcher actuation error 2% / 0.5 deg to 0.5% / 0.1 deg (experiments and mission) | Exact-law hit 49% (old 5 cm radius) | Exact-law hit 95.2% (2% radius) | Human decision 1: the ceiling was limited by actuation noise |
| 2026-10-04 | Hit radius 5 cm to max(5 cm, 2% of target distance). 3% was tried first. | True-form fit hit 47% | 94.2% (3%: 96.7%) | Decision 1. At 3% the best wrong form hit 50% of beyond-range targets (above 30%), so 2% was used; at 2% it hits 49%, caused by nesting forms |
| 2026-10-04 | Probes 20 mixed to 20 in-range plus 20 beyond-range; headline = experiments to 80% of beyond-range probes within the radius | Old 5 cm median metric: random 95%, greedy 100% reach | New headline: random 95%, greedy 100%; paired difference 0.3 (CI -0.75 to 1.6) | Human decision 2. Budget, noise and ranges unchanged |

## Decisions by the human

- None yet.
