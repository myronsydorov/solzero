"""One session's local checks and ledger. The service owns the budget."""
from __future__ import annotations
from collections.abc import Callable
from pathlib import Path
from threading import RLock

import httpx
from schemas import (Commit, CommitRequest, ExperimentRequest, ExperimentSpec, FitResult, Law,
                     NominateRequest, Prediction, PredictionsRequest, Result, SessionRequest)
from lab.client import WorldClient
from lab.ledger import Ledger


class LabSession:
    def __init__(self, client: WorldClient, world_id: str, condition: str = "lab", *,
                 runs_root: str | Path = "runs", approve_commit: Callable[[Commit], bool] | None = None):
        self.client = client
        self.info = client.start(SessionRequest(world_id=world_id, condition=condition))
        self.condition = condition
        existing = Path(runs_root) / self.info.session_id / "ledger.jsonl"
        if existing.exists() and existing.stat().st_size:
            raise FileExistsError("Session ledger already exists; use a fresh runs directory")
        self.ledger = Ledger(self.info.session_id, runs_root)
        self.budget_left = self.info.budget
        self.results: list[Result] = []
        self.live_laws: dict[str, Law] = {}
        self.pending: tuple[PredictionsRequest, str] | None = None
        self.tentative: ExperimentSpec | None = None
        self.committed = False
        self.uncertain = False
        self._approve_commit = approve_commit
        self._lock = RLock()

    def _active(self):
        if self.committed:
            raise RuntimeError("Session is already committed")
        if self.uncertain:
            raise RuntimeError("Previous mutation has an unknown outcome; reconcile before continuing")

    def set_laws(self, laws: list[Law]):
        with self._lock:
            self._active()
            if len(laws) > 4 or len({law.law_id for law in laws}) != len(laws):
                raise ValueError("Expected up to four distinct live laws")
            self.live_laws = {law.law_id: law for law in laws}
            self.pending = None
            self.ledger.append(len(self.results), "theorist", "law_set",
                               {"laws": [law.model_dump(mode="json") for law in laws]})

    def preregister(self, spec: ExperimentSpec, predictions: list[Prediction],
                    tentative_followup: ExperimentSpec | None, reason: str = "") -> str:
        with self._lock:
            self._active()
            identifiers = [prediction.law_id for prediction in predictions]
            if len(identifiers) != len(set(identifiers)) or set(identifiers) != set(self.live_laws):
                raise ValueError("Pre-registration requires one prediction for every live law")
            if not predictions and self.results:
                raise ValueError("Empty predictions are allowed only in cycle zero")
            request = PredictionsRequest(session_id=self.info.session_id, spec=spec,
                                         predictions=predictions, tentative_followup=tentative_followup)
            response = self.client.preregister(request)
            self.pending = (request, response.prediction_table_id)
            self.ledger.decision_diff(len(self.results) + 1, self.tentative, spec, reason)
            self.ledger.append(len(self.results) + 1, "experimentalist", "prediction_table",
                               {**request.model_dump(mode="json"), "prediction_table_id": response.prediction_table_id})
            return response.prediction_table_id

    def execute(self, spec: ExperimentSpec) -> Result:
        with self._lock:
            self._active()
            if self.budget_left <= 0:
                raise RuntimeError("Experiment budget exhausted")
            if self.condition != "random" and (self.pending is None or self.pending[0].spec != spec):
                raise ValueError("Matching pre-registration is required")
            pending = self.pending if self.pending and self.pending[0].spec == spec else None
            request = ExperimentRequest(session_id=self.info.session_id, spec=spec,
                                        prediction_table_id=pending[1] if pending else None)
            try:
                result = self.client.experiment(request)
            except (httpx.TransportError, ValueError):
                self.uncertain = True
                raise
            except httpx.HTTPStatusError as error:
                if error.response.status_code >= 500:
                    self.uncertain = True
                raise
            if (result.spec != spec or result.index != len(self.results) + 1
                    or result.budget_left != self.budget_left - 1):
                self.uncertain = True
                raise RuntimeError("Service returned an inconsistent experiment result")
            self.budget_left = result.budget_left
            self.results.append(result)
            self.tentative = pending[0].tentative_followup if pending else None
            self.pending = None
            self.ledger.append(result.index, "operator", "result", result)
            return result

    def nominate(self, law: Law, fit: FitResult):
        with self._lock:
            self._active()
            if law.law_id not in self.live_laws or self.live_laws[law.law_id] != law:
                raise ValueError("Nominate a current live law")
            request = NominateRequest(session_id=self.info.session_id, law=law, fit=fit)
            response = self.client.nominate(request)
            self.ledger.append(len(self.results), "analyst", "nomination", request)
            return response

    def commit(self, record: Commit):
        with self._lock:
            self._active()
            if len(record.shots) != 5 or {shot.target_id for shot in record.shots} != {target.target_id for target in self.info.targets}:
                raise ValueError("Exactly one shot per target is required")
            for shot in record.shots:
                for name, (lower, upper) in self.info.ranges["mission"].items():
                    if not lower <= getattr(shot, name) <= upper:
                        raise ValueError("Mission launcher settings outside safe envelope")
            if self._approve_commit is None or not self._approve_commit(record.model_copy(deep=True)):
                raise PermissionError("Human approval of this firing table is required")
            request = CommitRequest(session_id=self.info.session_id, **record.model_dump(mode="json"))
            try:
                response = self.client.commit(request)
            except (httpx.TransportError, ValueError):
                self.uncertain = True
                raise
            except httpx.HTTPStatusError as error:
                if error.response.status_code >= 500:
                    self.uncertain = True
                raise
            self.committed = True
            self.ledger.append(len(self.results), "pi", "commit", request)
            return response
