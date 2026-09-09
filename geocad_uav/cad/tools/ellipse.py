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
from ...core.planar import across_track_unit, along_track_unit
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
            # Measured before it was fixed (T-CE1): this used to fall back to
            # minor = major, so the operator drawing an ellipse watched a
            # perfect circle until they typed b. The engine was never wrong --
            # ge.ellipse_ring(20, 10, 0) spans 20 m East by 40 m North -- the
            # preview was. With b unknown there is no ellipse to draw, so the
            # major axis is drawn instead: it is what has actually been
            # decided, and it cannot be mistaken for a finished shape.
            return self._major_axis(major, azimuth or 0.0)
        if minor > major:
            # The engine will refuse this on commit. Previewing the refused
            # shape would suggest it is about to be drawn.
            return None
        return ge.ellipse_ring(self.origin, major, minor, azimuth or 0.0,
                               self.segments)

    def _major_axis(self, semi_major: float, azimuth_deg: float) -> np.ndarray:
        """The two ends of the major axis, through the centre."""
        ux, uy = along_track_unit(azimuth_deg)
        cx, cy = float(self.origin[0]), float(self.origin[1])
        return np.array([[cx - semi_major * ux, cy - semi_major * uy],
                         [cx + semi_major * ux, cy + semi_major * uy]],
                        dtype=float)

    def axes_points(self):
        """Major and minor axis ends, for a caller that can draw two bands.

        Returned as ``(major, minor)`` arrays, or ``(major, None)`` while the
        second axis is still unknown. The single rubber band the base tool
        owns can only draw one path, so this is here for the panel and the
        tests rather than for the canvas.
        """
        major = self.value("semi_major_m")
        azimuth = self.value("azimuth_deg") or 0.0
        if self.origin is None or not major:
            return None, None
        minor = self.value("semi_minor_m")
        major_pts = self._major_axis(float(major), azimuth)
        if not minor:
            return major_pts, None
        vx, vy = across_track_unit(azimuth)
        cx, cy = float(self.origin[0]), float(self.origin[1])
        minor_pts = np.array([[cx - minor * vx, cy - minor * vy],
                              [cx + minor * vx, cy + minor * vy]], dtype=float)
        return major_pts, minor_pts

    # -- readout -----------------------------------------------------------

    def hud_lines(self):
        """Axes, area and eccentricity, so a typo is visible before Enter."""
        lines = super().hud_lines()
        major = self.value("semi_major_m")
        minor = self.value("semi_minor_m")
        if major:
            lines.append("a {0:.3f} m".format(major))
            lines.append("2a {0:.3f} m".format(2.0 * major))
        if major and not minor:
            lines.append("b = ? (anteprima: solo l'asse maggiore)")
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
