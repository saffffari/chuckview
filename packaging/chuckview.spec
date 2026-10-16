# PyInstaller spec for ChuckView — a one-folder app (fast to start; one-file unpacks to a
# temp dir on every launch, which antivirus on hospital machines makes slow).
#
#   pyinstaller packaging/chuckview.spec --noconfirm
#
# The trap is the DICOM codecs: pylibjpeg finds libjpeg / openjpeg / rle through package
# entry points, which live in each package's dist-info. Without copy_metadata the frozen
# app opens uncompressed DICOM and silently fails on every JPEG-lossless film. The CI runs
# `ChuckView --self-test` against the built app to catch exactly that.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller

datas = [(str(ROOT / "chuckview" / "assets"), "chuckview/assets")]
datas += collect_data_files("pydicom", excludes=["**/tests/**", "**/test_files/**"])
for dist in ("pylibjpeg", "pylibjpeg-libjpeg", "pylibjpeg-openjpeg", "pylibjpeg-rle",
             "pydicom", "numpy"):
    datas += copy_metadata(dist)

hiddenimports = (collect_submodules("pydicom.pixels") + collect_submodules("pylibjpeg")
                 + ["libjpeg", "openjpeg", "rle", "libjpeg._libjpeg", "openjpeg._openjpeg",
                    "rle._rle"])

icon = ROOT / "packaging" / "chuckview.ico"
windows = sys.platform.startswith("win")

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "PyQt6.QtWebEngineCore", "PyQt6.QtQml",
              "PyQt6.QtQuick", "PyQt6.QtMultimedia", "PyQt6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ChuckView",
    console=False,
    icon=str(icon) if windows and icon.exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ChuckView")  # noqa: F821
