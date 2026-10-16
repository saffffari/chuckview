#!/usr/bin/env python3
"""ChuckView — find and collect medical images for research datasets.

A catalog you import into, laid out like a library: **sources** on the left (the catalog,
collections, imported folders, imports), the **filter bar** and **grid** in the middle, and
the **Info** panel on the right (Ctrl+I) with the facts, tags, notes and the masked DICOM
header. Double-click, Enter or E opens the **loupe**: the full-resolution picture, a CT
scrolled slice by slice, windowed live. G goes back to the grid.

Keys (Lightroom's, where Lightroom has one):

- ← → previous / next · ↑ ↓ PgUp PgDn and the wheel move through slices in the loupe
- P pick · X reject · U unflag · 0–5 rating · 6 7 8 9 red yellow green blue label
- Ctrl+K tags · Ctrl+' note · Ctrl+N collection from selection · Ctrl+Alt+N smart
  collection from the search · B add to the target collection
- L window preset · I invert · V axial/coronal/sagittal · Z fit/1:1 · Shift+Z fill
- MMB or Shift+LMB drag: window/level · Ctrl+Shift+I import · Ctrl+Shift+E export
- \\ shows and hides the facet columns · Ctrl+F searches

Named for Chuck, whose idea it was.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import QItemSelectionModel, QSize, QStandardPaths, Qt, QThreadPool, QTimer
from PyQt6.QtGui import QAction, QGuiApplication, QIcon, QImage, QImageReader, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QSplitter, QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

from chuckview import dicom
from chuckview.catalog import LABELS, SUFFIX, Catalog
from chuckview.common import configure_application
from chuckview.core import config_dir, open_path_command, reveal_command
from chuckview.importer import ImportDialog
from chuckview.inspector import Inspector
from chuckview.jobs import CommitJob, MissingJob, PreviewJob, Signals
from chuckview.library import FilterBar, Grid, Sources
from chuckview.panels import GUTTER, Panel, PanelState, column_sizes, load_layout, save_layout
from chuckview.previews import PreviewStore
from chuckview.stack import SYNC_PIXELS, Prefetcher
from chuckview.style import CHROME, MATERIAL_STYLE
from chuckview.viewer import ASYNC_BYTES, LoadJob, LoadSignal, Viewport, load_frames

HERE = Path(__file__).resolve().parent
ICON = HERE / "assets" / "chuckview.svg"
APP_ID = "Saffari.ChuckView"

COLUMNS = ("sources", "center", "inspector")
DEFAULT_PANELS = (
    PanelState("sources", width=250, open=True),
    PanelState("center", width=900, open=True),
    PanelState("inspector", width=360, open=True),
)


# --- small persistent settings: one key=value per line, edit it by hand ----------------------

def settings_file() -> Path:
    return config_dir() / "settings.txt"


def load_settings() -> dict[str, str]:
    try:
        lines = settings_file().read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    out = {}
    for line in lines:
        key, eq, value = line.partition("=")
        if eq and not key.strip().startswith("#"):
            out[key.strip()] = value.strip()
    return out


def save_settings(values: dict[str, str]) -> None:
    try:
        target = settings_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(f"{k} = {v}" for k, v in sorted(values.items()))
        target.write_text("# ChuckView settings. Edit freely.\n" + body + "\n", encoding="utf-8")
    except OSError:
        pass


def default_catalog_path() -> Path:
    documents = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
    return Path(documents) / "ChuckView" / f"Default{SUFFIX}"


class LibraryWindow(QMainWindow):
    def __init__(self, catalog_path: Path | None = None, import_folder: str | None = None):
        super().__init__()
        self.setWindowIcon(QIcon(str(ICON)))
        self.resize(1560, 940)
        self.settings = load_settings()
        self.catalog: Catalog | None = None
        self.store: PreviewStore | None = None
        self.target_collection: int | None = None
        self.signals = Signals()
        self.signals.preview.connect(self._preview_ready)
        self.signals.checked.connect(lambda _n: self._after_missing_check())
        self.signals.progress.connect(self._import_progress)
        self.signals.imported.connect(self._imported)
        self.signals.failed.connect(lambda message: self.flash(f"import failed: {message}"))
        self.previews = QThreadPool(self)
        self.previews.setMaxThreadCount(max(1, min(4, (os.cpu_count() or 2) // 2)))
        self.background = QThreadPool(self)
        self.background.setMaxThreadCount(1)
        self.sources_cache: OrderedDict[int, dicom.SliceSource] = OrderedDict()
        self.prefetcher = Prefetcher(self)
        self.prefetcher.frame_ready.connect(self._frame_decoded)
        self.load_signal = LoadSignal()
        self.load_signal.done.connect(self._frames_loaded)
        self.loader = QThreadPool(self)
        self.loader.setMaxThreadCount(1)
        self._generation = 0
        self._shown_id: int | None = None
        self._step_direction = 1

        # -- left: sources ------------------------------------------------------------
        self.sources = Sources()
        self.sources.chosen.connect(self._source_chosen)
        self.sources.menu_requested.connect(self._source_menu)
        # -- middle: filter bar over the grid / loupe -------------------------------------
        self.filter = FilterBar()
        self.filter.changed.connect(self.refresh)
        self.grid = Grid()
        self.grid.model_.previews_wanted.connect(self._want_previews)
        self.grid.opened.connect(lambda _row: self.show_loupe())
        self.grid.context_requested.connect(self._grid_menu)
        self.grid.selectionModel().currentChanged.connect(lambda *_: self._current_changed())
        self.view = Viewport()
        self.view.request_step.connect(self.step)
        self.view.state_changed.connect(self._viewer_state)
        self.view.context_requested.connect(self._grid_menu)
        loupe = QWidget()
        loupe.setObjectName("panelBody")
        loupe_box = QVBoxLayout(loupe)
        loupe_box.setContentsMargins(0, 0, 0, 0)
        loupe_box.setSpacing(0)
        loupe_box.addWidget(self.view, 1)
        loupe_box.addWidget(self.view.ruler, 0)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.grid)
        self.stack.addWidget(loupe)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        center = QWidget()
        center.setObjectName("panelBody")
        center_box = QVBoxLayout(center)
        center_box.setContentsMargins(0, 0, 0, 0)
        center_box.setSpacing(0)
        center_box.addWidget(self.filter, 0)
        center_box.addWidget(self.stack, 1)
        center_box.addWidget(self.status, 0)
        # -- right: info -------------------------------------------------------------------
        self.inspector = Inspector()
        self.inspector.tag_targets = self.targets
        self.inspector.edited.connect(lambda item_id: self._marks_changed([item_id]))

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(GUTTER)
        self.splitter.setChildrenCollapsible(False)
        self.panels = {
            "sources": Panel("sources", "Library", self.sources),
            "center": Panel("center", "Grid", center),
            "inspector": Panel("inspector", "Info", self.inspector),
        }
        for name in COLUMNS:
            self.splitter.addWidget(self.panels[name])
        layout = load_layout(DEFAULT_PANELS)
        for name, panel in self.panels.items():
            panel.state = layout.get(name, panel.state)
        self.panels["center"].state = PanelState("center", self.panels["center"].state.width, True)
        self.splitter.setStretchFactor(1, 1)
        shell = QWidget()
        shell.setObjectName("shell")
        shell_box = QVBoxLayout(shell)
        shell_box.setContentsMargins(0, 0, 0, 0)
        shell_box.addWidget(self.splitter)
        self.setCentralWidget(shell)

        self.toast = QLabel("", self)
        self.toast.setObjectName("toast")
        self.toast.setVisible(False)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self.toast.setVisible(False))
        self._facet_timer = QTimer(self)
        self._facet_timer.setSingleShot(True)
        self._facet_timer.setInterval(60)
        self._facet_timer.timeout.connect(
            lambda: self.filter.refresh(self.catalog, self.current_source()))
        # How much of the stack is in memory, re-read a few times a second while it fills.
        self._facts_timer = QTimer(self)
        self._facts_timer.setSingleShot(True)
        self._facts_timer.setInterval(250)
        self._facts_timer.timeout.connect(self._viewer_state)

        self._build_menus()
        self.filter.set_facets_visible(self.settings.get("facets", "1") == "1")
        self.grid.set_cell(int(self.settings.get("cell", "168") or 168))
        self.apply_columns()
        QApplication.instance().focusChanged.connect(self._focus_changed)

        path = catalog_path or (Path(self.settings["catalog"]) if self.settings.get("catalog")
                                else default_catalog_path())
        self.open_catalog(path, create=not Path(path).exists())
        if import_folder:
            QTimer.singleShot(0, lambda: self.import_folder(import_folder))

    # -- layout ------------------------------------------------------------------------
    def apply_columns(self) -> None:
        for name in COLUMNS:
            self.panels[name].setVisible(self.panels[name].state.open)
        ordered = [self.panels[name].state for name in COLUMNS]
        # The middle takes the leftovers, not the right panel: reorder for column_sizes.
        sizes = column_sizes([ordered[0], ordered[2], ordered[1]], max(1, self.splitter.width()))
        self.splitter.setSizes([sizes[0], sizes[2], sizes[1]])

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.apply_columns()

    def _remember_widths(self) -> None:
        sizes = self.splitter.sizes()
        for name, size in zip(COLUMNS, sizes):
            panel = self.panels[name]
            if panel.state.open and size > 0 and name != "center":
                panel.state = PanelState(name, size, True)

    def toggle_panel(self, name: str) -> None:
        self._remember_widths()
        panel = self.panels[name]
        panel.state = PanelState(name, panel.state.width, not panel.state.open)
        self.apply_columns()
        if name == "inspector" and panel.state.open:
            self._show_inspector()

    def closeEvent(self, event) -> None:
        self._remember_widths()
        try:
            save_layout(self.panels[name].state for name in COLUMNS)
        except OSError:
            pass
        self.settings["cell"] = str(self.grid.delegate.cell.width())
        self.settings["facets"] = "1" if self.filter.facets_box.isVisible() else "0"
        if self.catalog is not None:
            self.settings["catalog"] = str(self.catalog.path)
        save_settings(self.settings)
        for source in self.sources_cache.values():
            Prefetcher.release(source)
        # Let background work finish before the catalog goes away under it: a check that
        # lands after close would be talking to a closed database.
        for pool in (self.previews, self.background, self.loader):
            pool.clear()
            pool.waitForDone(10000)
        self.prefetcher.pool.clear()
        self.prefetcher.wait(10000)
        for signal in (self.signals.checked, self.signals.preview, self.signals.imported):
            try:
                signal.disconnect()
            except TypeError:
                pass
        for timer in (self._facet_timer, self._facts_timer, self._toast_timer):
            timer.stop()
        self.filter._debounce.stop()
        self.inspector._header_timer.stop()
        self.inspector.catalog = None
        if self.catalog is not None:
            self.catalog.close()
            self.catalog = None
        super().closeEvent(event)

    def paintEvent(self, event) -> None:
        from PyQt6.QtGui import QPainter

        QPainter(self).fillRect(self.rect(), CHROME)
        super().paintEvent(event)

    def flash(self, message: str, msecs: int = 1800) -> None:
        self.toast.setText(message.upper())
        self.toast.adjustSize()
        center = self.panels["center"].geometry()
        self.toast.move(center.center().x() - self.toast.width() // 2,
                        center.bottom() - self.toast.height() - 40)
        self.toast.setVisible(True)
        self.toast.raise_()
        self._toast_timer.start(msecs)

    # -- the catalog ------------------------------------------------------------------
    def open_catalog(self, path: str | os.PathLike, create: bool = False) -> None:
        try:
            catalog = Catalog.create(path) if create else Catalog(path)
        except (OSError, Exception) as error:  # noqa: BLE001
            QMessageBox.warning(self, "ChuckView", f"Could not open {path}:\n{error}")
            return
        if self.catalog is not None:
            self.catalog.close()
        self.catalog = catalog
        self.store = PreviewStore(catalog.data_dir)
        self.settings["catalog"] = str(catalog.path)
        self.setWindowTitle(f"{catalog.path.stem} — ChuckView")
        self.sources_cache.clear()
        self.sources.reload(catalog)
        self.filter.clear()
        self.refresh()
        self.background.start(MissingJob(str(catalog.path), self.signals))

    def _after_missing_check(self) -> None:
        if self.catalog is None:
            return
        self.sources.reload(self.catalog, self.current_source())
        self.refresh(keep=True)

    def new_catalog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "New catalog", str(default_catalog_path().parent),
                                              f"ChuckView catalog (*{SUFFIX})")
        if path:
            self.open_catalog(path, create=True)

    def choose_catalog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open catalog", str(default_catalog_path().parent),
                                              f"ChuckView catalog (*{SUFFIX})")
        if path:
            self.open_catalog(path)

    # -- import ------------------------------------------------------------------------
    def import_folder(self, folder: str | None = None) -> None:
        if self.catalog is None:
            return
        dialog = ImportDialog(self.catalog, folder, self)
        if folder is None:
            QTimer.singleShot(0, dialog.choose)
        if dialog.exec() and dialog.plan is not None:
            chosen = dialog.chosen()
            self.flash(f"importing {len(chosen)} items", 4000)
            self.background.start(CommitJob(str(self.catalog.path), dialog.plan, chosen,
                                            self.signals))

    def _import_progress(self, stage: str, done: int, total: int) -> None:
        if stage == "importing" and total:
            self.status.setText(f"  importing {done:,}/{total:,}")

    def _imported(self, import_id: int) -> None:
        if self.catalog is None:
            return
        added = self.catalog.db.execute("SELECT added FROM imports WHERE id = ?",
                                        (import_id,)).fetchone()
        self.flash(f"imported {added[0] if added else 0} items")
        self.sources.reload(self.catalog, ("last_import", None))
        self.sources.select(("last_import", None))
        self.refresh()

    # -- searching ---------------------------------------------------------------------
    def current_source(self):
        return self.sources.current()

    def _source_chosen(self, _source) -> None:
        self.refresh()

    def refresh(self, keep: bool = False) -> None:
        if self.catalog is None:
            self.grid.model_.set_rows([], None)
            return
        selected = set(self.grid.selected_ids()) if keep else set()
        current = self.current_id() if keep else None
        source = self.current_source()
        order = "collection" if source and source[0] == "collection" else self.settings.get(
            "order", "added")
        rows, compiled = self.catalog.search(self.filter.query(), source,
                                             self.filter.selections(), order)
        self.grid.model_.set_rows(rows, self.store)
        total = self.catalog.count()
        parts = [f"  {len(rows):,} of {total:,}"]
        if compiled.terms:
            parts.append("  ·  " + "  ".join(compiled.terms))
        if compiled.errors:
            parts.append("  ·  " + "; ".join(compiled.errors))
        self.status.setText("".join(parts))
        self.panels["center"].title.set_value(f"{len(rows):,} items")
        self._facet_timer.start()
        if rows:
            restore = [i for i, row in enumerate(rows) if row.id in selected]
            target = next((i for i, row in enumerate(rows) if row.id == current), None)
            model = self.grid.selectionModel()
            for index in restore:
                model.select(self.grid.model_.index(index),
                             QItemSelectionModel.SelectionFlag.Select)
            self.grid.setCurrentIndex(self.grid.model_.index(target if target is not None else 0))
        else:
            self.inspector.show_item(self.catalog, None)

    def focus_search(self) -> None:
        self.filter.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.filter.search.selectAll()

    def toggle_facets(self) -> None:
        self.filter.set_facets_visible(not self.filter.facets_box.isVisible())

    # -- selection -----------------------------------------------------------------------
    def current_id(self) -> int | None:
        row = self.grid.current_row()
        return row.id if row is not None else None

    def targets(self) -> list[int]:
        """What a mark applies to: the one in the loupe, else everything selected."""
        if self.stack.currentIndex() == 1:
            return [self._shown_id] if self._shown_id is not None else []
        ids = self.grid.selected_ids()
        if not ids and self.current_id() is not None:
            ids = [self.current_id()]
        return ids

    def _current_changed(self) -> None:
        self._show_inspector()
        if self.stack.currentIndex() == 1:
            self._show_in_loupe()

    def _show_inspector(self) -> None:
        if self.panels["inspector"].state.open:
            self.inspector.show_item(self.catalog, self.current_id())
            if self.stack.currentIndex() == 1:
                self._viewer_state()

    def step(self, direction: int) -> None:
        self._step_direction = 1 if direction > 0 else -1
        index = self.grid.currentIndex()
        row = (index.row() if index.isValid() else -1) + direction
        if 0 <= row < self.grid.model_.rowCount():
            self.grid.setCurrentIndex(self.grid.model_.index(row))

    # -- grid / loupe ------------------------------------------------------------------
    def show_grid(self) -> None:
        self.stack.setCurrentIndex(0)
        self.filter.setVisible(True)
        self.panels["center"].title._title = "GRID"
        self.panels["center"].title.update()
        self.grid.setFocus()

    def show_loupe(self) -> None:
        if self.current_id() is None:
            return
        self.stack.setCurrentIndex(1)
        # The picture gets the height: the filter bar is for choosing, the loupe is for
        # looking. G (or Esc) brings it back with the search exactly as it was.
        self.filter.setVisible(False)
        self.panels["center"].title._title = "LOUPE"
        self.panels["center"].title.update()
        self._show_in_loupe()
        self.view.setFocus()

    def toggle_loupe(self) -> None:
        self.show_grid() if self.stack.currentIndex() == 1 else self.show_loupe()

    def _show_in_loupe(self) -> None:
        if self.catalog is None:
            return
        item_id = self.current_id()
        if item_id is None or item_id == self._shown_id:
            return
        self._generation += 1
        self._shown_id = item_id
        row = self.catalog.item(item_id)
        path = self.catalog.key_path(item_id)
        self.view.source_path = path
        if row["missing"] or not os.path.exists(path):
            self._show_preview(item_id, row)
            self.flash("original offline — showing the catalog's preview")
            return
        if row["kind"] != "image":
            self._show_dicom(item_id, row)
        else:
            try:
                large = Path(path).stat().st_size > ASYNC_BYTES
            except OSError:
                large = False
            if large:
                self._show_preview(item_id, row, QImageReader(path).size())
                self.loader.start(LoadJob(self._generation, path, self.load_signal))
            else:
                self.view.show_frames(load_frames(path))
        self._viewer_state()

    def _show_preview(self, item_id: int, row, size: QSize | None = None) -> bool:
        if self.store is None:
            return False
        image = QImage(str(self.store.preview_path(item_id)))
        if image.isNull():
            image = QImage(str(self.store.thumb_path(item_id)))
        if image.isNull():
            self.view.show_frames([])
            return False
        if size is None and row is not None and row["cols"]:
            aspect = (row["spacing_c"] or 1) / (row["spacing_r"] or 1) if row["spacing_r"] else 1
            size = QSize(round(row["cols"] * max(1.0, aspect)),
                         round(row["rows"] * max(1.0, 1 / aspect if aspect else 1)))
        self.view.show_placeholder(image, size)
        return True

    def _show_dicom(self, item_id: int, row) -> None:
        source = self.sources_cache.pop(item_id, None)
        if source is None:
            item = self.catalog.viewer_item(item_id)
            if item is None:
                return
            source = dicom.SliceSource(item)
            source.center = len(source) // 2
        self.sources_cache[item_id] = source
        while len(self.sources_cache) > 3:
            _old, old = self.sources_cache.popitem(last=False)
            Prefetcher.release(old)
        big = ((row["rows"] or 0) * (row["cols"] or 0) > SYNC_PIXELS
               and source.peek(len(source) // 2) is None)
        placeholder = big and self._show_preview(item_id, row)
        preset = self.view.preset if self.view.preset != "custom" else "auto"
        self.view.show_source(source, preset, keep_pixmap=placeholder)
        self.prefetcher.ensure(source)
        self._prefetch_next()

    def _prefetch_next(self) -> None:
        """Decode the next item in the direction you are going, so → onto a big film is
        instant instead of a wait."""
        index = self.grid.currentIndex()
        if not index.isValid() or self.catalog is None:
            return
        row = index.row() + self._step_direction
        if not 0 <= row < self.grid.model_.rowCount():
            return
        neighbour = self.grid.model_.rows[row]
        if neighbour.kind == "image" or neighbour.missing or neighbour.id in self.sources_cache:
            return
        item = self.catalog.viewer_item(neighbour.id)
        if item is None:
            return
        source = dicom.SliceSource(item)
        source.center = len(source) // 2
        self.sources_cache[neighbour.id] = source
        if self._shown_id in self.sources_cache:
            self.sources_cache.move_to_end(self._shown_id)
        self.prefetcher.ensure(source)

    def _frame_decoded(self, source, index: int) -> None:
        self.view.frame_decoded(source, index)
        if source is self.view.source:
            self.prefetcher.ensure(source)
            if not self._facts_timer.isActive():
                self._facts_timer.start()

    def _frames_loaded(self, generation: int, path: str, frames: list) -> None:
        if generation == self._generation:
            fitted = self.view._fitted
            self.view.show_frames(frames)
            if fitted:
                self.view.fit()

    def _viewer_state(self) -> None:
        if self.stack.currentIndex() != 1:
            self.inspector.set_viewer_rows([])
            return
        view, rows = self.view, []
        count = view.slice_count()
        if count > 1:
            rows.append(f"slc\t{view.slice + 1}/{count} {view.plane}")
            if view.source is not None and view.source.held() < len(view.source):
                rows.append(f"mem\t{view.source.held()}/{len(view.source)} slices decoded")
        if view.source is not None and view.window is not None:
            center, width = view.window
            rows.append(f"win\t{center:g}/{width:g} {view.preset}" + (" inv" if view.inverted else ""))
        self.inspector.set_viewer_rows(rows)

    # -- previews ------------------------------------------------------------------------
    def _want_previews(self, ids: list[int]) -> None:
        if self.catalog is None:
            return
        for start in range(0, len(ids), 12):
            self.previews.start(PreviewJob(str(self.catalog.path), ids[start:start + 12],
                                           self.signals))

    def _preview_ready(self, item_id: int, built: bool) -> None:
        self.grid.model_.preview_ready(item_id, built)

    # -- marks ---------------------------------------------------------------------------
    def _marks_changed(self, ids: list[int]) -> None:
        if self.catalog is None:
            return
        self.grid.model_.replace_rows(self.catalog.rows(ids))
        self.grid.viewport().update()
        self.inspector._fill_facts()

    def _after_mark(self, ids: list[int], message: str) -> None:
        self._marks_changed(ids)
        self.flash(message)
        if self.auto_advance.isChecked() and len(ids) == 1:
            self.step(1)

    def flag(self, value: int) -> None:
        ids = self.targets()
        if not ids or self.catalog is None:
            return
        if value == 0:
            self.catalog.set_flag(ids, 0)
            result = 0
        else:
            result = self.catalog.toggle_flag(ids, value)
        self._after_mark(ids, {1: "pick", -1: "reject", 0: "unflagged"}[result])

    def rate(self, stars: int) -> None:
        ids = self.targets()
        if not ids or self.catalog is None:
            return
        self.catalog.set_rating(ids, stars)
        self._after_mark(ids, "•" * stars if stars else "no rating")

    def label(self, name: str) -> None:
        ids = self.targets()
        if not ids or self.catalog is None:
            return
        result = self.catalog.toggle_label(ids, name)
        self._after_mark(ids, f"label {result}" if result else "no label")

    # -- collections ---------------------------------------------------------------------
    def new_collection(self, smart: bool = False) -> None:
        if self.catalog is None:
            return
        query = self.filter.query() if smart else None
        if smart and not query:
            self.flash("type a search first — a smart collection is a saved search")
            return
        name, ok = QInputDialog.getText(self, "Smart collection" if smart else "New collection",
                                        "Name:", text=query or "")
        if not ok or not name.strip():
            return
        try:
            ident = self.catalog.create_collection(name, query)
        except Exception as error:  # noqa: BLE001 - duplicate names
            QMessageBox.warning(self, "Collection", str(error))
            return
        if not smart:
            self.catalog.add_to_collection(ident, self.targets())
            self.target_collection = ident
        self.sources.reload(self.catalog, self.current_source())
        self.flash(f"collection {name}")

    def add_to_target(self) -> None:
        if self.catalog is None:
            return
        if self.target_collection is None:
            self.new_collection()
            return
        ids = self.targets()
        self.catalog.add_to_collection(self.target_collection, ids)
        self.sources.reload(self.catalog, self.current_source())
        self.flash(f"{len(ids)} added to collection")

    def remove_from_current_collection(self) -> None:
        source = self.current_source()
        if self.catalog is None or not source or source[0] != "collection":
            return
        self.catalog.remove_from_collection(source[1], self.targets())
        self.sources.reload(self.catalog, source)
        self.refresh(keep=True)

    # -- context menus -------------------------------------------------------------------
    def _grid_menu(self, global_pos) -> None:
        if self.catalog is None:
            return
        ids = self.targets()
        menu = QMenu(self)
        menu.addAction("Open in Loupe\tE", self.show_loupe)
        menu.addSeparator()
        menu.addAction("Pick\tP", lambda: self.flag(1))
        menu.addAction("Reject\tX", lambda: self.flag(-1))
        menu.addAction("Unflag\tU", lambda: self.flag(0))
        rating = menu.addMenu("Rating")
        for stars in range(6):
            rating.addAction(("•" * stars or "none") + f"\t{stars}",
                             lambda s=stars: self.rate(s))
        labels = menu.addMenu("Label")
        for key, name in zip("6789", LABELS):
            labels.addAction(f"{name}\t{key}", lambda n=name: self.label(n))
        labels.addAction(LABELS[4], lambda: self.label(LABELS[4]))
        add = menu.addMenu("Add to Collection")
        for ident, name, query, _count in self.catalog.collections():
            if not query:
                add.addAction(name, lambda i=ident: self._add_to(i))
        add.addSeparator()
        add.addAction("New Collection…\tCtrl+N", self.new_collection)
        source = self.current_source()
        if source and source[0] == "collection":
            menu.addAction("Remove from This Collection", self.remove_from_current_collection)
        menu.addSeparator()
        menu.addAction("Add Tags…\tCtrl+K", self._focus_tags)
        menu.addAction("Add Note…\tCtrl+'", self._focus_note)
        menu.addSeparator()
        menu.addAction("Copy Path", self.copy_paths)
        menu.addAction("Reveal in File Manager", self.reveal)
        menu.addSeparator()
        menu.addAction(f"Remove {len(ids)} from Catalog…", self.remove_from_catalog)
        menu.exec(global_pos)

    def _add_to(self, ident: int) -> None:
        self.target_collection = ident
        self.add_to_target()

    def _source_menu(self, source, global_pos) -> None:
        if self.catalog is None:
            return
        kind, ident = source
        menu = QMenu(self)
        if kind == "collection":
            menu.addAction("Rename…", lambda: self._rename_collection(ident))
            menu.addAction("Set as Target (B)", lambda: setattr(self, "target_collection", ident))
            menu.addAction("Export Manifest…", lambda: self.export(("collection", ident)))
            menu.addSeparator()
            menu.addAction("Delete Collection", lambda: self._delete_collection(ident))
        elif kind == "root":
            menu.addAction("Find Missing Folder…", lambda: self.relocate(ident))
            menu.addAction("Reveal in File Manager",
                           lambda: self._open_path(self.catalog.root_path(ident)))
            menu.addAction("Import Again (new files only)",
                           lambda: self.import_folder(self.catalog.root_path(ident)))
        else:
            menu.addAction("Export Manifest…", lambda: self.export(source))
        menu.exec(global_pos)

    def _rename_collection(self, ident: int) -> None:
        name, ok = QInputDialog.getText(self, "Rename collection", "Name:")
        if ok and name.strip():
            self.catalog.rename_collection(ident, name)
            self.sources.reload(self.catalog, self.current_source())

    def _delete_collection(self, ident: int) -> None:
        if QMessageBox.question(self, "Delete collection",
                                "Delete this collection? Its images stay in the catalog."
                                ) == QMessageBox.StandardButton.Yes:
            self.catalog.delete_collection(ident)
            if self.target_collection == ident:
                self.target_collection = None
            self.sources.reload(self.catalog)
            self.refresh()

    def relocate(self, root_id: int) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, f"Where is {self.catalog.root_path(root_id)} now?", str(Path.home()))
        if folder:
            online = self.catalog.relocate_root(root_id, folder)
            self.flash(f"{online} items found there")
            self.sources.reload(self.catalog, self.current_source())
            self.refresh(keep=True)

    def remove_from_catalog(self) -> None:
        ids = self.targets()
        if not ids or self.catalog is None:
            return
        if QMessageBox.question(
                self, "Remove from catalog",
                f"Remove {len(ids)} item(s) from the catalog?\nThe files themselves are not "
                "touched.") != QMessageBox.StandardButton.Yes:
            return
        self.catalog.remove_items(ids)
        for item_id in ids:
            if self.store is not None:
                self.store.remove(item_id)
        self.sources.reload(self.catalog, self.current_source())
        self.refresh()

    # -- export ------------------------------------------------------------------------
    def export(self, source=None) -> None:
        if self.catalog is None:
            return
        if source is not None:
            ids = [row.id for row in self.catalog.search(source=source)[0]]
        else:
            ids = self.grid.selected_ids()
            if len(ids) <= 1:
                ids = [row.id for row in self.grid.model_.rows]
        if not ids:
            self.flash("nothing to export")
            return
        path, _ = QFileDialog.getSaveFileName(self, f"Export manifest of {len(ids)} items",
                                              str(Path.home() / "manifest.csv"), "CSV (*.csv)")
        if not path:
            return
        text, ok = QInputDialog.getText(
            self, "Splits", "Train/val/test by patient (e.g. 70/15/15) — blank for none:",
            text=self.settings.get("splits", "70/15/15"))
        if not ok:
            return
        splits = None
        if text.strip():
            try:
                parts = tuple(float(p) for p in text.replace(",", "/").split("/"))
                if len(parts) != 3:
                    raise ValueError
                splits = parts
                self.settings["splits"] = text.strip()
            except ValueError:
                QMessageBox.warning(self, "Splits", "Three numbers, like 70/15/15.")
                return
        target = self.catalog.export_manifest(ids, path, splits)
        self.flash(f"exported {len(ids)} items to {target.name}")

    # -- files ---------------------------------------------------------------------------
    def copy_paths(self) -> None:
        if self.catalog is None:
            return
        paths = [self.catalog.key_path(i) for i in self.targets()]
        QGuiApplication.clipboard().setText("\n".join(paths))

    def reveal(self) -> None:
        ids = self.targets()
        if ids and self.catalog is not None:
            self._open_path(self.catalog.key_path(ids[0]), select=True)

    def _open_path(self, path: str, select: bool = False) -> None:
        command = reveal_command(path) if select else None
        try:
            if command is not None:
                if sys.platform.startswith("win"):
                    subprocess.Popen(command)  # explorer exits 1 even when it worked
                    return
                if subprocess.run(command, capture_output=True, timeout=5).returncode == 0:
                    return
            target = path if not select else str(Path(path).parent)
            subprocess.Popen(open_path_command(target))
        except (OSError, subprocess.SubprocessError):
            pass

    # -- viewer verbs --------------------------------------------------------------------
    def _focus_tags(self) -> None:
        if not self.panels["inspector"].state.open:
            self.toggle_panel("inspector")
        self.inspector.focus_tags()

    def _focus_note(self) -> None:
        if not self.panels["inspector"].state.open:
            self.toggle_panel("inspector")
        self.inspector.focus_note()

    def cycle_preset(self) -> None:
        name = self.view.cycle_preset()
        if name:
            self.flash(f"window {name}")

    def cycle_plane(self) -> None:
        self.flash(self.view.cycle_plane())

    def toggle_zoom(self) -> None:
        if self.view._fitted:
            self.view.one_to_one()
        else:
            self.view.fit()

    # -- menus -------------------------------------------------------------------------
    def _build_menus(self) -> None:
        self.letter_actions: list[QAction] = []

        def act(menu, text, shortcut, fn, letter=None):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
                if letter if letter is not None else (len(shortcut) == 1 or shortcut in (
                        "Shift+Z", "\\")):
                    self.letter_actions.append(action)
            action.triggered.connect(lambda _checked=False: fn())
            menu.addAction(action)
            return action

        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        act(file_menu, "Import Folder…", "Ctrl+Shift+I", self.import_folder)
        act(file_menu, "Export Manifest…", "Ctrl+Shift+E", self.export)
        file_menu.addSeparator()
        act(file_menu, "New Catalog…", "Ctrl+Shift+N", self.new_catalog)
        act(file_menu, "Open Catalog…", "Ctrl+O", self.choose_catalog)
        act(file_menu, "Check for Missing Files", "", lambda: self.background.start(
            MissingJob(str(self.catalog.path), self.signals)) if self.catalog else None)
        file_menu.addSeparator()
        act(file_menu, "Quit", "Ctrl+Q", self.close)

        edit = bar.addMenu("&Edit")
        act(edit, "Select All", "Ctrl+A", self.grid.selectAll)
        act(edit, "Select None", "Ctrl+D", self.grid.clearSelection)
        act(edit, "Find…", "Ctrl+F", self.focus_search)
        act(edit, "Copy Path", "Ctrl+Shift+C", self.copy_paths)
        edit.addSeparator()
        act(edit, "Add Tags…", "Ctrl+K", self._focus_tags)
        act(edit, "Add Note…", "Ctrl+'", self._focus_note)
        edit.addSeparator()
        act(edit, "Remove from Catalog…", "Ctrl+Backspace", self.remove_from_catalog)

        mark = bar.addMenu("&Mark")
        act(mark, "Pick", "P", lambda: self.flag(1))
        act(mark, "Reject", "X", lambda: self.flag(-1))
        act(mark, "Unflag", "U", lambda: self.flag(0))
        mark.addSeparator()
        for stars in range(6):
            act(mark, f"Rating {'•' * stars or 'none'}", str(stars), lambda s=stars: self.rate(s))
        mark.addSeparator()
        for key, name in zip("6789", LABELS):
            act(mark, f"Label {name}", key, lambda n=name: self.label(n))
        act(mark, f"Label {LABELS[4]}", "", lambda: self.label(LABELS[4]))
        mark.addSeparator()
        self.auto_advance = act(mark, "Auto Advance After Marking", "", lambda: None)
        self.auto_advance.setCheckable(True)
        self.auto_advance.setChecked(self.settings.get("advance", "0") == "1")
        self.auto_advance.toggled.connect(
            lambda on: self.settings.__setitem__("advance", "1" if on else "0"))

        library = bar.addMenu("&Library")
        act(library, "New Collection from Selection…", "Ctrl+N", self.new_collection)
        act(library, "New Smart Collection from Search…", "Ctrl+Alt+N",
            lambda: self.new_collection(smart=True))
        act(library, "Add to Target Collection", "B", self.add_to_target)
        act(library, "Remove from This Collection", "", self.remove_from_current_collection)
        library.addSeparator()
        order = library.addMenu("Sort")
        for key, title in (("added", "Newest import first"), ("name", "Path"),
                           ("modality", "Modality"), ("rating", "Rating"),
                           ("frames", "Most frames"), ("patient", "Patient (keyed)")):
            act(order, title, "", lambda k=key: (self.settings.__setitem__("order", k),
                                                 self.refresh()))

        view = bar.addMenu("&View")
        act(view, "Grid", "G", self.show_grid)
        act(view, "Loupe", "E", self.show_loupe)
        act(view, "Toggle Grid / Loupe", "Space", self.toggle_loupe)
        view.addSeparator()
        act(view, "Info Panel", "Ctrl+I", lambda: self.toggle_panel("inspector"))
        act(view, "Library Panel", "Ctrl+L", lambda: self.toggle_panel("sources"))
        act(view, "Facet Columns", "\\", self.toggle_facets)
        view.addSeparator()
        act(view, "Fit / 1:1", "Z", self.toggle_zoom)
        act(view, "Fill", "Shift+Z", self.view.fill)
        act(view, "Window Preset", "L", self.cycle_preset)
        act(view, "Invert", "I", self.view.invert)
        act(view, "Plane (Axial / Coronal / Sagittal)", "V", self.cycle_plane)
        view.addSeparator()
        act(view, "Bigger Cells", "=", lambda: self.grid.set_cell(self.grid.delegate.cell.width() + 24))
        act(view, "Smaller Cells", "-", lambda: self.grid.set_cell(self.grid.delegate.cell.width() - 24))
        act(view, "Fullscreen", "F11", lambda: self.showNormal() if self.isFullScreen()
            else self.showFullScreen())
        act(view, "Back", "Escape", self._escape, letter=False)

        nav = [QAction(self) for _ in range(2)]
        for action, key, step in zip(nav, ("Left", "Right"), (-1, 1)):
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(lambda _c=False, s=step: self.step(s))
            self.addAction(action)
        self.arrow_actions = nav

    def _escape(self) -> None:
        if self.filter.search.hasFocus():
            self.filter.search.clearFocus()
            self.grid.setFocus()
        elif self.isFullScreen():
            self.showNormal()
        elif self.stack.currentIndex() == 1:
            self.show_grid()

    def _focus_changed(self, _old, new) -> None:
        """Bare-letter keys step aside while you type a search, a tag, or a note."""
        typing = isinstance(new, (QLineEdit, QPlainTextEdit, QTextEdit))
        for action in self.letter_actions + self.arrow_actions:
            action.setEnabled(not typing)


def self_test() -> int:
    """Print which DICOM codecs this build can decode and build a window offscreen — the
    check the Windows CI runs against the frozen .exe."""
    report = dicom.decoder_report()
    for name, ok in report.items():
        print(f"{name:26} {'ok' if ok else 'MISSING'}")
    needed = ("ExplicitVRLittleEndian", "RLELossless", "JPEGLosslessSV1", "JPEG2000Lossless",
              "JPEGBaseline8Bit")
    missing = [name for name in needed if not report.get(name)]
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CHUCKVIEW_CONFIG_HOME"] = tmp
        window = LibraryWindow(Path(tmp) / f"selftest{SUFFIX}")
        window.show()
        QApplication.processEvents()
        window.close()
        if window.catalog is not None:
            window.catalog.close()
    print("window ok")
    if missing:
        print("missing decoders:", ", ".join(missing))
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    argv = argv if argv is not None else sys.argv
    parser = argparse.ArgumentParser(
        prog="chuckview",
        description="ChuckView — find and collect medical images for research datasets.")
    parser.add_argument("path", nargs="?",
                        help=f"a catalog (*{SUFFIX}) to open, or a folder to import")
    parser.add_argument("--self-test", action="store_true",
                        help="report DICOM codecs, build a window, and exit")
    options = parser.parse_args(argv[1:])

    if sys.platform.startswith("win"):
        try:  # the taskbar groups and labels the window as ChuckView, not as python.exe
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass
    def report(kind, value, trace) -> None:
        # PyQt aborts the process on an exception escaping a slot unless a hook is set. A
        # viewer must not lose an afternoon's marks to one bad file: print it and go on.
        import traceback

        traceback.print_exception(kind, value, trace)

    sys.excepthook = report
    app = QApplication(argv)
    app.setDesktopFileName("chuckview")
    configure_application(app, "ChuckView")
    app.setStyleSheet(app.styleSheet() + MATERIAL_STYLE)
    if options.self_test:
        return self_test()
    catalog_path, folder = None, None
    if options.path:
        given = Path(options.path)
        if given.suffix == SUFFIX:
            catalog_path = given
        elif given.is_dir():
            folder = str(given)
    window = LibraryWindow(catalog_path, folder)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
