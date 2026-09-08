"""
Planar transforms: move, rotate, scale, mirror, rectangular and polar arrays.

PURE PYTHON + NUMPY, operating on ``(N, 2)`` coordinate arrays in the working
CRS. The QGIS wrappers that apply these to features inside an edit buffer live
in ``cad/modifiers.py``.

Rotation sign: a **positive angle rotates clockwise**, matching the compass
azimuth convention used everywhere else in the plugin (azimuth grows clockwise
from north). This is the opposite of the mathematical convention, and it is
deliberate: mixing "CCW for shapes, CW for bearings" is the single most
reliable way to ship mirrored geometry.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .constants import GEOM_EPS_M
from .errors import ConstraintError, InvalidInputError
from .planar import across_track_unit, along_track_unit


def _as_points(points) -> np.ndarray:
    pts = np.asarray(points, dtype=float)
    if pts.ndim != 2 or pts.shape[1] < 2:
        raise InvalidInputError(
            "expected an (N, 2) array of coordinates, got shape {0!r}".format(
                getattr(pts, "shape", None)),
            user_message="Geometria non valida per la trasformazione.")
    return pts


def rotation_matrix(angle_deg: float) -> np.ndarray:
    """2x2 clockwise rotation matrix for ``angle_deg``."""
    a = math.radians(float(angle_deg))
    ca, sa = math.cos(a), math.sin(a)
    # Row-vector convention: points are transformed as ``p @ M``, so this
    # is the transpose of the column-vector clockwise matrix.
    # Check: (1, 0) at 90 deg -> (0, -1), i.e. East turns to South.
    return np.array([[ca, -sa], [sa, ca]])


def translate(points, dx: float, dy: float) -> np.ndarray:
    pts = _as_points(points).copy()
    pts[:, 0] += float(dx)
    pts[:, 1] += float(dy)
    return pts


def rotate(points, angle_deg: float, pivot=None) -> np.ndarray:
    """Rotate clockwise by ``angle_deg`` about ``pivot`` (default: centroid)."""
    pts = _as_points(points)
    if pivot is None:
        pivot = pts[:, :2].mean(axis=0)
    piv = np.asarray(pivot, dtype=float).reshape(-1)[:2]
    out = pts.copy()
    out[:, :2] = (pts[:, :2] - piv) @ rotation_matrix(angle_deg) + piv
    return out


def scale(points, factor_x: float, factor_y: Optional[float] = None,
          origin=None) -> np.ndarray:
    """Scale about ``origin``. ``factor_y`` defaults to ``factor_x``."""
    pts = _as_points(points)
    fx = float(factor_x)
    fy = float(factor_x if factor_y is None else factor_y)
    if abs(fx) < GEOM_EPS_M or abs(fy) < GEOM_EPS_M:
        raise InvalidInputError(
            "scale factors must be non-zero, got ({0!r}, {1!r})".format(fx, fy),
            user_message="I fattori di scala non possono essere nulli.")
    if origin is None:
        origin = pts[:, :2].mean(axis=0)
    org = np.asarray(origin, dtype=float).reshape(-1)[:2]
    out = pts.copy()
    out[:, 0] = org[0] + (pts[:, 0] - org[0]) * fx
    out[:, 1] = org[1] + (pts[:, 1] - org[1]) * fy
    return out


def mirror(points, line_a, line_b) -> np.ndarray:
    """Reflect across the infinite line through ``line_a`` and ``line_b``."""
    pts = _as_points(points)
    a = np.asarray(line_a, dtype=float).reshape(-1)[:2]
    b = np.asarray(line_b, dtype=float).reshape(-1)[:2]
    d = b - a
    norm = float(np.hypot(*d))
    if norm < GEOM_EPS_M:
        raise ConstraintError(
            "mirror axis has zero length",
            user_message="L'asse di specchiatura ha lunghezza nulla.",
            hint="Indica due punti distinti per definire l'asse.")
    u = d / norm
    rel = pts[:, :2] - a
    proj = np.outer(rel @ u, u)
    out = pts.copy()
    out[:, :2] = a + 2.0 * proj - rel
    return out


def mirror_axis(points, origin, azimuth_deg: float) -> np.ndarray:
    """Reflect across the line through ``origin`` at ``azimuth_deg``."""
    org = np.asarray(origin, dtype=float).reshape(-1)[:2]
    ux, uy = along_track_unit(azimuth_deg)
    return mirror(points, org, org + np.array([ux, uy]))


# --------------------------------------------------------------------------
# Arrays
# --------------------------------------------------------------------------

def array_rectangular(points, n_cols: int, n_rows: int,
                      spacing_x: float, spacing_y: float,
                      azimuth_deg: float = 0.0) -> "list[np.ndarray]":
    """Grid of copies. ``spacing_y`` runs along ``azimuth_deg``.

    Returns ``n_cols * n_rows`` arrays including the original at index 0.
    """
    pts = _as_points(points)
    n_cols, n_rows = int(n_cols), int(n_rows)
    if n_cols < 1 or n_rows < 1:
        raise InvalidInputError(
            "array counts must be >= 1, got {0}x{1}".format(n_cols, n_rows),
            user_message="Il numero di copie deve essere almeno 1.")
    ux, uy = along_track_unit(azimuth_deg)          # row direction
    vx, vy = across_track_unit(azimuth_deg)         # column direction
    out = []
    for row in range(n_rows):
        for col in range(n_cols):
            dx = row * spacing_y * ux + col * spacing_x * vx
            dy = row * spacing_y * uy + col * spacing_x * vy
            out.append(translate(pts, dx, dy))
    return out


def array_polar(points, center, count: int,
                total_angle_deg: Optional[float] = None,
                step_angle_deg: Optional[float] = None,
                rotate_items: bool = True) -> "list[np.ndarray]":
    """Copies around ``center``.

    Give exactly one of ``total_angle_deg`` (spread evenly over that sweep) or
    ``step_angle_deg`` (fixed increment). ``rotate_items`` also spins each copy
    about its own position, which is what you want for radiating shapes and not
    what you want for, say, north-aligned labels.
    """
    pts = _as_points(points)
    count = int(count)
    if count < 1:
        raise InvalidInputError(
            "count must be >= 1, got {0}".format(count),
            user_message="Il numero di copie deve essere almeno 1.")
    if (total_angle_deg is None) == (step_angle_deg is None):
        raise InvalidInputError(
            "provide exactly one of total_angle_deg or step_angle_deg",
            user_message="Indica l'angolo totale oppure il passo angolare, non "
                         "entrambi.")
    if step_angle_deg is None:
        # A full 360 sweep must not duplicate the first copy on top of the last.
        divisor = count if abs(abs(total_angle_deg) - 360.0) < 1e-9 else max(count - 1, 1)
        step = float(total_angle_deg) / divisor
    else:
        step = float(step_angle_deg)

    cen = np.asarray(center, dtype=float).reshape(-1)[:2]
    out = []
    for i in range(count):
        angle = i * step
        if rotate_items:
            out.append(rotate(pts, angle, cen))
        else:
            local = rotate(pts[:, :2].mean(axis=0)[None, :], angle, cen)[0]
            offset = local - pts[:, :2].mean(axis=0)
            out.append(translate(pts, offset[0], offset[1]))
    return out


def array_along_path(points, path, spacing_m: float,
                     align_to_path: bool = True) -> "list[np.ndarray]":
    """Copies spaced along a polyline, optionally turned to follow it."""
    from .planar import headings_along, points_along

    pts = _as_points(points)
    stations, _ = points_along(np.asarray(path, dtype=float), spacing_m)
    if stations.shape[0] == 0:
        return []
    headings = headings_along(np.asarray(path, dtype=float))
    anchor = pts[:, :2].mean(axis=0)
    out = []
    for i, station in enumerate(stations):
        copy = translate(pts, station[0] - anchor[0], station[1] - anchor[1])
        if align_to_path:
            heading = headings[min(i, headings.size - 1)]
            copy = rotate(copy, heading, station)
        out.append(copy)
    return out
