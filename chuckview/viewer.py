"""The loupe: one picture, or one slice of a stack, and every gesture for looking at it.

Lifted from Light Table's viewport and extended for medical images. Ordinary pictures are
QImages; a DICOM item is shown from its SliceSource — frames decoded on demand, stored raw,
windowed on the way to the screen through a lookup table. Gestures:

- LMB drag pan · RMB drag zoom (horizontal RMB drag scrubs a stack) · Ctrl+wheel zoom
- wheel, ↑ ↓, PgUp PgDn, or the slice ruler under the picture: move through a stack
- MMB drag, or Shift+LMB drag: window/level (right widens, down darkens)
- Ctrl+LMB drag: carry the picture out to another app
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QMimeData, QObject, QPoint, QRunnable, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QImage, QImageReader, QPixmap, QTransform
from PyQt6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from chuckview import dicom
from chuckview.core import THUMB_SIZE, is_image_path
from chuckview.panels import MATTE
from chuckview.stack import PREVIEW_PIXELS, SYNC_PIXELS, SliceRuler, array_to_qimage

CANVAS = QColor(MATTE)


def image_from_mime(mime: QMimeData) -> QImage | None:
    """Pull a QImage out of a drag/clipboard payload, whatever wrapper it arrives in."""
    if not mime.hasImage():
        return None
    data = mime.imageData()
    if isinstance(data, QImage):
        image = data
    elif isinstance(data, QPixmap):
        image = data.toImage()
    else:
        image = QImage(data)
    return image if not image.isNull() else None


def local_image_urls(mime: QMimeData) -> list[str]:
    """Local image file paths carried by a drag/clipboard payload, in order."""
    if not mime.hasUrls():
        return []
    return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and is_image_path(u.toLocalFile())]

def load_frames(path: str) -> list[QImage]:
    """Decode an image; multi-frame files (z-stacks, multipage TIFF) load fully into RAM."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    frames: list[QImage] = []
    count = max(1, reader.imageCount())
    for index in range(count):
        if count > 1:
            reader.jumpToImage(index)
        image = reader.read()
        if not image.isNull():
            frames.append(image)
    return frames


# Flat pictures larger than this on disk decode off the UI thread, behind their thumbnail.
ASYNC_BYTES = 8 << 20


class LoadSignal(QObject):
    done = pyqtSignal(int, str, list)  # generation, path, frames


class LoadJob(QRunnable):
    """Decode one ordinary picture in the background — a 100-megapixel TIFF must not
    freeze the window between pressing → and seeing anything."""

    def __init__(self, generation: int, path: str, signal: LoadSignal):
        super().__init__()
        self.generation, self.path, self.signal = generation, path, signal

    def run(self) -> None:
        self.signal.done.emit(self.generation, self.path, load_frames(self.path))


class Viewport(QGraphicsView):
    """The image pane: pan, zoom, z-scrub, next/prev — all gesture, no chrome."""

    request_step = pyqtSignal(int)  # +1 next image, -1 previous
    images_dropped = pyqtSignal(list)         # local image paths dropped in
    image_data_dropped = pyqtSignal(QImage)   # raw bitmap dropped in (no source file)
    context_requested = pyqtSignal(QPoint)    # right-click (not a drag): global pos
    state_changed = pyqtSignal()              # slice, plane or window moved: re-read facts

    def __init__(self):
        super().__init__()
        self.setObjectName("canvas")
        self.setScene(QGraphicsScene(self))
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self.item)
        # The matting is painted, not styled: a stylesheet background on a QGraphicsView
        # lands on the frame, not the canvas. It is the darkest surface in the app, one
        # step under everything else, because whatever surrounds a picture is being
        # compared to it whether you meant to or not.
        self.setBackgroundBrush(CANVAS)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Nothing lies on top of the picture any more, so the view is free to scroll its
        # own backing store and repaint only the sliver that moved.
        self.setAcceptDrops(True)
        self.source_path: str | None = None  # file backing the picture on screen
        self.frames: list[QImage] = []
        self.slice = 0
        self.compare_pixmap: QPixmap | None = None  # previously viewed image
        self._peeking = False
        self._drag_button: Qt.MouseButton | None = None
        self._last_pos = None
        self._rmb_press = None  # global pos of a right-button press, to tell click from drag
        self._fit_on_load = True
        self._fitted = False  # is the view still the automatic fit, or has the user zoomed?
        # A DICOM item is shown from its source: frames decoded on demand, stored raw, and
        # windowed on the way to the screen. `frames` stays empty while one is on screen.
        self.source: dicom.SliceSource | None = None
        self.plane = "axial"
        self.volume = None              # the whole stack, once held, for COR and SAG
        self.window: tuple[float, float] | None = None  # center, width
        self.preset = "auto"
        self.inverted = False           # I: flipped against what the file asks for
        self.shown_slice: int | None = None  # which slice the pixmap actually is
        self.ruler = SliceRuler()
        self.ruler.scrubbed.connect(self.set_slice)
        self._windowing = False

    # -- drag-and-drop in --------------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        paths = local_image_urls(mime)
        if paths:
            self.images_dropped.emit(paths)
            event.acceptProposedAction()
            return
        image = image_from_mime(mime)
        if image is not None:
            self.image_data_dropped.emit(image)
            event.acceptProposedAction()

    # -- drag-and-drop out -------------------------------------------------------
    def drag_out(self) -> None:
        """Carry the picture on screen into another app: the file URL for file managers
        and chat, the decoded bitmap for image editors. Ctrl+drag, because a bare left
        drag is the pan."""
        if self.source_path is None or not Path(self.source_path).is_file():
            return
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(self.source_path)])
        if self.source is not None:
            image = self.item.pixmap().toImage()  # the slice as windowed, not the file
        else:
            image = self.frames[self.slice] if self.frames else QImage(self.source_path)
        if not image.isNull():
            data.setImageData(image)
        drag = QDrag(self)
        drag.setMimeData(data)
        preview = self.item.pixmap()
        if not preview.isNull():
            preview = preview.scaled(
                QSize(THUMB_SIZE, THUMB_SIZE), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            drag.setPixmap(preview)
            drag.setHotSpot(preview.rect().center())
        drag.exec(Qt.DropAction.CopyAction)

    # -- content ---------------------------------------------------------------
    def show_frames(self, frames: list[QImage]) -> None:
        if not self.item.pixmap().isNull():
            self.compare_pixmap = self.item.pixmap()
        self.source = None
        self.volume = None
        self.plane = "axial"
        self.item.setTransform(QTransform())
        self.frames = frames
        self.slice = 0
        self._show_slice()
        if self._fit_on_load:
            self.fit()
        self._sync_ruler()

    def show_placeholder(self, image: QImage, size: QSize | None = None) -> None:
        """Hold the thumbnail up, stretched to the picture's real size, while the picture
        itself decodes — so stepping onto a big film shows *something* at once, in the
        right place, and the sharp version simply lands on top of it."""
        if not self.item.pixmap().isNull():
            self.compare_pixmap = self.item.pixmap()
        self.frames = []
        self.source = None
        self.volume = None
        self.shown_slice = None
        pixmap = QPixmap.fromImage(image)
        self.item.setPixmap(pixmap)
        if size is not None and not image.isNull() and image.width() and image.height():
            self.item.setTransform(QTransform.fromScale(size.width() / image.width(),
                                                        size.height() / image.height()))
        else:
            self.item.setTransform(QTransform())
        self.scene().setSceneRect(self.item.sceneBoundingRect())
        if self._fit_on_load:
            self.fit()
        self._sync_ruler()

    def show_source(self, source: dicom.SliceSource, preset: str = "auto",
                    keep_pixmap: bool = False) -> None:
        """Put a DICOM item on screen: the middle slice of a stack, the only frame of a film.
        `keep_pixmap` leaves a placeholder up until the first real frame is decoded."""
        if not keep_pixmap and not self.item.pixmap().isNull():
            self.compare_pixmap = self.item.pixmap()
        self.frames = []
        self.source = source
        self.volume = None
        self.plane = "axial"
        names = [name for name, _value in dicom.presets_for(source.item)]
        self.preset = preset if preset in names else "auto"
        self.window = None
        self.slice = len(source) // 2
        source.center = self.slice
        self.shown_slice = None
        self._show_slice()
        if self._fit_on_load:
            self.fit()

    # -- stack rendering -------------------------------------------------------------
    def _preset_window(self, raw, slope: float, intercept: float) -> tuple[float, float]:
        for name, value in dicom.presets_for(self.source.item):
            if name == self.preset and value is not None:
                return value
        if self.preset == "full":
            return dicom.full_window(raw, slope, intercept)
        header = self.source.item.first
        return header.window or dicom.auto_window(raw, slope, intercept)

    def _current_raw(self, decode: bool):
        """The stored pixels of what should be on screen, or None if not decoded yet."""
        source = self.source
        if self.plane != "axial" and self.volume is not None:
            return dicom.plane_slice(self.volume, self.plane, self.slice), source.rescale(0)
        raw = source.peek(self.slice)
        header = source.item.first
        if raw is None and decode and header.rows * header.cols <= SYNC_PIXELS:
            raw = source.get(self.slice)  # small enough to fetch right now
        return raw, source.rescale(self.slice)

    def render_source(self, fast: bool = False) -> bool:
        """Window the current slice onto the screen. False if it is not decoded yet — the
        prefetcher will say when it is, and the previous picture stays up until then."""
        if self.source is None:
            return False
        raw, (slope, intercept) = self._current_raw(decode=True)
        if raw is None:
            return False
        if self.window is None:
            self.window = self._preset_window(raw, slope, intercept)
        step = 2 if fast and raw.shape[0] * raw.shape[1] > PREVIEW_PIXELS else 1
        if step > 1:
            raw = raw[::step, ::step]
        center, width = self.window
        pixels = dicom.to_display(raw, slope, intercept, center, width,
                                  self.source.item.invert != self.inverted, self.source.luts)
        refit = self._fitted and self.shown_slice is None
        self.item.setPixmap(QPixmap.fromImage(array_to_qimage(pixels)))
        sx, sy = dicom.plane_aspect(self.source.item, self.plane)
        self.item.setTransform(QTransform.fromScale(sx * step, sy * step))
        self.scene().setSceneRect(self.item.sceneBoundingRect())
        self.shown_slice = self.slice
        if refit:
            self.fit()
        return True

    def frame_decoded(self, source, index: int) -> None:
        """A background decode landed. Draw it if it is the one we are waiting on."""
        if source is not self.source:
            return
        if self.plane == "axial" and index == self.slice and self.shown_slice != self.slice:
            self.render_source()
            self.state_changed.emit()
        self._sync_ruler()

    def slice_count(self) -> int:
        if self.source is not None:
            if self.plane != "axial" and self.volume is not None:
                return dicom.plane_depth(self.volume.shape, self.plane)
            return len(self.source)
        return len(self.frames)

    def _show_slice(self) -> None:
        if self.source is not None:
            if self.plane == "axial":
                self.source.center = self.slice
            self.render_source()
        elif self.frames:
            self.item.setPixmap(QPixmap.fromImage(self.frames[self.slice]))
            self.scene().setSceneRect(self.item.boundingRect())
        self._sync_ruler()

    def _sync_ruler(self) -> None:
        source = self.source
        held = None
        if source is not None and self.plane == "axial" and not source.complete():
            snapshot = set(source._cache)
            held = snapshot.__contains__
        self.ruler.set_state(self.plane, self.slice, self.slice_count(), held)

    def set_slice(self, index: int) -> None:
        count = self.slice_count()
        if count:
            index = max(0, min(count - 1, index))
            if index == self.slice and self.shown_slice == index:
                return
            self.slice = index
            self._show_slice()
            self.state_changed.emit()

    def step_slice(self, direction: int) -> None:
        self.set_slice(self.slice + direction)

    @property
    def is_stack(self) -> bool:
        return self.slice_count() > 1

    # -- window, preset, plane -----------------------------------------------------
    def cycle_preset(self) -> str | None:
        """L: the next window preset for this kind of image. Returns its name to show."""
        if self.source is None:
            return None
        names = [name for name, _value in dicom.presets_for(self.source.item)]
        self.preset = names[(names.index(self.preset) + 1) % len(names)
                            if self.preset in names else 0]
        self.window = None
        self.render_source()
        self.state_changed.emit()
        return self.preset

    def invert(self) -> None:
        """I: flip the grey scale — bone black, air white, the way some readers want films."""
        if self.source is None:
            return
        self.inverted = not self.inverted
        self.render_source()
        self.state_changed.emit()

    def drag_window(self, dx: float, dy: float, fast: bool = True) -> None:
        """Middle-drag (or Shift+left-drag): right widens the window, down raises the level
        — darker. Speed scales with the window, so it is as fine on a lung as on a film."""
        if self.source is None or self.window is None:
            return
        center, width = self.window
        rate = max(1.0, width) / 300.0
        self.window = (center + dy * rate, max(1.0, width + dx * rate))
        self.preset = "custom"
        self.render_source(fast=fast)
        self.state_changed.emit()

    def cycle_plane(self) -> str:
        """V: axial → coronal → sagittal, for a stack. The reformatted planes need every
        slice in memory; until then this says how far along that is and stays put."""
        source = self.source
        if source is None or len(source) < 2 or source.item.rgb:
            return "not a stack"
        if self.volume is None:
            if not source.fits:
                return "too large for COR/SAG here"
            volume = source.volume()
            if volume is None:
                return f"loading {source.held()}/{len(source)} slices"
            self.volume = volume
        plane = dicom.PLANES[(dicom.PLANES.index(self.plane) + 1) % len(dicom.PLANES)]
        self.plane = plane
        self.slice = dicom.plane_depth(self.volume.shape, plane) // 2
        if plane == "axial":
            self.slice = source.center
        self.shown_slice = None
        self._show_slice()
        self.fit()
        self.state_changed.emit()
        return plane

    # -- view verbs --------------------------------------------------------------
    def fit(self) -> None:
        if not self.item.pixmap().isNull():
            self.fitInView(self.item, Qt.AspectRatioMode.KeepAspectRatio)
            self._fitted = True

    def fill(self) -> None:
        if not self.item.pixmap().isNull():
            self.fitInView(self.item, Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            self._fitted = False

    def one_to_one(self) -> None:
        self.resetTransform()
        self._fitted = False

    def zoom(self, factor: float) -> None:
        self.scale(factor, factor)
        self._fitted = False

    def resizeEvent(self, event) -> None:
        """Stay fitted while the picture is untouched — the first image is loaded before
        the window has been laid out, so without this it fits to a widget of nothing. Once
        you zoom or pan, the view is yours and resizing leaves it alone."""
        super().resizeEvent(event)
        if self._fitted:
            self.fit()

    # -- gestures ------------------------------------------------------------------
    def wheelEvent(self, event) -> None:
        dx, dy = event.angleDelta().x(), event.angleDelta().y()
        if abs(dx) > abs(dy):  # horizontal wheel scrolls the picture sideways
            self.translate_view(dx, 0)
        elif self.is_stack:  # vertical scroll steps stack slices
            self.set_slice(self.slice + (-1 if dy > 0 else 1))
        else:  # vertical scroll zooms flat images at the cursor
            self.zoom(1.15 if dy > 0 else 1 / 1.15)

    def mousePressEvent(self, event) -> None:
        button = event.button()
        if (button == Qt.MouseButton.LeftButton
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.drag_out()  # Ctrl+drag lifts the picture out into another app
            return
        if self.source is not None and (
                button == Qt.MouseButton.MiddleButton
                or (button == Qt.MouseButton.LeftButton
                    and event.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
            # Window/level: the one gesture a radiograph needs that a photograph does not.
            self._windowing = True
            self._drag_button = button
            self._last_pos = event.position()
            return
        if button in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton,
                      Qt.MouseButton.ForwardButton):
            self._drag_button = button
            self._last_pos = event.position()
            if button == Qt.MouseButton.RightButton:
                self._rmb_press = event.globalPosition().toPoint()
        elif button == Qt.MouseButton.BackButton and self.compare_pixmap is not None:
            # Hold-to-compare: flash the previously viewed image while held.
            self._peeking = True
            self._current_pixmap = self.item.pixmap()
            self.item.setPixmap(self.compare_pixmap)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_button is None or self._last_pos is None:
            return super().mouseMoveEvent(event)
        delta = event.position() - self._last_pos
        self._last_pos = event.position()
        if self._windowing:
            self.drag_window(delta.x(), delta.y(), fast=True)
            return
        if self._drag_button in (Qt.MouseButton.LeftButton, Qt.MouseButton.ForwardButton):
            self.translate_view(delta.x(), delta.y())
        elif self._drag_button == Qt.MouseButton.RightButton:
            if self.is_stack and abs(delta.x()) > abs(delta.y()):
                self.set_slice(self.slice + (1 if delta.x() > 0 else -1))
            elif delta.y():
                self.zoom(1.01 ** (-delta.y()))

    def translate_view(self, dx: float, dy: float) -> None:
        self.horizontalScrollBar().setValue(round(self.horizontalScrollBar().value() - dx))
        self.verticalScrollBar().setValue(round(self.verticalScrollBar().value() - dy))

    def mouseReleaseEvent(self, event) -> None:
        if self._windowing:
            self._windowing = False
            self._drag_button = None
            self._last_pos = None
            self.render_source()  # the full-resolution frame, now that the hand is still
            return
        if event.button() == Qt.MouseButton.BackButton and self._peeking:
            self._peeking = False
            self.item.setPixmap(self._current_pixmap)
        if event.button() == Qt.MouseButton.RightButton and self._rmb_press is not None:
            moved = (event.globalPosition().toPoint() - self._rmb_press).manhattanLength()
            self._rmb_press = None
            if moved < 6:  # a click, not a zoom drag → context menu
                self.context_requested.emit(event.globalPosition().toPoint())
        self._drag_button = None
        self._last_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_PageUp) and self.is_stack:
            self.step_slice(-1 if key == Qt.Key.Key_Up else -10)
            return
        if key in (Qt.Key.Key_Down, Qt.Key.Key_PageDown) and self.is_stack:
            self.step_slice(1 if key == Qt.Key.Key_Down else 10)
            return
        if key in (Qt.Key.Key_Right,) or (key == Qt.Key.Key_Space and not event.modifiers()):
            self.request_step.emit(1)
        elif key in (Qt.Key.Key_Left,) or (
            key == Qt.Key.Key_Space and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.request_step.emit(-1)
        else:
            super().keyPressEvent(event)


