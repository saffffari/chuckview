"""The columns, and the flat dark material they are made of.

Light Table was three floating panes of refracting glass for a while. It looked like a
demo and worked like one: you could put the contact sheet anywhere, which mostly meant
you could put it somewhere wrong, and a photograph seen through a tinted pane is a
photograph you cannot trust. So it is three columns again — **Locations**, **Gallery**,
**Viewer**, always in that order, left to right — and the material is flat fill.

Two rules hold the whole thing up:

- **Size, don't rearrange.** Every edge between columns is a splitter handle, so the
  layout is yours to proportion; the order is not yours to change, because there is only
  one order that reads left-to-right as narrowing down — where, then which, then this
  one. Columns open and close, and the widths persist.
- **Fills, not strokes.** Nothing here is separated by a line. Surfaces are told apart by
  sitting a few points above black — `BASE` under `RAISED` under `CONTROL` — and columns
  are separated by a black gutter, which is a fill too. Only icons get strokes. The
  picture is the one bright thing on screen, and nothing draws a bright edge next to it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QLayout, QSizePolicy, QVBoxLayout, QWidget

from apps.lighttable.core import config_dir

# --- The ladder -----------------------------------------------------------------------
# Dark mode, and near-black at that: every surface in the app lands within about 7% of
# black, so the brightest thing on screen is always the photograph. Depth is four steps of
# fill, not four borders — each one is a few points up from the one it sits on, which is
# enough to read as a separate surface and not enough to read as a box.
VOID = "#000000"       # the gutter between columns, and behind everything
MATTE = "#070707"      # the matting the picture is laid on
BASE = "#0e0e0e"       # a column's body
RAISED = "#151515"     # headers and the button bar: one step nearer the eye
CONTROL = "#1c1c1c"    # a button or a field at rest
HOVER = "#262626"      # under the pointer
ACTIVE = "#303030"     # pressed, or a toggle that is on

INK = "#f2f2f2"        # text
MUTED = "#7a7a7a"      # secondary text, and a toggle that is off
LABEL = "#5e5e5e"      # column headers

HEADER = 24            # height of a column's title strip
GUTTER = 2             # the black seam between two columns — also the grab handle
MINIMUM = QSize(150, 120)


@dataclass(frozen=True)
class PanelState:
    """How wide a column is and whether it is open. That is the entire layout.

    There is deliberately nowhere to record *where* a column is: position is a property of
    the app, not of the session, so it cannot drift and there is nothing to reset."""

    name: str
    width: int = 280
    open: bool = False


# --- Where the layout is kept ---------------------------------------------------------
# One flat line per column, in the same spirit as the favorites file: no database, no
# settings dialog, and if you want the same proportions on every machine you copy one text
# file. An unreadable or half-written line falls back to the default for that column
# rather than taking the layout down.

def layout_file() -> Path:
    return config_dir() / "lighttable-panels.txt"


def format_layout(states: Iterable[PanelState]) -> str:
    lines = [f"{state.name} {state.width} {int(state.open)}" for state in states]
    return "# Light Table columns — name width open\n" + "\n".join(lines) + "\n"


def parse_layout(text: str, defaults: Iterable[PanelState]) -> dict[str, PanelState]:
    """Read a layout, keeping the default for every column the file does not describe."""
    states = {state.name: state for state in defaults}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        # Exactly three fields, not "at least three": the file this replaces held eight
        # per line (x, y, width, height, snap…) for panels that could be dragged anywhere,
        # and reading the old x as the new width would open the app with a sliver of a
        # sidebar. A line that is not this format is not this format — take the default
        # and let the next save write it out properly.
        if len(parts) != 3 or parts[0] not in states:
            continue
        try:
            width = int(parts[1])
        except ValueError:
            continue
        is_open = parts[2] not in ("0", "false", "False")
        states[parts[0]] = PanelState(parts[0], max(MINIMUM.width(), width), is_open)
    return states


def load_layout(defaults: Iterable[PanelState], path: Path | None = None) -> dict[str, PanelState]:
    source = Path(path) if path is not None else layout_file()
    try:
        return parse_layout(source.read_text(encoding="utf-8"), defaults)
    except (OSError, UnicodeDecodeError):
        return {state.name: state for state in defaults}


def save_layout(states: Iterable[PanelState], path: Path | None = None) -> Path:
    target = Path(path) if path is not None else layout_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(format_layout(states), encoding="utf-8")
    return target


def column_sizes(states: Iterable[PanelState], total: int) -> list[int]:
    """What to hand the splitter: the stored width for every open column, and whatever is
    left over for the rightmost one.

    The last open column is the one that grows, so widening the window grows the picture
    (or, with no viewer open, the sheet) instead of fattening the sidebar. A closed column
    still gets a size, because the splitter is told about every widget whether or not it
    is showing one, and a zero there would be a column you could never drag back out."""
    ordered = list(states)
    sizes = [max(MINIMUM.width(), state.width) for state in ordered]
    open_at = [index for index, state in enumerate(ordered) if state.open]
    if not open_at:
        return sizes
    used = sum(sizes[index] for index in open_at[:-1])
    sizes[open_at[-1]] = max(MINIMUM.width(), total - used)
    return sizes


class FlowRow(QLayout):
    """A row of buttons that wraps instead of clipping.

    The buttons live at the top of the sidebar, and the sidebar is a column the user is
    free to make narrow. Qt's box layouts would either squash the buttons past legibility
    or push them off the edge, so this one lays them out left to right and starts a new
    line when the next one will not fit. It is the plainest possible flow layout: no
    stretch, no alignment, one spacing."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 4):
        super().__init__(parent)
        self._items: list = []
        self.setSpacing(spacing)

    def addItem(self, item) -> None:  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 - Qt API
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802 - Qt API
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802 - Qt API
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._lay_out(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._lay_out(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt API
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    def _lay_out(self, rect: QRect, apply: bool) -> int:
        """Place every item, and return the height the row ended up needing."""
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if line and x + hint.width() > area.right() + 1:
                x, y, line = area.x(), y + line + self.spacing(), 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            line = max(line, hint.height())
        return y + line - rect.y() + margins.bottom()


class Panel(QWidget):
    """One column: a title strip, then its content, on a flat near-black fill.

    The strip is not a handle — there is nothing to drag a column to. It is a label, so
    you can tell at a glance which of the two narrow columns you are looking at, and it
    doubles as the surface the sidebar's buttons sit on."""

    def __init__(self, name: str, title: str | None, content: QWidget,
                 header: QWidget | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._name = name
        self.state = PanelState(name)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(MINIMUM.width())

        # A column with no title is a column that does not need one: the viewer is a
        # photograph, and a strip of grey capitals over a photograph is a label on a
        # window telling you it is a window. It also means Focus is genuinely full bleed.
        self.title = None
        if title is not None:
            self.title = QLabel(title.upper(), self)
            self.title.setObjectName("panelTitle")
            self.title.setFixedHeight(HEADER)
            self.title.setContentsMargins(10, 0, 10, 0)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        if self.title is not None:
            box.addWidget(self.title, 0)
        if header is not None:
            box.addWidget(header, 0)
        content.setParent(self)
        box.addWidget(content, 1)
        self.content = content

    @property
    def name(self) -> str:
        return self._name


STYLE = f"""
QWidget#panel {{ background: {BASE}; }}
QLabel#panelTitle {{ background: {RAISED}; color: {LABEL}; font-size: 9px;
                     font-weight: 700; letter-spacing: 2px; }}
QWidget#buttonBar {{ background: {RAISED}; }}
QSplitter::handle {{ background: {VOID}; }}
"""
