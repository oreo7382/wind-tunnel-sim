"""Compressibility corrections, isentropic relations and local flow state.

Subsonic corrections map an incompressible pressure coefficient Cp0 to the
compressible value Cp at free-stream Mach number M:

* Prandtl-Glauert:  Cp = Cp0 / beta
* Karman-Tsien:     Cp = Cp0 / (beta + M^2 / (1 + beta) * Cp0 / 2)
* Laitone:          Cp = Cp0 / (beta + M^2 (1 + (g-1)/2 M^2) / (2 beta) * Cp0)

with beta = sqrt(1 - M^2).
"""
from __future__ import annotations

import numpy as np

from .atmosphere import GAMMA, R_AIR, Freestream

G = GAMMA
CORRECTIONS = ("Karman-Tsien", "Prandtl-Glauert", "Laitone")

# Free-stream Mach numbers closer to 1 than this are treated as transonic and the
# linear theories are evaluated at the band edge instead (they are singular at M = 1).
SONIC_BAND = (0.95, 1.08)

# Subsonic corrections are applied point by point; inside a supersonic pocket they are no
# longer valid. Real pockets end in a shock at local Mach numbers of roughly 1.3-1.5, so the
# corrected Cp is limited to the value for this local Mach number.
MAX_LOCAL_MACH = 1.6
WAVE_DRAG_CAP = 0.1


def beta(mach: float) -> float:
    return float(np.sqrt(abs(1.0 - mach * mach)))


def correct_cp(cp0, mach: float, method: str = "Karman-Tsien"):
    """Apply a subsonic compressibility correction to incompressible Cp0."""
    cp0 = np.asarray(cp0, dtype=float)
    if mach <= 1e-6:
        return cp0.copy()
    b = beta(mach)
    m2 = mach * mach
    if method == "Prandtl-Glauert":
        return cp0 / b
    if method == "Laitone":
        denom = b + m2 * (1 + 0.5 * (G - 1) * m2) / (2 * b) * cp0
    else:  # Karman-Tsien
        denom = b + m2 / (1 + b) * cp0 / 2
    # a non-positive denominator means the correction has broken down (deep supersonic pocket)
    denom = np.maximum(denom, 1e-3)
    cp = cp0 / denom
    return np.clip(cp, cp_at_local_mach(MAX_LOCAL_MACH, mach), None)


def cp_at_local_mach(m_local: float, mach: float) -> float:
    """Pressure coefficient of isentropic flow that has accelerated to Mach ``m_local``."""
    if mach <= 1e-6:
        return -1e9
    ratio = ((1 + 0.5 * (G - 1) * mach ** 2) / (1 + 0.5 * (G - 1) * m_local ** 2)) ** (G / (G - 1))
    return 2 / (G * mach ** 2) * (ratio - 1)


def cp_vacuum(mach: float) -> float:
    """Lowest physically possible Cp (zero local pressure)."""
    if mach <= 1e-6:
        return -1e9
    return -2.0 / (G * mach * mach)


def cp_critical(mach: float) -> float:
    """Pressure coefficient at which the local flow reaches Mach 1."""
    if mach <= 1e-6:
        return -1e9
    m2 = mach * mach
    return 2.0 / (G * m2) * (((2 + (G - 1) * m2) / (G + 1)) ** (G / (G - 1)) - 1.0)


def critical_mach(cp0_min: float, method: str = "Karman-Tsien") -> float:
    """Free-stream Mach number at which the minimum-pressure point first goes sonic."""
    if cp0_min >= 0:
        return 1.0
    f = lambda m: float(correct_cp(cp0_min, m, method)) - cp_critical(m)
    ms = np.linspace(0.01, 0.999, 400)
    vals = np.array([f(m) for m in ms])
    below = np.where(vals <= 0)[0]
    if len(below) == 0:
        return 0.999
    if below[0] == 0:
        return 0.01
    lo, hi = ms[below[0] - 1], ms[below[0]]
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def lock_wave_drag(mach: float, mcrit: float) -> float:
    """Lock's fourth-power law for transonic wave drag (valid up to about M = 1)."""
    if mach <= mcrit:
        return 0.0
    # the 4th-power law holds up to drag divergence; beyond that the estimate is capped
    return min(20.0 * (min(mach, 1.0) - mcrit) ** 4, WAVE_DRAG_CAP)


def skin_friction_compressibility_factor(mach: float) -> float:
    """Reduction of turbulent skin friction with Mach number (reference-temperature fit)."""
    return (1 + 0.144 * mach * mach) ** -0.65


def isentropic_mach_from_pressure_ratio(p_over_p0):
    """Local Mach number from static/total pressure ratio."""
    ratio = np.clip(np.asarray(p_over_p0, dtype=float), 1e-9, 1.0)
    return np.sqrt(np.maximum(2 / (G - 1) * (ratio ** (-(G - 1) / G) - 1), 0.0))


def local_state(cp, fs: Freestream, compressible: bool) -> dict:
    """Local static/total state at points with pressure coefficient ``cp``.

    For incompressible flow density and temperature are constant and
    V = V_inf * sqrt(1 - Cp). For compressible flow the local Mach number
    follows from isentropic relations with the free-stream total pressure.
    """
    cp = np.asarray(cp, dtype=float)
    atm = fs.atmosphere
    q = fs.dynamic_pressure
    if not compressible:
        v = fs.speed * np.sqrt(np.clip(1.0 - cp, 0.0, None))
        p = atm.pressure + q * cp
        return {
            "speed": v,
            "speed_ratio": v / max(fs.speed, 1e-9),
            "cp": cp,
            "mach": v / atm.speed_of_sound,
            "pressure": p,
            "temperature": np.full_like(cp, atm.temperature),
            "density": np.full_like(cp, atm.density),
            "total_pressure": np.full_like(cp, atm.pressure + q),
        }
    p0 = fs.total_pressure
    t0 = fs.total_temperature
    p = np.clip(atm.pressure + q * cp, 1.0, None)
    m = isentropic_mach_from_pressure_ratio(p / p0)
    t = t0 / (1 + 0.5 * (G - 1) * m * m)
    v = m * np.sqrt(G * R_AIR * t)
    return {
        "speed": v,
        "speed_ratio": v / max(fs.speed, 1e-9),
        "cp": cp,
        "mach": m,
        "pressure": p,
        "temperature": t,
        "density": p / (R_AIR * t),
        "total_pressure": np.full_like(cp, p0),
    }


def regime(mach: float, mcrit: float | None = None) -> str:
    if mach < 0.3:
        return "incompressible (M < 0.3)"
    if mcrit is not None and mach < mcrit:
        return "subsonic compressible"
    if mach < 1.0:
        return "transonic (above critical Mach)"
    if mach < 5.0:
        return "supersonic"
    return "hypersonic"
