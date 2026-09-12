"""
M04 -- which way the rows run.

Three ways of answering, because an operator has three different reasons:

* **manuale**     -- a bearing read off a compass or copied from a plan;
* **geometrico**  -- along the parcel, so the rows waste as little as
  possible at the ends: the long axis of the oriented minimum bounding box,
  which GEOS computes;
* **morfologico** -- from the ground itself, either along the contours
  (rows across the slope, the usual choice for erosion control and for
  working a hillside) or along the line of maximum slope.

Convention, as everywhere: 0 deg = North (+Y), 90 deg = East (+X), clockwise.
A row has no head and no tail, so every answer here is an *orientation* folded
into [0, 180): a row at 200 deg is the same row as one at 20 deg, and giving
back two numbers for one line invites a bug at the first comparison.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ...core.errors import InvalidInputError
from ...core.planar import along_track_unit, azimuth_of

#: Rows across the slope, parallel to the contours. The default: it is what
#: "a girapoggio" means and what a hillside plantation is normally laid out on.
ALIGN_CONTOUR = "contour"
#: Rows straight up and down the slope, "a rittochino".
ALIGN_SLOPE = "slope"
ALIGNMENTS = (ALIGN_CONTOUR, ALIGN_SLOPE)

ALIGNMENT_LABELS = {
    ALIGN_CONTOUR: "File parallele alle curve di livello (a girapoggio)",
    ALIGN_SLOPE: "File lungo la massima pendenza (a rittochino)",
}

MODE_MANUAL = "manual"
MODE_GEOMETRIC = "geometric"
MODE_MORPHOLOGIC = "morphologic"
MODES = (MODE_MANUAL, MODE_GEOMETRIC, MODE_MORPHOLOGIC)

MODE_LABELS = {
    MODE_MANUAL: "Manuale",
    MODE_GEOMETRIC: "Geometrico (asse maggiore della particella)",
    MODE_MORPHOLOGIC: "Morfologico (dal modello del terreno)",
}


def fold(azimuth_deg: float) -> float:
    """An orientation in [0, 180): a row and its reverse are one row."""
    return float(azimuth_deg) % 180.0


def perpendicular(azimuth_deg: float) -> float:
    return fold(float(azimuth_deg) + 90.0)


# --------------------------------------------------------------------------
# From the ground
# --------------------------------------------------------------------------

def mean_gradient(terrain, mask=None):
    """Mean uphill gradient ``(dz/dEast, dz/dNorth)`` over the window.

    Taken from the slope and aspect grids ``core.z`` already computes, not
    from a second derivative of the elevation: one Horn operator, one answer.
    Cells with no slope, and flat cells with no aspect, contribute nothing.
    """
    slope, aspect = terrain.grids()
    valid = np.isfinite(slope) & np.isfinite(aspect)
    if mask is not None:
        valid = valid & mask
    if not np.any(valid):
        return float("nan"), float("nan")
    theta = np.radians(slope[valid])
    alpha = np.radians(aspect[valid])
    magnitude = np.tan(theta)
    # Aspect points downhill, so the uphill gradient is its negative.
    gx = -float(np.mean(magnitude * np.sin(alpha)))
    gy = -float(np.mean(magnitude * np.cos(alpha)))
    return gx, gy


def steepest_descent_azimuth(terrain, mask=None) -> Optional[float]:
    """Compass bearing [0, 360) of the mean downhill direction, or None.

    ``None`` on ground too flat for the question to mean anything -- which is
    the honest answer, and the caller then falls back on the parcel or on the
    operator rather than on an arbitrary north.
    """
    gx, gy = mean_gradient(terrain, mask)
    if not (math.isfinite(gx) and math.isfinite(gy)):
        return None
    if math.hypot(gx, gy) < 1e-12:
        return None
    return azimuth_of(-gx, -gy)


def morphological_azimuth(terrain, mask=None,
                          alignment: str = ALIGN_CONTOUR) -> Optional[float]:
    """Row orientation read off the DEM, in [0, 180), or None if flat."""
    if alignment not in ALIGNMENTS:
        raise InvalidInputError(
            "unknown alignment {0!r}".format(alignment),
            user_message="Allineamento delle file non riconosciuto.")
    descent = steepest_descent_azimuth(terrain, mask)
    if descent is None:
        return None
    if alignment == ALIGN_SLOPE:
        return fold(descent)
    return perpendicular(descent)


# --------------------------------------------------------------------------
# From the parcel
# --------------------------------------------------------------------------

def geometric_azimuth(geometry) -> Optional[float]:
    """Orientation of the parcel's long axis, in [0, 180).

    ``QgsGeometry.orientedMinimumBoundingBox`` returns the box; the bearing
    is measured on the box's own longest edge rather than read from the angle
    it also returns, because an edge is unambiguous and an angle convention
    is one more thing that could differ between two QGIS versions.
    """
    if geometry is None or geometry.isEmpty():
        return None
    result = geometry.orientedMinimumBoundingBox()
    box = result[0] if isinstance(result, tuple) else result
    if box is None or box.isEmpty():
        return None
    ring = box.asPolygon()
    if not ring or len(ring[0]) < 3:
        return None
    points = ring[0]
    best_length = -1.0
    best_azimuth = None
    for start, end in zip(points, points[1:]):
        dx, dy = end.x() - start.x(), end.y() - start.y()
        length = math.hypot(dx, dy)
        if length > best_length:
            best_length = length
            best_azimuth = azimuth_of(dx, dy)
    return None if best_azimuth is None else fold(best_azimuth)


# --------------------------------------------------------------------------
# The one call a caller needs
# --------------------------------------------------------------------------

def resolve(mode: str, manual_deg: float = 0.0, terrain=None, geometry=None,
            mask=None, alignment: str = ALIGN_CONTOUR) -> float:
    """The row orientation for one project, by whichever route was chosen.

    Every route can fail to have an answer -- flat ground, no parcel -- and
    each falls back on the next most defensible source rather than on zero:
    morphological to geometric to manual, geometric to manual.
    """
    if mode not in MODES:
        raise InvalidInputError(
            "unknown orientation mode {0!r}".format(mode),
            user_message="Modalita' di orientamento non riconosciuta.")
    if mode == MODE_MANUAL:
        return fold(manual_deg)
    if mode == MODE_MORPHOLOGIC and terrain is not None:
        answer = morphological_azimuth(terrain, mask, alignment)
        if answer is not None:
            return answer
    if mode in (MODE_GEOMETRIC, MODE_MORPHOLOGIC) and geometry is not None:
        answer = geometric_azimuth(geometry)
        if answer is not None:
            return answer
    return fold(manual_deg)


def describe(mode: str, azimuth_deg: float,
             alignment: str = ALIGN_CONTOUR) -> "list[str]":
    lines = ["ORIENTAMENTO DELLE FILE",
             "  Criterio: {0}".format(MODE_LABELS.get(mode, mode))]
    if mode == MODE_MORPHOLOGIC:
        lines.append("  {0}".format(ALIGNMENT_LABELS.get(alignment,
                                                         alignment)))
    lines.append("  Azimut file: {0:.1f} deg".format(azimuth_deg))
    east, north = along_track_unit(azimuth_deg)
    lines.append("  Direzione: ({0:+.3f} E, {1:+.3f} N)".format(east, north))
    return lines
