"""
v1.3.4: mission playback.

The player is checked against the mission it was handed, never against a
formula written here. The expected duration comes from one call to the frozen
``uav.terrain_follow.segment_flight_time`` over the whole route; the expected
number of flashes is ``len(mission.photos)``. The clock is driven by calling
the player's own ``tick()`` slot, so nothing sleeps and nothing depends on
wall-clock timing.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_player.py
"""

import ast
import copy
import os
import re
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import QgsApplication                            # noqa: E402

QGS = QgsApplication([], False)
QGS.initQgis()

from osgeo import gdal, osr                                     # noqa: E402
from qgis.core import (QgsCoordinateReferenceSystem, QgsGeometry,  # noqa: E402
                       QgsProject, QgsRasterLayer, QgsRectangle)
from qgis.gui import QgsMapCanvas                               # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow                     # noqa: E402

from geocad_uav.core.models import AltitudeMode                 # noqa: E402
from geocad_uav.core.z import TerrainModel                      # noqa: E402
from geocad_uav.gui import mission_player as mp                 # noqa: E402
from geocad_uav.gui.mission_player import MissionPlayer         # noqa: E402
from geocad_uav.uav import cameras as cam_lib                   # noqa: E402
from geocad_uav.uav import drones as drone_lib                  # noqa: E402
from geocad_uav.uav import mission as mi                        # noqa: E402
from geocad_uav.uav import photogrammetry as pg                 # noqa: E402
from geocad_uav.uav import survey as sv                         # noqa: E402
from geocad_uav.uav import terrain_follow as tf                 # noqa: E402

FAILURES = []
SKIPS = []
TMP = tempfile.mkdtemp(prefix="geocad_player_")
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
    print("  [skip] {0}\n         reason: {1}".format(label, reason))
    SKIPS.append((label, reason))


def run_to_end(player, limit=200_000):
    """Drive the player's own slot until it stops. No sleeping, no timer."""
    ticks = 0
    while player.t_sim < player.duration_s and ticks < limit:
        player.tick()
        ticks += 1
    return ticks


# ------------------------------------------------------------- fixtures ---
CELL, NX, NY = 5.0, 240, 200
xs = OX + (np.arange(NX) + 0.5) * CELL
ys = OY - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(xs, ys)
Z = 300.0 + 0.08 * (XX - OX)

DEM_PATH = os.path.join(TMP, "ramp.tif")
gdal.UseExceptions()
_ds = gdal.GetDriverByName("GTiff").Create(DEM_PATH, NX, NY, 1, gdal.GDT_Float32)
_ds.SetGeoTransform((OX, CELL, 0.0, OY, 0.0, -CELL))
_srs = osr.SpatialReference()
_srs.ImportFromEPSG(32632)
_ds.SetProjection(_srs.ExportToWkt())
_ds.GetRasterBand(1).WriteArray(Z.astype(np.float32))
_ds.FlushCache()
_ds = None

DEM_LAYER = QgsRasterLayer(DEM_PATH, "dem ramp", "gdal")
AOI = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 100.0, OY - 700.0, OX + 600.0, OY - 400.0))

_camera = cam_lib.load_library()["dji_mavic3e"]
_drone = drone_lib.load_library()["dji_mavic3e"]
_geometry = pg.solve_survey_geometry(_camera, pg.Overlap(0.80, 0.70),
                                     h_agl_m=80.0)
_box = AOI.boundingBox()
TERRAIN, _warn = TerrainModel.from_layer(
    DEM_LAYER, CRS,
    (_box.xMinimum(), _box.yMinimum(), _box.xMaximum(), _box.yMaximum()),
    margin_m=max(_geometry.footprint_across_m, _geometry.footprint_along_m))
MISSION = mi.build_mission(
    sv.prepare_aoi([AOI])[0][0], TERRAIN,
    mi.MissionParams(
        camera=_camera, drone=_drone, overlap=pg.Overlap(0.80, 0.70),
        h_agl_m=80.0, altitude_mode=AltitudeMode.TERRAIN, safety_margin_m=5.0,
        azimuth_strategy=sv.AZIMUTH_MANUAL, manual_azimuth_deg=0.0,
        v_mission_ms=10.0, compute_footprints=False),
    crs_authid=CRS.authid())

WP_XY = np.array([[w.x, w.y] for w in MISSION.waypoints], dtype=float)
WP_Z = np.array([w.z_amsl for w in MISSION.waypoints], dtype=float)
WP_V = np.array([w.speed_ms for w in MISSION.waypoints], dtype=float)

#: The reference duration: one call to the frozen function, whole route.
EXPECTED_DURATION = tf.segment_flight_time(WP_XY, WP_Z, WP_V[:-1])


class CountingCanvas(QgsMapCanvas):
    def __init__(self):
        super().__init__()
        self.refresh_calls = 0

    def refresh(self):                                          # noqa: N802
        self.refresh_calls += 1
        super().refresh()


class FakeIface:
    def __init__(self):
        self._window = QMainWindow()
        self._canvas = CountingCanvas()
        self._canvas.setDestinationCrs(CRS)
        self._canvas.setExtent(QgsRectangle(OX, OY - 900, OX + 800, OY))

    def mainWindow(self):                                       # noqa: N802
        return self._window

    def mapCanvas(self):                                        # noqa: N802
        return self._canvas

    def messageBar(self):                                       # noqa: N802
        return None


iface = FakeIface()
canvas = iface.mapCanvas()
print("\n== fixture ==")
print("  {0} waypoints, {1} photos, {2} legs".format(
    len(MISSION.waypoints), len(MISSION.photos), len(MISSION.lines)))
print("  path time from segment_flight_time : {0:.9f} s".format(
    EXPECTED_DURATION))
print("  mission.stats.flight_time_s        : {0:.9f} s".format(
    MISSION.stats.flight_time_s))

# --------------------------------------------------------------------------
# S1 - one flash per exposure, over the whole route
# --------------------------------------------------------------------------
print("\n== S1: flashes == photo centres ==")
player = MissionPlayer(iface)
check_true("the mission loads", player.play(MISSION))
ticks = run_to_end(player)
print("        {0} ticks, {1} flashes, {2} photos".format(
    ticks, player.flash_count, len(MISSION.photos)))
check("flashes at the end of the route", player.flash_count,
      len(MISSION.photos))
check("every exposure fired once", len(set(player.flashed)),
      len(player.flashed))
check("the walk ended on the last waypoint", player.index,
      len(MISSION.waypoints) - 1)
check("progress reached the end", player.progress(), 1.0, 1e-12)
check_true("the timer stopped by itself", not player.is_playing)
check("photo waypoints and photo centres agree",
      sum(1 for w in MISSION.waypoints if w.kind == "photo"),
      len(MISSION.photos))

# --------------------------------------------------------------------------
# S2 - the duration is the engine's, not the player's
# --------------------------------------------------------------------------
print("\n== S2: duration == segment_flight_time over the route ==")
print("        signature: segment_flight_time(xy, z, speed_ms) -> float")
check("player.duration_s", player.duration_s, EXPECTED_DURATION, 1e-6)
check("simulated time at the end", player.t_sim, EXPECTED_DURATION, 1e-6)

per_leg = sum(
    tf.segment_flight_time(np.asarray(line, dtype=float)[:, :2],
                           np.asarray(line, dtype=float)[:, 2],
                           WP_V[start:start + len(line) - 1])
    for line, start in zip(
        MISSION.lines,
        np.cumsum([0] + [len(line) for line in MISSION.lines[:-1]])))
check_true("the legs alone are shorter than the whole route (transits count)",
           per_leg < player.duration_s)

PLAYER_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "gui", "mission_player.py")
source = open(PLAYER_PATH, encoding="utf-8").read()
PLAYER_AST = ast.parse(source)


def code_only(text):
    """The module without docstrings or comments: prose is not evidence."""
    stripped = re.sub(r"#.*", "", text)
    return re.sub(r'"""(?:.|\n)*?"""', "", stripped)


def imported_modules(tree):
    """Every module this file actually imports, relative ones spelled out."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            for alias in node.names:
                names.add("{0}.{1}".format(base, alias.name) if base
                          else alias.name)
                names.add(base)
    return {name for name in names if name}


PLAYER_CODE = code_only(source)
PLAYER_IMPORTS = imported_modules(PLAYER_AST)
print("        imports: {0}".format(sorted(PLAYER_IMPORTS)))

check_true("the player calls the frozen timing function",
           "tf.segment_flight_time(" in PLAYER_CODE)
check_true("no distance-over-speed arithmetic in the player",
           not any(token in PLAYER_CODE for token in
                   ("/ speed", "/speed", "hypot", "np.diff", "np.sqrt")))
check_true("the only timing call is the frozen one",
           PLAYER_CODE.count("segment_flight_time") == 1)
check_true("mission.stats.flight_time_s is reported, not recomputed",
           "mission.stats.flight_time_s" in PLAYER_CODE)
check_true("the two clocks differ by the ground overhead only",
           MISSION.stats.flight_time_s > player.duration_s)
print("        overhead reported separately: {0:.1f} s".format(
    MISSION.stats.flight_time_s - player.duration_s))

# --------------------------------------------------------------------------
# S3 - the rate touches the clock and nothing else
# --------------------------------------------------------------------------
print("\n== S3: 2x is faster, the mission is untouched ==")
before = copy.deepcopy(MISSION.waypoints)
before_wkt = [(w.x, w.y, w.z_amsl, w.z_agl, w.speed_ms, w.kind)
              for w in MISSION.waypoints]
before_stats = MISSION.stats.flight_time_s

player_1x = MissionPlayer(iface)
player_1x.play(MISSION)
ticks_1x = run_to_end(player_1x)

player_2x = MissionPlayer(iface)
player_2x.set_rate(2)
player_2x.play(MISSION)
ticks_2x = run_to_end(player_2x)

print("        1x: {0} ticks, 2x: {1} ticks".format(ticks_1x, ticks_2x))
check("2x needs half the ticks", ticks_2x, (ticks_1x + 1) // 2, 1.0)
check("2x covers the same simulated duration", player_2x.duration_s,
      player_1x.duration_s, 1e-12)
check("2x fires the same number of flashes", player_2x.flash_count,
      player_1x.flash_count)
check("the rate is stored", player_2x.rate, 2)

player_5x = MissionPlayer(iface)
player_5x.set_rate(5)
player_5x.play(MISSION)
ticks_5x = run_to_end(player_5x)
check("5x needs a fifth of the ticks", ticks_5x, (ticks_1x + 4) // 5, 1.0)
check("5x still fires every flash", player_5x.flash_count,
      len(MISSION.photos))
check("an unknown rate falls back to 1x", player_5x.set_rate(10), 1)

after_wkt = [(w.x, w.y, w.z_amsl, w.z_agl, w.speed_ms, w.kind)
             for w in MISSION.waypoints]
check_true("every waypoint is byte-identical after playback",
           after_wkt == before_wkt)
check("the waypoint count is unchanged", len(MISSION.waypoints), len(before))
check("stats.flight_time_s is unchanged", MISSION.stats.flight_time_s,
      before_stats, 0.0)
check("the AGL column is unchanged",
      float(np.max(np.abs(
          np.array([w.z_agl for w in MISSION.waypoints])
          - np.array([w.z_agl for w in before])))), 0.0)
check("the photo list is unchanged", len(MISSION.photos), len(MISSION.photos))

# --------------------------------------------------------------------------
# S4 - pause and resume
# --------------------------------------------------------------------------
print("\n== S4: play -> pause -> play resumes where it stopped ==")
resume = MissionPlayer(iface)
resume.play(MISSION)
for _ in range(40):
    resume.tick()
index_at_pause = resume.index
t_at_pause = resume.t_sim
flashes_at_pause = resume.flash_count
resume.pause()
check_true("the timer really stopped", not resume.is_playing)

for _ in range(5):
    pass                                # wall time passes, the clock does not
check("the clock did not move while paused", resume.t_sim, t_at_pause, 0.0)
check("the index did not move", resume.index, index_at_pause)

check_true("resuming keeps the mission", resume.play())
check("the index is the same after resuming", resume.index, index_at_pause)
check("the clock is the same after resuming", resume.t_sim, t_at_pause, 0.0)
check("the flashes so far are kept", resume.flash_count, flashes_at_pause)
resume.tick()
check_true("and it moves on from there", resume.t_sim > t_at_pause)

before_layers = len(QgsProject.instance().mapLayers())
run_to_end(resume)
check("resuming to the end still fires every flash", resume.flash_count,
      len(MISSION.photos))
check("no layer was created by the whole playback",
      len(QgsProject.instance().mapLayers()), before_layers)

# --------------------------------------------------------------------------
# S5 - stop clears the canvas
# --------------------------------------------------------------------------
print("\n== S5: stop leaves nothing behind ==")
layers_before = len(QgsProject.instance().mapLayers())
refresh_before = canvas.refresh_calls
stopper = MissionPlayer(iface)
stopper.play(MISSION)
for _ in range(30):
    stopper.tick()
check_true("something is drawn while playing", stopper.band_vertices() > 0)

stopper.stop()
check("nothing is left on the rubber bands", stopper.band_vertices(), 0)
check("the clock is back to zero", stopper.t_sim, 0.0)
check("the index is back to zero", stopper.index, 0)
check("the flash counter is back to zero", stopper.flash_count, 0)
check_true("the timer is stopped", not stopper.is_playing)
check("no layer created in the whole cycle",
      len(QgsProject.instance().mapLayers()), layers_before)
check("no canvas.refresh() anywhere in the playback",
      canvas.refresh_calls - refresh_before, 0)
check("no layer created in this entire test",
      len(QgsProject.instance().mapLayers()), 0)

# --------------------------------------------------------------------------
# S6 - nothing to play
# --------------------------------------------------------------------------
print("\n== S6: no mission ==")
empty = MissionPlayer(iface)
check_true("play(None) refuses", not empty.play(None))
check_true("the reason is not empty", bool(empty.message.strip()))
check_true("the reason is in Italian",
           "missione" in empty.message.lower())
print("        {0}".format(empty.message))
check("a tick without a mission does nothing", empty.t_sim, 0.0)
empty.tick()
check("...still nothing", empty.t_sim, 0.0)
check("duration without a mission", empty.duration_s, 0.0)
check("nothing is drawn", empty.band_vertices(), 0)
check_true("the summary says so, not a number",
           empty.summary() == empty.message)

try:
    from geocad_uav.gui.dock import GeoCadDock                   # noqa: F401

    check_true("the dock exposes the transport controls",
               all(hasattr(GeoCadDock, name) for name in
                   ("_play_mission", "_pause_mission", "_stop_mission",
                    "_refresh_player")))
except ImportError as exc:                                      # noqa: BLE001
    skip("the dock exposes the transport controls", str(exc))

# --------------------------------------------------------------------------
# R1 - the player is a viewer, not a planner
# --------------------------------------------------------------------------
print("\n== R1: the player imports no planner ==")
check_true("no import of uav.survey",
           not any("survey" in name for name in PLAYER_IMPORTS))
check_true("no import of uav.mission",
           not any(name.endswith("mission") or name.endswith(".mission")
                   for name in PLAYER_IMPORTS))
check_true("the only uav import is terrain_follow",
           {name for name in PLAYER_IMPORTS if "uav" in name}
           <= {"uav", "uav.terrain_follow"})
check_true("no plan_route or build_mission call",
           "plan_route" not in PLAYER_CODE
           and "build_mission(" not in PLAYER_CODE)
check_true("no TerrainModel of its own",
           "TerrainModel" not in PLAYER_CODE and "core.z" not in PLAYER_CODE)
check_true("it writes no features",
           not any(token in PLAYER_CODE for token in
                   ("addFeature", "memory_layer", "addMapLayer")))
check_true("no canvas.refresh in the player",
           "refresh()" not in PLAYER_CODE)
check_true("no sleep anywhere", "sleep" not in PLAYER_CODE)
check_true("the tick is driven by a QTimer", "QTimer" in PLAYER_CODE)
check("rates offered", len(mp.RATES), 3)
check_true("the rates are 1, 2 and 5", tuple(mp.RATES) == (1, 2, 5))

player.teardown()
player_1x.teardown()
player_2x.teardown()
player_5x.teardown()
resume.teardown()
stopper.teardown()
empty.teardown()

print("\n" + "=" * 78)
QgsProject.instance().removeAllMapLayers()
# The DEM layer is deliberately never registered (S5 counts project layers),
# so nothing else drops its GDAL handle before the interpreter exits.
DEM_LAYER = None
TERRAIN = None
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
