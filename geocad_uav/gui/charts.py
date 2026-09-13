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

import numpy as np
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import (QColor, QFont, QPainter, QPainterPath, QPen,
                             QPolygonF)
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


# --------------------------------------------------------------------------
# The altimetric profile
# --------------------------------------------------------------------------

#: Margins of the plotting area: room for the metre labels on the left and
#: the chainage labels underneath.
PAD_L, PAD_R, PAD_T, PAD_B = 46, 10, 10, 22

#: Horizontal grid lines, and vertical ones.
Y_TICKS = 4
X_TICKS = 4


class ElevationProfile(QWidget):
    """Terrain and commanded height along the whole route.

    Fed by :func:`gui.mission_report.profile_series`, which is the same
    function the HTML report draws from -- one stitching of the per-leg
    chainages, two renderings, so the picture in the panel and the picture
    in the report cannot disagree.

    ``set_cursor`` puts a vertical line at a distance along the route; the
    simulator moves it while the marker walks the map.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = np.empty(0)
        self.ground = np.empty(0)
        self.flight = np.empty(0)
        self.h_agl_m = 0.0
        self.cursor_s = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(150)
        self.setToolTip("")

    # -- what it shows -----------------------------------------------------

    def set_mission(self, mission) -> int:
        """Read a mission's profile. Returns how many samples it will draw."""
        from .mission_report import profile_series                # noqa: PLC0415

        if mission is None:
            self.s = self.ground = self.flight = np.empty(0)
            self.h_agl_m = 0.0
        else:
            self.s, self.ground, self.flight = profile_series(mission)
            self.h_agl_m = float(getattr(mission, "h_agl_m", 0.0) or 0.0)
        self.cursor_s = None
        self.setToolTip(self.describe())
        self.update()
        return int(self.s.size)

    def clear(self) -> None:
        self.set_mission(None)

    def set_cursor(self, distance_m) -> None:
        """Put the cursor at a chainage, or remove it with None."""
        self.cursor_s = (None if distance_m is None
                         else float(distance_m))
        self.update()

    @property
    def length_m(self) -> float:
        return float(self.s[-1] - self.s[0]) if self.s.size else 0.0

    def agl(self):
        """Height above ground at every sample, in metres."""
        if not self.s.size:
            return np.empty(0)
        return self.flight - self.ground

    def describe(self) -> str:
        if not self.s.size:
            return "Nessun profilo: genera prima la rotta."
        agl = self.agl()
        return ("Percorso {0:,.0f} m | terreno da {1:,.0f} a {2:,.0f} m "
                "s.l.m. | AGL da {3:.1f} a {4:.1f} m".format(
                    self.length_m, float(self.ground.min()),
                    float(self.ground.max()), float(agl.min()),
                    float(agl.max())))

    # -- painting ----------------------------------------------------------

    def _frame(self):
        return QRectF(PAD_L, PAD_T,
                      max(10.0, self.width() - PAD_L - PAD_R),
                      max(10.0, self.height() - PAD_T - PAD_B))

    def _scales(self):
        """``(x_min, x_span, y_min, y_span)`` of the plotted window."""
        x_min, x_max = float(self.s.min()), float(self.s.max())
        y_min = float(min(self.ground.min(), self.flight.min()))
        y_max = float(max(self.ground.max(), self.flight.max()))
        span = max(y_max - y_min, 1.0)
        y_min -= span * 0.08
        y_max += span * 0.08
        return x_min, max(x_max - x_min, 1.0), y_min, max(y_max - y_min, 1.0)

    def _points(self, values, x_min, x_span, y_min, y_span, frame):
        return [QPointF(
            frame.left() + (float(self.s[i]) - x_min) / x_span * frame.width(),
            frame.top() + (y_min + y_span - float(values[i])) / y_span
            * frame.height()) for i in range(values.size)]

    def paintEvent(self, event):                                # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(theme.PALETTE["panel"]))
        if self.s.size < 2:
            painter.setPen(QPen(QColor(theme.PALETTE["muted"])))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "nessun profilo")
            painter.end()
            return

        frame = self._frame()
        x_min, x_span, y_min, y_span = self._scales()
        small = QFont(self.font())
        small.setPointSize(theme.FONT_SMALL)
        painter.setFont(small)

        # Grid and axis labels.
        painter.setPen(QPen(QColor(theme.PALETTE["border"]), 1.0))
        for k in range(Y_TICKS + 1):
            value = y_min + y_span * k / Y_TICKS
            y = frame.top() + (y_min + y_span - value) / y_span * frame.height()
            painter.drawLine(QPointF(frame.left(), y),
                             QPointF(frame.right(), y))
            painter.setPen(QPen(QColor(theme.PALETTE["muted"])))
            painter.drawText(QRectF(0, y - 8, PAD_L - 5, 16),
                             int(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter),
                             "{0:.0f}".format(value))
            painter.setPen(QPen(QColor(theme.PALETTE["border"]), 1.0))
        painter.setPen(QPen(QColor(theme.PALETTE["muted"])))
        for k in range(X_TICKS + 1):
            value = x_min + x_span * k / X_TICKS
            x = frame.left() + (value - x_min) / x_span * frame.width()
            painter.drawText(QRectF(x - 40, frame.bottom() + 2, 80, PAD_B - 2),
                             int(Qt.AlignmentFlag.AlignCenter),
                             "{0:,.0f} m".format(value))

        ground_pts = self._points(self.ground, x_min, x_span, y_min, y_span,
                                  frame)
        flight_pts = self._points(self.flight, x_min, x_span, y_min, y_span,
                                  frame)

        # The ground, filled to the bottom of the frame.
        filled = QPolygonF(ground_pts
                           + [QPointF(frame.right(), frame.bottom()),
                              QPointF(frame.left(), frame.bottom())])
        path = QPainterPath()
        path.addPolygon(filled)
        terrain = QColor(theme.PALETTE["panel_alt"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(terrain)
        painter.drawPath(path)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(theme.PALETTE["muted"]), 1.4))
        painter.drawPolyline(QPolygonF(ground_pts))
        painter.setPen(QPen(QColor(theme.PALETTE["accent"]), 1.8))
        painter.drawPolyline(QPolygonF(flight_pts))

        if self.cursor_s is not None:
            x = frame.left() + (min(max(self.cursor_s, x_min),
                                    x_min + x_span) - x_min) / x_span \
                * frame.width()
            painter.setPen(QPen(QColor(theme.PALETTE["warning"]), 1.4))
            painter.drawLine(QPointF(x, frame.top()),
                             QPointF(x, frame.bottom()))

        painter.setPen(QPen(QColor(theme.PALETTE["muted"])))
        painter.drawText(QRectF(frame.left() + 4, frame.top(),
                                frame.width() - 8, 16),
                         int(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter),
                         "terreno / quota di volo - AGL {0:.0f} m".format(
                             self.h_agl_m))
        painter.end()


__all__ = ["SpeciesMixChart", "ElevationProfile", "ROW_H", "TOLERANCE_PCT"]
