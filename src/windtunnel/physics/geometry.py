"""Airfoil / body geometry: storage, normalisation, repanelling and shape properties.

Coordinates are stored in Selig order: starting at the trailing edge, running
forward over the upper surface to the leading edge and back along the lower
surface to the trailing edge (counter-clockwise).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def natural_cubic_spline(s: np.ndarray, y: np.ndarray):
    """Return a callable natural cubic spline through (s, y). ``s`` must be increasing."""
    n = len(s)
    h = np.diff(s)
    if n < 3:
        return lambda t: np.interp(t, s, y)
    a = np.zeros((n, n))
    rhs = np.zeros(n)
    a[0, 0] = a[-1, -1] = 1.0
    for i in range(1, n - 1):
        a[i, i - 1] = h[i - 1]
        a[i, i] = 2 * (h[i - 1] + h[i])
        a[i, i + 1] = h[i]
        rhs[i] = 6 * ((y[i + 1] - y[i]) / h[i] - (y[i] - y[i - 1]) / h[i - 1])
    m = np.linalg.solve(a, rhs)

    def evaluate(t):
        t = np.asarray(t, dtype=float)
        k = np.clip(np.searchsorted(s, t) - 1, 0, n - 2)
        hk = h[k]
        dt0 = t - s[k]
        dt1 = s[k + 1] - t
        return (m[k] * dt1 ** 3 / (6 * hk) + m[k + 1] * dt0 ** 3 / (6 * hk)
                + (y[k] / hk - m[k] * hk / 6) * dt1 + (y[k + 1] / hk - m[k + 1] * hk / 6) * dt0)

    return evaluate


@dataclass
class Airfoil:
    """A closed 2-D section: airfoil or blunt body (unit chord / unit reference length)."""
    name: str
    x: np.ndarray
    y: np.ndarray
    category: str = "Other"
    description: str = ""
    blunt: bool = False          # True: no sharp trailing edge, no Kutta condition
    reference_length: float = 1.0  # chord for airfoils, frontal height for bodies
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.x = np.asarray(self.x, dtype=float)
        self.y = np.asarray(self.y, dtype=float)

    # ------------------------------------------------------------------ shape
    @property
    def leading_edge_index(self) -> int:
        return int(np.argmin(self.x))

    def surfaces(self):
        """Upper and lower surfaces, each ordered from LE to TE."""
        i = self.leading_edge_index
        upper = (self.x[: i + 1][::-1], self.y[: i + 1][::-1])
        lower = (self.x[i:], self.y[i:])
        return upper, lower

    def thickness_camber(self, n: int = 101):
        """Thickness and camber distributions on a cosine-spaced x/c grid."""
        (xu, yu), (xl, yl) = self.surfaces()
        xs = 0.5 * (1 - np.cos(np.linspace(0, np.pi, n)))
        x0, x1 = self.x.min(), self.x.max()
        xs = x0 + (x1 - x0) * xs
        yu_i = np.interp(xs, *_monotone(xu, yu))
        yl_i = np.interp(xs, *_monotone(xl, yl))
        return xs, yu_i - yl_i, 0.5 * (yu_i + yl_i)

    def properties(self) -> dict:
        xs, t, c = self.thickness_camber(201)
        it, ic = int(np.argmax(t)), int(np.argmax(np.abs(c)))
        chord = self.x.max() - self.x.min()
        return {
            "max thickness": t[it] / chord,
            "max thickness at x/c": (xs[it] - self.x.min()) / chord,
            "max camber": c[ic] / chord,
            "max camber at x/c": (xs[ic] - self.x.min()) / chord,
            "trailing-edge gap": float(np.hypot(self.x[0] - self.x[-1], self.y[0] - self.y[-1])) / chord,
            "points": len(self.x),
        }

    # ------------------------------------------------------------ processing
    def normalized(self) -> "Airfoil":
        """Scale/translate so the leading edge is at (0, 0) and chord = 1 (airfoils only)."""
        if self.blunt:
            return self
        x, y = self.x.copy(), self.y.copy()
        i_le = int(np.argmin(x))
        te = 0.5 * np.array([x[0] + x[-1], y[0] + y[-1]])
        le = np.array([x[i_le], y[i_le]])
        chord_vec = te - le
        chord = float(np.hypot(*chord_vec))
        ang = np.arctan2(chord_vec[1], chord_vec[0])
        ca, sa = np.cos(-ang), np.sin(-ang)
        xr = (x - le[0]) * ca - (y - le[1]) * sa
        yr = (x - le[0]) * sa + (y - le[1]) * ca
        return Airfoil(self.name, xr / chord, yr / chord, self.category, self.description,
                       self.blunt, 1.0, dict(self.meta))

    def repaneled(self, n_panels: int = 160) -> "Airfoil":
        """Respace points with a cubic spline in arc length, clustering near LE and TE."""
        x, y = _dedupe(self.x, self.y)
        s = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
        sx, sy = natural_cubic_spline(s, x), natural_cubic_spline(s, y)
        if self.blunt:
            # closed smooth body: uniform spacing in arc length is best
            t = np.linspace(0, s[-1], n_panels + 1)
        else:
            s_le = _spline_leading_edge(s, sx, sy, s[int(np.argmin(x))])
            n_up = n_panels // 2
            n_lo = n_panels - n_up
            beta_u = np.linspace(0, np.pi, n_up + 1)
            beta_l = np.linspace(0, np.pi, n_lo + 1)
            t_up = s_le * 0.5 * (1 - np.cos(beta_u))
            t_lo = s_le + (s[-1] - s_le) * 0.5 * (1 - np.cos(beta_l))
            t = np.concatenate([t_up, t_lo[1:]])
        return Airfoil(self.name, sx(t), sy(t), self.category, self.description,
                       self.blunt, self.reference_length, dict(self.meta))

    def with_closed_trailing_edge(self) -> "Airfoil":
        """Close a finite trailing-edge gap by joining both surfaces at the TE midpoint."""
        if self.blunt:
            return self
        x, y = self.x.copy(), self.y.copy()
        xm, ym = 0.5 * (x[0] + x[-1]), 0.5 * (y[0] + y[-1])
        x[0] = x[-1] = xm
        y[0] = y[-1] = ym
        return Airfoil(self.name, x, y, self.category, self.description, False,
                       self.reference_length, dict(self.meta))


def _dedupe(x, y):
    keep = np.concatenate([[True], np.hypot(np.diff(x), np.diff(y)) > 1e-10])
    return x[keep], y[keep]


def _monotone(xs, ys):
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    keep = np.concatenate([[True], np.diff(xs) > 1e-12])
    return xs[keep], ys[keep]


def _spline_leading_edge(s, sx, sy, guess):
    """Arc-length position where the surface tangent is perpendicular to the chord line."""
    x_te = 0.5 * (sx(s[0]) + sx(s[-1]))
    y_te = 0.5 * (sy(s[0]) + sy(s[-1]))
    lo, hi = max(guess - 0.05 * s[-1], s[0]), min(guess + 0.05 * s[-1], s[-1])
    ts = np.linspace(lo, hi, 400)
    dist = (sx(ts) - x_te) ** 2 + (sy(ts) - y_te) ** 2
    return float(ts[int(np.argmax(dist))])
