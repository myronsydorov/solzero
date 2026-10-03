"""Law compilation and the batched fixed-step RK4 integrator shared by tools/ and world/.

A trajectory starts at (x0, z0) with velocity (vx0, vz0) and stops when it crosses
z = z_stop moving downward. Many trajectories, each with its own mass and parameter
values, are integrated together as flat numpy arrays.
"""

from __future__ import annotations

import numpy as np
import sympy

from schemas import LAW_VARIABLES, Law, parse_law_expr

DT = 0.008  # s; validated against solve_ivp in tests/test_integrator.py (error ~1e-6 m)
T_MAX = 8.0  # s; trajectories still airborne after this return NaN
Z_MAX = 10.0  # m; trajectories leaving the workspace upward or sideways return NaN
X_MAX = 40.0


class CompiledLaw:
    """A Law turned into a vectorised acceleration function."""

    def __init__(self, law: Law):
        self.law = law
        self.param_names = list(law.params)
        local = {n: sympy.Symbol(n) for n in list(LAW_VARIABLES) + self.param_names}
        ax = parse_law_expr(law.ax, self.param_names)
        az = parse_law_expr(law.az, self.param_names)
        args = [local[n] for n in list(LAW_VARIABLES) + self.param_names]
        self._f = sympy.lambdify(args, [ax, az], modules="numpy", cse=True)

    def accel(self, m, z, vx, vz, params):
        """params: sequence of arrays (or scalars) in self.param_names order."""
        speed = np.sqrt(vx * vx + vz * vz)
        with np.errstate(all="ignore"):
            ax, az = self._f(m, z, vx, vz, speed, *params)
        shape = np.shape(z)
        return np.broadcast_to(ax, shape), np.broadcast_to(az, shape)

    def static_force(self, m, z, params):
        """Weigh observable: force on a sample at rest, m * |az(v = 0)|."""
        m = np.asarray(m, float)
        zeros = np.zeros(np.broadcast(m, z).shape)
        _, az = self.accel(m, np.asarray(z, float) + zeros, zeros, zeros, params)
        return m * np.abs(az)


def integrate(law: CompiledLaw, m, x0, z0, vx0, vz0, z_stop, params, dt=DT, t_max=T_MAX):
    """Integrate a batch of trajectories. Inputs are flat arrays of one length B (or
    scalars); params is a list in law.param_names order. Returns (x_cross, t_cross),
    NaN where the trajectory does not cross z_stop moving downward within t_max."""
    x = np.array(x0, float)
    B = x.shape[0]
    full = lambda a: np.broadcast_to(np.asarray(a, float), (B,)).copy()
    z, vx, vz, m, z_stop = full(z0), full(vx0), full(vz0), full(m), full(z_stop)
    params = [full(p) for p in params]
    x_out = np.full(B, np.nan)
    t_out = np.full(B, np.nan)
    idx = np.arange(B)
    h = dt
    t = 0.0
    while idx.size and t < t_max:
        ax1, az1 = law.accel(m, z, vx, vz, params)
        vx2, vz2 = vx + 0.5 * h * ax1, vz + 0.5 * h * az1
        ax2, az2 = law.accel(m, z + 0.5 * h * vz, vx2, vz2, params)
        vx3, vz3 = vx + 0.5 * h * ax2, vz + 0.5 * h * az2
        ax3, az3 = law.accel(m, z + 0.5 * h * vz2, vx3, vz3, params)
        vx4, vz4 = vx + h * ax3, vz + h * az3
        ax4, az4 = law.accel(m, z + h * vz3, vx4, vz4, params)
        x_new = x + h / 6 * (vx + 2 * vx2 + 2 * vx3 + vx4)
        z_new = z + h / 6 * (vz + 2 * vz2 + 2 * vz3 + vz4)
        vx_new = vx + h / 6 * (ax1 + 2 * ax2 + 2 * ax3 + ax4)
        vz_new = vz + h / 6 * (az1 + 2 * az2 + 2 * az3 + az4)

        crossed = (z_new < z_stop) & (z >= z_stop)
        bad = ~(np.isfinite(z_new) & np.isfinite(x_new) & np.isfinite(vz_new))
        bad |= (z_new > Z_MAX) | (np.abs(x_new) > X_MAX)
        if crossed.any():
            c = np.nonzero(crossed)[0]
            s = _hermite_root(z[c], z_new[c], h * vz[c], h * vz_new[c], z_stop[c])
            x_out[idx[c]] = _hermite(x[c], x_new[c], h * vx[c], h * vx_new[c], s)
            t_out[idx[c]] = t + s * h
        keep = ~crossed & ~bad
        if not keep.all():
            idx = idx[keep]
            x_new, z_new, vx_new, vz_new = x_new[keep], z_new[keep], vx_new[keep], vz_new[keep]
            m, z_stop = m[keep], z_stop[keep]
            params = [p[keep] for p in params]
        x, z, vx, vz = x_new, z_new, vx_new, vz_new
        t += h
    return x_out, t_out


def _hermite(p0, p1, d0, d1, s):
    """Cubic Hermite interpolant on a step, at fraction s; d0, d1 are h * derivative."""
    return ((2 * s**3 - 3 * s**2 + 1) * p0 + (s**3 - 2 * s**2 + s) * d0
            + (-2 * s**3 + 3 * s**2) * p1 + (s**3 - s**2) * d1)


def _hermite_root(p0, p1, d0, d1, target):
    """Fraction s in [0, 1] where the Hermite interpolant equals target (Newton from linear)."""
    s = np.clip((p0 - target) / (p0 - p1), 0.0, 1.0)
    for _ in range(4):
        val = _hermite(p0, p1, d0, d1, s) - target
        der = ((6 * s**2 - 6 * s) * p0 + (3 * s**2 - 4 * s + 1) * d0
               + (-6 * s**2 + 6 * s) * p1 + (3 * s**2 - 2 * s) * d1)
        s = np.clip(s - val / np.where(der == 0, -1e-12, der), 0.0, 1.0)
    return s
