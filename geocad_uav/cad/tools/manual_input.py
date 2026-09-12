"""
Manual parametric input: type what it is, then say where it goes.

    dialog: shape + measures  ->  the exact shape rides the cursor  ->  click

Every other CAD tool asks for its numbers through the dock's dynamic input,
which is fast once the token syntax is in the fingers and opaque before that.
This one asks in words: a shape from a list, then the measure the operator
actually has -- a side, an area, a radius, a diagonal -- and the plugin works
out the rest.

Nothing geometric happens here. The dialog fills a parameter dictionary and
hands it to ``primitives.ring_for`` / ``primitives.build``, the same two calls
every other tool goes through, so a square typed as an area is byte for byte
the square the Square tool draws from the same area. The table below is the
whole of this module's knowledge: which parameters each existing primitive
accepts, and what to call them in Italian.

v1.7.0 -- the table holds exactly the three admitted primitives: rectangle,
square and regular polygon. This module *is* the parametric input mode of
each of them: the registry points three entries at it, one per shape, and the
dialog's shape list lets the operator move between them without leaving it.

v1.11.0 -- the numbers come first. The dialog opens when the tool is picked;
once the measures are confirmed the finished geometry follows the cursor at
full size, snapping like any other tool, and a single click puts it down.
Asking for the point first, as this module used to, made the operator commit
to a position before knowing how big the thing was.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from ...core.errors import InvalidInputError
from .. import primitives as pr
from .base import CadMapTool, CadToolSession, ToolState

#: How a field is entered and displayed. Not a unit conversion: the engine
#: works in metres, square metres and degrees, and so does the dialog.
KIND_LENGTH = "length"
KIND_AREA = "area"
KIND_ANGLE = "angle"
KIND_COUNT = "count"

SUFFIXES = {KIND_LENGTH: " m", KIND_AREA: " m2", KIND_ANGLE: " deg",
            KIND_COUNT: ""}
DECIMALS = {KIND_LENGTH: 3, KIND_AREA: 2, KIND_ANGLE: 4, KIND_COUNT: 0}


class Field:
    """One number the operator types, and the primitive parameter it fills."""

    __slots__ = ("name", "label", "kind", "default")

    def __init__(self, name: str, label: str, kind: str, default=None):
        self.name = name
        self.label = label
        self.kind = kind
        self.default = default


class ShapeSpec:
    """One entry of the shape list: an existing primitive, described.

    ``required`` are the fields always asked for; ``choices`` are the mutually
    exclusive ways of sizing the same shape, of which the operator picks one.
    ``params`` carries whatever the primitive needs that is not a number the
    operator types -- a mode, for instance.
    """

    __slots__ = ("tool", "label", "required", "choices", "params")

    def __init__(self, tool, label, required=(), choices=(), params=None):
        self.tool = tool
        self.label = label
        self.required = tuple(required)
        self.choices = tuple(choices)
        self.params = dict(params or {})


#: The shapes offered, in the order they appear in the dialog. Every entry
#: names a primitive that already exists and parameters ``_ring_for`` already
#: accepts; adding a fifth means writing a row here, not a builder.
SHAPES = (
    ShapeSpec(
        pr.TOOL_RECTANGLE, "Rettangolo",
        required=(Field("width_m", "Base", KIND_LENGTH, 20.0),
                  Field("height_m", "Altezza", KIND_LENGTH, 10.0),
                  Field("azimuth_deg", "Azimut", KIND_ANGLE, 0.0)),
        params={"mode": "center"}),
    ShapeSpec(
        pr.TOOL_SQUARE, "Quadrato",
        required=(Field("azimuth_deg", "Azimut", KIND_ANGLE, 0.0),),
        choices=(Field("side_m", "Lato", KIND_LENGTH, 10.0),
                 Field("diagonal_m", "Diagonale", KIND_LENGTH, 14.142136),
                 Field("area_m2", "Area", KIND_AREA, 100.0),
                 Field("perimeter_m", "Perimetro", KIND_LENGTH, 40.0))),
    ShapeSpec(
        pr.TOOL_POLYGON, "Poligono regolare",
        required=(Field("n_sides", "Numero di lati", KIND_COUNT, 6),
                  Field("azimuth_deg", "Azimut", KIND_ANGLE, 0.0)),
        choices=(Field("radius_m", "Raggio circoscritto", KIND_LENGTH, 10.0),
                 Field("apothem_m", "Apotema", KIND_LENGTH, 8.660254),
                 Field("side_m", "Lato", KIND_LENGTH, 10.0),
                 Field("area_m2", "Area", KIND_AREA, 259.807621))),
)

SHAPE_KEYS = tuple(spec.tool for spec in SHAPES)


def shape_spec(tool: str) -> ShapeSpec:
    for spec in SHAPES:
        if spec.tool == tool:
            return spec
    raise InvalidInputError(
        "manual input does not offer {0!r}".format(tool),
        user_message="Forma non disponibile nell'inserimento manuale.")


class ManualInputSession(CadToolSession):
    """Insertion point from the map, dimensions from the dialog."""

    tool_id = pr.TOOL_RECTANGLE          # replaced by set_shape()
    geometry_type = "Polygon"
    title = "Inserimento manuale"
    accepts_second_click = False

    def __init__(self, length_unit: str = "m", angle_unit: str = "deg",
                 shape: str = pr.TOOL_RECTANGLE):
        #: Values typed for the current shape, by primitive parameter name.
        self.values = {}
        self.spec = shape_spec(shape)
        self.choice = self.spec.choices[0].name if self.spec.choices else ""
        super().__init__(length_unit, angle_unit)
        self.tool_id = self.spec.tool
        self.load_defaults()

    def slots(self):
        """None: the numbers come from the dialog, not from typed tokens.

        Areas and side counts have no dynamic-input kind, and inventing one
        would mean a second parser. The dock therefore binds no field to this
        tool, which is exactly right -- its field is the dialog.
        """
        return []

    # -- the shape ---------------------------------------------------------

    def set_shape(self, tool: str) -> None:
        """Switch shape, keeping any value the new shape also understands."""
        spec = shape_spec(tool)
        kept = dict(self.values)
        self.spec = spec
        self.tool_id = spec.tool
        self.choice = spec.choices[0].name if spec.choices else ""
        self.values = {}
        self.load_defaults()
        for field in self.fields():
            if field.name in kept and kept[field.name] is not None:
                self.values[field.name] = kept[field.name]

    def set_choice(self, name: str) -> None:
        """Pick which measure sizes the shape."""
        if not self.spec.choices:
            return
        if name not in [field.name for field in self.spec.choices]:
            raise InvalidInputError(
                "{0} cannot be sized by {1!r}".format(self.spec.tool, name),
                user_message="Misura non prevista per questa forma.")
        self.choice = name

    def fields(self) -> list:
        """Every field the dialog must show for the current shape."""
        out = list(self.spec.required)
        for field in self.spec.choices:
            if field.name == self.choice:
                out.append(field)
        return out

    def load_defaults(self) -> None:
        for field in self.fields():
            if field.default is not None:
                self.values.setdefault(field.name, field.default)

    def set_field(self, name: str, value) -> None:
        for field in self.fields():
            if field.name == name:
                self.values[name] = (int(value) if field.kind == KIND_COUNT
                                     else float(value))
                return
        raise InvalidInputError(
            "unknown field {0!r} for {1}".format(name, self.spec.tool),
            user_message="Parametro non previsto da questa forma.")

    # -- completion --------------------------------------------------------

    @property
    def has_values(self) -> bool:
        """Every measure the shape needs has been typed.

        Separate from :attr:`is_ready`, which also wants an insertion point:
        between the dialog closing and the click landing the shape is fully
        defined and has nowhere to be yet, and that is precisely the state in
        which it rides the cursor.
        """
        return all(self.values.get(field.name) is not None
                   for field in self.fields())

    @property
    def is_ready(self) -> bool:
        if self.origin is None:
            return False
        return self.has_values

    @property
    def is_placing(self) -> bool:
        """Measures in hand, insertion point still to come."""
        return self.has_values and self.origin is None

    def confirm(self) -> str:
        if self.origin is None:
            raise InvalidInputError(
                "manual input needs an insertion point",
                user_message="Indica prima il punto di inserimento.")
        if not self.is_ready:
            raise InvalidInputError(
                "manual input is missing a value",
                user_message="Compila tutti i valori della forma.")
        self.state = ToolState.COMMIT
        return self.state

    # -- parameters --------------------------------------------------------

    def anchor(self):
        """Where the shape is drawn: the point clicked, or the cursor.

        The preview and the commit go through the same builder with the same
        numbers; the only difference between the shape following the mouse
        and the shape written to the layer is which point was handed in.
        """
        return self.origin if self.origin is not None else self.cursor

    def build_params(self, anchor=None) -> dict:
        anchor = anchor if anchor is not None else self.origin
        if anchor is None:
            raise InvalidInputError(
                "manual input needs an insertion point",
                user_message="Indica prima il punto di inserimento.")
        params = dict(self.spec.params)
        params["x"] = float(anchor[0])
        params["y"] = float(anchor[1])
        for field in self.fields():
            value = self.values.get(field.name)
            if value is None:
                continue
            params[field.name] = (int(value) if field.kind == KIND_COUNT
                                  else float(value))
        return params

    def preview_points(self) -> Optional[np.ndarray]:
        """The shape as typed, drawn by the same dispatch that commits it.

        Anchored on the cursor while it is being placed, so what the operator
        drags around the map is the exact geometry -- not a bounding box, not
        a marker -- and what lands on the click is the same ring.
        """
        anchor = self.anchor()
        if anchor is None or not self.has_values:
            return None
        try:
            coords, _closed = pr.ring_for(self.spec.tool,
                                          self.build_params(anchor))
        except InvalidInputError:
            return None            # half-typed numbers are not an error yet
        return np.asarray(coords, dtype=float)

    # -- readout -----------------------------------------------------------

    def hud_lines(self):
        lines = super().hud_lines()
        lines.append("Forma {0}".format(self.spec.label))
        if self.is_placing:
            lines.append("Click per posizionare (tasto destro: cambia misure)")
        for field in self.fields():
            value = self.values.get(field.name)
            if value is None:
                continue
            if field.kind == KIND_COUNT:
                lines.append("{0} {1}".format(field.label, int(value)))
            else:
                lines.append("{0} {1:.3f}{2}".format(
                    field.label, float(value), SUFFIXES[field.kind]))
        return lines


class ManualInputDialog:
    """Thin form over :class:`ManualInputSession`. Built on demand.

    Qt is imported inside the constructor, through ``qgis.PyQt``, so importing
    this module costs nothing in a headless test and the plugin keeps working
    on both the PyQt5 and the PyQt6 build of QGIS.
    """

    def __init__(self, session: ManualInputSession, parent=None):
        from qgis.PyQt.QtWidgets import (QComboBox,             # noqa: PLC0415
                                         QDialog, QDialogButtonBox,
                                         QDoubleSpinBox, QFormLayout,
                                         QSpinBox, QVBoxLayout)

        self.session = session
        self._editors = {}
        self.dialog = QDialog(parent)
        self.dialog.setWindowTitle("Inserimento manuale")
        layout = QVBoxLayout(self.dialog)

        self.form = QFormLayout()
        layout.addLayout(self.form)

        self.shape_box = QComboBox(self.dialog)
        for spec in SHAPES:
            self.shape_box.addItem(spec.label, spec.tool)
        self.shape_box.setCurrentIndex(SHAPE_KEYS.index(session.spec.tool))
        self.form.addRow("Forma", self.shape_box)

        self.choice_box = QComboBox(self.dialog)
        self.form.addRow("Misura", self.choice_box)

        self._QDoubleSpinBox = QDoubleSpinBox
        self._QSpinBox = QSpinBox
        self._rebuild_fields()

        self.shape_box.currentIndexChanged.connect(self._on_shape)
        self.choice_box.currentIndexChanged.connect(self._on_choice)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, parent=self.dialog)
        buttons.accepted.connect(self.dialog.accept)
        buttons.rejected.connect(self.dialog.reject)
        layout.addWidget(buttons)

    # -- wiring ------------------------------------------------------------

    def _on_shape(self, *_args):
        self.session.set_shape(self.shape_box.currentData())
        self._rebuild_fields()

    def _on_choice(self, *_args):
        data = self.choice_box.currentData()
        if data:
            self.session.set_choice(data)
            self._rebuild_fields(keep_choice=True)

    def _rebuild_fields(self, keep_choice: bool = False):
        """Redraw the numeric rows for the shape now selected."""
        for editor in self._editors.values():
            self.form.removeRow(editor)
        self._editors = {}

        if not keep_choice:
            self.choice_box.blockSignals(True)
            self.choice_box.clear()
            for field in self.session.spec.choices:
                self.choice_box.addItem(field.label, field.name)
            self.choice_box.setEnabled(bool(self.session.spec.choices))
            self.choice_box.blockSignals(False)

        for field in self.session.fields():
            if field.kind == KIND_COUNT:
                editor = self._QSpinBox(self.dialog)
                editor.setRange(3, 360)
                editor.setValue(int(self.session.values.get(field.name, 3)))
            else:
                editor = self._QDoubleSpinBox(self.dialog)
                editor.setDecimals(DECIMALS[field.kind])
                editor.setRange(0.0 if field.kind == KIND_ANGLE else 0.001,
                                360.0 if field.kind == KIND_ANGLE else 1e9)
                editor.setSuffix(SUFFIXES[field.kind])
                editor.setValue(float(self.session.values.get(
                    field.name, field.default or 0.0)))
            self.form.addRow(field.label, editor)
            self._editors[field.name] = editor

    # -- result ------------------------------------------------------------

    def harvest(self) -> None:
        """Copy what is on screen into the session."""
        for name, editor in self._editors.items():
            self.session.set_field(name, editor.value())

    def exec_accepted(self) -> bool:
        """Show it modally. True when the operator confirmed."""
        from qgis.PyQt.QtWidgets import QDialog                 # noqa: PLC0415

        accepted = self.dialog.exec() == QDialog.DialogCode.Accepted
        if accepted:
            self.harvest()
        return accepted


class ManualInputMapTool(CadMapTool):
    """Measures first, then the exact shape rides the cursor until it is put.

    The order matters. Asking for the point first and the numbers afterwards
    means the operator commits to a position before knowing how big the thing
    is; this way the geometry is complete before it is placed, it is drawn at
    the cursor at full size, and the click only decides where.
    """

    def __init__(self, canvas, session, iface=None, layer_provider=None,
                 dialog_factory=None):
        super().__init__(canvas, session, iface=iface,
                         layer_provider=layer_provider)
        #: Anything callable ``(session, parent) -> bool``. The default opens
        #: the real dialog; a test passes its own and never shows a window.
        self.dialog_factory = dialog_factory or self._default_dialog
        #: True while a dialog is up: the canvas events that arrive meanwhile
        #: must not open a second one.
        self._asking = False
        #: Whether the measures have been asked for since this activation.
        #: The scheduled dialog is the normal way; a first click is the
        #: fallback for an embedding where the timer never gets a turn, so
        #: the tool can never place a shape whose numbers nobody confirmed.
        self._asked = False

    @staticmethod
    def _default_dialog(session, parent) -> bool:
        return ManualInputDialog(session, parent).exec_accepted()

    # -- lifecycle ---------------------------------------------------------

    def activate(self):
        """Take the measures, then hand the shape to the cursor.

        Asked on every activation, not only the first: an operator who picks
        this tool has picked it in order to place something particular, and
        the defaults sitting in the fields are a starting point for the
        dialog, not an answer to it.

        Scheduled, not called: QGIS calls ``activate()`` from inside
        ``setMapTool()``, and opening a modal dialog there runs a second
        event loop inside the one that is still switching tools. A zero
        timer puts the dialog on the next turn of the loop, when the tool is
        properly current and the canvas is its own again.
        """
        super().activate()
        self._asked = False
        self.schedule_measures()

    def schedule_measures(self) -> None:
        """Ask for the measures as soon as the event loop comes back."""
        from qgis.PyQt.QtCore import QTimer                     # noqa: PLC0415

        QTimer.singleShot(0, self._ask_if_still_current)

    def _ask_if_still_current(self) -> None:
        """The scheduled ask, dropped if the operator moved on meanwhile.

        Only the *scheduled* one: a dialog the operator asked for by
        clicking is opened whatever the canvas thinks is current, because
        they just asked for it.
        """
        canvas = self.canvas()
        if canvas is not None and canvas.mapTool() is not self:
            return
        self.ask_measures()

    def ask_measures(self) -> bool:
        """Open the dialog. True when the operator confirmed the numbers.

        The values survive a placement, so one dialog serves a whole run of
        identical shapes: an operator laying out twenty 4 x 4 m plots types
        the numbers once and clicks twenty times.
        """
        if self._asking:
            return False
        parent = None
        if self.iface is not None:
            try:
                parent = self.iface.mainWindow()
            except AttributeError:
                parent = None
        self._asking = True
        try:
            accepted = bool(self.dialog_factory(self.session, parent))
        finally:
            self._asking = False
            self._asked = True
        if not accepted:
            self.session.values = {}
            self._escape()
            return False
        self.session.cursor = None
        self.clear_band()
        self._paint_hud()
        return True

    # -- mouse -------------------------------------------------------------

    def canvasMoveEvent(self, event):                           # noqa: N802
        """Carry the shape under the cursor while it is waiting to be placed.

        The base class only redraws once a construction has started; here
        there is nothing to start -- the shape is already whole -- so the
        band is refreshed on every move. Still no QgsGeometry and still no
        canvas refresh: the preview is points, as everywhere else.
        """
        super().canvasMoveEvent(event)
        if self._work_decision is not None and self.session.is_placing:
            self.update_band(self.canvas(), self._to_canvas)

    def canvasReleaseEvent(self, event):                        # noqa: N802
        """Left click puts the shape down; right click changes the measures."""
        from qgis.PyQt.QtCore import Qt                         # noqa: PLC0415

        if event.button() == Qt.MouseButton.RightButton:
            self.ask_measures()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().canvasReleaseEvent(event)
            return
        if self._work_decision is None:
            super().canvasReleaseEvent(event)
            return
        if (not self._asked or not self.session.has_values)                 and not self.ask_measures():
            return
        x, y = self._map_to_work(self.picked_point(event))
        self.session.set_origin(x, y)
        self._do_commit()
        # The numbers stay: the next click puts down another one just like it.
        self.session.cursor = None


def create(canvas, iface=None, layer_provider=None,
           shape: str = pr.TOOL_RECTANGLE, dialog_factory=None,
           length_unit: str = "m", angle_unit: str = "deg") -> CadMapTool:
    session = ManualInputSession(length_unit, angle_unit, shape)
    return ManualInputMapTool(canvas, session, iface=iface,
                              layer_provider=layer_provider,
                              dialog_factory=dialog_factory)
