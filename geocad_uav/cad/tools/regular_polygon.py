"""
Interactive regular polygon tool (Workflow B).

    click centro -> 10 Enter -> Enter = commit

or click the centre and then a vertex, which fixes both the circumradius and
the bearing of the first vertex.

``core.geometry_engine.regular_polygon`` owns every conversion --
``R = apothem / cos(pi/n)``, ``R = side / (2 sin(pi/n))``,
``R = sqrt(2A / (n sin(2 pi/n)))`` -- so the four ways of stating the size all
land on the same ring, and a polygon drawn here matches one built from
Processing.

The side count is a construction parameter rather than a typed slot, like the
circle's segment count: the dock renders slots as lengths or angles, and a
count is neither. Changing it interactively needs a third slot kind, which is
not part of this milestone.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ...core import geometry_engine as ge
from .. import dynamic_input as di
from .. import primitives as pr
from .base import CadMapTool, CadToolSession, ConstraintSlot

#: The size the operator types. Exactly one reaches the engine.
SIZE_RADIUS = "radius_m"
SIZE_APOTHEM = "apothem_m"
SIZE_SIDE = "side_m"
SIZE_AREA = "area_m2"

SIZE_LABELS = {
    SIZE_RADIUS: "Raggio (circoscritto)",
    SIZE_APOTHEM: "Apotema",
    SIZE_SIDE: "Lato",
    SIZE_AREA: "Area",
}

#: Default number of sides. Six is the one an operator draws by hand.
DEFAULT_SIDES = 6


def radius_from(size_mode: str, value: float, n_sides: int) -> float:
    """The circumradius implied by one of the four sizes.

    The conversions are the engine's, repeated here only to preview and to
    show the HUD; the committed geometry always goes through
    ``regular_polygon`` itself.
    """
    n = int(n_sides)
    value = float(value)
    if size_mode == SIZE_RADIUS:
        return value
    if size_mode == SIZE_APOTHEM:
        return value / math.cos(math.pi / n)
    if size_mode == SIZE_SIDE:
        return value / (2.0 * math.sin(math.pi / n))
    return math.sqrt(2.0 * value / (n * math.sin(2.0 * math.pi / n)))


def size_from_radius(size_mode: str, radius: float, n_sides: int) -> float:
    """The inverse, for turning a picked vertex into the typed quantity."""
    n = int(n_sides)
    radius = float(radius)
    if size_mode == SIZE_RADIUS:
        return radius
    if size_mode == SIZE_APOTHEM:
        return radius * math.cos(math.pi / n)
    if size_mode == SIZE_SIDE:
        return 2.0 * radius * math.sin(math.pi / n)
    return 0.5 * n * radius * radius * math.sin(2.0 * math.pi / n)


class RegularPolygonSession(CadToolSession):
    """A centre, a side count, one size and the bearing of the first vertex."""

    tool_id = pr.TOOL_POLYGON
    geometry_type = "Polygon"
    title = "Poligono regolare"
    accepts_second_click = True

    def __init__(self, length_unit: str = "m", angle_unit: str = "deg",
                 n_sides: int = DEFAULT_SIDES, size_mode: str = SIZE_RADIUS):
        # Both are read by slots(), which super().__init__ calls.
        self.n_sides = int(n_sides)
        self.size_mode = size_mode if size_mode in SIZE_LABELS else SIZE_RADIUS
        super().__init__(length_unit, angle_unit)

    def slots(self):
        return [
            ConstraintSlot(self.size_mode, di.KIND_LENGTH,
                           SIZE_LABELS[self.size_mode]),
            ConstraintSlot("azimuth_deg", di.KIND_ANGLE, "Rotazione"),
        ]

    # -- geometry ----------------------------------------------------------

    def radius_m(self) -> Optional[float]:
        value = self.value(self.size_mode)
        if value is None or float(value) <= 0.0:
            return None
        return radius_from(self.size_mode, value, self.n_sides)

    def derive_from_second(self) -> None:
        """The second click is the first vertex: circumradius and bearing."""
        dx = self.second[0] - self.origin[0]
        dy = self.second[1] - self.origin[1]
        radius = math.hypot(dx, dy)
        if radius <= 0.0:
            return
        self.set_value(self.size_mode,
                       size_from_radius(self.size_mode, radius, self.n_sides))
        self.set_value("azimuth_deg", math.degrees(math.atan2(dx, dy)) % 360.0)

    # -- parameters --------------------------------------------------------

    def build_params(self) -> dict:
        return {"x": float(self.origin[0]), "y": float(self.origin[1]),
                "n_sides": int(self.n_sides),
                self.size_mode: float(self.value(self.size_mode)),
                "azimuth_deg": float(self.value("azimuth_deg") or 0.0)}

    def preview_points(self) -> Optional[np.ndarray]:
        if self.origin is None or self.n_sides < 3:
            return None
        radius = self.radius_m()
        azimuth = self.value("azimuth_deg")
        if radius is None and self.cursor is not None:
            dx = self.cursor[0] - self.origin[0]
            dy = self.cursor[1] - self.origin[1]
            radius = math.hypot(dx, dy)
            if azimuth is None:
                azimuth = math.degrees(math.atan2(dx, dy)) % 360.0
        if not radius or radius <= 0.0:
            return None
        return ge.regular_polygon(self.origin, self.n_sides, azimuth or 0.0,
                                  radius_m=radius)

    # -- readout -----------------------------------------------------------

    def hud_lines(self):
        """Every way of stating the size at once, plus the side count."""
        lines = super().hud_lines()
        lines.append("n {0}".format(self.n_sides))
        radius = self.radius_m()
        if radius is None and self.origin is not None and self.cursor is not None:
            radius = math.hypot(self.cursor[0] - self.origin[0],
                                self.cursor[1] - self.origin[1])
        if radius and self.n_sides >= 3:
            n = self.n_sides
            lines.append("R {0:.3f} m".format(radius))
            lines.append("a {0:.3f} m".format(
                size_from_radius(SIZE_APOTHEM, radius, n)))
            lines.append("L {0:.3f} m".format(
                size_from_radius(SIZE_SIDE, radius, n)))
            lines.append("Area {0:.3f} m2".format(
                size_from_radius(SIZE_AREA, radius, n)))
        return lines


def create(canvas, iface=None, layer_provider=None,
           n_sides: int = DEFAULT_SIDES, size_mode: str = SIZE_RADIUS,
           length_unit: str = "m", angle_unit: str = "deg") -> CadMapTool:
    """Build the map tool. The dock calls this."""
    session = RegularPolygonSession(length_unit, angle_unit, n_sides, size_mode)
    return CadMapTool(canvas, session, iface=iface,
                      layer_provider=layer_provider)
