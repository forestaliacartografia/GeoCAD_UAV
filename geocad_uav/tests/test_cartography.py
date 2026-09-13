"""
v1.20.0: the drawing, on a sheet, that an operator can hand over.

A screenshot of the canvas is not cartography. This suite composes the sheet
the way the panel does -- title, legend pinned to the project's own layers,
scale bar, north arrow, a note with the CRS -- and then writes it to PDF and
to an image and looks at what came out: the file exists, it is not empty, the
image is the size the page and the resolution imply, and the scale printed on
the sheet is the scale the map frame is actually at.

The composition is QGIS's ``QgsPrintLayout``; nothing here draws anything.

NEEDS QGIS with widgets. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_cartography.py
"""

import gc
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication,                          # noqa: E402
                       QgsCoordinateReferenceSystem, QgsGeometry, QgsProject)

QGS = QgsApplication([], True)
QGS.initQgis()

from geocad_uav.gui import workflow as wf                       # noqa: E402
from geocad_uav.io import cartography as carto                  # noqa: E402

FAILURES = []
TMP = tempfile.mkdtemp(prefix="geocad_carto_")
CRS32632 = QgsCoordinateReferenceSystem("EPSG:32632")
OX, OY = 500000.0, 5000000.0
MM_PER_INCH = 25.4


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


AREA = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX, OY, OX + 400.0, OY + 310.0))

print("=" * 78)
print("Cartografia -- la tavola, non lo screenshot")
print("=" * 78)

workspace = wf.Workspace(None)
state = workspace.state
panel = workspace.context.cartography_panel
scheme = workspace.context.scheme_panel
generate_panel = workspace.context.generate_panel

# --------------------------------------------------------------------------
# K1 - nothing to draw is a refusal, not a blank sheet
# --------------------------------------------------------------------------
print("\n== K1: una tavola di niente non si compone ==")
messages = []
panel.warn = lambda exc: messages.append(exc.formatted())
check_true("senza progetto non compone", panel.compose() is None)
check_true("...e dice perche'", messages and "area" in messages[-1].lower())
print("        rifiuto: {0}".format(messages[-1]))
check_true("...e i pulsanti di esportazione restano spenti",
           not panel.pdf_button.isEnabled()
           and not panel.image_button.isEnabled())
check_true("esportare senza tavola e' un rifiuto",
           panel.export_pdf(path=os.path.join(TMP, "mai.pdf")) is None)
check_true("...leggibile", "Componi" in messages[-1])

# --------------------------------------------------------------------------
# K2 - the sheet, composed from the project
# --------------------------------------------------------------------------
print("\n== K2: la tavola prende i layer del progetto ==")
state.set_area(AREA, CRS32632, "Lotto Sant'Angelo")
# Deliberately NOT the project's CRS: a QGIS project left on EPSG:4326 with
# a reforestation project in UTM is the ordinary case, and a map frame that
# followed the QGIS one would draw the parcel as one pixel of the planet.
QgsProject.instance().setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
scheme.plant_distance.setValue(6.0)
scheme.row_distance.setValue(6.0)
scheme.species_key.setCurrentText("quercia")
scheme.species_percent.setValue(100.0)
scheme.on_add_species()
scheme.apply_scheme()
generate_panel.preview()
plants = generate_panel.generate()
print("        {0:,} piante generate".format(plants.featureCount()))

panel.title_edit.setText("Rimboschimento Sant'Angelo")
panel.author_edit.setText("Cap. N. M. Mancini")
panel.page_combo.setCurrentIndex(panel.page_combo.findData("A3"))
panel.orientation_combo.setCurrentIndex(
    panel.orientation_combo.findData(carto.ORIENTATION_LANDSCAPE))
layout = panel.compose()
check_true("la tavola e' stata composta", layout is not None)
check_text("...col titolo che l'operatore ha scritto", layout.name(),
           "Rimboschimento Sant'Angelo")
check_text("lo step Cartografia risulta fatto", state.status("cartography"),
           wf.DONE)

page = layout.pageCollection().page(0)
size = page.pageSize()
print("        pagina: {0:.0f} x {1:.0f} mm".format(size.width(),
                                                    size.height()))
check("il foglio e' un A3 orizzontale: 420 mm di base", size.width(), 420.0,
      0.6)
check("...per 297 di altezza", size.height(), 297.0, 0.6)

for item_id, name in ((carto.ITEM_MAP, "la mappa"),
                      (carto.ITEM_TITLE, "il titolo"),
                      (carto.ITEM_SUBTITLE, "il sottotitolo"),
                      (carto.ITEM_LEGEND, "la legenda"),
                      (carto.ITEM_SCALEBAR, "la scala grafica"),
                      (carto.ITEM_NORTH, "il nord"),
                      (carto.ITEM_CREDITS, "le note")):
    check_true("{0} e' sulla tavola".format(name),
               layout.itemById(item_id) is not None)

item_map = layout.itemById(carto.ITEM_MAP)
title = layout.itemById(carto.ITEM_TITLE)
credits = layout.itemById(carto.ITEM_CREDITS)
check_text("il titolo stampato e' quello chiesto", title.text(),
           "Rimboschimento Sant'Angelo")
print("        note: {0}".format(credits.text().replace("\n", " | ")))
check_true("le note dicono il sistema di riferimento",
           "EPSG:32632" in credits.text())
check_true("...e la scala", "Scala 1:" in credits.text())
check_true("...e chi l'ha redatta", "Mancini" in credits.text())
check_true("...e il formato con il suo orientamento",
           "A3, orizzontale" in credits.text())

drawn = item_map.layers()
project_layers = state.map_layers()
print("        layer in tavola: {0}".format([l.name() for l in drawn]))
check("la mappa disegna i layer del progetto", len(drawn),
      len(project_layers))
check_true("...proprio quelli, non quello che c'e' aperto in QGIS",
           {l.id() for l in drawn} == {l.id() for l in project_layers})
check_true("le piante sono fra questi",
           plants.id() in {l.id() for l in drawn})

legend = layout.itemById(carto.ITEM_LEGEND)
names = [node.name() for node in legend.model().rootGroup().children()]
print("        legenda: {0}".format(names))
check_true("la legenda elenca soltanto i layer del progetto",
           len(names) == len(project_layers))

print("\n-- l'inquadratura contiene il progetto --")
extent = item_map.extent()
print("        estensione: {0:.0f} x {1:.0f} m attorno a {2:.0f} x {3:.0f}"
      .format(extent.width(), extent.height(), AREA.boundingBox().width(),
              AREA.boundingBox().height()))
check_true("l'area di progetto ci sta tutta",
           extent.contains(AREA.boundingBox()))
check_true("...con un margine, non tagliata al vivo",
           extent.width() > AREA.boundingBox().width() * 1.02)
check_true("la scala e' un numero vero", item_map.scale() > 0.0)
check_text("il riquadro e' nel sistema del progetto, non in quello di QGIS",
           item_map.crs().authid(), "EPSG:32632")
print("        scala: 1:{0:,.0f}".format(item_map.scale()))
check_true("...e la scala e' quella di un lotto, non del pianeta",
           100.0 < item_map.scale() < 50_000.0)

# --------------------------------------------------------------------------
# K3 - a scale asked for is the scale set
# --------------------------------------------------------------------------
print("\n== K3: la scala chiesta e' la scala impostata ==")
panel.scale_spin.setValue(2000)
fixed = panel.compose()
item_map = fixed.itemById(carto.ITEM_MAP)
check("1:2000 chiesto, 1:2000 impostato", item_map.scale(), 2000.0, 1.0)
check_true("...e scritto nelle note",
           "1:2.000" in fixed.itemById(carto.ITEM_CREDITS).text())
check("comporre due volte non lascia due tavole",
      len([l for l in QgsProject.instance().layoutManager().layouts()
           if l.name() == "Rimboschimento Sant'Angelo"]), 1)
panel.scale_spin.setValue(0)

# --------------------------------------------------------------------------
# K4 - the sheet written out
# --------------------------------------------------------------------------
print("\n== K4: PDF e immagine scritti davvero ==")
panel.compose()
pdf_path = os.path.join(TMP, "tavola.pdf")
written = panel.export_pdf(path=pdf_path)
check_text("il PDF e' stato scritto dove chiesto", written, pdf_path)
size_pdf = os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0
print("        PDF: {0:,} byte".format(size_pdf))
check_true("...ed e' un file vero, non vuoto", size_pdf > 5000)
with open(pdf_path, "rb") as handle:
    head = handle.read(5)
check_text("...ed e' davvero un PDF", head, b"%PDF-")

panel.dpi_spin.setValue(150)
png_path = os.path.join(TMP, "tavola.png")
written = panel.export_image(path=png_path)
check_text("l'immagine e' stata scritta", written, png_path)
check_true("...e non e' vuota", os.path.getsize(png_path) > 5000)
from qgis.PyQt.QtGui import QImage                              # noqa: E402

image = QImage(png_path)
expected_w = 420.0 / MM_PER_INCH * 150.0
print("        immagine: {0} x {1} px, attesi {2:.0f} px di base".format(
    image.width(), image.height(), expected_w))
check_true("l'immagine e' grande quanto il foglio a quella risoluzione",
           abs(image.width() - expected_w) <= 3)
check_true("...e non e' bianca",
           len({image.pixel(x, y)
                for x in range(0, image.width(), 97)
                for y in range(0, image.height(), 89)}) > 1)

report = workspace.context.outputs_panel.build_report()
check_true("la relazione riporta la tavola", "CARTOGRAFIA" in report)
check_true("...col formato", "A3" in report)
check_true("...e la scala", "Scala:" in report)

# --------------------------------------------------------------------------
# K5 - the same composition on another sheet
# --------------------------------------------------------------------------
print("\n== K5: stesso disegno su un altro formato ==")
panel.page_combo.setCurrentIndex(panel.page_combo.findData("A4"))
panel.orientation_combo.setCurrentIndex(
    panel.orientation_combo.findData(carto.ORIENTATION_PORTRAIT))
panel.north_check.setChecked(False)
a4 = panel.compose()
a4_size = a4.pageCollection().page(0).pageSize()
print("        pagina: {0:.0f} x {1:.0f} mm".format(a4_size.width(),
                                                    a4_size.height()))
check("un A4 verticale e' largo 210 mm", a4_size.width(), 210.0, 0.6)
check("...e alto 297", a4_size.height(), 297.0, 0.6)
a4_map = a4.itemById(carto.ITEM_MAP)
check("la mappa occupa la stessa frazione di foglio",
      a4_map.rect().width() / a4_size.width(),
      carto.FRAME[carto.ITEM_MAP][2], 0.01)
check_true("il nord, tolto, non c'e'",
           a4.itemById(carto.ITEM_NORTH) is None)
check_true("la legenda invece c'e' ancora",
           a4.itemById(carto.ITEM_LEGEND) is not None)
check_true("l'area ci sta anche qui",
           a4_map.extent().contains(AREA.boundingBox()))

arrow = carto.north_arrow_path()
print("        freccia del nord: {0}".format(arrow))
check_true("QGIS fornisce una freccia del nord, e la troviamo",
           arrow is not None and os.path.isfile(arrow))

workspace.unmount()
check_true("chiudendo, la tavola resta nel progetto: e' roba dell'operatore",
           QgsProject.instance().layoutManager().layoutByName(
               "Rimboschimento Sant'Angelo") is not None)

print("\n" + "=" * 78)
plants = layout = fixed = a4 = item_map = a4_map = None
title = credits = legend = image = None
workspace = state = panel = scheme = generate_panel = None
gc.collect()
QgsProject.instance().layoutManager().clear()
QgsProject.instance().removeAllMapLayers()
QGS.exitQgis()
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for name in FAILURES:
        print("   - {0}".format(name))
    sys.exit(1)
print("ALL CHECKS PASSED")
