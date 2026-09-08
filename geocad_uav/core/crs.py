"""
CRS service: detect, warn, and move geometry into a metric working CRS.

Spec P1 is the whole point of this module: **no metric computation is ever done
in a geographic CRS.** A 1000 m line measured in EPSG:4326 comes out as roughly
0.009 -- of degrees -- and every downstream number (strip spacing, plant
density, GSD footprint) inherits that nonsense silently.

So every entry point that computes distances asks :func:`resolve_work_crs`
first. If the project is geographic, the caller gets a metric CRS suggestion
(the UTM zone of the data) plus a flag saying a transform is required; it is
then the GUI's job to ask the operator, and the service's job to transform
there and back.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .constants import GEODESIC_THRESHOLD_M
from .errors import CrsError, GeographicCrsError


def is_geographic(crs) -> bool:
    """True for a degree-based CRS. Returns False for an invalid CRS."""
    try:
        return bool(crs.isValid() and crs.isGeographic())
    except AttributeError as exc:
        raise CrsError("not a QgsCoordinateReferenceSystem: {0!r}".format(crs),
                       user_message="Sistema di riferimento non valido.") from exc


def utm_epsg_for(longitude: float, latitude: float) -> int:
    """EPSG code of the UTM zone containing a WGS84 lon/lat.

    Zones are 6 degrees wide starting at -180; northern hemisphere codes are
    326xx, southern 327xx. The Norway/Svalbard exceptions are deliberately not
    applied: they change the zone *boundary*, not the projection, and silently
    shifting an operator into a neighbouring zone is worse than the negligible
    scale difference at the edge.
    """
    if not (-180.0 <= longitude <= 180.0 and -90.0 <= latitude <= 90.0):
        raise CrsError(
            "longitude/latitude out of range: {0}, {1}".format(longitude, latitude),
            user_message="Coordinate geografiche fuori intervallo.")
    zone = int(math.floor((longitude + 180.0) / 6.0)) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if latitude >= 0 else 32700) + zone


def suggest_metric_crs(geom_or_rect, source_crs):
    """Suggest a metric working CRS for data in ``source_crs``.

    Uses the centroid of the data, transformed to WGS84, to pick a UTM zone.
    """
    from qgis.core import (QgsCoordinateReferenceSystem,        # noqa: PLC0415
                           QgsCoordinateTransform, QgsPointXY, QgsProject)

    if not is_geographic(source_crs):
        return source_crs

    try:
        rect = geom_or_rect.boundingBox()
    except AttributeError:
        rect = geom_or_rect
    cx = 0.5 * (rect.xMinimum() + rect.xMaximum())
    cy = 0.5 * (rect.yMinimum() + rect.yMaximum())

    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    if source_crs != wgs84:
        transform = QgsCoordinateTransform(source_crs, wgs84,
                                           QgsProject.instance())
        point = transform.transform(QgsPointXY(cx, cy))
        cx, cy = point.x(), point.y()

    crs = QgsCoordinateReferenceSystem("EPSG:{0}".format(utm_epsg_for(cx, cy)))
    if not crs.isValid():
        raise CrsError(
            "suggested UTM CRS is not available in this PROJ installation",
            user_message="Impossibile determinare un CRS metrico adatto.")
    return crs


@dataclass
class WorkCrsDecision:
    """The outcome of resolving a working CRS."""

    work_crs: object
    source_crs: object
    transform_required: bool
    reason: str = ""

    @property
    def authid(self) -> str:
        return self.work_crs.authid()


def resolve_work_crs(source_crs, geom_or_rect=None,
                     allow_geographic: bool = False) -> WorkCrsDecision:
    """Decide which CRS metric work should happen in.

    ``allow_geographic=True`` is the informed opt-out: the caller has told the
    operator what it means and the operator accepted. It is never the default.
    """
    if not source_crs or not source_crs.isValid():
        raise CrsError(
            "invalid source CRS",
            user_message="Il layer non ha un sistema di riferimento valido.",
            hint="Assegna un CRS al layer prima di procedere.")

    if not is_geographic(source_crs):
        return WorkCrsDecision(source_crs, source_crs, False,
                               "Il CRS di origine e' gia' proiettato metrico.")

    if allow_geographic:
        return WorkCrsDecision(
            source_crs, source_crs, False,
            "CRS geografico accettato esplicitamente dall'operatore: le "
            "misure metriche NON sono affidabili.")

    if geom_or_rect is None:
        raise GeographicCrsError(
            "geographic CRS {0} with no geometry to pick a UTM zone from"
            .format(source_crs.authid()),
            user_message="Il CRS {0} e' geografico e non e' stato possibile "
                         "dedurre una zona UTM.".format(source_crs.authid()))

    work = suggest_metric_crs(geom_or_rect, source_crs)
    return WorkCrsDecision(
        work, source_crs, True,
        "Il CRS {0} e' geografico: le distanze verrebbero calcolate in gradi. "
        "Si propone {1} come CRS metrico di lavoro.".format(
            source_crs.authid(), work.authid()))


def make_transform(src, dst, project=None):
    """A ``QgsCoordinateTransform`` using the project's transform context."""
    from qgis.core import QgsCoordinateTransform, QgsProject     # noqa: PLC0415

    if src == dst:
        return None
    project = project or QgsProject.instance()
    transform = QgsCoordinateTransform(src, dst, project)
    if not transform.isValid():
        raise CrsError(
            "no transform from {0} to {1}".format(src.authid(), dst.authid()),
            user_message="Trasformazione non disponibile da {0} a {1}.".format(
                src.authid(), dst.authid()),
            hint="Verifica l'installazione di PROJ e le griglie di conversione.")
    return transform


def transform_geometry(geom, src, dst, project=None):
    """Return a copy of ``geom`` in ``dst``. Returns the input when equal."""
    from qgis.core import QgsGeometry                            # noqa: PLC0415

    transform = make_transform(src, dst, project)
    if transform is None:
        return geom
    clone = QgsGeometry(geom)
    if clone.transform(transform) != 0:
        raise CrsError(
            "geometry transform failed from {0} to {1}".format(
                src.authid(), dst.authid()),
            user_message="Trasformazione della geometria non riuscita.")
    return clone


def wgs84_transform(src, project=None):
    """Transform from ``src`` to EPSG:4326, for export writers."""
    from qgis.core import QgsCoordinateReferenceSystem           # noqa: PLC0415
    return make_transform(src, QgsCoordinateReferenceSystem("EPSG:4326"), project)


def needs_geodesic(geom_or_rect) -> bool:
    """True when the extent is large enough that planar UTM error matters.

    A single UTM zone holds sub-decimetre planar accuracy over a few kilometres.
    Past ``GEODESIC_THRESHOLD_M`` the scale factor across the zone starts to
    show, and measurement should be done on the ellipsoid instead.
    """
    try:
        rect = geom_or_rect.boundingBox()
    except AttributeError:
        rect = geom_or_rect
    return max(rect.width(), rect.height()) > GEODESIC_THRESHOLD_M


def describe(crs) -> str:
    if crs is None or not crs.isValid():
        return "CRS non valido"
    kind = "geografico (gradi)" if crs.isGeographic() else "proiettato metrico"
    return "{0} - {1} [{2}]".format(crs.authid(), crs.description(), kind)
