"""
Where the work happens: an existing polygon, or one drawn now.

Lifted verbatim out of ``gui.grid_panel`` when the Grid tab was withdrawn in
v1.4.5. The class itself is unchanged -- the Forest and UAV tabs were already
sharing it, and moving it here is what let the tab that first hosted it go
away without taking the picker with it.

There is exactly one of these in the product. A second AOI selector would let
two tabs disagree about what "the area" means.
"""

from __future__ import annotations

import math
from typing import Optional

from qgis.core import QgsGeometry, QgsMapLayerProxyModel, QgsProject, QgsWkbTypes
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtWidgets import (QCheckBox, QFormLayout, QGroupBox,
                                 QHBoxLayout, QLabel, QPushButton)

from ..cad import tools as cad_tools
from ..core.errors import GeoCadError
from ..io import layer_factory as lf


def tr(text):
    return QCoreApplication.translate("GeoCadUav", text)


def plugin_geometry_for(tool_key: str) -> str:
    """Which scratch layer a CAD tool needs, per the plugin's own table.

    The mapping already exists in ``plugin.TOOL_GEOMETRY``; reading it here
    means the extent picker cannot drift away from what the tool actually
    writes.
    """
    from ..plugin import TOOL_GEOMETRY                          # noqa: PLC0415

    return TOOL_GEOMETRY.get(tool_key, "Polygon")


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
        #: Where the extent on hand came from: the layer combo, or anywhere
        #: else (drawn, handed in, restored). Only the combo's own extents
        #: are the combo's to replace with nothing.
        self._source = ""
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
            lambda: self.start_drawing("digitize"))

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

    def set_extent(self, geometry, crs, source: str = "explicit") -> None:
        """Adopt an extent. The drawing callbacks and the tests both use this."""
        self._source = source if geometry is not None else ""
        if geometry is None or geometry.isEmpty():
            self._geometry = None
            self._crs = None
            self.summary.setText(tr("Nessuna estensione."))
        else:
            if not geometry.isGeosValid():
                geometry = geometry.makeValid()
            self._geometry = geometry
            self._crs = crs
            self.summary.setText(self.describe(geometry, crs))
        self._notify()

    @staticmethod
    def describe(geometry, crs) -> str:
        """What was picked, in the units that kind of thing is measured in.

        A line has no area, and reporting 0 m2 for a road centreline reads
        as "nothing selected" rather than "a 2 km axis".
        """
        authid = crs.authid() if crs else "?"
        if QgsWkbTypes.geometryType(geometry.wkbType()) == \
                QgsWkbTypes.LineGeometry:
            return tr("Asse: {0:,.0f} m di sviluppo, CRS {1}").format(
                geometry.length(), authid)
        return tr("Estensione: {0:,.0f} m2 ({1:.3f} ha), CRS {2}").format(
            geometry.area(), geometry.area() / 10_000.0, authid)

    def is_line(self) -> bool:
        """True when what is held is a linear feature, not a surface."""
        if self._geometry is None:
            return False
        return QgsWkbTypes.geometryType(self._geometry.wkbType()) == \
            QgsWkbTypes.LineGeometry

    def _from_layer(self, *_args):
        layer = self.layer_combo.currentLayer()
        if layer is None:
            # Adding any polygon layer to the project repopulates this combo
            # and fires layerChanged with nothing chosen. An extent that came
            # from somewhere else -- drawn on the canvas, handed in by the
            # forest module -- is not this combo's to throw away, and losing
            # it silently is how "Nessuna area definita" appeared about an
            # area that was on screen.
            if self._source != "layer" and self._geometry is not None:
                return
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
        self.set_extent(merged, layer.crs(), source="layer")

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

        v1.7.0: both keys an operator can press here -- the rectangle and the
        polygon digitizer -- commit a real polygon, so the extent arrives as
        an area and nothing has to be converted. ``as_polygon`` still repairs
        a closed ring, because a project saved before this version can hand
        one back.
        """
        canvas = self.iface.mapCanvas() if self.iface else None
        if canvas is None:
            return None
        crs = canvas.mapSettings().destinationCrs()
        geometry_type = plugin_geometry_for(tool_key)
        layer = self._scratch_layer(self.DRAW_LAYER + " " + geometry_type,
                                    geometry_type, crs)
        self._draw_layer = layer

        tool = cad_tools.create_tool(tool_key, canvas, iface=self.iface,
                                     layer_provider=lambda: layer)
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
        # v1.7.0: the line tool is no longer a CAD primitive on the toolbar,
        # but it is still the cheapest way to measure one segment on the map.
        # Imported directly rather than through the registry, which now lists
        # only what an operator may draw.
        from ..cad.tools import line as line_tool               # noqa: PLC0415

        tool = line_tool.create(canvas, iface=self.iface,
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
