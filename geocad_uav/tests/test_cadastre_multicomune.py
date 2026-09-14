"""
v2.0.1: un progetto a cavallo di piu' Comuni li tiene tutti.

The intersection has always been spatial -- ``test_cadastre_spatial.py``
proves that on the recorded WFS answers. What this suite pins down is what
happens *after* it: that nothing is collapsed on the way to an operator.
Not the comune, not the foglio, not the particella, not the two surfaces --
and that the summary, the detail table, the map and the exported report are
the same reading of the same shares.

The ten cases are the ones a cadastral annex is judged on: one comune, two,
three; several fogli in one comune, several particelle in one foglio; the
same foglio and particella numbers in two different comuni -- the case where
a dictionary keyed on the sheet number silently loses half the project; a
multipart area; the arithmetic; the map; and the whole way through from the
service's own XML to the written document.

Parcels for the combinatorial cases are built as tiles in the service CRS
(EPSG:6706), because what is under test here is the model and not the
parser. Case J uses the recorded WFS document instead, so the round trip
starts where it really starts. No expected surface is written down by hand:
every one is recomputed from the shares.

NEEDS QGIS with widgets. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_cadastre_multicomune.py
"""

import io as _io
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsGeometry, QgsProject)

QGS = QgsApplication([], True)
QGS.initQgis()

from qgis.PyQt.QtCore import Qt                                 # noqa: E402

from geocad_uav.gui import map_layers as map_mod                # noqa: E402
from geocad_uav.gui import workflow as wf                       # noqa: E402
from geocad_uav.io import cadastre as cs                        # noqa: E402
from geocad_uav.io import documents as docs                     # noqa: E402

FAILURES = []
SKIPS = []
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures")
TMP = tempfile.mkdtemp(prefix="geocad_multi_")
CRS6706 = QgsCoordinateReferenceSystem("EPSG:6706")

#: A grid of tiles near Perugia, in the CRS the service publishes. One step
#: is about 165 m east-west and 220 m north-south: parcel sized, and far
#: enough from the equator that anything measured in degrees would show.
LON0, LAT0, STEP = 12.380, 43.100, 0.002

#: Three real Belfiore codes, so the names come from the shipped table and
#: not from this file.
PERUGIA, ROMA, FIRENZE = "G478", "H501", "D612"


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


def fixture(name):
    with _io.open(os.path.join(FIXTURES, name), encoding="utf-8",
                  errors="replace") as handle:
        return handle.read()


def tile(i0, j0, i1, j1):
    """A rectangle on the test grid, in EPSG:6706, x = longitudine."""
    x0, x1 = LON0 + i0 * STEP, LON0 + i1 * STEP
    y0, y1 = LAT0 + j0 * STEP, LAT0 + j1 * STEP
    return QgsGeometry.fromWkt(
        "POLYGON(({0:.8f} {1:.8f},{2:.8f} {1:.8f},{2:.8f} {3:.8f},"
        "{0:.8f} {3:.8f},{0:.8f} {1:.8f}))".format(x0, y0, x1, y1))


def parcel(code, foglio, particella):
    """A parcel identified the way the service identifies one."""
    return cs.CadastralParcel(
        comune_code=code, foglio=foglio, particella=particella,
        national_reference="{0}_{1:06d}.{2}".format(code, int(foglio) * 100,
                                                    particella))


def interpolate(project, parcels):
    """The frozen intersection, from the service CRS as it really runs."""
    return cs.interpolate_cadastral_data(project, CRS6706, parcels)


def keys_of(result):
    return {(s.parcel.comune_code, s.parcel.foglio, s.parcel.particella)
            for s in result.shares}


def tables_of(report):
    """``{titolo: righe}`` for the tables a report carries."""
    return {block.text: block.rows for block in report.blocks
            if block.kind == docs.BLOCK_TABLE}


print("=" * 78)
print("Catasto multicomune -- niente si perde fra intersezione ed elaborato")
print("=" * 78)

# --------------------------------------------------------------------------
# A - one comune
# --------------------------------------------------------------------------
print("\n== Caso A: area contenuta in un solo Comune ==")
result_a = interpolate(tile(1, 1, 2, 2),
                       [(parcel(PERUGIA, "12", "45"), tile(0, 0, 3, 3))])
check("un Comune", len(result_a.comuni()), 1)
check("una particella", result_a.n_parcels, 1)
check_text("...e il codice e' risolto in un nome",
           result_a.shares[0].comune.name.upper(), "PERUGIA")
check_text("il catasto copre tutto il progetto", result_a.status,
           cs.STATUS_OK)
check("la copertura e' totale", result_a.covered_fraction, 1.0, 1e-6)
share_a = result_a.shares[0]
print("        particella {0:,.1f} m2, interessata {1:,.1f} m2, {2:.2f} %"
      .format(share_a.parcel_area_m2, share_a.intersection_area_m2,
              share_a.percent_of_parcel))
check_true("le superfici sono metriche, non gradi quadrati",
           1.0 < share_a.intersection_area_m2 < share_a.parcel_area_m2 < 1e7)
check("la percentuale e' intersezione su particella",
      share_a.percent_of_parcel,
      100.0 * share_a.intersection_area_m2 / share_a.parcel_area_m2, 1e-9)
rows_a = result_a.comune_rows()
check("il riepilogo ha una riga", len(rows_a), 1)
check("...e quel Comune ha tutto il progetto", rows_a[0]["quota_progetto"],
      100.0, 1e-3)
check_text("...col suo codice Belfiore", rows_a[0]["belfiore"], PERUGIA)

# --------------------------------------------------------------------------
# B - two comuni
# --------------------------------------------------------------------------
print("\n== Caso B: l'area attraversa due Comuni ==")
# Four parcels tiling the project, two per comune. The boundary between the
# two comuni falls inside the project, which is the whole point.
PARCELS_B = [
    (parcel(PERUGIA, "12", "45"), tile(0, -1, 1, 3)),
    (parcel(PERUGIA, "12", "46"), tile(1, -1, 2, 3)),
    (parcel(ROMA, "7", "103"), tile(2, -1, 3, 3)),
    (parcel(ROMA, "7", "104"), tile(3, -1, 4, 3)),
]
PROJECT_B = tile(0, 0, 4, 2)
result = interpolate(PROJECT_B, PARCELS_B)
for row in result.comune_rows():
    print("        {0:<30} {1} particelle su {2} fogli, {3:>10,.0f} m2 "
          "({4:.1f} % del progetto)".format(
              row["comune"], row["particelle"], row["fogli"],
              row["superficie_interessata_m2"], row["quota_progetto"]))
check("due Comuni", len(result.comuni()), 2)
check("quattro particelle in tutto", result.n_parcels, 4)
check_true("nessun Comune e' stato perso",
           set(result.belfiore_codes()) == {PERUGIA, ROMA})
check("il primo Comune tiene le sue due particelle",
      len(result.shares_of(PERUGIA)), 2)
check("...e il secondo le sue", len(result.shares_of(ROMA)), 2)
check_true("le particelle del primo sono 45 e 46",
           {s.parcel.particella for s in result.shares_of(PERUGIA)}
           == {"45", "46"})
check_true("...e quelle del secondo 103 e 104",
           {s.parcel.particella for s in result.shares_of(ROMA)}
           == {"103", "104"})
check_true("i due nomi sono risolti dalla tabella dei Comuni",
           {c.name.upper() for _code, c, _g in result.comuni()}
           == {"PERUGIA", "ROMA"})
check("le particelle tappezzano il progetto", result.covered_fraction, 1.0,
      1e-6)
check_text("...quindi il risultato e' completo", result.status, cs.STATUS_OK)
check("il riepilogo ha due righe", len(result.comune_rows()), 2)
check("il dettaglio ne ha quattro", len(result.rows()), 4)

# --------------------------------------------------------------------------
# C - three comuni
# --------------------------------------------------------------------------
print("\n== Caso C: tre o piu' Comuni ==")
result_c = interpolate(tile(0, 0, 5, 2),
                       PARCELS_B + [(parcel(FIRENZE, "3", "9"),
                                     tile(4, -1, 5, 3))])
print("        {0}".format([row["comune"]
                            for row in result_c.comune_rows()]))
check("tre Comuni", len(result_c.comuni()), 3)
check_true("tutti e tre presenti",
           set(result_c.belfiore_codes()) == {PERUGIA, ROMA, FIRENZE})
check("cinque particelle", result_c.n_parcels, 5)
check_true("ognuno con le sue particelle",
           all(result_c.shares_of(code)
               for code in (PERUGIA, ROMA, FIRENZE)))
check("le quote dei tre Comuni fanno il cento per cento",
      sum(row["quota_progetto"] for row in result_c.comune_rows()), 100.0,
      0.02)
check_true("il riepilogo e' ordinato per superficie interessata",
           [row["superficie_interessata_m2"]
            for row in result_c.comune_rows()]
           == sorted([row["superficie_interessata_m2"]
                      for row in result_c.comune_rows()], reverse=True))
check("le etichette dei Comuni sono tre", len(result_c.comune_labels()), 3)

# --------------------------------------------------------------------------
# D - several fogli inside one comune
# --------------------------------------------------------------------------
print("\n== Caso D: piu' fogli nello stesso Comune ==")
result_d = interpolate(tile(0, 0, 3, 2), [
    (parcel(PERUGIA, "12", "45"), tile(0, -1, 1, 3)),
    (parcel(PERUGIA, "13", "7"), tile(1, -1, 2, 3)),
    (parcel(PERUGIA, "14", "1"), tile(2, -1, 3, 3)),
])
sheets = result_d.by_foglio()[PERUGIA]
print("        fogli: {0}".format(sorted(sheets)))
check("un solo Comune", len(result_d.comuni()), 1)
check("tre fogli", len(sheets), 3)
check_true("i fogli sono 12, 13 e 14", set(sheets) == {"12", "13", "14"})
check("il riepilogo del Comune li conta",
      result_d.comune_rows()[0]["fogli"], 3)
check("...e conta le particelle", result_d.comune_rows()[0]["particelle"], 3)
check("ogni foglio tiene la sua particella",
      sum(len(group) for group in sheets.values()), 3)
check_true("fogli() li ordina per superficie interessata",
           [f for f, _g in result_d.fogli(PERUGIA)]
           == sorted(sheets,
                     key=lambda f: -sum(s.intersection_area_m2
                                        for s in sheets[f])))

# --------------------------------------------------------------------------
# E - several particelle inside one foglio
# --------------------------------------------------------------------------
print("\n== Caso E: piu' particelle nello stesso foglio ==")
result_e = interpolate(tile(0, 0, 5, 2), [
    (parcel(PERUGIA, "12", str(n)), tile(n - 1, -1, n, 3))
    for n in range(1, 6)])
in_sheet = result_e.by_foglio()[PERUGIA]["12"]
check("cinque particelle", len(in_sheet), 5)
check_true("...tutte del foglio 12",
           all(s.parcel.foglio == "12" for s in in_sheet))
check_true("...e tutte distinte",
           {s.parcel.particella for s in in_sheet}
           == {"1", "2", "3", "4", "5"})
check("il riepilogo dice un foglio", result_e.comune_rows()[0]["fogli"], 1)
check("...e cinque particelle", result_e.comune_rows()[0]["particelle"], 5)
check("il dettaglio ha cinque righe", len(result_e.rows()), 5)

# --------------------------------------------------------------------------
# F - the same foglio and particella numbers in two comuni
# --------------------------------------------------------------------------
print("\n== Caso F: fogli e particelle omonimi in Comuni diversi ==")
# Deliberately different sizes: were one overwriting the other, the two
# surfaces would come out equal and a count alone would not notice.
result_f = interpolate(tile(0, 0, 3, 2), [
    (parcel(PERUGIA, "12", "45"), tile(0, -1, 1, 3)),
    (parcel(ROMA, "12", "45"), tile(1, -1, 3, 3)),
])
check("due Comuni", len(result_f.comuni()), 2)
check("due particelle, non una", result_f.n_parcels, 2)
grouped = result_f.by_foglio()
check_true("il foglio 12 esiste in entrambi, tenuti separati",
           set(grouped) == {PERUGIA, ROMA}
           and list(grouped[PERUGIA]) == ["12"]
           and list(grouped[ROMA]) == ["12"])
check_true("...e ciascuno tiene la propria particella 45",
           grouped[PERUGIA]["12"][0].parcel.comune_code == PERUGIA
           and grouped[ROMA]["12"][0].parcel.comune_code == ROMA)
areas_f = {code: round(result_f.shares_of(code)[0].intersection_area_m2, 1)
           for code in (PERUGIA, ROMA)}
print("        superfici interessate: {0}".format(areas_f))
check_true("le due superfici sono diverse: nessuna ha sovrascritto l'altra",
           areas_f[PERUGIA] != areas_f[ROMA])
check("il riepilogo ha due righe, una per Comune",
      len(result_f.comune_rows()), 2)
check_true("le righe di dettaglio portano il Comune, non solo il foglio",
           all(row["belfiore"] for row in result_f.rows()))
check("i codici Belfiore nel dettaglio sono due",
      len({row["belfiore"] for row in result_f.rows()}), 2)
report_f = "\n".join(result_f.describe())
check_true("anche la relazione li distingue",
           "PERUGIA" in report_f.upper() and "ROMA" in report_f.upper())

# --------------------------------------------------------------------------
# G - multipart, on both sides of the intersection
# --------------------------------------------------------------------------
print("\n== Caso G: geometrie multipart ==")
# Both parts strictly inside a parcel, and not on any boundary: a part
# whose edge lies on the next parcel's edge intersects it in a line, and a
# line of zero area is not a parcel the project touches.
multi = QgsGeometry.unaryUnion([tile(0.1, 0.1, 0.9, 1.9),
                                tile(3.1, 0.1, 3.9, 1.9)])
check_true("l'area di progetto di prova e' davvero multipart",
           multi is not None and not multi.isEmpty() and multi.isMultipart())
result_g = interpolate(multi, PARCELS_B)
print("        Comuni toccati: {0}".format(sorted(
    result_g.belfiore_codes())))
check_true("le due parti stanno in due Comuni diversi",
           set(result_g.belfiore_codes()) == {PERUGIA, ROMA})
check("due particelle toccate, una per parte", result_g.n_parcels, 2)
check_true("...e sono quelle sotto le due parti",
           keys_of(result_g) == {(PERUGIA, "12", "45"), (ROMA, "7", "104")})
check_true("nessuna parte e' stata scartata",
           all(share.intersection_area_m2 > 0.0
               for share in result_g.shares))
check("la superficie interessata e' quella delle due parti insieme",
      result_g.covered_area_m2, result_g.project_area_m2, 1.0)
check("...cioe' la somma delle due intersezioni",
      result_g.covered_area_m2,
      sum(s.intersection_area_m2 for s in result_g.shares), 1.0)
check_true("le geometrie per la mappa ci sono entrambe",
           all(share.intersection is not None
               and not share.intersection.isEmpty()
               for share in result_g.shares))

print("\n-- una particella in due pezzi --")
split = QgsGeometry.unaryUnion([tile(0, -1, 1, 3), tile(3, -1, 4, 3)])
result_gp = interpolate(PROJECT_B, [(parcel(PERUGIA, "12", "45"), split)])
piece_a = interpolate(PROJECT_B, [(parcel(PERUGIA, "12", "45"),
                                   tile(0, -1, 1, 3))]).shares[0]
piece_b = interpolate(PROJECT_B, [(parcel(PERUGIA, "12", "45"),
                                   tile(3, -1, 4, 3))]).shares[0]
check("una particella in due pezzi resta una particella",
      result_gp.n_parcels, 1)
check("...e la sua superficie catastale e' quella dei due pezzi",
      result_gp.shares[0].parcel_area_m2,
      piece_a.parcel_area_m2 + piece_b.parcel_area_m2, 1.0)
check("...e la superficie interessata pure",
      result_gp.shares[0].intersection_area_m2,
      piece_a.intersection_area_m2 + piece_b.intersection_area_m2, 1.0)
check_true("...e l'intersezione resta in due pezzi",
           result_gp.shares[0].intersection.isMultipart())

# --------------------------------------------------------------------------
# H - surfaces and percentages
# --------------------------------------------------------------------------
print("\n== Caso H: superfici e percentuali ==")
for share in result.shares:
    label = "{0}/{1}".format(share.parcel.comune_code,
                             share.parcel.particella)
    check("{0}: percentuale = interessata su catastale".format(label),
          share.percent_of_parcel,
          100.0 * share.intersection_area_m2 / share.parcel_area_m2, 1e-9)
    check_true("{0}: l'intersezione non supera la particella".format(label),
               share.intersection_area_m2 <= share.parcel_area_m2 + 1e-6)
    row = share.as_row()
    check("{0}: la riga porta la superficie catastale".format(label),
          row["superficie_catastale_m2"], share.parcel_area_m2, 0.005)
    check("{0}: ...e quella interessata".format(label),
          row["superficie_interessata_m2"], share.intersection_area_m2,
          0.005)

summary = {row["belfiore"]: row for row in result.comune_rows()}
for code in (PERUGIA, ROMA):
    group = result.shares_of(code)
    check("{0}: il riepilogo somma le superfici interessate".format(code),
          summary[code]["superficie_interessata_m2"],
          sum(s.intersection_area_m2 for s in group), 0.02)
    check("{0}: ...e quelle catastali".format(code),
          summary[code]["superficie_catastale_m2"],
          sum(s.parcel_area_m2 for s in group), 0.02)
    check("{0}: la percentuale del Comune e' sul suo catastale".format(code),
          summary[code]["percentuale"],
          100.0 * sum(s.intersection_area_m2 for s in group)
          / sum(s.parcel_area_m2 for s in group), 0.01)
check("le quote dei due Comuni fanno il cento per cento del progetto",
      sum(row["quota_progetto"] for row in result.comune_rows()), 100.0, 0.02)
check("il totale interessato e' la somma delle particelle",
      result.covered_area_m2,
      sum(s.intersection_area_m2 for s in result.shares), 1.0)
check("la superficie catastale totale e' la somma delle particelle",
      result.cadastral_area_m2,
      sum(s.parcel_area_m2 for s in result.shares), 1e-6)
attributes = result.as_attributes()
print("        riepilogo: {0} particelle, {1} Comuni, {2:.4f} ha".format(
    attributes["particelle"], attributes["comuni"],
    attributes["superficie_interessata_ha"]))
check("gli attributi contano due Comuni", attributes["comuni"], 2)
check("...e quattro particelle", attributes["particelle"], 4)
check("gli ettari interessati sono i metri quadri interessati",
      attributes["superficie_interessata_ha"],
      result.covered_area_m2 / 10000.0, 1e-4)
check_true("l'elenco dei Comuni li nomina tutti, non solo il primo",
           attributes["comuni_elenco"].count(";") == 1)
check_true("...e cosi' i codici Belfiore",
           set(attributes["belfiore_elenco"].split("; "))
           == {PERUGIA, ROMA})

# --------------------------------------------------------------------------
# I - the map draws every comune, and one comune can be picked out
# --------------------------------------------------------------------------
print("\n== Caso I: visualizzazione completa sulla mappa ==")
layers = map_mod.ProjectLayers(None, CRS6706.authid())
drawn = layers.draw_parcels(result)
check("una feature per particella, di tutti i Comuni", drawn,
      result.n_parcels)
layer = layers.layer("parcels")
features = list(layer.getFeatures())
check("il layer le tiene tutte", len(features), 4)
on_map = {(f["belfiore"], f["foglio"], f["particella"]) for f in features}
print("        sulla mappa: {0}".format(sorted(on_map)))
check_true("...ed esattamente quelle del modello", on_map == keys_of(result))
check_true("ogni feature porta le due superfici e la percentuale",
           all(f["superficie_cat_ha"] > 0 and f["superficie_int_ha"] > 0
               and f["percentuale"] > 0 for f in features))
check_true("nessuna feature ha geometria vuota",
           all(not f.geometry().isEmpty() for f in features))
check_true("ogni feature porta il nome del Comune, non solo il codice",
           all(f["comune"] for f in features))

rows_roma = [index for index, share in enumerate(result.shares)
             if share.parcel.comune_code == ROMA]
selected = layers.select_rows("parcels", rows_roma)
check("scegliere un Comune ne evidenzia le particelle", selected, 2)
check("...e solo quelle", layer.selectedFeatureCount(), 2)
check_true("...quelle giuste",
           {f["belfiore"] for f in layer.selectedFeatures()} == {ROMA})
check("un indice fuori elenco non seleziona nulla",
      layers.select_rows("parcels", [99]), 0)
layers.remove_all()

# --------------------------------------------------------------------------
# J - the whole way: WFS -> model -> GUI -> map -> export
# --------------------------------------------------------------------------
print("\n== Caso J: WFS -> modello -> GUI -> mappa -> export ==")
PARCEL_XML = fixture("catasto_parcel_getfeature.xml")
# The recorded answer with its second parcel moved to another comune: the
# geometries stay the service's own, only the administrative unit changes.
head, _, tail = PARCEL_XML.partition("</CP:CadastralParcel>")
two_comuni_xml = (head + "</CP:CadastralParcel>"
                  + tail.replace("G478_025200.1189", "H501_012300.77")
                        .replace(">G478<", ">H501<"))
pairs = cs.parse_parcel_geometries(two_comuni_xml)
check("il documento del servizio da' due particelle", len(pairs), 2)
check_true("...di due Comuni",
           {p.comune_code for p, _g in pairs} == {PERUGIA, ROMA})
project_j = QgsGeometry.unaryUnion(
    [QgsGeometry.fromRect(geometry.boundingBox()) for _p, geometry in pairs])
result_j = cs.interpolate_cadastral_data(project_j, CRS6706, pairs)
check("l'intersezione le trova entrambe", result_j.n_parcels, 2)
check("...in due Comuni", len(result_j.comuni()), 2)

data = cs.payload(result_j)
check("il payload porta una riga per particella", len(data["righe"]), 2)
check("...e una per Comune", len(data["comuni_righe"]), 2)
check_true("l'elenco dei Comuni e' completo",
           data["comuni_elenco"].count(";") == 1)
check_true("...e i codici Belfiore pure",
           PERUGIA in data["belfiore_elenco"]
           and ROMA in data["belfiore_elenco"])
check_true("l'oggetto completo viaggia col payload",
           data["result"] is result_j)

workspace = wf.Workspace(None)
state = workspace.state
panel = workspace.context.area_panel
state.set_area(project_j, CRS6706, "Area a cavallo di due Comuni")
state.cadastre = result_j
panel.on_cadastral_data(data)

check("la tabella di dettaglio ha una riga per particella",
      panel.parcel_table.rowCount(), 2)
check("...e sei colonne", panel.parcel_table.columnCount(), 6)
check("la tabella di riepilogo ha una riga per Comune",
      panel.comune_table.rowCount(), 2)
check("...e cinque colonne", panel.comune_table.columnCount(), 5)
shown = {panel.parcel_table.item(r, 0).text()
         for r in range(panel.parcel_table.rowCount())}
print("        Comuni nel dettaglio: {0}".format(sorted(shown)))
check("entrambi i Comuni compaiono nel dettaglio", len(shown), 2)
print("        etichetta Comuni: {0!r}".format(panel.comune_label.text()))
check_true("l'etichetta li nomina entrambi",
           panel.comune_label.text().count(";") == 1)
check_true("...e l'etichetta Belfiore li porta tutti e due",
           PERUGIA in panel.belfiore_label.text()
           and ROMA in panel.belfiore_label.text())
check_true("le superfici sono in tabella, non solo la percentuale",
           "m2" in panel.parcel_table.item(0, 3).text()
           and "m2" in panel.parcel_table.item(0, 4).text())
check_text("il conteggio delle particelle e' quello del modello",
           panel.parcels_label.text(), "2")

check("visualizza particelle le disegna tutte", panel.show_parcels(), 2)
drawn_layer = state.layers.layer("parcels")
check("...e restano sulla mappa", drawn_layer.featureCount(), 2)
check_true("...di entrambi i Comuni",
           {f["belfiore"] for f in drawn_layer.getFeatures()}
           == {PERUGIA, ROMA})

panel.comune_table.selectRow(0)
picked_code = panel.comune_table.item(0, 0).data(Qt.ItemDataRole.UserRole)
picked = panel.on_comune_picked()
print("        scelto {0}: {1} particelle evidenziate".format(picked_code,
                                                             picked))
check("scegliere un Comune evidenzia le sue particelle", picked,
      len(result_j.shares_of(picked_code)))
check_true("...e la selezione sulla mappa e' la sua",
           {f["belfiore"] for f in drawn_layer.selectedFeatures()}
           == {picked_code})

details = panel.show_details()
check_true("la finestra di dettaglio elenca entrambi i Comuni",
           details is not None and "PERUGIA" in details.upper()
           and "ROMA" in details.upper())

print("\n-- e nell'elaborato --")
document = workspace.context.outputs_panel.document()
tables = tables_of(document)
print("        tabelle nella relazione: {0}".format(sorted(tables)))
check_true("la relazione porta il riepilogo per Comune",
           "Riepilogo per Comune" in tables)
check_true("...e il dettaglio delle particelle",
           "Particelle catastali" in tables)
check("il riepilogo esportato ha una riga per Comune",
      len(tables["Riepilogo per Comune"]), 2)
check("il dettaglio esportato ha una riga per particella",
      len(tables["Particelle catastali"]), 2)
check_true("l'export nomina i due codici Belfiore",
           {str(row[1]) for row in tables["Riepilogo per Comune"]}
           == {PERUGIA, ROMA})
text = docs.as_text(document)
check_true("il testo della relazione cita entrambi i Comuni",
           "Perugia" in text and "Roma" in text)

written = workspace.context.outputs_panel.write_document(
    "docx", os.path.join(TMP, "relazione_catastale.docx"))
check_true("la relazione si scrive davvero",
           bool(written) and os.path.exists(written)
           and os.path.getsize(written) > 0)
with zipfile.ZipFile(written) as archive:
    body = archive.read("word/document.xml").decode("utf-8", "replace")
check_true("...e il documento scritto contiene entrambi i Comuni",
           "Perugia" in body and "Roma" in body)
check_true("...con i due codici Belfiore",
           PERUGIA in body and ROMA in body)

workspace.unmount()
workspace = None
state = None
panel = None

print("\n" + "=" * 78)
QgsProject.instance().removeAllMapLayers()
QGS.exitQgis()
if SKIPS:
    print("SKIPPED ({0}):".format(len(SKIPS)))
    for name, why in SKIPS:
        print("   - {0}: {1}".format(name, why))
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for name in FAILURES:
        print("   - {0}".format(name))
    sys.exit(1)
print("ALL CHECKS PASSED")
