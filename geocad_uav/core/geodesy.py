"""
Ellipsoidal measurement, wrapping ``QgsDistanceArea``.

Planar maths in a projected CRS (``core.planar``) is right for everything a
survey or a planting scheme does: those live inside one UTM zone where the
scale error is millimetric. This module is for the cases where that stops being
true -- an AOI spanning tens of kilometres, or an operator who explicitly wants
geodesic figures -- and for reporting a length in both flavours so the
difference is visible rather than assumed away.

Nothing here reimplements geodesy: ``QgsDistanceArea`` already carries the
project's ellipsoid and transform context (spec P9).
"""

from __future__ import annotations

import math
from typing import Optional

from .errors import CrsError


def distance_area(crs, ellipsoid: Optional[str] = None, project=None):
    """A configured ``QgsDistanceArea`` for ``crs``.

    ``ellipsoid`` defaults to the project's; passing ``"NONE"`` forces planar
    measurement, which is what the caller wants when it has already moved the
    data into a metric CRS.
    """
    from qgis.core import QgsDistanceArea, QgsProject            # noqa: PLC0415

    project = project or QgsProject.instance()
    measure = QgsDistanceArea()
    measure.setSourceCrs(crs, project.transformContext())
    chosen = ellipsoid if ellipsoid is not None else project.ellipsoid()
    if chosen and chosen != "NONE":
        if not measure.setEllipsoid(chosen):
            raise CrsError(
                "unknown ellipsoid {0!r}".format(chosen),
                user_message="Ellissoide non riconosciuto: '{0}'.".format(chosen),
                hint="Imposta un ellissoide valido nelle proprieta' del progetto.")
    return measure


def geodesic_length(geom, crs, ellipsoid: Optional[str] = None,
                    project=None) -> float:
    """Length in metres on the ellipsoid."""
    from qgis.core import QgsUnitTypes                           # noqa: PLC0415

    measure = distance_area(crs, ellipsoid, project)
    raw = measure.measureLength(geom)
    return measure.convertLengthMeasurement(
        raw, QgsUnitTypes.DistanceMeters)


def geodesic_area(geom, crs, ellipsoid: Optional[str] = None,
                  project=None) -> float:
    """Area in square metres on the ellipsoid."""
    from qgis.core import QgsUnitTypes                           # noqa: PLC0415

    measure = distance_area(crs, ellipsoid, project)
    raw = measure.measureArea(geom)
    return measure.convertAreaMeasurement(raw, QgsUnitTypes.AreaSquareMeters)


def geodesic_bearing(p1, p2, crs, ellipsoid: Optional[str] = None,
                     project=None) -> float:
    """Forward azimuth from p1 to p2, compass degrees clockwise from north."""
    measure = distance_area(crs, ellipsoid, project)
    return math.degrees(measure.bearing(p1, p2)) % 360.0


def compare_planar_geodesic(geom, crs, project=None):
    """(planar_m, geodesic_m, relative_difference).

    Reported in the mission and planting summaries whenever an AOI is large
    enough for the two to diverge: the operator should see the number, not be
    told to trust one of them.
    """
    planar = float(geom.length())
    geodesic = geodesic_length(geom, crs, project=project)
    if geodesic <= 0:
        return planar, geodesic, 0.0
    return planar, geodesic, abs(planar - geodesic) / geodesic
