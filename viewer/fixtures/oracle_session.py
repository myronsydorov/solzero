"""Fixture sessions for the replay viewer: the scripted greedy-disagreement oracle (no language
model) run through the real world server code in-process, written as a SPEC 5.5 ledger plus a
SPEC 5.7 metrics.json. Dev seeds only.

    .venv/bin/python -m viewer.fixtures.oracle_session --seeds 1000,1001,1002,1003 --out viewer/public/runs --jobs 4
"""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse
import json
import math
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from calibration.library import is_ordinary, library
from calibration.study import (BUDGET, GREEDY_SEED_DESIGN, N_CANDIDATES, TOP_K, bic, fit_all,
                               parse_seeds, random_spec)
from schemas import (CommitRequest, ExperimentRequest, LawsRequest, NominateRequest,
                     PredictionsRequest, SessionRequest, Shot, Verdict)
from tools.analysis import disagreement_many, plan_shot, predict_many
from tools.defaults import RANGES
from world.server import WorldServer, world_id_for

N_SHOWN = 6  # candidates written to the ledger per cycle
T0 = datetime(2026, 10, 4, tzinfo=timezone.utc)


def _finite(x):
    """Replace non-finite floats with None, recursively."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _finite(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_finite(v) for v in x]
    return x


def _dump(x):
    return json.loads(json.dumps(x, default=float))


class LedgerWriter:
    def __init__(self, path: Path):
        self.path = path
        self.n = 0
        path.write_text("")

    def append(self, cycle: int, agent: str, kind: str, payload) -> None:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(mode="json")
        ts = (T0 + timedelta(seconds=self.n)).isoformat().replace("+00:00", "Z")
        self.n += 1
        entry = {"ts": ts, "cycle": cycle, "agent": agent, "kind": kind, "payload": _finite(_dump(payload))}
        with self.path.open("a") as f:
            f.write(json.dumps(entry, separators=(",", ":"), allow_nan=False) + "\n")


def _rank(pool, live, laws, fits):
    """Pool indices sorted by summed pairwise disagreement among the live laws, and the records."""
    dis = disagreement_many(pool, [(laws[f], fits[f]) for f in live])
    gaps = np.array([sum(p.gap_sigma for p in d.pairs) for d in dis])
    return list(np.argsort(-gaps, kind="stable")), dis


def _verdict(law_id, pred, result) -> Verdict:
    if result.status != "ok":
        return Verdict(law_id=law_id, experiment_id=result.experiment_id, z=0.0,
                       verdict="insufficient_evidence", note="run failed: no observation to compare")
    worst, parts = 0.0, []
    for name, est in pred.observables.items():
        obs = result.observables.get(name)
        if obs is None or not (math.isfinite(est.mean) and math.isfinite(obs)) or est.sd <= 0:
            parts.append(f"{name}: law predicts no landing, one was observed")
            worst = worst if abs(worst) >= 99 else 99.0
            continue
        z = (obs - est.mean) / est.sd
        parts.append(f"{name} predicted {est.mean:.4g}±{est.sd:.2g}, observed {obs:.4g} (z={z:+.2f})")
        if abs(z) > abs(worst):
            worst = z
    v = "rejected" if abs(worst) > 3 else "supported" if abs(worst) <= 2 else "insufficient_evidence"
    return Verdict(law_id=law_id, experiment_id=result.experiment_id, z=float(worst), verdict=v,
                   note="; ".join(parts))


def run(seed: int, out: Path) -> dict:
    t0 = time.perf_counter()
    rng = np.random.default_rng([seed, 30])
    with tempfile.TemporaryDirectory() as tmp:
        ws = WorldServer([seed], runs_dir=Path(tmp))
        info = ws.create_session(SessionRequest(world_id=world_id_for(seed), condition="lab"))
        sid = info.session_id
        sess = ws.sessions[sid]
        w = sess.world
        run_dir = out / f"oracle_{seed}"
        run_dir.mkdir(parents=True, exist_ok=True)
        led = LedgerWriter(run_dir / "ledger.jsonl")

        laws = library()
        results = [w.shot_zero_result]
        fits = fit_all(laws, results)
        live: list[str] = []
        ws.set_laws(LawsRequest(session_id=sid, live_laws=[]))
        led.append(0, "theorist", "law_set", {"laws": []})

        tentative = None
        next_pool = None  # pool drawn at the previous pre-registration
        n_changed = 0
        for i in range(1, BUDGET + 1):
            # --- choose experiment i
            if i <= len(GREEDY_SEED_DESIGN):
                spec = GREEDY_SEED_DESIGN[i - 1]
                cand_payload = {"candidates": [spec.model_dump()], "chosen": spec.model_dump()}
                reason = "fixed seed design: one weigh, one drop, one launch before any disagreement ranking"
            else:
                pool = next_pool
                order, dis = _rank(pool, live, laws, fits)
                spec = pool[order[0]]
                shown = order[:N_SHOWN]
                cand_payload = {"candidates": [pool[k].model_dump() for k in shown],
                                "chosen": spec.model_dump(),
                                "disagreements": [dis[k].model_dump() for k in shown]}
                if tentative is None:
                    reason = "no tentative plan"
                elif spec == tentative:
                    reason = f"unchanged: the max-disagreement candidate is still the tentative one (live {live})"
                elif live != prev_live:
                    reason = f"live set changed from {prev_live} to {live}, which moved the max-disagreement candidate"
                else:
                    reason = f"refit after the last result moved the max-disagreement candidate (live {live})"
            changed = tentative is not None and tentative != spec
            n_changed += changed
            led.append(i, "experimentalist", "candidates", cand_payload)
            led.append(i, "experimentalist", "decision_diff",
                       {"tentative": tentative.model_dump() if tentative else None,
                        "actual": spec.model_dump(), "changed": changed, "reason": reason})

            # --- tentative follow-up for experiment i+1, under current fits
            if i < len(GREEDY_SEED_DESIGN):
                follow = GREEDY_SEED_DESIGN[i]
                next_pool = None
            elif i < BUDGET:
                next_pool = [random_spec(rng) for _ in range(N_CANDIDATES)]
                follow = next_pool[_rank(next_pool, live, laws, fits)[0][0]] if len(live) >= 2 else next_pool[0]
            else:
                follow = None

            # --- pre-register and run
            preds = [predict_many(laws[f], fits[f], [spec], 200, seed=seed * 100 + i)[0] for f in live]
            pt = ws.predictions(PredictionsRequest(session_id=sid, spec=spec, predictions=preds,
                                                   tentative_followup=follow))
            led.append(i, "experimentalist", "prediction_table",
                       {"session_id": sid, "spec": spec.model_dump(), "predictions": [p.model_dump() for p in preds],
                        "tentative_followup": follow.model_dump() if follow else None,
                        "prediction_table_id": pt.prediction_table_id})
            result = ws.experiment(ExperimentRequest(session_id=sid, spec=spec,
                                                     prediction_table_id=pt.prediction_table_id))
            led.append(i, "operator", "result", result)
            results.append(result)

            # --- refit, verdicts, nomination, new live set
            fits = fit_all(laws, results, warm=fits)
            scores = {f: bic(fits[f], laws[f], results) for f in fits}
            top = sorted(scores, key=scores.get)[:TOP_K]
            verdicts = [_verdict(p.law_id, p, result) for p in preds]
            led.append(i, "analyst", "verdicts", {
                "verdicts": [v.model_dump() for v in verdicts],
                "confound_note": ("no live laws were registered for this experiment" if not preds else
                                  f"after refitting all library forms the best by BIC is {top[0]} "
                                  f"(BIC {scores[top[0]]:.1f}; runner-up {top[1]} at {scores[top[1]]:.1f})")})
            best = top[0]
            prev_live = live
            live = top
            ws.set_laws(LawsRequest(session_id=sid, live_laws=[laws[f] for f in live]))
            ws.nominate(NominateRequest(session_id=sid, law=laws[best], fit=fits[best]))
            led.append(i, "analyst", "nomination",
                       {"session_id": sid, "law": laws[best].model_dump(), "fit": fits[best].model_dump()})
            led.append(i, "theorist", "law_set", {"laws": [laws[f].model_dump() for f in live]})
            tentative = follow

        # --- mission
        shots = []
        for t in w.targets:
            p = plan_shot(laws[best], fits[best], t, "mission_300", RANGES["mission"], n_draws=100, seed=seed)
            shots.append(Shot(target_id=t.target_id, speed_mps=p.speed_mps, elevation_deg=p.elevation_deg))
        req = CommitRequest(session_id=sid, law_id=best, shots=shots, claim="law_identified",
                            claims_non_ordinary=not is_ordinary(best))
        ws.commit(req)
        led.append(BUDGET, "pi", "commit", req)

        score = ws.score(sess)
        truth = {**w.truth(), "world_id": world_id_for(seed), "seed": seed,
                 "targets": [t.model_dump() | {"kind": w.target_kind[t.target_id]} for t in w.targets]}
        metrics = {"run_id": f"oracle_{seed}", "label": f"Scripted oracle on dev world {seed} (fixture)",
                   "condition": "oracle",
                   "policy": "scripted greedy disagreement over the 12-form library (no language model)",
                   "session_info": info.model_dump(mode="json"), "score": score, "truth": truth}
        (run_dir / "metrics.json").write_text(json.dumps(_finite(_dump(metrics)), indent=1, allow_nan=False))
    hits = sum(s["hit"] for s in score["commit"]["shots"])
    return {"seed": seed, "family": w.family, "p": w.p, "final": best, "hits": hits,
            "changed": n_changed, "runtime_s": round(time.perf_counter() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1000,1001,1002,1003")
    ap.add_argument("--out", default="viewer/public/runs")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    seeds = parse_seeds(args.seeds)  # refuses anything outside the dev seeds
    out = Path(args.out)
    with ProcessPoolExecutor(args.jobs) as ex:
        for r in ex.map(run, seeds, [out] * len(seeds)):
            print(json.dumps(r), flush=True)


if __name__ == "__main__":
    main()
