"""
Interactive square tool (Workflow B).

    click centro -> 10 Enter -> Enter = commit

or click the centre and then a point that the square's boundary must reach.

The square is a *constrained rectangle*: ``core.geometry_engine.square_from``
converts whichever size the operator knows -- side, diagonal, area or
perimeter -- into one side length and hands it to ``rectangle_from_center``.
That conversion is the engine's, not this file's, so a square drawn here and
one built from Processing agree to the last bit.

The four sizes are alternatives, not four values to fill in: the session is
created with the one the operator wants to type, exactly as the circle tool
takes its segment count. ``primitives._ring_for`` rejects anything but exactly
one of them, so a second size can never sneak into the record.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ...core import geometry_engine as ge
from ...core.planar import across_track_unit, along_track_unit
from .. import dynamic_input as di
from .. import primitives as pr
from .base import CadMapTool, CadToolSession, ConstraintSlot

#: The size the operator types. Exactly one reaches the engine.
SIZE_SIDE = "side_m"
SIZE_DIAGONAL = "diagonal_m"
SIZE_AREA = "area_m2"
SIZE_PERIMETER = "perimeter_m"

SIZE_LABELS = {
    SIZE_SIDE: "Lato",
    SIZE_DIAGONAL: "Diagonale",
    SIZE_AREA: "Area",
    SIZE_PERIMETER: "Perimetro",
}

#: side = size * this, for each way of expressing the size.
_TO_SIDE = {
    SIZE_SIDE: lambda v: float(v),
    SIZE_DIAGONAL: lambda v: float(v) / math.sqrt(2.0),
    SIZE_AREA: lambda v: math.sqrt(float(v)),
    SIZE_PERIMETER: lambda v: float(v) / 4.0,
}

#: ...and back, to turn a picked distance into the typed quantity.
_FROM_SIDE = {
    SIZE_SIDE: lambda s: s,
    SIZE_DIAGONAL: lambda s: s * math.sqrt(2.0),
    SIZE_AREA: lambda s: s * s,
    SIZE_PERIMETER: lambda s: 4.0 * s,
}


class SquareSession(CadToolSession):
    """A centre, one size expressed one of four ways, and a rotation."""

    tool_id = pr.TOOL_SQUARE
    geometry_type = "Polygon"
    title = "Quadrato"
    accepts_second_click = True

    def __init__(self, length_unit: str = "m", angle_unit: str = "deg",
                 size_mode: str = SIZE_SIDE):
        # Set before super().__init__, which calls slots().
        self.size_mode = size_mode if size_mode in SIZE_LABELS else SIZE_SIDE
        super().__init__(length_unit, angle_unit)

    def slots(self):
        return [
            ConstraintSlot(self.size_mode, di.KIND_LENGTH,
                           SIZE_LABELS[self.size_mode]),
            ConstraintSlot("azimuth_deg", di.KIND_ANGLE, "Rotazione"),
        ]

    # -- geometry ----------------------------------------------------------

    def side_m(self) -> Optional[float]:
        """The side length implied by whatever the operator typed."""
        value = self.value(self.size_mode)
        if value is None or float(value) <= 0.0:
            return None
        return _TO_SIDE[self.size_mode](value)

    def derive_from_second(self) -> None:
        """The second click is a point the square's boundary must reach.

        The click is resolved on the square's own axes, so the corner follows
        the rotation instead of a screen-aligned box.
        """
        azimuth = self.value("azimuth_deg")
        if azimuth is None:
            azimuth = 0.0
            self.set_value("azimuth_deg", 0.0)

        ux, uy = along_track_unit(azimuth)
        vx, vy = across_track_unit(azimuth)
        dx = self.second[0] - self.origin[0]
        dy = self.second[1] - self.origin[1]
        half = max(abs(dx * ux + dy * uy), abs(dx * vx + dy * vy))
        if half > 0.0:
            self.set_value(self.size_mode, _FROM_SIDE[self.size_mode](2.0 * half))

    # -- parameters --------------------------------------------------------

    def build_params(self) -> dict:
        return {"x": float(self.origin[0]), "y": float(self.origin[1]),
                self.size_mode: float(self.value(self.size_mode)),
                "azimuth_deg": float(self.value("azimuth_deg") or 0.0)}

    def preview_points(self) -> Optional[np.ndarray]:
        if self.origin is None:
            return None
        side = self.side_m()
        if side is None and self.cursor is not None:
            azimuth = self.value("azimuth_deg") or 0.0
            ux, uy = along_track_unit(azimuth)
            vx, vy = across_track_unit(azimuth)
            dx = self.cursor[0] - self.origin[0]
            dy = self.cursor[1] - self.origin[1]
            side = 2.0 * max(abs(dx * ux + dy * uy), abs(dx * vx + dy * vy))
        if not side or side <= 0.0:
            return None
        return ge.square_from(self.origin, self.value("azimuth_deg") or 0.0,
                              side_m=side)

    # -- readout -----------------------------------------------------------

    def hud_lines(self):
        """Side, diagonal, perimeter and area together: one number, four ways."""
        lines = super().hud_lines()
        side = self.side_m()
        if side is None and self.origin is not None and self.cursor is not None:
            azimuth = self.value("azimuth_deg") or 0.0
            ux, uy = along_track_unit(azimuth)
            vx, vy = across_track_unit(azimuth)
            dx = self.cursor[0] - self.origin[0]
            dy = self.cursor[1] - self.origin[1]
            side = 2.0 * max(abs(dx * ux + dy * uy), abs(dx * vx + dy * vy))
        if side:
            lines.append("L {0:.3f} m".format(side))
            lines.append("D {0:.3f} m".format(side * math.sqrt(2.0)))
            lines.append("P {0:.3f} m".format(4.0 * side))
            lines.append("Area {0:.3f} m2".format(side * side))
        return lines


def create(canvas, iface=None, layer_provider=None,
           size_mode: str = SIZE_SIDE, length_unit: str = "m",
           angle_unit: str = "deg") -> CadMapTool:
    """Build the map tool. The dock calls this."""
    session = SquareSession(length_unit, angle_unit, size_mode)
    return CadMapTool(canvas, session, iface=iface,
                      layer_provider=layer_provider)
