#!/usr/bin/env python3
"""Light Table — the 2D image viewer. Three columns, no buttons, ultra snappy.

Columns: folder tree · recursive contact sheet (newest first) · viewport.
Controls: LMB pan · RMB drag zoom (horizontal RMB scrubs z-stacks) · scroll zoom
(slices on stacks) · horizontal wheel / arrows / Space / Shift+Space next-prev ·
MX forward button pans, back button holds a compare with the last-viewed image ·
F fit · Shift+F fill · Ctrl+Shift+F focus (hide columns) · Alt+F fullscreen · 1 = 1:1.

Files are files (Rule 8): drag an image out of the contact sheet — or out of the viewport
with Ctrl held — into any app (URLs plus the bitmap itself); drop images onto the sheet or
viewport to import them into the current folder; drop onto the tree to move them. Ctrl+C
copies the selection, Ctrl+V pastes an image or files in, F2 renames, Delete trashes. A
right-click (no drag) opens the standard context menu, including Reveal in File Manager.

The window is one translucent slab: chrome and matting let the desktop through, the
picture is the only fully opaque thing on screen.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import (
    QDir, QMimeData, QModelIndex, QObject, QPoint, QRect, QRunnable, QSize, Qt,
    QThreadPool, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QDrag, QFileSystemModel, QFont, QGuiApplication, QIcon, QImage,
    QImageReader, QKeySequence, QPainter, QPixmap, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication, QGraphicsPixmapItem,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QListView, QMainWindow, QMenu, QMessageBox, QPushButton,
    QSizePolicy, QSplitter, QTabWidget, QToolBar, QTreeView, QVBoxLayout,
    QWidget,
)

from apps.common import HOUSE_STYLE, INK, MUTE, PANEL, SELECT, configure_application
from apps.lighttable.core import (
    THUMB_SIZE, add_favorite, cache_dir, is_image_path, load_favorites, mounted_drives,
    open_path_command, pasted_image_name, plan_import, remove_favorite, reveal_command,
    scan_images, thumb_path_for, trash_command, tree_anchor, unique_destination,
)

# --- The material -----------------------------------------------------------------------
# Light Table is meant to sit behind the picture, not in front of it, so the window is a
# single translucent slab in the house grey rather than a black box: same #0d0d0d hue,
# just letting the desktop through. Compositors that blur behind translucent windows
# (KWin, DWM's Mica) turn it frosted for free; where they don't, it degrades to a clean
# tinted pane. The image itself never goes translucent — matting is the darkest, most
# opaque layer so it stays honest to look at.
# Mica, not glass: the desktop is a tint under the grey, never something you read through.
# Chrome has to stay legible over a white window or a bright wallpaper, so the alphas are
# high — the translucency is a hint of depth, not a window.
CHROME = QColor(13, 13, 13, 244)  # the window slab itself (painted, not styled)
SLAB = "rgba(20,20,20,0.88)"      # sidebars: places, tree, contact sheet
CANVAS = QColor(8, 8, 8, 244)     # the matting around the picture (painted, not styled)
HAIR = "rgba(242,242,242,0.10)"   # hairline borders, one shade brighter than the grey
LIFT = "rgba(242,242,242,0.07)"   # hover / open-state wash
GUTTER = "rgba(242,242,242,0.03)" # the disclosure rail, a shade off the canvas
RAIL_INK = "#8c8c8c"              # rail labels: readable at rest, ink when open

# Layered on top of the house style: everything between the window and the picture goes
# transparent so one slab shows through the whole stack. Dialogs keep the opaque house
# background — a modal over a translucent window would be unreadable.
MATERIAL_STYLE = f"""
QMainWindow::separator {{ background: {HAIR}; }}
QWidget#tab, QWidget#paneColumn, QSplitter {{ background: transparent; }}
QMenuBar {{ background: transparent; }}
QMenuBar::item:selected {{ background: {LIFT}; }}
QMenu {{ background: {PANEL}; border: 1px solid {HAIR}; }}
QMenu::item:selected {{ background: {LIFT}; }}
QMenu::separator {{ background: {HAIR}; height: 1px; margin: 4px 0; }}
QToolBar#lighttable-toolbar {{ background: transparent; border: 0;
                               border-bottom: 1px solid {HAIR}; padding: 2px 4px; }}
QToolBar#lighttable-toolbar QToolButton {{ background: transparent; border: 1px solid transparent;
                               border-radius: 6px; padding: 4px 9px; color: {INK}; }}
QToolBar#lighttable-toolbar QToolButton:hover {{ background: {LIFT}; }}
QToolBar#lighttable-toolbar QToolButton:checked {{ background: {LIFT}; border-color: {HAIR}; }}
QTabWidget::pane {{ border: 0; background: transparent; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {MUTE}; border: 0; padding: 5px 12px; }}
QTabBar::tab:selected {{ color: {INK}; background: {LIFT}; }}
QTabWidget QPushButton {{ background: transparent; border: 1px solid {HAIR};
                          color: {MUTE}; font-size: 13px; padding: 0; }}
QTabWidget QPushButton:hover {{ color: {INK}; border-color: {INK}; }}
QListWidget#places, QTreeView#folders, QListWidget#sheet {{ background: {SLAB}; border: 0;
                               color: {INK}; }}
QGraphicsView#canvas {{ background: transparent; border: 0; }}
QSplitter::handle {{ background: {HAIR}; }}
QWidget#rail {{ background: {GUTTER}; border-right: 1px solid {HAIR}; }}
"""


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

PATH_ROLE = Qt.ItemDataRole.UserRole
FAVORITE_ROLE = Qt.ItemDataRole.UserRole + 1  # pinned by the user vs. a live mount


class ThumbSignal(QObject):
    done = pyqtSignal(int, str, str)  # row, source path, thumbnail file


class ThumbJob(QRunnable):
    """Decode one thumbnail into the persistent cache (all P-cores welcome)."""

    def __init__(self, row: int, path: str, signal: ThumbSignal):
        super().__init__()
        self.row, self.path, self.signal = row, path, signal

    def run(self) -> None:
        target = thumb_path_for(self.path)
        if target is None:
            return
        if not target.exists():
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


class Viewport(QGraphicsView):
    """The image pane: pan, zoom, z-scrub, next/prev — all gesture, no chrome."""

    request_step = pyqtSignal(int)  # +1 next image, -1 previous
    images_dropped = pyqtSignal(list)         # local image paths dropped in
    image_data_dropped = pyqtSignal(QImage)   # raw bitmap dropped in (no source file)
    context_requested = pyqtSignal(QPoint)    # right-click (not a drag): global pos

    def __init__(self):
        super().__init__()
        self.setObjectName("canvas")
        self.setScene(QGraphicsScene(self))
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self.item)
        # The matting is painted, not styled: a stylesheet background on a QGraphicsView
        # lands on the frame, not the canvas. Near-opaque — you have to be able to trust
        # what a picture looks like — but still part of the same slab.
        self.setBackgroundBrush(CANVAS)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        self.frames = frames
        self.slice = 0
        self._show_slice()
        if self._fit_on_load:
            self.fit()

    def _show_slice(self) -> None:
        if self.frames:
            self.item.setPixmap(QPixmap.fromImage(self.frames[self.slice]))
            self.scene().setSceneRect(self.item.boundingRect())

    def set_slice(self, index: int) -> None:
        if self.frames:
            self.slice = max(0, min(len(self.frames) - 1, index))
            self._show_slice()

    @property
    def is_stack(self) -> bool:
        return len(self.frames) > 1

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
        if abs(dx) > abs(dy):  # MX horizontal wheel: next / previous image
            self.request_step.emit(-1 if dx > 0 else 1)
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
        self.setStyleSheet(
            f"QListWidget::item{{color:{MUTE}; border:1px solid transparent;}}"
            f"QListWidget::item:selected{{border:1px solid {SELECT}; background:{LIFT};}}")
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
    """The top half of the locations column: pinned favorites, then every drive that is
    mounted right now. Favorites are yours (right-click a folder anywhere to pin it);
    drives are read off the mount table, so plugging a disk in makes it show up."""

    folder_selected = pyqtSignal(Path)
    favorites_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("places")
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setStyleSheet(
            f"QListWidget::item{{padding:5px 9px;}}"
            f"QListWidget::item:selected{{border-left:2px solid {SELECT}; background:{LIFT};}}"
        )
        self.itemActivated.connect(self._activate)
        self.itemClicked.connect(self._activate)
        self.reload()

    # -- contents ----------------------------------------------------------------
    def reload(self) -> None:
        """Rebuild from the favorites file plus the live mount table, keeping the
        selection where it was if that place is still on the list."""
        current = self.current_path()
        self.clear()
        for folder in load_favorites():
            if folder.is_dir():
                label = "Home" if folder == Path.home() else (folder.name or str(folder))
                self._add_place(label, folder, favorite=True)
        drives = mounted_drives()
        if drives:
            self._add_heading("DRIVES")
            for label, path in drives:
                self._add_place(label, path, favorite=False)
        if current is not None:
            self.select_path(current)

    def _add_place(self, label: str, path: Path, favorite: bool) -> None:
        item = QListWidgetItem(label)
        item.setData(PATH_ROLE, str(path))
        item.setData(FAVORITE_ROLE, favorite)
        item.setToolTip(str(path))
        self.addItem(item)

    def _add_heading(self, text: str) -> None:
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setForeground(QColor(MUTE))
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

    def select_path(self, folder: Path) -> None:
        """Highlight the place holding this folder, or clear the highlight if none does."""
        for row in range(self.count()):
            item = self.item(row)
            data = item.data(PATH_ROLE)
            if data and Path(data) == folder:
                self.setCurrentItem(item)
                return
        self.setCurrentItem(None)

    def _activate(self, item: QListWidgetItem) -> None:
        data = item.data(PATH_ROLE)
        if data and Path(data).is_dir():
            self.folder_selected.emit(Path(data))

    # -- pinning -----------------------------------------------------------------
    def contextMenuEvent(self, event) -> None:
        item = self.itemAt(event.pos())
        data = item.data(PATH_ROLE) if item is not None else None
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

    def _pin(self, folder: Path) -> None:
        add_favorite(folder)
        self.favorites_changed.emit()

    def _unpin(self, folder: Path) -> None:
        remove_favorite(folder)
        self.favorites_changed.emit()


class DisclosureButton(QAbstractButton):
    """A hairline tab on the window edge with its label running bottom-to-top — the
    handle you pull a column out by. Checked means the column is open."""

    WIDTH = 22

    def __init__(self, text: str):
        super().__init__()
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Show or hide {text.lower()}")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(self.WIDTH, metrics.horizontalAdvance(self.text()) + 30)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isChecked() or self.underMouse():
            painter.fillRect(self.rect(), QColor(242, 242, 242, 15 if self.isChecked() else 8))
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(-90)
        font = QFont(self.font())
        font.setPointSizeF(max(6.5, font.pointSizeF() - 2.5))
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        painter.setFont(font)
        painter.setPen(QColor(INK) if self.isChecked() else QColor(RAIL_INK))
        painter.drawText(QRect(-self.height() // 2, -self.WIDTH // 2, self.height(), self.WIDTH),
                         int(Qt.AlignmentFlag.AlignCenter), self.text().upper())


class DisclosureRail(QWidget):
    """The 22px gutter down the left edge. In Focus the columns are gone and these two
    tabs are the only chrome left — pull one and its column slides back in."""

    toggled = pyqtSignal(str, bool)

    def __init__(self):
        super().__init__()
        self.setObjectName("rail")
        self.setFixedWidth(DisclosureButton.WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(6)
        self.buttons: dict[str, DisclosureButton] = {}
        for pane, label in (("locations", "Locations"), ("gallery", "Gallery")):
            button = DisclosureButton(label)
            button.clicked.connect(
                lambda checked, name=pane: self.toggled.emit(name, checked))
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
            self.buttons[pane] = button
        layout.addStretch(1)

    def sync(self, states: dict[str, bool]) -> None:
        for pane, button in self.buttons.items():
            button.setChecked(states.get(pane, False))
            button.update()


class Tab(QWidget):
    """One tab: places + tree · contact sheet · viewport, with its own folder and state."""

    panes_changed = pyqtSignal()
    favorites_changed = pyqtSignal()

    def __init__(self, start_dir: Path):
        super().__init__()
        self.setObjectName("tab")
        self.pool = QThreadPool.globalInstance()
        self.signal = ThumbSignal()
        self.signal.done.connect(self._thumb_ready)
        self.paths: list[str] = []
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
        self.places.favorites_changed.connect(self._favorites_changed)

        self.sheet = ContactSheet()
        self.sheet.currentRowChanged.connect(self._image_selected)
        self.sheet.images_dropped.connect(self.import_and_view)
        self.sheet.image_data_dropped.connect(self.save_and_view_image)
        self.sheet.context_requested.connect(self.show_context_menu)

        self.view = Viewport()
        self.view.request_step.connect(self.step)
        self.view.images_dropped.connect(self.import_and_view)
        self.view.image_data_dropped.connect(self.save_and_view_image)
        self.view.context_requested.connect(self.show_context_menu)

        # Detail panels: folder facts under the tree, image facts under the sheet. W toggles.
        self.folder_info = QLabel(objectName="muted")
        self.folder_info.setWordWrap(True)
        self.folder_info.setContentsMargins(10, 6, 10, 8)
        self.image_info = QLabel(objectName="muted")
        self.image_info.setWordWrap(True)
        self.image_info.setContentsMargins(10, 6, 10, 8)

        def column(top: QWidget, info: QLabel) -> QWidget:
            box = QWidget()
            lay = QVBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            lay.addWidget(top, 1)
            lay.addWidget(info, 0)
            return box

        # The locations column is one pane split top/bottom: pinned places and drives
        # above, the live folder tree below. Drag the handle to trade one for the other.
        self.location_split = QSplitter(Qt.Orientation.Vertical)
        self.location_split.addWidget(self.places)
        self.location_split.addWidget(self.tree)
        self.location_split.setSizes([240, 470])
        self.location_split.setStretchFactor(1, 1)
        self.location_split.setStyleSheet(f"QSplitter::handle{{height:1px;}}")

        self.split = QSplitter()
        self.location_column = column(self.location_split, self.folder_info)
        self.location_column.setObjectName("paneColumn")
        self.gallery_column = column(self.sheet, self.image_info)
        self.gallery_column.setObjectName("paneColumn")
        self.split.addWidget(self.location_column)
        self.split.addWidget(self.gallery_column)
        self.split.addWidget(self.view)
        self.split.setSizes([240, 380, 800])
        self.split.setStretchFactor(2, 1)
        self.split.setStyleSheet(f"QSplitter::handle{{width:1px;}}")

        self.rail = DisclosureRail()
        self.rail.toggled.connect(self._rail_toggled)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.rail, 0)
        layout.addWidget(self.split, 1)
        self.set_folder(start_dir)

    # -- data flow ---------------------------------------------------------------
    def set_folder(self, folder: Path) -> None:
        self.folder = Path(folder)
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

    def rescan(self) -> None:
        self.sheet.clear()
        self.paths = [path for _mtime, path in scan_images(self.folder)]
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
            item.setToolTip(Path(path).name)
            self.sheet.addItem(item)
            cached = thumb_path_for(path)
            if cached is not None and cached.exists():
                item.setIcon(QIcon(str(cached)))
            else:
                self.pool.start(ThumbJob(row, path, self.signal))
        if self.paths:
            self.sheet.setCurrentRow(0)
        newest = Path(self.paths[0]).name if self.paths else "—"
        self.folder_info.setText(
            f"{self.folder}\n{len(self.paths)} images (recursive) · newest: {newest}")

    def _thumb_ready(self, row: int, path: str, thumb_file: str) -> None:
        if row < self.sheet.count() and self.paths[row] == path:
            self.sheet.item(row).setIcon(QIcon(thumb_file))

    def _image_selected(self, row: int) -> None:
        if not 0 <= row < len(self.paths):
            return
        path = self.paths[row]
        frames = load_frames(path)
        self.view.source_path = path  # so Ctrl+drag out of the viewport carries the file
        self.view.show_frames(frames)
        try:
            size_kb = Path(path).stat().st_size / 1024
        except OSError:
            size_kb = 0
        dims = f"{frames[0].width()}×{frames[0].height()}" if frames else "unreadable"
        stack = f" · {len(frames)} slices" if len(frames) > 1 else ""
        self.image_info.setText(f"{Path(path).name}\n{dims}{stack} · {size_kb:,.0f} KB")

    def set_details_visible(self, on: bool) -> None:
        self.folder_info.setVisible(on)
        self.image_info.setVisible(on)

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
        self.places.select_path(self.folder)

    # -- panes -------------------------------------------------------------------
    def _panes(self) -> dict[str, QWidget]:
        return {
            "locations": self.location_column,
            "gallery": self.gallery_column,
            "viewer": self.view,
        }

    def set_focus_mode(self, on: bool) -> None:
        self.location_column.setVisible(not on)
        self.gallery_column.setVisible(not on)
        self.view.setVisible(True)
        self.sync_rail()

    def set_pane_visible(self, pane: str, on: bool) -> None:
        widgets = self._panes()
        if pane in widgets:
            widgets[pane].setVisible(on)
            self.sync_rail()

    def pane_visible(self, pane: str) -> bool:
        return not self._panes()[pane].isHidden()

    def sync_rail(self) -> None:
        self.rail.sync({pane: self.pane_visible(pane) for pane in ("locations", "gallery")})

    def _rail_toggled(self, pane: str, visible: bool) -> None:
        self.set_pane_visible(pane, visible)
        self.panes_changed.emit()

    def new_folder(self, name: str) -> Path | None:
        target = self.folder / name.strip()
        if not name.strip() or target.exists():
            return None
        try:
            target.mkdir(parents=True)
        except OSError:
            return None
        return target


class LightTableWindow(QMainWindow):
    def __init__(self, start: Path | None = None):
        super().__init__()
        self.setWindowTitle("Light Table — 2Photon / The Elements")
        self.setWindowIcon(QIcon(str(ICON)))
        self.resize(1440, 900)
        # One translucent slab, blurred by the compositor where one is offered.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.focus_mode = True
        self.details_on = True
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.tabBarDoubleClicked.connect(self._rename_tab)
        self.tabs.currentChanged.connect(self._sync_pane_actions)
        add_tab = QPushButton("＋")
        add_tab.setToolTip("New tab (Ctrl+T)")
        add_tab.setFixedSize(30, 26)
        add_tab.clicked.connect(lambda: self.new_tab(self.current_tab().folder))
        self.tabs.setCornerWidget(add_tab, Qt.Corner.TopRightCorner)
        self.setCentralWidget(self.tabs)
        self._build_menus()
        self._build_toolbar()
        self._build_keys()
        start = Path(start) if start else Path.home() / "Pictures"
        if start.is_file():
            folder, file = start.parent, start
        else:
            folder, file = (start if start.is_dir() else Path.home()), None
        self.new_tab(folder)
        if file is not None:
            tab = self.current_tab()
            if str(file) in tab.paths:
                tab.sheet.setCurrentRow(tab.paths.index(str(file)))

    def paintEvent(self, event) -> None:
        """The slab. A translucent window paints no background of its own, and a
        stylesheet rule on QMainWindow does not fill one either — so fill it here, once,
        and let every child compose on top of it."""
        QPainter(self).fillRect(self.rect(), CHROME)
        super().paintEvent(event)

    def current_tab(self) -> Tab:
        return self.tabs.currentWidget()

    def new_tab(self, folder: Path | None = None) -> None:
        tab = Tab(folder or Path.home() / "Pictures")
        tab.panes_changed.connect(self._sync_pane_actions)
        tab.favorites_changed.connect(self._reload_places)
        self.tabs.addTab(tab, tab.folder.name or str(tab.folder))
        self.tabs.setCurrentWidget(tab)
        tab.set_focus_mode(self.focus_mode)
        tab.rail.setVisible(not self.isFullScreen())
        self._sync_pane_actions()

    def _reload_places(self) -> None:
        """A favorite pinned in one tab shows up in all of them — same file underneath."""
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            if tab is not self.sender():
                tab.reload_places()

    def _close_tab(self, index: int) -> None:
        if self.tabs.count() > 1:
            self.tabs.removeTab(index)

    def _rename_tab(self, index: int) -> None:
        if index < 0:
            return
        current = self.tabs.tabText(index)
        name, ok = QInputDialog.getText(self, "Rename Tab", "Tab name:", text=current)
        if ok and name.strip():
            self.tabs.setTabText(index, name.strip())

    # -- chrome: menus only, no buttons anywhere -----------------------------------
    def _build_menus(self) -> None:
        def act(menu, text, shortcut, fn):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(fn)
            menu.addAction(action)

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
        view_menu = self.menuBar().addMenu("&View")
        act(view_menu, "Fit", "F", lambda: self.current_tab().view.fit())
        act(view_menu, "Fill", "Shift+F", lambda: self.current_tab().view.fill())
        act(view_menu, "1:1", "1", lambda: self.current_tab().view.one_to_one())
        act(view_menu, "Details", "W", self.toggle_details)
        act(view_menu, "Focus Mode", "Ctrl+Shift+F", self.toggle_focus)
        act(view_menu, "Fullscreen", "Alt+F", self.toggle_fullscreen)

    def _build_toolbar(self) -> None:
        bar = QToolBar("File and panes", self)
        bar.setObjectName("lighttable-toolbar")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)

        def button(text: str, tooltip: str, fn, shortcut: str = "") -> QAction:
            action = QAction(text, self)
            action.setToolTip(tooltip)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(fn)
            bar.addAction(action)
            return action

        button("＋ Tab", "New tab", lambda: self.new_tab(self.current_tab().folder))
        bar.addSeparator()
        button("Copy", "Copy selected files", lambda: self.current_tab().copy_selection())
        button("Paste", "Paste files or image", lambda: self.current_tab().paste_clipboard())
        button("Rename", "Rename selected file", lambda: self.current_tab().rename())
        button("Copy Path", "Copy full file paths", lambda: self.current_tab().copy_paths())
        button("Reveal", "Reveal selected file in the file manager", lambda: self.current_tab().reveal())
        bar.addSeparator()

        self.pane_actions: dict[str, QAction] = {}
        for pane, label in (
            ("locations", "Locations"),
            ("gallery", "Gallery"),
            ("viewer", "Viewer"),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(
                lambda checked, name=pane: self._set_current_pane(name, checked))
            bar.addAction(action)
            self.pane_actions[pane] = action
        self.focus_action = QAction("Focus", self)
        self.focus_action.setCheckable(True)
        self.focus_action.setChecked(True)
        self.focus_action.triggered.connect(self._set_focus)
        bar.addAction(self.focus_action)

    def _set_current_pane(self, pane: str, visible: bool) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        tab.set_pane_visible(pane, visible)
        self.focus_mode = (
            not tab.pane_visible("locations")
            and not tab.pane_visible("gallery")
            and tab.pane_visible("viewer")
        )
        self.focus_action.blockSignals(True)
        self.focus_action.setChecked(self.focus_mode)
        self.focus_action.blockSignals(False)

    def _set_focus(self, on: bool) -> None:
        self.focus_mode = on
        tab = self.current_tab()
        if tab is not None:
            tab.set_focus_mode(on)
        self._sync_pane_actions()

    def _sync_pane_actions(self, *args) -> None:
        if not hasattr(self, "pane_actions"):
            return
        tab = self.current_tab()
        if tab is None:
            return
        for pane, action in self.pane_actions.items():
            action.blockSignals(True)
            action.setChecked(tab.pane_visible(pane))
            action.blockSignals(False)
        self.focus_action.blockSignals(True)
        self.focus_action.setChecked(
            not tab.pane_visible("locations")
            and not tab.pane_visible("gallery")
            and tab.pane_visible("viewer"))
        self.focus_action.blockSignals(False)

    def _new_folder(self) -> None:
        tab = self.current_tab()
        name, ok = QInputDialog.getText(self, "New Folder", f"Create in {tab.folder}:")
        if ok and name:
            tab.new_folder(name)

    def _build_keys(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._escape)

    def toggle_details(self) -> None:
        self.details_on = not self.details_on
        for index in range(self.tabs.count()):
            self.tabs.widget(index).set_details_visible(self.details_on)

    def toggle_focus(self) -> None:
        self.focus_mode = not self.focus_mode
        self.current_tab().set_focus_mode(self.focus_mode)
        self._sync_pane_actions()

    def toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()
        self._sync_rails()

    def _sync_rails(self) -> None:
        """Fullscreen means nothing but the picture — even the disclosure gutter goes."""
        for index in range(self.tabs.count()):
            self.tabs.widget(index).rail.setVisible(not self.isFullScreen())

    def _escape(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self._sync_rails()
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
    configure_application(app, "Light Table")
    app.setStyleSheet(HOUSE_STYLE + MATERIAL_STYLE)
    window = LightTableWindow(Path(options.path) if options.path else None)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
