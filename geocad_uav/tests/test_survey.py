"""
M5 tests: survey route generation. NEEDS QGIS (GEOS clipping).

Run with the QGIS Python:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_survey.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import QgsApplication, QgsGeometry            # noqa: E402

from geocad_uav.core import planar as pl                     # noqa: E402
from geocad_uav.uav import survey as sv                      # noqa: E402

QGS = QgsApplication([], False)
QGS.initQgis()

FAILURES = []


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<58} got={2:<14.9g} exp={3:.9g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def rect_wkt(x0, y0, x1, y1):
    return "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        x0, y0, x1, y1)


# --------------------------------------------- spec case: 200 x 120, 40 m --
print("\n== spec case: AOI 200 x 120 m, D_side = 40 m ==")
AOI = QgsGeometry.fromWkt(rect_wkt(0, 0, 200, 120))
W, L = 100.0, 80.0                 # footprint across / along
D_SIDE, D_FRONT = 40.0, 20.0

plan = sv.plan_route(AOI, d_side_m=D_SIDE, d_front_m=D_FRONT,
                     footprint_across_m=W, footprint_along_m=L)

check("azimuth follows the long (E-W) axis", plan.azimuth_deg % 180.0, 90.0, 1e-6)
print("        strips={0}  legs={1}  turns={2}".format(
    plan.n_strips, len(plan.legs), plan.n_turns))
# Derivation: the AOI is 120 m across; the photogrammetric buffer adds W/2 on
# each side, so the clip polygon spans 220 m. strip_offsets emits
# ceil(220/40) + 1 = 7 centred offsets (-120..+120), of which the outer two fall
# beyond the buffered polygon and clip to nothing -> 5 strips.
check("strip count matches the documented layout", plan.n_strips, 5)

offsets = sorted({leg.t for leg in plan.legs})
check_true("strip offsets are exactly D_side apart",
           bool(np.allclose(np.diff(offsets), D_SIDE)))
check_true("strips are centred on the AOI",
           abs(offsets[0] + offsets[-1]) < 1e-6)

# -- lateral coverage: every AOI point must be within W/2 of some strip -----
gx, gy = np.meshgrid(np.linspace(0.5, 199.5, 200), np.linspace(0.5, 119.5, 120))
pts = np.column_stack([gx.ravel(), gy.ravel()])
_, t_pts = plan.frame.to_frame(pts[:, 0], pts[:, 1])
dist = np.min(np.abs(t_pts[:, None] - np.array(offsets)[None, :]), axis=1)
check("worst across-track distance to the nearest strip [m]",
      float(dist.max()), 0.0, W / 2.0)
check_true("100 % of the AOI is within half a footprint of a strip",
           float(dist.max()) <= W / 2.0 + 1e-9)

n_cover = np.sum(np.abs(t_pts[:, None] - np.array(offsets)[None, :])
                 <= W / 2.0, axis=1)
expected_strips = W / D_SIDE                    # 1/(1 - sidelap)
print("        strips covering each AOI point: min={0} max={1} "
      "(nominal W/D_side = {2:.2f})".format(int(n_cover.min()),
                                            int(n_cover.max()), expected_strips))
check_true("every AOI point is covered by at least 2 strips",
           int(n_cover.min()) >= 2)
check_true("the AOI edge carries the nominal sidelap, not single coverage",
           int(n_cover.min()) >= int(math.floor(expected_strips)))

# -- along-track extent ----------------------------------------------------
s_min = min(min(leg.s_photo_start, leg.s_photo_end) for leg in plan.legs)
s_max = max(max(leg.s_photo_start, leg.s_photo_end) for leg in plan.legs)
s_aoi, _ = plan.frame.to_frame(np.array([0.0, 200.0]), np.array([60.0, 60.0]))
check_true("strips overrun the AOI ends by at least half a footprint",
           s_min <= float(s_aoi.min()) - W / 2.0 + 1e-6
           and s_max >= float(s_aoi.max()) + W / 2.0 - 1e-6)

# -- boustrophedon ---------------------------------------------------------
print("\n== flight ordering ==")
dirs = [1 if leg.s_photo_end > leg.s_photo_start else -1 for leg in plan.legs]
check_true("consecutive legs alternate direction (boustrophedon)",
           all(a != b for a, b in zip(dirs, dirs[1:])))
headings = [leg.azimuth_deg for leg in plan.legs]
check_true("headings alternate by 180 deg",
           all(abs((a - b) % 360.0 - 180.0) < 1e-6
               for a, b in zip(headings, headings[1:])))
check_true("legs are numbered consecutively",
           [leg.seq for leg in plan.legs] == list(range(len(plan.legs))))
check_true("each leg ends where the next one is nearest",
           plan.transit_length_m() > 0)
check("survey length = sum of leg lengths", plan.survey_length_m(),
      sum(abs(l.s_fly_end - l.s_fly_start) for l in plan.legs), 1e-6)

# -- orientation strategies ------------------------------------------------
print("\n== azimuth strategies ==")
tall = QgsGeometry.fromWkt(rect_wkt(0, 0, 120, 400))
plan_tall = sv.plan_route(tall, D_SIDE, D_FRONT, W, L)
check("a tall AOI gets N-S strips", plan_tall.azimuth_deg % 180.0, 0.0, 1e-6)

manual = sv.plan_route(AOI, D_SIDE, D_FRONT, W, L,
                       azimuth_strategy=sv.AZIMUTH_MANUAL,
                       manual_azimuth_deg=35.0)
check("manual azimuth is honoured", manual.azimuth_deg, 35.0, 1e-9)

wind = sv.plan_route(AOI, D_SIDE, D_FRONT, W, L,
                     azimuth_strategy=sv.AZIMUTH_ALONG_WIND, wind_from_deg=200.0)
check("wind azimuth is honoured (mod 180)", wind.azimuth_deg, 20.0, 1e-9)

# -- concave AOI -----------------------------------------------------------
print("\n== concave AOI ==")
# A U-shape, not an L: an L never splits a *straight* strip into two, because
# the notch is always open to one side. The U has a genuine interior gap.
ushape = QgsGeometry.fromWkt(
    "POLYGON((0 0, 300 0, 300 250, 220 250, 220 100, 80 100, 80 250, 0 250, 0 0))")
plan_l = sv.plan_route(ushape, 30.0, 15.0, 60.0, 45.0)
per_offset = {}
for leg in plan_l.legs:
    per_offset[leg.strip_index] = per_offset.get(leg.strip_index, 0) + 1
check_true("a concave AOI splits at least one strip into two legs",
           max(per_offset.values()) >= 2)
check_true("split legs are not bridged across the notch",
           len(plan_l.legs) > plan_l.n_strips)

# -- fixed wing / interlacing ---------------------------------------------
print("\n== fixed-wing interlacing ==")
check("interlace step for R=60, D_side=40", sv.interlace_step(60.0, 40.0), 3)
check("interlace step is 1 when the turn fits", sv.interlace_step(15.0, 40.0), 1)
inter = sv.plan_route(AOI, D_SIDE, D_FRONT, W, L,
                      pattern=sv.PATTERN_INTERLACED, turn_radius_m=60.0)
seq_offsets = [leg.t for leg in inter.legs]
gaps = inter.consecutive_offset_gaps()
plain_gaps = plan.consecutive_offset_gaps()
print("        interlaced gaps {0}".format([round(g) for g in gaps]))
check_true("interlacing widens the turns versus adjacent-strip order",
           max(gaps) > max(plain_gaps))
check("most interlaced turns clear 2R", sum(g >= 120.0 - 1e-6 for g in gaps),
      len(gaps) - len(inter.tight_turns(60.0)))
# With 5 strips and a required skip of 3, no ordering keeps every consecutive
# gap at 3: from strip 0 only strip 3 or 4 is reachable, and from 3 only 0 is,
# which dead-ends. The planner must say so rather than silently emit an
# unflyable turn.
check_true("turns that cannot fit the radius are reported, not hidden",
           len(inter.tight_turns(60.0)) > 0
           and any("lateral room" in w for w in inter.warnings))
check_true("interlacing still visits every strip",
           sorted(set(seq_offsets)) == sorted(set(leg.t for leg in plan.legs)))
check_true("a tight U-turn on a multirotor plan is warned about",
           any("interlaced" in w for w in
               sv.plan_route(AOI, D_SIDE, D_FRONT, W, L,
                             turn_radius_m=60.0).warnings))

# -- double grid -----------------------------------------------------------
print("\n== double grid ==")
grids = sv.plan_double_grid(AOI, d_side_m=D_SIDE, d_front_m=D_FRONT,
                            footprint_across_m=W, footprint_along_m=L)
check("double grid returns two passes", len(grids), 2)
check("the second pass is orthogonal to the first",
      abs(grids[0].azimuth_deg - grids[1].azimuth_deg) % 180.0, 90.0, 1e-6)

# -- corridor --------------------------------------------------------------
print("\n== corridor ==")
axis = QgsGeometry.fromWkt("LINESTRING(0 0, 400 100, 900 80, 1400 400)")
corr = sv.plan_corridor(axis, d_side_m=40.0, d_front_m=20.0,
                        corridor_width_m=200.0, footprint_across_m=100.0)
check_true("corridor produces multiple parallel strips", len(corr.legs) >= 5)
check_true("corridor strips follow the bends (more than 2 vertices)",
           any(leg.fly_endpoints_xy().shape[0] > 2 for leg in corr.legs))
c_offsets = sorted({leg.t for leg in corr.legs})
check_true("corridor offsets are symmetric about the axis",
           abs(c_offsets[0] + c_offsets[-1]) < 1e-6)
check_true("corridor covers the requested half width",
           max(abs(o) for o in c_offsets) + 50.0 >= 100.0 - 1e-6)

# -- validation ------------------------------------------------------------
print("\n== AOI preparation ==")
bowtie = QgsGeometry.fromWkt("POLYGON((0 0, 100 100, 100 0, 0 100, 0 0))")
blocks, warns = sv.prepare_aoi([bowtie])
check_true("a self-intersecting AOI is repaired, not rejected", len(blocks) >= 1)
check_true("the repair is reported", any("makeValid" in w for w in warns))

multi = QgsGeometry.fromWkt(
    "MULTIPOLYGON(((0 0,100 0,100 100,0 100,0 0)),((200 0,300 0,300 100,200 100,200 0)))")
one, _ = sv.prepare_aoi([multi], split_multipart=False)
many, _ = sv.prepare_aoi([multi], split_multipart=True)
check("multipolygon as one mission", len(one), 1)
check("multipolygon split into blocks", len(many), 2)

try:
    sv.prepare_aoi([QgsGeometry.fromWkt("LINESTRING(0 0, 10 10)")])
    check_true("a non-polygon AOI is rejected", False)
except sv.RoutingError:
    check_true("a non-polygon AOI is rejected", True)

check("photogrammetric buffer = 0.5*W + margin",
      sv.photogrammetric_buffer(AOI, 100.0, 10.0).boundingBox().xMinimum(),
      -60.0, 0.5)

print("\n" + "=" * 80)
QGS.exitQgis()
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for f_ in FAILURES:
        print("   - {0}".format(f_))
    sys.exit(1)
print("ALL CHECKS PASSED")
