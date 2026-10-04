"""Typed, fixed-route HTTP access to the experiment service."""
from __future__ import annotations
import os
import time

import httpx
from schemas import (CommitRequest, CommitResponse, ExperimentRequest, NominateRequest,
                     OkResponse, PredictionsRequest, PredictionsResponse, Result,
                     SessionInfo, SessionRequest, Law, LawsRequest)


class WorldClient:
    def __init__(self, base_url: str | None = None, *, transport=None, timeout: float = 30):
        address = base_url or os.environ.get("SOLZERO_WORLD_URL")
        if not address:
            raise ValueError("Set SOLZERO_WORLD_URL")
        parsed = httpx.URL(address)
        if parsed.scheme not in {"http", "https"} or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("Expected an HTTP service origin")
        self._http = httpx.Client(base_url=address, transport=transport, timeout=timeout,
                                  follow_redirects=False, trust_env=False)

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _post(self, path, request, response_type):
        for attempt in range(3):
            try:
                response = self._http.post(path, json=request.model_dump(mode="json"))
                break
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if attempt == 2:
                    raise
                time.sleep(0.1 * (2 ** attempt))
        response.raise_for_status()
        return response_type.model_validate(response.json())

    def start(self, request: SessionRequest) -> SessionInfo:
        return self._post("/session", request, SessionInfo)

    def replace_laws(self, session_id: str, live_laws: list[Law]) -> OkResponse:
        return self._post("/laws", LawsRequest(session_id=session_id, live_laws=live_laws), OkResponse)

    def preregister(self, request: PredictionsRequest) -> PredictionsResponse:
        return self._post("/predictions", request, PredictionsResponse)

    def experiment(self, request: ExperimentRequest) -> Result:
        return self._post("/experiment", request, Result)

    def nominate(self, request: NominateRequest) -> OkResponse:
        return self._post("/nominate", request, OkResponse)

    def commit(self, request: CommitRequest) -> CommitResponse:
        return self._post("/commit", request, CommitResponse)
