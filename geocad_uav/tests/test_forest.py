"""
M4 tests: Forest Planting Designer, including the DEM filters.

Pure numerics -- runs on the plain system Python:

    python geocad_uav/tests/test_forest.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from geocad_uav.core import grid as gr                        # noqa: E402
from geocad_uav.core import z as zc                           # noqa: E402
from geocad_uav.forest import planting as fp                  # noqa: E402
from geocad_uav.forest import stats as fs                     # noqa: E402

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


# ------------------------------------------------------------- fixtures ---
# AOI: 200 x 100 m rectangle = 20 000 m2 = exactly 2 ha.
X0, Y0, X1, Y1 = 0.0, 0.0, 200.0, 100.0
MARGIN = 2.0
AOI_AREA = (X1 - X0) * (Y1 - Y0)
USABLE_AREA = (X1 - X0 - 2 * MARGIN) * (Y1 - Y0 - 2 * MARGIN)


def inside_eroded(points):
    """Point-in-eroded-rectangle, the analytic stand-in for a GEOS predicate."""
    return ((points[:, 0] >= X0 + MARGIN) & (points[:, 0] <= X1 - MARGIN)
            & (points[:, 1] >= Y0 + MARGIN) & (points[:, 1] <= Y1 - MARGIN))


# ------------------------------------------- spec case: 2 ha, 3x2 m, 15 deg
print("\n== spec case: 2 ha, sesto 3 x 2 m, 15 deg, margine 2 m ==")
spec = gr.GridSpec(spacing_x=3.0, spacing_y=2.0, azimuth_deg=15.0,
                   margin_m=MARGIN)
result = fp.plan_planting((X0, Y0, X1, Y1), spec, inside=inside_eroded,
                          aoi_area_m2=AOI_AREA, usable_area_m2=USABLE_AREA)
st = fs.compute_stats(result)

check("AOI area is 2 ha", st.aoi_area_ha, 2.0, 1e-9)
check("usable area after 2 m erosion", st.usable_area_m2, USABLE_AREA, 1e-9)
check("theoretical count = usable / (3*2)", result.theoretical_count,
      math.floor(USABLE_AREA / 6.0))
print("        actual {0}, theoretical {1}, fill {2:.1%}".format(
    st.n_plants, st.theoretical_count, st.fill_ratio))
check_true("actual count <= theoretical count",
           st.n_plants <= result.theoretical_count)
check_true("actual count is a sensible fraction of theoretical (>85 %)",
           st.fill_ratio > 0.85)

pts = np.array([[p.x, p.y] for p in result.plants])
check_true("every plant is inside the eroded AOI",
           bool(np.all(inside_eroded(pts))))
check("no plant is nearer the edge than the margin",
      float(min(pts[:, 0].min() - X0, Y1 - pts[:, 1].max(),
                X1 - pts[:, 0].max(), pts[:, 1].min() - Y0)),
      MARGIN, 2.0)
check_true("no plant lies outside the gross AOI either",
           bool(np.all((pts[:, 0] >= X0) & (pts[:, 0] <= X1)
                       & (pts[:, 1] >= Y0) & (pts[:, 1] <= Y1))))

check("theoretical density = 10000/(3*2)", st.theoretical_density_per_ha,
      10_000.0 / 6.0, 1e-9)
check_true("actual density is within 15 % of theoretical ({0:.0f} vs {1:.0f})"
           .format(st.density_per_ha, st.theoretical_density_per_ha),
           abs(st.density_per_ha - st.theoretical_density_per_ha)
           / st.theoretical_density_per_ha < 0.15)

row_ids = [p.row_id for p in result.plants]
check_true("row_id starts at 1", min(row_ids) == 1)
check_true("row_id has no gaps (monotone numbering)",
           sorted(set(row_ids)) == list(range(1, max(row_ids) + 1)))
check("n_rows agrees with the record ids", st.n_rows, max(row_ids))
for rid in set(row_ids):
    seqs = sorted(p.seq_in_row for p in result.plants if p.row_id == rid)
    if seqs != list(range(1, len(seqs) + 1)):
        check_true("row {0} seq_in_row is consecutive from 1".format(rid), False)
        break
else:
    check_true("seq_in_row is consecutive from 1 in every row", True)

check_true("plant_id is unique and consecutive",
           [p.plant_id for p in result.plants] == list(range(1, st.n_plants + 1)))
check_true("row spacing matches the sesto",
           abs(st.nearest_neighbour_mean_m - 2.0) < 0.5)
check_true("excluded candidates are retained with a reason",
           len(result.excluded) > 0
           and all(e.excluded_reason for e in result.excluded))
check_true("exclusion summary is populated", bool(result.exclusion_summary()))

# ---------------------------------------------------- topographic filters --
print("\n== topographic filters ==")
CELL = 2.0
NX, NY = 150, 100
GT = (X0, CELL, 0.0, Y1, 0.0, -CELL)
xs = X0 + (np.arange(NX) + 0.5) * CELL
ys = Y1 - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(xs, ys)
# Plane rising to the east at 20 %: slope 11.31 deg, aspect 270 (faces west).
ZZ = 100.0 + 0.20 * (XX - X0)
terrain = zc.TerrainModel(ZZ, GT, "EPSG:32632")

slope_grid, aspect_grid = zc.slope_aspect(terrain)
check("Horn slope on a 20 % plane", float(np.nanmedian(slope_grid)),
      math.degrees(math.atan(0.20)), 1e-6)
check("Horn aspect on an east-rising plane faces west (270)",
      float(np.nanmedian(aspect_grid)), 270.0, 1e-6)


def elevation_fn(points):
    return terrain.sample(points[:, 0], points[:, 1])


def slope_aspect_fn(points):
    return zc.sample_slope_aspect(terrain, points[:, 0], points[:, 1])


# Slope filter that everything passes.
loose = fp.TopographicFilter(slope_max_deg=30.0)
res_loose = fp.plan_planting((X0, Y0, X1, Y1), spec, inside=inside_eroded,
                             aoi_area_m2=AOI_AREA, usable_area_m2=USABLE_AREA,
                             elevation=elevation_fn,
                             slope_aspect=slope_aspect_fn, topo_filter=loose)
check("slope filter 0-30 deg keeps everything on an 11.3 deg plane",
      len(res_loose.plants), st.n_plants)
check_true("elevation was sampled onto the records",
           all(p.z is not None for p in res_loose.plants))
check_true("slope was sampled onto the records",
           all(p.slope_deg is not None for p in res_loose.plants))

# Slope filter that nothing passes.
tight = fp.TopographicFilter(slope_max_deg=5.0)
res_tight = fp.plan_planting((X0, Y0, X1, Y1), spec, inside=inside_eroded,
                             aoi_area_m2=AOI_AREA, usable_area_m2=USABLE_AREA,
                             elevation=elevation_fn,
                             slope_aspect=slope_aspect_fn, topo_filter=tight)
check("slope filter 0-5 deg rejects an 11.3 deg plane", len(res_tight.plants), 0)
check_true("rejections are attributed to the slope",
           res_tight.exclusion_summary().get(fp.REASON_SLOPE, 0) > 0)

# Elevation band: the plane runs 100 m (west) to 140 m (east).
band = fp.TopographicFilter(elev_min_m=110.0, elev_max_m=130.0)
res_band = fp.plan_planting((X0, Y0, X1, Y1), spec, inside=inside_eroded,
                            aoi_area_m2=AOI_AREA, usable_area_m2=USABLE_AREA,
                            elevation=elevation_fn,
                            slope_aspect=slope_aspect_fn, topo_filter=band)
zs = [p.z for p in res_band.plants]
check_true("elevation filter keeps only the 110-130 m band",
           len(zs) > 0 and min(zs) >= 110.0 - 1e-6 and max(zs) <= 130.0 + 1e-6)
check_true("elevation filter actually removed something",
           len(res_band.plants) < st.n_plants)
check_true("rejections are attributed to elevation",
           res_band.exclusion_summary().get(fp.REASON_ELEVATION, 0) > 0)

# Aspect: the plane faces west (270), so a west sector keeps all.
west = fp.TopographicFilter(aspect_ranges=[(225.0, 315.0)])
res_west = fp.plan_planting((X0, Y0, X1, Y1), spec, inside=inside_eroded,
                            aoi_area_m2=AOI_AREA, usable_area_m2=USABLE_AREA,
                            elevation=elevation_fn,
                            slope_aspect=slope_aspect_fn, topo_filter=west)
check("west-facing sector keeps a west-facing slope",
      len(res_west.plants), st.n_plants)

east = fp.TopographicFilter(aspect_ranges=[(45.0, 135.0)])
res_east = fp.plan_planting((X0, Y0, X1, Y1), spec, inside=inside_eroded,
                            aoi_area_m2=AOI_AREA, usable_area_m2=USABLE_AREA,
                            elevation=elevation_fn,
                            slope_aspect=slope_aspect_fn, topo_filter=east)
check("east-facing sector rejects a west-facing slope", len(res_east.plants), 0)

# Wrap-around sector through north.
print("\n== aspect sector wrapping through north ==")
wrap = fp.TopographicFilter(aspect_ranges=[(315.0, 45.0)])
keep, _ = wrap.evaluate(np.array([10.0, 10.0, 10.0, 10.0, 10.0]),
                        np.array([0.0, 30.0, 330.0, 180.0, 90.0]),
                        np.array([100.0] * 5))
check_true("315-45 accepts 0, 30 and 330",
           bool(keep[0] and keep[1] and keep[2]))
check_true("315-45 rejects 180 and 90", bool(not keep[3] and not keep[4]))

nodata = fp.TopographicFilter(slope_max_deg=45.0, require_elevation=True)
keep, reasons = nodata.evaluate(np.array([1.0, np.nan]),
                                np.array([10.0, np.nan]),
                                np.array([100.0, np.nan]))
check_true("no-data positions are rejected, not guessed",
           bool(keep[0]) and not bool(keep[1])
           and reasons[1] == fp.REASON_NO_DEM)

filt = fp.TopographicFilter(slope_min_deg=5.0, slope_max_deg=25.0,
                            aspect_ranges=[(0.0, 90.0)])
check_true("filter description is human readable", len(filt.describe()) == 2)
check_true("inactive filter is detected",
           not fp.TopographicFilter().is_active)

# ------------------------------------------------------------- reporting --
print("\n== KPI report ==")
lines = fs.format_report(st, result)
check_true("report mentions density", any("Densita" in ln for ln in lines))
check_true("report mentions the sesto", any("Sesto" in ln for ln in lines))
check_true("report lists exclusions", any("scartate" in ln for ln in lines))
print("        " + "\n        ".join(lines[:8]))

print("\n" + "=" * 80)
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for f_ in FAILURES:
        print("   - {0}".format(f_))
    sys.exit(1)
print("ALL CHECKS PASSED")
