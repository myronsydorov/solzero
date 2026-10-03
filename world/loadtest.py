"""Load test for the world server: N concurrent full sessions plus two race checks.

    python -m world.loadtest --url http://127.0.0.1:8000 --admin-token T --sessions 16 --out runs/loadtest

Each session sets one live law, runs 12 experiments (lab: pre-registered; random: plain),
nominates after each, gets 409 on a 13th, and commits five shots. Random sessions fire
their last experiment twice concurrently (exactly one may succeed). One extra lab session
submits one prediction table twice concurrently (exactly one may succeed).
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np

from schemas import OBSERVABLES, Law

SPECS = [
    {"type": "weigh", "sample_id": "ref_100", "height_m": 0.5},
    {"type": "drop", "sample_id": "ref_200", "height_m": 1.0},
    {"type": "launch", "sample_id": "ref_050", "speed_mps": 3.0, "elevation_deg": 40.0},
    {"type": "weigh", "sample_id": "mission_300", "height_m": 1.1},
]
LAW = Law(law_id="L1", ax="-c*speed*vx/m", az="-g0 - c*speed*vz/m",
          params={"g0": {"init": 9.81, "lo": 1, "hi": 20}, "c": {"init": 0.01, "lo": 0, "hi": 1}}).model_dump()
FIT = {"law_id": "L1", "params": {"g0": {"value": 9.81, "sd": 0.1}, "c": {"value": 0.01, "sd": 0.001}},
       "chi2_dof": 1.0, "n_experiments": 0, "converged": True}


class Recorder:
    def __init__(self):
        self.rows: list[tuple[str, int, float]] = []
        self.lock = threading.Lock()

    def post(self, client: httpx.Client, path: str, body: dict) -> httpx.Response:
        t0 = time.perf_counter()
        r = client.post(path, json=body)
        with self.lock:
            self.rows.append((path, r.status_code, time.perf_counter() - t0))
        return r


def _prediction(spec: dict) -> dict:
    return {"law_id": "L1", "observables": {k: {"mean": 1.0, "sd": 0.1} for k in OBSERVABLES[spec["type"]]}}


def _session(url: str, rec: Recorder, world_id: str, condition: str) -> dict:
    out = {"condition": condition, "problems": []}
    with httpx.Client(base_url=url, timeout=60) as c:
        info = rec.post(c, "/session", {"world_id": world_id, "condition": condition}).json()
        sid = out["session_id"] = info["session_id"]
        rec.post(c, "/laws", {"session_id": sid, "live_laws": [LAW]})

        def experiment(i: int) -> httpx.Response:
            spec = SPECS[i % len(SPECS)]
            table = None
            if condition == "lab":
                table = rec.post(c, "/predictions", {"session_id": sid, "spec": spec,
                                                     "predictions": [_prediction(spec)]}).json()["prediction_table_id"]
            return rec.post(c, "/experiment", {"session_id": sid, "spec": spec, "prediction_table_id": table})

        last = 12 if condition == "lab" else 11
        for i in range(last):
            r = experiment(i)
            if r.status_code != 200:
                out["problems"].append(f"experiment {i + 1}: {r.status_code}")
            rec.post(c, "/nominate", {"session_id": sid, "law": LAW, "fit": FIT})
        if condition != "lab":
            # Budget race: one experiment left, two fired at once.
            with ThreadPoolExecutor(2) as ex:
                codes = sorted(f.result().status_code for f in [ex.submit(_raw_experiment, url, rec, sid, SPECS[0])
                                                                for _ in range(2)])
            out["budget_race"] = codes
            if codes != [200, 409]:
                out["problems"].append(f"budget race gave {codes}")
        r = experiment(12)
        if r.status_code != 409:
            out["problems"].append(f"13th experiment gave {r.status_code}")
        shots = [{"target_id": t["target_id"], "speed_mps": 5.0, "elevation_deg": 45.0} for t in info["targets"]]
        r = rec.post(c, "/commit", {"session_id": sid, "law_id": "L1", "shots": shots,
                                    "claim": "predictive_only", "claims_non_ordinary": False})
        if r.status_code != 200:
            out["problems"].append(f"commit gave {r.status_code}")
    return out


def _raw_experiment(url, rec, sid, spec, table=None):
    with httpx.Client(base_url=url, timeout=60) as c:
        return rec.post(c, "/experiment", {"session_id": sid, "spec": spec, "prediction_table_id": table})


def _table_race(url: str, rec: Recorder, world_id: str) -> dict:
    with httpx.Client(base_url=url, timeout=60) as c:
        sid = rec.post(c, "/session", {"world_id": world_id, "condition": "lab"}).json()["session_id"]
        rec.post(c, "/laws", {"session_id": sid, "live_laws": [LAW]})
        spec = SPECS[1]
        tid = rec.post(c, "/predictions", {"session_id": sid, "spec": spec,
                                           "predictions": [_prediction(spec)]}).json()["prediction_table_id"]
    with ThreadPoolExecutor(2) as ex:
        codes = sorted(f.result().status_code for f in [ex.submit(_raw_experiment, url, rec, sid, spec, tid)
                                                        for _ in range(2)])
    return {"session_id": sid, "codes": codes, "ok": codes in ([200, 409], [200, 422])}


def run_load(url: str, admin_token: str, sessions: int = 16, seeds=(1000, 1001, 1002, 1003)) -> dict:
    """Run the load test against a live server and return raw rows plus a summary."""
    with httpx.Client(base_url=url, timeout=60) as c:
        worlds = c.get("/admin/worlds", headers={"X-Admin-Token": admin_token}).json()
    ids = [worlds[str(s)] for s in seeds]
    rec = Recorder()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(sessions + 1) as ex:
        futs = [ex.submit(_session, url, rec, ids[i % len(ids)], "lab" if i % 2 == 0 else "random")
                for i in range(sessions)]
        race = ex.submit(_table_race, url, rec, ids[0])
        results = [f.result() for f in futs]
        table_race = race.result()
    wall = time.perf_counter() - t0
    with httpx.Client(base_url=url, timeout=60) as c:
        for r in results:
            score = c.get(f"/admin/score/{r['session_id']}", headers={"X-Admin-Token": admin_token}).json()
            r["used"] = score["used"]
            r["committed"] = score["commit"] is not None
            if score["used"] != 12 or not r["committed"]:
                r["problems"].append(f"admin score used={score['used']} committed={r['committed']}")
    by_path: dict[str, list[float]] = {}
    for path, _, dt in rec.rows:
        by_path.setdefault(path, []).append(dt)
    summary = {
        "sessions": sessions, "requests": len(rec.rows), "wall_s": round(wall, 2),
        "server_errors": sum(code >= 500 for _, code, _ in rec.rows),
        "problems": [p for r in results for p in r["problems"]] + ([] if table_race["ok"] else
                                                                   [f"table race gave {table_race['codes']}"]),
        "latency_ms": {p: {"n": len(v), "p50": round(1000 * float(np.percentile(v, 50)), 1),
                           "p95": round(1000 * float(np.percentile(v, 95)), 1)} for p, v in sorted(by_path.items())},
        "table_race": table_race,
    }
    return {"summary": summary, "sessions": results, "rows": rec.rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--admin-token", required=True)
    ap.add_argument("--sessions", type=int, default=16)
    ap.add_argument("--out", default="runs/loadtest")
    args = ap.parse_args()
    res = run_load(args.url, args.admin_token, args.sessions)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "loadtest.json").write_text(json.dumps(res, indent=1))
    s = res["summary"]
    print(f"sessions {s['sessions']}  requests {s['requests']}  wall {s['wall_s']} s  "
          f"5xx {s['server_errors']}  problems {len(s['problems'])}")
    for p, v in s["latency_ms"].items():
        print(f"  {p:14s} n={v['n']:4d}  p50 {v['p50']:7.1f} ms  p95 {v['p95']:7.1f} ms")
    for p in s["problems"]:
        print("  PROBLEM:", p)
    print("raw:", out / "loadtest.json")


if __name__ == "__main__":
    main()
