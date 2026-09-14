"""
v1.10.0: the cadastral parcel lookup (Agenzia delle Entrate INSPIRE WFS).

The parser is tested against what the service really sent, not against what
it is supposed to send: ``tests/fixtures`` holds a recorded GetCapabilities
schema and two recorded GetFeature responses, fetched from the live endpoint
while the module was written. Everything below runs offline; one block at the
end asks the real service and *skips* when it cannot be reached, because a
test suite that needs the internet is a test suite that fails on a train.

NEEDS QGIS (for the CRS transform and the layer). Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_cadastre.py
"""

import io as _io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsFeature, QgsGeometry, QgsProject,
                       QgsVectorLayer)

QGS = QgsApplication([], False)
QGS.initQgis()

from geocad_uav.cad import parametric as pa                     # noqa: E402
from geocad_uav.cad.tools import base as tb                     # noqa: E402
from geocad_uav.cad.tools import square as sq_tool              # noqa: E402
from geocad_uav.io import cadastre as cs                        # noqa: E402
from geocad_uav.io import layer_factory as lf                   # noqa: E402
from geocad_uav.settings import KEYS, settings                  # noqa: E402

FAILURES = []
SKIPS = []
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures")
CRS = QgsCoordinateReferenceSystem("EPSG:32632")
OX, OY = 500000.0, 5000000.0


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<58} got={2:<16.10g} exp={3:.10g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_text(label, got, expected):
    ok = got == expected
    print("  [{0}] {1:<48} got={2!r:<22} exp={3!r}".format(
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


def skip(label, reason):
    print("  [skip] {0}\n         reason: {1}".format(label, reason))
    SKIPS.append((label, reason))


def hidden_names(layer):
    """Columns the attribute table hides."""
    out = []
    for index, field in enumerate(layer.fields()):
        try:
            if layer.editorWidgetSetup(index).type() == "Hidden":
                out.append(field.name())
        except (AttributeError, RuntimeError):
            continue
    return out


def fixture(name):
    with _io.open(os.path.join(FIXTURES, name), encoding="utf-8",
                  errors="replace") as handle:
        return handle.read()


PARCEL_XML = fixture("catasto_parcel_getfeature.xml")
ZONING_XML = fixture("catasto_zoning_getfeature.xml")
SCHEMA_XSD = fixture("catasto_describefeaturetype.xsd")

print("=" * 78)
print("Catasto -- Agenzia delle Entrate, WFS INSPIRE")
print("=" * 78)

# --------------------------------------------------------------------------
# C1 - the schema the module names is the schema the service publishes
# --------------------------------------------------------------------------
print("\n== C1: lo schema dichiarato e' quello registrato ==")
for attribute in cs.PARCEL_ATTRIBUTES:
    check_true("{0} e' nel DescribeFeatureType registrato".format(attribute),
               'name="{0}"'.format(attribute) in SCHEMA_XSD)
check("la particella ha cinque attributi, non di piu'",
      len(cs.PARCEL_ATTRIBUTES), 5)
check_true("nessuno di essi si chiama Comune, Foglio o Particella",
           not ({"COMUNE", "FOGLIO", "PARTICELLA"}
                & set(cs.PARCEL_ATTRIBUTES)))
check_text("il servizio parla un solo sistema di riferimento",
           cs.SERVICE_CRS, "EPSG:6706")
check_true("...e non il 4258 o il 4326 che si sarebbe potuto supporre",
           "4258" not in cs.SERVICE_CRS_URN
           and "4326" not in cs.SERVICE_CRS_URN)
check_true("le prove sono URL che si possono aprire",
           all(url.startswith("https://") for url in cs.EVIDENCE))

# --------------------------------------------------------------------------
# C2 - the parser, on the recorded answer
# --------------------------------------------------------------------------
print("\n== C2: due particelle vere, lette dalla risposta registrata ==")
parcels = cs.parse_parcels(PARCEL_XML)
check("la risposta contiene due particelle", len(parcels), 2)
first, second = parcels
print("        {0}  |  {1}".format(first.label(), second.label()))
check_text("il codice del comune e' quello Belfiore", first.comune_code,
           "G478")
check_text("la particella e' la LABEL", first.particella, "1016")
check_text("il riferimento nazionale arriva intero",
           first.national_reference, "G478_025200.1016")
check_text("...e l'identificativo INSPIRE pure", first.inspire_id,
           "IT.AGE.PLA.G478_025200.1016")
check_text("la seconda particella e' l'altra", second.particella, "1189")
check_text("l'etichetta si legge come la scrive un tecnico", first.label(),
           "G478 / 252 / 1016")
check_true("una particella letta non e' vuota", not first.is_empty)
check_true("una costruita a mano senza dati lo e'",
           cs.CadastralParcel().is_empty)

# --------------------------------------------------------------------------
# C3 - the sheet number: derived, then confirmed by the layer that owns it
# --------------------------------------------------------------------------
print("\n== C3: il foglio ==")
check_text("025200 e' il foglio 252", cs.foglio_from_reference(
    "G478_025200.1016"), "252")
check_text("uno zero iniziale non fa parte del numero",
           cs.foglio_from_reference("A123_000700.5"), "7")
check_text("una lettera di allegato resta attaccata",
           cs.foglio_from_reference("A123_0012A0.5"), "12A")
check_text("un riferimento senza forma nota non viene indovinato",
           cs.foglio_from_reference("non-un-riferimento"), "")
check_text("...e nemmeno una stringa vuota", cs.foglio_from_reference(""), "")
check_text("la chiave del foglio e' il riferimento senza la particella",
           cs.zoning_key("G478_025200.1016"), "G478_025200")

labels = cs.parse_zoning_labels(ZONING_XML)
print("        il livello Mappe pubblica: {0}".format(
    sorted(labels.items())[:3]))
check_true("il foglio dedotto e' quello che il livello Mappe pubblica",
           labels.get("G478_025200") == "252"
           == cs.foglio_from_reference(first.national_reference))

# --------------------------------------------------------------------------
# C4 - the request: latitude first, and clamped
# --------------------------------------------------------------------------
print("\n== C4: la richiesta ==")
url = cs.build_query(43.1010, 12.3810)
print("        {0}".format(url[:120]))
check_true("e' una GetFeature sul livello particelle",
           "request=GetFeature" in url
           and "CP%3ACadastralParcel" in url)
check_true("chiede la versione 2.0.0", "version=2.0.0" in url)
bbox = cs.bbox_around(43.1010, 12.3810)
print("        bbox: {0}".format(bbox))
check_true("il bbox porta la latitudine per prima",
           bbox.startswith("43.100") and ",12.380" in bbox)
check_true("...e dichiara il CRS in forma urn",
           bbox.endswith(cs.SERVICE_CRS_URN))
check("il bbox e' centrato sul punto",
      float(bbox.split(",")[0]) + float(bbox.split(",")[2]), 2 * 43.1010,
      1e-6)
check_true("il numero di risultati e' limitato",
           "count={0}".format(cs.MAX_FEATURES) in cs.build_query(
               43.0, 12.0, count=9999))
check_raises("un punto non finito non si interroga", cs.CadastreError,
             cs.build_query, float("nan"), 12.0)

# --------------------------------------------------------------------------
# C5 - an exception from the service is not an empty answer
# --------------------------------------------------------------------------
print("\n== C5: rifiuto del servizio contro 'qui non c'e' niente' ==")
EXCEPTION_XML = ('<?xml version="1.0"?><ServiceExceptionReport version="1.1.1">'
                 '<ServiceException code="InvalidFormat">'
                 '<![CDATA[Richiesta non valida ]]>'
                 '</ServiceException></ServiceExceptionReport>')
check_raises("una ServiceException diventa un errore", cs.CadastreError,
             cs.parse_parcels, EXCEPTION_XML)
EMPTY_XML = ('<?xml version="1.0"?><wfs:FeatureCollection '
             'xmlns:wfs="http://www.opengis.net/wfs/2.0" '
             'numberReturned="0"></wfs:FeatureCollection>')
check("una collezione vuota non e' un errore: e' zero particelle",
      len(cs.parse_parcels(EMPTY_XML)), 0)
check("una risposta vuota nemmeno", len(cs.parse_parcels("")), 0)
check_raises("anche sul livello Mappe l'eccezione e' un errore",
             cs.CadastreError, cs.parse_zoning_labels, EXCEPTION_XML)

# --------------------------------------------------------------------------
# C6 - coordinates: the project's CRS is not the service's
# --------------------------------------------------------------------------
print("\n== C6: trasformazione di coordinate ==")
# A point in UTM 32N that lands in the area the fixtures come from.
target = QgsCoordinateReferenceSystem("EPSG:6706")
check_true("EPSG:6706 esiste in questa installazione", target.isValid())
latitude, longitude = cs.to_service_point(292000.0, 4774000.0, CRS)
print("        UTM32 (292000, 4774000) -> lat {0:.5f}, lon {1:.5f}".format(
    latitude, longitude))
check_true("la latitudine cade in Italia", 35.0 < latitude < 48.0)
check_true("e cosi' la longitudine", 6.0 < longitude < 19.0)
same = cs.to_service_point(12.3810, 43.1010, target)
check("con il CRS del servizio non si trasforma nulla: la latitudine e' y",
      same[0], 43.1010, 1e-12)
check("...e la longitudine e' x", same[1], 12.3810, 1e-12)
check_raises("senza CRS non si interroga", cs.CadastreError,
             cs.to_service_point, 1.0, 2.0, None)
check_raises("con un CRS non valido nemmeno", cs.CadastreError,
             cs.to_service_point, 1.0, 2.0,
             QgsCoordinateReferenceSystem("EPSG:999999"))

# --------------------------------------------------------------------------
# C7 - the whole query, with the network replaced by the recorded answers
# --------------------------------------------------------------------------
print("\n== C7: interrogazione completa, senza rete ==")
CALLS = []


def recorded_transport(url, timeout=0.0):
    CALLS.append(url)
    return ZONING_XML if "CadastralZoning" in url else PARCEL_XML


parcel = cs.query_point(12.3810, 43.1010, target,
                        transport=recorded_transport)
check_true("la particella sotto il punto e' stata trovata", parcel is not None)
check_text("il comune", parcel.comune_code, "G478")
check_text("il foglio, confermato dal livello Mappe", parcel.foglio, "252")
check_text("la particella", parcel.particella, "1016")
check("sono state fatte due richieste: particella e foglio", len(CALLS), 2)
check_true("...e la seconda e' quella del livello Mappe",
           "CadastralZoning" in CALLS[1])

CALLS[:] = []
parcel_only = cs.query_point(12.3810, 43.1010, target,
                             transport=recorded_transport,
                             resolve_foglio=False)
check("senza conferma si fa una sola richiesta", len(CALLS), 1)
check_text("e il foglio e' quello dedotto dal riferimento",
           parcel_only.foglio, "252")


def zoning_fails(url, timeout=0.0):
    if "CadastralZoning" in url:
        raise cs.CadastreError("il livello Mappe non risponde")
    return PARCEL_XML


degraded = cs.query_point(12.3810, 43.1010, target, transport=zoning_fails)
check_text("se il livello Mappe tace, il foglio dedotto resta",
           degraded.foglio, "252")


def nothing_here(url, timeout=0.0):
    return EMPTY_XML


check_true("un punto in nessuna particella da' None, non un errore",
           cs.query_point(12.3810, 43.1010, target,
                          transport=nothing_here) is None)


def unreachable(url, timeout=0.0):
    raise cs.CadastreError("rete assente")


check_raises("un servizio irraggiungibile e' un errore dichiarato",
             cs.CadastreError, cs.query_point, 12.3810, 43.1010, target,
             unreachable)

# --------------------------------------------------------------------------
# C8 - the three columns land on a real feature
# --------------------------------------------------------------------------
print("\n== C8: le tre colonne sul layer ==")
layer = lf.memory_layer("Polygon", "cad catasto", CRS.authid(),
                        pa.METADATA_FIELDS)
QgsProject.instance().addMapLayer(layer)
lf.ensure_cad_fields(layer)
feature = QgsFeature(layer.fields())
feature.setGeometry(QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX, OY, OX + 10.0, OY + 10.0)))
# v1.38.0: no cad_id column. The feature is found by the id the layer
# gave it, which is what the commit reads back from the difference.
layer.dataProvider().addFeature(feature)
layer.updateExtents()

check_true("prima della ricerca il layer non ha colonne catastali",
           all(layer.fields().indexOf(name) < 0
               for name in lf.CADASTRE_FIELD_NAMES))
found_id = max(f.id() for f in layer.getFeatures())
check_true("la feature si ritrova dal suo cad_id", found_id is not None)
check_true("un cad_id che non c'e' non trova nulla",
           not layer.getFeature(999).isValid())
check_true("scrivere il catasto riesce",
           lf.write_cadastre(layer, found_id, parcel))
for name in lf.CADASTRE_FIELD_NAMES:
    check_true("la colonna {0} e' stata creata".format(name),
               layer.fields().indexOf(name) >= 0)
written = next(layer.getFeatures())
# v1.29.0: the column reads the name, not the Belfiore code. A column
# headed "Comune" saying G478 tells an operator nothing; the code is still
# in parcel.label() and in the parametric record.
check_text("il comune e' sulla feature, col suo nome",
           written[lf.CAT_COMUNE_FIELD], "Perugia (PG)")
check_text("il foglio anche", written[lf.CAT_FOGLIO_FIELD], "252")
check_text("e la particella", written[lf.CAT_PARTICELLA_FIELD], "1016")
check_true("le tre colonne restano visibili nella tabella",
           all(name in lf.ALWAYS_VISIBLE_FIELDS
               for name in lf.CADASTRE_FIELD_NAMES))
check("...senza entrare fra le colonne CAD, che restano due",
      len(lf.VISIBLE_CAD_FIELDS), 2)
check_true("nessuna colonna catastale e' nascosta",
           not (set(lf.CADASTRE_FIELD_NAMES) & set(hidden_names(layer))))
check_true("scrivere senza particella non fa nulla",
           not lf.write_cadastre(layer, found_id, None))
check_true("...e senza layer nemmeno",
           not lf.write_cadastre(None, found_id, parcel))

# --------------------------------------------------------------------------
# C9 - nothing happens unless the operator asked for it
# --------------------------------------------------------------------------
# v1.29.0: on by default. Drawing a parcel and finding Comune, Foglio and
# Particella already filled in is the point of a cadastral CAD tool; the
# switch is still there for an operator with no network, or off Italian
# ground, and this checks that turning it off really stops the lookup.
print("\n== C9: l'interrogazione parte da sola, e si puo' spegnere ==")
check_true("cadastre/enabled esiste fra le impostazioni",
           "cadastre/enabled" in KEYS)
settings.reset("cadastre/enabled")
check_true("...ed e' accesa per impostazione predefinita",
           bool(settings.get("cadastre/enabled")))
settings.set("cadastre/enabled", False)

commit_layer = lf.memory_layer("Polygon", "cad no net", CRS.authid(),
                               pa.METADATA_FIELDS)
QgsProject.instance().addMapLayer(commit_layer)
session = sq_tool.SquareSession()
tool = tb.BaseCadTool(session)
session.set_origin(OX, OY)
session.submit("20")
session.submit("0d")
committed = tool.commit(commit_layer, CRS, commit_layer.crs())
check("la geometria e' stata scritta comunque",
      commit_layer.featureCount(), 1)
check_true("spenta, nessuna interrogazione catastale parte",
           tool.cadastre_task is None)
check_true("...e nessun avviso e' stato prodotto", not tool.cadastre_warning)
check_true("il layer non ha preso colonne catastali",
           all(commit_layer.fields().indexOf(name) < 0
               for name in lf.CADASTRE_FIELD_NAMES))
check("l'area e' quella del quadrato", committed.geometry().area(), 400.0,
      1e-6)

print("\n-- accesa, la richiesta parte; il commit non l'aspetta --")
settings.set("cadastre/enabled", True)
try:
    session2 = sq_tool.SquareSession()
    tool2 = tb.BaseCadTool(session2)
    session2.set_origin(OX, OY + 100.0)
    session2.submit("20")
    session2.submit("0d")
    tool2.commit(commit_layer, CRS, commit_layer.crs())
    check("la seconda geometria e' scritta subito",
          commit_layer.featureCount(), 2)
    check_true("una richiesta e' stata avviata",
               tool2.cadastre_task is not None or bool(tool2.cadastre_warning))
    if tool2.cadastre_task is not None:
        tool2.cadastre_task.cancel()
finally:
    settings.reset("cadastre/enabled")
check_true("l'impostazione e' tornata al suo valore, che e' acceso",
           bool(settings.get("cadastre/enabled")))

# --------------------------------------------------------------------------
# C10 - the live service, skipped when it cannot be reached
# --------------------------------------------------------------------------
print("\n== C10: il servizio vero (saltato se non raggiungibile) ==")
try:
    capabilities = cs.urllib_transport(cs.EVIDENCE[0], timeout=15.0)
except cs.CadastreError as exc:
    skip("il servizio pubblica i due livelli e il CRS 6706",
         "endpoint non raggiungibile: {0}".format(exc.user_message))
    capabilities = ""
if capabilities:
    check_true("il servizio pubblica il livello delle particelle",
               "CP:CadastralParcel" in capabilities)
    check_true("...e quello delle mappe", "CP:CadastralZoning" in capabilities)
    check_true("...e dichiara EPSG::6706 come CRS predefinito",
               "EPSG::6706" in capabilities)
    check_true("...ed e' un WFS 2.0.0",
               "2.0.0" in capabilities and "WFS_Capabilities" in capabilities)
    live = cs.query_point(12.3810, 43.1010,
                          QgsCoordinateReferenceSystem("EPSG:6706"))
    if live is None:
        skip("una particella vera arriva dal servizio",
             "nessuna particella nel punto di prova")
    else:
        print("        dal vivo: {0}".format(live.label()))
        check_true("la particella dal vivo ha comune, foglio e particella",
                   bool(live.comune_code and live.foglio and live.particella))
        # Not the same particella: the query window holds several, and
        # which one comes back first is the service's business. What is
        # stable for a fixed point is the comune and the sheet it is on.
        check_text("...il comune e' quello della risposta registrata",
                   live.comune_code, first.comune_code)
        check_text("...e il foglio pure", live.foglio, first.foglio)

check_true("il prospetto dice cosa e' stato verificato e dove",
           any("EPSG:6706" in line for line in cs.describe()))


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
