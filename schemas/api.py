"""Request and response bodies for the world server HTTP API (SPEC.md section 5.2)."""

from __future__ import annotations

from typing import Literal

from .records import Claim, ExperimentSpec, FitResult, Law, Prediction, Record, Shot

Condition = Literal["lab", "random", "single"]


class SessionRequest(Record):
    world_id: str
    condition: Condition


class PredictionsRequest(Record):
    session_id: str
    spec: ExperimentSpec
    predictions: list[Prediction]
    tentative_followup: ExperimentSpec | None = None


class PredictionsResponse(Record):
    prediction_table_id: str


class ExperimentRequest(Record):
    session_id: str
    spec: ExperimentSpec
    prediction_table_id: str | None = None  # required in the lab and single conditions


class NominateRequest(Record):
    session_id: str
    law: Law
    fit: FitResult


class OkResponse(Record):
    ok: Literal[True] = True


class CommitRequest(Record):
    session_id: str
    law_id: str
    shots: list[Shot]
    claim: Claim
    claims_non_ordinary: bool


class CommitResponse(Record):
    status: Literal["committed"] = "committed"
