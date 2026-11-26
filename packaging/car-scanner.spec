# PyInstaller spec for the Car Scanner desktop app.
#   pyinstaller packaging/car-scanner.spec --noconfirm
# macOS -> dist/Car Scanner.app   Windows/Linux -> dist/CarScanner/CarScanner(.exe)
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
IS_MAC = sys.platform == "darwin"
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "carscan" / "__init__.py").read_text()).group(1)

hidden = (collect_submodules("uvicorn") + collect_submodules("serial.tools")
          + ["carscan.api.auth", "carscan.api.garage", "carscan.api.workflows", "carscan.api.system",
             "multipart", "python_multipart"])

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "web"), "web")],
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="CarScanner",
    console=not IS_MAC,          # Windows/Linux: the console window is the "stop" control
    disable_windowed_traceback=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="CarScanner")

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Car Scanner.app",
        bundle_identifier="io.github.lordjhamb.carscanner",
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSBackgroundOnly": False,
        },
    )
