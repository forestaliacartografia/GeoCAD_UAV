"""
Geometry modifiers: offset, trim, extend, fillet, chamfer, explode, join, split.

Affine operations (move/rotate/scale/mirror/array) live in
``core.transform2d`` and are pure numpy; this module holds the operations that
either need GEOS (offset, split, union) or are corner-local constructions
(fillet, chamfer, trim, extend).

Failure is explicit. A fillet radius that does not fit the corner raises with
the largest radius that would, rather than producing a self-intersecting ring
that GEOS will reject three steps later.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ..core.constants import BUFFER_SEGMENTS, GEOM_EPS_M
from ..core.errors import ConstraintError, GeometryError, InvalidInputError

# Offset behaviour for closed geometry.
OFFSET_BUFFER = "buffer"        # grow/shrink the polygon (area changes)
OFFSET_CURVE = "curve"          # displace the boundary as a line


def _as_array(points) -> np.ndarray:
    arr = np.asarray(points, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2:
        raise InvalidInputError(
            "expected an (N, 2) array, got {0!r}".format(
                getattr(arr, "shape", None)),
            user_message="Geometria non valida.")
    return arr[:, :2]


# --------------------------------------------------------------------------
# Offset
# --------------------------------------------------------------------------

def offset_geometry(geom, distance_m: float, mode: str = OFFSET_CURVE,
                    segments: int = BUFFER_SEGMENTS, join_style=None):
    """Offset a geometry by ``distance_m``.

    For a polygon the two modes are genuinely different operations and the
    caller must choose: ``buffer`` grows or shrinks the *area*, ``curve``
    displaces the boundary as an open line. Conflating them is a classic source
    of "why is my parcel now a ring".
    """
    from qgis.core import Qgis, QgsGeometry, QgsWkbTypes         # noqa: PLC0415

    if geom is None or geom.isEmpty():
        raise GeometryError("empty geometry",
                            user_message="Geometria vuota.")
    if abs(distance_m) < GEOM_EPS_M:
        return QgsGeometry(geom)

    if join_style is None:
        join_style = Qgis.JoinStyle.Round

    gtype = QgsWkbTypes.geometryType(geom.wkbType())
    if gtype == QgsWkbTypes.GeometryType.PolygonGeometry and mode == OFFSET_BUFFER:
        result = geom.buffer(distance_m, segments)
    elif gtype == QgsWkbTypes.GeometryType.LineGeometry:
        result = geom.offsetCurve(distance_m, segments, join_style, 2.0)
    else:
        boundary = geom.constGet().boundary()
        if boundary is None:
            raise GeometryError(
                "geometry has no boundary to offset",
                user_message="La geometria non ha un contorno da offsettare.")
        result = QgsGeometry(boundary.clone()).offsetCurve(
            distance_m, segments, join_style, 2.0)

    if result is None or result.isEmpty():
        raise ConstraintError(
            "offset by {0} collapsed the geometry".format(distance_m),
            user_message="L'offset di {0:g} m annulla la geometria.".format(
                distance_m),
            hint="Riduci la distanza di offset.")
    return result


# --------------------------------------------------------------------------
# Fillet and chamfer
# --------------------------------------------------------------------------

def corner_vectors(prev_pt, vertex, next_pt):
    """Unit vectors from the vertex towards its neighbours, and the angle."""
    v = np.asarray(vertex, dtype=float)[:2]
    a = np.asarray(prev_pt, dtype=float)[:2] - v
    b = np.asarray(next_pt, dtype=float)[:2] - v
    la, lb = float(np.hypot(*a)), float(np.hypot(*b))
    if la < GEOM_EPS_M or lb < GEOM_EPS_M:
        raise ConstraintError(
            "coincident vertices at the corner",
            user_message="Vertici coincidenti: l'angolo non e' definito.")
    ua, ub = a / la, b / lb
    cos_theta = float(np.clip(ua @ ub, -1.0, 1.0))
    theta = math.acos(cos_theta)
    return ua, ub, la, lb, theta


def max_fillet_radius(prev_pt, vertex, next_pt) -> float:
    """Largest radius that fits this corner without overrunning either leg."""
    ua, ub, la, lb, theta = corner_vectors(prev_pt, vertex, next_pt)
    if theta < 1e-9 or abs(theta - math.pi) < 1e-9:
        return 0.0
    return min(la, lb) * math.tan(theta / 2.0)


def fillet_corner(prev_pt, vertex, next_pt, radius_m: float,
                  segments: int = 12):
    """Round one corner. Returns the arc points replacing the vertex.

    Construction: with the half-angle ``theta/2`` between the two legs, the
    tangent points sit at ``t = r / tan(theta/2)`` from the vertex along each
    leg, and the arc centre at ``r / sin(theta/2)`` along the angle bisector.
    """
    if radius_m <= 0:
        raise InvalidInputError(
            "fillet radius must be > 0, got {0!r}".format(radius_m),
            user_message="Il raggio di raccordo deve essere maggiore di zero.")
    ua, ub, la, lb, theta = corner_vectors(prev_pt, vertex, next_pt)
    if theta < 1e-6:
        raise ConstraintError(
            "the corner is degenerate (legs are collinear and folded back)",
            user_message="L'angolo e' degenere: non e' raccordabile.")
    if abs(theta - math.pi) < 1e-6:
        raise ConstraintError(
            "the corner is a straight line, there is nothing to fillet",
            user_message="I due segmenti sono allineati: non c'e' angolo da "
                         "raccordare.")

    half = theta / 2.0
    tangent = radius_m / math.tan(half)
    if tangent > la + 1e-9 or tangent > lb + 1e-9:
        largest = min(la, lb) * math.tan(half)
        raise ConstraintError(
            "fillet radius {0:g} needs {1:g} m of leg but only {2:g} is "
            "available".format(radius_m, tangent, min(la, lb)),
            user_message="Raggio di raccordo troppo grande per questo angolo.",
            hint="Il raggio massimo qui e' {0:.3f} m.".format(largest))

    v = np.asarray(vertex, dtype=float)[:2]
    t1 = v + ua * tangent
    t2 = v + ub * tangent

    bisector = ua + ub
    norm = float(np.hypot(*bisector))
    if norm < 1e-12:
        raise ConstraintError(
            "cannot build the angle bisector for this corner",
            user_message="Angolo non raccordabile.")
    centre = v + (bisector / norm) * (radius_m / math.sin(half))

    start = math.atan2(t1[1] - centre[1], t1[0] - centre[0])
    end = math.atan2(t2[1] - centre[1], t2[0] - centre[0])
    sweep = (end - start + math.pi) % (2.0 * math.pi) - math.pi
    angles = start + sweep * np.linspace(0.0, 1.0, max(int(segments), 2))
    return np.column_stack([centre[0] + radius_m * np.cos(angles),
                            centre[1] + radius_m * np.sin(angles)])


def chamfer_corner(prev_pt, vertex, next_pt, distance_a: float,
                   distance_b: Optional[float] = None):
    """Cut one corner. Returns the two points replacing the vertex."""
    distance_b = distance_a if distance_b is None else distance_b
    for name, value in (("distance_a", distance_a), ("distance_b", distance_b)):
        if value <= 0:
            raise InvalidInputError(
                "{0} must be > 0, got {1!r}".format(name, value),
                user_message="Le distanze di smusso devono essere maggiori di "
                             "zero.")
    ua, ub, la, lb, _ = corner_vectors(prev_pt, vertex, next_pt)
    if distance_a > la + 1e-9 or distance_b > lb + 1e-9:
        raise ConstraintError(
            "chamfer distances exceed the available legs ({0:g}, {1:g})".format(
                la, lb),
            user_message="Le distanze di smusso superano la lunghezza dei lati.",
            hint="Massimi qui: {0:.3f} m e {1:.3f} m.".format(la, lb))
    v = np.asarray(vertex, dtype=float)[:2]
    return np.vstack([v + ua * distance_a, v + ub * distance_b])


def fillet_polyline(points, radius_m: float, closed: bool = False,
                    segments: int = 12, skip_impossible: bool = True):
    """Fillet every corner of a polyline. Returns ``(points, skipped)``.

    Two feasibility tests are applied, and the second is the one that matters:

    1. Per corner -- the tangent length ``r / tan(theta/2)`` must fit within
       each of the two legs.
    2. Per **edge** -- the tangents consumed from the two ends of a shared edge
       must together fit inside it. Checking only (1) lets two adjacent fillets
       each claim most of the same side, and the arcs then cross: the result is
       a self-intersecting ring that GEOS rejects later, far from the cause.
       Where an edge is over-subscribed the greedier corner is dropped and
       counted, and the check repeats until every edge fits.

    ``skip_impossible`` leaves the offending corners square rather than failing
    the whole operation, which is what makes the tool usable on real parcel
    boundaries; set it False to get a hard error instead.
    """
    arr = _as_array(points)
    n = arr.shape[0]
    if closed and n > 2 and np.hypot(*(arr[0] - arr[-1])) < GEOM_EPS_M:
        arr = arr[:-1]
        n -= 1
    if n < 3:
        return arr.copy(), 0

    corners = list(range(n)) if closed else list(range(1, n - 1))
    tangents = {}
    usable = {}
    for i in corners:
        try:
            _ua, _ub, la, lb, theta = corner_vectors(
                arr[(i - 1) % n], arr[i], arr[(i + 1) % n])
            if theta < 1e-6 or abs(theta - math.pi) < 1e-6:
                raise ConstraintError("degenerate corner")
            tangent = radius_m / math.tan(theta / 2.0)
            tangents[i] = tangent
            usable[i] = tangent <= la + 1e-9 and tangent <= lb + 1e-9
        except ConstraintError:
            tangents[i] = math.inf
            usable[i] = False

    edges = ([(i, (i + 1) % n) for i in range(n)] if closed
             else [(i, i + 1) for i in range(n - 1)])
    changed = True
    while changed:
        changed = False
        for a, b in edges:
            ta = tangents.get(a, 0.0) if usable.get(a, False) else 0.0
            tb = tangents.get(b, 0.0) if usable.get(b, False) else 0.0
            if ta + tb <= float(np.hypot(*(arr[b] - arr[a]))) + 1e-9:
                continue
            victim = a if ta >= tb else b
            if usable.get(victim, False):
                usable[victim] = False
                changed = True

    skipped = sum(1 for i in corners if not usable[i])
    if skipped and not skip_impossible:
        raise ConstraintError(
            "{0} corners cannot take a {1:g} m fillet".format(skipped, radius_m),
            user_message="{0} angoli non possono ricevere un raccordo di "
                         "{1:g} m.".format(skipped, radius_m),
            hint="Riduci il raggio oppure raccorda un angolo alla volta.")

    out = []
    if not closed:
        out.append(arr[0])
    for i in corners:
        if usable[i]:
            out.extend(fillet_corner(arr[(i - 1) % n], arr[i],
                                     arr[(i + 1) % n], radius_m, segments))
        else:
            out.append(arr[i])
    if not closed:
        out.append(arr[-1])
    result = np.asarray(out, dtype=float)
    if closed:
        result = np.vstack([result, result[0]])
    return result, skipped


# --------------------------------------------------------------------------
# Trim and extend
# --------------------------------------------------------------------------

def line_intersection(a1, a2, b1, b2, segment_only: bool = False):
    """Intersection of two infinite lines (or segments). None when parallel."""
    p = np.asarray(a1, dtype=float)[:2]
    r = np.asarray(a2, dtype=float)[:2] - p
    q = np.asarray(b1, dtype=float)[:2]
    s = np.asarray(b2, dtype=float)[:2] - q
    denom = float(np.cross(r, s))
    if abs(denom) < 1e-12:
        return None
    t = float(np.cross(q - p, s)) / denom
    u = float(np.cross(q - p, r)) / denom
    if segment_only and not (0.0 <= t <= 1.0 and 0.0 <= u <= 1.0):
        return None
    return p + t * r


def extend_to(points, boundary_a, boundary_b, at_end: bool = True):
    """Extend the first or last segment until it meets a boundary line."""
    arr = _as_array(points).copy()
    if arr.shape[0] < 2:
        raise InvalidInputError(
            "need at least two points to extend",
            user_message="Servono almeno due punti per estendere.")
    if at_end:
        a, b = arr[-2], arr[-1]
    else:
        a, b = arr[1], arr[0]
    hit = line_intersection(a, b, boundary_a, boundary_b)
    if hit is None:
        raise ConstraintError(
            "the segment is parallel to the boundary",
            user_message="Il segmento e' parallelo al limite: non si incontrano.",
            hint="Scegli un limite non parallelo.")
    direction = b - a
    if float(direction @ (hit - b)) < 0:
        raise ConstraintError(
            "the boundary lies behind the segment",
            user_message="Il limite si trova dietro al segmento: estensione "
                         "non possibile in quella direzione.")
    if at_end:
        arr[-1] = hit
    else:
        arr[0] = hit
    return arr


def trim_to(points, boundary_a, boundary_b, at_end: bool = True):
    """Cut the first or last segment back to where it crosses a boundary."""
    arr = _as_array(points).copy()
    if arr.shape[0] < 2:
        raise InvalidInputError(
            "need at least two points to trim",
            user_message="Servono almeno due punti per tagliare.")
    if at_end:
        a, b = arr[-2], arr[-1]
    else:
        a, b = arr[1], arr[0]
    hit = line_intersection(a, b, boundary_a, boundary_b, segment_only=True)
    if hit is None:
        raise ConstraintError(
            "the boundary does not cross this segment",
            user_message="Il limite non interseca il segmento da tagliare.")
    if at_end:
        arr[-1] = hit
    else:
        arr[0] = hit
    return arr


# --------------------------------------------------------------------------
# Explode, join, split, align
# --------------------------------------------------------------------------

def explode(geom):
    """Break a geometry into its individual two-point segments."""
    from qgis.core import QgsGeometry, QgsPointXY                # noqa: PLC0415

    out = []
    for part in (geom.asGeometryCollection() if geom.isMultipart() else [geom]):
        vertices = [(v.x(), v.y()) for v in part.vertices()]
        for a, b in zip(vertices, vertices[1:]):
            if math.hypot(b[0] - a[0], b[1] - a[1]) < GEOM_EPS_M:
                continue
            out.append(QgsGeometry.fromPolylineXY(
                [QgsPointXY(*a), QgsPointXY(*b)]))
    return out


def join(geometries, tolerance_m: float = GEOM_EPS_M):
    """Merge touching lines into the longest possible chains."""
    from qgis.core import QgsGeometry                            # noqa: PLC0415

    if not geometries:
        raise InvalidInputError("nothing to join",
                                user_message="Nessuna geometria da unire.")
    merged = QgsGeometry.unaryUnion(list(geometries))
    if merged is None or merged.isEmpty():
        raise GeometryError("union produced nothing",
                            user_message="L'unione non ha prodotto geometrie.")
    return merged.mergeLines() if merged.type() == 1 else merged


def split(geom, blade_points):
    """Split a geometry with a cutting line. Returns the resulting parts."""
    from qgis.core import Qgis, QgsGeometry, QgsPointXY          # noqa: PLC0415

    blade = [QgsPointXY(float(p[0]), float(p[1]))
             for p in _as_array(blade_points)]
    if len(blade) < 2:
        raise InvalidInputError(
            "a blade needs at least two points",
            user_message="La linea di taglio deve avere almeno due punti.")
    clone = QgsGeometry(geom)
    result = clone.splitGeometry(blade, False)
    # splitGeometry returns (operationResult, newGeometries, topologyPoints)
    code, extra = result[0], result[1]
    success = getattr(Qgis, "GeometryOperationResult", None)
    ok = (code == success.Success) if success is not None else (code == 0)
    if not ok:
        raise ConstraintError(
            "splitGeometry returned {0}".format(code),
            user_message="Il taglio non e' riuscito.",
            hint="La linea di taglio deve attraversare completamente la "
                 "geometria.")
    return [clone] + list(extra)


def align(geometries, edge: str = "left"):
    """Align geometries to a common edge of their collective bounding box."""
    from qgis.core import QgsGeometry                            # noqa: PLC0415

    if not geometries:
        return []
    boxes = [g.boundingBox() for g in geometries]
    x_min = min(b.xMinimum() for b in boxes)
    x_max = max(b.xMaximum() for b in boxes)
    y_min = min(b.yMinimum() for b in boxes)
    y_max = max(b.yMaximum() for b in boxes)
    cx = 0.5 * (x_min + x_max)
    cy = 0.5 * (y_min + y_max)

    out = []
    for geom, box in zip(geometries, boxes):
        dx = dy = 0.0
        if edge == "left":
            dx = x_min - box.xMinimum()
        elif edge == "right":
            dx = x_max - box.xMaximum()
        elif edge == "top":
            dy = y_max - box.yMaximum()
        elif edge == "bottom":
            dy = y_min - box.yMinimum()
        elif edge == "center_x":
            dx = cx - box.center().x()
        elif edge == "center_y":
            dy = cy - box.center().y()
        else:
            raise InvalidInputError(
                "unknown alignment edge {0!r}".format(edge),
                user_message="Allineamento non riconosciuto: '{0}'.".format(edge))
        clone = QgsGeometry(geom)
        clone.translate(dx, dy)
        out.append(clone)
    return out
