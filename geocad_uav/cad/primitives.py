"""
QGIS adapter for the CAD primitives.

``core.geometry_engine`` does the mathematics and returns coordinate arrays;
this module turns those into ``QgsGeometry`` and attaches the
:class:`~..core.models.ParametricRecord` that lets the shape be regenerated
later from its numbers instead of being redrawn.

Keeping the split means the geometry can be verified without QGIS, and the
QGIS layer here is thin enough to read in one sitting.
"""

from __future__ import annotations

import hashlib
import math
from typing import Optional

import numpy as np

from ..core import geometry_engine as ge
from ..core.constants import CIRCLE_SEGMENTS
from ..core.errors import InvalidInputError
from ..core.models import ParametricRecord

# Tool identifiers, also used as the ``tool`` field of a ParametricRecord.
TOOL_POINT = "point"
TOOL_LINE = "line"
TOOL_POLYLINE = "polyline"
TOOL_RECTANGLE = "rectangle"
TOOL_SQUARE = "square"
TOOL_CIRCLE = "circle"
TOOL_POLYGON = "polygon"
TOOL_ELLIPSE = "ellipse"
TOOL_ARC = "arc"

TOOL_LABELS = {
    TOOL_POINT: "Punto", TOOL_LINE: "Linea", TOOL_POLYLINE: "Polilinea",
    TOOL_RECTANGLE: "Rettangolo", TOOL_SQUARE: "Quadrato",
    TOOL_CIRCLE: "Cerchio", TOOL_POLYGON: "Poligono regolare",
    TOOL_ELLIPSE: "Ellisse", TOOL_ARC: "Arco",
}

_CLOSED_TOOLS = (TOOL_RECTANGLE, TOOL_SQUARE, TOOL_CIRCLE, TOOL_POLYGON,
                 TOOL_ELLIPSE)


# --------------------------------------------------------------------------
# Coordinates -> QgsGeometry
# --------------------------------------------------------------------------

def points_to_polygon(ring, z: Optional[float] = None):
    """Closed ring -> ``QgsGeometry`` polygon, with Z when supplied."""
    from qgis.core import (QgsGeometry, QgsLineString,           # noqa: PLC0415
                           QgsPoint, QgsPointXY, QgsPolygon)

    arr = ge.close_ring(np.asarray(ring, dtype=float))
    if z is None:
        return QgsGeometry.fromPolygonXY(
            [[QgsPointXY(float(p[0]), float(p[1])) for p in arr]])
    polygon = QgsPolygon()
    polygon.setExteriorRing(QgsLineString(
        [QgsPoint(float(p[0]), float(p[1]), float(z)) for p in arr]))
    return QgsGeometry(polygon)


def points_to_line(points, z: Optional[float] = None):
    """Open polyline -> ``QgsGeometry`` linestring, with Z when supplied."""
    from qgis.core import (QgsGeometry, QgsLineString,           # noqa: PLC0415
                           QgsPoint, QgsPointXY)

    arr = np.asarray(points, dtype=float)
    if arr.shape[0] < 2:
        raise InvalidInputError(
            "a line needs at least two points",
            user_message="Servono almeno due punti per creare una linea.")
    if z is None:
        return QgsGeometry.fromPolylineXY(
            [QgsPointXY(float(p[0]), float(p[1])) for p in arr])
    return QgsGeometry(QgsLineString(
        [QgsPoint(float(p[0]), float(p[1]), float(z)) for p in arr]))


def geometry_signature(geom) -> str:
    """Stable fingerprint of a geometry, for detecting external vertex edits.

    Rounded to a millimetre before hashing so that a re-projection round trip
    or a WKB rewrite does not read as "the operator edited this".
    """
    if geom is None or geom.isEmpty():
        return ""
    coords = [(round(v.x(), 3), round(v.y(), 3)) for v in geom.vertices()]
    payload = ";".join("{0},{1}".format(x, y) for x, y in coords)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------

def _ring_for(tool: str, params: dict):
    """Dispatch to the pure engine. Returns (coords, is_closed)."""
    if tool == TOOL_POINT:
        return np.array([[float(params["x"]), float(params["y"])]]), False

    if tool == TOOL_LINE:
        if "length_m" in params and params["length_m"] is not None:
            return ge.line_from_length_azimuth(
                (params["x"], params["y"]), params["length_m"],
                params.get("azimuth_deg", 0.0)), False
        return np.array([[float(params["x"]), float(params["y"])],
                         [float(params["x2"]), float(params["y2"])]]), False

    if tool == TOOL_POLYLINE:
        segments = params.get("segments")
        if segments:
            return ge.polyline_from_segments((params["x"], params["y"]),
                                             segments), False
        return np.asarray(params["points"], dtype=float), False

    if tool == TOOL_RECTANGLE:
        mode = params.get("mode", "center")
        azimuth = params.get("azimuth_deg", 0.0)
        if mode == "center":
            return ge.rectangle_from_center(
                (params["x"], params["y"]), params["width_m"],
                params["height_m"], azimuth), True
        if mode == "corner":
            return ge.rectangle_from_corner(
                (params["x"], params["y"]), params["width_m"],
                params["height_m"], azimuth), True
        if mode == "opposite":
            return ge.rectangle_from_opposite_corners(
                (params["x"], params["y"]), (params["x2"], params["y2"]),
                azimuth), True
        if mode == "base":
            return ge.rectangle_from_base(
                (params["x"], params["y"]), (params["x2"], params["y2"]),
                params["height_m"], params.get("side", "left")), True
        raise InvalidInputError(
            "unknown rectangle mode {0!r}".format(mode),
            user_message="Modalita' rettangolo non riconosciuta.")

    if tool == TOOL_SQUARE:
        return ge.square_from(
            (params["x"], params["y"]), params.get("azimuth_deg", 0.0),
            side_m=params.get("side_m"), diagonal_m=params.get("diagonal_m"),
            area_m2=params.get("area_m2"),
            perimeter_m=params.get("perimeter_m")), True

    if tool == TOOL_CIRCLE:
        segments = int(params.get("segments", CIRCLE_SEGMENTS))
        mode = params.get("mode", "center_radius")
        if mode == "center_radius":
            radius = params.get("radius_m")
            if radius is None and params.get("diameter_m") is not None:
                radius = float(params["diameter_m"]) / 2.0
            if radius is None and params.get("area_m2") is not None:
                radius = ge.circle_radius_from_area(params["area_m2"])
            if radius is None and params.get("circumference_m") is not None:
                radius = ge.circle_radius_from_circumference(
                    params["circumference_m"])
            if radius is None:
                raise InvalidInputError(
                    "circle needs a radius, diameter, area or circumference",
                    user_message="Indica raggio, diametro, area o circonferenza.")
            return ge.circle_ring((params["x"], params["y"]), radius,
                                  segments), True
        if mode == "three_points":
            centre, radius = ge.circle_from_3_points(
                (params["x"], params["y"]), (params["x2"], params["y2"]),
                (params["x3"], params["y3"]))
            return ge.circle_ring(centre, radius, segments), True
        if mode == "two_points_radius":
            centre, radius = ge.circle_from_2_points_radius(
                (params["x"], params["y"]), (params["x2"], params["y2"]),
                params["radius_m"], params.get("side", "left"))
            return ge.circle_ring(centre, radius, segments), True
        raise InvalidInputError(
            "unknown circle mode {0!r}".format(mode),
            user_message="Modalita' cerchio non riconosciuta.")

    if tool == TOOL_POLYGON:
        return ge.regular_polygon(
            (params["x"], params["y"]), int(params["n_sides"]),
            params.get("azimuth_deg", 0.0),
            radius_m=params.get("radius_m"), apothem_m=params.get("apothem_m"),
            side_m=params.get("side_m"), area_m2=params.get("area_m2")), True

    if tool == TOOL_ELLIPSE:
        return ge.ellipse_ring(
            (params["x"], params["y"]), params["semi_major_m"],
            params["semi_minor_m"], params.get("azimuth_deg", 0.0),
            int(params.get("segments", CIRCLE_SEGMENTS))), True

    if tool == TOOL_ARC:
        # An arc is a line, not a ring: closed=False, so build() routes it
        # through points_to_line and it lands on a LineString layer.
        return ge.arc_ring(
            (params["x"], params["y"]), params["radius_m"],
            params["start_az"], params["end_az"],
            params.get("segments")), False

    raise InvalidInputError(
        "unknown tool {0!r}".format(tool),
        user_message="Strumento non riconosciuto: '{0}'.".format(tool))


def build(tool: str, params: dict, crs_authid: str = "",
          z: Optional[float] = None):
    """Create a geometry and its parametric record.

    Returns ``(QgsGeometry, ParametricRecord)``. The record carries a geometry
    signature so a later edit outside the plugin can be detected instead of
    being silently overwritten.
    """
    from qgis.core import QgsGeometry, QgsPoint, QgsPointXY      # noqa: PLC0415

    coords, closed = _ring_for(tool, params)

    if tool == TOOL_POINT:
        x, y = float(coords[0][0]), float(coords[0][1])
        geom = (QgsGeometry(QgsPoint(x, y, float(z))) if z is not None
                else QgsGeometry.fromPointXY(QgsPointXY(x, y)))
    elif closed:
        geom = points_to_polygon(coords, z)
    else:
        geom = points_to_line(coords, z)

    stored = dict(params)
    stored["_signature"] = geometry_signature(geom)
    stored.update(measure(geom, tool))
    record = ParametricRecord(tool=tool, params=stored, crs_authid=crs_authid)
    return geom, record


def measure(geom, tool: str) -> dict:
    """Denormalised measurements stored alongside the parameters.

    Deliberately prefixed ``measured_``: ``area_m2``, ``perimeter_m`` and
    ``length_m`` are all legitimate *input* parameters (a square from its area,
    a line from its length), so writing the measured values under those names
    would make :func:`rebuild` read an output back as an input and silently
    change the shape.
    """
    out = {}
    if geom is None or geom.isEmpty():
        return out
    if tool in _CLOSED_TOOLS:
        out["measured_area_m2"] = round(float(geom.area()), 6)
        out["measured_perimeter_m"] = round(float(geom.length()), 6)
    elif tool in (TOOL_LINE, TOOL_POLYLINE, TOOL_ARC):
        out["measured_length_m"] = round(float(geom.length()), 6)
    return out


def input_params(record: ParametricRecord) -> dict:
    """Just the construction inputs, with measurements and metadata stripped."""
    return {k: v for k, v in record.params.items()
            if not k.startswith("_") and not k.startswith("measured_")}


def rebuild(record: ParametricRecord, z: Optional[float] = None):
    """Regenerate a geometry from a stored record."""
    geom, fresh = build(record.tool, input_params(record), record.crs_authid, z)
    record.params.update(fresh.params)
    record.broken = False
    return geom, record
