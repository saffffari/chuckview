"""Render chuckview.svg to a multi-size Windows .ico (run before PyInstaller on Windows)."""

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

root = Path(__file__).resolve().parents[1]
app = QGuiApplication(sys.argv)
renderer = QSvgRenderer(str(root / "chuckview" / "assets" / "chuckview.svg"))
image = QImage(256, 256, QImage.Format.Format_ARGB32)
image.fill(Qt.GlobalColor.transparent)
painter = QPainter(image)
renderer.render(painter)
painter.end()
target = root / "packaging" / "chuckview.ico"
if not image.save(str(target), "ICO"):
    raise SystemExit("could not write the icon")
print(target)
