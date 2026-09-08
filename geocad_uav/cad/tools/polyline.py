"""
Interactive polyline tool: an unbounded chain of vertices.

    click, click, click ... Enter or double click
    click, @100<0 Enter, @100<90 Enter, @100<90 Enter, @100<90 Enter

The one behavioural difference from :mod:`.line` is the polar token. Here
``@25<37`` is measured **from the previous segment**, because
``CadToolSession.last_azimuth_deg()`` now returns a bearing; on a single-
segment tool the chain is empty, that method returns None, and the same token
still means "37 degrees from grid north". Both paths go through the already
tested ``cad.dynamic_input`` resolver -- nothing is re-implemented here.

Parameters are stored in the ``segments`` form that
``primitives.build(TOOL_POLYLINE, ...)`` already accepts::

    {"x": start_x, "y": start_y, "segments": [[length, azimuth], ...]}

That form is chosen over raw ``points`` on purpose: it keeps the record
*parametric*, so a traverse typed as a set of bearings rebuilds from those
bearings and closes to float64 rather than to whatever the clicks rounded to.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ...core.constants import GEOM_EPS_M
from ...core.errors import InvalidInputError
from ...core.planar import along_track_unit, azimuth_of
from .. import dynamic_input as di
from .. import primitives as pr
from .base import CadMapTool, CadToolSession, ConstraintSlot, ToolState


class PolylineSession(CadToolSession):
    """A chain of vertices, closed by the operator rather than by slot count."""

    tool_id = pr.TOOL_POLYLINE
    geometry_type = "LineString"
    title = "Polilinea"
    accepts_second_click = True
    multi_vertex = True

    def __init__(self, length_unit: str = "m", angle_unit: str = "deg",
                 close: bool = False):
        #: Close the ring. See :meth:`effective_vertices` for what this does
        #: and, importantly, what it does not do.
        self.close = bool(close)
        super().__init__(length_unit, angle_unit)

    def slots(self):
        """Optional numeric entry for the *next* segment.

        These are not a completion condition -- ``is_ready`` counts vertices --
        they are the pair the dock binds so a segment can be added by typing
        into the panel instead of onto the canvas.
        """
        return [
            ConstraintSlot("length_m", di.KIND_LENGTH, "Lunghezza segmento"),
            ConstraintSlot("azimuth_deg", di.KIND_ANGLE, "Azimut segmento"),
        ]

    # -- completion --------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """Two vertices are a valid one-segment polyline."""
        return len(self.vertices) >= 2

    def confirm(self) -> str:
        if not self.is_ready:
            raise InvalidInputError(
                "polyline needs at least two vertices, has {0}".format(
                    len(self.vertices)),
                user_message="Servono almeno due vertici per chiudere la "
                             "polilinea.")
        self.state = ToolState.COMMIT
        return self.state

    def reset(self):
        super().reset()
        # `close` is an operator setting, not construction state: it survives.

    # -- input -------------------------------------------------------------

    def submit(self, text: str) -> str:
        """Route scalars to segment construction, points to the base router.

        A bare length must *extend the chain*, not sit in a slot waiting for a
        partner, so the two scalar kinds are handled here. Point-valued tokens
        (``#x,y``, ``@dx,dy``, ``@25<37``) fall through to the base, which
        already anchors them on the last vertex and measures the polar angle
        from the last segment.
        """
        token = di.parse(text, self.length_unit, self.angle_unit)

        if token.kind == di.KIND_ANGLE:
            if self.origin is None:
                raise InvalidInputError(
                    "angle before an origin",
                    user_message="Indica prima il primo vertice.")
            self.set_value("azimuth_deg", token.angle_deg)
            self.message = "Ora la lunghezza del segmento"
            return self.state

        if token.kind == di.KIND_LENGTH:
            if self.origin is None:
                raise InvalidInputError(
                    "length before an origin",
                    user_message="Indica prima il primo vertice.")
            return self._extend_by(token.length_m)

        return super().submit(text)

    def _extend_by(self, length_m: float) -> str:
        """Add one vertex at ``length_m`` along the current direction.

        Direction, in order: an azimuth typed for this segment, else the
        bearing towards the cursor, else the bearing of the previous segment,
        else grid north.
        """
        azimuth = self.value("azimuth_deg")
        if azimuth is None:
            azimuth = self._cursor_azimuth()
        if azimuth is None:
            azimuth = self.last_azimuth_deg()
        if azimuth is None:
            azimuth = 0.0
        anchor = self.anchor_point()
        ux, uy = along_track_unit(azimuth)
        state = self.set_second(anchor[0] + length_m * ux,
                                anchor[1] + length_m * uy)
        # Both slots are consumed by the segment they just created.
        self.set_value("azimuth_deg", None)
        self.set_value("length_m", None)
        self.active_slot = 0
        return state

    def _cursor_azimuth(self) -> Optional[float]:
        anchor = self.anchor_point()
        if anchor is None or self.cursor is None:
            return None
        dx = self.cursor[0] - anchor[0]
        dy = self.cursor[1] - anchor[1]
        if math.hypot(dx, dy) < GEOM_EPS_M:
            return None
        return azimuth_of(dx, dy)

    # -- geometry ----------------------------------------------------------

    def effective_vertices(self) -> "list":
        """The vertices actually written, with the closing one if requested.

        ``close`` appends the first vertex again, producing a *closed
        LineString*. It does **not** produce a Polygon: ``TOOL_POLYLINE``
        always returns ``closed=False`` from the engine, and building a
        polygon would need a primitive that does not exist yet. Emitting one
        anyway would break the parametric round-trip, since ``rebuild()`` would
        hand back a LineString. The two are different geometries and the tool
        says so rather than converting in silence.
        """
        points = list(self.vertices)
        if self.close and len(points) >= 3:
            first, last = points[0], points[-1]
            if math.hypot(last[0] - first[0], last[1] - first[1]) > GEOM_EPS_M:
                points.append(first)
        return points

    def segments(self) -> "list":
        """[[length, azimuth], ...] between consecutive vertices.

        Zero-length steps are dropped: ``geometry_engine`` rejects a segment of
        length 0, and a double click can otherwise leave a duplicate vertex.
        """
        points = self.effective_vertices()
        out = []
        for start, end in zip(points, points[1:]):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            if length <= GEOM_EPS_M:
                continue
            out.append([length, azimuth_of(dx, dy)])
        return out

    def build_params(self) -> dict:
        points = self.effective_vertices()
        if len(points) < 2:
            raise InvalidInputError(
                "polyline needs at least two vertices",
                user_message="Servono almeno due vertici.")
        return {"x": float(points[0][0]), "y": float(points[0][1]),
                "segments": self.segments()}

    def preview_points(self) -> Optional[np.ndarray]:
        if not self.vertices:
            return None
        points = list(self.vertices)
        if self.cursor is not None and self.state != ToolState.COMMIT:
            points.append(self.cursor)          # rubber band follows the mouse
        if self.close and len(points) >= 3:
            points.append(points[0])
        if len(points) < 2:
            return None
        return np.asarray(points, dtype=float)

    # -- readout -----------------------------------------------------------

    def total_length(self) -> float:
        return float(sum(seg[0] for seg in self.segments()))

    def hud_lines(self):
        lines = super().hud_lines()
        lines.append("Vertici {0}".format(len(self.vertices)))
        chain = self.segments()
        if chain:
            lines.append("Ultimo segmento {0:.3f} m @ {1:.4f} deg".format(
                chain[-1][0], chain[-1][1]))
            lines.append("Totale {0:.3f} m".format(self.total_length()))
        base = self.last_azimuth_deg()
        if base is not None:
            lines.append("Polare relativa a {0:.4f} deg".format(base))
        if self.close:
            lines.append("Chiusura attiva (anello LineString)")
        return lines


def create(canvas, iface=None, layer_provider=None, close: bool = False,
           length_unit: str = "m", angle_unit: str = "deg") -> CadMapTool:
    session = PolylineSession(length_unit, angle_unit, close)
    return CadMapTool(canvas, session, iface=iface,
                      layer_provider=layer_provider)
