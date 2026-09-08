"""
M1 tests: units, CAD geometry engine, 2-D transforms, dynamic input.

Pure numerics -- runs on the plain system Python:

    python geocad_uav/tests/test_geometry.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from geocad_uav.cad import dynamic_input as di               # noqa: E402
from geocad_uav.core import geometry_engine as ge            # noqa: E402
from geocad_uav.core import planar as pl                     # noqa: E402
from geocad_uav.core import transform2d as tf2               # noqa: E402
from geocad_uav.core import units as un                      # noqa: E402
from geocad_uav.core.errors import (ConstraintError,         # noqa: E402
                                    InvalidInputError, UnitError)

FAILURES = []


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<56} got={2:<17.10g} exp={3:.10g}".format(
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


# ------------------------------------------------------------------ units --
print("\n== units ==")
check("1 ft is exactly 0.3048 m", un.to_metres(1, "ft"), 0.3048, 0)
check("1 in is exactly 0.0254 m", un.to_metres(1, "in"), 0.0254, 0)
check("2.5 km -> m", un.to_metres(2.5, "km"), 2500.0, 0)
check("round trip m -> ft -> m", un.to_metres(un.from_metres(123.456, "ft"), "ft"),
      123.456, 1e-12)
check("aliases: 'metri' == 'm'", un.to_metres(5, "metri"), 5.0, 0)
check("1 ha == 10000 m2", un.area_to_sq_metres(1, "ha"), 10_000.0, 0)
check("1 acre == 4046.8564224 m2", un.area_to_sq_metres(1, "acre"),
      4046.8564224, 1e-9)
check("400 gon == 360 deg", un.to_degrees(400, "gon"), 360.0, 1e-12)
check("pi rad == 180 deg", un.to_degrees(math.pi, "rad"), 180.0, 1e-12)
check("1 kt == 0.5144444 m/s", un.speed_to_ms(1, "kt"), 1852.0 / 3600.0, 1e-12)
check("wrap_signed(270)", un.wrap_signed(270.0), -90.0)
check("wrap_degrees(-10)", un.wrap_degrees(-10.0), 350.0)
check_true("format_duration", un.format_duration(3870) == "1h 04m 30s")
check_raises("unknown unit is rejected", UnitError, un.to_metres, 1, "furlong")

# -------------------------------------------------------- geometry engine --
print("\n== square: spec golden values (side 10) ==")
sq = ge.square_from((0.0, 0.0), side_m=10.0)
check("area == 100 m2", ge.polygon_area(sq), 100.0, 1e-9)
check("perimeter == 40 m", ge.polygon_perimeter(sq), 40.0, 1e-9)
check_true("ring is closed", bool(np.allclose(sq[0], sq[-1])))
check_true("5 points", sq.shape[0] == 5)

sq45 = ge.square_from((0.0, 0.0), azimuth_deg=45.0, side_m=10.0)
check("rotation 45 deg preserves area", ge.polygon_area(sq45), 100.0, 1e-9)
check("rotation 45 deg preserves perimeter", ge.polygon_perimeter(sq45), 40.0, 1e-9)
check("rotated square bbox width == 10*sqrt(2)",
      float(sq45[:, 0].max() - sq45[:, 0].min()), 10.0 * math.sqrt(2.0), 1e-9)

check("square from diagonal 14.142 -> side 10",
      ge.polygon_perimeter(ge.square_from((0, 0), diagonal_m=10 * math.sqrt(2))),
      40.0, 1e-9)
check("square from area 100 -> side 10",
      ge.polygon_perimeter(ge.square_from((0, 0), area_m2=100.0)), 40.0, 1e-9)
check("square from perimeter 40 -> area 100",
      ge.polygon_area(ge.square_from((0, 0), perimeter_m=40.0)), 100.0, 1e-9)
check_raises("square needs exactly one constraint", InvalidInputError,
             ge.square_from, (0, 0), side_m=10.0, area_m2=100.0)
check_raises("square rejects a negative side", InvalidInputError,
             ge.square_from, (0, 0), side_m=-5.0)

print("\n== rectangle ==")
rect = ge.rectangle_from_center((100.0, 200.0), width_m=25.0, height_m=12.0,
                                azimuth_deg=15.0)
check("area == 25*12", ge.polygon_area(rect), 300.0, 1e-9)
check("perimeter == 2*(25+12)", ge.polygon_perimeter(rect), 74.0, 1e-9)
cen = ge.polygon_centroid(rect)
check("centroid x preserved", cen[0], 100.0, 1e-9)
check("centroid y preserved", cen[1], 200.0, 1e-9)
# side lengths, in order, must be 25 / 12 / 25 / 12
sides = np.hypot(*np.diff(rect, axis=0).T)
check_true("sides are exactly 25 and 12 alternating",
           bool(np.allclose(sorted(np.round(sides, 9)), [12, 12, 25, 25])))
# The height axis must lie at the requested azimuth. rectangle_corners emits
# (+along,-across), (+along,+across), (-along,+across), (-along,-across), so the
# height axis joins the midpoints of {0,1} and {2,3}; {0,3} would be the width axis.
mid_far = 0.5 * (rect[0] + rect[1])
mid_near = 0.5 * (rect[2] + rect[3])
check("height axis bearing == azimuth",
      pl.azimuth_of(*(mid_far - mid_near)) % 180.0, 15.0, 1e-9)
check("height axis length == height", float(np.hypot(*(mid_far - mid_near))),
      12.0, 1e-9)
mid_left = 0.5 * (rect[0] + rect[3])
check("width axis is perpendicular to it",
      float(np.hypot(*(0.5 * (rect[1] + rect[2]) - mid_left))), 25.0, 1e-9)

corner_rect = ge.rectangle_from_opposite_corners((0, 0), (30, 20))
check("corner rectangle area", ge.polygon_area(corner_rect), 600.0, 1e-9)
check_raises("collinear corners rejected", ConstraintError,
             ge.rectangle_from_opposite_corners, (0, 0), (30, 0))

base_rect = ge.rectangle_from_base((0, 0), (40, 0), height_m=10.0, side="left")
check("base rectangle area", ge.polygon_area(base_rect), 400.0, 1e-9)
check_true("side='left' grows north of an eastward base",
           float(base_rect[:, 1].max()) > 1e-9
           and abs(float(base_rect[:, 1].min())) < 1e-9)
base_r = ge.rectangle_from_base((0, 0), (40, 0), height_m=10.0, side="right")
check_true("side='right' grows the other way",
           float(base_r[:, 1].min()) < -1e-9)

print("\n== circle: spec golden values (r=10, 72 segments) ==")
circ = ge.circle_ring((0.0, 0.0), 10.0)
true_area = math.pi * 100.0
approx = ge.polygon_area(circ)
err_pct = 100.0 * abs(approx - true_area) / true_area
check_true("area within 0.5 % of pi*r^2 ({0:.4f} % low)".format(err_pct),
           err_pct <= 0.5)
check("72 segments -> 73 closed points", circ.shape[0], 73)
check("every vertex is exactly r from the centre",
      float(np.max(np.abs(np.hypot(circ[:, 0], circ[:, 1]) - 10.0))), 0.0, 1e-9)
check("radius from area", ge.circle_radius_from_area(true_area), 10.0, 1e-12)
check("radius from circumference",
      ge.circle_radius_from_circumference(2 * math.pi * 10.0), 10.0, 1e-12)

c3, r3 = ge.circle_from_3_points((10, 0), (0, 10), (-10, 0))
check("circumcircle centre x", c3[0], 0.0, 1e-9)
check("circumcircle centre y", c3[1], 0.0, 1e-9)
check("circumcircle radius", r3, 10.0, 1e-9)
check_raises("collinear points rejected", ConstraintError,
             ge.circle_from_3_points, (0, 0), (5, 0), (10, 0))

c2, r2 = ge.circle_from_2_points_radius((0, 0), (10, 0), 13.0)
check("2-point circle keeps the radius", r2, 13.0, 1e-12)
check("2-point circle is equidistant from both",
      float(np.hypot(*(c2 - np.array([0.0, 0.0])))
            - np.hypot(*(c2 - np.array([10.0, 0.0])))), 0.0, 1e-9)
check("2-point circle centre offset = sqrt(r^2 - (d/2)^2)",
      abs(float(c2[1])), math.sqrt(169.0 - 25.0), 1e-9)
check_raises("radius smaller than half the chord rejected", ConstraintError,
             ge.circle_from_2_points_radius, (0, 0), (10, 0), 4.0)

print("\n== regular polygon ==")
hexa = ge.regular_polygon((0, 0), 6, radius_m=10.0)
check("hexagon has 7 closed points", hexa.shape[0], 7)
check("hexagon side == radius", float(np.hypot(*(hexa[1] - hexa[0]))), 10.0, 1e-9)
check("hexagon area = (3*sqrt(3)/2) r^2", ge.polygon_area(hexa),
      1.5 * math.sqrt(3.0) * 100.0, 1e-9)
check("polygon from apothem matches radius form",
      ge.polygon_area(ge.regular_polygon((0, 0), 6,
                                         apothem_m=10.0 * math.cos(math.pi / 6))),
      ge.polygon_area(hexa), 1e-9)
check("polygon from side matches radius form",
      ge.polygon_area(ge.regular_polygon((0, 0), 6, side_m=10.0)),
      ge.polygon_area(hexa), 1e-9)
check("polygon from area round-trips",
      ge.polygon_area(ge.regular_polygon((0, 0), 6, area_m2=250.0)), 250.0, 1e-9)
check("first vertex sits at the requested azimuth",
      pl.azimuth_of(*ge.regular_polygon((0, 0), 5, azimuth_deg=37.0,
                                        radius_m=10.0)[0]), 37.0, 1e-9)
check_raises("polygon needs >= 3 sides", InvalidInputError,
             ge.regular_polygon, (0, 0), 2, radius_m=10.0)

print("\n== ellipse ==")
ell = ge.ellipse_ring((0, 0), 20.0, 10.0, azimuth_deg=0.0, segments=360)
check_true("ellipse area approximates pi*a*b",
           abs(ge.polygon_area(ell) - math.pi * 200.0) / (math.pi * 200.0) < 0.001)
check("major axis extent along azimuth 0 (north-south)",
      float(ell[:, 1].max() - ell[:, 1].min()), 40.0, 1e-9)
check("minor axis extent across", float(ell[:, 0].max() - ell[:, 0].min()),
      20.0, 1e-9)
check_raises("minor > major rejected", ConstraintError,
             ge.ellipse_ring, (0, 0), 10.0, 20.0)

print("\n== line and constraints ==")
ln = ge.line_from_length_azimuth((0, 0), 100.0, 30.0)
check("line length is exact", float(np.hypot(*(ln[1] - ln[0]))), 100.0, 1e-12)
check("line bearing is exact", pl.azimuth_of(*(ln[1] - ln[0])), 30.0, 1e-12)

trav = ge.polyline_from_segments((0, 0), [(100, 0), (100, 90),
                                          (100, 180), (100, 270)])
check("closed traverse returns to the origin (float64, not pixels)",
      float(np.hypot(*(trav[-1] - trav[0]))), 0.0, 1e-9)

check("apply_length_constraint sets an exact length",
      float(np.hypot(*(ge.apply_length_constraint((0, 0), (3, 4), 100.0)))),
      100.0, 1e-9)
check("apply_azimuth_constraint keeps the length",
      float(np.hypot(*(ge.apply_azimuth_constraint((0, 0), (3, 4), 77.0)))),
      5.0, 1e-9)
check("perpendicular foot", float(ge.perpendicular_foot((5, 5), (0, 0), (10, 0))[1]),
      0.0, 1e-12)
check("interior angle of a right corner",
      ge.interior_angle((10, 0), (0, 0), (0, 10)), 90.0, 1e-9)

# ------------------------------------------------------------- transforms --
print("\n== transform2d ==")
pt = np.array([[1.0, 0.0]])
rot = tf2.rotate(pt, 90.0, pivot=(0, 0))
check_true("positive rotation is CLOCKWISE: East -> South",
           abs(float(rot[0, 0])) < 1e-12 and abs(float(rot[0, 1]) + 1.0) < 1e-12)
check("rotation preserves area", ge.polygon_area(tf2.rotate(sq, 33.0)), 100.0, 1e-9)
check("rotation preserves distance from pivot",
      float(np.hypot(*tf2.rotate(np.array([[3.0, 4.0]]), 61.0, (0, 0))[0])),
      5.0, 1e-12)
check("four 90 deg rotations are the identity",
      float(np.max(np.abs(tf2.rotate(tf2.rotate(tf2.rotate(tf2.rotate(
          sq, 90), 90), 90), 90) - sq))), 0.0, 1e-9)

check("uniform scale 2x quadruples the area",
      ge.polygon_area(tf2.scale(sq, 2.0)), 400.0, 1e-9)
check("anisotropic scale", ge.polygon_area(tf2.scale(sq, 2.0, 3.0)), 600.0, 1e-9)
check_raises("zero scale rejected", InvalidInputError, tf2.scale, sq, 0.0)

mir = tf2.mirror(np.array([[5.0, 3.0]]), (0, 0), (1, 0))
check("mirror across the x axis flips y", float(mir[0, 1]), -3.0, 1e-12)
check("mirroring twice is the identity",
      float(np.max(np.abs(tf2.mirror(tf2.mirror(sq, (0, 0), (1, 1)),
                                     (0, 0), (1, 1)) - sq))), 0.0, 1e-9)
check("mirror preserves area", ge.polygon_area(tf2.mirror(sq, (0, 0), (1, 2))),
      100.0, 1e-9)
check_raises("degenerate mirror axis rejected", ConstraintError,
             tf2.mirror, sq, (0, 0), (0, 0))

grid = tf2.array_rectangular(pt, n_cols=4, n_rows=3, spacing_x=10.0,
                             spacing_y=5.0, azimuth_deg=0.0)
check("rectangular array count", len(grid), 12)
xs = sorted({round(float(g[0, 0]), 6) for g in grid})
ys = sorted({round(float(g[0, 1]), 6) for g in grid})
check_true("array column spacing is 10 m eastward",
           np.allclose(np.diff(xs), 10.0))
check_true("array row spacing is 5 m northward", np.allclose(np.diff(ys), 5.0))

pol = tf2.array_polar(np.array([[10.0, 0.0]]), (0, 0), count=4,
                      total_angle_deg=360.0)
check("polar array over 360 deg makes 4 distinct copies", len(pol), 4)
check_true("polar copies keep the radius",
           all(abs(float(np.hypot(*p[0])) - 10.0) < 1e-9 for p in pol))
# wrap_degrees after rounding: a 270 deg turn lands on 359.999999..., which
# round() snaps to 360.0 rather than 0.0.
bearings = sorted(un.wrap_degrees(round(pl.azimuth_of(*p[0]), 6)) for p in pol)
check_true("polar copies are 90 deg apart, first not duplicated",
           np.allclose(bearings, [0.0, 90.0, 180.0, 270.0]))
check_raises("polar array needs exactly one angle spec", InvalidInputError,
             tf2.array_polar, pt, (0, 0), 4, total_angle_deg=90.0,
             step_angle_deg=30.0)

# --------------------------------------------------------- dynamic input ---
print("\n== dynamic input ==")
tok = di.parse("25")
check_true("bare number is a length", tok.kind == di.KIND_LENGTH)
check("bare number uses the current unit", tok.length_m, 25.0)
check("explicit unit wins", di.parse("25ft").length_m, 25.0 * 0.3048, 1e-12)
check("panel unit is applied", di.parse("250", length_unit="cm").length_m, 2.5, 1e-12)

tok = di.parse("37d")
check_true("'37d' is an angle", tok.kind == di.KIND_ANGLE)
check("angle value", tok.angle_deg, 37.0)
check("radians are converted", di.parse("0.5rad").angle_deg,
      math.degrees(0.5), 1e-12)
check("gon are converted", di.parse("50gon").angle_deg, 45.0, 1e-12)

tok = di.parse("@25<37")
check_true("'@25<37' is polar", tok.kind == di.KIND_POLAR)
check("polar distance", tok.length_m, 25.0)
check("polar angle", tok.angle_deg, 37.0)
check_true("polar angle is relative to the previous segment",
           tok.angle_is_relative)

tok = di.parse("#100.5,200.25")
check_true("'#x,y' is absolute", tok.kind == di.KIND_ABSOLUTE)
check("absolute x", tok.x_m, 100.5)
check("absolute y", tok.y_m, 200.25)

tok = di.parse("@10,-5")
check_true("'@dx,dy' is relative", tok.kind == di.KIND_RELATIVE)
check("relative dx", tok.dx_m, 10.0)
check("relative dy", tok.dy_m, -5.0)

check_raises("garbage is rejected", InvalidInputError, di.parse, "hello")
check_raises("empty input is rejected", InvalidInputError, di.parse, "   ")

# resolution against drawing state
p = di.resolve(di.parse("#100,200"), last_point=(0, 0))
check("absolute resolves ignoring the last point", float(p[0]), 100.0)
p = di.resolve(di.parse("@10,20"), last_point=(5, 5))
check_true("relative resolves from the last point",
           abs(float(p[0]) - 15.0) < 1e-12 and abs(float(p[1]) - 25.0) < 1e-12)
p = di.resolve(di.parse("@100<90"), last_point=(0, 0), last_azimuth_deg=0.0)
check("polar 90 deg off a northward segment goes due east", float(p[0]), 100.0, 1e-9)
check("...and does not move north", float(p[1]), 0.0, 1e-9)
p = di.resolve(di.parse("@100<0"), last_point=(0, 0), last_azimuth_deg=45.0)
check("polar 0 continues straight on the previous bearing",
      pl.azimuth_of(float(p[0]), float(p[1])), 45.0, 1e-9)
p = di.resolve(di.parse("50"), last_point=(0, 0), last_azimuth_deg=90.0)
check("bare length follows the current direction", float(p[0]), 50.0, 1e-9)
p = di.resolve(di.parse("30d"), last_point=(0, 0), pending_length_m=100.0)
check("bare angle completes a pending length",
      pl.azimuth_of(float(p[0]), float(p[1])), 30.0, 1e-9)
check_raises("bare length with no direction is rejected", InvalidInputError,
             di.resolve, di.parse("25"), (0, 0))
check_raises("bare angle with no length is rejected", InvalidInputError,
             di.resolve, di.parse("25d"), (0, 0))
check("segment_azimuth of an eastward segment",
      di.segment_azimuth((0, 0), (10, 0)), 90.0, 1e-12)

print("\n" + "=" * 80)
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for f_ in FAILURES:
        print("   - {0}".format(f_))
    sys.exit(1)
print("ALL CHECKS PASSED")
