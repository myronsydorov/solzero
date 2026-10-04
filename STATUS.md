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

**Current milestone (2026-10-04 02:00 Europe/Berlin):** both approved missions committed. L1–L5 implementation advanced; **94 tests pass**. Live acceptance and the paired comparison are **blocked by the Claude subscription session limit**, which reports a reset at 05:30 Europe/Berlin. The candidate tag is a code-review checkpoint with incomplete scientific validation, not a test-seed freeze.

### Approved missions and why they stopped

Both exact approved tables were submitted once with `python -m lab.approve <pending-file> --approved-sha256 <reviewed-hash>`. Receipts are `runs/session-{mock,real}-1000/pending-commit.attempt.json`; ledgers now contain `commit`. **The previous real five-cycle run and mock six-cycle run stopped because the PI chose to commit, not because they were cut short.** Both chose `predictive_only` and `claims_non_ordinary=false`.

| Target | Mock outcome | Mock miss, mm | Real dev outcome | Real miss, mm |
| --- | --- | ---: | --- | ---: |
| t1 | hit | 8.7 | hit | 2.3 |
| t2 | hit | 14.0 | hit | 1.2 |
| t3 | hit | 42.5 | hit | 17.5 |
| t4 | hit | 47.2 | hit | 20.4 |
| t5 | miss | 111.8 | hit | 5.6 |

Evidence caveat: the old servers were started without an admin token. Their original HTTP score endpoints return 401/403. The real numbers come from the persisted payload written by the same admin score routine (`runs/session-real-1000/admin-score-persisted.json`). The mock numbers come from a fresh deterministic replay through the admin endpoint, after **all six original observations and budgets matched exactly** (`runs/session-mock-1000/admin-score-replay.json`); these are explicitly labelled replay results. Combined report: `runs/l1-l5/approved-mission-report.json`. The new server on port 8003 has host-only admin access configured.

### L1–L5 implementation and verification

- **L1 implemented, live 12-experiment acceptance incomplete.** Five real prompts now state decision ownership, inputs, output schemas and that insufficient evidence is valid. Theorist registers/revises/retires via `/laws`; Experimentalist compares candidates and registers complete predictions/tentative follow-up; Operator executes once; Analyst records coverage, prior-prediction z-scores/confounds and nominates; PI plans and commits. All eight ledger kinds are supported. Shared models and tools only; source and generated-tool-schema leak checks pass.
- **Approval:** explicit `--auto-approve` is used by every dev/batch command under the user's standing authorization. The default interactive/demo command still pauses for human approval. Automatic approval does not bypass envelope, five-target or budget checks. `--min-experiments 12` is a disclosed fixed-budget verification setting; the normal default 0 leaves early stopping to the PI. No physics/noise/ranges/thresholds were tuned.
- **L2 implemented, model verification interrupted.** `--condition lab|random|single`; random uses host schedules generated by lane A's existing sampler and the same Theorist/Analyst/PI. One single-condition agent gets every scientific tool. All conditions share model, experiment ceiling and token ceiling. CLI contract is published under Requests and in `docs/agents.md`. Direct safe-module sampler export remains a lane A request; lab never imports calibration.
- **L3 partial.** Host selected seeds 1000–1007 (two per family) via admin endpoints; no family/seed metadata is sent in model context. Added Analyst `coverage` with per-sample actual specs/status and budget. Eight lab sessions started; each completed one measurement before the model limit. Tool audit found **zero wrapper/tool exceptions** in those sessions; longer-run law retirement, confounds and stop quality cannot yet be assessed. Initial model-driven runs before prompt expansion stopped at mock 6 / real 5; after expansion and fixed-budget verification, the new real acceptance run was interrupted at 2, not a PI stop. No before/after scientific performance claim is made.
- **L4 implemented with explicit limits.** Nested MLflow agent/tool spans persist inputs/outputs; tests read them back. SDK token usage and wall time are saved. Three connection-establishment attempts maximum; no retry after a read timeout/unknown mutation. Durable phase checkpoints, successful-call memo, runner lock and ledger recovery prevent acknowledged experiments being replayed. A crash after ledger fsync and before checkpoint save recovers from the durable result. Unknown outcomes without that result still need host reconciliation; server-restart recovery needs lane A snapshot/idempotency support.
- **Parallel verification:** 16 isolated subprocesses completed **192 HTTP experiments**, each with its own session, budget 0 and thirteenth request 409; engineering fixture elapsed **0.687 s**. Saved `runs/l1-l5/parallel16-engineering/summary.json`. This is a real process/HTTP test, **not** sixteen successful model sessions.
- **Second Analyst model:** the one-line `--analyst-model haiku` option was exercised in a random-condition diagnostic, but the shared subscription limit interrupted it before Analyst ran. Compatibility is unproven; no success claim.
- **L5 candidate only.** `docs/agents.md` documents prompts, policies, CLI, usage, tracing and resume limits. `lab-freeze-candidate` marks tested code with unresolved live-validation gates. The same-world 24-cell report contains **0 completed paired sessions**, 8 interrupted lab sessions and 16 unrun random/single cells; it does not treat unrun cells as zero hits. `scientific_freeze_ready=false` in `runs/l1-l5/paired-dev8/all-conditions-report.json` and `.md`. No test seeds generated, inspected or run.

### Measurements and exact commands

- Final `.venv/bin/python -m pytest -q`: **94 passed**, 6.37 s, three upstream deprecation warnings. Includes generated-schema vocabulary, role/envelope/auto-approval, retries, duplicate measurement suppression, checkpoint recovery, coverage and MLflow persistence.
- `uv pip install mlflow-skinny`: installed 3.16.1 for the explicitly requested tracing work. Root pyproject/lock remain untouched by lane B; integration request below. A fresh `uv sync` will remove this extra until lane A lands it.
- Initial tracing bug: `start_span(inputs=...)` is not supported by installed MLflow; fixed to `span.set_inputs`. It failed before **any experiments**. Resumed the original sessions after the fix rather than creating replacement measurements. Trace test also now flushes the asynchronous exporter before querying.
- Full-budget acceptance `runs/l1-l5/l1-budget12`: **2 experiments**, 143.8 s, **312,274 reported tokens including cache**, provider-interrupted. Not twelve-experiment completion.
- Single diagnostic: **1 experiment**, 13.0 s, **45,006 reported tokens**, provider-interrupted before completing the cycle. Random/Haiku diagnostic: **0 experiments**, 11.3 s, **17,494 reported tokens**, provider-interrupted. Raw logs and summaries retained. Usage on failed turns may be partial.

Eight-world lab attempt (all provider-interrupted at one experiment; tokens include cache and may omit unfinished-turn usage):

| Dev seed | Wall seconds | Reported tokens |
| --- | ---: | ---: |
| 1000 | 53.5 | 117883 |
| 1001 | 57.5 | 119782 |
| 1002 | 49.3 | 107177 |
| 1003 | 55.0 | 113546 |
| 1004 | 50.8 | 97581 |
| 1005 | 45.5 | 75631 |
| 1006 | 52.4 | 106870 |
| 1007 | 53.4 | 97772 |

```sh
# Current server: dev only; token read by host, never by scientific tools.
SOLZERO_ADMIN_TOKEN="$(cat runs/l1-l5/admin-token)" SOLZERO_RUNS_DIR=runs/l1-l5/server .venv/bin/python -m world.server --port 8003
# Resume the original real acceptance session after model capacity is restored.
SOLZERO_WORLD_URL=http://127.0.0.1:8003 .venv/bin/python -m lab.run --world w_3c4adce51f85 --condition lab --seed 1000 --min-experiments 12 --auto-approve --resume --output runs/l1-l5/l1-budget12
# Resume the same eight lab sessions; do not restart the in-memory server.
.venv/bin/python -m eval.dev_batch --base-url http://127.0.0.1:8003 --token-file runs/l1-l5/admin-token --output runs/l1-l5/paired-dev8 --conditions lab --jobs 8 --resume
# Then the same eight worlds under the other two conditions, up to 16 processes.
.venv/bin/python -m eval.dev_batch --base-url http://127.0.0.1:8003 --token-file runs/l1-l5/admin-token --output runs/l1-l5/paired-dev8 --conditions random single --jobs 16
.venv/bin/python -m eval.report_dev runs/l1-l5/paired-dev8
.venv/bin/python -m eval.verify_parallel --output runs/parallel-check-new
.venv/bin/python -m pytest -q
```

### Blockers and next step

1. **Missing model capacity:** Claude reports reset at 05:30 Europe/Berlin. No Anthropic/OpenAI API key or Databricks endpoint is configured. Checked the authenticated Codex alternative: GPT-5.5 completed a transport probe, but native filesystem and connected-app tools exceeded the lab allowlist, so it was **not used for scientific runs**. Probe logs retained. A user preference question about alternate capacity is pending; no automatic future run was scheduled.
2. **Missing shared dependency/API support:** lane A must integrate `mlflow-skinny`; safe sampler export and public idempotency/snapshot support remain requests. Current resume supports the same live server and acknowledged outcomes, not arbitrary server restart or lost-response reconciliation.
3. After capacity returns, run the exact resume commands, assess all eight completed lab ledgers, fix only demonstrated orchestration failures, then run the same worlds under random/single and update the candidate. The current incomplete matrix supports no comparative result and does not establish the under-ten-minute target.

**Earlier verified milestones:** Omnigent 0.16.0/Python 3.12.10; upstream YAML spec read at tag `82a7447`; bundled hello succeeded. Orchestrator plus two agents used declared `type: agent` / `sys_session_send` handoffs with JSON ExperimentSpec/Result; policy denied one call with zero body executions. Main integration commits `e126556`, `81ecf9a`; source/schema follow-ups `e1f2212`, `e17c3a0`; prior status `f4d72f0`. Initial 12-call HTTP fixture passed budget/pre-registration checks. All raw evidence remains under `runs/omnigent-smoke/`, `runs/mock-check-final/` and `runs/merge-integration/`.

## Requests (one lane asking the other, or the human, for something)

- **Lane B CLI contract for eval:** `SOLZERO_WORLD_URL=<origin> python -m lab.run --world <id> --condition lab|random|single --seed <dev-seed> --output <fresh-dir> --auto-approve`. Random additionally needs `--schedule <public-json>` from host shared sampler. `--min-experiments 12` selects fixed-budget verification; default 0 preserves PI early stop. Exit 0 committed, 2 CLI, 3 approval, 4 failure, 5 cycle cap. Ledger `<output>/<session_id>/ledger.jsonl`; summary contains wall seconds, SDK token use including caches, budget, stop reason and ledger path. Same model, 2M token ceiling checked between turns, 4096 output-token limit and 12-experiment cap across conditions. `--resume` requires same live service and output; ambiguous requests fail closed. `docs/agents.md` is the full contract.

- Lane B claims `eval/` for host-only dev manifests, batch launch and score reporting (L2–L5, user request). `docs/agents.md` authorized explicitly by user. No scientific agent receives host admin data.
- Lane B dependency request: add `mlflow-skinny==3.16.1` to root pyproject/lock (user explicitly requested MLflow). Installed with `uv pip install mlflow-skinny` for verification; root ownership preserved.
- Lane B sampler request: expose lane A `calibration.study.random_spec` from a safe shared `tools/` module. Until then, the host-only eval preparation step generates public schedules with that existing sampler; lab reads the supplied schedule and never imports calibration.
- Lane B robustness request: public session snapshot and idempotent experiment keys are needed for automatic recovery after a lost mutation response or server restart. Until supplied, client checkpoints resume against a live server and fail closed on ambiguous mutations, never replaying an experiment blindly.
- User authorization (2026-10-03 23:28 UTC): both pending firing tables approved and committed. Automatically approve subsequent dev/batch commits; preserve approval as default for interactive demo sessions. The real 5-cycle and mock 6-cycle runs stopped because PI chose to commit, not because the host cut them short. L1–L5 replaces the earlier prompt-stub scope.

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
