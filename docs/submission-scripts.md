# Submission video scripts

## Evidence and production notes

Primary run: **`s_dce1ebea1d`, development world 1000, condition `lab`**. This is a completed standalone lab run, not a completed comparative evaluation. The PI chose to commit after **10 of its 12 available experiments**. The server recorded **five hits from five mission shots**, including the three designated beyond-range targets. The final claim was `predictive_only`, with `claims_non_ordinary=false`.

Use the immutable baseline replay and manifest prepared for submission. All robot footage is **rendered simulation replay of recorded decisions**, not a live physical robot or live agent execution. Keep “DEVELOPMENT SIMULATION · RECORDED RUN” visible throughout. The replay compresses time: recorded active runtime was **1,028.8 seconds, about 17.1 minutes**, with pauses and resumes. Observed usage is incomplete and must not be described as an exact token total.

Evidence sources (all in this repository, under [`docs/evidence/lab-dev1000/`](evidence/lab-dev1000)):

- Ledger: [`ledger.jsonl`](evidence/lab-dev1000/ledger.jsonl).
- Summary, token usage and limitations: [`measurement.json`](evidence/lab-dev1000/measurement.json) and [`manifest.json`](evidence/lab-dev1000/manifest.json) (with SHA-256 hashes of each file).
- Persisted server grade: [`score.json`](evidence/lab-dev1000/score.json).
- Each target was a hit. Miss distances: **t1 15.18 mm; t2 0.40 mm; t3 4.63 mm; t4 16.93 mm; t5 7.33 mm**. Preserve server grades when rendering; do not substitute freshly simulated mission outcomes.

An older approved run, `s_40ebae9470`, was kept as a fallback only and is not part of this submission. It stopped after five experiments and also hit five targets. Its footage and firing table are not used in the videos.

## Demo narration — 120-second master storyboard (the submission cut is in `submission-scripts-60s.md`)

Record approximately 250 words at a measured pace; use the scene boundaries below and hold the final evidence card to reach exactly 2:00. All captions refer to the same baseline session.

| Time | Scene | Narration | On-screen callout |
| --- | --- | --- | --- |
| 0–15 s | Shot-zero opener; title over the lab | “What if your robot's physics model was wrong? Sol Zero gives an AI lab twelve experiments to learn from measurements, then asks it to hit five untouched targets. This is a development simulation.” | **Sol Zero · Learn, test, launch** |
| 15–32 s | Role cards over experiment replay | “In the recorded run, five specialist agents shared the work: proposing laws, choosing experiments, operating the lab, judging evidence, and deciding when to stop. Each role had a specific decision and a restricted set of tools.” | **Five roles · explicit responsibilities** |
| 32–48 s | Actual prediction table, then its measurement | “Before each experiment, the lab recorded a prediction for every live law. Then the Operator executed the chosen settings. A server enforced the budget, so the agents could not simply keep trying until they succeeded.” | **Predict first · measure once** |
| 48–65 s | Readable ledger and decision-diff record | “The important artifact is the evidence trail. We can inspect what was expected, what happened, and whether the next choice changed. This run recorded six changed decisions and completed ten experiments before the PI chose to commit.” | **10 / 12 experiments · 6 changed decisions** |
| 65–85 s | Mission sequence, with server target grades | “The five-shot firing table was committed once. All five targets were hits, including three beyond the experimental launch range. These grades come from the recorded server session; the animation replays that outcome.” | **5 / 5 hits · server graded** |
| 85–102 s | Final predictive-only claim and evidence folder | “The lab's final claim was predictive only. We are not claiming that it discovered new physics. The ledger, firing table, and server grades are included with the project so the result can be checked.” | **Predictive-only claim · inspect the evidence** |
| 102–120 s | End card; repository, runtime and limitations | “This is one completed standalone development run. It took about seventeen active minutes, compressed here into a replay. It does not prove that multiple agents outperform other methods. What it demonstrates is a working, auditable loop from prediction through experiment to action.” | **Recorded simulation · comparative validation pending** |

## Technical narration — 120-second master (the submission cut is in `submission-scripts-60s.md`)

| Time | Scene | Narration | On-screen callout |
| --- | --- | --- | --- |
| 0–15 s | Architecture diagram: agents → public API → world; separate host score path | “Sol Zero separates the scientific agents from the world implementation. The agents receive public measurements through a fixed HTTP interface. They cannot inspect generator code or call the admin scoring endpoints.” | **Public observations only** |
| 15–35 s | Five agent cards and the evidence loop | “The system has five roles. Four use Omnigent model calls: Theorist, Experimentalist, Analyst, and PI. The current Operator deterministically executes registered settings. The historical baseline shown here used a model-backed Operator. Its outcomes remain separate from subsequent implementation changes.” | **Explicit decisions and tool permissions** |
| 35–52 s | A prediction-table record beside its matching result | “Shared schemas connect the tools and server. Pre-registration covers every live law. The server enforces the experiment budget, and the client mirrors it. A failed measurement still costs an experiment. Insufficient evidence is a valid outcome.” | **Pre-register · measure · account** |
| 52–70 s | Ledger entries and checkpoint view | “The ledger preserves laws, candidates, predictions, results, verdicts, nominations and the final commit. Decision-diff records show whether the next experiment changed from the tentative plan. Checkpoints support resumption; an ambiguous experiment response is never blindly retried.” | **Durable records · no blind experiment replay** |
| 70–88 s | Shot-planning output and committed firing table | “The agents use shared fitting, prediction and shot-planning tools. Mission settings are checked against the safe envelope. Development runs can use explicit automatic approval; interactive demonstrations retain a human approval gate.” | **Same analysis tools · checked firing table** |
| 88–106 s | Baseline mission replay, then five target grades | “Our completed standalone baseline used ten experiments before the PI chose to stop. Its recorded mission hit all five targets. The server grades shown here come from that same session, not a new simulation of the shots.” | **Session s_dce1ebea1d · 10 experiments · 5 hits** |
| 106–120 s | Evidence links and limitations end card | “This video is a compressed simulation replay. The run took about seventeen active minutes. It is development evidence, not held-out validation or a claim that multiple agents beat a single agent or random experiments.” | **Dev evidence · comparison still pending** |

## Claim checks before export

- Keep the session ID, experiment count and server grades consistent across replay, captions and repository evidence.
- Do not say “discovered new physics”: this run made a predictive-only claim and did not claim non-ordinary physics.
- Do not say the run completed in one minute or met a ten-minute runtime target. Video duration is presentation time.
- Do not claim a physical robot demonstration, test-world success, or comparative superiority.
- If the optional comparison clip uses a rendered textbook trajectory, label that side **textbook simulation**, rather than implying a second recorded mission trial.
- Both recorded runs predate the deterministic Operator optimization. The baseline used model calls for all five roles. Current code uses four model-backed roles plus deterministic execution; do not attribute baseline results to that newer configuration.
