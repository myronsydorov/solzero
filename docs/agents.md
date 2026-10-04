# Scientific agents and execution

`python -m lab.run --world <opaque-id> --condition lab` runs the five roles
with four model-backed roles through open-source Omnigent 0.16.0 and its Claude SDK executor, plus a deterministic Operator. Set
`SOLZERO_WORLD_URL` to the server origin first. Python 3.12 is required.

| Role | Decision and input | Required output and tools |
| --- | --- | --- |
| Theorist | Propose, revise, retain or retire laws from public results and verdicts | Complete `/laws` replacement and fits from `fit_law` |
| Experimentalist | Choose the next measurement from live fits, coverage and allowed settings | At least two distinct candidates, disagreement, prediction per live law, chosen spec and tentative follow-up |
| Operator | Deterministically execute the durable registered settings; no model call | One `/experiment` result; no settings selection |
| Analyst | Assess prior predictions and confounds | Coverage, per-law verdict and signed z-score, refit and `/nominate` |
| PI | Stop or continue based on evidence, coverage and extrapolation | `plan_shot` for each target, then a five-shot `/commit` |

Model-role prompts are versioned in `lab/agents/*.yaml`; the Operator YAML is retained, but the current runner bypasses it. Prompts state ownership, inputs,
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

- `lab`: five roles (four model-backed, one deterministic) in the sequence above, with Theorist revision after
  each assessment and PI review after each cycle.
- `random`: the same Theorist, Analyst, Operator and PI; a host-prepared schedule
  from lane A's `eval.sampler.random_specs` chooses experiments in supplied order.
  Supply `--specs <public-json>` with exactly one spec per budget unit.
  Scientific code never imports the host sampler.
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

Standalone exit codes: 0 committed; 2 invalid CLI; 3 awaiting interactive approval;
4 runtime failure; 5 requested cycle cap reached before commitment. Interrupted
processes use the OS signal status and retain checkpoints. The summary includes
status, stop reason, budget, tools, elapsed seconds, token counts and ledger path.
Pre-session CLI/connection failures may not have a summary; the batch host records
that fact and the process exit status.

Output is a fresh explicit directory, or `runs/session-<timestamp>/` by default:

- `<session_id>/ledger.jsonl`: every SPEC 5.5 record, UTC timestamps and fsync.
- `tools.jsonl`: inputs, outputs and actual policy decisions.
- `conversation.jsonl`: model text, model identity and SDK-reported usage per model-backed turn.
- `model-calls.jsonl`: observed provider responses and usage; interrupted responses can leave accounting incomplete.
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
trigger automatic experiment retries. Successful measurements and commits are memoized within
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

## Demonstration provenance

Submitted session `s_dce1ebea1d` is a historical standalone development baseline:
10 experiments, PI-selected early stop, five server-graded mission hits, and a
`predictive_only` claim. **All five roles, including Operator, made model calls in
that run.** It took about 17.1 active minutes; one interrupted response makes its
token accounting incomplete. Evidence is in `docs/evidence/lab-dev1000`.

The current deterministic Operator reuses the registered spec and the same
policy, checkpoint and HTTP dispatch path, without asking a model to restate it.
That optimization is implemented, but the submitted baseline does not validate
its runtime or scientific outcomes. A subsequent optimized run stopped after two
experiments at a provider session limit. No completed matched comparison or
held-out-world result is claimed.

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

## Eval adapter (SPEC 5.6)

```sh
python -m lab.run --world-url http://127.0.0.1:8003 \
  --session-info session_info.json --condition lab --out runs/agent \
  --max-tokens 2000000 --max-wall-s 600 --approval auto
```

This mode adopts the supplied public session without calling `/session`.
It writes `ledger.jsonl` directly in the output directory and `summary.json`
with `session_id`, `status`, `tokens_used`, `wall_s`, `n_experiments`, and `error`.
Statuses are `committed`, `cap_reached`, `error`, or `pending_approval`.
Exit zero means that summary was written; setup/crash failures are nonzero.
Automatic commits include `approval: auto` in their ledger payload.
Random input is the exact ordered public list produced by the host sampler.

Remaining contract gap: the Claude executor exposes cumulative usage at turn
completion, so `--max-tokens` is currently checked between turns, not a hard
provider-side cap. Do not use this implementation for a claimed equal-hard-token
comparison. The wall deadline cancels further agent work, but an already running
HTTP or numerical worker may finish during cleanup; its mutation intent remains
on disk to prevent replay. No extra model turn is started to manufacture a final
mission after a cap. Both limitations are reported to the integrator in STATUS.
