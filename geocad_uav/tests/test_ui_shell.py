"""
v1.2.0: the UI shell -- one toolbar icon, one tabbed dock, symmetric unload.

Drives the real ``GeoCadUavPlugin`` against a stand-in ``iface``. The stand-in
implements only what the plugin actually calls, and every widget it hands back
is a real Qt object, so what is under test is the plugin's wiring rather than a
mock of it.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_ui_shell.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsProject, QgsRectangle)

QGS = QgsApplication([], False)
QGS.initQgis()

# Only after QgsApplication exists.
from qgis.gui import QgsMapCanvas                               # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow                     # noqa: E402

from geocad_uav import plugin as plugin_mod                     # noqa: E402
from geocad_uav.cad import tools as cad_tools                   # noqa: E402
from geocad_uav.cad.tools import base as tb                     # noqa: E402

FAILURES = []
SKIPS = []


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<54} got={2:<12.10g} exp={3:.10g}".format(
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


# --------------------------------------------------------------------------
# A stand-in for QgisInterface: only what the plugin calls, all real widgets.
# --------------------------------------------------------------------------

class FakeMessageBar:
    def __init__(self):
        self.messages = []

    def pushMessage(self, *args, **kwargs):                     # noqa: N802
        self.messages.append(args)

    def pushCritical(self, title, text):                        # noqa: N802
        self.messages.append((title, text))


class FakeIface:
    """Minimal QgisInterface. Records what the plugin mounts where."""

    def __init__(self):
        self._window = QMainWindow()
        self._canvas = QgsMapCanvas()
        self._canvas.setDestinationCrs(
            QgsCoordinateReferenceSystem("EPSG:32632"))
        self._canvas.setExtent(QgsRectangle(500000, 5000000, 500400, 5000300))
        self._bar = FakeMessageBar()
        self.toolbars = []
        self.docks = []
        self.menu_actions = []

    def mainWindow(self):                                       # noqa: N802
        return self._window

    def mapCanvas(self):                                        # noqa: N802
        return self._canvas

    def messageBar(self):                                       # noqa: N802
        return self._bar

    def addToolBar(self, name):                                 # noqa: N802
        toolbar = self._window.addToolBar(name)
        self.toolbars.append(toolbar)
        return toolbar

    def addDockWidget(self, area, dock):                        # noqa: N802
        self._window.addDockWidget(area, dock)
        self.docks.append(dock)

    def removeDockWidget(self, dock):                           # noqa: N802
        self._window.removeDockWidget(dock)
        if dock in self.docks:
            self.docks.remove(dock)

    def addPluginToMenu(self, menu, action):                    # noqa: N802
        self.menu_actions.append((menu, action))

    def removePluginMenu(self, menu, action):                   # noqa: N802
        if (menu, action) in self.menu_actions:
            self.menu_actions.remove((menu, action))


def real_actions(toolbar):
    """Actions on a toolbar, separators excluded."""
    return [a for a in toolbar.actions() if not a.isSeparator()]


# --------------------------------------------------------------------------
# U1 - exactly one action on the QGIS toolbar
# --------------------------------------------------------------------------
print("\n== U1: the QGIS toolbar carries one icon ==")
iface = FakeIface()
plugin = plugin_mod.GeoCadUavPlugin(iface)
plugin.initGui()

check("plugin created exactly one QGIS toolbar", len(iface.toolbars), 1)
toolbar = iface.toolbars[0]
mounted = real_actions(toolbar)
print("        toolbar actions: {0}".format([a.text() for a in mounted]))
check("actions on the QGIS toolbar", len(mounted), 1)
check_true("the single action is the dock toggle",
           mounted[0] is plugin.dock_action)
check_true("it is checkable", mounted[0].isCheckable())
check_true("it carries the plugin name", "GeoCad" in mounted[0].text())
check_true("the CAD actions are NOT on the QGIS toolbar",
           all(a not in mounted for a in plugin.tool_actions.values()))
check("the three algorithm launchers went to the menu",
      len(iface.menu_actions), 4)          # toggle + 3 algorithms

# --------------------------------------------------------------------------
# U2 - the toggle drives the dock, both ways
# --------------------------------------------------------------------------
print("\n== U2: toggle shows and hides the dock ==")
check("exactly one dock registered", len(iface.docks), 1)
dock = plugin.dock
# isHidden(), not isVisible(): this QMainWindow is never shown, so every widget
# inside it reports isVisible() == False whatever setVisible() did, and the
# assertion could not fail. isHidden() reports the explicit hide that the plugin
# actually performs, which is the behaviour under test.
check_true("the dock starts hidden", dock.isHidden())
check_true("...and the toggle starts unchecked",
           not plugin.dock_action.isChecked())

plugin.dock_action.trigger()
check_true("first trigger un-hides the dock", not dock.isHidden())
check_true("the action is checked", plugin.dock_action.isChecked())

plugin.dock_action.trigger()
check_true("second trigger hides the dock", dock.isHidden())
check_true("the action is unchecked", not plugin.dock_action.isChecked())

plugin.dock_action.trigger()
check_true("shown again", not dock.isHidden())

# Closing the dock from its own title bar must un-check the toolbar icon.
# Qt emits visibilityChanged only when visibility genuinely changes, which
# cannot happen inside a window that was never shown, so the signal itself is
# not exercised here. Asserted instead: the plugin connected it, and the
# receiving slot does the right thing. Those two halves are the plugin's
# responsibility; emitting the signal is Qt's.
# == and not `is`: attribute access on a bound method builds a fresh object
# every time, so `plugin._on_dock_visibility is plugin._on_dock_visibility` is
# False. Bound methods compare equal when they wrap the same function and the
# same instance, which is exactly the question here.
connected = any(slot == plugin._on_dock_visibility
                for _signal, slot in plugin._connections)
check_true("the plugin connected dock.visibilityChanged", connected)
plugin._on_dock_visibility(False)
check_true("...and that slot un-checks the toolbar icon",
           not plugin.dock_action.isChecked())
plugin._on_dock_visibility(True)
check_true("...and re-checks it when the dock reappears",
           plugin.dock_action.isChecked())
plugin.dock_action.setChecked(False)

# --------------------------------------------------------------------------
# U3 - six tabs, in a fixed order
# --------------------------------------------------------------------------
print("\n== U3: the dock has six tabs in a fixed order ==")
tabs = dock.tabs
titles = [tabs.tabText(i) for i in range(tabs.count())]
print("        tabs: {0}".format(titles))
check("tab count", tabs.count(), 6)
check_true("titles and order match the contract",
           titles == ["CAD", "Griglie", "Foresta", "UAV", "Layer/Export",
                      "Impostazioni"])
check_true("no MISSIONI tab was added",
           not any("ission" in t for t in titles))

# --------------------------------------------------------------------------
# U4 - the dock's CAD toolbar is populated and exclusive
# --------------------------------------------------------------------------
print("\n== U4: the CAD toolbar inside the dock ==")
cad_toolbar = dock.cad_toolbar
cad_mounted = real_actions(cad_toolbar)
print("        CAD toolbar actions: {0}".format(
    [a.text() for a in cad_mounted]))
check_true("the dock's CAD toolbar is NOT empty", len(cad_mounted) > 0)
check_true("at least Line, Rectangle and Circle are present",
           len(cad_mounted) >= 3)
labels = {a.text() for a in cad_mounted}
for expected in ("Linea", "Rettangolo", "Cerchio"):
    check_true("'{0}' is on the dock toolbar".format(expected),
               expected in labels)
check_true("every registered CAD tool is mounted",
           len(cad_mounted) == len(cad_tools.TOOL_REGISTRY))
check_true("all CAD actions are checkable",
           all(a.isCheckable() for a in cad_mounted))
check_true("they share one exclusive QActionGroup",
           plugin.tool_group is not None and plugin.tool_group.isExclusive())
check("the group holds every CAD action", len(plugin.tool_group.actions()),
      len(cad_mounted))

cad_mounted[0].setChecked(True)
cad_mounted[1].setChecked(True)
check_true("checking one un-checks the other (mutual exclusion)",
           not cad_mounted[0].isChecked() and cad_mounted[1].isChecked())
cad_mounted[1].setChecked(False)

# --------------------------------------------------------------------------
# U5 - triggering a CAD action sets the map tool
# --------------------------------------------------------------------------
print("\n== U5: CAD actions drive the canvas map tool ==")
canvas = iface.mapCanvas()
line_action = plugin.tool_actions["line"]
rect_action = plugin.tool_actions["rectangle"]

line_action.trigger()
line_tool = plugin.map_tools.get("line")
check_true("triggering Linea created a map tool", line_tool is not None)
check_true("...and it is a CAD map tool",
           isinstance(line_tool, tb.CadMapTool))
check_true("the canvas is using it", canvas.mapTool() is line_tool)

rect_action.trigger()
rect_tool = plugin.map_tools.get("rectangle")
check_true("triggering Rettangolo swaps the map tool",
           canvas.mapTool() is rect_tool and rect_tool is not line_tool)
check_true("the line action was un-checked by the group",
           not line_action.isChecked())

# --------------------------------------------------------------------------
# U6 - unload is symmetric and re-init does not duplicate
# --------------------------------------------------------------------------
print("\n== U6: unload is symmetric, re-init does not duplicate ==")
plugin.unload()
check("no dock left registered", len(iface.docks), 0)
check("no menu action left", len(iface.menu_actions), 0)
check("plugin forgot its actions", len(plugin.actions), 0)
check("plugin forgot its map tools", len(plugin.map_tools), 0)
check_true("the toolbar handle was released", plugin.toolbar is None)
check_true("the dock handle was released", plugin.dock is None)
check_true("the canvas no longer uses a plugin map tool",
           canvas.mapTool() is not rect_tool)

plugin.initGui()
check("a second initGui creates exactly one dock", len(iface.docks), 1)
check("...and exactly one toolbar action",
      len(real_actions(iface.toolbars[-1])), 1)
check("...and does not double the menu entries", len(iface.menu_actions), 4)
check("...and repopulates the dock's CAD toolbar",
      len(real_actions(plugin.dock.cad_toolbar)), len(cad_tools.TOOL_REGISTRY))
check_true("the toolbars were not stacked up",
           len(iface.toolbars) == 2)         # one per initGui, each cleaned

# --------------------------------------------------------------------------
# U7 - Processing provider survives the shell change
# --------------------------------------------------------------------------
print("\n== U7: the Processing provider still registers ==")
registry = QgsApplication.processingRegistry()
provider = registry.providerById("geocaduav")
if provider is None:
    skip("Processing algorithms are registered",
         "providerById('geocaduav') returned None in this headless run; "
         "not asserting a pass that was not demonstrated")
else:
    alg_ids = sorted(a.id() for a in provider.algorithms())
    print("        algorithms: {0}".format(alg_ids))
    check("three algorithms registered", len(alg_ids), 3)
    for expected in ("geocaduav:planflight", "geocaduav:creategrid",
                     "geocaduav:forestplanting"):
        check_true("{0} still available".format(expected),
                   expected in alg_ids)

plugin.unload()
check("final unload leaves no dock", len(iface.docks), 0)

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
