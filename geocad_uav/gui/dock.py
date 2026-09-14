"""
Dock panel: the CAD tools, what they just drew, and the settings.

Two tabs, and only what has nowhere better to be. The CAD tab carries the
map-tool toolbar, the numeric fields of the active tool and the cadastral
readout of the shape it just placed; Impostazioni holds the stored
preferences.

Everything else that was here has gone to the dashboard, where it belongs
to a module with its own navigation path: the flight planner and its
exports in 1.32.0, and the reforestation panel in 1.36.0. That panel was a
second, simpler planting -- no zones, no species mix, no constraints, no
cadastre -- and two ways to lay out the same stand is one too many. GUI
calls services; services never import widgets (spec section 2).
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


def tr(text):
    return QCoreApplication.translate("GeoCadUav", text)


M2_PER_HA = 10_000.0

#: The cadastral readout, in the order a deed reads: where, then which
#: sheet, then which parcel, then how much ground the shape covers.
CADASTRE_ROWS = (
    ("comune", "Comune"),
    ("foglio", "Foglio"),
    ("particella", "Particella"),
    ("area", "Superficie"),
    ("perimetro", "Perimetro"),
)

DASH = "--"


class GeoCadDock(QDockWidget):
    """Parameter panel with a live derived-values preview."""

    def __init__(self, iface, parent=None):
        super().__init__(tr("GeoCad UAV Toolkit"), parent)
        self.setObjectName("GeoCadUavDock")
        self.iface = iface
        self._connections = []
        self._cad_tool = None
        self.setWidget(self._build())
        self._load_settings()
        self._wire()

    # -- construction -----------------------------------------------------

    # v1.4.5: the Grid tab was withdrawn; the lattice engine (core.grid) is
    # still there and still feeds the reforestation schemes.
    # v1.32.0: UAV and Layer/Export went to the dashboard workflow.
    # v1.36.0: so did Rimboschimento, which had a simpler planting of its
    # own here. Two tabs left, and neither duplicates a module.
    TAB_CAD, TAB_SETTINGS = range(2)

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

        # ------------------------------------------------------- CATASTO
        # Not a tab of its own and not behind a button: the parcel a shape
        # falls on is part of what the operator just drew, so it is read
        # where the drawing happens. The lookup runs on its own task; this
        # box shows what the attribute table already holds and fills the
        # three cadastral rows in behind it when the service answers.
        self.cadastre_box = QGroupBox(tr("Dati catastali"))
        cadastre_form = QFormLayout(self.cadastre_box)
        self.cadastre_labels = {}
        for key, label in CADASTRE_ROWS:
            value = QLabel(DASH)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            cadastre_form.addRow(tr(label), value)
            self.cadastre_labels[key] = value
        self.cadastre_status = QLabel(tr("Nessuna geometria disegnata."))
        self.cadastre_status.setWordWrap(True)
        cadastre_form.addRow(self.cadastre_status)
        self.set_cadastre_enabled = QCheckBox(
            tr("Interroga il catasto a ogni geometria"))
        self.set_cadastre_enabled.setToolTip(tr(
            "Interrogazione WFS dell'Agenzia delle Entrate, su un task in "
            "background. Spegnila quando lavori senza rete o fuori dal "
            "territorio coperto: geometria, area e perimetro vengono "
            "scritti comunque."))
        cadastre_form.addRow(self.set_cadastre_enabled)

        self.tabs.addTab(self._scroll_page([self.cad_toolbar, self.cad_box,
                                            self.cadastre_box]),
                         tr("CAD"))

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

        self.credit = QLabel(tr(
            "Ideato e realizzato dal Cap. Niccolò Marco Mancini — "
            "Gruppo di Cartografia Numerica, RGPBIO."))
        self.credit.setWordWrap(True)
        self.credit.setStyleSheet("color:#555;")
        return [units_box, snap_box, default_box, self.settings_reset,
                self.credit]

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
        self.cad_apply.clicked.connect(self._apply_cad_values)
        self._connections.append((self.cad_apply.clicked,
                                  self._apply_cad_values))

        for widget, signal_name, slot in (
                (self.set_length_unit, "currentIndexChanged", self._save_settings),
                (self.set_angle_unit, "currentIndexChanged", self._save_settings),
                (self.set_decimals, "valueChanged", self._save_settings),
                (self.set_snap_enabled, "toggled", self._save_settings),
                (self.set_snap_tolerance, "valueChanged", self._save_settings),
                (self.set_snap_vertex, "toggled", self._save_settings),
                (self.set_snap_segment, "toggled", self._save_settings),
                (self.set_cadastre_enabled, "toggled", self._save_settings),
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
            self.set_cadastre_enabled.setChecked(
                app_settings.get("cadastre/enabled"))
            self._select_data(self.set_export_format,
                              app_settings.get("export/format"))
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
        app_settings.set("cadastre/enabled",
                         self.set_cadastre_enabled.isChecked())
        app_settings.set("export/format", self.set_export_format.currentData())
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
        if self._cad_tool is not None:
            self._cad_tool.commit_observer = None
        self._cad_tool = None

    # -- CAD tool binding --------------------------------------------------

    def bind_cad_tool(self, key, tool):
        """Show the numeric fields the active tool actually needs.

        The rows come from ``session.slot_list``: each tool declares its own
        constraints (name, kind, label) and the panel renders them. A new tool
        therefore needs no changes in this file.
        """
        self._cad_tool = tool
        # The one line that makes the readout live: the tool publishes each
        # commit, and this panel is what listens.
        tool.commit_observer = self.show_commit
        if getattr(tool, "last_commit", None) is not None:
            self.show_commit(tool.last_commit)
        else:
            self.clear_commit()
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

        # v1.7.0: the checkbox belongs to a session that can be told to close
        # its ring. The polygon tool closes on its own first vertex, so it has
        # no such switch and the control stays hidden for it.
        multi_vertex = bool(getattr(tool.session, "multi_vertex", False))
        self.cad_close_ring.setVisible(multi_vertex
                                       and hasattr(tool.session, "close"))
        if multi_vertex:
            self.cad_close_ring.setChecked(bool(
                getattr(tool.session, "close", False)))
            self.cad_hint.setText(tr(
                "Clicca i vertici. Richiudi sul primo vertice o premi Invio; "
                "il tasto destro toglie l'ultimo (Ctrl+Z), Ctrl+Y lo rimette, "
                "Esc annulla tutto."))
        else:
            self.cad_hint.setText(tr(
                "Clicca l'origine sulla mappa, poi digita i valori (Invio) "
                "oppure compilali qui e premi Applica. Esc annulla."))

    # -- the cadastral readout ---------------------------------------------

    def show_commit(self, report) -> None:
        """Mirror one committed CAD shape and the parcel it sits on.

        Called by the map tool: once when the feature lands, with the three
        cadastral rows still empty and the lookup out, and once more when
        the task answers. Everything shown is read off the report, which was
        itself read off the layer -- the panel never recomputes an area, so
        what it says and what the attribute table says cannot drift apart.
        """
        if report is None:
            self.clear_commit()
            return
        self.cadastre_labels["area"].setText(
            tr("{0:,.2f} m2 ({1:,.4f} ha)").format(
                report.area_m2, report.area_m2 / M2_PER_HA))
        self.cadastre_labels["perimetro"].setText(
            tr("{0:,.2f} m").format(report.perimeter_m))
        for key, value in (("comune", report.comune),
                           ("foglio", report.foglio),
                           ("particella", report.particella)):
            self.cadastre_labels[key].setText(
                value if value else (tr("interrogazione in corso...")
                                     if report.pending else DASH))
        if report.pending:
            self.cadastre_status.setText(tr(
                "Geometria scritta sul layer '{0}'. Il catasto risponde fra "
                "qualche secondo.").format(report.layer_name))
        elif report.has_parcel:
            self.cadastre_status.setText(tr(
                "{0}, comune di {1}.").format(report.parcel_label(),
                                              report.comune))
        elif report.warning:
            self.cadastre_status.setText(report.warning)
        elif not self.set_cadastre_enabled.isChecked():
            self.cadastre_status.setText(tr(
                "Interrogazione catastale disattivata: area e perimetro sono "
                "comunque sul layer."))
        else:
            self.cadastre_status.setText(tr(
                "Nessuna particella per questo punto: fuori copertura, "
                "oppure servizio non raggiungibile."))

    def clear_commit(self) -> None:
        """Back to dashes: no shape of this tool's is on screen any more."""
        for label in self.cadastre_labels.values():
            label.setText(DASH)
        self.cadastre_status.setText(tr("Nessuna geometria disegnata."))

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
        if self._cad_tool is not None:
            self._cad_tool.commit_observer = None
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
