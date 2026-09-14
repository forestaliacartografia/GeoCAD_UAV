"""
Snapping: a thin wrapper over QGIS's own snapping engine.

Spec P9 -- do not reinvent this. ``QgsSnappingUtils`` already resolves vertex,
segment, centroid, middle and area matches across every visible layer, honours
the project's snapping configuration, and is what the rest of QGIS uses. The
plugin's job is to *ask* it, and to add the two things it does not provide:
a plugin-owned construction grid, and perpendicular/tangent construction
snaps derived from a reference geometry.

Leaving the project's own snapping config untouched is deliberate (acceptance:
"QGIS nativo non e' rotto"): :func:`scoped_snapping` restores whatever the
operator had set as soon as the tool finishes.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from .constants import SNAP_TOLERANCE_PX, TOPO_EPS_M

# Extra snap kinds this plugin adds on top of the QGIS engine.
SNAP_GRID = "grid"
SNAP_PERPENDICULAR = "perpendicular"
SNAP_TANGENT = "tangent"
SNAP_EXTENSION = "extension"


@dataclass
class SnapResult:
    """Where a click actually landed, and why."""

    x: float
    y: float
    kind: str = ""              # "vertex", "segment", "grid", ...
    layer_name: str = ""
    matched: bool = False

    @property
    def point(self):
        return self.x, self.y

    def describe(self) -> str:
        if not self.matched:
            return "nessuno snap"
        labels = {
            "vertex": "vertice", "segment": "segmento", "area": "area",
            "centroid": "centroide", "middle": "punto medio",
            SNAP_GRID: "griglia", SNAP_PERPENDICULAR: "perpendicolare",
            SNAP_TANGENT: "tangente", SNAP_EXTENSION: "prolungamento",
        }
        label = labels.get(self.kind, self.kind)
        return "{0} su {1}".format(label, self.layer_name) if self.layer_name \
            else label


def _match_kind(match) -> str:
    """Human name for a QgsPointLocator match type."""
    from qgis.core import QgsPointLocator                        # noqa: PLC0415

    if match.hasVertex():
        return "vertex"
    if hasattr(match, "hasMiddleSegment") and match.hasMiddleSegment():
        return "middle"
    if hasattr(match, "hasCentroid") and match.hasCentroid():
        return "centroid"
    if match.hasEdge():
        return "segment"
    if hasattr(match, "hasArea") and match.hasArea():
        return "area"
    return "match"


def snap_to_map(canvas, map_point) -> SnapResult:
    """Ask QGIS's snapping engine where ``map_point`` should land."""
    if canvas is None:
        return SnapResult(map_point.x(), map_point.y(), matched=False)
    utils = canvas.snappingUtils()
    if utils is None:
        return SnapResult(map_point.x(), map_point.y(), matched=False)
    match = utils.snapToMap(map_point)
    if match is None or not match.isValid():
        return SnapResult(map_point.x(), map_point.y(), matched=False)
    point = match.point()
    layer = match.layer()
    return SnapResult(point.x(), point.y(), _match_kind(match),
                      layer.name() if layer else "", True)


@contextmanager
def scoped_snapping(project, enabled: bool = True,
                    tolerance_px: int = SNAP_TOLERANCE_PX,
                    types=None):
    """Temporarily adjust project snapping, then restore it exactly.

    The operator's own snapping setup survives the tool: nothing is more
    irritating than a plugin that silently turns vertex snapping off.
    """
    from qgis.core import QgsSnappingConfig, QgsTolerance        # noqa: PLC0415

    original = QgsSnappingConfig(project.snappingConfig())
    try:
        config = QgsSnappingConfig(original)
        config.setEnabled(enabled)
        config.setMode(QgsSnappingConfig.SnappingMode.AllLayers)
        config.setTolerance(tolerance_px)
        config.setUnits(QgsTolerance.UnitType.Pixels)
        if types is not None:
            try:
                config.setTypeFlag(types)
            except (AttributeError, TypeError):
                pass                # older/newer enum shape; keep the default
        project.setSnappingConfig(config)
        yield config
    finally:
        project.setSnappingConfig(original)


# --------------------------------------------------------------------------
# Construction snaps the QGIS engine does not provide
# --------------------------------------------------------------------------

def snap_to_grid(x: float, y: float, spacing: float,
                 origin=(0.0, 0.0), azimuth_deg: float = 0.0) -> SnapResult:
    """Snap to the plugin's own construction grid, rotated if required."""
    if spacing <= 0:
        return SnapResult(x, y, matched=False)
    from .planar import StripFrame                               # noqa: PLC0415

    frame = StripFrame(azimuth_deg, origin[0], origin[1])
    s, t = frame.to_frame(x, y)
    s = round(float(s) / spacing) * spacing
    t = round(float(t) / spacing) * spacing
    gx, gy = frame.to_world(s, t)
    return SnapResult(float(gx), float(gy), SNAP_GRID, "", True)


def snap_perpendicular(x: float, y: float, seg_a, seg_b,
                       tolerance_m: float = TOPO_EPS_M) -> SnapResult:
    """Foot of the perpendicular from a point onto a reference segment."""
    from .geometry_engine import perpendicular_foot              # noqa: PLC0415

    foot = perpendicular_foot((x, y), seg_a, seg_b)
    return SnapResult(float(foot[0]), float(foot[1]), SNAP_PERPENDICULAR,
                      "", True)


def snap_tangent(x: float, y: float, centre, radius: float) -> SnapResult:
    """Tangent point on a circle, for the ray from the circle centre.

    A point inside the circle has no tangent, and saying so is better than
    returning the nearest arc point and pretending it is one.
    """
    cx, cy = float(centre[0]), float(centre[1])
    dx, dy = x - cx, y - cy
    dist = math.hypot(dx, dy)
    if dist <= radius + 1e-12:
        return SnapResult(x, y, matched=False)
    # Tangent length and the angle between the centre ray and the tangent.
    alpha = math.acos(max(-1.0, min(1.0, radius / dist)))
    base = math.atan2(dy, dx)
    tx = cx + radius * math.cos(base + alpha)
    ty = cy + radius * math.sin(base + alpha)
    return SnapResult(tx, ty, SNAP_TANGENT, "", True)


def best_snap(canvas, map_point, grid_spacing: float = 0.0,
              grid_origin=(0.0, 0.0), grid_azimuth: float = 0.0,
              tolerance_m: float = TOPO_EPS_M) -> SnapResult:
    """QGIS snapping first, the construction grid as a fallback.

    Layer geometry wins over the grid: an operator snapping to an existing
    parcel corner means that corner, not the nearest grid node to it.
    """
    result = snap_to_map(canvas, map_point)
    if result.matched:
        return result
    if grid_spacing > 0:
        return snap_to_grid(map_point.x(), map_point.y(), grid_spacing,
                            grid_origin, grid_azimuth)
    return result
