"""
M6 integration test: mission assembly -> validation -> export.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_mission.py
"""

import math
import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsCoordinateTransform, QgsGeometry, QgsProject)

from geocad_uav.core.models import AltitudeMode, VerticalDatum   # noqa: E402
from geocad_uav.core import z as zc                              # noqa: E402
from geocad_uav.io import layer_factory as lf                    # noqa: E402
from geocad_uav.uav import cameras as cam_lib                    # noqa: E402
from geocad_uav.uav import drones as drone_lib                   # noqa: E402
from geocad_uav.uav import export as ex                          # noqa: E402
from geocad_uav.uav import mission as mi                         # noqa: E402
from geocad_uav.uav import photogrammetry as pg                  # noqa: E402
from geocad_uav.uav import validator as va                       # noqa: E402

QGS = QgsApplication([], False)
QGS.initQgis()

FAILURES = []
TMP = tempfile.mkdtemp(prefix="geocad_test_")


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<56} got={2:<16.10g} exp={3:.10g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


# ------------------------------------------------------------- fixtures ---
CELL = 5.0
OX, OY = 500000.0, 5000000.0
NX, NY = 140, 120                       # 700 x 600 m window
GT = (OX, CELL, 0.0, OY, 0.0, -CELL)

xs = OX + (np.arange(NX) + 0.5) * CELL
ys = OY - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(xs, ys)
# A 12 % slope plus a 40 m hill: enough relief that a fixed AMSL would be wrong.
Z = (300.0 + 0.12 * (XX - OX)
     + 40.0 * np.exp(-(((XX - OX - 350.0) ** 2 + (YY - OY + 300.0) ** 2)
                       / (2.0 * 120.0 ** 2))))
TERRAIN = zc.TerrainModel(Z, GT, "EPSG:32632", source="DTM sintetico",
                          is_surface_model=False,
                          vertical_datum=VerticalDatum.ORTHOMETRIC_EGM96)

AOI = QgsGeometry.fromWkt(
    "POLYGON(({0} {1}, {2} {1}, {2} {3}, {0} {3}, {0} {1}))".format(
        OX + 120.0, OY - 480.0, OX + 520.0, OY - 120.0))

CAM = cam_lib.load_library()["dji_mavic3e"]
DRONE = drone_lib.load_library()["dji_mavic3e"]
H_AGL = 80.0

print("\n== spec workflow D: H_AGL 80 m, overlap 80/70, V 8 m/s ==")
params = mi.MissionParams(
    camera=CAM, drone=DRONE,
    overlap=pg.Overlap(frontlap=0.80, sidelap=0.70),
    h_agl_m=H_AGL, altitude_mode=AltitudeMode.TERRAIN,
    v_mission_ms=8.0, vertical_datum=VerticalDatum.ORTHOMETRIC_EGM96,
    compute_footprints=True, footprint_edge_samples=1)

mission = mi.build_mission(AOI, TERRAIN, params, crs_authid="EPSG:32632")

# -- golden photogrammetric values at H = 80 m -----------------------------
print("\n== golden values, DJI Mavic 3E @ 80 m ==")
check("GSD [m/px]", mission.gsd_m, 80.0 * (17.3 / 5280.0) / 12.29, 1e-12)
check("GSD [cm/px]", mission.gsd_m * 100.0, 2.13280072, 1e-6)
geom = pg.solve_survey_geometry(CAM, params.overlap, h_agl_m=H_AGL)
check("footprint W [m]", geom.footprint_across_m, 112.6118796, 1e-6)
check("footprint L [m]", geom.footprint_along_m, 84.62164361, 1e-6)
check("D_side [m]", geom.d_side_m, 33.78356389, 1e-6)
check("D_front [m]", geom.d_front_m, 16.92432872, 1e-6)

print("\n== mission structure ==")
st = mission.stats
print("        strips={0} photos={1} waypoints={2} length={3:.0f} m "
      "time={4:.1f} min batteries={5}".format(
          st.n_strips, st.n_photos, st.n_waypoints, st.total_length_m,
          st.flight_time_s / 60.0, st.n_batteries))
check_true("mission has waypoints", st.n_waypoints > 0)
check_true("mission has photos", st.n_photos > 0)
check_true("mission has strips", st.n_strips >= 4)
check_true("flight lines were produced", len(mission.lines) > 0)
check_true("footprints were draped", len(mission.footprints) == st.n_photos)

# -- ACCEPTANCE: every waypoint sits on the DEM, not on a flat plane -------
print("\n== acceptance: Z = DEM + H_AGL at every waypoint ==")
wx = np.array([w.x for w in mission.waypoints])
wy = np.array([w.y for w in mission.waypoints])
wz = np.array([w.z_amsl for w in mission.waypoints])
z_dem = TERRAIN.sample(wx, wy)
residual = np.abs(wz - (z_dem + H_AGL))
check("max |Z_wp - (Z_DEM + H_AGL)| [m]", float(np.nanmax(residual)), 0.0, 1e-6)
check_true("no waypoint has a null height",
           bool(np.all(np.abs(wz) > 1.0)))
check_true("commanded heights are NOT a single flat AMSL",
           float(wz.max() - wz.min()) > 30.0)
print("        commanded height spans {0:.1f} m, terrain spans {1:.1f} m"
      .format(float(wz.max() - wz.min()), st.terrain_relief_m))

agl = np.array([w.z_agl for w in mission.waypoints])
check("AGL is constant at the nominal height (spread)",
      float(np.nanmax(agl) - np.nanmin(agl)), 0.0, 1e-6)
check("AGL equals H_AGL", float(np.nanmean(agl)), H_AGL, 1e-6)
check_true("no waypoint below the minimum AGL",
           float(np.nanmin(agl)) >= H_AGL - 1e-6)

# -- exposures -------------------------------------------------------------
print("\n== exposures ==")
gsd = np.array([p.gsd_m for p in mission.photos])
check_true("every photo has a finite GSD", bool(np.all(np.isfinite(gsd))))
check("effective GSD equals nominal on constant AGL",
      float(np.nanmax(np.abs(gsd - mission.gsd_m))), 0.0, 1e-9)
check_true("effective GSD within +15 % of target",
           float(gsd.max()) <= mission.gsd_m * 1.15 + 1e-12)

photo_wp = [w for w in mission.waypoints if w.is_photo]
check("photo waypoints match exposure count", len(photo_wp), st.n_photos)
check_true("every exposure carries a takePhoto action",
           all("takePhoto" in w.actions for w in photo_wp))

# exposure spacing along a strip must equal D_front
first_strip = min(p.strip_index for p in mission.photos)
strip_photos = [p for p in mission.photos if p.strip_index == first_strip]
if len(strip_photos) > 2:
    d = [math.hypot(b.x - a.x, b.y - a.y)
         for a, b in zip(strip_photos, strip_photos[1:])]
    check("exposure spacing equals D_front", float(np.median(d)),
          geom.d_front_m, 1e-6)

# -- waypoint thinning -----------------------------------------------------
print("\n== waypoint densification ==")
prof = np.array([[r["s"], r["z_flight"]] for r in mission.profile])
check_true("elevation profile was recorded", prof.shape[0] > st.n_waypoints)
check_true("waypoints are fewer than raw DEM samples (thinning works)",
           st.n_waypoints < prof.shape[0])

flat_terrain = zc.TerrainModel(np.full((NY, NX), 250.0), GT, "EPSG:32632")
flat_params = mi.MissionParams(
    camera=CAM, drone=DRONE, overlap=pg.Overlap(0.80, 0.70), h_agl_m=H_AGL,
    v_mission_ms=8.0, compute_footprints=False)
flat_mission = mi.build_mission(AOI, flat_terrain, flat_params, "EPSG:32632")
check_true("flat terrain needs fewer waypoints than rough terrain "
           "({0} vs {1})".format(flat_mission.stats.n_waypoints,
                                 st.n_waypoints),
           flat_mission.stats.n_waypoints < st.n_waypoints)

# -- altitude modes --------------------------------------------------------
print("\n== altitude modes ==")
strip_params = mi.MissionParams(
    camera=CAM, drone=DRONE, overlap=pg.Overlap(0.80, 0.70), h_agl_m=H_AGL,
    altitude_mode=AltitudeMode.STRIP_AMSL, v_mission_ms=8.0,
    compute_footprints=False)
strip_mission = mi.build_mission(AOI, TERRAIN, strip_params, "EPSG:32632")
per_strip = {}
for w in strip_mission.waypoints:
    per_strip.setdefault(w.strip_index, set()).add(round(w.z_amsl, 6))
check_true("strip-AMSL mode holds one height per strip",
           all(len(v) == 1 for v in per_strip.values()))
check_true("strip-AMSL mode still varies between strips", len(per_strip) > 1)

try:
    mi.build_mission(AOI, TERRAIN, mi.MissionParams(
        camera=CAM, drone=DRONE, overlap=pg.Overlap(0.80, 0.70), h_agl_m=H_AGL,
        altitude_mode=AltitudeMode.SINGLE_AMSL, compute_footprints=False),
        "EPSG:32632")
    check_true("single-AMSL refused when relief exceeds 10 % of H_AGL", False)
except Exception as exc:                                        # noqa: BLE001
    check_true("single-AMSL refused when relief exceeds 10 % of H_AGL",
               "dislivello" in str(getattr(exc, "user_message", "")).lower()
               or "relief" in str(exc).lower())

flat_single = mi.build_mission(AOI, flat_terrain, mi.MissionParams(
    camera=CAM, drone=DRONE, overlap=pg.Overlap(0.80, 0.70), h_agl_m=H_AGL,
    altitude_mode=AltitudeMode.SINGLE_AMSL, compute_footprints=False),
    "EPSG:32632")
check_true("single-AMSL allowed on flat ground",
           len({round(w.z_amsl, 6) for w in flat_single.waypoints}) == 1)

# -- validation ------------------------------------------------------------
print("\n== validator ==")
crs = QgsCoordinateReferenceSystem("EPSG:32632")
report = va.validate(mission, params, TERRAIN, AOI, crs)
print("        {0}".format(report.summary()))
for c in report.checks:
    if not c.passed:
        print("        [{0}] {1}: {2}".format(c.severity, c.label, c.detail))
check_true("validator ran a meaningful number of checks", len(report.checks) >= 15)
check_true("projected CRS accepted",
           any(c.code == "crs_projected" and c.passed for c in report.checks))
check_true("no zero-height waypoints",
           any(c.code == "zero_z" and c.passed for c in report.checks))
check_true("AGL minimum satisfied",
           any(c.code == "agl_below_min" and c.passed for c in report.checks))
check_true("GSD tolerance satisfied",
           any(c.code == "gsd_tolerance" and c.passed for c in report.checks))
check_true("DTM without vegetation clearance is warned about",
           any(c.code == "dtm_no_clearance" for c in report.checks))

geo_report = va.validate(mission, params, TERRAIN, AOI,
                         QgsCoordinateReferenceSystem("EPSG:4326"))
check_true("a geographic CRS is a blocking error",
           not geo_report.is_valid
           and any(c.code == "crs_geographic" for c in geo_report.errors))

print("\n== coverage on the draped footprints ==")
pct, min_seen = va.coverage_fraction(mission, AOI, params.min_photos_per_point,
                                     cell_m=10.0)
print("        coverage {0:.2f} %, minimum photos seen {1}".format(pct, min_seen))
check_true("AOI is 100 % covered by at least 3 photos", pct >= 99.999)
check_true("minimum observed photo count meets the requirement", min_seen >= 3)

# -- export ----------------------------------------------------------------
print("\n== export ==")
to_wgs84 = QgsCoordinateTransform(
    crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())

written = {}
for key in ("csv_waypoints", "csv_photos", "litchi", "mavlink", "gpx",
            "kml", "kmz", "geojson", "gpkg"):
    fmt = ex.FORMATS[key]
    out = os.path.join(TMP, "mission_" + key + fmt.extension)
    kwargs = {"crs": "EPSG:32632"} if key == "gpkg" else {}
    ex.write(mission, key, out, transform=to_wgs84, overwrite=True, **kwargs)
    size = os.path.getsize(out)
    written[key] = out
    check_true("{0:<14} written ({1:,} bytes)".format(key, size), size > 0)

# refuses to overwrite unless told to
try:
    ex.write(mission, "gpx", written["gpx"], transform=to_wgs84)
    check_true("refuses silent overwrite", False)
except Exception as exc:                                        # noqa: BLE001
    check_true("refuses silent overwrite", "esiste" in str(
        getattr(exc, "user_message", "")).lower())

# v1.39.0: WPML is written, from DJI's published schema. This fixture
# flies a Mavic 3 Enterprise, which DJI lists, so the file is produced --
# and it is a real wpmz archive, not a renamed KMZ.
import zipfile as _zipfile                                       # noqa: E402

wpml_path = os.path.join(TMP, "missione_wpml.kmz")
ex.write(mission, "dji_wpml", wpml_path, transform=to_wgs84,
         altitude_mode=ex.ALT_RELATIVE_HOME, home_z=300.0, overwrite=True)
with _zipfile.ZipFile(wpml_path) as _archive:
    _wpml_names = sorted(_archive.namelist())
    _waylines = _archive.read("wpmz/waylines.wpml").decode("utf-8")
check_true("WPML writes the two files DJI names",
           _wpml_names == ["wpmz/template.kml", "wpmz/waylines.wpml"])
check_true("...with DJI's own namespace",
           'xmlns:wpml="http://www.dji.com/wpmz/1.0.2"' in _waylines)
check_true("KMZ still states it is not a native DJI mission",
           "NON e' un file di missione DJI nativa" in ex.FORMATS["kmz"].notes
           or "NON e'" in ex.FORMATS["kmz"].notes)

# -- verify export content -------------------------------------------------
print("\n== export content ==")
with open(written["litchi"], encoding="utf-8") as handle:
    header = handle.readline().strip().split(",")
    rows = handle.readlines()
check("Litchi header column count", len(header), len(ex.LITCHI_HEADER))
check_true("Litchi header matches the documented order",
           header == ex.LITCHI_HEADER)
check("Litchi row count equals waypoint count", len(rows), st.n_waypoints)
first_row = rows[0].split(",")
lat, lon = float(first_row[0]), float(first_row[1])
check_true("Litchi latitude looks like WGS84 degrees", 40.0 < lat < 50.0)
check_true("Litchi longitude looks like WGS84 degrees", 5.0 < lon < 15.0)

with open(written["mavlink"], encoding="utf-8") as handle:
    wpl = handle.read().splitlines()
check_true("QGC WPL 110 header", wpl[0] == "QGC WPL 110")
check("QGC WPL columns per row", len(wpl[1].split("\t")), 12)
check_true("first item is flagged current", wpl[1].split("\t")[1] == "1")
check_true("mission ends with RTL", wpl[-1].split("\t")[3] == "20")

import json                                                     # noqa: E402
with open(written["geojson"], encoding="utf-8") as handle:
    gj = json.load(handle)
check_true("GeoJSON is a FeatureCollection", gj["type"] == "FeatureCollection")
check("GeoJSON feature count = waypoints + route", len(gj["features"]),
      st.n_waypoints + 1)
check_true("GeoJSON coordinates carry elevation",
           len(gj["features"][0]["geometry"]["coordinates"]) == 3)

import zipfile                                                  # noqa: E402
with zipfile.ZipFile(written["kmz"]) as archive:
    names = archive.namelist()
check_true("KMZ contains doc.kml", "doc.kml" in names)
check_true("KMZ ships the not-DJI disclaimer", "LEGGIMI.txt" in names)

# -- QGIS layers -----------------------------------------------------------
print("\n== QGIS layers ==")
layers = lf.build_mission_layers(mission, "EPSG:32632")
check_true("flight_lines built", layers["flight_lines"].featureCount() > 0)
check("waypoints layer count", layers["waypoints"].featureCount(), st.n_waypoints)
check("photo_centers layer count", layers["photo_centers"].featureCount(),
      st.n_photos)
check("footprints layer count", layers["photo_footprints"].featureCount(),
      st.n_photos)
check_true("waypoint layer is PointZ",
           layers["waypoints"].wkbType() == 1001                # QgsWkbTypes.PointZ
           or "Z" in layers["waypoints"].dataProvider().description())
sample = next(layers["waypoints"].getFeatures())
check_true("waypoint geometry carries Z",
           abs(sample.geometry().constGet().z()) > 1.0)
check_true("waypoint attributes populated",
           sample["z_agl"] is not None and sample["speed"] is not None)

cov = os.path.join(TMP, "coverage.tif")
gsd_tif = os.path.join(TMP, "gsd.tif")
lf.build_coverage_rasters(mission, AOI, cov, gsd_tif, crs.toWkt(), cell_m=15.0)
check_true("coverage raster written", os.path.getsize(cov) > 0)
check_true("gsd raster written", os.path.getsize(gsd_tif) > 0)

from osgeo import gdal                                          # noqa: E402
gdal.UseExceptions()
ds = gdal.Open(cov)
arr = ds.GetRasterBand(1).ReadAsArray()
ds = None
check_true("coverage raster reports at least 3 photos over the AOI core",
           float(np.max(arr)) >= 3.0)
ds = gdal.Open(gsd_tif)
garr = ds.GetRasterBand(1).ReadAsArray()
ds = None
valid = garr[garr > 0]
check_true("gsd raster is near the nominal GSD",
           valid.size > 0
           and abs(float(np.median(valid)) - mission.gsd_m * 100.0) < 0.2)

print("\n== assumptions recorded ==")
for line in mission.assumptions[:6]:
    print("        - {0}".format(line))
check_true("assumptions list the vertical datum",
           any("verticale" in a.lower() for a in mission.assumptions))
check_true("assumptions list the DEM sampling step",
           any("campionamento" in a.lower() for a in mission.assumptions))
check_true("assumptions warn about DTM without clearance",
           any("DTM" in a for a in mission.assumptions))

# -- report ----------------------------------------------------------------
print("\n== mission report ==")
from geocad_uav.gui import mission_report as mr                  # noqa: E402

html_doc = mr.build_html(mission, params, report, geom)
check_true("report is a complete HTML document",
           html_doc.startswith("<!DOCTYPE html")
           and html_doc.rstrip().endswith("</html>"))
for token in ("Profilo altimetrico", "GSD nominale", "D_side", "D_front",
              "Datum verticale", "Assunzioni dichiarate", "Copertura AOI"):
    check_true("report contains '{0}'".format(token), token in html_doc)
check_true("report embeds an inline SVG profile (no matplotlib)",
           "<svg" in html_doc and "</svg>" in html_doc)
check_true("report draws terrain and flight curves",
           html_doc.count("<path") >= 3)
check_true("report states the camera parameter source",
           "Fonte parametri" in html_doc)

report_path = os.path.join(TMP, "report.html")
mr.save_html(mission, report_path, params, report, geom, overwrite=True)
check_true("report written to disk", os.path.getsize(report_path) > 4000)
try:
    mr.save_html(mission, report_path, params, report, geom)
    check_true("report refuses silent overwrite", False)
except Exception:                                               # noqa: BLE001
    check_true("report refuses silent overwrite", True)

summary = mr.build_text_summary(mission, report)
check_true("text summary has the key figures", len(summary) >= 4)
print("        " + "\n        ".join(summary))

print("\n" + "=" * 80)
shutil.rmtree(TMP, ignore_errors=True)
QGS.exitQgis()
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for f_ in FAILURES:
        print("   - {0}".format(f_))
    sys.exit(1)
print("ALL CHECKS PASSED")
