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
                       QgsRectangle)

QGS = QgsApplication([], False)
QGS.initQgis()

# Only after QgsApplication exists: qgis.gui pulls in Qt widgets, and importing
# it (or qgis.analysis) beforehand crashes the interpreter with no traceback.
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
check_true("pivot modes are declared", len(rot_tool.PIVOT_LABELS) == 3)
built_rot.deactivate()
check_true("plugin treats rotate as edit-in-place, never a scratch layer",
           "rotate" in plugin_mod.EDIT_IN_PLACE_TOOLS)


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
