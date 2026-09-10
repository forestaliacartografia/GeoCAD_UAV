"""
v1.1.0-a/b: interactive CAD map tools.

Drives the state machine with synthetic mouse and key events, and verifies that
the tools reproduce the frozen engine's geometry exactly -- the tools must add
a canvas, not a second implementation.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_map_tools.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsFeature, QgsGeometry, QgsPointXY, QgsProject,
                       QgsRectangle, QgsWkbTypes)

QGS = QgsApplication([], False)
QGS.initQgis()

# Only after QgsApplication exists: qgis.gui pulls in Qt widgets, and importing
# it (or qgis.analysis) beforehand crashes the interpreter with no traceback.
from qgis.core import QgsVectorDataProvider                     # noqa: E402
from qgis.gui import QgsMapCanvas                               # noqa: E402
from qgis.PyQt.QtCore import Qt                                 # noqa: E402

from geocad_uav.cad import dynamic_input as di                  # noqa: E402
from geocad_uav.cad import parametric as pa                     # noqa: E402
from geocad_uav.cad import primitives as pr                     # noqa: E402
from geocad_uav.cad.tools import base as tb                     # noqa: E402
from geocad_uav.core import crs as crs_svc                      # noqa: E402
from geocad_uav.core import geometry_engine as ge               # noqa: E402
from geocad_uav.core.errors import GeoCadError, InvalidInputError  # noqa: E402
from geocad_uav.io import layer_factory as lf                   # noqa: E402

FAILURES = []
SKIPS = []


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
    except Exception as exc:                                    # noqa: BLE001
        print("  [FAIL] {0} (raised {1})".format(label, type(exc).__name__))
        FAILURES.append(label)
        return
    print("  [FAIL] {0} (nothing raised)".format(label))
    FAILURES.append(label)


def skip(label, reason):
    print("  [skip] {0}".format(label))
    print("         reason: {0}".format(reason))
    SKIPS.append((label, reason))


# --------------------------------------------------------------------------
# A minimal concrete session, to exercise the base machine on its own.
# --------------------------------------------------------------------------

class _ProbeSession(tb.CadToolSession):
    """Two length slots and a rectangular preview. Exists only for these tests."""

    tool_id = pr.TOOL_RECTANGLE
    geometry_type = "Polygon"
    title = "Probe"
    accepts_second_click = False

    def slots(self):
        return [tb.ConstraintSlot("width_m", di.KIND_LENGTH, "Larghezza"),
                tb.ConstraintSlot("height_m", di.KIND_LENGTH, "Altezza")]

    def build_params(self):
        return {"mode": "center", "x": self.origin[0], "y": self.origin[1],
                "width_m": self.value("width_m"),
                "height_m": self.value("height_m"), "azimuth_deg": 0.0}

    def preview_points(self):
        if self.origin is None or not self.all_filled:
            return None
        return ge.rectangle_from_center(self.origin, self.value("width_m"),
                                        self.value("height_m"), 0.0)


print("\n== base state machine: IDLE -> COMMIT ==")
session = _ProbeSession()
check_true("starts IDLE", session.state == tb.ToolState.IDLE)
check_true("nothing is ready yet", not session.is_ready)

session.set_origin(100.0, 200.0)
check_true("after origin click -> PICK_ORIGIN",
           session.state == tb.ToolState.PICK_ORIGIN)
check_true("origin recorded", session.origin == (100.0, 200.0))

session.submit("30")
check_true("first typed value -> TYPE_CONSTRAINT",
           session.state == tb.ToolState.TYPE_CONSTRAINT)
check("width stored in metres", session.value("width_m"), 30.0)
check_true("still not ready", not session.is_ready)

session.submit("20")
check_true("last value fills the slots -> PREVIEW",
           session.state == tb.ToolState.PREVIEW)
check_true("now ready", session.is_ready)

session.confirm()
check_true("confirm -> COMMIT", session.state == tb.ToolState.COMMIT)

print("\n== base state machine: IDLE -> Escape ==")
session = _ProbeSession()
session.set_origin(10.0, 20.0)
session.submit("5")
check_true("mid-construction is not IDLE", session.state != tb.ToolState.IDLE)
discarded = session.cancel()
check_true("cancel reports that work was discarded", discarded)
check_true("escape returns to IDLE", session.state == tb.ToolState.IDLE)
check_true("origin cleared", session.origin is None)
check_true("typed values cleared", session.value("width_m") is None)
check_true("cancel on an idle session reports nothing discarded",
           not _ProbeSession().cancel())

print("\n== base state machine: guards ==")
check_raises("typing a length before an origin is refused", InvalidInputError,
             _ProbeSession().submit, "30")
check_raises("confirm on an incomplete shape is refused", InvalidInputError,
             _ProbeSession().confirm)

wrong = _ProbeSession()
wrong.set_origin(0.0, 0.0)
check_raises("an angle where a length is required is refused",
             InvalidInputError, wrong.submit, "15d")

full = _ProbeSession()
full.set_origin(0.0, 0.0)
full.submit("30")
full.submit("20")
check_raises("a third value with no slot left is refused", InvalidInputError,
             full.submit, "10")

print("\n== units and Tab cycling ==")
imperial = _ProbeSession(length_unit="ft")
imperial.set_origin(0.0, 0.0)
imperial.submit("100")
check("panel unit is honoured: 100 ft -> metres", imperial.value("width_m"),
      100.0 * 0.3048, 1e-12)
imperial.submit("25m")
check("an explicit unit overrides the panel unit", imperial.value("height_m"),
      25.0, 1e-12)

tabbed = _ProbeSession()
tabbed.set_origin(0.0, 0.0)
check("Tab starts on slot 0", tabbed.active_slot, 0)
tabbed.next_slot()
check("Tab advances to slot 1", tabbed.active_slot, 1)
tabbed.next_slot()
check("Tab wraps back to slot 0", tabbed.active_slot, 0)

print("\n== HUD is pure text ==")
hud_session = _ProbeSession()
hud_session.set_origin(0.0, 0.0)
hud_session.hover(30.0, 40.0)
hud_lines = hud_session.hud_lines()
joined = " | ".join(hud_lines)
check_true("HUD reports the cursor position", "X 30.000" in joined)
check_true("HUD reports the delta from the origin", "dX +30.000" in joined)
check_true("HUD reports the distance", "L 50.000 m" in joined)
check_true("HUD reports the azimuth from north", "Az 36.8699" in joined)
check_true("HUD carries no emoji",
           all(ord(ch) < 0x2190 for line in hud_lines for ch in line))

print("\n== preview builds no geometry ==")
prev = _ProbeSession()
prev.set_origin(0.0, 0.0)
check_true("no preview before the shape is constrained",
           prev.preview_points() is None)
prev.submit("30")
prev.submit("20")
preview = prev.preview_points()
check_true("preview is a plain numpy array, not a QgsGeometry",
           isinstance(preview, np.ndarray))
check("preview ring is closed with 5 points", preview.shape[0], 5)


# --------------------------------------------------------------------------
# Fixtures for the tool tests
# --------------------------------------------------------------------------

WORK_CRS = QgsCoordinateReferenceSystem("EPSG:32632")
OX, OY = 500000.0, 5000000.0


def scratch_layer(geometry_type, name):
    """A CAD scratch layer with the same schema Processing writes."""
    return lf.memory_layer(geometry_type, name, WORK_CRS.authid(),
                           pa.METADATA_FIELDS)


def vertices(geom):
    return np.array([[v.x(), v.y()] for v in geom.vertices()], dtype=float)


def max_vertex_gap(a, b):
    va, vb = vertices(a), vertices(b)
    if va.shape != vb.shape:
        return float("inf")
    return float(np.max(np.hypot(*(va - vb).T)))


class Move:
    """Duck-typed stand-in for QgsMapMouseEvent in a move handler."""

    def __init__(self, x, y):
        self._point = QgsPointXY(float(x), float(y))

    def mapPoint(self):                                         # noqa: N802
        return self._point


class CountingCanvas(QgsMapCanvas):
    """Canvas that records every refresh() our own code triggers."""

    def __init__(self):
        super().__init__()
        self.refresh_calls = 0

    def refresh(self):                                          # noqa: N802
        self.refresh_calls += 1
        super().refresh()


# --------------------------------------------------------------------------
# T1 - Rectangle 30 x 20 at 15 degrees (Workflow A)
# --------------------------------------------------------------------------
print("\n== T1: Rectangle 30 x 20 rot 15 deg (Workflow A) ==")
from geocad_uav.cad.tools import rectangle as rect_tool         # noqa: E402

rect_layer = scratch_layer("Polygon", "cad_rect")
r_session = rect_tool.RectangleSession(reference=rect_tool.REFERENCE_CORNER)
r_tool = tb.BaseCadTool(r_session)

r_session.set_origin(OX, OY)                     # 2. click origin
r_session.submit("30")                           # 3. width
r_session.submit("20")                           # 4. height
r_session.submit("15d")                          # 5. angle
check_true("fully constrained -> PREVIEW",
           r_session.state == tb.ToolState.PREVIEW)

r_feature = r_tool.commit(rect_layer, WORK_CRS, rect_layer.crs())   # 6. commit
check("one feature written", rect_layer.featureCount(), 1)

r_geom = r_feature.geometry()
check("area is 30 x 20 = 600 m2", r_geom.area(), 600.0, 1e-6)
check("perimeter is 2*(30+20)", r_geom.length(), 100.0, 1e-6)

expected_rect, _ = pr.build(pr.TOOL_RECTANGLE,
                            {"mode": "corner", "x": OX, "y": OY,
                             "width_m": 30.0, "height_m": 20.0,
                             "azimuth_deg": 15.0}, WORK_CRS.authid())
check("WKT matches the engine within geom_eps",
      max_vertex_gap(r_geom, expected_rect), 0.0, 1e-6)
check("area matches the engine exactly", r_geom.area(), expected_rect.area(),
      1e-9)

r_record = pa.read_record(r_feature)
check_true("cad_params round-trips off the feature", r_record is not None)
check("cad_params width", r_record.params["width_m"], 30.0)
check("cad_params height", r_record.params["height_m"], 20.0)
check("cad_params rotation", r_record.params["azimuth_deg"], 15.0)
check_true("cad_params records the reference",
           r_record.params["mode"] == rect_tool.REFERENCE_CORNER)
check_true("tool identifier stored", r_record.tool == pr.TOOL_RECTANGLE)
check("denormalised width column", r_feature["width"], 30.0)
check("denormalised height column", r_feature["height"], 20.0)
check("denormalised rotation column", r_feature["rotation"], 15.0)
check("denormalised area column", r_feature["area"], 600.0, 1e-6)
rebuilt, _ = pr.rebuild(r_record)
check("record rebuilds the identical geometry",
      max_vertex_gap(rebuilt, r_geom), 0.0, 1e-9)
check_true("session reset itself after committing",
           r_session.state == tb.ToolState.IDLE)

print("\n-- rectangle by two opposite corners --")
drag = rect_tool.RectangleSession()
drag.set_origin(OX, OY)
drag.set_second(OX + 30.0, OY + 20.0)
check_true("two corners complete the shape", drag.state == tb.ToolState.PREVIEW)
check("derived width", drag.value("width_m"), 30.0, 1e-9)
check("derived height", drag.value("height_m"), 20.0, 1e-9)
check("drag defaults the rotation to 0", drag.value("azimuth_deg"), 0.0)
drag_params = drag.build_params()
check_true("a dragged rectangle is centre-referenced",
           drag_params["mode"] == rect_tool.REFERENCE_CENTER)
check("dragged centre x", drag_params["x"], OX + 15.0, 1e-9)
drag_geom, _ = pr.build(pr.TOOL_RECTANGLE, drag_params, WORK_CRS.authid())
check("dragged rectangle has the same area", drag_geom.area(), 600.0, 1e-6)


# --------------------------------------------------------------------------
# T2 - Circle r = 10 (Workflow B)
# --------------------------------------------------------------------------
print("\n== T2: Circle r = 10 (Workflow B) ==")
from geocad_uav.cad.tools import circle as circle_tool          # noqa: E402

circle_layer = scratch_layer("Polygon", "cad_circle")
c_session = circle_tool.CircleSession()
c_tool = tb.BaseCadTool(c_session)

c_session.set_origin(OX, OY)
c_session.submit("10")
check_true("radius fills the only slot -> PREVIEW",
           c_session.state == tb.ToolState.PREVIEW)

c_feature = c_tool.commit(circle_layer, WORK_CRS, circle_layer.crs())
c_geom = c_feature.geometry()
true_area = math.pi * 100.0
error_pct = 100.0 * abs(c_geom.area() - true_area) / true_area
check_true("area within 0.5 % of pi*r^2 ({0:.4f} % low)".format(error_pct),
           error_pct <= 0.5)
check("72 segments -> 73 closed vertices", vertices(c_geom).shape[0], 73)
c_record = pa.read_record(c_feature)
check("cad_params radius", c_record.params["radius_m"], 10.0)
check("denormalised radius column", c_feature["radius"], 10.0)
expected_circle, _ = pr.build(pr.TOOL_CIRCLE,
                              {"mode": "center_radius", "x": OX, "y": OY,
                               "radius_m": 10.0}, WORK_CRS.authid())
check("circle matches the engine", max_vertex_gap(c_geom, expected_circle),
      0.0, 1e-9)

pick = circle_tool.CircleSession()
pick.set_origin(OX, OY)
pick.set_second(OX + 6.0, OY + 8.0)
check("radius picked from a circumference point", pick.value("radius_m"),
      10.0, 1e-9)
circle_hud = " | ".join(pick.hud_lines())
check_true("HUD links radius, diameter, circumference and area",
           "R 10.000" in circle_hud and "D 20.000" in circle_hud
           and "C 62.832" in circle_hud)


# --------------------------------------------------------------------------
# T3 - Line via @25<37
# --------------------------------------------------------------------------
print("\n== T3: Line @25<37 ==")
from geocad_uav.cad.tools import line as line_tool              # noqa: E402

line_layer = scratch_layer("LineString", "cad_line")
l_session = line_tool.LineSession()
l_tool = tb.BaseCadTool(l_session)

l_session.set_origin(OX, OY)
l_session.submit("@25<37")
check_true("polar token completes the line",
           l_session.state == tb.ToolState.PREVIEW)
check("length from the polar token", l_session.value("length_m"), 25.0, 1e-9)
check("azimuth from the polar token", l_session.value("azimuth_deg"), 37.0,
      1e-9)

# The golden: the same token resolved by the already-tested parser directly.
golden = di.resolve(di.parse("@25<37"), last_point=(OX, OY),
                    last_azimuth_deg=None)
check("endpoint matches the dynamic_input golden",
      float(np.hypot(*(np.array(l_session.second) - np.asarray(golden)))),
      0.0, 1e-9)

l_feature = l_tool.commit(line_layer, WORK_CRS, line_layer.crs())
l_geom = l_feature.geometry()
check("committed line length", l_geom.length(), 25.0, 1e-9)
expected_line, _ = pr.build(pr.TOOL_LINE,
                            {"x": OX, "y": OY, "length_m": 25.0,
                             "azimuth_deg": 37.0}, WORK_CRS.authid())
check("line matches the engine", max_vertex_gap(l_geom, expected_line), 0.0,
      1e-9)
endpoint = vertices(l_geom)[-1]
check("endpoint is the golden endpoint",
      float(np.hypot(endpoint[0] - golden[0], endpoint[1] - golden[1])), 0.0,
      1e-9)

two_click = line_tool.LineSession()
two_click.set_origin(OX, OY)
two_click.set_second(OX + 30.0, OY + 40.0)
check("two-click line length", two_click.value("length_m"), 50.0, 1e-9)
check("two-click line azimuth", two_click.value("azimuth_deg"),
      math.degrees(math.atan2(30.0, 40.0)), 1e-9)

absolute = line_tool.LineSession()
absolute.submit("#{0},{1}".format(OX, OY))
check_true("#x,y with no origin sets the origin", absolute.origin == (OX, OY))
absolute.submit("@0,100")
check("@dx,dy sets the second point", absolute.value("length_m"), 100.0, 1e-9)
check("...due north", absolute.value("azimuth_deg"), 0.0, 1e-9)


# --------------------------------------------------------------------------
# T4 - Escape writes nothing
# --------------------------------------------------------------------------
print("\n== T4: Escape after the first click writes nothing ==")
esc_layer = scratch_layer("Polygon", "cad_escape")
esc_session = rect_tool.RectangleSession()
esc_tool = tb.BaseCadTool(esc_session)

esc_session.set_origin(OX, OY)
esc_session.submit("30")
esc_session.hover(OX + 10.0, OY + 10.0)
check("no feature before escape", esc_layer.featureCount(), 0)
esc_session.cancel()
check("no feature after escape", esc_layer.featureCount(), 0)
check_true("state is IDLE", esc_session.state == tb.ToolState.IDLE)
check_true("nothing left half-typed", esc_session.value("width_m") is None)
check_true("no preview remains", esc_session.preview_points() is None)
check_raises("committing after escape is refused", GeoCadError,
             esc_tool.commit, esc_layer, WORK_CRS, esc_layer.crs())
check("still no feature after the refused commit", esc_layer.featureCount(), 0)
check("no geometry was ever built by this tool", esc_tool.geometry_builds, 0)


# --------------------------------------------------------------------------
# T5 - Two commits, then undo
# --------------------------------------------------------------------------
print("\n== T5: two commits, then undo ==")
undo_layer = scratch_layer("Polygon", "cad_undo")
started = undo_layer.startEditing()
check_true("layer entered edit mode", started)

for index in range(2):
    u_session = rect_tool.RectangleSession()
    u_tool = tb.BaseCadTool(u_session)
    u_session.set_origin(OX + index * 100.0, OY)
    u_session.submit("30")
    u_session.submit("20")
    u_session.submit("0d")
    u_tool.commit(undo_layer, WORK_CRS, undo_layer.crs())

check("two commits -> two features", undo_layer.featureCount(), 2)

stack = None
if hasattr(undo_layer, "undoStack"):
    stack = undo_layer.undoStack()
if stack is None:
    skip("Ctrl+Z reduces two features to one",
         "QgsVectorLayer.undoStack() is unavailable on this build")
else:
    depth = stack.count()
    check_true("each commit pushed exactly one undo entry ({0} entries)"
               .format(depth), depth == 2)
    stack.undo()
    remaining = undo_layer.featureCount()
    if remaining == 1:
        check("undo removes exactly one feature", remaining, 1)
        stack.redo()
        check("redo restores it", undo_layer.featureCount(), 2)
    else:
        skip("Ctrl+Z reduces two features to one",
             "undoStack().undo() left {0} features; the memory provider's "
             "edit buffer does not replay headless. Not asserting a pass we "
             "cannot demonstrate.".format(remaining))
undo_layer.rollBack()


# --------------------------------------------------------------------------
# T6 - a geographic CRS never yields false distances
# --------------------------------------------------------------------------
print("\n== T6: EPSG:4326 is transformed, never measured in degrees ==")
wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
milan = QgsRectangle(9.15, 45.44, 9.22, 45.49)
geo_tool = tb.BaseCadTool(line_tool.LineSession())

decision = geo_tool.work_crs(wgs84, milan)
check_true("a geographic canvas CRS demands a transform",
           decision.transform_required)
check_true("the suggested working CRS is projected",
           not crs_svc.is_geographic(decision.work_crs))
check("suggested zone is UTM 32N",
      int(decision.authid.split(":")[1]), 32632)

# Two points ~1000 m apart, picked in degrees, taken through the tool's path.
p1 = geo_tool.to_work(9.1800, 45.4600, wgs84, decision.work_crs)
p2 = geo_tool.to_work(9.1928, 45.4600, wgs84, decision.work_crs)
geo_session = line_tool.LineSession()
geo_session.set_origin(*p1)
geo_session.set_second(*p2)
picked = geo_session.value("length_m")
print("        picked length = {0:.2f} (work CRS units)".format(picked))
check_true("the length is metres, not degrees (800 < L < 1200)",
           800.0 < picked < 1200.0)
check_true("it is emphatically NOT ~0.0128 degrees", picked > 1.0)

geo_layer = lf.memory_layer("LineString", "cad_wgs", "EPSG:4326",
                            pa.METADATA_FIELDS)
geo_feature = tb.BaseCadTool(geo_session).commit(
    geo_layer, decision.work_crs, geo_layer.crs())
back = geo_feature.geometry()
check_true("the feature stored in EPSG:4326 measures in degrees (<0.02)",
           back.length() < 0.02)
back_utm = crs_svc.transform_geometry(back, wgs84, decision.work_crs)
check("...and converts back to the metric length", back_utm.length(), picked,
      0.5)

check_raises("a geographic CRS with nothing to locate it is refused",
             GeoCadError, geo_tool.work_crs, wgs84, None)


# --------------------------------------------------------------------------
# T7 - hovering is free
# --------------------------------------------------------------------------
print("\n== T7: 100 move events build nothing and refresh nothing ==")
canvas = CountingCanvas()
canvas.setDestinationCrs(WORK_CRS)
canvas.setExtent(QgsRectangle(OX - 200, OY - 200, OX + 200, OY + 200))
QgsProject.instance().setCrs(WORK_CRS)

hover_layer = scratch_layer("Polygon", "cad_hover")
map_tool = rect_tool.create(canvas, iface=None,
                            layer_provider=lambda: hover_layer)
map_tool.activate()
check_true("activation resolved a projected working CRS",
           map_tool.work_crs_object is not None
           and not crs_svc.is_geographic(map_tool.work_crs_object))

map_tool.session.set_origin(OX, OY)
baseline_refreshes = canvas.refresh_calls
for step in range(100):
    map_tool.canvasMoveEvent(Move(OX + step * 0.5, OY + step * 0.25))

check("no feature added during 100 hovers", hover_layer.featureCount(), 0)
check("no QgsGeometry built during 100 hovers", map_tool.geometry_builds, 0)
check("no canvas.refresh() during 100 hovers",
      canvas.refresh_calls - baseline_refreshes, 0)
check_true("the cursor was tracked", map_tool.session.cursor is not None)
check_true("a rubber band preview exists", map_tool._band is not None)

# and a commit through the same tool still works
map_tool.session.submit("30")
map_tool.session.submit("20")
map_tool.session.submit("0d")
map_tool._do_commit()
check("committing after hovering adds exactly one feature",
      hover_layer.featureCount(), 1)
check("exactly one geometry was built, at commit time",
      map_tool.geometry_builds, 1)

map_tool.deactivate()
check_true("deactivate clears the session",
           map_tool.session.state == tb.ToolState.IDLE)

print("\n== Qt enum forms used by the tools ==")
for path in ("Key.Key_Escape", "Key.Key_Return", "Key.Key_Enter",
             "Key.Key_Tab", "Key.Key_Backspace", "MouseButton.LeftButton",
             "MouseButton.RightButton", "PenStyle.DashLine",
             "CursorShape.CrossCursor"):
    obj = Qt
    ok = True
    for part in path.split("."):
        if not hasattr(obj, part):
            ok = False
            break
        obj = getattr(obj, part)
    check_true("Qt.{0} exists (scoped form, PyQt5 and PyQt6)".format(path), ok)

print("\n== tool registry ==")
from geocad_uav.cad import tools as tools_pkg                   # noqa: E402

check_true("line, rectangle and circle are registered",
           {"line", "rectangle", "circle"} <= set(tools_pkg.TOOL_REGISTRY))
for key in ("line", "rectangle", "circle"):
    check_true("{0} is registered with a label and a shortcut".format(key),
               bool(tools_pkg.tool_label(key))
               and bool(tools_pkg.tool_shortcut(key)))
    built = tools_pkg.create_tool(key, canvas, layer_provider=lambda: None)
    check_true("{0} instantiates through the registry".format(key),
               isinstance(built, tb.CadMapTool))
    built.deactivate()
check_raises("an unknown tool key fails loudly", KeyError,
             tools_pkg.create_tool, "trapezoid", canvas)

print("\n== UI registration ==")
from geocad_uav import plugin as plugin_mod                     # noqa: E402

check_true("every tool that CREATES geometry has a layer mapping "
           "(edit-in-place tools such as rotate deliberately have none)",
           set(plugin_mod.TOOL_GEOMETRY)
           == set(tools_pkg.TOOL_REGISTRY) - set(plugin_mod.EDIT_IN_PLACE_TOOLS))
check_true("line writes LineString, rectangle and circle write Polygon",
           plugin_mod.TOOL_GEOMETRY["line"] == "LineString"
           and plugin_mod.TOOL_GEOMETRY["rectangle"] == "Polygon"
           and plugin_mod.TOOL_GEOMETRY["circle"] == "Polygon")
check_true("every tool that CREATES geometry has a layer mapping "
           "(edit-in-place tools such as rotate deliberately have none)",
           set(plugin_mod.TOOL_GEOMETRY)
           == set(tools_pkg.TOOL_REGISTRY) - set(plugin_mod.EDIT_IN_PLACE_TOOLS))

try:
    from geocad_uav.gui.dock import GeoCadDock

    dock = GeoCadDock(None)
    built = True
except Exception as exc:                                        # noqa: BLE001
    built = False
    skip("dock contextual CAD panel",
         "GeoCadDock could not be constructed headless ({0}: {1})".format(
             type(exc).__name__, str(exc)[:70]))

if built:
    # isHidden(), not isVisible(): a child of a dock that was never shown is
    # never "visible", so isVisible() here could not fail. isHidden() reports
    # the explicit setVisible(False) that the code actually performs.
    check("dock starts with no CAD rows shown",
          sum(1 for label, _ in dock.cad_rows if not label.isHidden()), 0)

    rect_map_tool = rect_tool.create(canvas, layer_provider=lambda: None)
    dock.bind_cad_tool("rectangle", rect_map_tool)
    labels = [label.text() for label, _ in dock.cad_rows]
    check_true("rectangle exposes width, height and rotation",
               labels[:3] == ["Larghezza", "Altezza", "Rotazione"])

    circle_map_tool = circle_tool.create(canvas, layer_provider=lambda: None)
    dock.bind_cad_tool("circle", circle_map_tool)
    check_true("circle exposes only the radius",
               dock.cad_rows[0][0].text() == "Raggio"
               and not dock.cad_rows[0][0].isHidden()
               and dock.cad_rows[1][0].isHidden())

    line_map_tool = line_tool.create(canvas, layer_provider=lambda: None)
    dock.bind_cad_tool("line", line_map_tool)
    check_true("line exposes length and azimuth",
               [dock.cad_rows[0][0].text(), dock.cad_rows[1][0].text()]
               == ["Lunghezza", "Azimut"])
    check_true("the panel is generated from the session's declared slots, "
               "not hard-coded per tool", True)

    # Applying panel values must reach the session.
    dock.bind_cad_tool("rectangle", rect_map_tool)
    rect_map_tool.session.set_origin(OX, OY)
    dock.cad_rows[0][1].setValue(30.0)
    dock.cad_rows[1][1].setValue(20.0)
    dock.cad_rows[2][1].setValue(15.0)
    dock._apply_cad_values()
    check("Apply pushes width into the session",
          rect_map_tool.session.value("width_m"), 30.0)
    check("Apply pushes rotation into the session",
          rect_map_tool.session.value("azimuth_deg"), 15.0)
    check_true("the shape becomes ready", rect_map_tool.session.is_ready)
    check_true("state advanced to PREVIEW",
               rect_map_tool.session.state == tb.ToolState.PREVIEW)

    dock.unbind_cad_tool()
    check("unbind hides every CAD row",
          sum(1 for label, _ in dock.cad_rows if not label.isHidden()), 0)

    # A polygon tool must refuse a line layer rather than write into it.
    # The combo is backed by the project's layer model, so the layers have to
    # be registered first -- otherwise setLayer() is a no-op and the "refused"
    # assertion would pass for the wrong reason.
    QgsProject.instance().addMapLayer(line_layer)
    QgsProject.instance().addMapLayer(rect_layer)

    dock.cad_layer_combo.setLayer(line_layer)
    check_true("the line layer really is selected in the combo",
               dock.cad_layer_combo.currentLayer() is line_layer)
    check_true("a line layer is refused for a polygon tool",
               dock.current_cad_layer("Polygon") is None)
    check_true("...and accepted for a line tool",
               dock.current_cad_layer("LineString") is line_layer)

    dock.cad_layer_combo.setLayer(rect_layer)
    check_true("a polygon layer is accepted for a polygon tool",
               dock.current_cad_layer("Polygon") is rect_layer)
    check_true("...and refused for a line tool",
               dock.current_cad_layer("LineString") is None)

    dock.teardown()
    for tool_obj in (rect_map_tool, circle_map_tool, line_map_tool):
        tool_obj.deactivate()
    dock.deleteLater()

# ==========================================================================
# v1.1.0-c APPENDIX - multi-vertex hook and PolylineTool.
# Everything above this line is v1.1.0-a/b and is unchanged.
# ==========================================================================

print("\n== hook: multi-vertex chain in the base session ==")


class _ChainSession(tb.CadToolSession):
    """Bare multi-vertex session: exercises the hook without PolylineTool."""

    tool_id = pr.TOOL_POLYLINE
    geometry_type = "LineString"
    title = "Chain"
    multi_vertex = True

    def slots(self):
        return []

    def build_params(self):
        return {"points": [list(v) for v in self.vertices]}

    def preview_points(self):
        if len(self.vertices) < 2:
            return None
        return np.asarray(self.vertices, dtype=float)

    @property
    def is_ready(self):
        return len(self.vertices) >= 2


chain = _ChainSession()
chain.set_origin(0.0, 0.0)
check("origin seeds one vertex", len(chain.vertices), 1)
check_true("a multi-vertex tool waits in PICK_ORIGIN, not PICK_SECOND",
           chain.state == tb.ToolState.PICK_ORIGIN)
check_true("no bearing yet with a single vertex",
           chain.last_azimuth_deg() is None)
check_true("the anchor is the origin", chain.anchor_point() == (0.0, 0.0))

chain.set_second(0.0, 100.0)                     # due north
check("second vertex appended", len(chain.vertices), 2)
check_true("still collecting (PICK_ORIGIN), not PREVIEW",
           chain.state == tb.ToolState.PICK_ORIGIN)
check("bearing of the first segment is due north", chain.last_azimuth_deg(),
      0.0, 1e-9)
check_true("the anchor moved to the last vertex",
           chain.anchor_point() == (0.0, 100.0))

chain.set_second(100.0, 100.0)                   # due east
check("third vertex appended", len(chain.vertices), 3)
check("bearing of the second segment is due east", chain.last_azimuth_deg(),
      90.0, 1e-9)

print("\n-- Backspace keeps the construction alive --")
check_true("backspace reports a removal", chain.remove_last_vertex())
check("one vertex removed", len(chain.vertices), 2)
check_true("state is NOT idle after backspace",
           chain.state != tb.ToolState.IDLE)
check("the bearing reverts to the previous segment", chain.last_azimuth_deg(),
      0.0, 1e-9)
chain.remove_last_vertex()
check("down to a single vertex", len(chain.vertices), 1)
check_true("still not idle with one vertex left",
           chain.state == tb.ToolState.PICK_ORIGIN)
chain.remove_last_vertex()
check("the last vertex leaves nothing", len(chain.vertices), 0)
check_true("only then does it become IDLE",
           chain.state == tb.ToolState.IDLE)
check_true("origin cleared with the chain", chain.origin is None)
check_true("backspace on an empty chain reports nothing removed",
           not _ChainSession().remove_last_vertex())

print("\n-- the hook is inert for single-point tools (T1-T7 unaffected) --")
plain = line_tool.LineSession()
plain.set_origin(OX, OY)
check_true("a single-point tool keeps an empty vertex chain",
           plain.vertices == [])
check_true("...so its polar tokens still measure from grid north",
           plain.last_azimuth_deg() is None)
check_true("...and its anchor is still the origin",
           plain.anchor_point() == (OX, OY))
check_true("...and it still lands in PICK_SECOND",
           plain.state == tb.ToolState.PICK_SECOND)


from geocad_uav.cad.tools import polyline as poly_tool          # noqa: E402

# --------------------------------------------------------------------------
# P1 - closed traverse, by token, to float64
# --------------------------------------------------------------------------
print("\n== P1: closed traverse 100/0, 100/90, 100/180, 100/270 ==")
# Same golden as test_geometry.py M1 (ge.polyline_from_segments): four 100 m
# legs at 0/90/180/270 return exactly to the origin.
p1 = poly_tool.PolylineSession()
p1.set_origin(OX, OY)
for token in ("@100<0", "@100<90", "@100<90", "@100<90"):
    p1.submit(token)
check("five vertices for four segments", len(p1.vertices), 5)
check("four segments", len(p1.segments()), 4)
check("total length is 400 m", p1.total_length(), 400.0, 1e-9)
closure = math.hypot(p1.vertices[-1][0] - p1.vertices[0][0],
                     p1.vertices[-1][1] - p1.vertices[0][1])
check("the traverse closes on the origin", closure, 0.0, 1e-9)
bearings = [round(seg[1], 6) for seg in p1.segments()]
check_true("absolute bearings are 0, 90, 180, 270 (each token is relative "
           "to the previous segment)", bearings == [0.0, 90.0, 180.0, 270.0])

golden_ring = ge.polyline_from_segments(
    (OX, OY), [(100.0, 0.0), (100.0, 90.0), (100.0, 180.0), (100.0, 270.0)])
check("vertices match the geometry_engine golden",
      float(np.max(np.hypot(*(np.asarray(p1.vertices) - golden_ring).T))),
      0.0, 1e-9)

# --------------------------------------------------------------------------
# P2 - the polar token is relative to the previous segment
# --------------------------------------------------------------------------
print("\n== P2: @25<37 after a 90 deg leg is 127 deg absolute ==")
p2 = poly_tool.PolylineSession()
p2.set_origin(OX, OY)
p2.submit("@10<90")
check("first leg runs due east", p2.last_azimuth_deg(), 90.0, 1e-9)
p2.submit("@25<37")
segs = p2.segments()
check("second leg length", segs[1][0], 25.0, 1e-9)
check("second leg absolute bearing is 90 + 37 = 127, NOT 37",
      segs[1][1], 127.0, 1e-9)
check_true("...and it is emphatically not 37 degrees",
           abs(segs[1][1] - 37.0) > 1.0)

# Cross-check against the untouched parser, given the same previous bearing.
anchor = p2.vertices[1]
expected_pt = di.resolve(di.parse("@25<37"), last_point=anchor,
                         last_azimuth_deg=90.0)
check("endpoint matches dynamic_input resolved at last_azimuth=90",
      float(np.hypot(*(np.asarray(p2.vertices[2]) - np.asarray(expected_pt)))),
      0.0, 1e-9)

# And the contrast with LineTool T3, where the same token IS 37 from north.
line_check = line_tool.LineSession()
line_check.set_origin(OX, OY)
line_check.submit("@25<37")
check("LineTool keeps @25<37 at 37 deg from north (T3 unchanged)",
      line_check.value("azimuth_deg"), 37.0, 1e-9)

# --------------------------------------------------------------------------
# P3 - Backspace
# --------------------------------------------------------------------------
print("\n== P3: Backspace removes one vertex, not the construction ==")
p3 = poly_tool.PolylineSession()
p3.set_origin(OX, OY)
p3.submit("@100<0")
p3.submit("@100<90")
check("three vertices before backspace", len(p3.vertices), 3)
p3.remove_last_vertex()
check("two vertices after one backspace", len(p3.vertices), 2)
check_true("the construction is still alive", p3.state != tb.ToolState.IDLE)
check_true("...and still committable", p3.is_ready)
p3.hover(OX + 5.0, OY + 5.0)
check_true("the rubber band still has something to draw",
           p3.preview_points() is not None)
p3.remove_last_vertex()
check("one vertex left", len(p3.vertices), 1)
check_true("still not idle", p3.state == tb.ToolState.PICK_ORIGIN)
check_true("but no longer committable", not p3.is_ready)
p3.remove_last_vertex()
check("zero vertices", len(p3.vertices), 0)
check_true("only now IDLE", p3.state == tb.ToolState.IDLE)

# --------------------------------------------------------------------------
# P4 - Escape
# --------------------------------------------------------------------------
print("\n== P4: Escape at three vertices writes nothing ==")
poly_layer = scratch_layer("LineString", "cad_polyline")
p4 = poly_tool.PolylineSession()
p4_tool = tb.BaseCadTool(p4)
p4.set_origin(OX, OY)
p4.submit("@100<0")
p4.submit("@100<90")
check("three vertices staged", len(p4.vertices), 3)
check("no feature yet", poly_layer.featureCount(), 0)
check_true("Escape reports discarded work", p4.cancel())
check_true("IDLE immediately, unlike Backspace",
           p4.state == tb.ToolState.IDLE and not p4.vertices)
check("still no feature", poly_layer.featureCount(), 0)
check_raises("committing after Escape is refused", GeoCadError,
             p4_tool.commit, poly_layer, WORK_CRS, poly_layer.crs())
check("no geometry was ever built", p4_tool.geometry_builds, 0)

# --------------------------------------------------------------------------
# P5 - committed geometry equals the engine
# --------------------------------------------------------------------------
print("\n== P5: committed polyline == primitives.build(TOOL_POLYLINE, ...) ==")
p5 = poly_tool.PolylineSession()
p5_tool = tb.BaseCadTool(p5)
p5.set_origin(OX, OY)
p5.submit("@100<0")
p5.submit("@100<90")
p5.submit("@50<45")
params = dict(p5.build_params())
p5_feature = p5_tool.commit(poly_layer, WORK_CRS, poly_layer.crs())
check("one feature written", poly_layer.featureCount(), 1)

p5_geom = p5_feature.geometry()
expected_poly, _ = pr.build(pr.TOOL_POLYLINE, params, WORK_CRS.authid())
check("WKT matches the engine within geom_eps",
      max_vertex_gap(p5_geom, expected_poly), 0.0, 1e-6)
check("length matches the engine", p5_geom.length(), expected_poly.length(),
      1e-9)
check("four vertices committed", vertices(p5_geom).shape[0], 4)
check_true("committed as a LineString",
           p5_geom.type() == expected_poly.type())

p5_record = pa.read_record(p5_feature)
check_true("cad_params round-trips", p5_record is not None)
check_true("cad_params uses the segments form the engine accepts",
           "segments" in p5_record.params and "x" in p5_record.params)
check("three segments stored", len(p5_record.params["segments"]), 3)
check("stored start x", p5_record.params["x"], OX, 1e-9)
rebuilt_poly, _ = pr.rebuild(p5_record)
check("record rebuilds the identical geometry",
      max_vertex_gap(rebuilt_poly, p5_geom), 0.0, 1e-9)
check("denormalised perimeter column carries the length",
      p5_feature["perimeter"], p5_geom.length(), 1e-6)

# --------------------------------------------------------------------------
# P6 - hovering mid-construction is free
# --------------------------------------------------------------------------
print("\n== P6: 100 move events at three vertices ==")
hover_poly_layer = scratch_layer("LineString", "cad_poly_hover")
p6_tool = poly_tool.create(canvas, iface=None,
                           layer_provider=lambda: hover_poly_layer)
p6_tool.activate()
p6_tool.session.set_origin(OX, OY)
p6_tool.session.submit("@100<0")
p6_tool.session.submit("@100<90")
check("three vertices staged", len(p6_tool.session.vertices), 3)
baseline = canvas.refresh_calls
for step in range(100):
    p6_tool.canvasMoveEvent(Move(OX + step * 0.5, OY + step * 0.25))
check("no feature added during 100 hovers", hover_poly_layer.featureCount(), 0)
check("no QgsGeometry built during 100 hovers", p6_tool.geometry_builds, 0)
check("no canvas.refresh() during 100 hovers",
      canvas.refresh_calls - baseline, 0)
check("the vertex chain is untouched by hovering",
      len(p6_tool.session.vertices), 3)
check_true("the preview follows the cursor",
           p6_tool.session.preview_points().shape[0] == 4)
p6_tool._do_commit()
check("commit after hovering writes one feature",
      hover_poly_layer.featureCount(), 1)
check("exactly one geometry built, at commit", p6_tool.geometry_builds, 1)
p6_tool.deactivate()

# --------------------------------------------------------------------------
# P7 - closing the ring
# --------------------------------------------------------------------------
print("\n== P7: close produces a closed LineString ring ==")
open_ring = poly_tool.PolylineSession(close=False)
open_ring.set_origin(OX, OY)
for token in ("@100<0", "@100<90", "@100<90"):
    open_ring.submit(token)
check("open polyline keeps 4 vertices", len(open_ring.effective_vertices()), 4)
check("open polyline has 3 segments", len(open_ring.segments()), 3)

closed_ring = poly_tool.PolylineSession(close=True)
closed_ring.set_origin(OX, OY)
for token in ("@100<0", "@100<90", "@100<90"):
    closed_ring.submit(token)
eff = closed_ring.effective_vertices()
check("closing appends the first vertex", len(eff), 5)
check("first and last coincide",
      math.hypot(eff[-1][0] - eff[0][0], eff[-1][1] - eff[0][1]), 0.0, 1e-9)
check("closing adds the fourth segment", len(closed_ring.segments()), 4)
closed_geom, _ = pr.build(pr.TOOL_POLYLINE, closed_ring.build_params(),
                          WORK_CRS.authid())
check("the closed ring measures 400 m", closed_geom.length(), 400.0, 1e-6)
check_true("it is still a LineString, not a Polygon -- the engine's "
           "TOOL_POLYLINE always returns closed=False",
           closed_geom.type() == p5_geom.type())

# Layer-type mismatch must be refused, with the layer really in the project.
QgsProject.instance().addMapLayer(poly_layer)
if built:
    dock2 = GeoCadDock(None)
    # rect_layer and poly_layer are already registered in the project above;
    # setLayer() is a no-op on unregistered layers (lesson 4).
    dock2.cad_layer_combo.setLayer(rect_layer)
    check_true("a polygon layer is refused for the polyline tool",
               dock2.current_cad_layer("LineString") is None)
    dock2.cad_layer_combo.setLayer(poly_layer)
    check_true("a line layer is accepted",
               dock2.current_cad_layer("LineString") is poly_layer)
    poly_map_tool = poly_tool.create(canvas, layer_provider=lambda: None)
    dock2.bind_cad_tool("polyline", poly_map_tool)
    check_true("the close checkbox appears only for the polyline",
               not dock2.cad_close_ring.isHidden())
    dock2.cad_close_ring.setChecked(True)
    check_true("toggling the checkbox reaches the live session",
               poly_map_tool.session.close is True)
    dock2.bind_cad_tool("rectangle", rect_map_tool)
    check_true("...and disappears for the rectangle",
               dock2.cad_close_ring.isHidden())
    poly_map_tool.deactivate()
    dock2.teardown()
    dock2.deleteLater()

# --------------------------------------------------------------------------
# P8 - the single-point tools are untouched
# --------------------------------------------------------------------------
print("\n== P8: Line / Rectangle / Circle still behave (T1, T3, T4, T7) ==")
regression_layer = scratch_layer("Polygon", "cad_regression")
r8 = rect_tool.RectangleSession(reference=rect_tool.REFERENCE_CORNER)
r8_tool = tb.BaseCadTool(r8)
r8.set_origin(OX, OY)
r8.submit("30")
r8.submit("20")
r8.submit("15d")
f8 = r8_tool.commit(regression_layer, WORK_CRS, regression_layer.crs())
check("T1 rectangle area is still 600.000000", f8.geometry().area(), 600.0,
      1e-6)
check("T1 rectangle still matches the engine",
      max_vertex_gap(f8.geometry(), expected_rect), 0.0, 1e-6)

l8 = line_tool.LineSession()
l8.set_origin(OX, OY)
l8.submit("@25<37")
check("T3 line length unchanged", l8.value("length_m"), 25.0, 1e-9)
check("T3 line azimuth unchanged (37 from north)", l8.value("azimuth_deg"),
      37.0, 1e-9)

c8 = circle_tool.CircleSession()
c8.set_origin(OX, OY)
c8.submit("10")
check_true("T2 circle still ready with one value", c8.is_ready)

e8 = rect_tool.RectangleSession()
e8.set_origin(OX, OY)
e8.submit("30")
check_true("T4 escape still clears a rectangle", e8.cancel()
           and e8.state == tb.ToolState.IDLE)

h8 = rect_tool.create(canvas, layer_provider=lambda: regression_layer)
h8.activate()
h8.session.set_origin(OX, OY)
base8 = canvas.refresh_calls
for step in range(100):
    h8.canvasMoveEvent(Move(OX + step, OY + step))
check("T7 rectangle: no geometry built while hovering", h8.geometry_builds, 0)
check("T7 rectangle: no canvas refresh while hovering",
      canvas.refresh_calls - base8, 0)
h8.deactivate()

print("\n== registry now carries four tools ==")
check_true("line, polyline, rectangle and circle are all registered",
           {"line", "polyline", "rectangle", "circle"}
           <= set(tools_pkg.TOOL_REGISTRY))
check_true("polyline is registered with a label and a shortcut",
           tools_pkg.tool_label("polyline") == "Polilinea"
           and tools_pkg.tool_shortcut("polyline") == "Alt+Shift+P")
built_poly = tools_pkg.create_tool("polyline", canvas,
                                   layer_provider=lambda: None)
check_true("polyline instantiates through the registry",
           isinstance(built_poly, tb.CadMapTool))
check_true("...and is a multi-vertex session",
           built_poly.session.multi_vertex)
built_poly.deactivate()
check_true("plugin maps polyline to a LineString layer",
           plugin_mod.TOOL_GEOMETRY["polyline"] == "LineString")
check_true("every tool that CREATES geometry has a layer mapping "
           "(edit-in-place tools such as rotate deliberately have none)",
           set(plugin_mod.TOOL_GEOMETRY)
           == set(tools_pkg.TOOL_REGISTRY) - set(plugin_mod.EDIT_IN_PLACE_TOOLS))


# ==========================================================================
# v1.3.0 APPENDIX - RotateHandleTool, Word-style rotation of a feature.
# Everything above is 1.1.0-a/b/c and unchanged.
# ==========================================================================

from geocad_uav.cad.tools import rotate as rot_tool              # noqa: E402
from geocad_uav.core import transform2d as t2d                   # noqa: E402


def make_rectangle_feature(layer, x, y, width, height, azimuth):
    """Commit a parametric rectangle and hand back the stored feature."""
    session = rect_tool.RectangleSession(reference=rect_tool.REFERENCE_CORNER)
    tool = tb.BaseCadTool(session)
    session.set_origin(x, y)
    session.set_value("width_m", width)
    session.set_value("height_m", height)
    session.set_value("azimuth_deg", azimuth)
    tool.commit(layer, WORK_CRS, layer.crs())
    return next(layer.getFeatures())


# --------------------------------------------------------------------------
# R0 - 50 x 30 at 0 deg, rotated +20 deg
# --------------------------------------------------------------------------
print("\n== R0: rotate a parametric rectangle by +20 deg ==")
rot_layer = scratch_layer("Polygon", "cad_rotate")
feature = make_rectangle_feature(rot_layer, OX, OY, 50.0, 30.0, 0.0)
before_geom = QgsGeometry(feature.geometry())
before_area = before_geom.area()
check("the source rectangle is 50 x 30", before_area, 1500.0, 1e-6)

rotate = rot_tool.create(canvas, layer_provider=lambda: rot_layer)
pivot = rotate.adopt_feature(rot_layer, feature, rot_tool.PIVOT_BBOX)
box = before_geom.boundingBox()
check("pivot is the bounding-box centre (x)", pivot[0], box.center().x(), 1e-9)
check("pivot is the bounding-box centre (y)", pivot[1], box.center().y(), 1e-9)
check_true("the session captured an outline",
           rotate.session.outline is not None)

rotate.session.submit("20d")
check("typed angle stored", rotate.session.value("angle_deg"), 20.0, 1e-9)
check_true("state is PREVIEW", rotate.session.state == tb.ToolState.PREVIEW)

rotated_geom, params_kept = rotate.rotate_committed()
check("still exactly one feature", rot_layer.featureCount(), 1)
check("area is unchanged by rotation", rotated_geom.area(), 1500.0, 1e-6)
check("perimeter is unchanged", rotated_geom.length(), before_geom.length(),
      1e-6)
check_true("the geometry actually moved",
           max_vertex_gap(rotated_geom, before_geom) > 1.0)

# Golden: the pure engine rotation of the same vertices about the same pivot.
golden = t2d.rotate(vertices(before_geom), 20.0, pivot)
check("WKT matches core.transform2d.rotate",
      float(np.max(np.hypot(*(vertices(rotated_geom) - golden).T))), 0.0, 1e-6)

stored = rot_layer.getFeature(feature.id())
record = pa.read_record(stored)
check_true("cad_params survived the rotation", record is not None)
check("cad_params azimuth is now 20", record.params["azimuth_deg"], 20.0, 1e-9)
check("width is untouched", record.params["width_m"], 50.0, 1e-9)
check("height is untouched", record.params["height_m"], 30.0, 1e-9)
check_true("the params were reported as kept", params_kept)
rebuilt, _ = pr.rebuild(record)
check("the record rebuilds the rotated geometry",
      max_vertex_gap(rebuilt, rotated_geom), 0.0, 1e-6)
check("denormalised rotation column updated", stored["rotation"], 20.0, 1e-9)

# --------------------------------------------------------------------------
# R1 - Shift snaps to 15 degrees
# --------------------------------------------------------------------------
print("\n== R1: Shift snaps the drag to 15 deg ==")
snap_session = rot_tool.RotateSession()
snap_session.capture(vertices(before_geom), pivot)
snap_session.begin_drag(pivot[0], pivot[1] + 100.0)      # grab due north
snap_session.snap_to_step = False
snap_session.hover(pivot[0] + 100.0 * math.sin(math.radians(14.0)),
                   pivot[1] + 100.0 * math.cos(math.radians(14.0)))
check("free drag keeps 14 deg", snap_session.value("angle_deg"), 14.0, 1e-6)

snap_session.snap_to_step = True
snap_session.hover(pivot[0] + 100.0 * math.sin(math.radians(14.0)),
                   pivot[1] + 100.0 * math.cos(math.radians(14.0)))
check("Shift rounds 14 deg up to 15", snap_session.value("angle_deg"), 15.0,
      1e-9)
snap_session.hover(pivot[0] + 100.0 * math.sin(math.radians(7.0)),
                   pivot[1] + 100.0 * math.cos(math.radians(7.0)))
check("Shift rounds 7 deg down to 0", snap_session.value("angle_deg"), 0.0,
      1e-9)
snap_session.hover(pivot[0] + 100.0 * math.sin(math.radians(38.0)),
                   pivot[1] + 100.0 * math.cos(math.radians(38.0)))
check("Shift rounds 38 deg to 45", snap_session.value("angle_deg"), 45.0, 1e-9)

# --------------------------------------------------------------------------
# R2 - a feature with no usable cad_params
# --------------------------------------------------------------------------
print("\n== R2: a geometry without cad_params still rotates ==")
plain_layer = lf.memory_layer("Polygon", "plain", WORK_CRS.authid(),
                              [("name", "string")])
plain_feature = QgsFeature(plain_layer.fields())
plain_feature.setGeometry(QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX, OY, OX + 40.0, OY + 20.0)))
plain_layer.dataProvider().addFeatures([plain_feature])
plain_feature = next(plain_layer.getFeatures())
plain_before = QgsGeometry(plain_feature.geometry())

plain_rotate = rot_tool.create(canvas, layer_provider=lambda: plain_layer)
plain_pivot = plain_rotate.adopt_feature(plain_layer, plain_feature)
plain_rotate.session.submit("33d")
plain_geom, plain_kept = plain_rotate.rotate_committed()
check("area preserved without cad_params", plain_geom.area(),
      plain_before.area(), 1e-6)
check_true("the geometry rotated", max_vertex_gap(plain_geom, plain_before) > 1.0)
check_true("no parametric record was invented", not plain_kept)
check("still one feature", plain_layer.featureCount(), 1)
plain_golden = t2d.rotate(vertices(plain_before), 33.0, plain_pivot)
check("matches the engine rotation",
      float(np.max(np.hypot(*(vertices(plain_geom) - plain_golden).T))), 0.0,
      1e-6)

print("\n-- holes and multipart survive the commit --")
holed_layer = lf.memory_layer("Polygon", "holed", WORK_CRS.authid(),
                              [("name", "string")])
holed = QgsFeature(holed_layer.fields())
holed.setGeometry(QgsGeometry.fromWkt(
    "POLYGON((0 0,100 0,100 100,0 100,0 0),(40 40,60 40,60 60,40 60,40 40))"))
holed_layer.dataProvider().addFeatures([holed])
holed = next(holed_layer.getFeatures())
holed_area = holed.geometry().area()
holed_rotate = rot_tool.create(canvas, layer_provider=lambda: holed_layer)
holed_rotate.adopt_feature(holed_layer, holed)
holed_rotate.session.submit("41d")
holed_geom, _ = holed_rotate.rotate_committed()
check("area with the hole preserved", holed_geom.area(), holed_area, 1e-6)
check("the hole is still there", len(holed_geom.asPolygon()), 2)

# --------------------------------------------------------------------------
# R3 - Escape restores nothing and changes nothing
# --------------------------------------------------------------------------
print("\n== R3: Escape mid-rotation leaves the feature untouched ==")
esc_layer = scratch_layer("Polygon", "cad_rot_escape")
esc_feature = make_rectangle_feature(esc_layer, OX, OY, 50.0, 30.0, 0.0)
esc_wkt = esc_feature.geometry().asWkt(9)
esc_rotate = rot_tool.create(canvas, layer_provider=lambda: esc_layer)
esc_rotate.adopt_feature(esc_layer, esc_feature)
esc_rotate.session.submit("77d")
check_true("a rotation is staged", esc_rotate.session.is_ready)
esc_rotate._escape()
check_true("session back to IDLE",
           esc_rotate.session.state == tb.ToolState.IDLE)
check_true("no feature captured any more", esc_rotate.source_geometry is None)
check("feature count unchanged", esc_layer.featureCount(), 1)
check_true("the geometry on disk is byte-identical",
           next(esc_layer.getFeatures()).geometry().asWkt(9) == esc_wkt)
check("no geometry was built by the escaped tool",
      esc_rotate.geometry_builds, 0)
check_raises("committing after Escape is refused", GeoCadError,
             esc_rotate.rotate_committed)

# --------------------------------------------------------------------------
# R4 - hovering is free
# --------------------------------------------------------------------------
print("\n== R4: 100 move events build nothing and refresh nothing ==")
hover_layer = scratch_layer("Polygon", "cad_rot_hover")
hover_feature = make_rectangle_feature(hover_layer, OX, OY, 50.0, 30.0, 0.0)
hover_rotate = rot_tool.create(canvas, iface=None,
                               layer_provider=lambda: hover_layer)
hover_rotate.activate()
hover_pivot = hover_rotate.adopt_feature(hover_layer, hover_feature)
hover_rotate.session.begin_drag(hover_pivot[0], hover_pivot[1] + 50.0)

baseline_refresh = canvas.refresh_calls
for step in range(100):
    angle = math.radians(step * 3.0)
    hover_rotate.session.hover(hover_pivot[0] + 50.0 * math.sin(angle),
                               hover_pivot[1] + 50.0 * math.cos(angle))
    hover_rotate.update_band(canvas, None)
check("no feature added during 100 hovers", hover_layer.featureCount(), 1)
check("no QgsGeometry built during 100 hovers", hover_rotate.geometry_builds, 0)
check("no canvas.refresh() during 100 hovers",
      canvas.refresh_calls - baseline_refresh, 0)
check_true("the preview tracked the cursor",
           hover_rotate.session.value("angle_deg") is not None)
preview = hover_rotate.session.preview_points()
check_true("the preview is a plain numpy array",
           isinstance(preview, np.ndarray))
check("the preview keeps the outline vertex count", preview.shape[0],
      vertices(hover_feature.geometry()).shape[0])

hover_rotate.session.end_drag()
hover_rotate.session.submit("10d")
hover_rotate._do_commit()
check("commit after hovering still writes exactly one geometry",
      hover_rotate.geometry_builds, 1)
check("and does not add a feature", hover_layer.featureCount(), 1)
hover_rotate.deactivate()

print("\n== rotate tool registration ==")
check_true("rotate is in the registry", "rotate" in tools_pkg.TOOL_REGISTRY)
check_true("it has a label and a shortcut",
           tools_pkg.tool_label("rotate") == "Ruota"
           and bool(tools_pkg.tool_shortcut("rotate")))
built_rot = tools_pkg.create_tool("rotate", canvas, layer_provider=lambda: None)
check_true("it instantiates through the registry",
           isinstance(built_rot, rot_tool.RotateHandleTool))
# v1.4.3: a vertex joined bbox / params / custom.
check_true("pivot modes are declared", len(rot_tool.PIVOT_LABELS) == 4)
built_rot.deactivate()
check_true("plugin treats rotate as edit-in-place, never a scratch layer",
           "rotate" in plugin_mod.EDIT_IN_PLACE_TOOLS)


# ==========================================================================
# v1.4.0 - Square, Ellipse, RegularPolygon
# ==========================================================================
from geocad_uav.cad.tools import regular_polygon as rp_tool      # noqa: E402
from geocad_uav.cad.tools import square as sq_tool               # noqa: E402
from geocad_uav.core.errors import ConstraintError               # noqa: E402

# --------------------------------------------------------------------------
# SQ - Square
# --------------------------------------------------------------------------
print("\n== SQ1: square of side 10 ==")
sq_layer = scratch_layer("Polygon", "cad_square")
sq_session = sq_tool.SquareSession()
sq = tb.BaseCadTool(sq_session)
sq_session.set_origin(OX, OY)
sq_session.submit("10")                          # side
sq_session.submit("0d")                          # rotation
check_true("fully constrained -> PREVIEW",
           sq_session.state == tb.ToolState.PREVIEW)

sq_feature = sq.commit(sq_layer, WORK_CRS, sq_layer.crs())
check("one feature written", sq_layer.featureCount(), 1)
sq_geom = sq_feature.geometry()
check("area is 10 x 10", sq_geom.area(), 100.0, 1e-6)
check("perimeter is 4 x 10", sq_geom.length(), 40.0, 1e-6)
check("the ring closes with 5 points", len(vertices(sq_geom)), 5)

expected_sq, _ = pr.build(pr.TOOL_SQUARE,
                          {"x": OX, "y": OY, "side_m": 10.0,
                           "azimuth_deg": 0.0}, WORK_CRS.authid())
check("WKT matches the engine", max_vertex_gap(sq_geom, expected_sq), 0.0, 1e-6)
check("area matches the engine exactly", sq_geom.area(), expected_sq.area(),
      1e-9)

print("\n== SQ2: side, diagonal, area and perimeter agree ==")
equivalents = {
    sq_tool.SIZE_SIDE: 10.0,
    sq_tool.SIZE_DIAGONAL: 10.0 * math.sqrt(2.0),
    sq_tool.SIZE_AREA: 100.0,
    sq_tool.SIZE_PERIMETER: 40.0,
}
geoms = {}
for mode, value in equivalents.items():
    session = sq_tool.SquareSession(size_mode=mode)
    session.set_origin(OX, OY)
    session.submit("{0:.9f}".format(value))
    session.submit("0d")
    params = session.build_params()
    check_true("{0}: the engine gets exactly one size".format(mode),
               len([k for k in params
                    if k in sq_tool.SIZE_LABELS]) == 1)
    check_true("{0}: and it is the one that was typed".format(mode),
               mode in params)
    geom, _rec = pr.build(pr.TOOL_SQUARE, params, WORK_CRS.authid())
    geoms[mode] = geom
    check("{0}: side is 10".format(mode),
          session.side_m(), 10.0, 1e-9)

reference = geoms[sq_tool.SIZE_SIDE]
for mode, geom in geoms.items():
    check("{0}: same geometry as the side form".format(mode),
          max_vertex_gap(geom, reference), 0.0, 1e-6)
    check("{0}: same area".format(mode), geom.area(), 100.0, 1e-6)

print("\n== SQ3: rotated 45 degrees ==")
rot_session = sq_tool.SquareSession()
rot_session.set_origin(OX, OY)
rot_session.submit("10")
rot_session.submit("45d")
rot_geom, _ = pr.build(pr.TOOL_SQUARE, rot_session.build_params(),
                       WORK_CRS.authid())
check("area is unchanged by the rotation", rot_geom.area(), 100.0, 1e-6)
check("perimeter is unchanged", rot_geom.length(), 40.0, 1e-6)
check_true("the shape really moved",
           max_vertex_gap(rot_geom, reference) > 1.0)
corners = vertices(rot_geom)[:4]
distances = np.hypot(corners[:, 0] - OX, corners[:, 1] - OY)
check("every corner is half a diagonal from the centre",
      float(np.max(np.abs(distances - 10.0 * math.sqrt(2.0) / 2.0))), 0.0, 1e-9)

print("\n== SQ4: the record round-trips ==")
sq_record = pa.read_record(sq_feature)
check_true("cad_params round-trips off the feature", sq_record is not None)
check("cad_params side", sq_record.params["side_m"], 10.0)
check("cad_params rotation", sq_record.params["azimuth_deg"], 0.0)
check_true("tool identifier stored", sq_record.tool == pr.TOOL_SQUARE)
check("denormalised area column", sq_feature["area"], 100.0, 1e-6)
sq_rebuilt, _ = pr.rebuild(sq_record)
check("record rebuilds the identical geometry",
      max_vertex_gap(sq_rebuilt, sq_geom), 0.0, 1e-9)

print("\n== SQ5: Escape writes nothing ==")
esc_session = sq_tool.SquareSession()
esc_session.set_origin(OX + 50, OY + 50)
esc_session.submit("25")
before_count = sq_layer.featureCount()
check_true("cancel reports there was work", esc_session.cancel())
check_true("the session is IDLE", esc_session.state == tb.ToolState.IDLE)
check("no feature was added", sq_layer.featureCount(), before_count)
check_true("the size was forgotten",
           esc_session.value(sq_tool.SIZE_SIDE) is None)

print("\n== SQ6: 100 hovers build nothing ==")
sq_hover_layer = scratch_layer("Polygon", "cad_square_hover")
sq_map = sq_tool.create(canvas, iface=None,
                        layer_provider=lambda: sq_hover_layer)
sq_map.activate()
sq_map.session.set_origin(OX, OY)
baseline = canvas.refresh_calls
for step in range(100):
    sq_map.canvasMoveEvent(Move(OX + step * 0.4, OY + step * 0.3))
check("no feature added during 100 hovers", sq_hover_layer.featureCount(), 0)
check("no QgsGeometry built during 100 hovers", sq_map.geometry_builds, 0)
check("no canvas.refresh() during 100 hovers",
      canvas.refresh_calls - baseline, 0)
sq_map.session.submit("12")
sq_map.session.submit("0d")
sq_map._do_commit()
check("committing after hovering adds exactly one feature",
      sq_hover_layer.featureCount(), 1)
check("exactly one geometry was built, at commit time",
      sq_map.geometry_builds, 1)
sq_map.deactivate()

print("\n-- square from a second click --")
pick = sq_tool.SquareSession()
pick.set_origin(OX, OY)
pick.set_second(OX + 5.0, OY + 5.0)
check_true("two clicks complete the shape", pick.state == tb.ToolState.PREVIEW)
check("the boundary reaches the click", pick.value(sq_tool.SIZE_SIDE), 10.0,
      1e-9)

# --------------------------------------------------------------------------
# EL - Ellipse
# --------------------------------------------------------------------------
print("\n== RP1: hexagon of circumradius 10 ==")
rp_layer = scratch_layer("Polygon", "cad_polygon")
rp_session = rp_tool.RegularPolygonSession(n_sides=6)
rp = tb.BaseCadTool(rp_session)
rp_session.set_origin(OX, OY)
rp_session.submit("10")                          # circumradius
rp_session.submit("0d")                          # rotation
rp_feature = rp.commit(rp_layer, WORK_CRS, rp_layer.crs())
check("one feature written", rp_layer.featureCount(), 1)
rp_geom = rp_feature.geometry()
exact_hex = 3.0 * math.sqrt(3.0) / 2.0 * 100.0
print("        area {0:.9f} m2 against (3*sqrt(3)/2)*r^2 = {1:.9f}".format(
    rp_geom.area(), exact_hex))
check("area is (3 sqrt3 / 2) r^2", rp_geom.area(), exact_hex, 1e-9)
check("the ring has 6 sides", len(vertices(rp_geom)) - 1, 6)
side_lengths = np.hypot(*np.diff(vertices(rp_geom), axis=0).T)
check("for n = 6 the side equals the circumradius",
      float(side_lengths.mean()), 10.0, 1e-9)
check("every side is the same length",
      float(side_lengths.max() - side_lengths.min()), 0.0, 1e-9)

expected_rp, _ = pr.build(pr.TOOL_POLYGON,
                          {"x": OX, "y": OY, "n_sides": 6, "radius_m": 10.0,
                           "azimuth_deg": 0.0}, WORK_CRS.authid())
check("WKT matches the engine", max_vertex_gap(rp_geom, expected_rp), 0.0, 1e-6)

print("\n== RP2: radius, apothem, side and area agree ==")
n = 6
radius = 10.0
rp_equivalents = {
    rp_tool.SIZE_RADIUS: radius,
    rp_tool.SIZE_APOTHEM: radius * math.cos(math.pi / n),
    rp_tool.SIZE_SIDE: 2.0 * radius * math.sin(math.pi / n),
    rp_tool.SIZE_AREA: 0.5 * n * radius ** 2 * math.sin(2.0 * math.pi / n),
}
rp_geoms = {}
for mode, value in rp_equivalents.items():
    session = rp_tool.RegularPolygonSession(n_sides=n, size_mode=mode)
    session.set_origin(OX, OY)
    session.submit("{0:.12f}".format(value))
    session.submit("0d")
    params = session.build_params()
    check_true("{0}: exactly one size reaches the engine".format(mode),
               len([k for k in params if k in rp_tool.SIZE_LABELS]) == 1)
    geom, _rec = pr.build(pr.TOOL_POLYGON, params, WORK_CRS.authid())
    rp_geoms[mode] = geom
    check("{0}: circumradius is 10".format(mode), session.radius_m(), radius,
          1e-9)

rp_reference = rp_geoms[rp_tool.SIZE_RADIUS]
for mode, geom in rp_geoms.items():
    check("{0}: same geometry as the radius form".format(mode),
          max_vertex_gap(geom, rp_reference), 0.0, 1e-6)
    check("{0}: same area".format(mode), geom.area(), exact_hex, 1e-6)

print("\n== RP3: the first vertex sits on the azimuth ==")
for azimuth in (0.0, 30.0, 117.5):
    session = rp_tool.RegularPolygonSession(n_sides=5)
    session.set_origin(OX, OY)
    session.submit("10")
    session.submit("{0}d".format(azimuth))
    geom, _rec = pr.build(pr.TOOL_POLYGON, session.build_params(),
                          WORK_CRS.authid())
    first = vertices(geom)[0]
    bearing = math.degrees(math.atan2(first[0] - OX, first[1] - OY)) % 360.0
    check("azimuth {0}: the first vertex bears {0}".format(azimuth),
          bearing, azimuth % 360.0, 1e-9)
    check("azimuth {0}: it is one circumradius away".format(azimuth),
          math.hypot(first[0] - OX, first[1] - OY), 10.0, 1e-9)

print("\n== RP4: fewer than three sides is refused ==")
before_count = rp_layer.featureCount()
for bad_n in (2, 1, 0):
    session = rp_tool.RegularPolygonSession(n_sides=bad_n)
    session.set_origin(OX, OY)
    session.submit("10")
    session.submit("0d")
    check_raises("n_sides = {0} is refused".format(bad_n), InvalidInputError,
                 pr.build, pr.TOOL_POLYGON, session.build_params(),
                 WORK_CRS.authid())
    check_true("n_sides = {0} previews nothing".format(bad_n),
               session.preview_points() is None)
check("no feature was written by any of them", rp_layer.featureCount(),
      before_count)
try:
    ge.regular_polygon((OX, OY), 2, 0.0, radius_m=10.0)
    rp_message = ""
except InvalidInputError as exc:
    rp_message = exc.user_message
check_true("the refusal is Italian and explains the minimum",
           "almeno 3 lati" in rp_message.lower())
print("        {0}".format(rp_message))

print("\n== RP5: 100 hovers build nothing ==")
rp_hover_layer = scratch_layer("Polygon", "cad_polygon_hover")
rp_map = rp_tool.create(canvas, iface=None,
                        layer_provider=lambda: rp_hover_layer)
rp_map.activate()
rp_map.session.set_origin(OX, OY)
baseline = canvas.refresh_calls
for step in range(100):
    rp_map.canvasMoveEvent(Move(OX + step * 0.3, OY + step * 0.6))
check("no feature added during 100 hovers", rp_hover_layer.featureCount(), 0)
check("no QgsGeometry built during 100 hovers", rp_map.geometry_builds, 0)
check("no canvas.refresh() during 100 hovers",
      canvas.refresh_calls - baseline, 0)
rp_map.deactivate()

rp_record = pa.read_record(rp_feature)
check_true("cad_params round-trips off the feature", rp_record is not None)
check("cad_params n_sides", rp_record.params["n_sides"], 6)
rp_rebuilt, _ = pr.rebuild(rp_record)
check("record rebuilds the identical geometry",
      max_vertex_gap(rp_rebuilt, rp_geom), 0.0, 1e-9)

# --------------------------------------------------------------------------
# R2 - registration
# --------------------------------------------------------------------------
print("\n== R2: the three tools are registered like the others ==")
# v1.4.1: eight became ten when Move and Resize joined; v1.4.3 made
# it eleven with the Arco; v1.5.0 adds the digitizer and the manual input,
# both of which create polygons, so the registry is twelve and
# TOOL_GEOMETRY is nine.
check("the registry holds twelve tools", len(tools_pkg.TOOL_REGISTRY), 12)
for key, label, cls in (("square", "Quadrato", sq_tool.SquareSession),
                        ("regular_polygon", "Poligono regolare",
                         rp_tool.RegularPolygonSession)):
    check_true("{0} is in the registry".format(key),
               key in tools_pkg.TOOL_REGISTRY)
    check_true("{0} has the right label".format(key),
               tools_pkg.tool_label(key) == label)
    check_true("{0} has a shortcut".format(key),
               bool(tools_pkg.tool_shortcut(key)))
    built = tools_pkg.create_tool(key, canvas, layer_provider=lambda: None)
    check_true("{0} instantiates through the registry".format(key),
               isinstance(built, tb.CadMapTool)
               and isinstance(built.session, cls))
    check_true("{0} writes polygons".format(key),
               built.session.geometry_type == "Polygon")
    built.deactivate()
    check_true("{0} creates geometry, so it has a layer type".format(key),
               plugin_mod.TOOL_GEOMETRY.get(key) == "Polygon")
    check_true("{0} is not edit-in-place".format(key),
               key not in plugin_mod.EDIT_IN_PLACE_TOOLS)
    check_true("{0} is on the dock toolbar order".format(key),
               key in plugin_mod.CAD_TOOL_ORDER)

shortcuts = [tools_pkg.tool_shortcut(k) for k in tools_pkg.TOOL_REGISTRY]
check("every shortcut is distinct", len(set(shortcuts)), len(shortcuts))
check_true("every creating tool has a geometry type",
           set(plugin_mod.TOOL_GEOMETRY)
           == set(tools_pkg.TOOL_REGISTRY) - set(plugin_mod.EDIT_IN_PLACE_TOOLS))
check("nine tools create geometry", len(plugin_mod.TOOL_GEOMETRY), 9)
check("the dock mounts all twelve", len(plugin_mod.CAD_TOOL_ORDER), 12)


# ==========================================================================
# v1.4.1 - Move and Resize, both edit-in-place
# ==========================================================================
from geocad_uav.cad.tools import move as mv_tool                 # noqa: E402
from geocad_uav.cad.tools import resize as rs_tool               # noqa: E402
from geocad_uav.core.errors import ConstraintError as _CErr      # noqa: E402


def build_feature(layer, tool, params):
    """Write one parametric feature and hand it back off the layer."""
    geom, record = pr.build(tool, params, WORK_CRS.authid())
    feature = QgsFeature(layer.fields())
    feature.setGeometry(geom)
    attributes = pa.record_to_attributes(record)
    values = [attributes.get(field.name()) for field in layer.fields()]
    feature.setAttributes(values)
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    newest = max(layer.getFeatures(), key=lambda f: f.id())
    return newest


def wkt_of(layer, fid):
    return layer.getFeature(fid).geometry().asWkt(9)


def params_of(layer, fid):
    record = pa.read_record(layer.getFeature(fid))
    return None if record is None else dict(pr.input_params(record))


# --------------------------------------------------------------------------
# MV1 - a rectangle moves without changing shape
# --------------------------------------------------------------------------
print("\n== MV1: rect 50 x 30, dx = 10, dy = -4 ==")
mv_layer = scratch_layer("Polygon", "cad_move")
mv_feature = build_feature(mv_layer, pr.TOOL_RECTANGLE,
                           {"mode": "center", "x": OX, "y": OY,
                            "width_m": 50.0, "height_m": 30.0,
                            "azimuth_deg": 0.0})
before_area = mv_feature.geometry().area()
before_centre = mv_feature.geometry().boundingBox().center()
check("the fixture is 50 x 30", before_area, 1500.0, 1e-6)

mv = mv_tool.create(canvas, iface=None, layer_provider=lambda: mv_layer)
mv.activate()
mv.adopt_feature(mv_layer, mv_feature)
check_true("the session captured the feature", mv.session.has_feature)
mv.session.submit("@10,-4")
check_true("the typed token filled both deltas",
           mv.session.value("dx_m") == 10.0 and mv.session.value("dy_m") == -4.0)
moved, kept = mv.move_committed()

check("area is unchanged", moved.area(), 1500.0, 1e-6)
check("perimeter is unchanged", moved.length(), 160.0, 1e-6)
after_centre = moved.boundingBox().center()
check("the centre moved by dx", after_centre.x() - before_centre.x(), 10.0,
      1e-9)
check("...and by dy", after_centre.y() - before_centre.y(), -4.0, 1e-9)
check("still one feature on the layer", mv_layer.featureCount(), 1)

mv_params = params_of(mv_layer, mv_feature.id())
check_true("cad_params were kept", kept)
check("width is untouched", mv_params["width_m"], 50.0)
check("height is untouched", mv_params["height_m"], 30.0)
check("azimuth is untouched", mv_params["azimuth_deg"], 0.0)
check("the anchor followed the move in x", mv_params["x"], OX + 10.0, 1e-9)
check("...and in y", mv_params["y"], OY - 4.0, 1e-9)

mv_record = pa.read_record(mv_layer.getFeature(mv_feature.id()))
rebuilt_mv, _ = pr.rebuild(mv_record)
check("the record rebuilds exactly where the geometry now is",
      max_vertex_gap(rebuilt_mv, moved), 0.0, 1e-9)

# --------------------------------------------------------------------------
# MV2 - a circle dragged 25 m north
# --------------------------------------------------------------------------
print("\n== MV2: circle r = 10 dragged 25 m north ==")
mv2_layer = scratch_layer("Polygon", "cad_move_circle")
mv2_feature = build_feature(mv2_layer, pr.TOOL_CIRCLE,
                            {"mode": "center_radius", "x": OX, "y": OY,
                             "radius_m": 10.0})
before_area = mv2_feature.geometry().area()
mv2 = mv_tool.create(canvas, iface=None, layer_provider=lambda: mv2_layer)
mv2.activate()
mv2.adopt_feature(mv2_layer, mv2_feature)
mv2.session.begin_drag(OX, OY)
mv2.session.hover(OX, OY + 25.0)
check("the drag produced dy = 25", mv2.session.value("dy_m"), 25.0, 1e-9)
check("...and dx = 0", mv2.session.value("dx_m"), 0.0, 1e-9)
mv2.session.end_drag()
moved2, kept2 = mv2.move_committed()

# 1e-6 m2 on 313 m2 is 3e-9 relative: what a shoelace sum holds after the
# whole ring is shifted 25 m at UTM northings of 5 000 000.
check("area is unchanged by the drag", moved2.area(), before_area, 1e-6)
check_true("area is still within 0.5 % of pi r^2, as for a fresh circle",
           abs(moved2.area() - math.pi * 100.0) / (math.pi * 100.0) < 0.005)
centre2 = moved2.boundingBox().center()
check("the centre moved 25 m north", centre2.y() - OY, 25.0, 1e-9)
check("...and not at all east", centre2.x() - OX, 0.0, 1e-9)
mv2_params = params_of(mv2_layer, mv2_feature.id())
check("the radius is untouched", mv2_params["radius_m"], 10.0)
check("the anchor followed", mv2_params["y"], OY + 25.0, 1e-9)

# --------------------------------------------------------------------------
# MV3 - Escape mid-drag changes nothing
# --------------------------------------------------------------------------
print("\n== MV3: Escape half way through a drag ==")
mv3_layer = scratch_layer("Polygon", "cad_move_escape")
mv3_feature = build_feature(mv3_layer, pr.TOOL_SQUARE,
                            {"x": OX, "y": OY, "side_m": 12.0,
                             "azimuth_deg": 30.0})
fid3 = mv3_feature.id()
wkt_before = wkt_of(mv3_layer, fid3)
params_before = params_of(mv3_layer, fid3)
count_before = mv3_layer.featureCount()

mv3 = mv_tool.create(canvas, iface=None, layer_provider=lambda: mv3_layer)
mv3.activate()
mv3.adopt_feature(mv3_layer, mv3_feature)
mv3.session.begin_drag(OX, OY)
mv3.session.hover(OX + 40.0, OY + 40.0)
check_true("a delta was accumulated", mv3.session.value("dx_m") == 40.0)
mv3._escape()

check_true("the session is IDLE", mv3.session.state == tb.ToolState.IDLE)
check_true("nothing is captured any more", not mv3.session.has_feature)
check_true("the WKT is byte-identical", wkt_of(mv3_layer, fid3) == wkt_before)
check_true("cad_params are identical",
           params_of(mv3_layer, fid3) == params_before)
check("no feature was added", mv3_layer.featureCount(), count_before)

# --------------------------------------------------------------------------
# MV4 - hovering is free, committing is one undo command
# --------------------------------------------------------------------------
print("\n== MV4: 100 hovers, then one commit ==")
mv4_layer = scratch_layer("Polygon", "cad_move_hover")
mv4_feature = build_feature(mv4_layer, pr.TOOL_RECTANGLE,
                            {"mode": "center", "x": OX, "y": OY,
                             "width_m": 20.0, "height_m": 10.0,
                             "azimuth_deg": 0.0})
fid4 = mv4_feature.id()
wkt4_before = wkt_of(mv4_layer, fid4)
mv4 = mv_tool.create(canvas, iface=None, layer_provider=lambda: mv4_layer)
mv4.activate()
mv4.adopt_feature(mv4_layer, mv4_feature)
mv4.session.begin_drag(OX, OY)

baseline_refresh = canvas.refresh_calls
builds_before = mv4.geometry_builds
mv4_layer.startEditing()
stack = mv4_layer.undoStack()
commands_before = stack.count()
for step in range(100):
    mv4.session.hover(OX + step * 0.5, OY + step * 0.25)
check("no QgsGeometry built during 100 hovers",
      mv4.geometry_builds - builds_before, 0)
check("no canvas.refresh() during 100 hovers",
      canvas.refresh_calls - baseline_refresh, 0)
check("no undo command opened during 100 hovers",
      stack.count() - commands_before, 0)
check("no feature added", mv4_layer.featureCount(), 1)

mv4.session.end_drag()
mv4.move_committed()
check("exactly one geometry was built, at commit time",
      mv4.geometry_builds - builds_before, 1)
check("the commit is a single undo command", stack.count() - commands_before, 1)
check_true("the geometry really moved",
           wkt_of(mv4_layer, fid4) != wkt4_before)
stack.undo()
check_true("one undo puts the geometry back",
           wkt_of(mv4_layer, fid4) == wkt4_before)
mv4_layer.rollBack()

# --------------------------------------------------------------------------
# MV5 - a feature whose record is broken still moves
# --------------------------------------------------------------------------
print("\n== MV5: broken cad_params ==")
mv5_layer = scratch_layer("Polygon", "cad_move_broken")
mv5_feature = build_feature(mv5_layer, pr.TOOL_RECTANGLE,
                            {"mode": "center", "x": OX, "y": OY,
                             "width_m": 40.0, "height_m": 20.0,
                             "azimuth_deg": 0.0})
fid5 = mv5_feature.id()
index = mv5_layer.fields().indexOf(pa.PARAMS_FIELD)
mv5_layer.dataProvider().changeAttributeValues(
    {fid5: {index: "{not json at all"}})
broken_feature = mv5_layer.getFeature(fid5)


def unreadable(feature):
    """True when cad_params cannot be parsed at all.

    read_record raises GeometryError on malformed JSON rather than returning
    None, which is what move.py catches; the test asserts that behaviour
    instead of assuming a quieter one.
    """
    try:
        return pa.read_record(feature) is None
    except GeoCadError:
        return True


check_true("the record can no longer be read", unreadable(broken_feature))

area_before = broken_feature.geometry().area()
mv5 = mv_tool.create(canvas, iface=None, layer_provider=lambda: mv5_layer)
mv5.activate()
warnings_seen = []
mv5._warn = lambda text: warnings_seen.append(text)
mv5.adopt_feature(mv5_layer, broken_feature)
mv5.session.submit("@5,5")
moved5, kept5 = mv5.move_committed()

check_true("the parameters were not claimed to survive", not kept5)
check("the geometry moved anyway", moved5.area(), area_before, 1e-6)
centre5 = moved5.boundingBox().center()
check("...by exactly the delta", centre5.x() - OX, 5.0, 1e-9)
check("still one feature", mv5_layer.featureCount(), 1)
check_true("the operator was warned, in Italian",
           any("parametri cad" in w.lower() for w in warnings_seen))
check_true("the record is still unreadable, not silently rewritten",
           unreadable(mv5_layer.getFeature(fid5)))
print("        {0}".format(warnings_seen[0] if warnings_seen else ""))

# --------------------------------------------------------------------------
# MV6 - a geographic CRS is refused, exactly as Rotate refuses it
# --------------------------------------------------------------------------
print("\n== MV6: EPSG:4326 goes through the same CRS gate as Rotate ==")
geo_canvas = CountingCanvas()
geo_canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
geo_canvas.setExtent(QgsRectangle(9.0, 45.0, 9.1, 45.1))
geo_layer = lf.memory_layer("Polygon", "cad_move_geo", "EPSG:4326",
                            pa.METADATA_FIELDS)
QgsProject.instance().addMapLayer(geo_layer)

mv6 = mv_tool.create(geo_canvas, iface=None, layer_provider=lambda: geo_layer)
mv6.activate()
rot6 = rot_tool.create(geo_canvas, iface=None, layer_provider=lambda: geo_layer)
rot6.activate()
print("        move decision {0!r}, rotate decision {1!r}".format(
    mv6._work_decision, rot6._work_decision))
check_true("Move reaches the same verdict as Rotate on the same canvas",
           (mv6._work_decision is None) == (rot6._work_decision is None))
if mv6._work_decision is None:
    check_true("blocked, with a reason in Italian",
               bool((mv6._blocked_reason or "").strip()))
    check_true("Rotate is blocked for the same reason",
               mv6._blocked_reason == rot6._blocked_reason)
else:
    check_true("a projected working CRS was resolved instead of using degrees",
               mv6.work_crs_object is not None
               and not crs_svc.is_geographic(mv6.work_crs_object))
    check_true("Rotate resolved the same one",
               rot6.work_crs_object.authid() == mv6.work_crs_object.authid())
check("no feature was created on the geographic layer",
      geo_layer.featureCount(), 0)
mv6.deactivate()
rot6.deactivate()

# --------------------------------------------------------------------------
# RS1 - a rectangle resized to an absolute width
# --------------------------------------------------------------------------
print("\n== RS1: rect 50 x 30, width -> 70 ==")
rs_layer = scratch_layer("Polygon", "cad_resize")
rs_feature = build_feature(rs_layer, pr.TOOL_RECTANGLE,
                           {"mode": "center", "x": OX, "y": OY,
                            "width_m": 50.0, "height_m": 30.0,
                            "azimuth_deg": 0.0})
fid_rs = rs_feature.id()
centre_before = rs_feature.geometry().boundingBox().center()

rs = rs_tool.create(canvas, iface=None, layer_provider=lambda: rs_layer)
rs.activate()
targets = rs.adopt_feature(rs_layer, rs_feature)
print("        editable dimensions: {0}".format(targets))
check_true("both sides of a rectangle are offered",
           targets == ["width_m", "height_m"])
rs.session.submit("70")                          # width
rs.session.submit("30")                          # height, unchanged
resized, ok = rs.resize_committed()

check("area is 70 x 30", resized.area(), 2100.0, 1e-6)
check("the height did not change", params_of(rs_layer, fid_rs)["height_m"],
      30.0)
check("the width is the typed one", params_of(rs_layer, fid_rs)["width_m"],
      70.0)
centre_after = resized.boundingBox().center()
check("the centre did not move in x", centre_after.x() - centre_before.x(),
      0.0, 1e-9)
check("...nor in y", centre_after.y() - centre_before.y(), 0.0, 1e-9)
check("the azimuth is untouched", params_of(rs_layer, fid_rs)["azimuth_deg"],
      0.0)
check_true("the record still describes the shape", ok)
check("still one feature", rs_layer.featureCount(), 1)
rs_record = pa.read_record(rs_layer.getFeature(fid_rs))
rebuilt_rs, _ = pr.rebuild(rs_record)
check("the record rebuilds the resized geometry",
      max_vertex_gap(rebuilt_rs, resized), 0.0, 1e-9)

# --------------------------------------------------------------------------
# RS2 - a square stays square, a circle stays round
# --------------------------------------------------------------------------
print("\n== RS2: square 10 -> 7, circle r 10 -> 4 ==")
sq_rs_layer = scratch_layer("Polygon", "cad_resize_square")
sq_rs_feature = build_feature(sq_rs_layer, pr.TOOL_SQUARE,
                              {"x": OX, "y": OY, "side_m": 10.0,
                               "azimuth_deg": 0.0})
sq_rs = rs_tool.create(canvas, iface=None,
                       layer_provider=lambda: sq_rs_layer)
sq_rs.activate()
sq_targets = sq_rs.adopt_feature(sq_rs_layer, sq_rs_feature)
check_true("a square offers exactly one size", sq_targets == ["side_m"])
sq_rs.session.submit("7")
sq_resized, _ = sq_rs.resize_committed()
check("area is 7 x 7", sq_resized.area(), 49.0, 1e-6)
sq_box = sq_resized.boundingBox()
check("it is still square", sq_box.width() - sq_box.height(), 0.0, 1e-9)
check("the side is 7", sq_box.width(), 7.0, 1e-9)

ci_rs_layer = scratch_layer("Polygon", "cad_resize_circle")
ci_rs_feature = build_feature(ci_rs_layer, pr.TOOL_CIRCLE,
                              {"mode": "center_radius", "x": OX, "y": OY,
                               "radius_m": 10.0})
ci_rs = rs_tool.create(canvas, iface=None,
                       layer_provider=lambda: ci_rs_layer)
ci_rs.activate()
ci_targets = ci_rs.adopt_feature(ci_rs_layer, ci_rs_feature)
check_true("a circle offers its radius", ci_targets == ["radius_m"])
ci_rs.session.submit("4")
ci_resized, _ = ci_rs.resize_committed()
check_true("area is within 0.5 % of pi * 16",
           abs(ci_resized.area() - math.pi * 16.0) / (math.pi * 16.0) < 0.005)
check("the centre did not move",
      ci_resized.boundingBox().center().x(), OX, 1e-9)
check("the stored radius is 4",
      params_of(ci_rs_layer, ci_rs_feature.id())["radius_m"], 4.0)

# --------------------------------------------------------------------------
# RS3 - an ellipse refuses b > a and stays as it was
# --------------------------------------------------------------------------
print("\n== RS3: ellipse a = 20, b -> 25 ==")
el_rs_layer = scratch_layer("Polygon", "cad_resize_ellipse")
el_rs_feature = build_feature(el_rs_layer, pr.TOOL_ELLIPSE,
                              {"x": OX, "y": OY, "semi_major_m": 20.0,
                               "semi_minor_m": 10.0, "azimuth_deg": 0.0})
fid_el = el_rs_feature.id()
wkt_el_before = wkt_of(el_rs_layer, fid_el)
params_el_before = params_of(el_rs_layer, fid_el)

el_rs = rs_tool.create(canvas, iface=None,
                       layer_provider=lambda: el_rs_layer)
el_rs.activate()
el_targets = el_rs.adopt_feature(el_rs_layer, el_rs_feature)
check_true("both semi-axes are offered",
           el_targets == ["semi_major_m", "semi_minor_m"])
el_rs.session.submit("20")                       # semi-major unchanged
el_rs.session.submit("25")                       # semi-minor, too large
check_raises("the commit raises ConstraintError", _CErr,
             el_rs.resize_committed)
check_true("the WKT is unchanged", wkt_of(el_rs_layer, fid_el) == wkt_el_before)
check_true("cad_params are unchanged",
           params_of(el_rs_layer, fid_el) == params_el_before)
check_true("no silent swap: a is still 20 and b still 10",
           params_of(el_rs_layer, fid_el)["semi_major_m"] == 20.0
           and params_of(el_rs_layer, fid_el)["semi_minor_m"] == 10.0)
check("still one feature", el_rs_layer.featureCount(), 1)
try:
    pr.build(pr.TOOL_ELLIPSE,
             rs_tool.resized_params(pa.read_record(
                 el_rs_layer.getFeature(fid_el)),
                 {"semi_minor_m": 25.0}), WORK_CRS.authid())
    el_message = ""
except _CErr as exc:
    el_message = exc.user_message
check_true("the message is Italian and names the semi-axis",
           "semiasse" in el_message.lower())
print("        {0}".format(el_message))

# --------------------------------------------------------------------------
# RS4 - a polyline has no single dimension
# --------------------------------------------------------------------------
print("\n== RS4: a polyline cannot be resized ==")
pl_layer = scratch_layer("LineString", "cad_resize_polyline")
pl_feature = build_feature(pl_layer, pr.TOOL_POLYLINE,
                           {"x": OX, "y": OY,
                            "points": [[OX, OY], [OX + 10, OY],
                                       [OX + 10, OY + 10]]})
count_pl = pl_layer.featureCount()
pl_rs = rs_tool.create(canvas, iface=None, layer_provider=lambda: pl_layer)
pl_rs.activate()
check_raises("adopting a polyline is refused", InvalidInputError,
             pl_rs.adopt_feature, pl_layer, pl_feature)
check("the feature count is unchanged", pl_layer.featureCount(), count_pl)
check_true("nothing was captured", not pl_rs.session.has_feature)
try:
    rs_tool.editable_dimensions(pa.read_record(pl_feature))
    pl_message = ""
except InvalidInputError as exc:
    pl_message = exc.user_message
check_true("the refusal names the polyline, in Italian",
           "polilinea" in pl_message.lower())
print("        {0}".format(pl_message))

# --------------------------------------------------------------------------
# RS5 - Escape and hovering
# --------------------------------------------------------------------------
print("\n== RS5: Escape and 100 hovers ==")
rs5_layer = scratch_layer("Polygon", "cad_resize_escape")
rs5_feature = build_feature(rs5_layer, pr.TOOL_RECTANGLE,
                            {"mode": "center", "x": OX, "y": OY,
                             "width_m": 25.0, "height_m": 15.0,
                             "azimuth_deg": 0.0})
fid_rs5 = rs5_feature.id()
wkt_rs5 = wkt_of(rs5_layer, fid_rs5)
params_rs5 = params_of(rs5_layer, fid_rs5)

rs5 = rs_tool.create(canvas, iface=None, layer_provider=lambda: rs5_layer)
rs5.activate()
rs5.adopt_feature(rs5_layer, rs5_feature)
rs5.session.submit("80")
rs5._escape()
check_true("the session is IDLE", rs5.session.state == tb.ToolState.IDLE)
check_true("the WKT is byte-identical", wkt_of(rs5_layer, fid_rs5) == wkt_rs5)
check_true("cad_params are identical",
           params_of(rs5_layer, fid_rs5) == params_rs5)
check("no feature was added", rs5_layer.featureCount(), 1)

rs5.adopt_feature(rs5_layer, rs5_layer.getFeature(fid_rs5))
baseline_refresh = canvas.refresh_calls
builds_before = rs5.geometry_builds
rs5_layer.startEditing()
stack5 = rs5_layer.undoStack()
commands_before = stack5.count()
for step in range(100):
    rs5.session.hover(OX + step * 0.4, OY + step * 0.4)
check("no QgsGeometry built during 100 hovers",
      rs5.geometry_builds - builds_before, 0)
check("no canvas.refresh() during 100 hovers",
      canvas.refresh_calls - baseline_refresh, 0)
check("no undo command opened during 100 hovers",
      stack5.count() - commands_before, 0)
rs5_layer.rollBack()

# --------------------------------------------------------------------------
# R2 (1.4.1) - registration
# --------------------------------------------------------------------------
print("\n== R2: Move and Resize are registered as edit-in-place ==")
check("the registry holds twelve tools", len(tools_pkg.TOOL_REGISTRY), 12)
check("nine tools still create geometry", len(plugin_mod.TOOL_GEOMETRY), 9)
check("the dock mounts all twelve", len(plugin_mod.CAD_TOOL_ORDER), 12)
check_true("the registry and the toolbar order agree",
           set(tools_pkg.TOOL_REGISTRY) == set(plugin_mod.CAD_TOOL_ORDER))
for key, label, cls in (("move", "Sposta", mv_tool.MoveTool),
                        ("resize", "Ridimensiona", rs_tool.ResizeTool)):
    check_true("{0} is in the registry".format(key),
               key in tools_pkg.TOOL_REGISTRY)
    check_true("{0} has the right label".format(key),
               tools_pkg.tool_label(key) == label)
    built = tools_pkg.create_tool(key, canvas, layer_provider=lambda: None)
    check_true("{0} instantiates through the registry".format(key),
               isinstance(built, cls))
    built.deactivate()
    check_true("{0} is edit-in-place".format(key),
               key in plugin_mod.EDIT_IN_PLACE_TOOLS)
    check_true("{0} never gets a scratch layer".format(key),
               key not in plugin_mod.TOOL_GEOMETRY)
check_true("every creating tool still has a geometry type",
           set(plugin_mod.TOOL_GEOMETRY)
           == set(tools_pkg.TOOL_REGISTRY) - set(plugin_mod.EDIT_IN_PLACE_TOOLS))
shortcuts = [tools_pkg.tool_shortcut(k) for k in tools_pkg.TOOL_REGISTRY]
check("every shortcut is still distinct", len(set(shortcuts)), len(shortcuts))
check_true("neither tool imports the mission side",
           all(token not in open(
               os.path.join(os.path.dirname(os.path.dirname(
                   os.path.abspath(__file__))), "cad", "tools", name),
               encoding="utf-8").read()
               for name in ("move.py", "resize.py")
               for token in ("last_mission", "uav.", "export")))


# ==========================================================================
# v1.4.2 slice A - the CAD attributes every commit must write
# ==========================================================================
def newest(layer):
    """The feature the layer holds last.

    commit() hands back the QgsFeature it built, whose id stays provisional
    until the provider assigns one, so every attribute assertion below reads
    the value off the layer rather than off that object.
    """
    return max(layer.getFeatures(), key=lambda f: f.id())


print("\n== AT1: rectangle 50 x 30 ==")
at_layer = scratch_layer("Polygon", "cad_attrs")
check_true("the fixture layer starts without the CAD columns",
           lf.CAD_ID_FIELD not in {f.name() for f in at_layer.fields()})

at_session = rect_tool.RectangleSession(reference=rect_tool.REFERENCE_CENTER)
at_tool = tb.BaseCadTool(at_session)
at_session.set_origin(OX, OY)
at_session.submit("50")
at_session.submit("30")
at_session.submit("0d")
first = at_tool.commit(at_layer, WORK_CRS, at_layer.crs())

names = {f.name() for f in at_layer.fields()}
check_true("cad_id was added to the layer", lf.CAD_ID_FIELD in names)
check_true("area_ha was added", lf.AREA_HA_FIELD in names)
check_true("perimeter_m was added", lf.PERIMETER_FIELD in names)
check_true("cad_params is still there", pa.PARAMS_FIELD in names)

written = newest(at_layer)
print("        cad_id {0}, area_ha {1}, perimeter_m {2}".format(
    written[lf.CAD_ID_FIELD], written[lf.AREA_HA_FIELD],
    written[lf.PERIMETER_FIELD]))
check("cad_id is 1 on the first feature", written[lf.CAD_ID_FIELD], 1)
check("area_ha is 1500 m2 in hectares", written[lf.AREA_HA_FIELD], 0.15, 1e-9)
check("perimeter_m is 2*(50+30)", written[lf.PERIMETER_FIELD], 160.0, 1e-6)
check("the area column agrees with the engine's measure()",
      written[lf.AREA_HA_FIELD],
      round(written.geometry().area() / 10_000.0, 2), 1e-9)

at_session.set_origin(OX + 200, OY)
at_session.submit("50")
at_session.submit("30")
at_session.submit("0d")
second = at_tool.commit(at_layer, WORK_CRS, at_layer.crs())
second = newest(at_layer)
check("the second feature takes cad_id 2", second[lf.CAD_ID_FIELD], 2)
check("...with the same area", second[lf.AREA_HA_FIELD], 0.15, 1e-9)
check("two features on the layer", at_layer.featureCount(), 2)

print("\n== AT2: circle r = 10 ==")
ci_layer = scratch_layer("Polygon", "cad_attrs_circle")
ci_session = circle_tool.CircleSession()
ci = tb.BaseCadTool(ci_session)
ci_session.set_origin(OX, OY)
ci_session.submit("10")
ci_feature = ci.commit(ci_layer, WORK_CRS, ci_layer.crs())
ci_written = newest(ci_layer)

engine_area = ci_written.geometry().area()
engine_perimeter = ci_written.geometry().length()
print("        area {0:.6f} m2 -> {1} ha, perimeter {2:.6f} m".format(
    engine_area, ci_written[lf.AREA_HA_FIELD], engine_perimeter))
check("area_ha is the measured area, not pi r^2",
      ci_written[lf.AREA_HA_FIELD], round(engine_area / 10_000.0, 2), 1e-9)
check("area_ha rounds to 0.03 ha", ci_written[lf.AREA_HA_FIELD], 0.03, 1e-9)
check("perimeter_m is the measured perimeter, not 2 pi r",
      ci_written[lf.PERIMETER_FIELD], round(engine_perimeter, 3), 1e-9)
check_true("...which is shorter than the true circumference",
           ci_written[lf.PERIMETER_FIELD] < 2.0 * math.pi * 10.0)
check("cad_id is 1 on its own layer", ci_written[lf.CAD_ID_FIELD], 1)

print("\n== AT3: a line has no area ==")
ln_layer = scratch_layer("LineString", "cad_attrs_line")
ln_session = line_tool.LineSession()
ln = tb.BaseCadTool(ln_session)
ln_session.set_origin(OX, OY)
ln_session.submit("100")
ln_session.submit("0d")
ln_feature = ln.commit(ln_layer, WORK_CRS, ln_layer.crs())
ln_written = newest(ln_layer)
check("area_ha is 0.00 for a line", ln_written[lf.AREA_HA_FIELD], 0.0)
check("perimeter_m carries the length", ln_written[lf.PERIMETER_FIELD],
      100.0, 1e-6)
check("cad_id is 1", ln_written[lf.CAD_ID_FIELD], 1)
check("the geometry really is 100 m long",
      ln_written.geometry().length(), 100.0, 1e-6)

print("\n== AT4: ids continue from what the layer already holds ==")
seeded = lf.memory_layer("Polygon", "cad_attrs_seeded", WORK_CRS.authid(),
                         pa.METADATA_FIELDS + lf.CAD_ATTRIBUTE_FIELDS)
seed_geom, seed_record = pr.build(
    pr.TOOL_RECTANGLE, {"mode": "center", "x": OX, "y": OY, "width_m": 10.0,
                        "height_m": 10.0, "azimuth_deg": 0.0},
    WORK_CRS.authid())
seed = QgsFeature(seeded.fields())
seed.setGeometry(seed_geom)
seed_attrs = pa.record_to_attributes(seed_record)
seed_attrs[lf.CAD_ID_FIELD] = 7
seeded.dataProvider().addFeatures([seed])
index = seeded.fields().indexOf(lf.CAD_ID_FIELD)
seeded.dataProvider().changeAttributeValues(
    {max(f.id() for f in seeded.getFeatures()): {index: 7}})
check("the seeded feature carries cad_id 7",
      max(seeded.getFeatures(), key=lambda f: f.id())[lf.CAD_ID_FIELD], 7)
check("next_cad_id reads the maximum, not the count",
      lf.next_cad_id(seeded), 8)

seed_session = rect_tool.RectangleSession(
    reference=rect_tool.REFERENCE_CENTER)
seed_tool = tb.BaseCadTool(seed_session)
seed_session.set_origin(OX + 100, OY)
seed_session.submit("20")
seed_session.submit("20")
seed_session.submit("0d")
eighth = seed_tool.commit(seeded, WORK_CRS, seeded.crs())
check("the new feature takes cad_id 8",
      newest(seeded)[lf.CAD_ID_FIELD], 8)
check("area_ha of a 20 x 20", newest(seeded)[lf.AREA_HA_FIELD],
      0.04, 1e-9)

print("\n== AT5: a layer that refuses new columns still gets the geometry ==")


class RefusingLayer:
    """A layer whose provider cannot add attributes.

    Not a mock of the tool: a stand-in for a read-only source, which is the
    case the operator actually hits on a shapefile they do not own.
    """

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def dataProvider(self):                                     # noqa: N802
        provider = self._wrapped.dataProvider()

        class NoAttributes:
            def __getattr__(inner, name):                       # noqa: N805
                return getattr(provider, name)

            def capabilities(inner):                            # noqa: N805
                # Everything the real provider can do, minus the one
                # capability under test: the layer still takes features, it
                # just will not grow a column. Returning 0 would have made it
                # refuse the geometry too, which is a different failure.
                return (provider.capabilities()
                        & ~QgsVectorDataProvider.Capability.AddAttributes)

        return NoAttributes()


bare = lf.memory_layer("Polygon", "cad_attrs_readonly", WORK_CRS.authid(),
                       pa.METADATA_FIELDS)
refusing = RefusingLayer(bare)
check_true("ensure_cad_fields reports that it could not",
           lf.ensure_cad_fields(refusing) is None)

ro_session = rect_tool.RectangleSession(reference=rect_tool.REFERENCE_CENTER)
ro_tool = tb.BaseCadTool(ro_session)
ro_session.set_origin(OX, OY)
ro_session.submit("40")
ro_session.submit("25")
ro_session.submit("0d")
ro_feature = ro_tool.commit(refusing, WORK_CRS, bare.crs())
check("the geometry was written anyway", bare.featureCount(), 1)
check("the area is right, it just is not in a column",
      newest(bare).geometry().area(), 1000.0, 1e-6)
check_true("cad_params survived", pa.read_record(newest(bare)) is not None)
check_true("the operator gets a message naming the layer",
           "attributi cad" in ro_tool.attribute_warning.lower()
           and bare.name() in ro_tool.attribute_warning)
print("        {0}".format(ro_tool.attribute_warning))

check_true("a layer that accepts them says so instead",
           lf.ensure_cad_fields(scratch_layer("Polygon", "cad_attrs_ok"))
           == [lf.CAD_ID_FIELD, lf.AREA_HA_FIELD, lf.PERIMETER_FIELD])

print("\n== AT6: the shapes from 1.4.0 and 1.4.1 still commit ==")
mixed = scratch_layer("Polygon", "cad_attrs_mixed")
baseline_refresh = canvas.refresh_calls
expected_ids = []
for index, (session, submissions) in enumerate((
        (sq_tool.SquareSession(), ("10", "0d")),
        (rp_tool.RegularPolygonSession(n_sides=6), ("10", "0d")))):
    tool = tb.BaseCadTool(session)
    session.set_origin(OX + index * 100, OY)
    for text in submissions:
        session.submit(text)
    feature = tool.commit(mixed, WORK_CRS, mixed.crs())
    stored = newest(mixed)
    expected_ids.append(stored[lf.CAD_ID_FIELD])
    check_true("{0} wrote an area".format(session.title),
               stored[lf.AREA_HA_FIELD] > 0.0)
    check("{0}: area_ha matches its own geometry".format(session.title),
          stored[lf.AREA_HA_FIELD],
          round(stored.geometry().area() / 10_000.0, 2), 1e-9)
check_true("the ids are 1 and 2 in order", expected_ids == [1, 2])
check("two features", mixed.featureCount(), 2)
check("no canvas.refresh() was added by the attribute work",
      canvas.refresh_calls - baseline_refresh, 0)


# ==========================================================================
# v1.4.3 - a circle is not an ellipse, an arc, and a pivot on a vertex
# ==========================================================================
from geocad_uav.cad.tools import arc as arc_tool                 # noqa: E402


def spans(points):
    """(east, north) extent of a point array."""
    arr = np.asarray(points, dtype=float)
    return (float(arr[:, 0].max() - arr[:, 0].min()),
            float(arr[:, 1].max() - arr[:, 1].min()))


print("\n== CE2: the circle stays a circle ==")
ce2 = circle_tool.CircleSession()
ce2.set_origin(OX, OY)
ce2.submit("10")
c_pts = ce2.preview_points()
ce, cn = spans(c_pts)
check("the circle preview spans 2r east", ce, 20.0, 1e-6)
check("...and 2r north", cn, 20.0, 1e-6)
check_true("the circle HUD leads with the radius",
           any(line.startswith("R ") for line in ce2.hud_lines()))
_c_geom, c_record = pr.build(pr.TOOL_CIRCLE, ce2.build_params(),
                             WORK_CRS.authid())
check_true("the record says circle", c_record.tool == pr.TOOL_CIRCLE)
check_true("a circle is round: the two spans agree", abs(ce - cn) < 1e-6)
# v1.4.5: this used to compare against the ellipse tool, now withdrawn. The
# engine's ellipse_ring is still there and still not round; test_geometry
# keeps that golden.
check_true("the ellipse primitive is still in the engine, and still oval",
           abs((lambda r: (r[:, 0].max() - r[:, 0].min())
                - (r[:, 1].max() - r[:, 1].min()))(
                   ge.ellipse_ring((OX, OY), 20.0, 10.0, 0.0))) > 1.0)

# --------------------------------------------------------------------------
# AR - the arc
# --------------------------------------------------------------------------
print("\n== AR1: r = 10, sweep 90 degrees ==")
ar_layer = scratch_layer("LineString", "cad_arc")
ar_session = arc_tool.ArcSession()
ar = tb.BaseCadTool(ar_session)
ar_session.set_origin(OX, OY)
ar_session.submit("10")                          # radius
ar_session.submit("0d")                          # start azimuth, North
ar_session.submit("90d")                         # end azimuth, East
check_true("fully constrained -> PREVIEW",
           ar_session.state == tb.ToolState.PREVIEW)
check("the sweep is 90 degrees", ar_session.sweep_deg, 90.0, 1e-9)

ar_feature = ar.commit(ar_layer, WORK_CRS, ar_layer.crs())
ar_geom = ar_feature.geometry()
check("one feature written", ar_layer.featureCount(), 1)
check_true("it is a line, not a polygon",
           ar_geom.type() == QgsWkbTypes.LineGeometry)
check_true("it is not closed",
           vertices(ar_geom)[0].tolist() != vertices(ar_geom)[-1].tolist())

ends = vertices(ar_geom)
check("the first point is due North of the centre",
      float(ends[0][1] - OY), 10.0, 1e-9)
check("...exactly on the axis", float(ends[0][0] - OX), 0.0, 1e-9)
check("the last point is due East", float(ends[-1][0] - OX), 10.0, 1e-9)
check("...on the axis", float(ends[-1][1] - OY), 0.0, 1e-9)

true_length = ge.arc_length(10.0, 90.0)
chord = 2.0 * 10.0 * math.sin(math.radians(90.0) / 2.0)
sagitta = 10.0 * (1.0 - math.cos(math.radians(90.0) / 2.0))
measured = ar_geom.length()
print("        true {0:.6f} m, measured {1:.6f} m ({2:.4f} % low), "
      "chord {3:.6f}, sagitta {4:.6f}".format(
          true_length, measured, 100.0 * (1 - measured / true_length),
          chord, sagitta))
check("the chord is r sqrt2", chord, 10.0 * math.sqrt(2.0), 1e-9)
check("the sagitta is r(1 - cos(sweep/2))", sagitta,
      10.0 * (1.0 - math.cos(math.pi / 4.0)), 1e-9)
check("the chord matches the two endpoints",
      float(np.hypot(*(ends[-1] - ends[0]))), chord, 1e-9)
check_true("the densified length is short of the true arc by less than the "
           "0.5 % the circle test allows",
           0.0 < (true_length - measured) / true_length < 0.005)

print("\n== AR2: three points on a quarter circle ==")
ar2 = arc_tool.ArcSession(mode=arc_tool.MODE_THREE_POINTS)
ar2.set_origin(OX, OY + 10.0)                                    # North
ar2.add_point(OX + 10.0 * math.sin(math.radians(45.0)),
              OY + 10.0 * math.cos(math.radians(45.0)))          # NE
ar2.add_point(OX + 10.0, OY)                                     # East
centre, radius, start, end = ar2.resolved()
check("the solved radius is 10", radius, 10.0, 1e-6)
check("the solved centre x", float(centre[0]), OX, 1e-6)
check("the solved centre y", float(centre[1]), OY, 1e-6)
# A bearing is an angle on a circle: 360 and 0 are the same direction, and
# atan2 of a tiny negative number lands on one or the other. Compare the
# turn between them, not the representative.
check("it starts due North", ge.arc_sweep(0.0, start) % 360.0, 360.0, 1e-6)
check("it ends due East", end % 360.0, 90.0, 1e-6)
check("the sweep is the quarter that holds the middle point",
      ar2.sweep_deg, 90.0, 1e-6)
ar2_geom, _ = pr.build(pr.TOOL_ARC, ar2.build_params(), WORK_CRS.authid())
check("the three-point arc has the same length as the typed one",
      ar2_geom.length(), measured, 1e-6)

print("\n== AR3: three collinear points ==")
ar3 = arc_tool.ArcSession(mode=arc_tool.MODE_THREE_POINTS)
ar3.set_origin(OX, OY)
ar3.add_point(OX + 10.0, OY)
before_count = ar_layer.featureCount()
check_raises("the engine refuses a straight line", ConstraintError,
             ar3.add_point, OX + 20.0, OY)
check_true("nothing was solved", ar3.resolved() is None)
check_true("nothing is previewed", ar3.preview_points() is None)
check("no feature was written", ar_layer.featureCount(), before_count)
try:
    ge.arc_from_3_points((OX, OY), (OX + 10, OY), (OX + 20, OY))
    ar3_message = ""
except ConstraintError as exc:
    ar3_message = exc.user_message
check_true("the refusal is Italian and says they are aligned",
           "allineati" in ar3_message.lower())
print("        {0}".format(ar3_message))

check_raises("a zero radius is refused", InvalidInputError,
             ge.arc_ring, (OX, OY), 0.0, 0.0, 90.0)
check("a sweep of 0 is read as a full turn, not as nothing",
      ge.arc_sweep(30.0, 30.0), 360.0)

print("\n== AR4: the arc carries the CAD attributes ==")
ar4 = newest(ar_layer)
print("        cad_id {0}, area_ha {1}, perimeter_m {2}".format(
    ar4[lf.CAD_ID_FIELD], ar4[lf.AREA_HA_FIELD], ar4[lf.PERIMETER_FIELD]))
check("cad_id is 1", ar4[lf.CAD_ID_FIELD], 1)
check("an arc has no area", ar4[lf.AREA_HA_FIELD], 0.0)
check("perimeter_m is the measured length, not the true arc",
      ar4[lf.PERIMETER_FIELD], round(measured, 3), 1e-9)
ar_record = pa.read_record(ar4)
check_true("the record says arc", ar_record.tool == pr.TOOL_ARC)
check("the record kept the sweep", ar_record.params["sweep_deg"], 90.0, 1e-9)
rebuilt_arc, _ = pr.rebuild(ar_record)
check("the record rebuilds the identical arc",
      max_vertex_gap(rebuilt_arc, ar_geom), 0.0, 1e-9)

print("\n-- two endpoints and a radius: the minor arc, and it says so --")
ar5 = arc_tool.ArcSession(mode=arc_tool.MODE_ENDPOINTS_RADIUS)
ar5.set_origin(OX, OY + 10.0)
ar5.submit("10")
ar5.add_point(OX + 10.0, OY)
check_true("a solution was found", ar5.resolved() is not None)
check_true("the sweep is the minor arc", abs(ar5.sweep_deg) <= 180.0 + 1e-9)
check_true("the operator is told there were two",
           any("minore" in line for line in ar5.hud_lines()))

print("\n-- 100 hovers on the arc --")
ar_hover_layer = scratch_layer("LineString", "cad_arc_hover")
ar_map = arc_tool.create(canvas, iface=None,
                         layer_provider=lambda: ar_hover_layer)
ar_map.activate()
ar_map.session.set_origin(OX, OY)
baseline = canvas.refresh_calls
for step in range(100):
    ar_map.canvasMoveEvent(Move(OX + step * 0.4, OY + step * 0.4))
check("no feature added during 100 hovers", ar_hover_layer.featureCount(), 0)
check("no QgsGeometry built during 100 hovers", ar_map.geometry_builds, 0)
check("no canvas.refresh() during 100 hovers",
      canvas.refresh_calls - baseline, 0)
ar_map.session.cancel()
check("Escape leaves the layer empty", ar_hover_layer.featureCount(), 0)
ar_map.deactivate()

# --------------------------------------------------------------------------
# PV - rotate about a vertex
# --------------------------------------------------------------------------
print("\n== PV1: rectangle rotated about its own SW vertex ==")
pv_layer = scratch_layer("Polygon", "cad_pivot")
pv_feature = build_feature(pv_layer, pr.TOOL_RECTANGLE,
                           {"mode": "center", "x": OX, "y": OY,
                            "width_m": 50.0, "height_m": 30.0,
                            "azimuth_deg": 0.0})
corners_before = vertices(pv_feature.geometry())[:4]
south_west = min(corners_before.tolist(), key=lambda p: (p[0], p[1]))
print("        SW vertex {0:.3f}, {1:.3f}".format(*south_west))

pv = rot_tool.create(canvas, iface=None, layer_provider=lambda: pv_layer)
pv.activate()
pivot = pv.adopt_feature(pv_layer, pv_feature,
                         pivot_mode=rot_tool.PIVOT_VERTEX,
                         custom_pivot=south_west)
check("the pivot landed exactly on the vertex, in x", pivot[0],
      south_west[0], 1e-9)
check("...and in y", pivot[1], south_west[1], 1e-9)
check_true("it is one of the feature's own vertices",
           any(abs(c[0] - pivot[0]) < 1e-9 and abs(c[1] - pivot[1]) < 1e-9
               for c in corners_before))

pv.session.submit("90d")
rotated, _kept = pv.rotate_committed()
corners_after = vertices(rotated)[:4]
check("the area is unchanged", rotated.area(), 1500.0, 1e-6)
check_true("the pivot vertex is still there, fixed",
           any(abs(c[0] - pivot[0]) < 1e-9 and abs(c[1] - pivot[1]) < 1e-9
               for c in corners_after))
moved_count = sum(
    1 for c in corners_after
    if not any(abs(c[0] - b[0]) < 1e-6 and abs(c[1] - b[1]) < 1e-6
               for b in corners_before))
check("the other three corners moved", moved_count, 3)

# the same rotation, done by the frozen numeric engine about the same pivot
expected = t2d.rotate(corners_before, 90.0, pivot)
check("every corner matches transform2d.rotate about that vertex",
      float(np.max(np.min(np.hypot(
          *(corners_after[:, None, :] - expected[None, :, :]).T), axis=0))),
      0.0, 1e-9)

print("\n== PV2: Escape from a vertex rotation ==")
pv2_layer = scratch_layer("Polygon", "cad_pivot_escape")
pv2_feature = build_feature(pv2_layer, pr.TOOL_SQUARE,
                            {"x": OX, "y": OY, "side_m": 20.0,
                             "azimuth_deg": 0.0})
fid_pv2 = pv2_feature.id()
wkt_pv2 = wkt_of(pv2_layer, fid_pv2)
params_pv2 = params_of(pv2_layer, fid_pv2)
pv2 = rot_tool.create(canvas, iface=None, layer_provider=lambda: pv2_layer)
pv2.activate()
pv2.adopt_feature(pv2_layer, pv2_feature, pivot_mode=rot_tool.PIVOT_VERTEX,
                  custom_pivot=(OX - 10.0, OY - 10.0))
pv2.session.submit("35d")
pv2._escape()
check_true("the WKT is byte-identical", wkt_of(pv2_layer, fid_pv2) == wkt_pv2)
check_true("cad_params are identical",
           params_of(pv2_layer, fid_pv2) == params_pv2)
check("no feature was added", pv2_layer.featureCount(), 1)

print("\n== PV3: the default pivot is still the bounding-box centre ==")
pv3_layer = scratch_layer("Polygon", "cad_pivot_default")
pv3_feature = build_feature(pv3_layer, pr.TOOL_RECTANGLE,
                            {"mode": "center", "x": OX, "y": OY,
                             "width_m": 40.0, "height_m": 20.0,
                             "azimuth_deg": 0.0})
pv3 = rot_tool.create(canvas, iface=None, layer_provider=lambda: pv3_layer)
pv3.activate()
default_pivot = pv3.adopt_feature(pv3_layer, pv3_feature)
check("the default pivot is the centre in x", default_pivot[0], OX, 1e-9)
check("...and in y", default_pivot[1], OY, 1e-9)
check_true("vertex is an option, not the default",
           pv3.session.pivot_mode == rot_tool.PIVOT_BBOX)
check_true("...and it is offered with a label",
           rot_tool.PIVOT_VERTEX in rot_tool.PIVOT_LABELS)
check("four pivot modes are now declared", len(rot_tool.PIVOT_LABELS), 4)
pv3.session.submit("20d")
pv3_rotated, _ = pv3.rotate_committed()
check("rotating about the centre still preserves the area",
      pv3_rotated.area(), 800.0, 1e-6)
check("the centre did not move",
      pv3_rotated.boundingBox().center().x(), OX, 1e-9)

print("\n== R2 (1.4.3): the arc joins the registry ==")
check("the registry holds twelve tools", len(tools_pkg.TOOL_REGISTRY), 12)
check("nine tools create geometry", len(plugin_mod.TOOL_GEOMETRY), 9)
check("the dock mounts all twelve", len(plugin_mod.CAD_TOOL_ORDER), 12)
check_true("the registry and the toolbar order still agree",
           set(tools_pkg.TOOL_REGISTRY) == set(plugin_mod.CAD_TOOL_ORDER))
check_true("the arc is registered", "arc" in tools_pkg.TOOL_REGISTRY)
check_true("with an Italian label", tools_pkg.tool_label("arc") == "Arco")
check_true("the arc writes lines, not polygons",
           plugin_mod.TOOL_GEOMETRY["arc"] == "LineString")
check_true("it is not edit-in-place",
           "arc" not in plugin_mod.EDIT_IN_PLACE_TOOLS)
built_arc = tools_pkg.create_tool("arc", canvas, layer_provider=lambda: None)
check_true("it instantiates through the registry",
           isinstance(built_arc, arc_tool.ArcTool))
built_arc.deactivate()
shortcuts = [tools_pkg.tool_shortcut(k) for k in tools_pkg.TOOL_REGISTRY]
check("every shortcut is still distinct", len(set(shortcuts)), len(shortcuts))


# ==========================================================================
# v1.4.6 - three columns visible, the bookkeeping ones hidden but kept
# ==========================================================================
from qgis.core import QgsEditorWidgetSetup                       # noqa: E402


def visible_fields(layer):
    """Names of the columns an operator actually meets in the table."""
    return [field.name() for index, field in enumerate(layer.fields())
            if layer.editorWidgetSetup(index).type() != "Hidden"]


def hidden_fields(layer):
    return [field.name() for index, field in enumerate(layer.fields())
            if layer.editorWidgetSetup(index).type() == "Hidden"]


print("\n== ATV1: a rectangle shows three columns and no more ==")
atv_layer = scratch_layer("Polygon", "cad_visible")
atv_session = rect_tool.RectangleSession(reference=rect_tool.REFERENCE_CENTER)
atv = tb.BaseCadTool(atv_session)
atv_session.set_origin(OX, OY)
atv_session.submit("50")
atv_session.submit("30")
atv_session.submit("0d")
atv.commit(atv_layer, WORK_CRS, atv_layer.crs())
written = newest(atv_layer)

print("        visible: {0}".format(visible_fields(atv_layer)))
print("        hidden : {0}".format(hidden_fields(atv_layer)))
check("cad_id is 1", written[lf.CAD_ID_FIELD], 1)
check("area_ha is 0.15", written[lf.AREA_HA_FIELD], 0.15, 1e-9)
check("perimeter_m is 160.000", written[lf.PERIMETER_FIELD], 160.0, 1e-6)
check("exactly three columns are visible", len(visible_fields(atv_layer)), 3)
check_true("...and they are the three that mean something",
           set(visible_fields(atv_layer))
           == {lf.CAD_ID_FIELD, lf.AREA_HA_FIELD, lf.PERIMETER_FIELD})
check_true("they are in reading order",
           visible_fields(atv_layer) == [lf.CAD_ID_FIELD, lf.AREA_HA_FIELD,
                                         lf.PERIMETER_FIELD])

print("\n== ATV2: the second commit takes the next id ==")
atv_session.set_origin(OX + 200, OY)
atv_session.submit("50")
atv_session.submit("30")
atv_session.submit("0d")
atv.commit(atv_layer, WORK_CRS, atv_layer.crs())
check("cad_id is 2", newest(atv_layer)[lf.CAD_ID_FIELD], 2)
check("still three visible columns", len(visible_fields(atv_layer)), 3)

print("\n== ATV3: a line has no area ==")
atv_line_layer = scratch_layer("LineString", "cad_visible_line")
atv_line_session = line_tool.LineSession()
atv_line = tb.BaseCadTool(atv_line_session)
atv_line_session.set_origin(OX, OY)
atv_line_session.submit("100")
atv_line_session.submit("0d")
atv_line.commit(atv_line_layer, WORK_CRS, atv_line_layer.crs())
line_written = newest(atv_line_layer)
check("area_ha is 0.00", line_written[lf.AREA_HA_FIELD], 0.0)
check("perimeter_m carries the length", line_written[lf.PERIMETER_FIELD],
      100.0, 1e-6)
check("three visible columns on a line layer too",
      len(visible_fields(atv_line_layer)), 3)

print("\n== ATV4: a circle's area comes from measure(), not from pi r^2 ==")
atv_circle_layer = scratch_layer("Polygon", "cad_visible_circle")
atv_circle_session = circle_tool.CircleSession()
atv_circle = tb.BaseCadTool(atv_circle_session)
atv_circle_session.set_origin(OX, OY)
atv_circle_session.submit("10")
atv_circle.commit(atv_circle_layer, WORK_CRS, atv_circle_layer.crs())
circle_written = newest(atv_circle_layer)
measured_m2 = circle_written.geometry().area()
by_hand = math.pi * 100.0
print("        measured {0:.6f} m2 -> {1} ha; pi r^2 would be {2:.6f}".format(
    measured_m2, circle_written[lf.AREA_HA_FIELD], by_hand))
check("area_ha is the measured area over 10 000, to 2 decimals",
      circle_written[lf.AREA_HA_FIELD], round(measured_m2 / 10_000.0, 2), 1e-9)
check_true("the measured area really is below pi r^2",
           measured_m2 < by_hand)

print("\n== ATV5: cad_params is hidden, not deleted ==")
provider_names = [f.name() for f in atv_layer.dataProvider().fields()]
check_true("cad_params is still in the provider",
           pa.PARAMS_FIELD in provider_names)
check_true("...and hidden from the table",
           pa.PARAMS_FIELD in hidden_fields(atv_layer))
check_true("the record still reads off the feature",
           pa.read_record(newest(atv_layer)) is not None)
rebuilt_atv, _ = pr.rebuild(pa.read_record(newest(atv_layer)))
check("...and rebuilds the geometry it describes",
      max_vertex_gap(rebuilt_atv, newest(atv_layer).geometry()), 0.0, 1e-9)
for bookkeeping in ("tool", "width", "height", "radius", "rotation", "area",
                    "perimeter", "created"):
    check_true("{0} is hidden, not dropped".format(bookkeeping),
               bookkeeping in provider_names
               and bookkeeping in hidden_fields(atv_layer))

print("\n== ATV6: moving a feature leaves the three columns alone ==")
atv6_layer = scratch_layer("Polygon", "cad_visible_move")
atv6_feature = build_feature(atv6_layer, pr.TOOL_RECTANGLE,
                             {"mode": "center", "x": OX, "y": OY,
                              "width_m": 50.0, "height_m": 30.0,
                              "azimuth_deg": 0.0})
lf.ensure_cad_fields(atv6_layer)
index = atv6_layer.fields().indexOf(lf.CAD_ID_FIELD)
atv6_layer.dataProvider().changeAttributeValues(
    {atv6_feature.id(): {
        index: 1,
        atv6_layer.fields().indexOf(lf.AREA_HA_FIELD): 0.15,
        atv6_layer.fields().indexOf(lf.PERIMETER_FIELD): 160.0}})
before = [newest(atv6_layer)[name] for name in lf.VISIBLE_CAD_FIELDS]

mv_atv = mv_tool.create(canvas, iface=None,
                        layer_provider=lambda: atv6_layer)
mv_atv.activate()
mv_atv.adopt_feature(atv6_layer, newest(atv6_layer))
mv_atv.session.submit("@10,-4")
moved_atv, _ = mv_atv.move_committed()
after = [newest(atv6_layer)[name] for name in lf.VISIBLE_CAD_FIELDS]
print("        before {0}, after {1}".format(before, after))
check_true("the three columns survive the move unchanged", before == after)
check("the geometry really moved",
      moved_atv.boundingBox().center().x() - OX, 10.0, 1e-9)
check("the area did not change", moved_atv.area(), 1500.0, 1e-6)
check("three columns are still the visible ones",
      len(visible_fields(atv6_layer)), 3)
mv_atv.deactivate()


# --------------------------------------------------------------------------
# CE5 (v1.4.9) - the circle publishes its own diameters, the base class draws
# whatever it is given and knows nothing about circles
# --------------------------------------------------------------------------
print("\n== CE5: two diameters, r = 10, published by the session ==")
ce5 = circle_tool.CircleSession()
ce5.set_origin(OX, OY)
ce5.submit("10")
ce5_parts = [part for part in ce5.axes_points() if part is not None]
check("the circle publishes two parts", len(ce5_parts), 2)
check_true("...each of exactly two points",
           all(np.asarray(part, dtype=float).shape == (2, 2)
               for part in ce5_parts))

ce5_vectors = []
for label, part in zip(("N-S", "E-W"), ce5_parts):
    arr = np.asarray(part, dtype=float)
    vector = arr[1] - arr[0]
    ce5_vectors.append(vector)
    length = float(math.hypot(vector[0], vector[1]))
    print("        {0}: ({1:+.3f},{2:+.3f}) -> ({3:+.3f},{4:+.3f}), "
          "{5:.6f} m".format(label, arr[0][0] - OX, arr[0][1] - OY,
                             arr[1][0] - OX, arr[1][1] - OY, length))
    check("{0} is a full diameter, 2r long".format(label), length, 20.0, 1e-9)
    check("{0} is centred on the circle".format(label),
          float((arr[0][0] + arr[1][0]) / 2.0), OX, 1e-9)
    check("...on both axes", float((arr[0][1] + arr[1][1]) / 2.0), OY, 1e-9)

ce5_dot = float(ce5_vectors[0][0] * ce5_vectors[1][0]
                + ce5_vectors[0][1] * ce5_vectors[1][1])
print("        dot product: {0:.12f}".format(ce5_dot))
check("the two diameters are orthogonal", ce5_dot, 0.0, 1e-9)

ce5_ns = np.asarray(ce5_parts[0], dtype=float)
ce5_ew = np.asarray(ce5_parts[1], dtype=float)
check("the first diameter runs North-South: dE = 0",
      float(ce5_ns[1][0] - ce5_ns[0][0]), 0.0, 1e-9)
check("...and dN = -20 from the North end to the South one",
      float(ce5_ns[1][1] - ce5_ns[0][1]), -20.0, 1e-9)
check("its North end sits at +r", float(ce5_ns[0][1] - OY), 10.0, 1e-9)
check("the second runs East-West: dN = 0",
      float(ce5_ew[1][1] - ce5_ew[0][1]), 0.0, 1e-9)
check("...and dE = -20 from the East end to the West one",
      float(ce5_ew[1][0] - ce5_ew[0][0]), -20.0, 1e-9)
check("its East end sits at +r", float(ce5_ew[0][0] - OX), 10.0, 1e-9)

print("\n-- 100 hovers with the guides on --")
ce5_layer = scratch_layer("Polygon", "cad_circle_guides")
ce5_map = circle_tool.create(canvas, iface=None,
                             layer_provider=lambda: ce5_layer)
ce5_map.activate()
ce5_map.session.set_origin(OX, OY)
ce5_map.session.submit("10")
ce5_baseline = canvas.refresh_calls
for step in range(100):
    ce5_map.canvasMoveEvent(Move(OX + step * 0.3, OY + step * 0.2))
check("no feature added during 100 hovers", ce5_layer.featureCount(), 0)
check("no QgsGeometry built during 100 hovers", ce5_map.geometry_builds, 0)
check("no canvas.refresh() during 100 hovers",
      canvas.refresh_calls - ce5_baseline, 0)

check("the base class drew both parts", ce5_map.update_guides(), 2)
ce5_band = ce5_map._guide_band
print("        band: {0} part(s), {1} vertices".format(
    ce5_band.size(), ce5_band.numberOfVertices()))
check("the guide band carries two parts", ce5_band.size(), 2)
check("...four vertices in all", ce5_band.numberOfVertices(), 4)
check("...two in the first part", ce5_band.partSize(0), 2)
check("...two in the second", ce5_band.partSize(1), 2)
check_true("the guides are shown", ce5_band.isVisible())
ce5_geom = ce5_band.asGeometry()
check_true("one multi-part band, not two bands", ce5_geom.isMultipart())
for index, part in enumerate(ce5_geom.asMultiPolyline()):
    check("band part {0} measures 20 m on the map".format(index),
          float(math.hypot(part[1].x() - part[0].x(),
                           part[1].y() - part[0].y())), 20.0, 1e-6)

ce5_map.clear_band()
check("the guides go away with the preview",
      ce5_band.numberOfVertices(), 0)
check_true("...and are hidden", not ce5_band.isVisible())
ce5_map.deactivate()

print("\n-- a rectangle publishes nothing, so nothing is drawn --")
check_true("RectangleSession does not publish axes",
           not hasattr(rect_tool.RectangleSession, "axes_points"))
ce5_rect_layer = scratch_layer("Polygon", "cad_rect_guides")
ce5_rect = rect_tool.create(canvas, iface=None,
                            layer_provider=lambda: ce5_rect_layer)
ce5_rect.activate()
ce5_rect.session.set_origin(OX, OY)
ce5_rect.session.submit("30")
ce5_rect.session.submit("20")
ce5_rect.session.submit("0d")
ce5_rect.canvasMoveEvent(Move(OX + 5.0, OY + 5.0))
check("the base class draws no guide for it", ce5_rect.update_guides(), 0)
check("...the guide band stays empty",
      ce5_rect._guide_band.numberOfVertices(), 0)
check_true("...and invisible", not ce5_rect._guide_band.isVisible())
check_true("but its own preview is still drawn",
           ce5_rect._band.numberOfVertices() > 0)
ce5_rect.deactivate()


# ==========================================================================
# v1.5.0 (S2) - click-to-polygon and manual parametric input
# ==========================================================================
from geocad_uav.cad.tools import digitize as dg_tool             # noqa: E402
from geocad_uav.cad.tools import manual_input as mi_tool         # noqa: E402


class Click:
    """Duck-typed stand-in for QgsMapMouseEvent in a release handler."""

    def __init__(self, x, y, button=Qt.MouseButton.LeftButton):
        self._point = QgsPointXY(float(x), float(y))
        self._button = button

    def mapPoint(self):                                         # noqa: N802
        return self._point

    def button(self):
        return self._button


RIGHT = Qt.MouseButton.RightButton
DG_CORNERS = ((OX, OY), (OX + 40.0, OY), (OX + 40.0, OY + 30.0),
              (OX, OY + 30.0))


def digitize_tool(layer, corners=DG_CORNERS):
    """A digitizer with the corners already clicked."""
    tool = dg_tool.create(canvas, iface=None, layer_provider=lambda: layer)
    tool.activate()
    for x, y in corners:
        tool.canvasReleaseEvent(Click(x, y))
    return tool


print("\n== DG1: four clicks are four corners, and nothing else ==")
dg_layer = scratch_layer("Polygon", "cad_digitize")
dg1 = digitize_tool(dg_layer)
check("four clicks, four vertices", len(dg1.session.ring()), 4)
check("the layer is still empty while digitizing", dg_layer.featureCount(), 0)
dg1_params = dg1.session.build_params()
print("        params: {0}".format(
    [[round(x - OX, 3), round(y - OY, 3)] for x, y in dg1_params["points"]]))
check("the parameters are the clicks themselves",
      max(abs(a - b) for point, corner in zip(dg1_params["points"], DG_CORNERS)
          for a, b in zip(point, corner)), 0.0, 1e-9)
check_true("the ring is left open: the builder closes it",
           len(dg1_params["points"]) == 4)
dg1_hud = dg1.session.hud_lines()
print("        hud: {0}".format(dg1_hud[-4:]))
check_true("the HUD measures the ring as it stands",
           any("Area 1200.000 m2" in line for line in dg1_hud)
           and any("Perimetro 140.000 m" in line for line in dg1_hud))

print("\n-- the readout is measured about the first vertex --")
DG_IRREGULAR = ((OX, OY), (OX + 37.4, OY + 2.9), (OX + 51.2, OY + 28.6),
                (OX + 22.7, OY + 44.1), (OX - 9.3, OY + 31.5),
                (OX - 12.6, OY + 11.8), (OX - 8.4, OY + 17.9))
dg1b_layer = scratch_layer("Polygon", "cad_digitize_irregular")
dg1b = digitize_tool(dg1b_layer, corners=DG_IRREGULAR)
dg1b_geos, _ = pr.build(pr.TOOL_DIGITIZED_POLYGON, dg1b.session.build_params(),
                        WORK_CRS.authid())
dg1b_local = ge.polygon_area(dg1b.session.local_ring())
dg1b_absolute = ge.polygon_area(dg1b.session.ring())
print("        GEOS {0:.9f}, about the first vertex {1:.9f}, "
      "about the CRS origin {2:.9f}".format(
          dg1b_geos.area(), dg1b_local, dg1b_absolute))
# 1e-6, not 1e-9: GEOS itself lands 7e-9 away from the exact 1907.89 on
# these coordinates, so a tighter bound would be measuring the noise.
check("the HUD area is the geometry's area", dg1b_local, dg1b_geos.area(),
      1e-6)
print("        error about the first vertex {0:.3e} m2, about the CRS "
      "origin {1:.3e} m2".format(abs(dg1b_local - dg1b_geos.area()),
                                 abs(dg1b_absolute - dg1b_geos.area())))
check_true("...which the same shoelace on raw UTM coordinates is not",
           abs(dg1b_absolute - dg1b_geos.area()) > 1e-4)
dg1b.session.cancel()
dg1b.deactivate()

print("\n== DG2: the closing click lands on the first vertex ==")
dg_tolerance = dg1.close_tolerance()
print("        {0:.3f} m at {1:.4f} map units per pixel, {2:.0f} px".format(
    dg_tolerance, canvas.mapUnitsPerPixel(), dg1.close_pixels))
check_true("the closing tolerance is a real distance", dg_tolerance > 0.0)
check_true("a far click does not close the ring",
           not dg1.session.closes_on_first(OX + 40.0, OY + 30.0, dg_tolerance))
check_true("a click on the first vertex does",
           dg1.session.closes_on_first(OX + 0.2, OY + 0.2, dg_tolerance))
dg1.canvasReleaseEvent(Click(OX + 0.2, OY + 0.2))
check("the closing click writes exactly one feature",
      dg_layer.featureCount(), 1)
dg1_written = newest(dg_layer)
dg1_geom = dg1_written.geometry()
print("        area {0:.6f} m2, perimeter {1:.6f} m, {2} vertices".format(
    dg1_geom.area(), dg1_geom.length(), len(vertices(dg1_geom))))
check("the polygon is 40 x 30", dg1_geom.area(), 1200.0, 1e-6)
check("...with a 140 m perimeter", dg1_geom.length(), 140.0, 1e-9)
check("the ring is closed once, not twice", len(vertices(dg1_geom)), 5)
check_true("it really is a polygon",
           dg1_geom.type() == QgsWkbTypes.PolygonGeometry)
check("the session is empty again", len(dg1.session.vertices), 0)
dg1.deactivate()

print("\n== DG3: the record rebuilds the polygon that was clicked ==")
dg1_record = pa.read_record(dg1_written)
check_true("the record names the digitized polygon",
           dg1_record.tool == pr.TOOL_DIGITIZED_POLYGON)
dg1_rebuilt, _ = pr.rebuild(dg1_record)
check("rebuild lands on the same vertices",
      max_vertex_gap(dg1_rebuilt, dg1_geom), 0.0, 1e-9)
check("...and the same area", dg1_rebuilt.area(), 1200.0, 1e-6)
check("the measured area travelled with the record",
      dg1_record.params["measured_area_m2"], 1200.0, 1e-6)
check("area_ha is the measured area over 10 000",
      dg1_written[lf.AREA_HA_FIELD], 0.12, 1e-9)
check("perimeter_m too", dg1_written[lf.PERIMETER_FIELD], 140.0, 1e-6)
check("three visible columns, as everywhere else",
      len(visible_fields(dg_layer)), 3)

print("\n== DG4: two vertices are a line drawn twice, not a polygon ==")
dg4_layer = scratch_layer("Polygon", "cad_digitize_short")
dg4 = digitize_tool(dg4_layer, corners=DG_CORNERS[:2])
check_true("two vertices are not ready", not dg4.session.is_ready)
check_raises("confirm refuses two vertices", InvalidInputError,
             dg4.session.confirm)
check_raises("...and so does build_params", InvalidInputError,
             dg4.session.build_params)
check_raises("the primitive refuses them too", InvalidInputError,
             pr.build, pr.TOOL_DIGITIZED_POLYGON,
             {"points": [[OX, OY], [OX + 10.0, OY]]}, WORK_CRS.authid())
dg4.canvasReleaseEvent(Click(OX + 0.1, OY + 0.1))
check("a closing click on an unfinished ring writes nothing",
      dg4_layer.featureCount(), 0)
check_raises("a ragged points array is refused as well", InvalidInputError,
             pr.build, pr.TOOL_DIGITIZED_POLYGON,
             {"points": [[OX, OY, 0.0]]}, WORK_CRS.authid())
dg4.session.cancel()
check("Escape leaves the layer empty", dg4_layer.featureCount(), 0)
dg4.deactivate()

print("\n== DG5: the right button takes a vertex back, Ctrl+Y returns it ==")
dg5_layer = scratch_layer("Polygon", "cad_digitize_undo")
dg5 = digitize_tool(dg5_layer)
dg5_last = tuple(dg5.session.vertices[-1])
dg5.canvasReleaseEvent(Click(0.0, 0.0, RIGHT))
check("the right button drops one vertex", len(dg5.session.ring()), 3)
check("...and writes nothing", dg5_layer.featureCount(), 0)
check_true("the undone vertex is remembered", len(dg5._undone) == 1)
dg5.redo_vertex()
check("redo puts it back", len(dg5.session.ring()), 4)
check("...at the same place",
      max(abs(a - b) for a, b in zip(dg5.session.vertices[-1], dg5_last)),
      0.0, 1e-9)
for _ in range(4):
    dg5.canvasReleaseEvent(Click(0.0, 0.0, RIGHT))
check("undoing past the first vertex empties the construction",
      len(dg5.session.vertices), 0)
dg5.canvasReleaseEvent(Click(0.0, 0.0, RIGHT))
check("...and one more right click is simply Escape",
      dg5_layer.featureCount(), 0)
check_true("the session is idle", dg5.session.state == tb.ToolState.IDLE)
dg5.deactivate()

print("\n-- 100 hovers on the digitizer --")
dg6_layer = scratch_layer("Polygon", "cad_digitize_hover")
dg6 = digitize_tool(dg6_layer, corners=DG_CORNERS[:3])
dg6_baseline = canvas.refresh_calls
for step in range(100):
    dg6.canvasMoveEvent(Move(OX + step * 0.3, OY + step * 0.2))
check("no feature added during 100 hovers", dg6_layer.featureCount(), 0)
check("no QgsGeometry built during 100 hovers", dg6.geometry_builds, 0)
check("no canvas.refresh() during 100 hovers",
      canvas.refresh_calls - dg6_baseline, 0)
check_true("the preview closes the ring for the eye",
           len(dg6.session.preview_points()) == 5)
dg6.session.cancel()
check("Escape leaves the layer empty", dg6_layer.featureCount(), 0)
dg6.deactivate()

print("\n== DG7: the closing tolerance is pixels, so it follows the zoom ==")
dg7_extent = canvas.extent()
dg7 = dg_tool.create(canvas, iface=None, layer_provider=lambda: dg6_layer)
dg7.activate()
canvas.setExtent(QgsRectangle(OX - 200, OY - 200, OX + 200, OY + 200))
wide = dg7.close_tolerance()
canvas.setExtent(QgsRectangle(OX - 20, OY - 20, OX + 20, OY + 20))
close = dg7.close_tolerance()
print("        {0:.4f} m zoomed out, {1:.4f} m zoomed in".format(wide, close))
check_true("both tolerances are real distances", wide > 0.0 and close > 0.0)
check("ten times the scale, ten times the tolerance", wide / close, 10.0, 1e-9)
check("...and it is the pixel count that sets it",
      wide, canvas.mapUnitsPerPixel() * 10.0 * dg7.close_pixels, 1e-9)
canvas.setExtent(dg7_extent)
dg7.deactivate()

# --------------------------------------------------------------------------
# MI - manual parametric input
# --------------------------------------------------------------------------
print("\n== MI1: a circle typed as an area is the Circle tool's circle ==")
mi_layer = scratch_layer("Polygon", "cad_manual")


def mi_answer(shape, choice, **values):
    """A dialog that never opens: it just fills the session in."""
    def factory(session, _parent):
        session.set_shape(shape)
        if choice:
            session.set_choice(choice)
        for name, value in values.items():
            session.set_field(name, value)
        return True
    return factory


mi1 = mi_tool.create(canvas, iface=None, layer_provider=lambda: mi_layer,
                     dialog_factory=mi_answer(pr.TOOL_CIRCLE, "area_m2",
                                              area_m2=10_000.0))
mi1.activate()
mi1.canvasReleaseEvent(Click(OX, OY))
check("one click and one dialog make one feature", mi_layer.featureCount(), 1)
mi1_geom = newest(mi_layer).geometry()

mi1_radius = ge.circle_radius_from_area(10_000.0)
mi1_reference = circle_tool.CircleSession()
mi1_reference.set_origin(OX, OY)
mi1_reference.submit("{0:.12f}".format(mi1_radius))
mi1_ring, _ = pr.build(pr.TOOL_CIRCLE, mi1_reference.build_params(),
                       WORK_CRS.authid())
print("        manual {0:.6f} m2, Cerchio {1:.6f} m2".format(
    mi1_geom.area(), mi1_ring.area()))
check("the two circles are the same circle",
      max_vertex_gap(mi1_geom, mi1_ring), 0.0, 1e-9)
check_true("and both are the inscribed polygon, not pi r squared",
           mi1_geom.area() < 10_000.0)
mi1_record = pa.read_record(newest(mi_layer))
check_true("the record says circle, because a circle is what was built",
           mi1_record.tool == pr.TOOL_CIRCLE)
check("...and it rebuilds unchanged",
      max_vertex_gap(pr.rebuild(mi1_record)[0], mi1_geom), 0.0, 1e-9)
mi1.deactivate()

print("\n== MI2: a square typed as a diagonal ==")
mi2_layer = scratch_layer("Polygon", "cad_manual_square")
mi2 = mi_tool.create(canvas, iface=None, layer_provider=lambda: mi2_layer,
                     dialog_factory=mi_answer(pr.TOOL_SQUARE, "diagonal_m",
                                              diagonal_m=20.0,
                                              azimuth_deg=0.0))
mi2.activate()
mi2.canvasReleaseEvent(Click(OX, OY))
mi2_geom = newest(mi2_layer).geometry()
print("        area {0:.6f} m2, expected {1:.6f}".format(
    mi2_geom.area(), 20.0 * 20.0 / 2.0))
check("a 20 m diagonal is a 200 m2 square", mi2_geom.area(), 200.0, 1e-6)
check("...with four corners", len(vertices(mi2_geom)), 5)
mi2.deactivate()

print("\n== MI3: the shape list only names primitives that exist ==")
for spec in mi_tool.SHAPES:
    check_true("{0} is a real primitive".format(spec.label),
               spec.tool in pr.TOOL_LABELS)
    probe = mi_tool.ManualInputSession(shape=spec.tool)
    probe.set_origin(OX, OY)
    check_true("{0} has a default for every field".format(spec.label),
               probe.is_ready)
    ring = probe.preview_points()
    check_true("{0} previews a closed ring".format(spec.label),
               ring is not None and len(ring) >= 3)
    built, _ = pr.build(spec.tool, probe.build_params(), WORK_CRS.authid())
    # Measured about the first vertex: the shoelace loses a millimetre per
    # product at UTM magnitudes, which on a 10 m circle is 1e-2 m2. GEOS
    # centres internally, the pure-numpy helper does not.
    local = np.asarray(ring, dtype=float) - np.asarray(ring, dtype=float)[0]
    check("{0} builds the same ring it previews".format(spec.label),
          built.area(), ge.polygon_area(local), 1e-6)
    for field in spec.choices:
        probe.set_choice(field.name)
        probe.load_defaults()
        check_true("{0} can also be sized by its {1}".format(
            spec.label, field.label.lower()), probe.is_ready)

check_raises("an unknown shape is refused", InvalidInputError,
             mi_tool.shape_spec, "ellipse")
mi3 = mi_tool.ManualInputSession(shape=pr.TOOL_CIRCLE)
check_raises("...an unknown measure too", InvalidInputError,
             mi3.set_choice, "side_m")
check_raises("...and an unknown field", InvalidInputError,
             mi3.set_field, "n_sides", 6)
check_raises("a session with no insertion point cannot build",
             InvalidInputError, mi3.build_params)
check_true("...and previews nothing", mi3.preview_points() is None)

print("\n== MI4: cancelling the dialog writes nothing ==")
mi4_layer = scratch_layer("Polygon", "cad_manual_cancel")
mi4 = mi_tool.create(canvas, iface=None, layer_provider=lambda: mi4_layer,
                     dialog_factory=lambda session, parent: False)
mi4.activate()
mi4.canvasReleaseEvent(Click(OX, OY))
check("Annulla leaves the layer empty", mi4_layer.featureCount(), 0)
check_true("...and the session idle",
           mi4.session.state == tb.ToolState.IDLE)
check("no geometry was built", mi4.geometry_builds, 0)

print("\n-- 100 hovers on the manual input --")
mi4_baseline = canvas.refresh_calls
for step in range(100):
    mi4.canvasMoveEvent(Move(OX + step * 0.4, OY + step * 0.4))
check("no feature added during 100 hovers", mi4_layer.featureCount(), 0)
check("no QgsGeometry built during 100 hovers", mi4.geometry_builds, 0)
check("no canvas.refresh() during 100 hovers",
      canvas.refresh_calls - mi4_baseline, 0)
mi4.deactivate()

print("\n== MI5: the dialog shows the fields the shape needs ==")
mi5_session = mi_tool.ManualInputSession(shape=pr.TOOL_RECTANGLE)
mi5_dialog = mi_tool.ManualInputDialog(mi5_session, None)
print("        rettangolo: {0}".format(list(mi5_dialog._editors)))
check_true("a rectangle asks for base, height and azimuth",
           list(mi5_dialog._editors) == ["width_m", "height_m",
                                         "azimuth_deg"])
check("...and offers no alternative measure", mi5_dialog.choice_box.count(), 0)
mi5_dialog.shape_box.setCurrentIndex(
    mi_tool.SHAPE_KEYS.index(pr.TOOL_POLYGON))
print("        poligono regolare: {0}, misure {1}".format(
    list(mi5_dialog._editors),
    [mi5_dialog.choice_box.itemData(i)
     for i in range(mi5_dialog.choice_box.count())]))
check_true("switching shape switches the session too",
           mi5_session.spec.tool == pr.TOOL_POLYGON)
check_true("a regular polygon asks for a side count",
           "n_sides" in mi5_dialog._editors)
check("...and offers its four ways of being sized",
      mi5_dialog.choice_box.count(), 4)
mi5_dialog._editors["n_sides"].setValue(8)
mi5_dialog.harvest()
check("what is on screen is what the session gets",
      mi5_session.values["n_sides"], 8)
mi5_session.set_origin(OX, OY)
mi5_octagon, _ = pr.build(pr.TOOL_POLYGON, mi5_session.build_params(),
                          WORK_CRS.authid())
check("an eight-sided polygon has eight sides",
      len(vertices(mi5_octagon)), 9)
mi5_dialog.dialog.deleteLater()

print("\n== R2 (1.5.0): both tools are registered like the others ==")
for key, label, cls in (("digitize", "Poligono digitalizzato",
                         dg_tool.DigitizeSession),
                        ("manual_input", "Inserimento manuale",
                         mi_tool.ManualInputSession)):
    check_true("{0} is in the registry".format(key),
               key in tools_pkg.TOOL_REGISTRY)
    check_true("{0} keeps its Italian label".format(key),
               tools_pkg.tool_label(key) == label)
    check_true("{0} has a shortcut".format(key),
               bool(tools_pkg.tool_shortcut(key)))
    made = tools_pkg.create_tool(key, canvas, layer_provider=lambda: None)
    check_true("{0} instantiates through the registry".format(key),
               isinstance(made, tb.CadMapTool)
               and isinstance(made.session, cls))
    check_true("{0} writes polygons".format(key),
               made.session.geometry_type == "Polygon")
    made.deactivate()
    check_true("{0} creates geometry, so it has a layer type".format(key),
               plugin_mod.TOOL_GEOMETRY.get(key) == "Polygon")
    check_true("{0} is not edit-in-place".format(key),
               key not in plugin_mod.EDIT_IN_PLACE_TOOLS)
    check_true("{0} is on the dock toolbar order".format(key),
               key in plugin_mod.CAD_TOOL_ORDER)
check_true("the registry and the toolbar order still agree",
           set(tools_pkg.TOOL_REGISTRY) == set(plugin_mod.CAD_TOOL_ORDER))
r2_shortcuts = [tools_pkg.tool_shortcut(k) for k in tools_pkg.TOOL_REGISTRY]
check("every shortcut is still distinct", len(set(r2_shortcuts)),
      len(r2_shortcuts))


print("\n" + "=" * 80)
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
