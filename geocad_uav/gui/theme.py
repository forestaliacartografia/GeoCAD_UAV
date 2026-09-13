"""
The look of the plugin's own panels: dark, dense, and nobody else's.

Two rules shaped this.

**It styles the plugin's widgets, never the application.** A stylesheet set on
``QgsApplication`` would re-skin QGIS itself, every other plugin's dock and
every dialog the operator opens -- from a plugin that was asked to draw its
own panels. So the sheet is applied to the two docks of the workspace and to
nothing else, and it is written with descendant selectors that stop there.

**Colours are named once.** A palette dictionary, one stylesheet built from
it, so a change of accent is one line and not forty. These are interface
colours -- panel, border, text, the accent on a focused control -- and not
species colours: those come from ``forest.reforestation.symbology``, which
generates them from the species list and is the only place allowed to decide
what a species looks like.

Density is deliberate: small metrics, tight padding, a readable monospace for
numbers. An operator reading a planting plan is reading numbers, and the
default Qt spacing puts four of them on a screen.
"""

from __future__ import annotations

#: Every colour the sheet uses, once.
PALETTE = {
    "bg": "#1b1f24",            # dock background
    "panel": "#22272e",         # group boxes, tables
    "panel_alt": "#2b313a",     # alternating rows, headers
    "border": "#39414b",
    "text": "#dfe5ec",
    "muted": "#8b95a3",
    "accent": "#4c9f70",        # the forest green the plugin answers to
    "accent_dim": "#3b7a56",
    "warning": "#d8a657",
    "field": "#171b20",
    "on_accent": "#ffffff",     # text on top of the accent
}

#: Type sizes in points. The numbers an operator reads are the big ones.
FONT_SMALL = 8
FONT_BASE = 9
FONT_METRIC = 13

QSS = """
QWidget {{
    background: {bg};
    color: {text};
    font-size: {font_base}pt;
}}
QDockWidget {{
    background: {bg};
    color: {text};
    titlebar-close-icon: none;
}}
QDockWidget::title {{
    background: {panel_alt};
    padding: 5px 8px;
    border-bottom: 1px solid {border};
    font-weight: bold;
}}
QGroupBox {{
    background: {panel};
    border: 1px solid {border};
    border-radius: 4px;
    margin-top: 14px;
    padding: 8px 6px 6px 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
    color: {muted};
    font-size: {font_small}pt;
    text-transform: uppercase;
    letter-spacing: 1px;
}}
QLabel {{
    background: transparent;
    color: {text};
}}
QLabel[metric="true"] {{
    font-size: {font_metric}pt;
    font-weight: bold;
    font-family: "Consolas", "DejaVu Sans Mono", monospace;
    color: {accent};
}}
QLabel[muted="true"] {{
    color: {muted};
    font-size: {font_small}pt;
}}
QPushButton {{
    background: {panel_alt};
    border: 1px solid {border};
    border-radius: 3px;
    padding: 4px 10px;
}}
QPushButton:hover {{ background: {border}; }}
QPushButton:pressed {{ background: {accent_dim}; }}
QPushButton:disabled {{ color: {muted}; background: {panel}; }}
QPushButton[primary="true"] {{
    background: {accent_dim};
    border: 1px solid {accent};
    font-weight: bold;
}}
QPushButton[primary="true"]:hover {{ background: {accent}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextBrowser, QPlainTextEdit {{
    background: {field};
    border: 1px solid {border};
    border-radius: 3px;
    padding: 2px 4px;
    selection-background-color: {accent_dim};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {accent};
}}
QComboBox QAbstractItemView {{
    background: {field};
    border: 1px solid {border};
    selection-background-color: {accent_dim};
}}
QTableWidget {{
    background: {panel};
    alternate-background-color: {panel_alt};
    gridline-color: {border};
    border: 1px solid {border};
    selection-background-color: {accent_dim};
}}
QHeaderView::section {{
    background: {panel_alt};
    color: {muted};
    border: 0;
    border-bottom: 1px solid {border};
    padding: 3px 5px;
    font-size: {font_small}pt;
    text-transform: uppercase;
}}
QListWidget {{
    background: {panel};
    border: 1px solid {border};
    outline: 0;
}}
QListWidget::item {{ padding: 5px 6px; }}
QListWidget::item:selected {{
    background: {accent_dim};
    color: {on_accent};
    font-weight: bold;
}}
QListWidget::item:hover {{ background: {panel_alt}; }}
QTabWidget::pane {{
    border: 1px solid {border};
    background: {panel};
}}
QTabBar::tab {{
    background: {panel};
    color: {muted};
    padding: 4px 12px;
    border: 1px solid {border};
    border-bottom: 0;
}}
QTabBar::tab:selected {{
    background: {panel_alt};
    color: {text};
    font-weight: bold;
}}
QToolBar {{
    background: {panel_alt};
    border: 0;
    border-bottom: 1px solid {border};
    spacing: 2px;
    padding: 2px;
}}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 3px 6px;
    font-size: {font_small}pt;
}}
QToolButton:hover {{ background: {border}; border: 1px solid {accent_dim}; }}
QToolButton:disabled {{ color: {muted}; }}
QCheckBox {{ background: transparent; spacing: 5px; }}
QScrollArea {{ background: {bg}; border: 0; }}
QScrollBar:vertical {{
    background: {bg}; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {border}; border-radius: 5px; min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{ background: {accent_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QProgressBar {{
    background: {field};
    border: 1px solid {border};
    border-radius: 3px;
    text-align: center;
    height: 14px;
}}
QProgressBar::chunk {{ background: {accent_dim}; border-radius: 2px; }}
"""


def stylesheet(palette=None) -> str:
    """The sheet, built from the palette. Pure text: no Qt needed."""
    values = dict(PALETTE)
    values.update(palette or {})
    values.update({"font_base": FONT_BASE, "font_small": FONT_SMALL,
                   "font_metric": FONT_METRIC})
    return QSS.format(**values)


def apply(*widgets) -> int:
    """Style these widgets and their children. Returns how many took it.

    Never ``QgsApplication.setStyleSheet``: that would re-skin QGIS itself
    and every other plugin's dock, from a plugin asked to draw its own
    panels. A widget that refuses the sheet keeps its own look, which is
    plain, not broken.
    """
    sheet = stylesheet()
    applied = 0
    for widget in widgets:
        if widget is None:
            continue
        try:
            widget.setStyleSheet(sheet)
            applied += 1
        except (AttributeError, RuntimeError):
            continue
    return applied


def mark(widget, **properties):
    """Set the dynamic properties the sheet selects on, and re-polish.

    Qt does not re-evaluate a property selector by itself: a widget marked
    after it was shown keeps the old look until its style is asked again.
    """
    if widget is None:
        return None
    for name, value in properties.items():
        try:
            widget.setProperty(name, value)
        except (AttributeError, RuntimeError):
            continue
    try:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
    except (AttributeError, RuntimeError):
        pass
    return widget


__all__ = ["PALETTE", "QSS", "stylesheet", "apply", "mark", "FONT_BASE",
           "FONT_SMALL", "FONT_METRIC"]
