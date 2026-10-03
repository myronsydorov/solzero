# Sol Zero viewer

A static replay site for recorded runs. It reads files only and never calls the world server. The input format is SPEC section 5.7.

- `public/index.html` replays one session: cycle timeline, law cards with rendered equations and live/rejected/retired status, the disagreement table, pre-registered predictions against each result with z-scores, plan-changed-by-evidence markers, the budget bar, the mission panel (targets, hit zones, fired shots), and the hidden law beside the discovered one with the hidden probe score per experiment.
- `public/results.html` shows aggregate eval output: learning curves, the section 7 primary metrics (lab, random, single agent, plus any reference conditions present), secondary metrics, paired per-world differences with a bootstrap interval, control-world false discovery, and law recovery by family.

No build tooling: plain HTML, CSS and ES modules. KaTeX and math.js load from cdnjs.

## Add a real run

```
mkdir viewer/public/runs/<run_id>
cp runs/<session_id>/ledger.jsonl viewer/public/runs/<run_id>/
# metrics.json: {run_id, label, condition, session_info, score, truth}
#   score = GET /admin/score/{session_id}, truth = GET /admin/truth/{world_id}
cp <video>.mp4 viewer/public/runs/<run_id>/video.mp4      # optional
node viewer/scripts/build-index.mjs                        # rewrites runs/index.json
```

Aggregate results: replace `viewer/public/eval/aggregate.json` with eval output in the 5.7 format.

## Fixtures (current content)

Until real runs exist, the site shows fixture data, labelled as such:

```
.venv/bin/python -m viewer.fixtures.oracle_session --seeds 1000,1001,1002,1003 --out viewer/public/runs --jobs 4
.venv/bin/python -m viewer.fixtures.calibration_aggregate calibration/results/dev20_v2 --out viewer/public/eval/aggregate.json
node viewer/scripts/build-index.mjs
```

- `oracle_session` runs the scripted greedy-disagreement oracle (12-form library, no language model) against the real `world.server.WorldServer` in-process, on dev seeds only, and writes a SPEC 5.5 ledger and a 5.7 `metrics.json`.
- `calibration_aggregate` converts calibration stage B (scripted random against greedy, dev seeds 1000 to 1019) into `aggregate.json`. Neither policy is the agent lab, so the lab and single-agent rows read "not run".

## Run locally

```
cd viewer/public && python3 -m http.server 8000      # http://localhost:8000
```

## Deploy

`viewer/vercel.json` builds the run index (`node scripts/build-index.mjs`) and serves `public/`. From `viewer/`, run `npx vercel deploy --prod`, or `npx vercel deploy --temporary` without a Vercel login.
