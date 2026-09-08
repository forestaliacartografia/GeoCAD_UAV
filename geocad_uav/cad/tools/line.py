"""
Interactive line tool.

    click start -> click end
    click start -> @25<37 Enter          (polar: 25 m at 37 deg)
    click start -> 25 Enter -> 37d Enter

The polar form goes through the already-tested ``cad.dynamic_input`` parser and
resolver; this module only turns the resolved point into the length/azimuth
pair that ``core.geometry_engine.line_from_length_azimuth`` wants.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ...core import geometry_engine as ge
from ...core.constants import GEOM_EPS_M
from ...core.planar import azimuth_of
from .. import dynamic_input as di
from .. import primitives as pr
from .base import CadMapTool, CadToolSession, ConstraintSlot


class LineSession(CadToolSession):
    """Start point plus length and bearing."""

    tool_id = pr.TOOL_LINE
    geometry_type = "LineString"
    title = "Linea"
    accepts_second_click = True

    def slots(self):
        return [
            ConstraintSlot("length_m", di.KIND_LENGTH, "Lunghezza"),
            ConstraintSlot("azimuth_deg", di.KIND_ANGLE, "Azimut"),
        ]

    # -- second click, or a resolved polar / relative token ----------------

    def derive_from_second(self) -> None:
        dx = self.second[0] - self.origin[0]
        dy = self.second[1] - self.origin[1]
        length = math.hypot(dx, dy)
        if length <= GEOM_EPS_M:
            return                     # a click on the start point sets nothing
        self.set_value("length_m", length)
        self.set_value("azimuth_deg", azimuth_of(dx, dy))

    # -- parameters --------------------------------------------------------

    def build_params(self) -> dict:
        return {"x": float(self.origin[0]), "y": float(self.origin[1]),
                "length_m": float(self.value("length_m")),
                "azimuth_deg": float(self.value("azimuth_deg"))}

    def preview_points(self) -> Optional[np.ndarray]:
        if self.origin is None:
            return None
        if self.all_filled:
            return ge.line_from_length_azimuth(
                self.origin, self.value("length_m"), self.value("azimuth_deg"))
        if self.cursor is None:
            return None
        return np.array([list(self.origin), list(self.cursor)], dtype=float)

    # -- readout -----------------------------------------------------------

    def hud_lines(self):
        lines = super().hud_lines()
        length = self.value("length_m")
        azimuth = self.value("azimuth_deg")
        if length is not None and azimuth is not None:
            lines.append("Segmento {0:.3f} m @ {1:.4f} deg".format(length,
                                                                    azimuth))
        return lines


def create(canvas, iface=None, layer_provider=None, length_unit: str = "m",
           angle_unit: str = "deg") -> CadMapTool:
    session = LineSession(length_unit, angle_unit)
    return CadMapTool(canvas, session, iface=iface,
                      layer_provider=layer_provider)
