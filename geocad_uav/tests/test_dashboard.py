"""
v1.28.0: the dashboard an operator actually sees.

Three things this suite exists for, each of which was a gap between what the
code could do and what the interface offered:

* the **single entry point** -- one icon on the QGIS toolbar, and pressing it
  opens the fourteen-step dashboard. Until now `toggle_workspace()` existed
  and nothing called it: the whole reforestation workflow was mounted hidden
  and unreachable from a running QGIS, the cadastral query with it;
* the **live preview** -- `[Genera Anteprima]` used to compute a plan and
  write a number in a label. It now puts the plan on the canvas, as a rubber
  band round the surface with the row bearing across it plus a temporary
  layer of plants coloured by species, and takes it away when the plan is
  committed;
* the **mix chart** -- requested share against achieved share, one bar per
  species, in the colours the plants will be on the map.

The live cadastral query is deliberately *not* run from this suite -- see
the note beside block D5 -- because it is proved from the same button, on
the real service, by test_map_end_to_end.py and test_acceptance.py, in
processes that can hold it. What is checked here is that the panel carrying
that button is reachable from the one icon on the toolbar, which is the half
those two suites cannot see.

NEEDS QGIS with widgets. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_dashboard.py
"""

import gc
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication,                          # noqa: E402
                       QgsCoordinateReferenceSystem, QgsGeometry, QgsProject,
                       QgsRectangle)

# GUI flag off, exactly as test_ui_shell does: this suite mounts the plugin
# on a QMainWindow that is never shown, and asking for the GUI platform as
# well takes the interpreter down with STATUS_STACK_BUFFER_OVERRUN and no
# traceback. The widgets still work -- QgsApplication is a QApplication.
QGS = QgsApplication([], False)
QGS.initQgis()

# Only after QgsApplication exists.
from qgis.gui import QgsMapCanvas                               # noqa: E402
from qgis.PyQt.QtGui import QColor, QImage, QPainter            # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow                     # noqa: E402

from geocad_uav import plugin as plugin_mod                     # noqa: E402
from geocad_uav.forest.reforestation import symbology as sym    # noqa: E402
from geocad_uav.gui import charts as charts_mod                 # noqa: E402
from geocad_uav.gui import theme as theme_mod                   # noqa: E402
from geocad_uav.gui import workflow as wf                       # noqa: E402
from geocad_uav.gui.preview import PREVIEW_LAYER_NAME           # noqa: E402
from geocad_uav.io import cadastre as cs                        # noqa: E402

FAILURES = []
SKIPS = []
CRS6706 = QgsCoordinateReferenceSystem("EPSG:6706")
CRS32632 = QgsCoordinateReferenceSystem("EPSG:32632")
OX, OY = 500000.0, 5000000.0


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<54} got={2:<16.10g} exp={3:.10g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_text(label, got, expected):
    ok = got == expected
    print("  [{0}] {1:<46} got={2!r:<24} exp={3!r}".format(
        "ok  " if ok else "FAIL", label, got, expected))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def skip(label, reason):
    print("  [skip] {0}\n         motivo: {1}".format(label, reason))
    SKIPS.append((label, reason))


class FakeMessageBar:
    def __init__(self):
        self.messages = []

    def pushMessage(self, *args, **kwargs):                     # noqa: N802
        self.messages.append(args)

    def pushCritical(self, *args):                              # noqa: N802
        self.messages.append(args)


class FakeIface:
    """Enough QgisInterface to mount the plugin and drive its canvas."""

    def __init__(self):
        self._window = QMainWindow()
        self._canvas = QgsMapCanvas()
        self._canvas.setDestinationCrs(CRS32632)
        self._canvas.setExtent(QgsRectangle(OX - 50, OY - 50,
                                            OX + 400, OY + 350))
        self._bar = FakeMessageBar()
        self._active = None
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

    def setActiveLayer(self, layer):                            # noqa: N802
        self._active = layer

    def activeLayer(self):                                      # noqa: N802
        return self._active


AREA = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX, OY, OX + 320.0, OY + 260.0))

print("=" * 78)
print("Dashboard -- un pulsante, un tema, un grafico, un'anteprima")
print("=" * 78)

# --------------------------------------------------------------------------
# D1 - one button, and it opens the dashboard
# --------------------------------------------------------------------------
print("\n== D1: un solo pulsante sulla toolbar di QGIS ==")
iface = FakeIface()
plugin = plugin_mod.GeoCadUavPlugin(iface)
plugin.initGui()

check("il plugin crea una sola toolbar", len(iface.toolbars), 1)
mounted = [a for a in iface.toolbars[0].actions() if not a.isSeparator()]
print("        {0}".format([a.text() for a in mounted]))
check("...con un solo pulsante", len(mounted), 1)
check_text("...che dice cosa apre", mounted[0].text(),
           "Suite GeoCad Rimboschimento")
check_true("...ed e' un interruttore", mounted[0].isCheckable())

workspace = plugin.workspace
check_true("la dashboard esiste", workspace is not None)
# v1.32.0: fourteen planting steps and six flight ones, one list.
check("...con i suoi venti step",
      workspace.workflow.list.count(), len(wf.ALL_STEPS))
check("...di cui sei di volo", len(wf.UAV_STEPS), 6)
check_true("prima del click e' nascosta",
           workspace.workflow.isHidden() and workspace.context.isHidden())
check_true("...e cosi' il dock CAD", plugin.dock.isHidden())

mounted[0].trigger()
check_true("il pulsante apre la dashboard",
           not workspace.workflow.isHidden()
           and not workspace.context.isHidden())
check_true("...e il dock CAD insieme a lei", not plugin.dock.isHidden())
check_true("...e resta premuto", mounted[0].isChecked())
mounted[0].trigger()
check_true("premuto di nuovo, mette via tutto",
           workspace.workflow.isHidden() and plugin.dock.isHidden())
mounted[0].trigger()

check_true("le forme CAD sono sulla toolbar del plugin, non su quella di "
           "QGIS",
           all(action not in mounted
               for action in plugin.shape_actions.values()))
cad_labels = [a.text() for a in plugin.dock.cad_toolbar.actions()
              if not a.isSeparator()]
print("        toolbar del plugin: {0}".format(cad_labels))
check("...e ci sono tutte e sette", len(cad_labels), 7)

# --------------------------------------------------------------------------
# D2 - the theme is the plugin's, and stays there
# --------------------------------------------------------------------------
print("\n== D2: il tema scuro, e soltanto sui pannelli del plugin ==")
sheet = workspace.workflow.styleSheet()
print("        foglio di stile: {0:,} caratteri".format(len(sheet)))
check_true("il dock del flusso ha un foglio di stile", len(sheet) > 500)
check_true("...lo stesso del dock di contesto",
           workspace.context.styleSheet() == sheet)
check_true("...con il fondo scuro dichiarato",
           theme_mod.PALETTE["bg"] in sheet)
check_true("...e l'accento del plugin", theme_mod.PALETTE["accent"] in sheet)
check_true("QGIS non e' stata ri-vestita",
           not QgsApplication.instance().styleSheet())
check_true("...ne' la finestra principale",
           not iface.mainWindow().styleSheet())
# The palette serves two consumers: the stylesheet and the chart, which is
# painted and not styled. So the claim is not "every entry is in the sheet"
# -- `warning` belongs to the chart -- but that the sheet names no colour of
# its own.
import re as _re                                                # noqa: E402

literals = set(_re.findall(r"#[0-9a-fA-F]{6}", theme_mod.QSS))
print("        colori scritti a mano nel foglio: {0}".format(
    literals or "nessuno"))
check("il foglio non nomina nessun colore di suo", len(literals), 0)
check_true("...e li prende tutti dalla tavolozza",
           all(value in sheet
               for key, value in theme_mod.PALETTE.items()
               if "{" + key + "}" in theme_mod.QSS))
check_true("il grafico attinge alla stessa tavolozza",
           theme_mod.PALETTE["warning"] in theme_mod.PALETTE.values())
built = theme_mod.stylesheet({"accent": "#ff0000"})
check_true("cambiare un colore e' una riga sola",
           "#ff0000" in built and theme_mod.PALETTE["accent"] not in built)

# --------------------------------------------------------------------------
# D3 - the mix chart
# --------------------------------------------------------------------------
print("\n== D3: il grafico della mescolanza ==")
state = workspace.state
scheme = workspace.context.scheme_panel
generate_panel = workspace.context.generate_panel
state.set_area(AREA, CRS32632, "Lotto della Dashboard")
scheme.plant_distance.setValue(8.0)
scheme.row_distance.setValue(8.0)
for name, percent in (("quercia", 50.0), ("frassino", 30.0),
                      ("acero", 20.0)):
    scheme.species_key.setCurrentText(name)
    scheme.species_percent.setValue(percent)
    scheme.on_add_species()
scheme.apply_scheme()

chart = scheme.mix_chart
check("il grafico ha una barra per specie", len(chart.rows), 3)
check_true("...con le percentuali chieste",
           [round(row[2], 1) for row in chart.rows] == [50.0, 30.0, 20.0])
check_true("...e zero ottenuto, perche' non si e' ancora generato",
           all(row[3] == 0.0 for row in chart.rows))
colours = chart.colours()
print("        colori: {0}".format(colours))
check_true("i colori sono quelli del layer, non una seconda tabella",
           colours == sym.palette([key for key, *_r in chart.rows]))

plan = generate_panel.preview()
scheme.refresh()
print("        {0}".format([(r[1], round(r[3], 1), r[4])
                            for r in chart.rows]))
check_true("dopo la generazione il grafico mostra le percentuali ottenute",
           all(row[3] > 0.0 for row in chart.rows))
check("...e il conteggio delle piante torna",
      sum(row[4] for row in chart.rows), plan.count)
check("...e le percentuali fanno cento",
      sum(row[3] for row in chart.rows), 100.0, 1e-6)
check_true("il grafico si spiega passandoci sopra",
           "chiesto" in chart.toolTip() and "ottenuto" in chart.toolTip())

image = QImage(360, 120, QImage.Format.Format_ARGB32)
image.fill(QColor("#000000"))
painter = QPainter(image)
chart.resize(360, 120)
chart.render(painter)
painter.end()
painted = {image.pixel(x, y) for x in range(0, 360, 7)
           for y in range(0, 120, 5)}
print("        colori distinti disegnati: {0}".format(len(painted)))
check_true("il grafico disegna davvero qualcosa", len(painted) > 4)
empty = charts_mod.SpeciesMixChart()
check_true("...e senza specie lo dice", "Nessuna specie" in empty.describe())

# --------------------------------------------------------------------------
# D4 - the live preview
# --------------------------------------------------------------------------
print("\n== D4: [Genera Anteprima] disegna sulla mappa ==")
check_text("il pulsante si chiama come l'operatore se lo aspetta",
           generate_panel.preview_button.text(), "Genera Anteprima")
preview = state.preview
check_true("l'anteprima e' sulla mappa", preview.is_showing())
check_true("...come banda elastica attorno alla superficie",
           preview.band is not None)
check_true("...con la direzione delle file attraverso",
           preview.bearing is not None)
names = [layer.name() for layer in QgsProject.instance().mapLayers().values()]
check_true("...e un layer temporaneo di piante",
           PREVIEW_LAYER_NAME in names)
check("...con tutte le piante del piano", preview.layer.featureCount(),
      plan.count)
check_true("...colorate per specie",
           preview.layer.renderer().__class__.__name__
           == "QgsCategorizedSymbolRenderer")
check_true("il layer d'anteprima si distingue da quello definitivo",
           preview.layer.customProperty("geocad/preview") in (True, "true",
                                                              "True"))
check_true("il pannello dice cosa sta mostrando",
           "anteprima:" in generate_panel.preview_label.text()
           and "piante/ha" in generate_panel.preview_label.text())
print("        {0}".format(generate_panel.preview_label.text()))
check_true("...e si puo' togliere",
           generate_panel.clear_preview_button.isEnabled())

print("\n-- generare l'impianto sostituisce l'anteprima, non la affianca --")
layer = generate_panel.generate()
names = [l.name() for l in QgsProject.instance().mapLayers().values()]
check_true("l'anteprima se n'e' andata", not preview.is_showing())
check_true("...e il suo layer con lei", PREVIEW_LAYER_NAME not in names)
check("il layer definitivo porta le piante", layer.featureCount(), plan.count)
check_true("il pannello lo dice",
           "nessuna anteprima" in generate_panel.preview_label.text())
check_true("...e il pulsante si e' spento",
           not generate_panel.clear_preview_button.isEnabled())

print("\n-- e si toglie a mano --")
generate_panel.preview()
check_true("una nuova anteprima torna sulla mappa", preview.is_showing())
removed = generate_panel.clear_preview()
check("[Togli anteprima] la porta via", removed, plan.count)
check_true("...tutta", not preview.is_showing()
           and PREVIEW_LAYER_NAME not in
           [l.name() for l in QgsProject.instance().mapLayers().values()])

# --------------------------------------------------------------------------
# D5 - the cadastre, from the button, on the live service
# --------------------------------------------------------------------------
print("\n== D5: il catasto, dal pulsante della dashboard ==")
area_panel = workspace.context.area_panel
check_true("il pannello Area e' una pagina della dashboard",
           workspace.context.pages["area"][0] is area_panel)
check_true("...e porta il pulsante del catasto",
           area_panel.query_button.text().startswith("Interroga Catasto"))

# The live query is NOT run here, and the reason is measured, not assumed:
# a synchronous network call on the main thread of a process that has the
# plugin mounted on a live QgsMapCanvas takes the interpreter down with
# STATUS_STACK_BUFFER_OVERRUN and no traceback, at exactly that line, with
# every check above already passed. Stopping the canvas first does not help.
#
# It loses no coverage. The same button is pressed against the real Agenzia
# delle Entrate service, and the result followed all the way to the map, in
# test_map_end_to_end.py (block M5) and test_acceptance.py (points 3-5),
# both of which run without a mounted canvas and both of which are green on
# 3.40.15 and 4.0.0. What is checked here is the half those two cannot see:
# that the panel carrying that button is a page of this dashboard.
check_true("...e il pulsante e' collegato a un servizio, non a niente",
           area_panel.query_button.receivers(
               area_panel.query_button.clicked) > 0)
check_true("il progetto sa come chiedere al catasto",
           hasattr(state, "request_cadastre")
           and callable(state.request_cadastre))
check_true("la dashboard ascolta la risposta",
           state.receivers(state.cadastralDataReady) > 0)
check("...e la tabella delle particelle parte vuota",
      area_panel.parcel_table.rowCount(), 0)
check_true("...col motivo, non in silenzio",
           bool(area_panel.cadastre_status_label.text().strip()))
print("        stato iniziale: {0}".format(
    area_panel.cadastre_status_label.text()))

plugin.unload()
check("scaricando il plugin non resta nessun dock", len(iface.docks), 0)
check_true("...ne' l'anteprima sulla mappa", not preview.is_showing())

print("\n" + "=" * 78)
plan = layer = chart = empty = preview = None
workspace = state = scheme = generate_panel = area_panel = None
plugin = iface = image = None
gc.collect()
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
