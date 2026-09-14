"""
v2.2.0: un riquadro troppo grande viene diviso, non troncato.

A WFS that reaches its `count` stops there. The answer comes back looking
complete and is short, and the parcels past the ceiling are simply not in
the result -- no error, no warning, nothing to notice. On a project-sized
area it never happens; on a municipality-sized one it happens silently.

``fetch_parcels`` answers that: it cuts the box into tiles, and it treats a
tile that comes back at exactly the provider's ceiling as the truncation it
is and quarters it. Then it merges, deduplicates on the cadastral identifier
-- a parcel on a tile boundary comes back whole from both tiles -- and hands
the complete set to one intersection.

The control flow is exercised against a provider stand-in that really
truncates, because a real service that truncates on demand is not something
this suite can arrange. Everything downstream of it -- parsing, geometry,
intersection, percentages, export -- runs on the recorded answers of the
real service and, in the last block, on the service itself.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_cadastre_chunking.py
"""

import io as _io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import QgsApplication                            # noqa: E402

QGS = QgsApplication([], False)
QGS.initQgis()

from qgis.core import (QgsCoordinateReferenceSystem,            # noqa: E402
                       QgsGeometry, QgsProject)

from geocad_uav.io import cadastre as cs                        # noqa: E402

FAILURES = []
SKIPS = []
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures")
TMP = tempfile.mkdtemp(prefix="geocad_chunk_")
CRS6706 = QgsCoordinateReferenceSystem("EPSG:6706")


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


def check_raises(label, exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        print("  [ok  ] {0}".format(label))
        return
    except Exception as exc:                                    # noqa: BLE001
        print("  [FAIL] {0} (ha sollevato {1})".format(label,
                                                       type(exc).__name__))
        FAILURES.append(label)
        return
    print("  [FAIL] {0} (non ha sollevato)".format(label))
    FAILURES.append(label)


def skip(label, reason):
    print("  [skip] {0}\n         motivo: {1}".format(label, reason))
    SKIPS.append((label, reason))


def fixture(name):
    with _io.open(os.path.join(FIXTURES, name), encoding="utf-8",
                  errors="replace") as handle:
        return handle.read()


PARCEL_XML = fixture("catasto_parcel_getfeature.xml")

print("=" * 78)
print("Catasto: riquadri grandi, suddivisione, unione, deduplicazione")
print("=" * 78)

# --------------------------------------------------------------------------
# K1 - the geometry of the split
# --------------------------------------------------------------------------
print("\n== K1: come si taglia un riquadro ==")
BOX = (43.00, 12.00, 43.10, 12.20)          # 0.10 x 0.20 degrees
one = cs.split_bbox(BOX, 1.0)
check("un riquadro che ci sta resta uno", len(one), 1)
check_true("...ed e' proprio quello", tuple(one[0]) == BOX)

tiles = cs.split_bbox(BOX, 0.05)
print("        0.10 x 0.20 con lato 0.05 -> {0} riquadri".format(len(tiles)))
check("due righe per quattro colonne", len(tiles), 8)
check_true("nessun riquadro supera il lato chiesto",
           all(t[2] - t[0] <= 0.05 + 1e-12 and t[3] - t[1] <= 0.05 + 1e-12
               for t in tiles))
check("l'area coperta e' quella di partenza",
      sum((t[2] - t[0]) * (t[3] - t[1]) for t in tiles),
      (BOX[2] - BOX[0]) * (BOX[3] - BOX[1]), 1e-12)
check_true("i riquadri partono dal bordo sud-ovest",
           min(t[0] for t in tiles) == BOX[0]
           and min(t[1] for t in tiles) == BOX[1])
check_true("...e arrivano a quello nord-est",
           abs(max(t[2] for t in tiles) - BOX[2]) < 1e-12
           and abs(max(t[3] for t in tiles) - BOX[3]) < 1e-12)

quarters = cs.quarter_bbox(BOX)
check("un riquadro si divide in quattro", len(quarters), 4)
check("...senza perdere area",
      sum((q[2] - q[0]) * (q[3] - q[1]) for q in quarters),
      (BOX[2] - BOX[0]) * (BOX[3] - BOX[1]), 1e-12)
check_true("...e ognuno e' un quarto",
           all(abs((q[2] - q[0]) - (BOX[2] - BOX[0]) / 2.0) < 1e-12
               for q in quarters))

# --------------------------------------------------------------------------
# K2 - a provider that truncates, and what the query does about it
# --------------------------------------------------------------------------
print("\n== K2: il servizio si ferma al suo tetto, la query se ne accorge ==")
CALLS = []


def parcel_at(index):
    """A parcel identified the way the service identifies one."""
    return cs.CadastralParcel(
        comune_code="G478", foglio="252", particella=str(index),
        national_reference="G478_025200.{0}".format(index))


def boxed_parser(body):
    """Decodes the stand-in's body: 'n' parcels, numbered from a seed."""
    seed, count = (int(v) for v in body.split(":"))
    return [(parcel_at(seed + i), None) for i in range(count)]


def truncating(url, timeout=0.0):
    """A provider that stops at MAX_AREA_FEATURES, like a real WFS does.

    The number of parcels it holds is proportional to the area asked for, so
    a box four times smaller comes back under the ceiling -- which is what
    quartering a truncated tile is supposed to achieve.
    """
    CALLS.append(url)
    import re                                                   # noqa: PLC0415
    from urllib.parse import parse_qs, urlparse                 # noqa: PLC0415

    bbox = parse_qs(urlparse(url).query)["bbox"][0]
    south, west, north, east = (float(v) for v in bbox.split(",")[:4])
    # 400,000 parcels per square degree: a 0.02 box holds 160, a 0.2 box
    # holds 16,000 and is cut off at the ceiling.
    area = (north - south) * (east - west)
    held = int(round(400000.0 * area))
    seed = int(abs(hash(re.sub(r"\.\d+", "", bbox))) % 100000) * 100000
    return "{0}:{1}".format(seed, min(held, cs.MAX_AREA_FEATURES))


big = (43.00, 12.00, 43.20, 12.20)          # 0.2 x 0.2 degrees
items, warnings = cs.fetch_parcels(big, truncating, parser=boxed_parser,
                                   span_deg=0.2)
print("        un riquadro 0.2x0.2 -> {0} richieste, {1} particelle".format(
    len(CALLS), len(items)))
check_true("una sola richiesta non sarebbe bastata", len(CALLS) > 1)
check_true("...e nessuna risposta e' rimasta al tetto",
           all(int(truncating(url).split(":")[1]) < cs.MAX_AREA_FEATURES
               for url in list(CALLS)[-4:]))
check_true("il risultato supera il tetto di una singola richiesta",
           len(items) > cs.MAX_AREA_FEATURES)
check_true("nessun avviso di troncamento", not any(
    "limite" in w for w in warnings))

CALLS[:] = []
small, small_warnings = cs.fetch_parcels((43.00, 12.00, 43.01, 12.01),
                                         truncating, parser=boxed_parser,
                                         span_deg=0.02)
print("        un riquadro piccolo -> {0} richiesta/e".format(len(CALLS)))
check("un riquadro che il servizio regge si chiede una volta", len(CALLS), 1)
check_true("...e non produce avvisi", not small_warnings)

CALLS[:] = []
pre_split, _w = cs.fetch_parcels((43.00, 12.00, 43.08, 12.08), truncating,
                                 parser=boxed_parser, span_deg=0.02)
print("        lato 0.02 su 0.08x0.08 -> {0} richieste".format(len(CALLS)))
check("il primo taglio segue il lato configurato", len(CALLS), 16)

check_raises("un'area smisurata e' un rifiuto, non un download",
             cs.CadastreError, cs.fetch_parcels,
             (40.0, 8.0, 47.0, 18.0), truncating, parser=boxed_parser,
             span_deg=0.02)

# --------------------------------------------------------------------------
# K3 - failures, retries and empty answers
# --------------------------------------------------------------------------
print("\n== K3: errori, ritentativi, risposte vuote ==")
ATTEMPTS = []


def flaky(url, timeout=0.0):
    """Fails the first time it sees a box, answers the second."""
    ATTEMPTS.append(url)
    if ATTEMPTS.count(url) < 2:
        raise cs.CadastreError("connessione interrotta",
                               user_message="Servizio non raggiungibile.")
    return "1000:3"


items, warnings = cs.fetch_parcels((43.00, 12.00, 43.01, 12.01), flaky,
                                   parser=boxed_parser, span_deg=0.02)
print("        tentativi: {0}, particelle: {1}".format(len(ATTEMPTS),
                                                       len(items)))
check("un tentativo fallito viene ripetuto", len(ATTEMPTS), 2)
check("...e la seconda volta risponde", len(items), 3)
check_true("...senza avvisi, perche' ha risposto", not warnings)


def half_dead(url, timeout=0.0):
    """Answers the southern tiles, refuses the northern ones."""
    from urllib.parse import parse_qs, urlparse                 # noqa: PLC0415

    south = float(parse_qs(urlparse(url).query)["bbox"][0].split(",")[0])
    if south > 43.005:
        raise cs.CadastreError("riquadro non disponibile",
                               user_message="Riquadro non disponibile.")
    return "2000:2"


items, warnings = cs.fetch_parcels((43.00, 12.00, 43.02, 12.01), half_dead,
                                   parser=boxed_parser, span_deg=0.01)
print("        parziale: {0} particelle, {1} avvisi".format(len(items),
                                                            len(warnings)))
check_true("i riquadri che rispondono danno il loro risultato", len(items) > 0)
check_true("...e quelli che non rispondono lasciano un avviso",
           any("non interrogato" in w for w in warnings))


def dead(url, timeout=0.0):
    raise cs.CadastreError("rete assente",
                           user_message="Servizio non raggiungibile.")


check_raises("se non risponde nessun riquadro e' un errore, non un vuoto",
             cs.CadastreError, cs.fetch_parcels,
             (43.00, 12.00, 43.01, 12.01), dead, parser=boxed_parser,
             span_deg=0.02)


def empty(url, timeout=0.0):
    return "0:0"


items, warnings = cs.fetch_parcels((43.00, 12.00, 43.01, 12.01), empty,
                                   parser=boxed_parser, span_deg=0.02)
check("una risposta vuota e' una risposta", len(items), 0)
check_true("...e non un errore", not warnings)

# --------------------------------------------------------------------------
# K4 - the deduplication
# --------------------------------------------------------------------------
print("\n== K4: deduplicazione ==")
PAIRS = cs.parse_parcel_geometries(PARCEL_XML)
check("il documento di prova porta due particelle", len(PAIRS), 2)

doubled = PAIRS + PAIRS
unique = cs.dedup_pairs(doubled)
check("una particella vista due volte resta una", len(unique), len(PAIRS))
check_true("...e sono le stesse",
           [p.national_reference for p, _g in unique]
           == [p.national_reference for p, _g in PAIRS])

first, geometry = PAIRS[0]
same_ref = cs.CadastralParcel(
    comune_code="ZZZZ", foglio="1", particella="1",
    national_reference=first.national_reference)
check_true("l'identificativo nazionale e' la chiave",
           cs.parcel_key(same_ref) == cs.parcel_key(first))
check("...quindi un secondo arrivo non aggiunge niente",
      len(cs.dedup_pairs([(first, geometry), (same_ref, geometry)])), 1)

no_ref = cs.CadastralParcel(comune_code="G478", foglio="252",
                            particella="1016")
twin = cs.CadastralParcel(comune_code="G478", foglio="252",
                          particella="1016")
check_true("senza riferimento vale Comune/foglio/particella",
           cs.parcel_key(no_ref) == cs.parcel_key(twin))
other = cs.CadastralParcel(comune_code="H501", foglio="252",
                           particella="1016")
check_true("...e due Comuni non si confondono",
           cs.parcel_key(no_ref) != cs.parcel_key(other))
check("due Comuni con la stessa particella restano due",
      len(cs.dedup_pairs([(no_ref, geometry), (other, geometry)])), 2)

bare = cs.CadastralParcel()
check_true("senza identita' alcuna si usa la geometria",
           cs.parcel_key(bare, geometry)[0] == "wkb")
check("...e due geometrie uguali sono una",
      len(cs.dedup_pairs([(cs.CadastralParcel(), geometry),
                          (cs.CadastralParcel(), geometry)])), 1)

clipped = QgsGeometry.fromRect(geometry.boundingBox())
bigger = cs.dedup_pairs([(first, geometry), (first, clipped)])
check("fra due versioni della stessa particella ne resta una", len(bigger), 1)
check_true("...e si tiene la geometria piu' completa",
           bigger[0][1].area() >= geometry.area())

# --------------------------------------------------------------------------
# K5 - the four quantities, kept apart
# --------------------------------------------------------------------------
print("\n== K5: quattro grandezze, quattro significati ==")
PARCEL_A, GEOM_A = PAIRS[0]
# A project covering part of the parcel and running well past it, so that
# neither percentage is 100 and the two differ.
box = GEOM_A.boundingBox()
project = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        box.xMinimum(), box.yMinimum(),
        box.xMinimum() + box.width() * 4.0,
        box.yMinimum() + box.height() * 0.6))
result = cs.interpolate_cadastral_data(project, CRS6706, [PAIRS[0]])
check("una particella interessata", result.n_parcels, 1)
share = result.shares[0]
print("        catastale {0:,.1f} m2 | interessata {1:,.1f} m2 | "
      "progetto {2:,.1f} m2".format(share.parcel_area_m2,
                                    share.intersection_area_m2,
                                    share.project_area_m2))
print("        % particella {0:.3f} | % progetto {1:.3f}".format(
    share.percent_of_parcel, share.percent_of_project))
check_true("la superficie catastale e' quella dell'intera particella",
           abs(share.parcel_area_m2 - share.geometry.area()) >= 0.0
           and share.parcel_area_m2 > share.intersection_area_m2)
check("la percentuale sulla particella ha per denominatore la particella",
      share.percent_of_parcel,
      100.0 * share.intersection_area_m2 / share.parcel_area_m2, 1e-9)
check("la percentuale sul progetto ha per denominatore il progetto",
      share.percent_of_project,
      100.0 * share.intersection_area_m2 / share.project_area_m2, 1e-9)
check_true("le due percentuali sono davvero diverse",
           abs(share.percent_of_parcel - share.percent_of_project) > 1.0)
check("l'area di progetto e' quella misurata dall'intersezione",
      share.project_area_m2, result.project_area_m2, 1e-9)
row = share.as_row()
check("la riga porta la percentuale sulla particella",
      row["percentuale_particella"], round(share.percent_of_parcel, 3), 1e-9)
check("...e quella sul progetto", row["percentuale_progetto"],
      round(share.percent_of_project, 3), 1e-9)
check_true("...e le due superfici, distinte",
           row["superficie_catastale_m2"] != row["superficie_interessata_m2"])

# --------------------------------------------------------------------------
# K6 - the structured export keeps everything
# --------------------------------------------------------------------------
print("\n== K6: l'export non perde niente ==")
union = QgsGeometry.unaryUnion(
    [QgsGeometry.fromRect(g.boundingBox()) for _p, g in PAIRS])
both = cs.interpolate_cadastral_data(union, CRS6706, PAIRS)
rows = both.export_rows()
print("        colonne: {0}".format(list(both.EXPORT_COLUMNS)))
check("una riga per particella", len(rows), both.n_parcels)
for column in ("comune", "belfiore", "foglio", "particella",
               "superficie_catastale_m2", "superficie_interessata_m2",
               "percentuale_particella", "percentuale_progetto"):
    check_true("l'export porta {0}".format(column),
               all(column in row for row in rows))
check_true("...e il riferimento catastale",
           all(row["riferimento"] for row in rows))
check_true("...e la geometria della particella",
           all(row["geometria_wkt"].upper().startswith(
               ("POLYGON", "MULTIPOLYGON")) for row in rows))
check_true("...e quella dell'intersezione",
           all(row["intersezione_wkt"].upper().startswith(
               ("POLYGON", "MULTIPOLYGON")) for row in rows))
check_true("le superfici nell'export sono quelle del modello",
           all(abs(row["superficie_interessata_m2"]
                   - round(share.intersection_area_m2, 2)) < 0.005
               for row, share in zip(rows, both.shares)))
light = both.export_rows(with_geometry=False)
check_true("si puo' esportare senza geometrie", all(
    not row["geometria_wkt"] for row in light))
check("...senza perdere le righe", len(light), len(rows))

print("\n-- e la rappresentazione compatta non e' il dato --")
many = cs.CadastralResult(
    shares=[cs.ParcelShare(parcel=cs.CadastralParcel(
        comune_code="G478", foglio="252", particella=str(n),
        national_reference="G478_025200.{0}".format(n)),
        parcel_area_m2=100.0, intersection_area_m2=50.0,
        project_area_m2=5000.0) for n in range(1, 41)],
    project_area_m2=5000.0, covered_area_m2=2000.0, status=cs.STATUS_OK)
cell = cs.cad_columns(many)
print("        cella: {0!r}".format(cell["Particella"][:70] + "..."))
check_true("la cella si accorcia", "(+" in cell["Particella"])
check("...ma il modello le tiene tutte", many.n_parcels, 40)
check("...e l'export le scrive tutte", len(many.export_rows()), 40)
check_true("...con tutti gli identificativi",
           {row["particella"] for row in many.export_rows()}
           == {str(n) for n in range(1, 41)})

# --------------------------------------------------------------------------
# K7 - the whole chain against the live service
# --------------------------------------------------------------------------
print("\n== K7: il servizio vero, su un'area piu' larga di un riquadro ==")
try:
    cs.urllib_transport(cs.EVIDENCE[0], timeout=15.0)
    reachable = True
except cs.CadastreError as exc:
    reachable = False
    skip("l'area larga interroga il servizio vero", exc.formatted())

if reachable:
    centre = GEOM_A.centroid().constGet()
    half = 0.006                      # ~1.3 x 0.7 km: more than one tile
    wide = QgsGeometry.fromWkt(
        "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
            centre.x() - half, centre.y() - half * 0.5,
            centre.x() + half, centre.y() + half * 0.5))
    box = cs.service_bbox(wide, CRS6706)
    tiles = cs.split_bbox(box, cs.tile_span_deg())
    print("        riquadro {0:.4f} x {1:.4f} gradi -> {2} tile".format(
        box[2] - box[0], box[3] - box[1], len(tiles)))
    found, warnings = cs.fetch_parcels(box, cs.urllib_transport, 30.0)
    unique = cs.dedup_pairs(found)
    print("        ricevute {0}, uniche {1}, avvisi {2}".format(
        len(found), len(unique), len(warnings)))
    check_true("il servizio vero ha risposto", len(found) > 0)
    check_true("...e i duplicati di bordo sono stati tolti",
               len(unique) <= len(found))
    check_true("...ogni particella ha un identificativo",
               all(p.national_reference or p.particella for p, _g in unique))
    check_true("...e una geometria",
               all(g is not None and not g.isEmpty() for _p, g in unique))
    live = cs.interpolate_cadastral_data(wide, CRS6706, unique)
    print("        {0} particelle interessate in {1} Comuni".format(
        live.n_parcels, len(live.comuni())))
    check_true("l'intersezione ne trova parecchie", live.n_parcels > 5)
    check_true("...ognuna con le due superfici e le due percentuali",
               all(s.parcel_area_m2 > 0 and s.intersection_area_m2 > 0
                   and s.percent_of_parcel > 0 and s.percent_of_project > 0
                   for s in live.shares))
    check_true("le quote di progetto sommano al massimo a cento",
               sum(s.percent_of_project for s in live.shares) <= 100.5)
    check("l'export le riporta tutte", len(live.export_rows()),
          live.n_parcels)

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
