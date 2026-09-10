"""
Click-to-polygon: a polygon whose parameters are the vertices themselves.

    click, click, click ... click the first vertex again  ->  commit
    click, click, click ... Enter or double click         ->  commit

The chain, the snapping and the rubber band all come from
:class:`~.base.CadToolSession` and :class:`~.base.CadMapTool`; this module adds
only the three things a free-form polygon needs and a polyline does not:

* it closes on the **first vertex**, within a tolerance measured in screen
  pixels, so the closing click is as easy at 1:200 as at 1:20 000;
* the **right button undoes** one vertex instead of discarding the work
  (Ctrl+Z does the same, Ctrl+Y puts it back);
* the record it writes is a *closed* ring, so ``rebuild()`` hands back a
  polygon rather than the closed LineString ``TOOL_POLYLINE`` is defined to
  return.

The ring is built by ``primitives.ring_for(TOOL_DIGITIZED_POLYGON, ...)``, the
same dispatch ``build()`` uses, so the preview and the committed geometry can
never drift apart.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ...core import geometry_engine as ge
from ...core.constants import GEOM_EPS_M
from ...core.errors import InvalidInputError
from .. import primitives as pr
from .base import CadMapTool, CadToolSession, ToolState

#: Closing radius around the first vertex, in screen pixels. Pixels, not
#: metres: the operator aims with the mouse, and the mouse works in pixels.
CLOSE_TOLERANCE_PX = 12.0


class DigitizeSession(CadToolSession):
    """An open-ended ring of clicked vertices."""

    tool_id = pr.TOOL_DIGITIZED_POLYGON
    geometry_type = "Polygon"
    title = "Poligono digitalizzato"
    accepts_second_click = True
    multi_vertex = True

    def slots(self):
        """None: this tool has no typed constraint.

        The vertices are the parameters. Point-valued tokens (``#x,y``,
        ``@dx,dy``, ``@25<37``) still work -- the base router turns them into
        vertices -- but there is no length or angle waiting to be filled, so
        the dock binds no spin box to it.
        """
        return []

    # -- completion --------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """Three corners. Two would be a line drawn twice."""
        return len(self.ring()) >= 3

    def confirm(self) -> str:
        ring = self.ring()
        if len(ring) < 3:
            raise InvalidInputError(
                "digitized polygon needs three vertices, has {0}".format(
                    len(ring)),
                user_message="Servono almeno tre vertici per chiudere il "
                             "poligono.")
        self.state = ToolState.COMMIT
        return self.state

    # -- the ring ----------------------------------------------------------

    def ring(self) -> list:
        """The clicked vertices, open and without repeats.

        A closing click lands on the first vertex, and a double click can
        leave the same point twice; both would make a zero-length side, which
        the engine rejects. They are dropped here rather than in the builder,
        so what the HUD measures is what gets committed.
        """
        out = []
        for vertex in self.vertices:
            if out and math.hypot(vertex[0] - out[-1][0],
                                  vertex[1] - out[-1][1]) <= GEOM_EPS_M:
                continue
            out.append((float(vertex[0]), float(vertex[1])))
        if len(out) > 1 and math.hypot(out[-1][0] - out[0][0],
                                       out[-1][1] - out[0][1]) <= GEOM_EPS_M:
            out.pop()
        return out

    def closes_on_first(self, x: float, y: float, tolerance_m: float) -> bool:
        """True when a click at (x, y) means "close the ring here"."""
        ring = self.ring()
        if len(ring) < 3 or tolerance_m <= 0.0:
            return False
        return math.hypot(x - ring[0][0], y - ring[0][1]) <= tolerance_m

    def build_params(self) -> dict:
        ring = self.ring()
        if len(ring) < 3:
            raise InvalidInputError(
                "digitized polygon needs three vertices",
                user_message="Servono almeno tre vertici.")
        return {"points": [[x, y] for x, y in ring]}

    def preview_points(self) -> Optional[np.ndarray]:
        """The ring so far, closed, with the cursor as the moving vertex."""
        points = list(self.ring())
        if self.cursor is not None and self.state != ToolState.COMMIT:
            points.append((float(self.cursor[0]), float(self.cursor[1])))
        if len(points) < 2:
            return None
        if len(points) >= 3:
            points.append(points[0])
        return np.asarray(points, dtype=float)

    # -- readout -----------------------------------------------------------

    def local_ring(self) -> np.ndarray:
        """The ring moved to its own first vertex.

        The shoelace formula multiplies coordinates together, and at UTM
        magnitudes (x around 5e5, y around 5e6) those products reach 2.5e13,
        which leaves a double about a millimetre of resolution *per term*.
        Measured on a 10 m circle that is an error of 1e-2 m2 -- visible in
        the readout. Translating the ring to its first vertex first costs one
        subtraction and gives the area back to full precision; the shape is
        unchanged, an area being invariant under translation.
        """
        arr = np.asarray(self.ring(), dtype=float)
        return arr - arr[0] if len(arr) else arr

    def hud_lines(self):
        """Vertices, perimeter and area, measured on the ring as it stands.

        ``ge.polygon_area`` is the same shoelace the engine uses everywhere
        else; nothing here builds a QgsGeometry, so it is safe on a hover.
        """
        lines = super().hud_lines()
        ring = self.ring()
        lines.append("Vertici {0}".format(len(ring)))
        if len(ring) >= 3:
            # Both helpers close the ring themselves and the area comes back
            # positive; there is nothing to normalise here.
            local = self.local_ring()
            area = ge.polygon_area(local)
            lines.append("Perimetro {0:.3f} m".format(
                ge.polygon_perimeter(local)))
            lines.append("Area {0:.3f} m2".format(area))
            lines.append("Area {0:.4f} ha".format(area / 10_000.0))
            lines.append("Chiudi sul primo vertice o Invio")
        elif ring:
            lines.append("Continua a cliccare i vertici")
        return lines


class DigitizeMapTool(CadMapTool):
    """The session on the canvas, with a closing click and an undo stack."""

    def __init__(self, canvas, session, iface=None, layer_provider=None,
                 close_pixels: float = CLOSE_TOLERANCE_PX):
        super().__init__(canvas, session, iface=iface,
                         layer_provider=layer_provider)
        #: Closing tolerance, in pixels. A parameter, not a cabled number.
        self.close_pixels = float(close_pixels)
        #: Vertices taken back by an undo, newest last.
        self._undone = []

    # -- lifecycle ---------------------------------------------------------

    def activate(self):
        super().activate()
        self._undone = []

    def deactivate(self):
        self._undone = []
        super().deactivate()

    # -- the closing tolerance --------------------------------------------

    def close_tolerance(self) -> float:
        """Pixels turned into work-CRS metres at the current scale.

        ``mapUnitsPerPixel`` is in *canvas* units, so when the work CRS and the
        canvas CRS differ the length is carried across by transforming a
        segment of that size rather than by assuming the two units match.
        """
        canvas = self.canvas()
        if canvas is None:
            return 0.0
        try:
            per_pixel = float(canvas.mapUnitsPerPixel())
        except (AttributeError, TypeError):
            return 0.0
        span = per_pixel * self.close_pixels
        if span <= 0.0:
            return 0.0
        if self._to_canvas is None:
            return span
        # Work CRS -> canvas is what we have; measure the canvas span back in
        # work units by walking one metre and seeing how far it went.
        ring = self.session.ring()
        if not ring:
            return span
        x0, y0 = ring[0]
        cx0, cy0 = self._to_canvas(x0, y0)
        cx1, cy1 = self._to_canvas(x0 + 1.0, y0)
        canvas_per_metre = math.hypot(cx1 - cx0, cy1 - cy0)
        if canvas_per_metre <= 0.0:
            return span
        return span / canvas_per_metre

    # -- mouse -------------------------------------------------------------

    def canvasReleaseEvent(self, event):                        # noqa: N802
        """Left click adds or closes; right click takes one vertex back."""
        from qgis.PyQt.QtCore import Qt                         # noqa: PLC0415

        if event.button() == Qt.MouseButton.RightButton:
            self.undo_vertex()
            return
        if event.button() == Qt.MouseButton.LeftButton and \
                self._work_decision is not None:
            x, y = self._map_to_work(self.picked_point(event))
            if self.session.closes_on_first(x, y, self.close_tolerance()):
                self._do_commit()
                self._undone = []
                return
        super().canvasReleaseEvent(event)
        self._undone = []

    # -- keyboard ----------------------------------------------------------

    def keyPressEvent(self, event):                             # noqa: N802
        """Ctrl+Z and Ctrl+Y, then everything the base already handles."""
        from qgis.PyQt.QtCore import Qt                         # noqa: PLC0415

        try:
            modifiers = event.modifiers()
            control = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        except (AttributeError, TypeError):
            control = False
        if control and event.key() == Qt.Key.Key_Z:
            self.undo_vertex()
            return
        if control and event.key() == Qt.Key.Key_Y:
            self.redo_vertex()
            return
        super().keyPressEvent(event)

    # -- undo / redo -------------------------------------------------------

    def undo_vertex(self) -> bool:
        """Drop the last vertex, keeping it for a redo.

        With nothing left to undo this is Escape, which is what a right click
        means everywhere else in the plugin.
        """
        vertices = self.session.vertices
        if not vertices:
            self._escape()
            return False
        self._undone.append(tuple(vertices[-1]))
        self.session.remove_last_vertex()
        self.update_band(self.canvas(), self._to_canvas)
        self._paint_hud()
        return True

    def redo_vertex(self) -> bool:
        """Put back the vertex the last undo took."""
        if not self._undone:
            return False
        x, y = self._undone.pop()
        if self.session.origin is None:
            self.session.set_origin(x, y)
        else:
            self.session.set_second(x, y)
        self.update_band(self.canvas(), self._to_canvas)
        self._paint_hud()
        return True


def create(canvas, iface=None, layer_provider=None,
           close_pixels: float = CLOSE_TOLERANCE_PX, length_unit: str = "m",
           angle_unit: str = "deg") -> CadMapTool:
    session = DigitizeSession(length_unit, angle_unit)
    return DigitizeMapTool(canvas, session, iface=iface,
                           layer_provider=layer_provider,
                           close_pixels=close_pixels)
