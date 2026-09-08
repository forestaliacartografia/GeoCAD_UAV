"""
M7 test: the Processing provider registers and every algorithm runs.

This is the test that catches the errors unit tests cannot: a wrong parameter
type, a missing enum, a sink schema that does not match the features written.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_processing.py
"""

import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from osgeo import gdal, osr                                     # noqa: E402
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsFeature, QgsGeometry, QgsProcessingFeedback,
                       QgsProject, QgsRasterLayer, QgsVectorLayer)

QGS = QgsApplication([], False)
QGS.initQgis()

# Both of these must come AFTER QgsApplication exists. Importing qgis.analysis
# first crashes the interpreter outright (STATUS_STACK_BUFFER_OVERRUN, with no
# traceback), and the bundled `processing` package lives under QGIS's own
# plugins directory, which is on sys.path inside the QGIS GUI but not here.
from qgis.analysis import QgsNativeAlgorithms                   # noqa: E402

_plugins_dir = os.path.join(QgsApplication.prefixPath(), "python", "plugins")
if os.path.isdir(_plugins_dir) and _plugins_dir not in sys.path:
    sys.path.append(_plugins_dir)

import processing                                               # noqa: E402
from processing.core.Processing import Processing               # noqa: E402

Processing.initialize()
# Keep a reference: addProvider takes ownership, and a temporary would be
# collected out from under the registry.
NATIVE = QgsNativeAlgorithms()
QgsApplication.processingRegistry().addProvider(NATIVE)

from geocad_uav.processing.provider import GeoCadProvider       # noqa: E402

PROVIDER = GeoCadProvider()
QgsApplication.processingRegistry().addProvider(PROVIDER)

FAILURES = []
TMP = tempfile.mkdtemp(prefix="geocad_proc_")


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<54} got={2:<14.9g} exp={3:.9g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


# ------------------------------------------------------------- fixtures ---
OX, OY = 500000.0, 5000000.0
CELL = 5.0
NX, NY = 160, 140

xs = OX + (np.arange(NX) + 0.5) * CELL
ys = OY - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(xs, ys)
Z = 300.0 + 0.10 * (XX - OX) + 25.0 * np.sin((YY - OY) / 90.0)

dem_path = os.path.join(TMP, "dem.tif")
gdal.UseExceptions()
driver = gdal.GetDriverByName("GTiff")
ds = driver.Create(dem_path, NX, NY, 1, gdal.GDT_Float32)
ds.SetGeoTransform((OX, CELL, 0.0, OY, 0.0, -CELL))
srs = osr.SpatialReference()
srs.ImportFromEPSG(32632)
ds.SetProjection(srs.ExportToWkt())
ds.GetRasterBand(1).WriteArray(Z.astype(np.float32))
ds.FlushCache()
ds = None

dem_layer = QgsRasterLayer(dem_path, "dem", "gdal")
QgsProject.instance().addMapLayer(dem_layer)

aoi_layer = QgsVectorLayer("Polygon?crs=EPSG:32632", "aoi", "memory")
feat = QgsFeature()
feat.setGeometry(QgsGeometry.fromWkt(
    "POLYGON(({0} {1}, {2} {1}, {2} {3}, {0} {3}, {0} {1}))".format(
        OX + 120, OY - 500, OX + 560, OY - 140)))
aoi_layer.dataProvider().addFeatures([feat])
aoi_layer.updateExtents()
QgsProject.instance().addMapLayer(aoi_layer)

geo_layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "aoi_geo", "memory")
gfeat = QgsFeature()
gfeat.setGeometry(QgsGeometry.fromWkt(
    "POLYGON((9.15 45.44, 9.22 45.44, 9.22 45.49, 9.15 45.49, 9.15 45.44))"))
geo_layer.dataProvider().addFeatures([gfeat])
QgsProject.instance().addMapLayer(geo_layer)


class Quiet(QgsProcessingFeedback):
    """Collects messages instead of printing the whole log."""

    def __init__(self):
        super().__init__()
        self.warnings = []
        self.errors = []
        self.info = []

    def pushWarning(self, text):                                # noqa: N802
        self.warnings.append(text)

    def reportError(self, text, fatalError=False):              # noqa: N802, N803
        self.errors.append(text)

    def pushInfo(self, text):                                   # noqa: N802
        self.info.append(text)


# ---------------------------------------------------------- registration --
print("\n== provider registration ==")
registry = QgsApplication.processingRegistry()
check_true("provider is registered",
           registry.providerById("geocaduav") is not None)
ids = sorted(a.id() for a in registry.providerById("geocaduav").algorithms())
print("        algorithms: {0}".format(ids))
for expected in ("geocaduav:planflight", "geocaduav:creategrid",
                 "geocaduav:forestplanting"):
    check_true("{0} is available".format(expected), expected in ids)
for alg_id in ids:
    alg = registry.algorithmById(alg_id)
    check_true("{0} has a display name and help".format(alg_id),
               bool(alg.displayName()) and bool(alg.shortHelpString()))
    check_true("{0} can create a fresh instance".format(alg_id),
               alg.createInstance() is not None)

# ------------------------------------------------------------ flight run --
print("\n== geocaduav:planflight ==")
feedback = Quiet()
result = processing.run("geocaduav:planflight", {
    "AOI": aoi_layer,
    "DEM": dem_layer,
    "DEM_IS_DSM": False,
    "TARGET_MODE": 0,
    "TARGET_VALUE": 80.0,
    "FRONTLAP": 80.0,
    "SIDELAP": 70.0,
    "ALT_MODE": 0,
    "AZIMUTH_MODE": 0,
    "PATTERN": 0,
    "SPEED": 8.0,
    "DZ_TOLERANCE": 2.0,
    "FOOTPRINTS": True,
    "OUT_LINES": "TEMPORARY_OUTPUT",
    "OUT_WAYPOINTS": "TEMPORARY_OUTPUT",
    "OUT_PHOTOS": "TEMPORARY_OUTPUT",
    "OUT_FOOTPRINTS": "TEMPORARY_OUTPUT",
    "OUT_REPORT": os.path.join(TMP, "report.html"),
}, feedback=feedback)

waypoints = result["OUT_WAYPOINTS"]
photos = result["OUT_PHOTOS"]
lines = result["OUT_LINES"]
footprints = result["OUT_FOOTPRINTS"]
if isinstance(waypoints, str):
    waypoints = QgsProcessingUtils = processing.tools.dataobjects.getLayerFromString(
        waypoints)
    photos = processing.tools.dataobjects.getLayerFromString(photos)
    lines = processing.tools.dataobjects.getLayerFromString(lines)
    footprints = processing.tools.dataobjects.getLayerFromString(footprints)

check_true("waypoint layer produced", waypoints.featureCount() > 0)
check_true("photo layer produced", photos.featureCount() > 0)
check_true("flight lines produced", lines.featureCount() > 0)
check_true("footprints produced", footprints.featureCount() > 0)
check("footprints match photo count", footprints.featureCount(),
      photos.featureCount())
print("        {0} waypoints, {1} photos, {2} lines".format(
    waypoints.featureCount(), photos.featureCount(), lines.featureCount()))

wz = [f.geometry().constGet().z() for f in waypoints.getFeatures()]
check_true("every waypoint carries a real height",
           all(abs(z) > 1.0 for z in wz))
check_true("heights follow the terrain, not a flat plane",
           max(wz) - min(wz) > 20.0)
agl = [f["z_agl"] for f in waypoints.getFeatures()]
check("AGL is held constant at 80 m", max(agl) - min(agl), 0.0, 1e-6)
check("AGL equals the requested height", float(np.mean(agl)), 80.0, 1e-6)

gsd = [f["gsd_cm"] for f in photos.getFeatures()]
check("effective GSD at 80 m", float(np.mean(gsd)), 2.13280072, 1e-4)

check_true("report was written",
           os.path.getsize(os.path.join(TMP, "report.html")) > 4000)
check_true("validation summary reached the log",
           any("VALIDAZIONE" in line for line in feedback.info))
check_true("assumptions reached the log",
           any("ASSUNZIONI" in line for line in feedback.info))
check_true("no errors reported", not feedback.errors)

# -- the CRS guard must fire --------------------------------------------
print("\n== CRS guard through Processing ==")
try:
    processing.run("geocaduav:planflight", {
        "AOI": geo_layer, "DEM": dem_layer, "TARGET_VALUE": 80.0,
        "OUT_LINES": "TEMPORARY_OUTPUT", "OUT_WAYPOINTS": "TEMPORARY_OUTPUT",
        "OUT_PHOTOS": "TEMPORARY_OUTPUT",
    }, feedback=Quiet())
    check_true("a geographic AOI is refused", False)
except Exception as exc:                                        # noqa: BLE001
    check_true("a geographic AOI is refused with a clear message",
               "geografico" in str(exc))

# ------------------------------------------------------------- grid run --
print("\n== geocaduav:creategrid ==")
grid_result = processing.run("geocaduav:creategrid", {
    "AOI": aoi_layer, "SPACING_X": 20.0, "SPACING_Y": 20.0,
    "PATTERN": 0, "AZIMUTH": 0.0, "MARGIN": 0.0,
    "OUT_POINTS": "TEMPORARY_OUTPUT", "OUT_ROWS": "TEMPORARY_OUTPUT",
}, feedback=Quiet())
points = grid_result["OUT_POINTS"]
if isinstance(points, str):
    points = processing.tools.dataobjects.getLayerFromString(points)
check_true("grid points produced", points.featureCount() > 0)
# AOI is 440 x 360 m; at 20 m spacing that is 23 x 19 = 437 nodes.
check("grid point count on a 440 x 360 m AOI at 20 m", points.featureCount(),
      23 * 19)
rows = sorted({f["row_id"] for f in points.getFeatures()})
check_true("rows are numbered from 1 with no gaps",
           rows == list(range(1, len(rows) + 1)))

# ----------------------------------------------------------- forest run --
print("\n== geocaduav:forestplanting ==")
forest_result = processing.run("geocaduav:forestplanting", {
    "AOI": aoi_layer, "DEM": dem_layer,
    "SPACING_X": 3.0, "SPACING_Y": 2.0, "PATTERN": 0,
    "AZIMUTH": 15.0, "MARGIN": 2.0,
    "SLOPE_MAX": 30.0,
    "OUT_PLANTS": "TEMPORARY_OUTPUT", "OUT_EXCLUDED": "TEMPORARY_OUTPUT",
    "OUT_ROWS": "TEMPORARY_OUTPUT",
}, feedback=Quiet())
plants = forest_result["OUT_PLANTS"]
if isinstance(plants, str):
    plants = processing.tools.dataobjects.getLayerFromString(plants)
check_true("plants produced", plants.featureCount() > 0)
print("        {0:,} plants".format(plants.featureCount()))
sample = next(plants.getFeatures())
check_true("plants carry DEM-derived slope",
           sample["slope_deg"] is not None)
check_true("plants carry an elevation", sample["z"] is not None)
ids = [f["plant_id"] for f in plants.getFeatures()]
check_true("plant ids are unique", len(set(ids)) == len(ids))

print("\n" + "=" * 78)
shutil.rmtree(TMP, ignore_errors=True)
QgsProject.instance().removeAllMapLayers()
QGS.exitQgis()
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for f_ in FAILURES:
        print("   - {0}".format(f_))
    sys.exit(1)
print("ALL CHECKS PASSED")
