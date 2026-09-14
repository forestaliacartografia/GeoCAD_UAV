"""
v2.1.0: il catasto del CAD e' quello del rimboschimento, e vede tutti i Comuni.

The CAD used to ask the cadastre a different question from the one the
reforestation module asks. It took ``geometry.centroid()`` and asked which
parcel that single point falls on -- so a shape across four parcels reported
one, a shape across two comuni reported one comune, and an L-shaped parcel
whose centroid falls in the notch reported a neighbour. The answer was a
single parcel by construction, not by measurement.

It now runs ``cadastre.area_task``: the same QgsTask, the same WFS call, the
same GML parsing, the same metric-CRS intersection, the same Belfiore
register. This suite proves the chain end to end, on the tool and on the
panel:

    geometria CAD -> interrogazione -> WFS/GML -> geometrie particelle
    -> intersezione -> superfici -> percentuali -> Comune -> Foglio
    -> Particella -> tabella attributi -> GUI -> mappa

The two-comune document is the recorded service answer with its second
parcel moved to another comune: the geometries stay the service's own. One
block asks the **live** service and skips with its reason when the endpoint
cannot be reached.

NEEDS QGIS with widgets. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_cad_cadastre_multicomune.py
"""

import gc
import os
import sys
from time import sleep, time

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication,                          # noqa: E402
                       QgsCoordinateReferenceSystem, QgsFeature,
                       QgsGeometry, QgsProject)

QGS = QgsApplication([], True)
QGS.initQgis()

from qgis.PyQt.QtCore import Qt                                 # noqa: E402

from geocad_uav.cad.tools import base as tb                     # noqa: E402
from geocad_uav.cad.tools import square as square_tool          # noqa: E402
from geocad_uav.gui import dock as dock_mod                     # noqa: E402
from geocad_uav.io import cadastre as cs                        # noqa: E402
from geocad_uav.io import layer_factory as lf                   # noqa: E402
from geocad_uav.settings import settings as app_settings        # noqa: E402

FAILURES = []
SKIPS = []
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures")
CRS6706 = QgsCoordinateReferenceSystem("EPSG:6706")
PERUGIA, ROMA = "G478", "H501"


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


def pump(predicate, seconds=60.0):
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

# The recorded answer, second parcel moved to another comune. Nothing else
# changes: same rings, same coordinate order, same attribute names.
_head, _, _tail = PARCEL_XML.partition("</CP:CadastralParcel>")
TWO_COMUNI_XML = (_head + "</CP:CadastralParcel>"
                  + _tail.replace("G478_025200.1189", "H501_012300.77")
                         .replace(">G478<", ">H501<"))
PAIRS = cs.parse_parcel_geometries(TWO_COMUNI_XML)
CALLS = []


def two_comuni(url, timeout=0.0):
    """The service, recorded, answering about two comuni."""
    CALLS.append(url)
    return ZONING_XML if "CadastralZoning" in url else TWO_COMUNI_XML


def cad_layer(name, crs=CRS6706):
    layer = lf.memory_layer("Polygon", name, crs.authid(),
                            list(lf.CAD_LAYER_FIELDS))
    QgsProject.instance().addMapLayer(layer)
    return layer


def put(layer, geometry):
    """One feature on a CAD layer, the way a commit leaves it.

    The id comes back through ``added_feature_id`` for the same reason the
    map tool uses it: ``addFeature`` reports a provisional negative id
    inside the edit buffer, and writing to that id succeeds and lands
    nowhere.
    """
    before = lf.ids_near(layer, geometry)
    layer.startEditing()
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry(geometry))
    layer.addFeature(feature)
    if not layer.commitChanges():
        raise AssertionError(
            "il layer non ha accettato la geometria: {0}".format(
                layer.commitErrors()))
    layer.updateExtents()
    return lf.added_feature_id(layer, geometry, before)


print("=" * 78)
print("CAD -> catasto multicomune -> tabella, pannello, mappa")
print("=" * 78)
check("il documento di prova porta due particelle", len(PAIRS), 2)
check_true("...di due Comuni diversi",
           {p.comune_code for p, _g in PAIRS} == {PERUGIA, ROMA})

# The CAD shape: one rectangle over both parcels, so it really lies across
# both comuni. One, not a union of two: a CAD layer is Polygon and refuses a
# MultiPolygon outright -- which is also what a CAD tool draws.
_box = QgsGeometry.unaryUnion(
    [QgsGeometry.fromRect(geometry.boundingBox())
     for _p, geometry in PAIRS]).boundingBox()
SHAPE = QgsGeometry.fromRect(_box)
check_true("la figura CAD di prova e' valida",
           SHAPE is not None and not SHAPE.isEmpty())
check_true("...ed e' un poligono singolo, come un layer CAD li vuole",
           not SHAPE.isMultipart())
check_true("...e copre entrambe le particelle",
           all(SHAPE.intersects(geometry) for _p, geometry in PAIRS))

# --------------------------------------------------------------------------
# M1 - the tool asks about the geometry, not about a point
# --------------------------------------------------------------------------
print("\n== M1: lo strumento CAD interroga la forma, non il centroide ==")
check_true("l'interrogazione automatica e' accesa",
           bool(app_settings.get("cadastre/enabled")))

layer = cad_layer("cad_multicomune")
feature_id = put(layer, SHAPE)
tool = tb.BaseCadTool(square_tool.SquareSession())
tool.cadastre_transport = two_comuni
task = tool.request_cadastre(layer, SHAPE, feature_id)
check_true("la richiesta ha avviato un task", task is not None)
check_true("il task ha risposto",
           pump(lambda: tool.cadastre_result is not None))

result = tool.cadastre_result
print("        {0} particelle in {1} Comuni".format(
    result.n_parcels, len(result.comuni())))
check("due particelle", result.n_parcels, 2)
check("due Comuni", len(result.comuni()), 2)
check_true("...e sono i due del documento",
           set(result.belfiore_codes()) == {PERUGIA, ROMA})
check_true("il calcolo e' avvenuto in un CRS metrico",
           result.work_crs_authid.startswith("EPSG:")
           and result.work_crs_authid != "EPSG:6706")
check_true("ogni particella porta le due superfici",
           all(share.parcel_area_m2 > 0.0
               and share.intersection_area_m2 > 0.0
               for share in result.shares))
check_true("...e una percentuale che e' il loro rapporto",
           all(abs(share.percent_of_parcel
                   - 100.0 * share.intersection_area_m2
                   / share.parcel_area_m2) < 1e-9
               for share in result.shares))
check_true("le superfici delle due particelle sono diverse",
           len({round(s.intersection_area_m2, 3)
                for s in result.shares}) == 2)
check_true("la richiesta e' passata dal WFS", bool(CALLS))
check_true("...chiedendo un riquadro, non un punto",
           any("BBOX" in url.upper() for url in CALLS))

# --------------------------------------------------------------------------
# M2 - the five columns say so, and stay five
# --------------------------------------------------------------------------
print("\n== M2: le cinque colonne dicono due Comuni ==")
written = layer.getFeature(feature_id)
names = [field.name() for field in layer.fields()]
print("        colonne: {0}".format(names))
check_text("il layer ha esattamente cinque colonne", str(len(names)), "5")
check_true("...e sono quelle di sempre",
           names == ["Area", "Perimetro", "Comune", "Foglio", "Particella"])
print("        Comune {0!r}".format(written["Comune"]))
print("        Foglio {0!r}".format(written["Foglio"]))
print("        Particella {0!r}".format(written["Particella"]))
check_true("il Comune nomina entrambi",
           "Perugia" in str(written["Comune"])
           and "Roma" in str(written["Comune"]))
check_true("il Foglio e' qualificato col Belfiore, perche' i Comuni sono due",
           PERUGIA in str(written["Foglio"]) and ROMA in str(written["Foglio"]))
check_true("la Particella porta entrambe",
           "1016" in str(written["Particella"])
           and "1189" in str(written["Particella"]))
check_true("...ciascuna qualificata dal suo foglio, che e' diverso",
           "252/1016" in str(written["Particella"])
           and "123/1189" in str(written["Particella"]))
check_true("la geometria e' rimasta al suo posto",
           not layer.getFeature(feature_id).geometry().isEmpty())

print("\n-- e una particella sola scrive quello che scriveva prima --")
single = cs.interpolate_cadastral_data(
    QgsGeometry.fromRect(PAIRS[0][1].boundingBox()), CRS6706, [PAIRS[0]])
columns = cs.cad_columns(single)
print("        {0}".format(columns))
check("una particella, una riga", single.n_parcels, 1)
check_text("il Comune e' il nome, senza codice",
           columns[lf.CAT_COMUNE_FIELD], "Perugia (PG)")
check_text("il Foglio e' nudo", columns[lf.CAT_FOGLIO_FIELD], "252")
check_text("la Particella pure", columns[lf.CAT_PARTICELLA_FIELD], "1016")
check_text("un risultato vuoto non scrive niente", str(cs.cad_columns(None)),
           "{}")

# --------------------------------------------------------------------------
# M3 - the report the panel is given
# --------------------------------------------------------------------------
print("\n== M3: il report che arriva al pannello ==")
report = tb.commit_report(layer, feature_id, result=result)
check_true("il report porta la geometria, letta dal layer",
           report.geometry is not None and not report.geometry.isEmpty())
check_text("...e il CRS del layer", report.crs_authid, "EPSG:6706")
check("...e il risultato intero, non tre stringhe", report.n_parcels, 2)
check("...con i suoi due Comuni", report.n_comuni, 2)
check_true("...e l'area letta dalla tabella", report.area_m2 >= 0.0)

# --------------------------------------------------------------------------
# M4 - the panel: query on demand, both tables, the map
# --------------------------------------------------------------------------
print("\n== M4: il pannello CAD interroga, elenca e disegna ==")
panel = dock_mod.GeoCadDock(None)
check_true("il pannello nasce col comando spento",
           not panel.cadastre_button.isEnabled())
check("...e con le tabelle vuote", panel.parcel_table.rowCount(), 0)

panel.show_commit(report)
check_true("posata una geometria, il comando si accende",
           panel.cadastre_button.isEnabled())
check("il dettaglio ha una riga per particella",
      panel.parcel_table.rowCount(), 2)
check("...e sei colonne", panel.parcel_table.columnCount(), 6)
check("il riepilogo ha una riga per Comune", panel.comune_table.rowCount(), 2)
check("...e cinque colonne", panel.comune_table.columnCount(), 5)
shown = {panel.parcel_table.item(r, 0).text()
         for r in range(panel.parcel_table.rowCount())}
print("        Comuni nel dettaglio: {0}".format(sorted(shown)))
check("entrambi i Comuni compaiono", len(shown), 2)
check_true("le superfici sono in tabella",
           "m2" in panel.parcel_table.item(0, 3).text()
           and "m2" in panel.parcel_table.item(0, 4).text())
print("        stato: {0!r}".format(panel.cadastre_status.text()))
check_true("lo stato dice quanti Comuni", "2 Comuni" in
           panel.cadastre_status.text())

print("\n-- il comando esplicito, con la sua attesa --")
panel.fill_cadastre_tables(None)
check("le tabelle sono state svuotate", panel.parcel_table.rowCount(), 0)
panel._last_commit.result = None
started = panel.query_cadastre(two_comuni)
check_true("Interroga catasto ha avviato un task", started is not None)
check_true("...e il comando si spegne mentre il servizio pensa",
           not panel.cadastre_button.isEnabled())
check_true("...dicendolo", "corso" in panel.cadastre_status.text())
check_true("il task ha risposto",
           pump(lambda: panel.parcel_table.rowCount() > 0))
check_true("...e il comando e' tornato disponibile",
           panel.cadastre_button.isEnabled())
check("il dettaglio si e' riempito di nuovo",
      panel.parcel_table.rowCount(), 2)
check("...e il riepilogo pure", panel.comune_table.rowCount(), 2)
print("        Comune: {0!r}".format(
    panel.cadastre_labels["comune"].text()))
check_true("l'etichetta Comune li nomina entrambi",
           "Perugia" in panel.cadastre_labels["comune"].text()
           and "Roma" in panel.cadastre_labels["comune"].text())
check_true("...e l'etichetta Particella pure",
           "1016" in panel.cadastre_labels["particella"].text()
           and "1189" in panel.cadastre_labels["particella"].text())

print("\n-- sulla mappa --")
drawn = panel.show_parcels()
check("tutte le particelle sono disegnate", drawn, 2)
parcels = panel.layer_service().layer("parcels")
check("...una feature ciascuna", parcels.featureCount(), 2)
on_map = {(f["belfiore"], f["foglio"], f["particella"])
          for f in parcels.getFeatures()}
print("        sulla mappa: {0}".format(sorted(on_map)))
check_true("...ed e' quello che dice il modello",
           on_map == {(s.parcel.comune_code, s.parcel.foglio,
                       s.parcel.particella)
                      for s in panel.cadastral_result().shares})
check_true("ogni feature porta le due superfici e la percentuale",
           all(f["superficie_cat_ha"] > 0 and f["superficie_int_ha"] > 0
               and f["percentuale"] > 0 for f in parcels.getFeatures()))
check_text("il layer delle particelle e' nel CRS del layer CAD",
           parcels.crs().authid(), "EPSG:6706")

panel.comune_table.selectRow(0)
code = panel.comune_table.item(0, 0).data(Qt.ItemDataRole.UserRole)
picked = panel.on_comune_picked()
print("        scelto {0}: {1} particelle evidenziate".format(code, picked))
check("scegliere un Comune ne evidenzia le particelle", picked,
      len(panel.cadastral_result().shares_of(code)))
check_true("...e la selezione sulla mappa e' la sua",
           {f["belfiore"] for f in parcels.selectedFeatures()} == {code})
panel.parcel_table.selectRow(1)
check_true("scegliere una particella la mostra", panel.on_parcel_picked())
check("...ed e' una sola", parcels.selectedFeatureCount(), 1)

details = panel.show_details()
check_true("i dettagli elencano entrambi i Comuni",
           details is not None and "PERUGIA" in details.upper()
           and "ROMA" in details.upper())

print("\n-- e un errore resta un errore --")


def dead(url, timeout=0.0):
    raise cs.CadastreError("rete assente",
                           user_message="Servizio non raggiungibile.")


panel._last_commit.result = None
panel.query_cadastre(dead)
check_true("il task fallito ha risposto",
           pump(lambda: panel.cadastre_button.isEnabled(), 30.0))
print("        stato: {0!r}".format(panel.cadastre_status.text()))
check("nessuna particella inventata", panel.parcel_table.rowCount(), 0)
check_true("...e lo stato lo dice", bool(panel.cadastre_status.text()))
check_true("il pannello e' ancora usabile",
           panel.cadastre_button.isEnabled())

panel.layer_service().remove_all()
panel.teardown()

# --------------------------------------------------------------------------
# M5 - the live service, from a shape that crosses parcels
# --------------------------------------------------------------------------
print("\n== M5: il servizio vero, su una forma a cavallo di piu' particelle ==")
try:
    cs.urllib_transport(cs.EVIDENCE[0], timeout=15.0)
    reachable = True
except cs.CadastreError as exc:
    reachable = False
    skip("la forma interroga il servizio vero", exc.formatted())

if reachable:
    live_layer = cad_layer("cad_live_multi")
    live_shape = QgsGeometry.fromRect(PAIRS[0][1].boundingBox())
    live_id = put(live_layer, live_shape)
    live_tool = tb.BaseCadTool(square_tool.SquareSession())
    live_task = live_tool.request_cadastre(live_layer, live_shape, live_id)
    check_true("la richiesta e' partita", live_task is not None)
    check_true("il servizio ha risposto",
               pump(lambda: live_tool.cadastre_result is not None, 90.0))
    live_result = live_tool.cadastre_result
    written_live = live_layer.getFeature(live_id)
    print("        {0} particelle: {1}".format(
        live_result.n_parcels,
        [s.parcel.particella for s in live_result.shares][:8]))
    print("        cella Particella: {0!r}".format(
        written_live["Particella"]))
    check_true("il servizio vero riporta piu' di una particella",
               live_result.n_parcels > 1)
    check_true("...tutte con superficie intersecata positiva",
               all(s.intersection_area_m2 > 0.0
                   for s in live_result.shares))
    check_true("...e la cella le elenca",
               all(s.parcel.particella in str(written_live["Particella"])
                   for s in live_result.shares[:cs.CAD_COLUMN_LIMIT]))
    check_true("il Comune e' risolto in chiaro",
               "Perugia" in str(written_live["Comune"]))

print("\n" + "=" * 78)
panel = report = tool = live_tool = None
layer = live_layer = None
result = single = None
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
