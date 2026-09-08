"""
Dock panel: live photogrammetric preview and mission launcher.

The panel recomputes the derived survey numbers on every parameter change.
That recomputation is pure arithmetic on the camera model -- no DEM read, no
GEOS, no layer access -- so it lands in microseconds and comfortably meets the
"under 500 ms for an AOI below 5 km2" budget without a worker thread.

The heavy work (terrain sampling, ray casting, validation, export) stays in the
Processing algorithm, which already runs with progress and cancellation. The
dock launches it rather than duplicating it: GUI calls services, services never
import widgets (spec section 2).
"""

from __future__ import annotations

import math

from qgis.core import Qgis, QgsMapLayerProxyModel, QgsProject
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import QCoreApplication, QSize, Qt
from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QDockWidget,
                                 QDoubleSpinBox, QFormLayout, QGroupBox,
                                 QHBoxLayout, QLabel, QPushButton, QScrollArea,
                                 QSpinBox, QTabWidget, QTextBrowser, QToolBar,
                                 QVBoxLayout, QWidget)

from ..cad import dynamic_input as di
from ..cad.tools.base import ToolState
from ..core import crs as crs_svc
from ..core.models import AltitudeMode
from ..core.units import format_duration
from ..settings import settings as app_settings
from .forest_panel import ForestPanel
from .grid_panel import GridPanel
from ..uav import cameras as cam_lib
from ..uav import drones as drone_lib
from ..uav import photogrammetry as pg
from ..uav import survey as sv


def tr(text):
    return QCoreApplication.translate("GeoCadUav", text)


class GeoCadDock(QDockWidget):
    """Parameter panel with a live derived-values preview."""

    def __init__(self, iface, parent=None):
        super().__init__(tr("GeoCad UAV Toolkit"), parent)
        self.setObjectName("GeoCadUavDock")
        self.iface = iface
        self._connections = []
        self._cad_tool = None
        for panel in (getattr(self, "grid_panel", None),
                      getattr(self, "forest_panel", None)):
            if panel is not None:
                try:
                    panel.teardown()
                except Exception:                               # noqa: BLE001
                    pass
        self._cameras = cam_lib.load_library()
        self._drones = drone_lib.load_library()
        self._camera_keys = sorted(self._cameras)
        self._drone_keys = sorted(self._drones)

        self.setWidget(self._build())
        self._load_settings()
        self._wire()
        self.recompute()

    # -- construction -----------------------------------------------------

    # -- construction -----------------------------------------------------

    TAB_CAD, TAB_GRID, TAB_FOREST, TAB_UAV, TAB_EXPORT, TAB_SETTINGS = range(6)

    def _scroll_page(self, widgets):
        """A scrollable tab page holding the given widgets, top-aligned."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)
        for widget in widgets:
            if isinstance(widget, QWidget):
                layout.addWidget(widget)
            else:
                layout.addLayout(widget)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def _placeholder(self, title, text):
        """An honest 'not built yet' panel, not a fake control."""
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color:#666;")
        layout.addWidget(label)
        return box

    def _build(self):
        """One dock, six tabs. Every widget below already existed in 1.1.1;
        this method only changes where they are mounted."""
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        self.tabs = QTabWidget()

        # ---------------------------------------------------------------- CAD
        # The plugin populates this toolbar with the CAD map-tool actions, so
        # the QGIS main toolbar keeps a single icon.
        self.cad_toolbar = QToolBar()
        self.cad_toolbar.setIconSize(QSize(20, 20))
        self.cad_toolbar.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        self.cad_box = QGroupBox(tr("Strumento attivo"))
        cad_form = QFormLayout(self.cad_box)
        self.cad_layer_combo = QgsMapLayerComboBox()
        self.cad_layer_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self.cad_layer_combo.setAllowEmptyLayer(
            True, tr("(layer di lavoro automatico)"))
        cad_form.addRow(tr("Layer destinazione"), self.cad_layer_combo)

        self.cad_rows = []
        for _index in range(3):
            label = QLabel("-")
            spin = QDoubleSpinBox()
            spin.setRange(-360.0, 1_000_000.0)
            spin.setDecimals(3)
            spin.setSingleStep(1.0)
            cad_form.addRow(label, spin)
            label.setVisible(False)
            spin.setVisible(False)
            self.cad_rows.append((label, spin))

        self.cad_close_ring = QCheckBox(tr("Chiudi l'anello (LineString chiusa)"))
        self.cad_close_ring.setToolTip(tr(
            "Aggiunge il primo vertice in coda. Produce una LineString chiusa, "
            "non un poligono: la conversione a poligono richiede una primitiva "
            "che non esiste ancora."))
        self.cad_close_ring.setVisible(False)
        cad_form.addRow(self.cad_close_ring)

        self.cad_pivot = QComboBox()
        self.cad_pivot.setToolTip(tr(
            "Punto attorno al quale ruota la geometria."))
        self.cad_pivot_label = QLabel(tr("Perno"))
        cad_form.addRow(self.cad_pivot_label, self.cad_pivot)
        self.cad_pivot_label.setVisible(False)
        self.cad_pivot.setVisible(False)

        self.cad_apply = QPushButton(tr("Applica alla forma"))
        self.cad_hint = QLabel(tr("Nessuno strumento CAD attivo."))
        self.cad_hint.setWordWrap(True)
        cad_form.addRow(self.cad_apply)
        cad_form.addRow(self.cad_hint)

        self.tabs.addTab(self._scroll_page([self.cad_toolbar, self.cad_box]),
                         tr("CAD"))

        # ------------------------------------------------------------ GRIGLIE
        self.grid_panel = GridPanel(self.iface)
        self.tabs.addTab(self._scroll_page([self.grid_panel]), tr("Griglie"))

        # ------------------------------------------------------------ FORESTA
        self.forest_panel = ForestPanel(self.iface)
        self.tabs.addTab(self._scroll_page([self.forest_panel]), tr("Foresta"))

        # ---------------------------------------------------------------- UAV
        area_box = QGroupBox(tr("1. Area"))
        area_form = QFormLayout(area_box)
        self.aoi_combo = QgsMapLayerComboBox()
        self.aoi_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.aoi_combo.setAllowEmptyLayer(True)
        area_form.addRow(tr("Poligono"), self.aoi_combo)
        self.selected_only = QCheckBox(tr("Solo le feature selezionate"))
        area_form.addRow(self.selected_only)
        self.crs_label = QLabel("-")
        self.crs_label.setWordWrap(True)
        area_form.addRow(tr("CRS"), self.crs_label)

        terrain_box = QGroupBox(tr("2. Terreno"))
        terrain_form = QFormLayout(terrain_box)
        self.dem_combo = QgsMapLayerComboBox()
        self.dem_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.dem_combo.setAllowEmptyLayer(True)
        terrain_form.addRow(tr("DEM / DTM"), self.dem_combo)
        self.is_dsm = QCheckBox(tr("E' un DSM (chiome ed edifici inclusi)"))
        terrain_form.addRow(self.is_dsm)
        self.alt_mode = QComboBox()
        for key in AltitudeMode.ALL:
            self.alt_mode.addItem(AltitudeMode.LABELS[key], key)
        terrain_form.addRow(tr("Modalita' quota"), self.alt_mode)
        self.safety_margin = self._spin(0.0, 0.0, 200.0, 1.0, " m")
        terrain_form.addRow(tr("Margine sicurezza"), self.safety_margin)
        self.veg_clearance = self._spin(0.0, 0.0, 100.0, 1.0, " m")
        terrain_form.addRow(tr("Clearance vegetazione"), self.veg_clearance)
        self.dz_tolerance = self._spin(2.0, 0.1, 50.0, 0.5, " m")
        terrain_form.addRow(tr("Tolleranza verticale"), self.dz_tolerance)

        gear_box = QGroupBox(tr("3. Camera e drone"))
        gear_form = QFormLayout(gear_box)
        self.camera_combo = QComboBox()
        for key in self._camera_keys:
            self.camera_combo.addItem(self._cameras[key].name, key)
        gear_form.addRow(tr("Camera"), self.camera_combo)
        self.drone_combo = QComboBox()
        for key in self._drone_keys:
            self.drone_combo.addItem(self._drones[key].name, key)
        gear_form.addRow(tr("Drone"), self.drone_combo)

        mission_box = QGroupBox(tr("4. Missione"))
        mission_form = QFormLayout(mission_box)
        self.target_mode = QComboBox()
        self.target_mode.addItem(tr("Quota H_AGL [m]"), "h")
        self.target_mode.addItem(tr("GSD target [cm/px]"), "gsd")
        mission_form.addRow(tr("Definisci con"), self.target_mode)
        self.target_value = self._spin(80.0, 0.1, 2000.0, 5.0, "")
        mission_form.addRow(tr("Valore"), self.target_value)
        self.frontlap = self._spin(80.0, 1.0, 95.0, 5.0, " %")
        mission_form.addRow(tr("Sovrapp. longitudinale"), self.frontlap)
        self.sidelap = self._spin(70.0, 1.0, 95.0, 5.0, " %")
        mission_form.addRow(tr("Sovrapp. laterale"), self.sidelap)
        self.azimuth_mode = QComboBox()
        self.azimuth_mode.addItem(tr("Automatico (lato maggiore)"),
                                  sv.AZIMUTH_LONGEST_SIDE)
        self.azimuth_mode.addItem(tr("Lungo le curve di livello"),
                                  sv.AZIMUTH_ACROSS_SLOPE)
        self.azimuth_mode.addItem(tr("Manuale"), sv.AZIMUTH_MANUAL)
        mission_form.addRow(tr("Orientamento strip"), self.azimuth_mode)
        self.azimuth = self._spin(0.0, 0.0, 360.0, 5.0, " deg")
        mission_form.addRow(tr("Azimut manuale"), self.azimuth)
        self.double_grid = QCheckBox(tr("Doppia griglia 90 gradi (3D)"))
        mission_form.addRow(self.double_grid)
        self.speed = self._spin(0.0, 0.0, 30.0, 1.0, " m/s")
        self.speed.setSpecialValueText(tr("crociera del drone"))
        mission_form.addRow(tr("Velocita' richiesta"), self.speed)

        preview_box = QGroupBox(tr("5. Valori derivati (in tempo reale)"))
        preview_layout = QVBoxLayout(preview_box)
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.preview.setMinimumHeight(210)
        preview_layout.addWidget(self.preview)

        self.run_button = QPushButton(tr("Genera missione..."))
        self.tabs.addTab(self._scroll_page(
            [area_box, terrain_box, gear_box, mission_box, preview_box,
             self.run_button]), tr("UAV"))

        # ------------------------------------------------------- LAYER/EXPORT
        self.tabs.addTab(self._scroll_page([
            self._placeholder(
                tr("Layer / Export"),
                tr("Gli esportatori esistono e sono verificati: GeoPackage, "
                   "GeoJSON, KML, KMZ, GPX, CSV waypoint e centri di presa, "
                   "Litchi Mission Hub CSV, QGC WPL 110.\n\n"
                   "Il WPML DJI nativo resta rifiutato: lo schema non e' "
                   "verificato.\n\n"
                   "La scheda unificata, con lo stato VERIFIED / PARTIAL / "
                   "UNSUPPORTED per ogni adattatore, arriva con la milestone "
                   "1.2.6. Per ora si esporta dal dialogo dell'algoritmo."))]),
            tr("Layer/Export"))

        # ------------------------------------------------------- IMPOSTAZIONI
        self.tabs.addTab(self._scroll_page(self._build_settings_widgets()),
                         tr("Impostazioni"))

        outer.addWidget(self.tabs)
        return container

    def _build_settings_widgets(self):
        """The Impostazioni tab. Reads and writes settings.store, nothing else."""
        units_box = QGroupBox(tr("Unita' e visualizzazione"))
        units_form = QFormLayout(units_box)
        self.set_length_unit = QComboBox()
        for unit in ("m", "cm", "mm", "km", "ft", "in"):
            self.set_length_unit.addItem(unit, unit)
        units_form.addRow(tr("Unita' di lunghezza"), self.set_length_unit)
        self.set_angle_unit = QComboBox()
        for unit in ("deg", "rad", "gon"):
            self.set_angle_unit.addItem(unit, unit)
        units_form.addRow(tr("Unita' angolare"), self.set_angle_unit)
        self.set_decimals = QSpinBox()
        self.set_decimals.setRange(0, 6)
        units_form.addRow(tr("Decimali"), self.set_decimals)

        snap_box = QGroupBox(tr("Aggancio (snapping)"))
        snap_form = QFormLayout(snap_box)
        self.set_snap_enabled = QCheckBox(tr("Attivo"))
        self.set_snap_enabled.setToolTip(tr(
            "Scrive nella configurazione di aggancio DEL PROGETTO: gli "
            "strumenti CAD usano lo stesso motore dell'editing nativo di QGIS."))
        snap_form.addRow(self.set_snap_enabled)
        self.set_snap_tolerance = QSpinBox()
        self.set_snap_tolerance.setRange(1, 50)
        self.set_snap_tolerance.setSuffix(" px")
        snap_form.addRow(tr("Tolleranza"), self.set_snap_tolerance)
        self.set_snap_vertex = QCheckBox(tr("Vertici"))
        self.set_snap_segment = QCheckBox(tr("Segmenti"))
        snap_form.addRow(self.set_snap_vertex)
        snap_form.addRow(self.set_snap_segment)

        default_box = QGroupBox(tr("Valori predefiniti"))
        default_form = QFormLayout(default_box)
        self.set_export_format = QComboBox()
        for key in ("gpkg", "geojson", "kml", "kmz", "gpx", "csv_waypoints",
                    "litchi", "mavlink"):
            self.set_export_format.addItem(key, key)
        default_form.addRow(tr("Formato di esportazione"),
                            self.set_export_format)
        self.settings_hint = QLabel(tr(
            "Camera, drone, sovrapposizioni e quota predefinite si impostano "
            "nella scheda UAV e vengono ricordate alla chiusura."))
        self.settings_hint.setWordWrap(True)
        self.settings_hint.setStyleSheet("color:#666;")
        default_form.addRow(self.settings_hint)

        self.settings_reset = QPushButton(tr("Ripristina i valori predefiniti"))
        return [units_box, snap_box, default_box, self.settings_reset]

    def _spin(self, value, minimum, maximum, step, suffix):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(2)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        return spin

    def _wire(self):
        """Connect every input to the live recompute, recording each link."""
        widgets = [
            (self.aoi_combo, "layerChanged"),
            (self.dem_combo, "layerChanged"),
            (self.camera_combo, "currentIndexChanged"),
            (self.drone_combo, "currentIndexChanged"),
            (self.target_mode, "currentIndexChanged"),
            (self.alt_mode, "currentIndexChanged"),
            (self.azimuth_mode, "currentIndexChanged"),
            (self.target_value, "valueChanged"),
            (self.frontlap, "valueChanged"),
            (self.sidelap, "valueChanged"),
            (self.speed, "valueChanged"),
            (self.safety_margin, "valueChanged"),
            (self.veg_clearance, "valueChanged"),
            (self.double_grid, "toggled"),
            (self.selected_only, "toggled"),
        ]
        for widget, signal_name in widgets:
            signal = getattr(widget, signal_name)
            signal.connect(self.recompute)
            self._connections.append((signal, self.recompute))

        for button, slot in ((self.run_button, self._run_flight),
                             (self.cad_apply, self._apply_cad_values)):
            button.clicked.connect(slot)
            self._connections.append((button.clicked, slot))

        for widget, signal_name, slot in (
                (self.set_length_unit, "currentIndexChanged", self._save_settings),
                (self.set_angle_unit, "currentIndexChanged", self._save_settings),
                (self.set_decimals, "valueChanged", self._save_settings),
                (self.set_snap_enabled, "toggled", self._save_settings),
                (self.set_snap_tolerance, "valueChanged", self._save_settings),
                (self.set_snap_vertex, "toggled", self._save_settings),
                (self.set_snap_segment, "toggled", self._save_settings),
                (self.set_export_format, "currentIndexChanged", self._save_settings),
                (self.settings_reset, "clicked", self._reset_settings),
                (self.tabs, "currentChanged", self._save_settings)):
            signal = getattr(widget, signal_name)
            signal.connect(slot)
            self._connections.append((signal, slot))

        # The close checkbox drives the live polyline session, not just the
        # next one to be created.
        self.cad_pivot.currentIndexChanged.connect(self._on_pivot_changed)
        self._connections.append((self.cad_pivot.currentIndexChanged,
                                  self._on_pivot_changed))

        self.cad_close_ring.toggled.connect(self._on_close_ring_toggled)
        self._connections.append((self.cad_close_ring.toggled,
                                  self._on_close_ring_toggled))

    # -- settings ----------------------------------------------------------

    def _load_settings(self):
        """Populate the panel from the persisted store. Never writes back."""
        self._loading_settings = True
        try:
            self._select_data(self.set_length_unit,
                              app_settings.get("units/length"))
            self._select_data(self.set_angle_unit,
                              app_settings.get("units/angle"))
            self.set_decimals.setValue(app_settings.get("display/decimals"))
            self.set_snap_enabled.setChecked(app_settings.get("snap/enabled"))
            self.set_snap_tolerance.setValue(
                app_settings.get("snap/tolerance_px"))
            types = app_settings.snap_types()
            self.set_snap_vertex.setChecked("vertex" in types)
            self.set_snap_segment.setChecked("segment" in types)
            self._select_data(self.set_export_format,
                              app_settings.get("export/format"))
            self._select_data(self.camera_combo, app_settings.get("uav/camera"))
            self._select_data(self.drone_combo, app_settings.get("uav/drone"))
            self.frontlap.setValue(app_settings.get("uav/frontlap") * 100.0)
            self.sidelap.setValue(app_settings.get("uav/sidelap") * 100.0)
            self.target_value.setValue(app_settings.get("uav/h_agl_m"))
            index = app_settings.get("ui/last_tab")
            if 0 <= index < self.tabs.count():
                self.tabs.setCurrentIndex(index)
        finally:
            self._loading_settings = False
        self._apply_snapping_to_project()

    def _save_settings(self, *_args):
        """Persist the panel. Ignored while the panel is being populated."""
        if getattr(self, "_loading_settings", False):
            return
        app_settings.set("units/length", self.set_length_unit.currentData())
        app_settings.set("units/angle", self.set_angle_unit.currentData())
        app_settings.set("display/decimals", self.set_decimals.value())
        app_settings.set("snap/enabled", self.set_snap_enabled.isChecked())
        app_settings.set("snap/tolerance_px", self.set_snap_tolerance.value())
        types = []
        if self.set_snap_vertex.isChecked():
            types.append("vertex")
        if self.set_snap_segment.isChecked():
            types.append("segment")
        app_settings.set("snap/types", ",".join(types))
        app_settings.set("export/format", self.set_export_format.currentData())
        app_settings.set("uav/camera", self.camera_combo.currentData() or "")
        app_settings.set("uav/drone", self.drone_combo.currentData() or "")
        app_settings.set("uav/frontlap", self.frontlap.value() / 100.0)
        app_settings.set("uav/sidelap", self.sidelap.value() / 100.0)
        app_settings.set("uav/h_agl_m", self.target_value.value())
        app_settings.set("ui/last_tab", self.tabs.currentIndex())
        self._apply_snapping_to_project()
        self._push_snap_to_tool()

    def _reset_settings(self):
        app_settings.reset()
        self._load_settings()

    def _select_data(self, combo, value):
        """Select the entry whose userData equals value, if present."""
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _apply_snapping_to_project(self):
        """Write the operator's choice into the PROJECT's snapping config.

        Deliberately the project's own ``QgsSnappingConfig`` and not a private
        state: the CAD tools call ``QgsMapMouseEvent.snapPoint()``, which reads
        that same config, so what the panel shows is what QGIS actually does --
        for this plugin and for native digitising alike.
        """
        try:
            from qgis.core import (Qgis, QgsProject, QgsSnappingConfig,
                                   QgsTolerance)
        except ImportError:                                     # pragma: no cover
            return
        project = QgsProject.instance()
        config = QgsSnappingConfig(project.snappingConfig())
        config.setEnabled(self.set_snap_enabled.isChecked())
        config.setMode(QgsSnappingConfig.AllLayers)
        config.setTolerance(float(self.set_snap_tolerance.value()))
        config.setUnits(QgsTolerance.Pixels)
        flags = 0
        if self.set_snap_vertex.isChecked():
            flags |= int(Qgis.SnappingType.Vertex)
        if self.set_snap_segment.isChecked():
            flags |= int(Qgis.SnappingType.Segment)
        if flags:
            try:
                config.setTypeFlag(Qgis.SnappingTypes(flags))
            except (TypeError, ValueError):
                pass          # older enum shape; the rest of the config stands
        project.setSnappingConfig(config)

    def _push_snap_to_tool(self):
        """Keep a live map tool in step with the snapping toggle."""
        if self._cad_tool is not None:
            self._cad_tool.snap_enabled = self.set_snap_enabled.isChecked()

    def teardown(self):
        """Disconnect everything. Called from the plugin's unload()."""
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._connections = []
        self._cad_tool = None
        for panel in (getattr(self, "grid_panel", None),
                      getattr(self, "forest_panel", None)):
            if panel is not None:
                try:
                    panel.teardown()
                except Exception:                               # noqa: BLE001
                    pass

    # -- CAD tool binding --------------------------------------------------

    def bind_cad_tool(self, key, tool):
        """Show the numeric fields the active tool actually needs.

        The rows come from ``session.slot_list``: each tool declares its own
        constraints (name, kind, label) and the panel renders them. A new tool
        therefore needs no changes in this file.
        """
        self._cad_tool = tool
        slots = list(tool.session.slot_list)
        self.cad_box.setTitle(tr("CAD - {0}").format(tool.session.title))
        for index, (label, spin) in enumerate(self.cad_rows):
            if index < len(slots):
                slot = slots[index]
                label.setText(slot.label)
                spin.setSuffix(" deg" if slot.kind == di.KIND_ANGLE else " m")
                spin.setValue(float(slot.value) if slot.value is not None
                              else 0.0)
                label.setVisible(True)
                spin.setVisible(True)
            else:
                label.setVisible(False)
                spin.setVisible(False)
        is_rotate = hasattr(tool.session, "pivot_mode")
        self.cad_pivot_label.setVisible(is_rotate)
        self.cad_pivot.setVisible(is_rotate)
        if is_rotate and self.cad_pivot.count() == 0:
            from ..cad.tools.rotate import PIVOT_LABELS
            for pivot_key, pivot_label in PIVOT_LABELS.items():
                self.cad_pivot.addItem(tr(pivot_label), pivot_key)
        if is_rotate:
            index = self.cad_pivot.findData(tool.session.pivot_mode)
            if index >= 0:
                self.cad_pivot.setCurrentIndex(index)

        is_polyline = bool(getattr(tool.session, "multi_vertex", False))
        self.cad_close_ring.setVisible(is_polyline)
        if is_polyline:
            self.cad_close_ring.setChecked(bool(
                getattr(tool.session, "close", False)))
            self.cad_hint.setText(tr(
                "Clicca i vertici. Backspace toglie l'ultimo, Invio o doppio "
                "click chiude, Esc annulla tutto. @25<37 e' relativo al "
                "segmento precedente."))
        else:
            self.cad_hint.setText(tr(
                "Clicca l'origine sulla mappa, poi digita i valori (Invio) "
                "oppure compilali qui e premi Applica. Esc annulla."))

    def polyline_close_requested(self) -> bool:
        """Whether the operator asked the polyline to close its ring."""
        return bool(self.cad_close_ring.isChecked())

    def _on_close_ring_toggled(self, checked):
        """Apply the setting to the polyline currently being drawn."""
        if self._cad_tool is None:
            return
        session = self._cad_tool.session
        if getattr(session, "multi_vertex", False):
            session.close = bool(checked)

    def current_pivot_mode(self):
        """Pivot chosen for the rotate tool, or None when not applicable."""
        return self.cad_pivot.currentData() if self.cad_pivot.isVisible() else None

    def _on_pivot_changed(self, _index):
        if self._cad_tool is None:
            return
        session = self._cad_tool.session
        if hasattr(session, "pivot_mode"):
            session.pivot_mode = self.cad_pivot.currentData()

    def unbind_cad_tool(self):
        self._cad_tool = None
        self.cad_close_ring.setVisible(False)
        self.cad_pivot.setVisible(False)
        self.cad_pivot_label.setVisible(False)
        for label, spin in self.cad_rows:
            label.setVisible(False)
            spin.setVisible(False)
        self.cad_box.setTitle(tr("CAD - strumento attivo"))
        self.cad_hint.setText(tr("Nessuno strumento CAD attivo."))

    def current_cad_layer(self, geometry_type):
        """The chosen destination layer, if it can hold this geometry type.

        Returns None when nothing suitable is selected, which tells the plugin
        to fall back to its scratch layer rather than writing a polygon into a
        line layer.
        """
        layer = self.cad_layer_combo.currentLayer()
        if layer is None or not layer.isValid():
            return None
        try:
            from qgis.core import QgsWkbTypes

            wanted = (QgsWkbTypes.PolygonGeometry
                      if geometry_type == "Polygon"
                      else QgsWkbTypes.LineGeometry)
            if layer.geometryType() != wanted:
                self.iface.messageBar().pushMessage(
                    tr("GeoCad UAV"),
                    tr("Il layer '{0}' non accetta geometrie di tipo {1}: "
                       "viene usato un layer di lavoro.").format(
                           layer.name(), geometry_type),
                    level=Qgis.Warning)
                return None
        except Exception:                                       # noqa: BLE001
            return None
        return layer

    def _apply_cad_values(self):
        """Push the panel's numbers into the active tool's session."""
        if self._cad_tool is None:
            return
        session = self._cad_tool.session
        slots = list(session.slot_list)
        try:
            for index, (_label, spin) in enumerate(self.cad_rows):
                if index >= len(slots):
                    break
                session.set_value(slots[index].name, float(spin.value()))
            session.active_slot = len(slots)
            if session.is_ready:
                session.state = ToolState.PREVIEW
                self.cad_hint.setText(tr(
                    "Forma definita: premi Invio sulla mappa per confermare."))
            else:
                self.cad_hint.setText(tr(
                    "Manca ancora il punto di origine: clicca sulla mappa."))
        except Exception as exc:                                # noqa: BLE001
            self.iface.messageBar().pushMessage(
                tr("GeoCad UAV"), str(exc), level=Qgis.Warning)

    def closeEvent(self, event):                                # noqa: N802
        super().closeEvent(event)

    # -- live preview -----------------------------------------------------

    def current_camera(self):
        return self._cameras[self.camera_combo.currentData()]

    def current_drone(self):
        return self._drones[self.drone_combo.currentData()]

    def recompute(self, *_args):
        """Recompute every derived value. Pure arithmetic, no I/O."""
        try:
            self.preview.setHtml(self._preview_html())
        except Exception as exc:                                # noqa: BLE001
            self.preview.setHtml(
                "<p style='color:#a4262c'>{0}</p>".format(
                    tr("Parametri non validi: {0}").format(exc)))

    def _preview_html(self):
        camera = self.current_camera()
        drone = self.current_drone()
        overlap = pg.Overlap(frontlap=self.frontlap.value() / 100.0,
                             sidelap=self.sidelap.value() / 100.0)

        if self.target_mode.currentData() == "h":
            geometry = pg.solve_survey_geometry(
                camera, overlap, h_agl_m=self.target_value.value())
        else:
            geometry = pg.solve_survey_geometry(
                camera, overlap, gsd_m_px=self.target_value.value() / 100.0)

        speed = self.speed.value() or drone.v_cruise_ms
        budget = pg.build_speed_budget(geometry, speed, drone.v_max_ms)

        rows = [
            (tr("GSD"), "{0:.2f} cm/px".format(geometry.gsd_cm_px)),
            (tr("Quota H_AGL"), "{0:.1f} m".format(geometry.h_agl_m)),
            (tr("Impronta a terra"), "{0:.1f} x {1:.1f} m".format(
                geometry.footprint_across_m, geometry.footprint_along_m)),
            (tr("Interasse strip D_side"), "{0:.2f} m".format(geometry.d_side_m)),
            (tr("Base di presa D_front"), "{0:.2f} m".format(geometry.d_front_m)),
            (tr("Velocita' effettiva"), "{0:.1f} m/s".format(budget.effective)),
            (tr("Vincolo determinante"), budget.binding),
            (tr("Intervallo di scatto"), "{0:.2f} s".format(
                geometry.interval_at_speed(budget.effective))),
        ]

        notes = []
        if budget.is_capped_below_request:
            notes.append(tr(
                "Velocita' ridotta da {0:.1f} a {1:.1f} m/s dal vincolo "
                "'{2}'.").format(speed, budget.effective, budget.binding))
        if geometry.h_agl_m > drone.max_agl_m:
            notes.append(tr(
                "La quota {0:.0f} m supera il limite di {1:.0f} m AGL del "
                "profilo drone.").format(geometry.h_agl_m, drone.max_agl_m))

        # AOI-dependent estimates, when a polygon is available.
        layer = self.aoi_combo.currentLayer()
        if layer is not None and layer.isValid():
            crs = layer.crs()
            self.crs_label.setText(crs_svc.describe(crs))
            if crs_svc.is_geographic(crs):
                notes.append(tr(
                    "Il CRS del layer e' geografico: le distanze verrebbero "
                    "calcolate in gradi. Riproietta in UTM prima di generare."))
            else:
                area = self._aoi_area(layer)
                if area > 0:
                    rows.extend(self._estimates(area, geometry, budget, drone))
        else:
            self.crs_label.setText("-")

        table = "".join(
            "<tr><td style='color:#555'>{0}</td>"
            "<td align='right'><b>{1}</b></td></tr>".format(k, v)
            for k, v in rows)
        html = ("<table width='100%' cellspacing='0' cellpadding='3'>"
                + table + "</table>")
        if notes:
            html += "<ul style='margin-left:-18px;color:#8a6100'>" + "".join(
                "<li>{0}</li>".format(n) for n in notes) + "</ul>"
        html += ("<p style='color:#777;font-size:11px'>"
                 + tr("Stime planimetriche. I valori definitivi (copertura, "
                      "GSD effettivo, batterie) si ottengono generando la "
                      "missione sul DEM.") + "</p>")
        return html

    def _aoi_area(self, layer):
        features = (layer.getSelectedFeatures() if self.selected_only.isChecked()
                    else layer.getFeatures())
        return sum(f.geometry().area() for f in features if f.hasGeometry())

    def _estimates(self, area_m2, geometry, budget, drone):
        """First-order mission size from the AOI area alone.

        Deliberately labelled as an estimate: it assumes a compact area and
        ignores relief, so the real strip count on a long thin parcel will
        differ. It exists to answer "is this roughly one battery or six?"
        while the operator is still turning knobs.
        """
        side = math.sqrt(max(area_m2, 1.0))
        n_strips = max(int(math.ceil(side / geometry.d_side_m)), 1)
        survey_length = n_strips * side
        n_photos = int(math.ceil(survey_length / geometry.d_front_m))
        time_s = survey_length / max(budget.effective, 0.1)
        batteries = max(int(math.ceil(time_s / drone.usable_endurance_s)), 1)
        double = 2 if self.double_grid.isChecked() else 1
        return [
            (tr("Superficie AOI"), "{0:,.2f} ha".format(area_m2 / 10_000.0)),
            (tr("Strip stimate"), "{0:,}".format(n_strips * double)),
            (tr("Foto stimate"), "{0:,}".format(n_photos * double)),
            (tr("Percorso stimato"), "{0:,.0f} m".format(survey_length * double)),
            (tr("Tempo stimato"), format_duration(time_s * double)),
            (tr("Batterie stimate"), "{0}".format(batteries * double)),
        ]

    # -- launchers --------------------------------------------------------

    def _prefill(self):
        """Seed the Processing dialog with what the panel already knows."""
        params = {}
        layer = self.aoi_combo.currentLayer()
        if layer is not None:
            params["AOI"] = layer
        dem = self.dem_combo.currentLayer()
        if dem is not None:
            params["DEM"] = dem
        params["DEM_IS_DSM"] = self.is_dsm.isChecked()
        params["CAMERA"] = self.camera_combo.currentIndex()
        params["DRONE"] = self.drone_combo.currentIndex()
        params["TARGET_MODE"] = self.target_mode.currentIndex()
        params["TARGET_VALUE"] = self.target_value.value()
        params["FRONTLAP"] = self.frontlap.value()
        params["SIDELAP"] = self.sidelap.value()
        params["ALT_MODE"] = self.alt_mode.currentIndex()
        params["AZIMUTH_MODE"] = self.azimuth_mode.currentIndex()
        params["AZIMUTH"] = self.azimuth.value()
        params["DOUBLE_GRID"] = self.double_grid.isChecked()
        params["SPEED"] = self.speed.value()
        params["SAFETY_MARGIN"] = self.safety_margin.value()
        params["VEG_CLEARANCE"] = self.veg_clearance.value()
        params["DZ_TOLERANCE"] = self.dz_tolerance.value()
        return params

    def _open(self, alg_id, params):
        try:
            from processing import execAlgorithmDialog
            execAlgorithmDialog("geocaduav:" + alg_id, params)
        except Exception as exc:                                # noqa: BLE001
            self.iface.messageBar().pushMessage(
                tr("GeoCad UAV"),
                tr("Impossibile aprire l'algoritmo: {0}").format(exc),
                level=Qgis.Critical)

    def _run_flight(self):
        layer = self.aoi_combo.currentLayer()
        if layer is None:
            self.iface.messageBar().pushMessage(
                tr("GeoCad UAV"),
                tr("Seleziona un layer poligonale come area di progetto."),
                level=Qgis.Warning)
            return
        if crs_svc.is_geographic(layer.crs()):
            self.iface.messageBar().pushMessage(
                tr("GeoCad UAV"),
                tr("Il CRS {0} e' geografico: riproietta l'area in un CRS "
                   "metrico (UTM) prima di pianificare.").format(
                       layer.crs().authid()),
                level=Qgis.Critical)
            return
        self._open("planflight", self._prefill())

    def _run_grid(self):
        layer = self.aoi_combo.currentLayer()
        self._open("creategrid", {"AOI": layer} if layer else {})

    def _run_forest(self):
        layer = self.aoi_combo.currentLayer()
        params = {"AOI": layer} if layer else {}
        dem = self.dem_combo.currentLayer()
        if dem is not None:
            params["DEM"] = dem
        self._open("forestplanting", params)
