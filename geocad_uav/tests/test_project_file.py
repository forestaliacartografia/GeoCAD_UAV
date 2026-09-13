"""
v1.23.0: the project saved, reopened, and taken back one step at a time.

Hours of work go into a project: an area traced on the cadastre, constraints
measured, zones cut, a mix decided, a stand generated and then edited plant by
plant. This suite writes all of that to a file, opens it in a *second*
workspace that never saw the first, and compares the two -- geometry, areas,
zones, species, scheme, plants, every coordinate.

Then it presses the toolbar: Nuovo empties the project, Annulla takes back one
operator action (not one keystroke), Ripeti puts it back, and Salva remembers
where it wrote.

NEEDS QGIS with widgets. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_project_file.py
"""

import gc
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication,                          # noqa: E402
                       QgsCoordinateReferenceSystem, QgsGeometry, QgsProject)

QGS = QgsApplication([], True)
QGS.initQgis()

from geocad_uav.core.errors import GeoCadError                  # noqa: E402
from geocad_uav.forest.reforestation import composition as comp  # noqa: E402
from geocad_uav.gui import workflow as wf                       # noqa: E402
from geocad_uav.io import project_file as pf                    # noqa: E402

FAILURES = []
TMP = tempfile.mkdtemp(prefix="geocad_proj_")
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


AREA = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX, OY, OX + 360.0, OY + 280.0))

print("=" * 78)
print("Progetto -- salvato, riaperto, annullato")
print("=" * 78)

workspace = wf.Workspace(None)
state = workspace.state
dock = workspace.workflow
scheme = workspace.context.scheme_panel
zones_panel = workspace.context.zones_panel
generate_panel = workspace.context.generate_panel
natural_panel = workspace.context.natural_panel
state.reset_history()

# --------------------------------------------------------------------------
# P1 - the toolbar is there, and it says what it can do
# --------------------------------------------------------------------------
print("\n== P1: la barra del progetto ==")
labels = [action.text() for action in dock.toolbar.actions()
          if not action.isSeparator()]
print("        comandi: {0}".format(labels))
for expected in ("Nuovo", "Apri", "Salva", "Salva con nome", "Annulla",
                 "Ripeti", "Aggiorna", "Impostazioni"):
    check_true("il comando {0} c'e'".format(expected), expected in labels)
check_true("Annulla e' spento finche' non c'e' niente da annullare",
           not dock.actions["undo"].isEnabled())
check_true("...e Ripeti pure", not dock.actions["redo"].isEnabled())
check_true("Annulla ha la scorciatoia che tutti conoscono",
           dock.actions["undo"].shortcut().toString() == "Ctrl+Z")
check_text("il pannello dice che non e' stato salvato",
           dock.file_label.text(), "progetto non salvato")

# --------------------------------------------------------------------------
# P2 - a real project
# --------------------------------------------------------------------------
print("\n== P2: un progetto vero ==")
state.set_area(AREA, CRS32632, "Podere del Vento")
state.constraints.declare("strada", 8.0, label="Strade")
state.constraints.add_geometry("strada", QgsGeometry.fromWkt(
    "LINESTRING({0} {1},{2} {1})".format(OX + 20.0, OY + 140.0,
                                         OX + 340.0, OY + 140.0)))
# The panel clears the features of any rule the operator left unticked,
# so the road has to be switched on before applying it.
workspace.context.constraints_panel.rows["strada"][0].setChecked(True)
workspace.context.constraints_panel.apply()
scheme.plant_distance.setValue(7.0)
scheme.row_distance.setValue(9.0)
scheme.azimuth.setValue(45.0)
for name, percent in (("quercia", 60.0), ("frassino", 40.0)):
    scheme.species_key.setCurrentText(name)
    scheme.species_percent.setValue(percent)
    scheme.on_add_species()
scheme.apply_scheme()
zones_panel.band_count.setValue(3)
zones_panel.split()
natural_panel.glade_count.setValue(2)
natural_panel.glade_radius.setValue(11.0)
natural_panel.min_distance.setValue(2.0)
natural_panel.apply_settings()
plan = generate_panel.preview()
generate_panel.generate()
print("        {0:,} piante, {1} zone, {2} radure, utile {3:,.0f} m2".format(
    plan.count, len(state.zones), len(state.glades), state.usable_m2))
check_true("c'e' qualcosa da salvare", plan.count > 100)
check_true("il vincolo ha davvero tolto superficie",
           state.usable_m2 < state.gross_m2)
check_true("il progetto risulta modificato", state.dirty)

# --------------------------------------------------------------------------
# P3 - written, and read into a workspace that never saw it
# --------------------------------------------------------------------------
print("\n== P3: salvato e riaperto altrove ==")
path = os.path.join(TMP, "podere")
written = dock.on_save_as(path=path)
check_text("il suffisso viene aggiunto se manca", written, path + pf.SUFFIX)
check_true("...e il file c'e'", os.path.isfile(written))
print("        file: {0:,} byte".format(os.path.getsize(written)))
check_true("il progetto non risulta piu' modificato", not state.dirty)
check_true("il pannello lo scrive", "podere" in dock.file_label.text())

with open(written, encoding="utf-8") as handle:
    raw = json.load(handle)
check_text("il file si dichiara", raw["format"], pf.FORMAT_MARKER)
check("...con la sua versione di formato", raw["version"], pf.FORMAT_VERSION)
check_true("la geometria e' WKT, non una coppia di numeri",
           raw["area"]["geometry"].startswith("Polygon"))
check_text("...col suo sistema di riferimento accanto", raw["area"]["crs"],
           "EPSG:32632")

second = wf.Workspace(None)
other = second.state
warnings = other.load_from(written)
print("        avvisi: {0}".format(warnings or "nessuno"))
check("l'area riaperta e' la stessa, al metro quadro",
      other.area.lorda_m2, state.area.lorda_m2, 1e-6)
check("...e la superficie utile pure", other.usable_m2, state.usable_m2, 1e-6)
check_text("...e l'etichetta", other.area.label, "Podere del Vento")
check_text("...e il CRS", other.crs.authid(), "EPSG:32632")
check("i vincoli tornano", len(other.constraints.rules),
      len(state.constraints.rules))
check("...con le loro geometrie",
      other.constraints.rules["strada"].n_features, 1)
check("...e la loro fascia", other.constraints.rules["strada"].buffer_m, 8.0)
check("il sesto torna", other.spec.plant_distance_m, 7.0)
check("...in tutte le sue misure", other.spec.row_distance_m, 9.0)
check("...orientamento compreso", other.spec.row_azimuth_deg, 45.0)
check("le specie tornano", len(other.shares), 2)
check_true("...con le loro percentuali",
           dict(other.shares) == dict(state.shares))
check("le zone tornano", len(other.zones), len(state.zones))
check_true("...con i loro nomi",
           other.zones.names() == state.zones.names())
check("le radure tornano", len(other.glades), len(state.glades))
check("le impostazioni naturaliformi tornano",
      other.natural.min_distance_m, 2.0)

check("le piante tornano tutte", other.result.count, plan.count)
originals = {p.plant_id: p for p in plan.plants}
worst = 0.0
species_kept = 0
for record in other.result.plants:
    source = originals.get(record.plant_id)
    if source is None:
        continue
    worst = max(worst, abs(record.x - source.x), abs(record.y - source.y))
    if comp.species_of(record) == comp.species_of(source):
        species_kept += 1
print("        scarto massimo su una coordinata: {0:.2e} m".format(worst))
check("nessuna pianta si e' spostata riaprendo", worst, 0.0, 1e-9)
check("...e ognuna ha conservato la sua specie", species_kept, plan.count)
# This project has no DEM, so every plant has z = None -- what has to
# survive is exactly that, not an invented elevation.
z_kept = sum(1 for record in other.result.plants
             if (originals.get(record.plant_id) is not None
                 and record.z == originals[record.plant_id].z))
check("la quota di ogni pianta e' tornata com'era", z_kept, plan.count)
check_true("...e senza DEM resta vuota, non inventata",
           all(p.z is None for p in other.result.plants[:50]))
check_true("il piano riaperto e' sulla mappa",
           other.layers.layers.get("plants") is not None)
check("...con tutte le piante",
      other.layers.layers["plants"].featureCount(), plan.count)
check_true("...e l'area pure", "area" in other.layers.layers)
check_true("riaprendo, il progetto non risulta modificato", not other.dirty)

report = second.context.outputs_panel.build_report()
check_true("la relazione del progetto riaperto e' piena",
           "Podere del Vento" in report and "ZONE DI IMPIANTO" in report)

# --------------------------------------------------------------------------
# P4 - Annulla takes back an action, not a keystroke
# --------------------------------------------------------------------------
print("\n== P4: Annulla e Ripeti ==")
check_true("adesso Annulla si puo' premere", dock.actions["undo"].isEnabled())
before = state.result.count
zones_before = len(state.zones)
check_true("Annulla riporta indietro", dock.on_undo())
print("        dopo un Annulla: piante={0}, zone={1}".format(
    None if state.result is None else state.result.count, len(state.zones)))
check_true("l'ultima azione e' stata tolta",
           state.result is None or state.result.count != before)
check_true("...e Ripeti si e' acceso", dock.actions["redo"].isEnabled())
check_true("il layer delle piante segue il piano",
           (state.layers.layers.get("plants") is None)
           == (state.result is None))

check_true("Ripeti rimette", dock.on_redo())
check("...e le piante sono quelle di prima", state.result.count, before)
check("...e le zone pure", len(state.zones), zones_before)

steps = 0
while dock.on_undo():
    steps += 1
    if steps > 40:
        break
print("        azioni annullate fino in fondo: {0}".format(steps))
check_true("si torna indietro fino al progetto vuoto", state.area is None)
check_true("...e Annulla si spegne", not dock.actions["undo"].isEnabled())
check_true("...e la mappa si svuota con lui",
           not state.layers.layers.get("area"))
while dock.on_redo():
    pass
check("rifacendo tutto si torna esattamente al punto di partenza",
      state.result.count, before)
check_text("...col nome dell'area", state.area.label, "Podere del Vento")

# --------------------------------------------------------------------------
# P5 - Nuovo, and the refusals
# --------------------------------------------------------------------------
print("\n== P5: Nuovo, e i rifiuti ==")
check_true("Nuovo svuota il progetto", dock.on_new())
check_true("...l'area non c'e' piu'", state.area is None)
check_true("...ne' le zone", len(state.zones) == 0)
check_true("...ne' le piante", state.result is None)
check("...e i layer del plugin se ne sono andati", len(state.layers.layers), 0)
check_text("...e il pannello lo dice", dock.file_label.text(),
           "progetto non salvato")
check_true("il catalogo delle specie resta: e' una tabella, non una scelta",
           state.catalog.has("quercia"))

messages = []
dock.warn = lambda exc: messages.append(exc.formatted())
broken = os.path.join(TMP, "rotto" + pf.SUFFIX)
with open(broken, "w", encoding="utf-8") as handle:
    handle.write("{non e' json")
check_true("un file danneggiato non viene aperto",
           dock.on_open(path=broken) is None)
check_true("...e lo dice", messages and "danneggiato" in messages[-1])
print("        rifiuto: {0}".format(messages[-1]))

alien = os.path.join(TMP, "altro" + pf.SUFFIX)
with open(alien, "w", encoding="utf-8") as handle:
    json.dump({"format": "qualcos-altro"}, handle)
check_true("un file di un altro programma nemmeno",
           dock.on_open(path=alien) is None)
check_true("...e lo dice", "non e' un progetto" in messages[-1])

future = os.path.join(TMP, "futuro" + pf.SUFFIX)
with open(future, "w", encoding="utf-8") as handle:
    json.dump({"format": pf.FORMAT_MARKER,
               "version": pf.FORMAT_VERSION + 5}, handle)
check_true("un file di una versione futura non viene letto a meta'",
           dock.on_open(path=future) is None)
check_true("...e dice quali versioni sono in ballo",
           str(pf.FORMAT_VERSION + 5) in messages[-1]
           and str(pf.FORMAT_VERSION) in messages[-1])
print("        rifiuto: {0}".format(messages[-1]))

check_true("riaprendo quello buono si torna al progetto",
           dock.on_open(path=written) is not None)
check_text("...con la sua area", state.area.label, "Podere del Vento")
check("...e le sue piante", state.result.count, plan.count)

print("\n-- un DEM che non c'e' piu' si dichiara --")
data = pf.to_dict(state)
data["terrain"]["source"] = os.path.join(TMP, "dem-che-non-esiste.tif")
moved = os.path.join(TMP, "senza_dem" + pf.SUFFIX)
with open(moved, "w", encoding="utf-8") as handle:
    json.dump(data, handle)
warnings = second.state.load_from(moved)
print("        avvisi: {0}".format(warnings))
check_true("il progetto si apre lo stesso", second.state.area is not None)
check_true("...e dice che il DEM non si trova piu'",
           any("DEM" in text for text in warnings))
check_true("...senza inventarsi un terreno", second.state.terrain is None)

workspace.unmount()
second.unmount()

print("\n" + "=" * 78)
plan = state = other = dock = scheme = zones_panel = None
generate_panel = natural_panel = workspace = second = None
gc.collect()
QgsProject.instance().removeAllMapLayers()
QGS.exitQgis()
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for name in FAILURES:
        print("   - {0}".format(name))
    sys.exit(1)
print("ALL CHECKS PASSED")
