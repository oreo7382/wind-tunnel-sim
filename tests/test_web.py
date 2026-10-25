"""The browser version: the Python bridge runs in normal Python, and the site builds."""
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web" / "py"))
sys.path.insert(0, str(ROOT / "web"))

import bridge  # noqa: E402
import build_site  # noqa: E402

FLOW = dict(altitude=0, dT=0, unit="m/s", n_lines=12, background="cp")


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDTUNNEL_DATA_DIR", str(tmp_path))


def run(fn, **params):
    return json.loads(getattr(bridge, fn)(json.dumps({**FLOW, **params})))


def test_physics_package_has_no_gui_dependencies():
    # Pyodide only loads NumPy, so the physics code must not import matplotlib or Qt
    for path in (ROOT / "src" / "windtunnel" / "physics").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "matplotlib" not in text and "PySide6" not in text, path.name


def test_airfoil_run_matches_desktop_physics():
    out = run("airfoil_run", name="NACA 2412", alpha=4, speed=50, chord=1, span=1)
    assert out["cl"] == pytest.approx(0.671, abs=0.01)
    field = out["field"]
    n_points = field["line_offsets"][-1]
    assert n_points > 100
    assert set(field["state"]) >= {"speed", "mach", "cp", "pressure", "temperature", "density"}
    assert "f32" in field["x"] and "values" in field["grid"]
    assert any(r[0] == "Lift coefficient CL" for r in out["rows"])


def test_supersonic_airfoil_run():
    out = run("airfoil_run", name="Biconvex 6%", alpha=2, speed=2.0, unit="Mach", chord=1, span=1)
    assert out["field"]["mach_lines"]
    assert any(r[0] == "Regime" and r[1] == "supersonic" for r in out["rows"])


def test_wing_and_propeller_runs():
    wing = run("wing_run", name="NACA 2412", span_b=10, root=1.6, taper=0.5, sweep=0, dihedral=3, twist=-2,
               alpha=5, speed=60, n_span=16, n_chord=2, station=0.5, vortices=True)
    assert wing["summary"]["CL"] > 0.3 and wing["vortices"]
    prop = run("prop_run", name="NACA 4412", diameter=1.8, hub=0.15, blades=2, pitch=1.5, c_root=0.12,
               c_tip=0.06, n_el=12, rpm=2400, speed=40, station=0.75)
    assert prop["summary"]["T"] > 0 and 0.6 < prop["summary"]["eta"] < 0.95


def test_site_builds(tmp_path):
    out = build_site.build(tmp_path / "site")
    for name in build_site.PAGE_FILES + ("icon.png", "windtunnel.zip"):
        assert (out / name).exists(), name
    names = zipfile.ZipFile(out / "windtunnel.zip").namelist()
    assert "bridge.py" in names and "windtunnel/physics/airfoil2d.py" in names
    assert "?v=" in (out / "index.html").read_text(encoding="utf-8")
