"""
M04 -- planting rows that follow the contours.

On a hillside the row that makes sense is the one that stays at the same
height: it holds the soil, it can be walked, and a machine can work it. That
row is a contour line, and a contour line is not something to re-derive here.
QGIS ships ``gdal:contour``, which is GDAL's own contouring; this module runs
it and then does the part that is actually about planting -- spacing the
plants along the line it gets back.

The spacing along a contour is corrected the same way as everywhere else in
this package, but with the *directional* slope: a contour is by construction
almost level, so the correction along it is small and honest, where applying
the maximum local slope would shorten every step by the full hillside angle
and plant far too many trees.

Walking the line is ``QgsGeometry.interpolate``: GEOS measures the distance
along the real geometry, bends included, so nothing here approximates a
polyline by its chords.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from ...core.constants import GEOM_EPS_M
from ...core.errors import InvalidInputError, RasterError
from ...core.models import PlantingRecord
from ...core.planar import azimuth_of
from .spacing import STEP_DIRECTIONAL, SlopeStepper

#: Vertical distance between two contours, in metres. A parameter of the
#: project: on a gentle slope 2 m says something and 10 m says nothing.
DEFAULT_INTERVAL_M = 10.0

#: Field ``gdal:contour`` writes the elevation into.
ELEVATION_FIELD = "ELEV"


@dataclass
class ContourRow:
    """One contour, as a planting row."""

    elevation_m: float
    geometry: object                    # QgsGeometry, a LineString
    length_m: float = 0.0
    plants: "list[PlantingRecord]" = field(default_factory=list)


def extract_contours(raster_layer, interval_m: float = DEFAULT_INTERVAL_M,
                     band: int = 1, output_path: str = "TEMPORARY_OUTPUT",
                     offset_m: float = 0.0, feedback=None):
    """Run ``gdal:contour`` over a DEM and return the resulting layer.

    The algorithm is GDAL's, reached through the Processing framework, which
    is how QGIS itself contours a raster. Nothing is re-implemented; what
    this function adds is the refusal to run with parameters that cannot
    produce anything, and an error an operator can read.
    """
    if raster_layer is None:
        raise RasterError(
            "no DEM to contour",
            user_message="Nessun layer DEM selezionato.")
    interval = float(interval_m)
    if not math.isfinite(interval) or interval <= 0.0:
        raise InvalidInputError(
            "contour interval must be > 0, got {0!r}".format(interval_m),
            user_message="L'equidistanza delle curve di livello deve essere "
                         "maggiore di zero.")

    import processing                                          # noqa: PLC0415

    try:
        result = processing.run("gdal:contour", {
            "INPUT": raster_layer,
            "BAND": int(band),
            "INTERVAL": interval,
            "FIELD_NAME": ELEVATION_FIELD,
            "OFFSET": float(offset_m),
            "CREATE_3D": False,
            "IGNORE_NODATA": False,
            "NODATA": None,
            "EXTRA": "",
            "OUTPUT": output_path,
        }, feedback=feedback)
    except Exception as exc:                                    # noqa: BLE001
        raise RasterError(
            "gdal:contour failed: {0}".format(exc),
            user_message="Estrazione delle curve di livello non riuscita.",
            hint="Verifica che il DEM sia leggibile e che l'equidistanza "
                 "sia adatta al dislivello.") from exc

    from qgis.core import QgsVectorLayer                        # noqa: PLC0415

    layer = result["OUTPUT"]
    if isinstance(layer, str):
        layer = QgsVectorLayer(layer, "curve di livello", "ogr")
    if layer is None or not layer.isValid():
        raise RasterError(
            "gdal:contour returned no usable layer",
            user_message="Le curve di livello non sono state generate.")
    return layer


def contour_rows(contour_layer, clip_geometry=None,
                 min_length_m: float = 0.0) -> "list[ContourRow]":
    """The contours as planting rows, optionally cut to the usable area.

    Clipping is ``QgsGeometry.intersection``: a contour crossing the parcel
    twice comes back as two parts, and each part is a row of its own, which
    is what it is on the ground.
    """
    rows: "list[ContourRow]" = []
    if contour_layer is None:
        return rows
    index = contour_layer.fields().indexOf(ELEVATION_FIELD)
    for feature in contour_layer.getFeatures():
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            continue
        if clip_geometry is not None:
            geometry = geometry.intersection(clip_geometry)
            if geometry is None or geometry.isEmpty():
                continue
        elevation = float(feature[index]) if index >= 0 else float("nan")
        for part in _line_parts(geometry):
            length = float(part.length())
            if length < max(min_length_m, GEOM_EPS_M):
                continue
            rows.append(ContourRow(elevation_m=elevation, geometry=part,
                                   length_m=length))
    rows.sort(key=lambda row: (row.elevation_m, -row.length_m))
    return rows


def _line_parts(geometry):
    """Every LineString in a geometry, as separate geometries."""
    from qgis.core import QgsGeometry                           # noqa: PLC0415

    if geometry.isMultipart():
        return [QgsGeometry.fromPolylineXY(part)
                for part in geometry.asMultiPolyline() if len(part) >= 2]
    line = geometry.asPolyline()
    return [QgsGeometry.fromPolylineXY(line)] if len(line) >= 2 else []


def walk(geometry, stepper: SlopeStepper, real_distance_m: float,
         start_offset_m: float = 0.0, max_points: int = 20_000):
    """Points along a line, one corrected step apart.

    The step is computed from the slope *along the line at the point already
    reached*, so a contour that starts to climb spaces its plants a little
    further apart in plan, exactly as a tape held on the ground would.
    """
    length = float(geometry.length())
    if length <= GEOM_EPS_M:
        return []
    points = []
    distance = max(0.0, float(start_offset_m))
    for _ in range(max_points):
        if distance > length + GEOM_EPS_M:
            break
        vertex = geometry.interpolate(min(distance, length))
        if vertex is None or vertex.isEmpty():
            break
        point = vertex.asPoint()
        x, y = float(point.x()), float(point.y())
        ux, uy = _tangent(geometry, distance, length)
        points.append((x, y, ux, uy))
        step = stepper.plan_step(x, y, real_distance_m, ux, uy)
        if step <= GEOM_EPS_M:
            break
        distance += step
    return points


def _tangent(geometry, distance: float, length: float):
    """Unit direction of the line at ``distance`` along it."""
    delta = min(max(length * 1e-6, 1e-3), max(length, 1e-3))
    before = max(0.0, min(distance, length) - delta)
    after = min(length, min(distance, length) + delta)
    if after - before <= 0.0:
        return 1.0, 0.0
    p0 = geometry.interpolate(before).asPoint()
    p1 = geometry.interpolate(after).asPoint()
    dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
    norm = math.hypot(dx, dy)
    if norm <= GEOM_EPS_M:
        return 1.0, 0.0
    return dx / norm, dy / norm


def plant_along_contours(rows: "list[ContourRow]", terrain,
                         real_distance_m: float,
                         stagger: bool = True,
                         inside=None) -> "list[PlantingRecord]":
    """Space plants along each contour row, correcting for its own slope.

    ``stagger`` displaces every other row by half a step, which is what turns
    a set of parallel contour rows into a quincunx on the hillside and is the
    reason contour planting holds soil rather than channelling water down the
    gaps.
    """
    if real_distance_m <= 0.0:
        raise InvalidInputError(
            "plant distance must be > 0, got {0!r}".format(real_distance_m),
            user_message="La distanza fra le piante deve essere maggiore di "
                         "zero.")
    stepper = SlopeStepper(terrain, STEP_DIRECTIONAL)
    plants: "list[PlantingRecord]" = []
    plant_id = 0
    for row_id, row in enumerate(rows):
        offset = 0.0
        if stagger and row_id % 2:
            offset = real_distance_m / 2.0
        row.plants = []
        for x, y, ux, uy in walk(row.geometry, stepper, real_distance_m,
                                 start_offset_m=offset):
            if inside is not None and not inside(x, y):
                continue
            slope, aspect, z = stepper.sample(x, y)
            plant_id += 1
            record = PlantingRecord(
                plant_id=plant_id, row_id=row_id, seq_in_row=len(row.plants),
                x=x, y=y,
                z=None if not math.isfinite(z) else float(z),
                spacing_x=real_distance_m, spacing_y=0.0,
                azimuth_deg=azimuth_of(ux, uy),
                slope_deg=None if not math.isfinite(slope) else float(slope),
                aspect_deg=None if not math.isfinite(aspect)
                else float(aspect))
            row.plants.append(record)
            plants.append(record)
    return plants


def describe(rows: "list[ContourRow]", interval_m: float) -> "list[str]":
    planted = sum(len(row.plants) for row in rows)
    lines = ["FILE SU CURVE DI LIVELLO",
             "  Equidistanza:  {0:.2f} m".format(interval_m),
             "  Curve usate:   {0:,}".format(len(rows)),
             "  Piante:        {0:,}".format(planted)]
    if rows:
        elevations = [row.elevation_m for row in rows
                      if math.isfinite(row.elevation_m)]
        if elevations:
            lines.append("  Quote:         {0:.1f} - {1:.1f} m".format(
                min(elevations), max(elevations)))
        lines.append("  Sviluppo:      {0:,.1f} m".format(
            sum(row.length_m for row in rows)))
    return lines
