"""
Standalone numeric tests for the pure-python core.

Run with the plain system Python (no QGIS needed):

    python tests/test_photogrammetry.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from geocad_uav.uav import cameras as cam_lib                   # noqa: E402
from geocad_uav.core import planar as geo                       # noqa: E402
from geocad_uav.uav import photogrammetry as pg                 # noqa: E402

FAILURES = []


def check(label, got, expected, tol=1e-6):
    ok = abs(got - expected) <= tol
    status = "ok  " if ok else "FAIL"
    print("  [{0}] {1:<52} got={2:<18.8g} expected={3:.8g}".format(
        status, label, got, expected))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    status = "ok  " if condition else "FAIL"
    print("  [{0}] {1}".format(status, label))
    if not condition:
        FAILURES.append(label)


# ---------------------------------------------------------------- cameras --
print("\n== camera library ==")
LIB = cam_lib.load_library()
print("  presets loaded: {0}".format(len(LIB)))
for key, c in LIB.items():
    warns = cam_lib.check_camera(c)
    if warns:
        for w in warns:
            print("  [WARN] {0}".format(w))
    check_true("{0}: pixel pitch self-consistent (<1%)".format(key),
               c.pitch_mismatch_pct <= 1.0)

M3E = LIB["dji_mavic3e"]

# ------------------------------------------------------------ photogrammetry --
print("\n== core relations, DJI Mavic 3E @ 100 m AGL ==")
H = 100.0
pitch = M3E.sensor_w_mm / M3E.image_w_px
check("pitch = Sw/Px [mm]", pitch, 17.3 / 5280.0)

gsd = pg.gsd_from_height(M3E, H)
check("GSD = H*pitch/f [m/px]", gsd, H * (17.3 / 5280.0) / 12.29)
check("GSD [cm/px]", gsd * 100.0, 2.66603, 1e-4)

# round trip
check("height_from_gsd inverts gsd_from_height",
      pg.height_from_gsd(M3E, gsd), H, 1e-9)

across, along = pg.footprint(M3E, H)
check("footprint across W = Sw/f*H [m]", across, 17.3 / 12.29 * H)
check("footprint along  L = Sh/f*H [m]", along, 13.0 / 12.29 * H)
check("W [m]", across, 140.7648, 1e-3)
check("L [m]", along, 105.7770, 1e-3)

ov = pg.OVERLAP_PRESETS["dsm_3d"]
check("D_side = W*(1-sidelap) [m]", pg.strip_spacing(across, ov.sidelap),
      across * 0.30, 1e-9)
check("D_front = L*(1-frontlap) [m]", pg.shot_spacing(along, ov.frontlap),
      along * 0.20, 1e-9)

geom = pg.solve_survey_geometry(M3E, ov, h_agl_m=H)
check("solved D_side [m]", geom.d_side_m, 42.2294, 1e-3)
check("solved D_front [m]", geom.d_front_m, 21.1554, 1e-3)

# GSD/height duality: solving from GSD must reproduce the same geometry
geom2 = pg.solve_survey_geometry(M3E, ov, gsd_m_px=gsd)
check("solve from GSD reproduces H", geom2.h_agl_m, H, 1e-9)
check("solve from GSD reproduces D_side", geom2.d_side_m, geom.d_side_m, 1e-9)

try:
    pg.solve_survey_geometry(M3E, ov, h_agl_m=H, gsd_m_px=gsd)
    check_true("supplying both H and GSD is rejected", False)
except pg.PhotogrammetryError:
    check_true("supplying both H and GSD is rejected", True)

# portrait mount swaps the footprint axes
across_p, along_p = pg.footprint(M3E, H, pg.ORIENT_ALONG)
check_true("portrait mount swaps footprint axes",
           abs(across_p - along) < 1e-9 and abs(along_p - across) < 1e-9)

# ------------------------------------------------------------------ speeds --
print("\n== speed budget ==")
check("V_blur = GSD*blur_px/t_shutter [m/s]",
      pg.blur_speed_limit(gsd, M3E.shutter_s, 1.5), gsd * 1.5 / 0.001)
check("V_trigger = D_front/t_interval [m/s]",
      pg.trigger_speed_limit(geom.d_front_m, M3E.min_interval_s),
      geom.d_front_m / 0.7)

budget = pg.build_speed_budget(geom, v_mission_ms=8.0, v_drone_max_ms=15.0)
check("effective speed = requested (nothing binds at 8 m/s)",
      budget.effective, 8.0)
check_true("binding constraint is the mission request",
           budget.binding == "v_mission")

tight = pg.build_speed_budget(geom, v_mission_ms=45.0, v_drone_max_ms=50.0)
check_true("unrealistic request is capped by a real constraint",
           tight.is_capped_below_request and tight.binding != "v_mission")

# climb-rate ceiling: 30 m up over 100 m horizontal at 4 m/s climb
check("V_climb = rate*d/|dz| [m/s]",
      pg.climb_speed_limit(30.0, 100.0, 4.0, 3.0), 4.0 * 100.0 / 30.0)
check("V_climb uses descent rate when going down",
      pg.climb_speed_limit(-30.0, 100.0, 4.0, 3.0), 3.0 * 100.0 / 30.0)
check_true("level segment is unconstrained",
           math.isinf(pg.climb_speed_limit(0.0, 100.0, 4.0, 3.0)))

# ------------------------------------------------------------ exterior orient --
print("\n== exterior orientation ==")
om, ph, ka = pg.opk_from_yaw_pitch(0.0, -90.0)
check_true("nadir shot heading N -> omega,phi ~ 0",
           abs(om) < 1e-6 and abs(ph) < 1e-6)
om2, ph2, _ = pg.opk_from_yaw_pitch(90.0, -90.0)
check_true("nadir shot heading E -> still level (omega,phi ~ 0)",
           abs(om2) < 1e-6 and abs(ph2) < 1e-6)
om3, ph3, _ = pg.opk_from_yaw_pitch(0.0, -45.0)
check_true("45 deg oblique tilts the frame by 45 deg",
           abs(abs(om3) + abs(ph3) - 45.0) < 1e-6)

# ---------------------------------------------------------------- geometry --
print("\n== strip frame ==")
for az in (0.0, 30.0, 90.0, 143.7, 270.0):
    f = geo.StripFrame(az, 500000.0, 5000000.0)
    # round trip
    x0, y0 = 500123.4, 5000987.6
    s, t = f.to_frame(x0, y0)
    x1, y1 = f.to_world(s, t)
    check("az={0:g}: world->frame->world round trip".format(az),
          math.hypot(float(x1) - x0, float(y1) - y0), 0.0, 1e-6)
    # a step of +1 along s must move exactly 1 m at bearing az
    xa, ya = f.to_world(0.0, 0.0)
    xb, yb = f.to_world(1.0, 0.0)
    check("az={0:g}: +1 along-track is 1 m at bearing az".format(az),
          geo.azimuth_of(float(xb - xa), float(yb - ya)), az % 360.0, 1e-6)
    # across-track is 90 deg clockwise from along-track
    xc, yc = f.to_world(0.0, 1.0)
    check("az={0:g}: +1 across-track is 1 m at bearing az+90".format(az),
          geo.azimuth_of(float(xc - xa), float(yc - ya)), (az + 90.0) % 360.0, 1e-6)

# axes are orthonormal
f = geo.StripFrame(37.0)
ux, uy = geo.along_track_unit(37.0)
vx, vy = geo.across_track_unit(37.0)
check("along/across axes orthogonal", ux * vx + uy * vy, 0.0, 1e-12)
check("along axis is unit length", math.hypot(ux, uy), 1.0, 1e-12)

print("\n== polyline utilities ==")
line = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 50.0]])
check("polyline_length", geo.polyline_length(line), 150.0)
pts, chain = geo.points_along(line, 25.0)
check("points_along count at 25 m over 150 m", len(pts), 7)
check("last station chainage", float(chain[-1]), 150.0)
check("station 5 lands past the corner (x=100, y=25)",
      float(pts[5][1]), 25.0, 1e-9)
res = geo.resample_polyline(line, 10.0)
check_true("resample keeps the corner vertex",
           bool(np.any(np.all(np.isclose(res, [100.0, 0.0]), axis=1))))
head = geo.headings_along(line)
check("heading of first segment (due E)", float(head[0]), 90.0)
check("heading of second segment (due N)", float(head[1]), 0.0)

rect = geo.rectangle_corners(0.0, 0.0, half_across=70.0, half_along=50.0,
                             azimuth_deg=0.0)
check_true("footprint rect is closed",
           bool(np.allclose(rect[0], rect[-1])))
check("footprint across extent = 2*half_across",
      float(rect[:, 0].max() - rect[:, 0].min()), 140.0, 1e-9)
check("footprint along extent = 2*half_along",
      float(rect[:, 1].max() - rect[:, 1].min()), 100.0, 1e-9)

# ------------------------------------------------------- sensor kind (1.34) --
print("\n== il tipo di sensore ==")
from geocad_uav.uav import cameras as _cam_lib                   # noqa: E402

_library = _cam_lib.load_library()
print("        preset: {0}".format(
    sorted({c.kind for c in _library.values()})))
check_true("ogni preset dichiara un tipo di sensore valido",
           all(c.kind in pg.SENSOR_KINDS for c in _library.values()))
check_true("i dieci presenti sono fotocamere RGB, e lo dicono",
           all(c.kind == pg.KIND_RGB for c in _library.values()))
check_true("il tipo ha un'etichetta leggibile",
           all(c.kind_label for c in _library.values()))
check_true("il vocabolario copre RGB, multispettrale, termico e LiDAR",
           set(pg.SENSOR_KINDS) == {"rgb", "multispectral", "thermal",
                                    "lidar"})

_one = next(iter(_library.values()))
check("il rapporto d'aspetto viene dai pixel", _one.aspect_ratio,
      _one.image_w_px / _one.image_h_px, 1e-12)
check_true("la descrizione porta il tipo",
           _one.kind_label in _cam_lib.describe(_one))

_bad = False
try:
    pg.Camera(name="ignota", focal_mm=10.0, sensor_w_mm=13.2,
              sensor_h_mm=8.8, image_w_px=5472, image_h_px=3648,
              kind="sonar")
except pg.PhotogrammetryError:
    _bad = True
check_true("un tipo inventato viene rifiutato", _bad)
check_true("...e l'assenza di tipo vale RGB",
           pg.Camera(name="senza tipo", focal_mm=10.0, sensor_w_mm=13.2,
                     sensor_h_mm=8.8, image_w_px=5472,
                     image_h_px=3648).kind == pg.KIND_RGB)

# ------------------------------------------------------- DJI Mini (1.37) --
print("\n== la serie DJI Mini e' in libreria, e l'ottica torna ==")
from geocad_uav.uav import drones as _drone_lib                  # noqa: E402

_drones = _drone_lib.load_library()

for _key, _name in (("dji_mini1", "DJI Mini (1)"),
                    ("dji_mini2", "DJI Mini 2"),
                    ("dji_mini3", "DJI Mini 3"),
                    ("dji_mini3pro", "DJI Mini 3 Pro")):
    check_true("il drone {0} c'e' di default".format(_name),
               _key in _drones and _drones[_key].name == _name)
for _key in ("dji_mini1", "dji_mini2", "dji_mini3", "dji_mini3pro_48",
             "dji_mini3pro_12"):
    check_true("la camera {0} c'e' di default".format(_key), _key in _library)

# What the operator gave, as the library holds it.
check("Mini 1: 249 g", _drones["dji_mini1"].weight_g, 249.0)
check("Mini 1: 30 min", _drones["dji_mini1"].endurance_min, 30.0)
check("Mini 1: 13 m/s", _drones["dji_mini1"].v_max_ms, 13.0)
check("Mini 2: 31 min", _drones["dji_mini2"].endurance_min, 31.0)
check("Mini 2: 16 m/s", _drones["dji_mini2"].v_max_ms, 16.0)
check("Mini 3 Pro: 34 min", _drones["dji_mini3pro"].endurance_min, 34.0)
check("...e 47 con la batteria Plus",
      _drones["dji_mini3pro_plus"].endurance_min, 47.0)
check_true("i Mini standard sono in classe sotto i 250 g",
           all(_drones[k].is_sub_250g for k in
               ("dji_mini1", "dji_mini2", "dji_mini3", "dji_mini3pro")))
check_true("...e quello con la Plus no, perche' pesa di piu'",
           not _drones["dji_mini3pro_plus"].is_sub_250g)
check_true("un peso non dichiarato non vale 'leggero'",
           not _drone_lib.DroneProfile(key="x", name="x").is_sub_250g)

# The pixel count is what was given, and the two pitch estimates agree.
check("Mini 2: 12 MP a 4000x3000",
      _library["dji_mini2"].image_w_px * _library["dji_mini2"].image_h_px,
      4000 * 3000)
check("Mini 3 Pro: 48 MP a 8064x6048",
      _library["dji_mini3pro_48"].image_w_px
      * _library["dji_mini3pro_48"].image_h_px, 8064 * 6048)
for _key in ("dji_mini1", "dji_mini2", "dji_mini3", "dji_mini3pro_48",
             "dji_mini3pro_12"):
    check_true("{0}: le due stime del passo pixel concordano".format(_key),
               not _cam_lib.check_camera(_library[_key]))

# And the GSD calculator uses them: H * passo / focale, ricalcolato qui.
for _key, _h in (("dji_mini2", 100.0), ("dji_mini3pro_48", 100.0),
                 ("dji_mini1", 50.0)):
    _cam = _library[_key]
    _own = _h * (_cam.sensor_w_mm / _cam.image_w_px) / _cam.focal_mm
    _engine = pg.gsd_from_height(_cam, _h)
    print("        {0} a {1:.0f} m -> {2:.3f} cm/px".format(
        _key, _h, 100.0 * _engine))
    check("{0}: il GSD e' quello dell'ottica".format(_key), _engine, _own,
          1e-12)
    check("...e la quota si ritrova dal GSD",
          pg.height_from_gsd(_cam, _engine), _h, 1e-9)

# The 1/1.3" optic has one focal in the library, not two.
check("Mini 3 Pro e Mini 4 Pro condividono la stessa focale",
      _library["dji_mini3pro_48"].focal_mm,
      _library["dji_mini4pro_48"].focal_mm, 1e-12)

# ------------------------------------------------------------------ verdict --
print("\n" + "=" * 72)
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for f_ in FAILURES:
        print("   - {0}".format(f_))
    sys.exit(1)
print("ALL CHECKS PASSED")
