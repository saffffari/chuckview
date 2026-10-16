"""The columns, and the flat black material they are made of.

Light Table was three floating panes of refracting glass for a while. It looked like a
demo and worked like one: you could put the contact sheet anywhere, which mostly meant
you could put it somewhere wrong, and a photograph seen through a tinted pane is a
photograph you cannot trust. So it is three columns again — **Locations**, **Gallery**,
**Viewer**, always in that order, left to right.

The material is the one the hardware monitor is made of: a black ground, white ink, and
a hairline where two things have to be told apart. Nothing is a slab, nothing is raised,
nothing has a radius. A label is small capitals with a rule under it; a value is hard
right on the same line. Read PC-6's readout and this one and you should not have to ask
whether they came from the same shop.

Two rules hold the whole thing up:

- **Size, don't rearrange.** Every edge between columns is a splitter handle, so the
  layout is yours to proportion; the order is not yours to change, because there is only
  one order that reads left-to-right as narrowing down — where, then which, then this
  one. Columns open and close, and the widths persist.
- **Rules, not fills.** Every surface in the app is the same black. Two of them are told
  apart by a hairline between them and by nothing else, which is why there is no grey
  ladder here any more: a photograph on black next to a panel on black is a photograph
  with nothing borrowed from it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from lighttable.core import config_dir

# --- The material ---------------------------------------------------------------------
# One ground, one ink, one rule. The picture is the only bright thing on screen and the
# only thing with a colour in it; everything around it is black with white writing on it.
VOID = "#000000"       # the ground: the window, every column, the gutter between them
MATTE = "#000000"      # the matting the picture is laid on — the same black, on purpose
RULE = "#242424"       # the hairline, and the only way two surfaces are ever told apart
EDGE = "#3c3c3c"       # a hairline you are pointing at (a splitter handle under the mouse)

INK = "#f2f2f2"        # text
MUTED = "#6e6e6e"      # secondary text: values you are not reading right now
LABEL = "#9a9a9a"      # small-capital keys — the left column of a readout, a column head

# Every letter of chrome in the app is monospaced small capitals, which is what makes a
# readout a readout: keys line up, values line up, and a path does not reflow when a digit
# changes. File names are chrome too here — they are data you scan, not prose you read.
MONO = "'JetBrains Mono', 'JetBrains Mono NL', 'DejaVu Sans Mono', monospace"
MONO_FAMILIES = ("JetBrains Mono", "JetBrains Mono NL", "DejaVu Sans Mono", "Monospace")

HEADER = 19            # height of a column's label strip, rule included
ROW = 15               # height of one readout row, rule included
GUTTER = 1             # the hairline seam between two columns — also the grab handle
PAD = 8                # the one horizontal inset, used by every strip in the app
MINIMUM = QSize(150, 120)


def mono_font(size: int = 9, bold: bool = False, spacing: float = 0.8) -> QFont:
    """The chrome face. Small, monospaced, letterspaced a hair so capitals can breathe."""
    font = QFont()
    font.setFamilies(list(MONO_FAMILIES))
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(size)
    font.setBold(bold)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


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


class ColumnHead(QWidget):
    """A column's name, and one fact about it, over a hairline.

    Key hard left, value hard right, a rule underneath — the same three moves as a row of
    the readout below it, because a column head *is* a row of a readout: the one that
    happens to name the column."""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("columnHead")
        self._title = title.upper()
        self._value = ""
        self.setFixedHeight(HEADER)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, value: str) -> None:
        """The one fact: how many frames are on the sheet, which folder you are in."""
        value = value.upper()
        if value != self._value:
            self._value = value
            self.update()

    def value(self) -> str:
        return self._value

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(VOID))
        painter.setFont(mono_font(9, bold=True, spacing=1.4))
        painter.setPen(QColor(LABEL))
        box = self.rect().adjusted(PAD, 0, -PAD, -1)
        painter.drawText(box, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                         self._title)
        if self._value:
            painter.setFont(mono_font(9, spacing=0.6))
            painter.setPen(QColor(MUTED))
            metrics = QFontMetrics(painter.font())
            room = max(0, box.width() - metrics.horizontalAdvance(self._title) - 3 * PAD)
            painter.drawText(
                box, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                metrics.elidedText(self._value, Qt.TextElideMode.ElideLeft, room))
        painter.fillRect(0, self.height() - 1, self.width(), 1, QColor(RULE))


class Readout(QWidget):
    """The facts, as the hardware monitor would draw them.

    One row per fact: a three-letter key at the left, the value hard right, a hairline
    under the row. Text goes in as lines of `KEY\\tVALUE` — a line with no tab in it is a
    value with no key, and an empty string is no readout at all. A long path is elided
    from the left, because the end of it is the part that tells it apart; anything else
    loses its middle, because the start of it is the answer."""

    KEY_WIDTH = 30

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("readout")
        self._text = ""
        self._rows: list[tuple[str, str]] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def setText(self, text: str) -> None:  # noqa: N802 - matches QLabel, which this replaces
        self._text = text or ""
        self._rows = []
        for line in self._text.splitlines():
            if not line.strip():
                continue
            key, tab, value = line.partition("\t")
            self._rows.append((key.upper(), value) if tab else ("", key))
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._text

    def rows(self) -> list[tuple[str, str]]:
        return list(self._rows)

    def sizeHint(self) -> QSize:
        return QSize(MINIMUM.width(), ROW * len(self._rows))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(VOID))
        key_font = mono_font(9, bold=True, spacing=1.0)
        value_font = mono_font(9, spacing=0.4)
        metrics = QFontMetrics(value_font)
        width = self.width()
        for index, (key, value) in enumerate(self._rows):
            top = index * ROW
            line = QRect(PAD, top, max(0, width - 2 * PAD), ROW - 1)
            if key:
                painter.setFont(key_font)
                painter.setPen(QColor(LABEL))
                painter.drawText(line, int(Qt.AlignmentFlag.AlignVCenter
                                           | Qt.AlignmentFlag.AlignLeft), key)
                line = line.adjusted(self.KEY_WIDTH, 0, 0, 0)
            painter.setFont(value_font)
            painter.setPen(QColor(INK if key else MUTED))
            mode = (Qt.TextElideMode.ElideLeft if "/" in value
                    else Qt.TextElideMode.ElideMiddle)
            painter.drawText(
                line, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                metrics.elidedText(value, mode, line.width()))
            painter.fillRect(0, top, width, 1, QColor(RULE))


class Panel(QWidget):
    """One column: a label strip, then its content, on black.

    The strip is not a handle — there is nothing to drag a column to. It is a label, so
    you can tell at a glance which of the two narrow columns you are looking at."""

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
        self.title = ColumnHead(title, self) if title is not None else None

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


# Qt draws the widgets; this is only here to say "black, no border, no radius" once for
# each kind of thing, because a stylesheet default that is a grey slab shows through
# anywhere a rule was not drawn by hand.
STYLE = f"""
QWidget#panel, QWidget#columnHead, QWidget#readout {{ background: {VOID}; }}
QSplitter::handle {{ background: {RULE}; }}
QSplitter::handle:hover, QSplitter::handle:pressed {{ background: {EDGE}; }}
"""
