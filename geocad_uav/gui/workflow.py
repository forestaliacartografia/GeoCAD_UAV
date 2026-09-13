"""
The reforestation workspace: a workflow on the left, its context on the right.

Eleven steps, in the order the work is actually done, and one panel at a time
on the right so the operator is never looking at controls for a decision they
have not reached. The map keeps the middle of the screen, which is the point
of the whole arrangement.

Architecture, deliberately thin:

    ProjectState        the single source of truth -- area, terrain,
                        constraints, species, scheme, result, cadastre
          |  signals
    panels              collect input, show numbers, call a service
          |  calls
    forest.reforestation / io.cadastre / io.dem_source   the services

No geometry is computed in this module, no WFS request is built here, no
plant is placed here. Every panel reaches for the module that already does
that job; what lives here is which button calls it and where its answer is
displayed. A widget that starts doing arithmetic is a widget that will
disagree with the engine.

The step list and the stack are connected the obvious way, with one
exception written down rather than hidden: *Specie* and *Sesti* are two steps
of the workflow but one panel with two tabs, because splitting them means
duplicating the preview that both of them change. The controller therefore
maps a row to a page and, where it matters, to a tab.
"""

from __future__ import annotations

import json
import math
import os
from typing import Optional

from qgis.core import (Qgis, QgsApplication,
                       QgsCoordinateReferenceSystem, QgsGeometry, QgsProject,
                       QgsWkbTypes)
from qgis.PyQt.QtCore import QObject, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QPixmap
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                                 QFileDialog, QToolBar,
                                 QDockWidget, QDoubleSpinBox, QFormLayout,
                                 QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                                 QLineEdit, QListWidget, QListWidgetItem,
                                 QMessageBox,
                                 QPushButton, QScrollArea, QSlider,
                                 QSpinBox,
                                 QStackedWidget, QTabWidget, QTableWidget,
                                 QTableWidgetItem, QTextBrowser, QVBoxLayout,
                                 QWidget)

from ..core import grid as grid_mod
from ..core.errors import GeoCadError
from ..forest.reforestation import area as area_mod
from ..forest.reforestation import composition as composition_mod
from ..forest.reforestation import constraints as constraints_mod
from ..forest.reforestation import curves as curves_mod
from ..forest.reforestation import density as density_mod
from ..forest.reforestation import natural as natural_mod
from ..forest.reforestation import orient as orient_mod
from ..forest.reforestation import spacing as spacing_mod
from ..forest.reforestation import species as species_mod
from ..forest.reforestation import symbology as symbology_mod
from ..forest.reforestation import terrain as terrain_mod
from ..forest.reforestation import zones as zones_mod
from ..io import cadastre as cadastre_mod
from ..io import cartography as carto_mod
from ..io import documents as docs_mod
from ..io import project_file as project_mod
from ..uav import forest_link as forest_link_mod
from . import mission_report as mission_report_mod
from . import charts as charts_mod
from . import map_layers as map_layers_mod
from . import theme as theme_mod
from .export_panel import ExportPanel
from .mission_player import MissionPlayer, RATES as PLAYER_RATES
from .preview import PlanPreview
from .uav_panel import UavPanel
from . import uav_panel as uav_mod
from .map_layers import ProjectLayers

M2_PER_HA = 10_000.0

#: Grown around the project before a DEM window is read, so the slope at the
#: very edge comes from real neighbours and not from the padding.
DEM_MARGIN_M = 50.0

#: The constraint key the slope limit writes into. A constraint like any
#: other, so it is subtracted, reported and drawn by the same machinery.
SUITABILITY_KEY = "idoneita"

#: How many operator actions can be taken back. Snapshots are the whole
#: project serialised, so this is a memory budget as much as a policy.
UNDO_DEPTH = 25

#: The workflow, in order. ``key`` is what the state reports status against.
STEPS = (
    ("area", "1. Area"),
    ("terrain", "2. Terreno"),
    ("constraints", "3. Vincoli"),
    ("zones", "4. Zone"),
    ("species", "5. Specie"),
    ("scheme", "6. Sesti"),
    ("orientation", "7. Orientamento"),
    ("generate", "8. Genera"),
    ("natural", "9. Naturaliforme"),
    ("optimise", "10. Ottimizza"),
    ("verify", "11. Verifica"),
    ("edit", "12. Editing"),
    ("cartography", "13. Cartografia"),
    ("outputs", "14. Elaborati"),
)

#: The flight workflow, worked after the planting one or on its own. The
#: widgets of every one of these steps belong to a single UavPanel: they are
#: laid out as six pages here instead of one long form, and there is no
#: second planner behind them.
UAV_STEPS = (
    (uav_mod.STEP_AREA, "V1. Area del volo"),
    (uav_mod.STEP_HARDWARE, "V2. Hardware e GSD"),
    (uav_mod.STEP_FLIGHT, "V3. Parametri di volo"),
    (uav_mod.STEP_SAFETY, "V4. Sicurezza e ostacoli"),
    (uav_mod.STEP_SIMULATION, "V5. Simulazione"),
    (uav_mod.STEP_EXPORT, "V6. Export"),
)

#: Everything the step list shows, in order.
ALL_STEPS = STEPS + UAV_STEPS

#: Where a section title goes, by index into ALL_STEPS.
SECTIONS = {0: "RIMBOSCHIMENTO", len(STEPS): "VOLO UAV"}

#: Visual state of one step. Derived from the model, never set by a widget
#: for its own convenience.
NOT_STARTED = "not_started"
ACTIVE = "active"
DONE = "done"
WARNING = "warning"
ERROR = "error"

STATE_COLORS = {
    NOT_STARTED: "#9e9e9e",
    ACTIVE: "#1e88e5",
    DONE: "#2e7d32",
    WARNING: "#ef6c00",
    ERROR: "#c62828",
}

#: Constraint kinds offered by the panel, with the distance each starts at.
#: Data, in one place: adding a kind is a line here, not a new widget.
CONSTRAINT_KINDS = (
    ("confine", "Confini", 0.0),
    ("strada", "Strade", 5.0),
    ("pista", "Piste", 3.0),
    ("fabbricato", "Fabbricati", 10.0),
    ("corso_acqua", "Corsi d'acqua", 10.0),
    ("fosso", "Fossi", 5.0),
    ("elettrodotto", "Elettrodotti", 3.0),
    ("infrastruttura", "Infrastrutture", 5.0),
    ("recinzione", "Recinzioni", 1.0),
    ("personalizzato", "Personalizzato", 0.0),
)

DASH = "--"

#: Positions of the simulator's time cursor. A thousand steps over a flight
#: of any length is finer than the eye, and an integer slider is the only
#: kind Qt has.
SLIDER_STEPS = 1000

#: The footprint overlay: a translucent fill, so two frames over the same
#: ground read darker than one. The value is the overlap, not the colour.
FOOTPRINT_FILL = "255,180,60,55"
FOOTPRINT_OUTLINE = "200,120,20,120"


def tr(text: str) -> str:
    from qgis.PyQt.QtCore import QCoreApplication              # noqa: PLC0415

    return QCoreApplication.translate("GeoCadWorkflow", text)


def ha(value_m2) -> str:
    try:
        return "{0:,.2f} ha".format(float(value_m2) / M2_PER_HA)
    except (TypeError, ValueError):
        return DASH


def swatch(colour: str, size: int = 12) -> QIcon:
    """A small filled square, for a species row or a legend entry."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(colour) if colour else QColor("#9e9e9e"))
    return QIcon(pixmap)


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------

class ProjectState(QObject):
    """Everything the panels read and write, in one object.

    Panels do not talk to each other: they change the state and the state
    says so. That is what keeps the surface shown in step 3 the same number
    the generator uses in step 8.
    """

    changed = pyqtSignal()
    statusChanged = pyqtSignal(str, str)        # step key, visual state
    cadastralDataReady = pyqtSignal(dict)
    #: Asked for by a panel whose settings changed the plan -- the
    #: naturaliform one. Panels do not call each other: the project says a
    #: new generation is due and whoever owns the generator answers.
    regenerateRequested = pyqtSignal()
    #: Asked for by a panel that has finished its job and is handing the
    #: operator on to another step. The workflow list is what answers: a
    #: panel never reaches into it.
    stepRequested = pyqtSignal(str)

    def __init__(self, iface=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        #: The project on the map. Every geometry the model holds is drawn
        #: here, and nowhere else: a panel never adds a layer of its own.
        self.layers = ProjectLayers(iface)
        #: What a plan looks like on the canvas before it is committed.
        self.preview = PlanPreview(iface)
        self.area = None                        # ReforestationArea
        self.crs = None                         # QgsCoordinateReferenceSystem
        self.terrain = None                     # TerrainAnalysis
        #: Id of the raster the terrain was read from, when it came from a
        #: layer of the project. The flight planner offers the operator that
        #: same layer; the warped working grid is a temporary file the
        #: project never adopted and no layer combo would list it.
        self.dem_layer_id = ""
        #: The contour lines extracted from the DEM and cut to the usable
        #: surface. They are rows an operator can see before deciding to
        #: plant along them, which is why they live on the project and not
        #: inside the generator.
        self.contours = []                      # [curves_mod.ContourRow]
        self.contour_interval_m = curves_mod.DEFAULT_INTERVAL_M
        self.constraints = constraints_mod.ConstraintSet()
        self.catalog = species_mod.SpeciesCatalog.from_layer(
            species_mod.SpeciesCatalog.memory_layer())
        self.shares = []                        # [(key, percent)]
        self.spec = spacing_mod.SlopeSpacing(plant_distance_m=3.0,
                                             row_distance_m=3.0)
        self.zones = zones_mod.ZoneSet()        # sub-areas, each planted alone
        self.natural = natural_mod.NaturalSettings()
        self.natural_outcome = None
        self.glades = []
        self.result = None                      # SlopeGridResult
        self.composition = None
        self.cadastre = None                    # CadastralResult
        self.cadastre_task = None               # the query in flight
        self.plants_layer = None
        #: How many plants the operator placed by hand, and whether the plan
        #: on the layer has diverged from the one the generator produced.
        self.added_plants = 0
        self.edited = False
        #: The sheet, once it has been composed. Kept because the report
        #: quotes its scale and the export writes it out again.
        self.layout = None
        self.layout_spec = carto_mod.LayoutSpec()
        self.scenarios = []
        self.anomalies = []
        #: Set by the Verify panel when it actually ran. An empty anomaly
        #: list means "no anomalies found", not "never looked".
        self.verified = False
        #: Set when the Orientation panel applied its answer to the scheme.
        self.orientation_applied = False
        #: Set when the operator actually chose a scheme. The spec always
        #: holds a default, and a default nobody looked at is not a decision.
        self.scheme_chosen = False
        #: Set when the operator asked for glades and they were placed.
        self.glades_placed = False
        #: The step being looked at. ACTIVE is *where the operator is*, and
        #: DONE is *what the model holds*: two different things, so the one
        #: the operator is standing on keeps its marker until it earns a
        #: better one.
        self.current_step = STEPS[0][0]
        #: The flight planner and its export tab, once the context dock has
        #: built them. The only thing the model does with either is read its
        #: state to mark the six flight steps; it never drives them.
        self.uav = None
        self.uav_export = None
        #: Where the project was last written, and whether it has changed
        #: since. Both are what a Save button has to know.
        self.path = ""
        self.dirty = False
        #: Snapshots of the whole project, the last one being what is on
        #: screen now. One entry per operator action, not per keystroke:
        #: undo should take back a decision, not half of one.
        self._undo = []
        self._redo = []
        self._status = {key: NOT_STARTED for key, _label in ALL_STEPS}

    # -- the project as a whole --------------------------------------------

    def clear(self) -> None:
        """Empty the project, keeping nothing but the catalogue.

        The catalogue stays because it is a reference table, not a decision:
        an operator starting a second project in the same session should not
        have to retype the species they added to the first.
        """
        self.layers.remove_all()
        self.preview.clear()
        if self.terrain is not None:
            self.terrain.release()
        self.area = None
        self.crs = None
        self.terrain = None
        self.contours = []
        self.contour_interval_m = curves_mod.DEFAULT_INTERVAL_M
        self.constraints = constraints_mod.ConstraintSet()
        self.shares = []
        self.spec = spacing_mod.SlopeSpacing(plant_distance_m=3.0,
                                             row_distance_m=3.0)
        self.zones = zones_mod.ZoneSet()
        self.natural = natural_mod.NaturalSettings()
        self.natural_outcome = None
        self.glades = []
        self.result = None
        self.composition = None
        self.cadastre = None
        self.plants_layer = None
        self.added_plants = 0
        self.edited = False
        self.layout = None
        self.layout_spec = carto_mod.LayoutSpec()
        self.scenarios = []
        self.anomalies = []
        self.verified = False
        self.orientation_applied = False
        self.scheme_chosen = False
        self.glades_placed = False
        self.path = ""
        self.dirty = False
        self._undo = []
        self._redo = []
        self.refresh_status()

    # -- taking it back ----------------------------------------------------

    def snapshot(self) -> str:
        """The project as one comparable string."""
        return json.dumps(project_mod.to_dict(self), sort_keys=True,
                          ensure_ascii=False)

    def checkpoint(self) -> bool:
        """Remember the project as it stands, if it actually changed.

        Called at the operator's actions -- an area taken, constraints
        applied, zones cut, a stand generated, an edit committed -- and not
        at every spin box, so that one press of Undo takes back one thing
        the operator did.
        """
        current = self.snapshot()
        if self._undo and self._undo[-1] == current:
            return False
        self._undo.append(current)
        del self._undo[:-UNDO_DEPTH]
        self._redo = []
        self.dirty = True
        self.changed.emit()
        return True

    def reset_history(self) -> None:
        self._undo = [self.snapshot()]
        self._redo = []

    @property
    def can_undo(self) -> bool:
        return len(self._undo) >= 2

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _restore(self, snapshot: str) -> None:
        project_mod.from_dict(self, json.loads(snapshot))
        self.materialise_plants()
        self.draw()
        self.refresh_status()

    def materialise_plants(self):
        """Put the plan on the map as a layer, or take the old one away.

        A project read from a file, or stepped back to with Undo, has a plan
        and no layer. Without this the operator sees the area and the zones
        come back while the plants they had generated stay exactly as they
        were -- or, worse, the plants of a plan that no longer exists stay
        drawn over the one that does.
        """
        previous = self.plants_layer
        self.plants_layer = None
        self.layers.remove("plants")
        if previous is not None:
            try:
                QgsProject.instance().removeMapLayer(previous.id())
            except (AttributeError, RuntimeError):
                pass
        if self.result is None or not self.result.plants or self.crs is None:
            return None
        layer = spacing_mod.plants_layer(self.result, self.crs.authid(),
                                         name=tr("Piante"),
                                         zone=tr("Progetto"))
        QgsProject.instance().addMapLayer(layer)
        self.plants_layer = layer
        self.layers.adopt("plants", layer)
        self.layers.refresh_canvas()
        return layer

    def undo(self) -> bool:
        """Go back one action. The plants layer is redrawn from the plan."""
        if not self.can_undo:
            return False
        # clear() empties the history, so the stacks are taken out of the
        # way and put back around the restore.
        undo, redo = self._undo, self._redo
        current = undo.pop()
        self._restore(undo[-1])
        redo.append(current)
        self._undo, self._redo = undo, redo
        self.dirty = True
        self.changed.emit()
        return True

    def redo(self) -> bool:
        if not self.can_redo:
            return False
        undo, redo = self._undo, self._redo
        snapshot = redo.pop()
        self._restore(snapshot)
        undo.append(snapshot)
        self._undo, self._redo = undo, redo
        self.dirty = True
        self.changed.emit()
        return True

    def save_to(self, path: str) -> str:
        """Write the project, and remember where."""
        written = project_mod.save(self, path)
        self.path = written
        self.dirty = False
        self.refresh_status()
        return written

    def load_from(self, path: str) -> "list[str]":
        """Read a project, draw it, and remember where it came from."""
        warnings = project_mod.load(self, path)
        self.path = path
        self.dirty = False
        self.reset_history()
        self.materialise_plants()
        self.draw()
        self.refresh_status()
        return warnings

    # -- status ------------------------------------------------------------

    def status(self, key: str) -> str:
        return self._status.get(key, NOT_STARTED)

    def set_status(self, key: str, state: str) -> None:
        if state == NOT_STARTED and key == self.current_step:
            state = ACTIVE
        if self._status.get(key) != state:
            self._status[key] = state
            self.statusChanged.emit(key, state)

    def set_current_step(self, key: str) -> None:
        """Remember where the operator is, and re-mark the two steps."""
        previous = self.current_step
        self.current_step = key
        if previous != key:
            self.refresh_status()

    def refresh_status(self) -> None:
        """Derive every step's state from what the model actually holds."""
        self.set_status("area", DONE if self.area is not None else NOT_STARTED)
        self.set_status("terrain",
                        DONE if self.terrain is not None else NOT_STARTED)
        has_features = any(rule.n_features
                           for rule in self.constraints.rules.values())
        self.set_status("constraints", DONE if has_features else NOT_STARTED)
        self.set_status("zones", DONE if len(self.zones) else NOT_STARTED)
        self.set_status("species", DONE if self.shares else NOT_STARTED)
        self.set_status("scheme", DONE if self.scheme_chosen else NOT_STARTED)
        self.set_status("orientation",
                        DONE if self.orientation_applied else NOT_STARTED)
        self.set_status("generate",
                        DONE if self.result is not None else NOT_STARTED)
        self.set_status("natural",
                        DONE if (self.natural is not None
                                 and self.natural.is_active) else NOT_STARTED)
        self.set_status("optimise", DONE if self.scenarios else NOT_STARTED)
        self.set_status("edit", DONE if self.edited else NOT_STARTED)
        self.set_status("cartography",
                        DONE if self.layout is not None else NOT_STARTED)
        if self.result is None or not self.verified:
            self.set_status("verify", NOT_STARTED)
        else:
            self.set_status("verify", WARNING if self.anomalies else DONE)
        self.set_status("outputs", DONE if self.plants_layer is not None
                        else NOT_STARTED)
        self.refresh_flight_status()
        self.changed.emit()

    def refresh_flight_status(self) -> None:
        """Mark the six flight steps from what the planner actually holds."""
        panel = self.uav
        if panel is None:
            for key, _label in UAV_STEPS:
                self.set_status(key, NOT_STARTED)
            return
        has_area = panel.extent.geometry() is not None
        has_dem = panel.dem_layer() is not None
        ready, _reason = panel.readiness()
        mission = panel.last_mission
        report = panel.last_report

        self.set_status(uav_mod.STEP_AREA,
                        DONE if (has_area and has_dem) else NOT_STARTED)
        self.set_status(uav_mod.STEP_HARDWARE,
                        DONE if ready else
                        (WARNING if has_area and has_dem else NOT_STARTED))
        self.set_status(uav_mod.STEP_FLIGHT,
                        DONE if mission is not None else NOT_STARTED)
        if report is None:
            self.set_status(uav_mod.STEP_SAFETY, NOT_STARTED)
        elif report.errors:
            self.set_status(uav_mod.STEP_SAFETY, ERROR)
        elif report.warnings:
            self.set_status(uav_mod.STEP_SAFETY, WARNING)
        else:
            self.set_status(uav_mod.STEP_SAFETY, DONE)
        self.set_status(uav_mod.STEP_SIMULATION,
                        DONE if mission is not None else NOT_STARTED)
        written = getattr(self.uav_export, "last_written", None)
        self.set_status(uav_mod.STEP_EXPORT,
                        DONE if written else NOT_STARTED)

    def touch(self) -> None:
        """Mark the project as changed since it was last written."""
        if not self.dirty:
            self.dirty = True
            self.changed.emit()

    # -- surfaces ----------------------------------------------------------

    @property
    def gross_m2(self) -> float:
        return self.area.lorda_m2 if self.area is not None else 0.0

    @property
    def excluded_m2(self) -> float:
        return self.area.esclusa_m2 if self.area is not None else 0.0

    @property
    def usable_m2(self) -> float:
        return self.area.utile_m2 if self.area is not None else 0.0

    def usable_geometry(self):
        return self.area.utile() if self.area is not None else None

    # -- what the panels change -------------------------------------------

    def set_area(self, geometry, crs, label: str = "") -> None:
        self.area = area_mod.ReforestationArea(
            geometry, crs.authid() if crs else "", label)
        self.crs = crs
        self.cadastre = None
        self.result = None
        # Contours are cut to the usable surface: on a new area they are
        # lines across somewhere else.
        self.contours = []
        # Zones belong to a surface: a new area is a new project, and zones
        # cut from the old one would be planting somewhere else.
        self.zones = zones_mod.ZoneSet()
        self.apply_constraints()
        self.checkpoint()

    def apply_constraints(self) -> None:
        """Re-derive the usable surface from the constraints as they stand."""
        if self.area is None:
            self.refresh_status()
            return
        self.area.clear_exclusions()
        self.constraints.apply_to(self.area)
        self.zones.container = self.area.utile()
        self.draw()
        self.refresh_status()

    def draw(self) -> None:
        """Put the model on the map. Called whenever the model changes."""
        if self.crs is not None:
            self.layers.set_crs(self.crs)
        self.layers.draw_area(self.area)
        self.layers.draw_zones(self.zones)
        self.layers.draw_glades(self.glades)
        self.layers.draw_contours(self.contours)
        self.layers.draw_parcels(self.cadastre)
        self.layers.refresh_canvas()

    def mix_rows(self):
        """``[(key, label, requested %, achieved %, plants)]`` for the chart.

        Requested from the shares the operator set, achieved measured on the
        plants that exist right now -- which after an edit are the ones read
        back off the layer, so the bars move when a species is reassigned.
        """
        achieved = composition_mod.achieved_percentages(
            self.result.plants if self.result is not None else [])
        counts = {}
        for record in (self.result.plants if self.result is not None else []):
            key = composition_mod.species_of(record)
            if key:
                counts[key] = counts.get(key, 0) + 1
        rows = []
        for key, percent in self.shares:
            try:
                record = self.catalog.get(key)
                label = record.name or key
            except GeoCadError:
                label = key
            rows.append((key, label, float(percent),
                         float(achieved.get(key, 0.0)), counts.get(key, 0)))
        return rows

    def mix(self) -> Optional[composition_mod.Mix]:
        if not self.shares:
            return None
        return composition_mod.Mix(self.shares, seed=0)

    @property
    def along_contours(self) -> bool:
        return self.spec.pattern == spacing_mod.PATTERN_CONTOUR

    def contour_length_m(self) -> float:
        return sum(row.length_m for row in self.contours)

    def expected_plants(self) -> float:
        """How many plants the scheme promises on this surface.

        Along the contours the rows are not a lattice: what is known in
        advance is the total development of the lines that were extracted,
        so the count comes from that and not from a row distance nobody is
        going to use.
        """
        if self.along_contours:
            step = self.spec.plant_distance_m
            if step <= 0.0 or not self.contours:
                return 0.0
            return self.contour_length_m() / step
        return density_mod.plants_for_area(self.density_per_ha(),
                                           self.usable_m2)

    def density_per_ha(self) -> float:
        if self.along_contours:
            if self.usable_m2 <= 0.0:
                return 0.0
            return self.expected_plants() / (self.usable_m2 / M2_PER_HA)
        return density_mod.density_from_spacing(
            self.spec.pattern, self.spec.plant_distance_m,
            self.spec.row_distance_m)

    def sync_plants_from_layer(self):
        """Take the plan back from the layer the operator edited.

        Once a plant can be moved, added or deleted on the map, the layer is
        the plan and the generator's output is history. Everything an
        operator is shown afterwards -- the count, the density, the mix, the
        anomalies, the export -- is re-derived from here, so nothing they
        read is a number from before the edit.

        Returns ``(count, added)``, or ``None`` when there is nothing to
        take back.
        """
        layer = self.plants_layer
        if layer is None or self.result is None:
            return None
        records, added = spacing_mod.plants_from_layer(
            layer, terrain=self.terrain)
        # "Edited" has to include a plant that only moved: the count is the
        # same, the ids are the same, and the plan is not.
        def _signature(plants):
            return {record.plant_id: (round(record.x, 4), round(record.y, 4),
                                      composition_mod.species_of(record))
                    for record in plants}

        changed = _signature(self.result.plants) != _signature(records)
        self.result.plants = records
        self.added_plants = spacing_mod.added_plants(records)
        self.composition = composition_mod.from_records(
            records, layout=(self.composition.layout
                             if self.composition is not None
                             else composition_mod.LAYOUT_UNIFORM))
        self.edited = self.edited or changed
        self.verified = False
        self.refresh_status()
        return len(records), added

    # -- the printed sheet -------------------------------------------------

    def map_layers(self):
        """The layers the drawing shows, back to front.

        The plugin's own, in the order the map service stacks them, plus the
        plants layer the generator committed. Nothing else the operator may
        have open: a project drawing is not a screenshot of their session.
        """
        ordered = []
        for key, _title, *_rest in map_layers_mod.LAYER_SPEC:
            layer = self.layers.layers.get(key)
            if layer is not None:
                ordered.append(layer)
        if (self.plants_layer is not None
                and self.plants_layer not in ordered):
            ordered.insert(0, self.plants_layer)
        return ordered

    def compose_layout(self, spec=None, name: str = ""):
        """Build the sheet and put it in the project's layout manager."""
        spec = spec if spec is not None else self.layout_spec
        spec.warnings = []
        layers = self.map_layers()
        extent = carto_mod.layers_extent(layers)
        layout = carto_mod.build_layout(
            QgsProject.instance(), spec, layers, extent,
            name or spec.title or tr("Rimboschimento"), crs=self.crs)
        carto_mod.register(QgsProject.instance(), layout)
        self.layout = layout
        self.layout_spec = spec
        self.refresh_status()
        return layout

    def set_species_on(self, feature_ids, key: str) -> int:
        """Give the chosen species to the plants an operator selected."""
        layer = self.plants_layer
        if layer is None or not feature_ids or not key:
            return 0
        index = layer.fields().indexOf("specie")
        if index < 0:
            return 0
        started = not layer.isEditable()
        if started:
            layer.startEditing()
        changed = 0
        for feature_id in feature_ids:
            if layer.changeAttributeValue(feature_id, index, key):
                changed += 1
        if started:
            layer.commitChanges()
        if changed:
            self.edited = True
            symbology_mod.apply_species_symbology(layer)
            layer.triggerRepaint()
        return changed

    def validate(self) -> list:
        """Check the plan that is actually there, against what was asked.

        On the project and not inside the Verify panel, because the
        Editing panel has to be able to ask the same question after an
        operator moves a plant, and two copies of these checks would
        drift apart the first time one of them was corrected.

        Every check is measured on ``result.plants`` as they stand: after
        an edit those are the records read back off the layer.
        """
        anomalies = []
        result = self.result
        geometry = self.usable_geometry()
        if result is None or geometry is None:
            self.anomalies = []
            self.verified = False
            self.refresh_status()
            return []

        outside = [p for p in result.plants
                   if not geometry.intersects(QgsGeometry.fromWkt(
                       "POINT({0} {1})".format(p.x, p.y)))]
        if outside:
            anomalies.append(tr("{0} piante fuori dalla superficie utile")
                             .format(len(outside)))

        excluded = (self.area.esclusa()
                    if self.area is not None else None)
        if excluded is not None:
            inside_exclusion = [p for p in result.plants
                                if excluded.intersects(QgsGeometry.fromWkt(
                                    "POINT({0} {1})".format(p.x, p.y)))]
            if inside_exclusion:
                anomalies.append(
                    tr("{0} piante dentro un'area esclusa").format(
                        len(inside_exclusion)))

        # The mean spacing is a check on a *regular* stand. Once the
        # naturaliform pass has taken plants out, the gaps left behind pull
        # the mean up by design, and flagging that would be reporting the
        # feature as a fault. What still has to hold there is the minimum,
        # checked below.
        thinned = (self.natural_outcome is not None
                   and self.natural_outcome.removed_total > 0)
        wanted = self.spec.effective_real_spacing[0]
        measured = result.mean_real_spacing()
        if not thinned and measured and abs(measured - wanted) > 0.05 * wanted:
            anomalies.append(
                tr("Distanza media {0:.2f} m contro {1:.2f} m richiesti")
                .format(measured, wanted))

        floor = (self.natural.min_distance_m
                 if self.natural is not None else 0.0)
        if floor > 0.0:
            measured = natural_mod.measure_min_distance(result.plants)
            if math.isfinite(measured) and measured < floor - 1e-6:
                anomalies.append(
                    tr("Distanza minima {0:.2f} m contro {1:.2f} m "
                       "richiesti").format(measured, floor))

        if self.shares:
            mix = self.mix()
            achieved = composition_mod.achieved_percentages(result.plants)
            for key, percent in mix.weights().items():
                got = achieved.get(key, 0.0)
                if abs(got - 100.0 * percent) > 1.0:
                    anomalies.append(
                        tr("{0}: {1:.1f} % invece di {2:.1f} %").format(
                            key, got, 100.0 * percent))

        self.anomalies = anomalies
        self.verified = True
        self.refresh_status()
        return anomalies


    # -- the contours ------------------------------------------------------

    def extract_contours(self, interval_m: float, min_length_m: float = 0.0):
        """Contour the working DEM and keep the lines inside the project.

        ``gdal:contour`` over the same warped window the slope comes from,
        then cut to the usable surface: what comes back are the rows that
        will be planted, so an operator sees them before choosing to.
        """
        if self.terrain is None:
            raise GeoCadError(
                "no DEM to contour",
                user_message=tr("Scarica prima il DEM dal pannello Terreno."))
        geometry = self.usable_geometry()
        if geometry is None:
            raise GeoCadError(
                "no area", user_message=tr("Definisci prima l'area."))
        layer = curves_mod.extract_contours(self.terrain.raster_layer(),
                                            interval_m=interval_m)
        rows = curves_mod.contour_rows(layer, clip_geometry=geometry,
                                       min_length_m=min_length_m)
        self.contours = rows
        self.contour_interval_m = float(interval_m)
        self.draw()
        self.checkpoint()
        self.refresh_status()
        return rows

    # -- the cadastre ------------------------------------------------------

    def request_cadastre(self, transport=None):
        """Ask the cadastre about the project area, in the background.

        Returns the task so the caller can watch it; the answer arrives on
        :attr:`cadastralDataReady`, which is what the Area panel listens to.
        """
        geometry = self.usable_geometry()
        if geometry is None or self.crs is None:
            raise GeoCadError(
                "no project area for the cadastral query",
                user_message="Definisci prima l'area di progetto.")
        # A task handed to the manager is not kept alive by it: the only
        # owner is Python, and the button that starts the query drops the
        # return value the moment it is made. Without this reference the
        # task is collected before it runs and the panel sits on
        # "interrogazione in corso..." for ever.
        if self.cadastre_task is not None:
            try:
                self.cadastre_task.cancel()     # a finished task ignores it
            except RuntimeError:
                pass
        task = cadastre_mod.area_task(geometry, self.crs, transport=transport)
        task.cadastralDataReady.connect(self._on_cadastre)
        self.cadastre_task = task
        return task

    def _on_cadastre(self, data: dict) -> None:
        self.cadastre = data.get("result")
        self.layers.draw_parcels(self.cadastre)
        self.layers.refresh_canvas()
        self.cadastralDataReady.emit(data)
        self.checkpoint()
        self.refresh_status()


# --------------------------------------------------------------------------
# Left dock: the workflow
# --------------------------------------------------------------------------

#: The project commands, above the steps: label, method, shortcut, tooltip.
#: A table rather than seven near-identical blocks of widget code.
PROJECT_COMMANDS = (
    ("new", "Nuovo", "on_new", "Ctrl+N",
     "Svuota il progetto e ricomincia"),
    ("open", "Apri", "on_open", "Ctrl+O",
     "Apre un progetto di rimboschimento"),
    ("save", "Salva", "on_save", "Ctrl+S",
     "Salva il progetto dove e' stato aperto"),
    ("save_as", "Salva con nome", "on_save_as", "Ctrl+Shift+S",
     "Salva il progetto in un nuovo file"),
    (None, None, None, None, None),
    ("undo", "Annulla", "on_undo", "Ctrl+Z",
     "Annulla l'ultima operazione sul progetto"),
    ("redo", "Ripeti", "on_redo", "Ctrl+Y",
     "Ripete l'operazione annullata"),
    (None, None, None, None, None),
    ("refresh", "Aggiorna", "on_refresh", "F5",
     "Ridisegna il progetto sulla mappa"),
    ("settings", "Impostazioni", "on_settings", "",
     "Apre le impostazioni del plugin"),
)


class WorkflowDock(QDockWidget):
    """The steps, the state each one is in, and the project commands."""

    stepChanged = pyqtSignal(int)

    def __init__(self, state: ProjectState, parent=None):
        super().__init__(tr("Rimboschimento"), parent)
        self.setObjectName("GeoCadWorkflowDock")
        self.state = state
        self.actions = {}
        self.toolbar = QToolBar(tr("Progetto"))
        self.toolbar.setObjectName("GeoCadProjectToolbar")
        for key, label, method, shortcut, tip in PROJECT_COMMANDS:
            if key is None:
                self.toolbar.addSeparator()
                continue
            action = self.toolbar.addAction(tr(label))
            action.setToolTip(tr(tip))
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(getattr(self, method))
            self.actions[key] = action
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setUniformItemSizes(True)
        for key, label in ALL_STEPS:
            item = QListWidgetItem(swatch(STATE_COLORS[NOT_STARTED]), label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.list.currentRowChanged.connect(self.stepChanged.emit)
        # A panel that finishes its own job and hands over to another step
        # asks for it here rather than reaching into the list.
        state.stepRequested.connect(self.select_step)

        self.file_label = QLabel(tr("progetto non salvato"))
        self.file_label.setWordWrap(True)

        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.list)
        layout.addWidget(self.file_label)
        self.setWidget(holder)
        self.setMinimumWidth(150)

        state.statusChanged.connect(self.set_status)
        state.changed.connect(self.refresh_commands)
        self.refresh_commands()

    # -- the project commands ----------------------------------------------

    def refresh_commands(self) -> None:
        """What can be pressed, and what the label under the list says."""
        self.actions["undo"].setEnabled(self.state.can_undo)
        self.actions["redo"].setEnabled(self.state.can_redo)
        self.actions["save"].setEnabled(bool(self.state.path)
                                        or self.state.area is not None)
        if not self.state.path:
            self.file_label.setText(tr("progetto non salvato"))
            return
        name = os.path.basename(self.state.path)
        self.file_label.setText(
            (tr("{0} - modificato") if self.state.dirty else tr("{0}"))
            .format(name))

    def warn(self, error) -> None:
        message = (error.formatted() if isinstance(error, GeoCadError)
                   else str(error))
        iface = self.state.iface
        if iface is not None:
            try:
                iface.messageBar().pushMessage("GeoCad UAV", message,
                                               level=Qgis.Warning, duration=6)
                return
            except Exception:                                   # noqa: BLE001
                pass
        QgsApplication.messageLog().logMessage(message, "GeoCad UAV",
                                               Qgis.Warning)

    def confirm_discard(self) -> bool:
        """Ask before throwing away unsaved work. True means go ahead."""
        if not self.state.dirty:
            return True
        if self.state.iface is None:
            return True                 # headless: the caller decided
        answer = QMessageBox.question(
            self, tr("Progetto non salvato"),
            tr("Il progetto ha modifiche non salvate. Continuare?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def on_new(self, *_args) -> bool:
        if not self.confirm_discard():
            return False
        self.state.clear()
        self.state.reset_history()
        self.refresh_commands()
        return True

    def on_open(self, *_args, path: str = ""):
        if not self.confirm_discard():
            return None
        if not path:
            path, _filter = QFileDialog.getOpenFileName(
                self, tr("Apri un progetto"), "",
                "GeoCad UAV (*{0})".format(project_mod.SUFFIX))
        if not path:
            return None
        try:
            warnings = self.state.load_from(path)
        except GeoCadError as exc:
            self.warn(exc)
            return None
        for warning in warnings:
            self.warn(GeoCadError("project warning", user_message=warning))
        self.refresh_commands()
        return warnings

    def on_save(self, *_args) -> str:
        if not self.state.path:
            return self.on_save_as()
        return self._write(self.state.path)

    def on_save_as(self, *_args, path: str = "") -> str:
        if not path:
            path, _filter = QFileDialog.getSaveFileName(
                self, tr("Salva il progetto"),
                "progetto" + project_mod.SUFFIX,
                "GeoCad UAV (*{0})".format(project_mod.SUFFIX))
        if not path:
            return ""
        return self._write(path)

    def _write(self, path: str) -> str:
        try:
            written = self.state.save_to(path)
        except GeoCadError as exc:
            self.warn(exc)
            return ""
        self.refresh_commands()
        return written

    def on_undo(self, *_args) -> bool:
        done = self.state.undo()
        self.refresh_commands()
        return done

    def on_redo(self, *_args) -> bool:
        done = self.state.redo()
        self.refresh_commands()
        return done

    def on_refresh(self, *_args) -> bool:
        self.state.draw()
        self.state.refresh_status()
        return True

    def on_settings(self, *_args) -> bool:
        """Open the plugin dock at its Impostazioni tab."""
        iface = self.state.iface
        if iface is None:
            return False
        try:
            for dock in iface.mainWindow().findChildren(QDockWidget):
                if dock.objectName() != "GeoCadUavDock":
                    continue
                dock.setVisible(True)
                tabs = getattr(dock, "tabs", None)
                if tabs is not None:
                    tabs.setCurrentIndex(tabs.count() - 1)
                return True
        except (AttributeError, RuntimeError):
            return False
        return False

    def set_status(self, key: str, visual: str) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                item.setIcon(swatch(STATE_COLORS.get(visual,
                                                     STATE_COLORS[NOT_STARTED])))
                item.setToolTip("{0}: {1}".format(item.text(), visual))
                return

    def current_key(self) -> str:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def select_step(self, key: str) -> bool:
        """Move to a step by name. Used when one step hands over to another."""
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == key:
                self.list.setCurrentRow(row)
                return True
        return False


# --------------------------------------------------------------------------
# The panels
# --------------------------------------------------------------------------

class Panel(QWidget):
    """Common shape: a title, a body, and access to the state."""

    def __init__(self, title: str, state: ProjectState, parent=None):
        super().__init__(parent)
        self.state = state
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(6, 6, 6, 6)
        heading = QLabel("<b>{0}</b>".format(title))
        heading.setWordWrap(True)
        self.layout.addWidget(heading)

    def say(self, message: str) -> None:
        """A plain notice. The message bar when there is one, print never."""
        iface = self.state.iface
        if iface is None:
            return
        try:
            iface.messageBar().pushMessage("GeoCad UAV", message,
                                           level=Qgis.Info, duration=5)
        except Exception:                                       # noqa: BLE001
            pass

    def warn(self, error) -> None:
        message = (error.formatted() if isinstance(error, GeoCadError)
                   else str(error))
        iface = self.state.iface
        if iface is not None:
            try:
                iface.messageBar().pushMessage("GeoCad UAV", message,
                                               level=Qgis.Warning, duration=6)
                return
            except Exception:                                   # noqa: BLE001
                pass
        QgsProject.instance()        # keep the import meaningful offscreen


class AreaPanel(Panel):
    """Step 1: where the project is, and what the cadastre says about it."""

    def __init__(self, state, parent=None):
        super().__init__(tr("Area di intervento"), state, parent)

        buttons = QHBoxLayout()
        self.select_button = QPushButton(tr("Seleziona"))
        self.draw_button = QPushButton(tr("Disegna"))
        buttons.addWidget(self.select_button)
        buttons.addWidget(self.draw_button)
        self.layout.addLayout(buttons)

        surfaces = QGroupBox(tr("Superfici"))
        form = QFormLayout(surfaces)
        self.gross_label = QLabel(DASH)
        self.excluded_label = QLabel(DASH)
        self.usable_label = QLabel(DASH)
        form.addRow(tr("Lorda"), self.gross_label)
        form.addRow(tr("Esclusa"), self.excluded_label)
        form.addRow(tr("Utile"), self.usable_label)
        self.layout.addWidget(surfaces)

        cadastre = QGroupBox(tr("Dati catastali"))
        cad_form = QFormLayout(cadastre)
        self.query_button = QPushButton(tr("Interroga Catasto (WFS)"))
        cad_form.addRow(self.query_button)
        self.comune_label = QLabel(DASH)
        self.belfiore_label = QLabel(DASH)
        self.parcels_label = QLabel(DASH)
        self.cadastral_area_label = QLabel(DASH)
        self.project_area_label = QLabel(DASH)
        self.cadastre_status_label = QLabel(tr("in attesa"))
        cad_form.addRow(tr("Comune"), self.comune_label)
        cad_form.addRow(tr("Belfiore"), self.belfiore_label)
        cad_form.addRow(tr("Particelle"), self.parcels_label)
        cad_form.addRow(tr("Superficie cat."), self.cadastral_area_label)
        cad_form.addRow(tr("Superficie progetto"), self.project_area_label)
        cad_form.addRow(tr("Stato"), self.cadastre_status_label)
        buttons_row = QHBoxLayout()
        self.details_button = QPushButton(tr("Dettagli"))
        self.details_button.setEnabled(False)
        self.show_button = QPushButton(tr("Visualizza particelle"))
        self.show_button.setEnabled(False)
        buttons_row.addWidget(self.details_button)
        buttons_row.addWidget(self.show_button)
        cad_form.addRow(buttons_row)

        self.parcel_table = QTableWidget(0, 4)
        self.parcel_table.setHorizontalHeaderLabels(
            [tr("Comune"), tr("Foglio"), tr("Particella"), tr("%")])
        self.parcel_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.parcel_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.parcel_table.setMaximumHeight(160)
        cad_form.addRow(self.parcel_table)
        self.layout.addWidget(cadastre)
        self.layout.addStretch(1)

        self.select_button.clicked.connect(self.take_selection)
        self.draw_button.clicked.connect(self.start_drawing)
        self.query_button.clicked.connect(self.query_cadastre)
        self.details_button.clicked.connect(self.show_details)
        self.show_button.clicked.connect(self.show_parcels)
        self.parcel_table.itemSelectionChanged.connect(self.on_parcel_picked)
        state.changed.connect(self.refresh)
        state.cadastralDataReady.connect(self.on_cadastral_data)

    # -- the area ----------------------------------------------------------

    def take_selection(self) -> bool:
        """The active polygon layer, its selection first."""
        layer = None
        if self.state.iface is not None:
            try:
                layer = self.state.iface.activeLayer()
            except AttributeError:
                layer = None
        try:
            built = area_mod.ReforestationArea.from_layer(layer)
        except GeoCadError as exc:
            self.warn(exc)
            return False
        self.state.set_area(built.geometry, layer.crs(), built.label)
        return True

    def start_drawing(self):
        """Hand the map to the polygon digitizer and take what it commits."""
        if self.state.iface is None:
            return None
        from ..cad import tools as cad_tools                    # noqa: PLC0415
        from ..io import layer_factory as lf                    # noqa: PLC0415

        canvas = self.state.iface.mapCanvas()
        crs = canvas.mapSettings().destinationCrs()
        layer = lf.memory_layer("Polygon", "Area di progetto", crs.authid(),
                                [])
        QgsProject.instance().addMapLayer(layer)

        def _taken(_fid):
            for feature in layer.getFeatures():
                geometry = feature.geometry()
                if geometry is not None and not geometry.isEmpty():
                    self.state.set_area(QgsGeometry(geometry), crs,
                                        tr("Area disegnata"))
                    break

        layer.featureAdded.connect(_taken)
        tool = cad_tools.create_tool("digitize", canvas,
                                     iface=self.state.iface,
                                     layer_provider=lambda: layer)
        canvas.setMapTool(tool)
        return tool

    # -- the cadastre ------------------------------------------------------

    def query_cadastre(self, transport=None):
        try:
            task = self.state.request_cadastre(
                transport if callable(transport) else None)
        except GeoCadError as exc:
            self.warn(exc)
            self.cadastre_status_label.setText(tr("non riuscita"))
            return None
        self.cadastre_status_label.setText(tr("interrogazione in corso..."))
        self.query_button.setEnabled(False)
        return task

    def on_cadastral_data(self, data: dict) -> None:
        """The slot the background task reaches when it has an answer."""
        self.query_button.setEnabled(True)
        self.comune_label.setText(str(data.get("comune") or DASH))
        self.belfiore_label.setText(str(data.get("belfiore") or DASH))
        self.parcels_label.setText(str(data.get("particelle", 0)))
        self.cadastral_area_label.setText("{0:,.2f} ha".format(
            float(data.get("superficie_catastale_ha") or 0.0)))
        self.project_area_label.setText("{0:,.2f} ha".format(
            float(data.get("superficie_interessata_ha") or 0.0)))
        status = str(data.get("stato") or "")
        # Never an empty table with no reason: the status line says what the
        # service answered, and the message says why when it is not OK.
        label = cadastre_mod.STATUS_LABELS.get(status, status or DASH)
        message = str(data.get("messaggio") or "")
        self.cadastre_status_label.setText(
            "{0} - {1}".format(label, message) if message else label)
        self.cadastre_status_label.setToolTip(
            "\n".join([message] + list(data.get("avvisi") or [])))
        self.details_button.setEnabled(bool(data.get("righe")))
        self.show_button.setEnabled(bool(data.get("righe")))
        self.fill_parcel_table(data.get("righe") or [])
        if data.get("righe"):
            self.show_parcels()

    def fill_parcel_table(self, rows) -> int:
        """One row per parcel, in the order the result puts them."""
        self.parcel_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (row.get("comune", ""), row.get("foglio", ""),
                      row.get("particella", ""),
                      "{0:.2f}".format(float(row.get("percentuale") or 0.0)))
            for column, value in enumerate(values):
                self.parcel_table.setItem(index, column,
                                          QTableWidgetItem(str(value)))
        return len(rows)

    def show_parcels(self) -> int:
        """Draw the parcels on the canvas and frame them."""
        drawn = self.state.layers.draw_parcels(self.state.cadastre)
        if drawn:
            self.state.layers.zoom_to("parcels")
        return drawn

    def on_parcel_picked(self) -> bool:
        """A row chosen in the table is a parcel shown on the map."""
        row = self.parcel_table.currentRow()
        if row < 0:
            return False
        return self.state.layers.select("parcels", row)

    def show_details(self):
        result = self.state.cadastre
        if result is None:
            return None
        text = "\n".join(result.describe())
        if self.state.iface is None:
            return text
        box = QMessageBox(self)
        box.setWindowTitle(tr("Particelle catastali"))
        box.setText(text)
        box.exec()
        return text

    def refresh(self) -> None:
        self.gross_label.setText(ha(self.state.gross_m2)
                                 if self.state.area else DASH)
        self.excluded_label.setText(ha(self.state.excluded_m2)
                                    if self.state.area else DASH)
        self.usable_label.setText(ha(self.state.usable_m2)
                                  if self.state.area else DASH)


class TerrainPanel(Panel):
    """Step 2: the ground, fetched rather than hunted for."""

    def __init__(self, state, parent=None):
        super().__init__(tr("Terreno"), state, parent)
        self.download_button = QPushButton(tr("Scarica DEM Area (Auto)"))
        self.layout.addWidget(self.download_button)

        self.source = QComboBox()
        from ..io import dem_source                             # noqa: PLC0415

        for adapter_id in dem_source.ADAPTER_ORDER:
            spec = dem_source.adapter(adapter_id)
            self.source.addItem("{0} [{1}]".format(spec.label, spec.status),
                                adapter_id)
        form = QFormLayout()
        form.addRow(tr("Sorgente"), self.source)
        # A regional DTM already loaded in QGIS is the commonest case in the
        # field, and it does not need the internet. The download stays for
        # the operator who has nothing.
        self.local_dem = QComboBox()
        self.use_local_button = QPushButton(tr("Usa DEM del progetto"))
        form.addRow(tr("DEM caricato"), self.local_dem)
        form.addRow(self.use_local_button)
        self.layout.addLayout(form)

        box = QGroupBox(tr("Morfologia"))
        stats = QFormLayout(box)
        self.elevation_label = QLabel(DASH)
        self.slope_label = QLabel(DASH)
        self.aspect_label = QLabel(DASH)
        self.suitable_label = QLabel(DASH)
        stats.addRow(tr("Quota"), self.elevation_label)
        stats.addRow(tr("Pendenza"), self.slope_label)
        stats.addRow(tr("Esposizione"), self.aspect_label)
        stats.addRow(tr("Aree non idonee"), self.suitable_label)
        self.layout.addWidget(box)

        limits = QGroupBox(tr("Idoneita'"))
        limit_form = QFormLayout(limits)
        self.slope_max = QDoubleSpinBox()
        self.slope_max.setRange(0.0, 90.0)
        self.slope_max.setValue(35.0)
        self.slope_max.setSuffix(" deg")
        limit_form.addRow(tr("Pendenza massima"), self.slope_max)
        self.apply_suitability_button = QPushButton(
            tr("Escludi le aree non idonee"))
        limit_form.addRow(self.apply_suitability_button)
        self.layout.addWidget(limits)

        contours = QGroupBox(tr("Curve di livello"))
        contour_form = QFormLayout(contours)
        self.contour_interval = QDoubleSpinBox()
        self.contour_interval.setRange(0.5, 500.0)
        self.contour_interval.setValue(curves_mod.DEFAULT_INTERVAL_M)
        self.contour_interval.setSuffix(" m")
        self.contour_min_length = QDoubleSpinBox()
        self.contour_min_length.setRange(0.0, 10_000.0)
        self.contour_min_length.setValue(20.0)
        self.contour_min_length.setSuffix(" m")
        contour_form.addRow(tr("Equidistanza"), self.contour_interval)
        contour_form.addRow(tr("Lunghezza minima"), self.contour_min_length)
        self.contour_button = QPushButton(tr("Estrai curve di livello"))
        contour_form.addRow(self.contour_button)
        self.contour_label = QLabel(tr("nessuna curva estratta"))
        self.contour_label.setWordWrap(True)
        contour_form.addRow(tr("Esito"), self.contour_label)
        self.layout.addWidget(contours)
        self.layout.addStretch(1)

        self.download_button.clicked.connect(self.download)
        self.use_local_button.clicked.connect(self.use_local_dem)
        self.apply_suitability_button.clicked.connect(self.apply_suitability)
        self.contour_button.clicked.connect(self.extract_contours)
        state.changed.connect(self.refresh)
        self.reload_rasters()

    def download(self, transport=None):
        geometry = self.state.usable_geometry()
        if geometry is None:
            self.warn(GeoCadError(
                "no area", user_message=tr("Definisci prima l'area.")))
            return None
        try:
            analysis = terrain_mod.TerrainAnalysis.auto_download(
                geometry, self.state.crs, self.source.currentData(),
                work_crs=self.state.crs,
                transport=transport if callable(transport) else None)
        except GeoCadError as exc:
            self.warn(exc)
            return None
        if analysis is None:
            return None
        self.state.terrain = analysis
        self.state.dem_layer_id = ""
        self.state.refresh_status()
        return analysis

    def reload_rasters(self) -> int:
        """List the raster layers the project already holds."""
        # isinstance, not layer.type(): the enum that names a raster layer
        # was QgsMapLayerType in 3.x and Qgis.LayerType from 3.30, and the
        # class itself is the one thing both versions agree on.
        from qgis.core import QgsRasterLayer                      # noqa: PLC0415

        current = self.local_dem.currentData()
        self.local_dem.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if not isinstance(layer, QgsRasterLayer):
                continue
            self.local_dem.addItem(layer.name(), layer.id())
        if current is not None:
            index = self.local_dem.findData(current)
            if index >= 0:
                self.local_dem.setCurrentIndex(index)
        return self.local_dem.count()

    def use_local_dem(self, *_args):
        """Read the chosen raster as the project's terrain."""
        geometry = self.state.usable_geometry()
        if geometry is None:
            self.warn(GeoCadError(
                "no area", user_message=tr("Definisci prima l'area.")))
            return None
        layer = QgsProject.instance().mapLayer(self.local_dem.currentData()
                                               or "")
        if layer is None:
            self.warn(GeoCadError(
                "no raster chosen",
                user_message=tr("Nessun DEM caricato da usare. Carica un "
                                "raster nel progetto, oppure scaricalo.")))
            return None
        try:
            analysis = terrain_mod.TerrainAnalysis.from_layer(
                layer, self.state.crs, geometry, margin_m=DEM_MARGIN_M)
        except GeoCadError as exc:
            self.warn(exc)
            return None
        self.state.terrain = analysis
        self.state.dem_layer_id = layer.id()
        self.state.contours = []
        self.state.draw()
        self.state.checkpoint()
        self.state.refresh_status()
        return analysis

    def apply_suitability(self, *_args):
        """Turn the slope limit into ground the plan will not use.

        Until this is pressed the limit is a percentage in a label: the
        generator plants over the whole usable surface whatever the DEM
        says. Pressed, the rejected cells become an exclusion like a road
        or a watercourse -- same mechanism, same report line, same colour on
        the map -- and the usable surface really shrinks.
        """
        from ..forest.planting import TopographicFilter          # noqa: PLC0415

        if self.state.terrain is None:
            self.warn(GeoCadError(
                "no terrain", user_message=tr("Carica prima il DEM.")))
            return None
        geometry = self.state.area.lorda() if self.state.area else None
        if geometry is None:
            self.warn(GeoCadError(
                "no area", user_message=tr("Definisci prima l'area.")))
            return None
        limit = self.slope_max.value()
        try:
            mask = self.state.terrain.suitability(
                TopographicFilter(slope_max_deg=limit))
            mask = self.state.terrain.restrict_to(mask, geometry)
            unsuitable = self.state.terrain.unsuitable_geometry(mask,
                                                                clip=geometry)
        except GeoCadError as exc:
            self.warn(exc)
            return None
        key = SUITABILITY_KEY
        label = tr("Pendenza oltre {0:g} gradi").format(limit)
        self.state.constraints.declare(key, 0.0, label=label)
        self.state.constraints.clear_features(key)
        if unsuitable is not None:
            self.state.constraints.add_geometry(key, unsuitable,
                                                source=tr("DEM"))
        self.state.apply_constraints()
        self.state.draw()
        self.state.checkpoint()
        self.refresh()
        if self.state.usable_m2 <= 0.0:
            # Better said out loud here than met three steps later as
            # "nessuna superficie utile" from the generator.
            self.warn(GeoCadError(
                "suitability leaves nothing",
                user_message=tr("Con una pendenza massima di {0:g} deg non "
                                "resta alcuna superficie utile: tutta l'area "
                                "e' piu' ripida.").format(limit)))
        return unsuitable

    def extract_contours(self, *_args) -> int:
        """Contour the DEM and put the lines on the map."""
        try:
            rows = self.state.extract_contours(
                self.contour_interval.value(),
                min_length_m=self.contour_min_length.value())
        except GeoCadError as exc:
            self.warn(exc)
            self.contour_label.setText(exc.formatted())
            return 0
        self.state.layers.zoom_to("contours")
        return len(rows)

    def describe_contours(self) -> str:
        rows = self.state.contours
        if not rows:
            return tr("nessuna curva estratta")
        heights = [row.elevation_m for row in rows
                   if math.isfinite(row.elevation_m)]
        text = tr("{0:,} curve, sviluppo {1:,.0f} m").format(
            len(rows), self.state.contour_length_m())
        if heights:
            text += tr(", quote {0:.0f}-{1:.0f} m").format(min(heights),
                                                           max(heights))
        return text

    def refresh(self) -> None:
        self.contour_label.setText(self.describe_contours())
        self.reload_rasters()
        analysis = self.state.terrain
        if analysis is None:
            for label in (self.elevation_label, self.slope_label,
                          self.aspect_label, self.suitable_label):
                label.setText(DASH)
            return
        stats = analysis.statistics()
        self.elevation_label.setText("{0:.0f} - {1:.0f} m".format(
            stats["z_min_m"], stats["z_max_m"]))
        self.slope_label.setText("{0:.1f} - {1:.1f} deg".format(
            stats["slope_min_deg"], stats["slope_max_deg"]))
        azimuth = stats["dominant_slope_azimuth_deg"]
        self.aspect_label.setText(DASH if azimuth is None
                                  else "{0:.0f} deg".format(azimuth))
        from ..forest.planting import TopographicFilter          # noqa: PLC0415

        mask = analysis.suitability(
            TopographicFilter(slope_max_deg=self.slope_max.value()))
        applied = self.state.constraints.rules.get(SUITABILITY_KEY)
        self.suitable_label.setText(
            tr("{0:.1%} idonea{1}").format(
                mask.suitable_fraction,
                tr(" - esclusa dal progetto") if (applied is not None
                                                  and applied.n_features)
                else tr(" - non ancora esclusa")))


class ConstraintsPanel(Panel):
    """Step 3: what to stay away from, and by how much."""

    def __init__(self, state, parent=None):
        super().__init__(tr("Vincoli e fasce di rispetto"), state, parent)
        self.rows = {}
        box = QGroupBox(tr("Fasce"))
        form = QFormLayout(box)
        for key, label, distance in CONSTRAINT_KINDS:
            state.constraints.declare(key, distance, label=label)
            row = QHBoxLayout()
            check = QCheckBox()
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 500.0)
            spin.setValue(distance)
            spin.setSuffix(" m")
            pick = QPushButton(tr("Layer"))
            row.addWidget(check)
            row.addWidget(spin, 1)
            row.addWidget(pick)
            holder = QWidget()
            holder.setLayout(row)
            form.addRow(label, holder)
            self.rows[key] = (check, spin, pick)
            spin.valueChanged.connect(
                lambda value, k=key: self.on_distance(k, value))
            check.toggled.connect(lambda _v: self.apply())
            pick.clicked.connect(lambda _v, k=key: self.take_layer(k))
        self.layout.addWidget(box)

        self.usable_label = QLabel(DASH)
        summary = QFormLayout()
        summary.addRow(tr("Superficie utile"), self.usable_label)
        self.layout.addLayout(summary)
        self.layout.addStretch(1)
        state.changed.connect(self.refresh)

    def on_distance(self, key: str, value: float) -> None:
        self.state.constraints.set_buffer(key, value)
        self.apply()

    def take_layer(self, key: str) -> int:
        layer = None
        if self.state.iface is not None:
            try:
                layer = self.state.iface.activeLayer()
            except AttributeError:
                layer = None
        added = self.state.constraints.add_layer(key, layer)
        if added:
            self.rows[key][0].setChecked(True)
        self.apply()
        return added

    def apply(self) -> None:
        for key, (check, _spin, _pick) in self.rows.items():
            if not check.isChecked():
                self.state.constraints.clear_features(key)
        self.state.apply_constraints()
        self.state.draw()
        self.state.checkpoint()

    def refresh(self) -> None:
        self.usable_label.setText(ha(self.state.usable_m2)
                                  if self.state.area else DASH)


class ZonesPanel(Panel):
    """Step 4: sub-areas, each planted with its own scheme and its own mix.

    A zone here is not a note to self: the generator plants each one
    separately, and what this table shows -- surface, scheme, density,
    species -- is what step 8 will produce for it.
    """

    def __init__(self, state, parent=None):
        super().__init__(tr("Zone di impianto"), state, parent)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [tr("Zona"), tr("Superficie"), tr("Sesto"), tr("Densita'"),
             tr("Specie")])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.layout.addWidget(self.table)

        buttons = QHBoxLayout()
        self.add_button = QPushButton(tr("Zona sull'area"))
        self.remove_button = QPushButton(tr("Rimuovi"))
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        self.layout.addLayout(buttons)

        bands = QHBoxLayout()
        self.band_count = QSpinBox()
        self.band_count.setRange(2, 50)
        self.band_count.setValue(3)
        self.split_button = QPushButton(tr("Dividi in fasce"))
        bands.addWidget(QLabel(tr("Fasce")))
        bands.addWidget(self.band_count)
        bands.addWidget(self.split_button, 1)
        self.layout.addLayout(bands)

        self.apply_button = QPushButton(
            tr("Applica sesto e specie correnti alla zona scelta"))
        self.layout.addWidget(self.apply_button)
        self.problems = QLabel("")
        self.problems.setWordWrap(True)
        self.layout.addWidget(self.problems)
        self.layout.addStretch(1)

        self.add_button.clicked.connect(self.add_zone)
        self.remove_button.clicked.connect(self.remove_zone)
        self.split_button.clicked.connect(self.split)
        self.apply_button.clicked.connect(self.apply_current)
        state.changed.connect(self.refresh)

    # -- building the zones ------------------------------------------------

    def _next_name(self) -> str:
        return "Zona {0}".format(chr(ord("A") + len(self.state.zones)))

    def add_zone(self, geometry=None) -> bool:
        """A zone over whatever is left of the usable surface.

        With a geometry it is that shape, clipped; without one it is the
        surface no other zone has taken, which is what "add a zone" means
        when there are none yet.
        """
        container = self.state.usable_geometry()
        if container is None:
            self.warn(GeoCadError(
                "no area", user_message=tr("Definisci prima l'area.")))
            return False
        shape = geometry if isinstance(geometry, QgsGeometry) else None
        if shape is None:
            shape = self.state.zones.uncovered() or QgsGeometry(container)
        try:
            self.state.zones.add(zones_mod.Zone(
                name=self._next_name(), geometry=QgsGeometry(shape),
                spec=self.state.spec, shares=tuple(self.state.shares)))
        except GeoCadError as exc:
            self.warn(exc)
            return False
        self.state.draw()
        self.state.checkpoint()
        self.state.refresh_status()
        return True

    def split(self) -> int:
        """Cut the usable surface into bands that run along the rows."""
        container = self.state.usable_geometry()
        if container is None:
            self.warn(GeoCadError(
                "no area", user_message=tr("Definisci prima l'area.")))
            return 0
        try:
            bands = zones_mod.split_bands(
                container, self.band_count.value(),
                self.state.spec.row_azimuth_deg, self.state.spec)
        except GeoCadError as exc:
            self.warn(exc)
            return 0
        self.state.zones.clear()
        self.state.zones.container = container
        added = 0
        for band in bands:
            band.shares = tuple(self.state.shares)
            try:
                self.state.zones.add(band)
                added += 1
            except GeoCadError:
                continue
        self.state.draw()
        self.state.checkpoint()
        self.state.refresh_status()
        return added

    def remove_zone(self) -> bool:
        row = self.table.currentRow()
        names = self.state.zones.names()
        if row < 0 or row >= len(names):
            return False
        self.state.zones.remove(names[row])
        self.state.draw()
        self.state.checkpoint()
        self.state.refresh_status()
        return True

    def apply_current(self) -> bool:
        """Give the selected zone the scheme and mix set in step 5 and 6."""
        row = self.table.currentRow()
        names = self.state.zones.names()
        if row < 0 or row >= len(names):
            return False
        zone = self.state.zones.get(names[row])
        zone.spec = self.state.spec
        zone.shares = tuple(self.state.shares)
        self.state.refresh_status()
        return True

    # -- what it shows -----------------------------------------------------

    def refresh(self) -> None:
        zones = list(self.state.zones)
        self.table.setRowCount(len(zones))
        for row, zone in enumerate(zones):
            spec = zone.spec
            values = (
                zone.name, ha(zone.area_m2),
                "--" if spec is None else "{0:g} x {1:g} m".format(
                    spec.plant_distance_m, spec.row_distance_m),
                "{0:,.0f}/ha".format(zone.density_per_ha()),
                ", ".join("{0} {1:g}%".format(k, p) for k, p in zone.shares)
                or "--")
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        problems = self.state.zones.validate()
        self.problems.setText("\n".join("! " + text for text in problems))


class SchemePanel(Panel):
    """Steps 5 and 6: one panel, two tabs, because they share a preview."""

    TAB_SCHEME = 0
    TAB_SPECIES = 1

    def __init__(self, state, parent=None):
        super().__init__(tr("Sesto e specie"), state, parent)
        self.tabs = QTabWidget()
        self.layout.addWidget(self.tabs)

        # -- the scheme ----------------------------------------------------
        scheme = QWidget()
        form = QFormLayout(scheme)
        self.pattern = QComboBox()
        for key in spacing_mod.ALL_PATTERNS:
            self.pattern.addItem(spacing_mod.PATTERN_LABELS[key], key)
        form.addRow(tr("Tipologia"), self.pattern)
        self.plant_distance = self._spin(3.0, " m")
        self.row_distance = self._spin(3.0, " m")
        self.margin = self._spin(0.0, " m")
        self.azimuth = self._spin(90.0, " deg", maximum=360.0)
        self.jitter = self._spin(0.0, " m")
        form.addRow(tr("Sulla fila"), self.plant_distance)
        form.addRow(tr("Interfila"), self.row_distance)
        form.addRow(tr("Margine"), self.margin)
        form.addRow(tr("Azimut file"), self.azimuth)
        form.addRow(tr("Sfalsamento casuale"), self.jitter)
        self.density_label = QLabel(DASH)
        form.addRow(tr("Densita'"), self.density_label)

        self.tabs.addTab(scheme, tr("Sesto"))

        # -- the species ---------------------------------------------------
        species = QWidget()
        species_layout = QVBoxLayout(species)
        self.species_table = QTableWidget(0, 3)
        self.species_table.setHorizontalHeaderLabels(
            [tr("Specie"), tr("%"), tr("Distanza minima (m)")])
        self.species_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        species_layout.addWidget(self.species_table)
        row = QHBoxLayout()
        self.species_key = QComboBox()
        self.species_key.setEditable(True)
        self.species_percent = QDoubleSpinBox()
        self.species_percent.setRange(0.1, 100.0)
        self.species_percent.setValue(25.0)
        self.species_percent.setSuffix(" %")
        self.add_species = QPushButton(tr("Aggiungi"))
        self.remove_species = QPushButton(tr("Rimuovi"))
        row.addWidget(self.species_key, 1)
        row.addWidget(self.species_percent)
        row.addWidget(self.add_species)
        row.addWidget(self.remove_species)
        species_layout.addLayout(row)
        self.mix_chart = charts_mod.SpeciesMixChart()
        species_layout.addWidget(self.mix_chart)
        self.total_label = QLabel(DASH)
        theme_mod.mark(self.total_label, metric=True)
        species_layout.addWidget(self.total_label)

        catalogue = QHBoxLayout()
        self.open_catalog_button = QPushButton(tr("Apri catalogo..."))
        self.save_catalog_button = QPushButton(tr("Salva catalogo..."))
        catalogue.addWidget(self.open_catalog_button)
        catalogue.addWidget(self.save_catalog_button)
        species_layout.addLayout(catalogue)
        self.catalog_label = QLabel(tr("catalogo vuoto"))
        self.catalog_label.setWordWrap(True)
        species_layout.addWidget(self.catalog_label)
        self.tabs.addTab(species, tr("Specie"))

        for widget in (self.plant_distance, self.row_distance, self.margin,
                       self.azimuth, self.jitter):
            widget.valueChanged.connect(self.apply_scheme)
        self.pattern.currentIndexChanged.connect(self.apply_scheme)
        self.add_species.clicked.connect(self.on_add_species)
        self.remove_species.clicked.connect(self.on_remove_species)
        self.open_catalog_button.clicked.connect(self.open_catalog)
        self.save_catalog_button.clicked.connect(self.save_catalog)
        self.species_table.itemChanged.connect(self.on_table_edited)
        state.changed.connect(self.refresh)
        self.reload_catalog()

    @staticmethod
    def _spin(value, suffix, maximum=1000.0):
        spin = QDoubleSpinBox()
        spin.setRange(0.0, maximum)
        spin.setDecimals(3)
        spin.setValue(value)
        spin.setSuffix(suffix)
        return spin

    def reload_catalog(self) -> None:
        current = self.species_key.currentText()
        self.species_key.clear()
        records = self.state.catalog.all()
        for record in records:
            self.species_key.addItem(record.name or record.key, record.key)
        if current:
            self.species_key.setEditText(current)
        with_distance = sum(1 for record in records
                            if record.min_distance_m > 0.0)
        if not records:
            self.catalog_label.setText(tr("catalogo vuoto"))
        else:
            self.catalog_label.setText(tr(
                "{0} specie in catalogo, {1} con una distanza minima "
                "propria").format(len(records), with_distance))
        self.refresh()

    # -- the catalogue -----------------------------------------------------

    def open_catalog(self, *_args, path: str = ""):
        """Read a species catalogue an operator prepared, as a GeoPackage.

        Without one, a species typed into the combo is a key and a name and
        nothing else -- no minimum distance, no elevation band, no slope
        limit. The naturaliform pass enforces each species' own distance,
        and until there is a catalogue it has nothing to enforce.
        """
        if not path:
            path, _filter = QFileDialog.getOpenFileName(
                self, tr("Apri il catalogo delle specie"), "",
                "GeoPackage (*.gpkg)")
        if not path:
            return None
        try:
            catalog = species_mod.SpeciesCatalog.open(path)
        except GeoCadError as exc:
            self.warn(exc)
            self.catalog_label.setText(exc.formatted())
            return None
        self.state.catalog = catalog
        self.reload_catalog()
        self.state.refresh_status()
        return catalog

    def save_catalog(self, *_args, path: str = "") -> str:
        """Write the catalogue out, so the next project starts with it."""
        records = self.state.catalog.all()
        if not records:
            self.warn(GeoCadError(
                "empty catalogue",
                user_message=tr("Non c'e' ancora nessuna specie da "
                                "salvare.")))
            return ""
        if not path:
            path, _filter = QFileDialog.getSaveFileName(
                self, tr("Salva il catalogo delle specie"), "specie.gpkg",
                "GeoPackage (*.gpkg)")
        if not path:
            return ""
        try:
            written = species_mod.SpeciesCatalog.create(path, records,
                                                        overwrite=True)
        except GeoCadError as exc:
            self.warn(exc)
            return ""
        self.say(tr("Catalogo salvato in {0}").format(
            getattr(written, "path", path) or path))
        return getattr(written, "path", path) or path

    def on_table_edited(self, item) -> bool:
        """The minimum distance typed in the table goes into the catalogue."""
        if item is None or item.column() != 2:
            return False
        key_item = self.species_table.item(item.row(), 0)
        if key_item is None:
            return False
        key = key_item.data(Qt.ItemDataRole.UserRole) or key_item.text()
        try:
            distance = float(str(item.text()).replace(",", ".") or 0.0)
        except ValueError:
            self.refresh()
            return False
        try:
            record = self.state.catalog.get(str(key))
        except GeoCadError:
            return False
        if abs(record.min_distance_m - distance) < 1e-9:
            return False
        record.min_distance_m = max(0.0, distance)
        self.state.catalog.update(record)
        self.state.checkpoint()
        self.state.refresh_status()
        return True

    def apply_scheme(self, *_args) -> None:
        """Rebuild the spec from the widgets. Nothing is generated here."""
        try:
            self.state.spec = spacing_mod.SlopeSpacing(
                plant_distance_m=max(0.01, self.plant_distance.value()),
                row_distance_m=max(0.01, self.row_distance.value()),
                row_azimuth_deg=self.azimuth.value(),
                pattern=self.pattern.currentData(),
                margin_m=self.margin.value(),
                jitter_m=self.jitter.value(),
                custom_offsets=(0.0, 0.5)
                if self.pattern.currentData() == spacing_mod.PATTERN_CUSTOM
                else ())
        except GeoCadError as exc:
            self.warn(exc)
            return
        self.state.scheme_chosen = True
        self.state.checkpoint()
        self.state.refresh_status()

    def on_add_species(self) -> bool:
        # An editable combo keeps the *index's* data after the text is typed
        # over it, so a freshly typed name would silently add whichever
        # species happened to be selected. The text wins when it differs.
        typed = self.species_key.currentText().strip()
        chosen = self.species_key.currentData()
        index = self.species_key.currentIndex()
        stale = (index >= 0
                 and typed != self.species_key.itemText(index).strip())
        key = (typed if (stale or not chosen) else str(chosen)).strip()
        if not key:
            return False
        if not self.state.catalog.has(key):
            self.state.catalog.add(species_mod.Species(key=key))
            self.reload_catalog()
        self.state.shares = [s for s in self.state.shares if s[0] != key]
        self.state.shares.append((key, self.species_percent.value()))
        self.state.checkpoint()
        self.state.refresh_status()
        return True

    def on_remove_species(self) -> bool:
        row = self.species_table.currentRow()
        if row < 0 or row >= len(self.state.shares):
            return False
        self.state.shares.pop(row)
        self.state.checkpoint()
        self.state.refresh_status()
        return True

    def refresh(self) -> None:
        self.density_label.setText("{0:,.0f} piante/ha".format(
            self.state.density_per_ha()))
        # Filling the table fires itemChanged for every cell written, and
        # the handler writes back into the catalogue: blocked, or typing one
        # number would rewrite the rest.
        self.species_table.blockSignals(True)
        self.species_table.setRowCount(len(self.state.shares))
        keys = [key for key, _percent in self.state.shares]
        for row, (key, percent) in enumerate(self.state.shares):
            try:
                record = self.state.catalog.get(key)
            except GeoCadError:
                record = None
            item = QTableWidgetItem(record.name if record is not None
                                    and record.name else key)
            item.setData(Qt.ItemDataRole.UserRole, key)
            # The colour is the one the layer will use: same module, same
            # golden-angle sequence, same order.
            item.setIcon(swatch(symbology_mod.palette(keys)[key]))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.species_table.setItem(row, 0, item)
            share_item = QTableWidgetItem("{0:g}".format(percent))
            share_item.setFlags(share_item.flags()
                                & ~Qt.ItemFlag.ItemIsEditable)
            self.species_table.setItem(row, 1, share_item)
            distance = record.min_distance_m if record is not None else 0.0
            distance_item = QTableWidgetItem("{0:g}".format(distance))
            distance_item.setToolTip(tr(
                "Distanza minima propria della specie: la usa il passaggio "
                "naturaliforme. Zero significa nessuna richiesta."))
            self.species_table.setItem(row, 2, distance_item)
        self.species_table.blockSignals(False)
        self.mix_chart.set_rows(self.state.mix_rows())
        total = sum(percent for _key, percent in self.state.shares)
        self.total_label.setText(tr("Totale: {0:g} %").format(total))


class OrientationPanel(Panel):
    """Step 7: which way the rows run."""

    def __init__(self, state, parent=None):
        super().__init__(tr("Orientamento delle file"), state, parent)
        form = QFormLayout()
        self.mode = QComboBox()
        for key in orient_mod.MODES:
            self.mode.addItem(orient_mod.MODE_LABELS[key], key)
        self.alignment = QComboBox()
        for key in orient_mod.ALIGNMENTS:
            self.alignment.addItem(orient_mod.ALIGNMENT_LABELS[key], key)
        self.manual = QDoubleSpinBox()
        self.manual.setRange(0.0, 360.0)
        self.manual.setSuffix(" deg")
        form.addRow(tr("Criterio"), self.mode)
        form.addRow(tr("Allineamento"), self.alignment)
        form.addRow(tr("Azimut manuale"), self.manual)
        self.result_label = QLabel(DASH)
        form.addRow(tr("Azimut file"), self.result_label)
        self.layout.addLayout(form)
        self.apply_button = QPushButton(tr("Applica al sesto"))
        self.layout.addWidget(self.apply_button)
        self.layout.addStretch(1)
        self.apply_button.clicked.connect(self.apply)

    def compute(self) -> float:
        return orient_mod.resolve(
            self.mode.currentData(), self.manual.value(),
            terrain=self.state.terrain,
            geometry=self.state.usable_geometry(),
            alignment=self.alignment.currentData())

    def apply(self) -> float:
        azimuth = self.compute()
        self.result_label.setText("{0:.1f} deg".format(azimuth))
        self.state.spec.row_azimuth_deg = azimuth
        self.state.orientation_applied = True
        self.state.refresh_status()
        return azimuth


class GeneratePanel(Panel):
    """Step 8: preview, then commit."""

    def __init__(self, state, parent=None):
        super().__init__(tr("Generazione impianto"), state, parent)
        form = QFormLayout()
        self.usable_label = QLabel(DASH)
        self.density_label = QLabel(DASH)
        self.expected_label = QLabel(DASH)
        self.actual_label = QLabel(DASH)
        form.addRow(tr("Superficie utile"), self.usable_label)
        form.addRow(tr("Densita'"), self.density_label)
        form.addRow(tr("Piante previste"), self.expected_label)
        form.addRow(tr("Piante generate"), self.actual_label)
        self.layout.addLayout(form)

        self.preview_button = QPushButton(tr("Genera Anteprima"))
        self.generate_button = QPushButton(tr("GENERA IMPIANTO"))
        self.layout.addWidget(self.preview_button)
        self.layout.addWidget(self.generate_button)
        self.layout.addStretch(1)

        self.clear_preview_button = QPushButton(tr("Togli anteprima"))
        self.clear_preview_button.setEnabled(False)
        self.layout.addWidget(self.clear_preview_button)
        self.preview_label = QLabel(tr("nessuna anteprima sulla mappa"))
        self.preview_label.setWordWrap(True)
        theme_mod.mark(self.preview_label, muted=True)
        self.layout.addWidget(self.preview_label)
        theme_mod.mark(self.generate_button, primary=True)

        self.flight_button = QPushButton(tr("Genera missione UAV"))
        self.flight_button.setToolTip(tr(
            "Passa l'area utile, le esclusioni e l'orientamento dei filari "
            "al pianificatore di volo, con il preset forestale gia' "
            "impostato. Non c'e' niente da riscrivere a mano."))
        self.flight_button.setEnabled(False)
        self.layout.addWidget(self.flight_button)

        self.preview_button.clicked.connect(self.preview)
        self.clear_preview_button.clicked.connect(self.clear_preview)
        self.generate_button.clicked.connect(self.generate)
        self.flight_button.clicked.connect(self.start_flight)
        state.regenerateRequested.connect(self.preview)
        state.changed.connect(self.refresh)

    def preview(self):
        """Run the generator and keep the result; no layer is created.

        With zones defined, each one is generated with its own scheme and
        its own mix and the pieces are kept apart; without them the whole
        usable surface is one scheme. The panel does not choose differently
        for the two cases -- the plan object answers the same questions.
        """
        geometry = self.state.usable_geometry()
        if geometry is None:
            self.warn(GeoCadError(
                "no area", user_message=tr("Definisci prima l'area.")))
            return None
        if self.state.along_contours:
            return self.preview_contours(geometry)
        if len(self.state.zones):
            try:
                result = zones_mod.plant(self.state.zones,
                                         terrain=self.state.terrain)
            except GeoCadError as exc:
                self.warn(exc)
                return None
            self.state.composition = None
            self.naturalise(result, geometry)
            self.state.result = result
            self.state.verified = False
            self.state.anomalies = []
            self.state.checkpoint()
            self.show_preview(result)
            self.state.refresh_status()
            return result
        try:
            result = spacing_mod.generate(geometry, self.state.spec,
                                          terrain=self.state.terrain)
        except GeoCadError as exc:
            self.warn(exc)
            return None
        mix = self.state.mix()
        if mix is not None:
            self.state.composition = composition_mod.assign(result.plants, mix)
        self.naturalise(result, geometry)
        self.state.result = result
        self.state.verified = False
        self.state.anomalies = []
        self.state.checkpoint()
        self.show_preview(result)
        self.state.refresh_status()
        return result

    def show_preview(self, result):
        """Put the plan on the canvas, before anything is committed."""
        geometry = self.state.usable_geometry()
        drawn = self.state.preview.show(
            result, geometry, self.state.crs,
            azimuth_deg=self.state.spec.row_azimuth_deg)
        showing = self.state.preview.is_showing()
        self.clear_preview_button.setEnabled(showing)
        if result is None:
            self.preview_label.setText(tr("nessuna anteprima sulla mappa"))
        else:
            self.preview_label.setText(tr(
                "anteprima: {0:,} piante, {1:,.0f} piante/ha, sesto {2}"
            ).format(result.count, result.density_per_ha(),
                     spacing_mod.PATTERN_LABELS.get(self.state.spec.pattern,
                                                    self.state.spec.pattern)))
        return drawn

    def clear_preview(self, *_args) -> int:
        removed = self.state.preview.clear()
        self.clear_preview_button.setEnabled(False)
        self.preview_label.setText(tr("nessuna anteprima sulla mappa"))
        return removed

    def preview_contours(self, geometry):
        """Plant along the contour lines the Terreno panel extracted.

        The rows here are not generated: they are the lines of the DEM, cut
        to the project, which the operator has already looked at on the map.
        What this adds is the spacing along them, corrected by the slope
        measured *along the line* -- a contour is almost level, so the
        correction is small and true, where the maximum local slope would
        shorten every step by the whole hillside angle.
        """
        from qgis.core import QgsPoint                           # noqa: PLC0415

        state = self.state
        if len(state.zones):
            self.warn(GeoCadError(
                "contour planting is not split by zone",
                user_message=tr("Le file su curve di livello si generano "
                                "sull'intera superficie utile: togli le zone "
                                "oppure scegli un altro sesto.")))
            return None
        if not state.contours:
            self.warn(GeoCadError(
                "no contours extracted",
                user_message=tr("Estrai prima le curve di livello dal "
                                "pannello Terreno.")))
            return None
        engine = QgsGeometry.createGeometryEngine(geometry.constGet())
        engine.prepareGeometry()

        def inside(x, y):
            return engine.intersects(QgsPoint(float(x), float(y)))

        try:
            plants = curves_mod.plant_along_contours(
                state.contours, state.terrain, state.spec.plant_distance_m,
                stagger=True, inside=inside)
        except GeoCadError as exc:
            self.warn(exc)
            return None
        result = spacing_mod.SlopeGridResult(
            plants=plants, spec=state.spec,
            usable_area_m2=float(geometry.area()))
        mix = state.mix()
        state.composition = (composition_mod.assign(result.plants, mix)
                             if mix is not None else None)
        self.naturalise(result, geometry)
        state.result = result
        state.verified = False
        state.anomalies = []
        state.layers.draw_contours(state.contours)
        state.checkpoint()
        self.show_preview(result)
        state.refresh_status()
        return result

    def naturalise(self, result, geometry) -> None:
        """Open the glades and thin the stand, in place on the plan.

        Runs on whatever the generator produced -- one area or many zones --
        because the passes work on plants, not on polygons. The plan's own
        plant list is replaced by the survivors, so everything downstream
        (layer, validation, report) sees the stand that will actually be
        planted.
        """
        settings = self.state.natural
        self.state.natural_outcome = None
        self.state.glades = []
        if settings is None or not settings.is_active or not result.plants:
            return
        glades = []
        if settings.glade_count and geometry is not None:
            try:
                glades = natural_mod.generate_glades(geometry, settings)
            except GeoCadError as exc:
                self.warn(exc)
                glades = []
        outcome = natural_mod.naturalise(
            result.plants, settings, self.state.spec.plant_distance_m,
            glades=glades, catalog=self.state.catalog)
        result.plants = outcome.plants
        if hasattr(result, "per_zone"):
            counts = {}
            for record in outcome.plants:
                name = zones_mod.zone_of(record)
                counts[name] = counts.get(name, 0) + 1
            for name, entry in result.per_zone.items():
                entry["plants"] = counts.get(name, 0)
                area = entry.get("area_m2", 0.0)
                entry["density_per_ha"] = (
                    entry["plants"] / (area / M2_PER_HA) if area > 0.0
                    else 0.0)
        self.state.natural_outcome = outcome
        self.state.glades = glades
        self.state.glades_placed = bool(glades)
        self.state.layers.draw_glades(glades)
        self.state.layers.refresh_canvas()

    def _flight_dem(self):
        """The raster the flight should read, as a layer of the project.

        The one the operator chose, when the terrain came from the project.
        Otherwise the warped working grid, adopted here: it is a temporary
        GeoTIFF nobody added, and a layer outside the project cannot be
        selected in a layer combo -- handing it over without adopting it
        would leave the flight step showing "(nessun DEM)".
        """
        state = self.state
        if state.terrain is None:
            return None
        chosen = QgsProject.instance().mapLayer(state.dem_layer_id or "")
        if chosen is not None:
            return chosen
        layer = state.terrain.raster_layer()
        if layer is None:
            return None
        if QgsProject.instance().mapLayer(layer.id()) is None:
            QgsProject.instance().addMapLayer(layer)
        return layer

    def start_flight(self) -> bool:
        """Hand the finished planting block to the flight planner.

        Reads what the project already holds through ``uav.forest_link`` --
        the usable surface, the exclusions and the orientation the rows were
        laid out at -- puts it on the flight planner and leaves the operator
        on the flight area step. Nothing is re-entered, so nothing can be
        re-entered wrongly.
        """
        state = self.state
        if state.area is None or state.crs is None:
            self.say(tr("Definisci prima l'area del progetto."))
            return False
        panel = state.uav
        if panel is None:
            self.say(tr("Il pianificatore di volo non e' disponibile."))
            return False
        azimuth = None
        if state.orientation_applied and state.spec is not None:
            # The bearing the rows RUN at (SlopeSpacing.row_azimuth_deg),
            # not the one the lattice steps along: the imagery has to line
            # up with what is on the ground.
            azimuth = float(state.spec.row_azimuth_deg)
        try:
            survey = forest_link_mod.survey_from_area(
                state.area, state.crs.authid(), row_azimuth_deg=azimuth,
                plant_count=(state.result.count
                             if state.result is not None else 0))
        except forest_link_mod.ForestLinkError as exc:
            self.warn(str(exc))
            return False

        panel.extent.set_extent(survey.aoi_geom, state.crs)
        panel.dem_combo.setLayer(self._flight_dem())
        overlap = forest_link_mod.forest_overlap()
        front, side = overlap.as_percent
        panel.frontlap.setValue(front)
        panel.sidelap.setValue(side)
        if azimuth is not None:
            index = panel.azimuth_mode.findData("manual")
            if index >= 0:
                panel.azimuth_mode.setCurrentIndex(index)
            panel.azimuth.setValue(azimuth % 360.0)
        else:
            index = panel.azimuth_mode.findData("optimised")
            if index >= 0:
                panel.azimuth_mode.setCurrentIndex(index)
        panel.recompute()
        self.say(tr("Missione UAV impostata sull'area utile ({0}). "
                    "Preset forestale: sovrapposizione {1:.0f}/{2:.0f} %.")
                 .format(ha(survey.area_m2), front, side))
        state.stepRequested.emit(uav_mod.STEP_AREA)
        return True

    def generate(self):
        """Commit the preview to a real PointZ layer, coloured by species."""
        result = self.state.result or self.preview()
        if result is None:
            return None
        crs = self.state.crs
        layer = spacing_mod.plants_layer(
            result, crs.authid() if crs else "",
            name=tr("Piante"), zone=tr("Progetto"))
        self.clear_preview()
        QgsProject.instance().addMapLayer(layer)
        self.state.plants_layer = layer
        self.state.layers.adopt("plants", layer)
        self.state.layers.refresh_canvas()
        self.state.refresh_status()
        return layer

    def refresh(self) -> None:
        self.usable_label.setText(ha(self.state.usable_m2)
                                  if self.state.area else DASH)
        self.density_label.setText("{0:,.0f} piante/ha".format(
            self.state.density_per_ha()))
        self.expected_label.setText("{0:,.0f}".format(
            self.state.expected_plants()) if self.state.area else DASH)
        self.actual_label.setText("{0:,}".format(self.state.result.count)
                                  if self.state.result else DASH)
        # There is nothing to fly over until there is a usable surface.
        self.flight_button.setEnabled(
            self.state.area is not None and self.state.usable_m2 > 0.0)


class NaturalPanel(Panel):
    """Step 9: making the stand look like a wood rather than an orchard.

    Three different things, applied in this order and reported separately,
    because an operator has to be able to tell which one cost them plants:

    * **radure** -- real openings, cut out of the stand;
    * **irregolarita'** -- the rows thinned at random within a bound, which
      never invents a position outside the scheme;
    * **distanza minima** -- the floor each species needs, enforced starting
      from the most demanding, because taken in planting order a species
      asking 7 m on a 4 m scheme loses every comparison and disappears.

    They are applied by the generator, on whatever it produced -- one area
    or many zones -- so this panel sets them and reads back what they did.
    """

    def __init__(self, state, parent=None):
        super().__init__(tr("Impianto naturaliforme"), state, parent)

        glades = QGroupBox(tr("Radure"))
        glades_form = QFormLayout(glades)
        self.glade_count = QSpinBox()
        self.glade_count.setRange(0, 200)
        self.glade_radius = self._spin(10.0, " m")
        self.glade_margin = self._spin(5.0, " m")
        glades_form.addRow(tr("Numero"), self.glade_count)
        glades_form.addRow(tr("Raggio"), self.glade_radius)
        glades_form.addRow(tr("Distacco dal bordo"), self.glade_margin)
        self.layout.addWidget(glades)

        variation = QGroupBox(tr("Irregolarita' e distanze"))
        variation_form = QFormLayout(variation)
        self.irregularity = QDoubleSpinBox()
        self.irregularity.setRange(0.0, natural_mod.MAX_AMPLITUDE * 100.0)
        self.irregularity.setSuffix(" %")
        self.min_distance = self._spin(0.0, " m")
        self.natural_seed = QSpinBox()
        self.natural_seed.setRange(0, 999999)
        variation_form.addRow(tr("Diradamento casuale"), self.irregularity)
        variation_form.addRow(tr("Distanza minima"), self.min_distance)
        variation_form.addRow(tr("Seme"), self.natural_seed)
        self.layout.addWidget(variation)

        self.apply_button = QPushButton(tr("Applica al progetto"))
        self.layout.addWidget(self.apply_button)

        outcome = QGroupBox(tr("Esito"))
        outcome_form = QFormLayout(outcome)
        self.glades_label = QLabel(DASH)
        self.removed_label = QLabel(DASH)
        self.measured_label = QLabel(DASH)
        outcome_form.addRow(tr("Radure collocate"), self.glades_label)
        outcome_form.addRow(tr("Piante tolte"), self.removed_label)
        outcome_form.addRow(tr("Distanza minima misurata"),
                            self.measured_label)
        self.natural_label = QLabel(tr("nessuna"))
        self.natural_label.setWordWrap(True)
        outcome_form.addRow(tr("Riepilogo"), self.natural_label)
        self.layout.addWidget(outcome)
        self.layout.addStretch(1)

        for widget in (self.glade_count, self.glade_radius, self.glade_margin,
                       self.irregularity, self.min_distance,
                       self.natural_seed):
            widget.valueChanged.connect(self.apply_settings)
        self.apply_button.clicked.connect(self.apply_to_project)
        state.changed.connect(self.refresh)

    def _spin(self, value: float, suffix: str = "") -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 10_000.0)
        spin.setDecimals(2)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        return spin

    def settings(self):
        return natural_mod.NaturalSettings(
            glade_count=self.glade_count.value(),
            glade_radius_m=self.glade_radius.value(),
            glade_margin_m=self.glade_margin.value(),
            glade_gap_m=self.glade_margin.value(),
            amplitude=self.irregularity.value() / 100.0,
            min_distance_m=self.min_distance.value(),
            seed=self.natural_seed.value())

    def apply_settings(self, *_args) -> bool:
        """Keep the project's settings in step with the controls."""
        try:
            self.state.natural = self.settings()
        except GeoCadError as exc:
            self.warn(exc)
            return False
        self.state.refresh_status()
        return True

    def apply_to_project(self, *_args):
        """Re-generate the plan with these settings and show what they did.

        Re-generated rather than applied on top: thinning an already thinned
        stand would take the plants twice, and an operator who lowers the
        irregularity expects more plants back, not fewer.
        """
        if not self.apply_settings():
            return None
        if self.state.result is None:
            self.warn(GeoCadError(
                "nothing generated yet",
                user_message=tr("Genera prima l'impianto.")))
            return None
        self.state.regenerateRequested.emit()
        self.refresh()
        return self.state.result

    def refresh(self) -> None:
        outcome = self.state.natural_outcome
        self.glades_label.setText("{0:,}".format(len(self.state.glades))
                                  if self.state.glades else DASH)
        if outcome is None or not outcome.removed_total:
            self.removed_label.setText(DASH)
            self.measured_label.setText(DASH)
            self.natural_label.setText(tr("nessuna"))
            return
        self.removed_label.setText("{0:,}".format(outcome.removed_total))
        self.measured_label.setText("{0:.2f} m".format(
            outcome.measured_min_distance_m))
        self.natural_label.setText(tr(
            "{0:,} piante tolte, minima misurata {1:.2f} m").format(
                outcome.removed_total, outcome.measured_min_distance_m))


class OptimisePanel(Panel):
    """Step 9: the same area under three schemes, compared."""

    def __init__(self, state, parent=None):
        super().__init__(tr("Ottimizzazione"), state, parent)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [tr("Scenario"), tr("Sesto"), tr("Densita'"), tr("Piante"),
             tr("Utilizzo")])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.layout.addWidget(self.table)
        self.run_button = QPushButton(tr("Confronta scenari"))
        self.apply_button = QPushButton(tr("Applica scenario scelto"))
        self.layout.addWidget(self.run_button)
        self.layout.addWidget(self.apply_button)
        self.layout.addStretch(1)
        self.run_button.clicked.connect(self.run)
        self.apply_button.clicked.connect(self.apply_selected)
        state.changed.connect(self.refresh)

    def run(self):
        """Three real generations, not three estimates."""
        geometry = self.state.usable_geometry()
        if geometry is None:
            self.warn(GeoCadError(
                "no area", user_message=tr("Definisci prima l'area.")))
            return []
        base = self.state.spec
        usable = float(geometry.area()) or 1.0
        scenarios = []
        for name, factor in (("A", 0.85), ("B", 1.0), ("C", 1.25)):
            spec = spacing_mod.SlopeSpacing(
                plant_distance_m=base.plant_distance_m * factor,
                row_distance_m=base.row_distance_m * factor,
                row_azimuth_deg=base.row_azimuth_deg, pattern=base.pattern,
                margin_m=base.margin_m, step_mode=base.step_mode)
            try:
                if self.state.along_contours:
                    count = self.count_along_contours(spec.plant_distance_m)
                    density = count / (usable / M2_PER_HA)
                    length = self.state.contour_length_m() or 1.0
                    layout = tr("{0:.2f} m sulle curve").format(
                        spec.plant_distance_m)
                    usage = count * spec.plant_distance_m / length
                else:
                    result = spacing_mod.generate(geometry, spec,
                                                  terrain=self.state.terrain)
                    count = result.count
                    density = result.density_per_ha()
                    layout = "{0:.2f} x {1:.2f} m".format(
                        spec.plant_distance_m, spec.row_distance_m)
                    usage = (count * spec.plant_distance_m
                             * spec.row_distance_m / usable)
            except GeoCadError as exc:
                self.warn(exc)
                continue
            scenarios.append({"name": name, "spec": spec, "plants": count,
                              "density": density, "sesto": layout,
                              "utilizzo": usage})
        self.state.scenarios = scenarios
        self.state.refresh_status()
        return scenarios

    def count_along_contours(self, step_m: float) -> int:
        """How many plants this step would put on the contours, generated.

        Generated, not estimated: the same function the plan uses, on copies
        of the rows so that comparing scenarios does not overwrite the plants
        the current plan put on them.
        """
        if not self.state.contours:
            raise GeoCadError(
                "no contours extracted",
                user_message=tr("Estrai prima le curve di livello dal "
                                "pannello Terreno."))
        rows = [curves_mod.ContourRow(elevation_m=row.elevation_m,
                                      geometry=row.geometry,
                                      length_m=row.length_m)
                for row in self.state.contours]
        return len(curves_mod.plant_along_contours(
            rows, self.state.terrain, step_m, stagger=True))

    def apply_selected(self) -> bool:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.state.scenarios):
            return False
        self.state.spec = self.state.scenarios[row]["spec"]
        self.state.result = None
        self.state.refresh_status()
        return True

    def refresh(self) -> None:
        self.table.setRowCount(len(self.state.scenarios))
        usable = self.state.usable_m2 or 1.0
        for row, scenario in enumerate(self.state.scenarios):
            spec = scenario["spec"]
            values = (scenario["name"],
                      scenario.get("sesto") or "{0:.2f} x {1:.2f} m".format(
                          spec.plant_distance_m, spec.row_distance_m),
                      "{0:,.0f}/ha".format(scenario["density"]),
                      "{0:,}".format(scenario["plants"]),
                      "{0:.1%}".format(scenario.get(
                          "utilizzo",
                          scenario["plants"] * spec.plant_distance_m
                          * spec.row_distance_m / usable)))
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))


class VerifyPanel(Panel):
    """Step 10: conforme, or the list of what is not."""

    def __init__(self, state, parent=None):
        super().__init__(tr("Verifica"), state, parent)
        self.verdict = QLabel(DASH)
        self.verdict.setWordWrap(True)
        self.layout.addWidget(self.verdict)
        self.report = QTextBrowser()
        self.layout.addWidget(self.report)
        self.run_button = QPushButton(tr("Verifica progetto"))
        self.layout.addWidget(self.run_button)
        self.run_button.clicked.connect(self.run)

    def run(self) -> list:
        """Ask the project to check itself, and show the answer."""
        if self.state.result is None or self.state.usable_geometry() is None:
            self.verdict.setText(tr("Genera prima l'impianto."))
            self.state.verified = False
            return []
        anomalies = self.state.validate()
        self.show_verdict(anomalies)
        return anomalies

    def show_verdict(self, anomalies) -> None:
        if anomalies:
            self.verdict.setText(
                "<b style='color:{0}'>{1}</b>".format(
                    STATE_COLORS[WARNING],
                    tr("{0} ANOMALIE").format(len(anomalies))))
            self.report.setPlainText(
                "\n".join("- " + a for a in anomalies))
        else:
            self.verdict.setText("<b style='color:{0}'>{1}</b>".format(
                STATE_COLORS[DONE], tr("PROGETTO CONFORME")))
            self.report.setPlainText(tr(
                "Area, vincoli, distanze, densita', specie e geometrie "
                "verificate."))

class EditPanel(Panel):
    """Step 11: the plan, changed on the map, and re-checked.

    A generated plan is a proposal. The operator knows there is a rock where
    plant 412 landed, that the corner by the track wants three more, and that
    the row along the ditch should be oaks. Until they can do that on the map
    and see the numbers follow, the plan is a picture.

    The editing itself is QGIS's own: the layer is put into edit mode and the
    standard digitising tools move, add and delete. This panel does the part
    QGIS cannot know about -- take the plan back off the layer afterwards,
    re-read the ground under every plant that moved, recount the mix, and run
    the same checks the Verify step runs.
    """

    def __init__(self, state, parent=None):
        super().__init__(tr("Editing dell'impianto"), state, parent)

        form = QFormLayout()
        self.count_label = QLabel(DASH)
        self.added_label = QLabel(DASH)
        self.mode_label = QLabel(tr("non modificabile"))
        form.addRow(tr("Piante"), self.count_label)
        form.addRow(tr("Aggiunte a mano"), self.added_label)
        form.addRow(tr("Stato layer"), self.mode_label)
        self.layout.addLayout(form)

        self.edit_button = QPushButton(tr("Abilita modifica"))
        self.edit_button.setCheckable(True)
        self.layout.addWidget(self.edit_button)

        tools = QGroupBox(tr("Strumenti QGIS"))
        tools_row = QHBoxLayout(tools)
        self.add_button = QPushButton(tr("Aggiungi"))
        self.move_button = QPushButton(tr("Sposta"))
        self.delete_button = QPushButton(tr("Elimina"))
        for button in (self.add_button, self.move_button, self.delete_button):
            button.setEnabled(False)
            tools_row.addWidget(button)
        self.layout.addWidget(tools)

        species = QGroupBox(tr("Specie della selezione"))
        species_form = QFormLayout(species)
        self.species_combo = QComboBox()
        self.assign_button = QPushButton(tr("Assegna alla selezione"))
        species_form.addRow(tr("Specie"), self.species_combo)
        species_form.addRow(self.assign_button)
        self.selection_label = QLabel(tr("nessuna pianta selezionata"))
        species_form.addRow(tr("Selezione"), self.selection_label)
        self.layout.addWidget(species)

        self.apply_button = QPushButton(tr("Applica modifiche e rivalida"))
        self.layout.addWidget(self.apply_button)
        self.outcome = QTextBrowser()
        self.outcome.setMaximumHeight(150)
        self.layout.addWidget(self.outcome)
        self.layout.addStretch(1)

        self.edit_button.toggled.connect(self.set_editing)
        self.add_button.clicked.connect(lambda: self.trigger("AddFeature"))
        self.move_button.clicked.connect(lambda: self.trigger("MoveFeature"))
        self.delete_button.clicked.connect(self.delete_selected)
        self.assign_button.clicked.connect(self.assign_species)
        self.apply_button.clicked.connect(self.apply_edits)
        state.changed.connect(self.refresh)

    # -- the layer, and QGIS's own tools -----------------------------------

    def layer(self):
        return self.state.plants_layer

    def set_editing(self, on: bool) -> bool:
        """Put the plants layer into QGIS edit mode, or take it out.

        Leaving edit mode commits: an operator who pressed the button to
        stop editing means the changes to be kept, and a layer left dirty
        would ask them again on the way out of QGIS.
        """
        layer = self.layer()
        if layer is None:
            self.edit_button.setChecked(False)
            self.warn(GeoCadError(
                "no plants layer",
                user_message=tr("Genera prima l'impianto.")))
            return False
        if on and not layer.isEditable():
            layer.startEditing()
        elif not on and layer.isEditable():
            layer.commitChanges()
            self.apply_edits()
        if self.state.iface is not None:
            try:
                self.state.iface.setActiveLayer(layer)
            except (AttributeError, RuntimeError):
                pass
        self.refresh()
        return bool(layer.isEditable())

    def trigger(self, action_name: str) -> bool:
        """Fire one of QGIS's digitising actions on the plants layer.

        QGIS's own tools, not a second digitiser: the map tool that adds a
        point here is the one the operator already knows, with the same
        snapping and the same undo stack.
        """
        layer = self.layer()
        iface = self.state.iface
        if layer is None or iface is None:
            return False
        if not layer.isEditable():
            layer.startEditing()
        try:
            iface.setActiveLayer(layer)
            getattr(iface, "action" + action_name)().trigger()
        except (AttributeError, RuntimeError):
            return False
        return True

    def delete_selected(self) -> int:
        """Remove the selected plants. Nothing selected removes nothing."""
        layer = self.layer()
        if layer is None:
            return 0
        chosen = list(layer.selectedFeatureIds())
        if not chosen:
            self.warn(GeoCadError(
                "nothing selected",
                user_message=tr("Seleziona prima le piante da eliminare.")))
            return 0
        started = not layer.isEditable()
        if started:
            layer.startEditing()
        layer.deleteFeatures(chosen)
        if started:
            layer.commitChanges()
        self.state.edited = True
        layer.triggerRepaint()
        self.apply_edits()
        return len(chosen)

    # -- species -----------------------------------------------------------

    def reload_species(self) -> int:
        current = self.species_combo.currentData()
        self.species_combo.clear()
        for key, _percent in self.state.shares:
            try:
                record = self.state.catalog.get(key)
            except GeoCadError:
                record = None       # a key the catalogue never met: show it
            self.species_combo.addItem(
                record.name if record is not None and record.name else key,
                key)
        if current is not None:
            index = self.species_combo.findData(current)
            if index >= 0:
                self.species_combo.setCurrentIndex(index)
        return self.species_combo.count()

    def assign_species(self) -> int:
        layer = self.layer()
        if layer is None:
            return 0
        chosen = list(layer.selectedFeatureIds())
        if not chosen:
            self.warn(GeoCadError(
                "nothing selected",
                user_message=tr("Seleziona prima le piante da cambiare.")))
            return 0
        key = self.species_combo.currentData()
        if not key:
            self.warn(GeoCadError(
                "no species chosen",
                user_message=tr("Scegli prima una specie fra quelle del "
                                "progetto.")))
            return 0
        changed = self.state.set_species_on(chosen, str(key))
        self.apply_edits()
        return changed

    # -- take the plan back ------------------------------------------------

    def apply_edits(self) -> list:
        """Read the layer, recount, and run the project's own checks."""
        synced = self.state.sync_plants_from_layer()
        if synced is None:
            self.outcome.setPlainText(tr("Nessun impianto da modificare."))
            return []
        anomalies = self.state.validate()
        self.state.checkpoint()
        self.show_outcome(synced, anomalies)
        return anomalies

    def show_outcome(self, synced, anomalies) -> None:
        count, added = synced
        lines = [tr("{0:,} piante sul layer, {1:,} aggiunte a mano").format(
            count, added)]
        achieved = composition_mod.achieved_percentages(
            self.state.result.plants if self.state.result else [])
        for key in sorted(achieved):
            lines.append("  {0}: {1:.1f} %".format(key, achieved[key]))
        if anomalies:
            lines.append("")
            lines.append(tr("ANOMALIE"))
            lines.extend("  - " + text for text in anomalies)
        else:
            lines.append("")
            lines.append(tr("Nessuna anomalia dopo la modifica."))
        self.outcome.setPlainText("\n".join(lines))

    def refresh(self) -> None:
        layer = self.layer()
        self.count_label.setText(
            "{0:,}".format(self.state.result.count) if self.state.result
            else DASH)
        self.added_label.setText("{0:,}".format(self.state.added_plants))
        editable = bool(layer is not None and layer.isEditable())
        self.mode_label.setText(tr("in modifica") if editable
                                else tr("non modificabile"))
        if self.edit_button.isChecked() != editable:
            self.edit_button.blockSignals(True)
            self.edit_button.setChecked(editable)
            self.edit_button.blockSignals(False)
        for button in (self.add_button, self.move_button, self.delete_button):
            button.setEnabled(editable)
        self.edit_button.setEnabled(layer is not None)
        selected = len(layer.selectedFeatureIds()) if layer is not None else 0
        self.selection_label.setText(
            tr("{0:,} piante selezionate").format(selected) if selected
            else tr("nessuna pianta selezionata"))
        self.reload_species()


class CartographyPanel(Panel):
    """Step 12: the drawing, on a sheet, with a scale that can be measured.

    A project is delivered on paper. Not a screenshot of the canvas: a sheet
    with a title, a legend naming the colours, a scale bar, a north arrow and
    a note saying which coordinate system the coordinates are in. All of that
    is ``QgsPrintLayout``; this panel chooses the sheet and presses the
    button, and the layout lands in the project's Layout Manager where the
    operator can open it and change anything they like.
    """

    def __init__(self, state, parent=None):
        super().__init__(tr("Cartografia"), state, parent)

        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText(tr("Titolo della tavola"))
        self.subtitle_edit = QLineEdit()
        self.subtitle_edit.setPlaceholderText(tr("Comune, localita', foglio"))
        self.author_edit = QLineEdit()
        self.author_edit.setPlaceholderText(tr("Redatto da"))
        form.addRow(tr("Titolo"), self.title_edit)
        form.addRow(tr("Sottotitolo"), self.subtitle_edit)
        form.addRow(tr("Autore"), self.author_edit)

        self.page_combo = QComboBox()
        for key, label in carto_mod.PAGE_SIZES:
            self.page_combo.addItem(label, key)
        self.page_combo.setCurrentIndex(self.page_combo.findData("A3"))
        self.orientation_combo = QComboBox()
        for key, label in carto_mod.ORIENTATIONS:
            self.orientation_combo.addItem(label, key)
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(0, 500_000)
        self.scale_spin.setSingleStep(500)
        self.scale_spin.setSpecialValueText(tr("adatta al progetto"))
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 1200)
        self.dpi_spin.setValue(carto_mod.DEFAULT_DPI)
        self.grid_spin = QDoubleSpinBox()
        self.grid_spin.setRange(0.0, 10_000.0)
        self.grid_spin.setSuffix(" m")
        self.grid_spin.setSpecialValueText(tr("nessuno"))
        form.addRow(tr("Formato"), self.page_combo)
        form.addRow(tr("Orientamento"), self.orientation_combo)
        form.addRow(tr("Scala"), self.scale_spin)
        form.addRow(tr("Risoluzione"), self.dpi_spin)
        form.addRow(tr("Reticolo"), self.grid_spin)
        self.layout.addLayout(form)

        elements = QGroupBox(tr("Elementi della tavola"))
        elements_row = QHBoxLayout(elements)
        self.legend_check = QCheckBox(tr("Legenda"))
        self.scalebar_check = QCheckBox(tr("Scala grafica"))
        self.north_check = QCheckBox(tr("Nord"))
        for box in (self.legend_check, self.scalebar_check, self.north_check):
            box.setChecked(True)
            elements_row.addWidget(box)
        self.layout.addWidget(elements)

        self.compose_button = QPushButton(tr("Componi tavola"))
        self.open_button = QPushButton(tr("Apri nel compositore"))
        self.open_button.setEnabled(False)
        self.layout.addWidget(self.compose_button)
        self.layout.addWidget(self.open_button)

        exports = QHBoxLayout()
        self.pdf_button = QPushButton(tr("Esporta PDF"))
        self.image_button = QPushButton(tr("Esporta immagine"))
        for button in (self.pdf_button, self.image_button):
            button.setEnabled(False)
            exports.addWidget(button)
        self.layout.addLayout(exports)

        self.outcome = QLabel(tr("nessuna tavola composta"))
        self.outcome.setWordWrap(True)
        self.layout.addWidget(self.outcome)
        self.layout.addStretch(1)

        self.compose_button.clicked.connect(self.compose)
        self.open_button.clicked.connect(self.open_designer)
        self.pdf_button.clicked.connect(self.export_pdf)
        self.image_button.clicked.connect(self.export_image)
        state.changed.connect(self.refresh)

    # -- the sheet ---------------------------------------------------------

    def spec(self):
        return carto_mod.LayoutSpec(
            title=self.title_edit.text().strip()
            or (self.state.area.label if self.state.area else "")
            or tr("Progetto di rimboschimento"),
            subtitle=self.subtitle_edit.text().strip() or self.default_subtitle(),
            author=self.author_edit.text().strip(),
            page=str(self.page_combo.currentData() or "A3"),
            orientation=str(self.orientation_combo.currentData()
                            or carto_mod.ORIENTATION_LANDSCAPE),
            scale=float(self.scale_spin.value()),
            dpi=int(self.dpi_spin.value()),
            legend=self.legend_check.isChecked(),
            scalebar=self.scalebar_check.isChecked(),
            north=self.north_check.isChecked(),
            grid_interval_m=float(self.grid_spin.value()))

    def default_subtitle(self) -> str:
        """What the sheet says when nobody typed anything: the real data."""
        parts = []
        cadastre = self.state.cadastre
        if cadastre is not None and cadastre.n_parcels:
            first = cadastre.shares[0]
            parts.append(first.comune_name or first.parcel.comune_code)
            parts.append(tr("Foglio {0}, particelle {1}").format(
                first.parcel.foglio or DASH, cadastre.n_parcels))
        if self.state.area is not None:
            parts.append(tr("superficie utile {0}").format(
                ha(self.state.usable_m2)))
        if self.state.result is not None:
            parts.append(tr("{0:,} piante").format(self.state.result.count))
        return " - ".join(part for part in parts if part)

    def compose(self, *_args):
        try:
            layout = self.state.compose_layout(self.spec())
        except GeoCadError as exc:
            self.warn(exc)
            self.outcome.setText(exc.formatted())
            return None
        self.refresh()
        return layout

    def open_designer(self, *_args) -> bool:
        """Hand the sheet to QGIS's own layout designer."""
        if self.state.layout is None or self.state.iface is None:
            return False
        try:
            self.state.iface.openLayoutDesigner(self.state.layout)
        except (AttributeError, RuntimeError):
            return False
        return True

    # -- writing it out ----------------------------------------------------

    def export_pdf(self, *_args, path: str = ""):
        return self._export(path, "PDF", "pdf", carto_mod.export_pdf)

    def export_image(self, *_args, path: str = ""):
        def _write(layout, target):
            return carto_mod.export_image(layout, target,
                                          dpi=int(self.dpi_spin.value()))

        return self._export(path, tr("Immagine"), "png", _write)

    def _export(self, path, label, suffix, writer):
        if self.state.layout is None:
            self.warn(GeoCadError(
                "no layout composed",
                user_message=tr("Componi prima la tavola.")))
            return None
        target = path or self.ask_path(label, suffix)
        if not target:
            return None
        try:
            written = writer(self.state.layout, target)
        except GeoCadError as exc:
            self.warn(exc)
            self.outcome.setText(exc.formatted())
            return None
        self.outcome.setText(tr("Tavola scritta in {0}").format(written))
        self.say(tr("Cartografia esportata: {0}").format(written))
        return written

    def ask_path(self, label, suffix) -> str:
        from qgis.PyQt.QtWidgets import QFileDialog              # noqa: PLC0415

        chosen, _filter = QFileDialog.getSaveFileName(
            self, tr("Salva la tavola in {0}").format(label), "",
            "{0} (*.{1})".format(label, suffix))
        if chosen and not chosen.lower().endswith("." + suffix):
            chosen += "." + suffix
        return chosen

    def refresh(self) -> None:
        layout = self.state.layout
        ready = layout is not None
        self.open_button.setEnabled(ready and self.state.iface is not None)
        self.pdf_button.setEnabled(ready)
        self.image_button.setEnabled(ready)
        if not ready:
            self.outcome.setText(tr("nessuna tavola composta"))
            return
        item = layout.itemById(carto_mod.ITEM_MAP)
        scale = item.scale() if item is not None else 0.0
        text = tr("Tavola '{0}' composta, scala 1:{1:,.0f}").format(
            layout.name(), scale).replace(",", ".")
        warnings = list(self.state.layout_spec.warnings)
        if warnings:
            text += " - " + "; ".join(warnings)
        self.outcome.setText(text)


class OutputsPanel(Panel):
    """Step 13: what leaves the plugin -- the layer, and the relazione."""

    #: What each format is, and what it takes to write it properly:
    #: label, OGR driver, suffix, whether it carries an attribute table, and
    #: the layer options the driver needs.
    #:
    #: Two of these were measured, not assumed. DXF is a drawing exchange
    #: format: OGR refuses to create a field on it, and asking anyway failed
    #: the whole export while a perfectly good drawing had already been
    #: written -- so the attributes are not asked for. And the CSV driver
    #: writes *no coordinates at all* unless told: without GEOMETRY=AS_XYZ
    #: an operator got a list of plant numbers and species with nowhere to
    #: plant them.
    FORMATS = (
        ("GeoPackage", "GPKG", ".gpkg", True, []),
        ("Shapefile", "ESRI Shapefile", ".shp", True, []),
        ("GeoJSON", "GeoJSON", ".geojson", True, []),
        ("CSV (X, Y, Z)", "CSV", ".csv", True,
         ["GEOMETRY=AS_XYZ", "SEPARATOR=COMMA"]),
        ("DXF (solo geometrie)", "DXF", ".dxf", False, []),
        ("KML", "KML", ".kml", True, []),
    )

    def __init__(self, state, parent=None):
        super().__init__(tr("Elaborati"), state, parent)

        layers_box = QGroupBox(tr("Dati"))
        layers_form = QFormLayout(layers_box)
        self.format_combo = QComboBox()
        for label, driver, suffix, attributes, layer_options in self.FORMATS:
            self.format_combo.addItem(
                label, (driver, suffix, attributes, list(layer_options)))
        layers_form.addRow(tr("Formato"), self.format_combo)
        self.export_button = QPushButton(tr("Esporta piante"))
        layers_form.addRow(self.export_button)
        self.layout.addWidget(layers_box)

        report_box = QGroupBox(tr("Relazione"))
        report_form = QFormLayout(report_box)
        self.author_edit = QLineEdit()
        self.author_edit.setPlaceholderText(tr("Redatto da"))
        self.document_combo = QComboBox()
        for key, label, _suffix, _writer in docs_mod.FORMATS:
            self.document_combo.addItem(label, key)
        report_form.addRow(tr("Autore"), self.author_edit)
        report_form.addRow(tr("Formato"), self.document_combo)
        buttons = QHBoxLayout()
        self.report_button = QPushButton(tr("Anteprima"))
        self.write_button = QPushButton(tr("Scrivi relazione"))
        buttons.addWidget(self.report_button)
        buttons.addWidget(self.write_button)
        report_form.addRow(buttons)
        self.layout.addWidget(report_box)

        self.report = QTextBrowser()
        self.layout.addWidget(self.report)
        self.export_button.clicked.connect(self.export)
        self.report_button.clicked.connect(self.build_report)
        self.write_button.clicked.connect(self.write_document)

    def export(self, path: str = "") -> str:
        from qgis.core import (QgsCoordinateTransformContext,   # noqa: PLC0415
                               QgsVectorFileWriter)

        layer = self.state.plants_layer
        if layer is None:
            self.warn(GeoCadError(
                "nothing to export",
                user_message=tr("Genera prima l'impianto.")))
            return ""
        (driver, suffix, keeps_attributes,
         layer_options) = self.format_combo.currentData()
        if not path:
            from qgis.PyQt.QtWidgets import QFileDialog         # noqa: PLC0415

            path, _ = QFileDialog.getSaveFileName(
                self, tr("Esporta piante"), "piante" + suffix,
                "*" + suffix)
        if not path:
            return ""
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = driver
        if layer_options:
            options.layerOptions = list(layer_options)
        if not keeps_attributes:
            # Not attributes=[]: an empty list means "all of them". This is
            # the switch that means none, and without it the driver refuses
            # each field in turn and the whole export is reported failed.
            options.skipAttributeCreation = True
        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, path, QgsCoordinateTransformContext(), options)
        if result[0] != QgsVectorFileWriter.NoError:
            self.warn(GeoCadError(
                "export failed: {0}".format(result),
                user_message=tr("Esportazione non riuscita."),
                hint=str(result[1])))
            return ""
        written = result[2] if len(result) > 2 and result[2] else path
        if not keeps_attributes:
            self.say(tr("{0}: il formato non porta attributi. Scritte le "
                        "geometrie delle {1:,} piante; specie, quote e "
                        "identificativi restano negli altri formati.").format(
                            driver, layer.featureCount()))
        return written

    def document(self) -> "docs_mod.Report":
        """The report as blocks: the one thing all three writers read.

        Assembled from what each module says about itself, exactly as the
        text report always was, plus the tables that only make sense as
        tables. Built once and written three ways, so the PDF, the Word file
        and the workbook cannot disagree by a rounding.
        """
        state = self.state
        report = docs_mod.Report(
            title=tr("RELAZIONE TECNICA - PROGETTO DI RIMBOSCHIMENTO"),
            subtitle=(state.area.label if state.area is not None else ""),
            author=self.author_edit.text().strip(),
            date=docs_mod.today())
        if state.area is not None:
            report.lines(state.area.summary())
        if state.cadastre is not None:
            report.lines(state.cadastre.describe())
            if state.cadastre.shares:
                report.table(
                    tr("Particelle catastali"),
                    (tr("Comune"), tr("Belfiore"), tr("Foglio"),
                     tr("Particella"), tr("Sup. catastale (ha)"),
                     tr("Sup. interessata (ha)"), tr("%")),
                    [(row["comune"], row["belfiore"], row["foglio"],
                      row["particella"],
                      round(row["superficie_catastale_m2"] / M2_PER_HA, 4),
                      round(row["superficie_interessata_m2"] / M2_PER_HA, 4),
                      row["percentuale"])
                     for row in state.cadastre.rows()])
        report.lines(state.constraints.describe())
        breakdown = state.constraints.breakdown()
        if breakdown:
            report.table(
                tr("Vincoli e fasce di rispetto"),
                (tr("Vincolo"), tr("Fascia (m)"), tr("Elementi"),
                 tr("Superficie (ha)")),
                [(label, distance, count, round(m2 / M2_PER_HA, 4))
                 for _key, label, distance, count, m2 in breakdown])
        if state.terrain is not None:
            report.lines(state.terrain.describe())
        report.lines(state.spec.describe())
        if state.along_contours:
            # The lattice density formula does not apply: between contour
            # rows the distance is set by the interval and the slope, so the
            # report gives the interval and the measured result instead of a
            # number derived from a row distance nobody used.
            report.lines(curves_mod.describe(state.contours,
                                             state.contour_interval_m))
            report.text("  " + tr("Densita' risultante: {0:,.0f} piante/ha")
                        .format(state.result.density_per_ha()
                                if state.result is not None
                                else state.density_per_ha()))
        else:
            report.lines(density_mod.describe(
                state.spec.pattern, state.spec.plant_distance_m,
                state.spec.row_distance_m))
            if state.contours:
                report.lines(curves_mod.describe(state.contours,
                                                 state.contour_interval_m))
        if len(state.zones):
            report.lines(state.zones.describe())
            report.table(
                tr("Zone"),
                (tr("Zona"), tr("Superficie (ha)"), tr("Sesto"),
                 tr("Densita' (piante/ha)")),
                [(zone.name, round(zone.area_ha, 4),
                  "--" if zone.spec is None else "{0:g} x {1:g} m".format(
                      zone.spec.plant_distance_m, zone.spec.row_distance_m),
                  round(zone.density_per_ha(), 1))
                 for zone in state.zones])
        if state.result is not None and hasattr(state.result, "per_zone"):
            report.lines(state.result.describe())
        if state.natural is not None and state.natural.is_active:
            report.lines(state.natural.describe())
        if state.natural_outcome is not None:
            report.lines(state.natural_outcome.describe())
        if state.composition is not None:
            report.lines(state.composition.describe())
        # The table comes from the plants, not from the Composition object:
        # a plan generated zone by zone assigns species per zone and leaves
        # state.composition empty, and the mix is still on the ground.
        columns, rows = self.composition_table()
        if rows:
            report.table(tr("Composizione"), columns, rows)
        if state.scenarios:
            report.table(
                tr("Scenari a confronto"),
                (tr("Scenario"), tr("Sesto"), tr("Densita' (piante/ha)"),
                 tr("Piante")),
                [(row["name"], row.get("sesto", ""),
                  round(row["density"], 1), row["plants"])
                 for row in state.scenarios])
        if state.layout is not None:
            report.lines(carto_mod.describe(state.layout_spec, state.layout))
        if state.anomalies:
            report.heading(tr("ANOMALIE"), level=2)
            for text in state.anomalies:
                report.text("  - " + text)
        if state.result is not None and state.result.plants:
            report.table(tr("Piante"), *self.plants_table(), sheet_only=True)
        return report

    def composition_table(self):
        """Species, how many, what came out and what was asked for."""
        state = self.state
        achieved = composition_mod.achieved_percentages(
            state.result.plants if state.result is not None else [])
        wanted = {}
        mix = state.mix()
        if mix is not None:
            wanted = {key: 100.0 * value
                      for key, value in mix.weights().items()}
        counts = state.composition.counts if state.composition else {}
        keys = sorted(set(counts) | set(achieved) | set(wanted))
        rows = []
        for key in keys:
            record = None
            try:
                record = state.catalog.get(key)
            except GeoCadError:
                record = None
            rows.append((record.name if record is not None and record.name
                         else key,
                         counts.get(key, 0),
                         round(achieved.get(key, 0.0), 2),
                         round(wanted.get(key, 0.0), 2)))
        return ((tr("Specie"), tr("Piante"), tr("% effettiva"),
                 tr("% richiesta")), rows)

    def plants_table(self):
        """Every plant, for the workbook. Not for the prose."""
        rows = []
        for record in self.state.result.plants:
            rows.append((record.plant_id, record.row_id, record.seq_in_row,
                         zones_mod.zone_of(record),
                         composition_mod.species_of(record),
                         round(record.x, 3), round(record.y, 3),
                         None if record.z is None else round(record.z, 3),
                         None if record.slope_deg is None
                         else round(record.slope_deg, 2),
                         None if record.aspect_deg is None
                         else round(record.aspect_deg, 2)))
        return ((tr("ID"), tr("Fila"), tr("Progressivo"), tr("Zona"),
                 tr("Specie"), "X", "Y", "Z", tr("Pendenza"),
                 tr("Esposizione")), rows)

    def build_report(self) -> str:
        """The technical report as text, shown in the panel."""
        text = docs_mod.as_text(self.document())
        self.report.setPlainText(text)
        return text

    def write_document(self, key: str = "", path: str = "") -> str:
        """Write the report in the chosen format. Returns the path."""
        key = key or str(self.document_combo.currentData() or "pdf")
        suffix = docs_mod.suffix_for(key)
        if not path:
            from qgis.PyQt.QtWidgets import QFileDialog          # noqa: PLC0415

            path, _filter = QFileDialog.getSaveFileName(
                self, tr("Salva la relazione"), "relazione" + suffix,
                "*" + suffix)
        if not path:
            return ""
        if suffix and not path.lower().endswith(suffix):
            path += suffix
        try:
            written = docs_mod.write(self.document(), path, key)
        except GeoCadError as exc:
            self.warn(exc)
            return ""
        self.say(tr("Relazione scritta in {0}").format(written))
        return written


# --------------------------------------------------------------------------
# Right dock: the context
# --------------------------------------------------------------------------

class ContextDock(QDockWidget):
    """One page per step, and only the controls that step needs."""

    def __init__(self, state: ProjectState, parent=None):
        super().__init__(tr("Contesto"), parent)
        self.setObjectName("GeoCadContextDock")
        self.state = state
        self.stack = QStackedWidget()

        self.area_panel = AreaPanel(state)
        self.terrain_panel = TerrainPanel(state)
        self.constraints_panel = ConstraintsPanel(state)
        self.zones_panel = ZonesPanel(state)
        self.scheme_panel = SchemePanel(state)
        self.orientation_panel = OrientationPanel(state)
        self.generate_panel = GeneratePanel(state)
        self.natural_panel = NaturalPanel(state)
        self.optimise_panel = OptimisePanel(state)
        self.verify_panel = VerifyPanel(state)
        self.edit_panel = EditPanel(state)
        self.cartography_panel = CartographyPanel(state)
        self.outputs_panel = OutputsPanel(state)

        #: step key -> (page, tab index or None). Specie and Sesti share the
        #: scheme panel: two steps, one panel, no duplicated preview.
        self.pages = {
            "area": (self.area_panel, None),
            "terrain": (self.terrain_panel, None),
            "constraints": (self.constraints_panel, None),
            "zones": (self.zones_panel, None),
            "species": (self.scheme_panel, SchemePanel.TAB_SPECIES),
            "scheme": (self.scheme_panel, SchemePanel.TAB_SCHEME),
            "orientation": (self.orientation_panel, None),
            "generate": (self.generate_panel, None),
            "natural": (self.natural_panel, None),
            "optimise": (self.optimise_panel, None),
            "verify": (self.verify_panel, None),
            "edit": (self.edit_panel, None),
            "cartography": (self.cartography_panel, None),
            "outputs": (self.outputs_panel, None),
        }
        for panel in (self.area_panel, self.terrain_panel,
                      self.constraints_panel, self.zones_panel,
                      self.scheme_panel, self.orientation_panel,
                      self.generate_panel, self.natural_panel,
                      self.optimise_panel,
                      self.verify_panel, self.edit_panel,
                      self.cartography_panel, self.outputs_panel):
            holder = QScrollArea()
            holder.setWidgetResizable(True)
            holder.setWidget(panel)
            self.stack.addWidget(holder)

        # ---------------------------------------------------------- VOLO
        # One planner, laid out as six pages. UavPanel owns the widgets and
        # every number they produce; this dock only decides which of them
        # the operator is looking at.
        self.uav_panel = UavPanel(state.iface)
        self.export_panel = ExportPanel(
            state.iface, lambda: self.uav_panel.last_mission)
        self.player = MissionPlayer(state.iface, self)
        self._player_connections = []
        #: Guards the two-way link between the time cursor and the clock.
        self._seeking = False
        flight = dict(self.uav_panel.pages())
        # One DEM download in the whole plugin, and it belongs to the
        # Terreno step. The flight step sends the operator there rather
        # than growing a second dialog of its own.
        self.dem_step_button = QPushButton(tr("Serve un DEM: vai a Terreno"))
        self.dem_step_button.clicked.connect(
            lambda: state.stepRequested.emit("terrain"))
        flight[uav_mod.STEP_AREA] = (list(flight[uav_mod.STEP_AREA])
                                     + [self.dem_step_button])
        flight[uav_mod.STEP_SIMULATION] = (
            list(flight[uav_mod.STEP_SIMULATION]) + [self._build_player_box()])
        self.report_button = QPushButton(tr("Relazione di missione (HTML)"))
        self.report_button.setToolTip(tr(
            "Scrive la relazione completa: parametri, statistiche, esito "
            "dei controlli e profilo altimetrico."))
        self.report_button.setEnabled(False)
        flight[uav_mod.STEP_EXPORT] = [self.export_panel, self.report_button]

        #: Flight step key -> (page widget, None). Kept apart from
        #: ``pages`` because these are one planner's controls, not panels
        #: sharing the planting project's state.
        self.flight_pages = {}
        for key, _label in UAV_STEPS:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(6, 6, 6, 6)
            layout.setSpacing(8)
            for widget in flight.get(key, []):
                if isinstance(widget, QWidget):
                    layout.addWidget(widget)
                else:
                    layout.addLayout(widget)
            layout.addStretch(1)
            holder = QScrollArea()
            holder.setWidgetResizable(True)
            holder.setWidget(page)
            self.stack.addWidget(holder)
            self.flight_pages[key] = (page, None)

        state.uav = self.uav_panel
        state.uav_export = self.export_panel
        self.uav_panel.load_settings()
        self._wire_player()

        self.setWidget(self.stack)
        self.setMinimumWidth(240)

    # -- the flight simulator ----------------------------------------------

    def _build_player_box(self):
        """Transport controls, a time cursor, and the altimetric profile."""
        box = QGroupBox(tr("Simulazione del volo"))
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.play_button = QPushButton(tr("Play"))
        self.pause_button = QPushButton(tr("Pausa"))
        self.stop_button = QPushButton(tr("Stop"))
        self.rate_combo = QComboBox()
        for rate in PLAYER_RATES:
            self.rate_combo.addItem("{0}x".format(rate), rate)
        for widget in (self.play_button, self.pause_button, self.stop_button,
                       self.rate_combo):
            row.addWidget(widget)
        layout.addLayout(row)

        # The time cursor. Dragging it is the same as having played to that
        # instant: the simulator rebuilds its whole state from the moment,
        # exposures included.
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setRange(0, SLIDER_STEPS)
        self.time_slider.setToolTip(tr(
            "Trascina per andare a un istante del volo: quota, batteria e "
            "fotogrammi seguono."))
        self.time_slider.setEnabled(False)
        layout.addWidget(self.time_slider)

        self.player_status = QLabel()
        self.player_status.setWordWrap(True)
        layout.addWidget(self.player_status)

        self.profile_chart = charts_mod.ElevationProfile()
        layout.addWidget(self.profile_chart)

        self.footprint_button = QPushButton(tr("Mostra le impronte a terra"))
        self.footprint_button.setToolTip(tr(
            "Disegna l'impronta di ogni scatto proiettata sul DEM. Dove le "
            "impronte si sovrappongono il colore si scurisce: e' la "
            "sovrapposizione longitudinale e laterale, vista da sopra."))
        layout.addWidget(self.footprint_button)
        return box

    def _wire_player(self):
        for signal, slot in (
                (self.play_button.clicked, self.play_mission),
                (self.pause_button.clicked, self.pause_mission),
                (self.stop_button.clicked, self.stop_mission),
                (self.rate_combo.currentIndexChanged, self._change_rate),
                (self.time_slider.valueChanged, self._seek),
                (self.footprint_button.clicked, self.show_footprints),
                (self.report_button.clicked, self.write_mission_report),
                (self.uav_panel.generate_button.clicked,
                 self.refresh_player),
                (self.uav_panel.generate_button.clicked,
                 self.export_panel.refresh),
                (self.uav_panel.generate_button.clicked,
                 self.state.refresh_status),
                (self.export_panel.export_button.clicked,
                 self.state.refresh_status),
                (self.player.ticked, self._on_player_tick),
                (self.player.finished, self.refresh_player)):
            signal.connect(slot)
            self._player_connections.append((signal, slot))
        self.refresh_player()

    def play_mission(self, *_args) -> bool:
        mission = self.uav_panel.last_mission
        if mission is None:
            self.player_status.setText(tr(
                "Nessuna rotta da simulare: generala nello step Simulazione."))
            return False
        if not self.player.play(mission):
            self.player_status.setText(self.player.message)
            return False
        self.refresh_player()
        return True

    def pause_mission(self, *_args) -> None:
        self.player.pause()
        self.refresh_player()

    def stop_mission(self, *_args) -> None:
        self.player.stop()
        self.refresh_player()

    def _change_rate(self, *_args) -> None:
        self.player.set_rate(self.rate_combo.currentData() or 1)

    def _seek(self, value) -> None:
        """The operator dragged the cursor."""
        if self._seeking or self.player.mission is None:
            return
        self._seeking = True
        try:
            self.player.seek(self.player.duration_s
                             * float(value) / SLIDER_STEPS)
        finally:
            self._seeking = False

    def _on_player_tick(self, *_args) -> None:
        self.player_status.setText(self.player.summary())
        self.profile_chart.set_cursor(
            self.profile_chart.length_m * self.player.distance_fraction())
        if self._seeking:
            return
        self._seeking = True
        try:
            total = self.player.duration_s
            self.time_slider.setValue(
                0 if total <= 0
                else int(round(SLIDER_STEPS * self.player.t_sim / total)))
        finally:
            self._seeking = False

    def refresh_player(self, *_args) -> None:
        mission = getattr(self.uav_panel, "last_mission", None)
        self.play_button.setEnabled(mission is not None)
        self.pause_button.setEnabled(self.player.is_playing)
        self.stop_button.setEnabled(self.player.mission is not None)
        self.footprint_button.setEnabled(mission is not None)
        self.report_button.setEnabled(mission is not None)
        if mission is not None and mission is not self.player.mission:
            # Loaded here rather than on Play, so the cursor and the profile
            # answer about the route on screen before anyone presses
            # anything.
            self.player.load(mission)
        samples = self.profile_chart.set_mission(mission)
        self.time_slider.setEnabled(mission is not None
                                    and self.player.duration_s > 0)
        self._seeking = True
        try:
            self.time_slider.setValue(0)
        finally:
            self._seeking = False
        if mission is None:
            self.player_status.setText(tr("Nessuna rotta caricata."))
        elif samples < 2:
            self.player_status.setText(tr(
                "Rotta caricata; profilo altimetrico non disponibile."))
        else:
            self.player_status.setText(self.player.summary())

    # -- what the flight leaves on the map and on disk ---------------------

    def show_footprints(self, *_args):
        """Draw the draped footprint of every exposure, one over the other.

        Semi-transparent on purpose: where two frames overlap the fill
        doubles up and the ground goes darker, so frontlap and sidelap are
        read off the map instead of off a number. The footprints are draped
        on the DEM by ray casting, so this is the coverage that will really
        be flown, not the plan's flat rectangle.
        """
        from qgis.core import QgsFillSymbol, QgsSingleSymbolRenderer

        from ..io import layer_factory as lf

        mission = getattr(self.uav_panel, "last_mission", None)
        if mission is None:
            self.player_status.setText(tr("Genera prima la rotta."))
            return None
        if not mission.footprints:
            self.player_status.setText(tr(
                "Impronte non calcolate: accendi 'Verifica la copertura "
                "sulle impronte a terra' nello step Sicurezza e rigenera la "
                "rotta."))
            return None

        crs = self.uav_panel.extent.crs()
        layer = lf.build_footprint_layer(mission,
                                         crs.authid() if crs else "")
        symbol = QgsFillSymbol.createSimple({
            "color": FOOTPRINT_FILL,
            "outline_color": FOOTPRINT_OUTLINE,
            "outline_width": "0.2",
        })
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.setName(tr("Impronte a terra ({0} scatti)").format(
            layer.featureCount()))
        QgsProject.instance().addMapLayer(layer)
        self.player_status.setText(tr(
            "{0} impronte disegnate: dove il colore si scurisce le foto si "
            "sovrappongono.").format(layer.featureCount()))
        return layer

    def write_mission_report(self, *_args, path: str = ""):
        """Write the flight report the plugin could always build.

        It existed only as the HTML output of a Processing algorithm, which
        is not a place anyone working in the dashboard ever looks.
        """
        mission = getattr(self.uav_panel, "last_mission", None)
        if mission is None:
            self.player_status.setText(tr("Genera prima la rotta."))
            return ""
        if not path:
            path, _filter = QFileDialog.getSaveFileName(
                self, tr("Relazione di missione"), "missione.html",
                tr("Pagina HTML (*.html)"))
        if not path:
            return ""
        try:
            # build_html's "geometry" is the photogrammetric geometry --
            # footprint, spacings, GSD -- not the AOI polygon.
            written = mission_report_mod.save_html(
                mission, path, params=self.uav_panel.build_params(),
                validation=self.uav_panel.last_report,
                geometry=self.uav_panel.survey_geometry())
        except (GeoCadError, OSError, ValueError) as exc:
            self.warn(exc)
            return ""
        self.player_status.setText(tr("Relazione scritta in {0}.")
                                   .format(written))
        return written

    def warn(self, error) -> None:
        message = (error.formatted() if isinstance(error, GeoCadError)
                   else str(error))
        iface = self.state.iface
        if iface is not None:
            try:
                iface.messageBar().pushMessage("GeoCad UAV", message,
                                               level=Qgis.Warning, duration=6)
                return
            except Exception:                                   # noqa: BLE001
                pass
        QgsApplication.messageLog().logMessage(message, "GeoCad UAV",
                                               Qgis.Warning)

    def teardown(self) -> None:
        """Called from the workspace when the plugin is unloaded."""
        for signal, slot in self._player_connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._player_connections = []
        self.uav_panel.save_settings()
        for panel in (self.player, self.uav_panel, self.export_panel):
            try:
                panel.teardown()
            except Exception:                                   # noqa: BLE001
                pass

    def show_step(self, row: int) -> None:
        """Called by the workflow list. Rows map to pages, sometimes to tabs."""
        if row < 0 or row >= len(ALL_STEPS):
            return
        key = ALL_STEPS[row][0]
        panel, tab = self.pages.get(key, self.flight_pages.get(key,
                                                               (None, None)))
        if panel is None:
            return
        index = self.stack.indexOf(panel.parentWidget().parentWidget())
        if index < 0:
            # The panel sits inside a QScrollArea's viewport; walk up to the
            # widget the stack actually holds.
            widget = panel
            while widget is not None and self.stack.indexOf(widget) < 0:
                widget = widget.parentWidget()
            index = self.stack.indexOf(widget) if widget is not None else -1
        if index >= 0:
            self.stack.setCurrentIndex(index)
        if tab is not None:
            self.scheme_panel.tabs.setCurrentIndex(tab)
        self.state.set_current_step(key)


# --------------------------------------------------------------------------
# The status bar
# --------------------------------------------------------------------------

class StatusBarInfo(QObject):
    """One permanent label: surface, plants, state. Nothing else."""

    def __init__(self, state: ProjectState, iface=None, parent=None):
        super().__init__(parent)
        self.state = state
        self.iface = iface
        self.label = QLabel(self.text())
        self.label.setObjectName("GeoCadStatusLabel")
        if iface is not None:
            try:
                iface.mainWindow().statusBar().addPermanentWidget(self.label)
            except Exception:                                   # noqa: BLE001
                pass
        state.changed.connect(self.refresh)

    def text(self) -> str:
        area = (ha(self.state.usable_m2) if self.state.area is not None
                else "{0} ha".format(DASH))
        plants = ("{0:,}".format(self.state.result.count)
                  if self.state.result is not None else DASH)
        if self.state.result is None or not self.state.verified:
            status = tr("ATTESA")
        elif self.state.anomalies:
            status = tr("{0} ANOMALIE").format(len(self.state.anomalies))
        else:
            status = tr("CONFORME")
        return "{0} {1}  |  {2} {3}  |  {4} {5}".format(
            tr("Area utile:"), area, tr("Piante:"), plants, tr("Stato:"),
            status)

    def refresh(self) -> None:
        self.label.setText(self.text())

    def remove(self) -> None:
        if self.iface is None:
            return
        try:
            self.iface.mainWindow().statusBar().removeWidget(self.label)
        except Exception:                                       # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# The workspace
# --------------------------------------------------------------------------

class Workspace(QObject):
    """The two docks, the status bar and the wiring between them."""

    def __init__(self, iface=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.state = ProjectState(iface)
        self.workflow = WorkflowDock(self.state)
        self.context = ContextDock(self.state)
        self.status = StatusBarInfo(self.state, iface)

        # The plugin's own two docks, and only those: a sheet on the
        # application would re-skin QGIS and every other plugin's panel.
        theme_mod.apply(self.workflow, self.context)

        # The one connection the whole navigation rests on.
        self.workflow.stepChanged.connect(self.context.show_step)
        self.context.show_step(0)
        self.state.refresh_status()

    def mount(self) -> None:
        if self.iface is None:
            return
        self.iface.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea,
                                 self.workflow)
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,
                                 self.context)

    def unmount(self) -> None:
        self.context.teardown()
        self.state.preview.dispose()
        self.state.layers.remove_all()
        if self.state.terrain is not None:
            # A QgsRasterLayer the project never adopted: released here,
            # while QGIS is still standing.
            self.state.terrain.release()
        self.state.contours = []
        self.status.remove()
        if self.iface is None:
            return
        for dock in (self.workflow, self.context):
            try:
                self.iface.removeDockWidget(dock)
            except Exception:                                   # noqa: BLE001
                pass
            dock.deleteLater()

    def set_visible(self, visible: bool) -> None:
        self.workflow.setVisible(bool(visible))
        self.context.setVisible(bool(visible))

    def is_visible(self) -> bool:
        return bool(self.workflow.isVisible() or self.context.isVisible())
