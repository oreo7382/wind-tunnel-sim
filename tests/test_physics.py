"""Validation of the physics models against exact solutions and published data."""
import numpy as np
import pytest

from windtunnel.physics import airfoil2d as a2
from windtunnel.physics import bluntbody as bb
from windtunnel.physics import boundary_layer as bl
from windtunnel.physics import compressibility as comp
from windtunnel.physics import naca, shapes
from windtunnel.physics import propeller as pr
from windtunnel.physics import wing3d as w3
from windtunnel.physics.atmosphere import Freestream, isa
from windtunnel.physics.flowfield import trace_streamlines
from windtunnel.physics.panel import PanelSolution


@pytest.fixture(scope="module")
def sea_level():
    return isa(0.0)


def fs_for_re(atm, re, chord=1.0):
    return Freestream(atm, re * atm.kinematic_viscosity / chord)


# ------------------------------------------------------------------ atmosphere
def test_isa_standard_values():
    a = isa(0)
    assert a.temperature == pytest.approx(288.15)
    assert a.pressure == pytest.approx(101325, rel=1e-6)
    assert a.density == pytest.approx(1.225, rel=1e-3)
    assert a.speed_of_sound == pytest.approx(340.29, rel=1e-3)
    t = isa(11000)
    assert t.temperature == pytest.approx(216.65)
    assert t.pressure == pytest.approx(22632, rel=2e-3)


# ------------------------------------------------------------------ panel method
def test_cylinder_matches_exact_potential_flow():
    ps = PanelSolution(shapes.cylinder(200))
    vt = ps.surface_velocity(0.0)
    theta = np.arctan2(ps.yc, ps.xc)
    assert np.max(np.abs((1 - vt ** 2) - (1 - 4 * np.sin(theta) ** 2))) < 1e-6


def test_naca0012_inviscid_lift_and_symmetry():
    sec = a2.Section(naca.from_code("0012"))
    a = np.radians(5.0)
    cl, cd, cm = sec.panel.forces(sec.inviscid(a)[1], a)
    assert cl == pytest.approx(0.605, abs=0.015)         # XFOIL inviscid ~0.60
    assert abs(cd) < 2e-3                                # d'Alembert
    cl0 = sec.panel.forces(sec.inviscid(0.0)[1], 0.0)[0]
    assert abs(cl0) < 1e-4


def test_naca2412_zero_lift_and_moment():
    sec = a2.Section(naca.from_code("2412"))
    a0, slope = sec.zero_lift_alpha()
    assert a0 == pytest.approx(-2.1, abs=0.3)            # thin-airfoil / experiment ~ -2.1 deg
    assert 6.3 < slope < 7.3
    cm = sec.panel.forces(sec.inviscid(0.0)[1], 0.0)[2]
    assert cm == pytest.approx(-0.053, abs=0.01)


# ------------------------------------------------------------------ compressibility
def test_critical_mach_numbers():
    assert comp.critical_mach(-3.0) == pytest.approx(0.40, abs=0.01)     # circular cylinder
    assert comp.critical_mach(-0.43, "Prandtl-Glauert") == pytest.approx(0.74, abs=0.01)
    assert comp.cp_critical(1.0) == pytest.approx(0.0, abs=1e-12)


def test_prandtl_glauert_scaling():
    assert float(comp.correct_cp(-0.5, 0.6, "Prandtl-Glauert")) == pytest.approx(-0.625)
    kt = float(comp.correct_cp(-0.5, 0.6, "Karman-Tsien"))
    assert kt < -0.625                                  # KT is stronger for suction peaks


def test_compressible_lift_increase(sea_level):
    sec = a2.Section(naca.from_code("0012"))
    s = a2.Settings(alpha_deg=2.0, viscous=False, flow_model="Compressible", correction="Prandtl-Glauert")
    cl_lo = a2.analyze(sec, Freestream.from_mach(sea_level, 0.05), s, build_flow=False).cl
    cl_hi = a2.analyze(sec, Freestream.from_mach(sea_level, 0.6), s, build_flow=False).cl
    assert cl_hi / cl_lo == pytest.approx(1 / np.sqrt(1 - 0.36), rel=0.02)


def test_supersonic_biconvex_matches_ackeret(sea_level):
    sec = a2.Section(shapes.biconvex(0.06))
    r = a2.analyze(sec, Freestream.from_mach(sea_level, 2.0), a2.Settings(alpha_deg=2, viscous=False))
    b = np.sqrt(3.0)
    a = np.radians(2)
    assert r.cl == pytest.approx(4 * a / b, rel=0.02)
    assert r.cd == pytest.approx(4 * a * a / b + 16 / 3 * 0.06 ** 2 / b, rel=0.05)


# ------------------------------------------------------------------ boundary layer
def test_thwaites_flat_plate_close_to_blasius():
    s = np.linspace(0, 1, 400)
    res = bl.march(s, s, np.ones_like(s), 1e5, None, free_transition=False)
    blasius = 0.664 / np.sqrt(1e5)
    assert res.theta[-1] == pytest.approx(blasius, rel=0.03)


def test_naca0012_drag_and_stall(sea_level):
    sec = a2.Section(naca.from_code("0012"))
    fs = fs_for_re(sea_level, 3e6)
    r0 = a2.analyze(sec, fs, a2.Settings(alpha_deg=0.0), build_flow=False)
    assert 0.005 < r0.cd < 0.0085                       # measured ~0.0055-0.0065
    p = a2.polar(sec, fs, a2.Settings(), np.arange(8.0, 22.0, 1.0))
    i = int(np.argmax(p["cl"]))
    assert 1.3 < p["cl"][i] < 1.7                       # measured clmax ~1.5-1.6
    assert 13 <= p["alpha"][i] <= 18


def test_streamlines_avoid_body(sea_level):
    sec = a2.Section(naca.from_code("2412"))
    r = a2.analyze(sec, Freestream(sea_level, 30.0), a2.Settings(alpha_deg=4))
    lines = trace_streamlines(r.flow, r.flow.seeds(20))
    assert len(lines) >= 18
    pts = np.vstack(lines)
    assert r.flow.inside(pts[:, 0], pts[:, 1]).sum() == 0


def test_probe_speeds_up_over_upper_surface(sea_level):
    sec = a2.Section(naca.from_code("2412"))
    fs = Freestream(sea_level, 50.0)
    r = a2.analyze(sec, fs, a2.Settings(alpha_deg=4))
    far = r.flow.state(np.array([-0.9]), np.array([0.1]))
    over = r.flow.state(np.array([0.2]), np.array([0.09]))
    assert far["speed"][0] == pytest.approx(50.0, rel=0.02)
    assert over["speed"][0] > 60.0
    assert over["pressure"][0] < sea_level.pressure


# ------------------------------------------------------------------ 3-D wing
def test_trefftz_elliptic_downwash_is_uniform():
    pf = w3.Planform(span=8.0, root_chord=1.0, taper=1.0, n_span=40, n_chord=2)
    lat = w3.Lattice(pf)
    gs = np.sqrt(1 - (2 * lat.y_mid / pf.span) ** 2)
    w = w3._trefftz_normalwash(lat, gs)
    assert np.allclose(w, -1.0 / pf.span, rtol=2e-3)


def test_vlm_lift_slope_and_efficiency(sea_level):
    sec = a2.Section(naca.from_code("0012"))
    pf = w3.Planform(span=8.0, root_chord=1.4, taper=0.4)
    m = w3.WingModel(pf, sec)
    r = m.solve(4.0, Freestream(sea_level, 30.0), a2.Settings(viscous=False))
    ref = w3.datcom_lift_slope(pf.aspect_ratio, r.mach, 0.0)
    assert r.CL_alpha == pytest.approx(ref, rel=0.08)
    assert 0.95 < r.e <= 1.0 + 1e-6


def test_vlm_compressibility_raises_lift_slope(sea_level):
    sec = a2.Section(naca.from_code("0012"))
    m = w3.WingModel(w3.Planform(span=8.0, root_chord=1.0, taper=1.0), sec)
    s = a2.Settings(viscous=False)
    lo = m.solve(3.0, Freestream.from_mach(sea_level, 0.1), s).CL_alpha
    hi = m.solve(3.0, Freestream.from_mach(sea_level, 0.7), s).CL_alpha
    assert hi > 1.1 * lo


def test_wing_stalls_at_high_angle(sea_level):
    sec = a2.Section(naca.from_code("2412"))
    m = w3.WingModel(w3.Planform(), sec)
    fs = Freestream(sea_level, 50.0)
    pre, post = m.solve(22.0, fs, a2.Settings()), m.solve(30.0, fs, a2.Settings())
    assert post.stalled.any() and not pre.stalled.all()
    assert post.CL < m.solve(24.0, fs, a2.Settings()).CL      # lift falls after stall


# ------------------------------------------------------------------ propeller
def test_propeller_performance_is_realistic(sea_level):
    sec = a2.Section(naca.from_code("4412"), 140)
    m = pr.PropellerModel(pr.PropellerGeometry(), sec)
    static = m.solve(2400, Freestream(sea_level, 0.0), a2.Settings())
    cruise = m.solve(2400, Freestream(sea_level, 50.0), a2.Settings())
    assert 0.5 < static.figure_of_merit < 0.85
    assert 0.75 < cruise.efficiency < 0.95
    assert static.thrust > cruise.thrust > 0
    # momentum theory consistency: T = 2 rho A (V + v) v
    A = np.pi * m.geo.radius ** 2
    v = cruise.induced_velocity
    assert cruise.thrust == pytest.approx(2 * sea_level.density * A * (50 + v) * v, rel=1e-6)


def test_propeller_windmills_at_high_advance_ratio(sea_level):
    sec = a2.Section(naca.from_code("4412"), 140)
    m = pr.PropellerModel(pr.PropellerGeometry(), sec)
    assert m.solve(2400, Freestream(sea_level, 85.0), a2.Settings()).thrust < 0


# ------------------------------------------------------------------ blunt bodies
def test_cylinder_separation_and_drag(sea_level):
    m = bb.BluntModel(shapes.cylinder())
    r = m.solve(Freestream(sea_level, 10.0), 0.1, 1.0)
    assert 100 < r.separation_angle < 108                # Thwaites on potential flow: ~104.5 deg
    assert r.cd == pytest.approx(1.2, abs=0.1)
    assert r.mcrit == pytest.approx(0.40, abs=0.015)
    assert bb.cylinder_cd(5e5) < 0.5 * bb.cylinder_cd(1e5)   # drag crisis


def test_sphere_drag_correlation():
    assert bb.sphere_cd(1e4) == pytest.approx(0.41, abs=0.05)
    assert bb.sphere_cd(1.0) == pytest.approx(27, rel=0.1)
    assert bb.sphere_cd(6e5) < 0.2


def test_spinning_cylinder_magnus_lift(sea_level):
    m = bb.BluntModel(shapes.cylinder())
    r = m.solve(Freestream(sea_level, 10.0), 0.1, 1.0, spin_ratio=1.0)
    assert r.cl_potential == pytest.approx(2 * np.pi)
    assert 0 < r.cl < r.cl_potential
