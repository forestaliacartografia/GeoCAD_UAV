"""
Interactive ellipse tool (Workflow B).

    click centro -> 20 Enter -> 10 Enter -> Enter = commit

or click the centre and then the end of the major axis, which fixes both the
semi-major length and the orientation; the semi-minor is still typed, because
one click cannot know it.

The ring is ``core.geometry_engine.ellipse_ring`` at the project's segment
count. The major axis lies along ``azimuth_deg`` in the compass sense used
everywhere else in this plugin -- azimuth 0 points North (+Y), 90 East (+X) --
because it is the same ``along_track_unit`` that orients grids and flight
strips.

A semi-minor larger than the semi-major is refused by the engine with a
``ConstraintError``; this tool does not swap the two behind the operator's
back, because a silent swap turns a typo into a shape nobody asked for.
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


class EllipseSession(CadToolSession):
    """Centre, two semi-axes and the bearing of the major one."""

    tool_id = pr.TOOL_ELLIPSE
    geometry_type = "Polygon"
    title = "Ellisse"
    accepts_second_click = True

    def __init__(self, length_unit: str = "m", angle_unit: str = "deg",
                 segments: int = CIRCLE_SEGMENTS):
        self.segments = int(segments)
        super().__init__(length_unit, angle_unit)

    def slots(self):
        return [
            ConstraintSlot("semi_major_m", di.KIND_LENGTH, "Semiasse maggiore"),
            ConstraintSlot("semi_minor_m", di.KIND_LENGTH, "Semiasse minore"),
            ConstraintSlot("azimuth_deg", di.KIND_ANGLE, "Rotazione"),
        ]

    # -- pick the end of the major axis ------------------------------------

    def derive_from_second(self) -> None:
        """The second click is the end of the major axis: length and bearing."""
        dx = self.second[0] - self.origin[0]
        dy = self.second[1] - self.origin[1]
        length = math.hypot(dx, dy)
        if length <= 0.0:
            return
        self.set_value("semi_major_m", length)
        self.set_value("azimuth_deg", math.degrees(math.atan2(dx, dy)) % 360.0)

    # -- parameters --------------------------------------------------------

    def build_params(self) -> dict:
        return {"x": float(self.origin[0]), "y": float(self.origin[1]),
                "semi_major_m": float(self.value("semi_major_m")),
                "semi_minor_m": float(self.value("semi_minor_m")),
                "azimuth_deg": float(self.value("azimuth_deg") or 0.0),
                "segments": self.segments}

    def preview_points(self) -> Optional[np.ndarray]:
        if self.origin is None:
            return None
        major = self.value("semi_major_m")
        azimuth = self.value("azimuth_deg")
        if major is None and self.cursor is not None:
            dx = self.cursor[0] - self.origin[0]
            dy = self.cursor[1] - self.origin[1]
            major = math.hypot(dx, dy)
            if azimuth is None:
                azimuth = math.degrees(math.atan2(dx, dy)) % 360.0
        if not major or major <= 0.0:
            return None
        minor = self.value("semi_minor_m")
        if minor is None:
            # Nothing to preview yet for the second axis: show the major axis
            # as a circle rather than inventing a ratio.
            minor = major
        if minor > major:
            # The engine will refuse this on commit. Previewing the refused
            # shape would suggest it is about to be drawn.
            return None
        return ge.ellipse_ring(self.origin, major, minor, azimuth or 0.0,
                               self.segments)

    # -- readout -----------------------------------------------------------

    def hud_lines(self):
        """Axes, area and eccentricity, so a typo is visible before Enter."""
        lines = super().hud_lines()
        major = self.value("semi_major_m")
        minor = self.value("semi_minor_m")
        if major:
            lines.append("a {0:.3f} m".format(major))
            lines.append("2a {0:.3f} m".format(2.0 * major))
        if major and minor:
            lines.append("b {0:.3f} m".format(minor))
            if minor > major:
                lines.append("b > a: non valido")
            else:
                lines.append("Area {0:.3f} m2".format(math.pi * major * minor))
                ratio = minor / major
                lines.append("e {0:.4f}".format(
                    math.sqrt(max(0.0, 1.0 - ratio * ratio))))
        return lines


def create(canvas, iface=None, layer_provider=None,
           segments: int = CIRCLE_SEGMENTS, length_unit: str = "m",
           angle_unit: str = "deg") -> CadMapTool:
    """Build the map tool. The dock calls this."""
    session = EllipseSession(length_unit, angle_unit, segments)
    return CadMapTool(canvas, session, iface=iface,
                      layer_provider=layer_provider)
