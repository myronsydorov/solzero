"""eval/: sampler, law grading, and one full pipeline pass on a dev world."""

import json
import subprocess
import sys

import pytest

from calibration.library import library
from eval.grade import law_dependence, law_recovered
from eval.sampler import random_specs
from schemas import FitResult, Law, ParamEstimate


def fit_of(law: Law, values: dict, sds: dict | None = None) -> FitResult:
    sds = sds or {}
    return FitResult(law_id=law.law_id, params={k: ParamEstimate(value=v, sd=sds.get(k, 0.0)) for k, v in values.items()},
                     chi2_dof=1.0, n_experiments=12, converged=True)


def test_sampler_is_deterministic_and_in_range():
    a, b = random_specs(1000), random_specs(1000)
    assert a == b and len(a) == 12 and a != random_specs(1001)
    assert all(s.sample_id != "mission_300" for s in a if s.type != "weigh")


@pytest.mark.parametrize("fid,truth,expected", [
    ("const_p2", {"family": "F0", "p": 2, "rho": 0.4}, True),
    ("const_p3", {"family": "F0", "p": 2, "rho": 0.4}, False),
    ("mass_p2", {"family": "F2", "p": 2, "rho": 0.4}, True),
    ("mass_p2", {"family": "F0", "p": 2, "rho": 0.4}, False),
    ("height_p1", {"family": "F3", "p": 1, "rho": 0.4}, True),
    ("const_none", {"family": "F0", "p": 2, "rho": 0.01}, True),  # exponent check waived
    ("const_none", {"family": "F0", "p": 2, "rho": 0.4}, False),
])
def test_law_grading(fid, truth, expected):
    law = library()[fid]
    vals = {"g0": 7.0, "alpha": 0.3, "kappa": 0.4, "c": 0.02}
    fit = fit_of(law, {k: vals[k] for k in law.params})
    assert law_recovered(law_dependence(law, fit), truth)["recovered"] is expected


def test_terms_within_two_sd_are_dropped():
    law = library()["mass_p2"]
    fit = fit_of(law, {"g0": 7.0, "alpha": 0.05, "c": 0.02}, {"alpha": 0.03})
    dep = law_dependence(law, fit)
    assert dep["mass_dep"] is False and dep["reduced_params"]["alpha"] == 0.0
    assert law_recovered(dep, {"family": "F0", "p": 2, "rho": 0.4})["recovered"]


def test_rate_limit_detection():
    from eval.run import rate_limit_wait

    assert rate_limit_wait({"status": "committed", "error": None}, "", 900) is None
    assert rate_limit_wait({"status": "error", "error": "ValueError: bad law"}, "", 900) is None
    assert rate_limit_wait({"status": "rate_limited", "retry_after_s": 5}, "", 900) == 5
    assert rate_limit_wait({"status": "error", "error": "429 Too Many Requests"}, "", 900) == 900
    w = rate_limit_wait({"status": "failed", "error": "RuntimeError: You've hit your session limit · resets "
                         "5:30am (Europe/Berlin)"}, "", 900)
    assert 120 <= w <= 24 * 3600 + 120
    assert rate_limit_wait(None, "... session limit · resets 11pm (UTC)", 900) is not None


def test_rate_limited_run_pauses_and_resumes(tmp_path):
    """A provider limit pauses the batch and the run continues in the same session."""
    import os

    py = sys.executable
    env = {**os.environ, "SOLZERO_STUB_RATE_LIMIT_AFTER": "3"}
    subprocess.run([py, "-m", "eval.run", "--condition", "lab", "--agent-cmd", f"{py} -m eval.agent_stub",
                    "--seeds", "1000", "--out", str(tmp_path), "--serve", "--max-concurrency", "2"],
                   check=True, timeout=300, env=env)
    state = json.loads((tmp_path / "lab" / "1000" / "state.json").read_text())
    m = json.loads((tmp_path / "lab" / "1000" / "metrics.json").read_text())
    assert state["status"] == "done" and state["attempts"] == 1 and state["rate_limit_pauses"] == 1
    assert m["experiments_used"] == 12 and m["rate_limit_pauses"] == 1


def test_pipeline_textbook_and_stub(tmp_path):
    """Runner, server, scripted reference, agent-CLI stub, grading and report on one dev world."""
    py = sys.executable
    for args in (["--condition", "textbook"],
                 ["--condition", "lab", "--agent-cmd", f"{py} -m eval.agent_stub"]):
        subprocess.run([py, "-m", "eval.run", *args, "--seeds", "1000", "--out", str(tmp_path), "--serve"],
                       check=True, timeout=300)
    for label in ("textbook", "lab"):
        m = json.loads((tmp_path / label / "1000" / "metrics.json").read_text())
        assert m["run_status"] == "done" and m["mission_hit_rate"] is not None
    lab = json.loads((tmp_path / "lab" / "1000" / "metrics.json").read_text())
    assert lab["n_experiments"] == 12 and lab["abstained"]
    subprocess.run([py, "-m", "eval.grade", str(tmp_path)], check=True)
    subprocess.run([py, "-m", "eval.report", str(tmp_path)], check=True, capture_output=True)
    assert (tmp_path / "report" / "summary_table.md").exists()
