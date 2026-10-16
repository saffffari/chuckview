"""Import: pick a folder, see everything under it, tick what goes in.

The scan runs in the background — every subfolder, DICOM grouped into series exactly as the
library will show it — and the dialog fills in as it goes: how many files, how many items,
how many of each modality, how many are already in the catalog (shown, unticked). Nothing
is written until Import is pressed, and then nothing is copied: the catalog records where
the files are.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListView,
    QListWidget, QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout,
)

from chuckview.catalog import Catalog, ImportPlan, PlannedItem
from chuckview.core import thumb_path_for
from chuckview.jobs import PlanJob, Signals
from chuckview.panels import ColumnHead, Readout, mono_font
from chuckview.previews import render

KEY_ROLE = Qt.ItemDataRole.UserRole + 30
THUMB = 128


class ThumbSignal(QObject):
    done = pyqtSignal(str, QImage)


class ThumbJob(QRunnable):
    """A small picture of a planned item, cached by path so a second look is instant."""

    def __init__(self, planned: PlannedItem, signal: ThumbSignal, generation: list[int]):
        super().__init__()
        self.planned, self.signal, self.generation = planned, signal, generation
        self.mine = generation[0]

    def run(self) -> None:
        if self.generation[0] != self.mine:
            return
        cached = thumb_path_for(self.planned.path)
        image = QImage(str(cached)) if cached is not None and cached.exists() else QImage()
        if image.isNull():
            image = render(self.planned.item, self.planned.path, 256) or QImage()
            if not image.isNull() and cached is not None:
                cached.parent.mkdir(parents=True, exist_ok=True)
                image.save(str(cached), "PNG")
        if not image.isNull():
            self.signal.done.emit(self.planned.path, image)


class ImportDialog(QDialog):
    def __init__(self, catalog: Catalog, folder: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import — ChuckView")
        self.resize(980, 680)
        self.catalog = catalog
        self.plan: ImportPlan | None = None
        self.job: PlanJob | None = None
        self.signals = Signals()
        self.signals.progress.connect(self._progress)
        self.signals.planned.connect(self._planned)
        self.signals.failed.connect(self._failed)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.thumbs = QThreadPool(self)
        self.thumbs.setMaxThreadCount(max(1, QThreadPool.globalInstance().maxThreadCount() // 2))
        self.thumb_signal = ThumbSignal()
        self.thumb_signal.done.connect(self._thumb)
        self.generation = [0]
        self.items: dict[str, QListWidgetItem] = {}

        self.head = ColumnHead("Import")
        self.folder_label = QLabel("No folder chosen")
        choose = QPushButton("CHOOSE FOLDER…")
        choose.clicked.connect(self.choose)
        top = QHBoxLayout()
        top.setContentsMargins(8, 4, 8, 4)
        top.addWidget(self.folder_label, 1)
        top.addWidget(choose, 0)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(3)
        self.stage = QLabel("")
        self.stage.setObjectName("muted")
        self.summary = Readout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("FILTER  —  CT, DX, a folder name…")
        self.filter.textChanged.connect(self._apply_filter)
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setIconSize(QSize(THUMB, THUMB))
        self.grid.setGridSize(QSize(THUMB + 28, THUMB + 34))
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grid.setFont(mono_font(8, spacing=0.4))
        self.grid.itemChanged.connect(lambda _item: self._update_button())
        all_button = QPushButton("CHECK ALL")
        all_button.clicked.connect(lambda: self._check(True))
        none_button = QPushButton("CHECK NONE")
        none_button.clicked.connect(lambda: self._check(False))
        selected_button = QPushButton("TOGGLE SELECTED")
        selected_button.clicked.connect(self._toggle_selected)
        self.cancel_button = QPushButton("CANCEL")
        self.cancel_button.clicked.connect(self.reject)
        self.import_button = QPushButton("IMPORT")
        self.import_button.setDefault(True)
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(8, 6, 8, 8)
        for button in (all_button, none_button, selected_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.import_button)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(self.head)
        box.addLayout(top)
        box.addWidget(self.bar)
        box.addWidget(self.stage)
        box.addWidget(self.summary)
        box.addWidget(self.filter)
        box.addWidget(self.grid, 1)
        box.addLayout(buttons)
        if folder:
            self.start(folder)

    # -- scanning --------------------------------------------------------------------
    def choose(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Import folder (and all subfolders)",
                                                  str(Path.home()))
        if folder:
            self.start(folder)

    def start(self, folder: str) -> None:
        if self.job is not None:
            self.job.cancelled = True
        self.generation[0] += 1
        self.plan = None
        self.items.clear()
        self.grid.clear()
        self.folder_label.setText(folder)
        self.head.set_value("scanning")
        self.bar.setRange(0, 0)
        self._update_button()
        self.job = PlanJob(str(self.catalog.path), folder, self.signals)
        self.pool.start(self.job)

    def _progress(self, stage: str, done: int, total: int) -> None:
        self.stage.setText(f"  {stage}  {done:,}/{total:,}" if total else f"  {stage}…")
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)

    def _failed(self, message: str) -> None:
        self.bar.setRange(0, 1)
        self.stage.setText(f"  {message}")
        self.head.set_value("failed")

    def _planned(self, plan: ImportPlan) -> None:
        self.plan = plan
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.stage.setText(f"  scanned in {plan.seconds:.1f}s")
        new = [p for p in plan.items if not p.existing]
        counts = "  ".join(f"{k} {v}" for k, v in plan.counts().items())
        self.summary.setText("\n".join([
            f"fil\t{plan.files_seen:,} files under the folder",
            f"itm\t{len(plan.items):,} items — {counts}",
            f"new\t{len(new):,} not yet in the catalog",
        ]))
        self.head.set_value(f"{len(plan.items)} items")
        self.grid.setUpdatesEnabled(False)
        blank = QPixmap(THUMB, THUMB)
        blank.fill(Qt.GlobalColor.transparent)
        placeholder = QIcon(blank)
        for planned in plan.items:
            name = Path(planned.path).parent.name if planned.kind == "series" else Path(planned.path).name
            item = QListWidgetItem(placeholder, f"{planned.label}\n{name[:22]}")
            item.setData(KEY_ROLE, planned.path)
            item.setToolTip(planned.path + ("\n(already in the catalog)" if planned.existing else ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if planned.existing else Qt.CheckState.Checked)
            if planned.existing:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.grid.addItem(item)
            self.items[planned.path] = item
            self.thumbs.start(ThumbJob(planned, self.thumb_signal, self.generation))
        self.grid.setUpdatesEnabled(True)
        self._update_button()

    def _thumb(self, path: str, image: QImage) -> None:
        item = self.items.get(path)
        if item is not None:
            item.setIcon(QIcon(QPixmap.fromImage(image)))

    # -- choosing ----------------------------------------------------------------------
    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            hay = (item.text() + " " + item.data(KEY_ROLE)).lower()
            item.setHidden(bool(needle) and needle not in hay)

    def _check(self, on: bool) -> None:
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            if not item.isHidden() and item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _toggle_selected(self) -> None:
        for item in self.grid.selectedItems():
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
                                   else Qt.CheckState.Checked)

    def chosen(self) -> list[str]:
        return [self.grid.item(row).data(KEY_ROLE) for row in range(self.grid.count())
                if self.grid.item(row).checkState() == Qt.CheckState.Checked]

    def _update_button(self) -> None:
        count = len(self.chosen()) if self.plan is not None else 0
        self.import_button.setEnabled(count > 0)
        self.import_button.setText(f"IMPORT {count:,}" if count else "IMPORT")

    def done(self, result: int) -> None:  # noqa: A003 - QDialog API
        self.generation[0] += 1  # stop thumbnails nobody will see
        if self.job is not None:
            self.job.cancelled = True
        super().done(result)
