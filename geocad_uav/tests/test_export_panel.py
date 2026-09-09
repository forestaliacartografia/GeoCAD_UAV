"""
v1.3.5: the Export tab.

A format is only badged VERIFIED here if this file writes it through the panel
and reads it back, counting what came out against the mission that went in.
Nothing is taken on trust from the format table.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_export_panel.py
"""

import ast
import csv
import json
import os
import sys
import tempfile
import zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import QgsApplication                            # noqa: E402

QGS = QgsApplication([], False)
QGS.initQgis()

from osgeo import gdal, osr                                     # noqa: E402
from qgis.core import (QgsCoordinateReferenceSystem, QgsGeometry,  # noqa: E402
                       QgsProject, QgsRasterLayer, QgsRectangle,
                       QgsVectorLayer)
from qgis.gui import QgsMapCanvas                               # noqa: E402
from qgis.PyQt.QtCore import Qt                                 # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow                     # noqa: E402

from geocad_uav.core.models import AltitudeMode                 # noqa: E402
from geocad_uav.core.z import TerrainModel                      # noqa: E402
from geocad_uav.gui.export_panel import ExportPanel             # noqa: E402
from geocad_uav.uav import cameras as cam_lib                   # noqa: E402
from geocad_uav.uav import drones as drone_lib                  # noqa: E402
from geocad_uav.uav import export as ex                         # noqa: E402
from geocad_uav.uav import mission as mi                        # noqa: E402
from geocad_uav.uav import photogrammetry as pg                 # noqa: E402
from geocad_uav.uav import survey as sv                         # noqa: E402
from geocad_uav.uav import validator as val                     # noqa: E402

FAILURES = []
SKIPS = []
TMP = tempfile.mkdtemp(prefix="geocad_export_")
CRS = QgsCoordinateReferenceSystem("EPSG:32632")
OX, OY = 500000.0, 5000000.0


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<54} got={2:<14.10g} exp={3:.10g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def skip(label, reason):
    print("  [skip] {0}\n         reason: {1}".format(label, reason))
    SKIPS.append((label, reason))


# ------------------------------------------------------------- fixtures ---
CELL, NX, NY = 5.0, 200, 180
xs = OX + (np.arange(NX) + 0.5) * CELL
ys = OY - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(xs, ys)
Z = 300.0 + 0.06 * (XX - OX)

DEM_PATH = os.path.join(TMP, "ramp.tif")
gdal.UseExceptions()
_ds = gdal.GetDriverByName("GTiff").Create(DEM_PATH, NX, NY, 1, gdal.GDT_Float32)
_ds.SetGeoTransform((OX, CELL, 0.0, OY, 0.0, -CELL))
_srs = osr.SpatialReference()
_srs.ImportFromEPSG(32632)
_ds.SetProjection(_srs.ExportToWkt())
_ds.GetRasterBand(1).WriteArray(Z.astype(np.float32))
_ds.FlushCache()
_ds = None

DEM_LAYER = QgsRasterLayer(DEM_PATH, "dem ramp", "gdal")
AOI = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 100.0, OY - 500.0, OX + 400.0, OY - 300.0))

_camera = cam_lib.load_library()["dji_mavic3e"]
_drone = drone_lib.load_library()["dji_mavic3e"]
_geometry = pg.solve_survey_geometry(_camera, pg.Overlap(0.80, 0.70),
                                     h_agl_m=80.0)
_box = AOI.boundingBox()
TERRAIN, _warn = TerrainModel.from_layer(
    DEM_LAYER, CRS,
    (_box.xMinimum(), _box.yMinimum(), _box.xMaximum(), _box.yMaximum()),
    margin_m=max(_geometry.footprint_across_m, _geometry.footprint_along_m))
MISSION = mi.build_mission(
    sv.prepare_aoi([AOI])[0][0], TERRAIN,
    mi.MissionParams(
        camera=_camera, drone=_drone, overlap=pg.Overlap(0.80, 0.70),
        h_agl_m=80.0, altitude_mode=AltitudeMode.TERRAIN, safety_margin_m=5.0,
        azimuth_strategy=sv.AZIMUTH_MANUAL, manual_azimuth_deg=0.0,
        v_mission_ms=10.0, compute_footprints=False),
    crs_authid=CRS.authid())

N_WP = len(MISSION.waypoints)
N_PHOTOS = len(MISSION.photos)


class FakeMessageBar:
    def __init__(self):
        self.messages = []

    def pushMessage(self, *args, **kwargs):                     # noqa: N802
        self.messages.append(args)


class FakeIface:
    def __init__(self):
        self._window = QMainWindow()
        self._canvas = QgsMapCanvas()
        self._canvas.setDestinationCrs(CRS)
        self._canvas.setExtent(QgsRectangle(OX, OY - 600, OX + 500, OY))
        self._bar = FakeMessageBar()

    def mainWindow(self):                                       # noqa: N802
        return self._window

    def mapCanvas(self):                                        # noqa: N802
        return self._canvas

    def messageBar(self):                                       # noqa: N802
        return self._bar


def check_format(key, item):
    """Set exactly one format checked."""
    for row in range(item.format_list.count()):
        entry = item.format_list.item(row)
        if not entry.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            continue
        entry.setCheckState(
            Qt.CheckState.Checked
            if entry.data(Qt.ItemDataRole.UserRole) == key
            else Qt.CheckState.Unchecked)


iface = FakeIface()
panel = ExportPanel(iface, lambda: MISSION)
OUT = os.path.join(TMP, "out")
os.makedirs(OUT, exist_ok=True)
panel.folder_edit.setText(OUT)
panel.basename_edit.setText("missione")

print("\n== fixture ==")
print("  {0} waypoints, {1} photos, CRS {2}".format(N_WP, N_PHOTOS,
                                                    MISSION.crs_authid))
print("  formats offered by uav.export: {0}".format(sorted(ex.FORMATS)))
print("  refused by uav.export       : {0}".format(sorted(ex.NOT_IMPLEMENTED)))

# --------------------------------------------------------------------------
# E1 - every offered format: write it, read it back, count what came out
# --------------------------------------------------------------------------
print("\n== E1: round-trip every VERIFIED format ==")


def count_csv_rows(path):
    with open(path, encoding="utf-8") as handle:
        rows = [line for line in handle
                if line.strip() and not line.startswith("#")]
    return max(len(rows) - 1, 0)                                # minus header


def read_back(key, path):
    """(n_records, note) read out of the written file, per format."""
    if key in ("csv_waypoints", "litchi"):
        return count_csv_rows(path), "righe"
    if key == "csv_photos":
        return count_csv_rows(path), "righe"
    if key == "mavlink":
        with open(path, encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if line.strip()]
        assert lines[0] == "QGC WPL 110"
        # header + one row per waypoint + take-off and RTL rows
        return len(lines) - 1, "righe WPL"
    if key == "gpx":
        text = open(path, encoding="utf-8").read()
        return text.count("<wpt "), "wpt"
    if key == "kml":
        text = open(path, encoding="utf-8").read()
        return text.count("<Placemark>"), "placemark"
    if key == "kmz":
        with zipfile.ZipFile(path) as archive:
            inner = [n for n in archive.namelist() if n.endswith(".kml")]
            text = archive.read(inner[0]).decode("utf-8")
        return text.count("<Placemark>"), "placemark nel kml"
    if key == "geojson":
        data = json.load(open(path, encoding="utf-8"))
        assert data["type"] == "FeatureCollection"
        points = [f for f in data["features"]
                  if f["geometry"]["type"] == "Point"]
        return len(points), "feature punto"
    if key == "gpkg":
        layer = QgsVectorLayer(path + "|layername=waypoints", "wp", "ogr")
        return (layer.featureCount() if layer.isValid() else -1), "feature"
    return -1, "?"


verified_keys = sorted(ex.FORMATS)
results = {}
for key in verified_keys:
    check_format(key, panel)
    planned = panel.refresh()
    written = panel.export()
    if len(written) != 1:
        check_true("{0}: one file written".format(key), False)
        continue
    path = written[0]
    size = os.path.getsize(path)
    count, unit = read_back(key, path)
    results[key] = (path, size, count, unit)
    print("        {0:<14} {1:>9,} B   {2:>5} {3}".format(key, size, count,
                                                          unit))
    check_true("{0}: the file exists and is not empty".format(key),
               os.path.isfile(path) and size > 0)
    check_true("{0}: badge is VERIFIED".format(key),
               ex.FORMATS[key].verified)

check("csv_waypoints holds every waypoint", results["csv_waypoints"][2], N_WP)
check("litchi holds every waypoint", results["litchi"][2], N_WP)
check("csv_photos holds every exposure", results["csv_photos"][2], N_PHOTOS)
check("gpx holds every waypoint", results["gpx"][2], N_WP)
check("geojson holds every waypoint", results["geojson"][2], N_WP)
check("gpkg holds every waypoint", results["gpkg"][2], N_WP)
check_true("mavlink has a row per waypoint plus take-off and RTL",
           results["mavlink"][2] >= N_WP)
check_true("kml has a placemark per waypoint",
           results["kml"][2] >= N_WP)
check_true("kmz carries the same kml", results["kmz"][2] == results["kml"][2])

# the Litchi header is the documented one, and the coordinates are degrees
with open(results["litchi"][0], encoding="utf-8") as handle:
    reader = csv.reader(handle)
    header = next(reader)
    first = next(reader)
check_true("litchi header matches the frozen column list",
           header == ex.LITCHI_HEADER)
check_true("litchi coordinates are WGS84 degrees",
           40.0 < float(first[0]) < 50.0 and 5.0 < float(first[1]) < 15.0)

# --------------------------------------------------------------------------
# E2 - WPML is refused
# --------------------------------------------------------------------------
print("\n== E2: DJI WPML ==")
before = sorted(os.listdir(OUT))
check_true("WPML is listed as not implemented", "dji_wpml" in ex.NOT_IMPLEMENTED)

wpml_item = None
for row in range(panel.format_list.count()):
    entry = panel.format_list.item(row)
    if entry.data(Qt.ItemDataRole.UserRole) == "dji_wpml":
        wpml_item = entry
check_true("WPML appears in the list with its reason", wpml_item is not None)
check_true("WPML is badged UNSUPPORTED", "UNSUPPORTED" in wpml_item.text())
check_true("WPML cannot be checked",
           not (wpml_item.flags() & Qt.ItemFlag.ItemIsUserCheckable))
check_true("...so it can never be selected for export",
           "dji_wpml" not in panel.selected_keys())

raised = None
try:
    ex.write(MISSION, "dji_wpml", os.path.join(OUT, "nope.kmz"),
             overwrite=True)
except Exception as exc:                                        # noqa: BLE001
    raised = exc
check_true("the writer raises UnsupportedFormatError",
           type(raised).__name__ == "UnsupportedFormatError")
check_true("the message is Italian",
           "non e' disponibile" in getattr(raised, "user_message", "").lower())
check_true("the reason names the unverified schema",
           "WPML" in getattr(raised, "hint", ""))
check_true("no file was written", sorted(os.listdir(OUT)) == before)
check_true("the panel prints the refusal to the operator",
           any("WPML" in line for line in ex.describe_unimplemented()))
print("        {0}".format(getattr(raised, "user_message", "")))

# --------------------------------------------------------------------------
# E3 - no mission
# --------------------------------------------------------------------------
print("\n== E3: without a mission ==")
empty_panel = ExportPanel(iface, lambda: None)
empty_panel.folder_edit.setText(OUT)
before = sorted(os.listdir(OUT))
empty_panel.refresh()
check_true("Esporta is disabled", not empty_panel.export_button.isEnabled())
ready, reason = empty_panel.readiness()
check_true("readiness says no", not ready)
check_true("the reason is Italian and not empty",
           bool(reason.strip()) and "missione" in reason.lower())
print("        {0}".format(reason))
check("export() writes nothing", len(empty_panel.export()), 0)
check_true("no file appeared", sorted(os.listdir(OUT)) == before)
check_true("the validation report is None without a mission",
           empty_panel.validate() is None)
empty_panel.teardown()

# a mission is present but no format is checked
check_format("__none__", panel)
ready, reason = panel.readiness()
check_true("no format checked -> not ready", not ready)
check_true("...and it says which choice is missing",
           "formato" in reason.lower())

# --------------------------------------------------------------------------
# E4 - the preview lists exactly the files that get written
# --------------------------------------------------------------------------
print("\n== E4: preview == what is written ==")
for row in range(panel.format_list.count()):
    entry = panel.format_list.item(row)
    if entry.flags() & Qt.ItemFlag.ItemIsUserCheckable:
        entry.setCheckState(Qt.CheckState.Checked)
panel.basename_edit.setText("anteprima")
planned = panel.refresh()
planned_paths = [path for _key, path in planned]
print("        {0} file previsti".format(len(planned_paths)))

for path in planned_paths:
    if os.path.exists(path):
        os.remove(path)
check_true("nothing exists before the export",
           not any(os.path.exists(path) for path in planned_paths))
check_true("the preview is shown to the operator, path by path",
           all(os.path.basename(path) in panel.report.toPlainText()
               for path in planned_paths))

written = panel.export()
check("every previewed file was written", len(written), len(planned_paths))
check_true("the names are identical, in the same order",
           [os.path.normcase(p) for p in written]
           == [os.path.normcase(p) for p in planned_paths])
check_true("each one is on disk",
           all(os.path.isfile(path) for path in written))
check("one file per offered format", len(written), len(ex.FORMATS))

# --------------------------------------------------------------------------
# E5 - a mission the validator rejects cannot be exported
# --------------------------------------------------------------------------
print("\n== E5: validation errors block the export ==")
# Not a doctored object: a mission planned at 200 m AGL, over the 120 m legal
# ceiling that uav.validator enforces. The engine calls it an error; the panel
# has to refuse to export it.
HIGH_MISSION = mi.build_mission(
    sv.prepare_aoi([AOI])[0][0], TERRAIN,
    mi.MissionParams(
        camera=_camera, drone=_drone, overlap=pg.Overlap(0.80, 0.70),
        h_agl_m=200.0, altitude_mode=AltitudeMode.TERRAIN,
        azimuth_strategy=sv.AZIMUTH_MANUAL, manual_azimuth_deg=0.0,
        v_mission_ms=10.0, compute_footprints=False),
    crs_authid=CRS.authid())

report = val.validate(HIGH_MISSION, crs=CRS)
codes = [c.code for c in report.errors]
print("        errori del validatore: {0}".format(codes))
check_true("the validator reports an error on a mission over the ceiling",
           bool(report.errors))
check_true("...and it is the legal-altitude check",
           "agl_over_legal" in codes)

broken_panel = ExportPanel(iface, lambda: HIGH_MISSION)
broken_panel.folder_edit.setText(OUT)
broken_panel.basename_edit.setText("fuori_limite")
check_format("csv_waypoints", broken_panel)
broken_panel.refresh()
before = sorted(os.listdir(OUT))

check_true("the panel picked the errors up",
           bool(broken_panel.last_report.errors))
check_true("Esporta is disabled", not broken_panel.export_button.isEnabled())
ready, reason = broken_panel.readiness()
check_true("readiness refuses", not ready)
check_true("the reason counts the errors, in Italian",
           "validazione" in reason.lower())
text = broken_panel.report.toPlainText()
check_true("every blocking error is listed with its label",
           all(item.label in text for item in broken_panel.last_report.errors))
check_true("the listed detail is the engine's own wording",
           all(item.detail[:30] in text
               for item in broken_panel.last_report.errors if item.detail))
print("        {0}".format(broken_panel.last_report.summary()))
check("export() writes nothing", len(broken_panel.export()), 0)
check_true("no file appeared", sorted(os.listdir(OUT)) == before)
check_true("not even a partial one",
           not any(name.startswith("fuori_limite") for name in os.listdir(OUT)))
broken_panel.teardown()

# warnings alone do not block
warn_report = panel.validate()
check_true("the good mission validates", warn_report.is_valid)
check_true("...and its warnings did not stop the export in E4",
           len(written) == len(ex.FORMATS))

# --------------------------------------------------------------------------
# E6 - an unwritable destination
# --------------------------------------------------------------------------
print("\n== E6: destination that does not exist ==")
missing = os.path.join(TMP, "non-esiste", "neanche-questa")
panel.folder_edit.setText(missing)
panel.basename_edit.setText("dovunque")
check_format("csv_waypoints", panel)
planned = panel.refresh()
result = panel.export()
check("nothing was written", len(result), 0)
check_true("the destination was not created", not os.path.isdir(missing))
check_true("no partial file anywhere",
           not any(name.startswith("dovunque") for name in os.listdir(OUT)))
check_true("the operator was told, in Italian",
           iface.messageBar().messages
           and "cartella" in str(iface.messageBar().messages[-1]).lower())
print("        {0}".format(str(iface.messageBar().messages[-1])[:120]))

panel.folder_edit.setText(OUT)
panel.refresh()
check_true("a valid folder makes it ready again", panel.readiness()[0])

# --------------------------------------------------------------------------
# R1 - the panel exports, it does not plan
# --------------------------------------------------------------------------
print("\n== R1: the panel is an adapter over uav.export ==")
PANEL_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "gui", "export_panel.py")
source = open(PANEL_PATH, encoding="utf-8").read()
tree = ast.parse(source)
imports = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imports.update(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        base = node.module or ""
        imports.add(base)
        imports.update("{0}.{1}".format(base, a.name) for a in node.names)
print("        imports: {0}".format(sorted(i for i in imports if i)))

check_true("no planner is imported",
           not any("survey" in name or name.endswith("uav.mission")
                   for name in imports))
check_true("plan_route and build_mission are never called",
           "plan_route" not in source and "build_mission(" not in source)
check_true("the panel writes through uav.export.write",
           "ex.write(" in source)
check_true("it defines no writer of its own",
           "def _write" not in source and "open(" not in source)
check_true("the validator is the frozen one", "val.validate(" in source)
check_true("the altitude mode comes from the settings store",
           'app_settings.get("export/altitude_mode")' in source)
import re                                                       # noqa: E402

code_only = re.sub(r"#.*", "", source)
code_only = re.sub(r'"""(?:.|\n)*?"""', "", code_only)
check_true("the panel never names a WPML key in code",
           "dji_wpml" not in code_only and "wpml" not in code_only.lower())
check_true("the refusal comes from the frozen table, not from here",
           "ex.NOT_IMPLEMENTED" in code_only)

for key in ex.FORMATS:
    check_true("{0} is offered by the panel".format(key),
               any(panel.format_list.item(row).data(Qt.ItemDataRole.UserRole)
                   == key for row in range(panel.format_list.count())))
check("the list offers every writer plus every refusal",
      panel.format_list.count(), len(ex.FORMATS) + len(ex.NOT_IMPLEMENTED))

panel.teardown()

print("\n" + "=" * 78)
QgsProject.instance().removeAllMapLayers()
DEM_LAYER = None
TERRAIN = None
QGS.exitQgis()
if SKIPS:
    print("SKIPPED ({0}):".format(len(SKIPS)))
    for label, reason in SKIPS:
        print("   - {0}: {1}".format(label, reason))
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for name in FAILURES:
        print("   - {0}".format(name))
    sys.exit(1)
print("ALL CHECKS PASSED")
