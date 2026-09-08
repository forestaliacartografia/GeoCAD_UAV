"""
M3 tests: Grid Designer lattice arithmetic.

Pure numerics -- runs on the plain system Python:

    python geocad_uav/tests/test_grid.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from geocad_uav.core import grid as gr                       # noqa: E402
from geocad_uav.core import planar as pl                     # noqa: E402
from geocad_uav.core.errors import InvalidInputError         # noqa: E402

FAILURES = []


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<56} got={2:<15.10g} exp={3:.10g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def check_raises(label, exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        check_true(label, True)
        return
    except Exception as exc:                                  # noqa: BLE001
        print("  [FAIL] {0} (raised {1})".format(label, type(exc).__name__))
        FAILURES.append(label)
        return
    print("  [FAIL] {0} (nothing raised)".format(label))
    FAILURES.append(label)


# ---------------------------------------------- spec golden case ----------
print("\n== spec case: AOI 100x100, step 5x5, no margin -> 21x21 = 441 ==")
spec = gr.GridSpec(spacing_x=5.0, spacing_y=5.0, pattern=gr.PATTERN_RECT)
res = gr.generate_grid((0.0, 0.0, 100.0, 100.0), spec)
check("total points", len(res), 441)
check("rows", res.n_rows, 21)
check("columns", res.n_cols, 21)
check("min x", float(res.xy[:, 0].min()), 0.0)
check("max x is the far edge, inclusive", float(res.xy[:, 0].max()), 100.0)
check("min y", float(res.xy[:, 1].min()), 0.0)
check("max y is the far edge, inclusive", float(res.xy[:, 1].max()), 100.0)
xs = np.unique(np.round(res.xy[:, 0], 9))
check_true("column spacing is exactly 5 m", bool(np.allclose(np.diff(xs), 5.0)))

# --------------------------------------------------------- spacing --------
print("\n== spacing and patterns ==")
rect = gr.generate_grid((0, 0, 30, 20),
                        gr.GridSpec(spacing_x=3.0, spacing_y=2.0))
check("non-square spacing: 11 cols x 11 rows", len(rect), 11 * 11)
check("dx honoured", float(np.unique(np.round(rect.xy[:, 0], 9))[1]), 3.0)
check("dy honoured", float(np.unique(np.round(rect.xy[:, 1], 9))[1]), 2.0)

sq = gr.GridSpec(spacing_x=4.0, spacing_y=99.0, pattern=gr.PATTERN_SQUARE)
check("square pattern forces dy = dx", sq.effective_spacing[1], 4.0)

hexs = gr.GridSpec(spacing_x=10.0, spacing_y=99.0, pattern=gr.PATTERN_HEX)
check("hex row spacing = dx*sqrt(3)/2", hexs.effective_spacing[1],
      10.0 * math.sqrt(3.0) / 2.0, 1e-12)
hex_res = gr.generate_grid((0, 0, 100, 100), hexs)
row0 = hex_res.xy[hex_res.row == 0]
row1 = hex_res.xy[hex_res.row == 1]
off = float(np.min(np.abs(np.sort(row1[:, 0])[:, None]
                          - np.sort(row0[:, 0])[None, :])))
check_true("hex rows are offset by half a step",
           abs(off - 5.0) < 1e-6 or abs(off) < 1e-6)
# a true hex lattice: nearest neighbour equals dx in every direction
p0 = row0[np.argmin(np.abs(row0[:, 0] - 50.0))]
d = np.hypot(hex_res.xy[:, 0] - p0[0], hex_res.xy[:, 1] - p0[1])
d = np.sort(d[d > 1e-9])
check("hex nearest-neighbour distance == dx", float(d[0]), 10.0, 1e-6)

quin = gr.generate_grid((0, 0, 40, 40),
                        gr.GridSpec(spacing_x=10.0, spacing_y=10.0,
                                    pattern=gr.PATTERN_QUINCUNX))
r0 = np.sort(quin.xy[quin.row == 0][:, 0])
r1 = np.sort(quin.xy[quin.row == 1][:, 0])
check("quincunx keeps the requested dy",
      float(np.unique(np.round(quin.xy[:, 1], 9))[1]), 10.0)
check_true("quincunx staggers alternate rows by dx/2",
           abs((r1[0] - r0[0]) % 10.0 - 5.0) < 1e-6)

# ------------------------------------------------------- orientation ------
print("\n== orientation ==")
rot = gr.generate_grid((0, 0, 100, 100),
                       gr.GridSpec(spacing_x=10.0, spacing_y=10.0,
                                   azimuth_deg=30.0))
check_true("rotated grid still covers the envelope",
           float(rot.xy[:, 0].min()) <= 0.0 + 1e-6
           and float(rot.xy[:, 0].max()) >= 100.0 - 1e-6
           and float(rot.xy[:, 1].min()) <= 0.0 + 1e-6
           and float(rot.xy[:, 1].max()) >= 100.0 - 1e-6)
line = rot.xy[rot.row == 1]
line = line[np.argsort(rot.col[rot.row == 1])]
bearing = pl.azimuth_of(*(line[1] - line[0]))
check("points within a row run across the azimuth (az + 90)",
      bearing % 180.0, (30.0 + 90.0) % 180.0, 1e-6)
step = float(np.hypot(*(line[1] - line[0])))
check("spacing is preserved under rotation", step, 10.0, 1e-9)

check("azimuth_from_two_points, due east",
      gr.azimuth_from_two_points((0, 0), (5, 0)), 90.0, 1e-12)
check("azimuth_of_longest_edge picks the long side",
      gr.azimuth_of_longest_edge(np.array([[0, 0], [100, 0], [100, 20],
                                           [0, 20], [0, 0]])), 90.0, 1e-12)
check_raises("coincident pick points rejected", InvalidInputError,
             gr.azimuth_from_two_points, (1, 1), (1, 1))

# ------------------------------------------------ stability and offset ----
print("\n== anchoring and offsets ==")
a = gr.generate_grid((0, 0, 100, 100), gr.GridSpec(spacing_x=10, spacing_y=10),
                     origin_xy=(0, 0))
b = gr.generate_grid((-3, -7, 100, 100), gr.GridSpec(spacing_x=10, spacing_y=10),
                     origin_xy=(0, 0))
common = {(round(x, 6), round(y, 6)) for x, y in a.xy}
overlap = sum(1 for x, y in b.xy if (round(x, 6), round(y, 6)) in common)
check_true("lattice is anchored: enlarging the AOI does not shift points",
           overlap == len(a))

off = gr.generate_grid((0, 0, 100, 100),
                       gr.GridSpec(spacing_x=10, spacing_y=10,
                                   offset_x=2.0, offset_y=3.0),
                       origin_xy=(0, 0))
check_true("offset shifts the lattice",
           abs(float(np.unique(np.round(off.xy[:, 0], 6))[0]) - (-8.0)) < 1e-6)

# ------------------------------------------------------------ clipping ----
print("\n== clipping and renumbering (concave AOI) ==")
full = gr.generate_grid((0, 0, 100, 100), gr.GridSpec(spacing_x=10, spacing_y=10))
# L-shape: drop the north-east quadrant.
keep = ~((full.xy[:, 0] > 50.0) & (full.xy[:, 1] > 50.0))
clipped = gr.renumber(gr.filter_result(full, keep))
check("clipped count", len(clipped), int(keep.sum()))
check_true("no surviving point is in the removed quadrant",
           not bool(np.any((clipped.xy[:, 0] > 50.0) & (clipped.xy[:, 1] > 50.0))))
check("renumber compacts rows to 0..n-1", int(clipped.row.max()),
      clipped.n_rows - 1)
check_true("row indices have no gaps",
           set(np.unique(clipped.row)) == set(range(clipped.n_rows)))
for r in range(clipped.n_rows):
    cols = np.sort(clipped.col[clipped.row == r])
    if not np.array_equal(cols, np.arange(cols.size)):
        check_true("row {0} is numbered consecutively".format(r), False)
        break
else:
    check_true("every row is numbered consecutively from 0", True)

lines = clipped.row_lines()
check("row_lines returns one polyline per row", len(lines), clipped.n_rows)
check_true("row polylines are ordered along the row",
           all(np.all(np.diff(ln[:, 0]) > 0) or np.all(np.diff(ln[:, 0]) < 0)
               for ln in lines))

print("\n== serpentine ==")
serp = gr.generate_grid((0, 0, 50, 50),
                        gr.GridSpec(spacing_x=10, spacing_y=10, serpentine=True))
r0 = serp.xy[serp.row == 0][np.argsort(serp.col[serp.row == 0])]
r1 = serp.xy[serp.row == 1][np.argsort(serp.col[serp.row == 1])]
check_true("serpentine reverses alternate rows (walking order)",
           float(r0[0, 0]) < float(r0[-1, 0])
           and float(r1[0, 0]) > float(r1[-1, 0]))

print("\n== validation ==")
check_raises("zero spacing rejected", InvalidInputError, gr.GridSpec, 0.0, 5.0)
check_raises("negative spacing rejected", InvalidInputError, gr.GridSpec, 5.0, -2.0)
check_raises("negative margin rejected", InvalidInputError,
             gr.GridSpec, 5.0, 5.0, margin_m=-1.0)
check_raises("unknown pattern rejected", InvalidInputError,
             gr.GridSpec, 5.0, 5.0, pattern="spiral")
check_raises("empty bounds rejected", InvalidInputError,
             gr.generate_grid, (0, 0, 0, 0), gr.GridSpec(5.0, 5.0))
check("area per point", gr.GridSpec(3.0, 2.0).area_per_point_m2(), 6.0)

print("\n" + "=" * 78)
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for f_ in FAILURES:
        print("   - {0}".format(f_))
    sys.exit(1)
print("ALL CHECKS PASSED")
