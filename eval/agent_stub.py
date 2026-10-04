"""Stand-in for lane B's agent CLI (SPEC.md section 5.6), for plumbing tests only.

It follows the CLI contract and the pre-registration rules but does no science. It keeps the
live law set empty, runs the given specs (or a fixed weigh), nominates textbook physics and
commits textbook shots. Replace it with `python -m lab.run` once lane B ships the contract.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from schemas import SessionInfo, WeighSpec, parse_spec
from world.generator import textbook_law

from .client import WorldClient
from .scripted import Ledger, plan_and_commit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world-url", required=True)
    ap.add_argument("--session-info", required=True)
    ap.add_argument("--condition", required=True, choices=["lab", "single", "random"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--specs")
    ap.add_argument("--max-tokens", type=int, default=0)
    ap.add_argument("--max-wall-s", type=float, default=600)
    ap.add_argument("--approval", choices=["human", "auto"], default="human")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    # Test hook: SOLZERO_STUB_RATE_LIMIT_AFTER=N stops a fresh run after N experiments with a
    # provider-limit status, as a real agent would on a session limit.
    limit_after = int(os.environ.get("SOLZERO_STUB_RATE_LIMIT_AFTER", "-1"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    info = SessionInfo.model_validate_json(Path(args.session_info).read_text())
    client = WorldClient(args.world_url)
    ledger = Ledger(out / "ledger.jsonl")
    t0 = time.perf_counter()
    specs = ([parse_spec(d) for d in json.loads(Path(args.specs).read_text())] if args.specs
             else [WeighSpec(sample_id="ref_100", height_m=0.5)] * info.budget)
    law, fit = textbook_law()
    status = "committed"
    done = 0
    if args.resume and ledger.path.exists():
        done = sum(json.loads(line)["kind"] == "result" for line in ledger.path.read_text().splitlines())
    else:
        ledger.write(0, "theorist", "law_set", {"laws": []})
    for i, spec in enumerate(specs, 1):
        if i <= done:
            continue
        if not args.resume and i - 1 == limit_after:
            (out / "summary.json").write_text(json.dumps({
                "session_id": info.session_id, "status": "rate_limited", "tokens_used": 0,
                "wall_s": time.perf_counter() - t0, "n_experiments": i - 1, "retry_after_s": 1,
                "error": "You've hit your session limit (simulated)", "agent": "stub"}, indent=1))
            return
        if time.perf_counter() - t0 > args.max_wall_s:
            status = "cap_reached"
            break
        table = None
        if args.condition in ("lab", "single"):
            table = client.predictions(info.session_id, spec, [])
        r = client.experiment(info.session_id, spec, table)
        ledger.write(i, "operator", "result", r.model_dump())
        client.nominate(info.session_id, law, fit)
    if args.approval == "auto":
        plan_and_commit(client, info, law, fit, "insufficient_evidence", False, ledger, len(specs))
    else:
        status = "pending_approval"
    (out / "summary.json").write_text(json.dumps({
        "session_id": info.session_id, "status": status, "tokens_used": 0,
        "wall_s": time.perf_counter() - t0, "n_experiments": len(specs), "error": None, "agent": "stub"}, indent=1))


if __name__ == "__main__":
    main()
