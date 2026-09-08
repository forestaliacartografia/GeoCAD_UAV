"""
Interactive rectangle tool (Workflow A).

    click origine -> 30 Enter -> 20 Enter -> 15d Enter -> Enter = commit

or, equivalently, click two opposite corners.

No geometry is computed here. The ring comes from
``core.geometry_engine.rectangle_from_*``, the same functions the Processing
algorithm uses, so a rectangle drawn on the canvas and one built headless are
byte-for-byte the same shape.
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

#: Where the picked origin sits on the rectangle.
REFERENCE_CORNER = "corner"
REFERENCE_CENTER = "center"


class RectangleSession(CadToolSession):
    """Width, height and azimuth, anchored at a corner or at the centre."""

    tool_id = pr.TOOL_RECTANGLE
    geometry_type = "Polygon"
    title = "Rettangolo"
    accepts_second_click = True

    def __init__(self, length_unit: str = "m", angle_unit: str = "deg",
                 reference: str = REFERENCE_CORNER):
        # Set before super().__init__, which calls slots().
        self.reference = reference
        #: Filled when the shape came from two corners rather than typed sizes.
        self._center_override = None
        super().__init__(length_unit, angle_unit)

    def slots(self):
        return [
            ConstraintSlot("width_m", di.KIND_LENGTH, "Larghezza"),
            ConstraintSlot("height_m", di.KIND_LENGTH, "Altezza"),
            ConstraintSlot("azimuth_deg", di.KIND_ANGLE, "Rotazione"),
        ]

    def reset(self):
        super().reset()
        self._center_override = None

    # -- two-corner drag ---------------------------------------------------

    def derive_from_second(self) -> None:
        """Turn origin + opposite corner into width / height / centre.

        The result is expressed in the *same* parameters as the typed workflow,
        so ``cad_params`` carries width/height/rotation whichever way the
        rectangle was drawn -- the Geometry Properties editor then behaves
        identically for both.
        """
        azimuth = self.value("azimuth_deg")
        if azimuth is None:
            azimuth = 0.0
            self.set_value("azimuth_deg", 0.0)

        ux, uy = along_track_unit(azimuth)
        vx, vy = across_track_unit(azimuth)
        dx = self.second[0] - self.origin[0]
        dy = self.second[1] - self.origin[1]
        along = dx * ux + dy * uy          # the height axis
        across = dx * vx + dy * vy         # the width axis

        self.set_value("width_m", abs(across))
        self.set_value("height_m", abs(along))
        self._center_override = (0.5 * (self.origin[0] + self.second[0]),
                                 0.5 * (self.origin[1] + self.second[1]))

    # -- parameters --------------------------------------------------------

    def build_params(self) -> dict:
        azimuth = self.value("azimuth_deg") or 0.0
        if self._center_override is not None:
            anchor, mode = self._center_override, REFERENCE_CENTER
        else:
            anchor, mode = self.origin, self.reference
        return {"mode": mode, "x": float(anchor[0]), "y": float(anchor[1]),
                "width_m": float(self.value("width_m")),
                "height_m": float(self.value("height_m")),
                "azimuth_deg": float(azimuth)}

    def preview_points(self) -> Optional[np.ndarray]:
        if self.origin is None:
            return None

        if self.all_filled:
            params = self.build_params()
            builder = (ge.rectangle_from_center if params["mode"] == REFERENCE_CENTER
                       else ge.rectangle_from_corner)
            return builder((params["x"], params["y"]), params["width_m"],
                           params["height_m"], params["azimuth_deg"])

        # Live drag: origin to cursor, as opposite corners.
        if self.cursor is None:
            return None
        azimuth = self.value("azimuth_deg") or 0.0
        try:
            return ge.rectangle_from_opposite_corners(self.origin, self.cursor,
                                                      azimuth)
        except Exception:                                       # noqa: BLE001
            return None            # degenerate while the cursor sits still

    # -- readout -----------------------------------------------------------

    def hud_lines(self):
        lines = super().hud_lines()
        width = self.value("width_m")
        height = self.value("height_m")
        if width and height:
            lines.append("Area {0:.3f} m2".format(width * height))
            lines.append("Perimetro {0:.3f} m".format(2.0 * (width + height)))
        lines.append("Riferimento: {0}".format(
            "centro" if (self._center_override is not None
                         or self.reference == REFERENCE_CENTER) else "origine"))
        return lines


def create(canvas, iface=None, layer_provider=None,
           reference: str = REFERENCE_CORNER, length_unit: str = "m",
           angle_unit: str = "deg") -> CadMapTool:
    """Build the map tool. The dock calls this."""
    session = RectangleSession(length_unit, angle_unit, reference)
    return CadMapTool(canvas, session, iface=iface,
                      layer_provider=layer_provider)
