"""The scripted oracle ledger (sim/oracle_ledger.py) follows SPEC 5.5 and the cycle order."""

import json

import pytest

from schemas import LedgerEntry, SessionInfo
from sim.oracle_ledger import run
from world.server import BUDGET

CYCLE_ORDER = ["law_set", "candidates", "decision_diff", "prediction_table", "result", "verdicts",
               "nomination"]


@pytest.fixture(scope="module")
def oracle(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("oracle")
    out = tmp / "out"
    summary = run(1001, out, tmp / "runs")  # full budget, ~15 s
    lines = (out / "ledger.jsonl").read_text().splitlines()
    return out, summary, [LedgerEntry.model_validate(json.loads(line)) for line in lines]


def test_files_and_summary(oracle):
    out, summary, _ = oracle
    SessionInfo.model_validate_json((out / "session_info.json").read_text())
    saved = json.loads((out / "summary.json").read_text())
    assert saved["status"] == "committed" and saved["n_experiments"] == BUDGET
    assert saved["policy"] == "scripted_oracle" and saved["seed"] == 1001
    ws = json.loads((out / "world_session.json").read_text())
    assert ws["session_id"] == summary["session_id"] and ws["used"] == BUDGET
    assert len(ws["commit"]["shots"]) == 5


def test_cycle_order(oracle):
    _, _, entries = oracle
    by_cycle = {}
    for e in entries:
        by_cycle.setdefault(e.cycle, []).append(e.kind)
    assert sorted(by_cycle) == list(range(1, BUDGET + 2))
    for c in range(1, BUDGET + 1):
        expected = [k for k in CYCLE_ORDER if k != "decision_diff" or c >= 2]
        assert by_cycle[c] == expected, (c, by_cycle[c])
    assert by_cycle[BUDGET + 1] == ["law_set", "commit"]
    assert sum(e.kind == "result" for e in entries) == BUDGET


def test_predictions_cover_live_laws(oracle):
    _, _, entries = oracle
    live = None
    for e in entries:
        if e.kind == "law_set":
            live = {law["law_id"] for law in e.payload["laws"]}
        elif e.kind == "prediction_table":
            assert {p["law_id"] for p in e.payload["predictions"]} == live
            assert len(e.payload["predictions"]) == len(live)
        elif e.kind == "verdicts":
            assert {v["law_id"] for v in e.payload["verdicts"]} == live
        elif e.kind == "candidates":
            p = e.payload
            assert p["chosen"] == p["candidates"][0] and len(p["candidates"]) == len(p["gaps"]) <= 5


def test_tentative_matches_decision_diff(oracle):
    _, _, entries = oracle
    tentative = {e.cycle: e.payload["tentative_followup"] for e in entries if e.kind == "prediction_table"}
    for e in entries:
        if e.kind == "decision_diff":
            assert e.payload["tentative"] == tentative[e.cycle - 1]
            assert e.payload["changed"] == (e.payload["tentative"] != e.payload["actual"])


def test_commit(oracle):
    _, summary, entries = oracle
    commit = [e for e in entries if e.kind == "commit"]
    assert len(commit) == 1
    p = commit[0].payload
    assert len(p["shots"]) == 5 and len({s["target_id"] for s in p["shots"]}) == 5
    assert p["law_id"] == summary["selected_form"] and p["approval"] == "auto"
    assert p["claim"] == "law_identified"
