"""The material, inherited from Light Table: black, white, and a hairline.

One ground everywhere; the picture is the only bright thing on screen and the only thing in
colour, apart from orange, which means *selected* and nothing else — and the five colour
labels, which are data you put on an image, drawn as a hairline under its thumbnail.
Every letter of chrome is monospaced small capitals so values line up in columns.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor

from chuckview.common import INK, SELECT
from chuckview.panels import EDGE, LABEL, MATTE, MONO, MUTED, PAD, RULE, VOID
from chuckview.panels import STYLE as PANEL_STYLE

CHROME = QColor(VOID)
CANVAS = QColor(MATTE)

# Colour labels are the researcher's own codes (include, exclude, unsure…). Muted, so a
# grid of them never competes with the pictures — and never orange.
LABEL_COLORS = {
    "red": "#c8463c", "yellow": "#c9b23c", "green": "#4f9a58", "blue": "#3f74c4",
    "purple": "#8a5cc0",
}

MATERIAL_STYLE = f"""
* {{ font-family: {MONO}; }}
QMainWindow, QWidget#shell, QDialog {{ background: {VOID}; }}
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

QListView, QListWidget, QTreeView, QTreeWidget, QWidget#panelBody, QTextEdit,
QPlainTextEdit, QScrollArea {{
    background: {VOID}; border: 0; color: {INK}; font-size: 10px; outline: 0;
    show-decoration-selected: 0; }}
QListWidget#sources::item, QTreeWidget::item, QListWidget#facet::item {{
    padding: 2px 2px; border: 0; }}
QListWidget#sources::item:selected, QListWidget#facet::item:selected,
QTreeWidget::item:selected {{ background: transparent; color: {SELECT}; }}
QListView#grid::item:selected {{ background: transparent; }}
QHeaderView::section {{ background: {VOID}; color: {LABEL}; border: 0;
                        border-bottom: 1px solid {RULE}; padding: 3px 6px; font-size: 9px; }}
QLabel {{ background: transparent; color: {INK}; font-size: 10px; }}
QLabel#muted {{ color: {MUTED}; font-size: 9px; }}
QLabel#error {{ color: {SELECT}; font-size: 9px; }}
QGraphicsView#canvas {{ background: {MATTE}; border: 0; }}
QSplitter {{ background: {VOID}; }}

QLineEdit {{ background: {VOID}; border: 0; border-bottom: 1px solid {RULE};
             padding: 5px {PAD}px; color: {INK}; font-size: 10px;
             selection-background-color: {SELECT}; selection-color: {VOID}; }}
QLineEdit:focus {{ border-bottom: 1px solid {SELECT}; }}
QTextEdit, QPlainTextEdit {{ border: 1px solid {RULE}; padding: 4px; }}
QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {EDGE}; }}
QComboBox {{ background: {VOID}; border: 0; border-bottom: 1px solid {RULE}; color: {LABEL};
             padding: 3px {PAD}px; font-size: 9px; }}
QComboBox QAbstractItemView {{ background: {VOID}; color: {INK}; border: 1px solid {RULE};
                               selection-background-color: {RULE}; }}
QCheckBox {{ color: {INK}; font-size: 10px; spacing: 6px; }}
QCheckBox::indicator {{ width: 10px; height: 10px; border: 1px solid {EDGE};
                        background: {VOID}; }}
QCheckBox::indicator:checked {{ background: {INK}; }}
QProgressBar {{ background: {VOID}; border: 0; border-bottom: 1px solid {RULE};
                height: 3px; color: transparent; }}
QProgressBar::chunk {{ background: {INK}; }}

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
QInputDialog, QMessageBox, QFileDialog {{ background: {VOID}; color: {INK}; }}
QPushButton {{ background: {VOID}; border: 1px solid {RULE}; padding: 4px 12px;
               color: {INK}; font-size: 9px; letter-spacing: 1px; border-radius: 0; }}
QPushButton:hover {{ border-color: {INK}; }}
QPushButton:default {{ border-color: {INK}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {RULE}; }}
QTabBar {{ background: {VOID}; }}
QTabBar::tab {{ background: {VOID}; color: {MUTED}; border: 0; border-bottom: 1px solid {VOID};
                padding: 4px 10px; font-size: 9px; letter-spacing: 1px; }}
QTabBar::tab:selected {{ color: {INK}; border-bottom: 1px solid {INK}; }}
QTabWidget::pane {{ border: 0; }}
""" + PANEL_STYLE
