"""Hess-Smith panel method for 2-D incompressible potential flow.

Each straight panel carries a constant-strength source q_j; all panels share one
constant vortex strength gamma. For airfoils gamma is fixed by the Kutta
condition (equal tangential speed on the two trailing-edge panels). For blunt
bodies gamma is prescribed (zero, or a value representing a spinning body).

Because the equations are linear in the free-stream direction, the system is
solved once per geometry for unit free streams along x and y (and for a unit
circulation). Any angle of attack is then a cheap superposition.

Velocities are normalised by the free-stream speed and lengths by the
reference length of the body.
"""
from __future__ import annotations

import numpy as np

from .geometry import Airfoil

TWO_PI = 2 * np.pi


class PanelSolution:
    def __init__(self, body: Airfoil):
        self.body = body
        # internal panel ordering is clockwise so that the left-hand normal points outwards
        xb = body.x[::-1].copy()
        yb = body.y[::-1].copy()
        if body.blunt and np.hypot(xb[0] - xb[-1], yb[0] - yb[-1]) > 1e-12:
            xb = np.append(xb, xb[0])
            yb = np.append(yb, yb[0])
        self.xb, self.yb = xb, yb
        self.n = len(xb) - 1
        dx, dy = np.diff(xb), np.diff(yb)
        self.length = np.hypot(dx, dy)
        self.tx, self.ty = dx / self.length, dy / self.length
        self.nx, self.ny = -self.ty, self.tx
        self.xc = 0.5 * (xb[:-1] + xb[1:])
        self.yc = 0.5 * (yb[:-1] + yb[1:])
        self._solve()

    # ------------------------------------------------------------ influences
    def _influence(self, px, py, self_mask=None):
        """Velocity components induced at points (px, py) by unit sources / unit vortex.

        Returns (us, vs, uv, vv): us, vs have shape (n_points, n_panels); uv, vv are
        the summed velocity of a unit vortex sheet on every panel, shape (n_points,).
        """
        px = np.asarray(px, dtype=float)[:, None]
        py = np.asarray(py, dtype=float)[:, None]
        ax, ay = self.xb[:-1][None, :], self.yb[:-1][None, :]
        tx, ty = self.tx[None, :], self.ty[None, :]
        L = self.length[None, :]
        rx, ry = px - ax, py - ay
        xi = rx * tx + ry * ty
        eta = -rx * ty + ry * tx
        r_a2 = xi * xi + eta * eta
        r_b2 = (xi - L) ** 2 + eta * eta
        theta = np.arctan2(eta * L, xi * (xi - L) + eta * eta)
        log_term = 0.5 * np.log(np.maximum(r_a2, 1e-30) / np.maximum(r_b2, 1e-30))
        if self_mask is not None:
            theta = np.where(self_mask, np.pi, theta)
        # source: u_xi = ln(rA/rB)/2pi, u_eta = theta/2pi
        s_xi, s_eta = log_term / TWO_PI, theta / TWO_PI
        # vortex (counter-clockwise positive): u_xi = -theta/2pi, u_eta = ln(rA/rB)/2pi
        v_xi, v_eta = -theta / TWO_PI, log_term / TWO_PI
        nx, ny = -ty, tx
        us = s_xi * tx + s_eta * nx
        vs = s_xi * ty + s_eta * ny
        uv = (v_xi * tx + v_eta * nx).sum(axis=1)
        vv = (v_xi * ty + v_eta * ny).sum(axis=1)
        return us, vs, uv, vv

    def _solve(self):
        n = self.n
        mask = np.eye(n, dtype=bool)
        us, vs, uv, vv = self._influence(self.xc, self.yc, mask)
        nx, ny, tx, ty = self.nx[:, None], self.ny[:, None], self.tx[:, None], self.ty[:, None]
        a_src_n = us * nx + vs * ny
        a_vor_n = uv * self.nx + vv * self.ny
        a_src_t = us * tx + vs * ty
        a_vor_t = uv * self.tx + vv * self.ty
        self._a_src_t, self._a_vor_t = a_src_t, a_vor_t

        A = np.zeros((n + 1, n + 1))
        A[:n, :n] = a_src_n
        A[:n, n] = a_vor_n
        kutta = not self.body.blunt
        if kutta:
            A[n, :n] = a_src_t[0] + a_src_t[-1]
            A[n, n] = a_vor_t[0] + a_vor_t[-1]
        else:
            A[n, n] = 1.0
        rhs = np.zeros((n + 1, 3))
        rhs[:n, 0] = -self.nx          # unit free stream along +x
        rhs[:n, 1] = -self.ny          # unit free stream along +y
        if kutta:
            rhs[n, 0] = -(self.tx[0] + self.tx[-1])
            rhs[n, 1] = -(self.ty[0] + self.ty[-1])
        else:
            rhs[n, 2] = 1.0            # unit vortex-sheet strength
        self._basis = np.linalg.solve(A, rhs)  # columns: x-stream, y-stream, circulation

    # -------------------------------------------------------------- results
    def strengths(self, alpha_rad: float, gamma_body: float = 0.0):
        """Source strengths and vortex strength for free stream at angle alpha.

        ``gamma_body`` is the prescribed vortex-sheet strength for blunt bodies.
        """
        c = self._basis[:, 0] * np.cos(alpha_rad) + self._basis[:, 1] * np.sin(alpha_rad)
        if self.body.blunt:
            c = c + self._basis[:, 2] * gamma_body
        return c[:-1], c[-1]

    def surface_velocity(self, alpha_rad: float, gamma_body: float = 0.0):
        """Tangential velocity at each control point (clockwise panel order)."""
        q, g = self.strengths(alpha_rad, gamma_body)
        vt = self._a_src_t @ q + self._a_vor_t * g
        vt += np.cos(alpha_rad) * self.tx + np.sin(alpha_rad) * self.ty
        return vt

    def circulation(self, alpha_rad: float, gamma_body: float = 0.0) -> float:
        """Total circulation, clockwise positive (lift-producing for flow along +x)."""
        _, g = self.strengths(alpha_rad, gamma_body)
        return -g * float(self.length.sum())

    def velocity(self, px, py, alpha_rad: float, gamma_body: float = 0.0, chunk: int = 4000):
        """Velocity (u, v) at arbitrary points (normalised by free-stream speed)."""
        px = np.atleast_1d(np.asarray(px, dtype=float))
        py = np.atleast_1d(np.asarray(py, dtype=float))
        shape = px.shape
        px, py = px.ravel(), py.ravel()
        q, g = self.strengths(alpha_rad, gamma_body)
        u = np.empty_like(px)
        v = np.empty_like(px)
        for i in range(0, len(px), chunk):
            sl = slice(i, i + chunk)
            us, vs, uv, vv = self._influence(px[sl], py[sl])
            u[sl] = us @ q + uv * g + np.cos(alpha_rad)
            v[sl] = vs @ q + vv * g + np.sin(alpha_rad)
        return u.reshape(shape), v.reshape(shape)

    def basis_velocity(self, px, py, chunk: int = 4000):
        """Field velocities for the three basis solutions, for fast re-use across alpha.

        Returns array of shape (3, 2, n_points): basis (x-stream, y-stream, circulation),
        component (u, v). Free-stream parts are included in the first two bases.
        """
        px = np.asarray(px, dtype=float).ravel()
        py = np.asarray(py, dtype=float).ravel()
        out = np.zeros((3, 2, len(px)))
        for i in range(0, len(px), chunk):
            sl = slice(i, i + chunk)
            us, vs, uv, vv = self._influence(px[sl], py[sl])
            for k in range(3):
                q, g = self._basis[:-1, k], self._basis[-1, k]
                out[k, 0, sl] = us @ q + uv * g
                out[k, 1, sl] = vs @ q + vv * g
        out[0, 0] += 1.0
        out[1, 1] += 1.0
        return out

    def combine_basis(self, basis, alpha_rad: float, gamma_body: float = 0.0):
        """Velocity from a ``basis_velocity`` result for a given alpha."""
        vel = basis[0] * np.cos(alpha_rad) + basis[1] * np.sin(alpha_rad)
        if self.body.blunt:
            vel = vel + basis[2] * gamma_body
        return vel[0], vel[1]

    def forces(self, cp: np.ndarray, alpha_rad: float, moment_ref=(0.25, 0.0)):
        """Integrate a surface Cp distribution (clockwise order) into cl, cd_pressure, cm."""
        fx = -np.sum(cp * self.nx * self.length)
        fy = -np.sum(cp * self.ny * self.length)
        ca, sa = np.cos(alpha_rad), np.sin(alpha_rad)
        cl = fy * ca - fx * sa
        cd = fx * ca + fy * sa
        rx, ry = self.xc - moment_ref[0], self.yc - moment_ref[1]
        mz = np.sum(rx * (-cp * self.ny * self.length) - ry * (-cp * self.nx * self.length))
        return cl, cd, -mz  # nose-up positive pitching moment
