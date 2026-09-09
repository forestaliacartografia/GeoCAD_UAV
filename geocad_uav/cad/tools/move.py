"""
Move an existing feature, in place.

Same contract as ``rotate``: the tool edits what the operator selected, it
never creates a feature and it has no entry in ``plugin.TOOL_GEOMETRY``. Three
ways in, one result:

* type ``@10,-4`` (or two lengths, dx then dy);
* click a base point, then a destination;
* drag the shape.

The translation itself is ``core.transform2d.translate`` for the preview and
``QgsGeometry.translate`` for the commit, which keeps holes, parts and Z that a
vertex-by-vertex rewrite would drop. Area, shape and azimuth are untouched by
construction: a translation cannot change them.

The parametric record follows. Every construction anchor in ``cad_params`` --
``x``/``y`` and the second and third picked points, or the vertex list of a
polyline -- is shifted by the same delta, then rebuilt and compared against the
geometric translation. If they disagree the record is marked broken rather than
written as if it still described the shape, exactly as rotation does. Without
that the next ``rebuild()`` would quietly put the feature back where it started.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ...core import transform2d as t2
from ...core.constants import GEOM_EPS_M
from ...core.errors import GeoCadError, InvalidInputError, LayerError
from .. import dynamic_input as di
from .. import parametric as pa
from .. import primitives as pr
from .base import CadMapTool, CadToolSession, ConstraintSlot, ToolState

#: Keys in ``cad_params`` that hold an absolute coordinate pair.
_POINT_KEYS = (("x", "y"), ("x2", "y2"), ("x3", "y3"))


def translated_params(record, dx: float, dy: float) -> dict:
    """``record``'s construction inputs, shifted by ``(dx, dy)``.

    Only the anchors move. Lengths, angles, side counts and segment counts
    describe the shape's own geometry and are invariant under a translation,
    so touching them would be wrong, not merely unnecessary.
    """
    params = dict(pr.input_params(record))
    for x_key, y_key in _POINT_KEYS:
        if params.get(x_key) is not None and params.get(y_key) is not None:
            params[x_key] = float(params[x_key]) + float(dx)
            params[y_key] = float(params[y_key]) + float(dy)
    points = params.get("points")
    if points:
        params["points"] = [[float(p[0]) + float(dx), float(p[1]) + float(dy)]
                            for p in points]
    return params


def _max_vertex_gap(geom_a, geom_b) -> float:
    """Largest vertex-to-vertex distance, or infinity if the shapes differ."""
    if geom_a is None or geom_b is None:
        return float("inf")
    a = np.array([[v.x(), v.y()] for v in geom_a.vertices()], dtype=float)
    b = np.array([[v.x(), v.y()] for v in geom_b.vertices()], dtype=float)
    if a.shape != b.shape or a.size == 0:
        return float("inf")
    return float(np.max(np.hypot(*(a - b).T)))


class MoveSession(CadToolSession):
    """A captured outline, a base point and one delta. No Qt, no QGIS."""

    tool_id = ""                       # moves an existing shape, builds none
    geometry_type = "Polygon"
    title = "Sposta"
    accepts_second_click = False

    def __init__(self, length_unit: str = "m", angle_unit: str = "deg"):
        #: (N, 2) outline captured when the feature was picked, unmoved.
        self.outline = None
        #: Where the drag or the base-point pick started, in work CRS.
        self.grab_point = None
        super().__init__(length_unit, angle_unit)

    def slots(self):
        return [ConstraintSlot("dx_m", di.KIND_LENGTH, "Spostamento X"),
                ConstraintSlot("dy_m", di.KIND_LENGTH, "Spostamento Y")]

    # -- lifecycle ---------------------------------------------------------

    def reset(self):
        super().reset()
        self.outline = None
        self.grab_point = None

    def capture(self, outline, anchor) -> str:
        """Adopt a feature's outline. Moves out of IDLE."""
        arr = np.asarray(outline, dtype=float)
        if arr.ndim != 2 or arr.shape[0] < 2:
            raise InvalidInputError(
                "outline must be an (N, 2) array with at least 2 points",
                user_message="La geometria selezionata non e' spostabile.")
        self.outline = arr[:, :2]
        self.origin = (float(anchor[0]), float(anchor[1]))
        self.set_value("dx_m", None)
        self.set_value("dy_m", None)
        self.grab_point = None
        self.state = ToolState.PICK_ORIGIN
        self.message = "Trascina, oppure digita @dx,dy"
        return self.state

    @property
    def has_feature(self) -> bool:
        return self.outline is not None

    @property
    def delta(self):
        return (float(self.value("dx_m") or 0.0),
                float(self.value("dy_m") or 0.0))

    @property
    def is_ready(self) -> bool:
        return self.has_feature and self.value("dx_m") is not None \
            and self.value("dy_m") is not None

    def confirm(self) -> str:
        if not self.is_ready:
            raise InvalidInputError(
                "move without a delta",
                user_message="Indica di quanto spostare la geometria.")
        self.state = ToolState.COMMIT
        return self.state

    # -- dragging and the base -> destination pick -------------------------

    def begin_drag(self, x: float, y: float) -> None:
        """Remember the base point the movement is measured from."""
        if self.has_feature:
            self.grab_point = (float(x), float(y))

    def hover(self, x: float, y: float) -> None:
        """Track the cursor and, while dragging, update the live delta.

        Two subtractions and no geometry: this is what keeps a mouse move
        free of GEOS (see the MV4 assertion).
        """
        super().hover(x, y)
        if not self.has_feature or self.grab_point is None:
            return
        self.set_value("dx_m", float(x) - self.grab_point[0])
        self.set_value("dy_m", float(y) - self.grab_point[1])
        self.state = ToolState.PREVIEW

    def end_drag(self) -> None:
        self.grab_point = None

    # -- typed delta -------------------------------------------------------

    def submit(self, text: str) -> str:
        """``@dx,dy`` in one token, or two lengths in a row."""
        if not self.has_feature:
            raise InvalidInputError(
                "no feature selected",
                user_message="Seleziona prima una geometria da spostare.")
        token = di.parse(text, self.length_unit, self.angle_unit)
        if token.kind == di.KIND_RELATIVE:
            self.set_value("dx_m", float(token.dx_m))
            self.set_value("dy_m", float(token.dy_m))
        elif token.kind == di.KIND_LENGTH:
            if self.value("dx_m") is None:
                self.set_value("dx_m", float(token.length_m))
                self.message = "Ora lo spostamento Y"
                self.state = ToolState.TYPE_CONSTRAINT
                return self.state
            self.set_value("dy_m", float(token.length_m))
        else:
            raise InvalidInputError(
                "expected @dx,dy or a length, got {0}".format(token.kind),
                user_message="Serve @dx,dy oppure due lunghezze.")
        self.state = ToolState.PREVIEW
        self.message = "Invio per confermare"
        return self.state

    # -- preview -----------------------------------------------------------

    def preview_points(self) -> Optional[np.ndarray]:
        if self.outline is None:
            return None
        dx, dy = self.delta
        return t2.translate(self.outline, dx, dy)

    def hud_lines(self):
        lines = super().hud_lines()
        if self.has_feature:
            dx, dy = self.delta
            lines.append("dx {0:.3f} m".format(dx))
            lines.append("dy {0:.3f} m".format(dy))
            lines.append("d {0:.3f} m".format(math.hypot(dx, dy)))
        return lines


class MoveTool(CadMapTool):
    """Translate the selected feature, in place."""

    def __init__(self, canvas, session=None, iface=None, layer_provider=None):
        super().__init__(canvas, session or MoveSession(), iface=iface,
                         layer_provider=layer_provider)
        self.feature_id = None
        self.source_layer = None
        self.source_geometry = None      # work CRS, unmoved
        self._dragging = False

    # -- picking -----------------------------------------------------------

    def active_layer(self):
        """The layer holding the feature to move."""
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

    def adopt_feature(self, layer, feature):
        """Capture a feature for moving. Returns the anchor used."""
        from qgis.core import QgsGeometry                       # noqa: PLC0415

        if layer is None or feature is None:
            raise LayerError(
                "no feature to move",
                user_message="Seleziona una geometria da spostare.",
                hint="Attiva un layer vettoriale e seleziona una feature.")
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            raise LayerError("empty geometry",
                             user_message="La geometria selezionata e' vuota.")

        self.source_layer = layer
        self.feature_id = feature.id()
        self.source_geometry = QgsGeometry(geometry)
        centre = geometry.boundingBox().center()
        anchor = (centre.x(), centre.y())
        self.session.capture(self._outline(geometry), anchor)
        return anchor

    def _outline(self, geometry):
        """Exterior outline for the drag preview, as a plain point array."""
        try:
            if geometry.isMultipart():
                parts = geometry.asGeometryCollection()
                geometry = parts[0] if parts else geometry
            polygon = geometry.asPolygon()
            if polygon:
                return np.array([[p.x(), p.y()] for p in polygon[0]],
                                dtype=float)
            line = geometry.asPolyline()
            if line:
                return np.array([[p.x(), p.y()] for p in line], dtype=float)
        except (TypeError, ValueError):
            pass
        return np.array([[v.x(), v.y()] for v in geometry.vertices()],
                        dtype=float)

    # -- canvas ------------------------------------------------------------

    def canvasMoveEvent(self, event):                           # noqa: N802
        if self._work_decision is None or not self.session.has_feature:
            return
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
                "no active layer", user_message="Nessun layer attivo.",
                hint="Scegli un layer vettoriale nel pannello o nella legenda.")
        features = list(layer.selectedFeatures())
        if not features:
            raise LayerError(
                "no selected feature",
                user_message="Nessuna geometria selezionata.",
                hint="Seleziona la feature da spostare, poi clicca sulla mappa.")
        self.adopt_feature(layer, features[0])

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
            self.move_committed()
        except GeoCadError as exc:
            self._warn(exc.formatted())
            self.session.state = ToolState.PREVIEW
            return
        self._typed = ""
        self.clear_band()
        self._paint_hud()

    def move_committed(self):
        """Write the translation. Returns ``(geometry, params_kept)``."""
        from qgis.core import QgsGeometry                       # noqa: PLC0415

        from ...core import undo

        if self.source_layer is None or self.source_geometry is None:
            raise LayerError("nothing captured",
                             user_message="Nessuna geometria da spostare.")
        self.session.confirm()
        dx, dy = self.session.delta

        moved = QgsGeometry(self.source_geometry)
        moved.translate(dx, dy)
        self.geometry_builds += 1

        attributes, params_kept = self._followed_params(moved, dx, dy)

        layer = self.source_layer
        fields = layer.fields()
        with undo.edit_command(layer, "GeoCad: Sposta"):
            if not layer.changeGeometry(self.feature_id, moved):
                raise LayerError(
                    "changeGeometry failed on {0}".format(layer.name()),
                    user_message="Aggiornamento della geometria non riuscito.")
            for name, value in (attributes or {}).items():
                index = fields.indexOf(name)
                if index >= 0:
                    layer.changeAttributeValue(self.feature_id, index, value)

        self.source_geometry = QgsGeometry(moved)
        centre = moved.boundingBox().center()
        self.session.reset()
        self.session.capture(self._outline(moved), (centre.x(), centre.y()))
        return moved, params_kept

    def _followed_params(self, moved, dx, dy):
        """Carry cad_params through the move, but only if they still fit."""
        try:
            feature = self.source_layer.getFeature(self.feature_id)
        except (AttributeError, RuntimeError):
            return None, False
        try:
            record = pa.read_record(feature)
        except GeoCadError:
            # Unparseable cad_params: the geometry still moves, but the
            # operator has to be told the record was left behind rather than
            # discovering it at the next rebuild.
            self._warn(
                "Parametri CAD illeggibili: lo spostamento e' stato applicato "
                "solo alla geometria.")
            return None, False
        if record is None:
            return None, False

        record = pa.check_integrity(feature, record)
        if record.broken:
            self._warn(
                "Parametri CAD non validi (geometria modificata a mano): "
                "lo spostamento e' stato applicato solo alla geometria.")
            return None, False

        try:
            params = translated_params(record, dx, dy)
            rebuilt, updated = pr.build(record.tool, params, record.crs_authid)
        except GeoCadError:
            rebuilt = None
            updated = None

        if rebuilt is None or _max_vertex_gap(rebuilt, moved) > GEOM_EPS_M:
            record.broken = True
            self._warn(
                "I parametri CAD non descrivono piu' la forma spostata: "
                "sono stati marcati come non validi.")
            return {pa.PARAMS_FIELD: record.to_json()}, False

        return pa.record_to_attributes(updated), True


def create(canvas, iface=None, layer_provider=None, length_unit: str = "m",
           angle_unit: str = "deg") -> MoveTool:
    session = MoveSession(length_unit, angle_unit)
    return MoveTool(canvas, session, iface=iface,
                    layer_provider=layer_provider)
