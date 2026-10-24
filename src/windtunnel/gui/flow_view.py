"""Flow-field view with streamlines and a draggable probe, plus the probe readout panel.

The probe sits on a streamline. Drag it (or click anywhere) to move it; it snaps
to the nearest streamline so you can follow a fluid particle from the free
stream to the body and watch the local speed, pressure, Mach number,
temperature and density change. The "Along streamline" plot shows the whole
history of the selected streamline with the probe position marked.
"""
from __future__ import annotations

import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from ..physics.flowfield import trace_streamlines
from .widgets import PlotCanvas, ResultsTable, combo, fmt, int_spin

BACKGROUNDS = ("None", "Pressure coefficient Cp", "Speed V/V∞", "Local Mach number")
_BG_KEY = {"Pressure coefficient Cp": "cp", "Speed V/V∞": "speed_ratio", "Local Mach number": "mach"}
_BG_CMAP = {"cp": "RdBu_r", "speed_ratio": "viridis", "mach": "plasma"}
ALONG = (("Speed V/V∞", "speed_ratio", ""), ("Speed", "speed", "m/s"), ("Local Mach", "mach", ""),
         ("Pressure coefficient Cp", "cp", ""), ("Static pressure", "pressure", "kPa"),
         ("Temperature", "temperature", "K"), ("Density", "density", "kg/m³"))


class _Reversed:
    """Wrap a flow field with reversed velocity (to trace streamlines upstream)."""

    def __init__(self, field):
        self.f = field
        self.bounds = field.bounds

    def velocity(self, x, y):
        u, v = self.f.velocity(x, y)
        return -u, -v

    def inside(self, x, y):
        return self.f.inside(x, y)


class FlowView(QWidget):
    probeMoved = Signal()

    def __init__(self, parent=None, length_label="x/c", y_label="y/c"):
        super().__init__(parent)
        self.figure = Figure(figsize=(7, 4.5), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.cax = None
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.length_label, self.y_label = length_label, y_label

        self.background = combo(BACKGROUNDS, 1)
        self.n_lines = int_spin(5, 120, 34)
        self.color_lines = QCheckBox("Colour by speed")
        self.snap = QCheckBox("Probe follows streamlines")
        self.snap.setChecked(True)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Background"))
        controls.addWidget(self.background)
        controls.addWidget(QLabel("Streamlines"))
        controls.addWidget(self.n_lines)
        controls.addWidget(self.color_lines)
        controls.addWidget(self.snap)
        controls.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.toolbar)
        lay.addLayout(controls)
        lay.addWidget(self.canvas, 1)

        self.field = None
        self.lines = []
        self.markers = []
        self.extra_draw = None
        self.line_index = None
        self.probe_xy = None
        self.probe_frac = 0.05
        self._dragging = False
        self._bg = None
        self._pts = None
        self._owner = None
        self._n_seeded = 0
        self._probe_art = self._hi_art = self._text_art = None
        self.canvas.mpl_connect("button_press_event", self._press)
        self.canvas.mpl_connect("motion_notify_event", self._motion)
        self.canvas.mpl_connect("button_release_event", self._release)
        self.canvas.mpl_connect("draw_event", self._on_draw)
        self.background.currentIndexChanged.connect(lambda *_: self.redraw())
        self.color_lines.toggled.connect(lambda *_: self.redraw())
        self.n_lines.valueChanged.connect(lambda *_: self.set_field(self.field, self.markers, keep_view=True))

    # ------------------------------------------------------------------ scene
    def set_field(self, field, markers=None, keep_view=False, extra_draw=None):
        """Show a new flow field. ``markers``: list of (x, y, style-dict, label)."""
        self.field = field
        self.markers = markers or []
        if extra_draw is not None:
            self.extra_draw = extra_draw
        if field is None:
            return
        self.lines = trace_streamlines(field, field.seeds(self.n_lines.value()),
                                       step=(field.bounds[1] - field.bounds[0]) / 260)
        self._n_seeded = len(self.lines)
        self._rebuild_point_index()
        self.redraw(keep_view=keep_view)
        if self.line_index is None or self.line_index >= len(self.lines):
            self.line_index = self._default_line()
        self._place_on_line(self.line_index, self.probe_frac)

    def _rebuild_point_index(self):
        if not self.lines:
            self._pts, self._owner = np.zeros((0, 2)), np.zeros(0, int)
            return
        self._pts = np.vstack(self.lines)
        self._owner = np.concatenate([np.full(len(l), i) for i, l in enumerate(self.lines)])

    def _default_line(self):
        """Pick the streamline that passes just above the body (most interesting to probe)."""
        if not self.lines:
            return None
        outline = self.field.outlines[0] if self.field.outlines else (np.array([0.0]), np.array([0.0]))
        ox, oy = outline
        x_mid = 0.5 * (ox.min() + ox.max())
        y_top = oy.max() if len(oy) else 0.0
        best, best_d = 0, np.inf
        for i, l in enumerate(self.lines):
            k = int(np.argmin(np.abs(l[:, 0] - x_mid)))
            dy = l[k, 1] - y_top
            if abs(l[k, 0] - x_mid) < 0.2 and 0 < dy < best_d:
                best, best_d = i, dy
        return best

    def redraw(self, keep_view=True):
        if self.field is None:
            return
        f = self.field
        xlim, ylim = (self.ax.get_xlim(), self.ax.get_ylim()) if keep_view and self.ax.has_data() else (None, None)
        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        ax = self.ax
        bg = self.background.currentText()
        if bg != "None":
            key = _BG_KEY[bg]
            X, Y, Z = f.scalar_grid(key)
            if key == "cp":
                lo, hi = np.nanpercentile(Z.compressed(), [1, 99]) if Z.count() else (-1, 1)
                lim = max(abs(lo), abs(hi), 0.05)
                levels = np.linspace(-lim, min(lim, 1.0) if lim > 1 else lim, 41)
            else:
                lo, hi = np.nanpercentile(Z.compressed(), [0.5, 99.5]) if Z.count() else (0, 1)
                levels = np.linspace(lo, hi if hi > lo else lo + 1e-3, 41)
            cs = ax.contourf(X, Y, np.clip(Z, levels[0], levels[-1]), levels=levels, cmap=_BG_CMAP[key],
                             extend="neither", alpha=0.9)
            cb = self.figure.colorbar(cs, ax=ax, shrink=0.85, pad=0.01)
            cb.set_label(bg)
        if self.lines:
            if self.color_lines.isChecked():
                segs, vals = [], []
                for l in self.lines:
                    u, v = f.velocity(l[:, 0], l[:, 1])
                    s = np.hypot(u, v)
                    segs.extend(np.stack([l[:-1], l[1:]], axis=1))
                    vals.extend(0.5 * (s[:-1] + s[1:]))
                lc = LineCollection(segs, array=np.asarray(vals), cmap="turbo", linewidths=1.1)
                ax.add_collection(lc)
                if bg == "None":
                    cb = self.figure.colorbar(lc, ax=ax, shrink=0.85, pad=0.01)
                    cb.set_label("Speed V/V∞ (incompressible field)")
            else:
                ax.add_collection(LineCollection(self.lines, colors="k", linewidths=0.8, alpha=0.75))
            # direction arrows
            for l in self.lines[::3]:
                k = len(l) // 4
                if 0 < k < len(l) - 1:
                    ax.annotate("", xy=l[k + 1], xytext=l[k],
                                arrowprops=dict(arrowstyle="->", color="k", lw=0.8))
        for ox, oy in f.outlines:
            ax.fill(ox, oy, color="#4a4f57", zorder=5)
            ax.plot(ox, oy, color="k", lw=0.8, zorder=6)
        for mx, my in f.annotations.get("mach_lines", []):
            ax.plot(mx, my, "--", color="#d03030", lw=1.0, zorder=4)
        if f.annotations.get("disk"):
            ax.plot([0, 0], [-1, 1], color="#303030", lw=4, solid_capstyle="butt", zorder=6)
            ax.text(0.05, 1.05, "propeller disk", fontsize=8, zorder=7)
        for mx, my, style, label in self.markers:
            ax.plot([mx], [my], linestyle="none", zorder=8, label=label, **style)
        if self.markers:
            ax.legend(loc="lower right", fontsize=8, framealpha=0.85)
        if self.extra_draw:
            self.extra_draw(ax)
        x0, x1, y0, y1 = f.bounds
        ax.set_xlim(*(xlim or (x0, x1)))
        ax.set_ylim(*(ylim or (y0, y1)))
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel(self.length_label)
        ax.set_ylabel(self.y_label)
        # animated probe artists (drawn with blitting)
        (self._hi_art,) = ax.plot([], [], color="#ff2d2d", lw=2.2, animated=True, zorder=9)
        (self._probe_art,) = ax.plot([], [], "o", ms=11, mfc="#ff2d2d", mec="white", mew=2, animated=True, zorder=11)
        self._text_art = ax.text(0.01, 0.99, "", transform=ax.transAxes, va="top", ha="left", fontsize=9,
                                 family="monospace", animated=True, zorder=12,
                                 bbox=dict(boxstyle="round", fc="white", ec="0.6", alpha=0.9))
        self.canvas.draw()

    # ------------------------------------------------------------------ probe
    def _place_on_line(self, index, frac):
        if index is None or not self.lines:
            return
        l = self.lines[index]
        s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(l, axis=0).T))])
        k = int(np.clip(np.searchsorted(s, frac * s[-1]), 0, len(l) - 1))
        self.line_index = index
        self.probe_xy = l[k].copy()
        self.probe_frac = float(s[k] / s[-1]) if s[-1] > 0 else 0.0
        self._update_probe()

    def move_probe_to(self, x, y):
        if self.snap.isChecked() and self._pts is not None and len(self._pts):
            d = np.hypot(self._pts[:, 0] - x, self._pts[:, 1] - y)
            j = int(np.argmin(d))
            idx = int(self._owner[j])
            l = self.lines[idx]
            k = j - int(np.sum(self._owner < idx))
            s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(l, axis=0).T))])
            self.line_index = idx
            self.probe_frac = float(s[k] / s[-1]) if s[-1] > 0 else 0.0
            self.probe_xy = l[k].copy()
        else:
            if self.field.inside(np.array([x]), np.array([y]))[0]:
                return
            self.probe_xy = np.array([x, y])
        self._update_probe()

    def _trace_through_probe(self):
        """In free mode, build the streamline that passes through the probe."""
        p = np.array([self.probe_xy])
        step = (self.field.bounds[1] - self.field.bounds[0]) / 260
        fwd = trace_streamlines(self.field, p, step=step)
        back = trace_streamlines(_Reversed(self.field), p, step=step)
        parts = []
        if back:
            parts.append(back[0][::-1])
        if fwd:
            parts.append(fwd[0][1:] if parts else fwd[0])
        if parts:
            line = np.vstack(parts)
            self.lines = self.lines[: self._n_seeded] + [line]
            self._rebuild_point_index()
            self.line_index = len(self.lines) - 1
            s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(line, axis=0).T))])
            k = int(np.argmin(np.hypot(*(line - self.probe_xy).T)))
            self.probe_frac = float(s[k] / s[-1]) if s[-1] > 0 else 0.0

    def probe_state(self):
        if self.probe_xy is None or self.field is None:
            return None
        st = self.field.state(np.array([self.probe_xy[0]]), np.array([self.probe_xy[1]]))
        return {k: float(np.asarray(v).ravel()[0]) for k, v in st.items()}

    def current_line(self):
        if self.line_index is None or not self.lines:
            return None
        return self.lines[self.line_index]

    def _update_probe(self):
        if self._probe_art is None or self.probe_xy is None:
            return
        self._probe_art.set_data([self.probe_xy[0]], [self.probe_xy[1]])
        line = self.current_line()
        if line is not None and (self.snap.isChecked() or self.line_index == len(self.lines) - 1):
            self._hi_art.set_data(line[:, 0], line[:, 1])
        st = self.probe_state()
        if st:
            self._text_art.set_text(
                f"V   {st['speed']:8.2f} m/s\nV/V∞{st['speed_ratio']:8.3f}\nM   {st['mach']:8.3f}\n"
                f"Cp  {st['cp']:8.3f}\np   {st['pressure'] / 1000:8.2f} kPa")
        self._blit()
        self.probeMoved.emit()

    def _on_draw(self, _event):
        self._bg = self.canvas.copy_from_bbox(self.figure.bbox)
        self._draw_animated()

    def _draw_animated(self):
        for art in (self._hi_art, self._probe_art, self._text_art):
            if art is not None:
                self.ax.draw_artist(art)

    def _blit(self):
        if self._bg is None:
            self.canvas.draw()
            return
        self.canvas.restore_region(self._bg)
        self._draw_animated()
        self.canvas.blit(self.figure.bbox)

    def _press(self, event):
        if event.inaxes is not self.ax or event.button != 1 or self.toolbar.mode:
            return
        self._dragging = True
        self.move_probe_to(event.xdata, event.ydata)

    def _motion(self, event):
        if self._dragging and event.inaxes is self.ax and event.xdata is not None:
            self.move_probe_to(event.xdata, event.ydata)

    def _release(self, _event):
        if self._dragging and not self.snap.isChecked() and self.field is not None:
            self._trace_through_probe()
            self._update_probe()
        self._dragging = False


class ProbePanel(QWidget):
    """Readout table for the probe plus the along-streamline plot."""

    def __init__(self, view: FlowView, parent=None):
        super().__init__(parent)
        self.view = view
        self.table = ResultsTable()
        self.table.setMaximumWidth(330)
        self.quantity = combo([a[0] for a in ALONG])
        self.plot = PlotCanvas(toolbar=False, figsize=(5, 2.6))
        right = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(QLabel("Along the selected streamline:"))
        top.addWidget(self.quantity)
        top.addStretch(1)
        right.addLayout(top)
        right.addWidget(self.plot, 1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)
        lay.addLayout(right, 1)
        self._line_cache = (None, None)
        view.probeMoved.connect(self.refresh)
        self.quantity.currentIndexChanged.connect(lambda *_: self.refresh(force=True))

    def refresh(self, force=False):
        v = self.view
        st = v.probe_state()
        if st is None:
            return
        fs = v.field.fs if hasattr(v.field, "fs") else None
        atm = fs.atmosphere if fs else None
        rows = [("Probe position",),
                ("x, y", f"{v.probe_xy[0]:.3f}, {v.probe_xy[1]:.3f}"),
                ("Local flow",),
                ("Speed V", fmt(st["speed"], "m/s")),
                ("V / V∞", fmt(st["speed_ratio"])),
                ("Change from free stream", fmt(st["speed"] - (fs.speed if fs else 0), "m/s")),
                ("Local Mach number", fmt(st["mach"])),
                ("Pressure coefficient Cp", fmt(st["cp"])),
                ("Static pressure", fmt(st["pressure"] / 1000, "kPa")),
                ("Δp from free stream", fmt(st["pressure"] - (atm.pressure if atm else 0), "Pa")),
                ("Total pressure", fmt(st["total_pressure"] / 1000, "kPa")),
                ("Temperature", f"{st['temperature']:.2f} K ({st['temperature'] - 273.15:.2f} °C)"),
                ("Density", fmt(st["density"], "kg/m³")),
                ("Dynamic pressure ½ρV²", fmt(0.5 * st["density"] * st["speed"] ** 2, "Pa"))]
        if st["mach"] >= 1.0:
            rows.append(("Note", "locally SUPERSONIC (M ≥ 1)"))
        self.table.set_rows(rows)
        self._plot_line(force)

    def _plot_line(self, force):
        v = self.view
        line = v.current_line()
        if line is None:
            return
        key = (id(v.field), v.line_index, len(line))
        name, k, unit = ALONG[self.quantity.currentIndex()]
        if self._line_cache[0] != key:
            st = v.field.state(line[:, 0], line[:, 1])
            self._line_cache = (key, st)
        st = self._line_cache[1]
        y = np.asarray(st[k], dtype=float)
        if k == "pressure":
            y = y / 1000
        x = line[:, 0]
        ps = v.probe_state()
        py = ps[k] / (1000 if k == "pressure" else 1)
        ax = self.plot.reset()[0]
        ax.plot(x, y, color="#1f6fd1", lw=1.6)
        ax.axhline(y[0], color="0.5", ls=":", lw=1, label="free stream")
        ax.plot([v.probe_xy[0]], [py], "o", ms=8, mfc="#ff2d2d", mec="white")
        ax.axvline(v.probe_xy[0], color="#ff2d2d", lw=0.8, alpha=0.6)
        ax.set_xlabel(v.length_label)
        ax.set_ylabel(f"{name} [{unit}]" if unit else name)
        if k == "cp":
            ax.invert_yaxis()
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, loc="best")
        self.plot.draw()


class SectionPanel(QWidget):
    """Flow view on top, tabbed plots (probe first) underneath."""

    def __init__(self, parent=None, length_label="x/c", y_label="y/c"):
        super().__init__(parent)
        self.view = FlowView(length_label=length_label, y_label=y_label)
        self.probe = ProbePanel(self.view)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.probe, "Probe")
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.view)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

    def add_plot_tab(self, title, nrows=1, ncols=1):
        canvas = PlotCanvas(nrows, ncols, figsize=(5, 2.6))
        self.tabs.addTab(canvas, title)
        return canvas
