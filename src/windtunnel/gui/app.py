"""Main window and application entry point."""
from __future__ import annotations

import sys

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QTabWidget

from .. import __version__

ABOUT = f"""<h3>Virtual Wind Tunnel {__version__}</h3>
<p>An educational aerodynamics simulator for airfoils, finite wings, propellers and blunt bodies.</p>
<p><b>Methods:</b> Hess-Smith panel method · Thwaites / Head boundary layers with Michel transition ·
Prandtl-Glauert, Karman-Tsien and Laitone compressibility rules · Busemann supersonic theory ·
vortex-lattice wings with viscous strip corrections · blade-element momentum propellers ·
empirical blunt-body drag.</p>
<p>Results are engineering estimates for learning, not certified design data.
See <i>docs/THEORY.md</i> for the equations and their limits.</p>"""

HELP = """<h3>Quick start</h3>
<ul>
<li>Choose a tab: <b>2D Airfoil</b>, <b>3D Wing</b>, <b>Propeller</b> or <b>Blunt Bodies</b>.</li>
<li>Change any setting on the left; results update automatically.</li>
<li><b>Drag the red probe</b> in the flow picture. It follows the streamlines, so you can move it from the
free stream up to the body and watch speed, pressure, Mach number, temperature and density change.
The <i>Probe</i> tab underneath plots the whole streamline.</li>
<li>Untick <i>Probe follows streamlines</i> to place the probe anywhere; a streamline through it is drawn
when you let go.</li>
<li>Use the magnifier / hand tools above the picture to zoom and pan (click the tool again to go back to
probe mode).</li>
<li>Click the airfoil name to open the library: search ~1,000 built-in airfoils, type any NACA code,
import a .dat file or download the UIUC database.</li>
</ul>"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Virtual Wind Tunnel")
        self.resize(1600, 950)
        from .tab_airfoil import AirfoilTab
        from .tab_blunt import BluntTab
        from .tab_propeller import PropellerTab
        from .tab_wing import WingTab
        self.tabs = QTabWidget()
        self.tabs.addTab(AirfoilTab(), "2D Airfoil")
        self.tabs.addTab(WingTab(), "3D Wing")
        self.tabs.addTab(PropellerTab(), "Propeller")
        self.tabs.addTab(BluntTab(), "Blunt Bodies")
        self.setCentralWidget(self.tabs)
        self._menus()
        self.statusBar().showMessage("Drag the red probe along a streamline to explore the flow.")

    def _menus(self):
        m = self.menuBar().addMenu("&File")
        save = QAction("Save flow picture…", self)
        save.setShortcut(QKeySequence.Save)
        save.triggered.connect(self.save_picture)
        m.addAction(save)
        quit_ = QAction("Quit", self)
        quit_.setShortcut(QKeySequence.Quit)
        quit_.triggered.connect(self.close)
        m.addAction(quit_)
        h = self.menuBar().addMenu("&Help")
        a = QAction("Quick start", self)
        a.triggered.connect(lambda: QMessageBox.information(self, "Quick start", HELP))
        h.addAction(a)
        a = QAction("About", self)
        a.triggered.connect(lambda: QMessageBox.about(self, "About", ABOUT))
        h.addAction(a)

    def save_picture(self):
        tab = self.tabs.currentWidget()
        fig = tab.current_figure() if hasattr(tab, "current_figure") else None
        if fig is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save picture", "wind_tunnel.png",
                                              "PNG image (*.png);;SVG image (*.svg);;PDF (*.pdf)")
        if path:
            fig.savefig(path, dpi=200)


def main(argv=None):
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("Virtual Wind Tunnel")
    win = MainWindow()
    win.show()
    return app.exec()
