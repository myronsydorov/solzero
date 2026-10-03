"""Derive the hidden vocabulary from SPEC section 2, never from held-out data."""
from pathlib import Path
import ast
import re

ROOT = Path(__file__).resolve().parents[1]


def forbidden_terms():
    section = (ROOT / "SPEC.md").read_text().split("## 2.", 1)[1].split("## 3.", 1)[0]
    terms = set()
    for code, name in re.findall(r"^\| (F\d+) ([^|]+)\|", section, re.M):
        terms.add(code)
        terms.add(name.strip().replace(" (control)", ""))
    for cell in re.findall(r"^\| ([^|]+)\|", section, re.M):
        cell = cell.strip()
        if cell in {"g0", "alpha", "kappa"}:
            terms.add(cell)
        if cell.startswith("Drag strength "):
            terms.add(cell.removeprefix("Drag strength "))
    # The equation names these force-law parameters in addition to table rows.
    equation = section.split("```", 2)[1]
    equation_symbols = set(re.findall(r"\b[A-Za-z_]\w*\b", equation))
    terms.update(equation_symbols - {"m", "dv", "dt", "g", "z", "z_hat", "v"})
    return terms


def test_lab_contains_no_hidden_vocabulary():
    forbidden = forbidden_terms()
    assert {"F0", "F1", "F2", "F3", "g0", "rho", "alpha", "kappa", "c", "p"} <= forbidden
    violations = []
    for path in (ROOT / "lab").rglob("*"):
        if path.suffix not in {".py", ".yaml", ".yml", ".md", ".txt"}:
            continue
        content = path.read_text()
        for term in sorted(forbidden):
            if re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", content, re.I):
                violations.append(f"{path.relative_to(ROOT)}: {term}")
    assert not violations, "Hidden vocabulary in lab/: " + ", ".join(violations)


def test_lab_has_no_forbidden_imports_or_admin_routes():
    forbidden = {"world", "calibration", "sim", "eval", "mock"}
    for path in (ROOT / "lab").rglob("*.py"):
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                assert not any(alias.name.split(".")[0] in forbidden for alias in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden, path
        assert "/admin/" not in source, path


def test_generated_tool_schemas_contain_no_hidden_vocabulary():
    import json
    from lab.policies import ROLE_TOOLS
    from lab.run import tool_schema
    for name in sorted(set().union(*ROLE_TOOLS.values())):
        content = json.dumps(tool_schema(name))
        for term in forbidden_terms():
            assert not re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", content, re.I), (name, term)
