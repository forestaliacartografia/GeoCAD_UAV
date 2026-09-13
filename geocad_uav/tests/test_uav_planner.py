"""
v1.31.0: an azimuth that is measured, and a flight that comes from the plan.

Two things are under test here.

**The azimuth sweep.** Until now the strip orientation was the long axis of
the oriented bounding box -- a good guess, and on a concave block a wrong
one. The sweep lays the strips out at every orientation over a half turn,
costs each layout on its real clipped geometry with the same time model the
mission itself reports, and returns the cheapest. The check below does not
trust the returned winner: it recomputes the cost of every candidate and
insists the winner really is the minimum, and it measures the saving against
the bounding box on a shape where the box is misleading.

**The join with the planting design.** A reforestation project already knows
the surface, the exclusions, the row orientation and the DEM. The link reads
them and returns flight parameters; the test builds a whole mission through
it and then checks the thing that makes terrain following real -- that the Z
of the waypoints moves with the ground, and by the amount the ground moves.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_uav_planner.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import QgsApplication, QgsGeometry                # noqa: E402

QGS = QgsApplication([], False)
QGS.initQgis()

from geocad_uav.core import constants as K                       # noqa: E402
from geocad_uav.core import z as zc                              # noqa: E402
from geocad_uav.core.models import AltitudeMode, VerticalDatum   # noqa: E402
from geocad_uav.forest.reforestation import area as area_mod     # noqa: E402
from geocad_uav.uav import cameras as cam_lib                    # noqa: E402
from geocad_uav.uav import drones as drone_lib                   # noqa: E402
from geocad_uav.uav import forest_link as fl                     # noqa: E402
from geocad_uav.uav import mission as mi                         # noqa: E402
from geocad_uav.uav import photogrammetry as pg                  # noqa: E402
from geocad_uav.uav import survey as sv                          # noqa: E402

FAILURES = []
SKIPS = []


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


def refuses(call):
    try:
        call()
    except Exception:                                           # noqa: BLE001
        return True
    return False


# ------------------------------------------------------------- fixtures ---
ROUTE = dict(d_side_m=40.0, d_front_m=20.0, footprint_across_m=100.0,
             footprint_along_m=75.0, user_margin_m=0.0,
             pattern=sv.PATTERN_BOUSTROPHEDON, turn_radius_m=0.0,
             lead_in_m=0.0)
V_MS = 10.0

#: Two blocks joined at a waist. The bounding box of it is nearly square and
#: its long axis says nothing useful about where the strips should run.
HOURGLASS = QgsGeometry.fromWkt(
    "POLYGON((0 0,900 400,900 500,0 900,0 800,700 450,0 100,0 0))")
BLOCK = QgsGeometry.fromWkt("POLYGON((0 0,600 0,600 400,0 400,0 0))")

print("=" * 78)
print("UAV: azimut misurato, e la missione che nasce dal progetto forestale")
print("=" * 78)

# --------------------------------------------------------------------------
# A1 - every orientation is costed, and the winner is the cheapest
# --------------------------------------------------------------------------
print("\n== A1: la scansione costa ogni orientamento ==")
azimuth, note, scores = sv.optimise_azimuth(HOURGLASS, V_MS, **ROUTE)
print("        {0}".format(note))
check_true("la scansione copre mezzo giro a 5 gradi", len(scores) >= 36)
check_true("ogni candidato porta il suo costo",
           all(s.time_s > 0 and s.n_strips > 0 for s in scores))

# Recomputed here, from the same numbers, without asking the optimiser.
def own_cost(score):
    return ((score.survey_length_m + score.transit_length_m) / V_MS
            + K.TURN_PENALTY_S * score.n_turns)


check_true("il costo e' lunghezza/velocita' piu' penalita' di virata",
           all(abs(own_cost(s) - s.time_s) <= 1e-9 for s in scores))
cheapest = min(scores, key=lambda s: s.time_s)
check("l'azimut restituito e' quello del candidato piu' economico",
      azimuth, cheapest.azimuth_deg, 1e-9)
check_true("...e nessun candidato costa meno",
           all(s.time_s >= cheapest.time_s - 1e-9 for s in scores))

# The plan really flown at that azimuth must cost what the sweep said.
flown = sv.plan_route(HOURGLASS, azimuth_strategy=sv.AZIMUTH_MANUAL,
                      manual_azimuth_deg=azimuth, **ROUTE)
replay = sv.score_azimuth(flown, V_MS)
check("ripianificando a quell'azimut il costo coincide", replay.time_s,
      cheapest.time_s, 1e-6)

# --------------------------------------------------------------------------
# A2 - and on a concave block it beats the bounding box, measurably
# --------------------------------------------------------------------------
print("\n== A2: sul blocco concavo batte il rettangolo circoscritto ==")
obb_az, obb_note = sv.choose_azimuth(HOURGLASS, sv.AZIMUTH_LONGEST_SIDE)
obb = sv.score_azimuth(
    sv.plan_route(HOURGLASS, azimuth_strategy=sv.AZIMUTH_MANUAL,
                  manual_azimuth_deg=obb_az, **ROUTE), V_MS)
print("        rettangolo: az={0:.0f} deg, {1} virate, {2:.0f} s".format(
    obb_az, obb.n_turns, obb.time_s))
print("        scansione : az={0:.0f} deg, {1} virate, {2:.0f} s".format(
    azimuth, cheapest.n_turns, cheapest.time_s))
saved = obb.time_s - cheapest.time_s
print("        risparmio : {0:.0f} s ({1:.1f} %)".format(
    saved, 100.0 * saved / obb.time_s))
check_true("la scansione non fa mai peggio del rettangolo", saved >= -1e-9)
check_true("...e qui fa molto meglio: oltre il 10 % del tempo di volo",
           saved > 0.10 * obb.time_s)
check_true("...con meno virate", cheapest.n_turns < obb.n_turns)

# On a plain rectangle the two agree: the sweep is not a different answer,
# it is the same answer arrived at by measuring.
rect_az, _ = sv.choose_azimuth(BLOCK, sv.AZIMUTH_LONGEST_SIDE)
rect_best, _note, rect_scores = sv.optimise_azimuth(BLOCK, V_MS, **ROUTE)
rect_obb = sv.score_azimuth(
    sv.plan_route(BLOCK, azimuth_strategy=sv.AZIMUTH_MANUAL,
                  manual_azimuth_deg=rect_az, **ROUTE), V_MS)
rect_won = min(rect_scores, key=lambda s: s.time_s)
print("        rettangolo semplice: obb {0:.0f} s, scansione {1:.0f} s"
      .format(rect_obb.time_s, rect_won.time_s))
check_true("su un rettangolo le due risposte coincidono in pratica",
           abs(rect_obb.time_s - rect_won.time_s) < 0.01 * rect_obb.time_s)

# --------------------------------------------------------------------------
# A3 - the strategy cannot be resolved where it has no numbers to measure
# --------------------------------------------------------------------------
print("\n== A3: la strategia rifiuta di fingere ==")
check_true("choose_azimuth non inventa un ottimo senza la geometria di presa",
           refuses(lambda: sv.choose_azimuth(BLOCK, sv.AZIMUTH_OPTIMISED)))
check_true("una velocita' nulla e' rifiutata",
           refuses(lambda: sv.optimise_azimuth(BLOCK, 0.0, **ROUTE)))
check_true("un passo di scansione nullo pure",
           refuses(lambda: sv.sweep_azimuths(BLOCK, V_MS, 0.0, **ROUTE)))

# --------------------------------------------------------------------------
# fixtures for the mission half: a DEM with real relief
# --------------------------------------------------------------------------
CELL = 5.0
OX, OY = 500000.0, 5000000.0
NX, NY = 160, 140
GT = (OX, CELL, 0.0, OY, 0.0, -CELL)
xs = OX + (np.arange(NX) + 0.5) * CELL
ys = OY - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(xs, ys)
# A 15 % slope with a 50 m knoll on it: a block no single AMSL height fits.
Z = (420.0 + 0.15 * (XX - OX)
     + 50.0 * np.exp(-(((XX - OX - 380.0) ** 2 + (YY - OY + 320.0) ** 2)
                       / (2.0 * 110.0 ** 2))))
TERRAIN = zc.TerrainModel(Z, GT, "EPSG:32632", source="DTM sintetico",
                          is_surface_model=False,
                          vertical_datum=VerticalDatum.ORTHOMETRIC_EGM96)
CAM = cam_lib.load_library()["dji_mavic3e"]
DRONE = drone_lib.load_library()["dji_mavic3e"]

PLOT = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 100.0, OY - 560.0, OX + 620.0, OY - 140.0))
ROAD = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 300.0, OY - 560.0, OX + 330.0, OY - 140.0))

# --------------------------------------------------------------------------
# F1 - the planting area read into a survey
# --------------------------------------------------------------------------
print("\n== F1: il progetto forestale diventa un'area di volo ==")
area = area_mod.ReforestationArea(PLOT, "EPSG:32632", "Particella di prova")
area.add_exclusion(ROAD, label="Strada, fascia 5 m", source="strada")
survey = fl.survey_from_area(area, "EPSG:32632", row_azimuth_deg=42.0,
                             plant_count=1234)

check("l'area di volo e' la superficie utile, non la lorda",
      survey.area_m2, area.utile_m2, 1e-6)
check_true("...ed e' davvero piu' piccola della lorda",
           survey.area_m2 < area.lorda_m2 - 1.0)
check("l'esclusione arriva come ostacolo disponibile",
      len(survey.obstacles), 1)
check_true("...con la sua provenienza", survey.obstacles[0].source == "strada")
check("...e la sua superficie", survey.obstacles[0].area_m2,
      ROAD.intersection(PLOT).area(), 1.0)
check("l'orientamento dei filari viaggia con l'area",
      survey.row_azimuth_deg, 42.0, 1e-9)
check_true("il CRS pure", survey.crs_authid == "EPSG:32632")
print("        " + " | ".join(survey.describe()[:2]))
check_true("un'area vuota e' un rifiuto, non una missione vuota",
           refuses(lambda: fl.survey_from_area(None)))

# --------------------------------------------------------------------------
# F2 - the forestry defaults, and what they refuse
# --------------------------------------------------------------------------
print("\n== F2: i parametri forestali vengono dalla tabella, non da qui ==")
params = fl.mission_params(survey, CAM, DRONE, h_agl_m=90.0)
preset = pg.OVERLAP_PRESETS[fl.FOREST_OVERLAP_KEY]
check("la sovrapposizione longitudinale e' quella del preset",
      params.overlap.frontlap, preset.frontlap, 1e-12)
check("...e la laterale pure", params.overlap.sidelap, preset.sidelap, 1e-12)
check_true("il terrain following e' sempre acceso",
           params.altitude_mode == AltitudeMode.TERRAIN)
check_true("le strisciate seguono i filari quando ce n'e' l'orientamento",
           params.azimuth_strategy == sv.AZIMUTH_MANUAL)
check("...esattamente su quell'azimut", params.manual_azimuth_deg, 42.0, 1e-9)
check("nessuna maggiorazione per la vegetazione viene aggiunta da sola",
      params.vegetation_clearance_m, 0.0, 1e-12)

swept = fl.mission_params(survey, CAM, DRONE, gsd_m=0.03, follow_rows=False)
check_true("senza i filari l'azimut e' quello misurato",
           swept.azimuth_strategy == sv.AZIMUTH_OPTIMISED)
check("...e la quota la decide il GSD richiesto", swept.gsd_m, 0.03, 1e-12)
check_true("quota e GSD insieme sono un rifiuto",
           refuses(lambda: fl.mission_params(survey, CAM, DRONE,
                                             h_agl_m=90.0, gsd_m=0.03)))
check_true("ne' l'una ne' l'altro pure",
           refuses(lambda: fl.mission_params(survey, CAM, DRONE)))

# --------------------------------------------------------------------------
# F3 - a whole mission, from the planting plan
# --------------------------------------------------------------------------
print("\n== F3: dalla particella alla rotta, in un passaggio ==")
flight = mi.build_mission(survey.aoi_geom, TERRAIN,
                          fl.mission_params(survey, CAM, DRONE, h_agl_m=90.0,
                                            follow_rows=False),
                          crs_authid="EPSG:32632")
print("        {0} waypoint, {1} scatti, {2} strisciate, {3:.0f} s".format(
    len(flight.waypoints), len(flight.photos), flight.stats.n_strips,
    flight.stats.flight_time_s))
check_true("la missione esiste", flight.is_valid)
check_true("l'azimut e' quello scelto dalla scansione",
           bool(flight.azimuth_scores))
best = min(flight.azimuth_scores, key=lambda s: s.time_s)
check("...ed e' il migliore fra quelli provati", flight.azimuth_deg,
      best.azimuth_deg, 1e-9)
check_true("la missione dichiara di averlo misurato",
           any("ottimizzato" in line.lower() for line in flight.assumptions))

# --------------------------------------------------------------------------
# T1 - the gate: terrain following really moves the Z of the waypoints
# --------------------------------------------------------------------------
print("\n== T1: la Z dei waypoint segue davvero il terreno ==")
xy = np.array([[w.x, w.y] for w in flight.waypoints], dtype=float)
z_flight = np.array([w.z_amsl for w in flight.waypoints], dtype=float)
z_ground = TERRAIN.sample(xy[:, 0], xy[:, 1])
valid = np.isfinite(z_ground)
check_true("il DEM risponde su ogni waypoint", bool(valid.all()))

agl = z_flight[valid] - z_ground[valid]
ground_span = float(np.nanmax(z_ground[valid]) - np.nanmin(z_ground[valid]))
flight_span = float(np.nanmax(z_flight[valid]) - np.nanmin(z_flight[valid]))
print("        terreno: {0:.1f} m di dislivello | volo: {1:.1f} m".format(
    ground_span, flight_span))
print("        AGL: min {0:.2f} max {1:.2f} media {2:.2f}".format(
    float(agl.min()), float(agl.max()), float(agl.mean())))
check_true("il terreno sotto la rotta ha un dislivello vero",
           ground_span > 50.0)
check_true("la quota di volo si muove insieme a lui, non resta piatta",
           flight_span > 0.8 * ground_span)
check("l'AGL medio e' la quota richiesta", float(agl.mean()), 90.0, 1.0)
check_true("...e nessun waypoint se ne discosta piu' di un paio di metri",
           float(np.abs(agl - 90.0).max()) < 3.0)
check_true("la quota di volo non e' mai sotto terra",
           bool((z_flight[valid] > z_ground[valid]).all()))

# The same route with the terrain shifted up must come out shifted up too:
# a profile that ignored the DEM would not move at all.
LIFTED = zc.TerrainModel(Z + 100.0, GT, "EPSG:32632",
                         source="DTM sintetico, alzato di 100 m",
                         is_surface_model=False,
                         vertical_datum=VerticalDatum.ORTHOMETRIC_EGM96)
lifted = mi.build_mission(
    survey.aoi_geom, LIFTED,
    fl.mission_params(survey, CAM, DRONE, h_agl_m=90.0, follow_rows=True),
    crs_authid="EPSG:32632")
same = mi.build_mission(
    survey.aoi_geom, TERRAIN,
    fl.mission_params(survey, CAM, DRONE, h_agl_m=90.0, follow_rows=True),
    crs_authid="EPSG:32632")
check("alzando il DEM di 100 m si alza di 100 m anche la rotta",
      float(np.mean([w.z_amsl for w in lifted.waypoints])
            - np.mean([w.z_amsl for w in same.waypoints])), 100.0, 0.5)
check_true("...e la pianta della rotta e' identica",
           len(lifted.waypoints) == len(same.waypoints))

print("\n" + "=" * 78)
QGS.exitQgis()
if SKIPS:
    print("SKIPPED ({0}):".format(len(SKIPS)))
    for name, reason in SKIPS:
        print("   - {0}: {1}".format(name, reason))
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for name in FAILURES:
        print("   - {0}".format(name))
    sys.exit(1)
print("ALL CHECKS PASSED")
