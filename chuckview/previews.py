"""Previews: the pictures a catalog keeps of its own, beside it in `<name>.chuckdata/`.

Every item gets a 256 px thumbnail for the grid and a 2048 px preview for the loupe —
the middle slice of a series, a film in its own window. Browsing, filtering, tagging and
rating then never touch the originals, which on a hospital share or an unplugged drive is
the difference between a catalog you can work in and one that shows grey squares. The
originals are only read to look at full resolution: scroll a CT, window a film, zoom 1:1.

Previews are JPEG at high quality. They are for choosing, not for measuring; anything
quantitative reads the original.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QImageReader

from chuckview import dicom
from chuckview.stack import array_to_qimage

THUMB = 256
PREVIEW = 2048
QUALITY = 92


class PreviewStore:
    def __init__(self, data_dir: str | os.PathLike):
        self.root = Path(data_dir)

    def thumb_path(self, item_id: int) -> Path:
        return self.root / "thumbs" / f"{item_id // 1000:04d}" / f"{item_id}.jpg"

    def preview_path(self, item_id: int) -> Path:
        return self.root / "previews" / f"{item_id // 1000:04d}" / f"{item_id}.jpg"

    def has(self, item_id: int) -> bool:
        return self.thumb_path(item_id).exists() and self.preview_path(item_id).exists()

    def build(self, item_id: int, item: dicom.Item | None, path: str) -> bool:
        """Render and save both sizes. False if the original cannot be read right now —
        the item stays without previews and is tried again next time."""
        image = render(item, path, PREVIEW)
        if image is None or image.isNull():
            return False
        for target, side in ((self.preview_path(item_id), PREVIEW),
                             (self.thumb_path(item_id), THUMB)):
            target.parent.mkdir(parents=True, exist_ok=True)
            scaled = fit(image, side)
            if not scaled.save(str(target), "JPG", QUALITY):
                return False
        return True

    def remove(self, item_id: int) -> None:
        for target in (self.thumb_path(item_id), self.preview_path(item_id)):
            try:
                target.unlink()
            except OSError:
                pass


def fit(image: QImage, side: int) -> QImage:
    if max(image.width(), image.height()) <= side:
        return image
    return image.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


def render(item: dicom.Item | None, path: str, side: int) -> QImage | None:
    """One picture of an item at most `side` px on its long edge, aspect-correct."""
    if item is None:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > side:
            reader.setScaledSize(size.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        return None if image.isNull() else image
    middle = item.count // 2
    file, frame = item.frames[middle]
    try:
        raw = dicom.decode_frame(file, frame)
    except Exception:  # noqa: BLE001 - offline, truncated, or no codec: no preview yet
        return None
    header = item.headers[middle] if len(item.headers) == item.count else item.first
    center, width = header.window or dicom.auto_window(raw, header.slope, header.intercept)
    image = array_to_qimage(dicom.to_display(raw, header.slope, header.intercept, center,
                                             width, item.invert))
    sx, sy = dicom.plane_aspect(item, "axial")
    w, h = image.width() * sx, image.height() * sy
    scale = min(1.0, side / max(w, h))
    return image.scaled(max(1, round(w * scale)), max(1, round(h * scale)),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
