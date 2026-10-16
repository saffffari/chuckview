"""The Qt side of stacks: background prefetch, the slice ruler, and pixels onto the screen.

A CT is scrolled, not opened. The viewer asks a SliceSource (dicom.py) for the slice it is
on; the prefetcher decodes the rest in the background, outward from wherever you are, on a
pool of its own so it never waits behind a folder's worth of thumbnails; and the ruler
under the picture shows where you are in the stack and how much of it is in memory —
which, on a slow hospital laptop, is the answer to "why is this slice still blank".
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QObject, QRect, QRunnable, Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from chuckview.dicom import SliceSource
from chuckview.panels import INK, LABEL, MUTED, PAD, ROW, RULE, VOID, mono_font

# Frames this big are never decoded on the UI thread: an EOS film is ~9 megapixels of
# JPEG-lossless and takes a quarter of a second to decode on a fast machine, several on a
# slow one. Anything smaller (a CT slice is a quarter of a megapixel) decodes faster than
# a frame of animation and is simply fetched when you scroll onto it.
SYNC_PIXELS = 1_500_000
# While the window is being dragged, frames above this are previewed at half resolution.
PREVIEW_PIXELS = 4_000_000


def array_to_qimage(array: np.ndarray) -> QImage:
    """8-bit grey or RGB to a QImage that owns its own copy of the pixels."""
    array = np.ascontiguousarray(array)
    height, width = array.shape[:2]
    if array.ndim == 2:
        image = QImage(array.data, width, height, array.strides[0],
                       QImage.Format.Format_Grayscale8)
    else:
        image = QImage(array.data, width, height, array.strides[0],
                       QImage.Format.Format_RGB888)
    return image.copy()


class PrefetchSignal(QObject):
    frame_ready = pyqtSignal(object, int)  # source, frame index


class PrefetchJob(QRunnable):
    """Decode what the source wants next until it wants nothing, or is let go of."""

    def __init__(self, source: SliceSource, signal: PrefetchSignal):
        super().__init__()
        self.source, self.signal = source, signal

    def run(self) -> None:
        source = self.source
        try:
            while not getattr(source, "released", False):
                index = source.next_wanted()
                if index is None:
                    return
                if source.get(index) is not None or index in source._failed:
                    self.signal.frame_ready.emit(source, index)
        finally:
            source.prefetching = False


class Prefetcher(QObject):
    """One background worker per open source, on a pool kept apart from the thumbnails.

    Two threads: the item on screen and the one you are about to step to. pydicom holds the
    interpreter lock while it parses, so more threads would only take turns — and every
    turn they take is one the window is not repainting."""

    frame_ready = pyqtSignal(object, int)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.signal = PrefetchSignal()
        self.signal.frame_ready.connect(self.frame_ready)

    def ensure(self, source: SliceSource) -> None:
        """Make sure something is filling this source. Cheap to call on every scroll: a
        source already being filled re-aims itself at the new centre on its own."""
        if getattr(source, "prefetching", False) or getattr(source, "released", False):
            return
        if source.next_wanted() is None:
            return
        source.prefetching = True
        self.pool.start(PrefetchJob(source, self.signal))

    @staticmethod
    def release(source: SliceSource) -> None:
        source.released = True

    def wait(self, msecs: int = 5000) -> bool:
        return self.pool.waitForDone(msecs)


class SliceRuler(QWidget):
    """The strip under a stack: which plane, which slice, and a track you can grab.

    Key hard left (`AX`, `COR`, `SAG`), the position hard right of it, and the track filling
    the rest — a hairline with the slices already in memory drawn a step brighter and the
    one on screen as a white tick. Click or drag anywhere on the track to go there; the
    wheel over it steps. It is the readout row the rest of the app is built from, turned
    into a scrubber, and it only exists while there is a stack to scrub."""

    scrubbed = pyqtSignal(int)

    KEY_WIDTH = 104

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(ROW + 4)
        self.setMouseTracking(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.plane = "axial"
        self.index = 0
        self.count = 0
        self.held = None  # callable(index) -> bool, or None when everything is in memory
        self.setVisible(False)

    def set_state(self, plane: str, index: int, count: int, held=None) -> None:
        self.plane, self.index, self.count, self.held = plane, index, count, held
        self.setVisible(count > 1)
        self.update()

    def _track(self) -> QRect:
        return self.rect().adjusted(self.KEY_WIDTH, 0, -PAD, 0)

    def index_at(self, x: float) -> int:
        track = self._track()
        if self.count <= 1 or track.width() <= 0:
            return 0
        fraction = (x - track.left()) / max(1, track.width() - 1)
        return int(round(min(1.0, max(0.0, fraction)) * (self.count - 1)))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(VOID))
        painter.setPen(QPen(QColor(RULE), 1))
        painter.drawLine(0, 0, self.width(), 0)
        if self.count <= 1:
            return
        middle = self.height() // 2
        painter.setFont(mono_font(9))
        painter.setPen(QColor(LABEL))
        key = {"axial": "AX", "coronal": "COR", "sagittal": "SAG"}.get(self.plane, "")
        painter.drawText(QRect(PAD, 0, 40, self.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, key)
        painter.setPen(QColor(INK))
        painter.drawText(QRect(36, 0, self.KEY_WIDTH - 44, self.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                         f"{self.index + 1}/{self.count}")
        track = self._track()
        painter.setPen(QPen(QColor(RULE), 1))
        painter.drawLine(track.left(), middle, track.right(), middle)
        if self.held is not None and track.width() > 0:
            painter.setPen(QPen(QColor(MUTED), 1))
            run_start = None
            for x in range(track.left(), track.right() + 1):
                held = self.held(self.index_at(x))
                if held and run_start is None:
                    run_start = x
                if (not held or x == track.right()) and run_start is not None:
                    painter.drawLine(run_start, middle, x, middle)
                    run_start = None
        else:
            painter.setPen(QPen(QColor(MUTED), 1))
            painter.drawLine(track.left(), middle, track.right(), middle)
        x = track.left() + round(self.index / max(1, self.count - 1) * max(0, track.width() - 1))
        painter.setPen(QPen(QColor(INK), 1))
        painter.drawLine(x, middle - 4, x, middle + 4)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.count > 1:
            self.scrubbed.emit(self.index_at(event.position().x()))

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and self.count > 1:
            self.scrubbed.emit(self.index_at(event.position().x()))

    def wheelEvent(self, event) -> None:
        if self.count > 1:
            step = -1 if event.angleDelta().y() > 0 else 1
            self.scrubbed.emit(max(0, min(self.count - 1, self.index + step)))
