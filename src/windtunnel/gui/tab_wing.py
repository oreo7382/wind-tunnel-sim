"""3-D finite-wing tab: vortex-lattice wing with tip vortices, span loading and a section view."""
from __future__ import annotations

import numpy as np
from matplotlib import cm
from matplotlib.colors import Normalize
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

from ..physics import airfoil2d as a2
from ..physics import wing3d as w3
from ..physics.atmosphere import Freestream
from .flow_view import SectionPanel
from .library_dialog import AirfoilChooser
from .tab_airfoil import SECTIONS, ModelOptions, bl_markers
from .widgets import (
    Debouncer, FlowConditions, PlotCanvas, ResultsTable, ValueSlider, WarningsLabel, check, fmt, int_spin,
    scroll_panel, spin,
)


class WingTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        controls = QWidget()
        cl = QVBoxLayout(controls)

        g = QGroupBox("Wing section (airfoil)")
        f = QFormLayout(g)
        self.chooser = AirfoilChooser("NACA 2412")
        f.addRow(self.chooser)
        cl.addWidget(g)

        g = QGroupBox("Planform")
        f = QFormLayout(g)
        self.span = spin(0.2, 80, 10.0, 0.5, 2, " m")
        self.root = spin(0.05, 20, 1.6, 0.1, 2, " m")
        self.taper = spin(0.05, 1.0, 0.5, 0.05, 2)
        self.sweep = spin(-45, 65, 0.0, 1, 1, " °")
        self.dihedral = spin(-15, 20, 3.0, 1, 1, " °")
        self.twist = spin(-10, 10, -2.0, 0.5, 1, " °")
        self.n_span = int_spin(10, 80, 36)
        self.n_chord = int_spin(1, 12, 5)
        self.geom_info = QLabel()
        self.geom_info.setWordWrap(True)
        f.addRow("Span b", self.span)
        f.addRow("Root chord", self.root)
        f.addRow("Taper ratio (tip/root)", self.taper)
        f.addRow("Sweep (quarter chord)", self.sweep)
        f.addRow("Dihedral", self.dihedral)
        f.addRow("Tip twist (− = washout)", self.twist)
        f.addRow("Spanwise panels", self.n_span)
        f.addRow("Chordwise panels", self.n_chord)
        f.addRow(self.geom_info)
        cl.addWidget(g)

        g = QGroupBox("Angle of attack")
        v = QVBoxLayout(g)
        self.alpha = ValueSlider(-10, 25, 5.0, 0.5, 1, " °")
        v.addWidget(self.alpha)
        cl.addWidget(g)

        self.flow = FlowConditions(speed=60.0)
        cl.addWidget(self.flow)
        self.model = ModelOptions(show_flow_model=False)
        self.model.stall.setText("Viscous strip correction (wing stall)")
        cl.addWidget(self.model)

        g = QGroupBox("Display")
        f = QFormLayout(g)
        self.station = ValueSlider(0.0, 0.98, 0.5, 0.02, 2)
        self.show_vortices = check("Show tip-vortex streamlines")
        f.addRow("Section station 2y/b", self.station)
        f.addRow(self.show_vortices)
        cl.addWidget(g)
        cl.addStretch(1)

        self.tabs = QTabWidget()
        self.view3d = PlotCanvas(projection="3d", figsize=(7, 5))
        self.loading = PlotCanvas(2, 2)
        self.section_panel = SectionPanel()
        self.section_cp = self.section_panel.add_plot_tab("Section pressure distribution")
        self.lift_curve = PlotCanvas(1, 3)
        btn = QPushButton("Compute lift curve and drag polar (sweep angle of attack)")
        self.lift_curve.layout().insertWidget(0, btn)
        self.tabs.addTab(self.view3d, "3D view")
        self.tabs.addTab(self.loading, "Span loading")
        self.tabs.addTab(self.section_panel, "Section at station")
        self.tabs.addTab(self.lift_curve, "Lift curve")

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

        self.model_cache = None
        self.result = None
        self._update = Debouncer(self.recompute, 200)
        for w in (self.span, self.root, self.taper, self.sweep, self.dihedral, self.twist, self.n_span,
                  self.n_chord, self.alpha):
            w.valueChanged.connect(self._update)
        self.flow.changed.connect(self._update)
        self.model.connect(self._update)
        self.chooser.changed.connect(self._update)
        self.station.valueChanged.connect(Debouncer(self._update_section, 80))
        self.show_vortices.toggled.connect(lambda *_: self._draw_3d())
        btn.clicked.connect(self.compute_lift_curve)

    def showEvent(self, event):
        super().showEvent(event)
        if self.result is None:
            QTimer.singleShot(0, self.recompute)

    def current_figure(self):
        w = self.tabs.currentWidget()
        if w is self.section_panel:
            return self.section_panel.view.figure
        return w.figure

    def planform(self):
        return w3.Planform(self.span.value(), self.root.value(), self.taper.value(), self.sweep.value(),
                           self.dihedral.value(), self.twist.value(), self.n_span.value(), self.n_chord.value())

    def wing_model(self):
        pf = self.planform()
        sec = SECTIONS.get(self.chooser.airfoil, 140)
        if self.model_cache is None or self.model_cache.pf != pf or self.model_cache.section is not sec:
            self.model_cache = w3.WingModel(pf, sec)
        return self.model_cache

    def recompute(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            m = self.wing_model()
            fs = self.flow.freestream()
            settings = self.model.settings()
            self.result = m.solve(self.alpha.value(), fs, settings, viscous_iteration=self.model.stall.isChecked())
            pf = m.pf
            self.geom_info.setText(f"Area S = {pf.area:.3f} m², aspect ratio AR = {pf.aspect_ratio:.2f}, "
                                   f"MAC = {pf.mac:.3f} m")
            self._draw_3d()
            self._draw_loading()
            self._update_section()
        finally:
            QApplication.restoreOverrideCursor()

    # ---------------------------------------------------------------- drawing
    def _draw_3d(self):
        r = self.result
        if r is None:
            return
        ax = self.view3d.reset(projection="3d")[0]
        polys = r.panels.reshape(-1, 4, 3)
        vals = r.dcp.ravel()
        norm = Normalize(vmin=min(0.0, vals.min()), vmax=max(vals.max(), 1e-3))
        colors = cm.viridis(norm(vals))
        ax.add_collection3d(Poly3DCollection(polys, facecolors=colors, edgecolors="k", linewidths=0.15))
        sm = cm.ScalarMappable(norm=norm, cmap="viridis")
        self.view3d.figure.colorbar(sm, ax=ax, shrink=0.6, pad=0.08, label="panel loading ΔCp")
        pf = r.planform
        if self.show_vortices.isChecked():
            b2 = pf.span / 2
            ct = pf.tip_chord
            seeds = []
            for side in (-1, 1):
                # a ring of seeds around each wing tip, just behind the trailing edge
                y_tip = side * b2 * 0.985
                x_te = pf.x_quarter_at(b2) + 0.75 * ct
                for rad, n in ((0.07 * ct, 5), (0.18 * ct, 7)):
                    for ang in np.linspace(0, 2 * np.pi, n, endpoint=False):
                        seeds.append((x_te, y_tip + rad * np.cos(ang), pf.z_at(b2) + rad * np.sin(ang)))
            for eta in (-0.6, -0.3, 0.3, 0.6):
                y = eta * b2
                seeds.append((pf.x_quarter_at(y) + 0.8 * pf.chord_at(y), y, pf.z_at(y) + 0.02 * pf.root_chord))
            step = 0.035 * ct
            paths = w3.trace_3d(r, seeds, step=step, n_steps=int(1.1 * pf.span / step))
            for i, p in enumerate(paths):
                color = "#d03030" if i < len(paths) - 4 else "#3070d0"
                ax.plot(p[:, 0], p[:, 1], p[:, 2], color=color, lw=0.8)
        span = pf.span
        x0 = -0.15 * span
        ax.set_xlim(x0, x0 + 1.25 * span)
        ax.set_ylim(-0.62 * span, 0.62 * span)
        ax.set_zlim(-0.3 * span, 0.3 * span)
        ax.set_box_aspect((1.25, 1.24, 0.6))
        ax.set_xlabel("x [m] (downstream)")
        ax.set_ylabel("y [m]")
        ax.set_zlabel("z [m]")
        ax.view_init(elev=22, azim=-125)
        ax.set_title(f"CL = {r.CL:.3f}, CDi = {r.CDi:.4f}, e = {r.e:.3f}\nred: tip-vortex streamlines, "
                     "blue: downwash behind the wing", fontsize=9)
        self.view3d.draw()

    def _draw_loading(self):
        r = self.result
        axes = self.loading.reset(2, 2)
        eta = 2 * r.y / r.planform.span
        cbar = r.planform.area / r.planform.span
        axes[0].plot(eta, r.cl, "o-", ms=2.5, label="local cl")
        if r.stalled.any():
            axes[0].plot(eta[r.stalled], r.cl[r.stalled], "x", color="r", ms=7, label="stalled")
        axes[0].axhline(r.CL, color="0.5", ls=":", label="wing CL")
        axes[0].set_ylabel("section lift coefficient cl")
        load = r.cl * r.chord / cbar
        ell = 4 / np.pi * r.CL * np.sqrt(np.clip(1 - eta ** 2, 0, None))
        axes[1].plot(eta, load, label="this wing")
        axes[1].plot(eta, ell, "--", color="0.4", label="elliptic (same CL)")
        axes[1].set_ylabel("span loading c·cl / c̄")
        axes[2].plot(eta, r.alpha_i, color="#d1491f", label="induced angle αi")
        axes[2].plot(eta, r.alpha_eff, color="#2a9d4a", label="effective angle α_eff")
        axes[2].axhline(r.alpha_deg, color="0.5", ls=":", label="geometric α (root)")
        axes[2].set_ylabel("angle [°]")
        axes[3].plot(eta, r.chord, color="#7a3fd1", label="chord")
        axes[3].set_ylabel("chord [m]")
        ax2 = axes[3].twinx()
        ax2.plot(eta, r.cd, color="#c08000", label="profile cd")
        ax2.set_ylabel("section cd")
        for ax in axes:
            ax.set_xlabel("2y/b")
            ax.grid(alpha=0.3)
            ax.legend(fontsize=7)
            ax.axvline(self.station.value(), color="r", lw=0.6)
            ax.axvline(-self.station.value(), color="r", lw=0.6)
        self.loading.draw()

    def _update_section(self):
        r = self.result
        if r is None:
            return
        pf = r.planform
        eta = self.station.value()
        i = r.strip_at(eta)
        chord = float(r.chord[i])
        alpha_eff = float(r.alpha_eff[i])
        sweep = np.radians(pf.sweep_deg)
        fs = r.freestream
        fs_n = Freestream(fs.atmosphere, fs.speed * np.cos(sweep))
        sec = SECTIONS.get(self.chooser.airfoil, 140)
        settings = self.model.settings(alpha_deg=alpha_eff, chord=chord, span=1.0)
        settings.flow_model = "Auto (from Mach number)"
        res = a2.analyze(sec, fs_n, settings)
        self.section_result = res
        markers = bl_markers(res) if res.model != "supersonic" else []
        self.section_panel.view.set_field(res.flow, markers, keep_view=True)
        ax = self.section_cp.reset()[0]
        for mask, name, color in ((res.upper, "upper", "#1f6fd1"), (~res.upper, "lower", "#d1491f")):
            o = np.argsort(res.x[mask])
            ax.plot(res.x[mask][o], res.cp[mask][o], color=color, label=name)
        ax.invert_yaxis()
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        ax.set_xlabel("x/c")
        ax.set_ylabel("Cp")
        ax.set_title(f"Section at 2y/b = {eta:.2f}: α_eff = {alpha_eff:.2f}° "
                     f"(geometric {r.alpha_deg + pf.twist_at(r.y[i]):.2f}°, induced {r.alpha_i[i]:.2f}°)", fontsize=9)
        self.section_cp.draw()
        self._fill_results(i, res)

    def _fill_results(self, i, sres):
        r = self.result
        pf = r.planform
        sec = SECTIONS.get(self.chooser.airfoil, 140)
        a0 = sec.zero_lift_alpha()[1]
        rows = [("Wing geometry",),
                ("Area S", fmt(pf.area, "m²")),
                ("Aspect ratio", fmt(pf.aspect_ratio)),
                ("Mean aerodynamic chord", fmt(pf.mac, "m")),
                ("Flow",),
                ("Mach number", fmt(r.mach)),
                ("Airspeed", fmt(r.freestream.speed, "m/s")),
                ("Reynolds number (MAC)", fmt(r.freestream.reynolds(pf.mac), digits=3)),
                ("Coefficients",),
                ("CL", fmt(r.CL)),
                ("CD total", fmt(r.CD)),
                ("   induced CDi", fmt(r.CDi)),
                ("   profile CD0", fmt(r.CD0)),
                ("   wave CDw", fmt(r.CDw)),
                ("Span efficiency e", fmt(r.e)),
                ("CL/(π·AR·e) check CDi", fmt(r.CL ** 2 / (np.pi * pf.aspect_ratio * max(r.e, 1e-6)))),
                ("Cm (MAC quarter chord)", fmt(r.Cm)),
                ("L / D", fmt(r.lift_to_drag)),
                ("3-D lift slope", f"{r.CL_alpha:.3f} /rad ({r.CL_alpha * np.pi / 180:.4f} /°)"),
                ("2-D section lift slope", f"{a0:.3f} /rad"),
                ("Forces",),
                ("Lift", fmt(r.lift, "N")),
                ("Drag", fmt(r.drag, "N")),
                ("Induced drag", fmt(r.CDi * r.q * pf.area, "N")),
                ("Lift in kg (÷ g)", fmt(r.lift / 9.80665, "kg")),
                (f"Section at 2y/b = {2 * r.y[i] / pf.span:.2f}",),
                ("Local chord", fmt(r.chord[i], "m")),
                ("Local cl (3-D)", fmt(r.cl[i])),
                ("Induced angle αi", fmt(r.alpha_i[i], "°")),
                ("Effective angle α_eff", fmt(r.alpha_eff[i], "°")),
                ("Section cl (2-D at α_eff)", fmt(sres.cl)),
                ("Section cd", fmt(sres.cd)),
                ("Normal Mach (sweep)", fmt(sres.mach))]
        self.results.set_rows(rows)
        self.warn.set_warnings(r.warnings + [w for w in sres.warnings if "Inviscid" not in w])

    def compute_lift_curve(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            m = self.wing_model()
            fs = self.flow.freestream()
            settings = self.model.settings()
            alphas = np.arange(-6.0, 24.1, 2.0)
            data = []
            for a in alphas:
                r = m.solve(a, fs, settings, viscous_iteration=self.model.stall.isChecked())
                data.append((r.CL, r.CD, r.CDi, r.Cm))
            data = np.array(data)
            sec = SECTIONS.get(self.chooser.airfoil, 140)
            s2 = self.model.settings(reynolds_override=fs.reynolds(m.pf.mac), flow_model="Incompressible")
            p2 = a2.polar(sec, Freestream(fs.atmosphere, min(fs.speed, 60.0)), s2, alphas)
        finally:
            QApplication.restoreOverrideCursor()
        axes = self.lift_curve.reset(1, 3)
        axes[0].plot(alphas, data[:, 0], "o-", ms=3, label="3-D wing CL")
        axes[0].plot(alphas, p2["cl"], "--", color="0.5", label="2-D airfoil cl")
        axes[0].set_xlabel("α [°]")
        axes[0].set_ylabel("lift coefficient")
        axes[0].legend(fontsize=8)
        axes[1].plot(data[:, 1], data[:, 0], "o-", ms=3, label="total CD")
        axes[1].plot(data[:, 2], data[:, 0], "--", label="induced CDi")
        axes[1].set_xlabel("CD")
        axes[1].set_ylabel("CL")
        axes[1].legend(fontsize=8)
        axes[2].plot(alphas, data[:, 0] / data[:, 1], "o-", ms=3, color="#2a9d4a")
        axes[2].set_xlabel("α [°]")
        axes[2].set_ylabel("L/D")
        for ax in axes:
            ax.grid(alpha=0.3)
        self.lift_curve.draw()
