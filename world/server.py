"""World server: the HTTP API of SPEC.md section 5.2, backed by the hidden generator.

    SOLZERO_ADMIN_TOKEN=... .venv/bin/python -m world.server --port 8000

Agent-facing endpoints never reveal the family, parameters, probe scores or mission hits.
Admin endpoints need the X-Admin-Token header to equal SOLZERO_ADMIN_TOKEN; they are
disabled when that variable is unset. Test seeds are only served with --final-eval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import threading
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Header, HTTPException

from schemas import (
    CommitRequest, CommitResponse, ExperimentRequest, LawsRequest, NominateRequest, OkResponse,
    PredictionsRequest, PredictionsResponse, Result, SessionInfo, SessionRequest,
)
from tools.analysis import compile_law, simulate_specs
from tools.defaults import LAUNCHER, NOISE_SD, RANGES, SAMPLES

from .generator import DEV_SEEDS, MASSES, World, make_world

BUDGET = 12
CONDITIONS = ("lab", "random", "single", "textbook", "oracle")
PREREGISTERED = ("lab", "single")
TEST_SEEDS_LOCK = Path(__file__).with_name("test_seeds.lock")
RUNS_DIR = Path(os.environ.get("SOLZERO_RUNS_DIR", "runs"))


def world_id_for(seed: int) -> str:
    """Opaque world id; the seed (and so the family) cannot be read off it."""
    return "w_" + hashlib.sha256(f"solzero-world-{seed}".encode()).hexdigest()[:12]


@lru_cache(maxsize=64)
def _world(seed: int, final_eval: bool) -> World:
    return make_world(seed, final_eval=final_eval)


@dataclass
class Session:
    session_id: str
    world: World
    condition: str
    rng: np.random.Generator
    results: list[Result] = field(default_factory=list)
    live_laws: dict = field(default_factory=dict)  # law_id -> Law
    tables: dict = field(default_factory=dict)  # table_id -> {spec, law_ids, used, tentative}
    nominations: list[dict] = field(default_factory=list)
    commit: dict | None = None

    @property
    def used(self) -> int:
        return len(self.results)


class WorldServer:
    def __init__(self, seeds, final_eval: bool = False, runs_dir: Path = RUNS_DIR):
        self.final_eval = final_eval
        self.seed_of = {world_id_for(s): s for s in seeds}
        self.sessions: dict[str, Session] = {}
        self.counts: dict[tuple[int, str], int] = {}
        self.runs_dir = runs_dir
        self.lock = threading.Lock()

    # --- helpers --------------------------------------------------------------

    def session(self, session_id: str) -> Session:
        s = self.sessions.get(session_id)
        if s is None:
            raise HTTPException(404, f"unknown session {session_id}")
        return s

    def save(self, s: Session) -> None:
        """Server-side raw record of the session, for evaluation (never served to agents)."""
        d = self.runs_dir / s.session_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "world_session.json").write_text(json.dumps(self.score(s), indent=1, default=float))

    @staticmethod
    def check_spec(spec, kind: str = "experiment") -> None:
        sample = next((x for x in SAMPLES if x.sample_id == spec.sample_id), None)
        if sample is None:
            raise HTTPException(422, f"unknown sample {spec.sample_id}")
        if spec.type != "weigh" and not sample.launchable:
            raise HTTPException(422, f"{spec.sample_id} may only be weighed before the mission")
        for name, (lo, hi) in RANGES[spec.type].items():
            v = getattr(spec, name)
            if not (lo <= v <= hi) or not math.isfinite(v):
                raise HTTPException(422, f"{spec.type}.{name}={v} outside [{lo}, {hi}]")

    # --- agent-facing ------------------------------------------------------------

    def create_session(self, req: SessionRequest) -> SessionInfo:
        seed = self.seed_of.get(req.world_id)
        if seed is None:
            raise HTTPException(404, f"unknown world {req.world_id}")
        w = _world(seed, self.final_eval)
        with self.lock:
            k = self.counts.get((seed, req.condition), 0)
            self.counts[(seed, req.condition)] = k + 1
            sid = f"s_{uuid.uuid4().hex[:10]}"
            rng = np.random.default_rng([seed, CONDITIONS.index(req.condition), k])
            s = Session(session_id=sid, world=w, condition=req.condition, rng=rng)
            self.sessions[sid] = s
        self.save(s)
        return SessionInfo(
            session_id=sid, budget=BUDGET, samples=SAMPLES,
            ranges={k: dict(v) for k, v in RANGES.items()},
            noise_sd=dict(NOISE_SD), launcher=LAUNCHER, targets=w.targets, shot_zero=w.shot_zero,
        )

    def set_laws(self, req: LawsRequest) -> OkResponse:
        s = self.session(req.session_id)
        if s.commit:
            raise HTTPException(409, "already committed")
        with self.lock:
            s.live_laws = {law.law_id: law for law in req.live_laws}
        return OkResponse()

    def predictions(self, req: PredictionsRequest) -> PredictionsResponse:
        s = self.session(req.session_id)
        self.check_spec(req.spec)
        ids = [p.law_id for p in req.predictions]
        if len(ids) != len(set(ids)) or set(ids) != set(s.live_laws):
            raise HTTPException(422, f"predictions must cover exactly the live laws {sorted(s.live_laws)}, "
                                     f"got {sorted(ids)}")
        tid = f"p_{uuid.uuid4().hex[:10]}"
        with self.lock:
            s.tables[tid] = {"spec": req.spec, "law_ids": frozenset(ids), "used": False,
                             "predictions": [p.model_dump() for p in req.predictions],
                             "tentative_followup": req.tentative_followup.model_dump() if req.tentative_followup else None}
        return PredictionsResponse(prediction_table_id=tid)

    def experiment(self, req: ExperimentRequest) -> Result:
        s = self.session(req.session_id)
        if s.commit:
            raise HTTPException(409, "already committed")
        if s.used >= BUDGET:
            raise HTTPException(409, "budget exhausted")
        self.check_spec(req.spec)
        with self.lock:
            if s.condition in PREREGISTERED or req.prediction_table_id is not None:
                t = s.tables.get(req.prediction_table_id or "")
                if t is None:
                    raise HTTPException(422, "missing or unknown prediction table")
                if t["used"]:
                    raise HTTPException(422, "prediction table already used")
                if t["spec"] != req.spec:
                    raise HTTPException(422, "spec does not match the prediction table")
                if t["law_ids"] != frozenset(s.live_laws):
                    raise HTTPException(422, "live law set changed since the prediction table")
                t["used"] = True
                t["experiment_index"] = s.used + 1
            if s.used >= BUDGET:
                raise HTTPException(409, "budget exhausted")
            i = s.used + 1
            r = s.world.run_experiment(req.spec, s.rng, index=i, budget_left=BUDGET - i)
            s.results.append(r)
        self.save(s)
        return r

    def nominate(self, req: NominateRequest) -> OkResponse:
        s = self.session(req.session_id)
        if req.fit.law_id != req.law.law_id or set(req.fit.params) != set(req.law.params):
            raise HTTPException(422, "fit does not match law (law_id or parameter names)")
        try:
            scores = probe_scores(s.world, req.law, req.fit)
        except Exception as exc:  # unparsable or numerically broken law
            raise HTTPException(422, f"law could not be evaluated: {exc}") from exc
        with self.lock:
            s.nominations.append({"n_experiments": s.used, "law": req.law.model_dump(),
                                  "fit": req.fit.model_dump(), **scores})
        self.save(s)
        return OkResponse()

    def commit(self, req: CommitRequest) -> CommitResponse:
        s = self.session(req.session_id)
        targets = {t.target_id: t for t in s.world.targets}
        seen = set()
        lo_v, hi_v = RANGES["mission"]["speed_mps"]
        lo_e, hi_e = RANGES["mission"]["elevation_deg"]
        for shot in req.shots:
            if shot.target_id not in targets:
                raise HTTPException(422, f"unknown target {shot.target_id}")
            if shot.target_id in seen:
                raise HTTPException(422, f"more than one shot at {shot.target_id}")
            seen.add(shot.target_id)
            if not (lo_v <= shot.speed_mps <= hi_v and lo_e <= shot.elevation_deg <= hi_e):
                raise HTTPException(422, f"shot at {shot.target_id} outside the safe launcher envelope")
        with self.lock:
            if s.commit:
                raise HTTPException(409, "already committed")
            shots = []
            for shot in req.shots:
                t = targets[shot.target_id]
                dv = s.rng.normal(0, NOISE_SD["speed_frac"], (1, 1))
                de = s.rng.normal(0, NOISE_SD["elevation_deg"], (1, 1))
                x = float(s.world.shot_x("mission_300", [shot.speed_mps], [shot.elevation_deg], t.z_m, dv, de)[0, 0])
                miss = abs(x - t.x_m) if math.isfinite(x) else float("inf")
                shots.append({**shot.model_dump(), "kind": s.world.target_kind[t.target_id],
                              "x_m": x if math.isfinite(x) else None, "miss_m": miss,
                              "miss_frac": miss / abs(t.x_m), "hit_radius_m": t.hit_radius_m,
                              "hit": bool(miss <= t.hit_radius_m)})
            s.commit = {**req.model_dump(exclude={"shots", "session_id"}), "after_experiments": s.used,
                        "shots": shots}
        self.save(s)
        return CommitResponse()

    # --- admin ------------------------------------------------------------------

    def score(self, s: Session) -> dict:
        w = s.world
        return {
            "session_id": s.session_id, "world_id": world_id_for(w.seed), "condition": s.condition,
            "budget": BUDGET, "used": s.used,
            "results": [r.model_dump() for r in s.results],
            "live_laws": sorted(s.live_laws),
            "prediction_tables": [{"id": k, "spec": v["spec"].model_dump(), "law_ids": sorted(v["law_ids"]),
                                   "used": v["used"], "experiment_index": v.get("experiment_index"),
                                   "tentative_followup": v["tentative_followup"]}
                                  for k, v in s.tables.items()],
            "nominations": s.nominations,
            "commit": s.commit,
            "targets": [t.model_dump() | {"kind": w.target_kind[t.target_id]} for t in w.targets],
        }


def probe_scores(w: World, law, fit) -> dict:
    """Hidden probe score of a nominated law (SPEC 7): share of beyond-range and in-range
    probes whose predicted landing point is within the hit radius, plus median error."""
    from schemas import hit_radius

    theta = np.array([[fit.params[k].value for k in law.params]])
    x = simulate_specs(compile_law(law), theta, w.probes, MASSES)[0, :, 0]
    err = np.abs(x - w.probe_x)
    err = np.where(np.isfinite(err), err, np.inf)
    within = err <= np.array([hit_radius(px) for px in w.probe_x])
    kind = np.array(w.probe_kind)
    return {"probe_median_error_m": float(np.median(err)),
            "probe_within_beyond": float(within[kind == "beyond"].mean()),
            "probe_within_in_range": float(within[kind == "in_range"].mean())}


def _require_admin(token: str | None) -> None:
    expected = os.environ.get("SOLZERO_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(403, "admin endpoints disabled: SOLZERO_ADMIN_TOKEN is unset")
    if token != expected:
        raise HTTPException(403, "bad admin token")


def create_app(seeds=DEV_SEEDS, final_eval: bool = False, runs_dir: Path = RUNS_DIR) -> FastAPI:
    ws = WorldServer(seeds, final_eval, runs_dir)
    app = FastAPI(title="Sol Zero world server")
    app.state.ws = ws

    app.post("/session", response_model=SessionInfo)(ws.create_session)
    app.post("/laws", response_model=OkResponse)(ws.set_laws)
    app.post("/predictions", response_model=PredictionsResponse)(ws.predictions)
    app.post("/experiment", response_model=Result)(ws.experiment)
    app.post("/nominate", response_model=OkResponse)(ws.nominate)
    app.post("/commit", response_model=CommitResponse)(ws.commit)

    @app.get("/admin/score/{session_id}")
    def admin_score(session_id: str, x_admin_token: str | None = Header(default=None)):
        _require_admin(x_admin_token)
        return ws.score(ws.session(session_id))

    @app.get("/admin/truth/{world_id}")
    def admin_truth(world_id: str, x_admin_token: str | None = Header(default=None)):
        _require_admin(x_admin_token)
        seed = ws.seed_of.get(world_id)
        if seed is None:
            raise HTTPException(404, f"unknown world {world_id}")
        w = _world(seed, ws.final_eval)
        return {**w.truth(), "world_id": world_id, "seed": seed,
                "targets": [t.model_dump() | {"kind": w.target_kind[t.target_id]} for t in w.targets]}

    @app.get("/admin/worlds")
    def admin_worlds(x_admin_token: str | None = Header(default=None)):
        _require_admin(x_admin_token)
        return {str(s): wid for wid, s in ws.seed_of.items()}

    return app


def main():
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--final-eval", action="store_true",
                    help="serve the frozen test seeds from world/test_seeds.lock instead of dev seeds")
    args = ap.parse_args()
    if args.final_eval:
        seeds = [int(x) for x in TEST_SEEDS_LOCK.read_text().split()]
    else:
        seeds = list(DEV_SEEDS)
    uvicorn.run(create_app(seeds, args.final_eval), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
