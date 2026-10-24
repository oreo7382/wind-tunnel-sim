"""Propeller analysis with blade-element momentum theory (BEMT).

Each blade element is solved for its inflow angle phi with a bracketed root
search on a residual that stays finite in static (V = 0) conditions. Prandtl
tip and hub losses are included. Section lift and drag come from the airfoil's
2-D viscous polar (extended past stall with flat-plate behaviour), corrected
for the local blade Mach number.

The slipstream is visualised with actuator-disk momentum theory.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import airfoil2d as a2
from . import compressibility as comp
from .atmosphere import Freestream
from .flowfield import FlowField


@dataclass
class PropellerGeometry:
    diameter: float = 1.8          # m
    hub_ratio: float = 0.15        # hub radius / tip radius
    blades: int = 2
    pitch: float = 1.5             # geometric pitch (m) at 75 % radius, constant-pitch helix
    root_chord_ratio: float = 0.12  # chord / R at the hub
    tip_chord_ratio: float = 0.06   # chord / R at the tip
    n_elements: int = 30

    @property
    def radius(self):
        return self.diameter / 2

    def stations(self):
        R = self.radius
        t = np.linspace(0, np.pi, self.n_elements + 1)
        edges = self.hub_ratio * R + (R - self.hub_ratio * R) * 0.5 * (1 - np.cos(t))
        r = 0.5 * (edges[:-1] + edges[1:])
        return r, np.diff(edges)

    def chord(self, r):
        x = (r / self.radius - self.hub_ratio) / (1 - self.hub_ratio)
        return self.radius * (self.root_chord_ratio + (self.tip_chord_ratio - self.root_chord_ratio) * x)

    def blade_angle(self, r):
        """Geometric blade angle (rad) of a constant-pitch helix."""
        return np.arctan(self.pitch / (2 * np.pi * r))

    @property
    def beta75_deg(self):
        return float(np.degrees(self.blade_angle(0.75 * self.radius)))


@dataclass
class PropellerResult:
    geometry: PropellerGeometry
    rpm: float
    freestream: Freestream
    thrust: float
    torque: float
    power: float
    efficiency: float
    CT: float
    CP: float
    CQ: float
    J: float
    tip_mach: float
    figure_of_merit: float
    induced_velocity: float      # mean axial induced velocity at the disk (momentum theory)
    r: np.ndarray
    dT: np.ndarray               # N/m per blade set
    dQ: np.ndarray               # N m/m
    phi: np.ndarray              # deg
    alpha: np.ndarray            # deg
    W: np.ndarray                # relative speed m/s
    mach: np.ndarray
    cl: np.ndarray
    cd: np.ndarray
    a: np.ndarray
    a_tan: np.ndarray
    F: np.ndarray
    warnings: list = field(default_factory=list)

    def element_at(self, r_over_R: float) -> int:
        return int(np.argmin(np.abs(self.r / self.geometry.radius - r_over_R)))


class SectionPolar:
    """Airfoil polar over the full +-180 deg range: 2-D viscous data near zero lift,
    blended into flat-plate (Viterna-like) behaviour beyond stall."""

    def __init__(self, section: a2.Section, fs: Freestream, reynolds: float, settings: a2.Settings):
        low = Freestream(fs.atmosphere, min(fs.speed, 0.2 * fs.atmosphere.speed_of_sound) or 1.0)
        s = a2.Settings(**{**settings.__dict__, "flow_model": "Incompressible", "reynolds_override": reynolds})
        alphas = np.arange(-15.0, 25.5, 1.0)
        p = a2.polar(section, low, s, alphas)
        self.alpha, self.cl, self.cd = p["alpha"], p["cl"], p["cd"]
        i_max = int(np.argmax(self.cl))
        self.alpha_stall, self.cl_max = float(self.alpha[i_max]), float(self.cl[i_max])
        self.cd_max = 2.0

    def __call__(self, alpha_deg, mach):
        alpha_deg = np.asarray(alpha_deg, dtype=float)
        a = np.radians(alpha_deg)
        cl_fp = self.cd_max / 2 * np.sin(2 * a)
        cd_fp = self.cd_max * np.sin(a) ** 2 + 0.02
        cl_t = np.interp(alpha_deg, self.alpha, self.cl)
        cd_t = np.interp(alpha_deg, self.alpha, self.cd)
        lo, hi = self.alpha[0], self.alpha[-1]
        w = np.clip(np.maximum((alpha_deg - hi) / 10.0, (lo - alpha_deg) / 10.0), 0, 1)
        inside = (alpha_deg >= lo) & (alpha_deg <= hi)
        cl = np.where(inside, cl_t, (1 - w) * np.interp(np.clip(alpha_deg, lo, hi), self.alpha, self.cl) + w * cl_fp)
        cd = np.where(inside, cd_t, np.maximum((1 - w) * np.interp(np.clip(alpha_deg, lo, hi), self.alpha, self.cd)
                                               + w * cd_fp, cd_t))
        b = np.sqrt(np.clip(1 - np.minimum(mach, 0.9) ** 2, 0.19, 1))
        return cl / b, cd


class PropellerModel:
    def __init__(self, geometry: PropellerGeometry, section: a2.Section):
        self.geo = geometry
        self.section = section
        self._polar_key = None
        self._polar = None

    def polar(self, fs, rpm, settings):
        R = self.geo.radius
        omega = rpm * 2 * np.pi / 60
        w75 = np.hypot(fs.speed, omega * 0.75 * R)
        re = w75 * self.geo.chord(0.75 * R) / fs.atmosphere.kinematic_viscosity
        key = (round(np.log10(max(re, 1e3)), 1), settings.transition)
        if key != self._polar_key:
            self._polar = SectionPolar(self.section, fs, re, settings)
            self._polar_key = key
        return self._polar

    def solve(self, rpm: float, fs: Freestream, settings: a2.Settings) -> PropellerResult:
        g = self.geo
        polar = self.polar(fs, rpm, settings)
        rho = fs.atmosphere.density
        a_snd = fs.atmosphere.speed_of_sound
        V = max(fs.speed, 0.0)
        omega = max(rpm, 1.0) * 2 * np.pi / 60
        R, Rh, B = g.radius, g.hub_ratio * g.radius, g.blades
        r, dr = g.stations()
        c = g.chord(r)
        theta = g.blade_angle(r)
        n = len(r)
        out = {k: np.zeros(n) for k in ("phi", "alpha", "W", "mach", "cl", "cd", "a", "ap", "F", "dT", "dQ")}
        warnings = []

        for i in range(n):
            sigma = B * c[i] / (2 * np.pi * r[i])

            def evaluate(phi):
                sphi, cphi = np.sin(phi), np.cos(phi)
                aoa = np.degrees(theta[i] - phi)
                w_guess = np.hypot(V, omega * r[i])
                cl, cd = polar(aoa, w_guess / a_snd)
                cx = cl * cphi - cd * sphi
                cy = cl * sphi + cd * cphi
                s = max(abs(sphi), 1e-6)
                f_tip = B / 2 * (R - r[i]) / (r[i] * s)
                f_hub = B / 2 * (r[i] - Rh) / (Rh * s) if Rh > 0 else 50
                F = (2 / np.pi) ** 2 * np.arccos(np.exp(-f_tip)) * np.arccos(np.exp(-min(f_hub, 50)))
                F = max(F, 1e-4)
                g_ = sphi - sigma * cx / (4 * F * sphi if abs(sphi) > 1e-6 else 1e-6)
                h_ = cphi + sigma * cy / (4 * F * sphi if abs(sphi) > 1e-6 else 1e-6)
                return g_ - V / (omega * r[i]) * h_, (cl, cd, cx, cy, F, h_, aoa)

            grid = np.linspace(np.radians(0.2), np.radians(89.5), 180)
            vals = np.array([evaluate(p)[0] for p in grid])
            idx = np.where(np.sign(vals[:-1]) != np.sign(vals[1:]))[0]
            if len(idx) == 0:
                grid = np.linspace(np.radians(-45), np.radians(-0.2), 120)
                vals = np.array([evaluate(p)[0] for p in grid])
                idx = np.where(np.sign(vals[:-1]) != np.sign(vals[1:]))[0]
            if len(idx) == 0:
                phi = theta[i]
            else:
                lo, hi = grid[idx[0]], grid[idx[0] + 1]
                f_lo = evaluate(lo)[0]
                for _ in range(50):
                    mid = 0.5 * (lo + hi)
                    f_mid = evaluate(mid)[0]
                    if np.sign(f_mid) == np.sign(f_lo):
                        lo, f_lo = mid, f_mid
                    else:
                        hi = mid
                phi = 0.5 * (lo + hi)
            _, (cl, cd, cx, cy, F, h_, aoa) = evaluate(phi)
            W = omega * r[i] / max(h_, 1e-6)
            out["phi"][i] = np.degrees(phi)
            out["alpha"][i] = aoa
            out["W"][i] = W
            out["mach"][i] = W / a_snd
            out["cl"][i], out["cd"][i], out["F"][i] = cl, cd, F
            out["dT"][i] = B * 0.5 * rho * W * W * c[i] * cx
            out["dQ"][i] = B * 0.5 * rho * W * W * c[i] * cy * r[i]
            va = W * np.sin(phi)
            vt = omega * r[i] - W * np.cos(phi)
            out["a"][i] = va / V - 1 if V > 1e-3 else np.nan
            out["ap"][i] = vt / (omega * r[i])

        T = float(np.sum(out["dT"] * dr))
        Q = float(np.sum(out["dQ"] * dr))
        P = Q * omega
        n_rps = omega / (2 * np.pi)
        D = g.diameter
        CT = T / (rho * n_rps ** 2 * D ** 4)
        CQ = Q / (rho * n_rps ** 2 * D ** 5)
        CP = P / (rho * n_rps ** 3 * D ** 5)
        J = V / (n_rps * D)
        eff = T * V / P if P > 1e-9 and T > 0 else 0.0
        area = np.pi * R * R
        fm = (max(T, 0) ** 1.5 / (P * np.sqrt(2 * rho * area))) if P > 1e-9 else 0.0
        # momentum-theory induced velocity: T = 2 rho A (V + v) v
        vi = (-V + np.sqrt(V * V + 2 * max(T, 0) / (rho * area))) / 2
        tip_mach = float(np.hypot(V, omega * R) / a_snd)
        if tip_mach > 0.85:
            warnings.append(f"Helical tip Mach number {tip_mach:.2f}: compressibility losses and noise rise "
                            "sharply above about 0.85.")
        if np.any(out["alpha"] > polar.alpha_stall):
            warnings.append("Part of the blade is stalled (common at low advance ratio / static thrust).")
        if T < 0:
            warnings.append("Negative thrust: the propeller is windmilling (advance ratio too high for this pitch).")
        return PropellerResult(g, rpm, fs, T, Q, P, eff, CT, CP, CQ, J, tip_mach, fm, vi, r,
                               out["dT"], out["dQ"], out["phi"], out["alpha"], out["W"], out["mach"],
                               out["cl"], out["cd"], out["a"], out["ap"], out["F"], warnings)

    def sweep_advance_ratio(self, rpm, fs, settings, j_values):
        n_rps = rpm / 60
        rows = []
        for J in j_values:
            f = Freestream(fs.atmosphere, J * n_rps * self.geo.diameter)
            r = self.solve(rpm, f, settings)
            rows.append((J, r.CT, r.CP, r.efficiency))
        return np.array(rows)


class ActuatorDiskField(FlowField):
    """Axisymmetric momentum-theory slipstream, shown in a plane through the axis.

    Lengths are in propeller radii, the disk sits at x = 0. The axial induced
    velocity along the axis follows v(x) = v_i (1 + x / sqrt(x^2 + R^2)), and each
    stream tube contracts to conserve mass flow.
    """

    def __init__(self, fs: Freestream, induced_velocity: float, thrust: float, radius: float,
                 bounds=(-3.0, 6.0, -2.5, 2.5)):
        self.fs = fs
        self.vi = induced_velocity
        self.T = thrust
        self.R = radius
        self.bounds = bounds
        self.outlines = []
        self.annotations = {"disk": True}
        self.V = max(fs.speed, 0.0)
        self.V_ref = max(self.V, induced_velocity, 1e-3)   # normalising speed (works for static thrust)
        self.dp = thrust / (np.pi * radius * radius)   # pressure jump across the disk

    def _axial(self, x):
        return self.V + self.vi * (1 + x / np.sqrt(x * x + 1.0))

    def tube_radius(self, x, r0):
        """Radius (in R) of the stream tube that crosses the disk at radius r0 (<= 1)."""
        u0 = self._axial(0.0)
        return r0 * np.sqrt(u0 / self._axial(x))

    def velocity(self, x, y):
        x = np.atleast_1d(np.asarray(x, dtype=float))
        y = np.atleast_1d(np.asarray(y, dtype=float))
        r = np.abs(y)
        u_ax = self._axial(x)
        edge = self.tube_radius(x, 1.0)
        inside = r <= edge
        # contraction slope gives the radial velocity of the stream tubes
        dx = 1e-3
        slope = (self.tube_radius(x + dx, 1.0) - self.tube_radius(x - dx, 1.0)) / (2 * dx)
        v_in = u_ax * slope * (r / np.maximum(edge, 1e-6))
        # outside: free stream, slightly drawn in by the contraction (decays with distance)
        decay = (edge / np.maximum(r, 1e-6)) ** 2
        u = np.where(inside, u_ax, self.V + 0.0 * x)
        v = np.where(inside, v_in, self.V * slope * decay * (self.vi / (self.V + self.vi)))
        u, v = u / self.V_ref, v / self.V_ref
        return u, v * np.sign(y + 1e-300)

    def state(self, x, y):
        x = np.atleast_1d(np.asarray(x, dtype=float))
        y = np.atleast_1d(np.asarray(y, dtype=float))
        u, v = self.velocity(x, y)
        speed = np.hypot(u, v) * self.V_ref
        rho = self.fs.atmosphere.density
        p_inf = self.fs.atmosphere.pressure
        inside = np.abs(y) <= self.tube_radius(x, 1.0)
        p0 = p_inf + 0.5 * rho * self.V ** 2 + np.where(inside & (x >= 0), self.dp, 0.0)
        p = p0 - 0.5 * rho * speed ** 2
        cp = (p - p_inf) / (0.5 * rho * self.V_ref ** 2)
        a = self.fs.atmosphere.speed_of_sound
        return {"speed": speed, "speed_ratio": speed / self.V_ref, "cp": cp, "mach": speed / a,
                "pressure": p, "temperature": np.full_like(speed, self.fs.atmosphere.temperature),
                "density": np.full_like(speed, rho), "total_pressure": p0}

    def inside(self, x, y):
        return np.zeros(np.shape(x), dtype=bool)

    def seeds(self, n):
        x0, _, y0, y1 = self.bounds
        if self.V < 0.3 * self.vi:   # near-static: only the captured stream tube moves
            h = 0.97 * float(self.tube_radius(x0, 1.0))
            y0, y1 = -h, h
        ys = np.linspace(y0 * 0.95, y1 * 0.95, n)
        return np.column_stack([np.full(n, x0), ys])
