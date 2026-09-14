"""
v1.30.0: the parcel is read where the drawing happens.

Since 1.29.0 a CAD commit starts a cadastral lookup by itself and the answer
lands in the attribute table. That is only half a feature: the operator is
looking at the CAD panel, not at the table, and nothing on that panel said
where the shape had just been drawn.

What is checked here is the readout, and only through what a user can see:
the labels of the "Dati catastali" box on the CAD tab. The shape is placed
with the same session the map tool drives, the lookup is the one the commit
starts on its own -- no panel button, no second code path -- and the box is
read afterwards.

The transport is injected, so the suite runs with the network unplugged and
never pretends a call succeeded: the recorded answer is the real service's
own GML, kept in fixtures/, and the live round trip stays in
test_cad_cadastre.py where it belongs.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_cad_panel.py
"""

import gc
import os
import sys
from time import sleep, time

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication,                          # noqa: E402
                       QgsCoordinateReferenceSystem, QgsProject,
                       QgsRectangle)

QGS = QgsApplication([], False)
QGS.initQgis()

from qgis.gui import QgsMapCanvas                               # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow                     # noqa: E402

from geocad_uav.cad import parametric as pa                     # noqa: E402
from geocad_uav.cad.tools import base as tb                     # noqa: E402
from geocad_uav.cad.tools import square as square_tool          # noqa: E402
from geocad_uav.gui.dock import GeoCadDock                      # noqa: E402
from geocad_uav.io import cadastre as cs                        # noqa: E402
from geocad_uav.io import layer_factory as lf                   # noqa: E402
from geocad_uav.settings import settings as app_settings        # noqa: E402

FAILURES = []
SKIPS = []
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures")
CRS6706 = QgsCoordinateReferenceSystem("EPSG:6706")
CRS32632 = QgsCoordinateReferenceSystem("EPSG:32632")


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<54} got={2:<16.10g} exp={3:.10g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_text(label, got, expected):
    ok = got == expected
    print("  [{0}] {1:<44} got={2!r:<26} exp={3!r}".format(
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


def pump(predicate, seconds=30.0):
    deadline = time() + float(seconds)
    while time() < deadline:
        if predicate():
            return True
        QGS.processEvents()
        sleep(0.01)
    return bool(predicate())


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8",
              errors="replace") as handle:
        return handle.read()


PARCEL_XML = fixture("catasto_parcel_getfeature.xml")
ZONING_XML = fixture("catasto_zoning_getfeature.xml")
INSIDE = cs.parse_parcel_geometries(PARCEL_XML)[0][1].centroid()
CX = float(INSIDE.constGet().x())
CY = float(INSIDE.constGet().y())


class FakeMessageBar:
    def __init__(self):
        self.messages = []

    def pushMessage(self, *args, **kwargs):                     # noqa: N802
        self.messages.append(args)


class FakeIface:
    def __init__(self):
        self._window = QMainWindow()
        self._canvas = QgsMapCanvas()
        self._canvas.setDestinationCrs(CRS32632)
        self._canvas.setExtent(QgsRectangle(500000, 5000000, 500400, 5000300))
        self._bar = FakeMessageBar()

    def mainWindow(self):                                       # noqa: N802
        return self._window

    def mapCanvas(self):                                        # noqa: N802
        return self._canvas

    def messageBar(self):                                       # noqa: N802
        return self._bar


def cad_layer(name, crs=CRS6706):
    """A CAD scratch layer, built exactly as the plugin builds one."""
    layer = lf.memory_layer("Polygon", name, crs.authid(),
                            list(pa.METADATA_FIELDS)
                            + list(lf.CAD_LAYER_FIELDS))
    QgsProject.instance().addMapLayer(layer)
    return layer


def new_tool(transport=None):
    """A CAD tool the way the dock binds one, with its transport chosen."""
    tool = tb.BaseCadTool(square_tool.SquareSession())
    tool.cadastre_transport = transport
    return tool


def draw_square(tool, layer, x, y, side=6.0, crs=CRS6706):
    """Place a square the way the map tool does, metric working CRS and all."""
    session = tool.session
    span = 1e-3 if crs.isGeographic() else 100.0
    decision = tool.work_crs(crs, QgsRectangle(x - span, y - span,
                                               x + span, y + span))
    work = decision.work_crs
    ox, oy = tool.to_work(x, y, crs, work)
    session.set_origin(ox, oy)
    session.submit(str(side))
    session.submit("0d")
    return tool.commit(layer, work, layer.crs())


def label(dock, key):
    return dock.cadastre_labels[key].text()


CALLS = []


def recorded(url, timeout=0.0):
    CALLS.append(url)
    return ZONING_XML if "CadastralZoning" in url else PARCEL_XML


def dead(url, timeout=0.0):
    raise cs.CadastreError(
        "network unplugged",
        user_message="Servizio catastale non raggiungibile.")


print("=" * 78)
print("CAD -> catasto -> pannello CAD")
print("=" * 78)

iface = FakeIface()
dock = GeoCadDock(iface)
WAS_ENABLED = bool(app_settings.get("cadastre/enabled"))

# --------------------------------------------------------------------------
# P1 - the box exists, on the CAD tab, with the switch that drives it
# --------------------------------------------------------------------------
print("\n== P1: la scheda CAD porta il riquadro catastale ==")
check_true("il riquadro 'Dati catastali' esiste",
           dock.cadastre_box is not None)
print("        titolo: {0!r}".format(dock.cadastre_box.title()))
check_true("...e si chiama cosi'", "catastali" in dock.cadastre_box.title())
for key in ("comune", "foglio", "particella", "area", "perimetro"):
    check_true("ha la riga {0}".format(key), key in dock.cadastre_labels)
check_true("le righe partono a trattini",
           all(label(dock, key) == "--"
               for key in dock.cadastre_labels))

cad_page = dock.tabs.widget(GeoCadDock.TAB_CAD)
check_true("il riquadro e' dentro la pagina CAD, non altrove",
           dock.cadastre_box in cad_page.findChildren(type(dock.cadastre_box)))

check_true("l'interruttore del catasto e' sul pannello",
           dock.set_cadastre_enabled is not None)
check_true("...e mostra il valore salvato",
           dock.set_cadastre_enabled.isChecked() == WAS_ENABLED)

# The switch is a control, not an ornament: what it does is written through.
dock.set_cadastre_enabled.setChecked(False)
check_true("spegnerlo scrive nelle impostazioni",
           not bool(app_settings.get("cadastre/enabled")))
dock.set_cadastre_enabled.setChecked(True)
check_true("riaccenderlo pure", bool(app_settings.get("cadastre/enabled")))

# --------------------------------------------------------------------------
# P2 - the commit reaches the panel at once, before any answer
# --------------------------------------------------------------------------
print("\n== P2: area e perimetro compaiono appena posata la forma ==")
tool = new_tool(recorded)
dock.bind_cad_tool("square", tool)
check_true("legare lo strumento installa l'osservatore",
           tool.commit_observer == dock.show_commit)

metric = cad_layer("panel_metric", CRS32632)
draw_square(tool, metric, 500000.0, 5000000.0, side=40.0, crs=CRS32632)
print("        Superficie: {0}".format(label(dock, "area")))
print("        Perimetro:  {0}".format(label(dock, "perimetro")))
check_true("la superficie e' quella disegnata, in m2 e in ha",
           "1,600.00 m2" in label(dock, "area")
           and "0.1600 ha" in label(dock, "area"))
check_true("il perimetro pure", "160.00 m" in label(dock, "perimetro"))
check_true("il commit ha pubblicato un rapporto",
           tool.last_commit is not None)
check("...sulla feature scritta", tool.last_commit.area_m2, 1600.0, 1e-6)
check_true("...con il nome del layer",
           tool.last_commit.layer_name == metric.name())

# --------------------------------------------------------------------------
# P3 - the recorded answer fills the box, with nothing else pressed
# --------------------------------------------------------------------------
print("\n== P3: la risposta del servizio arriva da sola nel riquadro ==")
tool = new_tool(recorded)
dock.bind_cad_tool("square", tool)
geo = cad_layer("panel_recorded")
draw_square(tool, geo, CX, CY, side=6.0)
print("        subito dopo il commit: {0!r}".format(label(dock, "particella")))
check_true("finche' il task e' fuori il riquadro lo dice",
           "corso" in label(dock, "particella").lower())
check_true("il task e' partito dal commit, non da un pulsante",
           tool.cadastre_task is not None)

check_true("il riquadro si riempie da solo",
           pump(lambda: "corso" not in label(dock, "particella").lower()))
print("        Comune {0!r} | Foglio {1!r} | Particella {2!r}".format(
    label(dock, "comune"), label(dock, "foglio"), label(dock, "particella")))
check_true("il comune e' il nome, non il codice Belfiore",
           "Perugia" in label(dock, "comune"))
check_text("il foglio e' quello del servizio", label(dock, "foglio"), "252")
check_text("la particella pure", label(dock, "particella"), "1016")
check_true("la riga di stato riassume la particella",
           "252" in dock.cadastre_status.text()
           and "1016" in dock.cadastre_status.text())
check_true("la superficie e' rimasta quella della forma",
           "m2" in label(dock, "area") and label(dock, "area") != "--")
check_true("il servizio e' stato interrogato davvero", len(CALLS) >= 1)

# Read by the id the commit resolved, not by position: "the first feature
# the iterator happens to hand back" is an assumption, and this check is
# about the panel agreeing with the table, not about ordering.
stored = geo.getFeature(tool.last_feature_id)
check_text("e il pannello dice quello che dice la tabella",
           str(stored[lf.CAT_PARTICELLA_FIELD]), label(dock, "particella"))

# --------------------------------------------------------------------------
# P4 - the service cannot answer: "N/D" on the panel, and no exception
# --------------------------------------------------------------------------
print("\n== P4: servizio irraggiungibile -> N/D, senza eccezioni ==")
tool = new_tool(dead)
dock.bind_cad_tool("square", tool)
offline = cad_layer("panel_offline")
draw_square(tool, offline, CX, CY, side=6.0)
check_true("il riquadro arriva a una risposta",
           pump(lambda: label(dock, "particella") == lf.NOT_AVAILABLE))
for key in ("comune", "foglio", "particella"):
    check_text("{0} dice N/D".format(key), label(dock, key), lf.NOT_AVAILABLE)
check_true("la superficie resta leggibile", "m2" in label(dock, "area"))
print("        stato: {0!r}".format(dock.cadastre_status.text()))
check_true("lo stato spiega il motivo, senza URL ne' chiavi",
           bool(dock.cadastre_status.text())
           and "http" not in dock.cadastre_status.text())
check("la geometria e' comunque sul layer", offline.featureCount(), 1)

# --------------------------------------------------------------------------
# P5 - the switch really stops the lookup, and the shape is written anyway
# --------------------------------------------------------------------------
print("\n== P5: a interrogazione spenta si disegna lo stesso ==")
dock.set_cadastre_enabled.setChecked(False)
tool = new_tool(dead)
dock.bind_cad_tool("square", tool)
quiet = cad_layer("panel_quiet")
draw_square(tool, quiet, CX, CY, side=6.0)
check_true("nessun task catastale e' partito", tool.cadastre_task is None)
check("la feature c'e' lo stesso", quiet.featureCount(), 1)
check_true("...con la sua superficie sul pannello",
           "m2" in label(dock, "area") and label(dock, "area") != "--")
print("        stato: {0!r}".format(dock.cadastre_status.text()))
check_true("e il pannello dice che l'interrogazione e' spenta",
           "disattivat" in dock.cadastre_status.text().lower())
dock.set_cadastre_enabled.setChecked(True)

# --------------------------------------------------------------------------
# P6 - unbinding takes the observer away again
# --------------------------------------------------------------------------
print("\n== P6: sganciare lo strumento stacca l'osservatore ==")
last = tool
dock.unbind_cad_tool()
check_true("l'osservatore e' stato rimosso", last.commit_observer is None)
orphan = cad_layer("panel_orphan")
draw_square(last, orphan, CX, CY, side=6.0)
check("la geometria si scrive comunque", orphan.featureCount(), 1)
check_true("...e lo strumento tiene il proprio rapporto",
           last.last_commit is not None and last.last_commit.area_m2 > 0.0)

# --------------------------------------------------------------------------
print("\n" + "=" * 78)
app_settings.set("cadastre/enabled", WAS_ENABLED)
dock.teardown()
dock.setWidget(None)
dock.deleteLater()
tool = last = None
dock = None
iface = None
QgsProject.instance().removeAllMapLayers()
gc.collect()
QGS.processEvents()
QGS.exitQgis()
if SKIPS:
    print("SKIPPED ({0}):".format(len(SKIPS)))
    for name, reason in SKIPS:
        print("   - {0}: {1}".format(name, reason))
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for name in FAILURES:
        print("   - {0}".format(name))
    sys.exit(1)
print("ALL CHECKS PASSED")
