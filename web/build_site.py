"""Assemble the browser version into a static folder for GitHub Pages.

    python web/build_site.py [output_folder]      (default: _site)

The folder contains the web page and windtunnel.zip, which holds the physics
package and the bridge module that Pyodide unpacks in the browser.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PKG = ROOT / "src" / "windtunnel"
PAGE_FILES = ("index.html", "style.css", "app.js", "flowcanvas.js", "worker.js")
MARKER = ".windtunnel-site"


def build(out: Path) -> Path:
    out = Path(out)
    if out.exists():
        if not (out / MARKER).exists():
            raise SystemExit(f"{out} exists and was not made by this script; choose another folder.")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("built by web/build_site.py\n")
    (out / ".nojekyll").write_text("")
    sources = [WEB / n for n in PAGE_FILES] + [WEB / "py" / "bridge.py", PKG / "__init__.py"]
    sources += sorted((PKG / "physics").glob("*.py"))
    version = hashlib.sha256(b"".join(p.read_bytes() for p in sources)).hexdigest()[:10]
    # cache-busting: every reference carries the content hash, so browsers fetch new versions
    rewrites = {
        "index.html": [('href="style.css"', f'href="style.css?v={version}"'),
                       ('src="app.js"', f'src="app.js?v={version}"')],
        "app.js": [('"./flowcanvas.js"', f'"./flowcanvas.js?v={version}"'),
                   ('new Worker("worker.js")', f'new Worker("worker.js?v={version}")')],
        "worker.js": [('fetch("windtunnel.zip")', f'fetch("windtunnel.zip?v={version}")')],
    }
    for name in PAGE_FILES:
        text = (WEB / name).read_text(encoding="utf-8")
        for old, new in rewrites.get(name, []):
            if old not in text:
                raise SystemExit(f"build_site: expected {old!r} in {name}")
            text = text.replace(old, new)
        (out / name).write_text(text, encoding="utf-8")
    shutil.copy2(PKG / "assets" / "windtunnel.png", out / "icon.png")
    with zipfile.ZipFile(out / "windtunnel.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.write(WEB / "py" / "bridge.py", "bridge.py")
        z.write(PKG / "__init__.py", "windtunnel/__init__.py")
        for path in sorted((PKG / "physics").glob("*.py")):
            z.write(path, f"windtunnel/physics/{path.name}")
    return out


if __name__ == "__main__":
    target = build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site")
    print(f"Built {target}")
