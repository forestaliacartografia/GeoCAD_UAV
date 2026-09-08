"""
Interactive circle tool (Workflow B).

    click centro -> 10 Enter -> Enter = commit

or click the centre and then a point on the circumference.

The ring comes from ``core.geometry_engine.circle_ring`` at the project's
segment count, so a circle drawn here matches one built from Processing.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ...core import geometry_engine as ge
from ...core.constants import CIRCLE_SEGMENTS
from .. import dynamic_input as di
from .. import primitives as pr
from .base import CadMapTool, CadToolSession, ConstraintSlot


class CircleSession(CadToolSession):
    """Centre plus radius. The radius may be typed or picked."""

    tool_id = pr.TOOL_CIRCLE
    geometry_type = "Polygon"
    title = "Cerchio"
    accepts_second_click = True

    def __init__(self, length_unit: str = "m", angle_unit: str = "deg",
                 segments: int = CIRCLE_SEGMENTS):
        self.segments = int(segments)
        super().__init__(length_unit, angle_unit)

    def slots(self):
        return [ConstraintSlot("radius_m", di.KIND_LENGTH, "Raggio")]

    # -- pick a point on the circumference ---------------------------------

    def derive_from_second(self) -> None:
        radius = math.hypot(self.second[0] - self.origin[0],
                            self.second[1] - self.origin[1])
        if radius > 0.0:
            self.set_value("radius_m", radius)

    # -- parameters --------------------------------------------------------

    def build_params(self) -> dict:
        return {"mode": "center_radius",
                "x": float(self.origin[0]), "y": float(self.origin[1]),
                "radius_m": float(self.value("radius_m")),
                "segments": self.segments}

    def preview_points(self) -> Optional[np.ndarray]:
        if self.origin is None:
            return None
        radius = self.value("radius_m")
        if radius is None and self.cursor is not None:
            radius = math.hypot(self.cursor[0] - self.origin[0],
                                self.cursor[1] - self.origin[1])
        if not radius or radius <= 0.0:
            return None
        return ge.circle_ring(self.origin, radius, self.segments)

    # -- readout -----------------------------------------------------------

    def hud_lines(self):
        """Radius, diameter, circumference and area, kept in step.

        The four are one number shown four ways; the panel shows them together
        because an operator usually knows one of them, not the radius.
        """
        lines = super().hud_lines()
        radius = self.value("radius_m")
        if radius is None and self.origin is not None and self.cursor is not None:
            radius = math.hypot(self.cursor[0] - self.origin[0],
                                self.cursor[1] - self.origin[1])
        if radius:
            lines.append("R {0:.3f} m".format(radius))
            lines.append("D {0:.3f} m".format(2.0 * radius))
            lines.append("C {0:.3f} m".format(2.0 * math.pi * radius))
            lines.append("Area {0:.3f} m2".format(math.pi * radius * radius))
        return lines


def create(canvas, iface=None, layer_provider=None,
           segments: int = CIRCLE_SEGMENTS, length_unit: str = "m",
           angle_unit: str = "deg") -> CadMapTool:
    session = CircleSession(length_unit, angle_unit, segments)
    return CadMapTool(canvas, session, iface=iface,
                      layer_provider=layer_provider)
