"""The library: sources on the left, the filter bar, and the grid.

The grid is a model over catalog rows, not a widget per image, so a catalog of a hundred
thousand films scrolls like one of a hundred: a cell is drawn from a Row (a dozen fields)
and a thumbnail loaded from the preview store when it first comes into view.

A cell says everything a cull needs without opening anything: what it is (`CT 240`,
`DX`), its flag (a pennant for a pick, a cross for a reject — which also dims it), its
rating as dots, its colour label as a hairline under the picture, whether it has tags or
notes, and whether its original is offline.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import (
    QAbstractListModel, QModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from chuckview.catalog import FACETS, Catalog, Row
from chuckview.common import INK, SELECT
from chuckview.panels import LABEL, MUTED, PAD, RULE, VOID, mono_font
from chuckview.previews import PreviewStore
from chuckview.style import LABEL_COLORS

ROW_ROLE = Qt.ItemDataRole.UserRole + 10
SOURCE_ROLE = Qt.ItemDataRole.UserRole + 11


class GridModel(QAbstractListModel):
    """Catalog rows, with thumbnails loaded lazily and asked for when missing."""

    previews_wanted = pyqtSignal(list)  # item ids whose thumbnail does not exist yet

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[Row] = []
        self.index_of: dict[int, int] = {}
        self.store: PreviewStore | None = None
        self._pixmaps: OrderedDict[int, QPixmap] = OrderedDict()
        self._asked: set[int] = set()
        self._pending: list[int] = []
        self._flush = QTimer(self)
        self._flush.setSingleShot(True)
        self._flush.setInterval(30)
        self._flush.timeout.connect(self._ask)

    def set_rows(self, rows: list[Row], store: PreviewStore | None) -> None:
        self.beginResetModel()
        self.rows = rows
        self.index_of = {row.id: i for i, row in enumerate(rows)}
        if store is not self.store:
            self._pixmaps.clear()
            self._asked.clear()
        self.store = store
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == ROW_ROLE:
            return row
        if role == Qt.ItemDataRole.DecorationRole:
            return self.pixmap(row.id)
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.path
        return None

    def pixmap(self, item_id: int) -> QPixmap | None:
        cached = self._pixmaps.get(item_id)
        if cached is not None:
            self._pixmaps.move_to_end(item_id)
            return cached
        if self.store is None:
            return None
        path = self.store.thumb_path(item_id)
        if path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                self._pixmaps[item_id] = pixmap
                if len(self._pixmaps) > 4000:
                    self._pixmaps.popitem(last=False)
                return pixmap
        if item_id not in self._asked:
            self._asked.add(item_id)
            self._pending.append(item_id)
            self._flush.start()
        return None

    def _ask(self) -> None:
        if self._pending:
            pending, self._pending = self._pending, []
            self.previews_wanted.emit(pending)

    def preview_ready(self, item_id: int, built: bool) -> None:
        self._pixmaps.pop(item_id, None)
        if not built:
            return  # stays a blank cell; asked again next time the source is opened
        row = self.index_of.get(item_id)
        if row is not None:
            index = self.index(row)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def forget_asks(self) -> None:
        self._asked.clear()

    def replace_rows(self, fresh: list[Row]) -> None:
        """New marks on some rows — redraw those cells, keep scroll and selection."""
        for row in fresh:
            position = self.index_of.get(row.id)
            if position is not None:
                self.rows[position] = row
                index = self.index(position)
                self.dataChanged.emit(index, index, [ROW_ROLE])


class GridDelegate(QStyledItemDelegate):
    PAD = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cell = QSize(168, 168)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return self.cell

    def paint(self, painter: QPainter, option, index) -> None:
        row: Row | None = index.data(ROW_ROLE)
        if row is None:
            return
        rect = option.rect.adjusted(2, 2, -2, -2)
        painter.save()
        painter.fillRect(option.rect, QColor(VOID))
        inner = rect.adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD - 4)
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        if pixmap is not None and not pixmap.isNull():
            scaled = pixmap.size().scaled(inner.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(inner.center())
            if row.flag < 0 or row.missing:
                painter.setOpacity(0.35)
            painter.drawPixmap(target, pixmap)
            painter.setOpacity(1.0)
        else:
            target = inner
            painter.setPen(QPen(QColor(RULE), 1))
            painter.drawRect(inner.adjusted(0, 0, -1, -1))
        if row.label in LABEL_COLORS:
            painter.fillRect(QRect(target.left(), inner.bottom() + 3, target.width(), 2),
                             QColor(LABEL_COLORS[row.label]))
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(QPen(QColor(SELECT), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.setFont(mono_font(8, spacing=0.6))
        metrics = painter.fontMetrics()
        badge = row.badge
        tag = QRect(target.left() + 3, target.bottom() - 15,
                    metrics.horizontalAdvance(badge) + 8, 13)
        painter.fillRect(tag, QColor(8, 8, 8, 200))
        painter.setPen(QColor(242, 242, 242, 235))
        painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, badge)
        if row.missing:
            text = "OFFLINE"
            box = QRect(0, 0, metrics.horizontalAdvance(text) + 10, 14)
            box.moveCenter(target.center())
            painter.fillRect(box, QColor(8, 8, 8, 220))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        if row.rating:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(INK))
            for star in range(row.rating):
                x = target.right() - 6 - star * 7
                painter.drawEllipse(QPointF(x, target.bottom() - 8), 2.2, 2.2)
        self._flag(painter, row, target)
        marks = ("N" if row.noted else "") + ("T" if row.tagged else "")
        if marks:
            box = QRect(target.right() - metrics.horizontalAdvance(marks) - 9,
                        target.top() + 3, metrics.horizontalAdvance(marks) + 6, 13)
            painter.fillRect(box, QColor(8, 8, 8, 200))
            painter.setPen(QColor(LABEL))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, marks)
        painter.restore()

    @staticmethod
    def _flag(painter: QPainter, row: Row, target: QRect) -> None:
        if not row.flag:
            return
        spot = QRectF(target.left() + 4, target.top() + 4, 13, 13)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(8, 8, 8, 200))
        painter.drawEllipse(spot)
        inner = spot.adjusted(3.5, 3.5, -3.5, -3.5)
        if row.flag > 0:
            painter.setBrush(QColor(242, 242, 242, 240))
            painter.drawPolygon(QPolygonF([
                inner.topLeft(), QPointF(inner.right(), inner.top() + inner.height() * 0.3),
                QPointF(inner.left(), inner.top() + inner.height() * 0.6), inner.bottomLeft()]))
        else:
            painter.setPen(QPen(QColor(242, 242, 242, 240), 1.4))
            painter.drawLine(inner.topLeft(), inner.bottomRight())
            painter.drawLine(inner.topRight(), inner.bottomLeft())


class Grid(QListView):
    """The contact sheet. Ctrl+wheel (or + and −) resizes the cells."""

    opened = pyqtSignal(int)            # row index double-clicked / Enter
    context_requested = pyqtSignal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("grid")
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setLayoutMode(QListView.LayoutMode.Batched)
        self.setBatchSize(500)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSpacing(0)
        self.setWordWrap(False)
        self.delegate = GridDelegate(self)
        self.setItemDelegate(self.delegate)
        self.model_ = GridModel(self)
        self.setModel(self.model_)
        self.doubleClicked.connect(lambda index: self.opened.emit(index.row()))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.context_requested.emit(self.viewport().mapToGlobal(pos)))

    def set_cell(self, side: int) -> None:
        side = max(80, min(420, side))
        self.delegate.cell = QSize(side, side)
        self.setGridSize(self.delegate.cell)
        self.scheduleDelayedItemsLayout()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            step = 16 if event.angleDelta().y() > 0 else -16
            self.set_cell(self.delegate.cell.width() + step)
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.opened.emit(self.currentIndex().row())
            return
        super().keyPressEvent(event)

    def selected_ids(self) -> list[int]:
        rows = sorted(index.row() for index in self.selectionModel().selectedIndexes())
        return [self.model_.rows[r].id for r in rows if r < len(self.model_.rows)]

    def current_row(self) -> Row | None:
        index = self.currentIndex()
        if index.isValid() and index.row() < len(self.model_.rows):
            return self.model_.rows[index.row()]
        return None


class Sources(QListWidget):
    """Where in the catalog: everything, the last import, the picks, what is offline, each
    collection, each imported folder, each import. Headings are rows that cannot be picked."""

    chosen = pyqtSignal(object)            # ("all", None), ("collection", 3), …
    menu_requested = pyqtSignal(object, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sources")
        self.setFont(mono_font(10, spacing=0.3))
        self.currentItemChanged.connect(self._changed)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self._quiet = False

    def reload(self, catalog: Catalog | None, current=None) -> None:
        self._quiet = True
        self.clear()
        if catalog is not None:
            self._heading("Catalog")
            self._add("All items", ("all", None), catalog.count())
            self._add("Last import", ("last_import", None), catalog.count(source=("last_import", None)))
            self._add("Picks", ("query", "is:pick"), catalog.count("is:pick"))
            self._add("Offline", ("query", "is:missing"), catalog.count("is:missing"))
            collections = catalog.collections()
            self._heading("Collections")
            if not collections:
                self._note("Ctrl+N makes one")
            for ident, name, query, count in collections:
                self._add(("⌕ " if query else "") + name, ("collection", ident), count)
            self._heading("Folders")
            for ident, path, online in catalog.roots():
                parts = Path(path).parts
                label = "/".join(parts[-2:]) if len(parts) > 1 else path
                label = label if online else f"{label}  (offline)"
                self._add(label, ("root", ident), catalog.count(source=("root", ident)))
                self.item(self.count() - 1).setToolTip(path)
            imports = catalog.imports()
            if imports:
                self._heading("Imports")
                for row in imports[:20]:
                    self._add(f"{row['at'][:16].replace('T', ' ')}", ("import", row["id"]),
                              row["added"])
        self._quiet = False
        self.select(current or ("all", None))

    def _heading(self, text: str) -> None:
        item = QListWidgetItem(text.upper())
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setFont(mono_font(9, bold=True, spacing=1.4))
        item.setForeground(QColor(LABEL))
        self.addItem(item)

    def _note(self, text: str) -> None:
        item = QListWidgetItem("  " + text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setForeground(QColor(MUTED))
        self.addItem(item)

    def _add(self, label: str, source, count: int) -> None:
        item = QListWidgetItem(f"  {label}")
        item.setData(SOURCE_ROLE, source)
        item.setData(Qt.ItemDataRole.ToolTipRole, f"{label} — {count}")
        item.setText(f"  {label}")
        item.setData(Qt.ItemDataRole.UserRole + 20, count)
        self.addItem(item)
        item.setText(f"  {label}  ·  {count}")

    def select(self, source) -> None:
        for row in range(self.count()):
            if self.item(row).data(SOURCE_ROLE) == source:
                self._quiet = True
                self.setCurrentRow(row)
                self._quiet = False
                return

    def current(self):
        item = self.currentItem()
        return item.data(SOURCE_ROLE) if item is not None else ("all", None)

    def _changed(self, item, _previous) -> None:
        if not self._quiet and item is not None and item.data(SOURCE_ROLE) is not None:
            self.chosen.emit(item.data(SOURCE_ROLE))

    def _menu(self, pos: QPoint) -> None:
        item = self.itemAt(pos)
        if item is not None and item.data(SOURCE_ROLE) is not None:
            self.menu_requested.emit(item.data(SOURCE_ROLE), self.viewport().mapToGlobal(pos))


FLAG_NAMES = {1: "Pick", -1: "Reject", 0: "Unflagged"}
DEFAULT_FACETS = ("modality", "body", "view", "series")


class FacetColumn(QWidget):
    """One column of the filter bar: choose a field, tick its values."""

    changed = pyqtSignal()

    def __init__(self, field: str, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self.chooser = QComboBox()
        for name, (title, _column) in FACETS.items():
            self.chooser.addItem(title.upper(), name)
        self.chooser.setCurrentIndex(list(FACETS).index(field))
        self.chooser.currentIndexChanged.connect(self._field_changed)
        self.values = QListWidget()
        self.values.setObjectName("facet")
        self.values.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.values.itemSelectionChanged.connect(self.changed)
        self.values.setFont(mono_font(9, spacing=0.3))
        box.addWidget(self.chooser)
        box.addWidget(self.values, 1)

    @property
    def field(self) -> str:
        return self.chooser.currentData()

    def _field_changed(self) -> None:
        self.values.blockSignals(True)
        self.values.clear()
        self.values.blockSignals(False)
        self.changed.emit()

    def selected(self) -> list:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.values.selectedItems()]

    def fill(self, counts: list[tuple[object, int]]) -> None:
        keep = set(map(repr, self.selected()))
        self.values.blockSignals(True)
        self.values.clear()
        for value, count in counts:
            item = QListWidgetItem(f"{self._label(value)}")
            item.setData(Qt.ItemDataRole.UserRole, value)
            item.setData(Qt.ItemDataRole.ToolTipRole, f"{count} items")
            item.setText(f"{self._label(value):<22.22} {count:>6}")
            self.values.addItem(item)
            if repr(value) in keep:
                item.setSelected(True)
        self.values.blockSignals(False)

    def _label(self, value) -> str:
        if value is None or value == "":
            return "—"
        if self.field == "flag":
            return FLAG_NAMES.get(value, str(value))
        if self.field == "rating":
            return "•" * int(value) if value else "unrated"
        if isinstance(value, float):
            return f"{value:g}"
        return str(value)


class FilterBar(QWidget):
    """The search line and, under it, four facet columns (\\ shows and hides them)."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panelBody")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, PAD, 0)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText(
            'SEARCH  —  mod:CT thick:<=1.5 body:spine tag:include -is:reject "sag"')
        self.search.setFont(mono_font(10, spacing=0.4))
        self.status = QLabel("")
        self.status.setObjectName("muted")
        line.addWidget(self.search, 1)
        line.addWidget(self.status, 0)
        box.addLayout(line)
        self.facets_box = QWidget()
        facets = QHBoxLayout(self.facets_box)
        facets.setContentsMargins(0, 0, 0, 0)
        facets.setSpacing(1)
        self.columns = [FacetColumn(name) for name in DEFAULT_FACETS]
        for column in self.columns:
            column.changed.connect(self.changed)
            facets.addWidget(column, 1)
        self.facets_box.setFixedHeight(132)
        box.addWidget(self.facets_box)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(160)
        self._debounce.timeout.connect(self.changed)
        self.search.textChanged.connect(lambda _text: self._debounce.start())

    def query(self) -> str:
        return self.search.text().strip()

    def selections(self) -> dict[str, list]:
        out: dict[str, list] = {}
        for column in self.columns:
            values = column.selected()
            if values:
                out.setdefault(column.field, []).extend(values)
        return out

    def refresh(self, catalog: Catalog | None, source) -> None:
        if catalog is None:
            for column in self.columns:
                column.fill([])
            return
        selections = self.selections()
        for column in self.columns:
            column.fill(catalog.facet(column.field, self.query(), source, selections))

    def clear(self) -> None:
        self.search.clear()
        for column in self.columns:
            column.values.clearSelection()

    def set_facets_visible(self, on: bool) -> None:
        self.facets_box.setVisible(on)
