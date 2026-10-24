"""Airfoil library and an off-screen smoke test of the whole GUI."""
import os

import numpy as np
import pytest

from windtunnel.physics import library, naca


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDTUNNEL_DATA_DIR", str(tmp_path))


def test_catalog_has_many_airfoils():
    cat = library.Catalog()
    assert len(cat.entries) > 900
    for family in ("NACA 4-digit", "NACA 5-digit", "NACA 4-digit modified", "Joukowski", "Supersonic"):
        assert family in cat.categories
    assert cat.search("2412")[0].name == "NACA 2412"


@pytest.mark.parametrize("code", ["0012", "2412", "6409", "0012-34", "2415-63", "23012", "23112", "44015"])
def test_naca_codes_build_valid_shapes(code):
    af = naca.from_code(code).normalized()
    props = af.properties()
    assert props["max thickness"] == pytest.approx(int(code[-2:] if "-" not in code else code[2:4]) / 100, rel=0.05)
    assert af.x.min() == pytest.approx(0.0, abs=1e-6)
    assert af.x.max() == pytest.approx(1.0, abs=1e-3)


@pytest.mark.parametrize("bad", ["12", "2012", "0000", "63215x"])
def test_bad_naca_codes_are_rejected(bad):
    with pytest.raises(ValueError):
        naca.from_code(bad)


def test_parse_selig_and_lednicer():
    af = naca.naca4("2412")
    selig = "MY AIRFOIL\n" + "\n".join(f"{x:.6f} {y:.6f}" for x, y in zip(af.x, af.y))
    s = library.parse_dat(selig)
    assert s.name == "MY AIRFOIL" and len(s.x) == len(af.x)
    (xu, yu), (xl, yl) = af.surfaces()
    led = (f"LEDNICER TEST\n{len(xu)}. {len(xl)}.\n\n" + "\n".join(f"{x} {y}" for x, y in zip(xu, yu)) + "\n\n"
           + "\n".join(f"{x} {y}" for x, y in zip(xl, yl)))
    l = library.parse_dat(led)
    assert np.allclose(sorted(l.x), sorted(np.concatenate([xu[::-1], xl[1:]])))
    assert l.y[: len(l.y) // 3].mean() > 0     # upper surface comes first (Selig order)


def test_import_copies_file(tmp_path):
    af = naca.naca4("4415")
    path = tmp_path / "mine.dat"
    path.write_text("MINE\n" + "\n".join(f"{x} {y}" for x, y in zip(af.x, af.y)))
    library.import_file(str(path))
    assert (library.imported_dir() / "mine.dat").exists()
    assert any(e.name == "mine" for e in library.Catalog().entries)


def test_gui_smoke():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from windtunnel.gui.app import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.show()
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        app.processEvents()
        tab = win.tabs.widget(i)
        tab.recompute()
        assert tab.result is not None
    # drag the probe across the airfoil and read the local state
    view = win.tabs.widget(0).panel.view
    view.move_probe_to(-0.8, 0.1)
    upstream = view.probe_state()["speed"]
    view.move_probe_to(0.25, 0.1)
    over = view.probe_state()["speed"]
    assert over > upstream
    # free-probe mode traces a streamline through the probe
    view.snap.setChecked(False)
    view.move_probe_to(0.5, 0.3)
    view._release(None)
    assert view.current_line() is not None
    win.close()
