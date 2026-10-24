"""The desktop-shortcut maker produces a launcher for the current operating system."""
import sys

from windtunnel import shortcut


def test_assets_exist():
    assert (shortcut.ASSETS / "windtunnel.png").exists()
    assert (shortcut.ASSETS / "windtunnel.ico").exists()


def test_create_shortcut_in_folder(tmp_path):
    made = shortcut.create_shortcuts(desktop=tmp_path, menu=False)
    assert len(made) == 1 and made[0].exists()
    path = made[0]
    if sys.platform == "win32":
        assert path.suffix == ".lnk" and path.stat().st_size > 0
    elif sys.platform == "darwin":
        launcher = path / "Contents" / "MacOS" / "launcher"
        assert "-m windtunnel" in launcher.read_text()
        assert (path / "Contents" / "Info.plist").exists()
    else:
        text = path.read_text()
        assert "Exec=" in text and "-m windtunnel" in text and "Icon=" in text
