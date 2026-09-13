"""
The small charts the dashboard draws for itself.

A mix of species is three numbers that have to add up to a hundred, and an
operator reading them in a table has to do the adding. A bar does it for
them: the share asked for, the share the ground actually got, and the gap
between the two, in the colour the plants will be on the map.

Painted with ``QPainter``. No plotting library: QGIS ships none the plugin
may rely on, and a bar chart of four rows is forty lines of paint code
against a dependency an operator would have to install.

The colours are not chosen here. They come from
``forest.reforestation.symbology``, which generates them from the species
list on a golden-angle sequence, so the bar, the legend and the point on the
map are the same colour by construction rather than by two tables agreeing.
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPen
from qgis.PyQt.QtWidgets import QSizePolicy, QWidget

from ..forest.reforestation import symbology as symbology_mod
from . import theme

#: Height of one species row, in pixels, and the gap under it.
ROW_H = 18
ROW_GAP = 6
#: Width given to the label column and to the numbers on the right.
LABEL_W = 96
VALUE_W = 104
#: How far the achieved share may differ from the requested one before the
#: row is drawn as a warning. The verification uses the same one point.
TOLERANCE_PCT = 1.0


class SpeciesMixChart(QWidget):
    """Requested share against achieved share, one bar per species.

    The requested share is the full bar in the species' own colour; the
    achieved share is drawn over it as a solid fill. A species that came out
    short shows the colour behind it, one that came out long overruns into
    the warning colour -- so "the mix is not what was asked" is visible
    without reading a number.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []                      # [(key, label, wanted, got, n)]
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(ROW_H + ROW_GAP)
        self.setToolTip("")

    # -- what it shows -----------------------------------------------------

    def set_rows(self, rows) -> int:
        """``[(key, label, requested %, achieved %, plants)]``, in order."""
        self.rows = [(str(key), str(label), float(wanted), float(got),
                      int(count)) for key, label, wanted, got, count in rows]
        self.setMinimumHeight(max(ROW_H + ROW_GAP,
                                  len(self.rows) * (ROW_H + ROW_GAP) + 4))
        self.setToolTip(self.describe())
        self.updateGeometry()
        self.update()
        return len(self.rows)

    def clear(self) -> None:
        self.set_rows([])

    def describe(self) -> str:
        if not self.rows:
            return "Nessuna specie nel progetto."
        lines = []
        for _key, label, wanted, got, count in self.rows:
            lines.append("{0}: {1:.1f} % chiesto, {2:.1f} % ottenuto "
                         "({3:,} piante)".format(label, wanted, got, count))
        return "\n".join(lines)

    def colours(self) -> dict:
        """The layer's own palette, for exactly these keys in this order."""
        if not self.rows:
            return {}
        return symbology_mod.palette([key for key, *_rest in self.rows])

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event):                                # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(theme.PALETTE["panel"]))
        if not self.rows:
            painter.setPen(QPen(QColor(theme.PALETTE["muted"])))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "nessuna specie")
            painter.end()
            return

        palette = self.colours()
        small = QFont(self.font())
        small.setPointSize(theme.FONT_SMALL)
        track_x = LABEL_W + 4
        track_w = max(20, self.width() - LABEL_W - VALUE_W - 12)
        biggest = max([row[2] for row in self.rows]
                      + [row[3] for row in self.rows] + [1.0])

        for index, (key, label, wanted, got, count) in enumerate(self.rows):
            top = 2 + index * (ROW_H + ROW_GAP)
            colour = QColor(palette.get(key, theme.PALETTE["accent"]))

            painter.setFont(small)
            painter.setPen(QPen(QColor(theme.PALETTE["text"])))
            painter.drawText(QRectF(2, top, LABEL_W - 6, ROW_H),
                             int(Qt.AlignmentFlag.AlignLeft
                                 | Qt.AlignmentFlag.AlignVCenter),
                             self._elide(painter, label, LABEL_W - 6))

            # The track: what was asked for, in the species' colour, faded.
            painter.setPen(Qt.PenStyle.NoPen)
            ghost = QColor(colour)
            ghost.setAlpha(70)
            painter.setBrush(ghost)
            asked_w = track_w * (wanted / biggest)
            painter.drawRoundedRect(QRectF(track_x, top + 2, asked_w,
                                           ROW_H - 4), 2.0, 2.0)

            # The fill: what the ground actually got.
            over = got > wanted + TOLERANCE_PCT
            short = got < wanted - TOLERANCE_PCT
            fill = QColor(theme.PALETTE["warning"]) if over else QColor(colour)
            painter.setBrush(fill)
            got_w = track_w * (got / biggest)
            painter.drawRoundedRect(QRectF(track_x, top + 4, got_w,
                                           ROW_H - 8), 2.0, 2.0)

            # The mark where the request sits, so a gap is measurable.
            painter.setPen(QPen(QColor(theme.PALETTE["text"]), 1.0))
            painter.drawLine(int(track_x + asked_w), top + 1,
                             int(track_x + asked_w), top + ROW_H - 1)

            painter.setPen(QPen(QColor(
                theme.PALETTE["warning"] if (over or short)
                else theme.PALETTE["muted"])))
            painter.drawText(
                QRectF(self.width() - VALUE_W - 2, top, VALUE_W, ROW_H),
                int(Qt.AlignmentFlag.AlignRight
                    | Qt.AlignmentFlag.AlignVCenter),
                "{0:.1f}% / {1:.1f}%  {2:,}".format(got, wanted, count))
        painter.end()

    @staticmethod
    def _elide(painter, text: str, width: int) -> str:
        metrics = painter.fontMetrics()
        if metrics.horizontalAdvance(text) <= width:
            return text
        while text and metrics.horizontalAdvance(text + "...") > width:
            text = text[:-1]
        return text + "..."


__all__ = ["SpeciesMixChart", "ROW_H", "TOLERANCE_PCT"]
