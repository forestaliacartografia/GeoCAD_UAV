"""
QGIS layer construction for mission and planting outputs.

Produces the six mission outputs required by the spec:

    flight_lines      LineStringZ   strips and transits
    waypoints         PointZ        seq, z_amsl, z_agl, heading, speed, action
    photo_centers     PointZ        approximate exterior orientation
    photo_footprints  PolygonZ      ground footprint draped on the DEM
    coverage_overlap  Raster        photos per cell
    gsd_map           Raster        effective GSD, cm/px

Field construction goes through :func:`make_field`, which prefers the
``QMetaType`` form (required from QGIS 3.38 on, where the ``QVariant`` form is
deprecated) and falls back to ``QVariant`` on 3.34. The probe runs once and is
cached, so the plugin loads on 3.34 LTR and on 4.0 without a version check.
"""

from __future__ import annotations

import math
import os
from typing import Optional

import numpy as np

from ..core.errors import ExportError, LayerError
from ..core.models import Mission

_FIELD_STYLE: Optional[str] = None


def _detect_field_style() -> str:
    """Decide once whether QgsField takes QMetaType or QVariant."""
    global _FIELD_STYLE
    if _FIELD_STYLE is not None:
        return _FIELD_STYLE
    from qgis.core import QgsField                              # noqa: PLC0415
    try:
        from qgis.PyQt.QtCore import QMetaType                  # noqa: PLC0415
        QgsField("probe", QMetaType.Type.Double)
        _FIELD_STYLE = "qmetatype"
    except (ImportError, AttributeError, TypeError):
        _FIELD_STYLE = "qvariant"
    return _FIELD_STYLE


def make_field(name: str, kind: str):
    """Build a ``QgsField``. ``kind`` is 'double' | 'int' | 'string' | 'bool'."""
    from qgis.core import QgsField                              # noqa: PLC0415

    if _detect_field_style() == "qmetatype":
        from qgis.PyQt.QtCore import QMetaType                  # noqa: PLC0415
        mapping = {"double": QMetaType.Type.Double,
                   "int": QMetaType.Type.Int,
                   "string": QMetaType.Type.QString,
                   "bool": QMetaType.Type.Bool}
    else:                                                       # QGIS 3.34
        from qgis.PyQt.QtCore import QVariant                   # noqa: PLC0415
        mapping = {"double": QVariant.Double, "int": QVariant.Int,
                   "string": QVariant.String, "bool": QVariant.Bool}
    if kind not in mapping:
        raise LayerError("unknown field kind {0!r}".format(kind),
                         user_message="Tipo di campo non riconosciuto.")
    return QgsField(name, mapping[kind])


def make_fields(spec):
    """Build a ``QgsFields`` from ``[(name, kind), ...]``."""
    from qgis.core import QgsFields                             # noqa: PLC0415
    fields = QgsFields()
    for name, kind in spec:
        fields.append(make_field(name, kind))
    return fields


def memory_layer(geometry_type: str, name: str, crs_authid: str, field_spec):
    """Create an in-memory layer with the given schema."""
    from qgis.core import QgsVectorLayer                        # noqa: PLC0415

    uri = "{0}?crs={1}".format(geometry_type, crs_authid or "EPSG:4326")
    layer = QgsVectorLayer(uri, name, "memory")
    if not layer.isValid():
        raise LayerError(
            "could not create memory layer {0} ({1})".format(name, uri),
            user_message="Impossibile creare il layer '{0}'.".format(name))
    provider = layer.dataProvider()
    provider.addAttributes(list(make_fields(field_spec)))
    layer.updateFields()
    return layer


def _num(value):
    """None for non-finite values, so NULL lands in the attribute table."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


# --------------------------------------------------------------------------
# CAD attributes
# --------------------------------------------------------------------------

CAD_ID_FIELD = "cad_id"
AREA_HA_FIELD = "area_ha"
PERIMETER_FIELD = "perimeter_m"

#: Written on every feature the CAD tools create, on top of the parametric
#: columns in ``cad.parametric.METADATA_FIELDS``. They exist because an
#: operator reads the attribute table, not the JSON in ``cad_params``.
CAD_ATTRIBUTE_FIELDS = [
    (CAD_ID_FIELD, "int"),
    (AREA_HA_FIELD, "double"),
    (PERIMETER_FIELD, "double"),
]


def ensure_cad_fields(layer):
    """Add the CAD attribute columns to ``layer`` if they are missing.

    Returns the names that were added, ``[]`` when they were already there,
    and ``None`` when the layer refused them -- a read-only source, or one
    whose provider cannot add attributes. The caller writes the geometry
    either way: losing three columns is not a reason to lose the shape.
    """
    if layer is None:
        return None
    try:
        existing = {field.name() for field in layer.fields()}
    except (AttributeError, RuntimeError):
        return None
    missing = [spec for spec in CAD_ATTRIBUTE_FIELDS
               if spec[0] not in existing]
    if not missing:
        return []

    provider = layer.dataProvider()
    try:
        from qgis.core import QgsVectorDataProvider              # noqa: PLC0415

        # Scoped form: it exists on 3.40 and on 4.0, both reporting 8.
        add_attributes = QgsVectorDataProvider.Capability.AddAttributes
        if not provider.capabilities() & add_attributes:
            return None
    except (AttributeError, RuntimeError, ImportError):
        pass
    try:
        if not provider.addAttributes(list(make_fields(missing))):
            return None
        layer.updateFields()
    except (AttributeError, RuntimeError):
        return None
    return [name for name, _kind in missing]


def next_cad_id(layer) -> int:
    """The next progressive id on ``layer``: ``max(cad_id) + 1``, else 1.

    Deliberately not a running count of features: after a delete the ids must
    not be handed out twice, and ``max + 1`` keeps every id unique for the
    life of the layer without renumbering anything that already exists.
    """
    if layer is None:
        return 1
    try:
        index = layer.fields().indexOf(CAD_ID_FIELD)
    except (AttributeError, RuntimeError):
        return 1
    if index < 0:
        return 1
    highest = 0
    try:
        for feature in layer.getFeatures():
            value = feature.attribute(index)
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            highest = max(highest, value)
    except (AttributeError, RuntimeError):
        return 1
    return highest + 1


def cad_attributes(record, layer) -> dict:
    """The three CAD columns for one freshly built record.

    The numbers come from ``primitives.measure`` -- which already stored them
    in the record at build time, in the metric working CRS -- so the tool
    never computes an area of its own and hectares are never derived from
    degrees. Hectares are rounded to 2 decimals because that is the precision
    a planting or forestry document is written in; metres keep 3.
    """
    params = getattr(record, "params", {}) or {}
    area_m2 = params.get("measured_area_m2")
    perimeter = (params.get("measured_perimeter_m")
                 or params.get("measured_length_m"))
    return {
        CAD_ID_FIELD: next_cad_id(layer),
        AREA_HA_FIELD: round(float(area_m2) / 10_000.0, 2) if area_m2 else 0.0,
        PERIMETER_FIELD: round(float(perimeter), 3) if perimeter else 0.0,
    }


# --------------------------------------------------------------------------
# Mission layers
# --------------------------------------------------------------------------

WAYPOINT_FIELDS = [
    ("seq", "int"), ("sub_mission", "int"), ("strip", "int"),
    ("z_amsl", "double"), ("z_agl", "double"), ("heading", "double"),
    ("gimbal_pitch", "double"), ("speed", "double"), ("type", "string"),
    ("action", "string"), ("dem_gap", "int"), ("climb_limited", "int"),
]

PHOTO_FIELDS = [
    ("photo_id", "int"), ("sub_mission", "int"), ("strip", "int"),
    ("z_amsl", "double"), ("z_agl", "double"), ("omega", "double"),
    ("phi", "double"), ("kappa", "double"), ("heading", "double"),
    ("gimbal_pitch", "double"), ("gsd_cm", "double"),
]

LINE_FIELDS = [("leg", "int"), ("role", "string"), ("length_m", "double")]

FOOTPRINT_FIELDS = [("photo_id", "int"), ("strip", "int"),
                    ("z_amsl", "double"), ("gsd_cm", "double"),
                    ("area_m2", "double")]


def build_waypoint_layer(mission: Mission, crs_authid: str = ""):
    from qgis.core import QgsFeature, QgsGeometry, QgsPoint     # noqa: PLC0415

    layer = memory_layer("PointZ", "waypoints",
                         crs_authid or mission.crs_authid, WAYPOINT_FIELDS)
    feats = []
    for wp in mission.waypoints:
        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry(QgsPoint(wp.x, wp.y, wp.z_amsl)))
        feat.setAttributes([
            wp.seq, wp.sub_mission, wp.strip_index, _num(wp.z_amsl),
            _num(wp.z_agl), _num(wp.heading_deg), _num(wp.gimbal_pitch_deg),
            _num(wp.speed_ms), wp.kind, ";".join(wp.actions),
            int(wp.dem_gap), int(wp.climb_limited)])
        feats.append(feat)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return layer


def build_photo_layer(mission: Mission, crs_authid: str = ""):
    from qgis.core import QgsFeature, QgsGeometry, QgsPoint     # noqa: PLC0415

    layer = memory_layer("PointZ", "photo_centers",
                         crs_authid or mission.crs_authid, PHOTO_FIELDS)
    feats = []
    for ph in mission.photos:
        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry(QgsPoint(ph.x, ph.y, ph.z_amsl)))
        feat.setAttributes([
            ph.photo_id, ph.sub_mission, ph.strip_index, _num(ph.z_amsl),
            _num(ph.z_agl), _num(ph.omega_deg), _num(ph.phi_deg),
            _num(ph.kappa_deg), _num(ph.heading_deg),
            _num(ph.gimbal_pitch_deg), _num(ph.gsd_cm)])
        feats.append(feat)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return layer


def build_line_layer(mission: Mission, crs_authid: str = ""):
    """Strips plus the transits between them, as LineStringZ."""
    from qgis.core import (QgsFeature, QgsGeometry,             # noqa: PLC0415
                           QgsLineString, QgsPoint)

    layer = memory_layer("LineStringZ", "flight_lines",
                         crs_authid or mission.crs_authid, LINE_FIELDS)
    feats = []
    for i, line in enumerate(mission.lines):
        arr = np.asarray(line, dtype=float)
        if arr.shape[0] < 2:
            continue
        pts = [QgsPoint(float(p[0]), float(p[1]), float(p[2])) for p in arr]
        geom = QgsGeometry(QgsLineString(pts))
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom)
        feat.setAttributes([i, "strip", _num(geom.length())])
        feats.append(feat)

        if i + 1 < len(mission.lines):
            nxt = np.asarray(mission.lines[i + 1], dtype=float)
            if nxt.shape[0] >= 1:
                link = QgsLineString([
                    QgsPoint(float(arr[-1][0]), float(arr[-1][1]),
                             float(arr[-1][2])),
                    QgsPoint(float(nxt[0][0]), float(nxt[0][1]),
                             float(nxt[0][2]))])
                lgeom = QgsGeometry(link)
                lfeat = QgsFeature(layer.fields())
                lfeat.setGeometry(lgeom)
                lfeat.setAttributes([i, "transit", _num(lgeom.length())])
                feats.append(lfeat)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return layer


def build_footprint_layer(mission: Mission, crs_authid: str = ""):
    from qgis.core import (QgsFeature, QgsGeometry,             # noqa: PLC0415
                           QgsLineString, QgsPoint, QgsPolygon)

    layer = memory_layer("PolygonZ", "photo_footprints",
                         crs_authid or mission.crs_authid, FOOTPRINT_FIELDS)
    feats = []
    for ring, photo in zip(mission.footprints, mission.photos):
        arr = np.asarray(ring, dtype=float)
        if arr.shape[0] < 4 or not np.all(np.isfinite(arr)):
            continue
        pts = [QgsPoint(float(p[0]), float(p[1]), float(p[2])) for p in arr]
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        poly = QgsPolygon()
        poly.setExteriorRing(QgsLineString(pts))
        geom = QgsGeometry(poly)
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom)
        feat.setAttributes([photo.photo_id, photo.strip_index,
                            _num(photo.z_amsl), _num(photo.gsd_cm),
                            _num(geom.area())])
        feats.append(feat)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return layer


def build_mission_layers(mission: Mission, crs_authid: str = ""):
    """All four vector layers, as a dict keyed by layer name."""
    layers = {
        "flight_lines": build_line_layer(mission, crs_authid),
        "waypoints": build_waypoint_layer(mission, crs_authid),
        "photo_centers": build_photo_layer(mission, crs_authid),
    }
    if mission.footprints:
        layers["photo_footprints"] = build_footprint_layer(mission, crs_authid)
    return layers


# --------------------------------------------------------------------------
# Planting layers
# --------------------------------------------------------------------------

PLANT_FIELDS = [
    ("plant_id", "int"), ("row_id", "int"), ("seq_in_row", "int"),
    ("z", "double"), ("spacing_x", "double"), ("spacing_y", "double"),
    ("azimuth", "double"), ("dist_to_edge", "double"),
    ("slope_deg", "double"), ("aspect_deg", "double"),
]

EXCLUDED_FIELDS = PLANT_FIELDS + [("reason", "string")]


def build_planting_layers(result, crs_authid: str, include_excluded: bool = True):
    """Plants, rejected candidates and row centre-lines."""
    from qgis.core import (QgsFeature, QgsGeometry,             # noqa: PLC0415
                           QgsLineString, QgsPoint, QgsPointXY)

    has_z = any(p.z is not None for p in result.plants)
    geom_type = "PointZ" if has_z else "Point"

    plants = memory_layer(geom_type, "planting", crs_authid, PLANT_FIELDS)
    feats = []
    for p in result.plants:
        feat = QgsFeature(plants.fields())
        if has_z:
            feat.setGeometry(QgsGeometry(QgsPoint(p.x, p.y, p.z or 0.0)))
        else:
            feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(p.x, p.y)))
        feat.setAttributes([p.plant_id, p.row_id, p.seq_in_row, _num(p.z),
                            _num(p.spacing_x), _num(p.spacing_y),
                            _num(p.azimuth_deg), _num(p.dist_to_edge),
                            _num(p.slope_deg), _num(p.aspect_deg)])
        feats.append(feat)
    plants.dataProvider().addFeatures(feats)
    plants.updateExtents()
    layers = {"planting": plants}

    if include_excluded and result.excluded:
        excluded = memory_layer("Point", "planting_excluded", crs_authid,
                                EXCLUDED_FIELDS)
        feats = []
        for p in result.excluded:
            feat = QgsFeature(excluded.fields())
            feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(p.x, p.y)))
            feat.setAttributes([p.plant_id, p.row_id, p.seq_in_row, _num(p.z),
                                _num(p.spacing_x), _num(p.spacing_y),
                                _num(p.azimuth_deg), _num(p.dist_to_edge),
                                _num(p.slope_deg), _num(p.aspect_deg),
                                p.excluded_reason])
            feats.append(feat)
        excluded.dataProvider().addFeatures(feats)
        excluded.updateExtents()
        layers["planting_excluded"] = excluded

    if result.rows:
        rows = memory_layer("LineString", "planting_rows", crs_authid,
                            [("row_id", "int"), ("n_plants", "int"),
                             ("length_m", "double")])
        feats = []
        for i, line in enumerate(result.rows):
            arr = np.asarray(line, dtype=float)
            if arr.shape[0] < 2:
                continue
            geom = QgsGeometry.fromPolylineXY(
                [QgsPointXY(float(p[0]), float(p[1])) for p in arr])
            feat = QgsFeature(rows.fields())
            feat.setGeometry(geom)
            feat.setAttributes([i + 1, arr.shape[0], _num(geom.length())])
            feats.append(feat)
        rows.dataProvider().addFeatures(feats)
        rows.updateExtents()
        layers["planting_rows"] = rows
    return layers


# --------------------------------------------------------------------------
# GeoPackage
# --------------------------------------------------------------------------

def write_layers_gpkg(layers: dict, path: str, overwrite: bool = True) -> str:
    """Write named layers into one GeoPackage.

    The first layer creates the file; the rest are appended, so the caller gets
    a single self-contained deliverable rather than a scatter of files.
    """
    from qgis.core import (QgsCoordinateTransformContext,       # noqa: PLC0415
                           QgsVectorFileWriter)

    if not layers:
        raise ExportError("no layers to write",
                          user_message="Nessun layer da esportare.")
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        raise ExportError(
            "directory does not exist: {0}".format(directory),
            user_message="La cartella di destinazione non esiste.", hint=directory)
    if os.path.exists(path) and overwrite:
        try:
            os.remove(path)
        except OSError as exc:
            raise ExportError(
                "cannot replace {0}: {1}".format(path, exc),
                user_message="Impossibile sostituire il GeoPackage esistente.",
                hint=str(exc)) from exc

    context = QgsCoordinateTransformContext()
    first = True
    for name, layer in layers.items():
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = name
        options.fileEncoding = "UTF-8"
        options.actionOnExistingFile = (
            QgsVectorFileWriter.CreateOrOverwriteFile if first
            else QgsVectorFileWriter.CreateOrOverwriteLayer)
        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, path, context, options)
        # writeAsVectorFormatV3 returns (error, message, newFile, newLayer)
        error = result[0]
        if error != QgsVectorFileWriter.NoError:
            raise ExportError(
                "GPKG write failed for layer {0}: {1}".format(name, result[1]),
                user_message="Scrittura del GeoPackage non riuscita per il "
                             "layer '{0}'.".format(name),
                hint=str(result[1]))
        first = False
    return path


def write_mission_gpkg(mission: Mission, path: str,
                       crs_authid: Optional[str] = None) -> str:
    return write_layers_gpkg(
        build_mission_layers(mission, crs_authid or mission.crs_authid), path)


# --------------------------------------------------------------------------
# Raster products
# --------------------------------------------------------------------------

def _write_geotiff(path: str, array: np.ndarray, geotransform, crs_wkt: str,
                   nodata: float = -9999.0) -> str:
    from osgeo import gdal                                      # noqa: PLC0415

    gdal.UseExceptions()
    driver = gdal.GetDriverByName("GTiff")
    rows, cols = array.shape
    try:
        dataset = driver.Create(path, cols, rows, 1, gdal.GDT_Float32,
                                options=["COMPRESS=DEFLATE", "TILED=YES"])
    except Exception as exc:                                    # noqa: BLE001
        raise ExportError("cannot create raster {0}: {1}".format(path, exc),
                          user_message="Impossibile creare il raster.",
                          hint=str(exc)) from exc
    dataset.SetGeoTransform(geotransform)
    if crs_wkt:
        dataset.SetProjection(crs_wkt)
    band = dataset.GetRasterBand(1)
    band.WriteArray(np.where(np.isfinite(array), array, nodata).astype(np.float32))
    band.SetNoDataValue(nodata)
    band.FlushCache()
    dataset = None
    return path


def build_coverage_rasters(mission: Mission, aoi_geom, path_coverage: str,
                           path_gsd: str, crs_wkt: str = "",
                           cell_m: Optional[float] = None,
                           feedback=None):
    """Photos-per-cell and effective-GSD rasters, from the draped footprints.

    Both are computed on the terrain-draped footprints, not on flat-plane
    rectangles: on a slope the two differ by exactly the amount that matters.

    ``gsd_map`` stores, per cell, the *best* (smallest) GSD among the photos
    that see it -- the resolution actually achievable there.
    """
    from qgis.core import (QgsFeature, QgsGeometry,             # noqa: PLC0415
                           QgsPointXY, QgsSpatialIndex)

    if not mission.footprints:
        raise ExportError(
            "no footprints available",
            user_message="Impronte a terra non disponibili: impossibile "
                         "calcolare copertura e mappa del GSD.",
            hint="Attiva il calcolo delle impronte nelle opzioni della missione.")

    box = aoi_geom.boundingBox()
    if cell_m is None:
        cell_m = max(min(box.width(), box.height()) / 300.0, 1.0)

    cols = max(int(math.ceil(box.width() / cell_m)), 1)
    rows = max(int(math.ceil(box.height() / cell_m)), 1)
    geotransform = (box.xMinimum(), cell_m, 0.0, box.yMaximum(), 0.0, -cell_m)

    polys, gsds = [], []
    index = QgsSpatialIndex()
    for i, (ring, photo) in enumerate(zip(mission.footprints, mission.photos)):
        arr = np.asarray(ring, dtype=float)
        if arr.shape[0] < 4 or not np.all(np.isfinite(arr[:, :2])):
            continue
        geom = QgsGeometry.fromPolygonXY(
            [[QgsPointXY(float(p[0]), float(p[1])) for p in arr]])
        if geom.isEmpty():
            continue
        feat = QgsFeature(len(polys))
        feat.setGeometry(geom)
        index.addFeature(feat)
        polys.append(geom)
        gsds.append(photo.gsd_m)

    coverage = np.zeros((rows, cols), dtype=np.float32)
    gsd_map = np.full((rows, cols), np.nan, dtype=np.float32)

    for r in range(rows):
        if feedback is not None:
            if feedback.isCanceled():
                break
            feedback.setProgress(100.0 * r / rows)
        y = box.yMaximum() - (r + 0.5) * cell_m
        for c in range(cols):
            x = box.xMinimum() + (c + 0.5) * cell_m
            pt = QgsGeometry.fromPointXY(QgsPointXY(x, y))
            best = math.inf
            count = 0
            for fid in index.intersects(pt.boundingBox()):
                if polys[fid].contains(pt):
                    count += 1
                    if gsds[fid] is not None and math.isfinite(gsds[fid]):
                        best = min(best, gsds[fid])
            coverage[r, c] = count
            if count and math.isfinite(best):
                gsd_map[r, c] = best * 100.0        # cm/px

    _write_geotiff(path_coverage, coverage, geotransform, crs_wkt, nodata=-1.0)
    _write_geotiff(path_gsd, gsd_map, geotransform, crs_wkt, nodata=-9999.0)
    return path_coverage, path_gsd
