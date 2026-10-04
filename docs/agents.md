# Scientific agents and execution

`python -m lab.run --world <opaque-id> --condition lab` runs the five roles
through open-source Omnigent 0.16.0 and its Claude SDK executor. Set
`SOLZERO_WORLD_URL` to the server origin first. Python 3.12 is required.

| Role | Decision and input | Required output and tools |
| --- | --- | --- |
| Theorist | Propose, revise, retain or retire laws from public results and verdicts | Complete `/laws` replacement and fits from `fit_law` |
| Experimentalist | Choose the next measurement from live fits, coverage and allowed settings | At least two distinct candidates, disagreement, prediction per live law, chosen spec and tentative follow-up |
| Operator | Execute the registered settings faithfully | One `/experiment` result; no settings selection |
| Analyst | Assess prior predictions and confounds | Coverage, per-law verdict and signed z-score, refit and `/nominate` |
| PI | Stop or continue based on evidence, coverage and extrapolation | `plan_shot` for each target, then a five-shot `/commit` |

Prompts are versioned in `lab/agents/*.yaml`. They state ownership, inputs,
output schemas and that insufficient evidence is valid. They provide no hidden
law menu, family names or parameter bounds. Public instrument limits come from
the session. Every domain record comes from `schemas/`; all conditions share the
same numerical analysis functions in `tools/`.

## Conditions and command contract

```sh
SOLZERO_WORLD_URL=http://127.0.0.1:8003 .venv/bin/python -m lab.run \
  --world <opaque-id> --condition lab --seed 1000 --auto-approve \
  --output runs/example
```

- `lab`: five specialists in the sequence above, with Theorist revision after
  each assessment and PI review after each cycle.
- `random`: the same Theorist, Analyst, Operator and PI; a host-prepared schedule
  from lane A's `calibration.study.random_spec` chooses experiments. Supply
  `--schedule <public-json>`. Host-only `eval.dev_batch` prepares this input.
  Scientific code never imports calibration. The sampler should move into
  `tools/` for direct reuse; this is requested in STATUS.md.
- `single`: one agent with all scientific tools owns the complete cycle. It has
  the same experiment limit, numerical tools, model and session token ceiling.

Flags: `--world-id` aliases `--world`; `--model sonnet` is the default;
`--analyst-model` optionally changes only the specialist Analyst. `--cycles`
caps evidence cycles at twelve. `--min-experiments 12` is an explicit fixed-budget
acceptance/comparison constraint; the default zero lets the PI stop early.
An early PI stop is recorded as `pi_chose_commit`, not as budget exhaustion.
`--seed` accepts development seeds only. No held-out-seed mode is implemented.

`--auto-approve` is enabled for all host-launched dev/batch runs per the user's
standing authorization. Without it, a safe commit saves `pending-commit.json`
and exits before firing: this is the default interactive demo behavior.
Unsafe settings and incomplete tables are denied even with automatic approval.

Exit codes: 0 committed; 2 invalid CLI; 3 awaiting interactive approval;
4 runtime failure; 5 requested cycle cap reached before commitment. Interrupted
processes use the OS signal status and retain checkpoints. The summary includes
status, stop reason, budget, tools, elapsed seconds, token counts and ledger path.
Pre-session CLI/connection failures may not have a summary; the batch host records
that fact and the process exit status.

Output is a fresh explicit directory, or `runs/session-<timestamp>/` by default:

- `<session_id>/ledger.jsonl`: every SPEC 5.5 record, UTC timestamps and fsync.
- `tools.jsonl`: inputs, outputs and actual policy decisions.
- `conversation.jsonl`: model text, model identity and SDK-reported usage per turn.
- `checkpoint.json`: host resume state, phase, successful-call memo and outstanding mutation.
- `summary.json`: outcome, timing, usage, ledger record counts and decision changes.
- `mlflow.db` plus MLflow artifacts: nested agent/tool spans, configurable with
  `--tracking-uri`; `--no-tracing` is an explicit diagnostic escape hatch.

The default session ceiling is 2,000,000 tokens including cache reads and writes,
checked between model turns. A turn can overshoot that ceiling; reports preserve
actual usage. Responses are limited to 4096 generated tokens, SDK turns to twenty
model calls, and each role turn to 32 tool calls. All conditions use these same
limits. Token usage is measured, not inferred from character count. Numerical
seeds are explicit; the model provider does not offer deterministic sampling in
this harness. Raw decisions are retained.

## Isolation, retries and recovery

The model runs in an empty temporary directory with no OS tools, host skills or
project settings. Its only interface is its assigned Python function tools.
The allowlist denies filesystem, shell and admin access, including access to
world, calibration, evaluation and simulation code. Only the Operator receives
measurement tools in specialist conditions; the single condition intentionally
receives all scientific tools. Admin truth and scores stay with the host.

The server owns the budget. Lab policies mirror the budget and enforce safe
settings; pre-registration must match every live law and the actual experiment.
The host persists a mutation intent before dispatch. Failed measurements consume
budget; transport ambiguity stops mutation instead of silently replaying it.
Only connection establishment failures retry automatically, at most twice after
the initial attempt. Read timeouts, bad response bodies and server errors never
trigger automatic experiment retries. Successful tool calls are memoized within
a phase so resuming that phase does not repeat an acknowledged measurement.

Use the same command and output with `--resume` against the original live server.
An exclusive runner lock prevents two processes from resuming one session.
Checkpoint resume preserves results, budget, fits, pending table and phase.
An ambiguous in-flight mutation requires host reconciliation; arbitrary server
restart recovery needs public snapshot/idempotency support from lane A. This
limitation is explicit and is not hidden behind a fresh session.

`eval.dev_batch` runs up to sixteen isolated processes with separate sessions,
checkpoints and traces. Its manifest contains the same eight dev worlds for all
conditions; family selection and scores are host-only artifacts. It automatically
approves dev commits and reports failures as failures, without dropping worlds.

## Verification and provenance

Run `.venv/bin/python -m pytest -q`. Tests cover API budgets/pre-registration,
shared schema use, role/envelope gates, leak scanning (including generated tool
schemas), durable ledgers, safe retries, checkpoint restoration and MLflow spans.
See STATUS.md and `runs/l1-l5/` for exact commands, observed results, limitations
and before/after records of orchestration fixes. A freeze candidate is a review
checkpoint, not authorization to run held-out seeds.

The alternative Codex transport was checked against the [official app-server
connection guidance](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server).
A successful authenticated probe confirmed access to GPT-5.5, but the tool
inventory included native filesystem and connected-app tools beyond the lab
allowlist. It was not adopted for scientific runs. Claude session-limit failures
are preserved in the raw reports; switching a provider is not silently mixed
into a paired comparison.
