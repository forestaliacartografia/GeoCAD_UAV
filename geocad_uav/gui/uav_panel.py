"""
UAV tab: an AOI, a DEM from the project, and a terrain-following mission.

Nothing is planned here. The route is ``uav.survey.plan_route``, the draping is
``uav.terrain_follow.build_flight_profile``, and the two are assembled by
``uav.mission.build_mission`` -- all frozen. This panel collects parameters,
converts the operator's units once (km/h -> m/s, percent -> fraction), shows the
derived photogrammetric numbers live, previews the route on a rubber band and
writes the layers through ``io.layer_factory.build_mission_layers``.

Two rules are structural rather than cosmetic:

* **No DEM, no mission.** Terrain following is the only altitude mode offered
  here, and it needs elevations. Without a raster layer the Generate button is
  disabled and says why, instead of quietly falling back to a single AMSL --
  which on real relief means flying into the hill.
* **The DEM comes from the project.** No download, no API key, no remote
  service. Remote elevation adapters are their own milestone.

The AOI picker is :class:`~.extent_source.ExtentSource`, shared with the
reforestation tab, so "the area" means the same thing in both.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from qgis.core import (Qgis, QgsGeometry, QgsMapLayerProxyModel, QgsPointXY,
                       QgsProject, QgsWkbTypes)
from qgis.gui import QgsMapLayerComboBox, QgsRubberBand
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                                 QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                                 QLineEdit, QPushButton, QTextBrowser,
                                 QVBoxLayout, QWidget)

from ..core import crs as crs_svc
from ..core import grid as grid_mod
from ..core.errors import GeoCadError
from ..core.models import AltitudeMode
from ..core.units import format_duration
from ..core.z import TerrainError, TerrainModel
from ..io import layer_factory as lf
from ..settings import settings as app_settings
from ..uav import cameras as cam_lib
from ..uav import drones as drone_lib
from ..uav import mission as mission_mod
from ..uav import photogrammetry as pg
from ..uav import survey as sv
from ..uav import validator as val
from .extent_source import ExtentSource


def tr(text):
    return QCoreApplication.translate("GeoCadUav", text)


#: Seconds per hour over metres per kilometre. The only place km/h exists.
KMH_TO_MS = 1.0 / 3.6

#: Which end of the optical relation the operator fixes. The other one is
#: derived and shown read-only: GSD = H * pitch / f has one degree of
#: freedom, and a panel that let both be typed would be lying about that.
HEIGHT_FROM_AGL = "agl"
HEIGHT_FROM_GSD = "gsd"

#: Flight patterns offered, keyed on the engine's own names.
PATTERNS = (
    (sv.PATTERN_BOUSTROPHEDON, "Strisciate adiacenti (andata e ritorno)"),
    (sv.PATTERN_INTERLACED, "Strisciate alternate (raggio di virata ampio)"),
)

#: Azimuth strategies offered. "optimised" is resolved by the sweep before
#: the route is planned; the others go straight to the engine.
AZIMUTHS = (
    ("manual", "Manuale"),
    ("longest", "Lato piu' lungo dell'area"),
    ("optimised", "Misurato (scansione degli orientamenti)"),
)

#: The twelve steps of the flight workflow, in the order they are worked.
STEP_AREA = "uav_area"
STEP_DRONE = "uav_drone"
STEP_SENSOR = "uav_sensor"
STEP_GSD = "uav_gsd"
STEP_TERRAIN = "uav_terrain"
STEP_CAPTURE = "uav_capture"
STEP_LINES = "uav_lines"
STEP_WAYPOINTS = "uav_waypoints"
STEP_SAFETY = "uav_safety"
STEP_SIMULATION = "uav_simulation"
STEP_VALIDATION = "uav_validation"
STEP_EXPORT = "uav_export"

CM_PER_M = 100.0


class UavPanel(QWidget):
    """Mission parameters, live derived values, route preview, one write."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._band = None
        #: Called at the end of every recompute, so a host that mounts these
        #: controls elsewhere can follow their state without polling.
        self._recompute_listeners = []
        #: Point bands for the preview: waypoints and exposure stations.
        self._point_bands = {}
        #: The layers "Anteprima missione" put on the map, so a second
        #: preview replaces them instead of stacking another set.
        self._preview_layers = {}
        self._connections = []
        self._updating = False
        self.last_mission = None
        #: The terrain and the AOI the last route was planned on. Kept
        #: because the pre-flight check needs them: a validator given only
        #: the mission cannot check coverage, endurance or clearance.
        self.last_terrain = None
        self.last_aoi = None
        self.last_report = None
        #: The last terrain diagnostic, in full. The message bar gets its
        #: first line; this is what a test and an operator read.
        self.last_terrain_error = ""
        self._cameras = cam_lib.load_library()
        self._drones = drone_lib.load_library()
        self._build()
        # The combo pre-selects the first matching raster on its own and that
        # assignment emits no layerChanged: read it, do not wait for a signal.
        self._dem_layer = self.dem_combo.currentLayer()
        self.recompute()

    # -- construction ------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ---------------------------------------------------------- AREA
        self.extent = ExtentSource(self.iface)
        self.extent.on_change(self.recompute)

        terrain_box = QGroupBox(tr("Terreno (obbligatorio)"))
        terrain_form = QFormLayout(terrain_box)
        self.dem_combo = QgsMapLayerComboBox()
        self.dem_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.dem_combo.setAllowEmptyLayer(True, tr("(nessun DEM)"))
        terrain_form.addRow(tr("DEM / DTM del progetto"), self.dem_combo)
        self.dem_note = QLabel()
        self.dem_note.setWordWrap(True)
        terrain_form.addRow(self.dem_note)
        # Minimum zero, not one: zero is how an operator says "I have
        # not decided yet", and readiness has to be able to see that.
        self.corridor_width = self._spin(120.0, 0.0, 5000.0, " m")
        self.corridor_width.setToolTip(tr(
            "Larghezza totale da riprendere, centrata sull'asse. Compare "
            "solo quando l'area scelta e' una linea: strada, corso d'acqua, "
            "elettrodotto."))
        self.corridor_label = QLabel(tr("Larghezza del corridoio"))
        terrain_form.addRow(self.corridor_label, self.corridor_width)
        self.corridor_label.setVisible(False)
        self.corridor_width.setVisible(False)

        self.user_margin = self._spin(0.0, 0.0, 500.0, " m")
        self.user_margin.setToolTip(tr(
            "Fascia aggiunta attorno all'area, oltre alla mezza impronta "
            "che il motore aggiunge gia' da solo per coprire i bordi."))
        terrain_form.addRow(tr("Margine sull'area"), self.user_margin)
        self.terrain_box = terrain_box

        # ------------------------------------------------------ HARDWARE
        drone_box = QGroupBox(tr("Drone"))
        drone_form = QFormLayout(drone_box)
        self.drone_combo = QComboBox()
        for key in sorted(self._drones):
            self.drone_combo.addItem(self._drones[key].name, key)
        drone_form.addRow(tr("Profilo"), self.drone_combo)
        self.drone_note = QLabel()
        self.drone_note.setWordWrap(True)
        drone_form.addRow(self.drone_note)
        self.drone_box = drone_box

        sensor_box = QGroupBox(tr("Sensore"))
        sensor_form = QFormLayout(sensor_box)
        self.camera_combo = QComboBox()
        for key in sorted(self._cameras):
            camera = self._cameras[key]
            self.camera_combo.addItem(
                "{0} [{1}]".format(camera.name, camera.kind_label), key)
        sensor_form.addRow(tr("Payload"), self.camera_combo)
        self.camera_note = QLabel()
        self.camera_note.setWordWrap(True)
        sensor_form.addRow(self.camera_note)
        self.sensor_box = sensor_box
        #: Kept as the pair of boxes the older code called "the gear box".
        self.gear_box = drone_box

        optics_box = QGroupBox(tr("Quota e GSD"))
        optics_form = QFormLayout(optics_box)
        self.height_mode = QComboBox()
        self.height_mode.addItem(tr("Fisso la quota"), HEIGHT_FROM_AGL)
        self.height_mode.addItem(tr("Fisso il GSD"), HEIGHT_FROM_GSD)
        self.height_mode.setToolTip(tr(
            "GSD = quota x passo del pixel / focale: un solo grado di "
            "liberta'. Si fissa un capo, l'altro si legge."))
        optics_form.addRow(tr("Vincolo"), self.height_mode)
        self.h_agl = self._spin(80.0, 1.0, 2000.0, " m")
        optics_form.addRow(tr("Quota H_AGL"), self.h_agl)
        self.gsd_target = self._spin(2.0, 0.01, 100.0, " cm/px")
        optics_form.addRow(tr("GSD"), self.gsd_target)
        self.optics_note = QLabel()
        self.optics_note.setWordWrap(True)
        optics_form.addRow(self.optics_note)
        self.optics_box = optics_box

        # -------------------------------------------------------- FLIGHT
        capture_box = QGroupBox(tr("Parametri di acquisizione"))
        flight_form = QFormLayout(capture_box)
        self.speed_kmh = self._spin(36.0, 1.0, 108.0, " km/h")
        flight_form.addRow(tr("Velocita'"), self.speed_kmh)
        self.frontlap = self._spin(80.0, 1.0, 95.0, " %")
        flight_form.addRow(tr("Sovrapp. longitudinale"), self.frontlap)
        self.sidelap = self._spin(70.0, 1.0, 95.0, " %")
        flight_form.addRow(tr("Sovrapp. laterale"), self.sidelap)

        self.interval = QLineEdit()
        self.interval.setReadOnly(True)
        self.interval.setToolTip(tr(
            "Derivato: base di presa D_front divisa per la velocita'. "
            "Non e' modificabile perche' non e' un parametro libero."))
        flight_form.addRow(tr("Intervallo di scatto"), self.interval)
        self.capture_box = capture_box

        lines_box = QGroupBox(tr("Strisciate"))
        flight_form = QFormLayout(lines_box)
        self.pattern_combo = QComboBox()
        for key, label in PATTERNS:
            self.pattern_combo.addItem(tr(label), key)
        flight_form.addRow(tr("Schema"), self.pattern_combo)
        self.double_grid = QCheckBox(tr("Doppia griglia ortogonale"))
        self.double_grid.setToolTip(tr(
            "Due passate perpendicolari. Raddoppia il tempo di volo ed e' "
            "cio' che separa un ortofoto usabile da un modello 3D usabile."))
        flight_form.addRow(self.double_grid)

        self.azimuth_mode = QComboBox()
        for key, label in AZIMUTHS:
            self.azimuth_mode.addItem(tr(label), key)
        flight_form.addRow(tr("Azimut"), self.azimuth_mode)
        self.azimuth = self._spin(0.0, 0.0, 360.0, " deg")
        flight_form.addRow(tr("Azimut strip"), self.azimuth)
        azimuth_buttons = QHBoxLayout()
        self.azimuth_from_map = QPushButton(tr("Da due click"))
        self.azimuth_from_edge = QPushButton(tr("Parallelo a un lato"))
        self.azimuth_optimise = QPushButton(tr("Ottimizza"))
        self.azimuth_optimise.setToolTip(tr(
            "Prova ogni orientamento a passi di 5 gradi su mezzo giro, "
            "dispone le strisciate sull'area vera e sceglie quello che costa "
            "meno tempo di volo. Sul rettangolo dara' la stessa risposta del "
            "lato piu' lungo; sulle aree concave no."))
        azimuth_buttons.addWidget(self.azimuth_from_map)
        azimuth_buttons.addWidget(self.azimuth_from_edge)
        azimuth_buttons.addWidget(self.azimuth_optimise)
        flight_form.addRow(azimuth_buttons)
        self.azimuth_note = QLabel()
        self.azimuth_note.setWordWrap(True)
        self.azimuth_note.setVisible(False)
        flight_form.addRow(self.azimuth_note)
        self.lines_box = lines_box
        #: The older name for the acquisition box, kept for callers.
        self.flight_box = capture_box

        # -------------------------------------------------------- SAFETY
        safety_box = QGroupBox(tr("Sicurezza e ostacoli"))
        safety_form = QFormLayout(safety_box)
        self.safety_margin = self._spin(0.0, 0.0, 200.0, " m")
        self.safety_margin.setToolTip(tr(
            "Aggiunta alla quota AGL su tutta la rotta."))
        safety_form.addRow(tr("Margine di sicurezza"), self.safety_margin)
        self.vegetation_clearance = self._spin(0.0, 0.0, 100.0, " m")
        self.vegetation_clearance.setToolTip(tr(
            "Franco sulla vegetazione. Serve quando il DEM e' un DTM di "
            "terreno nudo e sotto la rotta c'e' chioma o edificato: il DTM "
            "non sa che ci sono."))
        safety_form.addRow(tr("Franco sulla vegetazione"),
                           self.vegetation_clearance)
        # -1 keeps the drone profile's own reserve; anything else overrides
        # it, and the override reaches the sub-mission split, not only the
        # warning after it.
        self.reserve_pct = self._spin(-1.0, -1.0, 80.0, " %")
        self.reserve_pct.setToolTip(tr(
            "Riserva di batteria, in percentuale dell'autonomia nominale. "
            "-1 usa quella del profilo drone. Alzarla accorcia le tratte e "
            "puo' aggiungere una batteria: e' quello che deve fare."))
        safety_form.addRow(tr("Riserva batteria"), self.reserve_pct)
        self.battery_note = QLabel()
        self.battery_note.setWordWrap(True)
        safety_form.addRow(self.battery_note)
        self.obstacle_combo = QgsMapLayerComboBox()
        self.obstacle_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self.obstacle_combo.setAllowEmptyLayer(True, tr("(nessun ostacolo)"))
        self.obstacle_combo.setToolTip(tr(
            "Elettrodotti, edifici, gru: qualunque layer vettoriale. "
            "La rotta viene intersecata con questo layer dilatato del "
            "raggio di rischio."))
        safety_form.addRow(tr("Layer ostacoli"), self.obstacle_combo)
        self.obstacle_buffer = self._spin(30.0, 0.0, 1000.0, " m")
        safety_form.addRow(tr("Raggio di rischio"), self.obstacle_buffer)
        self.check_coverage = QCheckBox(
            tr("Verifica la copertura sulle impronte a terra"))
        self.check_coverage.setToolTip(tr(
            "Proietta ogni scatto sul DEM e misura quanta parte dell'area "
            "ricade in almeno tre foto. E' l'unica verifica di copertura "
            "che valga qualcosa, e costa un raggio per scatto: su missioni "
            "lunghe aggiunge secondi."))
        safety_form.addRow(self.check_coverage)
        self.safety_box = safety_box

        self.quality_box = QGroupBox(tr("Controllo pre-volo"))
        quality_layout = QVBoxLayout(self.quality_box)
        self.quality = QTextBrowser()
        self.quality.setMinimumHeight(180)
        quality_layout.addWidget(self.quality)
        self.quality_button = QPushButton(tr("Ricontrolla"))
        quality_layout.addWidget(self.quality_button)

        # ---------------------------------------------------- SIMULATION
        self.summary = QTextBrowser()
        self.summary.setMinimumHeight(190)

        buttons = QHBoxLayout()
        self.generate_button = QPushButton(tr("Genera rotta"))
        self.confirm_button = QPushButton(tr("Crea layer missione"))
        buttons.addWidget(self.generate_button)
        buttons.addWidget(self.confirm_button)
        self.button_row = buttons

        for widget in (self.extent, terrain_box, drone_box, sensor_box,
                       optics_box, capture_box, lines_box, safety_box,
                       self.quality_box, self.summary):
            layout.addWidget(widget)
        layout.addLayout(buttons)

        for widget, signal_name in (
                (self.dem_combo, "layerChanged"),
                (self.camera_combo, "currentIndexChanged"),
                (self.drone_combo, "currentIndexChanged"),
                (self.height_mode, "currentIndexChanged"),
                (self.h_agl, "valueChanged"),
                (self.gsd_target, "valueChanged"),
                (self.speed_kmh, "valueChanged"),
                (self.frontlap, "valueChanged"),
                (self.sidelap, "valueChanged"),
                (self.safety_margin, "valueChanged"),
                (self.vegetation_clearance, "valueChanged"),
                (self.reserve_pct, "valueChanged"),
                (self.user_margin, "valueChanged"),
                (self.corridor_width, "valueChanged"),
                (self.pattern_combo, "currentIndexChanged"),
                (self.double_grid, "toggled"),
                (self.check_coverage, "toggled"),
                (self.obstacle_combo, "layerChanged"),
                (self.obstacle_buffer, "valueChanged"),
                (self.azimuth_mode, "currentIndexChanged"),
                (self.azimuth, "valueChanged")):
            signal = getattr(widget, signal_name)
            signal.connect(self.recompute)
            self._connections.append((signal, self.recompute))

        for button, slot in ((self.generate_button, self.generate),
                             (self.confirm_button, self.confirm),
                             (self.azimuth_from_map, self._pick_azimuth),
                             (self.azimuth_from_edge, self._azimuth_from_edge),
                             (self.azimuth_optimise,
                              self.apply_optimised_azimuth),
                             (self.quality_button, self.run_quality_check)):
            button.clicked.connect(slot)
            self._connections.append((button.clicked, slot))

    # -- the workflow steps ------------------------------------------------

    def pages(self):
        """``{step key: [widgets]}``: the same controls, laid out as steps.

        The widgets are the panel's own. A host that mounts them reparents
        them out of this panel's layout, which is what makes this one
        planner shown six ways rather than six planners.
        """
        return {
            STEP_AREA: [self.extent],
            STEP_DRONE: [self.drone_box],
            STEP_SENSOR: [self.sensor_box],
            STEP_GSD: [self.optics_box],
            STEP_TERRAIN: [self.terrain_box],
            STEP_CAPTURE: [self.capture_box],
            STEP_LINES: [self.lines_box],
            # The action row is NOT here: it belongs to every flight
            # step, not to this one, and the dock mounts it once under the
            # whole stack. See ContextDock._build_flight_actions.
            STEP_WAYPOINTS: [self.summary],
            STEP_SAFETY: [self.safety_box],
            STEP_SIMULATION: [],
            STEP_VALIDATION: [self.quality_box],
            STEP_EXPORT: [],
        }

    def _spin(self, value, minimum, maximum, suffix):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        return spin

    # -- parameters --------------------------------------------------------

    def speed_ms(self) -> float:
        """The single km/h -> m/s conversion. The engine never sees km/h."""
        return self.speed_kmh.value() * KMH_TO_MS

    def current_camera(self):
        return self._cameras[self.camera_combo.currentData()]

    def current_drone(self):
        return self._drones[self.drone_combo.currentData()]

    def overlap(self) -> pg.Overlap:
        """Percent on screen, fraction to the engine."""
        return pg.Overlap(frontlap=self.frontlap.value() / 100.0,
                          sidelap=self.sidelap.value() / 100.0)

    def height_source(self) -> str:
        """Which end of the optical relation the operator is fixing."""
        return self.height_mode.currentData() or HEIGHT_FROM_AGL

    def gsd_m(self) -> float:
        """The GSD box in metres per pixel. The engine never sees cm."""
        return self.gsd_target.value() / CM_PER_M

    def survey_geometry(self) -> pg.SurveyGeometry:
        """The photogrammetric geometry, solved from whichever end is fixed.

        ``solve_survey_geometry`` takes exactly one of the two and derives
        the other, which is why the panel passes one and never both.
        """
        if self.height_source() == HEIGHT_FROM_GSD:
            return pg.solve_survey_geometry(self.current_camera(),
                                            self.overlap(),
                                            gsd_m_px=self.gsd_m())
        return pg.solve_survey_geometry(self.current_camera(), self.overlap(),
                                        h_agl_m=self.h_agl.value())

    def is_corridor(self) -> bool:
        """A corridor mission is one whose AOI is a line.

        Not a checkbox: what was picked already says which kind of mission
        this is, and a switch that could disagree with the geometry is a
        switch that will.
        """
        return bool(self.extent.is_line())

    def corridor_width_m(self) -> float:
        return (float(self.corridor_width.value()) if self.is_corridor()
                else 0.0)

    def azimuth_choice(self):
        """``(strategy, manual azimuth or None)`` from the azimuth combo."""
        key = self.azimuth_mode.currentData() or "manual"
        if key == "longest":
            return sv.AZIMUTH_LONGEST_SIDE, None
        if key == "optimised":
            return sv.AZIMUTH_OPTIMISED, None
        return sv.AZIMUTH_MANUAL, self.azimuth.value()

    def interval_s(self) -> float:
        """Shot interval, from the engine: D_front / v. Never typed in."""
        return self.survey_geometry().interval_at_speed(self.speed_ms())

    def dem_layer(self):
        return self.dem_combo.currentLayer()

    def build_params(self) -> mission_mod.MissionParams:
        """Everything the frozen assembler needs, in engine units."""
        strategy, manual = self.azimuth_choice()
        from_gsd = self.height_source() == HEIGHT_FROM_GSD
        return mission_mod.MissionParams(
            camera=self.current_camera(),
            drone=self.current_drone(),
            overlap=self.overlap(),
            h_agl_m=None if from_gsd else self.h_agl.value(),
            gsd_m=self.gsd_m() if from_gsd else None,
            altitude_mode=AltitudeMode.TERRAIN,
            safety_margin_m=self.safety_margin.value(),
            vegetation_clearance_m=self.vegetation_clearance.value(),
            reserve_pct=(None if self.reserve_pct.value() < 0.0
                         else self.reserve_pct.value()),
            user_margin_m=self.user_margin.value(),
            pattern=self.pattern_combo.currentData()
            or sv.PATTERN_BOUSTROPHEDON,
            double_grid=self.double_grid.isChecked(),
            corridor_width_m=self.corridor_width_m(),
            azimuth_strategy=strategy,
            manual_azimuth_deg=manual,
            v_mission_ms=self.speed_ms(),
            # The draped footprints are what coverage is judged on, and ray
            # casting one per exposure is not free: asked for only when the
            # operator wants the coverage checked.
            compute_footprints=self.check_coverage.isChecked())

    # -- azimuth helpers ---------------------------------------------------

    def _pick_azimuth(self):
        def apply_azimuth(_length, azimuth):
            self.azimuth.setValue(azimuth % 360.0)

        self.extent.start_measure(apply_azimuth)

    def longest_side_azimuth(self) -> Optional[float]:
        """Azimuth of the AOI's longest edge, or None without an AOI."""
        geometry = self.extent.geometry()
        if geometry is None:
            return None
        try:
            ring = geometry.asPolygon()[0]
        except (IndexError, TypeError):
            return None
        points = np.array([[p.x(), p.y()] for p in ring], dtype=float)
        return grid_mod.azimuth_of_longest_edge(points) % 360.0

    def _azimuth_from_edge(self):
        value = self.longest_side_azimuth()
        if value is not None:
            self.azimuth.setValue(value)
            self.azimuth_note.setVisible(False)

    def apply_optimised_azimuth(self, *_args):
        """Sweep the orientations on the real AOI and take the cheapest.

        The strips are laid out for every candidate with the spacing this
        panel is already set to, so the comparison is between orientations
        and nothing else; the saving shown is against the orientation the
        sweep found worst, which is what a bad guess would have cost.

        Returns the azimuth, or None when there is nothing to measure.
        """
        geometry = self.extent.geometry()
        if geometry is None:
            self._notify_user(tr(
                "Nessuna area definita: l'azimut si misura sull'area, non "
                "sul rettangolo dello schermo."))
            return None
        try:
            survey = self.survey_geometry()
            drone = self.current_drone()
            budget = pg.build_speed_budget(survey, self.speed_ms(),
                                           drone.v_max_ms)
            azimuth, note, scores = sv.optimise_azimuth(
                geometry, budget.effective,
                d_side_m=survey.d_side_m, d_front_m=survey.d_front_m,
                footprint_across_m=survey.footprint_across_m,
                footprint_along_m=survey.footprint_along_m,
                turn_radius_m=drone.turn_radius_m)
        except (GeoCadError, ValueError) as exc:
            self._notify_user(tr("Ottimizzazione non riuscita: {0}")
                              .format(exc))
            return None

        best = min(scores, key=lambda item: item.time_s)
        worst = max(scores, key=lambda item: item.time_s)
        self.azimuth.setValue(azimuth % 360.0)
        self.azimuth_note.setText(tr(
            "Azimut {0:.0f} deg su {1} orientamenti provati: {2} strisciate, "
            "{3} virate, {4} di volo stimati. L'orientamento peggiore ne "
            "costava {5}.").format(
                azimuth, len(scores), best.n_strips, best.n_turns,
                format_duration(best.time_s), format_duration(worst.time_s)))
        self.azimuth_note.setVisible(True)
        return azimuth

    # -- obstacles ---------------------------------------------------------

    def no_fly_geometries(self):
        """The obstacle layer near the AOI, dilated by the risk radius.

        Read in the AOI's CRS and clipped by a filter rectangle: a national
        power-line layer has no business being walked feature by feature to
        plan a twenty-hectare flight.
        """
        layer = self.obstacle_combo.currentLayer()
        if layer is None or not layer.isValid():
            return []
        aoi = self.extent.geometry()
        crs = self.extent.crs()
        if aoi is None or crs is None:
            return []
        radius = float(self.obstacle_buffer.value())

        # The AOI is not where the aircraft flies. The strips are buffered
        # outwards by half a footprint so the edge frames still cover the
        # boundary, and the turns reach further still: a filter rectangle
        # drawn round the AOI alone would skip the pylon the route passes
        # over just outside it. Measured on the real route when there is
        # one, and on the buffer when there is not yet.
        box = aoi.boundingBox()
        try:
            geometry = self.survey_geometry()
            reach = max(geometry.footprint_across_m,
                        geometry.footprint_along_m)
        except Exception:                                       # noqa: BLE001
            reach = 0.0
        box.grow(reach + float(self.user_margin.value()) + radius + 1.0)
        mission = self.last_mission
        if mission is not None and mission.waypoints:
            for waypoint in mission.waypoints:
                box.combineExtentWith(waypoint.x, waypoint.y)
            box.grow(radius + 1.0)
        request_box = box
        if layer.crs() != crs:
            back = crs_svc.make_transform(crs, layer.crs())
            if back is not None:
                try:
                    request_box = back.transformBoundingBox(box)
                except Exception:                               # noqa: BLE001
                    request_box = None

        from qgis.core import QgsFeatureRequest                 # noqa: PLC0415

        request = QgsFeatureRequest()
        if request_box is not None:
            request.setFilterRect(request_box)

        geometries = []
        for feature in layer.getFeatures(request):
            geometry = QgsGeometry(feature.geometry())
            if geometry.isEmpty():
                continue
            if layer.crs() != crs:
                geometry = crs_svc.transform_geometry(geometry, layer.crs(),
                                                      crs)
                if geometry is None or geometry.isEmpty():
                    continue
            if radius > 0.0:
                geometry = geometry.buffer(radius, 12)
            geometries.append(geometry)
        return geometries

    # -- the pre-flight check ----------------------------------------------

    def run_quality_check(self, *_args):
        """The frozen validator, with everything it needs to say something.

        Run with the parameters, the terrain and the AOI as well as the
        mission: given the mission alone it cannot check clearance,
        endurance or coverage, and reports them as "not verified" -- which
        is what the export tab was showing until now.
        """
        mission = self.last_mission
        if mission is None:
            self.last_report = None
            self.quality.setHtml(
                "<p>{0}</p>".format(tr("Genera prima la rotta.")))
            return None
        crs = self.extent.crs()
        try:
            report = val.validate(
                mission, params=self.build_params(),
                terrain=self.last_terrain, aoi_geom=self.last_aoi, crs=crs,
                no_fly_geoms=self.no_fly_geometries())
        except (GeoCadError, ValueError) as exc:                # noqa: BLE001
            self.last_report = None
            self.quality.setHtml("<p>{0}</p>".format(
                tr("Controllo non riuscito: {0}").format(exc)))
            return None
        self.last_report = report
        self.quality.setHtml(self._quality_html(report))
        return report

    @staticmethod
    def _quality_html(report):
        """Green, orange, red -- one line per check, with its number."""
        colours = {val.SEVERITY_OK: "#2e7d32",
                   val.SEVERITY_WARNING: "#ef6c00",
                   val.SEVERITY_ERROR: "#c62828"}
        marks = {val.SEVERITY_OK: "OK", val.SEVERITY_WARNING: "!",
                 val.SEVERITY_ERROR: "X"}
        rows = []
        for check in report.checks:
            colour = colours.get(check.severity, "#555")
            rows.append(
                "<tr><td width='24' align='center'><b "
                "style='color:{0}'>{1}</b></td>"
                "<td><b>{2}</b>{3}</td>"
                "<td align='right' style='color:{0}'>{4}</td></tr>".format(
                    colour, marks.get(check.severity, "?"), check.label,
                    "<br><span style='color:#777'>{0}</span>".format(
                        check.detail) if check.detail else "",
                    check.value or ""))
        head = "<p style='color:{0}'><b>{1}</b></p>".format(
            "#c62828" if report.errors else
            ("#ef6c00" if report.warnings else "#2e7d32"),
            report.summary())
        return (head + "<table width='100%' cellspacing='0' cellpadding='3'>"
                + "".join(rows) + "</table>")

    # -- readiness ---------------------------------------------------------

    def on_recompute(self, callback) -> None:
        """Follow every parameter change. The dock keeps its bar in step."""
        self._recompute_listeners.append(callback)

    def battery_plan(self):
        """The endurance arithmetic for the route on hand, or None."""
        if self.last_mission is None:
            return None
        return val.battery_plan(self.last_mission, self.build_params())

    def battery_summary(self) -> str:
        """One line about batteries, next to the control that sets them."""
        plan = self.battery_plan()
        if plan is None:
            drone = self.current_drone()
            reserve = (drone.rth_reserve_pct if self.reserve_pct.value() < 0.0
                       else self.reserve_pct.value())
            return tr(
                "Autonomia nominale {0:.0f} min, riserva {1:g} %: {2:.1f} "
                "min utili per batteria. Genera la rotta per il "
                "conteggio.").format(
                    drone.endurance_min, reserve,
                    drone.endurance_min * (1.0 - reserve / 100.0))
        return tr(
            "{0:.1f} min di volo su {1:.1f} min utili per batteria "
            "(nominali {2:.1f}, riserva {3:g} %): {4} batterie, margine "
            "{5:+.1f} min, tempo operativo {6:.0f} min.").format(
                plan["per_battery_s"] / 60.0, plan["usable_s"] / 60.0,
                plan["nominal_s"] / 60.0, plan["reserve_pct"],
                plan["batteries_needed"], plan["margin_s"] / 60.0,
                plan["operative_s"] / 60.0)

    def blocking_reason(self) -> str:
        """Why Genera rotta is off, in one line, or empty when it is on."""
        ready, reason = self.readiness()
        return "" if ready else str(reason or "")

    def readiness(self):
        """``(can_generate, italian_reason)``. The reason is never empty."""
        if self.dem_layer() is None:
            return False, tr(
                "Nessun DEM selezionato. Il terrain following non e' "
                "opzionale: senza quote del terreno la rotta non puo' "
                "mantenere l'AGL costante e il GSD sarebbe diverso su ogni "
                "strip. Carica un DEM/DTM nel progetto e selezionalo qui.")
        geometry = self.extent.geometry()
        if geometry is None:
            return False, tr(
                "Nessuna area definita: scegli un poligono del progetto "
                "oppure disegnalo sulla mappa.")
        crs = self.extent.crs()
        if crs is not None and crs_svc.is_geographic(crs):
            return False, tr(
                "Il CRS dell'area ({0}) e' geografico: distanze e "
                "sovrapposizioni verrebbero calcolate in gradi. Riproietta "
                "in un CRS metrico (UTM).").format(crs.authid())
        if self.is_corridor() and self.corridor_width.value() <= 0.0:
            return False, tr(
                "Hai scelto un asse lineare: indica la larghezza del "
                "corridoio da riprendere.")
        if self.height_source() == HEIGHT_FROM_GSD:
            try:
                height = pg.height_from_gsd(self.current_camera(),
                                            self.gsd_m())
            except pg.PhotogrammetryError as exc:
                return False, tr("GSD non valido: {0}").format(exc)
            limit = self.current_drone().max_agl_m
            if height > limit:
                return False, tr(
                    "Un GSD di {0:.2f} cm/px con questa camera richiede "
                    "{1:.0f} m di quota, oltre il limite di {2:.0f} m del "
                    "profilo drone. Alza il GSD o cambia ottica.").format(
                        self.gsd_target.value(), height, limit)
        return True, ""

    # -- live derived values (pure arithmetic, no DEM read) ----------------

    def recompute(self, *_args):
        """Refresh the interval and the derived table. Touches no raster."""
        if self._updating:
            return
        self._dem_layer = self.dem_combo.currentLayer()
        ready, reason = self.readiness()
        self.generate_button.setEnabled(ready)
        self.confirm_button.setEnabled(self.last_mission is not None)
        self.dem_note.setText("" if self.dem_layer() is not None else reason)

        try:
            geometry = self.survey_geometry()
            interval = geometry.interval_at_speed(self.speed_ms())
        except Exception as exc:                                # noqa: BLE001
            self.interval.setText("-")
            self.summary.setPlainText(
                tr("Parametri non validi: {0}").format(exc))
            return
        self.interval.setText("{0:.2f} s".format(interval))
        self.camera_note.setText(cam_lib.describe(self.current_camera()))
        self.drone_note.setText(drone_lib.describe(self.current_drone()))
        corridor = self.is_corridor()
        self.corridor_label.setVisible(corridor)
        self.corridor_width.setVisible(corridor)
        # A corridor's strips follow the axis: an azimuth and a grid pattern
        # would be controls with nothing to act on.
        for widget in (self.azimuth, self.azimuth_mode, self.pattern_combo,
                       self.double_grid, self.azimuth_optimise,
                       self.azimuth_from_edge, self.azimuth_from_map):
            widget.setEnabled(not corridor)
        self._pair_height_and_gsd(geometry)
        if not corridor:
            self.azimuth.setEnabled(
                self.azimuth_choice()[0] == sv.AZIMUTH_MANUAL)

        drone = self.current_drone()
        budget = pg.build_speed_budget(geometry, self.speed_ms(),
                                       drone.v_max_ms)
        rows = [
            (tr("GSD"), "{0:.2f} cm/px".format(geometry.gsd_cm_px)),
            (tr("Impronta a terra"), "{0:.1f} x {1:.1f} m".format(
                geometry.footprint_across_m, geometry.footprint_along_m)),
            (tr("Interasse strip D_side"),
             "{0:.2f} m".format(geometry.d_side_m)),
            (tr("Base di presa D_front"),
             "{0:.2f} m".format(geometry.d_front_m)),
            (tr("Velocita' richiesta"), "{0:.2f} m/s ({1:.1f} km/h)".format(
                self.speed_ms(), self.speed_kmh.value())),
            (tr("Velocita' ammessa"), "{0:.2f} m/s".format(budget.effective)),
            (tr("Vincolo determinante"), budget.binding),
            (tr("Intervallo di scatto"), "{0:.2f} s".format(interval)),
        ]
        suggestion = self.longest_side_azimuth()
        if suggestion is not None:
            rows.append((tr("Azimut del lato maggiore"),
                         "{0:.1f} deg".format(suggestion)))

        notes = []
        if budget.is_capped_below_request:
            notes.append(tr(
                "La velocita' richiesta supera il limite '{0}': la missione "
                "sara' volata a {1:.2f} m/s e l'intervallo reale diventa "
                "{2:.2f} s.").format(
                    budget.binding, budget.effective,
                    geometry.interval_at_speed(budget.effective)))
        if geometry.h_agl_m > drone.max_agl_m:
            notes.append(tr(
                "La quota {0:.0f} m supera il limite di {1:.0f} m AGL del "
                "profilo drone.").format(geometry.h_agl_m, drone.max_agl_m))
        if not ready:
            notes.append(reason)

        mission = self.last_mission
        self.battery_note.setText(self.battery_summary())
        if mission is not None:
            stats = mission.stats
            rows.extend([
                (tr("Strip"), "{0:,}".format(stats.n_strips)),
                (tr("Waypoint"), "{0:,}".format(stats.n_waypoints)),
                (tr("Scatti"), "{0:,}".format(stats.n_photos)),
                (tr("Percorso"), "{0:,.0f} m".format(stats.total_length_m)),
                (tr("Tempo di volo"), format_duration(stats.flight_time_s)),
                (tr("Batterie"), "{0}".format(stats.n_batteries)),
                (tr("AGL min / max"), "{0:.1f} / {1:.1f} m".format(
                    stats.agl_min, stats.agl_max)),
                (tr("GSD min / max"), "{0:.2f} / {1:.2f} cm/px".format(
                    stats.gsd_min_m * 100.0, stats.gsd_max_m * 100.0)),
                (tr("Sovrapposizione"),
                 "{0:.0f} % avanti, {1:.0f} % lato".format(
                     100.0 * mission.frontlap, 100.0 * mission.sidelap)),
                (tr("Copertura"),
                 tr("non misurata") if stats.coverage_pct != stats.coverage_pct
                 else "{0:.1f} % con almeno {1} foto".format(
                     stats.coverage_pct, stats.min_photos_observed)),
                (tr("Dislivello area"),
                 "{0:.1f} m".format(stats.terrain_relief_m)),
            ])
            notes.extend(mission.warnings[:6])

        self.summary.setHtml(self._html(rows, notes))
        for callback in self._recompute_listeners:
            try:
                callback()
            except (GeoCadError, RuntimeError):
                pass

    def _pair_height_and_gsd(self, geometry):
        """Write the derived end of the optical relation into its own box.

        The one the operator fixed is left alone and stays editable; the
        other is set from the engine's answer and greyed, so the pair on
        screen is always a pair that exists.
        """
        from_gsd = self.height_source() == HEIGHT_FROM_GSD
        self._updating = True
        try:
            if from_gsd:
                self.h_agl.setValue(geometry.h_agl_m)
            else:
                self.gsd_target.setValue(geometry.gsd_cm_px)
        finally:
            self._updating = False
        self.h_agl.setReadOnly(from_gsd)
        self.h_agl.setEnabled(not from_gsd)
        self.gsd_target.setReadOnly(not from_gsd)
        self.gsd_target.setEnabled(from_gsd)
        self.optics_note.setText(tr(
            "{0} cm/px a {1:.0f} m con {2} ({3:.0f} mm): impronta "
            "{4:.0f} x {5:.0f} m.").format(
                "{0:.2f}".format(geometry.gsd_cm_px), geometry.h_agl_m,
                self.current_camera().name, self.current_camera().focal_mm,
                geometry.footprint_across_m, geometry.footprint_along_m))

    @staticmethod
    def _html(rows, notes):
        table = "".join(
            "<tr><td style='color:#555'>{0}</td>"
            "<td align='right'><b>{1}</b></td></tr>".format(key, value)
            for key, value in rows)
        html = ("<table width='100%' cellspacing='0' cellpadding='3'>"
                + table + "</table>")
        if notes:
            html += "<ul style='margin-left:-18px;color:#8a6100'>" + "".join(
                "<li>{0}</li>".format(note) for note in notes) + "</ul>"
        return html

    # -- the mission -------------------------------------------------------

    def generate(self, *_args):
        """Plan the mission and draw it. Writes nothing, creates no layer."""
        ready, reason = self.readiness()
        if not ready:
            self.last_mission = None
            self.last_terrain = None
            self.last_aoi = None
            self.last_report = None
            self.clear_preview()
            self._notify_user(reason, Qgis.Warning)
            self.recompute()
            return None

        geometry = self.extent.geometry()
        crs = self.extent.crs()
        corridor = self.is_corridor()
        try:
            blocks, warnings = (sv.prepare_axis([geometry]) if corridor
                                else sv.prepare_aoi([geometry]))
        except sv.RoutingError as exc:
            self.last_mission = None
            self.last_terrain = None
            self.last_aoi = None
            self.last_report = None
            self.clear_preview()
            self._notify_user(str(exc), Qgis.Warning)
            self.recompute()
            return None
        aoi = blocks[0]

        params = self.build_params()
        geom_survey = self.survey_geometry()
        box = aoi.boundingBox()
        # The DEM window has to cover what is flown, and a corridor's axis
        # bounding box is a thin ribbon: the strips sit half the corridor
        # width away from it on both sides.
        margin = max(geom_survey.footprint_across_m,
                     geom_survey.footprint_along_m)
        if corridor:
            margin += 0.5 * self.corridor_width_m()
        try:
            terrain, dem_warnings = TerrainModel.from_layer(
                self.dem_layer(), crs,
                (box.xMinimum(), box.yMinimum(), box.xMaximum(),
                 box.yMaximum()),
                margin_m=margin)
        except TerrainError as exc:
            # Shown as it comes: it already names the CRS of the layer, the
            # CRS of the file, both extents in one CRS and the sample
            # counts. Wrapping it in "DEM non leggibile" renamed a coverage
            # problem into a reading problem and dropped the numbers that
            # say which of the two it is.
            self.last_mission = None
            self.last_terrain = None
            self.last_aoi = None
            self.last_report = None
            self.clear_preview()
            self.last_terrain_error = str(exc)
            self._notify_user(str(exc).splitlines()[0], Qgis.Critical)
            self._log(str(exc))
            self.recompute()
            # After the refresh, not before: recompute() rewrites this box
            # with the parameter table, and a diagnostic written into a box
            # that is about to be cleared is a diagnostic nobody reads.
            self.summary.setPlainText(str(exc))
            return None
        except Exception as exc:                                # noqa: BLE001
            self.last_mission = None
            self.last_terrain = None
            self.last_aoi = None
            self.last_report = None
            self.clear_preview()
            self.last_terrain_error = str(exc)
            self._notify_user(
                tr("DEM non leggibile: {0}").format(exc), Qgis.Critical)
            self._log(str(exc))
            self.recompute()
            return None

        try:
            mission = mission_mod.build_mission(
                aoi, terrain, params, crs_authid=crs.authid() if crs else "")
        except (GeoCadError, sv.RoutingError,
                pg.PhotogrammetryError) as exc:
            self.last_mission = None
            self.last_terrain = None
            self.last_aoi = None
            self.last_report = None
            self.clear_preview()
            message = getattr(exc, "user_message", "") or str(exc)
            self._notify_user(message, Qgis.Critical)
            self.recompute()
            return None

        mission.warnings = list(warnings) + list(dem_warnings) + \
            list(mission.warnings)
        self.last_mission = mission
        # Kept for the pre-flight check, which cannot judge clearance,
        # endurance or coverage from the mission alone.
        self.last_terrain = terrain
        self.last_aoi = aoi
        self._draw(mission)
        self.recompute()
        self.run_quality_check()
        return mission

    def _ensure_band(self):
        canvas = self.iface.mapCanvas() if self.iface else None
        if canvas is None:
            return None
        if self._band is None:
            self._band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
            self._band.setColor(QColor(200, 60, 20, 220))
            self._band.setWidth(2)
        return self._band

    def _ensure_points(self, key, colour, icon, size):
        """One point band per kind of point. Created on first use."""
        canvas = self.iface.mapCanvas() if self.iface else None
        if canvas is None:
            return None
        band = self._point_bands.get(key)
        if band is None:
            band = QgsRubberBand(canvas, QgsWkbTypes.PointGeometry)
            band.setColor(colour)
            band.setIcon(icon)
            band.setIconSize(size)
            band.setWidth(2)
            self._point_bands[key] = band
        return band

    def _draw(self, mission):
        """The mission as rubber bands: pixels, not features.

        Route, waypoints and exposure stations, which together are what a
        preview has to answer: where it flies, where it turns, where it
        photographs. Nothing is added to the project -- "Crea layer
        missione" is the command that does that.
        """
        band = self._ensure_band()
        if band is None:
            return 0
        band.reset(QgsWkbTypes.LineGeometry)
        drawn = 0
        for line in mission.lines:
            array = np.asarray(line, dtype=float)
            if array.shape[0] < 2:
                continue
            points = [QgsPointXY(float(p[0]), float(p[1])) for p in array]
            band.addGeometry(QgsGeometry.fromPolylineXY(points), None)
            drawn += 1
        band.show()

        waypoints = self._ensure_points(
            "waypoints", QColor(20, 90, 200, 200),
            QgsRubberBand.ICON_CIRCLE, 7)
        if waypoints is not None:
            waypoints.reset(QgsWkbTypes.PointGeometry)
            for wp in mission.waypoints:
                waypoints.addPoint(QgsPointXY(float(wp.x), float(wp.y)),
                                   False)
            waypoints.updatePosition()
            waypoints.show()

        photos = self._ensure_points(
            "photos", QColor(250, 170, 30, 230), QgsRubberBand.ICON_BOX, 5)
        if photos is not None:
            photos.reset(QgsWkbTypes.PointGeometry)
            for photo in mission.photos:
                photos.addPoint(QgsPointXY(float(photo.x), float(photo.y)),
                                False)
            photos.updatePosition()
            photos.show()
        return drawn

    def ensure_footprints(self):
        """The mission's footprints, draping them now if it has none.

        Same ray casting as the planner's, on the terrain and the parameters
        this panel kept when it built the route.
        """
        mission = self.last_mission
        if mission is None:
            return []
        if mission.footprints:
            return mission.footprints
        if self.last_terrain is None:
            return []
        try:
            return mission_mod.drape_footprints(
                mission, self.last_terrain, self.build_params(),
                self.survey_geometry())
        except (GeoCadError, pg.PhotogrammetryError, ValueError):
            return []

    def preview_vertices(self) -> int:
        """Vertices currently on the preview. What a test can count."""
        total = 0
        if self._band is not None:
            total += self._band.numberOfVertices()
        for band in self._point_bands.values():
            total += band.numberOfVertices()
        return total

    def show_mission_layers(self, *_args):
        """Anteprima missione: the route on the canvas as real layers.

        The same ``build_mission_layers`` the commit uses, so what is
        previewed and what is exported are the same features. Called twice
        it replaces its own layers rather than stacking a second copy.
        """
        mission = self.last_mission
        if mission is None:
            self._notify_user(tr("Genera prima la rotta."), Qgis.Warning)
            return {}
        crs = self.extent.crs()
        project = QgsProject.instance()
        for layer in list(self._preview_layers.values()):
            try:
                project.removeMapLayer(layer.id())
            except (AttributeError, RuntimeError):
                pass
        self._preview_layers = lf.build_mission_layers(
            mission, crs.authid() if crs else "")
        for layer in self._preview_layers.values():
            project.addMapLayer(layer)
        self.clear_preview()
        canvas = self.iface.mapCanvas() if self.iface else None
        if canvas is not None and self._preview_layers:
            try:
                extent = None
                for layer in self._preview_layers.values():
                    box = layer.extent()
                    if box.isEmpty():
                        continue
                    extent = box if extent is None else extent.combineExtentWith(box)
                if extent is not None and not extent.isEmpty():
                    canvas.setExtent(extent.buffered(
                        max(extent.width(), extent.height()) * 0.08))
                    canvas.refresh()
            except (AttributeError, RuntimeError):
                pass
        return self._preview_layers

    def clear_preview(self):
        if self._band is not None:
            self._band.reset(QgsWkbTypes.LineGeometry)
            self._band.hide()
        for band in self._point_bands.values():
            band.reset(QgsWkbTypes.PointGeometry)
            band.hide()

    # -- commit ------------------------------------------------------------

    def confirm(self, *_args):
        """Write the mission layers. ``build_mission_layers`` fills them."""
        mission = self.last_mission
        if mission is None:
            self._notify_user(tr("Genera prima la rotta."), Qgis.Warning)
            return {}
        if not mission.waypoints:
            self._notify_user(tr("La rotta non contiene waypoint."),
                              Qgis.Warning)
            return {}

        crs = self.extent.crs()
        layers = lf.build_mission_layers(mission,
                                         crs.authid() if crs else "")
        for layer in layers.values():
            QgsProject.instance().addMapLayer(layer)
        self.clear_preview()
        self._notify_user(tr(
            "Creati {0} layer: {1:,} waypoint, {2:,} scatti.").format(
                len(layers), len(mission.waypoints), len(mission.photos)))
        return layers

    def _log(self, text: str) -> None:
        """The full diagnostic to the QGIS log, where it can be read back."""
        try:
            from qgis.core import QgsApplication                 # noqa: PLC0415

            QgsApplication.messageLog().logMessage(text, "GeoCad UAV",
                                                   Qgis.Warning)
        except Exception:                                        # noqa: BLE001
            pass

    def _notify_user(self, text, level=None):
        if self.iface is None:
            return
        try:
            self.iface.messageBar().pushMessage(
                tr("GeoCad UAV"), text,
                level=level if level is not None else Qgis.Info)
        except Exception:                                       # noqa: BLE001
            pass

    # -- settings ----------------------------------------------------------

    def load_settings(self):
        """Restore the persisted defaults. Never writes back."""
        for combo, key in ((self.camera_combo, "uav/camera"),
                           (self.drone_combo, "uav/drone")):
            index = combo.findData(app_settings.get(key))
            if index >= 0:
                combo.setCurrentIndex(index)
        self.frontlap.setValue(app_settings.get("uav/frontlap") * 100.0)
        self.sidelap.setValue(app_settings.get("uav/sidelap") * 100.0)
        self.h_agl.setValue(app_settings.get("uav/h_agl_m"))
        self.reserve_pct.setValue(app_settings.get("uav/reserve_pct"))
        self.recompute()

    def save_settings(self):
        app_settings.set("uav/camera", self.camera_combo.currentData() or "")
        app_settings.set("uav/drone", self.drone_combo.currentData() or "")
        app_settings.set("uav/frontlap", self.frontlap.value() / 100.0)
        app_settings.set("uav/sidelap", self.sidelap.value() / 100.0)
        app_settings.set("uav/h_agl_m", self.h_agl.value())
        app_settings.set("uav/reserve_pct", self.reserve_pct.value())

    def teardown(self):
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._connections = []
        self.extent.teardown()
        self.clear_preview()
        # The preview layers belong to the panel, so they leave with it:
        # a plugin that unloads has to leave the project as it found it.
        project = QgsProject.instance()
        for layer in list(self._preview_layers.values()):
            try:
                project.removeMapLayer(layer.id())
            except (AttributeError, RuntimeError):
                pass
        self._preview_layers = {}
        self._point_bands = {}
