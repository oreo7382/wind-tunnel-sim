"""2-D airfoil wind tunnel tab."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QFormLayout, QGroupBox, QHBoxLayout, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from ..physics import airfoil2d as a2
from ..physics import compressibility as comp
from .flow_view import SectionPanel
from .library_dialog import AirfoilChooser
from .widgets import (
    Debouncer, FlowConditions, ResultsTable, ValueSlider, WarningsLabel, check, combo, fmt, int_spin,
    scroll_panel, spin,
)


class ModelOptions(QGroupBox):
    """Physics model options shared by the airfoil, wing and propeller tabs."""

    def __init__(self, title="Physics model", show_flow_model=True, parent=None):
        super().__init__(title, parent)
        form = QFormLayout(self)
        self.flow_model = combo(a2.FLOW_MODELS)
        self.correction = combo(comp.CORRECTIONS)
        self.viscous = check("Viscous effects (boundary layer, drag)")
        self.stall = check("Stall model (trailing-edge separation)")
        self.transition = combo(a2.TRANSITION_MODES)
        self.trip_u = spin(0.0, 1.0, 0.10, 0.05, 2)
        self.trip_l = spin(0.0, 1.0, 0.10, 0.05, 2)
        if show_flow_model:
            form.addRow("Flow model", self.flow_model)
        form.addRow("Compressibility rule", self.correction)
        form.addRow(self.viscous)
        form.addRow(self.stall)
        form.addRow("Transition", self.transition)
        form.addRow("Trip x/c upper", self.trip_u)
        form.addRow("Trip x/c lower", self.trip_l)
        self.transition.currentTextChanged.connect(self._trip_enable)
        self._trip_enable()

    def _trip_enable(self, *_):
        forced = self.transition.currentText().startswith("Forced")
        self.trip_u.setEnabled(forced)
        self.trip_l.setEnabled(forced)

    def connect(self, slot):
        self.flow_model.currentIndexChanged.connect(slot)
        self.correction.currentIndexChanged.connect(slot)
        self.viscous.toggled.connect(slot)
        self.stall.toggled.connect(slot)
        self.transition.currentIndexChanged.connect(slot)
        self.trip_u.valueChanged.connect(slot)
        self.trip_l.valueChanged.connect(slot)

    def settings(self, **kw) -> a2.Settings:
        values = dict(flow_model=self.flow_model.currentText(), correction=self.correction.currentText(),
                      viscous=self.viscous.isChecked(), stall_model=self.stall.isChecked(),
                      transition=self.transition.currentText(), trip_upper=self.trip_u.value(),
                      trip_lower=self.trip_l.value())
        values.update(kw)
        return a2.Settings(**values)


class SectionCache:
    def __init__(self):
        self._d = {}

    def get(self, airfoil, n_panels=160):
        key = (airfoil.name, len(airfoil.x), float(airfoil.x.sum()), n_panels)
        if key not in self._d:
            if len(self._d) > 12:
                self._d.clear()
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self._d[key] = a2.Section(airfoil, n_panels)
            finally:
                QApplication.restoreOverrideCursor()
        return self._d[key]


SECTIONS = SectionCache()


def bl_markers(res: a2.Result):
    """Stagnation, transition and separation markers for the flow view."""
    out = [(res.stagnation[0], res.stagnation[1], dict(marker="o", ms=7, mfc="yellow", mec="k"), "stagnation point")]
    x, y, up = res.x, res.y, res.upper

    def surf_y(xq, upper):
        m = up if upper else ~up
        o = np.argsort(x[m])
        return float(np.interp(xq, x[m][o], y[m][o]))

    tr_done = sep_done = False
    for b, upper in ((res.bl_upper, True), (res.bl_lower, False)):
        if b is None:
            continue
        if b.x_transition is not None:
            out.append((b.x_transition, surf_y(b.x_transition, upper),
                        dict(marker="^" if upper else "v", ms=9, mfc="#29a329", mec="k"),
                        None if tr_done else "transition"))
            tr_done = True
        if b.x_separation is not None:
            out.append((b.x_separation, surf_y(b.x_separation, upper),
                        dict(marker="X", ms=10, mfc="#e03030", mec="k"), None if sep_done else "separation"))
            sep_done = True
    return out


class AirfoilTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        controls = QWidget()
        cl = QVBoxLayout(controls)

        g = QGroupBox("Airfoil")
        f = QFormLayout(g)
        self.chooser = AirfoilChooser("NACA 2412")
        self.panels = int_spin(60, 400, 160)
        f.addRow(self.chooser)
        f.addRow("Panels", self.panels)
        cl.addWidget(g)

        g = QGroupBox("Angle of attack")
        v = QVBoxLayout(g)
        self.alpha = ValueSlider(-20, 25, 4.0, 0.5, 1, " °")
        v.addWidget(self.alpha)
        cl.addWidget(g)

        self.flow = FlowConditions(speed=50.0)
        cl.addWidget(self.flow)

        g = QGroupBox("Model size")
        f = QFormLayout(g)
        self.chord = spin(0.01, 50, 1.0, 0.1, 3, " m")
        self.span = spin(0.01, 100, 1.0, 0.5, 2, " m")
        f.addRow("Chord c", self.chord)
        f.addRow("Span (for total forces)", self.span)
        cl.addWidget(g)

        self.model = ModelOptions()
        cl.addWidget(self.model)

        g = QGroupBox("Surface point")
        f = QFormLayout(g)
        self.surf_x = ValueSlider(0.0, 1.0, 0.25, 0.01, 2)
        self.surf_side = combo(["Upper surface", "Lower surface"])
        f.addRow("x/c", self.surf_x)
        f.addRow("Side", self.surf_side)
        cl.addWidget(g)
        cl.addStretch(1)

        self.panel = SectionPanel()
        self.cp_plot = self.panel.add_plot_tab("Pressure distribution")
        self.polar_plot = self.panel.add_plot_tab("Polar", 2, 2)
        self.bl_plot = self.panel.add_plot_tab("Boundary layer", 1, 3)
        polar_btn = QPushButton("Compute polar (sweep angle of attack)")
        self.panel.tabs.widget(2).layout().insertWidget(0, polar_btn)

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
        lay = QHBoxLayout(self)
        lay.addWidget(split)

        self.result = None
        self._geometry_changed = True
        self._update = Debouncer(self.recompute)
        for w in (self.alpha, self.chord, self.span):
            w.valueChanged.connect(self._update)
        self.flow.changed.connect(self._update)
        self.model.connect(self._update)
        self.chooser.changed.connect(self._new_geometry)
        self.panels.valueChanged.connect(self._new_geometry)
        self.surf_x.valueChanged.connect(lambda *_: self._fill_results())
        self.surf_side.currentIndexChanged.connect(lambda *_: self._fill_results())
        polar_btn.clicked.connect(self.compute_polar)

    def showEvent(self, event):
        super().showEvent(event)
        if self.result is None:
            QTimer.singleShot(0, self.recompute)

    def current_figure(self):
        return self.panel.view.figure

    def _new_geometry(self, *_):
        self._geometry_changed = True
        self._update()

    def section(self):
        return SECTIONS.get(self.chooser.airfoil, self.panels.value())

    def recompute(self):
        sec = self.section()
        settings = self.model.settings(alpha_deg=self.alpha.value(), chord=self.chord.value(), span=self.span.value())
        fs = self.flow.freestream()
        self.result = res = a2.analyze(sec, fs, settings)
        markers = bl_markers(res) if res.model != "supersonic" else []
        self.panel.view.set_field(res.flow, markers, keep_view=not self._geometry_changed)
        self._geometry_changed = False
        self._fill_results()
        self._plot_cp()
        self._plot_bl()

    def _fill_results(self):
        r = self.result
        if r is None:
            return
        fs = r.freestream
        side_upper = self.surf_side.currentIndex() == 0
        ys, sp = r.surface_point(self.surf_x.value(), side_upper)
        rows = [("Flow",),
                ("Regime", r.regime),
                ("Model used", {"incompressible": "incompressible potential flow",
                                "subsonic": f"compressible ({r.settings.correction})",
                                "supersonic": "supersonic (Busemann 2nd order)"}[r.model]),
                ("Mach number", fmt(r.mach)),
                ("Airspeed", fmt(fs.speed, "m/s")),
                ("Reynolds number", fmt(r.reynolds, digits=3)),
                ("Dynamic pressure q", fmt(r.q, "Pa")),
                ("Coefficients",),
                ("Lift coefficient CL", fmt(r.cl)),
                ("Drag coefficient CD", fmt(r.cd)),
                ("   skin friction + form", fmt(r.cd_friction)),
                ("   separation", fmt(r.cd_separation)),
                ("   wave", fmt(r.cd_wave)),
                ("Moment coeff. Cm (c/4)", fmt(r.cm)),
                ("Lift / drag", fmt(r.lift_to_drag)),
                ("Inviscid CL", fmt(r.cl_inviscid)),
                ("Forces",),
                ("Lift per metre span", fmt(r.lift_per_span, "N/m")),
                ("Drag per metre span", fmt(r.drag_per_span, "N/m")),
                (f"Lift (span {r.settings.span:g} m)", fmt(r.lift, "N")),
                (f"Drag (span {r.settings.span:g} m)", fmt(r.drag, "N")),
                ("Pitching moment", fmt(r.moment, "N·m")),
                ("Compressibility",),
                ("Critical Mach number", fmt(r.mcrit) if r.model != "supersonic" else "—"),
                ("Critical Cp (at this M)", fmt(r.cp_crit) if r.mach > 0.2 else "—"),
                ("Minimum Cp", fmt(r.cp_min)),
                ("Boundary layer",)]
        for name, b in (("upper", r.bl_upper), ("lower", r.bl_lower)):
            if b is not None:
                rows.append((f"Transition x/c ({name})", fmt(b.x_transition) if b.x_transition is not None else "laminar"))
                rows.append((f"Separation x/c ({name})", fmt(b.x_separation) if b.x_separation is not None else "attached"))
        rows += [(f"Surface point (x/c = {self.surf_x.value():.2f}, {'upper' if side_upper else 'lower'})",),
                 ("Local speed", fmt(sp["speed"], "m/s")),
                 ("Local V/V∞", fmt(sp["speed_ratio"])),
                 ("Local Mach", fmt(sp["mach"])),
                 ("Local Cp", fmt(sp["cp"])),
                 ("Local pressure", fmt(sp["pressure"] / 1000, "kPa"))]
        self.results.set_rows(rows)
        self.warn.set_warnings(r.warnings)
        self._plot_cp(marker=(self.surf_x.value(), sp["cp"]))

    def _plot_cp(self, marker=None):
        r = self.result
        if r is None:
            return
        ax = self.cp_plot.reset()[0]
        up = r.upper
        for mask, name, color in ((up, "upper", "#1f6fd1"), (~up, "lower", "#d1491f")):
            o = np.argsort(r.x[mask])
            ax.plot(r.x[mask][o], r.cp[mask][o], color=color, lw=1.8, label=f"{name} surface")
            if r.model == "subsonic":
                ax.plot(r.x[mask][o], r.cp0[mask][o], color=color, lw=0.9, ls="--",
                        label=f"{name}, incompressible" if name == "upper" else None)
        if r.model != "incompressible" and r.mach > 0.2:
            ax.axhline(r.cp_crit, color="purple", ls=":", lw=1.2, label=f"Cp* (sonic) = {r.cp_crit:.2f}")
        if marker:
            ax.plot([marker[0]], [marker[1]], "o", ms=8, mfc="#ff2d2d", mec="white", label="surface point")
        ax.invert_yaxis()
        ax.set_xlabel("x/c")
        ax.set_ylabel("Cp")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        ax.set_title(f"{r.flow.__class__.__name__.replace('FlowField', '')} · α = {r.settings.alpha_deg:.1f}°, "
                     f"CL = {r.cl:.3f}", fontsize=9)
        self.cp_plot.draw()

    def _plot_bl(self):
        r = self.result
        axes = self.bl_plot.reset(1, 3)
        if r.bl_upper is None:
            axes[0].text(0.5, 0.5, "Viscous effects are off", ha="center", transform=axes[0].transAxes)
            self.bl_plot.draw()
            return
        for b, name, color in ((r.bl_upper, "upper", "#1f6fd1"), (r.bl_lower, "lower", "#d1491f")):
            axes[0].plot(b.x, b.theta * 1000, color=color, label=name)
            axes[1].plot(b.x, b.H, color=color)
            axes[2].plot(b.x, b.cf * b.ue ** 2 * 1000, color=color)
        axes[0].set_ylabel("θ / c  ×1000")
        axes[1].set_ylabel("shape factor H")
        axes[1].axhline(2.0, color="r", ls=":", lw=1)
        axes[2].set_ylabel("Cf ×1000 (free-stream q)")
        for ax in axes:
            ax.set_xlabel("x/c")
            ax.grid(alpha=0.3)
        axes[0].legend(fontsize=8)
        self.bl_plot.draw()

    def compute_polar(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            sec = self.section()
            fs = self.flow.freestream()
            settings = self.model.settings(chord=self.chord.value(), span=self.span.value())
            p = a2.polar(sec, fs, settings, np.arange(-12.0, 24.5, 1.0))
        finally:
            QApplication.restoreOverrideCursor()
        axes = self.polar_plot.reset(2, 2)
        a, clv, cd, cm = p["alpha"], p["cl"], p["cd"], p["cm"]
        axes[0].plot(a, clv, "o-", ms=3, label="viscous")
        axes[0].plot(a, p["cl_inviscid"], "--", color="0.5", label="inviscid")
        axes[0].set_xlabel("α [°]")
        axes[0].set_ylabel("CL")
        axes[0].legend(fontsize=8)
        axes[1].plot(cd, clv, "o-", ms=3)
        axes[1].set_xlabel("CD")
        axes[1].set_ylabel("CL")
        axes[2].plot(a, cm, "o-", ms=3, color="#7a3fd1")
        axes[2].set_xlabel("α [°]")
        axes[2].set_ylabel("Cm (c/4)")
        axes[3].plot(a, clv / np.where(cd > 0, cd, np.nan), "o-", ms=3, color="#2a9d4a")
        axes[3].set_xlabel("α [°]")
        axes[3].set_ylabel("L/D")
        for ax in axes:
            ax.grid(alpha=0.3)
            ax.axvline(self.alpha.value(), color="r", lw=0.6) if ax is not axes[1] else None
        i = int(np.argmax(clv))
        axes[0].set_title(f"CLmax ≈ {clv[i]:.2f} at {a[i]:.0f}°", fontsize=9)
        self.polar_plot.draw()
        self.panel.tabs.setCurrentIndex(2)
