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
import re
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
from geocad_uav.gui import workflow as wf                       # noqa: E402

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
# v1.14.0: the three primitives moved onto the QGIS toolbar, one action
# each, with a single toggle choosing between drawing them and typing them.
# Four buttons for six registry entries -- and one place to reach a shape.
check("actions on the QGIS toolbar", len(mounted), 5)
check_true("the first is still the dock toggle",
           mounted[0] is plugin.dock_action)
check_true("it is checkable", mounted[0].isCheckable())
toolbar_labels = [a.text() for a in mounted]
for expected in ("Quadrato", "Rettangolo", "Poligono", "Parametrico"):
    check_true("'{0}' is on the QGIS toolbar".format(expected),
               expected in toolbar_labels)
check_true("no other primitive reached the toolbar",
           not any(text in toolbar_labels
                   for text in ("Linea", "Cerchio", "Arco", "Polilinea",
                                "Poligono regolare")))
check_true("the three shapes are mutually exclusive",
           plugin.shape_group is not None and plugin.shape_group.isExclusive())
check_true("the mode switch is checkable and starts on free drawing",
           plugin.parametric_action.isCheckable()
           and not plugin.parametric_action.isChecked())
check_true("it carries the plugin name", "GeoCad" in mounted[0].text())
check_true("the CAD actions are NOT on the QGIS toolbar",
           all(a not in mounted for a in plugin.tool_actions.values()))
check("the three algorithm launchers went to the menu",
      len(iface.menu_actions), 4)          # toggle + 3 algorithms

# --------------------------------------------------------------------------
# U2 - the toggle drives the dock, both ways
# --------------------------------------------------------------------------
print("\n== U2: toggle shows and hides the dock ==")
# v1.14.0: the workflow dock and the context dock joined the old one.
check("three docks registered", len(iface.docks), 3)
check_true("the workflow dock is on the left and lists every step",
           plugin.workspace is not None
           and plugin.workspace.workflow.list.count() == len(wf.STEPS))
check_true("the context dock holds a stack of panels",
           plugin.workspace.context.stack.count() >= 10)
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
# v1.4.5: the Griglie tab was withdrawn. core.grid stays -- the reforestation
# schemes are built on it, they simply no longer have a tab of their own.
check("tab count", tabs.count(), 5)
check_true("titles and order match the contract",
           titles == ["CAD", "Rimboschimento", "UAV", "Layer/Export",
                      "Impostazioni"])
check_true("the forestry tab is named for the work, not the subject",
           "Foresta" not in titles and "Rimboschimento" in titles)
check_true("no Grid tab is left, not even an empty one",
           not any("rigli" in t for t in titles))
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
# v1.4.0: 5 tools became 8 (Square, Ellisse, Poligono regolare joined).
# v1.4.1: 8 became 10 (Sposta and Ridimensiona, both edit-in-place). The count
# is asserted against the registry below, so it follows the shipped set; this
# literal is the floor that says the toolbar was actually populated.
# v1.4.3: ten became eleven when the Arco tool joined.
# v1.4.5: eleven back to ten -- the Ellisse tool was withdrawn.
# v1.5.0: ten became twelve with the digitizer and the manual input.
# v1.7.0: twelve became nine -- three primitives, each drawn and each typed,
# plus the three modifiers.
# v1.14.0: the six primitive entries moved to the QGIS toolbar as three
# buttons and a mode switch; the dock keeps the modifiers, which are not
# primitives and have no second input mode.
check("the dock toolbar carries the modifiers", len(cad_mounted), 3)
labels = {a.text() for a in cad_mounted}
for expected in ("Ruota", "Sposta", "Ridimensiona"):
    check_true("'{0}' is on the dock toolbar".format(expected),
               expected in labels)
check_true("no primitive is offered twice",
           not (labels & {"Quadrato", "Rettangolo", "Poligono"}))
for withdrawn in ("Linea", "Polilinea", "Cerchio", "Arco", "Ellisse",
                  "Poligono regolare", "Inserimento manuale"):
    check_true("'{0}' is not offered any more".format(withdrawn),
               withdrawn not in labels)
# v1.14.0: one button per shape on the QGIS toolbar, and the mode switch
# decides which of its two registry entries that button arms.
check_true("each admitted shape has exactly one button",
           all(drawn in plugin.shape_actions
               for drawn, _typed in cad_tools.ADMITTED_SHAPES.values()))
check("...three buttons for three shapes", len(plugin.shape_actions), 3)
check_true("every registered CAD tool is reachable, once",
           len(cad_mounted) + 2 * len(plugin.shape_actions)
           == len(cad_tools.TOOL_REGISTRY))
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
square_action = plugin.shape_actions["square"]
rect_action = plugin.shape_actions["rectangle"]

square_action.trigger()
square_tool = plugin.map_tools.get("square")
check_true("triggering Quadrato created a map tool", square_tool is not None)
check_true("...and it is a CAD map tool",
           isinstance(square_tool, tb.CadMapTool))
check_true("the canvas is using it", canvas.mapTool() is square_tool)

rect_action.trigger()
rect_tool = plugin.map_tools.get("rectangle")
check_true("triggering Rettangolo swaps the map tool",
           canvas.mapTool() is rect_tool and rect_tool is not square_tool)
check_true("the square action was un-checked by the group",
           not square_action.isChecked())

# The same square button, with the mode switch on, reaches the parametric
# entry instead. One button per shape, two modes behind it.
# trigger() on a checkable action flips it and emits; setChecked() before
# it would flip it straight back.
square_action.trigger()
plugin.parametric_action.trigger()
params_tool = plugin.map_tools.get("square_params")
check_true("the mode switch reaches the parametric entry",
           params_tool is not None and params_tool is not square_tool)
check_true("...pointed at the square by the registry, not by the plugin",
           params_tool.session.spec.tool == "square")
check_true("the canvas is using it", canvas.mapTool() is params_tool)
plugin.parametric_action.trigger()
check_true("switching back returns to free drawing",
           not plugin.parametric_action.isChecked())
# Leave a drawing tool current: the parametric one schedules its measures
# dialog on the next turn of the event loop, and this suite has no stub for
# it. Switching away is also what tells that dialog not to open.
rect_action.trigger()
check_true("switching away leaves the drawing tool current",
           canvas.mapTool() is plugin.map_tools.get("rectangle"))

# --------------------------------------------------------------------------
# U6 - unload is symmetric and re-init does not duplicate
# --------------------------------------------------------------------------
print("\n== U6: unload is symmetric, re-init does not duplicate ==")
plugin.unload()
check("no dock left registered", len(iface.docks), 0)
check_true("the workspace went with it", plugin.workspace is None)
check("...and its shape actions too", len(plugin.shape_actions), 0)
check("no menu action left", len(iface.menu_actions), 0)
check("plugin forgot its actions", len(plugin.actions), 0)
check("plugin forgot its map tools", len(plugin.map_tools), 0)
check_true("the toolbar handle was released", plugin.toolbar is None)
check_true("the dock handle was released", plugin.dock is None)
check_true("the canvas no longer uses a plugin map tool",
           canvas.mapTool() is not rect_tool)

plugin.initGui()
check("a second initGui creates the same three docks", len(iface.docks), 3)
check("...and the same five toolbar actions",
      len(real_actions(iface.toolbars[-1])), 5)
check("...and does not double the menu entries", len(iface.menu_actions), 4)
check("...and repopulates the dock's CAD toolbar",
      len(real_actions(plugin.dock.cad_toolbar)),
      len(plugin_mod.EDIT_IN_PLACE_TOOLS))
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

# --------------------------------------------------------------------------
# I1 (v1.3.5): the shipped icon and the stated identity
# --------------------------------------------------------------------------
print("\n== I1: the archive carries the icon, the metadata carries the name ==")
import tempfile                                                 # noqa: E402
import zipfile                                                  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PACKAGE_DIR = os.path.join(ROOT, "geocad_uav")

metadata = open(os.path.join(PACKAGE_DIR, "metadata.txt"),
                encoding="utf-8").read()
check_true("metadata declares the raster icon",
           "icon=icon.png" in metadata)
check_true("the icon file is in the package", os.path.isfile(
    os.path.join(PACKAGE_DIR, "icon.png")))

source_icon = os.path.join(ROOT, "icon_plugin_cad.png")
if os.path.isfile(source_icon):
    with open(source_icon, "rb") as handle:
        original = handle.read()
    with open(os.path.join(PACKAGE_DIR, "icon.png"), "rb") as handle:
        packaged = handle.read()
    check_true("the packaged icon is the artwork byte for byte",
               original == packaged)
else:
    skip("the packaged icon is the artwork byte for byte",
         "icon_plugin_cad.png is not in the repository root")

description = re.search(r"^description=(.+)$", metadata, re.M)
check_true("description is present and not empty",
           description is not None and len(description.group(1).strip()) > 40)
check_true("description fits the plugin manager (< 250 chars)",
           description is not None and len(description.group(1)) < 250)

about = re.search(r"^about=(?:.*\n)(?:[ \t]+.*\n)*", metadata, re.M)
about_text = about.group(0) if about else ""
check_true("about names the author",
           "Cap. Niccol\u00f2 Marco Mancini" in about_text)
check_true("about names the unit", "RGPBIO" in about_text)
check_true("about names the Cartografia Numerica group",
           "Cartografia Numerica" in about_text)
check_true("author= carries the rank",
           "author=Cap. Niccol\u00f2 Marco Mancini" in metadata)
check_true("email is present and untouched",
           re.search(r"^email=\S+@\S+$", metadata, re.M) is not None)
check_true("no marketing comparison in the metadata",
           not any(word in metadata.lower()
                   for word in ("litchi mission hub e' meglio", "thopos",
                                "piu' potente", "powered by")))

sys.path.insert(0, ROOT)
import zip_plugin                                               # noqa: E402

check_true("the builder requires the icon in the archive",
           "icon.png" in zip_plugin.REQUIRED)

build_dir = tempfile.mkdtemp(prefix="geocad_zip_")
archive_path = zip_plugin.build(build_dir, with_tests=False)
with zipfile.ZipFile(archive_path) as archive:
    names = archive.namelist()
    icon_bytes = archive.read("geocad_uav/icon.png")
check_true("the archive contains geocad_uav/icon.png",
           "geocad_uav/icon.png" in names)
check_true("the archived icon is the artwork, uncompressed and unresized",
           icon_bytes == open(os.path.join(PACKAGE_DIR, "icon.png"),
                              "rb").read())
check_true("the archive root is still the package alone",
           {name.split("/")[0] for name in names} == {"geocad_uav"})
zip_plugin.verify(archive_path)

# The dock was unloaded above, so build a fresh one to read its credit line.
fresh = plugin_mod.GeoCadUavPlugin(FakeIface())
fresh.initGui()
credit = fresh.dock.credit.text()
print("        credit: {0}".format(credit))
check_true("the dock shows the credit line",
           "RGPBIO" in credit
           and "Cap. Niccol\u00f2 Marco Mancini" in credit
           and "Cartografia Numerica" in credit)
fresh.unload()

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
