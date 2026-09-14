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
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                                 QDockWidget, QDoubleSpinBox, QFormLayout,
                                 QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                                 QMessageBox, QPushButton, QScrollArea,
                                 QSpinBox, QTableWidget, QTableWidgetItem,
                                 QTabWidget, QToolBar, QTreeWidget,
                                 QTreeWidgetItem, QVBoxLayout, QWidget)

from ..cad import dynamic_input as di
from ..cad.tools.base import ToolState
from ..io import cadastre as cadastre_mod
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
        #: The last committed shape, with its geometry and whatever the
        #: cadastre said about it. What "Interroga catasto" asks about.
        self._last_commit = None
        #: The running cadastral task. Kept alive here: a QgsTask the caller
        #: drops is collected mid-flight.
        self._cadastre_task = None
        #: Injected by the plugin so CAD parcels and reforestation parcels
        #: land on the same layer set instead of two of them.
        self._layers = None
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
        self.cad_layer_combo.setFilters(QgsMapLayerProxyModel.Filter.VectorLayer)
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

        # COMUNE -> FOGLIO -> PARTICELLA. The reading a cadastral answer
        # actually has, navigable: pick a comune and its parcels light up,
        # pick a sheet and that sheet's do, pick a parcel and it is framed.
        cadastre_form.addRow(QLabel(tr("Comune / Foglio / Particella")))
        self.cadastre_tree = QTreeWidget()
        self.cadastre_tree.setColumnCount(4)
        self.cadastre_tree.setHeaderLabels(
            [tr("Elemento"), tr("Sup. catastale"), tr("Sup. interessata"),
             tr("% part. / % prog.")])
        self.cadastre_tree.setMaximumHeight(200)
        self.cadastre_tree.setToolTip(tr(
            "Scegli un Comune, un foglio o una particella: la selezione si "
            "riflette sulla mappa."))
        cadastre_form.addRow(self.cadastre_tree)

        # RIEPILOGO PER COMUNE. A CAD shape can lie across two comuni, and
        # one of them being named in a label is how the other gets lost.
        cadastre_form.addRow(QLabel(tr("Riepilogo per Comune")))
        self.comune_table = QTableWidget(0, 5)
        self.comune_table.setHorizontalHeaderLabels(
            [tr("Comune"), tr("Particelle"), tr("Sup. catastale"),
             tr("Sup. interessata"), tr("%")])
        self.comune_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.comune_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.comune_table.setMaximumHeight(110)
        self.comune_table.setToolTip(tr(
            "Scegli un Comune per evidenziarne tutte le particelle sulla "
            "mappa."))
        cadastre_form.addRow(self.comune_table)

        # DETTAGLIO PARTICELLA. Comune first on every row: two comuni can
        # both hold a foglio 12 particella 45.
        cadastre_form.addRow(QLabel(tr("Dettaglio particelle")))
        self.parcel_table = QTableWidget(0, 6)
        self.parcel_table.setHorizontalHeaderLabels(
            [tr("Comune"), tr("Foglio"), tr("Particella"),
             tr("Sup. catastale"), tr("Sup. interessata"), tr("%")])
        self.parcel_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.parcel_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.parcel_table.setMaximumHeight(160)
        cadastre_form.addRow(self.parcel_table)

        cadastre_buttons = QHBoxLayout()
        self.cadastre_button = QPushButton(tr("Interroga catasto"))
        self.cadastre_button.setToolTip(tr(
            "Interroga il WFS dell'Agenzia delle Entrate sulla geometria "
            "appena disegnata e ne interseca ogni particella. Non un punto: "
            "la forma intera, con tutti i Comuni che attraversa."))
        self.cadastre_button.setEnabled(False)
        self.cadastre_show_button = QPushButton(tr("Mostra sulla mappa"))
        self.cadastre_show_button.setToolTip(tr(
            "Disegna tutte le particelle interessate e inquadra la mappa "
            "su di esse."))
        self.cadastre_show_button.setEnabled(False)
        self.cadastre_details_button = QPushButton(tr("Dettagli"))
        self.cadastre_details_button.setEnabled(False)
        self.cadastre_export_button = QPushButton(tr("Esporta"))
        self.cadastre_export_button.setToolTip(tr(
            "Scrive un CSV con una riga per particella: Comune, Belfiore, "
            "foglio, particella, superficie catastale, superficie "
            "interessata, percentuale sulla particella, percentuale sul "
            "progetto e geometrie. Niente di quello che e' stato misurato "
            "resta dentro."))
        self.cadastre_export_button.setEnabled(False)
        for button in (self.cadastre_button, self.cadastre_show_button,
                       self.cadastre_details_button,
                       self.cadastre_export_button):
            cadastre_buttons.addWidget(button)
        cadastre_form.addRow(cadastre_buttons)

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
            "Creato dal Cap. Niccolò Marco Mancini — RGPBIO."))
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
                (self.cadastre_button, "clicked", self.query_cadastre),
                (self.cadastre_show_button, "clicked", self.show_parcels),
                (self.cadastre_details_button, "clicked", self.show_details),
                (self.cadastre_export_button, "clicked",
                 self.export_cadastre),
                (self.cadastre_tree, "itemSelectionChanged",
                 self.on_tree_picked),
                (self.comune_table, "itemSelectionChanged",
                 self.on_comune_picked),
                (self.parcel_table, "itemSelectionChanged",
                 self.on_parcel_picked),
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
            from qgis.core import QgsSnappingConfig, QgsTolerance
        except ImportError:                                     # pragma: no cover
            return
        project = QgsProject.instance()
        config = QgsSnappingConfig(project.snappingConfig())
        config.setEnabled(self.set_snap_enabled.isChecked())
        config.setMode(QgsSnappingConfig.SnappingMode.AllLayers)
        config.setTolerance(float(self.set_snap_tolerance.value()))
        config.setUnits(QgsTolerance.UnitType.Pixels)
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
        # The previous tool stops publishing here first. A cadastral task
        # it left running answers seconds later, and its announce would
        # overwrite this panel with a shape the operator has moved on from.
        if self._cad_tool is not None and self._cad_tool is not tool:
            self._cad_tool.commit_observer = None
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
        self._last_commit = report
        self.cadastre_button.setEnabled(report.geometry is not None
                                        and not report.pending)
        self.fill_cadastre_tables(report.result)
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
        elif report.n_comuni > 1:
            self.cadastre_status.setText(tr(
                "{0} particelle in {1} Comuni: {2}.").format(
                    report.n_parcels, report.n_comuni,
                    "; ".join(report.result.comune_labels())))
        elif report.n_parcels > 1:
            self.cadastre_status.setText(tr(
                "{0} particelle nel comune di {1}.").format(
                    report.n_parcels, report.comune))
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
        self._last_commit = None
        for label in self.cadastre_labels.values():
            label.setText(DASH)
        self.cadastre_status.setText(tr("Nessuna geometria disegnata."))
        self.fill_cadastre_tables(None)
        self.cadastre_button.setEnabled(False)
        self.cadastre_export_button.setEnabled(False)

    # -- the cadastre, on demand -------------------------------------------

    def set_layers(self, layers) -> None:
        """Adopt the plugin's layer service, so CAD and forest share it."""
        self._layers = layers

    def layer_service(self):
        """The layer set the parcels go on. One per host, created on use."""
        if self._layers is None:
            from .map_layers import ProjectLayers                # noqa: PLC0415

            self._layers = ProjectLayers(self.iface)
        return self._layers

    def query_cadastre(self, transport=None):
        """Ask the cadastre about the shape on screen. Returns the task.

        The same engine the reforestation module uses -- geometry against
        every parcel it meets, intersected in a metric CRS -- started here
        on demand rather than only behind a commit.
        """
        report = self._last_commit
        geometry = getattr(report, "geometry", None)
        if geometry is None:
            self.cadastre_status.setText(tr(
                "Nessuna geometria da interrogare: disegnane una."))
            return None
        from qgis.core import QgsCoordinateReferenceSystem        # noqa: PLC0415

        crs = QgsCoordinateReferenceSystem(report.crs_authid or "")
        if not crs.isValid():
            self.cadastre_status.setText(tr(
                "Il layer non dichiara un sistema di riferimento."))
            return None
        self.cadastre_button.setEnabled(False)
        self.cadastre_status.setText(tr("Interrogazione in corso..."))
        try:
            self._cadastre_task = cadastre_mod.area_task(
                geometry, crs, self.on_cadastral_result,
                transport=transport if callable(transport) else None)
        except Exception as exc:                                  # noqa: BLE001
            self._cadastre_task = None
            self.cadastre_button.setEnabled(True)
            self.cadastre_status.setText(tr(
                "Interrogazione non riuscita: {0}").format(exc))
        return self._cadastre_task

    def on_cadastral_result(self, result) -> None:
        """The task answered. Fill the tables and say what came back."""
        self.cadastre_button.setEnabled(True)
        if self._last_commit is not None:
            self._last_commit.result = result
        self.fill_cadastre_tables(result)
        if result is None or not result.shares:
            message = getattr(result, "message", "") if result else ""
            self.cadastre_status.setText(message or tr(
                "Nessuna particella per questa geometria: fuori copertura, "
                "oppure servizio non raggiungibile."))
            return
        columns = cadastre_mod.cad_columns(result)
        from ..io.layer_factory import (CAT_COMUNE_FIELD,         # noqa: PLC0415
                                        CAT_FOGLIO_FIELD,
                                        CAT_PARTICELLA_FIELD)
        for key, field in (("comune", CAT_COMUNE_FIELD),
                           ("foglio", CAT_FOGLIO_FIELD),
                           ("particella", CAT_PARTICELLA_FIELD)):
            self.cadastre_labels[key].setText(columns.get(field) or DASH)
        self.cadastre_status.setText(tr(
            "{0} particelle in {1} Comuni: {2}.").format(
                result.n_parcels, len(result.comuni()),
                "; ".join(result.comune_labels())))
        self.show_parcels()

    def fill_cadastre_tables(self, result) -> int:
        """Both readings of one answer: per Comune, and per particella."""
        rows = result.rows() if result is not None else []
        summary = result.comune_rows() if result is not None else []
        self.parcel_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                row.get("comune", ""), row.get("foglio", ""),
                row.get("particella", ""),
                "{0:,.0f} m2".format(
                    float(row.get("superficie_catastale_m2") or 0.0)),
                "{0:,.0f} m2".format(
                    float(row.get("superficie_interessata_m2") or 0.0)),
                "{0:.2f}".format(float(row.get("percentuale") or 0.0)))
            for column, value in enumerate(values):
                self.parcel_table.setItem(index, column,
                                          QTableWidgetItem(str(value)))
        self.comune_table.setRowCount(len(summary))
        for index, row in enumerate(summary):
            values = (
                "{0} [{1}]".format(row.get("comune", ""),
                                   row.get("belfiore", "")),
                "{0} su {1} fogli".format(row.get("particelle", 0),
                                          row.get("fogli", 0)),
                "{0:,.0f} m2".format(
                    float(row.get("superficie_catastale_m2") or 0.0)),
                "{0:,.0f} m2".format(
                    float(row.get("superficie_interessata_m2") or 0.0)),
                "{0:.2f}".format(float(row.get("percentuale") or 0.0)))
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole,
                                 row.get("belfiore", ""))
                self.comune_table.setItem(index, column, item)
        self.fill_cadastre_tree(result)
        has_rows = bool(rows)
        self.cadastre_show_button.setEnabled(has_rows)
        self.cadastre_details_button.setEnabled(has_rows)
        self.cadastre_export_button.setEnabled(has_rows)
        return len(rows)

    def fill_cadastre_tree(self, result) -> int:
        """Comune -> Foglio -> Particella. Returns the number of leaves.

        Every node carries the row index of the shares beneath it, so
        selecting one is a selection on the map without a second lookup.
        """
        self.cadastre_tree.clear()
        if result is None or not result.shares:
            return 0
        position = {id(share): index
                    for index, share in enumerate(result.shares)}
        leaves = 0
        for code, comune, group in result.comuni():
            name = comune.label() if comune is not None else code
            taken = sum(s.intersection_area_m2 for s in group)
            cadastral = sum(s.parcel_area_m2 for s in group)
            project = sum(s.percent_of_project for s in group)
            node = QTreeWidgetItem([
                "{0} [{1}]".format(name, code),
                "{0:,.0f} m2".format(cadastral),
                "{0:,.0f} m2".format(taken),
                "{0:.2f} / {1:.2f}".format(
                    100.0 * taken / cadastral if cadastral else 0.0, project)])
            node.setData(0, Qt.ItemDataRole.UserRole,
                         [position[id(s)] for s in group])
            self.cadastre_tree.addTopLevelItem(node)
            for foglio, shares in result.fogli(code):
                sheet_taken = sum(s.intersection_area_m2 for s in shares)
                sheet_cad = sum(s.parcel_area_m2 for s in shares)
                sheet = QTreeWidgetItem([
                    tr("Foglio {0}").format(foglio or "-"),
                    "{0:,.0f} m2".format(sheet_cad),
                    "{0:,.0f} m2".format(sheet_taken),
                    "{0:.2f} / {1:.2f}".format(
                        100.0 * sheet_taken / sheet_cad if sheet_cad else 0.0,
                        sum(s.percent_of_project for s in shares))])
                sheet.setData(0, Qt.ItemDataRole.UserRole,
                              [position[id(s)] for s in shares])
                node.addChild(sheet)
                for share in shares:
                    leaf = QTreeWidgetItem([
                        tr("Particella {0}").format(
                            share.parcel.particella or "-"),
                        "{0:,.0f} m2".format(share.parcel_area_m2),
                        "{0:,.0f} m2".format(share.intersection_area_m2),
                        "{0:.2f} / {1:.2f}".format(
                            share.percent_of_parcel,
                            share.percent_of_project)])
                    leaf.setData(0, Qt.ItemDataRole.UserRole,
                                 [position[id(share)]])
                    sheet.addChild(leaf)
                    leaves += 1
        self.cadastre_tree.expandToDepth(0)
        return leaves

    def on_tree_picked(self, *_args) -> int:
        """Whatever level was chosen, its parcels on the map."""
        items = self.cadastre_tree.selectedItems()
        if not items or self.cadastral_result() is None:
            return 0
        rows = items[0].data(0, Qt.ItemDataRole.UserRole) or []
        if not rows:
            return 0
        return self.layer_service().select_rows("parcels", list(rows))

    def export_cadastre(self, path: str = "") -> str:
        """Write every row the result holds. Returns the path written."""
        result = self.cadastral_result()
        if result is None or not result.shares:
            return ""
        if not path:
            from qgis.PyQt.QtWidgets import QFileDialog          # noqa: PLC0415

            path, _filter = QFileDialog.getSaveFileName(
                self, tr("Esporta i dati catastali"), "catasto.csv", "*.csv")
        if not path:
            return ""
        import csv                                              # noqa: PLC0415
        import io as _io                                        # noqa: PLC0415

        rows = result.export_rows()
        with _io.open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=list(result.EXPORT_COLUMNS))
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        self.cadastre_status.setText(tr(
            "Scritte {0} particelle in {1}").format(len(rows), path))
        return path

    def cadastral_result(self):
        return getattr(self._last_commit, "result", None)

    def show_parcels(self, *_args) -> int:
        """Every particella of every Comune on the map, and frame them."""
        result = self.cadastral_result()
        if result is None:
            return 0
        layers = self.layer_service()
        if result.work_crs_authid or getattr(self._last_commit, "crs_authid",
                                             ""):
            layers.set_crs(self._last_commit.crs_authid)
        drawn = layers.draw_parcels(result)
        if drawn:
            layers.zoom_to("parcels")
        return drawn

    def on_comune_picked(self, *_args) -> int:
        """A Comune chosen in the summary highlights all of its particelle."""
        result = self.cadastral_result()
        row = self.comune_table.currentRow()
        if result is None or row < 0:
            return 0
        item = self.comune_table.item(row, 0)
        code = item.data(Qt.ItemDataRole.UserRole) if item else ""
        if not code:
            return 0
        rows = [index for index, share in enumerate(result.shares)
                if share.parcel.comune_code == code]
        return self.layer_service().select_rows("parcels", rows)

    def on_parcel_picked(self, *_args) -> bool:
        """A row chosen in the detail is that particella on the map."""
        row = self.parcel_table.currentRow()
        if row < 0 or self.cadastral_result() is None:
            return False
        return self.layer_service().select("parcels", row)

    def show_details(self, *_args):
        """The whole reading as text: Comune, foglio, particella, superfici."""
        result = self.cadastral_result()
        if result is None:
            return None
        text = "\n".join(result.describe())
        if self.iface is None:
            return text
        box = QMessageBox(self)
        box.setWindowTitle(tr("Particelle catastali"))
        box.setText(text)
        box.exec()
        return text

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

            wanted = (QgsWkbTypes.GeometryType.PolygonGeometry
                      if geometry_type == "Polygon"
                      else QgsWkbTypes.GeometryType.LineGeometry)
            if layer.geometryType() != wanted:
                self.iface.messageBar().pushMessage(
                    tr("GeoCad UAV"),
                    tr("Il layer '{0}' non accetta geometrie di tipo {1}: "
                       "viene usato un layer di lavoro.").format(
                           layer.name(), geometry_type),
                    level=Qgis.MessageLevel.Warning)
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
                tr("GeoCad UAV"), str(exc), level=Qgis.MessageLevel.Warning)

    def closeEvent(self, event):                                # noqa: N802
        super().closeEvent(event)
