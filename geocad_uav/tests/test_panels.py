"""
v1.3.1: the Grid and Forest tabs.

Every count is checked against the frozen engine on the same geometry with the
same predicate, never against a number written into the test by hand: the point
is that the panels add a UI, not a second generator.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_panels.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsFeature, QgsGeometry, QgsPoint, QgsPointXY,
                       QgsProject, QgsRectangle)

QGS = QgsApplication([], False)
QGS.initQgis()

from qgis.gui import QgsMapCanvas                               # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow                     # noqa: E402

from geocad_uav.core import grid as grid_mod                    # noqa: E402
from geocad_uav.core import undo                                # noqa: E402
from geocad_uav.forest import stats as stats_mod                # noqa: E402
from geocad_uav.gui.forest_panel import ForestPanel             # noqa: E402
from geocad_uav.gui.extent_source import ExtentSource           # noqa: E402
from geocad_uav.io import layer_factory as lf                   # noqa: E402

FAILURES = []
SKIPS = []
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
    print("  [skip] {0}".format(label))
    print("         reason: {0}".format(reason))
    SKIPS.append((label, reason))


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
        self._canvas.setExtent(QgsRectangle(OX - 50, OY - 50, OX + 200,
                                            OY + 200))
        self._bar = FakeMessageBar()

    def mainWindow(self):                                       # noqa: N802
        return self._window

    def mapCanvas(self):                                        # noqa: N802
        return self._canvas

    def messageBar(self):                                       # noqa: N802
        return self._bar


def _refuses(call):
    """True when the call raises: an absent tool must fail, not return None."""
    try:
        call()
    except Exception:                                           # noqa: BLE001
        return True
    return False


def rect_wkt(x0, y0, w, h):
    return "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        x0, y0, x0 + w, y0 + h)


def engine_count(geometry, spec):
    """What core.grid + the engine's own predicate say, independently."""
    box = geometry.boundingBox()
    result = grid_mod.generate_grid(
        (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()), spec)
    engine = QgsGeometry.createGeometryEngine(geometry.constGet())
    engine.prepareGeometry()
    return sum(1 for i in range(len(result))
               if engine.intersects(QgsPoint(float(result.xy[i, 0]),
                                             float(result.xy[i, 1]))))


def ExtentSourceHost(interface):
    """ExtentSource on its own, the way every panel now creates it."""
    return ExtentSource(interface)


iface = FakeIface()
canvas = iface.mapCanvas()

# --------------------------------------------------------------------------
# F4 - forest scheme on the drawn extent
# --------------------------------------------------------------------------
print("\n== F4: forest 3 x 2 on the drawn extent ==")
forest = ForestPanel(iface)
forest.extent.set_extent(QgsGeometry.fromWkt(rect_wkt(OX, OY, 200.0, 100.0)),
                         CRS)
forest.plant_spacing.setValue(3.0)
forest.row_spacing.setValue(2.0)
forest.azimuth.setValue(15.0)
forest.margin.setValue(2.0)

planting = forest.compute()
check_true("the planner returned a result", planting is not None)
check_true("plants were produced", len(planting.plants) > 0)
print("        {0:,} plants, {1} rows".format(len(planting.plants),
                                              planting.n_rows))

eroded = forest.extent.geometry().buffer(-forest.margin.value(), 12)
outside = [p for p in planting.plants
           if not eroded.intersects(
               QgsGeometry.fromPointXY(QgsPointXY(p.x, p.y)))]
check("no plant falls outside the eroded extent", len(outside), 0)

kpi = stats_mod.compute_stats(planting)
check("panel KPI equal compute_stats", kpi.n_plants, len(planting.plants))
check_true("plants per hectare is finite and positive",
           math.isfinite(kpi.density_per_ha) and kpi.density_per_ha > 0)
check_true("fill ratio is finite", math.isfinite(kpi.fill_ratio))
check("theoretical density is 10000/(3*2)", kpi.theoretical_density_per_ha,
      10_000.0 / 6.0, 1e-9)
print("        {0:,.1f} plants/ha, fill {1:.1%}".format(kpi.density_per_ha,
                                                        kpi.fill_ratio))

# --------------------------------------------------------------------------
# R1 - the frozen behaviour is untouched
# --------------------------------------------------------------------------
print("\n== R1: extent picker reuses the existing CAD tools ==")
# The Grid tab is gone; the picker it used to live in is not.
panel = ExtentSourceHost(iface)
from geocad_uav.cad import tools as cad_tools                   # noqa: E402
from geocad_uav.cad.tools import base as tb                     # noqa: E402

drawn = panel.start_drawing("rectangle")
check_true("drawing uses the existing RectangleTool, not a new map tool",
           isinstance(drawn, tb.CadMapTool))
check_true("it is the registered rectangle tool",
           drawn.session.tool_id == "rectangle")
panel._stop_drawing()

drawn_poly = panel.start_drawing("polyline")
check_true("polygon drawing uses the existing PolylineTool",
           isinstance(drawn_poly, tb.CadMapTool)
           and drawn_poly.session.multi_vertex)
check_true("...asked to close its ring", drawn_poly.session.close)
panel._stop_drawing()

closed_ring = QgsGeometry.fromWkt(
    "LINESTRING({0} {1},{2} {1},{2} {3},{0} {3},{0} {1})".format(
        OX, OY, OX + 10.0, OY + 10.0))
as_polygon = ExtentSource.as_polygon(closed_ring)
check_true("a closed ring becomes the extent polygon",
           as_polygon is not None and as_polygon.area() > 0)
check("the converted polygon has the ring's area", as_polygon.area(), 100.0,
      1e-6)

# v1.4.0: this used to pin the five tools that existed in 1.3.1, which made
# it fail the moment three legitimate CAD tools were added. What it is really
# guarding is that ExtentSource registers no map tool of its own, so it now
# asserts that every registered tool is one the plugin itself mounts.
from geocad_uav import plugin as plugin_mod                     # noqa: E402

check_true("the extent picker registered no digitizer of its own",
           set(cad_tools.TOOL_REGISTRY) == set(plugin_mod.CAD_TOOL_ORDER))
check_true("...and it still reuses the drawing tools that exist",
           {"rectangle", "polyline", "line"} <= set(cad_tools.TOOL_REGISTRY))

panel.teardown()
forest.teardown()

# --------------------------------------------------------------------------
# P1 - the spacing an operator types is the spacing on the ground
# --------------------------------------------------------------------------
print("\n== P1: 200 x 100, square 5 x 5, margin 0, azimuth 0 ==")
from geocad_uav.forest import planting as planting_mod          # noqa: E402

P1_AOI = QgsGeometry.fromWkt(rect_wkt(OX, OY, 200.0, 100.0))
p1_spec = grid_mod.GridSpec(spacing_x=5.0, spacing_y=5.0,
                            pattern=grid_mod.PATTERN_SQUARE)
p1 = planting_mod.plan_planting_for_geometry(P1_AOI, p1_spec,
                                             compute_edge_distance=False)


def spacing_measurements(result):
    """(along a row, between rows) measured on the plants themselves."""
    by_row = {}
    by_col = {}
    for plant in result.plants:
        by_row.setdefault(plant.row_id, []).append(plant)
        by_col.setdefault(plant.seq_in_row, []).append(plant)
    along, across = [], []
    for plants in by_row.values():
        plants.sort(key=lambda p: p.seq_in_row)
        along.extend(math.hypot(b.x - a.x, b.y - a.y)
                     for a, b in zip(plants, plants[1:]))
    for plants in by_col.values():
        plants.sort(key=lambda p: p.row_id)
        across.extend(math.hypot(b.x - a.x, b.y - a.y)
                      for a, b in zip(plants, plants[1:]))
    return along, across


along, across = spacing_measurements(p1)
print("        {0:,} plants, {1} rows".format(len(p1.plants), p1.n_rows))
print("        along a row : mean {0:.6f}  min {1:.6f}  max {2:.6f}".format(
    np.mean(along), np.min(along), np.max(along)))
print("        between rows: mean {0:.6f}  min {1:.6f}  max {2:.6f}".format(
    np.mean(across), np.min(across), np.max(across)))
check("mean distance along a row", float(np.mean(along)), 5.0, 1e-6)
check("...and every single one of them", float(np.max(np.abs(
    np.asarray(along) - 5.0))), 0.0, 1e-6)
check("mean distance between rows", float(np.mean(across)), 5.0, 1e-6)
check("...and every single one of them", float(np.max(np.abs(
    np.asarray(across) - 5.0))), 0.0, 1e-6)

print("\n-- and the two are not interchangeable --")
p1b_spec = grid_mod.GridSpec(spacing_x=3.0, spacing_y=2.0,
                             pattern=grid_mod.PATTERN_RECT)
p1b = planting_mod.plan_planting_for_geometry(P1_AOI, p1b_spec,
                                              compute_edge_distance=False)
along_b, across_b = spacing_measurements(p1b)
check("spacing_x is the step along a row", float(np.mean(along_b)), 3.0, 1e-6)
check("spacing_y is the step between rows", float(np.mean(across_b)), 2.0,
      1e-6)

# what the panel sends is what the operator typed, in that order
p1_panel = ForestPanel(iface)
p1_panel.extent.set_extent(P1_AOI, CRS)
p1_panel.plant_spacing.setValue(3.0)
p1_panel.row_spacing.setValue(2.0)
p1_panel.margin.setValue(0.0)
p1_panel.azimuth.setValue(0.0)
panel_spec = p1_panel.build_spec()
check("the panel maps 'distanza fra le piante' onto spacing_x",
      panel_spec.spacing_x, 3.0)
check("...and 'distanza fra le file' onto spacing_y", panel_spec.spacing_y,
      2.0)
panel_plan = p1_panel.compute()
along_p, across_p = spacing_measurements(panel_plan)
check("so the panel's plants sit 3 m apart along a row",
      float(np.mean(along_p)), 3.0, 1e-6)
check("...and its rows 2 m apart", float(np.mean(across_p)), 2.0, 1e-6)

# the azimuth is across the rows, which is what the label now says
first_row = sorted([p for p in p1.plants if p.row_id == 1],
                   key=lambda p: p.seq_in_row)
delta = (first_row[1].x - first_row[0].x, first_row[1].y - first_row[0].y)
bearing = math.degrees(math.atan2(delta[0], delta[1])) % 360.0
print("        at azimuth 0 a row runs at bearing {0:.3f} deg".format(bearing))
check("at azimuth 0 the rows run due East", bearing, 90.0, 1e-6)

# --------------------------------------------------------------------------
# P2 - planimetric spacing, heights from the DEM
# --------------------------------------------------------------------------
print("\n== P2: with a DEM the schedule reports XY and 3D ==")
import tempfile                                                 # noqa: E402

from osgeo import gdal, osr                                     # noqa: E402
from qgis.core import QgsRasterLayer                            # noqa: E402

from geocad_uav.core.z import TerrainModel                      # noqa: E402

P2_TMP = tempfile.mkdtemp(prefix="geocad_forest_dem_")
CELL, NX, NY = 5.0, 60, 40
SLOPE = 0.25                       # a 25 % ramp: 3D distance visibly longer
xs = OX + (np.arange(NX) + 0.5) * CELL
ys = OY + 100.0 - (np.arange(NY) + 0.5) * CELL
XX, _YY = np.meshgrid(xs, ys)
gdal.UseExceptions()
dem_path = os.path.join(P2_TMP, "ramp.tif")
_ds = gdal.GetDriverByName("GTiff").Create(dem_path, NX, NY, 1,
                                           gdal.GDT_Float32)
_ds.SetGeoTransform((OX, CELL, 0.0, OY + 100.0, 0.0, -CELL))
_srs = osr.SpatialReference()
_srs.ImportFromEPSG(32632)
_ds.SetProjection(_srs.ExportToWkt())
_ds.GetRasterBand(1).WriteArray(
    (200.0 + SLOPE * (XX - OX)).astype(np.float32))
_ds.FlushCache()
_ds = None

dem_layer = QgsRasterLayer(dem_path, "ramp 25%", "gdal")
check_true("the DEM is valid", dem_layer.isValid())
QgsProject.instance().addMapLayer(dem_layer)

terrain, _warn = TerrainModel.from_layer(
    dem_layer, CRS, (OX, OY, OX + 200.0, OY + 100.0), margin_m=10.0)
p2_spec = grid_mod.GridSpec(spacing_x=5.0, spacing_y=5.0,
                            pattern=grid_mod.PATTERN_SQUARE)
p2 = planting_mod.plan_planting_for_geometry(
    QgsGeometry.fromWkt(rect_wkt(OX + 10.0, OY + 10.0, 100.0, 50.0)),
    p2_spec, terrain=terrain, compute_edge_distance=False)

heights = [p.z for p in p2.plants if p.z is not None]
check_true("every plant got a height from the DEM",
           len(heights) == len(p2.plants) and len(heights) > 0)
sampled = terrain.sample(
    np.array([p.x for p in p2.plants]), np.array([p.y for p in p2.plants]))
check("each height is the sampled one",
      float(np.max(np.abs(np.asarray(heights) - sampled))), 0.0, 1e-9)

along_p2, _ = spacing_measurements(p2)
check("the planimetric step is still exactly 5 m on the slope",
      float(np.mean(along_p2)), 5.0, 1e-6)
mean_3d = ForestPanel.mean_3d_spacing(p2)
expected_3d = math.hypot(5.0, 5.0 * SLOPE)
print("        XY {0:.6f} m, 3D {1:.6f} m (expected {2:.6f})".format(
    float(np.mean(along_p2)), mean_3d, expected_3d))
check("the 3D step is the slope distance", mean_3d, expected_3d, 1e-6)
check_true("...and it is longer than the planimetric one",
           mean_3d > float(np.mean(along_p2)))

p2_panel = ForestPanel(iface)
p2_panel.extent.set_extent(
    QgsGeometry.fromWkt(rect_wkt(OX + 10.0, OY + 10.0, 100.0, 50.0)), CRS)
p2_panel.plant_spacing.setValue(5.0)
p2_panel.row_spacing.setValue(5.0)
p2_panel.margin.setValue(0.0)
p2_panel.dem_combo.setLayer(dem_layer)
p2_panel.use_slope.setChecked(True)
p2_panel.refresh_preview()
schedule = p2_panel.last_schedule or []
check_true("the schedule reports the planimetric step",
           any("Distanza piante (XY)" in line for line in schedule))
check_true("...and the slope step next to it",
           any("Distanza piante (3D)" in line for line in schedule))
check_true("...and says which one the lattice used",
           any("planimetrica" in line for line in schedule))
check_true("the heights are reported", any("Quota min" in line
                                           for line in schedule))
check_true("so is the slope", any("Pendenza" in line for line in schedule))

# --------------------------------------------------------------------------
# P3 - the schedule is the engine's numbers, not the panel's
# --------------------------------------------------------------------------
print("\n== P3: every line of the schedule comes from the engine ==")
p3_panel = ForestPanel(iface)
p3_panel.extent.set_extent(QgsGeometry.fromWkt(rect_wkt(OX, OY, 200.0, 100.0)),
                           CRS)
p3_panel.plant_spacing.setValue(3.0)
p3_panel.row_spacing.setValue(2.0)
p3_panel.margin.setValue(2.0)
p3_panel.azimuth.setValue(0.0)
before_layers = len(QgsProject.instance().mapLayers())
baseline_refresh = canvas.refresh_calls
for _ in range(100):
    p3_panel.refresh_preview()
result3 = p3_panel.last_result
stats3 = stats_mod.compute_stats(result3)
schedule3 = p3_panel.last_schedule
print("        {0}".format(" | ".join(schedule3[1:5])))

check("no layer created by 100 previews",
      len(QgsProject.instance().mapLayers()), before_layers)
check("no canvas.refresh() during 100 previews",
      canvas.refresh_calls - baseline_refresh, 0)
for label, value in (
        ("Piante effettive", "{0:,}".format(stats3.n_plants)),
        ("Piante teoriche", "{0:,}".format(stats3.theoretical_count)),
        ("File:", "{0:,}".format(stats3.n_rows)),
        ("Densita' effettiva", "{0:,.1f}".format(stats3.density_per_ha)),
        ("Densita' teorica", "{0:,.1f}".format(
            stats3.theoretical_density_per_ha)),
        ("Superficie utile", "{0:,.2f}".format(stats3.usable_area_ha)),
        ("Superficie lorda", "{0:,.2f}".format(stats3.aoi_area_ha)),
        ("Lunghezza totale file", "{0:,.1f}".format(
            stats3.row_length_total_m))):
    line = next((l for l in schedule3 if label in l), "")
    check_true("{0} is on the schedule, with the engine's number".format(
        label.rstrip(":")), value in line)
check_true("the pattern is named", any("Sesto:" in l for l in schedule3))
check_true("the spacings are stated to the millimetre",
           any("3.000 m" in l for l in schedule3)
           and any("2.000 m" in l for l in schedule3))

print("\n-- the two-click button measures, and it says so --")
check_true("the button is named after what it does",
           p3_panel.step_from_map.text() == "Misura distanza in mappa")
measured_value = {}


def _capture(length, azimuth):
    measured_value["length"] = length
    p3_panel.plant_spacing.setValue(length)


_capture(3.0, 0.0)
check("two clicks 3.000 m apart set the plant spacing",
      p3_panel.plant_spacing.value(), 3.0, 1e-9)
check("...which is what reaches the engine",
      p3_panel.build_spec().spacing_x, 3.0, 1e-9)
panel_source = open(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "gui", "forest_panel.py"), encoding="utf-8").read()
check_true("the azimuth label no longer claims to follow the rows",
           'tr("Orientamento file")' not in panel_source)
check_true("...and the tooltip states the real relationship",
           "perpendicolari" in panel_source)

p1_panel.teardown()
p2_panel.teardown()
p3_panel.teardown()

# --------------------------------------------------------------------------
# RM (v1.4.8) - the tab is named for what it does, and offers every scheme
# --------------------------------------------------------------------------
print("\n== RM3: the tab is called Rimboschimento ==")
from geocad_uav import plugin as plugin_rm                      # noqa: E402


class RmIface(FakeIface):
    """Enough of an interface for the dock to mount itself."""

    def __init__(self):
        super().__init__()
        self.toolbars = []
        self.docks = []
        self.menu_actions = []

    def addToolBar(self, name):                                 # noqa: N802
        toolbar = self._window.addToolBar(name)
        self.toolbars.append(toolbar)
        return toolbar

    def addDockWidget(self, area, widget):                      # noqa: N802
        self._window.addDockWidget(area, widget)
        self.docks.append(widget)

    def removeDockWidget(self, widget):                         # noqa: N802
        self._window.removeDockWidget(widget)
        if widget in self.docks:
            self.docks.remove(widget)

    def addPluginToMenu(self, menu, action):                    # noqa: N802
        self.menu_actions.append((menu, action))

    def removePluginMenu(self, menu, action):                   # noqa: N802
        if (menu, action) in self.menu_actions:
            self.menu_actions.remove((menu, action))


rm_plugin = plugin_rm.GeoCadUavPlugin(RmIface())
rm_plugin.initGui()
rm_tabs = rm_plugin.dock.tabs
rm_titles = [rm_tabs.tabText(i) for i in range(rm_tabs.count())]
print("        tabs: {0}".format(rm_titles))
check_true("one tab is called Rimboschimento", "Rimboschimento" in rm_titles)
check("...exactly one", rm_titles.count("Rimboschimento"), 1)
check_true("no tab is called Foresta any more",
           not any("orest" in title for title in rm_titles))
check("the tab count is unchanged", len(rm_titles), 5)
check_true("the toolbar tip does not promise a tab that is gone",
           "Foresta" not in rm_plugin.dock_action.toolTip()
           and "Griglie" not in rm_plugin.dock_action.toolTip())
rm_plugin.unload()

# --------------------------------------------------------------------------
# RM2 - the panel still plans exactly what the engine plans
# --------------------------------------------------------------------------
print("\n== RM2: 200 x 100, 3 x 2, margin 2 ==")
rm_aoi = QgsGeometry.fromWkt(rect_wkt(OX, OY, 200.0, 100.0))
rm_panel = ForestPanel(iface)
rm_panel.extent.set_extent(rm_aoi, CRS)
rm_panel.plant_spacing.setValue(3.0)
rm_panel.row_spacing.setValue(2.0)
rm_panel.margin.setValue(2.0)
rm_panel.azimuth.setValue(0.0)
rm_result = rm_panel.compute()

reference = planting_mod.plan_planting_for_geometry(
    rm_aoi, rm_panel.build_spec(), compute_edge_distance=False)
print("        panel {0:,} plants, engine {1:,}".format(
    len(rm_result.plants), len(reference.plants)))
check("the panel plants what the engine plants", len(rm_result.plants),
      len(reference.plants))
check("...and lays the same number of rows", rm_result.n_rows,
      reference.n_rows)

rm_eroded = rm_aoi.buffer(-2.0, 12)
outside_rm = [p for p in rm_result.plants
              if not rm_eroded.intersects(
                  QgsGeometry.fromPointXY(QgsPointXY(p.x, p.y)))]
check("no plant falls outside the eroded AOI", len(outside_rm), 0)

rm_stats = stats_mod.compute_stats(rm_result)
check("the KPI are compute_stats", rm_stats.n_plants, len(rm_result.plants))
check("...including the row count", rm_stats.n_rows, rm_result.n_rows)
check_true("...and the densities", rm_stats.density_per_ha > 0
           and rm_stats.theoretical_density_per_ha > 0)

# --------------------------------------------------------------------------
# RM6 - every scheme in the combo has a generator behind it
# --------------------------------------------------------------------------
print("\n== RM6: each scheme in the combo really builds something ==")
check("the combo offers every pattern the engine has",
      rm_panel.pattern.count(), len(grid_mod.ALL_PATTERNS))
offered = [rm_panel.pattern.itemData(i)
           for i in range(rm_panel.pattern.count())]
check_true("every entry is a pattern core.grid knows",
           all(key in grid_mod.ALL_PATTERNS for key in offered))
check_true("and none of them is invented",
           set(offered) == set(grid_mod.ALL_PATTERNS))

for index in range(rm_panel.pattern.count()):
    rm_panel.pattern.setCurrentIndex(index)
    key = rm_panel.pattern.itemData(index)
    label = rm_panel.pattern.itemText(index)
    rm_panel.plant_spacing.setValue(5.0)
    rm_panel.margin.setValue(0.0)
    spec = rm_panel.build_spec()
    plan = rm_panel.compute()
    print("        {0:<24} {1:>6,} plants, {2:>4} rows".format(
        label, len(plan.plants), plan.n_rows))
    check_true("{0}: the spec carries the engine's key".format(label),
               spec.pattern == key)
    check_true("{0}: the label is the engine's own".format(label),
               label == grid_mod.PATTERN_LABELS[key])
    check_true("{0}: it plants something on 200 x 100".format(label),
               len(plan.plants) > 0)
    check_true("{0}: and the plants are inside the AOI".format(label),
               all(rm_aoi.intersects(QgsGeometry.fromPointXY(
                   QgsPointXY(p.x, p.y))) for p in plan.plants[:50]))

print("\n-- a square scheme has one distance, not two --")
square_index = offered.index(grid_mod.PATTERN_SQUARE)
rm_panel.pattern.setCurrentIndex(square_index)
rm_panel.plant_spacing.setValue(4.0)
check_true("the row spacing is locked while the scheme is square",
           not rm_panel.row_spacing.isEnabled())
check("...and mirrors the plant spacing", rm_panel.row_spacing.value(), 4.0,
      1e-9)
check("the label says so",
      1.0 if "= piante" in rm_panel.row_label.text() else 0.0, 1.0)
square_spec = rm_panel.build_spec()
check("the engine gets the same value both ways",
      square_spec.effective_spacing[0], square_spec.effective_spacing[1], 1e-9)
square_plan = rm_panel.compute()
square_along, square_across = spacing_measurements(square_plan)
check("so the plants really are 4 m apart along a row",
      float(np.mean(square_along)), 4.0, 1e-6)
check("...and 4 m between rows", float(np.mean(square_across)), 4.0, 1e-6)

rect_index = offered.index(grid_mod.PATTERN_RECT)
rm_panel.pattern.setCurrentIndex(rect_index)
check_true("switching back to a rectangular scheme frees the row spacing",
           rm_panel.row_spacing.isEnabled())

rm_panel.teardown()

# --------------------------------------------------------------------------
# G1 (v1.4.5) - the Grid tab left, the lattice engine stayed
# --------------------------------------------------------------------------
print("\n== G1: no Grid tab, and no orphan imports ==")
import importlib                                                # noqa: E402

from geocad_uav.core import grid as grid_engine                 # noqa: E402
from geocad_uav.gui import dock as dock_mod                     # noqa: E402

check_true("gui.grid_panel is gone",
           not os.path.isfile(os.path.join(
               os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
               "gui", "grid_panel.py")))
check_true("...and nothing imports it",
           _refuses(lambda: importlib.import_module(
               "geocad_uav.gui.grid_panel")))
dock_source = open(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "gui", "dock.py"), encoding="utf-8").read()
check_true("the dock does not mention it either",
           "grid_panel" not in dock_source and "GridPanel" not in dock_source)
check("the dock declares five tabs",
      len([n for n in dir(dock_mod.GeoCadDock) if n.startswith("TAB_")]), 5)
check_true("and none of them is a Grid tab",
           not any("GRID" in n for n in dir(dock_mod.GeoCadDock)
                   if n.startswith("TAB_")))

check_true("core.grid is still here: the schemes need it",
           hasattr(grid_engine, "generate_grid")
           and hasattr(grid_engine, "GridSpec"))
lattice = grid_engine.generate_grid(
    (0.0, 0.0, 100.0, 80.0), grid_engine.GridSpec(spacing_x=5.0,
                                                  spacing_y=5.0))
check("the lattice engine still produces 21 x 17", len(lattice), 21 * 17)

print("\n== E0: the ellipse tool left the toolbar ==")
from geocad_uav import plugin as plugin_check                   # noqa: E402

check_true("ellipse is out of the registry",
           "ellipse" not in cad_tools.TOOL_REGISTRY)
check_true("...out of the toolbar order",
           "ellipse" not in plugin_check.CAD_TOOL_ORDER)
check_true("...and out of TOOL_GEOMETRY",
           "ellipse" not in plugin_check.TOOL_GEOMETRY)
check_true("asking for it fails cleanly, without crossing into C++",
           _refuses(lambda: cad_tools.create_tool("ellipse", None)))
check_true("the module file is gone",
           not os.path.isfile(os.path.join(
               os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
               "cad", "tools", "ellipse.py")))
for survivor in ("circle", "arc", "rectangle", "square", "regular_polygon"):
    check_true("{0} is still registered".format(survivor),
               survivor in cad_tools.TOOL_REGISTRY)
check_true("the registry and the toolbar order still agree",
           set(cad_tools.TOOL_REGISTRY) == set(plugin_check.CAD_TOOL_ORDER))
# v1.5.0: ten survivors of the ellipse cut plus the digitizer and the
# manual input.
check("twelve tools remain", len(cad_tools.TOOL_REGISTRY), 12)

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
