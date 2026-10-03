"""Thin HTTP client for the world server, plus a helper that starts a local server."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from schemas import (
    CommitRequest, ExperimentRequest, FitResult, Law, LawsRequest, NominateRequest, PredictionsRequest,
    Result, SessionInfo, SessionRequest,
)


class WorldClient:
    """Agent-facing calls and admin calls. Raises httpx.HTTPStatusError on 4xx and 5xx."""

    def __init__(self, url: str, admin_token: str | None = None, timeout: float = 120.0):
        self.url = url.rstrip("/")
        self.admin_token = admin_token
        self.http = httpx.Client(base_url=self.url, timeout=timeout)

    def _post(self, path: str, body) -> dict:
        r = self.http.post(path, content=body.model_dump_json(), headers={"content-type": "application/json"})
        r.raise_for_status()
        return r.json()

    def _admin(self, path: str) -> dict:
        r = self.http.get(path, headers={"X-Admin-Token": self.admin_token or ""})
        r.raise_for_status()
        return r.json()

    # agent-facing
    def session(self, world_id: str, condition: str) -> SessionInfo:
        return SessionInfo.model_validate(self._post("/session", SessionRequest(world_id=world_id, condition=condition)))

    def laws(self, session_id: str, laws: list[Law]) -> None:
        self._post("/laws", LawsRequest(session_id=session_id, live_laws=laws))

    def predictions(self, session_id: str, spec, predictions, tentative=None) -> str:
        return self._post("/predictions", PredictionsRequest(
            session_id=session_id, spec=spec, predictions=predictions, tentative_followup=tentative))["prediction_table_id"]

    def experiment(self, session_id: str, spec, table_id: str | None = None) -> Result:
        return Result.model_validate(self._post("/experiment", ExperimentRequest(
            session_id=session_id, spec=spec, prediction_table_id=table_id)))

    def nominate(self, session_id: str, law: Law, fit: FitResult) -> None:
        self._post("/nominate", NominateRequest(session_id=session_id, law=law, fit=fit))

    def commit(self, req: CommitRequest) -> None:
        self._post("/commit", req)

    # admin
    def worlds(self) -> dict[int, str]:
        return {int(k): v for k, v in self._admin("/admin/worlds").items()}

    def score(self, session_id: str) -> dict:
        return self._admin(f"/admin/score/{session_id}")

    def truth(self, world_id: str) -> dict:
        return self._admin(f"/admin/truth/{world_id}")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalServer:
    """Starts `python -m world.server` on a free port with a fresh admin token."""

    def __init__(self, runs_dir: Path, final_eval: bool = False, log: Path | None = None):
        self.port = _free_port()
        self.token = secrets.token_hex(16)
        self.url = f"http://127.0.0.1:{self.port}"
        env = {**os.environ, "SOLZERO_ADMIN_TOKEN": self.token, "SOLZERO_RUNS_DIR": str(runs_dir)}
        cmd = [sys.executable, "-m", "world.server", "--port", str(self.port)]
        if final_eval:
            cmd.append("--final-eval")
        self.log = open(log or os.devnull, "a")
        self.proc = subprocess.Popen(cmd, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if httpx.get(self.url + "/docs", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.2)
            if self.proc.poll() is not None:
                break
        self.close()
        raise RuntimeError(f"world server did not start (see {log})")

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()
