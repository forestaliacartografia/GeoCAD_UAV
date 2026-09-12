"""
Forest tab: a planting scheme inside an extent, drawn now or already in a layer.

The scheme itself is ``forest.planting.plan_planting_for_geometry`` and the KPI
are ``forest.stats.compute_stats``, both untouched. This panel supplies the
extent, the ``GridSpec`` and the optional topographic filter, previews the
result, and writes it through ``io.layer_factory.build_planting_layers``.

The extent picker is :class:`~.extent_source.ExtentSource`, shared with the UAV
tab so the two cannot disagree about what "the area" means.
"""

from __future__ import annotations

import math

from typing import Optional

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
from .extent_source import ExtentSource


def tr(text):
    return QCoreApplication.translate("GeoCadUav", text)


class ForestPanel(QWidget):
    """Planting parameters, KPI, one write."""

    #: Every scheme core.grid actually generates, in the order an operator
    #: is likely to want them. There is no sixth: a scheme without a
    #: generator would be a label over nothing.
    #: Rectangular first, because that is GridSpec's own default: a panel
    #: that opened on a square scheme would silently force dy = dx on an
    #: operator who had typed two different distances.
    PATTERN_KEYS = (grid_mod.PATTERN_RECT, grid_mod.PATTERN_SQUARE,
                    grid_mod.PATTERN_QUINCUNX, grid_mod.PATTERN_HEX,
                    grid_mod.PATTERN_ROWS)

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._band = None
        self._connections = []
        self.last_result = None
        self.last_stats = None
        self.last_schedule = []
        self._build()
        self._apply_pattern_defaults()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.extent = ExtentSource(self.iface)
        self.extent.on_change(self.refresh_preview)
        layout.addWidget(self.extent)

        box = QGroupBox(tr("Sesto d'impianto"))
        form = QFormLayout(box)
        # Measured, not assumed (P1): spacing_x is the step between two
        # plants of the same row, spacing_y the step between two rows.
        self.plant_spacing = self._spin(3.0, 0.01, 1000.0, " m")
        form.addRow(tr("Distanza fra le piante (sulla fila)"),
                    self.plant_spacing)
        self.row_spacing = self._spin(2.0, 0.01, 1000.0, " m")
        self.row_label = QLabel(tr("Distanza fra le file"))
        form.addRow(self.row_label, self.row_spacing)
        self.step_from_map = QPushButton(tr("Misura distanza in mappa"))
        self.step_from_map.setToolTip(tr(
            "Due click sulla mappa: la distanza misurata diventa la "
            "distanza fra le piante sulla fila."))
        form.addRow(self.step_from_map)

        self.pattern = QComboBox()
        for key in self.PATTERN_KEYS:
            self.pattern.addItem(grid_mod.PATTERN_LABELS[key], key)
        form.addRow(tr("Schema"), self.pattern)

        self.azimuth = self._spin(0.0, 0.0, 360.0, " deg")
        # P1 again: at azimuth 0 a row runs due East, so this bearing is
        # across the rows, not along them. The old label said the opposite.
        self.azimuth.setToolTip(tr(
            "Azimut del reticolo, misurato in senso orario da Nord. Le file "
            "corrono perpendicolari a questa direzione: con 0 le file vanno "
            "da Ovest a Est."))
        form.addRow(tr("Azimut del sesto"), self.azimuth)
        # v1.12.0: the two alignment shortcuts that used to sit here are
        # gone. Both set this same spin box, two clicks later than typing the
        # number into it, and the orientation an operator actually wants on a
        # hillside comes from the ground rather than from a pair of clicks --
        # forest.reforestation.orient computes it from the DEM, a girapoggio
        # or a rittochino.

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

        self.plant_spacing.valueChanged.connect(self._mirror_square)
        self._connections.append((self.plant_spacing.valueChanged,
                                  self._mirror_square))
        self.pattern.currentIndexChanged.connect(self._on_pattern_changed)
        self._connections.append((self.pattern.currentIndexChanged,
                                  self._on_pattern_changed))

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
                             (self.step_from_map, self._pick_step)):
            button.clicked.connect(slot)
            self._connections.append((button.clicked, slot))

    def _on_pattern_changed(self, *_args):
        """A square scheme has one distance, not two.

        ``GridSpec.effective_spacing`` already forces dy = dx for the square
        pattern, so a row spacing the operator can still type would be
        ignored by the engine. The field is mirrored and disabled instead of
        quietly having no effect.
        """
        square = self.pattern.currentData() == grid_mod.PATTERN_SQUARE
        self.row_spacing.setEnabled(not square)
        if square:
            self.row_spacing.setValue(self.plant_spacing.value())
        self.row_label.setText(
            tr("Distanza fra le file (= piante)") if square
            else tr("Distanza fra le file"))

    def _apply_pattern_defaults(self):
        """Called once the widgets exist, so the first scheme is consistent."""
        self._on_pattern_changed()

    def _spin(self, value, minimum, maximum, suffix):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(3)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        return spin

    # -- parameters --------------------------------------------------------

    def _mirror_square(self, *_args):
        if self.pattern.currentData() == grid_mod.PATTERN_SQUARE:
            self.row_spacing.setValue(self.plant_spacing.value())

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
        self.last_schedule = self.schedule(result, self.last_stats)
        band = self._ensure_band()
        if band is not None:
            band.reset(QgsWkbTypes.PointGeometry)
            for plant in result.plants:
                band.addPoint(QgsPointXY(plant.x, plant.y), False)
            band.updatePosition()
            band.show()

        self.kpi.setPlainText("\n".join(
            self.last_schedule
            + [""] + stats_mod.format_report(self.last_stats, result)))
        return result

    def schedule(self, result, stats):
        """The planting schedule, in the operator's units.

        Every number comes from the engine: ``compute_stats`` for the
        densities and the row lengths, the spacing straight off the spec, and
        the elevations from whatever the planner sampled. Nothing is
        recomputed here, and nothing is rounded before it is displayed.
        """
        dx, dy = result.spec.effective_spacing
        lines = [
            tr("PROSPETTO DEL SESTO"),
            tr("Sesto:                     {0}").format(
                grid_mod.PATTERN_LABELS.get(result.spec.pattern,
                                            result.spec.pattern)),
            tr("Distanza piante (fila):    {0:.3f} m").format(dx),
            tr("Distanza fra le file:      {0:.3f} m").format(dy),
            tr("Margine dal bordo:         {0:.3f} m").format(
                result.spec.margin_m),
            tr("Azimut del sesto:          {0:.1f} deg").format(
                result.spec.azimuth_deg),
            tr("Superficie lorda:          {0:,.2f} ha").format(
                stats.aoi_area_ha),
            tr("Superficie utile:          {0:,.2f} ha").format(
                stats.usable_area_ha),
            tr("Piante effettive:          {0:,}").format(stats.n_plants),
            tr("Piante teoriche:           {0:,}").format(
                stats.theoretical_count),
            tr("Riempimento:               {0:.1%}").format(stats.fill_ratio),
            tr("Densita' teorica:          {0:,.1f} piante/ha").format(
                stats.theoretical_density_per_ha),
            tr("Densita' effettiva:        {0:,.1f} piante/ha").format(
                stats.density_per_ha),
            tr("File:                      {0:,}").format(stats.n_rows),
            tr("Lunghezza totale file:     {0:,.1f} m").format(
                stats.row_length_total_m),
        ]

        heights = [p.z for p in result.plants if p.z is not None]
        slopes = [p.slope_deg for p in result.plants
                  if p.slope_deg is not None]
        if heights:
            lines.append(tr("Quota min / max:           {0:.1f} / {1:.1f} m")
                         .format(min(heights), max(heights)))
            lines.append(tr("Dislivello:                {0:.1f} m").format(
                max(heights) - min(heights)))
        if slopes:
            lines.append(
                tr("Pendenza min/media/max:    {0:.1f} / {1:.1f} / {2:.1f} deg")
                .format(min(slopes), sum(slopes) / len(slopes), max(slopes)))
        if result.excluded:
            lines.append(tr("Escluse dai filtri:        {0:,}").format(
                len(result.excluded)))

        # Planimetric spacing, always. The plants sit at the lattice nodes,
        # which are laid out in plan; the DEM supplies the height of each one
        # but does not stretch the lattice along the slope.
        lines.append(tr("Distanza piante (XY):      {0:.3f} m").format(dx))
        if heights and len(heights) == len(result.plants):
            lines.append(tr("Distanza piante (3D):      {0:.3f} m").format(
                self.mean_3d_spacing(result)))
            lines.append(tr(
                "Spaziatura planimetrica; le quote vengono dal DEM."))
        return lines

    @staticmethod
    def mean_3d_spacing(result) -> float:
        """Mean slope distance between consecutive plants of the same row.

        Reported next to the planimetric step so an operator on steep ground
        can see how much rope the scheme really needs. It is a measurement of
        what was planned, never an input: the lattice stays planimetric.
        """
        by_row = {}
        for plant in result.plants:
            by_row.setdefault(plant.row_id, []).append(plant)
        gaps = []
        for plants in by_row.values():
            plants.sort(key=lambda p: p.seq_in_row)
            for first, second in zip(plants, plants[1:]):
                if first.z is None or second.z is None:
                    continue
                gaps.append(math.sqrt((second.x - first.x) ** 2
                                      + (second.y - first.y) ** 2
                                      + (second.z - first.z) ** 2))
        return sum(gaps) / len(gaps) if gaps else 0.0

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
