"""
Resize an existing feature to an absolute size.

Same contract as ``rotate`` and ``move``: it edits the selected feature, it
never creates one, and it has no entry in ``plugin.TOOL_GEOMETRY``.

This is not a scale factor. The operator types the size they want -- width 70,
radius 4, semi-major 20 -- and the tool writes that number into ``cad_params``
and calls ``primitives.rebuild``. The shape therefore comes out of the same
engine that built it, so a rectangle stays a rectangle and a square stays
square, and the record still describes what is on the map.

Which dimensions are offered depends on the record itself: whichever size key
the shape was built from is the one that can be edited, because
``primitives._ring_for`` accepts exactly one and would refuse a second. A
polyline has no single dimension to set and is refused in as many words.

The anchor never moves: ``x``/``y`` and ``azimuth_deg`` are copied through
untouched, so the shape grows about the point it was built from. If the
rebuilt geometry does not match the parameters within ``GEOM_EPS_M`` the
record is marked broken and the geometry is written anyway -- the operator
sees the shape they asked for and a warning that the record no longer
describes it, rather than a silent rollback.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from ...core.constants import GEOM_EPS_M
from ...core.errors import GeoCadError, InvalidInputError, LayerError
from .. import dynamic_input as di
from .. import parametric as pa
from .. import primitives as pr
from .base import CadMapTool, CadToolSession, ConstraintSlot, ToolState

#: Editable size keys per tool, in the order they are asked for. Only the ones
#: the record actually carries are offered: _ring_for wants exactly one size
#: for square, circle and polygon, and both axes for rectangle and ellipse.
DIMENSIONS = {
    pr.TOOL_RECTANGLE: ("width_m", "height_m"),
    pr.TOOL_SQUARE: ("side_m", "diagonal_m", "area_m2", "perimeter_m"),
    pr.TOOL_CIRCLE: ("radius_m", "diameter_m", "area_m2", "circumference_m"),
    pr.TOOL_ELLIPSE: ("semi_major_m", "semi_minor_m"),
    pr.TOOL_POLYGON: ("radius_m", "apothem_m", "side_m", "area_m2"),
    pr.TOOL_LINE: ("length_m",),
}

#: Tools that carry both of their dimensions at once.
_BOTH = (pr.TOOL_RECTANGLE, pr.TOOL_ELLIPSE)

LABELS = {
    "width_m": "Larghezza", "height_m": "Altezza", "side_m": "Lato",
    "diagonal_m": "Diagonale", "area_m2": "Area", "perimeter_m": "Perimetro",
    "radius_m": "Raggio", "diameter_m": "Diametro",
    "circumference_m": "Circonferenza", "apothem_m": "Apotema",
    "semi_major_m": "Semiasse maggiore", "semi_minor_m": "Semiasse minore",
    "length_m": "Lunghezza",
}


def editable_dimensions(record) -> "list[str]":
    """The size keys of ``record`` that this tool may change.

    Raises for a shape with no single size -- a polyline is a chain of
    vertices, not a dimension.
    """
    if record is None:
        return []
    if record.tool == pr.TOOL_POLYLINE:
        raise InvalidInputError(
            "resize does not apply to a polyline",
            user_message="Resize non si applica a una polilinea.",
            hint="Usa le maniglie dei vertici, oppure ridisegnala.")
    candidates = DIMENSIONS.get(record.tool)
    if not candidates:
        raise InvalidInputError(
            "resize does not apply to {0}".format(record.tool),
            user_message="Resize non si applica a questa forma.")
    params = pr.input_params(record)
    present = [key for key in candidates if params.get(key) is not None]
    if record.tool in _BOTH:
        return list(candidates) if len(present) == len(candidates) else present
    return present[:1]


def resized_params(record, values: dict) -> dict:
    """``record``'s inputs with the given sizes replaced, anchor untouched."""
    params = dict(pr.input_params(record))
    for key, value in values.items():
        params[key] = float(value)
    return params


def _max_vertex_gap(geom_a, geom_b) -> float:
    if geom_a is None or geom_b is None:
        return float("inf")
    a = np.array([[v.x(), v.y()] for v in geom_a.vertices()], dtype=float)
    b = np.array([[v.x(), v.y()] for v in geom_b.vertices()], dtype=float)
    if a.shape != b.shape or a.size == 0:
        return float("inf")
    return float(np.max(np.hypot(*(a - b).T)))


class ResizeSession(CadToolSession):
    """The captured record's editable sizes, typed as absolute values."""

    tool_id = ""                       # resizes an existing shape, builds none
    geometry_type = "Polygon"
    title = "Ridimensiona"
    accepts_second_click = False

    def __init__(self, length_unit: str = "m", angle_unit: str = "deg"):
        #: Size keys currently editable; filled by capture().
        self.targets = []
        #: (N, 2) outline captured when the feature was picked.
        self.outline = None
        #: The record being edited, or None for a feature without cad_params.
        self.record = None
        super().__init__(length_unit, angle_unit)

    def slots(self):
        return [ConstraintSlot(key, di.KIND_LENGTH,
                               LABELS.get(key, key)) for key in self.targets]

    # -- lifecycle ---------------------------------------------------------

    def reset(self):
        super().reset()
        self.targets = []
        self.outline = None
        self.record = None
        self._slots = []

    def capture(self, outline, record) -> str:
        """Adopt a feature and its record. Moves out of IDLE.

        The slot list depends on the shape, so it is rebuilt here rather than
        in ``__init__`` -- the base class reads ``self._slots``, which is
        exactly what ``slots()`` returns for the newly adopted record.
        """
        arr = np.asarray(outline, dtype=float)
        if arr.ndim != 2 or arr.shape[0] < 2:
            raise InvalidInputError(
                "outline must be an (N, 2) array with at least 2 points",
                user_message="La geometria selezionata non e' ridimensionabile.")
        self.targets = editable_dimensions(record)
        if not self.targets:
            raise InvalidInputError(
                "no editable dimension on {0}".format(
                    record.tool if record else "?"),
                user_message="Questa forma non ha una dimensione modificabile.",
                hint="Serve una geometria creata con gli strumenti CAD.")
        self.outline = arr[:, :2]
        self.record = record
        self._slots = self.slots()
        current = pr.input_params(record)
        for slot in self._slots:
            slot.value = None
            self.message = "Valore attuale {0}: {1:g}".format(
                LABELS.get(slot.name, slot.name),
                float(current.get(slot.name, 0.0)))
        self.active_slot = 0
        self.state = ToolState.PICK_ORIGIN
        return self.state

    @property
    def has_feature(self) -> bool:
        return self.record is not None and bool(self.targets)

    @property
    def is_ready(self) -> bool:
        return self.has_feature and self.all_filled

    def current(self) -> dict:
        """The sizes as they are now, for the HUD and for a partial edit."""
        if self.record is None:
            return {}
        params = pr.input_params(self.record)
        return {key: params.get(key) for key in self.targets}

    def values(self) -> dict:
        """Typed sizes, falling back to the current one for anything untouched."""
        out = dict(self.current())
        for slot in self._slots:
            if slot.value is not None:
                out[slot.name] = float(slot.value)
        return {k: v for k, v in out.items() if v is not None}

    def confirm(self) -> str:
        if not self.has_feature:
            raise InvalidInputError(
                "resize without a feature",
                user_message="Seleziona prima una geometria.")
        self.state = ToolState.COMMIT
        return self.state

    # -- typed sizes -------------------------------------------------------

    def submit(self, text: str) -> str:
        """One absolute size per Enter, in the order the slots are listed."""
        if not self.has_feature:
            raise InvalidInputError(
                "no feature selected",
                user_message="Seleziona prima una geometria da ridimensionare.")
        token = di.parse(text, self.length_unit, self.angle_unit)
        if token.kind != di.KIND_LENGTH:
            raise InvalidInputError(
                "expected a length, got {0}".format(token.kind),
                user_message="Serve una misura assoluta, per esempio 70.")
        index = min(self.active_slot, len(self._slots) - 1)
        self._slots[index].value = float(token.length_m)
        self.active_slot = index + 1
        self.state = (ToolState.PREVIEW if self.all_filled
                      else ToolState.TYPE_CONSTRAINT)
        self.message = ("Invio per confermare" if self.all_filled
                        else "Prossima misura")
        return self.state

    # -- preview -----------------------------------------------------------

    def preview_points(self) -> Optional[np.ndarray]:
        """The rebuilt ring, or None when the engine refuses these sizes."""
        if not self.has_feature:
            return None
        try:
            coords, _closed = pr._ring_for(self.record.tool,
                                           resized_params(self.record,
                                                          self.values()))
        except GeoCadError:
            return None
        return np.asarray(coords, dtype=float)

    def hud_lines(self):
        lines = super().hud_lines()
        current = self.current()
        for key in self.targets:
            now = current.get(key)
            wanted = self.value(key)
            lines.append("{0} {1:g} -> {2}".format(
                LABELS.get(key, key), float(now or 0.0),
                "{0:g}".format(wanted) if wanted is not None else "?"))
        return lines


class ResizeTool(CadMapTool):
    """Set the absolute size of the selected feature."""

    def __init__(self, canvas, session=None, iface=None, layer_provider=None):
        super().__init__(canvas, session or ResizeSession(), iface=iface,
                         layer_provider=layer_provider)
        self.feature_id = None
        self.source_layer = None
        self.source_geometry = None

    def active_layer(self):
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
        """Capture a feature and its record. Refuses what it cannot resize."""
        from qgis.core import QgsGeometry                       # noqa: PLC0415

        if layer is None or feature is None:
            raise LayerError(
                "no feature to resize",
                user_message="Seleziona una geometria da ridimensionare.")
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            raise LayerError("empty geometry",
                             user_message="La geometria selezionata e' vuota.")
        try:
            record = pa.read_record(feature)
        except GeoCadError:
            record = None
        if record is None:
            raise InvalidInputError(
                "feature has no cad_params",
                user_message="Questa geometria non ha parametri CAD.",
                hint="Resize agisce sui parametri, non sui vertici: ridisegna "
                     "la forma con uno strumento CAD.")
        record = pa.check_integrity(feature, record)
        if record.broken:
            raise InvalidInputError(
                "cad_params are broken",
                user_message="I parametri CAD di questa geometria non sono "
                             "validi (modificata a mano).",
                hint="Ridisegna la forma, oppure usa gli strumenti di QGIS.")

        self.source_layer = layer
        self.feature_id = feature.id()
        self.source_geometry = QgsGeometry(geometry)
        outline = np.array([[v.x(), v.y()] for v in geometry.vertices()],
                           dtype=float)
        self.session.capture(outline, record)
        return self.session.targets

    # -- canvas ------------------------------------------------------------

    def canvasMoveEvent(self, event):                           # noqa: N802
        """Hovering shows the cursor. Resize is typed, so nothing is built."""
        if self._work_decision is None or not self.session.has_feature:
            return
        x, y = self._map_to_work(self.picked_point(event))
        self.session.hover(x, y)
        self._paint_hud()

    def canvasReleaseEvent(self, event):                        # noqa: N802
        from qgis.PyQt.QtCore import Qt                         # noqa: PLC0415

        if event.button() == Qt.MouseButton.RightButton:
            self._escape()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        try:
            if not self.session.has_feature:
                self._pick_from_selection()
        except GeoCadError as exc:
            self._warn(exc.formatted())
            return
        self.update_band(self.canvas(), self._to_canvas)
        self._paint_hud()

    def _pick_from_selection(self):
        layer = self.active_layer()
        if layer is None:
            raise LayerError("no active layer",
                             user_message="Nessun layer attivo.")
        features = list(layer.selectedFeatures())
        if not features:
            raise LayerError(
                "no selected feature",
                user_message="Nessuna geometria selezionata.",
                hint="Seleziona la feature da ridimensionare.")
        self.adopt_feature(layer, features[0])

    def _escape(self):
        self.session.reset()
        self.feature_id = None
        self.source_layer = None
        self.source_geometry = None
        self._typed = ""
        self.clear_band()
        self._paint_hud()

    # -- commit ------------------------------------------------------------

    def _do_commit(self):
        try:
            self.resize_committed()
        except GeoCadError as exc:
            self._warn(exc.formatted())
            self.session.state = ToolState.PREVIEW
            return
        self._typed = ""
        self.clear_band()
        self._paint_hud()

    def resize_committed(self):
        """Write the new size. Returns ``(geometry, params_kept)``."""
        from qgis.core import QgsGeometry                       # noqa: PLC0415

        from ...core import undo

        if self.source_layer is None or self.source_geometry is None:
            raise LayerError(
                "nothing captured",
                user_message="Nessuna geometria da ridimensionare.")
        record = self.session.record
        self.session.confirm()

        params = resized_params(record, self.session.values())
        # Raises ConstraintError for b > a, InvalidInputError for a negative
        # size: the engine's verdict, reported, never worked around.
        resized, updated = pr.build(record.tool, params, record.crs_authid)
        self.geometry_builds += 1

        # The geometry was built from the parameters, so the two agree by
        # construction today; the round-trip is asserted anyway, because a
        # build that stopped being idempotent must not pass silently.
        broken = False
        rebuilt, _again = pr.build(record.tool, pr.input_params(updated),
                                   record.crs_authid)
        if _max_vertex_gap(rebuilt, resized) > GEOM_EPS_M:
            broken = True
            updated.broken = True
            self._warn(
                "I parametri CAD non riproducono la forma ridimensionata: "
                "sono stati marcati come non validi. La geometria e' stata "
                "scritta lo stesso.")

        attributes = ({pa.PARAMS_FIELD: updated.to_json()} if broken
                      else pa.record_to_attributes(updated))
        layer = self.source_layer
        fields = layer.fields()
        with undo.edit_command(layer, "GeoCad: Ridimensiona"):
            if not layer.changeGeometry(self.feature_id, resized):
                raise LayerError(
                    "changeGeometry failed on {0}".format(layer.name()),
                    user_message="Aggiornamento della geometria non riuscito.")
            for name, value in attributes.items():
                index = fields.indexOf(name)
                if index >= 0:
                    layer.changeAttributeValue(self.feature_id, index, value)

        self.source_geometry = QgsGeometry(resized)
        self.session.reset()
        outline = np.array([[v.x(), v.y()] for v in resized.vertices()],
                           dtype=float)
        self.session.capture(outline, updated)
        return resized, not broken


def create(canvas, iface=None, layer_provider=None, length_unit: str = "m",
           angle_unit: str = "deg") -> ResizeTool:
    session = ResizeSession(length_unit, angle_unit)
    return ResizeTool(canvas, session, iface=iface,
                      layer_provider=layer_provider)
