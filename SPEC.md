# Sol Zero: Build Specification

Version 0.1, 2026-10-03. This file is the source of truth for every coding assistant and human on the project.
Section 5 (Interfaces) is a contract: change it only in the same commit as the code that changes it, and note the change in `STATUS.md`.

## 1. Mission and claim

Sol Zero is a robot lab that must deliver a sample into a target on a world whose physics it has not been told, using 12 experiments.

- **Mission:** launch the mission sample into five untouched targets, one shot each, no retries.
- **Scientific question:** can a multi-agent lab discover an unfamiliar force law from a small experiment budget, and does the choice of experiments drive that?
- **Claim under test:** at equal budget, designed experiments reach a given predictive accuracy in fewer experiments than random ones, and a specialist lab beats a single agent.
- **Why the law matters to the mission:** the mission sample cannot be test-launched, and three of the five targets lie beyond the tested speed range. A fit with the wrong functional form does not extrapolate.
- **Shot zero:** before any experiment, one practice shot is fired with textbook physics (9.81 m/s^2, standard air drag). Every condition receives its result. It is the initial failure and the first datum.
- **Motivation only:** planetary robots that cannot wait for Earth. No Mars speedup is measured or claimed.

## 2. Physics families (hidden world generator)

Every world draws one of four families. The control family is ordinary physics with unfamiliar parameters.

```
m * dv/dt = -m * g(m, z) * z_hat  -  c * |v|^(p-1) * v
```

m is inertial mass (kg), z is height above the table (m), v is velocity (m/s).

| Family | Gravity term g(m, z) | Drag exponent p | What the lab must find |
| --- | --- | --- | --- |
| F0 Ordinary (control) | g0 | 2 | Parameters only. An exotic claim here is a false discovery. |
| F1 Drag-law shift | g0 | 1 or 3 | The functional form of drag |
| F2 Mass-dependent gravity | g0 * (m / 0.1)^alpha | 2 | Weight is not proportional to inertial mass |
| F3 Height-dependent gravity | g0 * (1 + kappa * z) | 2 | Gravity changes across the workspace |

| Parameter | Starting range | Applies to |
| --- | --- | --- |
| g0 | 3 to 14 m/s^2 | All families |
| Drag strength rho | 0.1 to 0.8 (F0: 0 to 0.8) | All families. rho = drag force / weight for the 100 g sample at 3 m/s. c is derived from rho. |
| alpha | 0.15 to 0.5, either sign | F2 |
| kappa | 0.25 to 0.6 per metre, either sign | F3 |

Ranges are starting values. Calibration may adjust them on dev worlds only (section 8).

- **Drag is always a live rival.** F2 and F3 keep ordinary quadratic drag, so "heavier falls faster" always has an ordinary explanation. Positive alpha is the hard, confounded case.
- **What separates the rivals:** drag effects vanish as speed approaches zero and grow with speed. Mass-dependent gravity changes static weight per unit mass. Height-dependent gravity is the same for all masses and shows up statically.
- **Samples only:** the hidden law acts on samples. The arm is treated as a calibrated instrument.
- **Generator isolation:** the generator runs in a separate server process. The scientific agents never see families, ranges, or code. Their prompts say only that physics may differ from Earth's.

## 3. Geometry, samples and experiments

### Geometry

- Motion is planar: x is downrange, z is up, the table surface is z = 0.
- The launcher muzzle is at (x, z) = (0, 0.20).
- Experiment launches land on the table plane z = 0.
- A target has a centre (x_T, z_T) with z_T between 0 and 0.5 m and a hit radius r_T = max(0.05 m, 0.02 * |x_T - x_launcher|). A shot hits if |x - x_T| <= r_T when the sample crosses z = z_T moving downward. Each target in `SessionInfo` carries its `hit_radius_m`.

### Samples

| Sample id | Inertial mass | Role |
| --- | --- | --- |
| `ref_020`, `ref_050`, `ref_100`, `ref_200`, `ref_400`, `ref_800` | 20, 50, 100, 200, 400, 800 g | Reference spheres. Any experiment. |
| `mission_300` | 300 g | Mission sample. May be weighed. Never dropped or launched before the mission. |

All spheres have the same 2 cm radius, so the drag coefficient c is the same for all of them. Masses are labelled and known to the agents.

### Experiments (each call costs one of 12)

| Tool | Agent chooses | Range | Returns | Starting noise (1 sd) |
| --- | --- | --- | --- | --- |
| `weigh(sample, height)` | Sample, height | 0 to 1.2 m | Static force on the wrist sensor (N) | 2% of reading |
| `drop(sample, height)` | Reference sample, height | 0.1 to 1.2 m | Fall time to the table (s) | 5 ms |
| `launch(sample, speed, elevation)` | Reference sample, speed, angle | 1 to 4 m/s, 15 to 75 degrees | Landing distance (m), flight time (s) | 1 cm, 5 ms. Actuation error: speed 0.5%, angle 0.1 degrees |

- **Summary observables, not trajectories.** Timing gates and a landing sensor return one or two numbers per experiment. With dense trajectories almost any 12 experiments identify the law, and experiment choice stops mattering.
- **Mission launcher:** the same fixture with its speed limit raised to 7 m/s, and the same actuation error (speed 0.5%, angle 0.1 degrees).
- **Targets:** five per world. Two are reachable inside the tested range. Three need launch speeds above 4 m/s or sit on a raised platform. Target positions are known to the agents.

### Robot execution

- **Arm:** a Franka from MuJoCo Menagerie running scripted primitives only: pick, move, hold, release, load launcher.
- **Launcher:** an actuated fixture with programmable speed and elevation. The arm loads it and does not throw.
- **Flight physics:** MuJoCo, with built-in gravity and fluid forces switched off for samples and the hidden law applied as an external force each step.
- **Two modes, same physics:** fast mode places the sample at its release state and runs headless, for evaluation. Full mode moves the arm, for the demo.
- **Equivalence test:** run 20 experiments in both modes and confirm the observables agree within noise.
- **Before the MuJoCo scene exists,** the world server integrates the same law directly (section 5.4 integrator). MuJoCo must reproduce those observables within noise.

## 4. Analysis tools

Every condition uses the same four analysis tools, and none of them offers a menu of laws.

| Tool | Input | Output |
| --- | --- | --- |
| `fit_law` | Acceleration expressions in m, z, vx, vz, speed, with named free parameters and bounds | Fitted parameters with uncertainty, chi-square per degree of freedom, leave-one-experiment-out error |
| `predict` | Law, experiment spec | Predicted observables with uncertainty |
| `disagreement` | Experiment spec, list of laws | Gap between each pair of predictions, in units of measurement noise |
| `plan_shot` | Law, target, sample | Launcher speed and elevation, predicted miss spread |

- **Free-form laws:** the Theorist writes any expression. The tool integrates it numerically for each experiment and treats `weigh` as the static case (v = 0, force = m * |az|).
- **Stack:** sympy to parse and lambdify, a batched fixed-step RK4 integrator for speed (validated against scipy `solve_ivp` on 20 cases), scipy `least_squares` to fit.
- **Fairness:** the random-experiment baseline uses the same `fit_law`, `predict` and `plan_shot`. Only the choice of experiments differs.

## 5. Interfaces (contract)

### 5.1 Conventions

- SI units everywhere: m, s, kg, N. Angles in degrees in specs, radians inside the integrator.
- All records are JSON. Shared Python models live in `schemas/` (pydantic). Both lanes import from there.
- Law expressions are arithmetic strings over the variables `m`, `z`, `vx`, `vz`, `speed` and the law's named parameters. No other names are allowed. Allowed syntax: numbers, names, `+ - * / **`, unary minus, parentheses, and calls to `sqrt exp log sin cos tan tanh abs Abs`. At most 400 characters. Anything else is rejected before sympy parses it (`schemas.check_expr`).
- The world server base URL comes from the environment variable `SOLZERO_WORLD_URL`.

### 5.2 World server HTTP API

Agent-facing endpoints:

| Method and path | Request | Response | Errors |
| --- | --- | --- | --- |
| `POST /session` | `{world_id, condition}` | `SessionInfo` | 404 unknown world |
| `POST /laws` | `{session_id, live_laws: [Law]}` | `{ok: true}` | 404 unknown session; 409 already committed; 422 duplicate law_id |
| `POST /predictions` | `{session_id, spec, predictions[], tentative_followup}` | `{prediction_table_id}` | 404 unknown session; 422 spec out of range, or predictions not covering exactly the live law set |
| `POST /experiment` | `{session_id, spec, prediction_table_id}` | `Result` | 409 budget exhausted; 422 missing or mismatched prediction table, out-of-range spec, forbidden sample |
| `POST /nominate` | `{session_id, law, fit}` | `{ok: true}` | |
| `POST /commit` | `{session_id, law_id, shots[], claim, claims_non_ordinary}` | `{status: "committed"}` | 409 already committed; 422 not exactly one shot per target, or a shot outside the mission envelope |

- `world_id` is opaque to agents. `condition` is one of `lab`, `random`, `single` (agent conditions) or `textbook`, `oracle` (scripted references, run by `eval/`).
- `/laws` replaces the session's live law set (it starts empty). Every condition may call it.
- `/predictions` is required before every `/experiment` in the `lab` and `single` conditions. Its `predictions` must contain exactly one entry per live law, with the same `law_id` set, or the server returns 422. It is an empty list only while the live set is empty (cycle 0).
- A prediction table is used once, by an `/experiment` with the same spec. The live set at `/experiment` time must still be the one the table was made for, or the server returns 422. The set is compared by full law content, so re-registering a `law_id` with a changed expression or parameter bounds also invalidates the table.
- `/nominate` is called after every experiment with the current best law. The server scores it on hidden probes and returns nothing about the score.
- `/commit` returns no hit or miss information to agents.

Admin endpoints (never exposed to agent tools; protected by `SOLZERO_ADMIN_TOKEN`):

| Method and path | Purpose |
| --- | --- |
| `GET /admin/score/{session_id}` | Probe error after each experiment, mission hits and misses, law recovery inputs |
| `GET /admin/truth/{world_id}` | Family and parameters, for grading and the demo reveal |
| `GET /admin/worlds` | Map from served seed to opaque `world_id`, for eval runners |

### 5.3 Records

```json
// ExperimentSpec
{"type": "weigh", "sample_id": "ref_100", "height_m": 0.8}
{"type": "drop", "sample_id": "ref_400", "height_m": 1.0}
{"type": "launch", "sample_id": "ref_050", "speed_mps": 3.5, "elevation_deg": 40.0}

// Result
{"experiment_id": "e07", "index": 7, "spec": {...},
 "observables": {"landing_x_m": 1.92, "flight_time_s": 0.63},
 "noise_sd": {"landing_x_m": 0.01, "flight_time_s": 0.005, "speed_frac": 0.005, "elevation_deg": 0.1},
 "status": "ok", "budget_left": 5}
// weigh observables: {"force_n": ...}; drop observables: {"fall_time_s": ...}
// launch noise_sd also carries the launcher actuation error (speed_frac, elevation_deg),
// which fit_law propagates into the effective measurement sd
// status is "ok" or "failed"; a failed run still consumes budget

// SessionInfo
{"session_id": "s_ab12", "budget": 12,
 "samples": [{"sample_id": "ref_020", "mass_kg": 0.02, "launchable": true}, ...],
 "ranges": {"weigh": {"height_m": [0, 1.2]}, "drop": {"height_m": [0.1, 1.2]},
            "launch": {"speed_mps": [1, 4], "elevation_deg": [15, 75]},
            "mission": {"speed_mps": [1, 7], "elevation_deg": [15, 75]}},
 "noise_sd": {"force_frac": 0.02, "fall_time_s": 0.005, "landing_x_m": 0.01, "flight_time_s": 0.005,
              "speed_frac": 0.005, "elevation_deg": 0.1},
 "launcher": {"x_m": 0.0, "z_m": 0.2},
 "targets": [{"target_id": "t1", "x_m": 1.4, "z_m": 0.0, "hit_radius_m": 0.05}, ...],
 "shot_zero": {"spec": {...}, "target_id": "t0", "landing_x_m": 1.1, "miss_m": 0.52}}

// Law
{"law_id": "L3", "description": "quadratic drag, gravity scales with mass",
 "ax": "-c*speed*vx/m",
 "az": "-g0*(m/0.1)**alpha - c*speed*vz/m",
 "params": {"g0": {"init": 9.8, "lo": 1, "hi": 20},
            "alpha": {"init": 0.0, "lo": -1, "hi": 1},
            "c": {"init": 0.01, "lo": 0, "hi": 1}}}

// FitResult
{"law_id": "L3", "params": {"g0": {"value": 6.1, "sd": 0.2}, ...},
 "chi2_dof": 1.1, "loo_error": 1.3, "n_experiments": 7, "converged": true,
 "cov": [[0.04, ...], ...]}
// loo_error: RMS leave-one-experiment-out prediction error, in units of measurement noise sd
// cov: optional parameter covariance, rows and columns in params order

// Prediction (one per live law, inside a prediction table)
{"law_id": "L3", "observables": {"landing_x_m": {"mean": 1.90, "sd": 0.04},
                                 "flight_time_s": {"mean": 0.62, "sd": 0.01}}}

// Disagreement
{"spec": {...}, "pairs": [{"a": "L1", "b": "L3", "gap_sigma": 4.2}], "max_gap_sigma": 4.2}

// ShotPlan
{"law_id": "L3", "target_id": "t4", "sample_id": "mission_300",
 "speed_mps": 5.6, "elevation_deg": 38.0, "predicted_miss_sd_m": 0.03, "reachable": true}

// Verdict
{"law_id": "L1", "experiment_id": "e07", "z": 3.4,
 "verdict": "rejected", "note": "over-predicts range for light samples"}
// verdict is "supported", "rejected" or "insufficient_evidence"

// Commit
{"law_id": "L3", "claim": "law_identified", "claims_non_ordinary": true,
 "shots": [{"target_id": "t1", "speed_mps": 3.1, "elevation_deg": 42.0}, ...]}
// claim is "law_identified", "predictive_only" or "insufficient_evidence"
```

### 5.4 Analysis tool signatures (Python, in `tools/`)

```python
fit_law(law: Law, results: list[Result], samples: list[Sample] | None = None) -> FitResult
predict(law: Law, fit: FitResult, spec: ExperimentSpec, n_draws: int = 200) -> Prediction
disagreement(spec: ExperimentSpec, laws: list[tuple[Law, FitResult]], noise_sd: dict | None = None) -> Disagreement
plan_shot(law: Law, fit: FitResult, target: Target, sample_id: str, limits: dict) -> ShotPlan
simulate(law_fn, params, spec, sample) -> observables   # shared integrator, also used by the world server
```

All of them also take keyword-only extras with safe defaults: `samples` (defaults to the section 3
samples), `noise_sd` (defaults to the SessionInfo values), `seed` (default 0) for any random draws,
and `fit_law(..., loo=True, x0=None, n_starts=1)`. `predict_many` and `disagreement_many` take a
list of specs and run one batched integration. `predict` sd combines parameter spread with
measurement noise (sensor plus actuation); `disagreement` gaps are in units of that sd, with
`n_draws=0` meaning measurement noise only. `samples=None` means the standard set.

These functions are pure and do not call the world server. The Omnigent tool wrappers in `lab/` call them.

### 5.5 Ledger

One JSON object per line in `runs/<session_id>/ledger.jsonl`:

```json
{"ts": "2026-10-04T03:12:09Z", "cycle": 4, "agent": "experimentalist",
 "kind": "candidates", "payload": {...}}
```

`kind` is one of `law_set`, `candidates`, `prediction_table`, `result`, `verdicts`, `decision_diff`, `nomination`, `commit`.
A `decision_diff` payload is `{"tentative": ExperimentSpec, "actual": ExperimentSpec, "changed": true, "reason": "..."}`.
A `candidates` payload is `{"candidates": [ExperimentSpec], "chosen": ExperimentSpec, "disagreements": [Disagreement]}`. `disagreements` is optional, one per candidate in the same order; the viewer shows it as the disagreement table and otherwise derives gaps for the chosen spec from the prediction table.

### 5.6 Viewer inputs

The replay site (`viewer/`) is static. It reads files only; it never calls the world server.

```
viewer/public/runs/index.json            // built by `node viewer/scripts/build-index.mjs`; lists run directories
viewer/public/runs/<run_id>/ledger.jsonl // section 5.5, unchanged
viewer/public/runs/<run_id>/metrics.json // see below
viewer/public/runs/<run_id>/video.mp4    // optional
viewer/public/eval/aggregate.json        // see below
```

```json
// metrics.json: written by the condition runner after the session, from admin data
{"run_id": "s_ab12", "label": "lab on dev world 1002", "condition": "lab",
 "session_info": SessionInfo,
 "score": {...},   // GET /admin/score/{session_id}, unchanged
 "truth": {...}}   // GET /admin/truth/{world_id}, unchanged

// aggregate.json: written by eval/, one row per (world, condition) session
{"label": "...", "budget": 12, "notes": {"<condition>": "..."},
 "rows": [{"world": "1002", "family": "F2", "condition": "lab",
           "within_beyond": [0.0, ...],   // after 0..budget experiments; null where not nominated
           "median_error_m": [0.31, ...], // same indexing
           "law_recovered": true, "claim": "law_identified", "claims_non_ordinary": true,
           "mission_hits": 4, "mission_hits_beyond": 2}]}
```

`condition` in aggregate rows is one of `lab`, `random`, `single`, `textbook`, `oracle`. The viewer computes experiments to accuracy (first index with `within_beyond >= 0.8`), the metric table and the control-world false-discovery rate (F0 rows) from the rows.

### 5.6 Agent run CLI (lane B exposes, `eval/` calls)

The eval runner opens the session itself and hands it to the agent process:

```
python -m lab.run --world-url URL --session-info FILE --condition {lab,single,random} --out DIR
                  [--specs FILE] [--max-tokens N] [--max-wall-s S] [--approval {human,auto}]
```

- `--session-info` is the `SessionInfo` JSON returned by `POST /session`. The agent process never calls `/session`.
- `--specs` (random condition only) is a JSON list of exactly `budget` ExperimentSpecs from the shared sampler (`eval.sampler`). The Operator runs them in order. The other agents still propose, fit, judge, nominate and commit.
- `--max-tokens` and `--max-wall-s` are hard caps. The agent stops cleanly, and commits if it can, when a cap is reached. The runner also kills the process at `max-wall-s + 60`.
- `--approval auto` approves the five-shot table automatically, for evaluation runs only. It is recorded in the ledger as `{"kind": "commit", "payload": {..., "approval": "auto"}}`. `human` keeps the section 6 gate.
- On exit it writes `DIR/summary.json`: `{"session_id", "status": "committed" | "cap_reached" | "error" | "pending_approval", "tokens_used", "wall_s", "n_experiments", "error": str | null}`, plus `DIR/ledger.jsonl` (section 5.5).
- Exit code 0 means `summary.json` was written. Any other code is a crash, and the runner retries the run once.

## 6. Agents and Omnigent

Five agents each own one decision and pass the records above.

| Agent | Decision it owns | Receives | Emits | Tools |
| --- | --- | --- | --- | --- |
| PI (orchestrator) | When to stop experimenting and commit the mission | Ledger | Commit with five shots | `plan_shot`, `commit_mission` |
| Theorist | Which laws are live, up to four: propose, revise, retire | Results, verdicts | Law set with fits | `fit_law` |
| Experimentalist | The next experiment | Law set | At least two candidate specs, the chosen one, a prediction per live law, a tentative follow-up | `predict`, `disagreement` |
| Operator | None. It executes. | Chosen spec | Result | `weigh`, `drop`, `launch` |
| Analyst | Verdict per law: supported, rejected, or insufficient evidence | Predictions, result | Verdicts, confound note, nominated best law | `fit_law`, `predict` |

Loop: Theorist -> Experimentalist -> Operator -> Analyst -> Theorist, until the budget is spent or the PI commits.

Rules that make the orchestration consequential:

- **Pre-registration:** the experiment server rejects any run that lacks a prediction from every live law.
- **Decision diff:** before each run the Experimentalist records its tentative follow-up. After the result, the actual next spec is compared with it, and a difference is logged as "plan changed by evidence".
- **Revision is free:** proposing, revising or retiring a law needs no approval.
- **Abstention is allowed:** "insufficient evidence" is a valid verdict and a valid final claim.
- **Budget:** the server counts the 12 experiments and is authoritative. An Omnigent tool-call policy mirrors it.
- **Permissions:** only the Operator reaches robot tools. No agent reaches generator code, admin endpoints, or the `world/`, `calibration/` and `eval/` directories.
- **One approval gate:** `commit_mission`, because the five shots are irreversible. Launcher settings outside the safe envelope are blocked outright.
- **Records:** the JSONL ledger plus MLflow traces.
- **Platform:** open-source Omnigent, so policies can be written in Python. The managed Databricks version supports built-in and CEL policies only.
- **No leaks:** agent prompts and tool descriptions never name the families, the parameter ranges, or the 12-form library.

## 7. Evaluation

| Condition | Who picks experiments | Who proposes laws | What it isolates |
| --- | --- | --- | --- |
| Sol Zero lab | Experimentalist | Theorist | The full system |
| Random experiments | Uniform random over the allowed space | Same agents, same tools | The value of experiment choice |
| Single agent | One agent with every tool | The same agent | The value of specialist orchestration |
| Textbook (reference) | None | Earth physics, no fitting | The floor |
| Oracle design (reference) | Greedy maximum disagreement, no language model | A fixed library of 12 forms: three gravity terms by four drag options (none, p = 1, 2, 3) | The ceiling, and whether plain experimental design would do |

- **Matched budgets:** same model, 12 experiments, same tools, same token cap, same world seeds, compared pairwise.
- **Dev worlds:** seeds 1000 to 1999, for calibration and prompt work.
- **Test worlds:** 40 seeds (ten per family) drawn from 9000 to 9999 by `python -m world.freeze` and written to `world/test_seeds.lock` at the freeze, which only the human runs. No code path runs a test seed without the flag `--final-eval`.
- **References run through the same server:** textbook (no experiments, Earth physics) and oracle (greedy disagreement over the 12-form library) open sessions with conditions `textbook` and `oracle`. The scripted-random reference (shared sampler plus library fitting) uses condition `random`. All three reuse `calibration/`.
- **Hidden scoring:** after each experiment the server scores the nominated best law on 40 hidden probe launches landing on the table, all with mixed samples: 20 in the tested range (1 to 4 m/s) and 20 beyond it (4 to 7 m/s). A probe counts as within the hit radius when the law's predicted landing point is within max(5 cm, 2% of the true landing distance) of the true one, with a perfect launcher. Agents never see these scores.

| Metric | Definition |
| --- | --- |
| **Beyond-range probe hit rate (primary)** | Share of the 20 beyond-range probes within the hit radius for the law nominated after the last experiment |
| **Mission hit rate (primary)** | Share of the five targets hit, split into in-range and beyond-range, with median miss as a fraction of target distance |
| **Law-form recovery (primary)** | The final law has the right dependence (graded as below) |
| **Control false discovery (primary)** | Share of control worlds where the run claims non-ordinary physics |
| Experiments to threshold (secondary) | Experiments until at least 80% of beyond-range probes are within the hit radius. Report the whole curve. Also the median landing error over all 40 probes. |
| Abstention | Share of worlds ending in "insufficient evidence", and accuracy on the rest |
| Evidence-driven replanning | Share of cycles logged as "plan changed by evidence", and runs where the initial explanation was rejected |

- **Primary metrics are fixed here (2026-10-04)** and are reported for every condition, whatever comes out.
- **Statistics:** per-world paired differences with a 95% bootstrap interval (lab minus random, lab minus single). Forty worlds is still a small sample, and the write-up says so.
- **Grading:** `eval/grade.py`, written before any test run. Law-form recovery:
  - The committed law (or the last nominated one) is reduced by setting to zero every parameter whose fitted value is within 2 sd of zero.
  - The reduced law is probed numerically:
    - Gravity depends on mass if the static acceleration at z = 0 changes by more than 1% between 20 g and 800 g.
    - Gravity depends on height if, for the 100 g sample, it changes by more than 1% between z = 0 and 1.2 m.
    - The drag exponent is the log-log slope of the horizontal drag acceleration of the 100 g sample between 1 and 4 m/s (none if the drag is zero).
  - A run recovers the law if both dependences match the truth (mass only in F2, height only in F3) and the exponent is within 0.3 of the true p.
  - When the true drag strength rho is below 0.05, the exponent check is waived.

## 8. Calibration (first milestone, time-boxed to two hours)

The hardest dependency is headroom: whether the choice of experiments matters at all under 12 noisy measurements. If random experiments identify the law as well as designed ones, no agent quality can show the contribution. If nothing identifies it, every condition fails the mission. This needs no language model and no robot.

**Stage A, ceiling.** Fit the true functional form to 12 well-spread experiments under noise, plan the five shots, and measure the hit rate. If the true form cannot hit, fix that before anything else.

**Stage B, headroom.** Compare two scripted policies that share the same fitting and model selection:

- *Random:* uniform over experiment type and parameters.
- *Greedy disagreement:* after three fixed seed experiments (one weigh, one drop, one launch), fit all 12 library forms, take the best three by fit score, and choose from a pool of about 60 random candidate specs the one with the largest summed pairwise disagreement.

Start with 5 dev worlds to measure runtime. Expand to 20 to 30 if practical, and toward 200 if runtime allows. Save raw results to `calibration/results/` and an error-versus-experiment plot.

| Criterion | Reference level |
| --- | --- |
| Ceiling | Exact-law shots hit at least 90% of targets (under actuation error). The true-form fit is reported beside it. |
| Wrong-form check | The best wrong-form fit hits at most 30% of beyond-range targets. Forms that contain the true law as a special case are not wrong and are excluded. The 2% hit radius is final (human decision, 2026-10-04). |
| Headroom | Greedy brings 80% of beyond-range probes within the hit radius within 12 experiments in at least 70% of worlds; random in at most 40% |
| Mission separability | On beyond-range targets, the best wrong-form fit misses and the true-form fit hits, in at least 70% of non-control worlds |
| Control | Greedy claims non-ordinary physics in under 10% of control worlds |

These levels are references for a go or no-go decision by the human, not targets to hit.

- **Do not force a favourable gap.** Report what is measured.
- **Diagnose failures by cause:** fitting (true form fails even on noiseless data), noise (ceiling falls as noise rises), unreachable targets (`plan_shot` finds no solution under the true law), uninformative experiments (greedy and random are both poor and similar).
- **Design changes** (noise levels, observables, budget, ranges) are allowed on dev worlds only. Log each one in `STATUS.md` with before and after numbers.
- **Never touch test seeds.**
- **Fallbacks if headroom stays small:** cut the budget to 8 experiments, or drop flight time from the launch observables.
- **Disclosure:** the README states that difficulty was tuned on dev worlds with a scripted policy before any agent saw a test world.

## 9. Critical path

Hours count from the start of the build.

| Hours | Lane A: world and science (Claude Code) | Lane B: Omnigent and agents (Codex) | Gate at end |
| --- | --- | --- | --- |
| 0 to 2 | Schemas, integrator, world server, fit, predict and plan tools, calibration stage A | Install Omnigent. Mock world server from section 5. Smoke test: two agents exchange a spec through a function tool, and a policy blocks one call. | Tools return numbers |
| 2 to 4 | Calibration stage B. Tune on dev worlds. Freeze generator and test seeds. | Tool wrappers, ledger, pre-registration flow against the mock | Headroom go or no-go |
| 4 to 8 | MuJoCo scene: arm, launcher, force callback. Fast and full mode equivalence test. | Five agent definitions. First complete run on one dev world against the real server. | One full loop, end to end |
| 8 to 12 | Hidden scoring endpoints. Random and oracle conditions. | Prompt iteration on dev worlds only. Decision-diff logging. Single-agent condition. Tracing. | Agents and prompts frozen in a tagged commit |
| 12 to 16 | Test evaluation: 12 worlds by 3 conditions in parallel, plus references | Monitor runs. Fix crashes only, and rerun every condition if code changes. | Results in |
| 16 to 19 | Figures: learning curves, hit rates, false discovery | Choose the demo run from the test runs and render it in full arm mode | Demo run rendered |
| 19 to 22 | README, agent specs, policies, one reproduction command | Viewer site on Vercel that replays the ledger and video | Repo and live demo up |
| 22 to 24 | Demo, tech and team videos | Buffer | Submitted |

- **Cut order if behind:** full-arm rendering beyond the demo run, then family F3, then 12 test worlds down to 8, then viewer polish.
- **Never cut:** the control world, the random baseline, the frozen test seeds.

## 10. Demo flow (two minutes, one real test-world run)

1. **0:00** Mission card. Shot zero, fired with textbook physics, misses, and the miss distance appears.
2. **0:20** The Theorist posts rival laws as equations with fitted numbers.
3. **0:40** The Experimentalist's disagreement table appears. It picks the experiment where the rivals differ most, and the arm runs it.
4. **1:00** The result lands. One law is rejected with its z-score, and the "plan changed by evidence" flag shows the next experiment differs from the tentative one.
5. **1:20** The revised law is fitted. The PI commits the firing table and the human approves it.
6. **1:35** A shot at an untouched, beyond-range target lands. The hidden law is revealed beside the discovered one.
7. **1:45** Aggregate results across test worlds against random and single-agent, plus the control-world false-discovery rate.

Fix the run-selection rule before looking at results, and state it. No reruns for looks.

## 11. Limits

- The physics is simulated and designed by the team.
- The families are few and smooth: no hidden object properties and no stochastic laws.
- Sensing is simulated: Gaussian noise on summary observables, with no vision.
- The arm runs fixed primitives, and manipulation is not the contribution.
- Twelve test worlds give wide intervals.
- Difficulty was calibrated on dev worlds, and this is disclosed.
- Next experiment: the same lab on a real arm with a launcher and a camera.

## 12. Repository layout and ownership

| Path | Contents | Owner | Visible to scientific agents |
| --- | --- | --- | --- |
| `schemas/` | Shared pydantic models for section 5 | Claude Code (changes need a spec update) | Yes |
| `tools/` | `fit_law`, `predict`, `disagreement`, `plan_shot`, integrator | Claude Code | Yes, through tool wrappers |
| `world/` | Generator, world server, hidden scoring, `test_seeds.lock` | Claude Code | No |
| `calibration/` | Scripted policies, study runner, results, plots | Claude Code | No |
| `sim/` | MuJoCo scene, arm primitives, launcher | Claude Code | No |
| `mock/` | Mock world server implementing section 5.2 | Codex | Yes |
| `lab/` | Omnigent agent definitions, tool wrappers, policies, ledger writer | Codex | Yes |
| `eval/` | Condition runners, grading script, figures | Shared, agreed in `STATUS.md` | No |
| `viewer/` | Replay site | Shared | No |
| `runs/` | Ledgers and traces | Generated | Own session only |
