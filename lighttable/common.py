"""Small shared Qt helpers for the Elements MVP applications."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontDatabase, QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel


BG = "#0d0d0d"
PANEL = "#151515"
INK = "#f2f2f2"
MUTE = "#5a5a5a"
BORDER = "#363636"
SELECT = "#ff6600"


HOUSE_STYLE = f"""
QWidget {{ background: {BG}; color: {INK}; }}
QLabel#elementMark {{ font-size: 30px; font-weight: 800; }}
QLabel#elementTitle {{ font-size: 11px; font-weight: 700; letter-spacing: 3px; }}
QLabel#muted {{ color: {MUTE}; }}
QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 9px; }}
QPushButton {{ color: {INK}; background: transparent; border: 1px solid {BORDER};
               border-radius: 7px; padding: 8px 11px; font-size: 10px;
               font-weight: 700; letter-spacing: 1px; }}
QPushButton:hover {{ border-color: {INK}; }}
QPushButton:pressed {{ background: {PANEL}; }}
QPushButton:checked {{ border-color: {SELECT}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ color: {INK}; background: {PANEL};
               border: 1px solid {BORDER}; border-radius: 7px; padding: 7px 9px;
               selection-background-color: {SELECT}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {SELECT}; }}
QSlider::groove:horizontal {{ background: {BORDER}; height: 2px; }}
QSlider::handle:horizontal {{ background: {INK}; border: 0; width: 12px; margin: -5px 0; border-radius: 6px; }}
QSlider::sub-page:horizontal {{ background: {SELECT}; }}
QScrollBar:vertical {{ background: transparent; width: 6px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; min-height: 28px; border-radius: 3px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QToolTip {{ color: {INK}; background: {PANEL}; border: 1px solid {BORDER}; }}
"""


def configure_application(app: QApplication, name: str) -> None:
    app.setApplicationName(name)
    app.setOrganizationName("2Photon")
    available = set(QFontDatabase.families())
    family = next(
        (f for f in ("Neue Haas Grotesk Display Pro", "Helvetica Neue", "Inter", "Arial") if f in available),
        app.font().family(),
    )
    app.setFont(QFont(family, 10))
    app.setStyleSheet(HOUSE_STYLE)


def normalize_u8(array: np.ndarray, low: float | None = None, high: float | None = None) -> np.ndarray:
    values = np.asarray(array)
    if values.ndim not in (2, 3):
        raise ValueError("display arrays must be 2D grayscale or 3D RGB/RGBA")
    if values.ndim == 3 and values.shape[2] not in (3, 4):
        raise ValueError("color arrays must have 3 or 4 channels")
    if np.issubdtype(values.dtype, np.complexfloating) or not (
        np.issubdtype(values.dtype, np.number) or np.issubdtype(values.dtype, np.bool_)
    ):
        raise ValueError("display arrays must contain real numeric values")
    if values.dtype == np.uint8 and low is None and high is None:
        return np.ascontiguousarray(values)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return np.zeros(values.shape, dtype=np.uint8)
    lo = float(np.percentile(finite, 1.0)) if low is None else float(low)
    hi = float(np.percentile(finite, 99.0)) if high is None else float(high)
    if hi <= lo:
        hi = lo + 1.0
    invalid = ~np.isfinite(values)
    scaled = np.clip((values.astype(np.float64) - lo) / (hi - lo), 0.0, 1.0)
    scaled[invalid] = 0.0
    return np.ascontiguousarray(np.round(scaled * 255.0).astype(np.uint8))


def array_to_qimage(array: np.ndarray) -> QImage:
    pixels = normalize_u8(array)
    if pixels.ndim == 2:
        height, width = pixels.shape
        image = QImage(pixels.data, width, height, pixels.strides[0], QImage.Format.Format_Grayscale8)
    else:
        height, width, channels = pixels.shape
        fmt = QImage.Format.Format_RGB888 if channels == 3 else QImage.Format.Format_RGBA8888
        image = QImage(pixels.data, width, height, pixels.strides[0], fmt)
    return image.copy()


class ArrayView(QLabel):
    """A resize-aware image label backed by a NumPy array."""

    def __init__(self, text: str = "DROP OR OPEN DATA") -> None:
        super().__init__(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(160, 120)
        self.setStyleSheet(f"background:{PANEL}; border:1px solid {BORDER}; color:{MUTE};")
        self._source: QPixmap | None = None

    def set_array(self, array: np.ndarray) -> None:
        self._source = QPixmap.fromImage(array_to_qimage(array))
        self._refresh()

    def clear_array(self, text: str = "DROP OR OPEN DATA") -> None:
        self._source = None
        self.clear()
        self.setText(text)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        if self._source is not None and not self._source.isNull():
            self.setPixmap(
                self._source.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )


def asset_icon(app_file: str, symbol: str) -> Path:
    return Path(app_file).resolve().parent / "assets" / f"{symbol}.svg"
