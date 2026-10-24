"""Airfoil library: built-in generated families, imported .dat files and the
UIUC Airfoil Coordinates Database (downloaded on request to the user's computer).

Built-in families are generated from their defining equations, so the library
needs no data files. Any NACA 4-digit, 4-digit-modified or 5-digit code can
also be typed in directly.
"""
from __future__ import annotations

import io
import os
import re
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import naca, shapes
from .geometry import Airfoil

UIUC_ZIP_URL = "https://m-selig.ae.illinois.edu/ads/archives/coord_seligFmt.zip"
UIUC_HOME = "https://m-selig.ae.illinois.edu/ads/coord_database.html"


def user_data_dir() -> Path:
    d = Path(os.environ.get("WINDTUNNEL_DATA_DIR", Path.home() / ".windtunnel-sim"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def imported_dir() -> Path:
    d = user_data_dir() / "airfoils"
    d.mkdir(parents=True, exist_ok=True)
    return d


def uiuc_dir() -> Path:
    return user_data_dir() / "uiuc"


@dataclass
class Entry:
    name: str
    category: str
    build: Callable[[], Airfoil]
    keywords: str = ""


# ------------------------------------------------------------------ .dat files
def parse_dat(text: str, fallback_name: str = "Imported airfoil") -> Airfoil:
    """Parse Selig- or Lednicer-format airfoil coordinates."""
    lines = [ln.strip() for ln in text.replace("\t", " ").splitlines()]
    name = fallback_name
    rows = []
    for ln in lines:
        if not ln:
            rows.append(None)
            continue
        parts = re.split(r"[\s,;]+", ln)
        try:
            vals = [float(p) for p in parts[:2]]
            if len(vals) == 2:
                rows.append(vals)
                continue
        except ValueError:
            pass
        if not rows or all(r is None for r in rows):
            name = ln or name
    pts = [r for r in rows if r is not None]
    if len(pts) < 6:
        raise ValueError("Fewer than 6 coordinate pairs found")
    first = pts[0]
    if first[0] > 1.5 and first[1] > 1.5:
        # Lednicer: counts line, then upper LE->TE, then lower LE->TE
        nu, nl = int(first[0]), int(first[1])
        body = pts[1:]
        upper = np.array(body[:nu])
        lower = np.array(body[nu:nu + nl])
        x = np.concatenate([upper[::-1, 0], lower[1:, 0]])
        y = np.concatenate([upper[::-1, 1], lower[1:, 1]])
    else:
        arr = np.array(pts)
        x, y = arr[:, 0], arr[:, 1]
        # ensure Selig order (TE -> upper -> LE -> lower -> TE, counter-clockwise)
        i_le = int(np.argmin(x))
        if i_le > 0 and np.mean(y[:i_le]) < np.mean(y[i_le:]):
            x, y = x[::-1], y[::-1]
    af = Airfoil(name.strip()[:80], x, y, "Imported", "Imported from coordinate file")
    return af


def load_dat(path: Path, category: str = "Imported") -> Airfoil:
    text = Path(path).read_text(errors="replace")
    af = parse_dat(text, Path(path).stem)
    af.category = category
    af.description = f"{category}: {Path(path).name}"
    return af


def import_file(path: str) -> Airfoil:
    """Validate a .dat file and copy it into the user's library folder."""
    af = load_dat(Path(path))
    dest = imported_dir() / Path(path).name
    dest.write_text(Path(path).read_text(errors="replace"))
    return af


# ------------------------------------------------------------------ UIUC download
def download_uiuc(progress: Callable[[str], None] | None = None) -> int:
    """Download and unpack the UIUC database into the user data folder. Returns file count."""
    say = progress or (lambda msg: None)
    say("Downloading UIUC airfoil database...")
    req = urllib.request.Request(UIUC_ZIP_URL, headers={"User-Agent": "windtunnel-sim"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    say(f"Downloaded {len(data) / 1e6:.1f} MB, unpacking...")
    target = uiuc_dir()
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.infolist():
            name = Path(member.filename).name
            if not name.lower().endswith(".dat") or member.is_dir():
                continue
            (target / name).write_bytes(zf.read(member))
            count += 1
    say(f"Installed {count} airfoils.")
    return count


# ------------------------------------------------------------------ catalog
_THICK = ["06", "08", "09", "10", "12", "15", "18", "21", "24"]


def _builtin_entries():
    e = []
    for t in _THICK:
        code = f"00{t}"
        e.append(Entry(f"NACA {code}", "NACA 4-digit symmetric", lambda c=code: naca.naca4(c), "symmetric"))
    for m in range(1, 10):
        for p in range(2, 8):
            for t in _THICK:
                code = f"{m}{p}{t}"
                e.append(Entry(f"NACA {code}", "NACA 4-digit", lambda c=code: naca.naca4(c), "cambered"))
    for t in ["06", "08", "09", "10", "12", "15", "18", "21"]:
        for i in (3, 4, 6, 9):
            for pos in (3, 4, 5):
                code = f"00{t}-{i}{pos}"
                e.append(Entry(f"NACA {code}", "NACA 4-digit modified",
                               lambda c=code: naca.naca4_modified(c), "modified"))
    for code in ("2412-34", "4412-34", "2415-63", "4415-63"):
        e.append(Entry(f"NACA {code}", "NACA 4-digit modified", lambda c=code: naca.naca4_modified(c), "modified"))
    for L in (1, 2, 3, 4):
        for P in range(1, 6):
            for t in _THICK:
                code = f"{L}{P}0{t}"
                e.append(Entry(f"NACA {code}", "NACA 5-digit", lambda c=code: naca.naca5(c), "five digit"))
        for P in range(2, 6):
            for t in _THICK:
                code = f"{L}{P}1{t}"
                e.append(Entry(f"NACA {code}", "NACA 5-digit reflex", lambda c=code: naca.naca5(c),
                               "five digit reflex flying wing"))
    for t in (0.06, 0.09, 0.12, 0.15, 0.18, 0.21):
        for f in (0.0, 0.02, 0.04, 0.06):
            e.append(Entry(f"Joukowski t={t:.0%} f={f:.0%}", "Joukowski",
                           lambda t=t, f=f: shapes.joukowski(t, f), "conformal exact"))
    for t in (0.08, 0.12, 0.16):
        for f in (0.0, 0.03):
            for te in (5.0, 10.0, 20.0):
                e.append(Entry(f"Karman-Trefftz t={t:.0%} f={f:.0%} TE {te:.0f}deg", "Karman-Trefftz",
                               lambda t=t, f=f, te=te: shapes.karman_trefftz(t, f, te), "conformal"))
    for t in (0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12):
        e.append(Entry(f"Biconvex {t:.0%}", "Supersonic", lambda t=t: shapes.biconvex(t), "supersonic sharp"))
        e.append(Entry(f"Double wedge {t:.0%} (max at 50%)", "Supersonic",
                       lambda t=t: shapes.double_wedge(t, 0.5), "supersonic diamond"))
        e.append(Entry(f"Double wedge {t:.0%} (max at 30%)", "Supersonic",
                       lambda t=t: shapes.double_wedge(t, 0.3), "supersonic wedge"))
    for t in (0.01, 0.02, 0.04):
        e.append(Entry(f"Flat plate {t:.0%}", "Plates & arcs", lambda t=t: shapes.flat_plate(t), "plate"))
    for f in (0.02, 0.04, 0.06, 0.08, 0.10, 0.12):
        e.append(Entry(f"Cambered plate {f:.0%}", "Plates & arcs", lambda f=f: shapes.circular_arc(f), "sail arc"))
    for t in (0.06, 0.12, 0.20):
        e.append(Entry(f"Elliptical {t:.0%}", "Plates & arcs", lambda t=t: shapes.elliptic_airfoil(t), "ellipse"))
    return e


def _file_entries(folder: Path, category: str):
    if not folder.exists():
        return []
    out = []
    for p in sorted(folder.glob("*.dat")):
        out.append(Entry(p.stem, category, lambda p=p, c=category: load_dat(p, c), p.stem))
    return out


class Catalog:
    def __init__(self):
        self.refresh()

    def refresh(self):
        self.entries = (_builtin_entries() + _file_entries(imported_dir(), "Imported")
                        + _file_entries(uiuc_dir(), "UIUC database"))
        self.by_name = {e.name: e for e in self.entries}

    @property
    def categories(self):
        seen = []
        for e in self.entries:
            if e.category not in seen:
                seen.append(e.category)
        return seen

    def search(self, text: str = "", category: str | None = None):
        words = text.lower().split()
        out = []
        for e in self.entries:
            if category and e.category != category:
                continue
            hay = f"{e.name} {e.category} {e.keywords}".lower()
            if all(w in hay for w in words):
                out.append(e)
        return out

    def get(self, name: str) -> Airfoil:
        if name in self.by_name:
            return self.by_name[name].build()
        return naca.from_code(name)

    def uiuc_installed(self) -> int:
        return sum(1 for e in self.entries if e.category == "UIUC database")
