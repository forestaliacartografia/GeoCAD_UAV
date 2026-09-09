"""
Dock panel: the CAD numeric fields, the tabs, and the settings.

Each tab owns its own work. The dock builds the CAD side (toolbar, constraint
fields, snapping) and then mounts one panel per domain -- Grid, Forest, UAV --
each of which talks to the frozen engine directly. The dock holds no mission
state of its own: what used to be a hand-built UAV form here now lives in
``gui.uav_panel``, which plans on the DEM instead of pre-filling a Processing
dialog. GUI calls services; services never import widgets (spec section 2).
"""

from __future__ import annotations

from qgis.core import Qgis, QgsMapLayerProxyModel, QgsProject
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import QCoreApplication, QSize, Qt
from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QDockWidget,
                                 QDoubleSpinBox, QFormLayout, QGroupBox,
                                 QHBoxLayout, QLabel, QPushButton, QScrollArea,
                                 QSpinBox, QTabWidget, QToolBar, QVBoxLayout,
                                 QWidget)

from ..cad import dynamic_input as di
from ..cad.tools.base import ToolState
from ..settings import settings as app_settings
from .forest_panel import ForestPanel
from .grid_panel import GridPanel
from .mission_player import MissionPlayer, RATES as PLAYER_RATES
from .uav_panel import UavPanel


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
                      getattr(self, "forest_panel", None),
                      getattr(self, "uav_panel", None),
                      getattr(self, "player", None)):
            if panel is not None:
                try:
                    panel.teardown()
                except Exception:                               # noqa: BLE001
                    pass
        self.setWidget(self._build())
        self._load_settings()
        self._wire()

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
        self.uav_panel = UavPanel(self.iface)
        self.dem_download_button = QPushButton(tr("Scarica un DEM..."))
        self.dem_download_button.setToolTip(tr(
            "Scarica un modello di elevazione e aggiungilo al progetto; "
            "comparira' nell'elenco DEM qui sopra."))
        self.tabs.addTab(
            self._scroll_page([self.uav_panel, self.dem_download_button,
                               self._build_player_box()]),
            tr("UAV"))

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

    def _build_player_box(self):
        """Transport controls over the mission the UAV panel already built."""
        box = QGroupBox(tr("Simulazione del volo"))
        layout = QVBoxLayout(box)
        self.player = MissionPlayer(self.iface, self)

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

        self.player_status = QLabel()
        self.player_status.setWordWrap(True)
        layout.addWidget(self.player_status)
        self._refresh_player()
        return box

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
        """Connect the dock's own controls, recording each link."""
        for button, slot in ((self.cad_apply, self._apply_cad_values),
                             (self.dem_download_button, self._open_dem_dialog),
                             (self.play_button, self._play_mission),
                             (self.pause_button, self._pause_mission),
                             (self.stop_button, self._stop_mission)):
            button.clicked.connect(slot)
            self._connections.append((button.clicked, slot))

        # The panel owns last_mission; the dock only reacts to it. Its own
        # slots are connected first, so by the time these run the attribute
        # already holds the mission that was just generated.
        for signal, slot in (
                (self.rate_combo.currentIndexChanged, self._change_rate),
                (self.uav_panel.generate_button.clicked, self._refresh_player),
                (self.player.ticked, self._on_player_tick),
                (self.player.finished, self._refresh_player)):
            signal.connect(slot)
            self._connections.append((signal, slot))

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
            self.uav_panel.load_settings()
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
        self.uav_panel.save_settings()
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
                      getattr(self, "forest_panel", None),
                      getattr(self, "uav_panel", None),
                      getattr(self, "player", None)):
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

    # -- mission playback --------------------------------------------------

    def _play_mission(self, *_args):
        mission = self.uav_panel.last_mission
        if mission is None:
            self.player_status.setText(tr(
                "Nessuna missione da simulare: premi prima Genera rotta."))
            self._refresh_player()
            return
        if not self.player.play(mission):
            self.player_status.setText(self.player.message)
        self._refresh_player()

    def _pause_mission(self, *_args):
        self.player.pause()
        self._refresh_player()

    def _stop_mission(self, *_args):
        self.player.stop()
        self._refresh_player()

    def _change_rate(self, *_args):
        self.player.set_rate(self.rate_combo.currentData() or 1)
        self._refresh_player()

    def _on_player_tick(self, *_args):
        self.player_status.setText(self.player.summary())

    def _refresh_player(self, *_args):
        """Play is enabled only when the panel actually holds a mission."""
        mission = getattr(self.uav_panel, "last_mission", None)
        self.play_button.setEnabled(mission is not None)
        self.pause_button.setEnabled(self.player.is_playing)
        self.stop_button.setEnabled(self.player.mission is not None)
        if mission is None:
            self.player_status.setText(tr(
                "Nessuna missione da simulare: genera prima la rotta qui "
                "sopra."))
        else:
            self.player_status.setText(self.player.summary())

    def _open_dem_dialog(self, *_args):
        """Modeless: the download runs on the task manager, not here."""
        from .dem_dialog import DemDownloadDialog                # noqa: PLC0415

        dialog = DemDownloadDialog(self.iface, self)
        dialog.show()
        self._dem_dialog = dialog

    def closeEvent(self, event):                                # noqa: N802
        super().closeEvent(event)
