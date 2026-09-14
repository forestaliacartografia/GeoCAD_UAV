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
                       QgsProject, QgsRectangle, QgsWkbTypes)

QGS = QgsApplication([], False)
QGS.initQgis()

from qgis.gui import QgsMapCanvas                               # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow                     # noqa: E402

from geocad_uav.core import grid as grid_mod                    # noqa: E402
from geocad_uav.core import undo                                # noqa: E402
from geocad_uav.forest import stats as stats_mod                # noqa: E402
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

# v1.7.0: the free-form AOI is digitised with the polygon tool, which
# commits an actual polygon instead of a closed LineString to be converted.
drawn_poly = panel.start_drawing("digitize")
check_true("free-form drawing uses the existing polygon digitizer",
           isinstance(drawn_poly, tb.CadMapTool)
           and drawn_poly.session.multi_vertex)
check_true("...and it writes polygons",
           drawn_poly.session.geometry_type == "Polygon")
check_true("...so there is no ring left to close by hand",
           not hasattr(drawn_poly.session, "close"))
check_true("its scratch layer is polygonal too",
           panel._draw_layer.geometryType()
           == QgsWkbTypes.GeometryType.PolygonGeometry)
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
           {"rectangle", "square", "digitize"} <= set(cad_tools.TOOL_REGISTRY))

panel.teardown()

# --------------------------------------------------------------------------
# RM3 (v1.36.0) - there is no reforestation tab: it is a module
# --------------------------------------------------------------------------
print("\n== RM3: nessuna scheda Rimboschimento nel dock CAD ==")
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
check_true("no tab is called Rimboschimento or Foresta",
           not any("Rimbosch" in title or "orest" in title
                   for title in rm_titles))
check("two tabs are left: CAD and Impostazioni", len(rm_titles), 2)
check_true("...and they are those two",
           rm_titles == ["CAD", "Impostazioni"])

# The module is reachable, as a module: its own path in the left dock.
import geocad_uav.gui.workflow as wf_rm                         # noqa: E402

workspace_rm = rm_plugin.workspace
check_true("il rimboschimento e' un modulo del dock sinistro",
           workspace_rm is not None
           and workspace_rm.workflow.set_module(wf_rm.MODULE_FOREST))
check("...coi suoi quattordici step",
      workspace_rm.workflow.list.count(), len(wf_rm.STEPS))
check_true("e il volo e' l'altro, separato",
           workspace_rm.workflow.set_module(wf_rm.MODULE_UAV))
check("...coi suoi sei", workspace_rm.workflow.list.count(),
      len(wf_rm.UAV_STEPS))
check_true("the toolbar tip does not promise a tab that is gone",
           "Foresta" not in rm_plugin.dock_action.toolTip()
           and "Griglie" not in rm_plugin.dock_action.toolTip())
workspace_rm = None
rm_plugin.unload()

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
check("the dock declares two tabs",
      len([n for n in dir(dock_mod.GeoCadDock) if n.startswith("TAB_")]), 2)
check_true("and no UAV, export or forest tab among them",
           not any("UAV" in n or "EXPORT" in n or "FOREST" in n
                   for n in dir(dock_mod.GeoCadDock)
                   if n.startswith("TAB_")))
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
for survivor in ("rectangle", "square", "digitize"):
    check_true("{0} is still registered".format(survivor),
               survivor in cad_tools.TOOL_REGISTRY)
check_true("the registry and the toolbar order still agree",
           set(cad_tools.TOOL_REGISTRY) == set(plugin_check.CAD_TOOL_ORDER))
# v1.7.0: three primitives x two input modes, plus the three modifiers.
check("nine tools remain", len(cad_tools.TOOL_REGISTRY), 9)

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
