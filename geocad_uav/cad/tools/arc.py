"""
Interactive circular arc (Workflow B).

    click centro -> 10 Enter -> 0d Enter -> 90d Enter -> Enter = commit

An arc is a *line*, not a ring: the session's ``geometry_type`` is
``LineString`` and ``primitives._ring_for`` returns it unclosed, so it lands on
a line layer and gets ``area_ha`` 0.00 with its length in ``perimeter_m`` --
the same treatment the line tool already gets.

Three constructions, exactly one per commit, chosen when the session is
created, in the same way the square picks which size it is typed from:

``centre``
    centre, radius, start azimuth, end azimuth;
``three_points``
    start, a point the arc passes through, end -- the middle point decides
    which of the two arcs is meant;
``endpoints_radius``
    the two ends and a radius. Two circles fit, each with a minor and a major
    arc; the engine returns the minor one and the HUD says so, rather than
    silently choosing one of four.

Sweeps run clockwise, in the compass sense that the rest of the plugin uses
(``planar.along_track_unit``: azimuth 0 is North, 90 is East). The maths lives
in ``core.geometry_engine.arc_*``; nothing here computes a radius.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ...core import geometry_engine as ge
from ...core.errors import ConstraintError
from .. import dynamic_input as di
from .. import primitives as pr
from .base import CadMapTool, CadToolSession, ConstraintSlot

#: How the arc is defined. One per session, like the square's size mode.
MODE_CENTRE = "centre"
MODE_THREE_POINTS = "three_points"
MODE_ENDPOINTS_RADIUS = "endpoints_radius"

MODE_LABELS = {
    MODE_CENTRE: "Centro, raggio e azimut",
    MODE_THREE_POINTS: "Tre punti sulla circonferenza",
    MODE_ENDPOINTS_RADIUS: "Due estremi e raggio",
}


class ArcSession(CadToolSession):
    """A centre, a radius and two azimuths -- however they were obtained."""

    tool_id = pr.TOOL_ARC
    geometry_type = "LineString"
    title = "Arco"
    accepts_second_click = True

    def __init__(self, length_unit: str = "m", angle_unit: str = "deg",
                 mode: str = MODE_CENTRE):
        self.mode = mode if mode in MODE_LABELS else MODE_CENTRE
        #: Points picked on the circumference, for the two geometric modes.
        self.picked = []
        #: Filled once a geometric mode has solved the circle.
        self.solved = None
        #: Set when the engine had to choose between two arcs.
        self.note = ""
        super().__init__(length_unit, angle_unit)

    def slots(self):
        if self.mode == MODE_CENTRE:
            return [
                ConstraintSlot("radius_m", di.KIND_LENGTH, "Raggio"),
                ConstraintSlot("start_az", di.KIND_ANGLE, "Azimut iniziale"),
                ConstraintSlot("end_az", di.KIND_ANGLE, "Azimut finale"),
            ]
        if self.mode == MODE_ENDPOINTS_RADIUS:
            return [ConstraintSlot("radius_m", di.KIND_LENGTH, "Raggio")]
        return []                          # three points need no typed value

    # -- lifecycle ---------------------------------------------------------

    def reset(self):
        super().reset()
        self.picked = []
        self.solved = None
        self.note = ""

    def set_origin(self, x: float, y: float) -> str:
        """The first click: the centre, or the first point on the arc."""
        state = super().set_origin(x, y)
        if self.mode != MODE_CENTRE:
            self.picked = [(float(x), float(y))]
        return state

    def add_point(self, x: float, y: float) -> str:
        """Another point on the circumference, for the geometric modes."""
        if self.mode == MODE_CENTRE:
            return self.state
        self.picked.append((float(x), float(y)))
        self._solve()
        return self.state

    def derive_from_second(self) -> None:
        """A second click means different things per mode."""
        if self.mode == MODE_CENTRE:
            dx = self.second[0] - self.origin[0]
            dy = self.second[1] - self.origin[1]
            radius = math.hypot(dx, dy)
            if radius > 0.0:
                self.set_value("radius_m", radius)
                if self.value("start_az") is None:
                    self.set_value(
                        "start_az",
                        math.degrees(math.atan2(dx, dy)) % 360.0)
            return
        self.add_point(self.second[0], self.second[1])

    def _solve(self):
        """Ask the engine for the circle once enough points are in."""
        self.note = ""
        try:
            if self.mode == MODE_THREE_POINTS and len(self.picked) >= 3:
                self.solved = ge.arc_from_3_points(*self.picked[:3])
            elif (self.mode == MODE_ENDPOINTS_RADIUS
                  and len(self.picked) >= 2
                  and self.value("radius_m")):
                self.solved = ge.arc_from_endpoints_radius(
                    self.picked[0], self.picked[1],
                    float(self.value("radius_m")))
                self.note = ("Scelto l'arco minore fra i due possibili "
                             "(ampiezza fino a 180 gradi).")
            else:
                return
        except ConstraintError:
            self.solved = None
            raise

    # -- geometry ----------------------------------------------------------

    def resolved(self):
        """``(centre, radius, start_az, end_az)`` or None."""
        if self.mode == MODE_CENTRE:
            radius = self.value("radius_m")
            start = self.value("start_az")
            end = self.value("end_az")
            if self.origin is None or not radius or start is None \
                    or end is None:
                return None
            return (self.origin, float(radius), float(start), float(end))
        return self.solved

    @property
    def sweep_deg(self) -> Optional[float]:
        solution = self.resolved()
        if solution is None:
            return None
        return ge.arc_sweep(solution[2], solution[3])

    @property
    def is_ready(self) -> bool:
        return self.resolved() is not None

    # -- parameters --------------------------------------------------------

    def build_params(self) -> dict:
        centre, radius, start, end = self.resolved()
        return {"mode": self.mode,
                "x": float(centre[0]), "y": float(centre[1]),
                "radius_m": float(radius),
                "start_az": float(start), "end_az": float(end),
                "sweep_deg": float(ge.arc_sweep(start, end))}

    def preview_points(self) -> Optional[np.ndarray]:
        solution = self.resolved()
        if solution is None:
            return None
        centre, radius, start, end = solution
        try:
            return ge.arc_ring(centre, radius, start, end)
        except (ConstraintError, ValueError):
            return None

    # -- readout -----------------------------------------------------------

    def hud_lines(self):
        """Radius, sweep, true length and chord, so a typo shows before Enter."""
        lines = super().hud_lines()
        lines.append(MODE_LABELS[self.mode])
        solution = self.resolved()
        if solution is None:
            return lines
        _centre, radius, start, end = solution
        sweep = ge.arc_sweep(start, end)
        lines.append("R {0:.3f} m".format(radius))
        lines.append("da {0:.1f} a {1:.1f} deg".format(start, end))
        lines.append("ampiezza {0:.1f} deg".format(sweep))
        lines.append("L {0:.3f} m".format(ge.arc_length(radius, sweep)))
        lines.append("corda {0:.3f} m".format(
            2.0 * radius * math.sin(math.radians(sweep) / 2.0)))
        if self.note:
            lines.append(self.note)
        return lines


class ArcTool(CadMapTool):
    """Map tool for the arc. Extra clicks feed the geometric modes."""

    def canvasReleaseEvent(self, event):                        # noqa: N802
        from qgis.PyQt.QtCore import Qt                         # noqa: PLC0415

        session = self.session
        if (event.button() == Qt.MouseButton.LeftButton
                and session.mode != MODE_CENTRE
                and session.origin is not None
                and len(session.picked) >= 2):
            # Third and later clicks: the base class only knows about two.
            if self._work_decision is None:
                self._warn(self._blocked_reason or
                           "Sistema di riferimento non utilizzabile.")
                return
            x, y = self._map_to_work(self.picked_point(event))
            try:
                session.add_point(x, y)
            except ConstraintError as exc:
                self._warn(exc.formatted())
                return
            self.update_band(self.canvas(), self._to_canvas)
            self._paint_hud()
            return
        super().canvasReleaseEvent(event)


def create(canvas, iface=None, layer_provider=None, mode: str = MODE_CENTRE,
           length_unit: str = "m", angle_unit: str = "deg") -> ArcTool:
    """Build the map tool. The dock calls this."""
    session = ArcSession(length_unit, angle_unit, mode)
    return ArcTool(canvas, session, iface=iface,
                   layer_provider=layer_provider)
