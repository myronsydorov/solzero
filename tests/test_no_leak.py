"""Leak test (AGENTS.md): lab/ must not name the hidden families, generator parameters or
library, and must not import from the hidden directories."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "lab"

# Family names and generator parameter names from world/generator.py and SPEC section 2.
FORBIDDEN_WORDS = [
    "drag-law shift", "drag law shift", "mass-dependent gravity", "height-dependent gravity",
    "12-form", "twelve forms", "form library", "kappa", "alpha", "rho", "g0",
    "F0", "F1", "F2", "F3",
]
FORBIDDEN_IMPORTS = re.compile(r"^\s*(from|import)\s+(world|calibration|sim|eval)\b", re.M)
TEXT_SUFFIXES = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".jinja", ".j2"}


def _files():
    return [p for p in LAB.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES]


@pytest.mark.skipif(not LAB.exists(), reason="lab/ is not on this branch yet")
def test_lab_has_no_leaks():
    hits = []
    for path in _files():
        text = path.read_text(errors="ignore")
        for word in FORBIDDEN_WORDS:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", text, re.I):
                hits.append(f"{path.relative_to(ROOT)}: {word!r}")
        if FORBIDDEN_IMPORTS.search(text):
            hits.append(f"{path.relative_to(ROOT)}: imports a hidden package")
    assert not hits, "leaks found:\n" + "\n".join(hits)
