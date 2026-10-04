"""Real-loopback contract smoke using the mock only; saves raw evidence."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from schemas import FitResult, Law, Prediction, parse_spec
from lab.client import WorldClient
from lab.session import LabSession
from lab import tool_functions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--output", default="runs/mock-check")
    args = parser.parse_args()
    if not 1000 <= args.seed <= 1999:
        parser.error("Use a development seed")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    address = f"http://127.0.0.1:{port}"
    env = dict(os.environ, SOLZERO_ADMIN_TOKEN="mock-check-only")
    with (output / "server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "mock.server", "--seed", str(args.seed),
                                    "--port", str(port)], stdout=log, stderr=subprocess.STDOUT, env=env)
        try:
            with httpx.Client(base_url=address, trust_env=False) as direct:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError("Mock server failed to start; see server.log")
                    try:
                        if direct.get("/openapi.json").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Mock server readiness timed out")
                with WorldClient(address) as client:
                    session = LabSession(client, "mock-dev", runs_root=output)
                    tool_functions.configure(session, seed=args.seed)
                    first = parse_spec({"type": "weigh", "sample_id": "ref_100", "height_m": 0.8})
                    raw = []
                    def request(path, payload):
                        response = direct.post(path, json=payload)
                        raw.append({"path": path, "request": payload, "status": response.status_code,
                                    "response": response.json()})
                        return response
                    base = {"session_id": session.info.session_id, "spec": first.model_dump(mode="json")}
                    assert request("/experiment", base).status_code == 422
                    session.preregister(first, [], None)
                    result = tool_functions.weigh("ref_100", 0.8)
                    assert result["budget_left"] == 11
                    # An explicitly labelled fixture law, not a scientific agent proposal.
                    law = Law(law_id="fixture-law", ax="0", az="-acceleration",
                              params={"acceleration": {"init": 9.81, "lo": 1, "hi": 20}})
                    session.set_laws([law])
                    fit = FitResult(law_id=law.law_id, params={"acceleration": {"value": 9.81, "sd": 0.01}},
                                    chi2_dof=0, loo_error=None, n_experiments=1, converged=True)
                    session.nominate(law, fit)
                    prediction = Prediction(law_id=law.law_id, observables={"force_n": {"mean": 0.981, "sd": 0.02}})
                    for _ in range(11):
                        session.preregister(first, [prediction], first)
                        result = session.execute(first)
                        session.nominate(law, fit.model_copy(update={"n_experiments": result.index}))
                    assert request("/experiment", base).status_code == 409
                    score = direct.get(f"/admin/score/{session.info.session_id}",
                                       headers={"Authorization": "Bearer mock-check-only"}).json()
                    (output / "responses.json").write_text(json.dumps(raw, indent=2) + "\n")
                    (output / "admin-score.json").write_text(json.dumps(score, indent=2) + "\n")
                    summary = {"passed": True, "seed": args.seed, "experiments": len(session.results),
                               "budget_left": session.budget_left, "missing_preregistration": 422,
                               "budget_exhausted": 409, "ledger": str(session.ledger.path)}
                    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
                    print(json.dumps(summary, indent=2))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
