# Status

Last updated: 2026-10-04 (integrator: E1 to E3). Each assistant edits only its own lane section, plus "Requests" and "Interface changes".

## Gates

| Gate | Target hour | State | Decided by |
| --- | --- | --- | --- |
| Schemas agreed (`SPEC.md` section 5) | 0.5 | In code (`schemas/`), awaiting human sign-off | Human |
| Calibration stage A: ceiling | 1.5 | After decision 1: exact law 95.2%, true-form fit 94.2% (2% radius); wrong-form check 49% (nesting) | Human |
| Calibration stage B: headroom go or no-go | 4 | New headline: random 95%, greedy 100%; co-headlines separate (recovery 16 vs 19 of 20, false discovery 2/5 vs 0/5) | Human |
| One full loop, end to end | 8 | Not started | Human |
| Agents and prompts frozen | 12 | Not started | Human |
| Test seeds frozen | Before hour 12 | Not started | Human only |

## Lane A / physics: eval and integration (Claude Code, branch `physics`, the integrator)

**Current milestone:** E1 to E3 done. Waiting on lane B's agent CLI (SPEC 5.6) to run the agent conditions.

**Done** (2026-10-04)

- Schemas and SPEC, committed first and separately (`5f60ab7`):
  - Law strings are checked against an arithmetic whitelist before sympy parses them. This closes the `parse_expr`/eval injection reported by lane B; 9 hostile expressions are tested.
  - `Condition` adds `textbook` and `oracle`.
  - SPEC 5.6 fixes the agent CLI contract.
  - SPEC 7 fixes the four primary metrics and the law-grading rule, with 40 test seeds (10 per family).
  - The SPEC 8 wrong-form check excludes forms that contain the true law.
- AGENTS.md: four lanes, integrator role, merge and autonomy rules (`50e96ee`).
- **E1 evaluation harness** (`eval/`, `caadca5`):
  - `eval.run` opens sessions, runs each condition as a subprocess through the SPEC 5.6 command line, runs N in parallel, caps wall clock (the agent gets `--max-tokens`), retries a crash once in a new session, and resumes from `state.json`.
  - `eval.sampler` is the shared fixed-seed random spec stream (`SAMPLER_SEED = 20261004`).
  - `eval.scripted` provides textbook, oracle and scripted random over HTTP, reusing `calibration/`.
  - `eval.agent_stub` stands in for lane B's CLI.
  - `eval.grade` computes the 4 primary metrics, the threshold curve, abstention, plan-changed share, laws rejected and initial explanation rejected. Law grading follows the SPEC 7 rule.
  - `eval.report` computes per-condition means and paired differences with 95% bootstrap CIs. It draws learning curves, in-range versus beyond-range hits and false discovery, writes the summary table as Markdown and PNG, and with `--readme` fills the README results block.
- **E1 dry run** on dev 1000 to 1002, all six labels, in `runs/eval-dryrun-dev3/` (report in `report/`):

  | Reference | Beyond probe hit | Mission hit | Law recovery |
  | --- | --- | --- | --- |
  | Textbook | 0.02 | 0.27 | 1/3 |
  | Oracle | 1.00 | 0.93 | 3/3 |
  | Scripted random | 0.83 | 0.80 | 2/3 |

  The lab, single and random rows are the stub (textbook physics plus 12 weighs) and test plumbing only. Wall time: about 3 s textbook, about 20 s oracle, about 15 s scripted random, about 4 s stub.
- **E2 scale**:
  - The 60-world scripted calibration is below.
  - Server hardening (subagent, merged): per-session locks, a world cache built once per seed, atomic budget, table use and commit, and atomic session files. Prediction tables are pinned to law content, and `/commit` requires exactly 5 shots (lane B's two requests).
  - The load test found a real bug, now fixed: `inf` misses produced a 500 on `/admin/score`; non-finite values are now written as `null`.
  - `tests/test_server_load.py` runs 16 concurrent sessions on a real uvicorn server, plus double-submit races. CLI: `python -m world.loadtest`. One run gave 16 sessions, 557 requests, 7.1 s, 0 errors; p95 latency was 157 ms for `/experiment` and 161 ms for `/nominate`. `/session` takes about 1.9 s the first time a world is generated.
  - `python -m world.freeze` is written and tested on dev seeds only. **It has not been run, and `world/test_seeds.lock` does not exist.**
- **E3** `README.md`: reproduction commands, the dev-calibration disclosure, and a results block that `eval.report --readme` fills.

**Results: scripted policies on 60 dev worlds** (1000 to 1059, 15 per family; 2% hit radius, 0.5% / 0.1 degree launcher). Raw data in `calibration/results/dev60/`, table in `primary_table.md`. Means with 95% bootstrap CIs:

| Metric | Random | Greedy | Paired (greedy - random) |
| --- | --- | --- | --- |
| Beyond-range probe hit rate after 12 | 0.892 [0.831, 0.945] | 0.995 [0.990, 0.999] | +0.103 [+0.052, +0.164] |
| Mission hit rate | 0.895 [0.846, 0.938] | 0.951 [0.924, 0.973] | +0.056 [+0.024, +0.094] |
| Mission hit rate, beyond-range targets | 0.845 [0.774, 0.909] | 0.926 [0.886, 0.960] | +0.081 [+0.036, +0.138] |
| Law-form recovery (SPEC 7 rule) | 0.900 [0.817, 0.967] | 0.967 [0.917, 1.000] | +0.067 [-0.017, +0.167] |
| Control false discovery (n = 15) | 0.200 [0.000, 0.400] | 0.133 [0.000, 0.333] | -0.067 [-0.333, +0.200] |

- Stage A: the exact law hits 96.5% and the true-form fit 95.1%. The best form that does not contain the truth hits 5.4% of beyond-range targets (F0 12%, F1 2%, F2 2%, F3 4%). No target is unreachable. The slowest single fit took 3.65 s.
- The two policies separate on beyond-range probes and mission hits. Law recovery and false discovery do not reach significance with 60 worlds, and 15 controls, respectively.
- Greedy's 2 of 15 false discoveries come from its claim rule: it claims non-ordinary physics whenever the BIC-selected form is not plain gravity with quadratic or no drag. That includes nesting forms whose extra parameter is within 2 sd of zero. I left the rule as it was, because changing it after seeing the numbers would be tuning.

**Commands**

```
.venv/bin/python -m pytest -q                                   # 59 passed, 1 skipped, about 21 s
.venv/bin/python -m calibration.study --seeds 1000-1059 --stages AB --hit-frac 0.02 --out calibration/results/dev60 --jobs 8
.venv/bin/python -m calibration.report calibration/results/dev60
.venv/bin/python -m eval.run --condition {textbook,oracle} --seeds 1000-1002 --out runs/eval-dryrun-dev3 --serve --parallel 3
.venv/bin/python -m eval.run --condition random --agent scripted --seeds 1000-1002 --out runs/eval-dryrun-dev3 --serve --parallel 3
.venv/bin/python -m eval.run --condition {lab,single,random} --agent-cmd ".venv/bin/python -m eval.agent_stub" --seeds 1000-1002 --out runs/eval-dryrun-dev3 --serve
.venv/bin/python -m eval.grade runs/eval-dryrun-dev3 && .venv/bin/python -m eval.report runs/eval-dryrun-dev3
SOLZERO_ADMIN_TOKEN=t .venv/bin/python -m world.loadtest --url http://127.0.0.1:8000 --admin-token t --sessions 16 --out runs/loadtest
```

**Blockers:** none of my own. The agent conditions need lane B's CLI (see Requests).

**Next step:** once lane B implements SPEC 5.6, run lab, single and random through the CLI on 3 dev worlds, then on the 60 dev worlds for a dev-world comparison. Then wait for the human freeze.

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
  - `viewer.fixtures.oracle_session` runs the scripted greedy-disagreement oracle (12-form library, no language model) against the real `world.server.WorldServer` in-process, on dev seeds 1000 to 1003. Prediction tables and the budget are checked by the server code. It writes SPEC 5.5 ledgers and 5.7 `metrics.json`.
  - `viewer.fixtures.calibration_aggregate` turns calibration stage B (`dev20_v2`) into `aggregate.json`.
  - Real runs load by dropping a directory into `viewer/public/runs/` and running `node viewer/scripts/build-index.mjs` (Vercel runs it on every build). See `viewer/README.md`.
- `tests/test_viewer_fixtures.py` validates every fixture ledger entry against `schemas`, checks that prediction tables cover exactly the live set, and checks the `metrics.json` and `aggregate.json` shapes.
- The interface is SPEC 5.5 (`candidates` payload) and 5.7 (viewer inputs). Both were committed separately, before the code.

**Results** (fixture data; dev seeds only)

| Seed | Family | Final form | Hits | Plan changed by evidence |
| --- | --- | --- | --- | --- |
| 1000 | F0 | const_p2 | 5/5 | 7 of 12 cycles |
| 1001 | F1 (p = 3) | const_p3 | 5/5 | 5 |
| 1002 | F2 | mass_p2 | 5/5 | 4 |
| 1003 | F3 | height_p2 | 4/5 | 5 |

- Runtime is 11 to 26 s per world.
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
.venv/bin/python -m pytest -q                            # 64 passed, 1 skipped (after merging main)
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
- **Fixture condition:** the fixture now opens `oracle` sessions; the merged server accepts that condition. The first version used `lab`, because the server did not accept `oracle` then. The oracle still sends a prediction table with every experiment, so the server checks each one.
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

**Eval importer (2026-10-04, after V3)**

- `viewer.import_eval` converts `eval/run.py` output straight into the viewer layout, so eval does not need to change its format:
  - per run: `session_info.json`, `admin_score.json`, `truth.json` and `agent/ledger.jsonl` become `runs/<label>_<seed>/`;
  - `all_metrics.jsonl` becomes `eval/aggregate.json`.
- Mission hit counts are rates × 2 in-range and × 3 beyond-range targets.
- Checked on the physics lane's dry run (`runs/eval-dryrun-dev3`, 18 sessions, 6 labels, dev worlds 1000 to 1002) in a scratch copy of the site. Every replay and the results page rendered, with no console errors.
- That dry run's lab, random and single rows come from the stub agent (0 tokens, about 1 s), so they are **not** deployed. The site still shows the oracle fixtures.
- `tests/test_viewer_import.py` covers the conversion.
- Supporting changes:
  - The viewer knows the `random-scripted` label (scripted-random reference), in its own colour slot. The six-colour palette passed the dataviz validator in light and dark modes.
  - Charts now have a legend whenever there are two or more series.
  - The results page adds the pairs oracle − scripted random and random − scripted random.

**Next step**

- When the real eval run finishes: `.venv/bin/python -m viewer.import_eval runs/eval --title "..." --remove-fixtures`, then redeploy.
- Add the demo video when the sim lane renders it.

## Lane sim: MuJoCo (Claude Code, branch `sim`)

**Current milestone (2026-10-04):** S1 to S5 are done. The demo video and the side-by-side clip render from any SPEC 5.5 ledger.

**Done**

- **S1, the scene** (`sim/scene.py`, `sim/arm.py`, `sim/experiment.py`):
  - Table, a Menagerie Panda on a 0.35 m pedestal, a launcher with an elevation actuator, a tray with the seven spheres, target bins, and a wrist force sensor.
  - MuJoCo gravity is 0 and fluid forces are off. Every step, the hidden law (`world.law` compiled by `tools.analysis.compile_law`) is added as an external force on every sample, in the passive-force callback. Flights use RK4 at 1 ms, so the law is evaluated at every stage.
- **S2, primitives:**
  - The primitives are pick, transit, move_sample_to, hold, release (drop), load_launcher, aim and fire.
  - Fast mode places the sample at its release state. Full mode runs the arm.
  - The arm servo now has integral action: a load offset is learned while the arm settles, and the hidden law is never used.
- **S3, equivalence:** `sim/validate.py` runs full mode against `World.run_experiment` with the same random stream.
- **S4, the renderer** (`sim/render.py`):
  - Sequence: title, shot zero, each experiment executed by the arm, then the five mission shots, then the hidden law beside the discovered law.
  - Overlays per experiment: experiment number, spec, each live law's pre-registered prediction, the measured value, verdicts with z, the plan-changed flag, and the budget.
  - Output is 1920x1080 at 30 fps, rendered offscreen. Arm sequences are compressed to 2.2 s, flights run near real time (capped at 1.5 s), and results hold for 1.2 s.
  - Inputs, in order of preference:
    - `metrics.json` (SPEC 5.7);
    - else `session_info.json` + `world_session.json`;
    - else a bare ledger plus `--seed` (SessionInfo is rebuilt from the world).
  - Mission shots are steered to the server's graded landing point when one is available. Otherwise they use a seeded actuation draw, and the scoreboard says "simulated here: no server grade".
- **S5, the clip** (`sim/clip.py`):
  - Left: `mission_300` aimed with textbook physics by the shared `plan_shot`. Right: the committed shot.
  - Both are fired into the hidden law, side by side. By default the target is the beyond-range target with the worst textbook miss.
- **Tests:** `tests/test_sim.py` has 7 tests, including a render and clip smoke test of about 60 s. It skips without mujoco or the assets. `tests/test_sim_oracle_ledger.py` also runs.

**Results** (dev worlds only; raw JSON in `sim/results/`)

- **S2 failure rate:**

  | Run | Failed | Worst deviation from integrator (noiseless) | Median deviation |
  | --- | --- | --- | --- |
  | 50 randomized full-mode experiments (seeds 1000 to 1009) | **0/50** | 0.022 sd | 0.0005 sd |
  | 500 randomized (seeds 1000 to 1049), before integral action | 1/500 (weigh of ref_800 at 0.04 m, "arm did not settle") | 0.086 sd (drops of ref_800 began up to 1.1 mm low) | |
  | 500 randomized, after integral action | **0/500** | 0.032 sd | 0.0006 sd |

  - Zero failures in 50 bounds the failure rate at about 6% (95%). Zero in 500 bounds it at about 0.6%.
  - One full-mode experiment takes 1.5 to 1.8 s of wall time and 15 to 19 s of simulated time.
- **S3 equivalence:** 20 full-mode experiments, 5 each on seeds 1000 to 1003 (F0 to F3), against the server path with identical noise draws.

  | Observable | n | Max abs difference |
  | --- | --- | --- |
  | force_n | 6 | 0.0009 sd |
  | fall_time_s | 6 | 0.011 sd |
  | landing_x_m | 8 | 0.0005 sd |
  | flight_time_s | 8 | 0.0008 sd |

  - All are within noise, with no status mismatches. Fast mode alone agrees with the integrator to 1e-9 m on an 11 m shot.
- **S4 renders:**
  - The scripted oracle on dev 1001 (viewer fixture): 12 experiments in **67.3 s**, 1920x1080, 30 fps, h.264, 3.4 MB. It takes 100 s to render.
  - Lane B's real agent ledger on dev 1000 (5 experiments, then commit): 40.6 s.
- **S5 clip** on dev 1001, target t5 (beyond range, radius 5.75 cm), 5.3 s:

  | Shot | Speed | Elevation | Miss | Outcome |
  | --- | --- | --- | --- | --- |
  | Textbook | 6.09 m/s | 32.5° | 41.8 cm | miss |
  | Discovered (const_p3) | 6.08 m/s | 52.5° | 3.2 cm | hit |

- **Bugs fixed during S1 to S2:**
  - MjSpec reads angles in degrees by default, which pinned the launcher at 2 degrees.
  - The fingers touched the barrel while loading.
  - Holding the force constant over a step gave first-order error (8 mm on an 11 m shot at 0.1 ms).
  - The servo sagged under the heaviest sample.
  - The model-compile path calls the passive callback, so the callback is installed only while stepping.

**Commands**

```
.venv/bin/python -m sim.fetch_assets      # Menagerie panda at commit 4d038b3f into sim/assets/ (gitignored)
uv pip install --python .venv/bin/python mujoco==3.14.0   # until physics adds it to pyproject (see Requests)
.venv/bin/python -m sim.experiment --seed 1002 --mode full --spec '{"type":"drop","sample_id":"ref_100","height_m":1.0}'
.venv/bin/python -m sim.validate robustness --seeds 1000-1009 --per-world 5 --out sim/results/robustness.json          # S2, 9 s
.venv/bin/python -m sim.validate robustness --seeds 1000-1049 --per-world 10 --jobs 12 --out sim/results/robustness_500.json  # 90 s
.venv/bin/python -m sim.validate equivalence --seeds 1000-1003 --per-world 5 --out sim/results/equivalence.json        # S3, 7 s
.venv/bin/python -m sim.oracle_ledger --seed 1001 --out runs/oracle_1001
.venv/bin/python -m sim.render --ledger viewer/public/runs/oracle_1001/ledger.jsonl --out runs/render/oracle_1001.mp4   # S4, about 100 s
.venv/bin/python -m sim.render --ledger runs/laneb_real_1000/ledger.jsonl --seed 1000 --out runs/render/laneb_real_1000.mp4
.venv/bin/python -m sim.clip --ledger viewer/public/runs/oracle_1001/ledger.jsonl --out runs/render/oracle_1001_clip.mp4  # S5, 10 s
.venv/bin/python -m pytest -q             # 76 passed, 1 skipped (with mujoco and the assets)
```

Videos are in `runs/render/`, which is untracked. For the demo run on a test world, pass `--final-eval` to `sim.render` and `sim.clip`, and only after the human's freeze.

**Decisions made autonomously**

- The Menagerie assets (34 MB) are fetched at a pinned commit, not committed.
- The arm is gravity-compensated and has integral action on its joint servos.
- Grasping uses a weld constraint, and samples never collide with the fingers.
- The weigh station sits beyond the table edge, so height 0 touches nothing. Above about 1 m the sample is held with the gripper horizontal.
- The launcher releases the sample at the muzzle (the barrel guides it). The arm's loading error is checked, and anything over 5 mm fails as a misload. Loading happens at 75° elevation, so the barrel clears the fingers.
- Target bins are visual only, and hits are scored geometrically as in SPEC 3.
- The law force goes in through MuJoCo's passive-force callback rather than `xfrc_applied`. It is still an external force each step, now also at every RK4 stage.
- **S2 counts these failure causes:** IK failure, an arm that does not settle within 1.5 s to 0.2 mrad, arm contact with the table, launcher or pedestal, a slipped grasp, a misload, and a launcher that does not reach its elevation.
- **Renderer:**
  - On-screen numbers are the ledger's. The arm re-executes each spec without noise.
  - The side camera looks from the far side of the launch plane, so the arm is not in the way, and is mirrored so downrange runs left to right.
  - Shot zero's practice target t0 is drawn as a bin.
- **The clip's textbook shot** uses the shared `plan_shot` with `textbook_law()`, plus a seeded actuation draw (stream `[seed, 88, k]`).

**Update (2026-10-04, second session)**

- **`python -m sim.demo --run <id | run dir | ledger> [--style normal|large|both]`** renders three assets into `runs/demo/<run>/`, in parallel, plus a `demo.json` manifest:
  - `replay.mp4`, the full replay;
  - `clip.mp4`, the side-by-side;
  - `opener.mp4`, exactly 10 s of shot zero missing, with a zoom on the miss.
- **Large-type variant (`--style large`):** overlays are drawn on a 1280x720 canvas and scaled up, so all type is 1.5x bigger. Panels use a relative layout.
- **Run resolution:**
  - Accepted layouts: eval runner (`attempt*/admin_score.json`, `truth.json`, `session_info.json`), viewer `metrics.json`, lane B's `admin-score-persisted.json`, oracle `world_session.json`.
  - Mission hits come from the server's admin score whenever one is present; `demo.json` records which source was used.
- **Lane B's real session `s_40ebae9470`** (dev 1000; the PI committed after 5 of 12 experiments) rendered with the server's admin score: **5/5 hits** (misses 0.1 to 2.0 cm).
  - Clip on t4 (beyond range): the textbook shot misses by 51.6 cm; the agents' law lands 2.0 cm off (radius 5.4 cm).
  - Both styles took 1 min 34 s. The first attempt took 14.5 min; demo workers now run BLAS single-threaded.

**Next step**

- Rerun the sim tests on main once `mujoco` is in `pyproject.toml` (still missing as of this update).
- Render the first complete eval-runner session from lane B with `sim.demo` as soon as one exists. As of this update, every `lab`/`single` eval run is `eval.agent_stub`.


- Render the demo run once the human freezes the test seeds and picks it (SPEC 10: the selection rule is fixed before results are seen).
- Copy `video.mp4` into the viewer run directory, coordinated with the viewer lane.

## Requests (one lane asking the other, or the human, for something)

- Lane sim to physics: please `uv add mujoco` (3.14 tested). Until then, `tests/test_sim.py` skips. Please also add `runs/` to `.gitignore`.
- **Integrator to lane B (open, 2026-10-04):** please implement the agent CLI exactly as in SPEC 5.6, or amend 5.6 in a schema-and-SPEC commit:
  - `python -m lab.run --world-url --session-info FILE --condition {lab,single,random} --out DIR [--specs FILE] [--max-tokens N] [--max-wall-s S] [--approval {human,auto}]`.
  - The runner opens the session and passes its `SessionInfo`; `lab.run` must not call `/session`.
  - Write `DIR/summary.json` and `DIR/ledger.jsonl`, and exit 0.
  - `eval.agent_stub` is a reference implementation of the plumbing. `tests/test_eval.py::test_pipeline_textbook_and_stub` shows the expected behaviour.
- **Integrator to lane B (fyi):**
  - `/admin/score` now writes non-finite misses as `null`.
  - `/commit` requires exactly 5 shots.
  - Prediction tables are pinned to law content, so changing a law under the same id invalidates the table, as your mock already does.
  - The `schemas.check_expr` grammar is now enforced at the HTTP boundary.
- **Integrator merge check (2026-10-04):** test-merging `omnigent` (`f4d72f0`) into `main` (`a254def`) conflicts only in `STATUS.md`. With the conflict resolved, the full suite passes: 110 tests, including lane B's and the leak test with `lab/` present. Lane B can `git merge main`, keep both lane sections and both sets of Requests rows, and merge to main.
- Resolved: lane B's requests about law-content invalidation, exactly-five-shot commits and safe expression parsing are done (`5f60ab7`, and the server merge after it).

- Lane A to human: should the wrong-form check exclude forms that contain the true law? See the lane A Blockers.
- Lane A to lane B: the real server is in `world/server.py`, with the same contract as `mock/` plus `POST /laws` and `Target.hit_radius_m`. The mock needs `/laws` and the live-set check to stay faithful to 5.2.
- Viewer to lane B: please add `disagreements` (one `Disagreement` per candidate, from the `disagreement` tool) to the `candidates` ledger payload (SPEC 5.5). The replay shows it as the disagreement table; without it the viewer can only derive gaps for the chosen spec.
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
| 2026-10-04 | Claude Code (viewer) | SPEC 5.7: aggregate `condition` is the eval label, adding `random-scripted`; `viewer.import_eval` converts `eval/run.py` output | eval writes the scripted-random reference under its own label beside the agent random condition |
| 2026-10-03 | Claude Code | SPEC 5.4: analysis tools take keyword-only extras (`samples`, `noise_sd`, `seed`, `n_draws`); added `predict_many` and `disagreement_many` | Explicit seeds; batched candidate scoring |

## Design changes from calibration (dev worlds only)

| When | Change | Before | After | Reason |
| --- | --- | --- | --- | --- |
| 2026-10-04 | Launcher actuation error 2% / 0.5 deg to 0.5% / 0.1 deg (experiments and mission) | Exact-law hit 49% (old 5 cm radius) | Exact-law hit 95.2% (2% radius) | Human decision 1: the ceiling was limited by actuation noise |
| 2026-10-04 | Hit radius 5 cm to max(5 cm, 2% of target distance). 3% was tried first. | True-form fit hit 47% | 94.2% (3%: 96.7%) | Decision 1. At 3% the best wrong form hit 50% of beyond-range targets (above 30%), so 2% was used; at 2% it hits 49%, caused by nesting forms |
| 2026-10-04 | Probes 20 mixed to 20 in-range plus 20 beyond-range; headline = experiments to 80% of beyond-range probes within the radius | Old 5 cm median metric: random 95%, greedy 100% reach | New headline: random 95%, greedy 100%; paired difference 0.3 (CI -0.75 to 1.6) | Human decision 2. Budget, noise and ranges unchanged |

## Decisions made autonomously

| When | Who | Decision | Why |
| --- | --- | --- | --- |
| 2026-10-04 | integrator | Proposed the SPEC 5.6 agent CLI, in which the runner opens the session and passes `SessionInfo` | The runner needs the session id for admin scoring and crash recovery, and there is no GET-session endpoint |
| 2026-10-04 | integrator | `--approval auto` exists for evaluation runs only, recorded in the ledger. `human` keeps the SPEC 6 gate | Hundreds of eval runs cannot each wait for a human approval |
| 2026-10-04 | integrator | Textbook and oracle run through the world server as conditions `textbook` and `oracle`; scripted random uses `random` with label `random-scripted` | One scoring path for every condition |
| 2026-10-04 | integrator | A crashed agent is retried once in a new session (new noise stream). A run that is still running when the runner itself crashes is restarted from scratch | The server keeps sessions in memory, and a fresh session keeps the pre-registration record clean |
| 2026-10-04 | integrator | Default caps: 1800 s wall clock and 2,000,000 tokens per run, with 4 parallel runs | Placeholders until lane B measures a full run; all are flags |
| 2026-10-04 | integrator | Law grading thresholds: a dependence counts above a 1% change in gravity over the range; the drag exponent is the slope between 1 and 4 m/s; the exponent check is waived when the true rho is below 0.05 | Needed to make the human's 2-sd rule executable on free-form laws. Written into SPEC 7 before any test run |
| 2026-10-04 | integrator | The beyond-range probe hit rate uses the last nomination made after the final experiment. With no nomination at all it counts as 0, and the run is flagged | A run that never names a law gets no credit |
| 2026-10-04 | integrator | A missing shot counts as a miss in the mission hit rate (the server now refuses fewer than 5 anyway) | Conservative |
| 2026-10-04 | integrator | The scripted claim rule (non-ordinary whenever the selected form is non-ordinary) is unchanged after its 2/15 false discoveries were seen | Changing it after seeing the result would be tuning |
| 2026-10-04 | integrator | I did not merge `omnigent` into `main`; lane B merges its own branch under the new rules. I will resolve conflicts if they appear | Each lane merges itself when its tests pass |

## Decisions by the human

- 2026-10-04: the wrong-form check excludes forms that contain the true law, and the 2% hit radius is final. For grading, a fitted form matches the true law after dropping terms whose parameter is within 2 sd of zero.
- 2026-10-04: experiments to threshold becomes secondary. The primary metrics are the beyond-range probe hit rate after 12 experiments, mission hit rate, law-form recovery and control false discovery. Single-use prediction tables, live-set invalidation and `/admin/worlds` are approved.
- 2026-10-04: the physics branch is the integrator, with lanes physics, omnigent, sim and viewer.

- None yet.
