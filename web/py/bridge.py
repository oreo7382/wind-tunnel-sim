"""Bridge between the browser UI and the physics package (runs inside Pyodide).

Every public function takes a JSON string of parameters and returns a JSON string
with plain lists, so the JavaScript side never touches Python objects. The same
physics code as the desktop app is used unchanged; only the drawing happens in
the browser.
"""
from __future__ import annotations

import base64
import json
import math
from dataclasses import astuple

import numpy as np

from windtunnel.physics import airfoil2d as a2
from windtunnel.physics import compressibility as comp
from windtunnel.physics import library
from windtunnel.physics import propeller as pr
from windtunnel.physics import wing3d as w3
from windtunnel.physics.atmosphere import Freestream, isa
from windtunnel.physics.flowfield import FlowField, trace_streamlines

FlowField.grid_shape = (180, 120)   # lighter velocity grid: WebAssembly is slower than native NumPy

_TO_MS = {"m/s": 1.0, "km/h": 1 / 3.6, "knots": 0.514444, "mph": 0.44704}
_STATE_KEYS = ("speed", "speed_ratio", "mach", "cp", "pressure", "temperature", "density", "total_pressure")
_catalog = None
_sections: dict = {}
_wing_models: dict = {}
_prop_models: dict = {}


# ------------------------------------------------------------------ helpers
def _catalog_get():
    global _catalog
    if _catalog is None:
        _catalog = library.Catalog()
    return _catalog


def _section(name: str, n_panels: int = 160) -> a2.Section:
    key = (name, n_panels)
    if key not in _sections:
        if len(_sections) > 8:
            _sections.clear()
        _sections[key] = a2.Section(_catalog_get().get(name), n_panels)
    return _sections[key]


def _freestream(p) -> Freestream:
    atm = isa(float(p.get("altitude", 0.0)), float(p.get("dT", 0.0)))
    speed = float(p.get("speed", 50.0))
    unit = p.get("unit", "m/s")
    v = speed * atm.speed_of_sound if unit == "Mach" else speed * _TO_MS[unit]
    return Freestream(atm, max(v, 0.0))


def _settings(p, **kw) -> a2.Settings:
    values = dict(flow_model=p.get("flow_model", a2.FLOW_MODELS[0]),
                  correction=p.get("correction", "Karman-Tsien"),
                  viscous=bool(p.get("viscous", True)), stall_model=bool(p.get("stall", True)),
                  transition=p.get("transition", a2.TRANSITION_MODES[0]),
                  trip_upper=float(p.get("trip_u", 0.1)), trip_lower=float(p.get("trip_l", 0.1)))
    values.update(kw)
    return a2.Settings(**values)


def _arr(a, digits=None):
    """Numeric array for the browser: little-endian float32, base64-encoded (NaN = missing)."""
    a = np.ma.filled(np.ma.masked_invalid(np.ma.asarray(a, dtype=float)), np.nan).ravel()
    return {"f32": base64.b64encode(a.astype("<f4").tobytes()).decode("ascii")}


def fmt(value, unit="", digits=4):
    if value is None:
        return "—"
    v = float(value)
    if math.isnan(v):
        return "—"
    if math.isinf(v):
        return "∞"
    a = abs(v)
    if a != 0 and (a >= 1e6 or a < 1e-3):
        s = f"{v:.{digits - 1}e}"
    else:
        dec = max(0, digits - 1 - (int(math.floor(math.log10(a))) if a > 0 else 0))
        s = f"{v:.{dec}f}"
    return f"{s} {unit}".strip()


def _freestream_info(fs: Freestream):
    a = fs.atmosphere
    return (f"V = {fs.speed:.1f} m/s ({fs.speed * 3.6:.0f} km/h), M = {fs.mach:.3f} · "
            f"T = {a.temperature:.1f} K, p = {a.pressure / 1000:.2f} kPa, ρ = {a.density:.4f} kg/m³, "
            f"a = {a.speed_of_sound:.1f} m/s, q = {fs.dynamic_pressure:.0f} Pa")


def _field_payload(field, n_lines: int, background: str, markers=None):
    """Streamlines, the local state at every streamline point, and a background grid."""
    x0, x1, y0, y1 = field.bounds
    lines = trace_streamlines(field, field.seeds(int(n_lines)), step=(x1 - x0) / 220)
    if lines:
        pts = np.vstack(lines)
    else:
        pts = np.zeros((0, 2))
    try:
        st = field.state(pts[:, 0], pts[:, 1], exact=False)
    except TypeError:
        st = field.state(pts[:, 0], pts[:, 1])
    payload = {
        "bounds": [x0, x1, y0, y1],
        "outlines": [[_arr(ox), _arr(oy)] for ox, oy in field.outlines],
        "line_offsets": np.cumsum([0] + [len(l) for l in lines]).tolist(),
        "x": _arr(pts[:, 0]),
        "y": _arr(pts[:, 1]),
        "state": {k: _arr(st[k]) for k in _STATE_KEYS},
        "freestream_speed": float(field.fs.speed),
        "freestream_pressure": float(field.fs.atmosphere.pressure),
        "mach_lines": [[_arr(mx), _arr(my)] for mx, my in field.annotations.get("mach_lines", [])],
        "disk": bool(field.annotations.get("disk")),
        "markers": markers or [],
    }
    if background and background != "none":
        nx, ny = 150, 100
        X, Y, Z = field.scalar_grid(background, nx, ny)
        payload["grid"] = {"kind": background, "nx": nx, "ny": ny, "values": _arr(Z)}
    return payload


def _bl_markers(res: a2.Result):
    out = [{"x": float(res.stagnation[0]), "y": float(res.stagnation[1]), "kind": "stagnation"}]
    x, y, up = res.x, res.y, res.upper

    def surf_y(xq, upper):
        m = up if upper else ~up
        o = np.argsort(x[m])
        return float(np.interp(xq, x[m][o], y[m][o]))

    for b, upper in ((res.bl_upper, True), (res.bl_lower, False)):
        if b is None:
            continue
        if b.x_transition is not None:
            out.append({"x": float(b.x_transition), "y": surf_y(b.x_transition, upper), "kind": "transition"})
        if b.x_separation is not None:
            out.append({"x": float(b.x_separation), "y": surf_y(b.x_separation, upper), "kind": "separation"})
    return out


def _cp_payload(res: a2.Result):
    out = {}
    for mask, side in ((res.upper, "upper"), (~res.upper, "lower")):
        o = np.argsort(res.x[mask])
        out[side] = {"x": _arr(res.x[mask][o]), "cp": _arr(res.cp[mask][o]), "cp0": _arr(res.cp0[mask][o])}
    out["cp_crit"] = float(res.cp_crit) if res.mach > 0.2 and res.model != "incompressible" else None
    out["model"] = res.model
    return out


# ------------------------------------------------------------------ catalog
def catalog(_args="{}") -> str:
    cat = _catalog_get()
    return json.dumps({"names": [e.name for e in cat.entries], "categories": cat.categories})


def airfoil_info(args) -> str:
    p = json.loads(args)
    af = _catalog_get().get(p["name"])
    props = af.properties()
    return json.dumps({"name": af.name, "description": af.description, "category": af.category,
                       "x": _arr(af.x), "y": _arr(af.y),
                       "thickness": props["max thickness"], "camber": props["max camber"]})


# ------------------------------------------------------------------ 2-D airfoil
def airfoil_run(args) -> str:
    p = json.loads(args)
    sec = _section(p["name"], int(p.get("n_panels", 160)))
    fs = _freestream(p)
    s = _settings(p, alpha_deg=float(p["alpha"]), chord=float(p["chord"]), span=float(p["span"]))
    r = a2.analyze(sec, fs, s)
    markers = _bl_markers(r) if r.model != "supersonic" else []
    upper = bool(p.get("surf_upper", True))
    ys, sp = r.surface_point(float(p.get("surf_x", 0.25)), upper)
    model_name = {"incompressible": "incompressible potential flow",
                  "subsonic": f"compressible ({r.settings.correction})",
                  "supersonic": "supersonic (Busemann 2nd order)"}[r.model]
    rows = [["Flow"], ["Regime", r.regime], ["Model used", model_name], ["Mach number", fmt(r.mach)],
            ["Airspeed", fmt(fs.speed, "m/s")], ["Reynolds number", fmt(r.reynolds, digits=3)],
            ["Dynamic pressure q", fmt(r.q, "Pa")],
            ["Coefficients"], ["Lift coefficient CL", fmt(r.cl)], ["Drag coefficient CD", fmt(r.cd)],
            ["   skin friction + form", fmt(r.cd_friction)], ["   separation", fmt(r.cd_separation)],
            ["   wave", fmt(r.cd_wave)], ["Moment coeff. Cm (c/4)", fmt(r.cm)],
            ["Lift / drag", fmt(r.lift_to_drag)],
            ["Forces"], ["Lift per metre span", fmt(r.lift_per_span, "N/m")],
            ["Drag per metre span", fmt(r.drag_per_span, "N/m")],
            [f"Lift (span {r.settings.span:g} m)", fmt(r.lift, "N")],
            [f"Drag (span {r.settings.span:g} m)", fmt(r.drag, "N")],
            ["Compressibility"], ["Critical Mach number", fmt(r.mcrit) if r.model != "supersonic" else "—"],
            ["Minimum Cp", fmt(r.cp_min)]]
    if r.bl_upper is not None:
        rows.append(["Boundary layer"])
        for name, b in (("upper", r.bl_upper), ("lower", r.bl_lower)):
            rows.append([f"Transition x/c ({name})", fmt(b.x_transition) if b.x_transition is not None else "laminar"])
            rows.append([f"Separation x/c ({name})", fmt(b.x_separation) if b.x_separation is not None else "attached"])
    rows += [[f"Surface point (x/c = {float(p.get('surf_x', 0.25)):.2f}, {'upper' if upper else 'lower'})"],
             ["Local speed", fmt(sp["speed"], "m/s")], ["Local Mach", fmt(sp["mach"])],
             ["Local Cp", fmt(sp["cp"])], ["Local pressure", fmt(sp["pressure"] / 1000, "kPa")]]
    field = _field_payload(r.flow, p.get("n_lines", 30), p.get("background", "cp"), markers)
    return json.dumps({"field": field, "cp": _cp_payload(r), "rows": rows, "warnings": r.warnings,
                       "surface_point": {"x": float(p.get("surf_x", 0.25)), "cp": sp["cp"]},
                       "freestream": _freestream_info(fs), "name": sec.name, "cl": r.cl})


def airfoil_polar(args) -> str:
    p = json.loads(args)
    sec = _section(p["name"], int(p.get("n_panels", 160)))
    fs = _freestream(p)
    s = _settings(p, chord=float(p["chord"]), span=float(p["span"]))
    pol = a2.polar(sec, fs, s, np.arange(-10.0, 22.5, 1.0))
    return json.dumps({k: _arr(v) for k, v in pol.items()})


# ------------------------------------------------------------------ 3-D wing
def _planform(p) -> w3.Planform:
    return w3.Planform(float(p["span_b"]), float(p["root"]), float(p["taper"]), float(p["sweep"]),
                       float(p["dihedral"]), float(p["twist"]), int(p.get("n_span", 30)), int(p.get("n_chord", 4)))


def wing_run(args) -> str:
    p = json.loads(args)
    pf = _planform(p)
    sec = _section(p["name"], 140)
    key = (astuple(pf), p["name"])
    if key not in _wing_models:
        if len(_wing_models) > 4:
            _wing_models.clear()
        _wing_models[key] = w3.WingModel(pf, sec)
    m = _wing_models[key]
    fs = _freestream(p)
    settings = _settings(p)
    r = m.solve(float(p["alpha"]), fs, settings, viscous_iteration=bool(p.get("stall", True)))

    vortices = []
    if p.get("vortices", True):
        b2, ct = pf.span / 2, pf.tip_chord
        seeds = []
        for side in (-1, 1):
            y_tip = side * b2 * 0.985
            x_te = pf.x_quarter_at(b2) + 0.75 * ct
            for rad, n in ((0.07 * ct, 4), (0.18 * ct, 6)):
                for ang in np.linspace(0, 2 * np.pi, n, endpoint=False):
                    seeds.append((x_te, y_tip + rad * np.cos(ang), pf.z_at(b2) + rad * np.sin(ang)))
        step = 0.045 * ct
        paths = w3.trace_3d(r, seeds, step=step, n_steps=int(1.0 * pf.span / step))
        vortices = [[_arr(q[::2, 0]), _arr(q[::2, 1]), _arr(q[::2, 2])] for q in paths]

    eta = 2 * r.y / pf.span
    cbar = pf.area / pf.span
    i = r.strip_at(float(p.get("station", 0.5)))
    chord = float(r.chord[i])
    alpha_eff = float(r.alpha_eff[i])
    fs_n = Freestream(fs.atmosphere, fs.speed * math.cos(math.radians(pf.sweep_deg)))
    s2 = _settings(p, alpha_deg=alpha_eff, chord=chord, span=1.0, flow_model="Auto (from Mach number)")
    sres = a2.analyze(sec, fs_n, s2)
    a0 = sec.zero_lift_alpha()[1]
    rows = [["Wing geometry"], ["Area S", fmt(pf.area, "m²")], ["Aspect ratio", fmt(pf.aspect_ratio)],
            ["Mean aerodynamic chord", fmt(pf.mac, "m")],
            ["Flow"], ["Mach number", fmt(r.mach)], ["Airspeed", fmt(fs.speed, "m/s")],
            ["Reynolds number (MAC)", fmt(fs.reynolds(pf.mac), digits=3)],
            ["Coefficients"], ["CL", fmt(r.CL)], ["CD total", fmt(r.CD)], ["   induced CDi", fmt(r.CDi)],
            ["   profile CD0", fmt(r.CD0)], ["   wave CDw", fmt(r.CDw)], ["Span efficiency e", fmt(r.e)],
            ["CL²/(π·AR·e)", fmt(r.CL ** 2 / (math.pi * pf.aspect_ratio * max(r.e, 1e-6)))],
            ["L / D", fmt(r.lift_to_drag)],
            ["3-D lift slope", f"{r.CL_alpha:.3f} /rad"], ["2-D section lift slope", f"{a0:.3f} /rad"],
            ["Forces"], ["Lift", fmt(r.lift, "N")], ["Drag", fmt(r.drag, "N")],
            ["Induced drag", fmt(r.CDi * r.q * pf.area, "N")],
            [f"Section at 2y/b = {eta[i]:.2f}"], ["Local chord", fmt(r.chord[i], "m")],
            ["Local cl (3-D)", fmt(r.cl[i])], ["Induced angle αi", fmt(r.alpha_i[i], "°")],
            ["Effective angle α_eff", fmt(r.alpha_eff[i], "°")], ["Section cd", fmt(sres.cd)]]
    corners = r.panels.reshape(-1, 4, 3)
    return json.dumps({
        "panels": {"x": _arr(corners[..., 0]), "y": _arr(corners[..., 1]), "z": _arr(corners[..., 2]),
                   "dcp": _arr(r.dcp.ravel())},
        "vortices": vortices,
        "span": {"eta": _arr(eta), "cl": _arr(r.cl), "chord": _arr(r.chord), "alpha_i": _arr(r.alpha_i),
                 "alpha_eff": _arr(r.alpha_eff), "cd": _arr(r.cd), "stalled": r.stalled.tolist(),
                 "loading": _arr(r.cl * r.chord / cbar),
                 "elliptic": _arr(4 / math.pi * r.CL * np.sqrt(np.clip(1 - eta ** 2, 0, None))),
                 "CL": r.CL, "alpha": r.alpha_deg},
        "span_b": pf.span, "station_eta": float(eta[i]),
        "rows": rows, "warnings": r.warnings + [w for w in sres.warnings if "Inviscid" not in w],
        "section": {"field": _field_payload(sres.flow, p.get("n_lines", 26), p.get("background", "cp"),
                                            _bl_markers(sres) if sres.model != "supersonic" else []),
                    "cp": _cp_payload(sres), "alpha_eff": alpha_eff, "alpha_i": float(r.alpha_i[i])},
        "summary": {"CL": r.CL, "CDi": r.CDi, "e": r.e},
        "freestream": _freestream_info(fs),
    })


# ------------------------------------------------------------------ propeller
def _geometry(p) -> pr.PropellerGeometry:
    return pr.PropellerGeometry(float(p["diameter"]), float(p["hub"]), int(p["blades"]), float(p["pitch"]),
                                float(p["c_root"]), float(p["c_tip"]), int(p.get("n_el", 24)))


def _prop_model(p):
    geo = _geometry(p)
    key = (astuple(geo), p["name"])
    if key not in _prop_models:
        if len(_prop_models) > 4:
            _prop_models.clear()
        _prop_models[key] = pr.PropellerModel(geo, _section(p["name"], 140))
    return _prop_models[key]


def prop_run(args) -> str:
    p = json.loads(args)
    m = _prop_model(p)
    g = m.geo
    fs = _freestream(p)
    settings = _settings(p)
    r = m.solve(float(p["rpm"]), fs, settings)
    slip = pr.ActuatorDiskField(fs, r.induced_velocity, r.thrust, g.radius)
    i = r.element_at(float(p.get("station", 0.75)))
    chord = float(g.chord(r.r[i]))
    fs_local = Freestream(fs.atmosphere, float(r.W[i]))
    s2 = _settings(p, alpha_deg=float(r.alpha[i]), chord=chord, span=1.0, flow_model="Auto (from Mach number)")
    sres = a2.analyze(m.section, fs_local, s2)
    x = r.r / g.radius
    rows = [["Operating point"], ["Flight speed", fmt(fs.speed, "m/s")], ["Rotational speed", f"{r.rpm:.0f} rpm"],
            ["Advance ratio J = V/(nD)", fmt(r.J)], ["Helical tip Mach", fmt(r.tip_mach)],
            ["Blade angle at 75 % radius", fmt(g.beta75_deg, "°")],
            ["Performance"], ["Thrust T", fmt(r.thrust, "N")], ["Torque Q", fmt(r.torque, "N·m")],
            ["Shaft power P", fmt(r.power / 1000, "kW")], ["Propulsive efficiency η", fmt(r.efficiency)],
            ["Thrust coefficient CT", fmt(r.CT)], ["Power coefficient CP", fmt(r.CP)],
            ["Figure of merit (hover)", fmt(r.figure_of_merit)],
            ["Momentum theory"], ["Induced velocity at disk", fmt(r.induced_velocity, "m/s")],
            ["Disk loading T/A", fmt(r.thrust / (math.pi * g.radius ** 2), "Pa")],
            [f"Blade element r/R = {x[i]:.2f}"], ["Relative speed W", fmt(r.W[i], "m/s")],
            ["Section Mach", fmt(r.mach[i])], ["Angle of attack", fmt(r.alpha[i], "°")],
            ["Inflow angle φ", fmt(r.phi[i], "°")],
            ["Blade angle β", fmt(math.degrees(float(g.blade_angle(r.r[i]))), "°")],
            ["Section cl / cd", f"{r.cl[i]:.3f} / {r.cd[i]:.4f}"]]
    return json.dumps({
        "dist": {"x": _arr(x), "dT": _arr(r.dT), "dQ": _arr(r.dQ), "alpha": _arr(r.alpha), "phi": _arr(r.phi),
                 "beta": _arr(np.degrees(g.blade_angle(r.r))), "mach": _arr(r.mach), "W": _arr(r.W)},
        "station_x": float(x[i]),
        "rows": rows, "warnings": r.warnings,
        "slip": _field_payload(slip, p.get("n_lines", 30), p.get("background", "cp")),
        "section": {"field": _field_payload(sres.flow, p.get("sec_n_lines", 26), p.get("sec_background", "cp"),
                                            _bl_markers(sres) if sres.model != "supersonic" else []),
                    "cp": _cp_payload(sres)},
        "summary": {"T": r.thrust, "P": r.power, "eta": r.efficiency, "J": r.J},
        "freestream": _freestream_info(fs),
    })


def prop_map(args) -> str:
    p = json.loads(args)
    m = _prop_model(p)
    fs = _freestream(p)
    pd = m.geo.pitch / m.geo.diameter
    data = m.sweep_advance_ratio(float(p["rpm"]), fs, _settings(p), np.linspace(0.0, pd * 1.25, 11))
    return json.dumps({"J": _arr(data[:, 0]), "CT": _arr(data[:, 1]), "CP": _arr(data[:, 2]), "eta": _arr(data[:, 3])})
