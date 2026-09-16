"""Floating panels, and the glass they are made of.

Light Table used to be three columns side by side, which meant the picture only ever got
the third of the window nobody else was using. Now the picture is the window, edge to
edge, and everything else floats over it on panes of glass: pick a pane up by its header,
drop it anywhere, drag its edges to size it, shove it against a side of the window to dock
it there. Nothing is nailed down, and nothing permanently costs the picture any room.

That only works if the panes are honestly transparent — a solid box parked over a
photograph is worse than a column beside it. So each one blurs what it is covering,
bends it at the edge (`glass.py` does the optics, from the desktop's own settings), and
carries a lit rim so you can tell where the pane stops and the picture starts. Depth is
the whole point: shadow under the pane, the pane, then the controls raised again on top
of it, three layers you can read apart at a glance.

The cost of all that is kept honest by only ever doing the expensive part once. The
viewport is captured small and blurred once per change, not once per panel; the refracting
edge is the only thing computed per pixel, and only along a band; and the maps behind it
are cached per size, so a panel dragged across a photograph recomputes nothing at all.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen,
)
from PyQt6.QtWidgets import QGraphicsView, QSizePolicy, QVBoxLayout, QWidget

from apps.lighttable.core import config_dir
from apps.lighttable.glass import MaterialCache, blur_passes, read_glass_settings

# The material, in the house grey. The tint is a veil over the blurred picture: high
# enough that off-white ink stays legible over a white photograph, low enough that you can
# always see what the panel is standing on.
TINT = (13, 13, 13)
TINT_ALPHA = 0.62
HEADER_ALPHA = 0.30      # the header is the same glass, one layer nearer the eye
INK = QColor(242, 242, 242)
MUTED = QColor(242, 242, 242, 120)
HAIRLINE = QColor(242, 242, 242, 26)

HEADER = 26              # height of a panel's title bar
GRIP = 6                 # how wide the resize edge is
SNAP = 26                # how close to a window edge before the panel docks to it
MINIMUM = QSize(190, 120)
SHADOW_DEPTH = 18        # how far the drop shadow spreads


def image_to_rgb(image: QImage) -> np.ndarray:
    """A QImage as an (h, w, 3) uint8 array, copied out of Qt's buffer.

    The copy is not optional: the QImage behind this is a temporary crop, and NumPy would
    otherwise be pointing at freed memory the moment it goes out of scope."""
    converted = image.convertToFormat(QImage.Format.Format_RGB888)
    width, height = converted.width(), converted.height()
    bits = converted.constBits()
    bits.setsize(converted.sizeInBytes())
    rows = np.frombuffer(bits, dtype=np.uint8).reshape(height, converted.bytesPerLine())
    return rows[:, : width * 3].reshape(height, width, 3).copy()


def rgba_to_image(array: np.ndarray) -> QImage:
    """An (h, w, 4) uint8 array as a QImage that owns its own pixels."""
    height, width, _ = array.shape
    data = np.ascontiguousarray(array)
    return QImage(data.data, width, height, 4 * width,
                  QImage.Format.Format_RGBA8888).copy()


class Backdrop:
    """What the panels are covering, captured small and blurred once per change.

    Capturing at a fraction of the window is not a shortcut, it is the blur: the picture
    is going behind frosted glass, so resolution spent on it would be thrown away in the
    next step anyway. One capture serves every panel in the window."""

    SCALE = 4  # the capture is this much smaller than the window, in each direction

    def __init__(self, view: QGraphicsView, settings=None):
        self.view = view
        self.settings = settings if settings is not None else read_glass_settings()
        self.reduce = 2 ** blur_passes(self.settings, self.SCALE)
        self.image: QImage | None = None

    def refresh(self) -> None:
        """Re-capture the viewport. Rendering the *scene* rather than the widget keeps
        this outside Qt's paint path, so it can never re-enter the repaint it was
        triggered by."""
        width = max(1, self.view.width() // self.SCALE)
        height = max(1, self.view.height() // self.SCALE)
        capture = QImage(width, height, QImage.Format.Format_RGB32)
        capture.fill(self.view.backgroundBrush().color())
        painter = QPainter(capture)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.view.render(painter, QRectF(0, 0, width, height),
                         QRect(0, 0, self.view.width(), self.view.height()),
                         Qt.AspectRatioMode.IgnoreAspectRatio)
        painter.end()
        # Frost it: down and back up with smooth scaling is a box filter written in C++,
        # which is both better looking and far faster than anything we would write here.
        tiny = capture.scaled(max(1, width // self.reduce), max(1, height // self.reduce),
                              Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        self.image = tiny.scaled(capture.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)

    def crop(self, rect: QRect) -> QImage | None:
        """The blurred backdrop behind `rect` (in viewport coordinates), at full size."""
        if self.image is None or rect.isEmpty():
            return None
        scale = self.SCALE
        small = QRect(rect.x() // scale, rect.y() // scale,
                      rect.width() // scale + 2, rect.height() // scale + 2)
        small = small.intersected(self.image.rect())
        if small.isEmpty():
            return None
        return self.image.copy(small).scaled(rect.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)


class Glass:
    """One window's worth of glass: the desktop's settings, the cached per-size maps, and
    the live backdrop every panel shares."""

    def __init__(self, view: QGraphicsView):
        self.settings = read_glass_settings()
        self.material = MaterialCache(self.settings)
        self.backdrop = Backdrop(view, self.settings)

    @property
    def radius(self) -> float:
        return self.settings.radius

    def pane(self, painter: QPainter, rect: QRect, host_offset: QPoint,
             tint_alpha: float = TINT_ALPHA) -> None:
        """Paint one pane of glass into `rect`, showing what is behind it.

        `host_offset` is where `rect` sits in the viewport, which is what the backdrop is
        indexed by. Three steps, cheapest first: the blurred picture, the tint over it,
        and finally the refracting edge — the only part that is computed per pixel."""
        radius = self.radius
        body = QPainterPath()
        body.addRoundedRect(QRectF(rect), radius, radius)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        source = QRect(host_offset, rect.size())
        backdrop = self.backdrop.crop(source)
        if backdrop is not None:
            brush = QBrush(backdrop)
            brush.setTransform(brush.transform().translate(rect.x(), rect.y()))
            painter.setBrush(brush)
            painter.drawPath(body)
        tint = QColor(*TINT)
        tint.setAlphaF(tint_alpha)
        painter.fillPath(body, tint)

        pixels = image_to_rgb(backdrop) if backdrop is not None else None
        edge = self.material.rim_image(pixels, rect.width(), rect.height(), TINT, tint_alpha)
        painter.drawImage(rect.topLeft(), rgba_to_image(edge))


def paint_shadow(painter: QPainter, rect: QRect, radius: float,
                 depth: int = SHADOW_DEPTH) -> None:
    """A soft shadow under a pane of glass, drawn as a stack of fading outlines.

    Qt's own drop-shadow effect would re-render the widget into an offscreen buffer on
    every repaint; a dozen rounded rectangles cost nothing and, unlike an effect, can be
    drawn *underneath* a sibling widget instead of around it."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    steps = max(1, depth // 2)
    for step in range(steps, 0, -1):
        spread = step * 2
        alpha = int(40 * (1.0 - step / (steps + 1)) ** 2)
        if alpha <= 0:
            continue
        painter.setBrush(QColor(0, 0, 0, alpha))
        painter.drawRoundedRect(
            QRectF(rect.adjusted(-spread, -spread + 3, spread, spread + 3)),
            radius + spread, radius + spread)


class ShadowLayer(QWidget):
    """The shadows for every panel in one click-through sheet.

    A widget cannot paint outside itself, so a panel cannot cast its own shadow without
    reserving a dead margin all around it — and a dead margin over a photograph is a place
    the pan gesture mysteriously stops working. Instead the shadows live here, in a single
    transparent sheet stacked between the picture and the panels, which nothing can click
    on and which therefore costs the picture nothing."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("shadows")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.panels: list[GlassPanel] = []
        self.radius = 12.0

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        for panel in self.panels:
            if panel.isVisible():
                paint_shadow(painter, panel.geometry(), self.radius)


@dataclass(frozen=True)
class PanelState:
    """Where a panel is and how it is behaving. The whole layout is a handful of these,
    and they are what gets written to the layout file."""

    name: str
    x: int = 40
    y: int = 40
    width: int = 300
    height: int = 520
    open: bool = False
    snap: str = "free"       # free · left · right · top · bottom
    collapsed: bool = False


EDGES = ("left", "right", "top", "bottom")


# --- Where the layout is kept ---------------------------------------------------------
# One flat line per panel, in the same spirit as the favorites file: no database, no
# settings dialog, and if you want your panels somewhere specific on every machine you
# copy one text file. An unreadable or half-written line falls back to the default for
# that panel rather than taking the layout down.

def layout_file() -> Path:
    return config_dir() / "lighttable-panels.txt"


def format_layout(states: Iterable[PanelState]) -> str:
    lines = [
        f"{state.name} {state.x} {state.y} {state.width} {state.height} "
        f"{int(state.open)} {state.snap} {int(state.collapsed)}"
        for state in states
    ]
    return ("# Light Table panels — name x y width height open snap collapsed\n"
            + "\n".join(lines) + "\n")


def parse_layout(text: str, defaults: Iterable[PanelState]) -> dict[str, PanelState]:
    """Read a layout, keeping the default for every panel the file does not describe."""
    states = {state.name: state for state in defaults}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 7 or parts[0] not in states:
            continue
        name = parts[0]
        try:
            x, y, width, height = (int(part) for part in parts[1:5])
            is_open = parts[5] not in ("0", "false", "False")
            snap = parts[6] if parts[6] in EDGES or parts[6] == "free" else "free"
            collapsed = len(parts) > 7 and parts[7] not in ("0", "false", "False")
        except ValueError:
            continue
        states[name] = PanelState(name, x, y, width, height, is_open, snap, collapsed)
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


def snap_geometry(state: PanelState, area: QRect) -> QRect:
    """Where a panel actually sits, given the area it is floating in.

    The area is the window minus whatever is already lying on it — the command bar keeps
    the top strip, so a panel docked to the left starts under the bar rather than behind
    it. A free panel keeps the geometry it was dropped at, clamped so it can never be
    dragged somewhere it cannot be picked up again. A docked one keeps its thickness and
    takes the whole of its edge; sharing that edge is handled in `share_edge`, because it
    depends on who else is docked there."""
    width = max(MINIMUM.width(), min(state.width, area.width()))
    height = HEADER if state.collapsed else max(MINIMUM.height(), state.height)
    # Rolled up is rolled up wherever it is: a docked panel gives its edge back rather
    # than sitting there as a full-height title bar.
    tall = HEADER if state.collapsed else area.height()
    if state.snap == "left":
        return QRect(area.left(), area.top(), width, tall)
    if state.snap == "right":
        return QRect(area.right() - width + 1, area.top(), width, tall)
    if state.snap == "top":
        return QRect(area.left(), area.top(), area.width(), min(height, area.height()))
    if state.snap == "bottom":
        height = min(height, area.height())
        return QRect(area.left(), area.bottom() - height + 1, area.width(), height)
    x = max(area.left() - width + 120, min(state.x, area.right() - 120))
    y = max(area.top(), min(state.y, max(area.top(), area.bottom() - HEADER)))
    return QRect(x, y, width, height)


def snap_for(pointer: QPoint, area: QRect) -> str:
    """Which edge a panel dropped with the pointer here should dock to, if any.

    The pointer, not the panel. Measuring the panel sounds more direct and is a trap: a
    tall panel is near the top edge and the bottom edge at the same time no matter where
    you put it, so "closest edge" ends up docking a column to the ceiling because the
    arithmetic came out a pixel shorter. Where you let go of it is unambiguous, and it is
    also how every window manager snaps a window — drag it at the edge and it takes the
    edge. Let go anywhere else and it floats."""
    distances = {
        "left": pointer.x() - area.left(),
        "right": area.right() - pointer.x(),
        "top": pointer.y() - area.top(),
        "bottom": area.bottom() - pointer.y(),
    }
    edge = min(distances, key=lambda name: distances[name])
    return edge if distances[edge] <= SNAP else "free"


def share_edge(states: list[PanelState], area: QRect) -> dict[str, QRect]:
    """Geometry for every panel, with panels docked to the same edge splitting it.

    Two panels shoved against the left edge is not an error, it is a column of two — so
    they take half the height each, in the order they were dropped. This is what makes the
    layout rearrangeable rather than just movable."""
    geometry = {state.name: snap_geometry(state, area) for state in states}
    for edge in EDGES:
        sharing = [state for state in states if state.snap == edge]
        if len(sharing) < 2:
            continue
        vertical = edge in ("left", "right")
        span = area.height() if vertical else area.width()
        origin = area.top() if vertical else area.left()
        # A rolled-up panel takes its header and no more; the rest of the edge is split
        # between the ones that are actually showing something.
        rolled = [state for state in sharing if state.collapsed]
        open_count = len(sharing) - len(rolled)
        left_over = max(HEADER, span - HEADER * len(rolled))
        each = left_over // open_count if open_count else left_over
        start = 0
        for index, state in enumerate(sharing):
            rect = QRect(geometry[state.name])
            last = index == len(sharing) - 1
            length = HEADER if state.collapsed else (span - start if last else each)
            if vertical:
                rect.setY(origin + start)
                rect.setHeight(length)
            else:
                rect.setX(origin + start)
                rect.setWidth(length)
            geometry[state.name] = rect
            start += length
    return geometry


class GlassPanel(QWidget):
    """One floating pane: a header you drag it by, a content widget, and edges you size it
    from. Everything it knows about itself lives in `state`, which is also what gets
    saved — there is no second copy of the layout anywhere."""

    moved = pyqtSignal()
    closed = pyqtSignal(str)

    def __init__(self, state: PanelState, title: str, content: QWidget, glass: Glass,
                 parent: QWidget):
        super().__init__(parent)
        self.state = state
        self.title = title
        self.glass = glass
        self.content = content
        self.setObjectName("glassPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, HEADER, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(content)

        self._drag_origin: QPoint | None = None   # where in the header it was picked up
        self._resize_edges: tuple[bool, bool, bool, bool] = (False, False, False, False)
        self._resize_start: tuple[QRect, QPoint] | None = None

    # -- geometry ----------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.state.name

    def apply_state(self, state: PanelState, rect: QRect) -> None:
        """Take a new state and the geometry the host worked out for it."""
        self.state = state
        self.setVisible(state.open)
        self.content.setVisible(not state.collapsed)
        self.setGeometry(rect)

    def close_button_rect(self) -> QRect:
        return QRect(self.width() - HEADER, 0, HEADER, HEADER)

    def header_rect(self) -> QRect:
        return QRect(0, 0, self.width(), HEADER)

    def _edges_at(self, pos: QPoint) -> tuple[bool, bool, bool, bool]:
        """Which edges the cursor is on, as (left, right, top, bottom)."""
        return (
            pos.x() <= GRIP,
            pos.x() >= self.width() - GRIP,
            pos.y() <= GRIP,
            pos.y() >= self.height() - GRIP,
        )

    @staticmethod
    def _cursor_for(edges: tuple[bool, bool, bool, bool]) -> Qt.CursorShape:
        left, right, top, bottom = edges
        if (left and top) or (right and bottom):
            return Qt.CursorShape.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        if top or bottom:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    # -- the gestures that make it modular ---------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pos = event.position().toPoint()
        if self.close_button_rect().contains(pos):
            self.closed.emit(self.name)
            return
        edges = self._edges_at(pos)
        if any(edges):
            self._resize_edges = edges
            self._resize_start = (QRect(self.geometry()), event.globalPosition().toPoint())
            return
        if self.header_rect().contains(pos):
            self.raise_()
            self._drag_origin = pos
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._resize_start is not None:
            self._resize(event.globalPosition().toPoint())
            return
        if self._drag_origin is not None:
            self._move_to(self.mapToParent(pos) - self._drag_origin)
            return
        self.setCursor(self._cursor_for(self._edges_at(pos)))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        dropped, resized = self._drag_origin is not None, self._resize_start is not None
        if dropped or resized:
            self._drag_origin = self._resize_start = None
            if dropped:
                # Docking happens on drop, not while dragging: a panel that leapt to the
                # edge halfway through would be impossible to place anywhere near one.
                # Dragging an *edge* never re-docks — you were sizing it, not moving it.
                host = self.parentWidget()
                pointer = host.mapFromGlobal(event.globalPosition().toPoint())
                self.state = replace(self.state,
                                     snap=snap_for(pointer, host.panel_area()))
            self.moved.emit()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.header_rect().contains(event.position().toPoint()):
            self.state = replace(self.state, collapsed=not self.state.collapsed)
            self.moved.emit()
            return
        super().mouseDoubleClickEvent(event)

    def _move_to(self, corner: QPoint) -> None:
        self.state = replace(self.state, x=corner.x(), y=corner.y(), snap="free")
        self.moved.emit()

    def _resize(self, globally: QPoint) -> None:
        start_rect, start_pos = self._resize_start
        left, right, top, bottom = self._resize_edges
        delta = globally - start_pos
        rect = QRect(start_rect)
        if left:
            rect.setLeft(min(rect.left() + delta.x(), rect.right() - MINIMUM.width()))
        if right:
            rect.setRight(max(rect.right() + delta.x(), rect.left() + MINIMUM.width()))
        if top:
            rect.setTop(min(rect.top() + delta.y(), rect.bottom() - MINIMUM.height()))
        if bottom:
            rect.setBottom(max(rect.bottom() + delta.y(), rect.top() + MINIMUM.height()))
        # A docked panel keeps its edge while you drag its thickness around; only its
        # width (or height) is yours to change, which is what docking is for.
        snap = self.state.snap
        self.state = replace(self.state, x=rect.x(), y=rect.y(),
                             width=rect.width(), height=rect.height(),
                             snap=snap, collapsed=False)
        self.moved.emit()

    # -- paint: shadow (below, in the shadow layer) · pane · controls -------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        rect = self.rect().adjusted(0, 0, -1, -1)
        self.glass.pane(painter, rect, self.pos())

        # The header is the same glass one layer nearer the eye: a lighter wash, a
        # hairline under it, and the name of the panel in muted ink. Three planes —
        # shadow, pane, header — is what gives the thing depth you can actually read.
        header = self.header_rect().adjusted(0, 0, -1, 0)
        radius = self.glass.radius
        cap = QPainterPath()
        cap.addRoundedRect(QRectF(rect), radius, radius)
        clip = QPainterPath()
        clip.addRect(QRectF(header))
        painter.fillPath(cap.intersected(clip), QColor(242, 242, 242, 14))
        painter.setPen(QPen(HAIRLINE, 1))
        painter.drawLine(header.bottomLeft(), header.bottomRight())

        font = QFont(self.font())
        font.setPointSizeF(max(6.5, font.pointSizeF() - 2.0))
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
        painter.setFont(font)
        painter.setPen(MUTED)
        painter.drawText(header.adjusted(10, 0, -HEADER, 0),
                         int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                         self.title.upper())

        close = self.close_button_rect()
        painter.setPen(QPen(MUTED if not self.underMouse() else INK, 1.2))
        inset = close.adjusted(9, 9, -9, -9)
        painter.drawLine(inset.topLeft(), inset.bottomRight())
        painter.drawLine(inset.topRight(), inset.bottomLeft())


class GlassRail(QWidget):
    """A pane of glass with no header and nothing to drag — the base for the bits of
    chrome that float over the picture without being panels: the pull-tabs, the command
    bar. The difference between a picture with a toolbar and a picture with something
    resting on it.

    Subclasses that do not live inside the viewport override `backdrop_origin` to say
    where they are in it, since that is what the backdrop is indexed by."""

    def __init__(self, glass: Glass | None, parent: QWidget):
        super().__init__(parent)
        self._glass = glass
        self.setObjectName("glassRail")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def current_glass(self) -> Glass | None:
        return self._glass

    def backdrop_origin(self) -> QPoint:
        return self.pos()

    def paintEvent(self, event) -> None:
        glass = self.current_glass()
        if glass is None:
            return
        painter = QPainter(self)
        glass.pane(painter, self.rect().adjusted(0, 0, -1, -1), self.backdrop_origin(),
                   tint_alpha=TINT_ALPHA + 0.06)
