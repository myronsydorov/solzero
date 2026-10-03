"""16 concurrent sessions against a real uvicorn server (SPEC 5.2 under load)."""

import socket
import threading
import time

import uvicorn

from world.loadtest import run_load
from world.server import create_app

TOKEN = "load-token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_sixteen_concurrent_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLZERO_ADMIN_TOKEN", TOKEN)
    port = _free_port()
    app = create_app(seeds=[1000, 1001, 1002, 1003], runs_dir=tmp_path)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    try:
        res = run_load(f"http://127.0.0.1:{port}", TOKEN, sessions=16)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    s = res["summary"]
    assert s["server_errors"] == 0
    assert s["problems"] == []
    assert all(r["used"] == 12 and r["committed"] for r in res["sessions"])
    assert s["table_race"]["ok"], s["table_race"]
    assert s["wall_s"] < 60
