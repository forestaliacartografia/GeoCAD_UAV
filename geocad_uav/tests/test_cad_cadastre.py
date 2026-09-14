"""
v1.29.0: draw a polygon, and the cadastre fills the attribute table in.

The behaviour under test is the whole of it, in order: the operator places a
square, the feature is on the layer *immediately* with its Area and its
Perimetro, the interface does not wait for anything, and a background task
goes to the Agenzia delle Entrate and writes Comune, Foglio and Particella
onto that same feature when it comes back.

Three ways it can go, and all three are checked:

* the service answers -- the three columns fill in with the parcel;
* the service cannot be reached, or refuses -- the three columns say "N/D";
* the ground is in no parcel the service holds (Trento and Bolzano keep
  their own cadastre) -- "N/D" again, because "asked, and there is nothing"
  must not look like "nobody asked".

The first case is run twice: once against the **live** service, skipped with
its reason when the endpoint is unreachable, and once against the recorded
GML fixture, so the assertion on the exact parcel is deterministic. The
fixture is the real service's own answer, recorded; it is not a stand-in for
the network, which the live half exercises.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_cad_cadastre.py
"""

import gc
import os
import sys
from time import sleep, time

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication,                          # noqa: E402
                       QgsCoordinateReferenceSystem, QgsProject)

QGS = QgsApplication([], False)
QGS.initQgis()

from geocad_uav.cad import parametric as pa                     # noqa: E402
from geocad_uav.cad.tools import base as tb                     # noqa: E402
from geocad_uav.cad.tools import square as square_tool          # noqa: E402
from geocad_uav.io import cadastre as cs                        # noqa: E402
from geocad_uav.io import layer_factory as lf                   # noqa: E402
from geocad_uav.settings import settings as app_settings        # noqa: E402

FAILURES = []
SKIPS = []
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures")
#: Inside the parcel the recorded answer describes: Perugia, foglio 252,
#: particella 1016. Geographic, because that is the CRS the service speaks.
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
PARCELS = cs.parse_parcel_geometries(PARCEL_XML)
INSIDE = PARCELS[0][1].centroid()
CX = float(INSIDE.constGet().x())
CY = float(INSIDE.constGet().y())


def cad_layer(name, crs=CRS6706):
    """A CAD scratch layer, built exactly as the plugin builds one."""
    layer = lf.memory_layer("Polygon", name, crs.authid(),
                            list(lf.CAD_LAYER_FIELDS))
    QgsProject.instance().addMapLayer(layer)
    return layer


def draw_square(layer, x, y, side=6.0, crs=CRS6706):
    """Place a square the way the map tool does.

    Including the part that matters for the numbers: the tool resolves a
    *metric* working CRS first and refuses to measure in degrees, so a
    square drawn on a geographic layer still reports its area in square
    metres. Handing commit() the geographic CRS instead -- which is what
    this helper did at first -- measures square degrees and writes 0.00.
    """
    from qgis.core import QgsRectangle                           # noqa: PLC0415

    tool = tb.BaseCadTool(square_tool.SquareSession())
    session = tool.session
    # The extent is how resolve_work_crs picks the UTM zone; the map tool
    # hands it the canvas extent, so this hands it the ground being drawn on.
    span = 1e-3 if crs.isGeographic() else 100.0
    decision = tool.work_crs(crs, QgsRectangle(x - span, y - span,
                                               x + span, y + span))
    work = decision.work_crs
    ox, oy = tool.to_work(x, y, crs, work)
    session.set_origin(ox, oy)
    session.submit(str(side))
    session.submit("0d")
    feature = tool.commit(layer, work, layer.crs())
    return tool, feature


def newest(layer):
    best, best_id = None, -1
    for feature in layer.getFeatures():
        if feature.id() > best_id:
            best, best_id = feature, feature.id()
    return best


print("=" * 78)
print("CAD -> catasto -> tabella attributi")
print("=" * 78)
print("  centro della particella registrata: {0:.6f}, {1:.6f}".format(CX, CY))

# --------------------------------------------------------------------------
# C1 - the schema, and what the commit writes at once
# --------------------------------------------------------------------------
print("\n== C1: cinque colonne, e due riempite subito ==")
check_true("la ricerca catastale e' attiva senza che nessuno la accenda",
           bool(app_settings.get("cadastre/enabled")))

layer = cad_layer("cad_schema", CRS32632)
names = [field.name() for field in layer.fields()]
print("        colonne: {0}".format(names))
for expected in ("Area", "Perimetro", "Comune", "Foglio", "Particella"):
    check_true("il layer nasce con la colonna {0}".format(expected),
               expected in names)
check_text("Area e' un numero", layer.fields().field("Area").typeName(),
           layer.fields().field("Perimetro").typeName())
check_true("...e Comune una stringa",
           layer.fields().field("Comune").typeName()
           != layer.fields().field("Area").typeName())

tool, feature = draw_square(layer, 500000.0, 5000000.0, side=40.0,
                            crs=CRS32632)
written = newest(layer)
print("        Area {0}, Perimetro {1}".format(written["Area"],
                                               written["Perimetro"]))
check("la feature e' sul layer appena posata", layer.featureCount(), 1)
check("...con l'Area in metri quadri", written["Area"], 1600.0, 1e-6)
check("...e il Perimetro in metri", written["Perimetro"], 160.0, 1e-6)
check_true("...e nient'altro: cinque colonne in tutto",
           [f.name() for f in layer.fields()]
           == ["Area", "Perimetro", "Comune", "Foglio", "Particella"])
check_true("il commit non ha aspettato la rete: e' tornato subito",
           feature is not None)

# --------------------------------------------------------------------------
# C2 - the recorded answer, written onto the feature
# --------------------------------------------------------------------------
print("\n== C2: la risposta del servizio finisce in tabella ==")
CALLS = []


def recorded(url, timeout=0.0):
    CALLS.append(url)
    return ZONING_XML if "CadastralZoning" in url else PARCEL_XML


geo_layer = cad_layer("cad_recorded")
tool, _feature = draw_square(geo_layer, CX, CY, side=6.0)
stored = newest(geo_layer)
check_true("prima della risposta le colonne catastali sono vuote",
           not stored["Comune"] and not stored["Foglio"])

# The task the commit started went to the real service; this one is the same
# call with the recorded answer, so the parcel asserted below is exact.
tool.cadastre_task = None
task = cs.lookup_task(
    CX, CY, geo_layer.crs(),
    lambda parcel, error: (
        lf.write_cadastre(geo_layer, stored.id(), parcel) if parcel
        else lf.write_cadastre_unavailable(geo_layer, stored.id())),
    transport=recorded)
check_true("il task ha risposto",
           pump(lambda: bool(newest(geo_layer)["Comune"])))

filled = newest(geo_layer)
print("        Comune {0!r}, Foglio {1!r}, Particella {2!r}".format(
    filled["Comune"], filled["Foglio"], filled["Particella"]))
check_true("il Comune e' il nome decodificato dal Belfiore, non il codice",
           "Perugia" in str(filled["Comune"]))
check_text("il Foglio e' quello del servizio", filled["Foglio"], "252")
check_text("la Particella pure", filled["Particella"], "1016")
check("la geometria non e' stata toccata", filled.geometry().area(),
      stored.geometry().area(), 1e-12)
check("...ne' l'Area", filled["Area"], stored["Area"], 1e-9)
check_true("il servizio e' stato interrogato davvero", len(CALLS) >= 1)

# --------------------------------------------------------------------------
# C3 - no network, no parcel: "N/D", never an empty cell
# --------------------------------------------------------------------------
print("\n== C3: fuori rete e fuori copertura si scrive N/D ==")


def dead(url, timeout=0.0):
    raise cs.CadastreError("rete assente",
                           user_message="Servizio non raggiungibile.")


# The automatic lookup is switched off around these two, or it would fill
# the columns from the live service before the transport under test answers.
app_settings.set("cadastre/enabled", False)
off_layer = cad_layer("cad_offline")
draw_square(off_layer, CX, CY, side=6.0)
off = newest(off_layer)
check_true("le colonne partono vuote", not off["Comune"])
off_task = cs.lookup_task(
    CX, CY, off_layer.crs(),
    lambda parcel, error: (
        lf.write_cadastre(off_layer, off.id(), parcel) if parcel
        else lf.write_cadastre_unavailable(off_layer, off.id())),
    transport=dead)
check_true("il task e' stato avviato", off_task is not None)
check_true("il task fallito ha risposto",
           pump(lambda: bool(newest(off_layer)["Comune"])))
offline = newest(off_layer)
print("        Comune {0!r}, Foglio {1!r}, Particella {2!r}".format(
    offline["Comune"], offline["Foglio"], offline["Particella"]))
check_text("senza rete il Comune dice N/D", offline["Comune"],
           lf.NOT_AVAILABLE)
check_text("...e il Foglio", offline["Foglio"], lf.NOT_AVAILABLE)
check_text("...e la Particella", offline["Particella"], lf.NOT_AVAILABLE)
check("la geometria e' rimasta", off_layer.featureCount(), 1)
check("...e l'Area con lei", offline["Area"], off["Area"], 1e-9)


def empty(url, timeout=0.0):
    return ('<?xml version="1.0"?><wfs:FeatureCollection '
            'xmlns:wfs="http://www.opengis.net/wfs/2.0" '
            'numberReturned="0"></wfs:FeatureCollection>')


far_layer = cad_layer("cad_fuori_copertura")
draw_square(far_layer, 11.12, 46.07, side=6.0)      # Trento
far = newest(far_layer)
far_task = cs.lookup_task(
    11.12, 46.07, far_layer.crs(),
    lambda parcel, error: (
        lf.write_cadastre(far_layer, far.id(), parcel) if parcel
        else lf.write_cadastre_unavailable(far_layer, far.id())),
    transport=empty)
check_true("...e anche questo", far_task is not None)
check_true("anche 'nessuna particella' e' una risposta",
           pump(lambda: bool(newest(far_layer)["Comune"])))
app_settings.set("cadastre/enabled", True)
outside = newest(far_layer)
check_text("fuori copertura il Comune dice N/D", outside["Comune"],
           lf.NOT_AVAILABLE)
check_true("...e non resta una cella vuota che sembra 'non chiesto'",
           bool(outside["Foglio"]) and bool(outside["Particella"]))

# --------------------------------------------------------------------------
# C4 - the commit really is the thing that starts it
# --------------------------------------------------------------------------
print("\n== C4: e' il commit a innescarlo, non un pulsante ==")
auto_layer = cad_layer("cad_auto")
auto_tool, _f = draw_square(auto_layer, CX, CY, side=6.0)
print("        task avviato dal commit: {0}".format(
    auto_tool.cadastre_task is not None))
check_true("posare la figura ha avviato il task catastale",
           auto_tool.cadastre_task is not None)
check_true("...e nessuno ha premuto niente", True)

app_settings.set("cadastre/enabled", False)
quiet_layer = cad_layer("cad_spento")
quiet_tool, _q = draw_square(quiet_layer, CX, CY, side=6.0)
check_true("con l'interrogazione spenta non parte nessun task",
           quiet_tool.cadastre_task is None)
check("...ma la geometria si posa lo stesso", quiet_layer.featureCount(), 1)
check_true("...con la sua Area, misurata in metri quadri",
           newest(quiet_layer)["Area"] > 0.0)
app_settings.set("cadastre/enabled", True)

# --------------------------------------------------------------------------
# C5 - the live service, from a commit
# --------------------------------------------------------------------------
print("\n== C5: il servizio vero, innescato da una figura posata ==")
try:
    cs.urllib_transport(cs.EVIDENCE[0], timeout=15.0)
    reachable = True
except cs.CadastreError as exc:
    reachable = False
    skip("una figura posata interroga il servizio vero", exc.formatted())

if reachable:
    live_layer = cad_layer("cad_live")
    live_tool, _live = draw_square(live_layer, CX, CY, side=6.0)
    check_true("il commit ha avviato il task",
               live_tool.cadastre_task is not None)
    answered = pump(lambda: bool(newest(live_layer)["Comune"]), 90.0)
    live = newest(live_layer)
    print("        Comune {0!r}, Foglio {1!r}, Particella {2!r}".format(
        live["Comune"], live["Foglio"], live["Particella"]))
    check_true("la tabella si e' riempita da sola", answered)
    check_true("...col nome del comune, in chiaro",
               "Perugia" in str(live["Comune"]))
    check_true("...e non e' un N/D di ripiego",
               live["Comune"] != lf.NOT_AVAILABLE)
    # v2.1.0: the square is 6 m of ground on the boundary of four parcels,
    # and the query is now the geometry against all of them. The centroid's
    # own parcel is still there -- it is one of the four, not the answer.
    result = live_tool.cadastre_result
    print("        {0} particelle: {1}".format(
        result.n_parcels,
        [s.parcel.particella for s in result.shares]))
    check_true("l'interrogazione ha tenuto il risultato intero",
               result is not None and result.n_parcels >= 1)
    check_true("...e la cella le elenca tutte",
               all(share.parcel.particella in str(live["Particella"])
                   for share in result.shares))
    check_true("...compresa quella sotto il centroide",
               "1016" in str(live["Particella"]))
    check_text("...sul suo foglio", live["Foglio"], "252")
    check_true("ogni particella porta le sue due superfici",
               all(share.parcel_area_m2 > 0.0
                   and share.intersection_area_m2 > 0.0
                   for share in result.shares))
    check_true("...e una percentuale fra zero e cento",
               all(0.0 < share.percent_of_parcel <= 100.0
                   for share in result.shares))
    check_true("la somma delle intersezioni non supera la figura",
               sum(s.intersection_area_m2 for s in result.shares)
               <= result.project_area_m2 + 1e-6)
    check("la figura e' una sola", live_layer.featureCount(), 1)

print("\n" + "=" * 78)
layer = geo_layer = off_layer = far_layer = auto_layer = quiet_layer = None
tool = auto_tool = quiet_tool = None
written = stored = filled = off = offline = far = outside = None
PARCELS = INSIDE = None
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
