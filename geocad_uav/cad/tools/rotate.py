"""
Rotate an existing feature with a handle, the way an image rotates in Word.

Select a feature, grab the green handle above it, drag: the outline follows the
cursor around a pivot, the HUD shows the angle, and Shift snaps to 15 degrees.
The same rotation can be typed into the dock's angle field instead.

This tool does not construct geometry of its own:

* the numeric rotation is ``core.transform2d.rotate`` (pure numpy, clockwise);
* the committed geometry is ``QgsGeometry.rotate``, which keeps holes, parts
  and Z that a vertex-by-vertex rewrite would lose.

Both were verified to use the *same* clockwise convention on QGIS 3.40 and
4.0: (100, 0) rotated +90 becomes (0, -100) in each. Note that
``cad.modifiers`` has no ``rotate``; rotation has always lived in
``core.transform2d``.

The parametric record is carried through the rotation rather than invalidated:
the anchor point is rotated about the pivot and the stored azimuth gains the
delta, which reproduces the rotated shape exactly for every primitive the
engine builds. The result is then *checked* against the geometric rotation, and
only written back if it matches; otherwise the record is marked broken and the
rotated geometry is still committed. A record is never left describing a shape
it does not describe.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ...core import transform2d as t2d
from ...core.constants import GEOM_EPS_M
from ...core.errors import GeoCadError, InvalidInputError, LayerError
from ...core.planar import azimuth_of
from .. import dynamic_input as di
from .. import parametric as pa
from .. import primitives as pr
from .base import CadMapTool, CadToolSession, ConstraintSlot, ToolState

#: Angle increments when Shift is held.
SNAP_STEP_DEG = 15.0

#: How far above the top of the bounding box the handle sits, as a fraction of
#: the box height. Matches the feel of an image handle in a word processor.
HANDLE_OFFSET_RATIO = 0.18
HANDLE_MIN_OFFSET_M = 1.0

# Pivot choices offered in the dock.
PIVOT_BBOX = "bbox"          # centre of the bounding box (default)
PIVOT_PARAMS = "params"      # the shape's own anchor, when cad_params are intact
PIVOT_CUSTOM = "custom"      # a point clicked on the map
PIVOT_VERTEX = "vertex"      # the feature's own vertex nearest the click

PIVOT_LABELS = {PIVOT_VERTEX: "Vertice della geometria",
                PIVOT_BBOX: "Centro del rettangolo di selezione",
                PIVOT_PARAMS: "Centro dei parametri CAD",
                PIVOT_CUSTOM: "Punto indicato sulla mappa"}


class RotateSession(CadToolSession):
    """Pivot, reference outline and one angle. No Qt, no QGIS."""

    tool_id = ""                       # rotates an existing shape, builds none
    geometry_type = "Polygon"
    title = "Ruota"
    accepts_second_click = False

    def __init__(self, length_unit: str = "m", angle_unit: str = "deg"):
        #: (N, 2) outline captured when the feature was picked, unrotated.
        self.outline = None
        #: Bearing from pivot to cursor when the drag started.
        self.grab_azimuth = None
        #: True while Shift is held.
        self.snap_to_step = False
        self.pivot_mode = PIVOT_BBOX
        super().__init__(length_unit, angle_unit)

    def slots(self):
        return [ConstraintSlot("angle_deg", di.KIND_ANGLE, "Angolo")]

    # -- lifecycle ---------------------------------------------------------

    def reset(self):
        super().reset()
        self.outline = None
        self.grab_azimuth = None
        self.snap_to_step = False

    def capture(self, outline, pivot) -> str:
        """Adopt a feature's outline and pivot. Moves out of IDLE."""
        arr = np.asarray(outline, dtype=float)
        if arr.ndim != 2 or arr.shape[0] < 2:
            raise InvalidInputError(
                "outline must be an (N, 2) array with at least 2 points",
                user_message="La geometria selezionata non e' ruotabile.")
        self.outline = arr[:, :2]
        self.origin = (float(pivot[0]), float(pivot[1]))
        self.set_value("angle_deg", None)
        self.grab_azimuth = None
        self.state = ToolState.PICK_ORIGIN
        self.message = "Trascina la maniglia, oppure digita un angolo"
        return self.state

    @property
    def has_feature(self) -> bool:
        return self.outline is not None and self.origin is not None

    @property
    def is_ready(self) -> bool:
        return self.has_feature and self.value("angle_deg") is not None

    def confirm(self) -> str:
        if not self.is_ready:
            raise InvalidInputError(
                "rotation without an angle",
                user_message="Indica un angolo di rotazione.")
        self.state = ToolState.COMMIT
        return self.state

    # -- dragging ----------------------------------------------------------

    def begin_drag(self, x: float, y: float) -> None:
        """Remember the bearing the drag started from."""
        if not self.has_feature:
            return
        self.grab_azimuth = self._bearing(x, y)

    def hover(self, x: float, y: float) -> None:
        """Track the cursor and, while dragging, update the live angle.

        Pure bookkeeping and one atan2: no geometry is built here, which is
        what keeps a mouse move free (see the R4 assertion).
        """
        super().hover(x, y)
        if not self.has_feature or self.grab_azimuth is None:
            return
        current = self._bearing(x, y)
        if current is None:
            return
        angle = (current - self.grab_azimuth) % 360.0
        if self.snap_to_step:
            angle = round(angle / SNAP_STEP_DEG) * SNAP_STEP_DEG % 360.0
        self.set_value("angle_deg", angle)
        self.state = ToolState.PREVIEW

    def end_drag(self) -> None:
        self.grab_azimuth = None

    def _bearing(self, x: float, y: float) -> Optional[float]:
        dx = float(x) - self.origin[0]
        dy = float(y) - self.origin[1]
        if math.hypot(dx, dy) < GEOM_EPS_M:
            return None
        return azimuth_of(dx, dy)

    # -- typed angle -------------------------------------------------------

    def submit(self, text: str) -> str:
        """Only an angle makes sense here; a length would be meaningless."""
        if not self.has_feature:
            raise InvalidInputError(
                "no feature selected",
                user_message="Seleziona prima una geometria da ruotare.")
        token = di.parse(text, self.length_unit, self.angle_unit)
        if token.kind != di.KIND_ANGLE:
            raise InvalidInputError(
                "expected an angle, got {0}".format(token.kind),
                user_message="Serve un angolo, per esempio 20d.")
        self.set_value("angle_deg", token.angle_deg % 360.0)
        self.state = ToolState.PREVIEW
        self.message = "Invio per confermare"
        return self.state

    # -- preview -----------------------------------------------------------

    @property
    def angle(self) -> float:
        value = self.value("angle_deg")
        return 0.0 if value is None else float(value)

    def preview_points(self) -> Optional[np.ndarray]:
        """The captured outline rotated by the current angle.

        Pure numpy on a fixed point array, so a drag costs one matrix multiply
        and never touches GEOS.
        """
        if not self.has_feature:
            return None
        return t2d.rotate(self.outline, self.angle, self.origin)

    def handle_point(self) -> Optional[np.ndarray]:
        """Where the rotation handle sits, for the current angle."""
        points = self.preview_points()
        if points is None:
            return None
        top = float(points[:, 1].max())
        centre_x = 0.5 * (float(points[:, 0].min()) + float(points[:, 0].max()))
        height = float(points[:, 1].max() - points[:, 1].min())
        offset = max(height * HANDLE_OFFSET_RATIO, HANDLE_MIN_OFFSET_M)
        return np.array([centre_x, top + offset])

    # -- readout -----------------------------------------------------------

    def hud_lines(self):
        lines = [
            "{0} - {1}".format(self.title, self.state),
            "Perno {0}".format(PIVOT_LABELS.get(self.pivot_mode,
                                                self.pivot_mode)),
        ]
        if self.origin is not None:
            lines.append("Perno X {0:.3f}  Y {1:.3f}".format(*self.origin))
        if self.value("angle_deg") is not None:
            lines.append("Rotazione {0:.4f} deg".format(self.angle))
            lines.append("Delta {0:+.4f} deg".format(
                ((self.angle + 180.0) % 360.0) - 180.0))
        if self.snap_to_step:
            lines.append("Shift: passo {0:g} deg".format(SNAP_STEP_DEG))
        if self.message:
            lines.append(self.message)
        return lines


# --------------------------------------------------------------------------
# Parametric record follow-through
# --------------------------------------------------------------------------

def rotated_params(record, delta_deg: float, pivot) -> dict:
    """The construction parameters of ``record``, rotated about ``pivot``.

    Every primitive in the engine is built as "an anchor point plus a frame at
    an azimuth", so rotating the anchor and adding the delta to the azimuth
    reproduces the rotated shape for all of them -- rectangle (corner, centre
    or opposite corners), square, circle, ellipse, regular polygon and the
    polyline's per-segment bearings alike.
    """
    params = dict(pr.input_params(record))
    for x_key, y_key in (("x", "y"), ("x2", "y2"), ("x3", "y3")):
        if x_key in params and y_key in params:
            moved = t2d.rotate(
                np.array([[float(params[x_key]), float(params[y_key])]]),
                delta_deg, pivot)[0]
            params[x_key] = float(moved[0])
            params[y_key] = float(moved[1])
    if "azimuth_deg" in params:
        params["azimuth_deg"] = (float(params["azimuth_deg"])
                                 + delta_deg) % 360.0
    if params.get("segments"):
        params["segments"] = [[float(length), (float(azimuth) + delta_deg)
                               % 360.0]
                              for length, azimuth in params["segments"]]
    return params


def _max_vertex_gap(geom_a, geom_b) -> float:
    a = np.array([[v.x(), v.y()] for v in geom_a.vertices()], dtype=float)
    b = np.array([[v.x(), v.y()] for v in geom_b.vertices()], dtype=float)
    if a.shape != b.shape or a.size == 0:
        return math.inf
    return float(np.max(np.hypot(*(a - b).T)))


# --------------------------------------------------------------------------
# Map tool
# --------------------------------------------------------------------------

class RotateHandleTool(CadMapTool):
    """Word-style rotation of a selected feature."""

    def __init__(self, canvas, session=None, iface=None, layer_provider=None):
        super().__init__(canvas, session or RotateSession(), iface=iface,
                         layer_provider=layer_provider)
        self.feature_id = None
        self.source_layer = None
        self.source_geometry = None      # work CRS, unrotated
        self._dragging = False

    # -- picking -----------------------------------------------------------

    def active_layer(self):
        """The layer holding the feature to rotate."""
        if self.layer_provider is not None:
            layer = self.layer_provider()
            if layer is not None:
                return layer
        if self.iface is not None:
            try:
                return self.iface.activeLayer()
            except AttributeError:
                return None
        return None

    def adopt_feature(self, layer, feature, pivot_mode=PIVOT_BBOX,
                      custom_pivot=None):
        """Capture a feature for rotation. Returns the pivot used."""
        from qgis.core import QgsGeometry                       # noqa: PLC0415

        if layer is None or feature is None:
            raise LayerError(
                "no feature to rotate",
                user_message="Seleziona una geometria da ruotare.",
                hint="Attiva un layer vettoriale e seleziona una feature.")
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            raise LayerError(
                "empty geometry",
                user_message="La geometria selezionata e' vuota.")

        self.source_layer = layer
        self.feature_id = feature.id()
        self.source_geometry = QgsGeometry(geometry)
        self.session.pivot_mode = pivot_mode

        pivot = self._resolve_pivot(feature, pivot_mode, custom_pivot)
        self.session.capture(self._outline(geometry), pivot)
        return pivot

    def _outline(self, geometry):
        """Exterior outline used for the drag preview.

        The preview traces the first exterior ring; the commit rotates the
        whole geometry, holes and parts included. Keeping the preview to a
        plain point array is what lets a mouse move avoid GEOS entirely.
        """
        try:
            if geometry.isMultipart():
                parts = geometry.asGeometryCollection()
                geometry = parts[0] if parts else geometry
            polygon = geometry.asPolygon()
            if polygon:
                ring = polygon[0]
                return np.array([[p.x(), p.y()] for p in ring], dtype=float)
            line = geometry.asPolyline()
            if line:
                return np.array([[p.x(), p.y()] for p in line], dtype=float)
        except (TypeError, ValueError):
            pass
        return np.array([[v.x(), v.y()] for v in geometry.vertices()],
                        dtype=float)

    def _resolve_pivot(self, feature, pivot_mode, custom_pivot):
        if pivot_mode == PIVOT_CUSTOM and custom_pivot is not None:
            return (float(custom_pivot[0]), float(custom_pivot[1]))
        if pivot_mode == PIVOT_VERTEX:
            vertex = self._nearest_vertex(feature, custom_pivot)
            if vertex is not None:
                return vertex
        if pivot_mode == PIVOT_PARAMS:
            anchor = self._params_anchor(feature)
            if anchor is not None:
                return anchor
        box = feature.geometry().boundingBox()
        centre = box.center()
        return (centre.x(), centre.y())

    def _nearest_vertex(self, feature, near=None):
        """The feature's own vertex closest to ``near``, or the first one.

        The pivot is a vertex of the geometry, so it lands exactly on a
        corner rather than near it: QgsGeometry.closestVertex is the same
        search QGIS's own vertex tool uses, and the returned point is the
        stored coordinate, not a snapped approximation of it.
        """
        from qgis.core import QgsPointXY                        # noqa: PLC0415

        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            return None
        if near is None:
            for vertex in geometry.vertices():
                return (float(vertex.x()), float(vertex.y()))
            return None
        point, _index, _prev, _next, _dist = geometry.closestVertex(
            QgsPointXY(float(near[0]), float(near[1])))
        if point is None or point.isEmpty():
            return None
        return (float(point.x()), float(point.y()))

    def _params_anchor(self, feature):
        try:
            record = pa.read_record(feature, self.source_layer)
        except GeoCadError:
            return None
        if record is None:
            return None
        params = record.params
        if "x" in params and "y" in params:
            return (float(params["x"]), float(params["y"]))
        return None

    # -- events ------------------------------------------------------------

    def canvasMoveEvent(self, event):                           # noqa: N802
        from qgis.PyQt.QtCore import Qt                         # noqa: PLC0415

        if self._work_decision is None or not self.session.has_feature:
            return
        self.session.snap_to_step = bool(
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        x, y = self._map_to_work(self.picked_point(event))
        self.session.hover(x, y)
        self.update_band(self.canvas(), self._to_canvas)
        self._paint_hud()

    def canvasReleaseEvent(self, event):                        # noqa: N802
        from qgis.PyQt.QtCore import Qt                         # noqa: PLC0415

        if event.button() == Qt.MouseButton.RightButton:
            self._escape()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._work_decision is None:
            self._warn(self._blocked_reason or
                       "Sistema di riferimento non utilizzabile.")
            return

        x, y = self._map_to_work(self.picked_point(event))
        try:
            if not self.session.has_feature:
                self._pick_from_selection()
            elif self._dragging:
                self.session.end_drag()
                self._dragging = False
                self._do_commit()
                return
            else:
                self.session.begin_drag(x, y)
                self._dragging = True
        except GeoCadError as exc:
            self._warn(exc.formatted())
            return
        self.update_band(self.canvas(), self._to_canvas)
        self._paint_hud()

    def _pick_from_selection(self):
        layer = self.active_layer()
        if layer is None:
            raise LayerError(
                "no active layer",
                user_message="Nessun layer attivo.",
                hint="Scegli un layer vettoriale nel pannello o nella legenda.")
        features = list(layer.selectedFeatures())
        if not features:
            raise LayerError(
                "no selected feature",
                user_message="Nessuna geometria selezionata.",
                hint="Seleziona la feature da ruotare, poi clicca sulla mappa.")
        self.adopt_feature(layer, features[0], self.session.pivot_mode)

    def _escape(self):
        self.session.reset()
        self.feature_id = None
        self.source_layer = None
        self.source_geometry = None
        self._dragging = False
        self._typed = ""
        self.clear_band()
        self._paint_hud()

    # -- commit ------------------------------------------------------------

    def _do_commit(self):
        try:
            self.rotate_committed()
        except GeoCadError as exc:
            self._warn(exc.formatted())
            self.session.state = ToolState.PREVIEW
            return
        self._typed = ""
        self.clear_band()
        self._paint_hud()

    def rotate_committed(self):
        """Write the rotation. Returns ``(geometry, params_kept)``."""
        from qgis.core import QgsGeometry, QgsPointXY           # noqa: PLC0415

        from ...core import undo

        if self.source_layer is None or self.source_geometry is None:
            raise LayerError(
                "nothing captured",
                user_message="Nessuna geometria da ruotare.")
        self.session.confirm()
        delta = self.session.angle
        pivot = self.session.origin

        rotated = QgsGeometry(self.source_geometry)
        rotated.rotate(delta, QgsPointXY(pivot[0], pivot[1]))
        self.geometry_builds += 1

        attributes, params_kept = self._followed_params(rotated, delta, pivot)

        layer = self.source_layer
        fields = layer.fields()
        with undo.edit_command(layer, "GeoCad: Ruota"):
            if not layer.changeGeometry(self.feature_id, rotated):
                raise LayerError(
                    "changeGeometry failed on {0}".format(layer.name()),
                    user_message="Aggiornamento della geometria non riuscito.")
            for name, value in (attributes or {}).items():
                index = fields.indexOf(name)
                if index >= 0:
                    layer.changeAttributeValue(self.feature_id, index, value)

        self.source_geometry = QgsGeometry(rotated)
        self.session.reset()
        self.session.capture(self._outline(rotated), pivot)
        return rotated, params_kept

    def _followed_params(self, rotated, delta, pivot):
        """Carry cad_params through the rotation, but only if they still fit.

        The rotated parameters are rebuilt and compared against the geometric
        rotation. A mismatch means the record can no longer describe the shape,
        so it is marked broken instead of being written as if it could.
        """
        feature = None
        try:
            feature = self.source_layer.getFeature(self.feature_id)
        except (AttributeError, RuntimeError):
            return None, False
        try:
            record = pa.read_record(feature, self.source_layer)
        except GeoCadError:
            record = None
        if record is None:
            return None, False

        record = pa.check_integrity(feature, record)
        if record.broken:
            self._warn(
                "Parametri CAD non validi (geometria modificata a mano): "
                "la rotazione e' stata applicata solo alla geometria.")
            return None, False

        try:
            params = rotated_params(record, delta, pivot)
            rebuilt, updated = pr.build(record.tool, params, record.crs_authid)
        except GeoCadError:
            rebuilt = None
            updated = None

        if rebuilt is None or _max_vertex_gap(rebuilt, rotated) > GEOM_EPS_M:
            record.broken = True
            self._warn(
                "I parametri CAD non descrivono piu' la forma ruotata: "
                "sono stati marcati come non validi.")
            pa.store_record(self.source_layer, self.feature_id, record)
            return {pa.PARAMS_FIELD: record.to_json()}, False

        pa.store_record(self.source_layer, self.feature_id, updated)
        return pa.record_to_attributes(updated), True


def create(canvas, iface=None, layer_provider=None, length_unit: str = "m",
           angle_unit: str = "deg") -> RotateHandleTool:
    session = RotateSession(length_unit, angle_unit)
    return RotateHandleTool(canvas, session, iface=iface,
                            layer_provider=layer_provider)
