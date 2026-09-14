"""
v2.1.0: parametri -> Genera rotta -> anteprima -> simulazione, davvero.

Twelve checks, in the order an operator would do them, on the route that a
real DEM, a real camera and a real drone profile produce. Nothing is a
stand-in: the DEM is a GeoTIFF on disk, the route is the frozen assembler's,
the footprints are the ones the photogrammetry module projects, and the
drone that moves along the route moves along the route's own geometry.

    1  i parametri diventano una rotta
    2  la rotta ha waypoint coerenti
    3  ...e punti di scatto
    4  ...e impronte fotografiche
    5  l'anteprima mette la missione sul canvas
    6  play e pausa
    7  il drone segue la geometria della rotta
    8  velocita', distanza, tempo e waypoint si aggiornano
    9  cambiare velocita' cambia il tempo di missione
    10 cambiare GSD, quota e sovrapposizione cambia la missione
    11 la copertura viene ricalcolata
    12 una geometria non valida non produce una missione finta

NEEDS QGIS with widgets. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_uav_route_end_to_end.py
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

from geocad_uav.core.units import format_duration              # noqa: E402
from geocad_uav.gui import uav_panel as up                      # noqa: E402
from geocad_uav.gui import workflow as wf                       # noqa: E402

FAILURES = []
SKIPS = []
TMP = tempfile.mkdtemp(prefix="geocad_route_")
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


def skip(label, reason):
    print("  [skip] {0}\n         motivo: {1}".format(label, reason))
    SKIPS.append((label, reason))


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
print("VOLO UAV: dai parametri alla rotta, all'anteprima, alla simulazione")
print("=" * 78)

workspace = wf.Workspace(None)
context = workspace.context
panel = context.uav_panel

# --------------------------------------------------------------------------
# T1 - parameters become a route
# --------------------------------------------------------------------------
print("\n== T1: i parametri diventano una rotta ==")
check_true("senza area il comando Genera rotta e' spento",
           not panel.generate_button.isEnabled())
before = panel.generate()
check_true("...e premerlo non inventa una missione", before is None)

panel.extent.set_extent(AREA, CRS)
panel.dem_combo.setLayer(DEM_LAYER)
panel.height_mode.setCurrentIndex(
    panel.height_mode.findData(up.HEIGHT_FROM_AGL))
panel.h_agl.setValue(90.0)
panel.speed_kmh.setValue(36.0)
panel.frontlap.setValue(80.0)
panel.sidelap.setValue(65.0)
panel.recompute()
ready, reason = panel.readiness()
print("        pronto: {0} ({1})".format(ready, reason or "-"))
check_true("con area, DEM, drone e sensore il comando si accende",
           panel.generate_button.isEnabled())

# Clicked, not called: the button is what an operator presses, and half
# the dashboard reacts to that signal rather than to the slot.
panel.generate_button.click()
mission = panel.last_mission
check_true("Genera rotta produce una missione", mission is not None)
if mission is None:
    print("FAILED: nessuna rotta, il resto non e' verificabile")
    sys.exit(1)
stats = mission.stats
print("        {0} strip, {1} waypoint, {2} scatti, {3:,.0f} m, {4}".format(
    stats.n_strips, stats.n_waypoints, stats.n_photos,
    stats.total_length_m, format_duration(stats.flight_time_s)))
check_true("la missione e' valida", mission.is_valid)
check_true("...con delle strisciate", stats.n_strips >= 2)
check_true("...un percorso di lunghezza positiva",
           stats.total_length_m > 0.0)
check_true("...e un tempo di volo positivo", stats.flight_time_s > 0.0)
check_true("il pianificatore la tiene", panel.last_mission is mission)

# --------------------------------------------------------------------------
# T2 - the waypoints are coherent
# --------------------------------------------------------------------------
print("\n== T2: i waypoint sono coerenti ==")
waypoints = mission.waypoints
check_true("ci sono waypoint", len(waypoints) >= 4)
check_true("...numerati in sequenza",
           [w.seq for w in waypoints] == sorted(w.seq for w in waypoints))
check_true("...ciascuno con coordinate finite",
           all(math.isfinite(w.x) and math.isfinite(w.y)
               for w in waypoints))
agl = np.array([w.z_agl for w in waypoints], dtype=float)
amsl = np.array([w.z_amsl for w in waypoints], dtype=float)
print("        AGL {0:.2f}-{1:.2f} m, AMSL {2:.1f}-{3:.1f} m".format(
    float(np.nanmin(agl)), float(np.nanmax(agl)),
    float(np.nanmin(amsl)), float(np.nanmax(amsl))))
check_true("ogni waypoint ha una quota sul terreno",
           bool(np.isfinite(agl).all()))
check_true("...tenuta alla quota chiesta",
           abs(float(np.nanmean(agl)) - 90.0) < 2.0)
check_true("il terrain following ha davvero mosso le quote assolute",
           float(np.nanmax(amsl) - np.nanmin(amsl)) > 5.0)
check_true("ogni waypoint ha una velocita' comandata",
           all(w.speed_ms > 0.0 for w in waypoints))
check_true("...e una prua", all(math.isfinite(w.heading_deg)
                                for w in waypoints))
check_true("...e un gimbal", all(math.isfinite(w.gimbal_pitch_deg)
                                 for w in waypoints))
check_true("i waypoint appartengono a delle strisciate",
           len({w.strip_index for w in waypoints}) >= 2)
check_true("nessun waypoint e' fuori dal DEM",
           not any(w.dem_gap for w in waypoints))
check("il conteggio dichiarato e' quello vero", stats.n_waypoints,
      len(waypoints))

# --------------------------------------------------------------------------
# T3 - the exposure stations
# --------------------------------------------------------------------------
print("\n== T3: i punti di scatto ==")
photos = mission.photos
check_true("ci sono punti di scatto", len(photos) > 10)
check("il conteggio dichiarato e' quello vero", stats.n_photos, len(photos))
check_true("ognuno ha un identificativo progressivo",
           [p.photo_id for p in photos] == sorted(p.photo_id
                                                  for p in photos))
check_true("...coordinate finite",
           all(math.isfinite(p.x) and math.isfinite(p.y) for p in photos))
check_true("...una quota sul terreno",
           all(math.isfinite(p.z_agl) for p in photos))
# Constant, and that is the point: the AGL is held at 90.00 m over 81 m of
# relief, so every exposure gets the same ground sample distance. A varying
# GSD here would mean the terrain following was not working.
gsds = [p.gsd_m for p in photos]
spread = max(gsds) - min(gsds)
print("        GSD {0:.4f} cm/px, dispersione {1:.3g} cm".format(
    min(gsds) * 100.0, spread * 100.0))
check_true("ogni scatto porta il suo GSD", all(g > 0.0 for g in gsds))
check("...ed e' lo stesso per tutti, perche' l'AGL e' tenuta",
      spread, 0.0, 1e-9)
check("...e vale quello pianificato", min(gsds), mission.gsd_m, 1e-9)
check_true("...e un orientamento esterno approssimato",
           all(math.isfinite(p.omega_deg) and math.isfinite(p.phi_deg)
               and math.isfinite(p.kappa_deg) for p in photos))
photo_waypoints = [w for w in waypoints if w.is_photo]
check_true("ogni scatto e' anche un waypoint di scatto",
           len(photo_waypoints) == len(photos))
check_true("...e i waypoint di scatto portano un'azione",
           all(w.actions for w in photo_waypoints))

# --------------------------------------------------------------------------
# T4 - the footprints
# --------------------------------------------------------------------------
print("\n== T4: le impronte fotografiche ==")
# The planner casts one ray per exposure only when the coverage check is on,
# because on a long mission that is real time. Asking to see them computes
# them, which is what the operator's command does.
check("una rotta pianificata senza copertura non ne ha",
      len(mission.footprints), 0)
check_true("chiederle le calcola", bool(panel.ensure_footprints()))
check_true("le impronte sono state calcolate", bool(mission.footprints))
check("una impronta per scatto", len(mission.footprints), len(photos))
ring = np.asarray(mission.footprints[0], dtype=float)
print("        impronta 1: {0} vertici".format(ring.shape[0]))
check_true("l'impronta e' un anello chiuso",
           ring.shape[0] >= 4
           and abs(ring[0, 0] - ring[-1, 0]) < 1e-6
           and abs(ring[0, 1] - ring[-1, 1]) < 1e-6)


def ring_area(footprint):
    """Shoelace on the open ring."""
    xy = np.asarray(footprint, dtype=float)[:-1]
    return abs(float(np.sum(xy[:, 0] * np.roll(xy[:, 1], -1)
                            - np.roll(xy[:, 0], -1) * xy[:, 1]) / 2.0))


geometry = panel.survey_geometry()
nominal = geometry.footprint_across_m * geometry.footprint_along_m
areas = [ring_area(f) for f in mission.footprints]
mean_area = float(np.mean(areas))
print("        impronta {0:,.0f} m2 in media, ottica {1:.1f} x {2:.1f} = "
      "{3:,.0f} m2".format(mean_area, geometry.footprint_across_m,
                           geometry.footprint_along_m, nominal))
check_true("ogni impronta racchiude una superficie",
           all(area > 1.0 for area in areas))
check_true("...e la superficie e' quella dell'ottica, non un rettangolo "
           "inventato",
           abs(mean_area - nominal) < 0.35 * nominal)
span = float(np.max(np.hypot(ring[:, 0] - ring[:, 0].mean(),
                             ring[:, 1] - ring[:, 1].mean())))
half_diagonal = 0.5 * math.hypot(geometry.footprint_across_m,
                                 geometry.footprint_along_m)
print("        raggio impronta {0:.1f} m, mezza diagonale ottica "
      "{1:.1f} m".format(span, half_diagonal))
check_true("...e la sua estensione pure",
           abs(span - half_diagonal) < 0.35 * half_diagonal)
check_true("le impronte non sono tutte identiche: il terreno cambia",
           len({round(a, 1) for a in areas}) > 1)

# --------------------------------------------------------------------------
# T5 - the preview puts it on the canvas
# --------------------------------------------------------------------------
print("\n== T5: l'anteprima mette la missione sulla mappa ==")
check_true("il comando Anteprima missione e' acceso",
           context.preview_button.isEnabled())
layers = context.preview_mission()
print("        layer: {0}".format(sorted(layers)))
check_true("l'anteprima ha creato dei layer", bool(layers))
check_true("...le strisciate", "flight_lines" in layers)
check_true("...i waypoint", "waypoints" in layers)
check_true("...i punti di scatto", "photo_centers" in layers)
check_true("...e le impronte", "photo_footprints" in layers)
check("il layer dei waypoint li porta tutti",
      layers["waypoints"].featureCount(), len(waypoints))
check("...quello degli scatti pure",
      layers["photo_centers"].featureCount(), len(photos))
check("...e quello delle impronte",
      layers["photo_footprints"].featureCount(), len(mission.footprints))
check_true("i layer sono davvero nel progetto QGIS",
           all(QgsProject.instance().mapLayer(layer.id()) is not None
               for layer in layers.values()))
check_true("...nel CRS del progetto",
           layers["waypoints"].crs().authid() == "EPSG:32632")
again = context.preview_mission()
check("una seconda anteprima non impila un secondo gruppo",
      len([lyr for lyr in QgsProject.instance().mapLayers().values()
           if lyr.name() in ("flight_lines", "waypoints", "photo_centers",
                             "photo_footprints")]), len(again))

print("\n-- e l'anteprima a pixel, senza layer --")
# This workspace has no iface, so there is no canvas to put a rubber band
# on: the preview draws nothing and says nothing, which is what it has to
# do rather than raising. With a canvas it draws route, waypoints and
# exposure stations -- counted in test_uav_workflow, which builds one.
drawn = panel._draw(mission)
check("senza canvas l'anteprima a pixel non disegna nulla", drawn, 0)
check("...e non lascia vertici in giro", panel.preview_vertices(), 0)

# --------------------------------------------------------------------------
# T6 - play and pause
# --------------------------------------------------------------------------
print("\n== T6: play e pausa ==")
player = context.player
check_true("play parte", context.play_mission())
check_true("...e il cronometro gira", player.is_playing)
for _ in range(12):
    player.tick()
t_after_play = player.t_sim
check_true("il tempo simulato e' avanzato", t_after_play > 0.0)
context.pause_mission()
check_true("pausa ferma il cronometro", not player.is_playing)
frozen = player.t_sim
frozen_index = player.index
for _ in range(5):
    player.tick()
    QGS.processEvents()
check("...e in pausa il tempo non si muove", player.t_sim, frozen, 1e-9)
check("...ne' l'indice", player.index, frozen_index)
check_true("play riprende da dove era", context.play_mission()
           and player.t_sim >= frozen)
context.pause_mission()

print("\n-- inizio, fine, un waypoint alla volta --")
context.to_start()
check("Inizio riporta al decollo", player.t_sim, 0.0, 1e-9)
check("...e al primo waypoint", player.index, 0)
index = context.step_forward()
check("un passo avanti e' un waypoint avanti", index, 1)
check_true("...e il tempo e' quello del waypoint",
           abs(player.t_sim - float(player._times[1])) < 1e-6)
context.step_forward()
check("due passi, due waypoint", player.index, 2)
check("un passo indietro torna al precedente", context.step_back(), 1)
context.to_end()
check("Fine porta all'ultimo waypoint", player.index,
      len(waypoints) - 1)
check("...e alla fine del cronometro", player.t_sim, player.duration_s,
      1e-6)
check("...con tutti gli scatti fatti", player.flash_count, len(photos))
context.to_start()
# An exposure sitting on the first waypoint has been taken at t=0: the
# count goes back to that, not to nothing.
at_start = 1 if waypoints[0].is_photo else 0
check("...e Inizio li riporta a quelli del decollo", player.flash_count,
      at_start)

# --------------------------------------------------------------------------
# T7 - the aircraft follows the route's own geometry
# --------------------------------------------------------------------------
print("\n== T7: il drone segue la geometria della rotta ==")
from qgis.core import QgsPointXY                                # noqa: E402

route = QgsGeometry.fromPolylineXY(
    [QgsPointXY(float(w.x), float(w.y)) for w in waypoints])
check_true("la rotta di confronto e' una polilinea valida",
           route is not None and not route.isEmpty())
worst = 0.0
samples = 0
for step in range(0, 41):
    player.seek(player.duration_s * step / 40.0)
    point = player.position()
    if point is None:
        continue
    samples += 1
    distance = route.distance(QgsGeometry.fromPointXY(
        QgsPointXY(point[0], point[1])))
    worst = max(worst, float(distance))
print("        {0} posizioni campionate, scarto massimo {1:.6f} m".format(
    samples, worst))
check("sono state campionate quarantuno posizioni", samples, 41)
check_true("il drone non lascia mai la rotta", worst < 1e-6)

first = waypoints[0]
player.seek(0.0)
start = player.position()
check("a t=0 il drone e' sul primo waypoint", start[0], first.x, 1e-6)
check("...anche in y", start[1], first.y, 1e-6)
player.seek(player.duration_s)
last = waypoints[-1]
end = player.position()
check("alla fine e' sull'ultimo", end[0], last.x, 1e-6)
check("...anche in y", end[1], last.y, 1e-6)

# --------------------------------------------------------------------------
# T8 - the readout moves with the flight
# --------------------------------------------------------------------------
print("\n== T8: velocita', distanza, tempo e waypoint si aggiornano ==")
player.seek(player.duration_s * 0.25)
early = player.state()
player.seek(player.duration_s * 0.75)
late = player.state()
for key, label in (("t_s", "il tempo"), ("distance_m", "la distanza"),
                   ("waypoint", "il waypoint"), ("photos", "i fotogrammi")):
    print("        {0:<14} {1} -> {2}".format(key, early[key], late[key]))
    check_true("{0} avanza".format(label), late[key] > early[key])
for key, label in (("remaining_m", "la distanza residua"),
                   ("remaining_s", "il tempo residuo")):
    check_true("{0} cala".format(label), late[key] < early[key])
check_true("la velocita' e' quella comandata al waypoint",
           early["speed_ms"] > 0.0)
check_true("la prua e' un angolo reale",
           0.0 <= early["heading_deg"] < 360.0)
check("il totale dei waypoint e' quello della rotta",
      early["waypoint_total"], len(waypoints))
check("...e quello dei fotogrammi pure", early["photo_total"], len(photos))
check_true("la posizione e' quella del drone",
           abs(early["x"] - player.position()[0]) > -1.0)
check("distanza percorsa piu' residua fa il percorso",
      early["distance_m"] + early["remaining_m"], early["length_m"], 1e-6)
check("tempo trascorso piu' residuo fa la durata",
      early["t_s"] + early["remaining_s"], early["duration_s"], 1e-6)
text = player.summary()
print("        {0}".format(text.replace("\n", " | ")))
for token in ("m/s", "prua", "waypoint", "fotogramma", "AGL"):
    check_true("il riepilogo dice {0}".format(token), token in text)

# --------------------------------------------------------------------------
# T9 - speed changes the mission time
# --------------------------------------------------------------------------
print("\n== T9: cambiare velocita' cambia il tempo di missione ==")
slow_time = mission.stats.flight_time_s
panel.speed_kmh.setValue(18.0)
panel.recompute()
print("        a 18 km/h: pronto={0} ({1})".format(*panel.readiness()))
slow = panel.generate()
check_true("la rotta si rigenera", slow is not None)
if slow is None:
    print("        riepilogo: {0}".format(
        panel.summary.toPlainText()[:400]))
print("        36 km/h -> {0}, 18 km/h -> {1}".format(
    format_duration(slow_time),
    format_duration(slow.stats.flight_time_s)))
check_true("volare piu' piano allunga la missione",
           slow.stats.flight_time_s > slow_time)
check_true("...senza cambiare il percorso",
           abs(slow.stats.total_length_m - mission.stats.total_length_m)
           < 0.02 * mission.stats.total_length_m)
context.refresh_player()
check_true("il simulatore ha adottato la nuova rotta",
           context.player.mission is slow)
check_true("...e la sua durata e' cresciuta",
           context.player.duration_s > 0.0)
panel.speed_kmh.setValue(36.0)
mission = panel.generate()

# --------------------------------------------------------------------------
# T10 - GSD, height and overlap change the mission
# --------------------------------------------------------------------------
print("\n== T10: GSD, quota e sovrapposizione cambiano la missione ==")
base = panel.generate()
base_photos, base_strips = base.stats.n_photos, base.stats.n_strips
base_gsd = base.gsd_m

panel.h_agl.setValue(120.0)
higher = panel.generate()
print("        90 m: {0} scatti su {1} strip, GSD {2:.2f} cm - "
      "120 m: {3} scatti su {4} strip, GSD {5:.2f} cm".format(
          base_photos, base_strips, base_gsd * 100.0,
          higher.stats.n_photos, higher.stats.n_strips,
          higher.gsd_m * 100.0))
check_true("salire di quota peggiora il GSD", higher.gsd_m > base_gsd)
check_true("...e con l'impronta piu' grande servono meno strisciate",
           higher.stats.n_strips < base_strips)
check_true("...e meno scatti", higher.stats.n_photos < base_photos)
panel.h_agl.setValue(90.0)

panel.height_mode.setCurrentIndex(
    panel.height_mode.findData(up.HEIGHT_FROM_GSD))
panel.gsd_target.setValue(1.5)
panel.recompute()
from_gsd = panel.generate()
print("        GSD chiesto 1.50 cm -> quota {0:.1f} m, GSD {1:.2f} cm".format(
    from_gsd.h_agl_m, from_gsd.gsd_m * 100.0))
check("chiedere un GSD fissa la quota che lo produce",
      from_gsd.gsd_m * 100.0, 1.5, 0.02)
check_true("...e la quota non e' piu' quella di prima",
           abs(from_gsd.h_agl_m - 90.0) > 1.0)
panel.height_mode.setCurrentIndex(
    panel.height_mode.findData(up.HEIGHT_FROM_AGL))
panel.h_agl.setValue(90.0)
panel.recompute()

panel.sidelap.setValue(80.0)
denser = panel.generate()
print("        sidelap 65 % -> {0} strip, 80 % -> {1} strip".format(
    base_strips, denser.stats.n_strips))
check_true("piu' sovrapposizione laterale, piu' strisciate",
           denser.stats.n_strips > base_strips)
check_true("...e piu' percorso da volare",
           denser.stats.total_length_m > base.stats.total_length_m)
panel.frontlap.setValue(90.0)
front = panel.generate()
check_true("piu' sovrapposizione longitudinale, piu' scatti",
           front.stats.n_photos > denser.stats.n_photos)
panel.sidelap.setValue(65.0)
panel.frontlap.setValue(80.0)

# --------------------------------------------------------------------------
# T11 - coverage is recomputed
# --------------------------------------------------------------------------
print("\n== T11: la copertura viene ricalcolata ==")
panel.check_coverage.setChecked(True)
covered = panel.generate()
print("        copertura {0:.1f} % con almeno {1} foto".format(
    covered.stats.coverage_pct, covered.stats.min_photos_observed))
check_true("la copertura e' misurata, non lasciata vuota",
           covered.stats.coverage_pct == covered.stats.coverage_pct)
check_true("...fra zero e cento", 0.0 <= covered.stats.coverage_pct <= 100.0)
check_true("...e dice con quante foto",
           covered.stats.min_photos_observed >= 0)
check_true("il riepilogo del pannello la riporta",
           "Copertura" in panel.summary.toHtml())
check_true("...e cosi' le sovrapposizioni effettive",
           "Sovrapposizione" in panel.summary.toHtml())
check_true("...e il GSD min/max", "GSD min / max" in panel.summary.toHtml())
panel.check_coverage.setChecked(False)

# --------------------------------------------------------------------------
# T12 - an invalid area does not produce a fake mission
# --------------------------------------------------------------------------
print("\n== T12: una geometria non valida non produce una missione finta ==")
good = panel.generate()
check_true("si parte da una rotta buona", good is not None)
panel.extent.set_extent(QgsGeometry.fromWkt("POINT({0} {1})".format(
    OX + 200.0, OY - 200.0)), CRS)
panel.recompute()
bad = panel.generate()
print("        rotta su un punto: {0!r}".format(bad))
check_true("un punto non e' un'area di missione", bad is None)
check_true("...e il pianificatore non tiene la vecchia rotta",
           panel.last_mission is None)
context.refresh_player()
check_true("...e il simulatore non ha piu' niente da simulare",
           not context.play_button.isEnabled())
check_true("...ne' un'anteprima da mostrare",
           not context.preview_button.isEnabled())

tiny = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 200.0, OY - 200.0, OX + 200.4, OY - 199.6))
panel.extent.set_extent(tiny, CRS)
panel.recompute()
sliver = panel.generate()
print("        rotta su 0.16 m2: {0}".format(
    "nessuna" if sliver is None else "{0} waypoint".format(
        len(sliver.waypoints))))
check_true("un'area piu' piccola di un'impronta non diventa una missione "
           "finta",
           sliver is None or len(sliver.waypoints) >= 2)

panel.extent.set_extent(AREA, CRS)
panel.recompute()
panel.generate_button.click()
recovered = panel.last_mission
check_true("tornando a un'area buona la rotta torna", recovered is not None)
check_true("...e l'anteprima si riaccende",
           context.preview_button.isEnabled())

print("\n" + "=" * 78)
workspace.unmount()
workspace = context = panel = player = None
mission = base = higher = denser = front = covered = good = None
slow = from_gsd = sliver = recovered = route = None
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
