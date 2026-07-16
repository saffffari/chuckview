#!/usr/bin/env python3
"""Light Table — the 2D image viewer. Three columns, no buttons, ultra snappy.

Columns: folder tree · recursive contact sheet (newest first) · viewport.
Controls: LMB pan · RMB drag zoom (horizontal RMB scrubs z-stacks) · scroll zoom
(slices on stacks) · horizontal wheel / arrows / Space / Shift+Space next-prev ·
MX forward button pans, back button holds a compare with the last-viewed image ·
F fit · Shift+F fill · Ctrl+Shift+F focus (hide columns) · Alt+F fullscreen · 1 = 1:1.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import (
    QDir, QModelIndex, QObject, QRunnable, QSize, Qt, QThreadPool, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QFileSystemModel, QIcon, QImage, QImageReader, QKeySequence,
    QPixmap, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QGraphicsPixmapItem, QGraphicsScene,
    QGraphicsView, QLabel, QListWidget, QListWidgetItem, QListView,
    QMainWindow, QSplitter, QTabWidget, QTreeView, QVBoxLayout, QWidget,
)

import shutil

from PyQt6.QtCore import QMimeData, QUrl
from PyQt6.QtWidgets import QAbstractItemView, QInputDialog

from apps.common import BG, BORDER, INK, MUTE, PANEL, SELECT, configure_application
from apps.lighttable.core import THUMB_SIZE, cache_dir, scan_images, thumb_path_for

HERE = Path(__file__).resolve().parent
ICON = HERE / "assets" / "Lt.svg"

PATH_ROLE = Qt.ItemDataRole.UserRole


class ThumbSignal(QObject):
    done = pyqtSignal(int, str)  # row, thumbnail file


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
        self.signal.done.emit(self.row, str(target))


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

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self.item)
        self.setBackgroundBrush(Qt.GlobalColor.transparent)
        self.setStyleSheet(f"background:{BG}; border:0;")
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.frames: list[QImage] = []
        self.slice = 0
        self.compare_pixmap: QPixmap | None = None  # previously viewed image
        self._peeking = False
        self._drag_button: Qt.MouseButton | None = None
        self._last_pos = None
        self._fit_on_load = True

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

    def fill(self) -> None:
        if not self.item.pixmap().isNull():
            self.fitInView(self.item, Qt.AspectRatioMode.KeepAspectRatioByExpanding)

    def one_to_one(self) -> None:
        self.resetTransform()

    def zoom(self, factor: float) -> None:
        self.scale(factor, factor)

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
        if button in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton,
                      Qt.MouseButton.ForwardButton):
            self._drag_button = button
            self._last_pos = event.position()
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
    """The recursive gallery. RMB drag resizes thumbnails (zooms this pane)."""

    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setWordWrap(False)
        # Shift-click = consecutive range, Ctrl-click = toggle one — Qt's extended mode.
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.thumb = 128
        self._apply_size()
        self.setStyleSheet(
            f"QListWidget{{background:{BG}; border:0;}}"
            f"QListWidget::item{{color:{MUTE}; border:1px solid transparent;}}"
            f"QListWidget::item:selected{{border:1px solid {SELECT}; background:{PANEL};}}")
        self._rmb_last = None

    def _apply_size(self) -> None:
        self.setIconSize(QSize(self.thumb, self.thumb))
        self.setGridSize(QSize(self.thumb + 12, self.thumb + 12))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._rmb_last = event.position()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._rmb_last is not None:
            delta = event.position().y() - self._rmb_last.y()
            self._rmb_last = event.position()
            self.thumb = max(48, min(512, round(self.thumb * (1.01 ** -delta))))
            self._apply_size()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._rmb_last = None
        super().mouseReleaseEvent(event)

    def mimeData(self, items) -> QMimeData:
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(item.data(PATH_ROLE)) for item in items])
        return data


class FolderTree(QTreeView):
    """Directory tree that accepts image drops: dropping moves the files."""

    files_moved = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)

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


class Tab(QWidget):
    """One tab: tree · contact sheet · viewport, with its own folder and state."""

    def __init__(self, start_dir: Path):
        super().__init__()
        self.pool = QThreadPool.globalInstance()
        self.signal = ThumbSignal()
        self.signal.done.connect(self._thumb_ready)
        self.paths: list[str] = []

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot)
        self.model.setRootPath(str(Path.home()))
        self.tree = FolderTree()
        self.tree.setModel(self.model)
        self.tree.files_moved.connect(self.rescan)
        self.tree.setRootIndex(self.model.index(str(Path.home())))
        for column in range(1, self.model.columnCount()):
            self.tree.hideColumn(column)
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(f"QTreeView{{background:{BG}; border:0; color:{INK};}}")
        self.tree.selectionModel().currentChanged.connect(self._folder_changed)

        self.sheet = ContactSheet()
        self.sheet.currentRowChanged.connect(self._image_selected)

        self.view = Viewport()
        self.view.request_step.connect(self.step)

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

        self.split = QSplitter()
        self.split.addWidget(column(self.tree, self.folder_info))
        self.split.addWidget(column(self.sheet, self.image_info))
        self.split.addWidget(self.view)
        self.split.setSizes([220, 380, 800])
        self.split.setStyleSheet(f"QSplitter::handle{{background:{BORDER}; width:1px;}}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.split)
        self.set_folder(start_dir)

    # -- data flow ---------------------------------------------------------------
    def set_folder(self, folder: Path) -> None:
        self.folder = Path(folder)
        index = self.model.index(str(self.folder))
        if index.isValid():
            self.tree.setCurrentIndex(index)
        self.rescan()

    def _folder_changed(self, index: QModelIndex) -> None:
        if index.isValid():
            self.folder = Path(self.model.filePath(index))
            self.rescan()

    def rescan(self) -> None:
        self.sheet.clear()
        self.paths = [path for _mtime, path in scan_images(self.folder)]
        placeholder = QIcon()
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

    def _thumb_ready(self, row: int, thumb_file: str) -> None:
        if row < self.sheet.count() and self.sheet.item(row).data(PATH_ROLE) == self.paths[row]:
            self.sheet.item(row).setIcon(QIcon(thumb_file))

    def _image_selected(self, row: int) -> None:
        if not 0 <= row < len(self.paths):
            return
        path = self.paths[row]
        frames = load_frames(path)
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

    def set_focus_mode(self, on: bool) -> None:
        self.tree.setVisible(not on)
        self.sheet.setVisible(not on)

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
        self.focus_mode = False
        self.details_on = True
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.setCentralWidget(self.tabs)
        self._build_menus()
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

    def current_tab(self) -> Tab:
        return self.tabs.currentWidget()

    def new_tab(self, folder: Path | None = None) -> None:
        tab = Tab(folder or Path.home() / "Pictures")
        self.tabs.addTab(tab, tab.folder.name or str(tab.folder))
        self.tabs.setCurrentWidget(tab)

    def _close_tab(self, index: int) -> None:
        if self.tabs.count() > 1:
            self.tabs.removeTab(index)

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
        act(file_menu, "Close Tab", "Ctrl+W", lambda: self._close_tab(self.tabs.currentIndex()))
        act(file_menu, "Quit", "Ctrl+Q", self.close)
        view_menu = self.menuBar().addMenu("&View")
        act(view_menu, "Fit", "F", lambda: self.current_tab().view.fit())
        act(view_menu, "Fill", "Shift+F", lambda: self.current_tab().view.fill())
        act(view_menu, "1:1", "1", lambda: self.current_tab().view.one_to_one())
        act(view_menu, "Details", "W", self.toggle_details)
        act(view_menu, "Focus Mode", "Ctrl+Shift+F", self.toggle_focus)
        act(view_menu, "Fullscreen", "Alt+F", self.toggle_fullscreen)

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
        for index in range(self.tabs.count()):
            self.tabs.widget(index).set_focus_mode(self.focus_mode)

    def toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _escape(self) -> None:
        if self.isFullScreen():
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
    configure_application(app, "Light Table")
    window = LightTableWindow(Path(options.path) if options.path else None)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
