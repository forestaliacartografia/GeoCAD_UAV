"""
Forest tab: a planting scheme inside an extent, drawn now or already in a layer.

The scheme itself is ``forest.planting.plan_planting_for_geometry`` and the KPI
are ``forest.stats.compute_stats``, both untouched. This panel supplies the
extent, the ``GridSpec`` and the optional topographic filter, previews the
result, and writes it through ``io.layer_factory.build_planting_layers``.

The extent picker is :class:`~.grid_panel.ExtentSource`, shared with the Grid
tab so the two cannot disagree about what "the area" means.
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
                                 QMessageBox, QPushButton, QTextBrowser,
                                 QVBoxLayout, QWidget)

from ..core import crs as crs_svc
from ..core import grid as grid_mod
from ..core import undo
from ..core.errors import GeoCadError
from ..forest import planting as planting_mod
from ..forest import stats as stats_mod
from ..io import layer_factory as lf
from .grid_panel import ExtentSource


def tr(text):
    return QCoreApplication.translate("GeoCadUav", text)


class ForestPanel(QWidget):
    """Planting parameters, KPI, one write."""

    PATTERN_KEYS = (grid_mod.PATTERN_RECT, grid_mod.PATTERN_SQUARE,
                    grid_mod.PATTERN_QUINCUNX, grid_mod.PATTERN_HEX)

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._band = None
        self._connections = []
        self.last_result = None
        self.last_stats = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.extent = ExtentSource(self.iface)
        self.extent.on_change(self.refresh_preview)
        layout.addWidget(self.extent)

        box = QGroupBox(tr("Sesto d'impianto"))
        form = QFormLayout(box)
        self.plant_spacing = self._spin(3.0, 0.01, 1000.0, " m")
        form.addRow(tr("Distanza fra le piante"), self.plant_spacing)
        self.row_spacing = self._spin(2.0, 0.01, 1000.0, " m")
        form.addRow(tr("Distanza fra le file"), self.row_spacing)
        self.step_from_map = QPushButton(tr("Sesto da due click"))
        form.addRow(self.step_from_map)

        self.pattern = QComboBox()
        for key in self.PATTERN_KEYS:
            self.pattern.addItem(grid_mod.PATTERN_LABELS[key], key)
        form.addRow(tr("Schema"), self.pattern)

        self.azimuth = self._spin(0.0, 0.0, 360.0, " deg")
        form.addRow(tr("Orientamento file"), self.azimuth)
        azimuth_buttons = QHBoxLayout()
        self.azimuth_from_map = QPushButton(tr("Da due click"))
        self.azimuth_from_edge = QPushButton(tr("Parallelo a un lato"))
        azimuth_buttons.addWidget(self.azimuth_from_map)
        azimuth_buttons.addWidget(self.azimuth_from_edge)
        form.addRow(azimuth_buttons)

        self.margin = self._spin(2.0, 0.0, 1000.0, " m")
        form.addRow(tr("Margine dal bordo"), self.margin)
        layout.addWidget(box)

        filters = QGroupBox(tr("Filtri topografici (richiedono un DEM)"))
        filter_form = QFormLayout(filters)
        self.dem_combo = QgsMapLayerComboBox()
        self.dem_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.dem_combo.setAllowEmptyLayer(True, tr("(nessun DEM)"))
        filter_form.addRow(tr("DEM"), self.dem_combo)
        self.use_slope = QCheckBox(tr("Limita la pendenza"))
        filter_form.addRow(self.use_slope)
        self.slope_max = self._spin(30.0, 0.0, 90.0, " deg")
        filter_form.addRow(tr("Pendenza massima"), self.slope_max)
        layout.addWidget(filters)

        self.kpi = QTextBrowser()
        self.kpi.setMinimumHeight(150)
        layout.addWidget(self.kpi)

        buttons = QHBoxLayout()
        self.preview_button = QPushButton(tr("Anteprima"))
        self.confirm_button = QPushButton(tr("Crea layer"))
        buttons.addWidget(self.preview_button)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)

        for widget, signal_name in (
                (self.plant_spacing, "valueChanged"),
                (self.row_spacing, "valueChanged"),
                (self.pattern, "currentIndexChanged"),
                (self.azimuth, "valueChanged"),
                (self.margin, "valueChanged"),
                (self.use_slope, "toggled"),
                (self.slope_max, "valueChanged"),
                (self.dem_combo, "layerChanged")):
            signal = getattr(widget, signal_name)
            signal.connect(self.refresh_preview)
            self._connections.append((signal, self.refresh_preview))

        for button, slot in ((self.preview_button, self.refresh_preview),
                             (self.confirm_button, self.confirm),
                             (self.step_from_map, self._pick_step),
                             (self.azimuth_from_map, self._pick_azimuth),
                             (self.azimuth_from_edge, self._azimuth_from_edge)):
            button.clicked.connect(slot)
            self._connections.append((button.clicked, slot))

    def _spin(self, value, minimum, maximum, suffix):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(3)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        return spin

    # -- parameters --------------------------------------------------------

    def build_spec(self) -> grid_mod.GridSpec:
        return grid_mod.GridSpec(
            spacing_x=self.plant_spacing.value(),
            spacing_y=self.row_spacing.value(),
            azimuth_deg=self.azimuth.value(),
            pattern=self.pattern.currentData(),
            margin_m=self.margin.value())

    def build_filter(self) -> Optional[planting_mod.TopographicFilter]:
        if not self.use_slope.isChecked():
            return None
        return planting_mod.TopographicFilter(
            slope_max_deg=self.slope_max.value())

    def _pick_step(self):
        def apply_step(length, _azimuth):
            self.plant_spacing.setValue(length)
            self.refresh_preview()

        self.extent.start_measure(apply_step)

    def _pick_azimuth(self):
        def apply_azimuth(_length, azimuth):
            self.azimuth.setValue(azimuth % 360.0)
            self.refresh_preview()

        self.extent.start_measure(apply_azimuth)

    def _azimuth_from_edge(self):
        geometry = self.extent.geometry()
        if geometry is None:
            return
        try:
            ring = geometry.asPolygon()[0]
        except (IndexError, TypeError):
            return
        points = np.array([[p.x(), p.y()] for p in ring], dtype=float)
        self.azimuth.setValue(grid_mod.azimuth_of_longest_edge(points) % 360.0)
        self.refresh_preview()

    # -- computation -------------------------------------------------------

    def compute(self):
        """Run the frozen planner on the current extent. Returns the result."""
        geometry = self.extent.geometry()
        if geometry is None:
            return None
        crs = self.extent.crs()
        if crs is not None and crs_svc.is_geographic(crs):
            raise GeoCadError(
                "geographic CRS for a metric planting scheme",
                user_message="Il CRS dell'estensione ({0}) e' geografico: il "
                             "sesto verrebbe letto in gradi.".format(
                                 crs.authid()),
                hint="Riproietta l'estensione in un CRS metrico (UTM).")

        terrain = None
        topo = self.build_filter()
        dem_layer = self.dem_combo.currentLayer()
        if dem_layer is not None and topo is not None:
            from ..core.z import TerrainModel                   # noqa: PLC0415

            spec_preview = self.build_spec()
            box = geometry.boundingBox()
            terrain, _warnings = TerrainModel.from_layer(
                dem_layer, crs,
                (box.xMinimum(), box.yMinimum(), box.xMaximum(),
                 box.yMaximum()),
                margin_m=max(spec_preview.spacing_x,
                             spec_preview.spacing_y) * 2.0)

        return planting_mod.plan_planting_for_geometry(
            geometry, self.build_spec(), terrain=terrain, topo_filter=topo,
            compute_edge_distance=False)

    # -- preview -----------------------------------------------------------

    def _ensure_band(self):
        canvas = self.iface.mapCanvas() if self.iface else None
        if canvas is None:
            return None
        if self._band is None:
            self._band = QgsRubberBand(canvas, QgsWkbTypes.PointGeometry)
            self._band.setColor(QColor(30, 140, 60, 210))
            self._band.setIcon(QgsRubberBand.ICON_CIRCLE)
            self._band.setIconSize(4)
            self._band.setWidth(1)
        return self._band

    def refresh_preview(self, *_args):
        """Plan and draw. Writes nothing; never refreshes the canvas."""
        try:
            result = self.compute()
        except GeoCadError as exc:
            self.kpi.setPlainText(exc.formatted())
            self.clear_preview()
            self.last_result = None
            self.last_stats = None
            return None

        self.last_result = result
        if result is None:
            self.kpi.setPlainText(tr("Nessuna estensione."))
            self.clear_preview()
            return None

        self.last_stats = stats_mod.compute_stats(result)
        band = self._ensure_band()
        if band is not None:
            band.reset(QgsWkbTypes.PointGeometry)
            for plant in result.plants:
                band.addPoint(QgsPointXY(plant.x, plant.y), False)
            band.updatePosition()
            band.show()

        self.kpi.setPlainText("\n".join(
            stats_mod.format_report(self.last_stats, result)))
        return result

    def clear_preview(self):
        if self._band is not None:
            self._band.reset(QgsWkbTypes.PointGeometry)
            self._band.hide()

    # -- commit ------------------------------------------------------------

    def confirm(self, *_args):
        """Write plants, rejected positions and row lines. One undo each."""
        result = self.last_result if self.last_result is not None \
            else self.refresh_preview()
        if result is None or not result.plants:
            return {}
        if undo.needs_confirmation(len(result.plants)) \
                and not self._ask(len(result.plants)):
            return {}

        crs = self.extent.crs()
        layers = lf.build_planting_layers(
            result, crs.authid() if crs else "EPSG:4326", include_excluded=True)
        for layer in layers.values():
            QgsProject.instance().addMapLayer(layer)
        self.clear_preview()
        self._notify_user(tr("Creati {0} layer, {1:,} piante.").format(
            len(layers), len(result.plants)))
        return layers

    def _ask(self, count):
        answer = QMessageBox.question(
            self, tr("GeoCad UAV"),
            undo.confirmation_message(count, tr("impianto")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _notify_user(self, text):
        if self.iface is None:
            return
        try:
            self.iface.messageBar().pushMessage(tr("GeoCad UAV"), text,
                                                level=Qgis.Info)
        except Exception:                                       # noqa: BLE001
            pass

    def teardown(self):
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._connections = []
        self.extent.teardown()
        self.clear_preview()
