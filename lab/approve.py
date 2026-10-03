"""Submit an exact, human-reviewed pending firing table once."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import httpx
from schemas import CommitRequest
from lab.client import WorldClient
from lab.ledger import Ledger


def submit(path: Path, approved_sha256: str):
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != approved_sha256:
        raise ValueError("The reviewed firing table hash does not match this file")
    document = json.loads(raw)
    request = CommitRequest(session_id=document["session_id"], **document["commit"])
    marker = path.with_suffix(".attempt.json")
    record = {"sha256": digest, "session_id": request.session_id, "status": "outcome_unknown"}
    # Written before HTTP; a crash or lost response cannot cause an automatic replay.
    with marker.open("x") as stream:
        json.dump(record, stream)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    try:
        with WorldClient(document["base_url"]) as client:
            response = client.commit(request)
    except httpx.HTTPStatusError as error:
        if error.response.status_code < 500:
            record.update(status="rejected", http_status=error.response.status_code)
            marker.write_text(json.dumps(record, indent=2) + "\n")
        raise
    ledger = Ledger(request.session_id, path.parent)
    records = [json.loads(line) for line in ledger.path.read_text().splitlines()] if ledger.path.exists() else []
    cycle = max((item["cycle"] for item in records if item["kind"] == "result"), default=0)
    ledger.append(cycle, "pi", "commit", request)
    record.update(status=response.status)
    marker.write_text(json.dumps(record, indent=2) + "\n")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--approved-sha256", required=True,
                        help="Hash of the exact firing-table file already approved by the human")
    args = parser.parse_args()
    print(json.dumps(submit(args.path, args.approved_sha256), indent=2))


if __name__ == "__main__":
    main()
