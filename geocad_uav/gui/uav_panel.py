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

The AOI picker is :class:`~.grid_panel.ExtentSource`, shared with the Grid and
Forest tabs, so "the area" means the same thing in all three.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from qgis.core import (Qgis, QgsGeometry, QgsMapLayerProxyModel, QgsPointXY,
                       QgsProject, QgsWkbTypes)
from qgis.gui import QgsMapLayerComboBox, QgsRubberBand
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout,
                                 QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                                 QPushButton, QTextBrowser, QVBoxLayout,
                                 QWidget)

from ..core import crs as crs_svc
from ..core import grid as grid_mod
from ..core.errors import GeoCadError
from ..core.models import AltitudeMode
from ..core.units import format_duration
from ..core.z import TerrainModel
from ..io import layer_factory as lf
from ..settings import settings as app_settings
from ..uav import cameras as cam_lib
from ..uav import drones as drone_lib
from ..uav import mission as mission_mod
from ..uav import photogrammetry as pg
from ..uav import survey as sv
from .grid_panel import ExtentSource


def tr(text):
    return QCoreApplication.translate("GeoCadUav", text)


#: Seconds per hour over metres per kilometre. The only place km/h exists.
KMH_TO_MS = 1.0 / 3.6


class UavPanel(QWidget):
    """Mission parameters, live derived values, route preview, one write."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._band = None
        self._connections = []
        self.last_mission = None
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

        self.extent = ExtentSource(self.iface)
        self.extent.on_change(self.recompute)
        layout.addWidget(self.extent)

        terrain_box = QGroupBox(tr("Terreno (obbligatorio)"))
        terrain_form = QFormLayout(terrain_box)
        self.dem_combo = QgsMapLayerComboBox()
        self.dem_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.dem_combo.setAllowEmptyLayer(True, tr("(nessun DEM)"))
        terrain_form.addRow(tr("DEM / DTM del progetto"), self.dem_combo)
        self.dem_note = QLabel()
        self.dem_note.setWordWrap(True)
        terrain_form.addRow(self.dem_note)
        self.safety_margin = self._spin(0.0, 0.0, 200.0, " m")
        terrain_form.addRow(tr("Margine di sicurezza"), self.safety_margin)
        layout.addWidget(terrain_box)

        gear_box = QGroupBox(tr("Camera e drone"))
        gear_form = QFormLayout(gear_box)
        self.camera_combo = QComboBox()
        for key in sorted(self._cameras):
            self.camera_combo.addItem(self._cameras[key].name, key)
        gear_form.addRow(tr("Camera"), self.camera_combo)
        self.drone_combo = QComboBox()
        for key in sorted(self._drones):
            self.drone_combo.addItem(self._drones[key].name, key)
        gear_form.addRow(tr("Drone"), self.drone_combo)
        layout.addWidget(gear_box)

        flight_box = QGroupBox(tr("Volo"))
        flight_form = QFormLayout(flight_box)
        self.h_agl = self._spin(80.0, 1.0, 2000.0, " m")
        flight_form.addRow(tr("Quota H_AGL"), self.h_agl)
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

        self.azimuth = self._spin(0.0, 0.0, 360.0, " deg")
        flight_form.addRow(tr("Azimut strip"), self.azimuth)
        azimuth_buttons = QHBoxLayout()
        self.azimuth_from_map = QPushButton(tr("Da due click"))
        self.azimuth_from_edge = QPushButton(tr("Parallelo a un lato"))
        azimuth_buttons.addWidget(self.azimuth_from_map)
        azimuth_buttons.addWidget(self.azimuth_from_edge)
        flight_form.addRow(azimuth_buttons)
        layout.addWidget(flight_box)

        self.summary = QTextBrowser()
        self.summary.setMinimumHeight(190)
        layout.addWidget(self.summary)

        buttons = QHBoxLayout()
        self.generate_button = QPushButton(tr("Genera rotta"))
        self.confirm_button = QPushButton(tr("Crea layer missione"))
        buttons.addWidget(self.generate_button)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)

        for widget, signal_name in (
                (self.dem_combo, "layerChanged"),
                (self.camera_combo, "currentIndexChanged"),
                (self.drone_combo, "currentIndexChanged"),
                (self.h_agl, "valueChanged"),
                (self.speed_kmh, "valueChanged"),
                (self.frontlap, "valueChanged"),
                (self.sidelap, "valueChanged"),
                (self.safety_margin, "valueChanged"),
                (self.azimuth, "valueChanged")):
            signal = getattr(widget, signal_name)
            signal.connect(self.recompute)
            self._connections.append((signal, self.recompute))

        for button, slot in ((self.generate_button, self.generate),
                             (self.confirm_button, self.confirm),
                             (self.azimuth_from_map, self._pick_azimuth),
                             (self.azimuth_from_edge, self._azimuth_from_edge)):
            button.clicked.connect(slot)
            self._connections.append((button.clicked, slot))

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

    def survey_geometry(self) -> pg.SurveyGeometry:
        return pg.solve_survey_geometry(self.current_camera(), self.overlap(),
                                        h_agl_m=self.h_agl.value())

    def interval_s(self) -> float:
        """Shot interval, from the engine: D_front / v. Never typed in."""
        return self.survey_geometry().interval_at_speed(self.speed_ms())

    def dem_layer(self):
        return self.dem_combo.currentLayer()

    def build_params(self) -> mission_mod.MissionParams:
        """Everything the frozen assembler needs, in engine units."""
        return mission_mod.MissionParams(
            camera=self.current_camera(),
            drone=self.current_drone(),
            overlap=self.overlap(),
            h_agl_m=self.h_agl.value(),
            altitude_mode=AltitudeMode.TERRAIN,
            safety_margin_m=self.safety_margin.value(),
            # The spin box is the only source of the strip azimuth: no
            # hidden "0 means automatic" rule. The longest-side value is
            # offered in the derived table and applied by its own button.
            azimuth_strategy=sv.AZIMUTH_MANUAL,
            manual_azimuth_deg=self.azimuth.value(),
            v_mission_ms=self.speed_ms(),
            compute_footprints=False)

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

    # -- readiness ---------------------------------------------------------

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
        return True, ""

    # -- live derived values (pure arithmetic, no DEM read) ----------------

    def recompute(self, *_args):
        """Refresh the interval and the derived table. Touches no raster."""
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
                (tr("Dislivello area"),
                 "{0:.1f} m".format(stats.terrain_relief_m)),
            ])
            notes.extend(mission.warnings[:6])

        self.summary.setHtml(self._html(rows, notes))

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
            self.clear_preview()
            self._notify_user(reason, Qgis.Warning)
            self.recompute()
            return None

        geometry = self.extent.geometry()
        crs = self.extent.crs()
        try:
            blocks, warnings = sv.prepare_aoi([geometry])
        except sv.RoutingError as exc:
            self.last_mission = None
            self.clear_preview()
            self._notify_user(str(exc), Qgis.Warning)
            self.recompute()
            return None
        aoi = blocks[0]

        params = self.build_params()
        geom_survey = self.survey_geometry()
        box = aoi.boundingBox()
        try:
            terrain, dem_warnings = TerrainModel.from_layer(
                self.dem_layer(), crs,
                (box.xMinimum(), box.yMinimum(), box.xMaximum(),
                 box.yMaximum()),
                margin_m=max(geom_survey.footprint_across_m,
                             geom_survey.footprint_along_m))
        except Exception as exc:                                # noqa: BLE001
            self.last_mission = None
            self.clear_preview()
            self._notify_user(
                tr("DEM non leggibile: {0}").format(exc), Qgis.Critical)
            self.recompute()
            return None

        try:
            mission = mission_mod.build_mission(
                aoi, terrain, params, crs_authid=crs.authid() if crs else "")
        except (GeoCadError, sv.RoutingError,
                pg.PhotogrammetryError) as exc:
            self.last_mission = None
            self.clear_preview()
            message = getattr(exc, "user_message", "") or str(exc)
            self._notify_user(message, Qgis.Critical)
            self.recompute()
            return None

        mission.warnings = list(warnings) + list(dem_warnings) + \
            list(mission.warnings)
        self.last_mission = mission
        self._draw(mission)
        self.recompute()
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

    def _draw(self, mission):
        """The route as a rubber band: pixels, not features."""
        band = self._ensure_band()
        if band is None:
            return
        band.reset(QgsWkbTypes.LineGeometry)
        for line in mission.lines:
            array = np.asarray(line, dtype=float)
            if array.shape[0] < 2:
                continue
            points = [QgsPointXY(float(p[0]), float(p[1])) for p in array]
            band.addGeometry(QgsGeometry.fromPolylineXY(points), None)
        band.show()

    def clear_preview(self):
        if self._band is not None:
            self._band.reset(QgsWkbTypes.LineGeometry)
            self._band.hide()

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

    def save_settings(self):
        app_settings.set("uav/camera", self.camera_combo.currentData() or "")
        app_settings.set("uav/drone", self.drone_combo.currentData() or "")
        app_settings.set("uav/frontlap", self.frontlap.value() / 100.0)
        app_settings.set("uav/sidelap", self.sidelap.value() / 100.0)
        app_settings.set("uav/h_agl_m", self.h_agl.value())

    def teardown(self):
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._connections = []
        self.extent.teardown()
        self.clear_preview()
