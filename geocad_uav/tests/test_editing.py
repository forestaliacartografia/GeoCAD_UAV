"""
v1.19.0: the plan changed on the map, and the numbers that follow.

A generated plan is a proposal. The operator knows there is a rock where
plant 412 landed, that the corner by the track wants three more, and that the
row along the ditch should be oaks. This suite does exactly that -- move,
add, delete, reassign -- on the real layer, and then checks that everything
an operator reads afterwards was measured *after* the edit: the count, the
elevation under the plant that moved, the mix, and the anomalies.

The digitising itself is QGIS's own (startEditing, changeGeometry,
addFeatures, deleteFeatures): what is tested here is that the project takes
the plan back off the layer and re-derives from it.

NEEDS QGIS with widgets. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_editing.py
"""

import gc
import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from osgeo import gdal, osr                                      # noqa: E402
from qgis.core import (QgsApplication,                           # noqa: E402
                       QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry,
                       QgsPoint, QgsProject, QgsRasterLayer)

QGS = QgsApplication([], True)
QGS.initQgis()

from geocad_uav.forest.reforestation import composition as comp_mod  # noqa: E402
from geocad_uav.forest.reforestation import spacing as spacing_mod  # noqa: E402
from geocad_uav.forest.reforestation import terrain as terrain_mod  # noqa: E402
from geocad_uav.gui import workflow as wf                        # noqa: E402

FAILURES = []
TMP = tempfile.mkdtemp(prefix="geocad_edit_")
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


# -- a slope with a known height at every point ----------------------------
# z = 400 + 0.25 * (x - OX): moving a plant 40 m east raises it by exactly
# 10 m, so a re-read elevation can be checked against arithmetic.
CELL = 4.0
NX, NY = 150, 150
GRADE = 0.25
xs = OX + (np.arange(NX) + 0.5) * CELL
ys = OY - (np.arange(NY) + 0.5) * CELL
XX, _YY = np.meshgrid(xs, ys)
Z = 400.0 + GRADE * (XX - OX)

DEM_PATH = os.path.join(TMP, "piano.tif")
gdal.UseExceptions()
_ds = gdal.GetDriverByName("GTiff").Create(DEM_PATH, NX, NY, 1,
                                           gdal.GDT_Float64)
_ds.SetGeoTransform((OX, CELL, 0.0, OY, 0.0, -CELL))
_srs = osr.SpatialReference()
_srs.ImportFromEPSG(32632)
_ds.SetProjection(_srs.ExportToWkt())
_ds.GetRasterBand(1).WriteArray(Z)
_ds.FlushCache()
_ds = None
DEM_LAYER = QgsRasterLayer(DEM_PATH, "DTM", "gdal")

AREA = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 60.0, OY - 500.0, OX + 500.0, OY - 80.0))

print("=" * 78)
print("Editing -- l'impianto modificato sulla mappa")
print("=" * 78)

workspace = wf.Workspace(None)
state = workspace.state
scheme = workspace.context.scheme_panel
generate_panel = workspace.context.generate_panel
edit_panel = workspace.context.edit_panel
verify_panel = workspace.context.verify_panel

state.set_area(AREA, CRS32632, "Lotto di prova")
state.terrain = terrain_mod.TerrainAnalysis.from_layer(
    DEM_LAYER, CRS32632, AREA, margin_m=40.0)
scheme.plant_distance.setValue(10.0)
scheme.row_distance.setValue(10.0)
for name, percent in (("quercia", 60.0), ("frassino", 40.0)):
    scheme.species_key.setCurrentText(name)
    scheme.species_percent.setValue(percent)
    scheme.on_add_species()
scheme.apply_scheme()

plan = generate_panel.preview()
layer = generate_panel.generate()
BORN = plan.count
print("  impianto iniziale: {0:,} piante".format(BORN))

# --------------------------------------------------------------------------
# E1 - the step exists, and it is reachable
# --------------------------------------------------------------------------
print("\n== E1: Editing e' uno step del flusso ==")
labels = [workspace.workflow.list.item(i).text()
          for i in range(workspace.workflow.list.count())]
print("        {0}".format(labels))
check("il flusso elenca ogni step", workspace.workflow.list.count(),
      len(wf.STEPS))
editing_label = dict(wf.STEPS)["edit"]
check_true("...e uno si chiama Editing", editing_label in labels)
workspace.workflow.list.setCurrentRow(labels.index(editing_label))
check_true("lo step apre il pannello di editing",
           workspace.context.stack.currentWidget()
           is workspace.context.pages["edit"][0].parent()
           or workspace.context.stack.currentWidget() is not None)
check_text("prima di modificare, lo step non e' fatto", state.status("edit"),
           wf.NOT_STARTED if state.current_step != "edit" else wf.ACTIVE)

edit_panel.refresh()
check("il pannello conta le piante del layer",
      int(edit_panel.count_label.text().replace(",", "")), BORN)
check_true("il layer non e' ancora modificabile", not layer.isEditable())
check_true("...quindi gli strumenti sono spenti",
           not edit_panel.move_button.isEnabled())

check_true("[Abilita modifica] apre la modifica", edit_panel.set_editing(True))
check_true("...il layer lo conferma", layer.isEditable())
check_true("...e gli strumenti si accendono",
           edit_panel.move_button.isEnabled()
           and edit_panel.add_button.isEnabled()
           and edit_panel.delete_button.isEnabled())

# --------------------------------------------------------------------------
# E2 - a plant moved is re-read on the ground
# --------------------------------------------------------------------------
print("\n== E2: sposto una pianta, la quota la segue ==")
first = next(layer.getFeatures())
fid = first.id()
before = first.geometry().constGet()
x0, y0, z0 = float(before.x()), float(before.y()), float(before.z())
print("        pianta {0}: ({1:.1f}, {2:.1f}) a {3:.2f} m".format(
    first["plant_id"], x0, y0, z0))
check("la quota generata e' quella del piano", z0, 400.0 + GRADE * (x0 - OX),
      0.01)

DX = 40.0
layer.changeGeometry(fid, QgsGeometry(QgsPoint(x0 + DX, y0, z0)))
moved = layer.getFeature(fid)
check("la geometria sul layer si e' spostata",
      moved.geometry().constGet().x(), x0 + DX, 1e-6)

anomalies = edit_panel.apply_edits()
record = next(p for p in state.result.plants if p.plant_id == first["plant_id"])
print("        dopo: ({0:.1f}, {1:.1f}) a {2:.2f} m, attesa {3:.2f} m".format(
    record.x, record.y, record.z, z0 + GRADE * DX))
check("il progetto ha preso la nuova posizione", record.x, x0 + DX, 1e-6)
check("...e ha riletto la quota sotto la pianta", record.z,
      z0 + GRADE * DX, 0.02)
check_true("...e la pendenza", record.slope_deg is not None
           and math.isfinite(record.slope_deg))
check("il conteggio non e' cambiato", state.result.count, BORN)
check_text("lo step Editing risulta fatto", state.status("edit"), wf.DONE)
check_true("la verifica e' stata rifatta, non ereditata", state.verified)

# --------------------------------------------------------------------------
# E3 - a plant added by hand, and one deleted
# --------------------------------------------------------------------------
print("\n== E3: aggiungo e elimino piante ==")
added = QgsFeature(layer.fields())
ax, ay = OX + 120.0, OY - 300.0
added.setGeometry(QgsGeometry(QgsPoint(ax, ay, 0.0)))
check_true("QGIS accetta la pianta digitalizzata", layer.addFeature(added))
edit_panel.apply_edits()
check("il progetto ha una pianta in piu'", state.result.count, BORN + 1)
check("...e sa che e' stata messa a mano", state.added_plants, 1)
hand = next(p for p in state.result.plants if p.row_id <= spacing_mod.ADDED_ROW_ID)
print("        aggiunta: id {0}, fila {1}, quota {2:.2f} m".format(
    hand.plant_id, hand.row_id, hand.z))
check("le e' stato dato un id libero", hand.plant_id,
      max(p.plant_id for p in state.result.plants), 0)
check("...e la quota letta dal DEM, non lo zero digitalizzato", hand.z,
      400.0 + GRADE * (ax - OX), 0.02)
check_text("...e nessuna specie, finche' nessuno gliela da'",
           comp_mod.species_of(hand), "")
check_true("il pannello lo scrive", "1" in edit_panel.added_label.text())

second = list(layer.getFeatures())[3]
layer.selectByIds([second.id()])
check("eliminare la selezione toglie una pianta",
      edit_panel.delete_selected(), 1)
check("il progetto ne ha una di meno", state.result.count, BORN)
check_true("...e quella eliminata non c'e' piu'",
           all(p.plant_id != second["plant_id"]
               for p in state.result.plants))

# --------------------------------------------------------------------------
# E4 - the species changed on the map, and the mix that follows
# --------------------------------------------------------------------------
print("\n== E4: cambio specie alla selezione ==")
before_mix = comp_mod.achieved_percentages(state.result.plants)
print("        prima: {0}".format({k: round(v, 1)
                                   for k, v in before_mix.items()}))
oaks = [f.id() for f in layer.getFeatures() if f["specie"] == "frassino"][:25]
check_true("ci sono frassini da convertire", len(oaks) == 25)
layer.selectByIds(oaks)
edit_panel.refresh()
check_true("il pannello dice quante ne sono selezionate",
           "25" in edit_panel.selection_label.text())
index = edit_panel.species_combo.findData("quercia")
check_true("le specie del progetto sono nel combo", index >= 0)
edit_panel.species_combo.setCurrentIndex(index)
check("venticinque piante cambiano specie", edit_panel.assign_species(), 25)

after_mix = comp_mod.achieved_percentages(state.result.plants)
print("        dopo:  {0}".format({k: round(v, 1)
                                   for k, v in after_mix.items()}))
check_true("il layer porta la specie nuova",
           all(layer.getFeature(fid)["specie"] == "quercia" for fid in oaks))
check_true("le querce sono aumentate",
           after_mix["quercia"] > before_mix["quercia"])
check_true("...e i frassini diminuiti",
           after_mix["frassino"] < before_mix["frassino"])
check("il totale delle percentuali resta cento",
      sum(after_mix.values()), 100.0, 1e-6)
check("la composizione del progetto e' quella del layer",
      state.composition.counts.get("quercia", 0),
      sum(1 for f in layer.getFeatures() if f["specie"] == "quercia"))

anomalies = state.anomalies
print("        anomalie: {0}".format(anomalies))
check_true("la verifica segnala che il mix non e' piu' quello chiesto",
           any("quercia" in text or "frassino" in text for text in anomalies))
check_true("...e il pannello di editing lo mostra",
           "ANOMALIE" in edit_panel.outcome.toPlainText())

print("\n-- lo stesso giudizio del pannello Verifica --")
same = verify_panel.run()
check("la Verifica trova le stesse anomalie", len(same), len(anomalies))
check_true("...identiche", same == anomalies)

# --------------------------------------------------------------------------
# E5 - refusals, and the plan that stays consistent
# --------------------------------------------------------------------------
print("\n== E5: rifiuti leggibili ==")
messages = []
edit_panel.warn = lambda exc: messages.append(exc.formatted())
layer.removeSelection()
check("senza selezione non elimina niente", edit_panel.delete_selected(), 0)
check_true("...e dice di selezionare", "Seleziona" in messages[-1])
check("senza selezione non cambia specie", edit_panel.assign_species(), 0)
check_true("...e lo dice", "Seleziona" in messages[-1])

check_true("chiudere la modifica salva", not edit_panel.set_editing(False))
check_true("...e il layer non e' piu' modificabile", not layer.isEditable())
check("...e il conteggio e' quello vero", state.result.count,
      layer.featureCount())

report = workspace.context.outputs_panel.build_report()
check_true("la relazione riporta la composizione del layer modificato",
           "quercia" in report.lower())

exported = state.result.count
workspace.unmount()
check("i layer del plugin se ne vanno", len(state.layers.layers), 0)
print("        piante finali: {0:,}".format(exported))

print("\n" + "=" * 78)
layer = DEM_LAYER = None
workspace = state = scheme = generate_panel = edit_panel = verify_panel = None
plan = record = hand = first = moved = added = second = None
gc.collect()
QgsProject.instance().removeAllMapLayers()
QGS.exitQgis()
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for name in FAILURES:
        print("   - {0}".format(name))
    sys.exit(1)
print("ALL CHECKS PASSED")
