"""Integral boundary-layer methods marched along the inviscid surface velocity.

* Laminar:     Thwaites' method (Cebeci-Bradshaw correlations for H and skin friction)
* Transition:  Michel's criterion, a forced trip location, or laminar separation
* Turbulent:   Head's entrainment method with the Ludwieg-Tillmann skin-friction law
* Drag:        Squire-Young formula at the trailing edge

All lengths are normalised by the chord (or body length), velocities by V_inf.
The coupling is one-way (no viscous-inviscid interaction), which is adequate
for attached flow and gives a useful estimate of where separation starts.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

H_TURB_SEPARATION = 2.0   # shape factor at which a turbulent layer is taken as separated
                          # (literature range 1.8-2.4; 2.0 matches NACA 0012 stall data)
LAMBDA_LAM_SEPARATION = -0.09
THETA_MAX = 0.03          # cap on momentum thickness / chord (massively separated flow)


@dataclass
class SurfaceBL:
    s: np.ndarray            # arc length from stagnation point
    x: np.ndarray            # x/c of each station
    ue: np.ndarray           # edge velocity / V_inf
    theta: np.ndarray        # momentum thickness / c
    H: np.ndarray            # shape factor
    cf: np.ndarray           # skin friction based on local edge dynamic pressure
    turbulent: np.ndarray    # bool per station
    x_transition: float | None
    x_separation: float | None   # turbulent separation (None = attached to TE)
    x_laminar_separation: float | None
    cd: float                # Squire-Young contribution of this surface

    @property
    def delta_star(self):
        return self.theta * self.H


def _thwaites_l_h(lam):
    lam = np.clip(lam, -0.1, 0.25)
    pos = lam >= 0
    l_pos = 0.22 + 1.57 * lam - 1.8 * lam ** 2
    h_pos = 2.61 - 3.75 * lam + 5.24 * lam ** 2
    l_neg = 0.22 + 1.402 * lam + 0.018 * lam / (lam + 0.107)
    h_neg = 2.088 + 0.0731 / (lam + 0.14)
    return np.where(pos, l_pos, l_neg), np.where(pos, h_pos, h_neg)


def _h1_from_h(h):
    return 3.3 + (0.8234 * (h - 1.1) ** -1.287 if h <= 1.6 else 1.5501 * (h - 0.6778) ** -3.064)


def _h_from_h1(h1):
    h1 = max(h1, 3.3 + 1e-6)
    return 0.6778 + 1.1536 * (h1 - 3.3) ** -0.326 if h1 <= 5.3 else 1.1 + 0.86 * (h1 - 3.3) ** -0.777


def _cf_ludwieg_tillmann(h, re_theta):
    return 0.246 * 10 ** (-0.678 * h) * max(re_theta, 10.0) ** -0.268


def march(s, x, ue, reynolds: float, trip_x: float | None = None, free_transition: bool = True) -> SurfaceBL:
    """March the boundary layer along one surface starting from the stagnation point (s[0] = 0)."""
    n = len(s)
    ue = np.maximum(np.asarray(ue, dtype=float), 1e-4)
    theta = np.zeros(n)
    H = np.zeros(n)
    cf = np.zeros(n)
    turb = np.zeros(n, dtype=bool)
    re = max(reynolds, 1e3)

    # --- laminar part (Thwaites) -------------------------------------------
    integrand = ue ** 5
    integral = np.concatenate([[0.0], np.cumsum(0.5 * (integrand[1:] + integrand[:-1]) * np.diff(s))])
    due = np.gradient(ue, s, edge_order=1)
    theta2 = np.empty(n)
    theta2[1:] = 0.45 / re * integral[1:] / ue[1:] ** 6
    theta2[0] = 0.075 / (re * max(due[0], (ue[1] - ue[0]) / max(s[1] - s[0], 1e-9), 1e-3))
    lam = theta2 * due * re
    l_lam, h_lam = _thwaites_l_h(lam)

    i_tr = None
    x_lam_sep = None
    for i in range(1, n):
        th = np.sqrt(max(theta2[i], 1e-14))
        re_theta = re * ue[i] * th
        re_x = max(re * ue[i] * s[i], 1.0)
        tripped = trip_x is not None and x[i] >= trip_x
        michel = free_transition and re_theta > 1.174 * (1 + 22400 / re_x) * re_x ** 0.46
        lam_sep = lam[i] < LAMBDA_LAM_SEPARATION and s[i] > 0.01
        if lam_sep and x_lam_sep is None:
            x_lam_sep = float(x[i])
        if tripped or michel or lam_sep:
            i_tr = i
            break
        theta[i] = th
        H[i] = h_lam[i]
        cf[i] = 2 * l_lam[i] / max(re_theta, 1e-9)
    theta[0] = np.sqrt(theta2[0])
    H[0] = h_lam[0]
    cf[0] = cf[1] if n > 1 else 0.0

    x_sep = None
    if i_tr is None:
        cd = _squire_young(theta[-1], H[-1], ue[-1])
        return SurfaceBL(s, x, ue, theta, H, cf, turb, None, None, x_lam_sep, cd)

    # --- turbulent part (Head) --------------------------------------------
    th = np.sqrt(max(theta2[i_tr], 1e-14))
    h = 1.4
    theta[i_tr], H[i_tr], turb[i_tr:] = th, h, True
    y = ue[i_tr] * th * _h1_from_h(h)
    separated = False
    for i in range(i_tr + 1, n):
        ds = s[i] - s[i - 1]
        dudx = (ue[i] - ue[i - 1]) / max(ds, 1e-12)
        sub = 4
        h_step = ds / sub
        for k in range(sub):
            u_a = ue[i - 1] + dudx * h_step * k
            u_b = u_a + dudx * h_step

            def rates(th_, y_, u_):
                if separated:
                    # separated shear layer: grows in adverse gradients, never shrinks
                    return max(-(H_TURB_SEPARATION + 2) * th_ / max(u_, 0.05) * dudx, 0.0), 0.0
                h1 = max(y_ / (u_ * th_), 3.3 + 1e-6)
                hh = _h_from_h1(h1)
                c_f = _cf_ludwieg_tillmann(hh, re * u_ * th_)
                dth = -(hh + 2) * th_ / u_ * dudx + 0.5 * c_f
                dy = u_ * 0.0306 * max(h1 - 3.0, 1e-6) ** -0.6169
                return dth, dy

            k1 = rates(th, y, u_a)
            k2 = rates(th + h_step * k1[0], y + h_step * k1[1], u_b)
            th = min(max(th + 0.5 * h_step * (k1[0] + k2[0]), 1e-9), THETA_MAX)
            y = y + 0.5 * h_step * (k1[1] + k2[1])
        theta[i] = th
        if separated:
            H[i] = H_TURB_SEPARATION
            cf[i] = 0.0
            continue
        h1 = y / (ue[i] * th)
        h = _h_from_h1(h1)
        H[i] = h
        cf[i] = _cf_ludwieg_tillmann(h, re * ue[i] * th)
        if h >= H_TURB_SEPARATION or h1 <= 3.31:
            separated = True
            x_sep = float(x[i])
            H[i] = H_TURB_SEPARATION
            cf[i] = 0.0
    cd = _squire_young(theta[-1], H[-1], ue[-1])
    return SurfaceBL(s, x, ue, theta, H, cf, turb, float(x[i_tr]), x_sep, x_lam_sep, cd)


def _squire_young(theta_te, h_te, ue_te):
    return 2 * theta_te * max(ue_te, 1e-3) ** ((min(h_te, 2.5) + 5) / 2)


def split_surfaces(xc, yc, vt):
    """Split a clockwise panel solution at the stagnation point.

    Returns two (s, x, ue, idx) tuples: upper surface and lower surface, each from
    the stagnation point towards the trailing edge.
    """
    n = len(vt)
    sign_change = np.where((vt[:-1] <= 0) & (vt[1:] > 0))[0]
    if len(sign_change) == 0:
        i = int(np.argmin(np.abs(vt)))
        frac = 0.0
    else:
        # choose the crossing closest to the leading edge (min x)
        i = int(sign_change[np.argmin(xc[sign_change])])
        frac = -vt[i] / (vt[i + 1] - vt[i])
    xs = xc[i] + frac * (xc[i + 1] - xc[i]) if i + 1 < n else xc[i]
    ys = yc[i] + frac * (yc[i + 1] - yc[i]) if i + 1 < n else yc[i]

    def build(indices, sign):
        px = np.concatenate([[xs], xc[indices]])
        py = np.concatenate([[ys], yc[indices]])
        s = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(px), np.diff(py)))])
        ue = np.concatenate([[0.0], sign * vt[indices]])
        return s, px, ue, indices

    upper = build(np.arange(i + 1, n), 1.0)
    lower = build(np.arange(i, -1, -1), -1.0)
    return upper, lower, (xs, ys)
