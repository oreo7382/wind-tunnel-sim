"""Analytic airfoil families (Joukowski, Karman-Trefftz, supersonic sections, plates)
and blunt-body shapes."""
from __future__ import annotations

import numpy as np

from .geometry import Airfoil


# ----------------------------------------------------------------- conformal maps
def _circle(eps_x: float, eps_y: float, n: int):
    center = complex(-eps_x, eps_y)
    radius = abs(1 - center)
    theta_te = np.angle(1 - center)
    theta = theta_te + np.linspace(0, 2 * np.pi, n)
    return center, radius, center + radius * np.exp(1j * theta)


def _finish(name, z, category, description, meta=None):
    z = np.asarray(z)
    af = Airfoil(name, z.real, z.imag, category, description, meta=meta or {})
    return af.normalized()


def joukowski(thickness: float = 0.12, camber: float = 0.02, n: int = 241) -> Airfoil:
    """Joukowski airfoil; thickness and camber are approximate fractions of chord."""
    eps_x = thickness / 1.299
    eps_y = 2 * camber
    center, radius, zeta = _circle(eps_x, eps_y, n)
    z = zeta + 1 / zeta
    return _finish(f"Joukowski t={thickness:.0%} f={camber:.0%}", z, "Joukowski",
                   "Conformal map of a circle; has an exact potential-flow solution and a cusped trailing edge.",
                   {"eps_x": eps_x, "eps_y": eps_y})


def karman_trefftz(thickness: float = 0.12, camber: float = 0.02, te_angle_deg: float = 10.0,
                   n: int = 241) -> Airfoil:
    """Karman-Trefftz airfoil: like Joukowski but with a finite trailing-edge angle."""
    eps_x = thickness / 1.299
    eps_y = 2 * camber
    _, _, zeta = _circle(eps_x, eps_y, n)
    k = 2 - np.radians(te_angle_deg) / np.pi
    zp, zm = (zeta + 1) ** k, (zeta - 1) ** k
    z = k * (zp + zm) / (zp - zm)
    return _finish(f"Karman-Trefftz t={thickness:.0%} f={camber:.0%} TE {te_angle_deg:.0f}deg", z,
                   "Karman-Trefftz", "Conformal-map airfoil with a finite trailing-edge angle.")


# ----------------------------------------------------------------- simple sections
def _from_surfaces(name, x, yu, yl, category, description):
    xs = np.concatenate([x[::-1], x[1:]])
    ys = np.concatenate([yu[::-1], yl[1:]])
    return Airfoil(name, xs, ys, category, description)


def _cos_x(n=121):
    return 0.5 * (1 - np.cos(np.linspace(0, np.pi, n)))


def biconvex(thickness: float = 0.06, n: int = 121) -> Airfoil:
    x = _cos_x(n)
    yt = 2 * thickness * x * (1 - x)
    return _from_surfaces(f"Biconvex {thickness:.0%}", x, yt, -yt, "Supersonic",
                          "Symmetric parabolic-arc biconvex section with sharp leading and trailing edges.")


def double_wedge(thickness: float = 0.06, max_at: float = 0.5, n: int = 121) -> Airfoil:
    x = _cos_x(n)
    x = np.unique(np.concatenate([x, [max_at]]))
    yt = 0.5 * thickness * np.where(x < max_at, x / max_at, (1 - x) / (1 - max_at))
    return _from_surfaces(f"Double wedge {thickness:.0%} (max at {max_at:.0%})", x, yt, -yt, "Supersonic",
                          "Diamond section made of flat facets; classic supersonic shape.")


def _thin_plate_thickness(x, t):
    nose = np.sqrt(np.clip(x / 0.03, 0, 1))
    tail = np.clip((1 - x) / 0.25, 0, 1)
    return 0.5 * t * nose * tail


def flat_plate(thickness: float = 0.02, n: int = 121) -> Airfoil:
    x = _cos_x(n)
    yt = _thin_plate_thickness(x, thickness)
    return _from_surfaces(f"Flat plate {thickness:.0%}", x, yt, -yt, "Plates & arcs",
                          "Thin flat plate with a rounded nose and tapered tail.")


def circular_arc(camber: float = 0.05, thickness: float = 0.02, n: int = 121) -> Airfoil:
    x = _cos_x(n)
    yc = 4 * camber * x * (1 - x)
    yt = _thin_plate_thickness(x, thickness)
    return _from_surfaces(f"Cambered plate {camber:.0%}", x, yc + yt, yc - yt, "Plates & arcs",
                          "Thin circular-arc (parabolic) cambered plate, like a sail or model wing.")


def elliptic_airfoil(thickness: float = 0.12, n: int = 121) -> Airfoil:
    x = _cos_x(n)
    yt = 0.5 * thickness * np.sqrt(np.clip(1 - (2 * x - 1) ** 2, 0, None))
    return _from_surfaces(f"Elliptical {thickness:.0%}", x, yt, -yt, "Plates & arcs",
                          "Elliptical section (rounded trailing edge, Kutta condition imposed at TE).")


# ----------------------------------------------------------------- blunt bodies
# Each body has unit frontal height (reference length d = 1) and is centred at the origin.
# ``meta`` holds empirical drag data: cd_sub (subcritical), cd_super (supercritical, for
# rounded bodies with a drag crisis), strouhal number.

def _body(name, x, y, description, **meta):
    return Airfoil(name, np.asarray(x), np.asarray(y), "Blunt bodies", description, blunt=True,
                   reference_length=1.0, meta=meta)


def _polygon(vertices, n_per_side=40):
    vertices = np.asarray(vertices, dtype=float)
    pts = []
    for i in range(len(vertices)):
        a, b = vertices[i], vertices[(i + 1) % len(vertices)]
        t = 0.5 * (1 - np.cos(np.linspace(0, np.pi, n_per_side, endpoint=False)))
        pts.append(a[None, :] + (b - a)[None, :] * t[:, None])
    pts = np.vstack(pts)
    return pts[:, 0], pts[:, 1]


def cylinder(n=200):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return _body("Circular cylinder", 0.5 * np.cos(t), 0.5 * np.sin(t),
                 "Circular cylinder of diameter d. Shows the drag crisis near Re = 3e5 and the Magnus effect when spun.",
                 shape="cylinder", strouhal=0.21, can_spin=True)


def ellipse_body(ratio: float, n=200):
    """Ellipse with length/height = ratio (ratio > 1: long axis along the flow)."""
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    data = {0.5: (1.6, 1.6, 0.18), 2.0: (0.6, 0.2, 0.2), 4.0: (0.35, 0.15, 0.2), 8.0: (0.25, 0.1, 0.2)}
    cd_sub, cd_sup, st = data.get(ratio, (0.6, 0.2, 0.2))
    return _body(f"Ellipse {ratio:g}:1", 0.5 * ratio * np.cos(t), 0.5 * np.sin(t),
                 f"Elliptical cylinder, length/height = {ratio:g}.",
                 shape="ellipse", cd_sub=cd_sub, cd_super=cd_sup, strouhal=st, rounded=True)


def square(n_side=50):
    x, y = _polygon([(0.5, 0.5), (-0.5, 0.5), (-0.5, -0.5), (0.5, -0.5)], n_side)
    return _body("Square cylinder", x, y, "Square cylinder facing the flow. Sharp edges fix separation, so drag barely depends on Re.",
                 shape="square", cd_sub=2.1, cd_super=2.1, strouhal=0.13)


def diamond(n_side=50):
    x, y = _polygon([(0.5, 0.0), (0.0, 0.5), (-0.5, 0.0), (0.0, -0.5)], n_side)
    return _body("Diamond (square on edge)", x, y, "Square cylinder rotated 45 degrees.",
                 shape="diamond", cd_sub=1.6, cd_super=1.6, strouhal=0.15)


def normal_plate(n_side=60):
    t = 0.04
    x, y = _polygon([(t / 2, 0.5), (-t / 2, 0.5), (-t / 2, -0.5), (t / 2, -0.5)], n_side)
    return _body("Flat plate normal to flow", x, y, "Thin plate perpendicular to the stream: the highest-drag 2-D shape.",
                 shape="plate", cd_sub=2.0, cd_super=2.0, strouhal=0.15)


def half_cylinder(rounded_front: bool, n=160):
    t = np.linspace(np.pi / 2, 3 * np.pi / 2, n)
    arc_x, arc_y = 0.5 * np.cos(t), 0.5 * np.sin(t)
    flat_y = np.linspace(-0.5, 0.5, 40)[1:-1]
    x = np.concatenate([arc_x, np.zeros_like(flat_y)])
    y = np.concatenate([arc_y, flat_y])
    if not rounded_front:
        x = -x[::-1]
        y = y[::-1]
    x = x + (0.25 if rounded_front else -0.25)
    name = "D-section, round side forward" if rounded_front else "D-section, flat side forward"
    return _body(name, x, y, "Half cylinder (semicircular section).", shape="dsection",
                 cd_sub=1.2 if rounded_front else 1.7, cd_super=1.2 if rounded_front else 1.7, strouhal=0.18)


def triangle(apex_forward: bool, n_side=50):
    h = np.sqrt(3) / 2
    if apex_forward:
        v = [(h / 2, 0.5), (-h / 2, 0.0), (h / 2, -0.5)]
    else:
        v = [(h / 2, 0.0), (-h / 2, 0.5), (-h / 2, -0.5)]
    x, y = _polygon(v, n_side)
    return _body("Triangle, apex forward" if apex_forward else "Triangle, flat side forward", x, y,
                 "Equilateral triangular cylinder.", shape="triangle",
                 cd_sub=1.6 if apex_forward else 2.0, cd_super=1.6 if apex_forward else 2.0, strouhal=0.15)


def sphere(n=200):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return _body("Sphere (3-D, axisymmetric)", 0.5 * np.cos(t), 0.5 * np.sin(t),
                 "Sphere of diameter d; flow shown in a plane through the axis (exact 3-D potential flow).",
                 shape="sphere", strouhal=0.19, axisymmetric=True, rounded=True)


def blunt_bodies():
    return [cylinder(), sphere(), ellipse_body(2.0), ellipse_body(4.0), ellipse_body(8.0),
            ellipse_body(0.5), square(), diamond(), normal_plate(), half_cylinder(True),
            half_cylinder(False), triangle(True), triangle(False)]
