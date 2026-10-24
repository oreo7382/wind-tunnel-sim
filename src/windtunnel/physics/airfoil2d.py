"""Complete 2-D airfoil analysis: inviscid panel solution + boundary layer +
compressibility + stall model, for any flow regime from incompressible to supersonic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import boundary_layer as bl
from . import compressibility as comp
from .atmosphere import Freestream
from .flowfield import PanelFlowField, SupersonicFlowField
from .geometry import Airfoil
from .panel import PanelSolution

FLOW_MODELS = ("Auto (from Mach number)", "Incompressible", "Compressible")
DECAMBER_MIN = 0.93  # cap on the empirical boundary-layer decambering factor
TRANSITION_MODES = ("Free (Michel criterion)", "Forced (trip strips)", "Fully turbulent", "Fully laminar")


@dataclass
class Settings:
    alpha_deg: float = 4.0
    chord: float = 1.0                 # m
    span: float = 1.0                  # m, for total forces of a 2-D (infinite) wing segment
    flow_model: str = FLOW_MODELS[0]
    correction: str = "Karman-Tsien"
    viscous: bool = True
    stall_model: bool = True
    transition: str = TRANSITION_MODES[0]
    trip_upper: float = 0.1
    trip_lower: float = 0.1
    reynolds_override: float | None = None  # use a given Re instead of the free-stream one


class Section:
    """Geometry-dependent data reused across angles of attack and flow conditions."""

    def __init__(self, airfoil: Airfoil, n_panels: int = 160):
        base = airfoil if airfoil.blunt else airfoil.normalized()
        self.source = base
        self.airfoil = base if base.blunt else base.repaneled(n_panels)
        self.panel = PanelSolution(self.airfoil)
        self.grid_cache: dict = {}
        self._cp0_min_cache: dict = {}
        self.properties = self.airfoil.properties() if not airfoil.blunt else {}

    @property
    def name(self):
        return self.airfoil.name

    def inviscid(self, alpha_rad: float, gamma_body: float = 0.0):
        vt = self.panel.surface_velocity(alpha_rad, gamma_body)
        return vt, 1.0 - vt ** 2

    def zero_lift_alpha(self) -> float:
        """Inviscid zero-lift angle (deg)."""
        cl0 = self.panel.forces(self.inviscid(0.0)[1], 0.0)[0]
        a1 = np.radians(5.0)
        cl1 = self.panel.forces(self.inviscid(a1)[1], a1)[0]
        slope = (cl1 - cl0) / a1
        return float(np.degrees(-cl0 / slope)), float(slope)


@dataclass
class Result:
    settings: Settings
    freestream: Freestream
    regime: str
    model: str                 # 'incompressible', 'subsonic', 'transonic', 'supersonic'
    mach: float
    reynolds: float
    x: np.ndarray              # control-point coordinates (clockwise order)
    y: np.ndarray
    upper: np.ndarray          # bool mask of upper-surface panels
    cp0: np.ndarray            # incompressible Cp
    cp: np.ndarray             # Cp used for forces (corrected / supersonic)
    cl: float
    cd: float
    cm: float
    cl_inviscid: float
    cd_friction: float
    cd_separation: float
    cd_wave: float
    cd_pressure: float
    mcrit: float
    cp_crit: float
    cp_min: float
    separation_fraction: float
    bl_upper: bl.SurfaceBL | None
    bl_lower: bl.SurfaceBL | None
    stagnation: tuple
    flow: object
    warnings: list = field(default_factory=list)

    @property
    def q(self):
        return self.freestream.dynamic_pressure

    @property
    def lift_per_span(self):
        return self.cl * self.q * self.settings.chord

    @property
    def drag_per_span(self):
        return self.cd * self.q * self.settings.chord

    @property
    def lift(self):
        return self.lift_per_span * self.settings.span

    @property
    def drag(self):
        return self.drag_per_span * self.settings.span

    @property
    def moment(self):
        return self.cm * self.q * self.settings.chord ** 2 * self.settings.span

    @property
    def lift_to_drag(self):
        return self.cl / self.cd if abs(self.cd) > 1e-9 else float("inf")

    def surface_point(self, x_c: float, upper_side: bool):
        """Local state at a chordwise position on the upper or lower surface."""
        mask = self.upper if upper_side else ~self.upper
        xs, cps = self.x[mask], self.cp[mask]
        order = np.argsort(xs)
        cp = float(np.interp(x_c, xs[order], cps[order]))
        ys = float(np.interp(x_c, xs[order], self.y[mask][order]))
        compressible = self.model != "incompressible"
        st = comp.local_state(np.array([cp]), self.freestream, compressible)
        return ys, {k: float(v[0]) for k, v in st.items()}


def _trim_te(surface, airfoil: Airfoil):
    """Drop stations in the last 1.5 % of chord, where the inviscid solution has a
    trailing-edge stagnation singularity that would trigger false separation."""
    s, x, ue, _ = surface
    if airfoil.blunt:
        return s, x, ue
    keep = x <= airfoil.x.max() - 0.015 * (airfoil.x.max() - airfoil.x.min())
    keep[:3] = True
    return s[keep], x[keep], ue[keep]


def choose_model(settings: Settings, mach: float) -> str:
    if settings.flow_model == "Incompressible":
        return "incompressible"
    if settings.flow_model == "Auto (from Mach number)" and mach < 0.3:
        return "incompressible"
    if mach >= 1.0:
        return "supersonic"
    return "subsonic"


def analyze(section: Section, fs: Freestream, settings: Settings, build_flow: bool = True) -> Result:
    alpha = np.radians(settings.alpha_deg)
    mach = fs.mach
    reynolds = settings.reynolds_override or fs.reynolds(settings.chord)
    model = choose_model(settings, mach)
    warnings = []
    panel = section.panel
    vt, cp0 = section.inviscid(alpha)
    upper_mask = np.zeros(panel.n, dtype=bool)

    # --- boundary layer on the incompressible surface velocity ----------------
    up, lo, stag = bl.split_surfaces(panel.xc, panel.yc, vt)
    upper_mask[up[3]] = True
    bl_u = bl_l = None
    if settings.viscous:
        mode = settings.transition
        free = mode.startswith("Free") or mode.startswith("Forced")
        trip_u = trip_l = None
        if mode.startswith("Forced"):
            trip_u, trip_l = settings.trip_upper, settings.trip_lower
        elif mode.startswith("Fully turbulent"):
            trip_u = trip_l = -1.0
        elif mode.startswith("Fully laminar"):
            free = False
        bl_u = bl.march(*_trim_te(up, section.airfoil), reynolds, trip_u, free)
        bl_l = bl.march(*_trim_te(lo, section.airfoil), reynolds, trip_l, free)

    cp0_min = float(cp0.min())
    mcrit = comp.critical_mach(cp0_min, settings.correction)

    m_eff = mach
    if model == "subsonic" and mach > comp.SONIC_BAND[0]:
        m_eff = comp.SONIC_BAND[0]
        warnings.append(f"Near-sonic flow: linear compressibility theory breaks down near M = 1; "
                        f"pressures are evaluated at M = {m_eff:.2f}.")
    if model == "supersonic" and mach < comp.SONIC_BAND[1]:
        m_eff = comp.SONIC_BAND[1]
        warnings.append(f"Near-sonic flow: supersonic linear theory is evaluated at M = {m_eff:.2f}.")

    # --- pressures --------------------------------------------------------------
    if model == "supersonic":
        fs_eff = Freestream.from_mach(fs.atmosphere, m_eff)
        flow = SupersonicFlowField(section.airfoil.x, section.airfoil.y, alpha, fs_eff)
        cp, delta = flow.surface_cp(panel)
        if np.degrees(np.abs(delta)).max() > 25 or section.properties.get("max thickness", 0) > 0.12:
            warnings.append("Round or thick leading edge: real supersonic flow has a detached bow shock; "
                            "thin-airfoil (Busemann) theory is only approximate here.")
        mcrit_report = mcrit
    else:
        compressible = model == "subsonic"
        cp = comp.correct_cp(cp0, m_eff, settings.correction) if compressible else cp0.copy()
        if compressible and cp.min() <= comp.cp_at_local_mach(comp.MAX_LOCAL_MACH, m_eff) + 1e-9:
            warnings.append(f"Strong supersonic pocket: the correction would exceed local Mach "
                            f"{comp.MAX_LOCAL_MACH:g}; peak suction is limited (a shock wave would form here).")
        flow = None
        if build_flow:
            flow = PanelFlowField(panel, alpha, fs, mach=m_eff, compressible=compressible,
                                  correction=settings.correction, grid_cache=section.grid_cache)
        mcrit_report = mcrit
        if model == "incompressible" and mach > 0.3:
            warnings.append(f"Incompressible model at M = {mach:.2f}: density changes are ignored "
                            "(switch the flow model to Compressible or Auto to include them).")
    cl_inv, cd_p, cm = panel.forces(cp, alpha)

    # --- viscous drag, stall --------------------------------------------------------
    cd_f = cd_sep = 0.0
    f_sep = 1.0
    cl = cl_inv
    if settings.viscous and model != "supersonic":
        cd_f = bl_u.cd + bl_l.cd
        if model == "subsonic":
            cd_f *= comp.skin_friction_compressibility_factor(mach)
        # boundary-layer displacement thickness at the TE "decambers" the section (empirical fit)
        decamber = float(np.clip(1 - 7.0 * (bl_u.delta_star[-1] + bl_l.delta_star[-1]), DECAMBER_MIN, 1.0))
        cl = cl_inv * decamber
        if settings.stall_model:
            suction = bl_u if cl_inv >= 0 else bl_l
            if suction.x_separation is not None:
                f_sep = float(np.clip(suction.x_separation, 0.0, 1.0))
            kirchhoff = ((1 + np.sqrt(f_sep)) / 2) ** 2
            cl = cl_inv * decamber * kirchhoff
            a0, _ = section.zero_lift_alpha()
            cd_sep = abs(cl_inv * np.sin(alpha - np.radians(a0))) * (kirchhoff - np.sqrt(f_sep))
            cm = cm * (0.5 + 0.5 * kirchhoff) - 0.1 * (1 - f_sep) * np.sign(cl_inv)
            if f_sep < 0.9:
                warnings.append(f"Trailing-edge separation from x/c = {f_sep:.2f}: lift reduced "
                                "(Kirchhoff stall model).")
            if f_sep < 0.3:
                warnings.append("Deep stall: the potential-flow streamlines no longer represent the real, "
                                "fully separated flow.")
    elif settings.viscous and model == "supersonic":
        re = max(reynolds, 1e5)
        cd_f = 2 * 0.074 * re ** -0.2 * comp.skin_friction_compressibility_factor(mach)

    cd_wave = 0.0
    if model == "subsonic":
        cd_wave = comp.lock_wave_drag(mach, mcrit)
        if mach > mcrit:
            warnings.append(f"Free stream above the critical Mach number ({mcrit:.3f}): supersonic pockets "
                            "and shock waves form; wave drag estimated with Lock's 4th-power law.")
    elif model == "supersonic":
        cd_wave = cd_p  # pressure drag in supersonic flow is wave drag
        cd_p = 0.0

    cd = cd_f + cd_sep + cd_wave
    if not settings.viscous and model != "supersonic":
        warnings.append("Inviscid analysis: drag is zero (d'Alembert's paradox) except wave drag.")

    regime = comp.regime(mach, mcrit)
    return Result(settings, fs, regime, model, mach, reynolds, panel.xc, panel.yc, upper_mask, cp0, cp,
                  float(cl), float(cd), float(cm), float(cl_inv), float(cd_f), float(cd_sep), float(cd_wave),
                  float(cd_p), float(mcrit_report), comp.cp_critical(mach), float(cp.min()), f_sep,
                  bl_u, bl_l, stag, flow, warnings)


def polar(section: Section, fs: Freestream, settings: Settings, alphas=None):
    """Sweep the angle of attack; returns dict of arrays alpha, cl, cd, cm."""
    if alphas is None:
        alphas = np.arange(-10.0, 20.5, 1.0)
    out = {"alpha": [], "cl": [], "cd": [], "cm": [], "cl_inviscid": []}
    for a in alphas:
        s = Settings(**{**settings.__dict__, "alpha_deg": float(a)})
        r = analyze_forces(section, fs, s)
        out["alpha"].append(a)
        out["cl"].append(r["cl"])
        out["cd"].append(r["cd"])
        out["cm"].append(r["cm"])
        out["cl_inviscid"].append(r["cl_inviscid"])
    return {k: np.asarray(v) for k, v in out.items()}


def analyze_forces(section: Section, fs: Freestream, settings: Settings) -> dict:
    """Same physics as :func:`analyze` without building a flow field (fast, for sweeps)."""
    r = analyze(section, fs, settings, build_flow=False)
    return {"cl": r.cl, "cd": r.cd, "cm": r.cm, "cl_inviscid": r.cl_inviscid}


def max_lift(section: Section, fs: Freestream, settings: Settings):
    """Maximum lift coefficient and the angle where it occurs (viscous, with stall model)."""
    alphas = np.arange(0.0, 26.0, 1.0)
    p = polar(section, fs, settings, alphas)
    i = int(np.argmax(p["cl"]))
    return float(p["cl"][i]), float(alphas[i])
