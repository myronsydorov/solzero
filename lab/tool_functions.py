"""Small JSON function adapters; configure one session in each isolated runner."""
from __future__ import annotations

from schemas import Commit, FitResult, Law, Prediction, Verdict, parse_spec
from lab.session import LabSession

_session: LabSession | None = None
_seed: int | None = None


def configure(session: LabSession, *, seed: int) -> None:
    """Host-only binding. Never expose this function as an agent tool."""
    global _session, _seed
    _session, _seed = session, seed


def current() -> LabSession:
    if _session is None:
        raise RuntimeError("Host must bind a session before exposing tools")
    return _session


def validated_law(record: dict) -> Law:
    return Law.model_validate(record)


def set_laws(laws: list[dict]) -> dict:
    """Record the complete current set of proposed laws."""
    current().set_laws([validated_law(record) for record in laws])
    return {"law_ids": list(current().live_laws)}


def preregister(spec: dict, predictions: list[dict], tentative_followup: dict | None = None,
                reason: str = "") -> dict:
    """Register predictions and a tentative next experiment before execution."""
    identifier = current().preregister(parse_spec(spec), [Prediction.model_validate(record) for record in predictions],
                                       parse_spec(tentative_followup) if tentative_followup is not None else None, reason)
    return {"prediction_table_id": identifier}


def weigh(sample_id: str, height_m: float) -> dict:
    """Measure the static force on a sample at the requested height."""
    return current().execute(parse_spec({"type": "weigh", "sample_id": sample_id,
                                        "height_m": height_m})).model_dump(mode="json")


def drop(sample_id: str, height_m: float) -> dict:
    """Measure fall time for a reference sample released at the requested height."""
    return current().execute(parse_spec({"type": "drop", "sample_id": sample_id,
                                        "height_m": height_m})).model_dump(mode="json")


def launch(sample_id: str, speed_mps: float, elevation_deg: float) -> dict:
    """Measure landing distance and flight time for a reference sample."""
    return current().execute(parse_spec({"type": "launch", "sample_id": sample_id,
                                        "speed_mps": speed_mps, "elevation_deg": elevation_deg})).model_dump(mode="json")


def fit_law(law: dict) -> dict:
    """Fit a proposed expression to this session's observed results."""
    from tools import fit_law as fit_function
    session = current()
    return fit_function(validated_law(law), session.results, session.info.samples, seed=_seed).model_dump(mode="json")


def predict(law: dict, fit: dict, spec: dict, n_draws: int = 200) -> dict:
    """Predict an experiment's observables under a supplied fitted law."""
    from tools import predict as predict_function
    session = current()
    return predict_function(validated_law(law), FitResult.model_validate(fit), parse_spec(spec), n_draws,
                            samples=session.info.samples, noise_sd=session.info.noise_sd, seed=_seed).model_dump(mode="json")


def disagreement(spec: dict, laws: list[dict]) -> dict:
    """Compare the predictions of supplied law/fit pairs for an experiment."""
    from tools import disagreement as compare_function
    session = current()
    pairs = [(validated_law(record["law"]), FitResult.model_validate(record["fit"])) for record in laws]
    return compare_function(parse_spec(spec), pairs, session.info.noise_sd,
                            samples=session.info.samples, seed=_seed).model_dump(mode="json")


def plan_shot(law: dict, fit: dict, target_id: str, sample_id: str = "mission_300") -> dict:
    """Plan one launcher setting for a known target using a supplied fitted law."""
    from tools import plan_shot as plan_function
    session = current()
    target = next((target for target in session.info.targets if target.target_id == target_id), None)
    if target is None:
        raise ValueError("Unknown target")
    return plan_function(validated_law(law), FitResult.model_validate(fit), target, sample_id,
                         session.info.ranges["mission"], samples=session.info.samples,
                         noise_sd=session.info.noise_sd, seed=_seed).model_dump(mode="json")


def nominate(law: dict, fit: dict) -> dict:
    """Nominate the current best law without receiving hidden scores."""
    return current().nominate(validated_law(law), FitResult.model_validate(fit)).model_dump(mode="json")


def record_candidates(candidates: list[dict], chosen: dict) -> dict:
    """Record at least two candidate experiments and the selected one."""
    parsed = [parse_spec(spec) for spec in candidates]
    actual = parse_spec(chosen)
    if len({item.model_dump_json() for item in parsed}) < 2 or actual not in parsed:
        raise ValueError("Choose from at least two candidates")
    current().ledger.append(len(current().results) + 1, "experimentalist", "candidates",
                            {"candidates": [spec.model_dump(mode="json") for spec in parsed],
                             "chosen": actual.model_dump(mode="json")})
    return {"ok": True}


def record_verdicts(verdicts: list[dict], confound_note: str = "") -> dict:
    """Record evidence assessments; insufficient evidence is a valid verdict."""
    parsed = [Verdict.model_validate(record) for record in verdicts]
    session = current()
    if not session.results or any(record.experiment_id != session.results[-1].experiment_id for record in parsed):
        raise ValueError("Verdicts must refer to the latest result")
    identifiers = [record.law_id for record in parsed]
    if len(identifiers) != len(set(identifiers)) or set(identifiers) != set(session.live_laws):
        raise ValueError("Provide one verdict for every live law")
    session.ledger.append(len(session.results), "analyst", "verdicts",
                          {"verdicts": [record.model_dump(mode="json") for record in parsed], "confound_note": confound_note})
    return {"ok": True}


def commit_mission(commit: dict) -> dict:
    """Submit a reviewed five-shot firing table after human approval."""
    return current().commit(Commit.model_validate(commit)).model_dump(mode="json")


def coverage() -> dict:
    """Summarize which samples and instrument settings have actually been tested."""
    session = current()
    return {"experiments": len(session.results), "budget_left": session.budget_left,
            "by_sample": {sample.sample_id: [
                {"index": result.index, "status": result.status, **result.spec.model_dump(mode="json")}
                for result in session.results if result.spec.sample_id == sample.sample_id]
                for sample in session.info.samples}}
