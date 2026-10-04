"""Law-form analysis shared by grading and the scripted policies (SPEC.md section 7).

The law is reduced by zeroing every parameter within 2 sd of zero, then probed numerically
for mass dependence, height dependence and the drag exponent.
"""

from __future__ import annotations

import math

import numpy as np

from schemas import FitResult, Law
from tools.analysis import compile_law

DEP_TOL = 0.01  # a dependence counts when gravity changes by more than 1% over the range
P_TOL = 0.3
RHO_WAIVE = 0.05
ORDINARY_P = 2.0


def reduced_values(law: Law, fit: FitResult) -> dict[str, float]:
    """Fitted values, with every parameter within 2 sd of zero set to zero."""
    vals = {}
    for k in law.params:
        est = fit.params.get(k)
        v, sd = (est.value, est.sd) if est else (law.params[k].init, 0.0)
        vals[k] = 0.0 if abs(v) <= 2 * sd else v
    return vals


def law_dependence(law: Law, fit: FitResult) -> dict:
    """Probe the reduced law: does gravity depend on mass, on height; drag exponent."""
    cl = compile_law(law)
    vals = reduced_values(law, fit)
    P = [np.array([vals[k]]) for k in cl.param_names]

    def static_g(m, z):
        _, az = cl.accel(np.array([m]), np.array([z]), np.zeros(1), np.zeros(1), P)
        return float(-az[0])

    def drag_ax(v):
        ax, _ = cl.accel(np.array([0.1]), np.zeros(1), np.array([v]), np.zeros(1), P)
        return float(-ax[0])

    g_ref = static_g(0.1, 0.0)
    scale = max(abs(g_ref), 1e-9)
    mass_dep = abs(static_g(0.02, 0.0) - static_g(0.8, 0.0)) / scale > DEP_TOL
    height_dep = abs(static_g(0.1, 0.0) - static_g(0.1, 1.2)) / scale > DEP_TOL
    a1, a4 = drag_ax(1.0), drag_ax(4.0)
    if not (math.isfinite(a1) and math.isfinite(a4)) or (abs(a1) < 1e-9 and abs(a4) < 1e-9):
        p = None
    elif a1 <= 0 or a4 <= 0:
        p = float("nan")  # not a drag (does not oppose motion)
    else:
        p = math.log(a4 / a1) / math.log(4.0)
    return {"mass_dep": bool(mass_dep), "height_dep": bool(height_dep), "drag_p": p,
            "g_ref": g_ref, "reduced_params": vals}


def law_recovered(dep: dict, truth: dict) -> dict:
    fam = truth["family"]
    mass_ok = dep["mass_dep"] == (fam == "F2")
    height_ok = dep["height_dep"] == (fam == "F3")
    if truth["rho"] < RHO_WAIVE:
        p_ok = True
    else:
        p = dep["drag_p"]
        p_ok = p is not None and math.isfinite(p) and abs(p - truth["p"]) <= P_TOL
    return {"mass_ok": mass_ok, "height_ok": height_ok, "drag_ok": p_ok,
            "recovered": bool(mass_ok and height_ok and p_ok)}


def claims_non_ordinary(dep: dict) -> bool:
    """Non-ordinary unless gravity is constant and drag is quadratic (within 0.3) or absent."""
    p = dep["drag_p"]
    drag_ordinary = p is None or (math.isfinite(p) and abs(p - ORDINARY_P) <= P_TOL)
    return bool(dep["mass_dep"] or dep["height_dep"] or not drag_ordinary)


def claim_for(law: Law, fit: FitResult) -> bool:
    return claims_non_ordinary(law_dependence(law, fit))
