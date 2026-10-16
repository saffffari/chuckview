"""The right panel (Ctrl+I): what this item is, what you have said about it, and its header.

Three sections, top to bottom:

- **Facts** — the catalog's readout of the item and, while it is in the loupe, where you are
  in it (slice, plane, window). Never a name, an ID, or a date.
- **Tags and notes** — type tags separated by commas and press Enter; select one and press
  Delete to take it off. Notes are timestamped and signed with your login; Ctrl+Enter adds
  one. Both are searchable from the search line (`tag:`, `note:`, or any bare word).
- **DICOM** — every element of the header, read live from the file, with a line to filter
  it. Identifying elements show as ••••••; *Reveal identifiers* shows them for this session
  only and is never remembered.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from chuckview import dicom, phi
from chuckview.catalog import Catalog
from chuckview.panels import GUTTER, LABEL, MUTED, ColumnHead, Readout, mono_font


class Inspector(QWidget):
    edited = pyqtSignal(int)  # item id whose tags or notes changed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panelBody")
        self.catalog: Catalog | None = None
        self.item_id: int | None = None
        self.viewer_rows: list[str] = []
        self.reveal = False

        self.facts = Readout()
        self.tag_line = QLineEdit()
        self.tag_line.setPlaceholderText("ADD TAGS, COMMA SEPARATED  ⏎")
        self.tag_line.returnPressed.connect(self._add_tags)
        self.tags = QListWidget()
        self.tags.setFlow(QListWidget.Flow.LeftToRight)
        self.tags.setWrapping(True)
        self.tags.setMaximumHeight(54)
        self.tags.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tags.setToolTip("Delete removes the selected tags")
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self.tags, activated=self._remove_tags,
                  context=Qt.ShortcutContext.WidgetShortcut)
        self.notes = QListWidget()
        self.notes.setWordWrap(True)
        self.notes.setFont(mono_font(10, spacing=0.2))
        self.note_edit = QPlainTextEdit()
        self.note_edit.setPlaceholderText("NOTE  —  Ctrl+Enter adds it")
        self.note_edit.setMaximumHeight(70)
        QShortcut(QKeySequence("Ctrl+Return"), self.note_edit, activated=self._add_note,
                  context=Qt.ShortcutContext.WidgetShortcut)
        QShortcut(QKeySequence("Ctrl+Enter"), self.note_edit, activated=self._add_note,
                  context=Qt.ShortcutContext.WidgetShortcut)

        upper = QWidget()
        upper_box = QVBoxLayout(upper)
        upper_box.setContentsMargins(0, 0, 0, 0)
        upper_box.setSpacing(0)
        upper_box.addWidget(ColumnHead("Facts"))
        upper_box.addWidget(self.facts)
        upper_box.addWidget(ColumnHead("Tags"))
        upper_box.addWidget(self.tag_line)
        upper_box.addWidget(self.tags)
        upper_box.addWidget(ColumnHead("Notes"))
        upper_box.addWidget(self.notes, 1)
        upper_box.addWidget(self.note_edit)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("FILTER TAGS  —  name, keyword or (gggg,eeee)")
        self.filter.textChanged.connect(self._fill_header)
        self.reveal_box = QCheckBox("Reveal identifiers (this session)")
        self.reveal_box.toggled.connect(self._set_reveal)
        self.header_head = ColumnHead("DICOM")
        self.header = QTreeWidget()
        self.header.setColumnCount(3)
        self.header.setHeaderLabels(["TAG", "NAME", "VALUE"])
        self.header.setRootIsDecorated(False)
        self.header.setUniformRowHeights(True)
        self.header.setFont(mono_font(9, spacing=0.2))
        self.header.setColumnWidth(0, 86)
        self.header.setColumnWidth(1, 170)
        self._rows: list[phi.Row] = []
        lower = QWidget()
        lower_box = QVBoxLayout(lower)
        lower_box.setContentsMargins(0, 0, 0, 0)
        lower_box.setSpacing(0)
        lower_box.addWidget(self.header_head)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 6, 0)
        line.addWidget(self.filter, 1)
        line.addWidget(self.reveal_box, 0)
        lower_box.addLayout(line)
        lower_box.addWidget(self.header, 1)

        split = QSplitter(Qt.Orientation.Vertical)
        split.setHandleWidth(GUTTER)
        split.setChildrenCollapsible(False)
        split.addWidget(upper)
        split.addWidget(lower)
        split.setSizes([420, 420])
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(split)

        self._header_timer = QTimer(self)
        self._header_timer.setSingleShot(True)
        self._header_timer.setInterval(120)
        self._header_timer.timeout.connect(self._read_header)

    # -- showing an item -----------------------------------------------------------
    def show_item(self, catalog: Catalog | None, item_id: int | None) -> None:
        self.catalog, self.item_id = catalog, item_id
        self.viewer_rows = []
        self._fill_facts()
        self._fill_tags()
        self._fill_notes()
        self._rows = []
        self.header.clear()
        if self.isVisible():
            self._header_timer.start()

    def set_viewer_rows(self, rows: list[str]) -> None:
        """Slice, plane and window from the loupe, appended to the facts."""
        self.viewer_rows = rows
        self._fill_facts()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.item_id is not None and not self._rows:
            self._header_timer.start()

    def _fill_facts(self) -> None:
        if self.catalog is None or self.item_id is None:
            self.facts.setText("")
            return
        row = self.catalog.item(self.item_id)
        if row is None:
            self.facts.setText("")
            return
        lines = [f"key\t{row['rel']}", f"kin\t{row['modality'] or '—'} · {row['kind']}"]
        for key, column in (("ser", "description"), ("stu", "study_description"),
                            ("pro", "protocol"), ("bod", "body"), ("vw", "view"),
                            ("lat", "laterality"), ("mfr", "manufacturer"), ("mdl", "model"),
                            ("krn", "kernel")):
            if row[column]:
                lines.append(f"{key}\t{row[column]}")
        frames = row["frames"] or 1
        if row["cols"]:
            lines.append(f"dim\t{row['cols']}×{row['rows']}" + (f"×{frames}" if frames > 1 else ""))
        if row["spacing_c"]:
            z = f"×{row['spacing_z']:g}" if frames > 1 and row["spacing_z"] else ""
            lines.append(f"spc\t{row['spacing_c']:g}×{row['spacing_r']:g}{z} mm")
        if row["thickness"]:
            lines.append(f"thk\t{row['thickness']:g} mm")
        if row["kvp"]:
            lines.append(f"kvp\t{row['kvp']:g}")
        who = " · ".join(str(v) for v in (row["sex"], f"{row['age']}y" if row["age"] is not None
                                          else None, row["year"]) if v)
        if who:
            lines.append(f"pt\t{who}" + ("" if row["age"] != 90 else " (90+)"))
        if row["patient"]:
            lines.append(f"pid\t{row['patient'][:10]}… (keyed hash)")
        marks = []
        if row["flag"]:
            marks.append("pick" if row["flag"] > 0 else "reject")
        if row["rating"]:
            marks.append("•" * row["rating"])
        if row["label"]:
            marks.append(row["label"])
        if marks:
            lines.append(f"mrk\t{' · '.join(marks)}")
        if row["missing"]:
            lines.append("off\toriginal not found — showing preview")
        lines.extend(self.viewer_rows)
        self.facts.setText("\n".join(lines))

    def _fill_tags(self) -> None:
        self.tags.clear()
        if self.catalog is None or self.item_id is None:
            return
        for name in self.catalog.tags_of(self.item_id):
            self.tags.addItem(QListWidgetItem(name))

    def _fill_notes(self) -> None:
        self.notes.clear()
        if self.catalog is None or self.item_id is None:
            return
        for note in self.catalog.notes_of(self.item_id):
            when = (note["at"] or "")[:16].replace("T", " ")
            item = QListWidgetItem(f"{note['body']}\n— {note['author'] or '?'}, {when}")
            item.setData(Qt.ItemDataRole.UserRole, note["id"])
            self.notes.addItem(item)

    # -- editing -------------------------------------------------------------------
    def tag_targets(self) -> list[int]:
        return [self.item_id] if self.item_id is not None else []

    def _add_tags(self) -> None:
        names = [n.strip() for n in self.tag_line.text().split(",") if n.strip()]
        if self.catalog is None or not names:
            return
        ids = self.tag_targets()
        self.catalog.add_tags(ids, names)
        self.tag_line.clear()
        self._fill_tags()
        for item_id in ids:
            self.edited.emit(item_id)

    def _remove_tags(self) -> None:
        names = [item.text() for item in self.tags.selectedItems()]
        if self.catalog is None or not names:
            return
        ids = self.tag_targets()
        self.catalog.remove_tags(ids, names)
        self._fill_tags()
        for item_id in ids:
            self.edited.emit(item_id)

    def _add_note(self) -> None:
        if self.catalog is None or self.item_id is None:
            return
        if self.catalog.add_note(self.item_id, self.note_edit.toPlainText()):
            self.note_edit.clear()
            self._fill_notes()
            self.edited.emit(self.item_id)

    def focus_note(self) -> None:
        self.note_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def focus_tags(self) -> None:
        self.tag_line.setFocus(Qt.FocusReason.ShortcutFocusReason)

    # -- the header ------------------------------------------------------------------
    def _set_reveal(self, on: bool) -> None:
        self.reveal = on
        self._read_header()

    def _read_header(self) -> None:
        self._rows = []
        self.header.clear()
        if self.catalog is None or self.item_id is None or not dicom.AVAILABLE:
            return
        row = self.catalog.item(self.item_id)
        if row is None or row["kind"] == "image":
            self.header_head.set_value("not DICOM")
            return
        files = self.catalog.files(self.item_id)
        if not files:
            return
        path = files[len(files) // 2]
        self._rows = phi.header_rows(path, reveal=self.reveal)
        self.header_head.set_value(f"{len(self._rows)} elements" if self._rows
                                   else "file offline")
        self._fill_header()

    def _fill_header(self) -> None:
        self.header.clear()
        needle = self.filter.text().strip().lower()
        items = []
        for row in self._rows:
            if needle and not (needle in row.tag.lower() or needle in row.keyword.lower()
                               or needle in row.name.lower() or needle in row.value.lower()):
                continue
            indent = "  " * row.depth
            item = QTreeWidgetItem([row.tag, indent + (row.name or row.keyword), row.value])
            item.setToolTip(2, row.value)
            item.setToolTip(1, f"{row.keyword} · {row.vr}")
            if row.identifying:
                item.setForeground(2, QColor(MUTED if not self.reveal else LABEL))
            item.setForeground(0, QColor(MUTED))
            items.append(item)
        self.header.addTopLevelItems(items)


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")
