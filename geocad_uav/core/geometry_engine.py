"""
CAD primitives, solved algebraically.

Spec P0: every shape is *computed*, never approximated from where the mouse
happened to be. A rectangle asked for as 25.00 x 12.00 m at azimuth 15 deg
comes out at exactly those dimensions, to float64 in the working CRS; the click
supplies a reference point, not the geometry.

PURE PYTHON + NUMPY. Every function returns an ``(N, 2)`` array of projected
metres. The QGIS adapter that turns these into ``QgsGeometry`` and attaches a
:class:`~.models.ParametricRecord` lives in ``cad/primitives.py``, so the
mathematics stays testable without a QGIS runtime.

Angles are compass azimuths, degrees clockwise from grid north -- the single
convention used across CAD, forest and flight modules.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .constants import CIRCLE_SEGMENTS, GEOM_EPS_M
from .errors import ConstraintError, InvalidInputError
from .planar import across_track_unit, along_track_unit, rectangle_corners


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------

def _positive(value: float, name: str, italian: str) -> float:
    """Require a finite, strictly positive number."""
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(
            "{0} is not a number: {1!r}".format(name, value),
            user_message="{0} non e' un numero valido.".format(italian)) from exc
    if not math.isfinite(value) or value <= 0.0:
        raise InvalidInputError(
            "{0} must be > 0, got {1!r}".format(name, value),
            user_message="{0} deve essere maggiore di zero.".format(italian),
            hint="Valore ricevuto: {0}.".format(value))
    return value


def _point(value, name: str = "point") -> np.ndarray:
    pt = np.asarray(value, dtype=float).reshape(-1)
    if pt.size < 2 or not np.all(np.isfinite(pt[:2])):
        raise InvalidInputError(
            "{0} must be a finite (x, y), got {1!r}".format(name, value),
            user_message="Coordinate non valide.")
    return pt[:2].astype(float)


def close_ring(points) -> np.ndarray:
    """Append the first vertex if the ring is not already closed."""
    pts = np.asarray(points, dtype=float)
    if pts.shape[0] < 3:
        raise InvalidInputError(
            "a ring needs at least 3 vertices, got {0}".format(pts.shape[0]),
            user_message="Servono almeno tre vertici per chiudere un poligono.")
    if np.hypot(*(pts[0] - pts[-1])) > GEOM_EPS_M:
        pts = np.vstack([pts, pts[0]])
    return pts


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------

def polygon_area(ring) -> float:
    """Planar area by the shoelace formula. Always positive."""
    pts = close_ring(ring)
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(float(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1])))


def polygon_perimeter(ring) -> float:
    pts = close_ring(ring)
    return float(np.hypot(*np.diff(pts, axis=0).T).sum())


def polygon_centroid(ring) -> np.ndarray:
    """Area centroid (not the vertex average) of a simple closed ring."""
    pts = close_ring(ring)
    x, y = pts[:, 0], pts[:, 1]
    cross = x[:-1] * y[1:] - x[1:] * y[:-1]
    area = 0.5 * float(cross.sum())
    if abs(area) < GEOM_EPS_M ** 2:
        return pts[:-1].mean(axis=0)
    cx = float(((x[:-1] + x[1:]) * cross).sum()) / (6.0 * area)
    cy = float(((y[:-1] + y[1:]) * cross).sum()) / (6.0 * area)
    return np.array([cx, cy])


def is_clockwise(ring) -> bool:
    pts = close_ring(ring)
    x, y = pts[:, 0], pts[:, 1]
    return float(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1])) < 0.0


# --------------------------------------------------------------------------
# Point and line
# --------------------------------------------------------------------------

def line_from_length_azimuth(start, length_m: float,
                             azimuth_deg: float) -> np.ndarray:
    """Segment of an exact length at an exact bearing from ``start``."""
    p0 = _point(start, "start")
    length_m = _positive(length_m, "length_m", "La lunghezza")
    ux, uy = along_track_unit(azimuth_deg)
    return np.array([p0, [p0[0] + length_m * ux, p0[1] + length_m * uy]])


def polyline_from_segments(start, segments) -> np.ndarray:
    """Chain of ``(length, azimuth)`` steps.

    This is what the dynamic-input parser feeds: each vertex is placed by
    solving the constraint, so a closed traverse closes to float64 rather than
    to pixel accuracy.
    """
    pts = [_point(start, "start")]
    for i, seg in enumerate(segments):
        length, azimuth = seg
        length = _positive(length, "segment {0} length".format(i),
                           "La lunghezza del segmento {0}".format(i + 1))
        ux, uy = along_track_unit(azimuth)
        last = pts[-1]
        pts.append(np.array([last[0] + length * ux, last[1] + length * uy]))
    if len(pts) < 2:
        raise InvalidInputError(
            "a polyline needs at least one segment",
            user_message="Serve almeno un segmento per creare una polilinea.")
    return np.asarray(pts, dtype=float)


# --------------------------------------------------------------------------
# Rectangle and square
# --------------------------------------------------------------------------

def rectangle_from_center(center, width_m: float, height_m: float,
                          azimuth_deg: float = 0.0) -> np.ndarray:
    """Rectangle centred on ``center``.

    ``azimuth_deg`` is the bearing of the *height* axis; ``width`` is measured
    across it. Axes stay exactly orthogonal in the rotated frame, so the shape
    is a true rectangle at any bearing.
    """
    c = _point(center, "center")
    width_m = _positive(width_m, "width_m", "La larghezza")
    height_m = _positive(height_m, "height_m", "L'altezza")
    return rectangle_corners(c[0], c[1], 0.5 * width_m, 0.5 * height_m,
                             azimuth_deg)


def rectangle_from_corner(corner, width_m: float, height_m: float,
                          azimuth_deg: float = 0.0) -> np.ndarray:
    """Rectangle grown from one corner along the azimuth frame."""
    p0 = _point(corner, "corner")
    width_m = _positive(width_m, "width_m", "La larghezza")
    height_m = _positive(height_m, "height_m", "L'altezza")
    ux, uy = along_track_unit(azimuth_deg)
    vx, vy = across_track_unit(azimuth_deg)
    centre = np.array([p0[0] + 0.5 * (height_m * ux + width_m * vx),
                       p0[1] + 0.5 * (height_m * uy + width_m * vy)])
    return rectangle_corners(centre[0], centre[1], 0.5 * width_m,
                             0.5 * height_m, azimuth_deg)


def rectangle_from_opposite_corners(p1, p2,
                                    azimuth_deg: float = 0.0) -> np.ndarray:
    """Rectangle spanned by two opposite corners, in the azimuth frame.

    At azimuth 0 this is the familiar axis-aligned drag-rectangle. At any other
    bearing the two points are interpreted in the rotated frame, which is what
    makes a rotated drag behave the way a CAD user expects.
    """
    a = _point(p1, "p1")
    b = _point(p2, "p2")
    ux, uy = along_track_unit(azimuth_deg)
    vx, vy = across_track_unit(azimuth_deg)
    d = b - a
    height = d[0] * ux + d[1] * uy          # along the azimuth
    width = d[0] * vx + d[1] * vy           # across it
    if abs(width) < GEOM_EPS_M or abs(height) < GEOM_EPS_M:
        raise ConstraintError(
            "degenerate rectangle: width={0!r} height={1!r}".format(width, height),
            user_message="I due punti sono allineati: il rettangolo sarebbe degenere.",
            hint="Scegli due angoli non coincidenti e non allineati.")
    centre = a + 0.5 * np.array([height * ux + width * vx,
                                 height * uy + width * vy])
    return rectangle_corners(centre[0], centre[1], 0.5 * abs(width),
                             0.5 * abs(height), azimuth_deg)


def rectangle_from_base(p1, p2, height_m: float,
                        side: str = "left") -> np.ndarray:
    """Rectangle built on a base segment, extended by ``height_m``.

    The base ``p1 -> p2`` fixes both the width and the bearing; ``side``
    chooses which way the rectangle grows ("left" or "right" of the direction
    of travel).
    """
    a = _point(p1, "p1")
    b = _point(p2, "p2")
    base = b - a
    width = float(np.hypot(*base))
    if width < GEOM_EPS_M:
        raise ConstraintError(
            "base segment has zero length",
            user_message="Il segmento di base ha lunghezza nulla.")
    height_m = _positive(height_m, "height_m", "L'altezza")
    if side not in ("left", "right"):
        raise InvalidInputError(
            "side must be 'left' or 'right', got {0!r}".format(side),
            user_message="Il lato deve essere 'left' o 'right'.")

    # Unit normal, 90 deg to the left of the base direction.
    nx, ny = -base[1] / width, base[0] / width
    if side == "right":
        nx, ny = -nx, -ny
    c = a + 0.5 * base + 0.5 * height_m * np.array([nx, ny])
    azimuth = math.degrees(math.atan2(base[0], base[1])) % 360.0
    # The base runs across the azimuth frame, so width is the across dimension.
    return rectangle_corners(c[0], c[1], 0.5 * width, 0.5 * height_m,
                             (azimuth + 90.0) % 360.0)


def square_from(center, azimuth_deg: float = 0.0, *,
                side_m: Optional[float] = None,
                diagonal_m: Optional[float] = None,
                area_m2: Optional[float] = None,
                perimeter_m: Optional[float] = None) -> np.ndarray:
    """Square from exactly one of side / diagonal / area / perimeter."""
    given = {k: v for k, v in (("side_m", side_m), ("diagonal_m", diagonal_m),
                               ("area_m2", area_m2),
                               ("perimeter_m", perimeter_m))
             if v is not None}
    if len(given) != 1:
        raise InvalidInputError(
            "provide exactly one of side/diagonal/area/perimeter, got {0}"
            .format(sorted(given) or "none"),
            user_message="Indica esattamente uno fra lato, diagonale, area o "
                         "perimetro.")
    if side_m is not None:
        side = _positive(side_m, "side_m", "Il lato")
    elif diagonal_m is not None:
        side = _positive(diagonal_m, "diagonal_m", "La diagonale") / math.sqrt(2.0)
    elif area_m2 is not None:
        side = math.sqrt(_positive(area_m2, "area_m2", "L'area"))
    else:
        side = _positive(perimeter_m, "perimeter_m", "Il perimetro") / 4.0
    return rectangle_from_center(center, side, side, azimuth_deg)


# --------------------------------------------------------------------------
# Circle
# --------------------------------------------------------------------------

def circle_ring(center, radius_m: float,
                segments: int = CIRCLE_SEGMENTS) -> np.ndarray:
    """Inscribed regular polygon approximating a circle.

    At the default 72 segments the area is 0.13 % below the true circle, which
    is the usual planning tolerance. Raise ``segments`` where it matters;
    formats that support true curves should store the arc instead.
    """
    c = _point(center, "center")
    radius_m = _positive(radius_m, "radius_m", "Il raggio")
    if segments < 3:
        raise InvalidInputError(
            "segments must be >= 3, got {0!r}".format(segments),
            user_message="Servono almeno 3 segmenti per approssimare un cerchio.")
    ang = np.radians(np.linspace(0.0, 360.0, int(segments), endpoint=False))
    pts = np.column_stack([c[0] + radius_m * np.sin(ang),
                           c[1] + radius_m * np.cos(ang)])
    return close_ring(pts)


def circle_from_3_points(p1, p2, p3):
    """Circumcircle of three points. Returns ``(center, radius)``."""
    a, b, c = _point(p1, "p1"), _point(p2, "p2"), _point(p3, "p3")
    d = 2.0 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1])
               + c[0] * (a[1] - b[1]))
    if abs(d) < GEOM_EPS_M:
        raise ConstraintError(
            "the three points are collinear (determinant {0:g})".format(d),
            user_message="I tre punti sono allineati: non definiscono un cerchio.",
            hint="Sposta uno dei punti fuori dalla retta degli altri due.")
    sa = a[0] ** 2 + a[1] ** 2
    sb = b[0] ** 2 + b[1] ** 2
    sc = c[0] ** 2 + c[1] ** 2
    ux = (sa * (b[1] - c[1]) + sb * (c[1] - a[1]) + sc * (a[1] - b[1])) / d
    uy = (sa * (c[0] - b[0]) + sb * (a[0] - c[0]) + sc * (b[0] - a[0])) / d
    centre = np.array([ux, uy])
    return centre, float(np.hypot(*(a - centre)))


def circle_from_2_points_radius(p1, p2, radius_m: float, side: str = "left"):
    """Circle of a given radius through two points. Returns ``(center, radius)``.

    Two solutions exist, mirrored about the chord; ``side`` picks one relative
    to the direction ``p1 -> p2``.
    """
    a, b = _point(p1, "p1"), _point(p2, "p2")
    radius_m = _positive(radius_m, "radius_m", "Il raggio")
    chord = b - a
    dist = float(np.hypot(*chord))
    if dist < GEOM_EPS_M:
        raise ConstraintError(
            "the two points coincide",
            user_message="I due punti coincidono: il cerchio non e' definito.")
    half = 0.5 * dist
    if radius_m < half - GEOM_EPS_M:
        raise ConstraintError(
            "radius {0:g} is smaller than half the chord {1:g}".format(
                radius_m, half),
            user_message="Il raggio e' troppo piccolo per passare per i due punti.",
            hint="Il raggio minimo possibile e' {0:.3f} m.".format(half))
    offset = math.sqrt(max(radius_m ** 2 - half ** 2, 0.0))
    mid = a + 0.5 * chord
    nx, ny = -chord[1] / dist, chord[0] / dist
    if side == "right":
        nx, ny = -nx, -ny
    elif side != "left":
        raise InvalidInputError(
            "side must be 'left' or 'right', got {0!r}".format(side),
            user_message="Il lato deve essere 'left' o 'right'.")
    return mid + offset * np.array([nx, ny]), radius_m


def circle_radius_from_area(area_m2: float) -> float:
    return math.sqrt(_positive(area_m2, "area_m2", "L'area") / math.pi)


def circle_radius_from_circumference(circumference_m: float) -> float:
    return _positive(circumference_m, "circumference_m",
                     "La circonferenza") / (2.0 * math.pi)


# --------------------------------------------------------------------------
# Regular polygon and ellipse
# --------------------------------------------------------------------------

def regular_polygon(center, n_sides: int, azimuth_deg: float = 0.0, *,
                    radius_m: Optional[float] = None,
                    apothem_m: Optional[float] = None,
                    side_m: Optional[float] = None,
                    area_m2: Optional[float] = None) -> np.ndarray:
    """Regular n-gon from exactly one size constraint.

    ``radius_m`` is the circumradius. The first vertex sits at bearing
    ``azimuth_deg`` from the centre.

    Conversions used::

        R = apothem / cos(pi/n)
        R = side / (2 sin(pi/n))
        R = sqrt(2A / (n sin(2 pi/n)))
    """
    c = _point(center, "center")
    try:
        n = int(n_sides)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(
            "n_sides is not an integer: {0!r}".format(n_sides),
            user_message="Il numero di lati non e' un intero.") from exc
    if n < 3:
        raise InvalidInputError(
            "n_sides must be >= 3, got {0}".format(n),
            user_message="Un poligono regolare deve avere almeno 3 lati.")

    given = {k: v for k, v in (("radius_m", radius_m), ("apothem_m", apothem_m),
                               ("side_m", side_m), ("area_m2", area_m2))
             if v is not None}
    if len(given) != 1:
        raise InvalidInputError(
            "provide exactly one of radius/apothem/side/area, got {0}"
            .format(sorted(given) or "none"),
            user_message="Indica esattamente uno fra raggio, apotema, lato o area.")

    if radius_m is not None:
        radius = _positive(radius_m, "radius_m", "Il raggio")
    elif apothem_m is not None:
        radius = _positive(apothem_m, "apothem_m", "L'apotema") / math.cos(math.pi / n)
    elif side_m is not None:
        radius = _positive(side_m, "side_m", "Il lato") / (2.0 * math.sin(math.pi / n))
    else:
        area = _positive(area_m2, "area_m2", "L'area")
        radius = math.sqrt(2.0 * area / (n * math.sin(2.0 * math.pi / n)))

    ang = np.radians(azimuth_deg + np.arange(n) * (360.0 / n))
    pts = np.column_stack([c[0] + radius * np.sin(ang),
                           c[1] + radius * np.cos(ang)])
    return close_ring(pts)


def ellipse_ring(center, semi_major_m: float, semi_minor_m: float,
                 azimuth_deg: float = 0.0,
                 segments: int = CIRCLE_SEGMENTS) -> np.ndarray:
    """Ellipse with the major axis along ``azimuth_deg``."""
    c = _point(center, "center")
    a = _positive(semi_major_m, "semi_major_m", "Il semiasse maggiore")
    b = _positive(semi_minor_m, "semi_minor_m", "Il semiasse minore")
    if b > a:
        raise ConstraintError(
            "semi_minor {0:g} exceeds semi_major {1:g}".format(b, a),
            user_message="Il semiasse minore non puo' superare il maggiore.",
            hint="Scambia i due valori.")
    if segments < 8:
        raise InvalidInputError(
            "segments must be >= 8 for an ellipse, got {0!r}".format(segments),
            user_message="Servono almeno 8 segmenti per approssimare un'ellisse.")
    ux, uy = along_track_unit(azimuth_deg)      # major axis direction
    vx, vy = across_track_unit(azimuth_deg)     # minor axis direction
    t = np.linspace(0.0, 2.0 * math.pi, int(segments), endpoint=False)
    ca, sb = a * np.cos(t), b * np.sin(t)
    pts = np.column_stack([c[0] + ca * ux + sb * vx,
                           c[1] + ca * uy + sb * vy])
    return close_ring(pts)


# --------------------------------------------------------------------------
# Constraint solving on an existing chain
# --------------------------------------------------------------------------

def apply_length_constraint(p_from, p_to, length_m: float) -> np.ndarray:
    """Move ``p_to`` along the existing direction to an exact length."""
    a, b = _point(p_from, "p_from"), _point(p_to, "p_to")
    length_m = _positive(length_m, "length_m", "La lunghezza")
    d = b - a
    norm = float(np.hypot(*d))
    if norm < GEOM_EPS_M:
        raise ConstraintError(
            "cannot set the length of a zero-length segment",
            user_message="Il segmento ha lunghezza nulla: la direzione non e' definita.",
            hint="Indica prima un angolo o un secondo punto.")
    return a + d / norm * length_m


def apply_azimuth_constraint(p_from, p_to, azimuth_deg: float) -> np.ndarray:
    """Rotate ``p_to`` about ``p_from`` to an exact bearing, keeping length."""
    a, b = _point(p_from, "p_from"), _point(p_to, "p_to")
    length = float(np.hypot(*(b - a)))
    if length < GEOM_EPS_M:
        raise ConstraintError(
            "cannot set the azimuth of a zero-length segment",
            user_message="Il segmento ha lunghezza nulla: l'angolo non e' definito.")
    ux, uy = along_track_unit(azimuth_deg)
    return a + length * np.array([ux, uy])


def perpendicular_foot(point, seg_a, seg_b) -> np.ndarray:
    """Orthogonal projection of ``point`` onto the infinite line ``a-b``."""
    p = _point(point, "point")
    a, b = _point(seg_a, "seg_a"), _point(seg_b, "seg_b")
    d = b - a
    denom = float(d @ d)
    if denom < GEOM_EPS_M ** 2:
        raise ConstraintError(
            "cannot project onto a zero-length segment",
            user_message="Il segmento di riferimento ha lunghezza nulla.")
    t = float((p - a) @ d) / denom
    return a + t * d


def interior_angle(p_prev, p_vertex, p_next) -> float:
    """Interior angle at ``p_vertex``, in degrees, in [0, 360)."""
    a = _point(p_prev, "p_prev") - _point(p_vertex, "p_vertex")
    b = _point(p_next, "p_next") - _point(p_vertex, "p_vertex")
    if float(np.hypot(*a)) < GEOM_EPS_M or float(np.hypot(*b)) < GEOM_EPS_M:
        raise ConstraintError(
            "coincident vertices have no interior angle",
            user_message="Vertici coincidenti: l'angolo non e' definito.")
    ang = math.degrees(math.atan2(float(np.cross(a, b)), float(a @ b)))
    return ang % 360.0
