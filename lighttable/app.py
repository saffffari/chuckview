#!/usr/bin/env python3
"""Light Table — the 2D image viewer. Three columns, dark, and nothing in the way.

Left to right: **Locations** (one line to search on, places, marks, drives, the folder
tree, and the facts at the foot), **Gallery** (the recursive contact sheet) and the
**Viewer**. That order is the app: where, then which, then this one. Drag the seam
between two columns to change the proportions; there is nowhere to drag a column *to*,
because a picture browser with a rearrangeable layout is a picture browser you have to
re-learn every time you open it.

There are no buttons. There was a bar of twelve of them across the top of the sidebar and
it was the part of the app nobody touched twice: every one of them is a key you already
press or a line in a menu you already have, and all of them together cost a strip of the
one column with work to do. What is left is the search line. Verbs are the keys below,
the menu bar, and the right-click menu on a frame.

Open an image and it opens in **Focus** — the picture edge to edge, nothing else. Open a
folder (or the app on its own) and it opens on the folder and the gallery, with no viewer
taking up room until you double-click a frame.

Marks: **A** puts the selection in the Quick Collection or takes it out again, **F** flags
or unflags it. Both sets have a row in the sidebar and open in the sheet like a folder,
and both are one flat text file each — marking a picture never touches the picture.

Controls: **← and →** are the previous and the next picture from anywhere in the window,
including with the keyboard in the folder tree — the one thing this app is for cannot
depend on having clicked the right pane first. LMB pan · RMB drag zoom (horizontal RMB
scrubs z-stacks) · scroll zoom (slices on stacks) · horizontal wheel scrolls sideways ·
Space / Shift+Space next-prev ·
Ctrl+F searches the folder · MX forward button pans, back button holds a compare with the
last-viewed image · 0 fit · 9 fill · 1 = 1:1 · Ctrl+Shift+F focus · Alt+F fullscreen.

Files are files (Rule 8): drag an image out of the contact sheet — or out of the viewport
with Ctrl held — into any app (URLs plus the bitmap itself); drop images onto the sheet or
viewport to import them into the current folder; drop onto the tree to move them. Ctrl+C
copies the selection, Ctrl+V pastes an image or files in, F2 renames, Delete trashes. A
right-click (no drag) opens the standard context menu, including Reveal in File Manager.

The material is PC-6's: a black ground, white monospaced small capitals, and a hairline
wherever two things have to be told apart. No slabs, no radii, no shadows. The facts are
a readout — a three-letter key at the left, the value hard right, a rule under the row —
and the picture is the only bright thing on screen and the only thing in colour, except
for the one orange hairline that means *this one is selected*.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import (
    QDir, QEvent, QMimeData, QModelIndex, QObject, QPoint, QPointF, QRect, QRectF,
    QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtCore import QFile
from PyQt6.QtGui import (
    QAction, QColor, QDrag, QFileSystemModel, QFont, QGuiApplication, QIcon, QImage,
    QImageReader, QKeySequence, QPainter, QPen, QPixmap, QPolygonF, QShortcut, QTransform,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QGraphicsPixmapItem,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QStyle, QStyleOptionViewItem,
    QPushButton, QSizePolicy, QSplitter, QStyledItemDelegate, QTabWidget, QToolButton,
    QTreeView, QVBoxLayout, QWidget,
)

from lighttable.common import HOUSE_STYLE, INK, SELECT, configure_application
from lighttable.core import (
    COLLECTIONS, THUMB_SIZE, add_favorite, cache_dir, collection_members,
    collection_title, filter_paths, is_image_path, load_collection, load_favorites,
    load_marks, mounted_drives, open_path_command, pasted_image_name, plan_import,
    remove_favorite, reveal_command, save_collection, scan_images, thumb_path_for,
    toggle_collection, trash_command, tree_anchor, unique_destination,
)
from lighttable import dicom
from lighttable.panels import (
    EDGE, GUTTER, LABEL, MATTE, MONO, MUTED, PAD, RULE,
    STYLE as PANEL_STYLE, VOID, ColumnHead, Panel, PanelState, Readout,
    column_sizes, load_layout, mono_font, save_layout,
)
from lighttable.stack import (
    PREVIEW_PIXELS, SYNC_PIXELS, Prefetcher, SliceRuler, array_to_qimage,
)

# --- The material -----------------------------------------------------------------------
# Black, white, and a hairline. The app is one unbroken black ground; the photograph is
# the only bright thing on it and the only thing in colour. Where two surfaces have to be
# told apart there is a 1px rule and nothing else — no slab a shade lighter, no border
# box, no rounded corner, no shadow. This is the material PC-6 is made of, and the reason
# is the same in both: a readout should look like it is etched into the panel, not like a
# card lying on one.
#
# Every letter of chrome is monospaced small capitals so keys and values line up in a
# column. Orange is selection and only ever selection, and here it is a hairline too.
CHROME = QColor(VOID)     # the window itself (painted, not styled)
CANVAS = QColor(MATTE)    # the matting around the picture (painted, not styled)

MATERIAL_STYLE = f"""
* {{ font-family: {MONO}; }}
QMainWindow, QWidget#shell, QWidget#tab {{ background: {VOID}; }}
QMainWindow::separator {{ background: {VOID}; width: 0; height: 0; }}

QMenuBar {{ background: {VOID}; color: {MUTED}; padding: 0 {PAD - 4}px;
            border-bottom: 1px solid {RULE}; font-size: 9px; }}
QMenuBar::item {{ background: transparent; padding: 5px 9px; }}
QMenuBar::item:selected {{ color: {INK}; }}
QMenu {{ background: {VOID}; border: 1px solid {RULE}; padding: 3px 0; color: {INK};
         font-size: 10px; }}
QMenu::item {{ padding: 5px 20px 5px 12px; }}
QMenu::item:selected {{ background: {RULE}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::separator {{ background: {RULE}; height: 1px; margin: 3px 0; }}

QTabWidget::pane {{ border: 0; background: {VOID}; }}
QTabBar {{ background: {VOID}; }}
QTabBar::tab {{ background: {VOID}; color: {MUTED}; border: 0;
                border-bottom: 1px solid {VOID}; padding: 5px 12px;
                font-size: 9px; letter-spacing: 1px; }}
QTabBar::tab:selected {{ color: {INK}; border-bottom: 1px solid {INK}; }}

/* The three columns. Each is the same black; the rule under a column head and the
   hairline splitter between two of them are the whole of the structure. */
QListWidget#places, QTreeView#folders, QListWidget#sheet, QWidget#panelBody {{
    background: {VOID}; border: 0; color: {INK}; font-size: 10px;
    outline: 0; show-decoration-selected: 0; }}
QListWidget#places::item, QTreeView#folders::item {{ padding: 3px 2px; border: 0; }}
QListWidget#places::item:selected, QTreeView#folders::item:selected {{
    background: transparent; color: {SELECT}; }}
QListWidget#places::item:hover, QTreeView#folders::item:hover {{ color: {INK}; }}
QListWidget#sheet::item:selected {{ background: transparent; }}
QTreeView#folders {{ color: {MUTED}; }}
QLabel#muted {{ background: transparent; color: {MUTED}; font-size: 9px; }}
QGraphicsView#canvas {{ background: {MATTE}; border: 0; }}
QSplitter {{ background: {VOID}; }}

/* The one control left in the app. A line to type on, with a rule under it — the same
   rule as every other row, so the field reads as part of the readout, not as a box. */
QLineEdit#search {{ background: {VOID}; border: 0; border-bottom: 1px solid {RULE};
                    padding: 5px {PAD}px; color: {INK}; font-size: 10px;
                    letter-spacing: 0.5px; selection-background-color: {SELECT};
                    selection-color: {VOID}; }}
QLineEdit#search:focus {{ border-bottom: 1px solid {SELECT}; }}

QScrollBar:vertical {{ background: {VOID}; width: 6px; margin: 0; }}
QScrollBar:horizontal {{ background: {VOID}; height: 6px; margin: 0; }}
QScrollBar::handle {{ background: {RULE}; }}
QScrollBar::handle:hover {{ background: {EDGE}; }}
QScrollBar::handle:vertical {{ min-height: 24px; }}
QScrollBar::handle:horizontal {{ min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: {VOID}; }}

QLabel#toast {{ background: {VOID}; border: 1px solid {RULE}; padding: 6px 12px;
                color: {INK}; font-size: 9px; letter-spacing: 1.5px; }}
QToolTip {{ color: {INK}; background: {VOID}; border: 1px solid {RULE}; padding: 3px 6px;
            font-size: 9px; }}
QInputDialog, QMessageBox {{ background: {VOID}; color: {INK}; }}
QInputDialog QLineEdit {{ background: {VOID}; border: 1px solid {RULE}; padding: 4px;
                          color: {INK}; }}
QPushButton {{ background: {VOID}; border: 1px solid {RULE}; padding: 4px 12px;
               color: {INK}; font-size: 9px; letter-spacing: 1px; }}
QPushButton:hover {{ border-color: {INK}; }}
""" + PANEL_STYLE


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

HERE = Path(__file__).resolve().parent
ICON = HERE / "assets" / "Lt.svg"
# The desktop entry install-default.sh writes, and the one the shortcut on the desktop
# uses. Naming it in the app is what ties a running window back to its launcher.
DESKTOP_ENTRY = "2photon-lighttable"

PATH_ROLE = Qt.ItemDataRole.UserRole
FAVORITE_ROLE = Qt.ItemDataRole.UserRole + 1    # pinned by the user vs. a live mount
COLLECTION_ROLE = Qt.ItemDataRole.UserRole + 2  # a place that is a set, not a folder
MARKS_ROLE = Qt.ItemDataRole.UserRole + 3       # which sets this thumbnail is in
LABEL_ROLE = Qt.ItemDataRole.UserRole + 4       # "CT 240", "DX": what a DICOM frame is

# The three columns, in the only order they are ever in, and the width each starts at on a
# machine that has never run Light Table. `open` here is the browsing arrangement —
# locations and gallery, no viewer — which is what opening a *folder* gives you; opening
# an image overrides it with Focus. The rightmost open column absorbs the rest of the
# window, so these are two sidebar widths and a hint, not a carve-up.
COLUMNS = ("locations", "gallery", "viewer")
DEFAULT_PANELS = (
    PanelState("locations", width=260, open=True),
    PanelState("gallery", width=520, open=True),
    PanelState("viewer", width=760, open=False),
)
# The viewer's is None on purpose: see Panel. The other two are narrow columns of similar
# content and the label is what tells them apart at a glance.
PANEL_TITLES = {"locations": "Locations", "gallery": "Gallery", "viewer": None}


class MarkDelegate(QStyledItemDelegate):
    """Draws the flag and the quick-collection dot on a thumbnail.

    Marks have to be visible on the sheet or the keys are a guess: you press F and nothing
    on screen changes. Both glyphs are off-white on a dark disc — the disc is what keeps
    them readable over a white photograph — and never orange, which in this house means
    selection and only ever selection."""

    SIZE = 15

    def paint(self, painter, option, index) -> None:
        # Qt would fill the whole cell with the selection colour. A block of orange the
        # size of a photograph is not a mark on a photograph, it is a replacement for
        # one — so the fill is taken away here and the selection is drawn as a hairline
        # box around the frame instead, the same weight as every other rule in the app.
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        plain = QStyleOptionViewItem(option)
        if selected:
            plain.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, plain, index)
        if selected:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(SELECT), 1))
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
            painter.restore()
        label = index.data(LABEL_ROLE)
        if label:
            # What a DICOM frame is, bottom left: a CT stack and an X-ray are both one
            # grey rectangle at thumbnail size, and the depth of a stack is invisible.
            painter.save()
            font = mono_font(8, spacing=0.6)
            painter.setFont(font)
            width = painter.fontMetrics().horizontalAdvance(label) + 8
            tag = QRect(option.rect.left() + 5, option.rect.bottom() - 18, width, 13)
            painter.fillRect(tag, QColor(8, 8, 8, 200))
            painter.setPen(QColor(242, 242, 242, 235))
            painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, label)
            painter.restore()
        marks = index.data(MARKS_ROLE) or ()
        if not marks:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        spot = QRect(option.rect.left() + 5, option.rect.top() + 5, self.SIZE, self.SIZE)
        for mark in ("flagged", "quick"):
            if mark not in marks:
                continue
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(8, 8, 8, 190))
            painter.drawEllipse(spot)
            inner = QRectF(spot.adjusted(4, 4, -4, -4))
            if mark == "flagged":
                # A pennant: the flag you put on a frame you are keeping.
                pennant = QPolygonF([
                    inner.topLeft(),
                    QPointF(inner.right(), inner.top() + inner.height() * 0.25),
                    QPointF(inner.left(), inner.top() + inner.height() * 0.5),
                    inner.bottomLeft(),
                ])
                painter.setBrush(QColor(242, 242, 242, 235))
                painter.drawPolygon(pennant)
            else:
                # A ring: on the shelf, not yet decided.
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(242, 242, 242, 235), 1.6))
                painter.drawEllipse(inner)
            spot.translate(self.SIZE + 4, 0)
        painter.restore()


class ThumbSignal(QObject):
    done = pyqtSignal(int, str, str)  # row, source path, thumbnail file


class ThumbJob(QRunnable):
    """Decode one thumbnail into the persistent cache (all P-cores welcome)."""

    def __init__(self, row: int, path: str, signal: ThumbSignal,
                 item: dicom.Item | None = None):
        super().__init__()
        self.row, self.path, self.signal, self.item = row, path, signal, item

    def run(self) -> None:
        target = thumb_path_for(self.path)
        if target is None:
            return
        if not target.exists() and self.item is not None:
            image = dicom_thumbnail(self.item)
            if image is None:
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(str(target), "PNG")
        elif not target.exists():
            reader = QImageReader(self.path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and size.width() > 0:
                scale = THUMB_SIZE / max(size.width(), size.height())
                if scale < 1:
                    reader.setScaledSize(QSize(round(size.width() * scale), round(size.height() * scale)))
            image = reader.read()
            if image.isNull():
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(str(target), "PNG")
        self.signal.done.emit(self.row, self.path, str(target))


def dicom_thumbnail(item: dicom.Item) -> QImage | None:
    """The middle frame of a DICOM item in its own window, at thumbnail size. Only that one
    frame is decoded — a 400-slice CT costs one slice to put on the sheet."""
    middle = item.count // 2
    path, frame = item.frames[middle]
    try:
        raw = dicom.decode_frame(path, frame)
    except Exception:  # noqa: BLE001 - an undecodable file just gets no thumbnail
        return None
    header = item.headers[middle] if len(item.headers) == item.count else item.first
    center, width = header.window or dicom.auto_window(raw, header.slope, header.intercept)
    image = array_to_qimage(dicom.to_display(raw, header.slope, header.intercept,
                                             center, width, item.invert))
    sx, sy = dicom.plane_aspect(item, "axial")
    w, h = image.width() * sx, image.height() * sy
    scale = min(1.0, THUMB_SIZE / max(w, h))
    return image.scaled(max(1, round(w * scale)), max(1, round(h * scale)),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


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


class ContactSheet(QListWidget):
    """The recursive gallery. RMB drag resizes thumbnails; a plain RMB click opens
    the context menu. Drag images out (URLs + bitmap) or drop images in to import."""

    images_dropped = pyqtSignal(list)         # local image paths dropped in
    image_data_dropped = pyqtSignal(QImage)   # raw bitmap dropped in (no source file)
    context_requested = pyqtSignal(QPoint)    # right-click (not a resize drag): global pos

    def __init__(self):
        super().__init__()
        self.setObjectName("sheet")
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setWordWrap(False)
        # Shift-click = consecutive range, Ctrl-click = toggle one — Qt's extended mode.
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setDropIndicatorShown(False)
        self.thumb = 128
        self._apply_size()
        self.setItemDelegate(MarkDelegate(self))
        # Selection is a ring, not a block. A filled orange cell the size of a
        # photograph replaces the photograph; the ring is drawn by the delegate on the
        # cell's own padding, which is black, so it never lands on the picture and never
        # gets lost against a light edge.
        self.setStyleSheet(
            f"QListWidget::item{{color:{MUTED}; border:0; padding:3px;}}"
            f"QListWidget::item:selected{{background:transparent; color:{INK};}}")
        self._rmb_last = None          # local pos of the last right-button event
        self._rmb_press = None         # global pos of the right-button press
        self._rmb_moved = False        # did the right button drag (resize) or just click?

    def _apply_size(self) -> None:
        self.setIconSize(QSize(self.thumb, self.thumb))
        self.setGridSize(QSize(self.thumb + 12, self.thumb + 12))

    def selected_paths(self) -> list[str]:
        items = self.selectedItems() or ([self.currentItem()] if self.currentItem() else [])
        return [item.data(PATH_ROLE) for item in items if item is not None]

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._rmb_last = event.position()
            self._rmb_press = event.globalPosition().toPoint()
            self._rmb_moved = False
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._rmb_last is not None:
            delta = event.position().y() - self._rmb_last.y()
            self._rmb_last = event.position()
            if abs(delta) > 0:
                self._rmb_moved = True
            self.thumb = max(48, min(512, round(self.thumb * (1.01 ** -delta))))
            self._apply_size()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton and self._rmb_press is not None:
            press, local = self._rmb_press, self._rmb_last
            self._rmb_last = self._rmb_press = None
            if not self._rmb_moved:
                item = self.itemAt(local.toPoint()) if local is not None else None
                if item is not None and not item.isSelected():
                    self.setCurrentItem(item)  # right-click an unselected item selects it
                self.context_requested.emit(press)
            return
        super().mouseReleaseEvent(event)

    def mimeData(self, items) -> QMimeData:
        data = QMimeData()
        paths = [item.data(PATH_ROLE) for item in items]
        data.setUrls([QUrl.fromLocalFile(p) for p in paths])
        if len(paths) == 1:  # a single image also carries its bitmap for image editors
            image = QImage(paths[0])
            if not image.isNull():
                data.setImageData(image)
        return data

    def startDrag(self, supported_actions) -> None:
        """Start an explicit URL drag that works across Wayland applications."""
        items = self.selectedItems()
        if not items and self.currentItem() is not None:
            items = [self.currentItem()]
        if not items:
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData(items))
        icon = items[0].icon().pixmap(self.iconSize())
        if not icon.isNull():
            drag.setPixmap(icon)
            drag.setHotSpot(icon.rect().center())
        drag.exec(Qt.DropAction.CopyAction)

    # -- drag-and-drop in --------------------------------------------------------
    def _wants(self, event) -> bool:
        return event.source() is not self and (
            event.mimeData().hasUrls() or event.mimeData().hasImage())

    def dragEnterEvent(self, event) -> None:
        if self._wants(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._wants(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.source() is self:  # never reorder — order is by modified time
            return
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


class FolderTree(QTreeView):
    """Directory tree that accepts image drops: dropping moves the files.
    Right-clicking a folder asks the tab for the folder menu (favorite, reveal, …)."""

    files_moved = pyqtSignal()
    folder_menu_requested = pyqtSignal(Path, QPoint)

    def __init__(self):
        super().__init__()
        self.setObjectName("folders")
        self.setAcceptDrops(True)

    def contextMenuEvent(self, event) -> None:
        index = self.indexAt(event.pos())
        if not index.isValid():
            return
        self.setCurrentIndex(index)
        self.folder_menu_requested.emit(
            Path(self.model().filePath(index)), event.globalPos())

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        index = self.indexAt(event.position().toPoint())
        if not (index.isValid() and event.mimeData().hasUrls()):
            return
        target = Path(self.model().filePath(index))
        moved = False
        for url in event.mimeData().urls():
            source = Path(url.toLocalFile())
            destination = target / source.name
            if source.is_file() and not destination.exists():
                try:
                    shutil.move(str(source), str(destination))
                    moved = True
                except OSError:
                    pass
        if moved:
            event.acceptProposedAction()
            self.files_moved.emit()


class Places(QListWidget):
    """The top half of the locations column: the two sets of marks, then pinned favorites,
    then every drive that is mounted right now. Favorites are yours (right-click a folder
    anywhere to pin it); drives are read off the mount table, so plugging a disk in makes
    it show up; the sets are always there, because a collection with nothing in it is
    where you put the first thing."""

    folder_selected = pyqtSignal(Path)
    collection_selected = pyqtSignal(str)
    favorites_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("places")
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # The selected place is the one written in orange. Nothing in this app is told
        # apart by a filled rectangle any more, and the sidebar is the place that proves
        # it: a list of one-line answers, one of which is lit.
        self.setStyleSheet(
            f"QListWidget::item{{padding:4px 9px; border:0;}}"
            f"QListWidget::item:hover{{color:{INK};}}"
            f"QListWidget::item:selected{{background:transparent; color:{SELECT};}}"
        )
        self.itemActivated.connect(self._activate)
        self.itemClicked.connect(self._activate)
        self.reload()

    # -- contents ----------------------------------------------------------------
    def reload(self) -> None:
        """Rebuild from the two mark files, the favorites file, and the live mount table,
        keeping the selection where it was if that place is still on the list."""
        current, collection = self.current_path(), self.current_collection()
        self.clear()
        self._add_heading("COLLECTIONS")
        for name in COLLECTIONS:
            self._add_collection(name)
        self._add_heading("PLACES")
        for folder in load_favorites():
            if folder.is_dir():
                label = "Home" if folder == Path.home() else (folder.name or str(folder))
                self._add_place(label, folder, favorite=True)
        drives = mounted_drives()
        if drives:
            self._add_heading("DRIVES")
            for label, path in drives:
                self._add_place(label, path, favorite=False)
        if collection is not None:
            self.select_collection(collection)
        elif current is not None:
            self.select_path(current)

    def _add_place(self, label: str, path: Path, favorite: bool) -> None:
        item = QListWidgetItem(label)
        item.setData(PATH_ROLE, str(path))
        item.setData(FAVORITE_ROLE, favorite)
        item.setToolTip(str(path))
        self.addItem(item)

    def _add_collection(self, name: str) -> None:
        """One row per set, with what is in it right now. The count is the whole file,
        including anything on a drive that is not plugged in — the set did not shrink
        because you unplugged it."""
        count = len(load_collection(name))
        item = QListWidgetItem(f"{collection_title(name)}   {count}" if count
                               else collection_title(name))
        item.setData(COLLECTION_ROLE, name)
        item.setToolTip(f"{count} images · A adds to the quick collection, F flags")
        self.addItem(item)

    def _add_heading(self, text: str) -> None:
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setForeground(QColor(LABEL))
        font = item.font()
        font.setPointSizeF(max(6.0, font.pointSizeF() - 2))
        font.setBold(True)
        item.setFont(font)
        self.addItem(item)

    # -- selection ---------------------------------------------------------------
    def current_path(self) -> Path | None:
        item = self.currentItem()
        data = item.data(PATH_ROLE) if item is not None else None
        return Path(data) if data else None

    def current_collection(self) -> str | None:
        item = self.currentItem()
        return item.data(COLLECTION_ROLE) if item is not None else None

    def select_path(self, folder: Path) -> None:
        """Highlight the place holding this folder, or clear the highlight if none does."""
        for row in range(self.count()):
            item = self.item(row)
            data = item.data(PATH_ROLE)
            if data and Path(data) == folder:
                self.setCurrentItem(item)
                return
        self.setCurrentItem(None)

    def select_collection(self, name: str) -> None:
        for row in range(self.count()):
            item = self.item(row)
            if item.data(COLLECTION_ROLE) == name:
                self.setCurrentItem(item)
                return
        self.setCurrentItem(None)

    def _activate(self, item: QListWidgetItem) -> None:
        collection = item.data(COLLECTION_ROLE)
        if collection:
            self.collection_selected.emit(collection)
            return
        data = item.data(PATH_ROLE)
        if data and Path(data).is_dir():
            self.folder_selected.emit(Path(data))

    # -- pinning -----------------------------------------------------------------
    def contextMenuEvent(self, event) -> None:
        item = self.itemAt(event.pos())
        if item is None:
            return
        collection = item.data(COLLECTION_ROLE)
        if collection:
            menu = QMenu(self)
            menu.addAction("Open").triggered.connect(
                lambda: self.collection_selected.emit(collection))
            menu.addSeparator()
            empty = menu.addAction(f"Empty {collection_title(collection)}")
            empty.setEnabled(bool(load_collection(collection)))
            empty.triggered.connect(lambda: self._empty(collection))
            menu.exec(event.globalPos())
            return
        data = item.data(PATH_ROLE)
        if not data:
            return
        path, pinned = Path(data), bool(item.data(FAVORITE_ROLE))
        menu = QMenu(self)
        menu.addAction("Open").triggered.connect(lambda: self.folder_selected.emit(path))
        menu.addSeparator()
        if pinned:
            menu.addAction("Remove from Favorites").triggered.connect(
                lambda: self._unpin(path))
        else:
            menu.addAction("Add to Favorites").triggered.connect(lambda: self._pin(path))
        menu.exec(event.globalPos())

    def _empty(self, name: str) -> None:
        """Clear a set. Worth an "are you sure": the pictures are untouched, but the pass
        you made over them is not, and there is no undo for a decision."""
        title = collection_title(name)
        confirm = QMessageBox.question(
            self, f"Empty {title}",
            f"Take all {len(load_collection(name))} images out of {title}?\n"
            "The files themselves are not touched.")
        if confirm == QMessageBox.StandardButton.Yes:
            save_collection(name, [])
            self.favorites_changed.emit()  # the sidebar and the marks both need rebuilding

    def _pin(self, folder: Path) -> None:
        add_favorite(folder)
        self.favorites_changed.emit()

    def _unpin(self, folder: Path) -> None:
        remove_favorite(folder)
        self.favorites_changed.emit()


class Tab(QWidget):
    """One tab: three columns side by side, and the folder they are all looking at.

    A tab owns a folder (or one of the two sets of marks), which of the three columns are
    open, and how wide they are. Two tabs can be on the same folder with different columns
    open, which is the point of having tabs at all — one to cull in, one to look in."""

    panes_changed = pyqtSignal()
    favorites_changed = pyqtSignal()
    marks_changed = pyqtSignal()
    search_typed = pyqtSignal(str)

    def __init__(self, start_dir: Path, layout_states: dict[str, PanelState] | None = None):
        super().__init__()
        self.setObjectName("tab")
        self.pool = QThreadPool.globalInstance()
        self.signal = ThumbSignal()
        self.signal.done.connect(self._thumb_ready)
        self.all_paths: list[str] = []   # everything under the folder
        self.paths: list[str] = []       # what the search leaves on the sheet
        self.filter_text = ""
        self.collection: str | None = None  # showing a set of marks instead of a folder
        self.marks = load_marks()           # membership, cached: the sheet asks per thumb
        self._syncing = False

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot)
        self.model.setRootPath(str(Path.home()))
        self.tree = FolderTree()
        self.tree.setModel(self.model)
        self.tree.files_moved.connect(self.rescan)
        self.tree.folder_menu_requested.connect(self.show_folder_menu)
        self.tree_root: Path | None = None
        for column in range(1, self.model.columnCount()):
            self.tree.hideColumn(column)
        self.tree.setHeaderHidden(True)
        self.tree.selectionModel().currentChanged.connect(self._folder_changed)
        self.places = Places()
        self.places.folder_selected.connect(self.set_folder)
        self.places.collection_selected.connect(self.set_collection)
        self.places.favorites_changed.connect(self._favorites_changed)

        self.sheet = ContactSheet()
        self.sheet.currentRowChanged.connect(self._image_selected)
        self.sheet.images_dropped.connect(self.import_and_view)
        self.sheet.image_data_dropped.connect(self.save_and_view_image)
        self.sheet.context_requested.connect(self.show_context_menu)
        # Single click picks a frame; double click is the one that costs you a column, so
        # it is the one that opens the viewer.
        self.sheet.itemDoubleClicked.connect(lambda _item: self.open_current())

        self.view = Viewport()
        self.view.request_step.connect(self.step)
        self.view.images_dropped.connect(self.import_and_view)
        self.view.image_data_dropped.connect(self.save_and_view_image)
        self.view.context_requested.connect(self.show_context_menu)

        # Search: one line at the top of the sidebar, under the column head. It belongs
        # to the tab and not to the window because the question is per folder — two tabs
        # are two folders and two different things being looked for in them.
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("SEARCH")
        self.search.setFont(mono_font(10, spacing=0.5))
        self.search.textChanged.connect(self._typed)

        # Facts: folder above image, at the foot of the sidebar where the rest of the
        # answers about "where am I" already are. W shows and hides it. Both are readouts
        # now — key left, value right, a rule per row — and not two paragraphs of grey.
        self.folder_info = Readout()
        self.image_info = Readout()
        self.facts = self._facts_panel()
        self.facts.setVisible(False)

        # The sidebar is itself split top/bottom: pinned places, the two sets of marks and
        # the drives above, the live folder tree below. Drag the handle to trade one for
        # the other — the same rule as the columns, one dimension down.
        self.location_split = QSplitter(Qt.Orientation.Vertical)
        self.location_split.setObjectName("sidebarSplit")
        self.location_split.setHandleWidth(GUTTER)
        self.location_split.setChildrenCollapsible(False)
        self.location_split.addWidget(self.places)
        self.location_split.addWidget(self.tree)
        self.location_split.setSizes([300, 400])
        self.location_split.setStretchFactor(1, 1)

        sidebar = QWidget()
        sidebar.setObjectName("panelBody")
        stack = QVBoxLayout(sidebar)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)
        stack.addWidget(self.location_split, 1)
        stack.addWidget(self.facts, 0)

        # The three columns, in the only order they come in. The splitter is the whole
        # layout engine: it sizes, it does not rearrange, and Qt gives us the drag handles
        # and the minimums for free.
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("columns")
        self.splitter.setHandleWidth(GUTTER)
        self.splitter.setChildrenCollapsible(False)
        self.panels: dict[str, Panel] = {}
        for name, content, header in (("locations", sidebar, self.search),
                                      ("gallery", self.sheet, None),
                                      ("viewer", self.view, None)):
            panel = Panel(name, PANEL_TITLES[name], content, header, self)
            self.panels[name] = panel
            self.splitter.addWidget(panel)
        self.splitter.setStretchFactor(COLUMNS.index("viewer"), 1)
        self.splitter.splitterMoved.connect(self._widths_changed)

        frame = QVBoxLayout(self)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setSpacing(0)
        frame.addWidget(self.splitter, 1)

        # The old names for the two sidebars, because they are still what the rest of the
        # app (and the tests) call them.
        self.location_column = self.panels["locations"]
        self.gallery_column = self.panels["gallery"]

        self.toast = QLabel("", self)
        self.toast.setObjectName("toast")
        self.toast.setVisible(False)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self.toast.setVisible(False))

        self.apply_layout(dict(layout_states or {state.name: state
                                                 for state in DEFAULT_PANELS}))
        self.set_folder(start_dir)

    def _facts_panel(self) -> QWidget:
        """Folder facts over image facts, at the foot of the sidebar. W toggles it.

        Two readouts stacked, each row a key and a value over a hairline — the same
        instrument the hardware monitor uses, because the question is the same one: what
        is in front of me right now, in as few characters as it can be said in."""
        box = QWidget()
        box.setObjectName("panelBody")
        facts = QVBoxLayout(box)
        facts.setContentsMargins(0, 0, 0, 0)
        facts.setSpacing(0)
        facts.addWidget(self.folder_info, 0)
        facts.addWidget(self.image_info, 0)
        return box

    def _typed(self, text: str) -> None:
        """The search line, live. Filter this tab, then say so — the window decides
        whether a result nobody can see is worth opening the gallery for."""
        self.set_filter(text)
        self.search_typed.emit(text)

    # -- the columns -------------------------------------------------------------
    def relayout(self) -> None:
        """Show the open columns, hide the closed ones, and give the splitter its widths.

        This is the one place geometry is decided. Nothing else sets a column's size, so
        what is on screen and what gets saved can never disagree."""
        ordered = [self.panels[name].state for name in COLUMNS]
        for name in COLUMNS:
            panel = self.panels[name]
            panel.setVisible(panel.state.open)
        self.splitter.setSizes(column_sizes(ordered, max(1, self.splitter.width())))

    def resizeEvent(self, event) -> None:
        """Re-assert the widths. Left to itself a splitter divides new space between all
        of its children in proportion, which would fatten the sidebar every time the
        window grew; here the sidebars keep the width they were given and the rightmost
        open column takes the difference, so a wider window is a bigger picture."""
        super().resizeEvent(event)
        self.relayout()

    def _widths_changed(self, *args) -> None:
        """The user dragged a seam. Take the new widths back into the states, so the
        arrangement that gets saved is the one that is on screen.

        The rightmost open column is skipped: its width is whatever is left over, not a
        setting, and recording it would mean the gallery remembered the full width of the
        window from when it had no viewer beside it.

        A tab that is not on screen is skipped entirely. Qt gives an unshown widget a
        nominal size, and a splitter asked to divide it hands every column its minimum —
        so reading widths off a tab being built would save a sidebar squeezed to 150px as
        if that were the arrangement you chose."""
        if not self.isVisible():
            return
        sizes = self.splitter.sizes()
        open_names = [name for name in COLUMNS if self.panels[name].state.open]
        for name, size in zip(COLUMNS, sizes):
            panel = self.panels[name]
            if panel.state.open and size > 0 and name != open_names[-1]:
                panel.state = replace(panel.state, width=size)

    def panel_states(self) -> list[PanelState]:
        self._widths_changed()
        return [self.panels[name].state for name in COLUMNS]

    def apply_layout(self, states: dict[str, PanelState]) -> None:
        for name, panel in self.panels.items():
            if name in states:
                panel.state = states[name]
        self.relayout()

    def flash(self, message: str) -> None:
        """Say what just happened, over the picture, briefly.

        In Focus there is no sheet and no sidebar, so pressing A or F would otherwise
        change something you cannot see. This is the receipt, and it sits over whichever
        column is furthest right — the picture if there is one, the sheet otherwise —
        because that is where you were looking when you pressed the key."""
        self.toast.setText(message)
        self.toast.adjustSize()
        open_panels = [self.panels[name] for name in COLUMNS if self.panels[name].state.open]
        over = open_panels[-1].geometry() if open_panels else self.rect()
        self.toast.move(over.center().x() - self.toast.width() // 2,
                        over.bottom() - self.toast.height() - 28)
        self.toast.setVisible(True)
        self.toast.raise_()
        self._toast_timer.start(1500)

    # -- data flow ---------------------------------------------------------------
    def set_folder(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.collection = None  # a folder and a set of marks are not both on screen
        self._reroot(self.folder)
        index = self.model.index(str(self.folder))
        self._syncing = True  # moving the tree cursor must not re-enter set_folder
        try:
            if index.isValid():
                self.tree.setCurrentIndex(index)
                self.tree.expand(index)
                self.tree.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
            self.places.select_path(self.folder)
        finally:
            self._syncing = False
        self.rescan()

    def _reroot(self, folder: Path) -> None:
        """Show the folder under its own drive (or home), not under the whole disk —
        that is what makes citadel and a USB stick browsable from the same tree."""
        anchor = tree_anchor(folder)
        if anchor == self.tree_root:
            return
        self.model.setRootPath(str(anchor))
        self.tree.setRootIndex(self.model.index(str(anchor)))
        self.tree_root = anchor

    def _folder_changed(self, index: QModelIndex) -> None:
        if self._syncing or not index.isValid():
            return
        self.folder = Path(self.model.filePath(index))
        self.places.select_path(self.folder)
        self.rescan()

    def set_collection(self, name: str) -> None:
        """Show one of the sets of marks in place of a folder.

        The folder underneath does not change: it is still where a paste or a drop lands,
        because a collection is a list of pictures, not a place to put one."""
        self.collection = name
        self._syncing = True
        try:
            self.places.select_collection(name)
        finally:
            self._syncing = False
        self.rescan()

    def rescan(self) -> None:
        """Re-read what is on screen — the folder from disk, or one of the sets from its
        file — then show it through the current search."""
        if self.collection is not None:
            self.all_paths = collection_members(self.collection)
        else:
            self.all_paths = [path for _mtime, path in scan_images(self.folder)]
        self._fill_sheet()

    # -- marks: the quick collection and the flags -------------------------------
    def toggle_mark(self, kind: str) -> None:
        """A and F, for the selection on the sheet — or, in Focus, for the one picture on
        screen, which is the only thing selected when there is no sheet to select on."""
        paths = self.selected_paths() or ([self.current_path()] if self.current_path() else [])
        if not paths:
            return
        _updated, added = toggle_collection(kind, paths)
        verb = "added to" if added else "removed from"
        count = f"{len(paths)} images" if len(paths) > 1 else Path(paths[0]).name
        self.flash(f"{count} {verb} {collection_title(kind)}")
        self.marks_changed.emit()

    def refresh_marks(self) -> None:
        """Re-read both sets and show them: badges on the sheet, counts in the sidebar.

        Looking at one of the sets is the one case that needs a real rescan — taking a
        picture out of the quick collection while the quick collection is what you are
        looking at has to actually remove it from the sheet."""
        self.marks = load_marks()
        if self.collection is not None:
            self.rescan()
        else:
            self._mark_sheet()
        self.reload_places()
        self.describe_current()

    def _mark_sheet(self) -> None:
        """Push membership onto the thumbnails for the delegate to draw."""
        for row in range(self.sheet.count()):
            item = self.sheet.item(row)
            path = item.data(PATH_ROLE)
            item.setData(MARKS_ROLE, tuple(
                name for name, members in self.marks.items() if path in members))
        self.sheet.viewport().update()

    def set_filter(self, query: str) -> None:
        """Narrow the sheet by name. The folder is already scanned, so this never
        touches the disk — it is the same images, fewer of them."""
        query = query.strip()
        if query == self.filter_text:
            return
        self.filter_text = query
        self._fill_sheet()

    def _fill_sheet(self) -> None:
        self.sheet.clear()
        # In a folder the sheet is recursive, so a subfolder is part of an image's name to
        # search by. A set of marks spans folders and has no root to be "below", so there
        # the name is all there is to match on.
        root = None if self.collection is not None else self.folder
        self.paths = filter_paths(self.all_paths, self.filter_text, root)
        # A full-size transparent placeholder, not an empty QIcon: with uniform item
        # sizes the view caches the decoration size off the first icon it sees, so an
        # empty one makes every thumbnail that lands later draw as a speck. Only ever
        # visible on a cold cache — which is exactly the first look at a new folder.
        blank = QPixmap(self.sheet.iconSize())
        blank.fill(Qt.GlobalColor.transparent)
        placeholder = QIcon(blank)
        for row, path in enumerate(self.paths):
            item = QListWidgetItem(placeholder, "")
            item.setData(PATH_ROLE, path)
            item.setData(MARKS_ROLE, tuple(
                name for name, members in self.marks.items() if path in members))
            item.setToolTip(Path(path).name)
            self.sheet.addItem(item)
            cached = thumb_path_for(path)
            if cached is not None and cached.exists():
                item.setIcon(QIcon(str(cached)))
            else:
                self.pool.start(ThumbJob(row, path, self.signal))
        if self.paths:
            self.sheet.setCurrentRow(0)
        heading = (collection_title(self.collection) if self.collection is not None
                   else str(self.folder))
        self.folder_info.setText("\n".join([f"dir\t{heading}", *self._count_rows()]))
        head = self.panels["gallery"].title
        if head is not None:
            head.set_value(f"{len(self.paths)}/{len(self.all_paths)}"
                           if self.filter_text else f"{len(self.all_paths)}")

    def _count_rows(self) -> list[str]:
        """What is on the sheet, one fact per row.

        A count and the name of the newest frame are two different answers, and a row
        that carries both is a row where one of them gets elided away — so they are two
        rows, keyed like everything else in the readout."""
        shown = len(self.paths)
        images = "image" if shown == 1 else "images"
        if self.filter_text:
            return [f"img\t{shown} of {len(self.all_paths)} {images}",
                    f"qry\t{self.filter_text}"]
        if self.collection is not None:
            # A set keeps the order you built it in — the only order in the app that is
            # yours rather than the disk's — so "newest first" would be a lie here.
            missing = len(load_collection(self.collection)) - len(self.all_paths)
            rows = [f"img\t{shown} {images}, in the order you marked them"]
            if missing > 0:
                rows.append(f"off\t{missing} not on this machine")
            return rows
        return [f"img\t{shown} {images} (recursive)",
                f"new\t{Path(self.paths[0]).name if self.paths else '—'}"]

    def _thumb_ready(self, row: int, path: str, thumb_file: str) -> None:
        if row < self.sheet.count() and self.paths[row] == path:
            self.sheet.item(row).setIcon(QIcon(thumb_file))

    def _image_selected(self, row: int) -> None:
        if not 0 <= row < len(self.paths):
            self.image_info.setText("")
            return
        path = self.paths[row]
        self.view.source_path = path  # so Ctrl+drag out of the viewport carries the file
        self.view.show_frames(load_frames(path))
        self.describe_current()

    def describe_current(self) -> None:
        """The facts about the picture on screen, including which sets it is in.

        Kept apart from selecting it because marking is not selecting: pressing F has to
        update this line without decoding the file again — which would also throw away
        wherever you had zoomed to, in the middle of a pass over a shoot."""
        path = self.current_path()
        if path is None:
            self.image_info.setText("")
            return
        try:
            size_kb = Path(path).stat().st_size / 1024
        except OSError:
            size_kb = 0
        frames = self.view.frames
        dims = f"{frames[0].width()}×{frames[0].height()}" if frames else "unreadable"
        stack = f" · {len(frames)} slices" if len(frames) > 1 else ""
        marks = " · ".join(collection_title(name) for name, members in self.marks.items()
                           if path in members)
        self.image_info.setText(f"sel\t{Path(path).name}\ndim\t{dims}{stack}"
                                f"\nsiz\t{size_kb:,.0f} KB"
                                + (f"\nmrk\t{marks}" if marks else ""))

    def set_details_visible(self, on: bool) -> None:
        """W: the facts, at the foot of the sidebar. Not a column of its own — three
        columns is the layout, and a fourth one for two lines of text is not a layout."""
        self.facts.setVisible(on)

    def step(self, direction: int) -> None:
        row = self.sheet.currentRow() + direction
        if 0 <= row < self.sheet.count():
            self.sheet.setCurrentRow(row)

    # -- selection helpers -------------------------------------------------------
    def selected_paths(self) -> list[str]:
        return self.sheet.selected_paths()

    def current_path(self) -> str | None:
        row = self.sheet.currentRow()
        return self.paths[row] if 0 <= row < len(self.paths) else None

    def select_path(self, path: str) -> None:
        want = Path(path).resolve()
        for row, existing in enumerate(self.paths):
            if Path(existing).resolve() == want:
                self.sheet.setCurrentRow(row)
                return

    # -- import / paste ----------------------------------------------------------
    def import_and_view(self, sources: list[str]) -> None:
        """Copy external images into this folder (files are files), then select one."""
        plan = plan_import(sources, self.folder)
        landed: list[Path] = []
        for source, destination in plan.copies:
            try:
                shutil.copy2(source, destination)
                landed.append(destination)
            except OSError:
                pass
        if plan.copies:
            self.rescan()
        target = landed[0] if landed else (plan.existing[0] if plan.existing else None)
        if target is not None:
            self.select_path(str(target))

    def save_and_view_image(self, image: QImage) -> None:
        """Write raw dropped/pasted bitmap data into this folder as a PNG, then view it."""
        if image.isNull():
            return
        destination = unique_destination(self.folder, pasted_image_name(datetime.now()))
        if image.save(str(destination)):
            self.rescan()
            self.select_path(str(destination))

    # -- clipboard ---------------------------------------------------------------
    def copy_selection(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(p) for p in paths])
        if len(paths) == 1:
            image = QImage(paths[0])
            if not image.isNull():
                data.setImageData(image)
        QGuiApplication.clipboard().setMimeData(data)

    def copy_paths(self) -> None:
        paths = self.selected_paths()
        if paths:
            QGuiApplication.clipboard().setText("\n".join(paths))

    def paste_clipboard(self) -> None:
        clipboard = QGuiApplication.clipboard()
        paths = local_image_urls(clipboard.mimeData())
        if paths:
            self.import_and_view(paths)
            return
        image = clipboard.image()
        if not image.isNull():
            self.save_and_view_image(image)

    # -- file-manager verbs ------------------------------------------------------
    def reveal(self, path: str | None = None) -> None:
        """Show the file in the OS file manager, selected; fall back to its folder."""
        path = path or self.current_path()
        if not path:
            return
        command = reveal_command(path)
        try:
            if command is not None and subprocess.run(command, capture_output=True, timeout=5).returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            subprocess.Popen(open_path_command(str(Path(path).parent)), start_new_session=True)
        except OSError:
            pass

    def rename(self, path: str | None = None) -> None:
        path = path or self.current_path()
        if not path:
            return
        source = Path(path)
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=source.name)
        name = name.strip()
        if not ok or not name or name == source.name:
            return
        try:
            destination = source.with_name(name)
        except ValueError:
            QMessageBox.warning(self, "Rename", "That name isn't valid.")
            return
        if destination.exists():
            QMessageBox.warning(self, "Rename", f"{name} already exists.")
            return
        try:
            source.rename(destination)
        except OSError as error:
            QMessageBox.warning(self, "Rename", str(error))
            return
        self.rescan()
        self.select_path(str(destination))

    def trash(self, paths: list[str] | None = None) -> None:
        """Move the selection to the OS trash (recoverable), then rescan."""
        paths = [p for p in (paths if paths is not None else self.selected_paths()) if p]
        if not paths:
            return
        trashed, unsupported = False, False
        for path in paths:
            command = trash_command(path)
            if command is None:
                unsupported = True
                continue
            try:
                if subprocess.run(command, capture_output=True, timeout=10).returncode == 0:
                    trashed = True
            except (OSError, subprocess.SubprocessError):
                pass
        if trashed:
            self.rescan()
        elif unsupported:
            QMessageBox.information(self, "Move to Trash",
                                    "Move to Trash isn't available on this system.")

    def prompt_new_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "New Folder", f"Create in {self.folder}:")
        if ok and name:
            self.new_folder(name)

    # -- context menu ------------------------------------------------------------
    def build_context_menu(self) -> QMenu:
        """The standard right-click menu over the current selection."""
        paths = self.selected_paths()
        one = len(paths) == 1
        some = bool(paths)
        mime = QGuiApplication.clipboard().mimeData()
        can_paste = bool(local_image_urls(mime)) or mime.hasImage()

        menu = QMenu(self)

        def add(label: str, enabled: bool, handler) -> None:
            action = menu.addAction(label)
            action.setEnabled(enabled)
            action.triggered.connect(handler)

        add("Open", one, lambda: self.select_path(paths[0]) if paths else None)
        menu.addSeparator()
        add("Copy\tCtrl+C", some, self.copy_selection)
        add("Copy Path", some, self.copy_paths)
        add("Paste\tCtrl+V", can_paste, self.paste_clipboard)
        menu.addSeparator()
        add("Reveal in File Manager", one, lambda: self.reveal(paths[0]) if paths else None)
        add("Rename…\tF2", one, lambda: self.rename(paths[0]) if paths else None)
        add("Move to Trash\tDel", some, lambda: self.trash(paths))
        menu.addSeparator()
        add("New Folder…", True, self.prompt_new_folder)
        add("Refresh", True, self.rescan)
        return menu

    def show_context_menu(self, global_pos: QPoint) -> None:
        self.build_context_menu().exec(global_pos)

    def build_folder_menu(self, folder: Path) -> QMenu:
        """Right-click on a folder in the tree: open it, pin it, reveal it, nest one."""
        pinned = folder in load_favorites()
        menu = QMenu(self)
        menu.addAction("Open").triggered.connect(lambda: self.set_folder(folder))
        menu.addSeparator()
        label = "Remove from Favorites" if pinned else "Add to Favorites"
        menu.addAction(label).triggered.connect(
            lambda: self._set_favorite(folder, not pinned))
        menu.addSeparator()
        menu.addAction("Reveal in File Manager").triggered.connect(
            lambda: self.reveal(str(folder)))
        menu.addAction("New Folder…").triggered.connect(self.prompt_new_folder)
        return menu

    def show_folder_menu(self, folder: Path, global_pos: QPoint) -> None:
        self.build_folder_menu(folder).exec(global_pos)

    def _set_favorite(self, folder: Path, pinned: bool) -> None:
        add_favorite(folder) if pinned else remove_favorite(folder)
        self._favorites_changed()

    def _favorites_changed(self) -> None:
        self.reload_places()
        self.favorites_changed.emit()

    def reload_places(self) -> None:
        self.places.reload()
        if self.collection is not None:
            self.places.select_collection(self.collection)
        else:
            self.places.select_path(self.folder)

    # -- panes -------------------------------------------------------------------
    # The viewport is no longer a pane you can close — it is the window — but it keeps its
    # name here because Focus is still defined as "nothing open but the picture".
    PANES = ("locations", "gallery", "info", "viewer")

    def _panes(self) -> dict[str, QWidget]:
        return dict(self.panels)

    def set_focus_mode(self, on: bool) -> None:
        """Focus is the viewer and nothing else. Coming back out is the browsing
        arrangement — the folder and the gallery — which is what the app opens on when you
        hand it a folder rather than a picture."""
        self._widths_changed()
        for name in COLUMNS:
            open_now = (name == "viewer") if on else (name != "viewer")
            self.panels[name].state = replace(self.panels[name].state, open=open_now)
        self.relayout()

    def set_pane_visible(self, pane: str, on: bool) -> None:
        """Open or close one column. The last one open cannot be closed — a window with
        nothing in it is not a state anyone asked for — so closing it opens the viewer
        instead, which is Focus."""
        if pane not in self.panels:
            return
        self._widths_changed()
        self.panels[pane].state = replace(self.panels[pane].state, open=on)
        if not any(panel.state.open for panel in self.panels.values()):
            self.panels["viewer"].state = replace(self.panels["viewer"].state, open=True)
        self.relayout()

    def pane_visible(self, pane: str) -> bool:
        if pane == "info":
            return self.facts.isVisible()
        return bool(self.panels[pane].state.open)

    def open_current(self) -> None:
        """Show the selected frame in the viewer, opening that column if it is closed.

        Double-clicking a thumbnail means "this one" — the picture has to end up somewhere
        you can see it, and the gallery keeps its place beside it rather than being thrown
        away for a full-bleed view you did not ask for."""
        if not self.pane_visible("viewer"):
            self.set_pane_visible("viewer", True)
            self.panes_changed.emit()
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def new_folder(self, name: str) -> Path | None:
        target = self.folder / name.strip()
        if not name.strip() or target.exists():
            return None
        try:
            target.mkdir(parents=True)
        except OSError:
            return None
        return target


class Shell(QWidget):
    """The whole window under the menu bar: the tabs, filling it. Nothing floats any
    more, so this is a plain box with one thing in it."""

    def __init__(self, window: LightTableWindow):
        super().__init__()
        self.setObjectName("shell")
        self.window_ref = window
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self.box = box


class LightTableWindow(QMainWindow):
    def __init__(self, start: Path | None = None):
        super().__init__()
        self.setWindowTitle("Light Table — 2Photon / The Elements")
        self.setWindowIcon(QIcon(str(ICON)))
        self.resize(1440, 900)
        self.details_on = False
        # Where the columns were last time. New tabs start from the same proportions and
        # then go their own way — a starting point, not a shared layout.
        self.layout_states = load_layout(DEFAULT_PANELS)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.tabBarDoubleClicked.connect(self._rename_tab)
        self.tabs.currentChanged.connect(self._sync_pane_actions)
        # No corner widget: a floating ＋ in the top right is a button that is not at the
        # top of the sidebar with the others, and it sits over the Gallery's own header.
        # New tabs are Ctrl+T, the File menu, and the ＋ Tab button on the bar.
        # One tab is not a set of tabs, it is just this window — so the strip only appears
        # once there is a choice to make.
        self.tabs.tabBar().setVisible(False)

        self.shell = Shell(self)
        self.shell.box.addWidget(self.tabs, 1)
        self.setCentralWidget(self.shell)
        self._build_menus()
        self._build_keys()

        # How the app opens is a question about what you handed it. A picture is a thing
        # you want to look at, so it opens in Focus, full bleed. A folder — or nothing at
        # all — is a thing you want to look *through*, so it opens on the folder and the
        # gallery and does not spend a third of the window on a picture you have not
        # picked yet.
        start = Path(start) if start else Path.home() / "Pictures"
        if start.is_file():
            folder, file = start.parent, start
        else:
            folder, file = (start if start.is_dir() else Path.home()), None
        self.focus_mode = file is not None
        self.new_tab(folder)
        if file is not None:
            tab = self.current_tab()
            if str(file) in tab.paths:
                tab.sheet.setCurrentRow(tab.paths.index(str(file)))

    def paintEvent(self, event) -> None:
        """The window under everything. Opaque, and the darkest thing in the app after the
        matting — a picture viewer that lets the wallpaper through is a picture viewer
        that lies about what the picture looks like."""
        QPainter(self).fillRect(self.rect(), CHROME)
        super().paintEvent(event)

    def current_tab(self) -> Tab:
        return self.tabs.currentWidget()

    def new_tab(self, folder: Path | None = None) -> None:
        tab = Tab(folder or Path.home() / "Pictures", self.layout_states)
        tab.panes_changed.connect(self._sync_pane_actions)
        tab.favorites_changed.connect(self._reload_places)
        tab.marks_changed.connect(self._marks_changed)
        tab.search_typed.connect(self._search_changed)
        tab.search.installEventFilter(self)  # bare keys step aside while typing
        self.tabs.addTab(tab, tab.folder.name or str(tab.folder))
        self.tabs.setCurrentWidget(tab)
        tab.set_focus_mode(self.focus_mode)
        tab.set_details_visible(self.details_on)
        self.tabs.tabBar().setVisible(self.tabs.count() > 1)
        self._sync_pane_actions()

    def _marks_changed(self) -> None:
        """One set of marks, every tab: the files underneath are the same two, so a flag
        put on in one tab has to show up in all of them."""
        for index in range(self.tabs.count()):
            self.tabs.widget(index).refresh_marks()

    def closeEvent(self, event) -> None:
        """Keep the arrangement. The layout belongs to the person, not the session.

        Only when it actually moved, though: a window opened and closed without touching a
        panel should leave the config directory exactly as it found it."""
        tab = self.current_tab()
        if tab is not None:
            states = {state.name: state for state in tab.panel_states()}
            if states != self.layout_states:
                try:
                    save_layout(states.values())
                except OSError:
                    pass  # an unwritable config directory must not stop the app closing
        super().closeEvent(event)

    def _reload_places(self) -> None:
        """A favorite pinned in one tab shows up in all of them — same file underneath."""
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            if tab is not self.sender():
                tab.reload_places()

    def _close_tab(self, index: int) -> None:
        if self.tabs.count() > 1:
            self.tabs.removeTab(index)
            self.tabs.tabBar().setVisible(self.tabs.count() > 1)


    def _rename_tab(self, index: int) -> None:
        if index < 0:
            return
        current = self.tabs.tabText(index)
        name, ok = QInputDialog.getText(self, "Rename Tab", "Tab name:", text=current)
        if ok and name.strip():
            self.tabs.setTabText(index, name.strip())

    # -- chrome: menus only, no buttons anywhere -----------------------------------
    def _build_menus(self) -> None:
        # Single-key shortcuts and a search box do not mix: Qt hands a plain letter to the
        # shortcut before it reaches the field you are typing in, so "car" would fit, flag
        # and go 1:1 instead of filtering. Every bare-letter action is collected here and
        # switched off while the search box has the keyboard.
        self.letter_actions: list[QAction] = []

        def act(menu, text, shortcut, fn):
            action = QAction(text, self)
            if shortcut:
                sequence = QKeySequence(shortcut)
                action.setShortcut(sequence)
                if len(shortcut) == 1:
                    self.letter_actions.append(action)
            action.triggered.connect(fn)
            menu.addAction(action)
            return action

        file_menu = self.menuBar().addMenu("&File")
        act(file_menu, "New Tab", "Ctrl+T", lambda: self.new_tab(self.current_tab().folder))
        act(file_menu, "New Folder…", "Ctrl+Shift+N", self._new_folder)
        act(file_menu, "Reveal in File Manager", "Ctrl+R", lambda: self.current_tab().reveal())
        act(file_menu, "Close Tab", "Ctrl+W", lambda: self._close_tab(self.tabs.currentIndex()))
        act(file_menu, "Quit", "Ctrl+Q", self.close)
        edit_menu = self.menuBar().addMenu("&Edit")
        act(edit_menu, "Copy", "Ctrl+C", lambda: self.current_tab().copy_selection())
        act(edit_menu, "Copy Path", "Ctrl+Shift+C", lambda: self.current_tab().copy_paths())
        act(edit_menu, "Paste", "Ctrl+V", lambda: self.current_tab().paste_clipboard())
        act(edit_menu, "Rename…", "F2", lambda: self.current_tab().rename())
        act(edit_menu, "Move to Trash", "Delete", lambda: self.current_tab().trash())
        act(edit_menu, "Search…", "Ctrl+F", self.focus_search)

        # Marks. Both keys are one press for both directions — the thing you want while
        # going through a shoot is one finger that means "this one" and means it again to
        # take it back, not two keys and a decision about which of them applies.
        mark_menu = self.menuBar().addMenu("&Mark")
        act(mark_menu, "Add to Quick Collection", "A",
            lambda: self.current_tab().toggle_mark("quick"))
        act(mark_menu, "Flag", "F", lambda: self.current_tab().toggle_mark("flagged"))
        mark_menu.addSeparator()
        act(mark_menu, "Open Quick Collection", "Ctrl+Shift+A",
            lambda: self.current_tab().set_collection("quick"))
        act(mark_menu, "Open Flagged", "Ctrl+Shift+G",
            lambda: self.current_tab().set_collection("flagged"))

        view_menu = self.menuBar().addMenu("&View")
        # Zoom lives on the number row now: F is worth more as the flag key, and 9-0-1 in
        # a row is easier to find in the dark than a letter anyway.
        act(view_menu, "Fit", "0", lambda: self.current_tab().view.fit())
        act(view_menu, "Fill", "9", lambda: self.current_tab().view.fill())
        act(view_menu, "1:1", "1", lambda: self.current_tab().view.one_to_one())
        view_menu.addSeparator()
        # Everything below used to be a row of buttons at the top of the sidebar. They
        # were pressed about as often as a menu item is, and they cost a strip of the one
        # column that has real work to do, so they are menu items — checkable ones, which
        # is the part the buttons were actually carrying: a statement of what is open.
        self.pane_actions: dict[str, QAction] = {}
        for pane, label in (("locations", "Locations"), ("gallery", "Gallery"),
                            ("viewer", "Viewer")):
            action = act(view_menu, label, "", lambda checked=False, name=pane:
                         self._set_current_pane(name, checked))
            action.setCheckable(True)
            self.pane_actions[pane] = action
        self.facts_action = act(view_menu, "Facts", "W",
                                lambda _checked=False: self.toggle_details())
        self.facts_action.setCheckable(True)
        self.focus_action = act(view_menu, "Focus Mode", "Ctrl+Shift+F",
                                lambda _checked=False: self.toggle_focus())
        self.focus_action.setCheckable(True)
        act(view_menu, "Fullscreen", "Alt+F", self.toggle_fullscreen)
        view_menu.addSeparator()
        act(view_menu, "Reset Column Widths", "", self.reset_layout)

    def eventFilter(self, watched, event) -> bool:
        """Hand the bare-letter keys to the search box while it has the keyboard, and
        take them back the moment it does not."""
        if watched is self.search and event.type() in (QEvent.Type.FocusIn,
                                                       QEvent.Type.FocusOut):
            typing = event.type() == QEvent.Type.FocusIn
            for action in self.letter_actions:
                action.setEnabled(not typing)
            for shortcut in getattr(self, "arrow_keys", ()):
                shortcut.setEnabled(not typing)
        return super().eventFilter(watched, event)

    @property
    def search(self):
        """The search line of whichever tab is in front.

        There is one per tab, because the question is per folder: two tabs are two
        folders and two different things being looked for in them. Nothing is synced on
        the way in — each field already holds its own tab's answer."""
        tab = self.current_tab()
        return tab.search if tab is not None else None

    def _search_changed(self, text: str) -> None:
        """Results you cannot see are not results: a search opens the Gallery if the
        window is in Focus. Clearing the box leaves it open — you asked for it once."""
        tab = self.current_tab()
        if tab is None or not text.strip() or tab.pane_visible("gallery"):
            return
        self._set_current_pane("gallery", True)
        self._sync_pane_actions()

    def focus_search(self) -> None:
        field = self.search
        if field is None:
            return
        field.setFocus(Qt.FocusReason.ShortcutFocusReason)
        field.selectAll()

    def clear_search(self) -> None:
        """Escape out of a search: drop the filter, hand the keyboard back to the picture."""
        field = self.search
        if field is not None:
            field.clear()
        tab = self.current_tab()
        if tab is not None:
            tab.view.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _set_current_pane(self, pane: str, visible: bool) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        tab.set_pane_visible(pane, visible)
        self.focus_mode = self._is_focused(tab)
        self._sync_pane_actions()

    @staticmethod
    def _is_focused(tab: Tab) -> bool:
        """Focus is not a mode you set, it is a thing that is true: the viewer open and
        nothing beside it. Closing the last column by hand is therefore the same as
        pressing Focus, and there is no state where the two disagree."""
        return (tab.pane_visible("viewer")
                and not any(tab.pane_visible(pane) for pane in ("locations", "gallery")))

    def _set_focus(self, on: bool) -> None:
        self.focus_mode = on
        tab = self.current_tab()
        if tab is not None:
            tab.set_focus_mode(on)
        self._sync_pane_actions()

    def _sync_pane_actions(self, *args) -> None:
        """Make the buttons say what is true. Every one of them is a statement about the
        window, so none of them is ever set by hand — they are read back off the tab."""
        if not hasattr(self, "pane_actions"):
            return
        tab = self.current_tab()
        if tab is None:
            return
        for pane, widget in self.pane_actions.items():
            widget.blockSignals(True)
            widget.setChecked(tab.pane_visible(pane))
            widget.blockSignals(False)
        for widget, state in ((self.facts_action, tab.pane_visible("info")),
                              (self.focus_action, self._is_focused(tab))):
            widget.blockSignals(True)
            widget.setChecked(state)
            widget.blockSignals(False)

    def reset_layout(self) -> None:
        """Put the columns back to their starting widths, in every tab. Which columns are
        open is left alone — that is what you are looking at, not how it is proportioned."""
        self.layout_states = {state.name: state for state in DEFAULT_PANELS}
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            tab.apply_layout({name: replace(state, open=tab.pane_visible(name))
                              for name, state in self.layout_states.items()})
        self._sync_pane_actions()

    def _new_folder(self) -> None:
        tab = self.current_tab()
        name, ok = QInputDialog.getText(self, "New Folder", f"Create in {tab.folder}:")
        if ok and name:
            tab.new_folder(name)

    def _build_keys(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._escape)
        # Left and right are the previous and the next picture, and they are that no
        # matter which column the keyboard is in. Going through a shoot is the thing this
        # app is for, and it cannot depend on having clicked the right pane first: the
        # folder tree gives up its expand/collapse keys, which it can spare, because a
        # disclosure triangle is one click away and the next frame is not.
        self.arrow_keys = [
            QShortcut(QKeySequence(key), self,
                      activated=lambda step=step: self._step(step))
            for key, step in (("Left", -1), ("Right", 1),
                              ("Space", 1), ("Shift+Space", -1))
        ]

    def _step(self, direction: int) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.step(direction)

    def toggle_details(self) -> None:
        self.details_on = not self.details_on
        for index in range(self.tabs.count()):
            self.tabs.widget(index).set_details_visible(self.details_on)
        self._sync_pane_actions()

    def toggle_focus(self) -> None:
        self.focus_mode = not self.focus_mode
        self.current_tab().set_focus_mode(self.focus_mode)
        self._sync_pane_actions()

    def toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _escape(self) -> None:
        field = self.search
        if field is not None and (field.hasFocus() or field.text()):
            self.clear_search()
        elif self.isFullScreen():
            self.showNormal()
        elif self.focus_mode:
            self.toggle_focus()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="lighttable",
        description="Light Table — the 2D image viewer. Open a folder or an image file.")
    parser.add_argument("path", nargs="?", help="image file or folder to open")
    options = parser.parse_args((argv if argv is not None else sys.argv)[1:])

    cache_dir().mkdir(parents=True, exist_ok=True)
    app = QApplication(argv if argv is not None else sys.argv)
    # Say which desktop entry this window belongs to. Without it a Wayland compositor
    # takes the app id from the executable, which for a Python app is "python3" — so the
    # task bar and the window switcher show a Python icon instead of Lt, and the shortcut
    # on the desktop never lights up as the thing that is running.
    app.setDesktopFileName(DESKTOP_ENTRY)
    configure_application(app, "Light Table")
    app.setStyleSheet(HOUSE_STYLE + MATERIAL_STYLE)
    window = LightTableWindow(Path(options.path) if options.path else None)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
