"""NACA airfoil generators: 4-digit, 4-digit modified and 5-digit (standard and reflex)."""
from __future__ import annotations

import re

import numpy as np

from .geometry import Airfoil

_N_DEFAULT = 121  # points per surface


def _cosine_x(n: int) -> np.ndarray:
    return 0.5 * (1 - np.cos(np.linspace(0, np.pi, n)))


def thickness_4digit(x, t, closed_te=True):
    a4 = -0.1036 if closed_te else -0.1015
    return 5 * t * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x ** 2 + 0.2843 * x ** 3 + a4 * x ** 4)


def thickness_4digit_modified(x, t, le_index: int, max_pos: float):
    """NACA 4-digit-modified thickness (e.g. 0012-34): LE radius index I and max-thickness position."""
    m = max_pos
    d1_table = {0.2: 0.200, 0.3: 0.234, 0.4: 0.315, 0.5: 0.465, 0.6: 0.700}
    d1 = np.interp(m, list(d1_table), list(d1_table.values()))
    d0 = 0.002
    k = 1 - m
    # aft polynomial in (1 - x): y(m) = 0.1 and y'(m) = 0
    A = np.array([[k ** 2, k ** 3], [2 * k, 3 * k ** 2]])
    b = np.array([0.1 - d0 - d1 * k, -d1])
    d2, d3 = np.linalg.solve(A, b)
    # LE radius scales with (I/6)^2 relative to the normal 4-digit section; I = 9 is 3x normal
    a0 = 0.296904 * (le_index / 6.0 if le_index < 9 else np.sqrt(3.0))
    curv_aft = 2 * d2 + 6 * d3 * k
    # forward polynomial a0 sqrt(x) + a1 x + a2 x^2 + a3 x^3: value, slope, curvature continuity at m
    A = np.array([[m, m ** 2, m ** 3],
                  [1, 2 * m, 3 * m ** 2],
                  [0, 2, 6 * m]])
    b = np.array([0.1 - a0 * np.sqrt(m),
                  -a0 / (2 * np.sqrt(m)),
                  curv_aft + a0 / (4 * m ** 1.5)])
    a1, a2, a3 = np.linalg.solve(A, b)
    x = np.asarray(x, dtype=float)
    fwd = a0 * np.sqrt(x) + a1 * x + a2 * x ** 2 + a3 * x ** 3
    xa = 1 - x
    aft = d0 + d1 * xa + d2 * xa ** 2 + d3 * xa ** 3
    return 5 * t * np.where(x < m, fwd, aft)


def camber_4digit(x, m, p):
    if m == 0 or p == 0:
        return np.zeros_like(x), np.zeros_like(x)
    yc = np.where(x < p, m / p ** 2 * (2 * p * x - x ** 2), m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x ** 2))
    dyc = np.where(x < p, 2 * m / p ** 2 * (p - x), 2 * m / (1 - p) ** 2 * (p - x))
    return yc, dyc


_FIVE_STD = {1: (0.0580, 361.4), 2: (0.1260, 51.64), 3: (0.2025, 15.957), 4: (0.2900, 6.643), 5: (0.3910, 3.230)}
_FIVE_REFLEX = {2: (0.1300, 51.99, 0.000764), 3: (0.2170, 15.793, 0.00677),
                4: (0.3180, 6.520, 0.0303), 5: (0.4410, 3.191, 0.1355)}


def camber_5digit(x, design_cl_digit: int, pos_digit: int, reflex: bool):
    scale = (0.15 * design_cl_digit) / 0.3
    if not reflex:
        r, k1 = _FIVE_STD[pos_digit]
        yc = np.where(x < r, k1 / 6 * (x ** 3 - 3 * r * x ** 2 + r ** 2 * (3 - r) * x), k1 * r ** 3 / 6 * (1 - x))
        dyc = np.where(x < r, k1 / 6 * (3 * x ** 2 - 6 * r * x + r ** 2 * (3 - r)), -k1 * r ** 3 / 6)
    else:
        r, k1, k21 = _FIVE_REFLEX[pos_digit]
        yc = np.where(x < r,
                      k1 / 6 * ((x - r) ** 3 - k21 * (1 - r) ** 3 * x - r ** 3 * x + r ** 3),
                      k1 / 6 * (k21 * (x - r) ** 3 - k21 * (1 - r) ** 3 * x - r ** 3 * x + r ** 3))
        dyc = np.where(x < r,
                       k1 / 6 * (3 * (x - r) ** 2 - k21 * (1 - r) ** 3 - r ** 3),
                       k1 / 6 * (3 * k21 * (x - r) ** 2 - k21 * (1 - r) ** 3 - r ** 3))
    return yc * scale, dyc * scale


def _assemble(name, x, yt, yc, dyc, category, description):
    th = np.arctan(dyc)
    xu, yu = x - yt * np.sin(th), yc + yt * np.cos(th)
    xl, yl = x + yt * np.sin(th), yc - yt * np.cos(th)
    xs = np.concatenate([xu[::-1], xl[1:]])
    ys = np.concatenate([yu[::-1], yl[1:]])
    return Airfoil(name, xs, ys, category, description)


def naca4(code: str, n: int = _N_DEFAULT, closed_te: bool = True) -> Airfoil:
    m, p, t = int(code[0]) / 100, int(code[1]) / 10, int(code[2:4]) / 100
    x = _cosine_x(n)
    yc, dyc = camber_4digit(x, m, p)
    desc = (f"NACA 4-digit: {t*100:.0f}% thick" +
            (f", {m*100:.0f}% camber at {p*100:.0f}% chord" if m > 0 else ", symmetric"))
    return _assemble(f"NACA {code}", x, thickness_4digit(x, t, closed_te), yc, dyc,
                     "NACA 4-digit" if m > 0 else "NACA 4-digit symmetric", desc)


def naca4_modified(code: str, n: int = _N_DEFAULT) -> Airfoil:
    base, mod = code.split("-")
    m, p, t = int(base[0]) / 100, int(base[1]) / 10, int(base[2:4]) / 100
    le_index, max_pos = int(mod[0]), int(mod[1]) / 10
    x = _cosine_x(n)
    yc, dyc = camber_4digit(x, m, p)
    yt = thickness_4digit_modified(x, t, le_index, max_pos)
    return _assemble(f"NACA {code}", x, yt, yc, dyc, "NACA 4-digit modified",
                     f"NACA 4-digit modified: {t*100:.0f}% thick, LE radius index {le_index}, "
                     f"max thickness at {max_pos*100:.0f}% chord")


def naca5(code: str, n: int = _N_DEFAULT, closed_te: bool = True) -> Airfoil:
    L, P, Q, t = int(code[0]), int(code[1]), int(code[2]), int(code[3:5]) / 100
    reflex = Q == 1
    table = _FIVE_REFLEX if reflex else _FIVE_STD
    if P not in table or L == 0:
        raise ValueError(f"Unsupported NACA 5-digit series {code[:3]}")
    x = _cosine_x(n)
    yc, dyc = camber_5digit(x, L, P, reflex)
    return _assemble(f"NACA {code}", x, thickness_4digit(x, t, closed_te), yc, dyc,
                     "NACA 5-digit reflex" if reflex else "NACA 5-digit",
                     f"NACA 5-digit: design cl {0.15*L:.2f}, max camber near {P*5}% chord, "
                     f"{t*100:.0f}% thick" + (", reflexed mean line" if reflex else ""))


_RE_4 = re.compile(r"^\d{4}$")
_RE_4M = re.compile(r"^\d{4}-\d{2}$")
_RE_5 = re.compile(r"^\d{5}$")


def from_code(code: str, n: int = _N_DEFAULT) -> Airfoil:
    """Build any NACA 4-digit, 4-digit-modified or 5-digit airfoil from its designation."""
    c = code.upper().replace("NACA", "").replace(" ", "")
    if _RE_4.match(c):
        if int(c[2:]) == 0:
            raise ValueError("Thickness must be greater than zero")
        if int(c[0]) > 0 and int(c[1]) == 0:
            raise ValueError("Cambered 4-digit airfoils need a camber position digit 1-9")
        return naca4(c, n)
    if _RE_4M.match(c):
        if not 2 <= int(c[-1]) <= 6:
            raise ValueError("Max-thickness position digit must be 2-6")
        return naca4_modified(c, n)
    if _RE_5.match(c):
        return naca5(c, n)
    raise ValueError(f"'{code}' is not a NACA 4-digit (2412), 4-digit modified (0012-34) or 5-digit (23012) code")
