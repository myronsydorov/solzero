"""In-memory development world. Never use this fixture for scientific claims."""
from __future__ import annotations

from dataclasses import dataclass, field
import argparse
import hmac
import math
import os
import random
from threading import RLock
from typing import Callable

from fastapi import FastAPI, Header, HTTPException
from schemas import (
    CommitRequest, CommitResponse, ExperimentRequest, ExperimentSpec,
    NominateRequest, OkResponse, PredictionsRequest, PredictionsResponse,
    Result, SessionInfo, SessionRequest, Law, LawsRequest, Target, hit_radius, parse_spec,
)

BUDGET = 12
GRAVITY = 9.81
MASSES = {f"ref_{grams:03}": grams / 1000 for grams in (20, 50, 100, 200, 400, 800)}
MASSES["mission_300"] = 0.3
RANGES = {"weigh": {"height_m": (0, 1.2)}, "drop": {"height_m": (0.1, 1.2)},
          "launch": {"speed_mps": (1, 4), "elevation_deg": (15, 75)},
          "mission": {"speed_mps": (1, 7), "elevation_deg": (15, 75)}}
from tools.defaults import NOISE_SD

NOISE = dict(NOISE_SD)
TARGETS = [Target(target_id=f"t{idx}", x_m=distance, z_m=0.0).model_dump(mode="json")
           for idx, distance in enumerate((1.0, 1.5, 2.3, 3.2, 4.2), 1)]



def flight(speed: float, elevation: float, target_height: float = 0.0) -> tuple[float, float] | None:
    angle = math.radians(elevation)
    vertical = speed * math.sin(angle)
    discriminant = vertical ** 2 + 2 * GRAVITY * (0.2 - target_height)
    if discriminant < 0:
        return None
    duration = (vertical + math.sqrt(discriminant)) / GRAVITY
    return speed * math.cos(angle) * duration, duration


def measure(spec: ExperimentSpec, rng: random.Random) -> tuple[dict, dict]:
    if spec.type == "weigh":
        force = MASSES[spec.sample_id] * GRAVITY
        sd = force * NOISE["force_frac"]
        return {"force_n": rng.gauss(force, sd)}, {"force_n": sd}
    if spec.type == "drop":
        sd = NOISE["fall_time_s"]
        return {"fall_time_s": rng.gauss(math.sqrt(2 * spec.height_m / GRAVITY), sd)}, {"fall_time_s": sd}
    actual_speed = rng.gauss(spec.speed_mps, NOISE["speed_frac"] * spec.speed_mps)
    actual_angle = rng.gauss(spec.elevation_deg, NOISE["elevation_deg"])
    distance, duration = flight(actual_speed, actual_angle)
    return {"landing_x_m": rng.gauss(distance, NOISE["landing_x_m"]),
            "flight_time_s": rng.gauss(duration, NOISE["flight_time_s"])}, {
                "landing_x_m": NOISE["landing_x_m"], "flight_time_s": NOISE["flight_time_s"],
                "speed_frac": NOISE["speed_frac"], "elevation_deg": NOISE["elevation_deg"]}


def validate_spec(spec: ExperimentSpec) -> None:
    if spec.sample_id not in MASSES:
        raise HTTPException(422, "Unknown sample")
    if spec.type != "weigh" and spec.sample_id == "mission_300":
        raise HTTPException(422, "Mission sample cannot be dropped or launched")
    for name, (lower, upper) in RANGES[spec.type].items():
        value = getattr(spec, name)
        if not math.isfinite(value) or not lower <= value <= upper:
            raise HTTPException(422, f"{name} outside allowed range")


@dataclass
class Session:
    info: SessionInfo
    condition: str
    rng: random.Random
    tables: dict[str, tuple[PredictionsRequest, int, int]] = field(default_factory=dict)
    live_laws: dict[str, Law] = field(default_factory=dict)
    law_revision: int = 0
    used_tables: set[str] = field(default_factory=set)
    results: list[Result] = field(default_factory=list)
    nominations: list[dict] = field(default_factory=list)
    probe_scores: list[dict] = field(default_factory=list)
    mission: list[dict] = field(default_factory=list)
    proposed: bool = False
    committed: CommitRequest | None = None


def create_app(*, seed: int = 1000, world_id: str = "mock-dev", admin_token: str | None = None,
               measurement: Callable = measure) -> FastAPI:
    if not 1000 <= seed <= 1999:
        raise ValueError("Mock server accepts only dev seeds 1000–1999")
    probe_rng = random.Random(seed)
    probe_specs = [parse_spec({"type": "launch", "sample_id": probe_rng.choice(list(MASSES)[:-1]),
                              "speed_mps": probe_rng.uniform(lower, upper), "elevation_deg": probe_rng.uniform(15, 75)})
                   for lower, upper in ((1, 4), (4, 7)) for _ in range(20)]
    app = FastAPI(title="Sol Zero development mock", docs_url=None, redoc_url=None)
    sessions: dict[str, Session] = {}
    lock = RLock()
    token = admin_token if admin_token is not None else os.environ.get("SOLZERO_ADMIN_TOKEN")

    def session_for(identifier: str) -> Session:
        if identifier not in sessions:
            raise HTTPException(404, "Unknown session")
        return sessions[identifier]

    def active(session: Session) -> None:
        if session.committed is not None:
            raise HTTPException(409, "Session already committed")

    def authorize(authorization: str | None) -> None:
        if not token or not hmac.compare_digest((authorization or "").encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "Admin authorization required")

    @app.post("/session", response_model=SessionInfo)
    def start(request: SessionRequest):
        with lock:
            if request.world_id != world_id:
                raise HTTPException(404, "Unknown world")
            identifier = f"s_mock_{len(sessions) + 1:04d}"
            zero_spec = parse_spec({"type": "launch", "sample_id": "ref_100", "speed_mps": 3, "elevation_deg": 45})
            zero, _ = measure(zero_spec, random.Random(seed))
            info = SessionInfo(session_id=identifier, budget=BUDGET,
                samples=[{"sample_id": name, "mass_kg": mass, "launchable": name != "mission_300"}
                         for name, mass in MASSES.items()], ranges=RANGES, noise_sd=NOISE,
                launcher={"x_m": 0, "z_m": 0.2}, targets=TARGETS,
                shot_zero={"spec": zero_spec, "target_id": "t0", "landing_x_m": zero["landing_x_m"],
                           "miss_m": abs(zero["landing_x_m"] - 1.1)})
            sessions[identifier] = Session(info, request.condition, random.Random(seed))
            return info

    @app.post("/laws", response_model=OkResponse)
    def replace_laws(request: LawsRequest):
        with lock:
            session = session_for(request.session_id)
            live_laws = request.live_laws
            active(session)
            identifiers = [law.law_id for law in live_laws]
            if len(identifiers) > 4 or len(identifiers) != len(set(identifiers)):
                raise HTTPException(422, "Expected up to four distinct live laws")
            session.live_laws = {law.law_id: law for law in live_laws}
            session.law_revision += 1
            session.proposed |= bool(live_laws)
            return OkResponse()

    @app.post("/predictions", response_model=PredictionsResponse)
    def predictions(request: PredictionsRequest):
        with lock:
            session = session_for(request.session_id)
            active(session)
            validate_spec(request.spec)
            if request.tentative_followup is not None:
                validate_spec(request.tentative_followup)
            identifiers = [prediction.law_id for prediction in request.predictions]
            if len(identifiers) != len(set(identifiers)) or set(identifiers) != set(session.live_laws):
                raise HTTPException(422, "Predictions must cover exactly the registered live laws")
            if not identifiers and (session.results or session.proposed):
                raise HTTPException(422, "Empty predictions are only allowed before any law in cycle zero")
            expected = {"weigh": {"force_n"}, "drop": {"fall_time_s"},
                        "launch": {"landing_x_m", "flight_time_s"}}[request.spec.type]
            for prediction in request.predictions:
                if set(prediction.observables) != expected:
                    raise HTTPException(422, "Prediction observables do not match experiment")
                if any(not math.isfinite(value.mean) or not math.isfinite(value.sd) or value.sd < 0
                       for value in prediction.observables.values()):
                    raise HTTPException(422, "Invalid prediction uncertainty")
            identifier = f"pt_{request.session_id}_{len(session.tables) + 1:04d}"
            session.tables[identifier] = (request, len(session.results), session.law_revision)
            session.proposed |= bool(identifiers)
            return PredictionsResponse(prediction_table_id=identifier)

    @app.post("/experiment", response_model=Result)
    def experiment(request: ExperimentRequest):
        with lock:
            session = session_for(request.session_id)
            active(session)
            if len(session.results) >= BUDGET:
                raise HTTPException(409, "Budget exhausted")
            validate_spec(request.spec)
            identifier = request.prediction_table_id
            if session.condition != "random" or identifier is not None:
                table = session.tables.get(identifier)
                if (table is None or identifier in session.used_tables or table[0].spec != request.spec
                        or table[1] != len(session.results) or table[2] != session.law_revision
                        or (session.proposed and not table[0].predictions)):
                    raise HTTPException(422, "Missing, mismatched, stale or used prediction table")
                session.used_tables.add(identifier)
            index = len(session.results) + 1
            status = "ok"
            try:
                observables, noise = measurement(request.spec, session.rng)
                if not all(math.isfinite(value) for value in (*observables.values(), *noise.values())):
                    raise ArithmeticError("Nonfinite measurement")
            except Exception:
                # The operation has started: every instrument failure consumes one slot.
                observables, noise, status = {}, {}, "failed"
            result = Result(experiment_id=f"e{index:02d}", index=index, spec=request.spec,
                            observables=observables, noise_sd=noise, status=status, budget_left=BUDGET-index)
            session.results.append(result)
            return result

    @app.post("/nominate", response_model=OkResponse)
    def nominate(request: NominateRequest):
        with lock:
            session = session_for(request.session_id)
            active(session)
            if session.live_laws.get(request.law.law_id) != request.law:
                raise HTTPException(422, "Nominate a registered live law")
            if request.law.law_id != request.fit.law_id or request.fit.n_experiments != len(session.results):
                raise HTTPException(422, "Nomination law/fit or experiment count mismatch")
            session.nominations.append({"index": len(session.results), **request.model_dump(mode="json")})
            session.proposed = True
            score = {"index": len(session.results), "median_landing_error_m": None}
            try:
                from tools import predict
                import statistics
                errors, within = [], []
                for probe in probe_specs:
                    predicted = predict(request.law, request.fit, probe, n_draws=0, seed=seed)
                    predicted_x = predicted.observables["landing_x_m"].mean
                    if not math.isfinite(predicted_x):
                        raise ArithmeticError("Prediction did not reach the target plane")
                    actual_x = flight(probe.speed_mps, probe.elevation_deg)[0]
                    error = abs(predicted_x - actual_x)
                    errors.append(error)
                    within.append(error <= hit_radius(actual_x))
                score.update(median_landing_error_m=statistics.median(errors), errors_m=errors,
                             probe_within_in_range=sum(within[:20]) / 20,
                             probe_within_beyond=sum(within[20:]) / 20)
            except ImportError:
                score["unavailable"] = "Shared analysis tools are not installed"
            except (ArithmeticError, ValueError, RuntimeError) as error:
                score["unavailable"] = f"Numerical scoring failure: {type(error).__name__}"
            session.probe_scores.append(score)
            return OkResponse()

    @app.post("/commit", response_model=CommitResponse)
    def commit(request: CommitRequest):
        with lock:
            session = session_for(request.session_id)
            active(session)
            if len(request.shots) != 5 or {shot.target_id for shot in request.shots} != {item["target_id"] for item in TARGETS}:
                raise HTTPException(422, "Commit requires exactly one shot for every target")
            for shot in request.shots:
                for name, (lower, upper) in RANGES["mission"].items():
                    value = getattr(shot, name)
                    if not math.isfinite(value) or not lower <= value <= upper:
                        raise HTTPException(422, "Unsafe mission launcher settings")
            session.committed = request
            for shot in request.shots:
                target = next(item for item in TARGETS if item["target_id"] == shot.target_id)
                actual_speed = session.rng.gauss(shot.speed_mps, NOISE["speed_frac"] * shot.speed_mps)
                actual_angle = session.rng.gauss(shot.elevation_deg, NOISE["elevation_deg"])
                crossing = flight(actual_speed, actual_angle, target["z_m"])
                miss = abs(crossing[0] - target["x_m"]) if crossing is not None else None
                session.mission.append({"target_id": shot.target_id, "miss_m": miss,
                                        "hit": miss is not None and miss <= target["hit_radius_m"]})
            return CommitResponse()

    @app.get("/admin/truth/{requested_world_id}")
    def truth(requested_world_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        if requested_world_id != world_id:
            raise HTTPException(404, "Unknown world")
        return {"mock": True, "description": "Earth ballistics without air resistance", "gravity_mps2": GRAVITY}

    @app.get("/admin/score/{session_id}")
    def score(session_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        with lock:
            session = session_for(session_id)
            return {"mock": True, "probe_error": session.probe_scores,
                    "nominations": session.nominations, "mission": session.mission,
                    "commit": session.committed.model_dump(mode="json") if session.committed else None}

    return app


app = create_app()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--world-id", default="mock-dev")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(seed=args.seed, world_id=args.world_id), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
