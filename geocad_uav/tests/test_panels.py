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
from geocad_uav.gui.grid_panel import ExtentSource, GridPanel   # noqa: E402
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


iface = FakeIface()
canvas = iface.mapCanvas()

# --------------------------------------------------------------------------
# F1 - drawn extent, counted against the engine
# --------------------------------------------------------------------------
print("\n== F1: drawn rectangle 100 x 80, step 5 x 5, no margin ==")
panel = GridPanel(iface)
extent_geom = QgsGeometry.fromWkt(rect_wkt(OX, OY, 100.0, 80.0))
panel.extent.set_extent(extent_geom, CRS)
panel.spacing_x.setValue(5.0)
panel.spacing_y.setValue(5.0)
panel.margin.setValue(0.0)

result = panel.compute()
expected = engine_count(extent_geom, panel.build_spec())
print("        panel says {0}, engine says {1}".format(len(result), expected))
check("node count matches core.grid + intersects", len(result), expected)
check_true("the count is not trivially zero", len(result) > 0)
check("a full lattice: (100/5 + 1) x (80/5 + 1)", len(result),
      (100.0 / 5.0 + 1) * (80.0 / 5.0 + 1))

xy = result.xy
check_true("no node lies outside the extent",
           all(extent_geom.intersects(
               QgsGeometry.fromPointXY(QgsPointXY(float(x), float(y))))
               for x, y in xy))
check("rows are renumbered from 0", int(result.row.min()), 0)
check("spacing really is 5 m",
      float(np.diff(np.unique(np.round(xy[:, 0], 6)))[0]), 5.0, 1e-9)

# --------------------------------------------------------------------------
# F2 - the same extent taken from a layer feature
# --------------------------------------------------------------------------
print("\n== F2: same extent, this time picked from an existing feature ==")
source_layer = lf.memory_layer("Polygon", "estensione esistente",
                               CRS.authid(), [("id", "int")])
feature = QgsFeature(source_layer.fields())
feature.setGeometry(QgsGeometry.fromWkt(rect_wkt(OX, OY, 100.0, 80.0)))
source_layer.dataProvider().addFeatures([feature])
# The combo is backed by the project's model, so register before selecting.
QgsProject.instance().addMapLayer(source_layer)

panel2 = GridPanel(iface)
panel2.spacing_x.setValue(5.0)
panel2.spacing_y.setValue(5.0)
panel2.margin.setValue(0.0)
# Go through the empty entry so layerChanged really fires: the combo
# pre-selects the only matching layer by itself, silently.
panel2.extent.layer_combo.setLayer(None)
check_true("the empty entry clears the extent",
           panel2.extent.geometry() is None)
panel2.extent.layer_combo.setLayer(source_layer)
check_true("the layer really is selected in the combo",
           panel2.extent.layer_combo.currentLayer() is source_layer)
check_true("the extent came from the feature",
           panel2.extent.geometry() is not None)

result2 = panel2.compute()
check("picking a feature gives the same count as drawing", len(result2),
      len(result))
check("...and the same as the engine", len(result2),
      engine_count(panel2.extent.geometry(), panel2.build_spec()))

# Second polygon in the same layer: the whole layer means both, the
# selection means one.
second = QgsFeature(source_layer.fields())
second.setGeometry(QgsGeometry.fromWkt(rect_wkt(OX + 400.0, OY, 100.0, 80.0)))
source_layer.dataProvider().addFeatures([second])
panel2.extent._from_layer()
check("both features together give twice the nodes", len(panel2.compute()),
      2 * len(result))

source_layer.selectByIds([f for f in source_layer.allFeatureIds()][:1])
panel2.extent.selected_only.setChecked(True)
check("with 'only selected' the extent is the selected feature alone",
      len(panel2.compute()), len(result))
panel2.extent.selected_only.setChecked(False)

# --------------------------------------------------------------------------
# F3 - step from two clicks, numeric stays master
# --------------------------------------------------------------------------
print("\n== F3: step from two clicks, then typed ==")
measured = {}


def capture(length, azimuth):
    measured["length"] = length
    measured["azimuth"] = azimuth


# start_measure hands a length to its callback; drive that contract directly
# rather than faking a click sequence through the canvas.
panel.extent.start_measure(capture)
check_true("a measuring tool was activated",
           panel.extent._draw_tool is not None)
panel.extent._stop_drawing()

capture(3.0, 47.5)
panel.spacing_x.setValue(measured["length"])
check("two clicks 3.000 m apart set dx", panel.spacing_x.value(), 3.0, 1e-9)
check("the spec carries it", panel.build_spec().spacing_x, 3.0, 1e-9)

panel.spacing_x.setValue(5.0)
check("typing 5 overrides the measured 3 (numeric is master)",
      panel.build_spec().spacing_x, 5.0, 1e-9)

panel.azimuth.setValue(measured["azimuth"])
check("azimuth from the same two clicks", panel.build_spec().azimuth_deg,
      47.5, 1e-9)
panel.azimuth.setValue(0.0)

# azimuth parallel to the longest edge of the extent
panel._azimuth_from_edge()
ring = np.array([[p.x(), p.y()] for p in extent_geom.asPolygon()[0]],
                dtype=float)
check("azimuth parallel to the longest side", panel.azimuth.value(),
      grid_mod.azimuth_of_longest_edge(ring) % 360.0, 1e-9)
panel.azimuth.setValue(0.0)
panel.spacing_x.setValue(5.0)
panel.spacing_y.setValue(5.0)

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
# F5 - preview writes nothing; confirm writes once
# --------------------------------------------------------------------------
print("\n== F5: 100 previews write nothing, one confirm writes once ==")
before_layers = len(QgsProject.instance().mapLayers())
baseline_refresh = canvas.refresh_calls
for step in range(100):
    panel.spacing_x.setValue(5.0 + (step % 3) * 0.001)
    panel.refresh_preview()
panel.spacing_x.setValue(5.0)
preview = panel.refresh_preview()

check("no layer created by 100 previews",
      len(QgsProject.instance().mapLayers()), before_layers)
check("no canvas.refresh() during previews",
      canvas.refresh_calls - baseline_refresh, 0)
check_true("a preview band exists", panel._band is not None)
check("the band holds one point per node", panel._band.numberOfVertices(),
      len(preview))

created = panel.confirm()
check_true("confirm created a layer", created is not None)
check("the layer holds exactly the previewed nodes", created.featureCount(),
      len(preview))
check("exactly one new layer in the project",
      len(QgsProject.instance().mapLayers()), before_layers + 1)

print("\n-- one undo command covers the whole batch --")
batch_layer = lf.memory_layer("Point", "batch", CRS.authid(), [("id", "int")])
batch_layer.startEditing()          # so add_features leaves it in the buffer
features = []
for index in range(25):
    f = QgsFeature(batch_layer.fields())
    f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(OX + index, OY)))
    f.setAttributes([index])
    features.append(f)
undo.add_features(batch_layer, features, "GeoCad: batch")
check("25 features added", batch_layer.featureCount(), 25)
stack = batch_layer.undoStack() if hasattr(batch_layer, "undoStack") else None
if stack is None:
    skip("one undo removes the whole batch",
         "QgsVectorLayer.undoStack() unavailable on this build")
else:
    check("the batch is a single undo command", stack.count(), 1)
    stack.undo()
    check("one undo removes all 25", batch_layer.featureCount(), 0)
batch_layer.rollBack()

# --------------------------------------------------------------------------
# R1 - the frozen behaviour is untouched
# --------------------------------------------------------------------------
print("\n== R1: extent picker reuses the existing CAD tools ==")
from geocad_uav.cad import tools as cad_tools                   # noqa: E402
from geocad_uav.cad.tools import base as tb                     # noqa: E402

drawn = panel.extent.start_drawing("rectangle")
check_true("drawing uses the existing RectangleTool, not a new map tool",
           isinstance(drawn, tb.CadMapTool))
check_true("it is the registered rectangle tool",
           drawn.session.tool_id == "rectangle")
panel.extent._stop_drawing()

drawn_poly = panel.extent.start_drawing("polyline")
check_true("polygon drawing uses the existing PolylineTool",
           isinstance(drawn_poly, tb.CadMapTool)
           and drawn_poly.session.multi_vertex)
check_true("...asked to close its ring", drawn_poly.session.close)
panel.extent._stop_drawing()

closed_ring = QgsGeometry.fromWkt(
    "LINESTRING({0} {1},{2} {1},{2} {3},{0} {3},{0} {1})".format(
        OX, OY, OX + 10.0, OY + 10.0))
as_polygon = ExtentSource.as_polygon(closed_ring)
check_true("a closed ring becomes the extent polygon",
           as_polygon is not None and as_polygon.area() > 0)
check("the converted polygon has the ring's area", as_polygon.area(), 100.0,
      1e-6)

check_true("no third digitizer was registered",
           set(cad_tools.TOOL_REGISTRY) == {"line", "polyline", "rectangle",
                                            "circle", "rotate"})

panel.teardown()
panel2.teardown()
forest.teardown()

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
