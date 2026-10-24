"""Create a clickable desktop shortcut (and a Start menu / app menu entry) for the simulator.

    python -m windtunnel.shortcut             # desktop + menu entry
    python -m windtunnel.shortcut --no-menu   # desktop only

The shortcut runs the Python interpreter that executes this module, so run it with
the interpreter from the simulator's virtual environment (the scripts in ``scripts/``
do that for you).

* Windows: ``Virtual Wind Tunnel.lnk`` on the desktop and in the Start menu
* macOS:   ``Virtual Wind Tunnel.app`` on the desktop (and in ~/Applications)
* Linux:   ``virtual-wind-tunnel.desktop`` on the desktop and in the application menu
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "Virtual Wind Tunnel"
DESCRIPTION = "Airfoil, wing, propeller and blunt-body wind tunnel simulator"
ASSETS = Path(__file__).resolve().parent / "assets"


def gui_python() -> Path:
    """The interpreter to launch the GUI with (pythonw on Windows: no console window)."""
    exe = Path(sys.executable)
    if sys.platform == "win32":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            return pythonw
    return exe


# ------------------------------------------------------------------ Windows
def _ps_quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _windows_folder(name: str) -> Path:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"[Environment]::GetFolderPath('{name}')"],
                         capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


def _windows_shortcut(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    link = folder / f"{APP_NAME}.lnk"
    script = "; ".join([
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(link) + ")",
        "$s.TargetPath = " + _ps_quote(gui_python()),
        "$s.Arguments = '-m windtunnel'",
        "$s.WorkingDirectory = " + _ps_quote(Path.home()),
        "$s.IconLocation = " + _ps_quote(f"{ASSETS / 'windtunnel.ico'},0"),
        "$s.Description = " + _ps_quote(DESCRIPTION),
        "$s.Save()",
    ])
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True, text=True)
    return link


def create_windows(desktop: Path | None, menu: bool) -> list[Path]:
    made = [_windows_shortcut(desktop or _windows_folder("Desktop"))]
    if menu:
        made.append(_windows_shortcut(_windows_folder("Programs")))
    return made


# ------------------------------------------------------------------ macOS
_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>{name}</string>
    <key>CFBundleDisplayName</key><string>{name}</string>
    <key>CFBundleIdentifier</key><string>io.github.oreo7382.windtunnel</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundleIconFile</key><string>windtunnel.icns</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""


def _mac_app(folder: Path) -> Path:
    app = folder / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text(_PLIST.format(name=APP_NAME))
    launcher = macos / "launcher"
    launcher.write_text(f'#!/bin/bash\nexec "{gui_python()}" -m windtunnel "$@"\n')
    launcher.chmod(0o755)
    try:
        from PIL import Image
        Image.open(ASSETS / "windtunnel.png").save(resources / "windtunnel.icns")
    except Exception:
        pass  # the app still works, just with the generic icon
    return app


def create_macos(desktop: Path | None, menu: bool) -> list[Path]:
    made = [_mac_app(desktop or Path.home() / "Desktop")]
    if menu:
        apps = Path.home() / "Applications"
        apps.mkdir(exist_ok=True)
        made.append(_mac_app(apps))
    return made


# ------------------------------------------------------------------ Linux
def _linux_desktop_dir() -> Path:
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, check=True)
        if out.stdout.strip():
            return Path(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        pass
    return Path.home() / "Desktop"


def _linux_entry() -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        f"Comment={DESCRIPTION}",
        f'Exec="{gui_python()}" -m windtunnel',
        f"Icon={ASSETS / 'windtunnel.png'}",
        "Terminal=false",
        "Categories=Education;Science;Physics;",
        "",
    ])


def _write_linux(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_linux_entry())
    path.chmod(0o755)
    # GNOME only launches desktop files that are marked as trusted
    try:
        subprocess.run(["gio", "set", str(path), "metadata::trusted", "true"], capture_output=True)
    except OSError:
        pass
    return path


def create_linux(desktop: Path | None, menu: bool) -> list[Path]:
    name = "virtual-wind-tunnel.desktop"
    made = [_write_linux((desktop or _linux_desktop_dir()) / name)]
    if menu:
        data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        made.append(_write_linux(data / "applications" / name))
    return made


# ------------------------------------------------------------------ entry point
def create_shortcuts(desktop: Path | None = None, menu: bool = True) -> list[Path]:
    """Create the shortcuts for this operating system and return their paths."""
    if sys.platform == "win32":
        return create_windows(desktop, menu)
    if sys.platform == "darwin":
        return create_macos(desktop, menu)
    return create_linux(desktop, menu)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create a desktop shortcut for the Virtual Wind Tunnel.")
    parser.add_argument("--no-menu", action="store_true", help="only create the desktop shortcut")
    parser.add_argument("--desktop", type=Path, help="folder to put the shortcut in (default: your desktop)")
    args = parser.parse_args(argv)
    for path in create_shortcuts(args.desktop, not args.no_menu):
        print(f"Created {path}")
    if sys.platform.startswith("linux"):
        print("If your desktop shows the icon as a text file, right-click it and choose 'Allow Launching'.")


if __name__ == "__main__":
    main()
