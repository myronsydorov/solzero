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

**Current milestone (2026-10-03 22:47 UTC):** main integrated; shared-schema mock, wrappers, ledger and five-role host runner verified. Full model-driven sessions reached the human approval gate on both servers: mock after six experiments, real dev seed 1000 after five. No model-run mission fired. Final suite: 85 passed.

**Done**

- Installed open-source Omnigent 0.16.0 on Python 3.12.10; read upstream `docs/AGENT_YAML_SPEC.md` at release v0.16.0 (`82a7447`). Bundled hello-world returned `SOLZERO_CONNECTION_OK` via the existing Claude subscription login (`claude-sdk`, `sonnet`). Source-main's web build needed a newer Node; the release wheel resolved that missing dependency without a Node upgrade.
- Verified orchestrator plus two declared agents passing ExperimentSpec and Result through Python functions. Working handoff: `type: agent` via `sys_session_send`, JSON in child messages/tool arguments/results. Audit: spec 1, result 1, received 1, policy denial 1, blocked body executions 0. Raw evidence: `runs/omnigent-smoke/{bundled-example.log,handoff.log,handoff.jsonl,summary.json}`.
- Ran `git merge main` twice, first `36bc1be` (merge `e126556`), then updated `c64d2fa`. Conflict resolutions: `.gitignore` union, lane A from main and lane B from this branch, both Requests/Interface rows, lane B leak test. Removed `lab/requirements.txt`; `uv sync` now uses main's root dependencies. No local changes to root pyproject, lockfile or schemas relative to main.
- Mock implements the six public routes with Earth ballistics, explicit dev seed, atomic budget, failed-run charging, pre-registration and one-use/spec/ownership checks, exact live-law coverage and replacement invalidation, including same-ID expression changes. It mirrors public target radii and launcher noise defaults. Admin scoring uses 40 probes and shared numerical tools; this fixture supports engineering checks, not scientific claims.
- Every domain record imports from `schemas/`; no copied models. The Theorist wrapper sends `/laws` on live-set updates. Covariance and noise-unit LOO values pass through shared analysis unchanged. Same pure `fit_law`, `predict`, `disagreement`, `plan_shot` tools remain available to all conditions.
- Typed HTTP adapters, fail-closed ambiguous mutation handling, role/budget/envelope policies, and fsynced JSONL ledger implemented. Repaired bugs found during integration: nonfinite ledger values becoming null, direct SDK message shape, and nomination descriptions unnecessarily invalidating an otherwise identical registered law.
- Five YAML role prompts remain stubs. `lab.run` supplies generic role/record instructions and sequences Omnigent SDK executors with Python tools in one process; it supplies no scientific laws, bounds or experiment sequence. Only the Operator measures. Empty temporary cwd, no OS tools, no skills, public HTTP metadata/observations and own-session ledger isolate model context. All six YAML files load through Omnigent.
- Safe five-shot requests pause with `pending-commit.json`; `lab.approve` accepts the hash of a human-reviewed file and blocks replays after both successful and ambiguous submission. No model can call this host command. No mission has fired in the model-driven runs.
- No test seeds generated, run or inspected. No lane B calibration tuning. The leak test uses SPEC section 2 terms and checks forbidden imports/admin routes; it passes. An additional check caught a generated schema display title derived from `claims_non_ordinary` matching a forbidden family term. Generated display titles are now omitted without changing fields; all 13 tool schemas pass the new regression test. The longer real run began before this display-title fix; raw evidence is retained and it is not a frozen evaluation. Its early `disagreement` calls exposed an underspecified pair shape; generated input schemas now explicitly compose shared `Law` and `FitResult`, with regression coverage. The running session was not restarted.

**Commands and measured results**

```sh
uv sync
.venv/bin/python -m pytest -q --junitxml=runs/merge-integration/final-tests.xml
.venv/bin/python -m mock.check --seed 1000 --output runs/mock-check-final
.venv/bin/python -m mock.server --seed 1000 --port 8002
SOLZERO_RUNS_DIR=runs/real-server-dev .venv/bin/python -m world.server --port 8001
SOLZERO_WORLD_URL=http://127.0.0.1:8002 .venv/bin/python -m lab.run --world-id mock-dev --seed 1000 --cycles 12 --output runs/session-mock-1000
SOLZERO_WORLD_URL=http://127.0.0.1:8001 .venv/bin/python -m lab.run --world-id w_3c4adce51f85 --seed 1000 --cycles 1 --output runs/full-loop-real-1000
SOLZERO_WORLD_URL=http://127.0.0.1:8001 .venv/bin/python -m lab.run --world-id w_3c4adce51f85 --seed 1000 --cycles 12 --output runs/session-real-1000
```

- `uv sync`: 111 resolved, 105 installed packages checked. Full root suite at merge: **84 passed**, one upstream Starlette/httpx deprecation warning, 4.50 s; XML saved above. Final `.venv/bin/python -m pytest -q` after the generated-schema regression test: **85 passed**, 4.19 s (including explicit law/fit-pair tool schemas).
- Fresh HTTP mock fixture: **12 experiments, budget 0, missing pre-registration 422, thirteenth experiment 409**. Raw responses, ledger and admin-only engineering score saved in `runs/mock-check-final/`.
- Model mock session `s_mock_0001`: **6 complete evidence/revision cycles, 6 budget left, 62 calls**, PI chose `predictive_only`, no non-ordinary claim, five shots pending approval. Raw tools, policies, public transcript, ledger and summary in `runs/session-mock-1000/`. This process started before the final structured-tool-schema/description-cache fixes; its unmodified trace is retained.
- Real dev seed 1000, opaque world `w_3c4adce51f85`, session `s_ad8db5fe92`: **1 complete evidence/revision cycle, 11 budget left, 11 calls**, Analyst nominated a fit and recorded insufficient evidence; PI continued. Raw outputs in `runs/full-loop-real-1000/`. A one-cycle check is not a completed mission. Longer real session `s_40ebae9470` completed **5 evidence/revision cycles, 7 budget left, 65 calls**, then PI chose `predictive_only` with no non-ordinary claim. All 5 experiments have verdicts and nominations; 4 decision diffs changed the tentative follow-up (mock: 0 of 6). Raw evidence: `runs/session-real-1000/`, combined audit `runs/merge-integration/model-run-audit.json`. Five shots are pending approval, not fired.
- Initial SDK message bug failed before any experiment in `runs/full-loop-mock-1000/`; corrected one-cycle mock check in `runs/full-loop-mock-1000-v2/` completed 1 experiment/11 calls. Pre-integration missing-schema collection errors and raw evidence are retained, superseded by the passing full suite.

**Blockers and limits**

- Setup/schema and live-law contract blockers are resolved by main. Remaining mission pause is SPEC section 6's human approval gate for the exact five-shot table; model-run shots have not fired.
- Real-server direct-client validation gaps are requested below; current lab wrappers independently enforce them. No changes made to lane A files.
- MLflow tracing and comparison-condition runners remain later milestones. Current raw tool/policy JSONL and ledger are saved. The model service exposes no sampling seed through this harness; explicit numerical seeds and raw decisions support audit, not bitwise model regeneration.
- Original two-hour box: 20:56–22:56 UTC, including dependency wait. Stop and record partial evidence at the deadline.

**Next step**

1. Both model sessions reached approval within the time box (22:47 UTC); integration commits `81ecf9a`, `e1f2212`, `e17c3a0` are on `omnigent`, not pushed to main.
2. Obtain the human's approval of each exact pending firing table before the host submission command; keep the corresponding in-memory server alive meanwhile (real port 8001/PID 85971, mock port 8002/PID 85972). Save the receipt and commit ledger entry. Pending mock hash: `b205390c555482254efcf7ced88abe1babbf08f0baa6a4fe71bfb7e02183334f`; pending real hash: `a01863dbb97671e9288d9dfcb91d6e2b4726c3152ef70510390e3052bf8c52b2`. Exact files are `runs/session-mock-1000/pending-commit.json` and `runs/session-real-1000/pending-commit.json`.
3. Human integrates this tested branch to main. Lane A addresses its server requests; tracing/comparison runners follow in the next milestone.

## Requests (one lane asking the other, or the human, for something)

- Lane B resolution (2026-10-03 22:43 UTC): all earlier schema/dependency/pytest-path and `/laws` integration requests below are resolved by main `c64d2fa`; historical rows are retained as requested. `lab/requirements.txt` is deleted.
- Lane B → lane A (bugs, direct HTTP clients): `world/server.py` stores only law IDs in prediction tables, so replacing an expression under the same ID does not invalidate a table; track a law revision/snapshot. `/commit` also accepts fewer than five target shots; require exactly one per target. Mock and lab already enforce both.

- Lane A to human: should the wrong-form check exclude forms that contain the true law? See the lane A Blockers.
- Lane A to lane B: the real server is in `world/server.py`, with the same contract as `mock/` plus `POST /laws` and `Target.hit_radius_m`. The mock needs `/laws` and the live-set check to stay faithful to 5.2.
- Lane B integration (2026-10-03 22:28 UTC): initial main merge resolved as requested; temporary requirements removed; 72 combined tests pass, 12-call HTTP fixture passes, and one live specialist evidence/revision cycle completed with one experiment, 11 remaining, and 11 tool calls. Updated main has now landed; merging its dependencies, target schema and real server next.
- Lane A to human: decide calibration blockers 1 (actuation noise versus mission ceiling) and 2 (headroom metric). See lane A Blockers.
- Lane B → human: merge `schemas/` to main first (initial commit `76ff1d6`; compatible follow-up `5d0547d`) so the mock/wrappers can import and test the shared models. This is the active integration blocker.
- Lane B → lane A: integrate `lab/requirements.txt` dependencies into your root pyproject: `omnigent==0.16.0`, `fastapi>=0.100,<1`, `uvicorn>=0.30,<1`, `httpx>=0.27,<1`, `pydantic>=2,<3`, and test dependency `pytest>=8,<10`. Also include `mock/tests` in the root pytest testpaths. Human authorized `.gitignore` and `tests/test_no_leak.py`; root pyproject remains lane A owned.
- Lane B → lane A/human (design gap): section 6 requires server enforcement of predictions for every live law, but section 5.2 has no live-law registration field/endpoint. Lane B wrappers enforce full current-set coverage; server can enforce table/spec/cycle/empty-list checks but cannot independently know omitted newly proposed or retired laws. Please resolve in a jointly agreed interface revision; no contract change made here.
- Lane B → lane A: mock admin scoring uses shared `tools.predict` when installed and explicitly marks a score unavailable otherwise. Mock is for API development, not scientific evaluation.
- Lane B → lane A (bug): `schemas.records.law_expr_symbols` currently passes untrusted expression strings directly to `sympy.parse_expr`. Please validate a restricted arithmetic AST before parsing; lab tool adapters now do so before model validation, but HTTP schema parsing needs the same boundary protection in your lane.

## Interface changes (every change to `SPEC.md` section 5)

| When | Who | What changed | Why |
| --- | --- | --- | --- |
| 2026-10-03 | Claude Code | `FitResult.cov` (optional parameter covariance) added; `loo_error` defined as RMS in noise-sd units | `predict` needs correlated parameter draws; the units were undefined |
| 2026-10-03 | Claude Code | `noise_sd` in `SessionInfo` and in launch `Result` gains `speed_frac` and `elevation_deg` (launcher actuation error) | Actuation error dominates launch noise; the fit must weight launches by it |
| 2026-10-04 | Claude Code | `POST /laws {session_id, live_laws}` replaces the live set; `/predictions` must cover exactly that set (422 otherwise); `LawsRequest` in schemas; a prediction table is single-use and is invalidated if the live set changes | Human decision 3 |
| 2026-10-04 | Claude Code | `Target.hit_radius_m` (default max(5 cm, 2% of distance)); `noise_sd` actuation values now 0.005 / 0.1; `plan_shot` `reachable=false` only without a nominal solution | Human decision 1 |
| 2026-10-04 | Claude Code | Admin `GET /admin/worlds` (dev seed to opaque world id) | Eval runners need world ids; ids are hashes so agents cannot read the seed |
| 2026-10-03 | Claude Code | SPEC 5.4: analysis tools take keyword-only extras (`samples`, `noise_sd`, `seed`, `n_draws`); added `predict_many` and `disagreement_many` | Explicit seeds; batched candidate scoring |
| 2026-10-03 | Codex | Mock and Theorist wrapper implement POST /laws with exact prediction coverage and pending-table invalidation; launcher noise metadata forwarded | Human-requested interface closure; no private model copies |

## Design changes from calibration (dev worlds only)

| When | Change | Before | After | Reason |
| --- | --- | --- | --- | --- |
| 2026-10-04 | Launcher actuation error 2% / 0.5 deg to 0.5% / 0.1 deg (experiments and mission) | Exact-law hit 49% (old 5 cm radius) | Exact-law hit 95.2% (2% radius) | Human decision 1: the ceiling was limited by actuation noise |
| 2026-10-04 | Hit radius 5 cm to max(5 cm, 2% of target distance). 3% was tried first. | True-form fit hit 47% | 94.2% (3%: 96.7%) | Decision 1. At 3% the best wrong form hit 50% of beyond-range targets (above 30%), so 2% was used; at 2% it hits 49%, caused by nesting forms |
| 2026-10-04 | Probes 20 mixed to 20 in-range plus 20 beyond-range; headline = experiments to 80% of beyond-range probes within the radius | Old 5 cm median metric: random 95%, greedy 100% reach | New headline: random 95%, greedy 100%; paired difference 0.3 (CI -0.75 to 1.6) | Human decision 2. Budget, noise and ranges unchanged |

## Decisions by the human

- None yet.
