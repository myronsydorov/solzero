"""The 12-form library used by the scripted calibration policies and the oracle condition
(SPEC.md section 7): three gravity terms by four drag options. Never shown to agents."""

from __future__ import annotations

from schemas import Law

GRAVITY = {
    "const": ("g0", {}),
    "mass": ("g0*(m/0.1)**alpha", {"alpha": {"init": 0.0, "lo": -1.0, "hi": 1.0}}),
    "height": ("g0*(1 + kappa*z)", {"kappa": {"init": 0.0, "lo": -1.5, "hi": 1.5}}),
}
DRAG = {
    "none": None,
    "p1": ("c*vx/m", "c*vz/m"),
    "p2": ("c*speed*vx/m", "c*speed*vz/m"),
    "p3": ("c*speed**2*vx/m", "c*speed**2*vz/m"),
}
ORDINARY = {("const", "p2"), ("const", "none")}


def form_id(gravity: str, drag: str) -> str:
    return f"{gravity}_{drag}"


def library() -> dict[str, Law]:
    laws = {}
    for g, (gexpr, gparams) in GRAVITY.items():
        for d, dexpr in DRAG.items():
            params = {"g0": {"init": 9.81, "lo": 0.5, "hi": 30.0}, **gparams}
            if dexpr is None:
                ax, az = "0", f"-{gexpr}"
            else:
                params["c"] = {"init": 1e-3, "lo": 0.0, "hi": 2.0}
                ax, az = f"-{dexpr[0]}", f"-{gexpr} - {dexpr[1]}"
            fid = form_id(g, d)
            laws[fid] = Law(law_id=fid, description=f"gravity {g}, drag {d}", ax=ax, az=az, params=params)
    return laws


def true_form(family: str, p: float) -> str:
    gravity = {"F0": "const", "F1": "const", "F2": "mass", "F3": "height"}[family]
    return form_id(gravity, f"p{int(p)}")


def is_ordinary(fid: str) -> bool:
    g, d = fid.split("_")
    return (g, d) in ORDINARY
