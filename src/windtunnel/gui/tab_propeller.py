"""Propeller tab: blade-element momentum theory, slipstream view and blade-section view."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

from ..physics import airfoil2d as a2
from ..physics import propeller as pr
from ..physics.atmosphere import Freestream
from .flow_view import SectionPanel
from .library_dialog import AirfoilChooser
from .tab_airfoil import SECTIONS, ModelOptions, bl_markers
from .widgets import (
    Debouncer, FlowConditions, PlotCanvas, ResultsTable, ValueSlider, WarningsLabel, fmt, int_spin,
    scroll_panel, spin,
)


class PropellerTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        controls = QWidget()
        cl = QVBoxLayout(controls)

        g = QGroupBox("Blade section (airfoil)")
        f = QFormLayout(g)
        self.chooser = AirfoilChooser("NACA 4412")
        f.addRow(self.chooser)
        cl.addWidget(g)

        g = QGroupBox("Propeller geometry")
        f = QFormLayout(g)
        self.diameter = spin(0.05, 8.0, 1.8, 0.05, 3, " m")
        self.hub = spin(0.05, 0.5, 0.15, 0.01, 2)
        self.blades = int_spin(1, 8, 2)
        self.pitch = spin(0.01, 10.0, 1.5, 0.05, 3, " m")
        self.c_root = spin(0.02, 0.4, 0.12, 0.01, 3)
        self.c_tip = spin(0.01, 0.4, 0.06, 0.01, 3)
        self.n_el = int_spin(10, 80, 30)
        self.beta_info = QLabel()
        f.addRow("Diameter", self.diameter)
        f.addRow("Hub radius / R", self.hub)
        f.addRow("Number of blades", self.blades)
        f.addRow("Geometric pitch", self.pitch)
        f.addRow(self.beta_info)
        f.addRow("Root chord / R", self.c_root)
        f.addRow("Tip chord / R", self.c_tip)
        f.addRow("Blade elements", self.n_el)
        cl.addWidget(g)

        g = QGroupBox("Operating point")
        v = QFormLayout(g)
        self.rpm = ValueSlider(100, 30000, 2400, 50, 0, " rpm")
        v.addRow("Rotational speed", self.rpm)
        cl.addWidget(g)
        self.flow = FlowConditions("Flight condition", speed=40.0, allow_zero=True)
        cl.addWidget(self.flow)
        self.model = ModelOptions(show_flow_model=False)
        self.model.stall.setText("Stall model in section polar")
        cl.addWidget(self.model)

        g = QGroupBox("Display")
        f = QFormLayout(g)
        self.station = ValueSlider(0.2, 0.98, 0.75, 0.01, 2)
        f.addRow("Blade section r/R", self.station)
        cl.addWidget(g)
        cl.addStretch(1)

        self.tabs = QTabWidget()
        self.slip = SectionPanel(length_label="x / R", y_label="r / R")
        self.loading = PlotCanvas(2, 2)
        self.perf = PlotCanvas(1, 3)
        btn = QPushButton("Compute performance map (sweep advance ratio J)")
        self.perf.layout().insertWidget(0, btn)
        self.blade = SectionPanel()
        self.blade_cp = self.blade.add_plot_tab("Section pressure distribution")
        self.view3d = PlotCanvas(projection="3d")
        self.tabs.addTab(self.slip, "Slipstream")
        self.tabs.addTab(self.loading, "Blade loading")
        self.tabs.addTab(self.blade, "Blade section")
        self.tabs.addTab(self.perf, "Performance map")
        self.tabs.addTab(self.view3d, "3D view")

        self.results = ResultsTable()
        self.warn = WarningsLabel()
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.warn)
        rl.addWidget(self.results, 1)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(scroll_panel(controls))
        split.addWidget(self.tabs)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 900, 330])
        QHBoxLayout(self).addWidget(split)

        self.result = None
        self._model = None
        self._update = Debouncer(self.recompute, 200)
        for w in (self.diameter, self.hub, self.blades, self.pitch, self.c_root, self.c_tip, self.n_el, self.rpm):
            w.valueChanged.connect(self._update)
        self.flow.changed.connect(self._update)
        self.model.connect(self._update)
        self.chooser.changed.connect(self._update)
        self.station.valueChanged.connect(Debouncer(self._update_section, 80))
        btn.clicked.connect(self.compute_map)

    def showEvent(self, event):
        super().showEvent(event)
        if self.result is None:
            QTimer.singleShot(0, self.recompute)

    def current_figure(self):
        w = self.tabs.currentWidget()
        if isinstance(w, SectionPanel):
            return w.view.figure
        return w.figure

    def geometry(self):
        return pr.PropellerGeometry(self.diameter.value(), self.hub.value(), self.blades.value(),
                                    self.pitch.value(), self.c_root.value(), self.c_tip.value(), self.n_el.value())

    def prop_model(self):
        geo = self.geometry()
        sec = SECTIONS.get(self.chooser.airfoil, 140)
        if self._model is None or self._model.geo != geo or self._model.section is not sec:
            self._model = pr.PropellerModel(geo, sec)
        return self._model

    def recompute(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            m = self.prop_model()
            fs = self.flow.freestream()
            self.result = r = m.solve(self.rpm.value(), fs, self.model.settings())
            self.beta_info.setText(f"Blade angle at 75 % radius: {m.geo.beta75_deg:.1f}°  "
                                   f"(pitch/diameter = {m.geo.pitch / m.geo.diameter:.2f})")
            field = pr.ActuatorDiskField(fs, r.induced_velocity, r.thrust, m.geo.radius)
            self.slip.view.set_field(field, [], keep_view=True)
            self._draw_loading()
            self._draw_3d()
            self._update_section()
        finally:
            QApplication.restoreOverrideCursor()

    def _draw_loading(self):
        r = self.result
        x = r.r / r.geometry.radius
        axes = self.loading.reset(2, 2)
        axes[0].plot(x, r.dT, label="dT/dr [N/m]")
        axes[0].set_ylabel("thrust per unit radius [N/m]")
        axes[1].plot(x, r.dQ, color="#d1491f")
        axes[1].set_ylabel("torque per unit radius [N·m/m]")
        axes[2].plot(x, r.alpha, label="angle of attack α")
        axes[2].plot(x, r.phi, label="inflow angle φ")
        axes[2].plot(x, np.degrees(r.geometry.blade_angle(r.r)), "--", label="blade angle β")
        axes[2].set_ylabel("angle [°]")
        axes[2].legend(fontsize=7)
        axes[3].plot(x, r.mach, color="#7a3fd1", label="section Mach")
        axes[3].axhline(0.85, color="r", ls=":", lw=1, label="0.85 (losses grow)")
        axes[3].set_ylabel("relative Mach number")
        axes[3].legend(fontsize=7)
        for ax in axes:
            ax.set_xlabel("r / R")
            ax.grid(alpha=0.3)
            ax.axvline(self.station.value(), color="r", lw=0.6)
        self.loading.draw()

    def _draw_3d(self):
        r = self.result
        g = r.geometry
        ax = self.view3d.reset(projection="3d")[0]
        sec = SECTIONS.get(self.chooser.airfoil, 140).source
        xs, ys = sec.x - 0.25, sec.y
        rr = np.linspace(g.hub_ratio * g.radius, g.radius, 25)
        for k in range(g.blades):
            psi = 2 * np.pi * k / g.blades
            X, Y, Z = [], [], []
            for ri in rr:
                c = g.chord(ri)
                beta = g.blade_angle(ri)
                # section in the plane normal to the radius, rotated by the blade angle
                axial = -(xs * np.sin(beta) + ys * np.cos(beta)) * c
                tang = (xs * np.cos(beta) - ys * np.sin(beta)) * c
                X.append(axial)
                Y.append(ri * np.cos(psi) - tang * np.sin(psi))
                Z.append(ri * np.sin(psi) + tang * np.cos(psi))
            ax.plot_surface(np.array(X), np.array(Y), np.array(Z), color="#8a93a6", linewidth=0, alpha=0.95)
        t = np.linspace(0, 2 * np.pi, 60)
        hub = g.hub_ratio * g.radius
        ax.plot(np.zeros_like(t), hub * np.cos(t), hub * np.sin(t), color="k")
        ax.plot(np.zeros_like(t), g.radius * np.cos(t), g.radius * np.sin(t), color="0.6", ls=":")
        R = g.radius
        ax.set_xlim(-R, R)
        ax.set_ylim(-R, R)
        ax.set_zlim(-R, R)
        ax.set_box_aspect((1, 1, 1))
        ax.set_xlabel("axial x [m] (flight direction ←)")
        ax.set_title(f"{g.blades}-blade propeller, D = {g.diameter:.2f} m", fontsize=9)
        self.view3d.draw()

    def _update_section(self):
        r = self.result
        if r is None:
            return
        i = r.element_at(self.station.value())
        g = r.geometry
        chord = float(g.chord(r.r[i]))
        fs_local = Freestream(r.freestream.atmosphere, float(r.W[i]))
        sec = SECTIONS.get(self.chooser.airfoil, 140)
        settings = self.model.settings(alpha_deg=float(r.alpha[i]), chord=chord, span=1.0)
        settings.flow_model = "Auto (from Mach number)"
        res = a2.analyze(sec, fs_local, settings)
        markers = bl_markers(res) if res.model != "supersonic" else []
        self.blade.view.set_field(res.flow, markers, keep_view=True)
        ax = self.blade_cp.reset()[0]
        for mask, name, color in ((res.upper, "suction side", "#1f6fd1"), (~res.upper, "pressure side", "#d1491f")):
            o = np.argsort(res.x[mask])
            ax.plot(res.x[mask][o], res.cp[mask][o], color=color, label=name)
        ax.invert_yaxis()
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        ax.set_xlabel("x/c")
        ax.set_ylabel("Cp")
        ax.set_title(f"r/R = {r.r[i] / g.radius:.2f}: W = {r.W[i]:.1f} m/s, M = {r.mach[i]:.3f}, "
                     f"α = {r.alpha[i]:.2f}°", fontsize=9)
        self.blade_cp.draw()
        self._fill_results(i, res)

    def _fill_results(self, i, sres):
        r = self.result
        g = r.geometry
        rows = [("Operating point",),
                ("Flight speed", fmt(r.freestream.speed, "m/s")),
                ("Rotational speed", f"{r.rpm:.0f} rpm"),
                ("Advance ratio J = V/(nD)", fmt(r.J)),
                ("Helical tip Mach", fmt(r.tip_mach)),
                ("Performance",),
                ("Thrust T", fmt(r.thrust, "N")),
                ("Torque Q", fmt(r.torque, "N·m")),
                ("Shaft power P", fmt(r.power / 1000, "kW")),
                ("Propulsive efficiency η", fmt(r.efficiency)),
                ("Thrust coefficient CT", fmt(r.CT)),
                ("Power coefficient CP", fmt(r.CP)),
                ("Torque coefficient CQ", fmt(r.CQ)),
                ("Figure of merit (hover)", fmt(r.figure_of_merit)),
                ("Momentum theory",),
                ("Induced velocity at disk", fmt(r.induced_velocity, "m/s")),
                ("Far-wake velocity", fmt(r.freestream.speed + 2 * r.induced_velocity, "m/s")),
                ("Disk loading T/A", fmt(r.thrust / (np.pi * g.radius ** 2), "Pa")),
                (f"Blade element r/R = {r.r[i] / g.radius:.2f}",),
                ("Relative speed W", fmt(r.W[i], "m/s")),
                ("Section Mach", fmt(r.mach[i])),
                ("Angle of attack", fmt(r.alpha[i], "°")),
                ("Inflow angle φ", fmt(r.phi[i], "°")),
                ("Blade angle β", fmt(np.degrees(g.blade_angle(r.r[i])), "°")),
                ("Section cl / cd (BEMT)", f"{r.cl[i]:.3f} / {r.cd[i]:.4f}"),
                ("Section Re", fmt(sres.reynolds, digits=3)),
                ("Prandtl loss factor F", fmt(r.F[i])),
                ("Axial induction a", fmt(r.a[i])),
                ("Swirl induction a'", fmt(r.a_tan[i]))]
        self.results.set_rows(rows)
        self.warn.set_warnings(r.warnings)

    def compute_map(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            m = self.prop_model()
            fs = self.flow.freestream()
            pd = m.geo.pitch / m.geo.diameter
            j = np.linspace(0.0, pd * 1.25, 18)
            data = m.sweep_advance_ratio(self.rpm.value(), fs, self.model.settings(), j)
        finally:
            QApplication.restoreOverrideCursor()
        axes = self.perf.reset(1, 3)
        axes[0].plot(data[:, 0], data[:, 1], "o-", ms=3)
        axes[0].set_ylabel("CT")
        axes[1].plot(data[:, 0], data[:, 2], "o-", ms=3, color="#d1491f")
        axes[1].set_ylabel("CP")
        axes[2].plot(data[:, 0], data[:, 3], "o-", ms=3, color="#2a9d4a")
        axes[2].set_ylabel("efficiency η")
        axes[2].set_ylim(0, 1)
        for ax in axes:
            ax.set_xlabel("advance ratio J")
            ax.grid(alpha=0.3)
            ax.axvline(self.result.J, color="r", lw=0.6)
            ax.axhline(0, color="k", lw=0.5)
        self.perf.draw()
