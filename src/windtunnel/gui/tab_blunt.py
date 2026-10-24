"""Blunt-body tab: cylinders, spheres, plates and other bluff shapes."""
from __future__ import annotations

import numpy as np
from matplotlib.patches import Polygon
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QFormLayout, QGroupBox, QHBoxLayout, QSplitter, QVBoxLayout, QWidget,
)

from ..physics import bluntbody as bb
from ..physics import compressibility as comp
from ..physics import shapes
from .flow_view import SectionPanel
from .widgets import (
    Debouncer, FlowConditions, ResultsTable, ValueSlider, WarningsLabel, check, combo, fmt, scroll_panel, spin,
)


class BluntTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bodies = shapes.blunt_bodies()
        controls = QWidget()
        cl = QVBoxLayout(controls)

        g = QGroupBox("Body")
        f = QFormLayout(g)
        self.shape = combo([b.name for b in self.bodies])
        self.diameter = spin(0.001, 50, 0.1, 0.01, 3, " m")
        self.span = spin(0.01, 100, 1.0, 0.1, 2, " m")
        self.spin_ratio = ValueSlider(-4.0, 4.0, 0.0, 0.1, 2)
        f.addRow("Shape", self.shape)
        f.addRow("Frontal height d", self.diameter)
        f.addRow("Span (2-D bodies)", self.span)
        f.addRow("Spin ratio ωa/V (cylinder)", self.spin_ratio)
        cl.addWidget(g)

        self.flow = FlowConditions(speed=20.0)
        cl.addWidget(self.flow)
        g = QGroupBox("Model")
        f = QFormLayout(g)
        self.compressible = check("Compressibility correction (M > 0.3)")
        self.correction = combo(comp.CORRECTIONS)
        self.show_wake = check("Sketch separated wake")
        f.addRow(self.compressible)
        f.addRow("Rule", self.correction)
        f.addRow(self.show_wake)
        cl.addWidget(g)
        cl.addStretch(1)

        self.panel = SectionPanel(length_label="x / d", y_label="y / d")
        self.cp_plot = self.panel.add_plot_tab("Surface pressure")
        self.re_plot = self.panel.add_plot_tab("Drag vs Reynolds number")

        self.results = ResultsTable()
        self.warn = WarningsLabel()
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.warn)
        rl.addWidget(self.results, 1)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(scroll_panel(controls))
        split.addWidget(self.panel)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 900, 330])
        QHBoxLayout(self).addWidget(split)

        self._models = {}
        self._new_shape = True
        self.result = None
        self._update = Debouncer(self.recompute)
        for w in (self.diameter, self.span, self.spin_ratio):
            w.valueChanged.connect(self._update)
        self.flow.changed.connect(self._update)
        self.compressible.toggled.connect(self._update)
        self.correction.currentIndexChanged.connect(self._update)
        self.show_wake.toggled.connect(self._update)
        self.shape.currentIndexChanged.connect(self._shape_changed)

    def showEvent(self, event):
        super().showEvent(event)
        if self.result is None:
            QTimer.singleShot(0, self.recompute)

    def current_figure(self):
        return self.panel.view.figure

    def _shape_changed(self, *_):
        self._new_shape = True
        self._update()

    def model(self):
        body = self.bodies[self.shape.currentIndex()]
        if body.name not in self._models:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self._models[body.name] = bb.BluntModel(body)
            finally:
                QApplication.restoreOverrideCursor()
        return self._models[body.name]

    def recompute(self):
        m = self.model()
        self.spin_ratio.setEnabled(bool(m.body.meta.get("can_spin")))
        fs = self.flow.freestream()
        r = m.solve(fs, self.diameter.value(), self.span.value(), self.spin_ratio.value(),
                                self.compressible.isChecked(), self.correction.currentText())
        self.result = r
        self.panel.view.set_field(r.flow, [], keep_view=not self._new_shape,
                                  extra_draw=self._draw_wake)
        self._new_shape = False
        self._fill()
        self._plot_cp()
        self._plot_re()

    def _draw_wake(self, ax):
        r = self.result
        if r is None or not self.show_wake.isChecked() or abs(r.spin_ratio) > 1e-9:
            return
        body = r.body
        x_back = body.x.max()
        if r.separation_angle is not None:
            th = np.radians(r.separation_angle if r.reynolds < 3e5 else 120.0)
            xs, ys = -0.5 * np.cos(th), 0.5 * np.sin(th)
        else:
            k = int(np.argmax(body.y))
            xs, ys = body.x[k], body.y[k]
        width = ys * (1.0 if r.reynolds < 3e5 or not body.meta.get("rounded", body.meta.get("shape") in ("cylinder", "sphere")) else 0.7)
        L = 2.5
        wake = Polygon([(xs, ys), (x_back + L, width * 1.25), (x_back + L, -width * 1.25), (xs, -ys)],
                       closed=True, fc="#e05050", ec="none", alpha=0.15, zorder=3)
        ax.add_patch(wake)
        ax.text(x_back + 0.3, 0, "separated wake\n(real flow, sketch)", fontsize=8, color="#a02020",
                va="center", zorder=4)

    def _fill(self):
        r = self.result
        fs = r.freestream
        rows = [("Flow",),
                ("Airspeed", fmt(fs.speed, "m/s")),
                ("Mach number", fmt(fs.mach)),
                ("Reynolds number Re_d", fmt(r.reynolds, digits=3)),
                ("Regime", r.regime),
                ("Forces (measured-data model)",),
                ("Drag coefficient CD", fmt(r.cd)),
                ("Drag force", fmt(r.drag if not r.body.meta.get("axisymmetric") else
                                   r.cd * r.q * np.pi * r.diameter ** 2 / 4, "N")),
                ("Lift coefficient CL", fmt(r.cl)),
                ("Lift force", fmt(r.lift, "N")),
                ("Potential-flow CL (Kutta-Joukowski)", fmt(r.cl_potential)),
                ("Vortex shedding",),
                ("Strouhal number", fmt(r.strouhal)),
                ("Shedding frequency", fmt(r.shedding_frequency, "Hz")),
                ("Potential flow",),
                ("Minimum Cp", fmt(r.cp_min)),
                ("Critical Mach number", fmt(r.mcrit) if r.body.meta.get("rounded") or
                 r.body.meta.get("shape") in ("cylinder", "sphere") else "n/a (sharp edges)"),
                ("Laminar separation (Thwaites)", f"{r.separation_angle:.1f}° from front"
                 if r.separation_angle is not None else "—")]
        if r.body.meta.get("axisymmetric"):
            rows.insert(7, ("Reference area", "frontal disk π d²/4"))
        self.results.set_rows(rows)
        notes = list(r.warnings)
        notes.append("Streamlines show ideal (potential) flow, which never separates. Real blunt bodies shed a "
                     "separated wake; drag comes from measured data.")
        self.warn.set_warnings(notes)

    def _plot_cp(self):
        r = self.result
        ax = self.cp_plot.reset()[0]
        o = np.argsort(r.angle)
        ax.plot(r.angle[o], r.cp[o], color="#1f6fd1", label="potential flow")
        if r.body.meta.get("shape") == "cylinder" and abs(r.spin_ratio) < 1e-9:
            th = np.radians(np.linspace(0, 180, 181))
            ax.plot(np.degrees(th), 1 - 4 * np.sin(th) ** 2, ":", color="k", label="exact 1 − 4 sin²θ")
            # typical measured subcritical distribution (base pressure plateau)
            meas = np.where(np.degrees(th) < 70, 1 - 4 * np.sin(th) ** 2 * 0.62, -1.2)
            ax.plot(np.degrees(th), np.maximum(meas, -1.2), "--", color="#d1491f", label="typical measured (Re≈1e5)")
        if r.separation_angle is not None:
            ax.axvline(r.separation_angle, color="r", lw=0.8, label="laminar separation (Thwaites)")
            ax.axvline(-r.separation_angle, color="r", lw=0.8)
        ax.set_xlabel("angle from front stagnation point [°]")
        ax.set_ylabel("Cp")
        ax.invert_yaxis()
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        self.cp_plot.draw()

    def _plot_re(self):
        r = self.result
        ax = self.re_plot.reset()[0]
        re = np.logspace(0, 7, 300)
        ax.loglog(re, [bb.cylinder_cd(x) for x in re], label="circular cylinder")
        ax.loglog(re[re < 1.5e6], [bb.sphere_cd(x) for x in re[re < 1.5e6]], label="sphere (Morrison)")
        ax.loglog([r.reynolds], [r.cd / bb.mach_drag_factor(r.freestream.mach)], "o", ms=9, mfc="#ff2d2d",
                  mec="k", label=f"current: {r.body.name}")
        ax.set_xlabel("Reynolds number Re_d")
        ax.set_ylabel("CD (incompressible)")
        ax.grid(alpha=0.3, which="both")
        ax.legend(fontsize=8)
        ax.set_title("Note the 'drag crisis' near Re = 3×10⁵", fontsize=9)
        self.re_plot.draw()
