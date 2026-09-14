"""
v2.2.0: il volo si fa dalla GUI, passo per passo, senza chiamare niente.

This suite never calls ``generate()``. It does what an operator does: it
opens the flight module in the left dock, walks the twelve steps, fills the
controls it finds on their pages, looks for the command that turns those
parameters into a route, presses it, and then reads what the dock shows.

    area -> parametri -> GENERA ROTTA -> anteprima -> simulazione
    -> validazione -> export

The command is a real QPushButton and it is pressed with ``click()``, which
is the signal path a mouse uses. If the button were missing, hidden,
unreachable from a step, disabled with the parameters complete, or wired to
nothing, this suite fails -- which is the whole reason it exists.

NEEDS QGIS with widgets. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_uav_gui_e2e.py
"""

import gc
import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from osgeo import gdal, osr                                     # noqa: E402
from qgis.core import (QgsApplication,                          # noqa: E402
                       QgsCoordinateReferenceSystem, QgsGeometry,
                       QgsProject, QgsRasterLayer)

QGS = QgsApplication([], True)
QGS.initQgis()

from qgis.PyQt.QtCore import Qt                                 # noqa: E402
from qgis.PyQt.QtWidgets import QPushButton                     # noqa: E402

from geocad_uav.gui import uav_panel as up                      # noqa: E402
from geocad_uav.gui import workflow as wf                       # noqa: E402

FAILURES = []
SKIPS = []
TMP = tempfile.mkdtemp(prefix="geocad_gui_")
CRS = QgsCoordinateReferenceSystem("EPSG:32632")
OX, OY = 500000.0, 5000000.0


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<54} got={2:<16.10g} exp={3:.10g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def visible_in(page, widget) -> bool:
    """True when the widget really lives under that page of the stack."""
    parent = widget.parentWidget()
    while parent is not None:
        if parent is page:
            return True
        parent = parent.parentWidget()
    return False


# -- a hillside on disk ----------------------------------------------------
CELL = 5.0
NX, NY = 120, 110
xs = OX + (np.arange(NX) + 0.5) * CELL
ys = OY - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(xs, ys)
Z = 400.0 + 0.14 * (XX - OX) + 18.0 * np.sin((YY - OY) / 120.0)

DEM_PATH = os.path.join(TMP, "versante.tif")
gdal.UseExceptions()
_ds = gdal.GetDriverByName("GTiff").Create(DEM_PATH, NX, NY, 1,
                                           gdal.GDT_Float32)
_ds.SetGeoTransform((OX, CELL, 0.0, OY, 0.0, -CELL))
_srs = osr.SpatialReference()
_srs.ImportFromEPSG(32632)
_ds.SetProjection(_srs.ExportToWkt())
_ds.GetRasterBand(1).WriteArray(Z.astype(np.float32))
_ds.FlushCache()
_ds = None
DEM_LAYER = QgsRasterLayer(DEM_PATH, "DTM di prova", "gdal")
QgsProject.instance().addMapLayer(DEM_LAYER)

AREA = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 150.0, OY - 420.0, OX + 450.0, OY - 120.0))

print("=" * 78)
print("VOLO UAV dalla GUI: area, parametri, Genera rotta, anteprima, volo")
print("=" * 78)

workspace = wf.Workspace(None)
state = workspace.state
context = workspace.context
dock = workspace.workflow
panel = context.uav_panel


def goto(key):
    """Navigate the left dock to a step, the way a click on the list does."""
    dock.select_step(key)
    for row in range(dock.list.count()):
        item = dock.list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == key:
            dock.list.setCurrentRow(row)
            break
    QGS.processEvents()
    return context.stack.currentWidget()


# --------------------------------------------------------------------------
# G1 - the flight module, and the command under every one of its steps
# --------------------------------------------------------------------------
print("\n== G1: il modulo volo, e il comando sotto ogni suo step ==")
check_true("all'avvio si e' nel rimboschimento",
           dock.module == wf.MODULE_FOREST)
check_true("...e la barra della missione non c'e'",
           not context.flight_actions.isVisibleTo(context))

dock.set_module(wf.MODULE_UAV)
goto(up.STEP_AREA)
check_true("scelto il volo, la barra della missione compare",
           context.flight_actions.isVisibleTo(context))
check_true("il comando 'Genera rotta' e' un vero pulsante",
           isinstance(context.generate_button, QPushButton))
print("        testo del comando: {0!r}".format(
    context.generate_button.text()))
check_true("...e si chiama cosi'",
           "genera" in context.generate_button.text().lower()
           and "rotta" in context.generate_button.text().lower())
check_true("il comando e' quello del pianificatore, non una copia",
           context.generate_button is panel.generate_button)
check_true("la barra porta anche l'anteprima",
           isinstance(context.preview_button, QPushButton))
check_true("...e la creazione dei layer",
           context.confirm_button is panel.confirm_button)

missing = [label for key, label in wf.UAV_STEPS
           if not (goto(key) is not None
                   and context.flight_actions.isVisibleTo(context))]
print("        step senza la barra: {0}".format(missing or "nessuno"))
check("la barra e' raggiungibile da tutti e dodici gli step",
      len(wf.UAV_STEPS) - len(missing), 12)

hidden = []
for key, label in wf.STEPS:
    goto(key)
    if context.flight_actions.isVisibleTo(context):
        hidden.append(label)
print("        step di rimboschimento con la barra: {0}".format(
    hidden or "nessuno"))
check("...e non compare in nessuno step di rimboschimento", len(hidden), 0)
dock.set_module(wf.MODULE_UAV)

# --------------------------------------------------------------------------
# G2 - with nothing set, the command is off and says why
# --------------------------------------------------------------------------
print("\n== G2: senza parametri il comando e' spento, e dice perche' ==")
goto(up.STEP_AREA)
check_true("senza area il comando e' spento",
           not context.generate_button.isEnabled())
reason = context.refresh_flight_actions()
print("        motivo: {0}".format(reason))
check_true("...e la barra dice perche'", bool(reason))
check_true("...con un motivo leggibile, non un codice",
           len(reason) > 20 and reason[0].isupper())
check_true("il motivo e' visibile sotto il comando",
           context.flight_hint.isVisibleTo(context.flight_actions))
before = panel.last_mission
context.generate_button.click()
QGS.processEvents()
check_true("premerlo comunque non inventa una missione",
           panel.last_mission is before)

# --------------------------------------------------------------------------
# G3 - the parameters, step by step, on the pages they belong to
# --------------------------------------------------------------------------
print("\n== G3: i parametri, step per step ==")
page = goto(up.STEP_AREA)
check_true("lo step Area mostra il selettore dell'estensione",
           visible_in(page, panel.extent))
panel.extent.set_extent(AREA, CRS)
QGS.processEvents()

page = goto(up.STEP_DRONE)
check_true("lo step Drone mostra la libreria droni",
           visible_in(page, panel.drone_combo))
panel.drone_combo.setCurrentIndex(0)

page = goto(up.STEP_SENSOR)
check_true("lo step Sensore mostra la libreria camere",
           visible_in(page, panel.camera_combo))
panel.camera_combo.setCurrentIndex(0)

page = goto(up.STEP_GSD)
check_true("lo step GSD mostra quota e risoluzione",
           visible_in(page, panel.h_agl) and visible_in(page, panel.gsd_target))
panel.height_mode.setCurrentIndex(
    panel.height_mode.findData(up.HEIGHT_FROM_AGL))
panel.h_agl.setValue(90.0)

page = goto(up.STEP_TERRAIN)
check_true("lo step DEM mostra il selettore del raster",
           visible_in(page, panel.dem_combo))
panel.dem_combo.setLayer(DEM_LAYER)
QGS.processEvents()

page = goto(up.STEP_CAPTURE)
check_true("lo step Acquisizione mostra le sovrapposizioni",
           visible_in(page, panel.frontlap) and visible_in(page, panel.sidelap))
panel.frontlap.setValue(80.0)
panel.sidelap.setValue(65.0)
panel.speed_kmh.setValue(36.0)

page = goto(up.STEP_SAFETY)
check_true("lo step Sicurezza mostra la riserva di batteria",
           visible_in(page, panel.reserve_pct))
QGS.processEvents()

print("        motivo dopo i parametri: {0!r}".format(
    context.refresh_flight_actions()))
check_true("con i parametri completi il comando si accende",
           context.generate_button.isEnabled())
check_true("...e la barra non ha piu' niente da obiettare",
           not context.flight_hint.isVisibleTo(context.flight_actions))

# --------------------------------------------------------------------------
# G4 - the click, and what it produces
# --------------------------------------------------------------------------
print("\n== G4: il click, e quello che produce ==")
goto(up.STEP_LINES)
check_true("il comando e' raggiungibile anche da qui",
           context.flight_actions.isVisibleTo(context)
           and context.generate_button.isEnabled())
context.generate_button.click()
QGS.processEvents()

mission = panel.last_mission
check_true("il click ha prodotto una missione", mission is not None)
if mission is None:
    print("FAILED: nessuna rotta dalla GUI, il resto non e' verificabile")
    sys.exit(1)
stats = mission.stats
print("        {0} strisciate, {1} waypoint, {2} scatti, {3:,.0f} m".format(
    stats.n_strips, stats.n_waypoints, stats.n_photos, stats.total_length_m))
check_true("...con delle strisciate", stats.n_strips >= 2)
check_true("...dei waypoint", len(mission.waypoints) > 10)
check_true("...dei punti di scatto", len(mission.photos) > 10)
check_true("...e un percorso", stats.total_length_m > 0.0)
check_true("il riepilogo del pannello si e' aggiornato",
           "Waypoint" in panel.summary.toPlainText())
check_true("...e riporta il numero di scatti",
           "{0:,}".format(stats.n_photos) in panel.summary.toPlainText())

check_true("l'anteprima e' ora disponibile",
           context.preview_button.isEnabled())
check_true("la creazione dei layer pure",
           context.confirm_button.isEnabled())
check_true("il simulatore ha qualcosa da simulare",
           context.play_button.isEnabled())
check_true("...e il validatore pure", panel.quality_button.isEnabled())

print("\n-- le impronte, chieste dalla GUI --")
check("una rotta senza copertura accesa non le porta",
      len(mission.footprints), 0)
footprints = context.show_footprints()
check_true("il comando le calcola e le disegna", footprints is not None)
check("...una per scatto", len(mission.footprints), len(mission.photos))
QgsProject.instance().removeMapLayer(footprints.id())

# --------------------------------------------------------------------------
# G5 - the preview, from its own command
# --------------------------------------------------------------------------
print("\n== G5: l'anteprima, dal suo comando ==")
context.preview_button.click()
QGS.processEvents()
names = {layer.name() for layer in QgsProject.instance().mapLayers().values()}
print("        layer nel progetto: {0}".format(
    sorted(n for n in names if n in ("flight_lines", "waypoints",
                                     "photo_centers", "photo_footprints"))))
for wanted in ("flight_lines", "waypoints", "photo_centers",
               "photo_footprints"):
    check_true("l'anteprima ha messo {0} sulla mappa".format(wanted),
               wanted in names)
layers = panel._preview_layers
check("il layer dei waypoint li porta tutti",
      layers["waypoints"].featureCount(), len(mission.waypoints))
check("...e quello degli scatti pure",
      layers["photo_centers"].featureCount(), len(mission.photos))

# --------------------------------------------------------------------------
# G6 - the simulator, driven from its buttons
# --------------------------------------------------------------------------
print("\n== G6: il simulatore, dai suoi pulsanti ==")
goto(up.STEP_SIMULATION)
player = context.player
check_true("il cursore del tempo e' attivo", context.time_slider.isEnabled())
check_true("il profilo altimetrico ha dei campioni",
           context.profile_chart.statistics()["samples"] > 100)
stats_profile = context.profile_chart.statistics()
print("        profilo: AMSL {0:,.0f}-{1:,.0f} m, AGL {2:.1f}-{3:.1f} m, "
      "buchi DEM {4}".format(stats_profile["ground_min_m"],
                             stats_profile["ground_max_m"],
                             stats_profile["agl_min_m"],
                             stats_profile["agl_max_m"],
                             stats_profile["dem_gaps"]))
check_true("il profilo distingue AMSL e AGL",
           stats_profile["ground_min_m"] < stats_profile["flight_min_m"]
           and stats_profile["agl_min_m"] > 0.0)
check_true("...e dichiara il dislivello", stats_profile["relief_m"] > 5.0)
check("...e i buchi DEM, che qui non ci sono", stats_profile["dem_gaps"], 0)

check_true("Play parte", context.play_button.click() is None
           and player.is_playing)
for _ in range(20):
    player.tick()
running = player.state()
print("        {0}".format(player.summary().replace("\n", " | ")))
check_true("il drone si e' mosso", running["distance_m"] > 0.0)
check_true("...e la telemetria dice la strisciata", running["strip"] >= 0)
check_true("...la percentuale di missione",
           0.0 < running["progress_pct"] < 100.0)
check_true("il percorso gia' volato e' disegnato",
           player.track_vertices() >= 2)
context.pause_button.click()
check_true("Pausa ferma il cronometro", not player.is_playing)

print("\n-- e la copertura, che cresce col volo --")
# Sampled along the flight rather than at the first exposure: the first
# exposure is on the lead-in, outside the AOI, and this counts AOI ground
# imaged -- so zero there is the right answer.
seen = []
for fraction in (0.1, 0.25, 0.5, 0.75, 1.0):
    player.seek(player.duration_s * fraction)
    seen.append(player.state()["coverage_pct"])
print("        copertura al 10/25/50/75/100 %: {0}".format(
    ["{0:.1f}".format(v) for v in seen]))
check_true("la copertura cresce mentre il volo procede",
           all(b >= a - 1e-9 for a, b in zip(seen, seen[1:])))
check_true("...ed e' gia' positiva a un quarto di missione", seen[1] > 0.0)
check_true("...e a meta' ha coperto una parte consistente", seen[2] > 25.0)
half = player.state()
check("coperto piu' scoperto fa l'area di missione",
      half["covered_m2"] + half["uncovered_m2"], 90000.0, 1.0)
player.seek(0.0)
check("tornare all'inizio riporta la copertura a zero",
      player.state()["coverage_pct"], 0.0, 1e-9)
context.play_button.click()
context.pause_button.click()

context.end_button.click()
final = player.state()
check("Fine porta all'ultimo waypoint", final["waypoint"],
      len(mission.waypoints))
check("...con tutti gli scatti presi", player.flash_count,
      len(mission.photos))
print("        copertura a fine missione: {0:.1f} % ({1:,.0f} m2 su "
      "{2:,.0f})".format(final["coverage_pct"], final["covered_m2"],
                         final["covered_m2"] + final["uncovered_m2"]))
check_true("la copertura a fine missione e' quasi totale",
           final["coverage_pct"] > 90.0)
context.start_button.click()
check("Inizio riazzera la copertura", player.state()["coverage_pct"], 0.0,
      1e-9)
# At take-off the track is the aircraft's own position: one point, no line.
check("...e il percorso volato torna al solo punto di decollo",
      player.track_vertices(), 1)
context.forward_button.click()
check("un passo avanti e' un waypoint avanti", player.index, 1)
context.back_button.click()
check("...e uno indietro torna", player.index, 0)

print("\n-- e la velocita' di riproduzione --")
rates = [context.rate_combo.itemData(i)
         for i in range(context.rate_combo.count())]
print("        velocita' offerte: {0}".format(rates))
for wanted in (0.25, 0.5, 1.0, 2.0, 4.0):
    check_true("c'e' la velocita' {0:g}x".format(wanted), wanted in rates)
context.rate_combo.setCurrentIndex(context.rate_combo.findData(0.25))
check("scegliere 0.25x rallenta davvero l'orologio", player.rate, 0.25)
player.seek(0.0)
context.play_button.click()
for _ in range(10):
    player.tick()
slow_t = player.t_sim
context.pause_button.click()
context.rate_combo.setCurrentIndex(context.rate_combo.findData(4.0))
player.seek(0.0)
context.play_button.click()
for _ in range(10):
    player.tick()
fast_t = player.t_sim
context.pause_button.click()
print("        dieci tick: 0.25x -> {0:.2f} s, 4x -> {1:.2f} s".format(
    slow_t, fast_t))
check("quattro volte piu' veloce e' sedici volte l'altra", fast_t / slow_t,
      16.0, 1e-6)
context.rate_combo.setCurrentIndex(context.rate_combo.findData(1.0))
context.stop_button.click()

# --------------------------------------------------------------------------
# G7 - the pre-flight check and the battery plan
# --------------------------------------------------------------------------
print("\n== G7: controllo pre-volo e piano batterie ==")
goto(up.STEP_VALIDATION)
panel.quality_button.click()
QGS.processEvents()
verdict = panel.quality.toPlainText()
print("        {0}".format(verdict.splitlines()[0] if verdict else "-"))
check_true("il controllo ha prodotto un verdetto", bool(verdict.strip()))
report = panel.last_report
check_true("...e un rapporto con dei controlli",
           report is not None and len(report.checks) > 15)
severities = {c.severity for c in report.checks}
print("        esiti: {0}, errori {1}, avvisi {2}".format(
    sorted(severities), len(report.errors), len(report.warnings)))
check_true("gli esiti sono OK / WARNING / ERROR",
           severities <= {"ok", "warning", "error"})
check_true("il verdetto e' leggibile nella GUI",
           any(word in verdict.lower()
               for word in ("valida", "errore", "avviso", "ok")))

plan = panel.battery_plan()
print("        {0}".format(panel.battery_summary()))
check_true("il piano batterie esiste", plan is not None)
check_true("...la riserva e' esplicita", plan["reserve_pct"] > 0.0)
check("...e tolta dall'autonomia nominale",
      plan["usable_s"], plan["nominal_s"] * (1.0 - plan["reserve_pct"] / 100.0),
      1e-6)
check_true("...il margine e' dichiarato",
           math.isfinite(plan["margin_s"]))
check_true("...le batterie necessarie pure",
           plan["batteries_needed"] >= 1)
check_true("...e il tempo operativo comprende i cambi",
           plan["operative_s"] >= plan["flight_s"])
check_true("la riserva compare accanto al comando che la imposta",
           "riserva" in panel.battery_note.text().lower())

print("\n-- alzare la riserva stringe il budget che divide le tratte --")
from geocad_uav.uav.mission import endurance_budget_s             # noqa: E402

goto(up.STEP_SAFETY)
panel.reserve_pct.setValue(0.0)
QGS.processEvents()
context.generate_button.click()
QGS.processEvents()
loose = panel.last_mission
loose_budget = endurance_budget_s(panel.build_params())
panel.reserve_pct.setValue(80.0)
QGS.processEvents()
context.generate_button.click()
QGS.processEvents()
strict = panel.last_mission
strict_plan = panel.battery_plan()
strict_budget = endurance_budget_s(panel.build_params())
print("        riserva 0 %: {0:.1f} min utili, {1} batterie".format(
    loose_budget / 60.0, loose.stats.n_batteries))
print("        riserva 80 %: {0:.1f} min utili, {1} batterie".format(
    strict_budget / 60.0, strict.stats.n_batteries))
check_true("la riserva e' quella chiesta, non quella del profilo",
           abs(strict_plan["reserve_pct"] - 80.0) < 1e-9)
check("...e il budget che divide le tratte la rispetta",
      strict_budget, loose_budget * 0.2, 1e-6)
check_true("...quindi non servono meno batterie di prima",
           strict.stats.n_batteries >= loose.stats.n_batteries)
panel.reserve_pct.setValue(-1.0)
QGS.processEvents()
context.generate_button.click()
QGS.processEvents()
mission = panel.last_mission

# --------------------------------------------------------------------------
# G8 - regenerating replaces, it does not stack
# --------------------------------------------------------------------------
print("\n== G8: rigenerare sostituisce, non impila ==")
goto(up.STEP_CAPTURE)
panel.sidelap.setValue(80.0)
QGS.processEvents()
context.generate_button.click()
QGS.processEvents()
again = panel.last_mission
print("        prima {0} strisciate, ora {1}".format(
    mission.stats.n_strips, again.stats.n_strips))
check_true("una nuova missione ha sostituito la precedente",
           again is not None and again is not mission)
check_true("...ed e' davvero diversa",
           again.stats.n_strips != mission.stats.n_strips)
check_true("il simulatore ha adottato quella nuova",
           context.player.mission is again)
check("...e il conteggio degli scatti la segue",
      context.player.photo_count, len(again.photos))

context.preview_button.click()
QGS.processEvents()
counted = {}
for layer in QgsProject.instance().mapLayers().values():
    counted[layer.name()] = counted.get(layer.name(), 0) + 1
duplicated = {name: n for name, n in counted.items()
              if n > 1 and name in ("flight_lines", "waypoints",
                                    "photo_centers", "photo_footprints")}
print("        layer duplicati: {0}".format(duplicated or "nessuno"))
check("una seconda anteprima non lascia un secondo gruppo",
      len(duplicated), 0)
check("il layer dei waypoint porta quelli nuovi",
      panel._preview_layers["waypoints"].featureCount(),
      len(again.waypoints))

# --------------------------------------------------------------------------
# G9 - export, at the end of the path
# --------------------------------------------------------------------------
print("\n== G9: export ==")
goto(up.STEP_EXPORT)
check_true("lo step Export mostra il pannello di esportazione",
           visible_in(context.stack.currentWidget(), context.export_panel))
export_panel = context.export_panel
export_panel.refresh()
export_panel.folder_edit.setText(TMP)
export_panel.basename_edit.setText("missione_gui")
for row in range(export_panel.format_list.count()):
    entry = export_panel.format_list.item(row)
    if not entry.flags() & Qt.ItemFlag.ItemIsUserCheckable:
        continue
    entry.setCheckState(
        Qt.CheckState.Checked
        if entry.data(Qt.ItemDataRole.UserRole) == "geojson"
        else Qt.CheckState.Unchecked)
written = export_panel.export()
print("        scritti: {0}".format([os.path.basename(p) for p in written]))
check_true("l'export scrive davvero dei file",
           bool(written) and all(os.path.getsize(p) > 0 for p in written))
report_path = context.write_mission_report(
    path=os.path.join(TMP, "missione.pdf"), key="pdf")
check_true("la relazione di missione pure",
           bool(report_path) and os.path.exists(report_path)
           and os.path.getsize(report_path) > 0)

print("\n" + "=" * 78)
workspace.unmount()
workspace = context = panel = player = dock = state = None
mission = again = strict = None
DEM_LAYER = None
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
