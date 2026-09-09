"""
Shared machinery for the interactive CAD map tools.

Two layers, deliberately separated:

* :class:`CadToolSession` -- the state machine. **Pure Python**: no Qt, no
  QGIS. It holds the picked anchor, the typed constraints and the preview
  points, and it is what the test suite drives with synthetic events.
* :class:`BaseCadTool` -- a thin ``QgsMapTool`` that turns mouse and key
  events into session calls, draws the rubber band, and commits.

The tools compute **no geometry of their own**. Every number comes from the
frozen, already-tested engine: ``cad.dynamic_input`` parses the typed tokens,
``core.geometry_engine`` (through ``cad.primitives``) builds the shape,
``core.crs`` moves coordinates between frames, ``core.undo`` wraps the write.

Four rules paid for in blood during v1.0.0 and enforced here:

1. **No invented Qt/QGIS API.** Every enum below is the *scoped* form
   (``Qt.Key.Key_Escape``, not ``Qt.Key_Escape``): the unscoped names were
   removed in PyQt6, which QGIS 4.0 ships. Both forms were verified present on
   PyQt5 5.15 (QGIS 3.40 LTR) and only the scoped one on PyQt6 6.10.
2. **No QgsGeometry in a move event.** The preview is points fed to a rubber
   band; a real geometry is constructed only at commit.
3. **No canvas.refresh() while moving.** Rubber band and HUD only.
4. **Every write inside beginEditCommand/endEditCommand**, via
   ``core.undo.edit_command``, so one shape is one Ctrl+Z.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ...core import crs as crs_svc
from ...core.constants import GEOM_EPS_M
from ...core.errors import GeoCadError, InvalidInputError, LayerError
from ...io import layer_factory as lf
from .. import dynamic_input as di
from .. import parametric as pa
from .. import primitives as pr


# --------------------------------------------------------------------------
# State machine
# --------------------------------------------------------------------------

class ToolState:
    """Explicit states. A tool is always in exactly one of these."""

    IDLE = "idle"
    PICK_ORIGIN = "pick_origin"          # origin fixed, awaiting more input
    PICK_SECOND = "pick_second"          # awaiting a second click
    TYPE_CONSTRAINT = "type_constraint"  # partway through typed values
    PREVIEW = "preview"                  # fully constrained, ready to commit
    COMMIT = "commit"                    # transient: feature written

    ALL = (IDLE, PICK_ORIGIN, PICK_SECOND, TYPE_CONSTRAINT, PREVIEW, COMMIT)


class ConstraintSlot:
    """One typed value a tool needs, in the order it is asked for."""

    __slots__ = ("name", "kind", "label", "value")

    def __init__(self, name: str, kind: str, label: str):
        self.name = name
        self.kind = kind          # di.KIND_LENGTH | di.KIND_ANGLE
        self.label = label
        self.value = None

    @property
    def filled(self) -> bool:
        return self.value is not None


class CadToolSession:
    """The interactive construction, as data. No Qt, no canvas, no QGIS.

    Subclasses of :class:`BaseCadTool` provide a factory for this via
    ``make_session()``; concrete tools override :meth:`slots`,
    :meth:`build_params` and :meth:`preview_points`.
    """

    #: primitives.TOOL_* identifier this session builds.
    tool_id = ""
    #: "LineString" or "Polygon" -- the scratch layer geometry type.
    geometry_type = "Polygon"
    #: Human name used in the undo entry and the HUD.
    title = "Forma"
    #: True when a second map click can complete the shape instead of typing.
    accepts_second_click = True
    #: True for tools that take an unbounded number of vertices (polyline).
    #: Such a tool stays in PICK_ORIGIN while collecting, and only reaches
    #: PREVIEW when the operator closes the construction.
    multi_vertex = False

    def __init__(self, length_unit: str = "m", angle_unit: str = "deg"):
        self.length_unit = length_unit
        self.angle_unit = angle_unit
        self._slots = self.slots()
        self.state = ToolState.IDLE
        self.origin = None            # (x, y) in the WORK crs
        self.second = None            # (x, y) in the WORK crs
        self.cursor = None            # last hovered point, WORK crs
        self.vertices = []            # multi-vertex chain, WORK crs
        self.active_slot = 0
        self.message = ""

    # -- multi-vertex support ---------------------------------------------

    def last_azimuth_deg(self):
        """Bearing of the last drawn segment, or None below two vertices.

        This is what makes a polar token relative to the *previous segment*
        rather than to grid north, and it is the only behavioural difference
        between a polyline and a single line.
        """
        if len(self.vertices) < 2:
            return None
        (ax, ay), (bx, by) = self.vertices[-2], self.vertices[-1]
        if math.hypot(bx - ax, by - ay) < GEOM_EPS_M:
            return None
        return math.degrees(math.atan2(bx - ax, by - ay)) % 360.0

    def anchor_point(self):
        """Where the next relative/polar token starts from."""
        return self.vertices[-1] if self.vertices else self.origin

    def remove_last_vertex(self) -> bool:
        """Backspace: drop one vertex, keeping the construction alive.

        With vertices left the tool returns to PICK_ORIGIN and keeps taking
        input; only when the last one goes does it become genuinely IDLE.
        Backspace is deliberately not Escape: it never discards everything.
        """
        if not self.vertices:
            return False
        self.vertices.pop()
        if not self.vertices:
            self.reset()
        else:
            self.origin = self.vertices[0]
            self.second = self.vertices[-1] if len(self.vertices) > 1 else None
            self.state = ToolState.PICK_ORIGIN
            self.message = self._prompt()
        return True

    # -- to be provided by concrete tools ---------------------------------

    def slots(self) -> "list[ConstraintSlot]":
        raise NotImplementedError

    def build_params(self) -> dict:
        raise NotImplementedError

    def preview_points(self) -> Optional[np.ndarray]:
        """(N, 2) array in the work CRS, or None when there is nothing to show.

        Must never build a QgsGeometry: this runs on every mouse move.
        """
        raise NotImplementedError

    # -- slot access -------------------------------------------------------

    @property
    def slot_list(self):
        return self._slots

    def value(self, name: str):
        for slot in self._slots:
            if slot.name == name:
                return slot.value
        return None

    def set_value(self, name: str, value) -> None:
        for slot in self._slots:
            if slot.name == name:
                slot.value = value
                return
        raise InvalidInputError(
            "unknown constraint {0!r} for {1}".format(name, self.tool_id),
            user_message="Parametro non previsto da questo strumento.")

    @property
    def all_filled(self) -> bool:
        return all(slot.filled for slot in self._slots)

    @property
    def is_ready(self) -> bool:
        """True when a commit would produce a valid shape."""
        return self.origin is not None and self.all_filled

    # -- transitions -------------------------------------------------------

    def reset(self) -> None:
        """Back to IDLE, discarding everything. Never touches a layer."""
        for slot in self._slots:
            slot.value = None
        self.state = ToolState.IDLE
        self.origin = None
        self.second = None
        self.cursor = None
        self.vertices = []
        self.active_slot = 0
        self.message = ""

    def cancel(self) -> bool:
        """Escape. Returns True if a construction was actually discarded."""
        had_work = self.state != ToolState.IDLE
        self.reset()
        return had_work

    def set_origin(self, x: float, y: float) -> str:
        self.origin = (float(x), float(y))
        if self.multi_vertex:
            # PICK_ORIGIN doubles as "collecting more vertices" here: the
            # construction is open-ended and only the operator closes it.
            self.vertices = [self.origin]
            self.state = ToolState.PICK_ORIGIN
        else:
            self.state = (ToolState.PICK_SECOND if self.accepts_second_click
                          else ToolState.PICK_ORIGIN)
        self.active_slot = 0
        self.message = self._prompt()
        return self.state

    def set_second(self, x: float, y: float) -> str:
        """A second map click. Derives the constraint values from geometry."""
        if self.origin is None:
            raise InvalidInputError(
                "second point before an origin",
                user_message="Indica prima il punto di origine.")
        self.second = (float(x), float(y))
        if self.multi_vertex:
            # Keep collecting: a polyline is finished by the operator
            # (Enter / double click), never by running out of slots.
            self.vertices.append(self.second)
            self.state = ToolState.PICK_ORIGIN
            self.message = self._prompt()
            return self.state
        self.derive_from_second()
        self.state = ToolState.PREVIEW if self.all_filled else ToolState.PICK_SECOND
        self.message = self._prompt()
        return self.state

    def derive_from_second(self) -> None:
        """Fill the slots from origin + second point. Overridden per tool."""

    def hover(self, x: float, y: float) -> None:
        """Track the cursor. Pure bookkeeping -- builds nothing."""
        self.cursor = (float(x), float(y))

    def submit(self, text: str) -> str:
        """Feed one dynamic-input token into the construction.

        Parsing and resolution are delegated to ``cad.dynamic_input``, which is
        already tested; nothing is re-parsed here.

        Point-valued tokens (``#x,y``, ``@dx,dy``, ``@25<37``) place a point:
        the origin if there is not one yet, otherwise the second point, which
        the tool turns into its own constraints. Scalar tokens (a length or an
        angle) fill the next empty slot.
        """
        token = di.parse(text, self.length_unit, self.angle_unit)

        if token.kind == di.KIND_ABSOLUTE:
            point = di.resolve(token)
            if self.origin is None:
                return self.set_origin(float(point[0]), float(point[1]))
            return self.set_second(float(point[0]), float(point[1]))

        if token.kind in (di.KIND_RELATIVE, di.KIND_POLAR):
            if self.origin is None:
                raise InvalidInputError(
                    "{0} token before an origin".format(token.kind),
                    user_message="Serve un punto di origine per un input "
                                 "relativo o polare.",
                    hint="Clicca sulla mappa, oppure usa #x,y.")
            # With a previous segment the polar angle is measured from it;
            # without one it falls back to grid north, exactly as
            # dynamic_input documents. A single-point tool never has a
            # previous segment, so its behaviour is unchanged.
            point = di.resolve(token, last_point=self.anchor_point(),
                               last_azimuth_deg=self.last_azimuth_deg())
            return self.set_second(float(point[0]), float(point[1]))

        if self.origin is None:
            raise InvalidInputError(
                "typed constraint before an origin",
                user_message="Indica prima il punto di origine sulla mappa, "
                             "oppure usa #x,y.")
        slot = self._next_slot()
        if slot is None:
            raise InvalidInputError(
                "no constraint left to fill",
                user_message="Tutti i parametri sono gia' definiti: premi "
                             "Invio per confermare.")

        if slot.kind == di.KIND_LENGTH:
            if token.kind != di.KIND_LENGTH:
                raise InvalidInputError(
                    "expected a length for {0}, got {1}".format(slot.name,
                                                                token.kind),
                    user_message="'{0}' richiede una lunghezza (es. 30 oppure "
                                 "30m).".format(slot.label))
            slot.value = token.length_m
        elif slot.kind == di.KIND_ANGLE:
            if token.kind != di.KIND_ANGLE:
                raise InvalidInputError(
                    "expected an angle for {0}, got {1}".format(slot.name,
                                                                token.kind),
                    user_message="'{0}' richiede un angolo (es. 15d).".format(
                        slot.label))
            slot.value = token.angle_deg
        else:
            raise InvalidInputError(
                "unsupported slot kind {0!r}".format(slot.kind),
                user_message="Tipo di vincolo non gestito.")

        self.active_slot = min(self.active_slot + 1, len(self._slots))
        self.state = (ToolState.PREVIEW if self.all_filled
                      else ToolState.TYPE_CONSTRAINT)
        self.message = self._prompt()
        return self.state

    def next_slot(self) -> None:
        """Tab: move focus to the next slot without filling this one."""
        if self._slots:
            self.active_slot = (self.active_slot + 1) % len(self._slots)
            self.message = self._prompt()

    def confirm(self) -> str:
        """Enter on a fully constrained shape. Moves to COMMIT."""
        if not self.is_ready:
            raise InvalidInputError(
                "confirm on an incomplete construction",
                user_message="Mancano ancora dei parametri: {0}.".format(
                    ", ".join(s.label for s in self._slots if not s.filled)))
        self.state = ToolState.COMMIT
        return self.state

    def _next_slot(self) -> Optional[ConstraintSlot]:
        for index in range(self.active_slot, len(self._slots)):
            if not self._slots[index].filled:
                self.active_slot = index
                return self._slots[index]
        for index, slot in enumerate(self._slots):
            if not slot.filled:
                self.active_slot = index
                return slot
        return None

    def _prompt(self) -> str:
        pending = self._next_slot()
        if pending is not None:
            return "Inserisci {0}".format(pending.label)
        return "Invio per confermare"

    # -- HUD ---------------------------------------------------------------

    def hud_lines(self) -> "list[str]":
        """Text for the on-canvas readout. Pure, so tests can assert on it."""
        lines = ["{0} - {1}".format(self.title, self.state)]
        if self.cursor is not None:
            lines.append("X {0:.3f}  Y {1:.3f}".format(*self.cursor))
        if self.origin is not None and self.cursor is not None:
            dx = self.cursor[0] - self.origin[0]
            dy = self.cursor[1] - self.origin[1]
            distance = math.hypot(dx, dy)
            lines.append("dX {0:+.3f}  dY {1:+.3f}".format(dx, dy))
            lines.append("L {0:.3f} m".format(distance))
            if distance > GEOM_EPS_M:
                lines.append("Az {0:.4f} deg".format(
                    math.degrees(math.atan2(dx, dy)) % 360.0))
        for slot in self._slots:
            if slot.filled:
                lines.append("{0}: {1:.3f}".format(slot.label, slot.value))
        if self.message:
            lines.append(self.message)
        return lines


# --------------------------------------------------------------------------
# Map tool
# --------------------------------------------------------------------------

def _qt():
    """Import Qt lazily, and only the scoped enum forms.

    Kept in a function so importing this module does not require a Qt
    application to exist -- the pure session above is importable on its own.
    """
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QColor
    return Qt, QColor


def rubber_band_geometry_type(is_polygon: bool):
    """Geometry-type enum accepted by ``QgsRubberBand``.

    ``QgsWkbTypes.PolygonGeometry`` is present on 3.34, 3.40 and 4.0 (verified);
    ``Qgis.GeometryType`` only arrived in 3.30, so the old name is tried first
    and is the one that actually gets used on every supported release.
    """
    from qgis.core import QgsWkbTypes

    if hasattr(QgsWkbTypes, "PolygonGeometry"):
        return (QgsWkbTypes.PolygonGeometry if is_polygon
                else QgsWkbTypes.LineGeometry)
    from qgis.core import Qgis                                  # noqa: PLC0415
    return Qgis.GeometryType.Polygon if is_polygon else Qgis.GeometryType.Line


class BaseCadTool:
    """Mixin holding everything a CAD map tool does besides being a QgsMapTool.

    Kept separate from ``QgsMapTool`` so the commit path can be exercised
    without a canvas: :class:`CadMapTool` below combines the two.
    """

    def __init__(self, session: Optional[CadToolSession] = None, iface=None,
                 canvas=None):
        """``session`` is optional on purpose.

        When this class is mixed into ``QgsMapTool`` (see :class:`CadMapTool`),
        sip's ``QgsMapTool.__init__`` walks the MRO and calls this with no
        arguments. Requiring ``session`` here makes that call a TypeError, so
        the setup is factored into :meth:`init_cad`, which the map tool invokes
        explicitly once the Qt half is constructed.
        """
        self.init_cad(session, iface, canvas)

    def init_cad(self, session: Optional[CadToolSession], iface=None,
                 canvas=None):
        self.session = session
        self.iface = iface
        self._canvas = canvas
        self.target_layer = None
        self._band = None
        self._marker = None
        self._typed = ""
        #: Mirrors settings "snap/enabled"; refreshed on activate().
        self.snap_enabled = True
        #: Diagnostics the tests assert on: neither may move during a hover.
        self.geometry_builds = 0
        self.canvas_refreshes = 0

    # -- CRS ---------------------------------------------------------------

    def work_crs(self, canvas_crs, extent=None):
        """Resolve the metric working CRS, refusing degrees (spec P1)."""
        decision = crs_svc.resolve_work_crs(canvas_crs, extent)
        return decision

    def to_work(self, x: float, y: float, canvas_crs, work_crs):
        """Canvas CRS -> work CRS for a single picked point."""
        if canvas_crs == work_crs:
            return float(x), float(y)
        from qgis.core import QgsPointXY                        # noqa: PLC0415
        transform = crs_svc.make_transform(canvas_crs, work_crs)
        if transform is None:
            return float(x), float(y)
        point = transform.transform(QgsPointXY(float(x), float(y)))
        return point.x(), point.y()

    # -- commit ------------------------------------------------------------

    def commit(self, layer, work_crs, layer_crs=None):
        """Build the geometry and write one feature inside one undo command.

        This is the only place a ``QgsGeometry`` is constructed, and the only
        place a feature is added. Returns the created ``QgsFeature``.
        """
        from qgis.core import QgsFeature                        # noqa: PLC0415

        from ...core import undo

        if layer is None:
            raise LayerError(
                "no target layer",
                user_message="Nessun layer di destinazione selezionato.",
                hint="Scegli un layer nel pannello GeoCad UAV.")
        if not self.session.is_ready:
            raise InvalidInputError(
                "commit on an incomplete construction",
                user_message="La forma non e' ancora completamente definita.")

        params = self.session.build_params()
        geometry, record = pr.build(self.session.tool_id, params,
                                    work_crs.authid() if work_crs else "")
        self.geometry_builds += 1

        if layer_crs is not None and work_crs is not None and layer_crs != work_crs:
            geometry = crs_svc.transform_geometry(geometry, work_crs, layer_crs)

        # The CAD columns an operator actually reads in the attribute table.
        # Added here because commit() is the one place a feature is written;
        # a layer that refuses them still gets its geometry, with a warning.
        attributes = pa.record_to_attributes(record)
        self.attribute_warning = ""
        if lf.ensure_cad_fields(layer) is None:
            self.attribute_warning = (
                "Il layer '{0}' non accetta nuovi campi: la geometria e' "
                "stata scritta, gli attributi CAD no.".format(layer.name()))
        else:
            attributes.update(lf.cad_attributes(record, layer))

        fields = layer.fields()
        feature = QgsFeature(fields)
        feature.setGeometry(geometry)
        for name, value in attributes.items():
            index = fields.indexOf(name)
            if index >= 0:
                feature.setAttribute(index, value)

        with undo.edit_command(layer, "GeoCad: {0}".format(self.session.title)):
            if not layer.addFeature(feature):
                raise LayerError(
                    "addFeature failed on {0}".format(layer.name()),
                    user_message="Inserimento della geometria non riuscito sul "
                                 "layer '{0}'.".format(layer.name()))
        layer.updateExtents()
        self.session.reset()
        return feature

    # -- rubber band -------------------------------------------------------

    def ensure_band(self, canvas):
        """Create the rubber band once, on first use."""
        if self._band is not None:
            return self._band
        from qgis.core import QgsWkbTypes                       # noqa: PLC0415
        from qgis.gui import QgsRubberBand, QgsVertexMarker     # noqa: PLC0415

        Qt, QColor = _qt()
        is_polygon = self.session.geometry_type != "LineString"
        self._band = QgsRubberBand(canvas, rubber_band_geometry_type(is_polygon))
        self._band.setColor(QColor(255, 140, 0, 200))
        self._band.setFillColor(QColor(255, 140, 0, 40))
        self._band.setWidth(2)
        self._band.setLineStyle(Qt.PenStyle.DashLine)
        self._marker = QgsVertexMarker(canvas)
        self._marker.setIconType(QgsVertexMarker.ICON_CROSS)
        self._marker.setColor(QColor(255, 140, 0))
        self._marker.setPenWidth(2)
        self._marker.hide()
        return self._band

    def update_band(self, canvas, to_canvas=None):
        """Redraw the preview from the session's points.

        Points only: no ``QgsGeometry`` is created here, which is what keeps a
        mouse move cheap and keeps rule 2 satisfiable.
        """
        if self._band is None:
            self.ensure_band(canvas)
        from qgis.core import QgsPointXY                        # noqa: PLC0415

        points = self.session.preview_points()
        is_polygon = self.session.geometry_type != "LineString"
        self._band.reset(rubber_band_geometry_type(is_polygon))
        if points is None or len(points) < 2:
            return 0
        for point in np.asarray(points, dtype=float):
            x, y = float(point[0]), float(point[1])
            if to_canvas is not None:
                x, y = to_canvas(x, y)
            self._band.addPoint(QgsPointXY(x, y), False)
        self._band.updatePosition()
        self._band.show()
        return len(points)

    def clear_band(self):
        if self._band is not None:
            is_polygon = self.session.geometry_type != "LineString"
            self._band.reset(rubber_band_geometry_type(is_polygon))
            self._band.hide()
        if self._marker is not None:
            self._marker.hide()

    def destroy_band(self, canvas):
        """Remove the rubber band from the scene. Called on deactivate."""
        for item in (self._band, self._marker):
            if item is None:
                continue
            try:
                scene = canvas.scene() if canvas is not None else None
                if scene is not None:
                    scene.removeItem(item)
            except (AttributeError, RuntimeError):
                pass
        self._band = None
        self._marker = None


# --------------------------------------------------------------------------
# QgsMapTool adapter
# --------------------------------------------------------------------------

from qgis.gui import QgsMapTool                                 # noqa: E402


class CadMapTool(QgsMapTool, BaseCadTool):
    """Turns canvas events into :class:`CadToolSession` transitions.

    Everything numeric happens in the session or in the frozen engine; this
    class only routes events, paints the preview and calls
    :meth:`BaseCadTool.commit`.
    """

    def __init__(self, canvas, session: CadToolSession, iface=None,
                 layer_provider=None):
        # QgsMapTool.__init__ reaches BaseCadTool.__init__ through the MRO with
        # no arguments, which is why that signature is all-optional; the real
        # setup happens in init_cad immediately afterwards.
        QgsMapTool.__init__(self, canvas)
        self.init_cad(session, iface=iface, canvas=canvas)
        #: Callable returning the layer to write into; supplied by the dock so
        #: the tool never has to guess or invent a destination.
        self.layer_provider = layer_provider
        self._work_decision = None
        self._canvas_crs = None
        self._to_canvas = None
        self._blocked_reason = ""

    # -- lifecycle ---------------------------------------------------------

    def activate(self):
        QgsMapTool.activate(self)
        Qt, _ = _qt()
        try:
            self.setCursor(Qt.CursorShape.CrossCursor)
        except (AttributeError, TypeError):
            pass                       # a cursor is cosmetic; never fatal
        try:
            from ...settings import settings as _settings      # noqa: PLC0415
            self.snap_enabled = bool(_settings.get("snap/enabled"))
        except Exception:                                       # noqa: BLE001
            self.snap_enabled = True
        self._resolve_frames()
        self.session.reset()

    def deactivate(self):
        self.session.reset()
        self.clear_band()
        self.destroy_band(self.canvas())
        QgsMapTool.deactivate(self)

    def flags(self):
        """Mark this as an edit tool where the enum exists.

        ``QgsMapTool.EditTool`` is present on 3.34, 3.40 and 4.0 (verified);
        ``Qgis.MapToolFlag`` is *not* present on 3.40, so it is not used.
        """
        if hasattr(QgsMapTool, "EditTool"):
            return QgsMapTool.Flags(QgsMapTool.EditTool)
        return QgsMapTool.flags(self)

    # -- coordinate frames -------------------------------------------------

    def _resolve_frames(self):
        """Decide the working CRS once per activation (spec P1)."""
        self._blocked_reason = ""
        self._to_canvas = None
        canvas = self.canvas()
        if canvas is None:
            return
        self._canvas_crs = canvas.mapSettings().destinationCrs()
        try:
            self._work_decision = self.work_crs(self._canvas_crs,
                                                canvas.extent())
        except GeoCadError as exc:
            self._work_decision = None
            self._blocked_reason = exc.formatted()
            self._warn(self._blocked_reason)
            return
        if self._work_decision.transform_required:
            self._warn(self._work_decision.reason)
        work = self._work_decision.work_crs
        if work != self._canvas_crs:
            transform = crs_svc.make_transform(work, self._canvas_crs)
            if transform is not None:
                from qgis.core import QgsPointXY                # noqa: PLC0415

                def _to_canvas(x, y, _t=transform):
                    point = _t.transform(QgsPointXY(float(x), float(y)))
                    return point.x(), point.y()

                self._to_canvas = _to_canvas

    @property
    def work_crs_object(self):
        return self._work_decision.work_crs if self._work_decision else None

    def _warn(self, text):
        if self.iface is not None and text:
            try:
                from qgis.core import Qgis                      # noqa: PLC0415
                self.iface.messageBar().pushMessage(
                    "GeoCad UAV", text, level=Qgis.Warning, duration=6)
            except Exception:                                   # noqa: BLE001
                pass

    def _map_to_work(self, map_point):
        return self.to_work(map_point.x(), map_point.y(), self._canvas_crs,
                            self.work_crs_object)

    def picked_point(self, event):
        """Map point of a click, snapped when the operator has snapping on.

        Uses ``QgsMapMouseEvent.snapPoint()`` -- the same call QGIS's own
        digitising tools make. It routes through the canvas's
        ``QgsSnappingUtils``, so the project's snapping configuration and its
        on-canvas indicators behave exactly as they do for native editing.
        ``core.snap`` is deliberately *not* used here: it would be a second
        path to the same engine. Its extra construction snaps (grid,
        perpendicular, tangent) remain unwired and are scheduled separately.
        """
        if self.snap_enabled:
            try:
                return event.snapPoint()
            except (AttributeError, TypeError):
                pass            # not a QgsMapMouseEvent (synthetic test event)
        return event.mapPoint()

    # -- mouse -------------------------------------------------------------

    def canvasMoveEvent(self, event):                           # noqa: N802
        """Hover. Draws only: no geometry, no canvas refresh (rules 2 and 3)."""
        if self._work_decision is None:
            return
        x, y = self._map_to_work(self.picked_point(event))
        self.session.hover(x, y)
        if self.session.state != ToolState.IDLE:
            self.update_band(self.canvas(), self._to_canvas)
        self._paint_hud()

    def canvasReleaseEvent(self, event):                        # noqa: N802
        Qt, _ = _qt()
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
            if self.session.state == ToolState.IDLE:
                self.session.set_origin(x, y)
            elif self.session.state == ToolState.PREVIEW:
                self._do_commit()
                return
            elif self.session.accepts_second_click:
                self.session.set_second(x, y)
                if self.session.state == ToolState.PREVIEW:
                    self._do_commit()
                    return
        except GeoCadError as exc:
            self._warn(exc.formatted())
            return
        self.update_band(self.canvas(), self._to_canvas)
        self._paint_hud()

    def canvasDoubleClickEvent(self, event):                    # noqa: N802
        """Finish a polyline. Verified hook: QgsMapTool.canvasDoubleClickEvent
        exists on 3.34, 3.40 and 4.0; there is no QgsMapTool.DoubleClick flag.
        """
        if self.session.multi_vertex and len(self.session.vertices) >= 2:
            self._do_commit()

    # -- keyboard ----------------------------------------------------------

    def keyPressEvent(self, event):                             # noqa: N802
        Qt, _ = _qt()
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._escape()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._enter()
            return
        if key == Qt.Key.Key_Tab:
            self.session.next_slot()
            self._paint_hud()
            return
        if key == Qt.Key.Key_Backspace:
            if self._typed:
                self._typed = self._typed[:-1]
            elif self.session.multi_vertex:
                self.session.remove_last_vertex()
                self.update_band(self.canvas(), self._to_canvas)
            self._paint_hud()
            return
        text = event.text()
        if text and text.isprintable():
            self._typed += text
            self._paint_hud()

    def _enter(self):
        try:
            if self._typed.strip():
                self.session.submit(self._typed.strip())
                self._typed = ""
            elif self.session.is_ready:
                self._do_commit()
                return
            else:
                return
        except GeoCadError as exc:
            self._warn(exc.formatted())
            self._typed = ""
            return
        self.update_band(self.canvas(), self._to_canvas)
        self._paint_hud()

    def _escape(self):
        """Escape: rubber band gone, zero features written, back to IDLE."""
        self.session.cancel()
        self._typed = ""
        self.clear_band()
        self._paint_hud()

    def _do_commit(self):
        layer = self.layer_provider() if self.layer_provider else self.target_layer
        try:
            self.session.confirm()
            self.commit(layer, self.work_crs_object,
                        layer.crs() if layer is not None else None)
        except GeoCadError as exc:
            self._warn(exc.formatted())
            self.session.state = ToolState.PREVIEW
            return
        self._warn(getattr(self, "attribute_warning", ""))
        self._typed = ""
        self.clear_band()
        self._paint_hud()

    # -- HUD ---------------------------------------------------------------

    def _paint_hud(self):
        """Push the readout to the status bar. Never refreshes the canvas."""
        if self.iface is None:
            return
        lines = list(self.session.hud_lines())
        if self._typed:
            lines.append("> {0}".format(self._typed))
        try:
            self.iface.mainWindow().statusBar().showMessage(
                "  |  ".join(lines))
        except Exception:                                       # noqa: BLE001
            pass
