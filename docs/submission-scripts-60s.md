# Submission video scripts: 60-second cuts

| File | Duration (ffprobe) | Narration |
| --- | --- | --- |
| `docs/media/demo_60s.mp4` | **57.000 s** | 125 words, synthetic voice, burned-in captions |
| `docs/media/technical_60s.mp4` | **57.200 s** | 124 words, synthetic voice, burned-in captions |

Both are 1920×1080, 30 fps, H.264 with AAC stereo at 48 kHz. Matching subtitle files are `demo_60s.srt` and `technical_60s.srt`. The 120-second masters (`demo.mp4`, `technical.mp4`) and `docs/submission-scripts.md` are unchanged.

## How the cuts were made

- Each cut is assembled from scenes of the 120-second master. Every scene is trimmed, and some are sped up uniformly, so it fits its narration line. The speed for each scene is in the tables below.
- No scene, number or grade was re-rendered or re-simulated. All footage is the existing replay of session `s_dce1ebea1d` on development world 1000. The **DEVELOPMENT SIMULATION · RECORDED RUN** footer and the session ID stay visible in every frame.
- The narration was generated locally with Kokoro TTS (`kokoro-onnx`, voice `am_michael`, speed 1.08) and normalised to −16 LUFS.
- The technical cut leaves out master scene 05 (policy and recovery). The "TECHNICAL / 0n" counter is covered so the skipped number doesn't show.
- To rebuild after editing a line or a cut, run `python docs/media/build_60s.py --vo <dir with d1..d6.wav, t1..t8.wav>`. The script holds the narration text and the cut table.

## Demo (57.0 s)

| Time | Master source (speed) | Narration | What is on screen |
| --- | --- | --- | --- |
| 0:00.0–0:10.1 | 3.0–18.0 s (1.48×) | "Robots assume Earth's physics. Sol Zero drops one into a simulated world with an unknown force law. Its Earth-physics first shot misses." | Title card; shot zero aimed with Earth's physics; miss marker |
| 0:10.1–0:18.1 | 21.0–33.0 s (1.50×) | "So an AI lab of specialist agents takes over. It gets twelve experiments, and it chooses every one itself." | Five roles; experiments 1–2 with budget bar |
| 0:18.1–0:30.0 | 42.0–56.0 s (1.18×) | "Before each measurement, it pre-registers predictions for every rival law. One predicted two point three six metres. The robot measured zero point five six. Rejected." | Experiment 2 pre-registered table (2.361 m), then measured 0.563 m with the law crossed out |
| 0:30.0–0:37.2 | 65.7–80.0 s (2.01×) | "Evidence changed the plan six times. After ten experiments, the lab decided it knew enough, and committed." | "PLAN CHANGED BY EVIDENCE" records, experiments 5–10 |
| 0:37.2–0:48.1 | 82.0–100.0 s (1.65×) | "One firing table. Five untouched targets, one shot each. The server graded five hits out of five, including three beyond the range it ever tested." | Five mission shots, server-graded target panel, final claim card |
| 0:48.1–0:57.0 | 100.0–108.9 s (1.00×) | "Every prediction, measurement and decision is in an auditable ledger. One recorded development run, replayed in simulation." | Predictive-only claim, 17.1 active minutes, comparison pending |

## Technical walkthrough (57.2 s)

| Time | Master source (speed) | Narration | What is on screen |
| --- | --- | --- | --- |
| 0:00.0–0:08.9 | 0.0–8.9 s (1.00×) | "The world stays hidden. Omnigent agents reach it only through a public HTTP API, and scoring stays with the host." | "Agents see measurements, not the world" |
| 0:08.9–0:21.8 | 15.0–30.0 s (1.16×) | "Each role owns one decision. The Theorist fits laws, the Experimentalist ranks tests, the Operator executes, the Analyst judges, and the principal investigator decides when to stop." | Role handoffs over experiments 2–3 |
| 0:21.8–0:28.9 | 30.0–37.1 s (1.00×) | "Predictions must cover every live law before a run. The server enforces the twelve-experiment budget." | "The server enforces the contract" |
| 0:28.9–0:37.8 | 45.0–60.0 s (1.68×) | "Each verdict feeds the next choice of experiment. Laws, predictions, results, verdicts and decision diffs land in a durable ledger." | "Learning is visible in the ledger" over experiments 4–6 |
| 0:37.8–0:44.9 | 75.0–82.1 s (1.00×) | "Today's Operator runs deterministically, with no model turn. The recorded baseline predates that." | "Current Operator uses no model turn" |
| 0:44.9–0:51.9 | 90.5–104.5 s (2.01×) | "Shared planning tools produce one five-shot firing table. The server graded it: five of five hits." | Mission shots with server grades; "PI stopped after 10 / 12 experiments" |
| 0:51.9–0:57.2 | 105.0–110.3 s (1.00×) | "A compressed replay of a development simulation. The evidence is in the repo." | Rendered simulation, not a physical robot; matched evaluation next |

## Recording your own voice instead

To replace the synthetic voice, record each line to start at its row's start time plus 0.25 s, and finish before the row ends. Each row allows at least 0.45 s more than the synthetic read took. Then either:

- save the lines as `d1.wav`…`d6.wav` and `t1.wav`, `t2.wav`, `t3.wav`, `t4.wav`, `t6.wav`, `t7.wav`, `t8.wav` (16-bit PCM) and rerun `build_60s.py`, which fits each scene to your takes and keeps the total under 60 s if your pace is similar; or
- strip the audio (`ffmpeg -i demo_60s.mp4 -an -c:v copy demo_60s_silent.mp4`) and lay one continuous take over it. The burned-in captions show where each line falls.

Reading pace is about 140 words per minute. Before uploading, check that the total stays under 60 s: `ffprobe -v error -show_entries format=duration -of csv=p=0 <file>`.

## Claim checks (all against `docs/evidence/lab-dev1000/`)

- "Ten experiments": `score.json` has `used: 10` of `budget: 12`.
- "Five of five hits, three beyond the tested range": from the persisted server score shown in the replay's target panel (t1 15.18 mm, t2 0.40 mm, t3 4.63 mm, t4 16.93 mm, t5 7.33 mm). These are not re-simulated.
- "Predicted 2.36 m, measured 0.56 m": experiment 2 in the ledger (`landing_x_m` 0.5627).
- "Changed the plan six times": the six "PLAN CHANGED BY EVIDENCE" decision diffs shown in the master.
- "Unknown force law" means unknown to the agents. The world-1000 claim card in the replay states the hidden law was ordinary gravity with quadratic drag (a control world). Neither cut says the lab discovered new physics; the final claim was `predictive_only`.
- The deterministic Operator is described as today's code. The narration says the recorded baseline predates it.
- Neither cut claims a physical robot, test-world results or comparative superiority. "One recorded development run, replayed in simulation" and the footer carry the disclosure.
