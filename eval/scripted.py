"""Scripted (no language model) agents that play a session over the HTTP API.

- textbook: no experiments; Earth physics plans the five shots (the floor).
- oracle: three fixed seed experiments, then greedy maximum disagreement among the best
  three of the 12-form library (the ceiling, SPEC section 7).
- scripted_random: the shared sampler's experiments with the same library fitting.

All three reuse calibration/ (library, BIC selection, seed design) and tools/.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from calibration.library import library
from calibration.study import GREEDY_SEED_DESIGN, N_CANDIDATES, TOP_K, bic, fit_all
from schemas import Commit, CommitRequest, LedgerEntry, Result, SessionInfo, Shot
from tools.analysis import disagreement_many, plan_shot
from world.generator import textbook_law

from .client import WorldClient
from .lawform import claim_for
from .sampler import SAMPLER_SEED, random_spec, random_specs


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, cycle: int, agent: str, kind: str, payload: dict) -> None:
        e = LedgerEntry(ts=datetime.now(timezone.utc).isoformat(timespec="seconds"), cycle=cycle,
                        agent=agent, kind=kind, payload=payload)
        with self.path.open("a") as f:
            f.write(e.model_dump_json() + "\n")


def shot_zero_result(info: SessionInfo) -> Result:
    """Shot zero as a datum: landing distance only, as the server reports it."""
    sz = info.shot_zero
    noise = {k: info.noise_sd[k] for k in ("landing_x_m", "speed_frac", "elevation_deg") if k in info.noise_sd}
    return Result(experiment_id="shot0", index=0, spec=sz.spec, observables={"landing_x_m": sz.landing_x_m},
                  noise_sd=noise, status="ok", budget_left=info.budget)


def plan_and_commit(client: WorldClient, info: SessionInfo, law, fit, claim: str, non_ordinary: bool,
                    ledger: Ledger, cycle: int) -> dict:
    shots = []
    for t in info.targets:
        p = plan_shot(law, fit, t, "mission_300", info.ranges["mission"], samples=info.samples, seed=0)
        shots.append(Shot(target_id=t.target_id, speed_mps=p.speed_mps, elevation_deg=p.elevation_deg))
    commit = Commit(law_id=law.law_id, claim=claim, claims_non_ordinary=non_ordinary, shots=shots)
    client.commit(CommitRequest(session_id=info.session_id, **commit.model_dump()))
    ledger.write(cycle, "pi", "commit", {**commit.model_dump(), "approval": "scripted"})
    return commit.model_dump()


def run_textbook(client: WorldClient, info: SessionInfo, out: Path, **_) -> dict:
    ledger = Ledger(out / "ledger.jsonl")
    law, fit = textbook_law()
    client.nominate(info.session_id, law, fit)
    ledger.write(0, "analyst", "nomination", {"law": law.model_dump(), "fit": fit.model_dump()})
    plan_and_commit(client, info, law, fit, "predictive_only", False, ledger, 0)
    return {"n_experiments": 0}


def run_library_policy(client: WorldClient, info: SessionInfo, out: Path, *, policy: str, world_seed: int,
                       specs: list | None = None) -> dict:
    """oracle (greedy disagreement) or scripted_random (fixed spec list), with BIC selection."""
    ledger = Ledger(out / "ledger.jsonl")
    laws = library()
    results = [shot_zero_result(info)]
    fits = fit_all(laws, results)
    rng = np.random.default_rng([SAMPLER_SEED, world_seed, 1])
    if policy == "scripted_random":
        specs = specs or random_specs(world_seed, info.budget)

    def best():
        scores = {fid: bic(f, laws[fid], results) for fid, f in fits.items()}
        return min(scores, key=scores.get), scores

    sel, scores = best()
    client.nominate(info.session_id, laws[sel], fits[sel])
    for i in range(1, info.budget + 1):
        if policy == "scripted_random":
            spec = specs[i - 1]
        elif i <= len(GREEDY_SEED_DESIGN):
            spec = GREEDY_SEED_DESIGN[i - 1]
        else:
            top = sorted(scores, key=scores.get)[:TOP_K]
            cands = [random_spec(rng) for _ in range(N_CANDIDATES)]
            dis = disagreement_many(cands, [(laws[f], fits[f]) for f in top], samples=info.samples)
            spec = cands[int(np.argmax([sum(p.gap_sigma for p in d.pairs) for d in dis]))]
            ledger.write(i, "experimentalist", "candidates", {"top_forms": top, "chosen": spec.model_dump()})
        r = client.experiment(info.session_id, spec)
        results.append(r)
        ledger.write(i, "operator", "result", r.model_dump())
        fits = fit_all(laws, results, warm=fits)
        sel, scores = best()
        client.nominate(info.session_id, laws[sel], fits[sel])
        ledger.write(i, "analyst", "nomination", {"law_id": sel, "bic": scores[sel]})
    # SPEC 7: scripted references claim non-ordinary physics by the 2-sd grading rule.
    plan_and_commit(client, info, laws[sel], fits[sel], "law_identified", claim_for(laws[sel], fits[sel]),
                    ledger, info.budget)
    return {"n_experiments": len(results) - 1, "final_form": sel}


def main():
    """Same command line as the agent CLI (SPEC 5.6), plus --kind and --rng-seed."""
    import argparse

    from schemas import parse_spec

    ap = argparse.ArgumentParser()
    ap.add_argument("--world-url", required=True)
    ap.add_argument("--session-info", required=True)
    ap.add_argument("--condition", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--specs")
    ap.add_argument("--max-tokens", type=int)
    ap.add_argument("--max-wall-s", type=float)
    ap.add_argument("--approval", default="auto")
    ap.add_argument("--kind", required=True, choices=["textbook", "oracle", "scripted_random"])
    ap.add_argument("--rng-seed", type=int, required=True, help="world seed, for the candidate pool and sampler")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    info = SessionInfo.model_validate_json(Path(args.session_info).read_text())
    client = WorldClient(args.world_url)
    t0 = time.perf_counter()
    try:
        if args.kind == "textbook":
            extra = run_textbook(client, info, out)
        else:
            specs = [parse_spec(d) for d in json.loads(Path(args.specs).read_text())] if args.specs else None
            extra = run_library_policy(client, info, out, policy=args.kind, world_seed=args.rng_seed, specs=specs)
        status, err = "committed", None
    except Exception as exc:  # recorded, not raised: summary.json must be written (SPEC 5.6)
        extra, status, err = {}, "error", f"{type(exc).__name__}: {exc}"
    summary = {"session_id": info.session_id, "status": status, "tokens_used": 0,
               "wall_s": time.perf_counter() - t0, "n_experiments": extra.get("n_experiments"),
               "error": err, "agent": f"scripted:{args.kind}", **extra}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
