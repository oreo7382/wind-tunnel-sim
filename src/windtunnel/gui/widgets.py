"""Reusable Qt widgets: linked slider/spin boxes, flow-condition inputs, results tables."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QSizePolicy, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QScrollArea,
    QAbstractItemView,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from ..physics.atmosphere import Freestream, isa


class ValueSlider(QWidget):
    """A slider and a spin box that edit the same floating-point value."""
    valueChanged = Signal(float)

    def __init__(self, minimum, maximum, value, step=0.1, decimals=1, suffix="", parent=None):
        super().__init__(parent)
        self._scale = 10 ** decimals
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(round(minimum * self._scale)), int(round(maximum * self._scale)))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(minimum, maximum)
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(step)
        self.spin.setSuffix(suffix)
        self.spin.setKeyboardTracking(False)
        self.spin.setMinimumWidth(90)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.setValue(value)

    def _from_slider(self, v):
        val = v / self._scale
        if abs(val - self.spin.value()) > 0.5 / self._scale:
            self.spin.blockSignals(True)
            self.spin.setValue(val)
            self.spin.blockSignals(False)
            self.valueChanged.emit(val)

    def _from_spin(self, val):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(val * self._scale)))
        self.slider.blockSignals(False)
        self.valueChanged.emit(val)

    def value(self) -> float:
        return float(self.spin.value())

    def setValue(self, val):
        self.spin.setValue(val)
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(val * self._scale)))
        self.slider.blockSignals(False)


def spin(minimum, maximum, value, step=1.0, decimals=2, suffix="") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(minimum, maximum)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(value)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


def int_spin(minimum, maximum, value) -> QSpinBox:
    s = QSpinBox()
    s.setRange(minimum, maximum)
    s.setValue(value)
    s.setKeyboardTracking(False)
    return s


def combo(items, current=0) -> QComboBox:
    c = QComboBox()
    c.addItems(list(items))
    c.setCurrentIndex(current)
    return c


SPEED_UNITS = ("m/s", "km/h", "knots", "mph", "Mach")
_TO_MS = {"m/s": 1.0, "km/h": 1 / 3.6, "knots": 0.514444, "mph": 0.44704}


class FlowConditions(QGroupBox):
    """Free-stream inputs: altitude, temperature offset, speed (any unit or Mach)."""
    changed = Signal()

    def __init__(self, title="Free stream", speed=50.0, altitude=0.0, allow_zero=False, parent=None):
        super().__init__(title, parent)
        form = QFormLayout(self)
        self.altitude = spin(-500, 80000, altitude, 500, 0, " m")
        self.dT = spin(-60, 60, 0.0, 1, 1, " K")
        self.unit = combo(SPEED_UNITS)
        self.speed = spin(0.0 if allow_zero else 0.1, 3000.0, speed, 1.0, 2)
        self._min_speed = 0.0 if allow_zero else 0.1
        row = QHBoxLayout()
        row.addWidget(self.speed, 1)
        row.addWidget(self.unit)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color: palette(mid); font-size: 11px;")
        form.addRow("Altitude", self.altitude)
        form.addRow("ISA temperature offset", self.dT)
        form.addRow("Speed", row)
        form.addRow(self.info)
        self._last_unit = "m/s"
        for w in (self.altitude, self.dT, self.speed):
            w.valueChanged.connect(self._emit)
        self.unit.currentTextChanged.connect(self._unit_changed)
        self._update_info()

    def _speed_ms(self, value=None, unit=None):
        value = self.speed.value() if value is None else value
        unit = unit or self.unit.currentText()
        if unit == "Mach":
            return value * self.atmosphere().speed_of_sound
        return value * _TO_MS[unit]

    def _unit_changed(self, unit):
        ms = self._speed_ms(self.speed.value(), self._last_unit)
        self._last_unit = unit
        self.speed.blockSignals(True)
        if unit == "Mach":
            self.speed.setDecimals(3)
            self.speed.setSingleStep(0.01)
            self.speed.setRange(self._min_speed / 340, 8.0)
            self.speed.setValue(ms / self.atmosphere().speed_of_sound)
        else:
            self.speed.setDecimals(2)
            self.speed.setSingleStep(1.0)
            self.speed.setRange(self._min_speed, 3000 / _TO_MS[unit])
            self.speed.setValue(ms / _TO_MS[unit])
        self.speed.blockSignals(False)
        self._emit()

    def atmosphere(self):
        return isa(self.altitude.value(), self.dT.value())

    def freestream(self) -> Freestream:
        return Freestream(self.atmosphere(), self._speed_ms())

    def set_speed(self, ms: float):
        self.unit.setCurrentText("m/s")
        self.speed.setValue(ms)

    def _emit(self, *_):
        self._update_info()
        self.changed.emit()

    def _update_info(self):
        fs = self.freestream()
        a = fs.atmosphere
        self.info.setText(
            f"V = {fs.speed:.1f} m/s ({fs.speed * 3.6:.0f} km/h), M = {fs.mach:.3f}<br>"
            f"T = {a.temperature:.1f} K ({a.temperature - 273.15:.1f} °C), p = {a.pressure / 1000:.2f} kPa<br>"
            f"ρ = {a.density:.4f} kg/m³, a = {a.speed_of_sound:.1f} m/s, "
            f"q = {fs.dynamic_pressure:.0f} Pa")


class ResultsTable(QTableWidget):
    """Two-column table of named results; rows can be grouped with header rows."""

    def __init__(self, parent=None):
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["Quantity", "Value"])
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setAlternatingRowColors(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_rows(self, rows):
        """rows: list of (name, value_str) or (header_str,) tuples."""
        self.setRowCount(len(rows))
        for i, row in enumerate(rows):
            if len(row) == 1:
                item = QTableWidgetItem(row[0])
                f = item.font()
                f.setBold(True)
                item.setFont(f)
                self.setItem(i, 0, item)
                self.setItem(i, 1, QTableWidgetItem(""))
            else:
                self.setItem(i, 0, QTableWidgetItem(row[0]))
                self.setItem(i, 1, QTableWidgetItem(row[1]))
        self.resizeRowsToContents()


class WarningsLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextFormat(Qt.RichText)
        self.setStyleSheet("QLabel { background: rgba(230, 160, 0, 40); border-radius: 4px; padding: 6px; }")
        self.hide()

    def set_warnings(self, items):
        if not items:
            self.hide()
            return
        self.setText("<br>".join(f"⚠ {w}" for w in items))
        self.show()


class PlotCanvas(QWidget):
    """Matplotlib figure with a navigation toolbar."""

    def __init__(self, nrows=1, ncols=1, projection=None, toolbar=True, parent=None, figsize=(6, 4)):
        super().__init__(parent)
        self.figure = Figure(figsize=figsize, layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if toolbar:
            self.toolbar = NavigationToolbar2QT(self.canvas, self)
            lay.addWidget(self.toolbar)
        lay.addWidget(self.canvas, 1)
        self.reset(nrows, ncols, projection)

    def reset(self, nrows=1, ncols=1, projection=None):
        self.figure.clear()
        kw = {"projection": projection} if projection else {}
        self.axes = [self.figure.add_subplot(nrows, ncols, i + 1, **kw) for i in range(nrows * ncols)]
        return self.axes

    def draw(self):
        self.canvas.draw_idle()


def scroll_panel(widget: QWidget, width=330) -> QScrollArea:
    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setMinimumWidth(width)
    area.setMaximumWidth(width + 80)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return area


class Debouncer:
    """Collapse bursts of change signals into one call after a short delay."""

    def __init__(self, callback, delay_ms=120):
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(delay_ms)
        self.timer.timeout.connect(callback)

    def __call__(self, *_):
        self.timer.start()


def fmt(value, unit="", digits=4):
    """Format a number with sensible significant figures."""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
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


def check(text, checked=True) -> QCheckBox:
    c = QCheckBox(text)
    c.setChecked(checked)
    return c
