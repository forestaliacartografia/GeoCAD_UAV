"""
v1.3.2: the UAV tab.

The panel plans nothing. Every number here is checked against the frozen
engine called independently on the same geometry with the same parameters --
``uav.mission.build_mission`` over ``uav.survey.plan_route`` and
``uav.terrain_follow.build_flight_profile`` -- never against a constant typed
into the test.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_uav_panel.py
"""

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsGeometry, QgsProject, QgsRasterLayer, QgsRectangle)

QGS = QgsApplication([], False)
QGS.initQgis()

from osgeo import gdal, osr                                     # noqa: E402
from qgis.gui import QgsMapCanvas                               # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow, QPushButton        # noqa: E402

from geocad_uav.core.models import AltitudeMode                 # noqa: E402
from geocad_uav.core.z import TerrainModel                      # noqa: E402
from geocad_uav.gui import uav_panel as up                      # noqa: E402
from geocad_uav.gui.extent_source import ExtentSource           # noqa: E402
from geocad_uav.gui.uav_panel import UavPanel                   # noqa: E402
from geocad_uav.uav import mission as mi                        # noqa: E402
from geocad_uav.uav import photogrammetry as pg                 # noqa: E402
from geocad_uav.uav import survey as sv                         # noqa: E402

FAILURES = []
SKIPS = []
CRS = QgsCoordinateReferenceSystem("EPSG:32632")
TMP = tempfile.mkdtemp(prefix="geocad_uav_panel_")

OX, OY = 500000.0, 5000000.0
CELL = 5.0
NX, NY = 240, 200
SLOPE = 0.08                       # a clean 8 % ramp: bilinear sampling exact


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
xs = OX + (np.arange(NX) + 0.5) * CELL
ys = OY - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(xs, ys)
Z = 300.0 + SLOPE * (XX - OX)

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
QgsProject.instance().addMapLayer(DEM_LAYER)

# 500 x 300 m, well inside the DEM window.
AOI_WKT = "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
    OX + 100.0, OY - 700.0, OX + 600.0, OY - 400.0)
AOI = QgsGeometry.fromWkt(AOI_WKT)

H_AGL = 80.0
MARGIN = 5.0
SPEED_KMH = 36.0
FRONTLAP, SIDELAP = 80.0, 70.0


class CountingCanvas(QgsMapCanvas):
    def __init__(self):
        super().__init__()
        self.refresh_calls = 0

    def refresh(self):                                          # noqa: N802
        self.refresh_calls += 1
        super().refresh()


class FakeMessageBar:
    def __init__(self):
        self.messages = []

    def pushMessage(self, *args, **kwargs):                     # noqa: N802
        self.messages.append(args)


class FakeIface:
    def __init__(self):
        self._window = QMainWindow()
        self._canvas = CountingCanvas()
        self._canvas.setDestinationCrs(CRS)
        self._canvas.setExtent(QgsRectangle(OX, OY - 900, OX + 800, OY))
        self._bar = FakeMessageBar()

    def mainWindow(self):                                       # noqa: N802
        return self._window

    def mapCanvas(self):                                        # noqa: N802
        return self._canvas

    def messageBar(self):                                       # noqa: N802
        return self._bar


def engine_mission(panel):
    """Run the frozen assembler independently, on the same AOI and DEM."""
    camera = panel.current_camera()
    drone = panel.current_drone()
    geometry = pg.solve_survey_geometry(
        camera, pg.Overlap(FRONTLAP / 100.0, SIDELAP / 100.0), h_agl_m=H_AGL)
    box = AOI.boundingBox()
    terrain, _warnings = TerrainModel.from_layer(
        DEM_LAYER, CRS,
        (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()),
        margin_m=max(geometry.footprint_across_m, geometry.footprint_along_m))
    params = mi.MissionParams(
        camera=camera, drone=drone,
        overlap=pg.Overlap(FRONTLAP / 100.0, SIDELAP / 100.0),
        h_agl_m=H_AGL, altitude_mode=AltitudeMode.TERRAIN,
        safety_margin_m=MARGIN,
        azimuth_strategy=sv.AZIMUTH_MANUAL, manual_azimuth_deg=0.0,
        v_mission_ms=SPEED_KMH / 3.6, compute_footprints=False)
    return mi.build_mission(sv.prepare_aoi([AOI])[0][0], terrain, params,
                            crs_authid=CRS.authid()), terrain, geometry


iface = FakeIface()
canvas = iface.mapCanvas()

panel = UavPanel(iface)
panel.extent.set_extent(AOI, CRS)
panel.h_agl.setValue(H_AGL)
panel.speed_kmh.setValue(SPEED_KMH)
panel.frontlap.setValue(FRONTLAP)
panel.sidelap.setValue(SIDELAP)
panel.safety_margin.setValue(MARGIN)
panel.azimuth.setValue(0.0)

# --------------------------------------------------------------------------
# V1 - the panel produces exactly what the engine produces
# --------------------------------------------------------------------------
print("\n== V1: 500 x 300 AOI, H_AGL 80 m, 80/70, DEM ramp ==")
check_true("the DEM raster is valid", DEM_LAYER.isValid())
check_true("the panel found the DEM without a layerChanged signal",
           panel.dem_layer() is DEM_LAYER)

mission = panel.generate()
reference, terrain, geom_survey = engine_mission(panel)

print("        panel {0} wp / {1} photos, engine {2} wp / {3} photos".format(
    len(mission.waypoints), len(mission.photos),
    len(reference.waypoints), len(reference.photos)))
check("waypoint count equals build_mission on the same geometry",
      len(mission.waypoints), len(reference.waypoints))
check("photo count equals the engine", len(mission.photos),
      len(reference.photos))
check("strip count equals the engine", mission.stats.n_strips,
      reference.stats.n_strips)
check_true("the mission is not trivially empty", len(mission.waypoints) > 0)

# ...and the strips themselves come from uav.survey, not from the panel.
route = sv.plan_route(
    AOI, d_side_m=geom_survey.d_side_m, d_front_m=geom_survey.d_front_m,
    footprint_across_m=geom_survey.footprint_across_m,
    footprint_along_m=geom_survey.footprint_along_m,
    azimuth_strategy=sv.AZIMUTH_MANUAL, manual_azimuth_deg=0.0,
    terrain=terrain)
check("strips equal uav.survey.plan_route on the same AOI",
      mission.stats.n_strips, route.n_strips)
check("the flown azimuth is the one asked for", mission.azimuth_deg, 0.0)

# --------------------------------------------------------------------------
# V2 - terrain following, to the metre it claims
# --------------------------------------------------------------------------
print("\n== V2: Z_wp == Z_raster + H_AGL + margine, on a known ramp ==")
xy = np.array([[wp.x, wp.y] for wp in mission.waypoints], dtype=float)
z_wp = np.array([wp.z_amsl for wp in mission.waypoints], dtype=float)
z_terrain = terrain.sample(xy[:, 0], xy[:, 1])
delta = np.abs(z_wp - (z_terrain + H_AGL + MARGIN))
print("        max |Z_wp - (Z_raster + H_AGL + margin)| = {0:.3e} m".format(
    float(np.nanmax(delta))))
check("max |dZ| against the sampled DEM", float(np.nanmax(delta)), 0.0, 1e-6)
check_true("no waypoint has a NaN height", bool(np.isfinite(z_wp).all()))

agl = np.array([wp.z_agl for wp in mission.waypoints], dtype=float)
check("min AGL", float(agl.min()), H_AGL + MARGIN, 1e-6)
check_true("no waypoint flies below H_AGL", bool((agl >= H_AGL - 1e-9).all()))

# The DEM is a plane, so the analytical elevation is known independently of
# the sampler: this catches a sampler that is self-consistently wrong.
z_true = 300.0 + SLOPE * (xy[:, 0] - OX)
check("max |dZ| against the analytical ramp",
      float(np.nanmax(np.abs(z_wp - (z_true + H_AGL + MARGIN)))), 0.0, 1e-3)

check("the altitude mode really is terrain following",
      1.0 if mission.altitude_mode == AltitudeMode.TERRAIN else 0.0, 1.0)

# --------------------------------------------------------------------------
# V3 - no DEM, no mission
# --------------------------------------------------------------------------
print("\n== V3: without a DEM the panel refuses, in Italian ==")
before_layers = len(QgsProject.instance().mapLayers())
naked = UavPanel(iface)
naked.extent.set_extent(AOI, CRS)
naked.dem_combo.setLayer(None)
naked.recompute()

check_true("the DEM combo really is empty", naked.dem_layer() is None)
check_true("Genera is disabled", not naked.generate_button.isEnabled())
ready, reason = naked.readiness()
check_true("readiness says no", not ready)
check_true("the reason is not empty", bool(reason.strip()))
check_true("the reason names terrain following",
           "terrain following" in reason.lower())
check_true("the reason is shown next to the combo",
           bool(naked.dem_note.text().strip()))
print("        reason: {0}".format(reason[:96]))

check_true("generate() returns nothing", naked.generate() is None)
check_true("no mission was kept", naked.last_mission is None)
check("no layer was created without a DEM",
      len(QgsProject.instance().mapLayers()), before_layers)
check("confirm without a mission writes nothing", len(naked.confirm()), 0)
check("still no layer", len(QgsProject.instance().mapLayers()), before_layers)
naked.teardown()

# --------------------------------------------------------------------------
# V4 - km/h stays in the GUI
# --------------------------------------------------------------------------
print("\n== V4: 36 km/h reaches the engine as 10.0 m/s ==")
panel.speed_kmh.setValue(36.0)
check("the panel converts once", panel.speed_ms(), 10.0, 1e-12)
params = panel.build_params()
check("MissionParams.v_mission_ms", params.v_mission_ms, 10.0, 1e-12)
check("KMH_TO_MS is 1/3.6", up.KMH_TO_MS * 36.0, 10.0, 1e-12)

field_names = [f for f in params.__dataclass_fields__]
check_true("no km/h field reaches the engine",
           not any("kmh" in name.lower() or "km_h" in name.lower()
                   for name in field_names))
check_true("no engine argument carries a km/h value",
           not any(isinstance(getattr(params, name), float)
                   and abs(getattr(params, name) - 36.0) < 1e-12
                   for name in field_names))
check("108 km/h is 30 m/s", up.KMH_TO_MS * 108.0, 30.0, 1e-12)

# the read-only interval follows D_front / v and cannot be typed into
geom_now = panel.survey_geometry()
check("interval_s == D_front / v", panel.interval_s(),
      geom_now.d_front_m / 10.0, 1e-12)
check_true("the interval field is read only", panel.interval.isReadOnly())
panel.speed_kmh.setValue(72.0)
check("the interval halves when the speed doubles", panel.interval_s(),
      geom_now.d_front_m / 20.0, 1e-12)
check_true("...and the field followed",
           panel.interval.text().startswith(
               "{0:.2f}".format(geom_now.d_front_m / 20.0)))
panel.speed_kmh.setValue(SPEED_KMH)

# --------------------------------------------------------------------------
# V5 - previewing writes nothing, confirming writes once
# --------------------------------------------------------------------------
print("\n== V5: 100 route previews write nothing ==")
before_layers = len(QgsProject.instance().mapLayers())
baseline_refresh = canvas.refresh_calls
for step in range(100):
    panel.h_agl.setValue(H_AGL + (step % 3) * 0.01)
    panel.generate()
panel.h_agl.setValue(H_AGL)
preview = panel.generate()

check("no layer created by 100 previews",
      len(QgsProject.instance().mapLayers()), before_layers)
check("no canvas.refresh() during previews",
      canvas.refresh_calls - baseline_refresh, 0)
check_true("the route is on a rubber band", panel._band is not None)
check("the band carries the whole route", panel._band.numberOfVertices(),
      sum(len(line) for line in preview.lines))

layers = panel.confirm()
check("confirm created the mission layers", len(layers), 3)
check("exactly three new layers in the project",
      len(QgsProject.instance().mapLayers()), before_layers + 3)
check("the waypoint layer holds the previewed waypoints",
      layers["waypoints"].featureCount(), len(preview.waypoints))
check("the photo layer holds the previewed exposures",
      layers["photo_centers"].featureCount(), len(preview.photos))
check_true("every written layer is valid",
           all(layer.isValid() for layer in layers.values()))
check_true("the preview band was cleared after the write",
           panel._band.numberOfVertices() == 0)

# --------------------------------------------------------------------------
# R1 - the frozen engine is the one doing the work
# --------------------------------------------------------------------------
print("\n== R1: the panel is an adapter, not a second planner ==")
source = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "gui", "uav_panel.py"),
    encoding="utf-8").read()
check_true("the panel calls the frozen assembler",
           "mission_mod.build_mission(" in source)
check_true("the panel calls the frozen AOI preparation",
           "sv.prepare_aoi(" in source)
check_true("the panel writes through the frozen layer factory",
           "lf.build_mission_layers(" in source)
check_true("the panel defines no planner of its own",
           "def plan_route" not in source
           and "def build_flight_profile" not in source
           and "def build_mission" not in source)
check_true("the AOI picker is the shared one",
           isinstance(panel.extent, ExtentSource))
check_true("the panel adds no second AOI selector",
           source.count("ExtentSource(") == 1)
check_true("terrain following is the only altitude mode offered",
           "AltitudeMode.TERRAIN" in source
           and "SINGLE_AMSL" not in source
           and "STRIP_AMSL" not in source)
check_true("no online elevation source",
           not any(word in source.lower() for word in
                   ("http://", "https://", "api_key", "apikey", "google",
                    "copernicus", "nasadem", "tinitaly", "opentopography")))

# --------------------------------------------------------------------------
# R2 - no export button, no WPML
# --------------------------------------------------------------------------
print("\n== R2: no export controls on this tab ==")
buttons = panel.findChildren(QPushButton)
labels = [b.text() for b in buttons]
print("        buttons: {0}".format(labels))
check_true("no button mentions export or WPML",
           not any(word in label.lower() for label in labels
                   for word in ("export", "esport", "wpml", "kmz", "litchi")))
check_true("the panel source names no exporter",
           "export" not in source.lower().replace("# ", ""))
check_true("no simulator either",
           "simulat" not in source.lower())

panel.teardown()

print("\n" + "=" * 78)
QgsProject.instance().removeAllMapLayers()
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
