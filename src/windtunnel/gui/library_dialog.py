"""Airfoil library browser: search, preview, NACA code entry, .dat import and UIUC download."""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QVBoxLayout,
)

from ..physics import library
from .widgets import PlotCanvas

_catalog = None


def catalog() -> library.Catalog:
    global _catalog
    if _catalog is None:
        _catalog = library.Catalog()
    return _catalog


class _Downloader(QThread):
    message = Signal(str)
    finished_count = Signal(int, str)

    def run(self):
        try:
            n = library.download_uiuc(self.message.emit)
            self.finished_count.emit(n, "")
        except Exception as exc:  # network problems are reported to the user
            self.finished_count.emit(0, str(exc))


class LibraryDialog(QDialog):
    def __init__(self, parent=None, current: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Airfoil library")
        self.resize(980, 620)
        self.cat = catalog()
        self.selected = None

        self.categories = QListWidget()
        self.categories.setMaximumWidth(230)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search, e.g. 2412, reflex, biconvex, clark…")
        self.results = QListWidget()
        self.code = QLineEdit()
        self.code.setPlaceholderText("Any NACA code: 2412, 0012-34, 23015, 24112…")
        use_code = QPushButton("Use NACA code")
        self.preview = PlotCanvas(toolbar=False, figsize=(5, 2.2))
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.count = QLabel()
        import_btn = QPushButton("Import .dat file…")
        self.download_btn = QPushButton("Download UIUC database (~1,650 airfoils)…")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)

        left = QVBoxLayout()
        left.addWidget(QLabel("Families"))
        left.addWidget(self.categories, 1)
        left.addWidget(import_btn)
        left.addWidget(self.download_btn)
        mid = QVBoxLayout()
        mid.addWidget(self.search)
        mid.addWidget(self.results, 1)
        mid.addWidget(self.count)
        right = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(self.code, 1)
        row.addWidget(use_code)
        right.addLayout(row)
        right.addWidget(self.preview, 1)
        right.addWidget(self.info)
        body = QHBoxLayout()
        body.addLayout(left)
        body.addLayout(mid, 1)
        body.addLayout(right, 2)
        lay = QVBoxLayout(self)
        lay.addLayout(body, 1)
        lay.addWidget(buttons)

        self._fill_categories()
        self.categories.currentRowChanged.connect(lambda *_: self._filter())
        self.search.textChanged.connect(lambda *_: self._filter())
        self.results.currentTextChanged.connect(self._preview_entry)
        self.results.itemDoubleClicked.connect(lambda *_: self.accept())
        use_code.clicked.connect(self._use_code)
        self.code.returnPressed.connect(self._use_code)
        import_btn.clicked.connect(self._import)
        self.download_btn.clicked.connect(self._download)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._filter()
        if current:
            items = self.results.findItems(current, Qt.MatchExactly)
            if items:
                self.results.setCurrentItem(items[0])

    def _fill_categories(self):
        self.categories.clear()
        self.categories.addItem(f"All ({len(self.cat.entries)})")
        for c in self.cat.categories:
            n = sum(1 for e in self.cat.entries if e.category == c)
            self.categories.addItem(f"{c} ({n})")
        self.categories.setCurrentRow(0)
        if self.cat.uiuc_installed():
            self.download_btn.setText(f"Update UIUC database ({self.cat.uiuc_installed()} installed)…")

    def _filter(self):
        row = self.categories.currentRow()
        cat = None if row <= 0 else self.cat.categories[row - 1]
        hits = self.cat.search(self.search.text(), cat)
        self.results.clear()
        self.results.addItems([e.name for e in hits[:5000]])
        self.count.setText(f"{len(hits)} airfoils")
        if hits:
            self.results.setCurrentRow(0)

    def _show(self, af):
        self.selected = af
        ax = self.preview.reset()[0]
        ax.fill(af.x, af.y, color="#5a6070")
        ax.plot(af.x, af.y, "k", lw=0.8)
        ax.set_aspect("equal")
        ax.grid(alpha=0.3)
        ax.set_title(af.name)
        self.preview.draw()
        props = af.properties() if not af.blunt else {}
        text = f"<b>{af.name}</b> — {af.category}<br>{af.description}<br>"
        if props:
            text += (f"Max thickness {props['max thickness']:.1%} at {props['max thickness at x/c']:.0%} chord · "
                     f"max camber {props['max camber']:.2%} at {props['max camber at x/c']:.0%} chord · "
                     f"{props['points']} points")
        self.info.setText(text)

    def _preview_entry(self, name):
        if not name:
            return
        try:
            self._show(self.cat.get(name))
        except Exception as exc:
            self.info.setText(f"Could not build {name}: {exc}")

    def _use_code(self):
        try:
            self._show(self.cat.get(self.code.text().strip()))
        except ValueError as exc:
            QMessageBox.warning(self, "NACA code", str(exc))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import airfoil coordinates", "",
                                              "Airfoil files (*.dat *.txt *.csv);;All files (*)")
        if not path:
            return
        try:
            af = library.import_file(path)
        except Exception as exc:
            QMessageBox.warning(self, "Import failed", f"Could not read {path}:\n{exc}")
            return
        self.cat.refresh()
        self._fill_categories()
        self._show(af)

    def _download(self):
        ok = QMessageBox.question(
            self, "Download UIUC airfoil database",
            "This downloads the UIUC Airfoil Coordinates Database (about 1,650 airfoils, a few MB) from\n"
            f"{library.UIUC_ZIP_URL}\ninto {library.uiuc_dir()}.\n\nContinue?")
        if ok != QMessageBox.Yes:
            return
        self.download_btn.setEnabled(False)
        self._thread = _Downloader()
        self._thread.message.connect(self.count.setText)
        self._thread.finished_count.connect(self._download_done)
        QApplication.setOverrideCursor(Qt.BusyCursor)
        self._thread.start()

    def _download_done(self, n, error):
        QApplication.restoreOverrideCursor()
        self.download_btn.setEnabled(True)
        if error:
            QMessageBox.warning(self, "Download failed", f"Could not download the database:\n{error}")
            return
        self.cat.refresh()
        self._fill_categories()
        QMessageBox.information(self, "UIUC database", f"Installed {n} airfoils. Find them under 'UIUC database'.")


class AirfoilChooser(QPushButton):
    """Button showing the current airfoil; opens the library when clicked."""
    changed = Signal(object)

    def __init__(self, name="NACA 2412", parent=None):
        super().__init__(parent)
        self.airfoil = catalog().get(name)
        self._refresh()
        self.clicked.connect(self._choose)

    def _refresh(self):
        self.setText(f"{self.airfoil.name}   (change…)")
        self.setToolTip(self.airfoil.description)

    def _choose(self):
        dlg = LibraryDialog(self.window(), self.airfoil.name)
        if dlg.exec() == QDialog.Accepted and dlg.selected is not None:
            self.airfoil = dlg.selected
            self._refresh()
            self.changed.emit(self.airfoil)
