"""Finite (3-D) wing analysis with a vortex-lattice method (VLM).

* Horseshoe vortices on a lattice of panels (bound leg at the panel 1/4 chord,
  control point at 3/4 chord, trailing legs to infinity downstream).
* Section camber enters through the airfoil's zero-lift angle; twist is linear.
* Compressibility: Prandtl-Glauert / Goethert rule (geometry stretched by 1/beta in x).
* Induced drag from a Trefftz-plane analysis of the trailing vortex sheet.
* Optional viscous correction: each strip is iterated onto the 2-D viscous polar
  of its airfoil ("decambering"), which captures wing stall, and profile drag
  is integrated strip by strip.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import airfoil2d as a2
from . import compressibility as comp
from .atmosphere import Freestream

FOUR_PI = 4 * np.pi


@dataclass
class Planform:
    span: float = 8.0               # tip to tip, m
    root_chord: float = 1.4         # m
    taper: float = 0.5              # tip chord / root chord
    sweep_deg: float = 0.0          # quarter-chord sweep
    dihedral_deg: float = 0.0
    twist_deg: float = 0.0          # tip incidence relative to root (negative = washout)
    n_span: int = 40
    n_chord: int = 6

    @property
    def tip_chord(self):
        return self.root_chord * self.taper

    @property
    def area(self):
        return 0.5 * self.span * (self.root_chord + self.tip_chord)

    @property
    def aspect_ratio(self):
        return self.span ** 2 / self.area

    @property
    def mac(self):
        lam = self.taper
        return 2 / 3 * self.root_chord * (1 + lam + lam * lam) / (1 + lam)

    def chord_at(self, y):
        return self.root_chord * (1 - (1 - self.taper) * np.abs(2 * np.asarray(y) / self.span))

    def x_quarter_at(self, y):
        return np.abs(y) * np.tan(np.radians(self.sweep_deg))

    def z_at(self, y):
        return np.abs(y) * np.tan(np.radians(self.dihedral_deg))

    def twist_at(self, y):
        return self.twist_deg * np.abs(2 * np.asarray(y) / self.span)


@dataclass
class WingResult:
    planform: Planform
    alpha_deg: float
    mach: float
    CL: float
    CDi: float
    CD0: float
    CDw: float
    Cm: float
    e: float
    CL_alpha: float           # per rad, linear VLM
    y: np.ndarray             # strip centres
    chord: np.ndarray
    cl: np.ndarray            # local section lift coefficient
    gamma: np.ndarray         # strip circulation / (V b)
    alpha_i: np.ndarray       # induced angle (deg)
    alpha_eff: np.ndarray     # effective section angle (deg)
    cd: np.ndarray            # section profile drag
    stalled: np.ndarray
    panels: np.ndarray        # (n_span, n_chord, 4, 3) panel corners (unstretched)
    dcp: np.ndarray           # (n_span, n_chord) panel loading coefficient
    lattice: "Lattice"
    freestream: Freestream
    warnings: list = field(default_factory=list)

    @property
    def CD(self):
        return self.CDi + self.CD0 + self.CDw

    @property
    def q(self):
        return self.freestream.dynamic_pressure

    @property
    def lift(self):
        return self.CL * self.q * self.planform.area

    @property
    def drag(self):
        return self.CD * self.q * self.planform.area

    @property
    def lift_to_drag(self):
        return self.CL / self.CD if self.CD > 0 else float("inf")

    def strip_at(self, eta: float) -> int:
        """Index of the strip nearest to the spanwise station eta = 2y/b (-1..1)."""
        return int(np.argmin(np.abs(self.y - eta * self.planform.span / 2)))


class Lattice:
    """Horseshoe-vortex lattice for a planform (optionally stretched in x by 1/beta)."""

    def __init__(self, pf: Planform, x_stretch: float = 1.0):
        self.pf = pf
        ns, nc = pf.n_span, pf.n_chord
        theta = np.linspace(np.pi, 0, ns + 1)
        ye = 0.5 * pf.span * np.cos(theta)          # strip edges, cosine spaced, -b/2 .. b/2
        self.y_edges = ye
        c_e = pf.chord_at(ye)
        xle_e = pf.x_quarter_at(ye) - 0.25 * c_e
        z_e = pf.z_at(ye)
        f = np.linspace(0, 1, nc + 1)
        # panel corner grid: (ns+1, nc+1, 3)
        X = xle_e[:, None] + f[None, :] * c_e[:, None]
        Y = np.repeat(ye[:, None], nc + 1, axis=1)
        Z = np.repeat(z_e[:, None], nc + 1, axis=1)
        self.corners = np.stack([X, Y, Z], axis=-1)
        Xs = X * x_stretch
        # bound vortex endpoints (1/4 of each panel) and control points (3/4)
        def lerp(a, b, t):
            return a + (b - a) * t
        xb = lerp(Xs[:, :-1], Xs[:, 1:], 0.25)                   # (ns+1, nc)
        self.A = np.stack([xb[:-1], Y[:-1, :-1], Z[:-1, :-1]], axis=-1).reshape(-1, 3)
        self.B = np.stack([xb[1:], Y[1:, :-1], Z[1:, :-1]], axis=-1).reshape(-1, 3)
        # control stations at the midpoints in theta (not in y): with cosine spacing this
        # keeps the tip strips well behaved (equivalent to Lan's quasi-vortex-lattice choice)
        theta_mid = 0.5 * (theta[:-1] + theta[1:])
        y_mid = 0.5 * pf.span * np.cos(theta_mid)
        t = ((y_mid - ye[:-1]) / np.diff(ye))[:, None]
        xc_edge = lerp(Xs[:, :-1], Xs[:, 1:], 0.75)
        xc = lerp(xc_edge[:-1], xc_edge[1:], t)
        yc = np.repeat(y_mid[:, None], nc, axis=1)
        zc = lerp(Z[:-1, :-1], Z[1:, :-1], t)
        self.C = np.stack([xc, yc, zc], axis=-1).reshape(-1, 3)
        self.ns, self.nc = ns, nc
        self.y_mid = y_mid
        self.dy = np.diff(ye)
        self.ds = np.hypot(np.diff(ye), np.diff(z_e))
        self.chord = pf.chord_at(self.y_mid)
        phi = np.radians(pf.dihedral_deg) * np.sign(self.y_mid)
        self.phi = phi
        self.core = 1e-9 * pf.span                 # numerically point-like for the AIC
        self.field_core = 0.03 * pf.mac            # smoothed core for flow visualisation
        self.x_stretch = x_stretch
        self._aic = None

    def normals(self, incidence_rad):
        """Panel normals for strip incidences (ns,), repeated chordwise."""
        eps = np.repeat(incidence_rad, self.nc)
        phi = np.repeat(self.phi, self.nc)
        return np.column_stack([np.sin(eps), -np.cos(eps) * np.sin(phi), np.cos(eps) * np.cos(phi)])

    def induced(self, P, gamma=None, core=None):
        """Velocity induced at points P (m,3) by unit horseshoes (m, n, 3), or summed with gamma."""
        P = np.asarray(P, dtype=float)
        core = self.core if core is None else core
        A, B = self.A[None, :, :], self.B[None, :, :]
        Pp = P[:, None, :]
        d = np.array([1.0, 0.0, 0.0])
        v = _segment(Pp, A, B, core) + _semi_infinite(Pp, B, d, core) - _semi_infinite(Pp, A, d, core)
        if gamma is None:
            return v
        return np.einsum("mnk,n->mk", v, gamma)

    def aic(self):
        if self._aic is None:
            self._aic = self.induced(self.C)
        return self._aic


def _segment(P, A, B, core):
    r0 = B - A
    r1 = P - A
    r2 = P - B
    cross = np.cross(r1, r2)
    c2 = np.sum(cross * cross, axis=-1) + (core ** 2) * np.sum(r0 * r0, axis=-1)
    n1 = np.linalg.norm(r1, axis=-1) + 1e-12
    n2 = np.linalg.norm(r2, axis=-1) + 1e-12
    k = np.sum(r0 * (r1 / n1[..., None] - r2 / n2[..., None]), axis=-1) / (FOUR_PI * c2)
    return cross * k[..., None]


def _semi_infinite(P, A, d, core):
    r1 = P - A
    cross = np.cross(d, r1)
    c2 = np.sum(cross * cross, axis=-1) + core ** 2
    n1 = np.linalg.norm(r1, axis=-1) + 1e-12
    k = (1 + np.sum(r1 * d, axis=-1) / n1) / (FOUR_PI * c2)
    return cross * k[..., None]


class WingModel:
    """Caches the lattice and the section polar so that sweeps are fast."""

    def __init__(self, planform: Planform, section: a2.Section):
        self.pf = planform
        self.section = section
        self.alpha_l0_deg, self.a_inv = section.zero_lift_alpha()   # deg, per rad
        self._lattices = {}
        self._polar_key = None
        self._polar = None

    def lattice(self, mach: float) -> Lattice:
        b = round(comp.beta(min(mach, 0.95)), 4)
        if b not in self._lattices:
            self._lattices[b] = Lattice(self.pf, 1.0 / b)
        return self._lattices[b]

    def polar(self, fs: Freestream, settings: a2.Settings):
        re = fs.reynolds(self.pf.mac)
        key = (round(np.log10(max(re, 1e3)), 2), settings.transition, settings.viscous)
        if key != self._polar_key:
            atm = fs.atmosphere
            low = Freestream(atm, min(fs.speed, 0.2 * atm.speed_of_sound))
            s = a2.Settings(**{**settings.__dict__, "flow_model": "Incompressible", "reynolds_override": re})
            alphas = np.arange(-20.0, 30.5, 1.0)
            self._polar = a2.polar(self.section, low, s, alphas)
            self._polar_key = key
        return self._polar

    def solve(self, alpha_deg: float, fs: Freestream, settings: a2.Settings, viscous_iteration: bool = True):
        pf = self.pf
        mach = fs.mach
        warnings = []
        if mach >= 0.95:
            warnings.append("Vortex-lattice theory is subsonic only; results are evaluated at M = 0.95.")
        m_eff = min(mach, 0.95)
        beta = comp.beta(m_eff)
        lat = self.lattice(m_eff)
        alpha = np.radians(alpha_deg)
        vinf = np.array([np.cos(alpha), 0.0, np.sin(alpha)])
        base_inc = np.radians(pf.twist_at(lat.y_mid) - self.alpha_l0_deg)
        aic = lat.aic()

        def solve_gamma(extra):
            n = lat.normals(base_inc + extra)
            A = np.einsum("mnk,mk->mn", aic, n)
            rhs = -(n @ vinf)
            return np.linalg.solve(A, rhs)

        def strip_cl(g):
            gs = g.reshape(lat.ns, lat.nc).sum(axis=1)
            return gs, 2 * gs / lat.chord

        delta = np.zeros(lat.ns)
        g = solve_gamma(delta)
        gs, cl_v = strip_cl(g)
        a_inv = self.a_inv / beta
        viscous = settings.viscous and viscous_iteration
        polar = self.polar(fs, settings) if settings.viscous else None
        alpha_e = np.degrees(cl_v / a_inv) + self.alpha_l0_deg
        if viscous:
            for _ in range(60):
                alpha_e = np.degrees(cl_v / a_inv - delta) + self.alpha_l0_deg
                target = np.interp(alpha_e, polar["alpha"], polar["cl"]) / beta
                change = 0.35 * (target - cl_v) / a_inv
                delta = delta + change
                g = solve_gamma(delta)
                gs, cl_v = strip_cl(g)
                if np.max(np.abs(change)) < 1e-5:
                    break
            alpha_e = np.degrees(cl_v / a_inv - delta) + self.alpha_l0_deg

        # forces: lift by Kutta-Joukowski on bound legs, induced drag in the Trefftz plane
        S = pf.area
        CL = float(np.sum(2 * gs * lat.dy) / S)
        w_n = _trefftz_normalwash(lat, gs)
        CDi = float(-np.sum(gs * w_n * lat.ds) / S)
        alpha_i = np.degrees(-w_n / 2)
        if not viscous:
            alpha_e = alpha_deg + pf.twist_at(lat.y_mid) + alpha_i
        e = CL ** 2 / (np.pi * pf.aspect_ratio * CDi) if CDi > 1e-9 else 1.0

        # pitching moment about the quarter chord of the mean aerodynamic chord
        A = lat.A.reshape(lat.ns, lat.nc, 3)
        x_bound = 0.5 * (A[..., 0] + lat.B.reshape(lat.ns, lat.nc, 3)[..., 0]) / lat.x_stretch
        lam = pf.taper
        y_mac = pf.span / 6 * (1 + 2 * lam) / (1 + lam)
        x_ref = pf.x_quarter_at(y_mac)
        gpan = g.reshape(lat.ns, lat.nc)
        Cm = float(-np.sum(2 * gpan * lat.dy[:, None] * (x_bound - x_ref)) / (S * pf.mac))

        cd = np.zeros(lat.ns)
        CD0 = 0.0
        if settings.viscous:
            cd = np.interp(alpha_e, polar["alpha"], polar["cd"]) * comp.skin_friction_compressibility_factor(mach)
            CD0 = float(np.sum(cd * lat.chord * lat.dy) / S)
        cl_max_2d = float(np.max(polar["cl"])) if polar is not None else np.inf
        alpha_stall = float(polar["alpha"][int(np.argmax(polar["cl"]))]) if polar is not None else np.inf
        stalled = alpha_e > alpha_stall if polar is not None else np.zeros(lat.ns, bool)
        if stalled.any():
            eta = np.abs(2 * lat.y_mid[stalled] / pf.span)
            warnings.append(f"Sections are stalled between |2y/b| = {eta.min():.2f} and {eta.max():.2f} "
                            f"(2-D cl_max = {cl_max_2d:.2f} at {alpha_stall:.0f} deg).")

        # wave drag with simple sweep theory
        CDw = 0.0
        sweep = np.radians(pf.sweep_deg)
        if mach > 0.3:
            vt, cp0 = self.section.inviscid(np.radians(self.alpha_l0_deg + np.degrees(CL / max(self.a_inv, 1e-6))))
            mcrit2d = comp.critical_mach(float(cp0.min()), settings.correction)
            mcrit = min(mcrit2d / np.sqrt(max(np.cos(sweep), 0.2)), 0.99)
            CDw = comp.lock_wave_drag(mach * 1.0, mcrit)
            if mach > mcrit:
                warnings.append(f"Above the wing critical Mach number ({mcrit:.3f}): wave drag added.")

        # panel loading coefficient: dCp = 2 Gamma / (panel chord)  (V = 1)
        pan_c = lat.chord[:, None] / lat.nc
        dcp = 2 * gpan / pan_c
        # linear lift slope for reference
        g2 = solve_gamma(delta + np.radians(1.0))
        CL2 = float(np.sum(2 * strip_cl(g2)[0] * lat.dy) / S)
        corners = np.stack([lat.corners[:-1, :-1], lat.corners[1:, :-1], lat.corners[1:, 1:],
                            lat.corners[:-1, 1:]], axis=2)
        return WingResult(pf, alpha_deg, mach, CL, CDi, CD0, CDw, Cm, float(e),
                          float(np.degrees(CL2 - CL)), lat.y_mid, lat.chord, cl_v, gs / pf.span,
                          alpha_i, alpha_e, cd, stalled, corners, dcp, lat, fs, warnings)


def _trefftz_normalwash(lat: Lattice, gs: np.ndarray):
    """Normal-wash (positive along the strip normal) far downstream, per unit V."""
    ye = lat.y_edges
    ze = lat.pf.z_at(ye)
    g_edges = np.concatenate([[0.0], gs, [0.0]])
    shed = g_edges[:-1] - g_edges[1:]           # trailing vortex strength at each strip edge
    yc, zc = lat.y_mid, lat.pf.z_at(lat.y_mid)
    ry = yc[:, None] - ye[None, :]
    rz = zc[:, None] - ze[None, :]
    r2 = ry * ry + rz * rz + 1e-12
    vy = np.sum(shed[None, :] * (-rz) / (2 * np.pi * r2), axis=1)
    vz = np.sum(shed[None, :] * ry / (2 * np.pi * r2), axis=1)
    return -np.sin(lat.phi) * vy + np.cos(lat.phi) * vz


def wing_velocity(result: WingResult, points):
    """Velocity / V_inf at 3-D points (for streamlines).

    Each strip is lumped into one horseshoe vortex on its quarter-chord line: the same
    far field as the full lattice and several times cheaper to evaluate.
    """
    lat = result.lattice
    A = lat.A.reshape(lat.ns, lat.nc, 3)[:, 0].copy()
    B = lat.B.reshape(lat.ns, lat.nc, 3)[:, 0].copy()
    A[:, 0] = lat.pf.x_quarter_at(lat.y_edges[:-1])
    B[:, 0] = lat.pf.x_quarter_at(lat.y_edges[1:])
    gamma = result.gamma * lat.pf.span
    P = np.asarray(points, dtype=float)[:, None, :]
    alpha = np.radians(result.alpha_deg)
    d = np.array([np.cos(alpha), 0.0, np.sin(alpha)])   # wake follows the free stream
    core = lat.field_core
    v = (_segment(P, A[None], B[None], core) + _semi_infinite(P, B[None], d, core)
         - _semi_infinite(P, A[None], d, core))
    v = np.einsum("mnk,n->mk", v, gamma)
    v[:, 0] += np.cos(alpha)
    v[:, 2] += np.sin(alpha)
    return v


def trace_3d(result: WingResult, seeds, step=None, n_steps=500):
    """RK2 streamlines in 3-D (used for the tip-vortex visualisation)."""
    pf = result.planform
    step = step or 0.02 * pf.root_chord
    pts = np.array(seeds, dtype=float)
    paths = [pts.copy()]
    for _ in range(n_steps):
        v1 = wing_velocity(result, pts)
        v1 /= np.linalg.norm(v1, axis=1, keepdims=True) + 1e-12
        mid = pts + 0.5 * step * v1
        v2 = wing_velocity(result, mid)
        v2 /= np.linalg.norm(v2, axis=1, keepdims=True) + 1e-12
        pts = pts + step * v2
        paths.append(pts.copy())
    return np.stack(paths, axis=1)


def datcom_lift_slope(aspect_ratio: float, mach: float, sweep_half_chord_deg: float, kappa: float = 1.0):
    """DATCOM/Helmbold lift-curve slope (per rad), for checking the VLM."""
    b = comp.beta(min(mach, 0.95))
    t = np.tan(np.radians(sweep_half_chord_deg))
    A = aspect_ratio
    return 2 * np.pi * A / (2 + np.sqrt(A * A * b * b / kappa ** 2 * (1 + t * t / (b * b)) + 4))
