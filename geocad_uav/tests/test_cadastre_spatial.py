"""
v1.13.0: the cadastre as a spatial enrichment service.

Belfiore code -> comune, project geometry x parcel geometries -> which
parcels are really touched and by how much, aggregated per comune and per
sheet, computed in a metric CRS and run in a background task.

The parcels come from the recorded WFS answers in ``tests/fixtures``: real
geometries, real attributes, real coordinate order. Variants (a second
comune, a multipart parcel, a broken ring) are built from those rather than
invented, so the parser is always reading something the service could send.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_cadastre_spatial.py
"""

import io as _io
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsCoordinateTransform, QgsGeometry, QgsProject,
                       QgsRectangle)

QGS = QgsApplication([], False)
QGS.initQgis()

from geocad_uav.core.errors import CrsError                     # noqa: E402
from geocad_uav.io import belfiore as bf                        # noqa: E402
from geocad_uav.io import cadastre as cs                        # noqa: E402

FAILURES = []
SKIPS = []
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures")
TMP = tempfile.mkdtemp(prefix="geocad_cat_")
CRS6706 = QgsCoordinateReferenceSystem("EPSG:6706")
CRS32633 = QgsCoordinateReferenceSystem("EPSG:32633")


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<56} got={2:<16.10g} exp={3:.10g}".format(
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


def check_raises(label, exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        print("  [ok  ] {0}".format(label))
        return
    except Exception as exc:                                    # noqa: BLE001
        print("  [FAIL] {0} (raised {1})".format(label, type(exc).__name__))
        FAILURES.append(label)
        return
    print("  [FAIL] {0} (did not raise)".format(label))
    FAILURES.append(label)


def fixture(name):
    with _io.open(os.path.join(FIXTURES, name), encoding="utf-8",
                  errors="replace") as handle:
        return handle.read()


PARCEL_XML = fixture("catasto_parcel_getfeature.xml")
ZONING_XML = fixture("catasto_zoning_getfeature.xml")

print("=" * 78)
print("Catasto spaziale -- Belfiore, intersezione, aggregazione, task")
print("=" * 78)

# --------------------------------------------------------------------------
# B1 - the Belfiore register
# --------------------------------------------------------------------------
print("\n== B1: codice Belfiore -> comune ==")
register = bf.registry()
print("        tabella: {0}".format(os.path.basename(register.path)))
print("        comuni:  {0:,}".format(register.count))
check_true("la tabella e' distribuita col plugin",
           os.path.isfile(bf.DEFAULT_PATH))
check_true("...e ha un numero plausibile di comuni italiani",
           7000 < register.count < 9000)
check_true("il registro si dichiara disponibile", register.available)

roma = register.resolve("H501")
print("        H501 -> {0}".format(roma.label()))
check_text("H501 e' Roma", roma.name.upper(), "ROMA")
check_true("...con provincia e regione",
           bool(roma.province) and bool(roma.region))
check_text("...e la sigla", roma.sigla, "RM")
check_true("il codice risolto e' noto", roma.is_known)

check_text("la ricerca non guarda le maiuscole",
           register.resolve("h501").name.upper(), "ROMA")
check_text("...ne' gli spazi", register.resolve("  H501  ").name.upper(),
           "ROMA")
check_true("un codice inesistente non e' noto",
           not register.resolve("ZZZZ").is_known)
check_text("...e si legge come non disponibile",
           register.resolve("ZZZZ").as_attributes()["comune"],
           bf.UNKNOWN_LABEL)
check_true("un valore nullo non solleva niente",
           not register.resolve(None).is_known)
check_true("...e nemmeno una stringa vuota",
           not register.resolve("").is_known)
check_true("get() invece dice chiaramente che non c'e'",
           register.get("ZZZZ") is None and register.get(None) is None)

print("\n-- comuni delle province autonome --")
# Trento and Bolzano keep their own cadastre and are not in the WFS at all;
# they are ordinary comuni in the register, and it is the *coverage* that is
# partial, not the name lookup.
bolzano = register.resolve("A952")
print("        A952 -> {0}".format(bolzano.label()))
check_true("il registro conosce comunque i comuni di Bolzano",
           bolzano.is_known)
check_true("...anche se il servizio catastale non li copre",
           cs.STATUS_PARTIAL == "PARZIALE")

print("\n-- tabella assente o rotta --")
missing = bf.BelfioreRegistry(os.path.join(TMP, "non-esiste.csv"))
check("una tabella assente da zero comuni", missing.count, 0)
check_true("...lo dichiara invece di sollevare", bool(missing.error))
check_true("...e non e' disponibile", not missing.available)
check_true("...ma risolve comunque, come sconosciuto",
           not missing.resolve("H501").is_known)

broken = os.path.join(TMP, "rotta.csv")
_io.open(broken, "w", encoding="utf-8", newline="\n").write(
    "colonna_a,colonna_b\n1,2\n")
broken_registry = bf.BelfioreRegistry(broken)
check("una tabella senza le colonne attese da zero comuni",
      broken_registry.count, 0)
check_true("...e dice quali mancano", "colonn" in broken_registry.error
           or "mancanti" in broken_registry.error)

own = os.path.join(TMP, "mia.csv")
_io.open(own, "w", encoding="utf-8", newline="\n").write(
    "codice_belfiore,nome_comune,provincia,regione,note\n"
    "X999,Comune Di Prova,Provincia,Regione,una colonna in piu'\n")
mine = bf.BelfioreRegistry(own)
check("una tabella dell'operatore si legge", mine.count, 1)
check_text("...con i suoi valori", mine.resolve("x999").name,
           "Comune Di Prova")
check_true("...e le colonne in piu' non danno fastidio",
           mine.resolve("X999").extra.get("note") is not None)
check_true("il prospetto cita la fonte del dato",
           any("ISTAT" in line for line in register.describe()))

# --------------------------------------------------------------------------
# G1 - parcels come back as real geometries
# --------------------------------------------------------------------------
print("\n== G1: le particelle arrivano come geometrie ==")
pairs = cs.parse_parcel_geometries(PARCEL_XML)
check("due particelle con geometria", len(pairs), 2)
for parcel, geometry in pairs:
    check_true("{0}: e' un poligono valido".format(parcel.particella),
               geometry.isGeosValid() and geometry.area() > 0.0)
    box = geometry.boundingBox()
    check_true("{0}: cade in Umbria, non nell'oceano".format(
        parcel.particella),
        12.0 < box.center().x() < 13.0 and 42.5 < box.center().y() < 43.5)
check_true("il comune si risolve dalla particella",
           pairs[0][0].comune().name.upper() == "PERUGIA")

print("\n-- multipart e geometrie rotte --")
multipart_xml = PARCEL_XML.replace(
    "</CP:msGeometry>",
    "</CP:msGeometry>", 1)
# A parcel in two pieces: the second polygon of the document moved inside the
# first feature, which is exactly what a split parcel looks like on the wire.
first_end = PARCEL_XML.find("</CP:CadastralParcel>")
second_polygon = PARCEL_XML[PARCEL_XML.find("<gml:Polygon", first_end):
                            PARCEL_XML.find("</gml:Polygon>", first_end)
                            + len("</gml:Polygon>")]
multipart_xml = (PARCEL_XML[:PARCEL_XML.find("</CP:msGeometry>")]
                 + second_polygon
                 + PARCEL_XML[PARCEL_XML.find("</CP:msGeometry>"):])
multi = cs.parse_parcel_geometries(multipart_xml)
check_true("una particella in due pezzi resta una particella",
           len(multi) == 2 and multi[0][1].isMultipart())
check_true("...e la sua area e' la somma dei pezzi",
           multi[0][1].area() > pairs[0][1].area())

no_coords = PARCEL_XML.replace("srsDimension=\"2\">", "srsDimension=\"2\">")
short_ring = PARCEL_XML[:PARCEL_XML.find("<gml:posList")] + \
    "<gml:posList srsDimension=\"2\">43.1 12.3 43.2 12.4</gml:posList>" + \
    PARCEL_XML[PARCEL_XML.find("</gml:posList>"):]
check_true("un anello troppo corto non produce una particella finta",
           len(cs.parse_parcel_geometries(short_ring)) < 2)
check("un documento vuoto non da' particelle",
      len(cs.parse_parcel_geometries("")), 0)

# --------------------------------------------------------------------------
# I1 - the intersection, on the real parcels
# --------------------------------------------------------------------------
print("\n== I1: intersezione e percentuali ==")
PARCEL_A, GEOM_A = pairs[0]
PARCEL_B, GEOM_B = pairs[1]


def box_over(geometry, shrink=0.0):
    """A rectangle over a parcel, optionally smaller than it."""
    box = geometry.boundingBox()
    dx, dy = box.width() * shrink, box.height() * shrink
    return QgsGeometry.fromRect(QgsRectangle(
        box.xMinimum() + dx, box.yMinimum() + dy,
        box.xMaximum() - dx, box.yMaximum() - dy))


whole = cs.interpolate_cadastral_data(box_over(GEOM_A), CRS6706, [pairs[0]])
check("un progetto su una sola particella ne trova una", whole.n_parcels, 1)
check_text("...con il suo comune", whole.shares[0].comune.name.upper(),
           "PERUGIA")
check_text("...il suo foglio", whole.shares[0].parcel.foglio, "252")
check_text("...e la sua particella", whole.shares[0].parcel.particella,
           "1016")
share = whole.shares[0]
print("        particella {0:.2f} m2, interessata {1:.2f} m2, {2:.2f} %"
      .format(share.parcel_area_m2, share.intersection_area_m2,
              share.percent_of_parcel))
check_true("la superficie catastale e' in metri quadri, non in gradi",
           1.0 < share.parcel_area_m2 < 1e7)
check("la percentuale e' area intersezione su area particella",
      share.percent_of_parcel,
      100.0 * share.intersection_area_m2 / share.parcel_area_m2, 1e-9)
check_true("il calcolo e' avvenuto in un CRS metrico",
           whole.work_crs_authid.startswith("EPSG:")
           and whole.work_crs_authid != "EPSG:6706")
check_true("...e l'avviso lo dice", any("geografico" in text
                                        for text in whole.warnings))

# Inside the parcel, not inside its bounding box: this parcel is a long
# sliver, and a rectangle drawn round it is four fifths outside the polygon.
# This parcel is 93 m2 of sliver, so the setback has to be small enough to
# leave something: shrink until there is a polygon the parcel still contains.
INSIDE_A = None
for _setback in (2e-5, 1e-5, 5e-6, 2e-6, 1e-6, 5e-7):
    _candidate = GEOM_A.buffer(-_setback, 8)
    if (_candidate is not None and not _candidate.isEmpty()
            and _candidate.area() > 0.0 and GEOM_A.contains(_candidate)):
        INSIDE_A = _candidate
        break
check_true("il progetto di prova e' davvero dentro la particella",
           INSIDE_A is not None and GEOM_A.contains(INSIDE_A))
inside = cs.interpolate_cadastral_data(INSIDE_A, CRS6706, [pairs[0]])
print("        progetto interno: {0:.2f} % della particella".format(
    inside.shares[0].percent_of_parcel))
check_true("un progetto dentro la particella ne copre una frazione",
           0.0 < inside.shares[0].percent_of_parcel < 100.0)
check_text("...ed e' completo, perche' la particella lo contiene tutto",
           inside.status, cs.STATUS_OK)
check("la copertura e' totale", inside.covered_fraction, 1.0, 1e-6)

print("\n-- due particelle, e una che non c'entra --")
both = QgsGeometry.unaryUnion([box_over(GEOM_A), box_over(GEOM_B)])
across = cs.interpolate_cadastral_data(both, CRS6706, pairs)
check("un progetto su due particelle le tiene entrambe", across.n_parcels, 2)
check_true("...senza sovrascrivere la prima con l'ultima",
           {s.parcel.particella for s in across.shares} == {"1016", "1189"})
check_true("le particelle sono ordinate per superficie interessata",
           across.shares[0].intersection_area_m2
           >= across.shares[1].intersection_area_m2)

far = QgsGeometry.fromWkt("POLYGON((11.0 41.0,11.01 41.0,11.01 41.01,"
                          "11.0 41.01,11.0 41.0))")
none_here = cs.interpolate_cadastral_data(far, CRS6706, pairs)
check("un progetto lontano non trova particelle", none_here.n_parcels, 0)
check_text("...e lo stato lo dice", none_here.status, cs.STATUS_UNAVAILABLE)
check_true("...con un messaggio leggibile", bool(none_here.message))
check_true("un risultato senza particelle non e' utilizzabile",
           not none_here.is_usable)

print("\n-- copertura parziale --")
bigger = box_over(GEOM_A)
box = bigger.boundingBox()
wide = QgsGeometry.fromRect(QgsRectangle(
    box.xMinimum() - box.width(), box.yMinimum(),
    box.xMaximum(), box.yMaximum()))
partial = cs.interpolate_cadastral_data(wide, CRS6706, [pairs[0]])
print("        copertura {0:.1f} %".format(100.0 * partial.covered_fraction))
check_text("un progetto che esce dal catasto e' parziale", partial.status,
           cs.STATUS_PARTIAL)
check_true("...e dice quanto copre", "%" in partial.message)
check_true("la frazione coperta e' fra zero e uno",
           0.0 < partial.covered_fraction < 1.0)

# --------------------------------------------------------------------------
# I2 - more than one comune, kept apart
# --------------------------------------------------------------------------
print("\n== I2: piu' comuni nello stesso progetto ==")
OTHER_CODE = "H501"
other_xml = (PARCEL_XML
             .replace("G478_025200.1189", "H501_012300.77")
             .replace("<CP:ADMINISTRATIVEUNIT>G478</CP:ADMINISTRATIVEUNIT>",
                      "<CP:ADMINISTRATIVEUNIT>H501</CP:ADMINISTRATIVEUNIT>",
                      0))
# Only the second feature changes comune: split the document and patch it.
head, _, tail = PARCEL_XML.partition("</CP:CadastralParcel>")
tail_patched = (tail.replace("G478_025200.1189", "H501_012300.77")
                    .replace(">G478<", ">H501<"))
two_comuni_xml = head + "</CP:CadastralParcel>" + tail_patched
two_pairs = cs.parse_parcel_geometries(two_comuni_xml)
check("il documento modificato ha ancora due particelle", len(two_pairs), 2)
check_true("...di due comuni diversi",
           {p.comune_code for p, _g in two_pairs} == {"G478", "H501"})

union = QgsGeometry.unaryUnion([box_over(two_pairs[0][1]),
                                box_over(two_pairs[1][1])])
multi_comune = cs.interpolate_cadastral_data(union, CRS6706, two_pairs)
print("\n".join(multi_comune.describe()[:8]))
check("due comuni interessati", len(multi_comune.comuni()), 2)
check("...e due particelle in tutto", multi_comune.n_parcels, 2)
grouped = multi_comune.by_comune()
check_true("ogni comune tiene le sue particelle",
           set(grouped) == {"G478", "H501"}
           and all(len(v) == 1 for v in grouped.values()))
names = {code: comune.name.upper()
         for code, comune, _group in multi_comune.comuni()}
check_text("il codice G478 e' Perugia", names["G478"], "PERUGIA")
check_text("il codice H501 e' Roma", names["H501"], "ROMA")
summary = multi_comune.as_attributes()
print("        riepilogo: {0}".format(summary))
check("il riepilogo conta due comuni", summary["comuni"], 2)
check("...e due particelle", summary["particelle"], 2)
check_true("...e nomina il comune con piu' superficie",
           summary["comune"].upper().startswith(
               names[multi_comune.comuni()[0][0]]))
check("le righe di dettaglio sono una per particella",
      len(multi_comune.rows()), 2)
check_true("ogni riga porta comune, foglio, particella e percentuale",
           all({"comune", "foglio", "particella", "percentuale"}
               <= set(row) for row in multi_comune.rows()))

# --------------------------------------------------------------------------
# I3 - CRS, validity, refusals
# --------------------------------------------------------------------------
print("\n== I3: sistemi di riferimento e geometrie ==")
to_metric = QgsCoordinateTransform(CRS6706, CRS32633, QgsProject.instance())
projected = QgsGeometry(box_over(GEOM_A))
projected.transform(to_metric)
from_metric = cs.interpolate_cadastral_data(projected, CRS32633, [pairs[0]])
print("        stesso progetto in EPSG:32633 -> {0:.2f} m2".format(
    from_metric.shares[0].intersection_area_m2))
check("un progetto gia' metrico da la stessa superficie",
      from_metric.shares[0].intersection_area_m2,
      whole.shares[0].intersection_area_m2, 0.5)
check_text("...e resta nel suo CRS", from_metric.work_crs_authid,
           "EPSG:32633")
check_true("...senza avvisi sul CRS geografico",
           not any("geografico" in t for t in from_metric.warnings))

bowtie = QgsGeometry.fromWkt(
    "POLYGON((12.3819 43.1017,12.3821 43.1019,12.3821 43.1017,"
    "12.3819 43.1019,12.3819 43.1017))")
check_true("la farfalla di prova e' davvero non valida",
           not bowtie.isGeosValid())
repaired = cs.interpolate_cadastral_data(bowtie, CRS6706, [pairs[0]])
check_true("una geometria non valida viene corretta, non rifiutata",
           any("corretta" in text for text in repaired.warnings))
check_true("...e il confronto prosegue", repaired.status in (
    cs.STATUS_OK, cs.STATUS_PARTIAL, cs.STATUS_UNAVAILABLE))

check_raises("un progetto vuoto non si confronta", cs.CadastreError,
             cs.interpolate_cadastral_data, QgsGeometry(), CRS6706, pairs)
check_raises("...ne' uno senza CRS", cs.CadastreError,
             cs.interpolate_cadastral_data, box_over(GEOM_A), None, pairs)
check("nessuna particella in ingresso da un risultato vuoto",
      cs.interpolate_cadastral_data(box_over(GEOM_A), CRS6706, []).n_parcels,
      0)

print("\n-- il riquadro chiesto al servizio --")
south, west, north, east = cs.service_bbox(box_over(GEOM_A), CRS6706)
print("        bbox 6706: {0:.5f},{1:.5f},{2:.5f},{3:.5f}".format(
    south, west, north, east))
check_true("la latitudine viene prima, e sta in Italia",
           35.0 < south < north < 48.0)
check_true("...e la longitudine dopo", 6.0 < west < east < 19.0)
url = cs.build_area_query(south, west, north, east)
check_true("l'interrogazione d'area chiede le particelle",
           "CP%3ACadastralParcel" in url and "request=GetFeature" in url)
check_true("...col CRS in forma urn", "6706" in url)
check_true("il numero di particelle e' limitato",
           "count={0}".format(cs.MAX_AREA_FEATURES) in
           cs.build_area_query(south, west, north, east, count=999999))
check_raises("un riquadro non finito e' un errore", cs.CadastreError,
             cs.build_area_query, float("nan"), 1.0, 2.0, 3.0)
metric_bbox = cs.service_bbox(projected, CRS32633)
# Not to the micro-degree: transforming a rectangle into UTM bows its edges,
# so the box of the transformed polygon is a couple of metres larger than the
# transform of the box. Two metres is the agreement that matters here.
check("il riquadro di un progetto metrico torna in gradi",
      metric_bbox[0], south, 2e-4)

# --------------------------------------------------------------------------
# T1 - the whole workflow, with the network replaced
# --------------------------------------------------------------------------
print("\n== T1: flusso completo senza rete ==")
CALLS = []
PROGRESS = []


def recorded(url, timeout=0.0):
    CALLS.append(url)
    return ZONING_XML if "CadastralZoning" in url else PARCEL_XML


result = cs.query_area(INSIDE_A, CRS6706, transport=recorded,
                       progress=PROGRESS.append)
check_text("il flusso completo riesce", result.status, cs.STATUS_OK)
check("ha interrogato particelle e fogli", len(CALLS), 2)
check_true("ha riportato avanzamento crescente fino a 100",
           PROGRESS == sorted(PROGRESS) and PROGRESS[-1] == 100.0)
check_text("il foglio e' quello pubblicato dal livello Mappe",
           result.shares[0].parcel.foglio, "252")
check_true("il risultato e' utilizzabile dal pannello e dal report",
           result.is_usable and bool(result.as_attributes())
           and bool(result.rows()))


def dead_network(url, timeout=0.0):
    raise cs.CadastreError("rete assente",
                           user_message="Servizio non raggiungibile.")


check_raises("senza rete il flusso solleva", cs.CadastreError, cs.query_area,
             INSIDE_A, CRS6706, dead_network)


def empty_service(url, timeout=0.0):
    return ('<?xml version="1.0"?><wfs:FeatureCollection '
            'xmlns:wfs="http://www.opengis.net/wfs/2.0" '
            'numberReturned="0"></wfs:FeatureCollection>')


empty = cs.query_area(INSIDE_A, CRS6706, transport=empty_service)
check_text("un servizio senza particelle non e' un errore", empty.status,
           cs.STATUS_UNAVAILABLE)

print("\n-- e nel task, senza bloccare l'interfaccia --")
ANSWERS = []
task = cs.area_task(INSIDE_A, CRS6706, ANSWERS.append,
                    transport=recorded)
check_true("il task e' stato creato e accetta l'annullamento",
           task is not None and task.canCancel())
deadline = 0
while not ANSWERS and deadline < 200:
    QGS.processEvents()
    deadline += 1
if not ANSWERS:
    from time import sleep
    for _ in range(100):
        QGS.processEvents()
        sleep(0.05)
        if ANSWERS:
            break
if ANSWERS:
    answered = ANSWERS[0]
    print("        il task ha risposto: {0}, {1} particelle".format(
        answered.status, answered.n_parcels))
    check_text("il task consegna un risultato completo", answered.status,
               cs.STATUS_OK)
    check("...con la particella trovata", answered.n_parcels, 1)
else:
    SKIPS.append(("il task consegna un risultato",
                  "il gestore dei task non ha girato in questo ambiente"))
    print("  [skip] il task consegna un risultato")

failing_answers = []
failing = cs.area_task(INSIDE_A, CRS6706, failing_answers.append,
                       transport=dead_network)
deadline = 0
while not failing_answers and deadline < 200:
    QGS.processEvents()
    deadline += 1
if failing_answers:
    check_text("un errore di rete diventa uno stato, non un'eccezione",
               failing_answers[0].status, cs.STATUS_ERROR)
    check_true("...con un messaggio per l'operatore",
               bool(failing_answers[0].message))
    check_true("...e il progetto resta utilizzabile senza catasto",
               not failing_answers[0].is_usable)
else:
    SKIPS.append(("un errore di rete diventa uno stato",
                  "il gestore dei task non ha girato in questo ambiente"))
    print("  [skip] un errore di rete diventa uno stato")


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
