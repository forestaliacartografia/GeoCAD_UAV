"""
Standalone numeric tests for the terrain-following core.

Uses synthetic elevation grids, so it runs on the plain system Python:

    python tests/test_terrain.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from geocad_uav.core import z as zc                         # noqa: E402
from geocad_uav.uav import cameras as cam_lib               # noqa: E402
from geocad_uav.uav import photogrammetry as pg             # noqa: E402
from geocad_uav.uav import terrain_follow as tf             # noqa: E402

FAILURES = []


def check(label, got, expected, tol=1e-6):
    ok = (math.isnan(got) and math.isnan(expected)) or abs(got - expected) <= tol
    print("  [{0}] {1:<54} got={2:<16.8g} exp={3:.8g}".format(
        "ok  " if ok else "FAIL", label, got, expected))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


# --------------------------------------------------------------- fixtures --
CELL = 2.0
NX, NY = 300, 240
OX, OY = 500000.0, 5000000.0        # upper-left corner
GT = (OX, CELL, 0.0, OY, 0.0, -CELL)


def planar_grid(a=100.0, b_east=0.05, b_north=0.02):
    """An exactly planar surface, rising by b_east to the E and b_north to the N.

    Note the sign convention: b_north is a genuine *northward* gradient, so the
    grid must be built from (Y - y_south), not (OY - Y) -- OY is the top edge,
    so (OY - Y) grows southward and would silently mirror every slope test.
    """
    xs = OX + (np.arange(NX) + 0.5) * CELL
    ys = OY - (np.arange(NY) + 0.5) * CELL
    X, Y = np.meshgrid(xs, ys)
    return a + b_east * (X - OX) + b_north * (Y - Y.min()), X, Y


# ------------------------------------------------------------- sampling ----
print("\n== bilinear sampling ==")
Z, X, Y = planar_grid()
model = zc.TerrainModel(Z, GT, "EPSG:32632")
check_true("grid metadata", model.cols == NX and model.rows == NY
           and abs(model.cellsize - CELL) < 1e-12)

# Bilinear interpolation of a plane is exact, so this is a strong check.
px = OX + np.array([13.7, 100.0, 355.2, 5.0])
py = OY - np.array([9.3, 200.0, 411.9, 3.0])
got = model.sample(px, py)
exp = 100.0 + 0.05 * (px - OX) + 0.02 * (py - float(Y.min()))
check("plane sampled exactly (max err)", float(np.max(np.abs(got - exp))), 0.0, 1e-9)

# Nearest-neighbour would quantise to the cell value; confirm we are not doing that.
mid = model.sample(OX + 1.0, OY - 1.0)      # exactly a cell centre
off = model.sample(OX + 1.9, OY - 1.0)      # 0.9 m along +x within the same cell
check_true("sampling is bilinear, not nearest-neighbour",
           abs(float(off) - float(mid)) > 1e-6)

# outside the grid -> NaN, never extrapolated
outside = model.sample(np.array([OX - 50.0, OX + NX * CELL + 50.0]),
                       np.array([OY - 10.0, OY - 10.0]))
check_true("outside the grid returns NaN (no extrapolation)",
           bool(np.all(~np.isfinite(outside))))

# no-data propagation
Zh = Z.copy()
Zh[100:110, 120:130] = np.nan
holed = zc.TerrainModel(Zh, GT, "EPSG:32632")
in_hole = holed.sample(OX + 125 * CELL, OY - 105 * CELL)
check_true("no-data cell returns NaN", not np.isfinite(float(in_hole)))
halo = holed.sample(OX + (119.6) * CELL, OY - 105 * CELL)
check_true("no-data halo: a neighbour of a hole is NaN too",
           not np.isfinite(float(halo)))
check("nodata_fraction", holed.nodata_fraction, 100.0 / (NX * NY), 1e-9)

# ------------------------------------------------------ slope orientation --
print("\n== dominant slope azimuth ==")
# Plane rising to the East: gradient points E, so the max-slope line is E-W.
grid_e, _, _ = planar_grid(b_east=0.10, b_north=0.0)
az_e = zc.TerrainModel(grid_e, GT).dominant_slope_azimuth()
check("slope purely E-W -> azimuth 90", az_e, 90.0, 1e-6)
# Plane rising to the North.
grid_n, _, _ = planar_grid(b_east=0.0, b_north=0.10)
az_n = zc.TerrainModel(grid_n, GT).dominant_slope_azimuth()
check("slope purely N-S -> azimuth 0", az_n, 0.0, 1e-6)
# NE and SE are distinguished only if the north/south sign is right.
grid_ne, _, _ = planar_grid(b_east=0.10, b_north=0.10)
check("slope NE -> azimuth 45",
      zc.TerrainModel(grid_ne, GT).dominant_slope_azimuth(), 45.0, 1e-6)
grid_se, _, _ = planar_grid(b_east=0.10, b_north=-0.10)
check("slope SE -> azimuth 135 (catches a north/south sign flip)",
      zc.TerrainModel(grid_se, GT).dominant_slope_azimuth(), 135.0, 1e-6)
check_true("flat surface has no dominant slope",
           zc.TerrainModel(planar_grid(b_east=0.0, b_north=0.0)[0], GT)
           .dominant_slope_azimuth() is None)

# -------------------------------------------------------- flight profile ---
print("\n== terrain-following profile ==")
line = np.array([[OX + 20.0, OY - 20.0], [OX + 400.0, OY - 20.0]])
prof = tf.build_flight_profile(model, line, h_agl_m=100.0, step_m=5.0)
agl = prof.agl
check("AGL is constant along the profile (spread)",
      float(np.nanmax(agl) - np.nanmin(agl)), 0.0, 1e-9)
check("AGL equals the requested height", float(np.nanmean(agl)), 100.0, 1e-9)
check_true("profile is not flat in absolute height (it follows the ground)",
           float(np.nanmax(prof.z_flight) - np.nanmin(prof.z_flight)) > 15.0)
check("safety margin and vegetation clearance add on top",
      float(np.nanmean(tf.build_flight_profile(
          model, line, 100.0, 5.0, safety_margin_m=5.0,
          vegetation_clearance_m=15.0).agl)), 120.0, 1e-9)

check("sample step = min(D_front, cell, 5 m)",
      tf.sample_step_for(21.16, CELL), 2.0)
check("sample step capped at 5 m on a coarse DEM",
      tf.sample_step_for(21.16, 30.0), 5.0)

# ---------------------------------------------------- waypoint thinning ----
print("\n== vertical simplification ==")
s = np.linspace(0.0, 1000.0, 501)
flat = np.full_like(s, 250.0)
check("flat profile collapses to 2 waypoints",
      len(tf.simplify_vertical(s, flat, 2.0)), 2)

vee = 250.0 + np.abs(s - 500.0) * 0.1        # a clean V, 50 m deep
keep = tf.simplify_vertical(s, vee, 2.0)
check("V-shaped profile keeps exactly 3 waypoints", len(keep), 3)
check_true("the retained middle waypoint is the apex",
           abs(s[keep[1]] - 500.0) < 2.1)

rng = np.random.default_rng(42)
rough = 250.0 + np.cumsum(rng.normal(0.0, 1.2, s.size))
for tol in (0.5, 2.0, 5.0):
    kept = tf.simplify_vertical(s, rough, tol)
    recon = np.interp(s, s[kept], rough[kept])
    err = float(np.max(np.abs(recon - rough)))
    check_true("tol={0} m: reconstruction error {1:.3f} m <= tolerance"
               .format(tol, err), err <= tol + 1e-9)
    print("        (kept {0}/{1} waypoints)".format(len(kept), s.size))
check_true("looser tolerance keeps fewer waypoints",
           len(tf.simplify_vertical(s, rough, 5.0))
           < len(tf.simplify_vertical(s, rough, 0.5)))

# --------------------------------------------------------- kinematics ------
print("\n== climb-rate limiting ==")
# 100 m horizontal, 30 m up: at 4 m/s climb the ground speed cap is 13.33 m/s
xy = np.array([[0.0, 0.0], [100.0, 0.0], [200.0, 0.0]])
z = np.array([0.0, 30.0, 0.0])
kin = tf.apply_climb_limits(xy, z, v_target_ms=15.0,
                            climb_rate_ms=4.0, descent_rate_ms=3.0)
check("climb segment capped to rate*d/|dz|", float(kin.speed_ms[0]), 4.0 * 100 / 30)
check("descent segment uses the descent rate", float(kin.speed_ms[1]), 3.0 * 100 / 30)
check_true("both segments flagged as climb-limited", bool(kin.climb_limited.all()))
check("required vertical rate equals the airframe climb rate",
      float(kin.required_rate_ms[0]), 4.0, 1e-9)

gentle = tf.apply_climb_limits(np.array([[0.0, 0.0], [100.0, 0.0]]),
                               np.array([0.0, 1.0]), v_target_ms=8.0)
check("gentle slope is not capped", float(gentle.speed_ms[0]), 8.0)
check_true("gentle slope not flagged", not bool(gentle.climb_limited.any()))

cliff = tf.apply_climb_limits(np.array([[0.0, 0.0], [10.0, 0.0]]),
                              np.array([0.0, 90.0]), v_target_ms=8.0,
                              climb_rate_ms=4.0, v_min_ms=1.0)
check_true("a cliff is flagged infeasible rather than silently skipped",
           bool(cliff.infeasible.all()))

t = tf.segment_flight_time(np.array([[0.0, 0.0], [300.0, 0.0]]),
                           np.array([0.0, 400.0]), np.array([5.0]))
check("flight time uses 3-D length (3-4-5 triangle)", t, 500.0 / 5.0, 1e-9)

# ------------------------------------------------------------ ray casting --
print("\n== ray casting onto the DEM ==")
flat_grid = np.full((NY, NX), 200.0)
flat = zc.TerrainModel(flat_grid, GT)
cam = np.array([OX + 300.0, OY - 240.0, 300.0])       # 100 m above the plane
hit = zc.raycast_to_terrain(flat, cam[None, :], np.array([[0.0, 0.0, -1.0]]),
                            t_max=400.0)
check("nadir ray hits the flat plane at z=200", float(hit[0, 2]), 200.0, 1e-3)
check("nadir ray lands directly below the camera",
      float(np.hypot(hit[0, 0] - cam[0], hit[0, 1] - cam[1])), 0.0, 1e-3)

# 45 degree ray must travel exactly 100 m horizontally over a 100 m drop
diag = np.array([[1.0, 0.0, -1.0]])
hit2 = zc.raycast_to_terrain(flat, cam[None, :], diag, t_max=400.0)
check("45 deg ray travels h horizontally", float(hit2[0, 0] - cam[0]), 100.0, 1e-3)

# Ray casting against a tilted plane, checked analytically.
tilt_grid, _, _ = planar_grid(a=200.0, b_east=0.2, b_north=0.0)     # 20 % slope up to E
tilt = zc.TerrainModel(tilt_grid, GT)
cam_t = np.array([OX + 200.0, OY - 240.0, 200.0 + 0.2 * 200.0 + 100.0])
hit3 = zc.raycast_to_terrain(tilt, cam_t[None, :], diag, t_max=600.0)
# Solve: cam.z - dx = 200 + 0.2*(cam.x + dx - OX)
dx_exact = (cam_t[2] - 200.0 - 0.2 * (cam_t[0] - OX)) / 1.2
check("oblique ray on a 20 % slope matches the analytic solution",
      float(hit3[0, 0] - cam_t[0]), dx_exact, 1e-2)

# ---------------------------------------------- footprint cross-validation --
print("\n== draped footprint vs closed-form footprint ==")
M3E = cam_lib.load_library()["dji_mavic3e"]
H = 100.0
w_exact, l_exact = pg.footprint(M3E, H)
ring = tf.drape_footprint(flat, cam, azimuth_deg=0.0, camera=M3E,
                          samples_per_edge=0)
# heading North: along-track is +N (y), across-track is +E (x)
w_ray = float(ring[:, 0].max() - ring[:, 0].min())
l_ray = float(ring[:, 1].max() - ring[:, 1].min())
check("ray-cast footprint width  == Sw/f*H", w_ray, w_exact, 2e-3)
check("ray-cast footprint length == Sh/f*H", l_ray, l_exact, 2e-3)
check_true("footprint ring is closed", bool(np.allclose(ring[0], ring[-1])))
check("footprint is centred under the camera (E)",
      float(ring[:4, 0].mean()), cam[0], 1e-3)

# rotated heading: extents swap
ring90 = tf.drape_footprint(flat, cam, azimuth_deg=90.0, camera=M3E,
                            samples_per_edge=0)
check("heading 90 deg swaps the footprint extents",
      float(ring90[:, 0].max() - ring90[:, 0].min()), l_exact, 2e-3)

# on a slope the draped footprint must be LARGER than the flat-plane rectangle
ring_t = tf.drape_footprint(tilt, cam_t, azimuth_deg=0.0, camera=M3E,
                            samples_per_edge=2)
area_flat = w_exact * l_exact
xs, ys = ring_t[:, 0], ring_t[:, 1]
area_t = 0.5 * abs(float(np.dot(xs[:-1], ys[1:]) - np.dot(xs[1:], ys[:-1])))
check_true("draped footprint on a 20 % slope is larger than the flat one "
           "({0:.0f} m2 vs {1:.0f} m2)".format(area_t, area_flat),
           area_t > area_flat * 1.01)

# ------------------------------------------------------------- gap filling --
print("\n== DEM gap handling ==")
prof_h = tf.build_flight_profile(holed, np.array(
    [[OX + 240.0, OY - 190.0], [OX + 270.0, OY - 250.0]]), 100.0, 2.0)
check_true("profile detects the DEM hole", prof_h.has_gaps)
warns = tf.fill_profile_gaps(prof_h, "hold_max")
check_true("hold_max leaves no NaN in the commanded height",
           bool(np.all(np.isfinite(prof_h.z_flight))))
check_true("gap fill emits a warning", len(warns) == 1)
check_true("gap mask is retained after filling", prof_h.gap_mask.any())

print("\n" + "=" * 76)
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for f_ in FAILURES:
        print("   - {0}".format(f_))
    sys.exit(1)
print("ALL CHECKS PASSED")
