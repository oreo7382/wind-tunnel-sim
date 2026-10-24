"""Flow fields around 2-D sections, for streamlines, contour plots and the probe.

Every field exposes the same small interface used by the GUI:

* ``bounds``                     (xmin, xmax, ymin, ymax) in reference lengths
* ``outlines``                   list of (x, y) arrays to draw as solid bodies
* ``velocity(x, y)``             velocity / V_inf as (u, v) arrays
* ``state(x, y)``                local physical state (speed, Mach, Cp, p, T, rho, p0)
* ``inside(x, y)``               boolean mask of points inside a body
* ``seeds(n)``                   upstream starting points for streamlines
* ``annotations``                extra drawing hints (Mach lines, markers, regions)
"""
from __future__ import annotations

import numpy as np
from matplotlib.path import Path

from . import compressibility as comp
from .atmosphere import Freestream
from .panel import PanelSolution


class FlowField:
    bounds = (-1.0, 2.0, -1.0, 1.0)
    outlines: list = []
    annotations: dict = {}

    def velocity(self, x, y):
        raise NotImplementedError

    def cp(self, x, y):
        raise NotImplementedError

    def state(self, x, y) -> dict:
        raise NotImplementedError

    def inside(self, x, y):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        mask = np.zeros(x.shape, dtype=bool)
        pts = np.column_stack([x.ravel(), y.ravel()])
        for path in self._paths():
            mask |= path.contains_points(pts).reshape(x.shape)
        return mask

    def _paths(self):
        if not hasattr(self, "_path_cache"):
            self._path_cache = [Path(np.column_stack(o)) for o in self.outlines]
        return self._path_cache

    def seeds(self, n: int):
        x0, x1, y0, y1 = self.bounds
        # denser seeding close to the body centre line
        t = np.linspace(-1, 1, n)
        ys = 0.5 * (y0 + y1) + 0.5 * (y1 - y0) * np.sign(t) * np.abs(t) ** 1.35
        return np.column_stack([np.full(n, x0), ys])

    def grid(self, nx: int = 220, ny: int = 150):
        x0, x1, y0, y1 = self.bounds
        X, Y = np.meshgrid(np.linspace(x0, x1, nx), np.linspace(y0, y1, ny))
        return X, Y

    def scalar_grid(self, kind: str, nx: int = 220, ny: int = 150):
        """Gridded scalar for a background contour: 'cp', 'speed_ratio' or 'mach'."""
        X, Y = self.grid(nx, ny)
        st = self.state(X.ravel(), Y.ravel())
        Z = np.ma.masked_array(st[kind].reshape(X.shape), mask=self.inside(X, Y))
        return X, Y, Z


class PanelFlowField(FlowField):
    """Panel-method potential flow, optionally with a subsonic compressibility correction.

    The velocity field (and so the streamline shapes) is the incompressible one;
    the local pressure, Mach number, density and speed are corrected with the
    chosen rule applied point by point to the incompressible Cp.
    """

    def __init__(self, panel: PanelSolution, alpha_rad: float, fs: Freestream, *, mach: float,
                 compressible: bool, correction: str, gamma_body: float = 0.0,
                 bounds=None, grid_cache: dict | None = None):
        self.panel = panel
        self.alpha = alpha_rad
        self.fs = fs
        self.mach = mach
        self.compressible = compressible
        self.correction = correction
        self.gamma_body = gamma_body
        body = panel.body
        self.outlines = [(np.append(body.x, body.x[0]), np.append(body.y, body.y[0]))]
        if bounds is None:
            if body.blunt:
                w = body.x.max() - body.x.min()
                bounds = (body.x.min() - 2.2, body.x.max() + 2.2 + w, -2.0, 2.0)
            else:
                bounds = (-1.0, 2.0, -0.9, 0.9)
        self.bounds = bounds
        self.annotations = {}
        self._grid_cache = grid_cache if grid_cache is not None else {}
        pad = 0.06 * (body.x.max() - body.x.min())
        self._near = (body.x.min() - pad, body.x.max() + pad, body.y.min() - pad, body.y.max() + pad)
        self._fast_grid()

    # --- fast velocity: bilinear interpolation of a cached basis grid, exact near the body
    def _fast_grid(self, nx=260, ny=170):
        key = (id(self.panel), self.bounds, nx, ny)
        if key not in self._grid_cache:
            x0, x1, y0, y1 = self.bounds
            gx, gy = np.linspace(x0, x1, nx), np.linspace(y0, y1, ny)
            X, Y = np.meshgrid(gx, gy)
            basis = self.panel.basis_velocity(X.ravel(), Y.ravel()).reshape(3, 2, ny, nx)
            self._grid_cache.clear()
            self._grid_cache[key] = (gx, gy, basis)
        gx, gy, basis = self._grid_cache[key]
        u, v = self.panel.combine_basis(basis, self.alpha, self.gamma_body)
        self._gx, self._gy, self._gu, self._gv = gx, gy, u, v

    def _interp(self, x, y):
        gx, gy = self._gx, self._gy
        fx = np.clip((x - gx[0]) / (gx[1] - gx[0]), 0, len(gx) - 1.000001)
        fy = np.clip((y - gy[0]) / (gy[1] - gy[0]), 0, len(gy) - 1.000001)
        i, j = fx.astype(int), fy.astype(int)
        tx, ty = fx - i, fy - j

        def bil(g):
            return ((1 - tx) * (1 - ty) * g[j, i] + tx * (1 - ty) * g[j, i + 1]
                    + (1 - tx) * ty * g[j + 1, i] + tx * ty * g[j + 1, i + 1])
        return bil(self._gu), bil(self._gv)

    def velocity(self, x, y):
        x = np.atleast_1d(np.asarray(x, dtype=float))
        y = np.atleast_1d(np.asarray(y, dtype=float))
        u, v = self._interp(x, y)
        x0, x1, y0, y1 = self._near
        near = (x > x0) & (x < x1) & (y > y0) & (y < y1)
        if near.any():
            ue, ve = self.panel.velocity(x[near], y[near], self.alpha, self.gamma_body)
            u[near], v[near] = ue, ve
        return u, v

    def velocity_exact(self, x, y):
        return self.panel.velocity(x, y, self.alpha, self.gamma_body)

    def cp(self, x, y, exact=True):
        u, v = self.velocity_exact(x, y) if exact else self.velocity(x, y)
        cp0 = 1.0 - (u * u + v * v)
        if self.compressible:
            return comp.correct_cp(cp0, self.mach, self.correction)
        return cp0

    def state(self, x, y, exact=True):
        cp = self.cp(x, y, exact)
        fs = self.fs if not self.compressible else Freestream.from_mach(self.fs.atmosphere, self.mach)
        st = comp.local_state(cp, fs, self.compressible)
        if self.compressible and abs(self.mach - self.fs.mach) > 1e-9:
            # Mach was clamped out of the transonic band: rescale to the real free stream
            st["speed"] = st["speed_ratio"] * self.fs.speed
        return st

    def scalar_grid(self, kind: str, nx: int = 220, ny: int = 150):
        X, Y = self.grid(nx, ny)
        st = self.state(X.ravel(), Y.ravel(), exact=False)
        Z = np.ma.masked_array(st[kind].reshape(X.shape), mask=self.inside(X, Y))
        return X, Y, Z


class SupersonicFlowField(FlowField):
    """Linearised (Ackeret / Busemann second-order) supersonic flow over a thin airfoil.

    Disturbances travel unchanged along Mach lines: a point above the airfoil takes
    the flow deflection of the upper-surface point where its left-running Mach line
    starts, a point below takes it from the lower surface. Pressures use Busemann's
    second-order formula; local states follow isentropically.
    """

    def __init__(self, x, y, alpha_rad: float, fs: Freestream, bounds=(-1.0, 2.0, -0.9, 0.9)):
        self.fs = fs
        self.mach = fs.mach
        self.alpha = alpha_rad
        self.bounds = bounds
        self.beta = comp.beta(self.mach)
        self.outlines = [(np.append(x, x[0]), np.append(y, y[0]))]
        ca, sa = np.cos(alpha_rad), np.sin(alpha_rad)
        # wind-frame coordinates (free stream along +X)
        X = x * ca + y * sa
        Y = -x * sa + y * ca
        i_le = int(np.argmin(X))
        xu, yu = X[: i_le + 1][::-1], Y[: i_le + 1][::-1]
        xl, yl = X[i_le:], Y[i_le:]
        self.x_le, self.x_te = float(X.min()), float(X.max())
        self._xs = np.linspace(self.x_le, self.x_te, 400)
        self._yu = np.interp(self._xs, *_sorted(xu, yu))
        self._yl = np.interp(self._xs, *_sorted(xl, yl))
        self._su = np.gradient(self._yu, self._xs)
        self._sl = np.gradient(self._yl, self._xs)
        self._ca, self._sa = ca, sa
        m = self.mach
        b = self.beta
        self.c1 = 2 / b
        self.c2 = ((comp.G + 1) * m ** 4 - 4 * b * b) / (2 * b ** 4)
        mu = np.arcsin(1 / m)
        lines = []
        for xs, ys in ((self.x_le, 0.0), (self.x_te, float(np.interp(self.x_te, self._xs, 0.5 * (self._yu + self._yl))))):
            for sgn in (1, -1):
                L = 3.0
                lines.append(self._to_body(np.array([xs, xs + L * np.cos(mu)]),
                                           np.array([ys, ys + sgn * L * np.sin(mu)])))
        self.annotations = {"mach_lines": lines, "mach_angle_deg": float(np.degrees(mu))}

    def _to_body(self, X, Y):
        return X * self._ca - Y * self._sa, X * self._sa + Y * self._ca

    def _deflection(self, x, y):
        """Flow deflection (rad, wind frame) at points; 0 outside the disturbed zone."""
        x = np.atleast_1d(np.asarray(x, dtype=float))
        y = np.atleast_1d(np.asarray(y, dtype=float))
        X = x * self._ca + y * self._sa
        Y = -x * self._sa + y * self._ca
        mid = np.interp(X, self._xs, 0.5 * (self._yu + self._yl), left=0.0,
                        right=float(0.5 * (self._yu[-1] + self._yl[-1])))
        upper = Y >= mid
        yu_ref = np.interp(X, self._xs, self._yu, left=0.0, right=float(self._yu[-1]))
        yl_ref = np.interp(X, self._xs, self._yl, left=0.0, right=float(self._yl[-1]))
        xi = np.where(upper, X - self.beta * (Y - yu_ref), X + self.beta * (Y - yl_ref))
        in_zone = (xi >= self.x_le) & (xi <= self.x_te)
        su = np.interp(xi, self._xs, self._su)
        sl = np.interp(xi, self._xs, self._sl)
        flow_angle = np.where(upper, su, sl)          # flow follows the surface slope
        compression = np.where(upper, su, -sl)        # turning into the flow compresses
        flow_angle = np.where(in_zone, flow_angle, 0.0)
        compression = np.where(in_zone, compression, 0.0)
        return flow_angle, compression

    def cp(self, x, y):
        _, delta = self._deflection(x, y)
        return np.clip(self.c1 * delta + self.c2 * delta ** 2, comp.cp_vacuum(self.mach), None)

    def state(self, x, y):
        return comp.local_state(self.cp(x, y), self.fs, True)

    def velocity(self, x, y):
        ang, _ = self._deflection(x, y)
        ratio = self.state(x, y)["speed_ratio"]
        U, V = ratio * np.cos(ang), ratio * np.sin(ang)
        return U * self._ca - V * self._sa, U * self._sa + V * self._ca

    def surface_cp(self, panel: PanelSolution):
        """Busemann Cp on each panel using the exact local inclination to the free stream."""
        d = np.array([np.cos(self.alpha), np.sin(self.alpha)])
        delta = np.arcsin(np.clip(-(panel.nx * d[0] + panel.ny * d[1]), -1, 1))
        cp = self.c1 * delta + self.c2 * delta ** 2
        cp_stag = stagnation_cp_supersonic(self.mach)
        return np.clip(cp, comp.cp_vacuum(self.mach), cp_stag), delta


def stagnation_cp_supersonic(m: float) -> float:
    """Cp at the stagnation point behind a normal shock (Rayleigh pitot formula)."""
    g = comp.G
    p02_p1 = (((g + 1) ** 2 * m * m) / (4 * g * m * m - 2 * (g - 1))) ** (g / (g - 1)) * \
        (1 - g + 2 * g * m * m) / (g + 1)
    return (p02_p1 - 1) / (0.5 * g * m * m)


def _sorted(xs, ys):
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    keep = np.concatenate([[True], np.diff(xs) > 1e-12])
    return xs[keep], ys[keep]


class SphereFlowField(FlowField):
    """Exact potential flow past a sphere, shown in a meridian plane (axisymmetric)."""

    def __init__(self, fs: Freestream, *, mach: float, compressible: bool, correction: str,
                 radius: float = 0.5, bounds=(-2.7, 3.7, -2.0, 2.0)):
        self.fs, self.mach, self.compressible, self.correction = fs, mach, compressible, correction
        self.a = radius
        self.bounds = bounds
        t = np.linspace(0, 2 * np.pi, 200)
        self.outlines = [(radius * np.cos(t), radius * np.sin(t))]
        self.annotations = {}

    def velocity(self, x, y):
        x = np.atleast_1d(np.asarray(x, dtype=float))
        r_axis = np.abs(np.atleast_1d(np.asarray(y, dtype=float)))
        R2 = np.maximum(x * x + r_axis * r_axis, 1e-12)
        a3 = self.a ** 3
        R5 = R2 ** 2.5
        # potential phi = U x (1 + a^3 / (2 R^3))
        u = 1 + a3 / (2 * R2 ** 1.5) - 1.5 * a3 * x * x / R5
        vr = -1.5 * a3 * x * r_axis / R5
        return u, vr * np.sign(np.atleast_1d(y) + 1e-300)

    def cp(self, x, y):
        u, v = self.velocity(x, y)
        cp0 = 1 - (u * u + v * v)
        return comp.correct_cp(cp0, self.mach, self.correction) if self.compressible else cp0

    def state(self, x, y):
        fs = Freestream.from_mach(self.fs.atmosphere, self.mach) if self.compressible else self.fs
        st = comp.local_state(self.cp(x, y), fs, self.compressible)
        st["speed"] = st["speed_ratio"] * self.fs.speed
        return st

    def inside(self, x, y):
        return np.asarray(x) ** 2 + np.asarray(y) ** 2 < self.a ** 2


def trace_streamlines(field: FlowField, seeds: np.ndarray, step: float = 0.012, max_steps: int = 900):
    """Integrate streamlines with fixed arc-length RK4 steps (vectorised over seeds)."""
    x0, x1, y0, y1 = field.bounds
    pts = np.array(seeds, dtype=float)
    n = len(pts)
    paths = [[p.copy()] for p in pts]
    alive = np.ones(n, dtype=bool)

    def direction(p):
        u, v = field.velocity(p[:, 0], p[:, 1])
        mag = np.hypot(u, v)
        mag = np.where(mag < 1e-9, 1e-9, mag)
        return np.column_stack([u / mag, v / mag]), mag

    for _ in range(max_steps):
        idx = np.where(alive)[0]
        if len(idx) == 0:
            break
        p = pts[idx]
        k1, mag = direction(p)
        k2, _ = direction(p + 0.5 * step * k1)
        k3, _ = direction(p + 0.5 * step * k2)
        k4, _ = direction(p + step * k3)
        new = p + step / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
        out = (new[:, 0] < x0) | (new[:, 0] > x1) | (new[:, 1] < y0) | (new[:, 1] > y1)
        stuck = mag < 1e-3
        hit = field.inside(new[:, 0], new[:, 1])
        for k, i in enumerate(idx):
            if hit[k] or stuck[k]:
                alive[i] = False
                continue
            paths[i].append(new[k].copy())
            if out[k]:
                alive[i] = False
        pts[idx] = new
    return [np.array(p) for p in paths if len(p) > 2]
