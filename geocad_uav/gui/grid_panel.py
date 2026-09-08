"""
Grid tab: define an extent, then lay a lattice inside it.

Two ways to get an extent, one generator. The lattice itself is
``core.grid.generate_grid`` untouched; this panel only decides *where* and
clips the result with the same predicate the Processing algorithm uses --
``intersects``, not ``contains``, because ``contains`` is strictly interior and
silently drops every node sitting on the boundary (on a 100 x 100 m extent at
5 m that turns 441 nodes into 361).

Drawing reuses the existing CAD map tools. There is no third digitizer here:
:class:`ExtentSource` instantiates ``RectangleTool`` / ``PolylineTool`` /
``LineTool`` from ``cad.tools`` and reads back what they commit.

:class:`ExtentSource` is shared with ``forest_panel``; it lives here so the two
tabs cannot drift apart.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from qgis.core import (Qgis, QgsGeometry, QgsMapLayerProxyModel, QgsPoint,
                       QgsPointXY, QgsProject, QgsWkbTypes)
from qgis.gui import QgsMapLayerComboBox, QgsRubberBand
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                                 QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                                 QMessageBox, QPushButton, QVBoxLayout,
                                 QWidget)

from ..cad import tools as cad_tools
from ..core import crs as crs_svc
from ..core import grid as grid_mod
from ..core import undo
from ..core.errors import GeoCadError
from ..io import layer_factory as lf


def tr(text):
    return QCoreApplication.translate("GeoCadUav", text)


# --------------------------------------------------------------------------
# Extent source, shared by the Grid and Forest tabs
# --------------------------------------------------------------------------

class ExtentSource(QGroupBox):
    """Pick an existing polygon, or draw one now with the existing CAD tools.

    Never forces a redraw: if the operator already has the polygon in a layer,
    the combo takes it as it is.
    """

    #: Scratch layer names, so repeated drawing does not litter the project.
    DRAW_LAYER = "GeoCad estensione"
    MEASURE_LAYER = "GeoCad misura"

    def __init__(self, iface, parent=None):
        super().__init__(tr("Estensione"), parent)
        self.iface = iface
        self._geometry = None
        self._crs = None
        self._listeners = []
        self._draw_layer = None
        self._measure_layer = None
        self._draw_tool = None
        self._previous_tool = None
        self._measure_callback = None
        self._connections = []
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self):
        form = QFormLayout(self)
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.layer_combo.setAllowEmptyLayer(True, tr("(nessun layer)"))
        form.addRow(tr("Layer poligonale"), self.layer_combo)

        self.selected_only = QCheckBox(tr("Solo le feature selezionate"))
        form.addRow(self.selected_only)

        buttons = QHBoxLayout()
        self.draw_rect_button = QPushButton(tr("Disegna rettangolo"))
        self.draw_poly_button = QPushButton(tr("Disegna poligono"))
        buttons.addWidget(self.draw_rect_button)
        buttons.addWidget(self.draw_poly_button)
        form.addRow(buttons)

        self.summary = QLabel(tr("Nessuna estensione."))
        self.summary.setWordWrap(True)
        form.addRow(self.summary)

        self.layer_combo.layerChanged.connect(self._from_layer)
        self._connections.append((self.layer_combo.layerChanged,
                                  self._from_layer))
        self.selected_only.toggled.connect(self._from_layer)
        self._connections.append((self.selected_only.toggled, self._from_layer))
        self.draw_rect_button.clicked.connect(
            lambda: self.start_drawing("rectangle"))
        self.draw_poly_button.clicked.connect(
            lambda: self.start_drawing("polyline"))

        # The combo pre-selects the first matching layer on its own, and that
        # assignment emits nothing: without this the panel would claim "no
        # extent" while showing a layer.
        self._from_layer()

    # -- listeners ---------------------------------------------------------

    def on_change(self, callback):
        self._listeners.append(callback)

    def _notify(self):
        for callback in self._listeners:
            try:
                callback()
            except GeoCadError:
                pass

    # -- extent ------------------------------------------------------------

    def geometry(self) -> Optional[QgsGeometry]:
        return self._geometry

    def crs(self):
        return self._crs

    def set_extent(self, geometry, crs) -> None:
        """Adopt an extent. The drawing callbacks and the tests both use this."""
        if geometry is None or geometry.isEmpty():
            self._geometry = None
            self._crs = None
            self.summary.setText(tr("Nessuna estensione."))
        else:
            if not geometry.isGeosValid():
                geometry = geometry.makeValid()
            self._geometry = geometry
            self._crs = crs
            self.summary.setText(tr(
                "Estensione: {0:,.0f} m2 ({1:.3f} ha), CRS {2}").format(
                    geometry.area(), geometry.area() / 10_000.0,
                    crs.authid() if crs else "?"))
        self._notify()

    def _from_layer(self, *_args):
        layer = self.layer_combo.currentLayer()
        if layer is None:
            self.set_extent(None, None)
            return
        features = (list(layer.selectedFeatures())
                    if self.selected_only.isChecked()
                    else list(layer.getFeatures()))
        geometries = [f.geometry() for f in features
                      if f.hasGeometry() and not f.geometry().isEmpty()]
        if not geometries:
            self.set_extent(None, None)
            return
        merged = (geometries[0] if len(geometries) == 1
                  else QgsGeometry.unaryUnion(geometries))
        self.set_extent(merged, layer.crs())

    # -- drawing, using the existing CAD tools -----------------------------

    def _scratch_layer(self, name, geometry_type, crs):
        project = QgsProject.instance()
        for layer in project.mapLayersByName(name):
            try:
                if layer.isValid():
                    return layer
            except RuntimeError:
                continue
        layer = lf.memory_layer(geometry_type, name, crs.authid(),
                               [("id", "int")])
        project.addMapLayer(layer)
        return layer

    def start_drawing(self, tool_key):
        """Hand control to an existing CAD tool and take back what it commits.

        ``polyline`` is asked to close its ring, and the closed LineString is
        converted to a polygon *for the extent only* -- the extent is an area
        of interest, not a stored CAD feature, so no new primitive is involved.
        """
        canvas = self.iface.mapCanvas() if self.iface else None
        if canvas is None:
            return None
        crs = canvas.mapSettings().destinationCrs()
        geometry_type = "Polygon" if tool_key == "rectangle" else "LineString"
        layer = self._scratch_layer(self.DRAW_LAYER + " " + geometry_type,
                                    geometry_type, crs)
        self._draw_layer = layer

        options = {"close": True} if tool_key == "polyline" else {}
        tool = cad_tools.create_tool(tool_key, canvas, iface=self.iface,
                                     layer_provider=lambda: layer, **options)
        self._previous_tool = canvas.mapTool()
        self._draw_tool = tool
        try:
            layer.featureAdded.connect(self._on_drawn)
            self._connections.append((layer.featureAdded, self._on_drawn))
        except (AttributeError, TypeError):
            pass
        canvas.setMapTool(tool)
        return tool

    def _on_drawn(self, feature_id):
        layer = self._draw_layer
        if layer is None:
            return
        try:
            feature = layer.getFeature(feature_id)
        except RuntimeError:
            return
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            return
        self.set_extent(self.as_polygon(geometry), layer.crs())
        self._stop_drawing()

    @staticmethod
    def as_polygon(geometry):
        """A closed ring drawn as a LineString becomes the extent polygon."""
        if geometry is None or geometry.isEmpty():
            return None
        if QgsWkbTypes.geometryType(geometry.wkbType()) == \
                QgsWkbTypes.PolygonGeometry:
            return geometry
        line = geometry.asPolyline()
        if line and len(line) >= 4:
            points = list(line)
            if points[0] != points[-1]:
                points.append(points[0])
            return QgsGeometry.fromPolygonXY([points])
        return None

    def _stop_drawing(self):
        canvas = self.iface.mapCanvas() if self.iface else None
        if self._draw_layer is not None:
            try:
                self._draw_layer.featureAdded.disconnect(self._on_drawn)
            except (TypeError, RuntimeError):
                pass
        if canvas is not None and self._draw_tool is not None:
            try:
                canvas.unsetMapTool(self._draw_tool)
                if self._previous_tool is not None:
                    canvas.setMapTool(self._previous_tool)
            except RuntimeError:
                pass
        self._draw_tool = None
        self._previous_tool = None

    # -- measuring with the existing LineTool ------------------------------

    def start_measure(self, callback):
        """Draw one segment; ``callback(length_m, azimuth_deg)`` receives it.

        Backs both "step from two clicks" and "azimuth from two clicks"
        without adding a map tool: it is the existing LineTool.
        """
        canvas = self.iface.mapCanvas() if self.iface else None
        if canvas is None:
            return None
        crs = canvas.mapSettings().destinationCrs()
        layer = self._scratch_layer(self.MEASURE_LAYER, "LineString", crs)
        self._measure_layer = layer
        self._measure_callback = callback
        tool = cad_tools.create_tool("line", canvas, iface=self.iface,
                                     layer_provider=lambda: layer)
        self._previous_tool = canvas.mapTool()
        self._draw_tool = tool
        try:
            layer.featureAdded.connect(self._on_measured)
            self._connections.append((layer.featureAdded, self._on_measured))
        except (AttributeError, TypeError):
            pass
        canvas.setMapTool(tool)
        return tool

    def _on_measured(self, feature_id):
        layer = self._measure_layer
        if layer is None or self._measure_callback is None:
            return
        try:
            geometry = layer.getFeature(feature_id).geometry()
        except RuntimeError:
            return
        line = geometry.asPolyline()
        if not line or len(line) < 2:
            return
        dx = line[-1].x() - line[0].x()
        dy = line[-1].y() - line[0].y()
        length = math.hypot(dx, dy)
        azimuth = math.degrees(math.atan2(dx, dy)) % 360.0
        try:
            layer.featureAdded.disconnect(self._on_measured)
        except (TypeError, RuntimeError):
            pass
        callback = self._measure_callback
        self._measure_callback = None
        self._stop_drawing()
        callback(length, azimuth)

    def teardown(self):
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._connections = []
        self._listeners = []


# --------------------------------------------------------------------------
# Clipping, shared by both tabs
# --------------------------------------------------------------------------

def clip_to_geometry(result, geometry):
    """Keep the lattice nodes that meet ``geometry``.

    ``intersects`` and not ``contains``: the boundary belongs to the extent.
    This is the predicate ``processing/alg_design`` already uses, so a lattice
    built here and one built from the toolbox agree node for node.
    """
    engine = QgsGeometry.createGeometryEngine(geometry.constGet())
    engine.prepareGeometry()
    keep = np.zeros(len(result), dtype=bool)
    for index in range(len(result)):
        keep[index] = engine.intersects(
            QgsPoint(float(result.xy[index, 0]), float(result.xy[index, 1])))
    return grid_mod.renumber(grid_mod.filter_result(result, keep))


def erode(geometry, margin_m):
    """The extent minus the edge margin, or None when the margin eats it."""
    if margin_m <= 0:
        return geometry
    working = geometry.buffer(-float(margin_m), 12)
    if working is None or working.isEmpty():
        return None
    return working


# --------------------------------------------------------------------------
# Grid tab
# --------------------------------------------------------------------------

class GridPanel(QWidget):
    """Lattice parameters, live preview, one write."""

    PATTERN_KEYS = (grid_mod.PATTERN_RECT, grid_mod.PATTERN_SQUARE,
                    grid_mod.PATTERN_QUINCUNX, grid_mod.PATTERN_HEX)

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._band = None
        self._connections = []
        self.last_result = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.extent = ExtentSource(self.iface)
        self.extent.on_change(self.refresh_preview)
        layout.addWidget(self.extent)

        box = QGroupBox(tr("Reticolo"))
        form = QFormLayout(box)
        self.spacing_x = self._spin(5.0, 0.001, 100000.0, " m")
        form.addRow(tr("Passo dx"), self.spacing_x)
        self.spacing_y = self._spin(5.0, 0.001, 100000.0, " m")
        form.addRow(tr("Passo dy"), self.spacing_y)

        self.step_from_map = QPushButton(tr("Passo da due click"))
        form.addRow(self.step_from_map)

        self.pattern = QComboBox()
        for key in self.PATTERN_KEYS:
            self.pattern.addItem(grid_mod.PATTERN_LABELS[key], key)
        form.addRow(tr("Schema"), self.pattern)

        self.azimuth = self._spin(0.0, 0.0, 360.0, " deg")
        form.addRow(tr("Azimut file"), self.azimuth)
        azimuth_buttons = QHBoxLayout()
        self.azimuth_from_map = QPushButton(tr("Da due click"))
        self.azimuth_from_edge = QPushButton(tr("Parallelo a un lato"))
        azimuth_buttons.addWidget(self.azimuth_from_map)
        azimuth_buttons.addWidget(self.azimuth_from_edge)
        form.addRow(azimuth_buttons)

        self.margin = self._spin(0.0, 0.0, 10000.0, " m")
        form.addRow(tr("Margine dal bordo"), self.margin)
        self.serpentine = QCheckBox(tr("Numerazione a serpentina"))
        form.addRow(self.serpentine)
        layout.addWidget(box)

        self.summary = QLabel(tr("Nessuna anteprima."))
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        buttons = QHBoxLayout()
        self.preview_button = QPushButton(tr("Anteprima"))
        self.confirm_button = QPushButton(tr("Crea layer"))
        buttons.addWidget(self.preview_button)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)

        for widget, signal_name in (
                (self.spacing_x, "valueChanged"),
                (self.spacing_y, "valueChanged"),
                (self.pattern, "currentIndexChanged"),
                (self.azimuth, "valueChanged"),
                (self.margin, "valueChanged"),
                (self.serpentine, "toggled")):
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
            spacing_x=self.spacing_x.value(),
            spacing_y=self.spacing_y.value(),
            azimuth_deg=self.azimuth.value(),
            pattern=self.pattern.currentData(),
            margin_m=self.margin.value(),
            serpentine=self.serpentine.isChecked())

    def _pick_step(self):
        """Two clicks give dx. The spin box stays master: typing 3 wins."""
        def apply_step(length, _azimuth):
            self.spacing_x.setValue(length)
            if self.pattern.currentData() == grid_mod.PATTERN_SQUARE:
                self.spacing_y.setValue(length)
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
        """The clipped lattice for the current extent, or None."""
        geometry = self.extent.geometry()
        if geometry is None:
            return None
        crs = self.extent.crs()
        if crs is not None and crs_svc.is_geographic(crs):
            raise GeoCadError(
                "geographic CRS for a metric lattice",
                user_message="Il CRS dell'estensione ({0}) e' geografico: i "
                             "passi verrebbero letti in gradi.".format(
                                 crs.authid()),
                hint="Riproietta l'estensione in un CRS metrico (UTM).")
        spec = self.build_spec()
        working = erode(geometry, spec.margin_m)
        if working is None:
            raise GeoCadError(
                "margin erodes the extent",
                user_message="Il margine di {0:g} m elimina completamente "
                             "l'estensione.".format(spec.margin_m))
        box = working.boundingBox()
        result = grid_mod.generate_grid(
            (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()),
            spec)
        return clip_to_geometry(result, working)

    # -- preview -----------------------------------------------------------

    def _ensure_band(self):
        canvas = self.iface.mapCanvas() if self.iface else None
        if canvas is None:
            return None
        if self._band is None:
            self._band = QgsRubberBand(canvas, QgsWkbTypes.PointGeometry)
            self._band.setColor(QColor(20, 120, 200, 200))
            self._band.setIcon(QgsRubberBand.ICON_CIRCLE)
            self._band.setIconSize(4)
            self._band.setWidth(1)
        return self._band

    def refresh_preview(self, *_args):
        """Draw the nodes. Writes nothing and never refreshes the canvas."""
        try:
            result = self.compute()
        except GeoCadError as exc:
            self.summary.setText(exc.formatted())
            self.clear_preview()
            self.last_result = None
            return None
        self.last_result = result
        if result is None:
            self.summary.setText(tr("Nessuna estensione."))
            self.clear_preview()
            return None

        band = self._ensure_band()
        if band is not None:
            band.reset(QgsWkbTypes.PointGeometry)
            for index in range(len(result)):
                band.addPoint(QgsPointXY(float(result.xy[index, 0]),
                                         float(result.xy[index, 1])), False)
            band.updatePosition()
            band.show()

        dx, dy = result.spec.effective_spacing
        self.summary.setText(tr(
            "{0:,} punti, {1} file, passo {2:g} x {3:g} m").format(
                len(result), result.n_rows, dx, dy))
        return result

    def clear_preview(self):
        if self._band is not None:
            self._band.reset(QgsWkbTypes.PointGeometry)
            self._band.hide()

    # -- commit ------------------------------------------------------------

    def confirm(self, *_args):
        """Write the lattice once, inside a single undo command."""
        from qgis.core import QgsFeature                        # noqa: PLC0415

        result = self.last_result if self.last_result is not None \
            else self.refresh_preview()
        if result is None or len(result) == 0:
            return None
        if undo.needs_confirmation(len(result)) and not self._ask(len(result)):
            return None

        crs = self.extent.crs()
        layer = lf.memory_layer(
            "Point", "GeoCad griglia", crs.authid() if crs else "EPSG:4326",
            [("id", "int"), ("row_id", "int"), ("seq_in_row", "int"),
             ("x", "double"), ("y", "double")])
        fields = layer.fields()
        features = []
        for index in range(len(result)):
            feature = QgsFeature(fields)
            x = float(result.xy[index, 0])
            y = float(result.xy[index, 1])
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
            feature.setAttributes([index + 1, int(result.row[index]) + 1,
                                   int(result.col[index]) + 1, x, y])
            features.append(feature)

        undo.add_features(layer, features, "GeoCad: Griglia")
        QgsProject.instance().addMapLayer(layer)
        self.clear_preview()
        self._notify_user(tr("Creato il layer '{0}' con {1:,} punti.").format(
            layer.name(), layer.featureCount()))
        return layer

    def _ask(self, count):
        answer = QMessageBox.question(
            self, tr("GeoCad UAV"),
            undo.confirmation_message(count, tr("griglia")),
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
