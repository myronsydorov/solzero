"""Scripted oracle session that writes a SPEC 5.5 ledger, for building the video renderer
before real agent ledgers exist. No language model.

    .venv/bin/python -m sim.oracle_ledger --seed 1001 --out runs/oracle_1001 [--runs-dir runs]

Policy: the calibration greedy policy (calibration.study.stage_b, "greedy"). The live law set
is the top three library forms by BIC; it is empty in cycle 1 (BIC on shot zero alone is
meaningless) and filled from the fits after experiment 1 onwards. Experiments 1-3 are
GREEDY_SEED_DESIGN; later ones are the argmax summed pairwise disagreement of the live laws
over N_CANDIDATES random specs. The candidate pool for experiment i+1 is drawn before
experiment i runs, so the tentative follow-up (argmax under the current fits) is fixed before
the result is seen; the actual choice is the argmax over the same pool after refitting.

The session runs in-process against world.server.WorldServer with condition "lab", so the
server enforces pre-registration and writes <runs-dir>/<session_id>/world_session.json.
"""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse
import json
import math
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from calibration.library import is_ordinary, library
from calibration.study import GREEDY_SEED_DESIGN, N_CANDIDATES, TOP_K, bic, fit_all, random_spec
from schemas import (
    CommitRequest, ExperimentRequest, LawsRequest, LedgerEntry, NominateRequest, PredictionsRequest,
    SessionRequest, Shot, Verdict,
)
from tools import disagreement_many, plan_shot, predict
from tools.defaults import RANGES
from world.generator import DEV_SEEDS, make_world
from world.server import BUDGET, WorldServer, world_id_for

N_LISTED = 5  # candidates written to the ledger: the chosen one plus the next best


def clean(x):
    """JSON-safe copy: NaN and infinities become None."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    return x


class Ledger:
    def __init__(self, path: Path):
        self.f = path.open("w")

    def write(self, cycle: int, agent: str, kind: str, payload: dict) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = LedgerEntry(ts=ts, cycle=cycle, agent=agent, kind=kind, payload=clean(payload))
        self.f.write(json.dumps(e.model_dump(mode="json"), allow_nan=False) + "\n")
        self.f.flush()


def scored(specs, live, laws, fits):
    """(spec, summed pairwise gap between the live laws) for each spec, in input order."""
    dis = disagreement_many(specs, [(laws[f], fits[f]) for f in live])
    return [(s, float(sum(p.gap_sigma for p in d.pairs))) for s, d in zip(specs, dis)]


def ranked(specs, live, laws, fits):
    """scored() sorted by gap, best first; ties keep the pool order (as np.argmax)."""
    return sorted(scored(specs, live, laws, fits), key=lambda x: -x[1])


def verdict(law_id, pred, r) -> Verdict:
    if r.status != "ok":
        return Verdict(law_id=law_id, experiment_id=r.experiment_id, z=0.0,
                       verdict="insufficient_evidence", note="experiment failed")
    best = None  # (|z|, z, note)
    for name, est in pred.observables.items():
        obs = r.observables[name]
        if not math.isfinite(est.mean):
            z, note = 999.0, f"{name}: law predicts no landing, obs {obs:.3f}"
        else:
            z = (obs - est.mean) / max(est.sd, 1e-12)
            note = f"{name}: pred {est.mean:.3f}±{est.sd:.3f}, obs {obs:.3f}"
        if best is None or abs(z) > best[0]:
            best = (abs(z), z, note)
    _, z, note = best
    v = "rejected" if abs(z) > 3 else "supported" if abs(z) <= 2 else "insufficient_evidence"
    return Verdict(law_id=law_id, experiment_id=r.experiment_id, z=float(z), verdict=v, note=note)


def run(seed: int, out: Path, runs_dir: Path, budget: int = BUDGET) -> dict:
    if seed not in DEV_SEEDS:
        raise SystemExit(f"the oracle ledger runs on dev seeds 1000-1999 only; refused {seed}")
    t0 = time.perf_counter()
    out.mkdir(parents=True, exist_ok=True)
    ws = WorldServer([seed], runs_dir=runs_dir)
    wid = world_id_for(seed)
    info = ws.create_session(SessionRequest(world_id=wid, condition="lab"))
    sid = info.session_id
    (out / "session_info.json").write_text(json.dumps(clean(info.model_dump(mode="json")), indent=1))
    ledger = Ledger(out / "ledger.jsonl")

    laws = library()
    rng = np.random.default_rng([seed, 30])
    results = [make_world(seed).shot_zero_result]  # same data stage_b starts from
    fits = fit_all(laws, results)
    live: list[str] = []  # empty until experiment 1 is in
    pools: dict[int, list] = {}  # experiment index -> candidate pool drawn in advance
    tentative = None
    n_seed = len(GREEDY_SEED_DESIGN)

    def best_form():
        scores = {f: bic(fits[f], laws[f], results) for f in fits}
        return sorted(scores, key=scores.get)

    def set_live(cycle):
        ws.set_laws(LawsRequest(session_id=sid, live_laws=[laws[f] for f in live]))
        ledger.write(cycle, "theorist", "law_set", {"laws": [laws[f].model_dump() for f in live]})

    for i in range(1, budget + 1):
        set_live(i)
        # Experimentalist: choose this experiment, then fix the tentative follow-up.
        if i <= n_seed:
            rank = scored(GREEDY_SEED_DESIGN[i - 1:], live, laws, fits)  # chosen + the rest of the design
        else:
            rank = ranked(pools.pop(i), live, laws, fits)
        spec = rank[0][0]
        shown = rank[:N_LISTED]
        ledger.write(i, "experimentalist", "candidates", {
            "candidates": [s.model_dump() for s, _ in shown], "chosen": spec.model_dump(),
            "gaps": [g for _, g in shown]})
        if i >= 2:
            changed = tentative != spec
            reason = ("fixed seed design" if i <= n_seed else
                      "plan changed by evidence: the refit after the last result moved the disagreement peak"
                      if changed else "tentative plan confirmed after refit")
            ledger.write(i, "experimentalist", "decision_diff", {
                "tentative": tentative.model_dump() if tentative else None, "actual": spec.model_dump(),
                "changed": changed, "reason": reason})
        if i + 1 > budget:
            tentative = None
        elif i + 1 <= n_seed:
            tentative = GREEDY_SEED_DESIGN[i]
        else:
            pools[i + 1] = [random_spec(rng) for _ in range(N_CANDIDATES)]
            tentative = ranked(pools[i + 1], live, laws, fits)[0][0]
        preds = [predict(laws[f], fits[f], spec, n_draws=200, seed=i) for f in live]
        table = ws.predictions(PredictionsRequest(session_id=sid, spec=spec, predictions=preds,
                                                  tentative_followup=tentative))
        ledger.write(i, "experimentalist", "prediction_table", {
            "session_id": sid, "spec": spec.model_dump(), "predictions": [p.model_dump() for p in preds],
            "tentative_followup": tentative.model_dump() if tentative else None,
            "prediction_table_id": table.prediction_table_id})
        # Operator.
        r = ws.experiment(ExperimentRequest(session_id=sid, spec=spec,
                                            prediction_table_id=table.prediction_table_id))
        ledger.write(i, "operator", "result", r.model_dump())
        # Analyst: verdicts on the pre-registered predictions, then refit and nominate.
        ledger.write(i, "analyst", "verdicts", {
            "verdicts": [verdict(p.law_id, p, r).model_dump() for p in preds]})
        results.append(r)
        fits = fit_all(laws, results, warm=fits)
        order = best_form()
        top = order[0]
        ws.nominate(NominateRequest(session_id=sid, law=laws[top], fit=fits[top]))
        ledger.write(i, "analyst", "nomination", {
            "session_id": sid, "law": laws[top].model_dump(), "fit": fits[top].model_dump()})
        live = order[:TOP_K]

    # PI: final law set, five shots with the best-BIC form, automatic approval.
    c = budget + 1
    set_live(c)
    sel = best_form()[0]
    plans = [plan_shot(laws[sel], fits[sel], t, "mission_300", RANGES["mission"], seed=k)
             for k, t in enumerate(info.targets)]
    claims_non_ordinary = not is_ordinary(sel)
    ws.commit(CommitRequest(
        session_id=sid, law_id=sel, claim="law_identified", claims_non_ordinary=claims_non_ordinary,
        shots=[Shot(target_id=p.target_id, speed_mps=p.speed_mps, elevation_deg=p.elevation_deg)
               for p in plans]))
    ledger.write(c, "pi", "commit", {
        "session_id": sid, "law_id": sel, "law": laws[sel].model_dump(), "fit": fits[sel].model_dump(),
        "shots": [p.model_dump() for p in plans], "claim": "law_identified",
        "claims_non_ordinary": claims_non_ordinary, "approval": "auto"})
    ledger.f.close()

    shutil.copy(runs_dir / sid / "world_session.json", out / "world_session.json")
    summary = {"session_id": sid, "seed": seed, "world_id": wid, "policy": "scripted_oracle",
               "selected_form": sel, "status": "committed", "n_experiments": budget,
               "wall_s": time.perf_counter() - t0}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--runs-dir", type=Path, default=Path("runs"))
    ap.add_argument("--budget", type=int, default=BUDGET, help="experiments to run (tests only)")
    args = ap.parse_args()
    print(json.dumps(run(args.seed, args.out, args.runs_dir, args.budget)))


if __name__ == "__main__":
    main()
