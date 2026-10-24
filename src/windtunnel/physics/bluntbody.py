"""Blunt-body aerodynamics: potential flow (panel method / exact sphere solution)
for the pressure field, plus empirical drag, vortex shedding and Magnus lift.

Potential flow predicts zero drag for any closed body (d'Alembert's paradox):
real blunt-body drag comes from flow separation and the low-pressure wake, so
drag coefficients here come from published measurements (Hoerner, White,
Achenbach, Morrison's sphere correlation).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import boundary_layer as bl
from . import compressibility as comp
from .atmosphere import Freestream
from .flowfield import PanelFlowField, SphereFlowField
from .geometry import Airfoil
from .panel import PanelSolution

# Circular cylinder drag vs Reynolds number (smooth cylinder, from classic measurements)
_CYL_RE = np.array([1e-1, 1e0, 1e1, 1e2, 1e3, 1e4, 1e5, 2e5, 3e5, 4e5, 5e5, 1e6, 3e6, 1e7])
_CYL_CD = np.array([60.0, 10.0, 2.8, 1.4, 1.0, 1.15, 1.2, 1.2, 0.8, 0.35, 0.3, 0.33, 0.6, 0.7])

# Subsonic compressibility increase of cylinder drag (approximate measured trend)
_CYL_M = np.array([0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5, 2.0, 3.0])
_CYL_MF = np.array([1.0, 1.0, 1.15, 1.25, 1.4, 1.65, 1.75, 1.7, 1.55, 1.4, 1.3, 1.25])


def cylinder_cd(re: float) -> float:
    return float(np.exp(np.interp(np.log(max(re, 0.1)), np.log(_CYL_RE), np.log(_CYL_CD))))


def sphere_cd(re: float) -> float:
    """Morrison (2013) correlation, valid up to Re ~ 1e6."""
    re = max(re, 1e-3)
    return (24 / re + 2.6 * (re / 5.0) / (1 + (re / 5.0) ** 1.52)
            + 0.411 * (re / 263000) ** -7.94 / (1 + (re / 263000) ** -8.0)
            + 0.25 * (re / 1e6) / (1 + re / 1e6))


def mach_drag_factor(mach: float) -> float:
    return float(np.interp(mach, _CYL_M, _CYL_MF))


@dataclass
class BluntResult:
    body: Airfoil
    freestream: Freestream
    diameter: float
    span: float
    spin_ratio: float
    reynolds: float
    cd: float
    cl: float
    cl_potential: float
    regime: str
    strouhal: float
    shedding_frequency: float
    mcrit: float
    cp_min: float
    angle: np.ndarray            # surface position angle from the front stagnation point (deg)
    cp: np.ndarray
    x: np.ndarray
    y: np.ndarray
    separation_angle: float | None
    flow: object
    warnings: list = field(default_factory=list)

    @property
    def q(self):
        return self.freestream.dynamic_pressure

    @property
    def drag(self):
        return self.cd * self.q * self.diameter * self.span

    @property
    def lift(self):
        return self.cl * self.q * self.diameter * self.span


class BluntModel:
    def __init__(self, body: Airfoil):
        self.body = body
        self.axisymmetric = bool(body.meta.get("axisymmetric"))
        self.panel = None if self.axisymmetric else PanelSolution(body)
        self.grid_cache: dict = {}

    def solve(self, fs: Freestream, diameter: float, span: float, spin_ratio: float = 0.0,
              compressible: bool = True, correction: str = "Karman-Tsien") -> BluntResult:
        body = self.body
        meta = body.meta
        mach = fs.mach
        re = fs.reynolds(diameter)
        warnings = []
        compressible = compressible and mach >= 0.3
        m_eff = min(mach, comp.SONIC_BAND[0])
        if mach > comp.SONIC_BAND[0]:
            warnings.append("Transonic/supersonic blunt-body flow has a detached bow shock; the potential-flow "
                            f"field is shown for M = {m_eff:.2f}. Drag uses measured compressibility trends.")
        can_spin = bool(meta.get("can_spin"))
        spin = spin_ratio if can_spin else 0.0

        if self.axisymmetric:
            flow = SphereFlowField(fs, mach=m_eff, compressible=compressible, correction=correction)
            th = np.linspace(0, np.pi, 181)
            xs, ys = -0.5 * np.cos(th), 0.5 * np.sin(th)
            cp0 = 1 - 2.25 * np.sin(th) ** 2
            angle = np.degrees(th)
            ue = 1.5 * np.sin(th)
            s = 0.5 * th
        else:
            perimeter = float(self.panel.length.sum())
            # spinning cylinder: surface speed = spin_ratio * V; circulation 2 pi a (omega a)
            gamma_sheet = -2 * np.pi * 0.5 * spin / perimeter
            flow = PanelFlowField(self.panel, 0.0, fs, mach=m_eff, compressible=compressible,
                                  correction=correction, gamma_body=gamma_sheet, grid_cache=self.grid_cache)
            vt = self.panel.surface_velocity(0.0, gamma_sheet)
            cp0 = 1 - vt ** 2
            xs, ys = self.panel.xc, self.panel.yc
            angle = np.degrees(np.arctan2(ys, -(xs - xs.mean()))) % 360
            angle = np.where(angle > 180, angle - 360, angle)
            up, lo, _ = bl.split_surfaces(self.panel.xc, self.panel.yc, vt)
            s, ue = up[0], up[2]
        cp = comp.correct_cp(cp0, m_eff, correction) if compressible else cp0
        mcrit = comp.critical_mach(float(np.min(cp0)), correction)

        # laminar separation on the potential-flow velocity (Thwaites)
        sep_angle = None
        if meta.get("shape") in ("cylinder", "sphere") and abs(spin) < 1e-9:
            res = bl.march(s, s, ue, max(re, 1e3), None, free_transition=False)
            if res.x_laminar_separation is not None:
                sep_angle = float(np.degrees(res.x_laminar_separation / 0.5))

        shape = meta.get("shape")
        if shape == "cylinder":
            cd = cylinder_cd(re)
            st = 0.21 if re < 2e5 else 0.27 if re < 3.5e6 else 0.25
        elif shape == "sphere":
            cd = sphere_cd(re)
            st = 0.19
        else:
            crisis = meta.get("rounded", False)
            lo_cd, hi_cd = meta.get("cd_sub", 1.2), meta.get("cd_super", meta.get("cd_sub", 1.2))
            if crisis:
                w = np.clip((np.log10(max(re, 1)) - np.log10(2e5)) / (np.log10(6e5) - np.log10(2e5)), 0, 1)
                cd = (1 - w) * lo_cd + w * hi_cd
            else:
                cd = lo_cd
            if re < 1e3:
                cd *= 1 + 10 / max(re, 1) ** (2 / 3)
                warnings.append("Low Reynolds number: drag data for this shape are extrapolated below Re = 1000.")
            st = meta.get("strouhal", 0.2)
        cd *= mach_drag_factor(mach)

        cl_pot = 2 * np.pi * spin if can_spin else 0.0
        cl = 0.0
        if can_spin and spin != 0.0:
            # measured Magnus lift is far below the potential-flow value (Prandtl limit 4 pi)
            cl = float(spin * (1 - np.exp(-1.5 * abs(spin))))   # fit to measured trends
            cl = float(np.clip(cl, -abs(cl_pot), abs(cl_pot)))
            warnings.append(f"Magnus effect: potential flow gives cl = 2 pi x spin ratio = {cl_pot:.2f}; real "
                            f"cylinders reach about {cl:.2f} because the boundary layer separates.")
            if abs(spin) > 2:
                warnings.append("Spin ratio above 2: the stagnation points have joined below/above the "
                                "cylinder (closed circulating region).")

        regime = _drag_regime(re, shape, meta)
        freq = st * fs.speed / diameter
        return BluntResult(body, fs, diameter, span, spin, re, float(cd), float(cl), float(cl_pot), regime,
                           float(st), float(freq), float(mcrit), float(np.min(cp)), np.asarray(angle),
                           np.asarray(cp), np.asarray(xs), np.asarray(ys), sep_angle, flow, warnings)


def _drag_regime(re, shape, meta):
    if re < 1:
        return "creeping (Stokes) flow"
    if re < 40:
        return "steady separated twin vortices"
    if re < 200:
        return "laminar vortex street"
    if meta.get("rounded") or shape in ("cylinder", "sphere"):
        if re < 2e5:
            return "subcritical (laminar separation, wide wake)"
        if re < 5e5:
            return "critical (drag crisis)"
        if re < 3e6:
            return "supercritical (turbulent separation, narrow wake)"
        return "transcritical"
    return "fixed separation at sharp edges (Re-independent)"
