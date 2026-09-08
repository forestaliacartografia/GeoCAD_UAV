"""
M2 tests: CAD primitives adapter, modifiers, parametric round-trip.

NEEDS QGIS (GEOS). Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_cad.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsGeometry, QgsPointXY, QgsRectangle)

from geocad_uav.cad import modifiers as md                      # noqa: E402
from geocad_uav.cad import parametric as pa                     # noqa: E402
from geocad_uav.cad import primitives as pr                     # noqa: E402
from geocad_uav.core import crs as crs_svc                      # noqa: E402
from geocad_uav.core import geometry_engine as ge               # noqa: E402
from geocad_uav.core.errors import (ConstraintError,            # noqa: E402
                                    GeographicCrsError, GeometryError)

QGS = QgsApplication([], False)
QGS.initQgis()

FAILURES = []


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<56} got={2:<16.10g} exp={3:.10g}".format(
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
    except Exception as exc:                                    # noqa: BLE001
        print("  [FAIL] {0} (raised {1})".format(label, type(exc).__name__))
        FAILURES.append(label)
        return
    print("  [FAIL] {0} (nothing raised)".format(label))
    FAILURES.append(label)


# --------------------------------------------------------------- primitives --
print("\n== primitives -> QgsGeometry ==")
geom, rec = pr.build(pr.TOOL_RECTANGLE,
                     {"mode": "center", "x": 100.0, "y": 200.0,
                      "width_m": 25.0, "height_m": 12.0, "azimuth_deg": 15.0},
                     "EPSG:32632")
check("rectangle area survives the QGIS round trip", geom.area(), 300.0, 1e-9)
check("rectangle perimeter", geom.length(), 74.0, 1e-9)
check_true("geometry is a valid polygon", geom.isGeosValid())
check("record stores the measured area", rec.params["measured_area_m2"],
      300.0, 1e-6)
check_true("record keeps the construction inputs",
           rec.params["width_m"] == 25.0 and rec.params["azimuth_deg"] == 15.0)
check_true("record carries a signature", bool(rec.params["_signature"]))

sq, _ = pr.build(pr.TOOL_SQUARE, {"x": 0, "y": 0, "side_m": 10.0}, "EPSG:32632")
check("square area", sq.area(), 100.0, 1e-9)

circ, crec = pr.build(pr.TOOL_CIRCLE,
                      {"mode": "center_radius", "x": 0, "y": 0,
                       "radius_m": 10.0}, "EPSG:32632")
check_true("circle area within 0.5 % of pi r^2",
           abs(circ.area() - math.pi * 100.0) / (math.pi * 100.0) <= 0.005)
c3, _ = pr.build(pr.TOOL_CIRCLE,
                 {"mode": "three_points", "x": 10, "y": 0, "x2": 0, "y2": 10,
                  "x3": -10, "y3": 0}, "EPSG:32632")
check_true("3-point circle matches the direct one",
           abs(c3.area() - circ.area()) < 1e-6)
by_area, _ = pr.build(pr.TOOL_CIRCLE,
                      {"mode": "center_radius", "x": 0, "y": 0,
                       "area_m2": math.pi * 100.0}, "EPSG:32632")
check_true("circle from area reproduces radius 10",
           abs(by_area.area() - circ.area()) < 1e-6)

hexa, _ = pr.build(pr.TOOL_POLYGON,
                   {"x": 0, "y": 0, "n_sides": 6, "radius_m": 10.0},
                   "EPSG:32632")
check("hexagon area", hexa.area(), 1.5 * math.sqrt(3.0) * 100.0, 1e-6)

line, lrec = pr.build(pr.TOOL_LINE,
                      {"x": 0, "y": 0, "length_m": 100.0, "azimuth_deg": 30.0},
                      "EPSG:32632")
check("line length", line.length(), 100.0, 1e-9)
check("record stores the measured length", lrec.params["measured_length_m"],
      100.0, 1e-6)

ell, _ = pr.build(pr.TOOL_ELLIPSE,
                  {"x": 0, "y": 0, "semi_major_m": 20.0, "semi_minor_m": 10.0,
                   "segments": 360}, "EPSG:32632")
check_true("ellipse area approximates pi a b",
           abs(ell.area() - math.pi * 200.0) / (math.pi * 200.0) < 0.001)

ptz, _ = pr.build(pr.TOOL_POINT, {"x": 5.0, "y": 6.0}, "EPSG:32632", z=123.4)
check("point Z is carried through", ptz.constGet().z(), 123.4, 1e-9)

# ----------------------------------------------------- parametric round trip --
print("\n== parametric round trip ==")
rebuilt, rec2 = pr.rebuild(rec)
check("rebuild reproduces the area", rebuilt.area(), 300.0, 1e-9)
check_true("rebuild reproduces the geometry exactly",
           pr.geometry_signature(rebuilt) == pr.geometry_signature(geom))

line_rebuilt, _ = pr.rebuild(lrec)
check("rebuild does not mistake the measured length for an input",
      line_rebuilt.length(), 100.0, 1e-9)

two_pt, tp_rec = pr.build(pr.TOOL_LINE,
                          {"x": 0, "y": 0, "x2": 30.0, "y2": 40.0},
                          "EPSG:32632")
check("two-point line length", two_pt.length(), 50.0, 1e-9)
tp_rebuilt, _ = pr.rebuild(tp_rec)
check("two-point line rebuilds to the same length", tp_rebuilt.length(),
      50.0, 1e-9)


class _FakeFeature:
    """Minimal stand-in for a QgsFeature in the parametric checks."""

    def __init__(self, geometry, params_json):
        self._geom = geometry
        self._params = params_json

    def geometry(self):
        return self._geom

    def __getitem__(self, key):
        if key == pa.PARAMS_FIELD:
            return self._params
        raise KeyError(key)


feat = _FakeFeature(geom, rec.to_json())
loaded = pa.read_record(feat)
check_true("record reads back from the attribute", loaded is not None
           and loaded.tool == pr.TOOL_RECTANGLE)
loaded = pa.check_integrity(feat, loaded)
check_true("unmodified geometry is not flagged broken", not loaded.broken)

moved = QgsGeometry(geom)
moved.translate(5.0, 0.0)
edited = _FakeFeature(moved, rec.to_json())
broken = pa.check_integrity(edited, pa.read_record(edited))
check_true("a hand-edited geometry is flagged broken", broken.broken)
check_raises("a broken record is not silently reapplied", GeometryError,
             pa.apply_changes, edited, broken, {"width_m": 30.0})
forced_geom, _ = pa.apply_changes(edited, broken, {"width_m": 30.0},
                                  allow_broken=True)
check("explicit override rebuilds from the parameters", forced_geom.area(),
      30.0 * 12.0, 1e-9)

changed, changed_rec = pa.apply_changes(feat, loaded, {"width_m": 30.0})
check("editing width rebuilds the rectangle", changed.area(), 360.0, 1e-9)
check("the new measurement is stored",
      changed_rec.params["measured_area_m2"], 360.0, 1e-6)
check_raises("unknown parameters are rejected", Exception,
             pa.apply_changes, feat, loaded, {"nonsense": 1.0})

attrs = pa.record_to_attributes(changed_rec)
check_true("denormalised attributes are populated",
           attrs["tool"] == pr.TOOL_RECTANGLE and abs(attrs["area"] - 360.0) < 1e-6
           and abs(attrs["rotation"] - 15.0) < 1e-9)

# ---------------------------------------------------------------- modifiers --
print("\n== offset ==")
poly = QgsGeometry.fromWkt("POLYGON((0 0, 100 0, 100 60, 0 60, 0 0))")
grown = md.offset_geometry(poly, 2.0, md.OFFSET_BUFFER)
check_true("buffer +2 m grows the area", grown.area() > poly.area())
check("buffer +2 m area is close to the analytic value", grown.area(),
      100 * 60 + 2 * 2 * (100 + 60) + math.pi * 4.0, 1.0)
shrunk = md.offset_geometry(poly, -2.0, md.OFFSET_BUFFER)
check("buffer -2 m area", shrunk.area(), 96 * 56, 1e-6)
check_raises("an offset that collapses the geometry is refused",
             ConstraintError, md.offset_geometry, poly, -40.0, md.OFFSET_BUFFER)

open_line = QgsGeometry.fromWkt("LINESTRING(0 0, 100 0)")
off_curve = md.offset_geometry(open_line, 5.0, md.OFFSET_CURVE)
check("offsetCurve keeps the length", off_curve.length(), 100.0, 1e-6)

print("\n== fillet and chamfer ==")
corner_prev, corner_v, corner_next = (0.0, 50.0), (0.0, 0.0), (50.0, 0.0)
check("max fillet radius at a right angle with 50 m legs",
      md.max_fillet_radius(corner_prev, corner_v, corner_next), 50.0, 1e-9)
arc = md.fillet_corner(corner_prev, corner_v, corner_next, 10.0, segments=64)
check_true("fillet returns an arc", arc.shape[0] == 64)
r = np.hypot(arc[:, 0] - 10.0, arc[:, 1] - 10.0)
check("every arc point is exactly r from the fillet centre",
      float(np.max(np.abs(r - 10.0))), 0.0, 1e-9)
check("arc starts on the first leg", float(arc[0, 0]), 0.0, 1e-9)
check("arc ends on the second leg", float(arc[-1, 1]), 0.0, 1e-9)
check_raises("an oversized fillet radius is refused with the maximum",
             ConstraintError, md.fillet_corner, corner_prev, corner_v,
             corner_next, 80.0)
check_raises("a collinear corner cannot be filleted", ConstraintError,
             md.fillet_corner, (-10, 0), (0, 0), (10, 0), 2.0)

cham = md.chamfer_corner(corner_prev, corner_v, corner_next, 8.0)
check_true("chamfer returns two points", cham.shape == (2, 2))
check("chamfer cut on the first leg", float(cham[0, 1]), 8.0, 1e-9)
check("chamfer cut on the second leg", float(cham[1, 0]), 8.0, 1e-9)

square_ring = ge.square_from((0, 0), side_m=100.0)
filleted, skipped = md.fillet_polyline(square_ring, 10.0, closed=True,
                                       segments=24)
check("filleting a square skips nothing", skipped, 0)
check_true("filleted square is closed",
           bool(np.allclose(filleted[0], filleted[-1])))
fg = pr.points_to_polygon(filleted)
check_true("filleted square has a smaller area than the original",
           fg.area() < 10000.0)
check("filleted square area = square minus corner offcuts", fg.area(),
      10000.0 - (4 * 100.0 - math.pi * 100.0), 2.0)
# r=80 fits each corner in isolation (legs are 100 m), but two adjacent
# fillets would each claim 80 m of the same 100 m side. The per-edge check must
# catch that and drop corners until the ring is still simple.
tight, skipped_tight = md.fillet_polyline(square_ring, 80.0, closed=True)
check_true("over-subscribed edges are detected and corners dropped",
           skipped_tight > 0)
check_true("the filleted ring is still a valid, simple polygon",
           pr.points_to_polygon(tight).isGeosValid())
check_raises("skip_impossible=False turns it into a hard error",
             ConstraintError, md.fillet_polyline, square_ring, 80.0,
             closed=True, skip_impossible=False)

print("\n== trim and extend ==")
seg = np.array([[0.0, 0.0], [50.0, 0.0]])
ext = md.extend_to(seg, (100.0, -10.0), (100.0, 10.0))
check("extend reaches the boundary", float(ext[-1, 0]), 100.0, 1e-9)
trimmed = md.trim_to(np.array([[0.0, 0.0], [100.0, 0.0]]),
                     (60.0, -10.0), (60.0, 10.0))
check("trim cuts back to the boundary", float(trimmed[-1, 0]), 60.0, 1e-9)
check_raises("a parallel boundary cannot be reached", ConstraintError,
             md.extend_to, seg, (0.0, 10.0), (50.0, 10.0))
check_raises("a boundary behind the segment is refused", ConstraintError,
             md.extend_to, seg, (-20.0, -10.0), (-20.0, 10.0))

print("\n== explode, join, split, align ==")
pieces = md.explode(QgsGeometry.fromWkt("LINESTRING(0 0, 10 0, 10 10)"))
check("explode gives one geometry per segment", len(pieces), 2)
check("exploded segment length", pieces[0].length(), 10.0, 1e-9)

joined = md.join([QgsGeometry.fromWkt("LINESTRING(0 0, 10 0)"),
                  QgsGeometry.fromWkt("LINESTRING(10 0, 20 0)")])
check("join merges touching lines", joined.length(), 20.0, 1e-9)

parts = md.split(QgsGeometry.fromWkt("POLYGON((0 0,100 0,100 100,0 100,0 0))"),
                 [(50.0, -10.0), (50.0, 110.0)])
check("split produces two parts", len(parts), 2)
check("split halves conserve the area",
      sum(p.area() for p in parts), 10000.0, 1e-6)

aligned = md.align([QgsGeometry.fromWkt("POLYGON((0 0,10 0,10 10,0 10,0 0))"),
                    QgsGeometry.fromWkt("POLYGON((50 50,60 50,60 60,50 60,50 50))")],
                   "left")
check("align moves both to the same left edge",
      aligned[1].boundingBox().xMinimum(),
      aligned[0].boundingBox().xMinimum(), 1e-9)

# --------------------------------------------------------------------- CRS --
print("\n== CRS guard (P1) ==")
wgs = QgsCoordinateReferenceSystem("EPSG:4326")
utm = QgsCoordinateReferenceSystem("EPSG:32632")
check_true("EPSG:4326 is detected as geographic", crs_svc.is_geographic(wgs))
check_true("EPSG:32632 is detected as projected",
           not crs_svc.is_geographic(utm))
check("UTM zone for Milan (9.19 E, 45.46 N)",
      crs_svc.utm_epsg_for(9.19, 45.46), 32632)
check("UTM zone in the southern hemisphere",
      crs_svc.utm_epsg_for(-58.4, -34.6), 32721)
check_true("opt-out authid is a property, not a method",
           isinstance(crs_svc.resolve_work_crs(utm, QgsRectangle(0, 0, 1, 1)).authid,
                      str))

decision = crs_svc.resolve_work_crs(utm, QgsRectangle(0, 0, 100, 100))
check_true("a projected CRS needs no transform", not decision.transform_required)

milan = QgsRectangle(9.15, 45.44, 9.22, 45.49)
decision = crs_svc.resolve_work_crs(wgs, milan)
check_true("a geographic CRS triggers a transform",
           decision.transform_required)
check("suggested working CRS is the right UTM zone",
      int(decision.authid.split(":")[1]), 32632)
check_true("the reason explains why", "geografico" in decision.reason)

opt_out = crs_svc.resolve_work_crs(wgs, milan, allow_geographic=True)
check_true("informed opt-out is possible but flagged",
           not opt_out.transform_required
           and "NON sono affidabili" in opt_out.reason)
check_raises("a geographic CRS with no geometry is a hard error",
             GeographicCrsError, crs_svc.resolve_work_crs, wgs, None)

# The acceptance criterion: 1000 m must measure 1000 m, and must not measure
# ~0.009 of anything.
p1 = QgsPointXY(500000.0, 5000000.0)
p2 = QgsPointXY(501000.0, 5000000.0)
metric_line = QgsGeometry.fromPolylineXY([p1, p2])
check("1000 m in UTM measures 1000 m", metric_line.length(), 1000.0, 0.01)
transformed = crs_svc.transform_geometry(metric_line, utm, wgs)
check_true("the same line in EPSG:4326 measures ~0.0128 degrees, not metres",
           transformed.length() < 0.02)
back = crs_svc.transform_geometry(transformed, wgs, utm)
check("round trip through WGS84 preserves the length", back.length(),
      1000.0, 0.01)

print("\n" + "=" * 80)
QGS.exitQgis()
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for f_ in FAILURES:
        print("   - {0}".format(f_))
    sys.exit(1)
print("ALL CHECKS PASSED")
