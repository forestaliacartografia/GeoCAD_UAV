"""
The project, drawn on the map.

Everything the plugin computes used to live in labels and tables: the usable
surface was a number, the cadastral parcels were a status line, the glades
were a count. An operator plans on a map, so this module is the one place
that puts the model onto it.

One service owns the layers. It creates them on demand, keeps them in step
with the model, and takes them away when the plugin unloads -- so the project
never fills up with half a dozen orphaned scratch layers, and a second run
does not stack a second set on top of the first.

The layers are declared in a table rather than written out one by one: name,
geometry type, colour, opacity, draw order. Adding a layer is a row, and the
styling of all of them can be read in one place.

Nothing here computes anything. It asks the model for geometry and draws it.
"""

from __future__ import annotations

from typing import Optional

from qgis.core import (QgsCoordinateReferenceSystem, QgsFeature, QgsField,
                       QgsFields, QgsGeometry, QgsProject, QgsVectorLayer)
from qgis.PyQt.QtGui import QColor

from ..io import layer_factory as lf

#: The layers the plugin draws, in the order they are stacked -- first in the
#: table is drawn on top. Colour is the outline; fill is the same colour at
#: the given opacity.
#:
#: key, title, geometry type, colour, fill opacity, outline width
LAYER_SPEC = (
    ("plants", "Piante", "PointZ", "#2e7d32", 1.0, 0.4),
    ("glades", "Radure", "Polygon", "#8d6e63", 0.25, 0.4),
    ("zones", "Zone", "Polygon", "#6a1b9a", 0.12, 0.6),
    ("parcels", "Particelle catastali", "Polygon", "#1565c0", 0.10, 0.6),
    ("excluded", "Aree escluse", "Polygon", "#c62828", 0.30, 0.4),
    ("usable", "Superficie utile", "Polygon", "#2e7d32", 0.18, 0.6),
    ("area", "Area di progetto", "Polygon", "#37474f", 0.05, 0.9),
)

LAYER_TITLES = {key: title for key, title, *_rest in LAYER_SPEC}

#: Attribute schema per layer. Only what an operator reads in the table.
LAYER_FIELDS = {
    "area": (("nome", "string"), ("superficie_ha", "double")),
    "usable": (("nome", "string"), ("superficie_ha", "double")),
    "excluded": (("motivo", "string"), ("superficie_ha", "double")),
    "parcels": (("comune", "string"), ("belfiore", "string"),
                ("foglio", "string"), ("particella", "string"),
                ("superficie_cat_ha", "double"),
                ("superficie_int_ha", "double"),
                ("percentuale", "double")),
    "zones": (("zona", "string"), ("superficie_ha", "double"),
              ("sesto", "string"), ("densita", "double")),
    "glades": (("radura", "string"), ("raggio_m", "double"),
               ("superficie_ha", "double")),
}

M2_PER_HA = 10_000.0


def _symbol_for(geometry_type: str, colour: str, opacity: float,
                width: float):
    """A plain symbol in the declared colour. No renderer classes here."""
    from qgis.core import (QgsFillSymbol, QgsLineSymbol,        # noqa: PLC0415
                           QgsMarkerSymbol)

    pen = QColor(colour)
    brush = QColor(colour)
    brush.setAlphaF(max(0.0, min(1.0, float(opacity))))
    if geometry_type.startswith("Point"):
        return QgsMarkerSymbol.createSimple({
            "name": "circle", "color": brush.name(QColor.NameFormat.HexArgb),
            "outline_color": pen.name(), "size": "1.6"})
    if geometry_type.startswith("LineString"):
        return QgsLineSymbol.createSimple({
            "color": pen.name(), "width": str(width)})
    return QgsFillSymbol.createSimple({
        "color": brush.name(QColor.NameFormat.HexArgb),
        "outline_color": pen.name(), "outline_width": str(width)})


class ProjectLayers:
    """The plugin's own layers on the map, created and removed as one set."""

    GROUP_NAME = "GeoCad UAV - Rimboschimento"

    def __init__(self, iface=None, crs: Optional[str] = None):
        self.iface = iface
        self.crs_authid = crs or "EPSG:4326"
        self.layers = {}

    # -- the layers --------------------------------------------------------

    def set_crs(self, crs) -> None:
        """Follow the project's CRS. Changing it rebuilds the layers.

        A memory layer cannot change its CRS, and drawing a UTM polygon on a
        layer declared in degrees puts it off the coast of Africa without
        saying anything.
        """
        authid = crs.authid() if hasattr(crs, "authid") else str(crs or "")
        if authid and authid != self.crs_authid:
            self.remove_all()
            self.crs_authid = authid

    def spec_for(self, key: str):
        for row in LAYER_SPEC:
            if row[0] == key:
                return row
        raise KeyError("unknown project layer {0!r}".format(key))

    def layer(self, key: str):
        """The layer for this key, created and added the first time."""
        existing = self.layers.get(key)
        if existing is not None:
            try:
                if existing.isValid():
                    return existing
            except RuntimeError:
                pass                    # deleted under us; make another
        _key, title, geometry_type, colour, opacity, width = self.spec_for(key)
        layer = QgsVectorLayer(
            "{0}?crs={1}".format(geometry_type, self.crs_authid), title,
            "memory")
        if not layer.isValid():
            return None
        fields = LAYER_FIELDS.get(key)
        if fields:
            layer.dataProvider().addAttributes(list(lf.make_fields(fields)))
            layer.updateFields()
        symbol = _symbol_for(geometry_type, colour, opacity, width)
        if symbol is not None:
            layer.renderer().setSymbol(symbol)
        QgsProject.instance().addMapLayer(layer)
        self.layers[key] = layer
        return layer

    def clear(self, key: str) -> None:
        layer = self.layers.get(key)
        if layer is None:
            return
        try:
            layer.dataProvider().truncate()
            layer.updateExtents()
            layer.triggerRepaint()
        except (AttributeError, RuntimeError):
            pass

    def remove(self, key: str) -> None:
        layer = self.layers.pop(key, None)
        if layer is None:
            return
        try:
            QgsProject.instance().removeMapLayer(layer.id())
        except (AttributeError, RuntimeError):
            pass

    def remove_all(self) -> None:
        for key in list(self.layers):
            self.remove(key)

    def adopt(self, key: str, layer) -> None:
        """Take over a layer somebody else created -- the plants layer."""
        previous = self.layers.get(key)
        if previous is not None and previous is not layer:
            self.remove(key)
        self.layers[key] = layer

    # -- drawing -----------------------------------------------------------

    def draw(self, key: str, rows) -> int:
        """Replace a layer's contents. ``rows`` is ``[(geometry, values)]``."""
        layer = self.layer(key)
        if layer is None:
            return 0
        self.clear(key)
        fields = layer.fields()
        features = []
        for geometry, values in rows:
            if geometry is None or geometry.isEmpty():
                continue
            feature = QgsFeature(fields)
            feature.setGeometry(QgsGeometry(geometry))
            for name, value in (values or {}).items():
                index = fields.indexOf(name)
                if index >= 0:
                    feature.setAttribute(index, value)
            features.append(feature)
        if features:
            layer.dataProvider().addFeatures(features)
        layer.updateExtents()
        layer.triggerRepaint()
        return len(features)

    # -- the model, drawn --------------------------------------------------

    def draw_area(self, area) -> int:
        if area is None:
            self.clear("area")
            self.clear("usable")
            self.clear("excluded")
            return 0
        self.draw("area", [(area.lorda(), {
            "nome": area.label or "Area di progetto",
            "superficie_ha": round(area.lorda_ha, 4)})])
        self.draw("usable", [(area.utile(), {
            "nome": "Superficie utile",
            "superficie_ha": round(area.utile_ha, 4)})])
        self.draw("excluded", [
            (exclusion.geometry, {
                "motivo": exclusion.label or exclusion.source or "esclusione",
                "superficie_ha": round(exclusion.area_m2() / M2_PER_HA, 4)})
            for exclusion in area.exclusions])
        return 1

    def draw_parcels(self, result) -> int:
        """The cadastral parcels the project touches, with their numbers."""
        if result is None or not getattr(result, "shares", None):
            self.clear("parcels")
            return 0
        rows = []
        for share in result.shares:
            geometry = getattr(share, "geometry", None)
            if geometry is None:
                continue
            rows.append((geometry, {
                "comune": share.comune_name,
                "belfiore": share.parcel.comune_code,
                "foglio": share.parcel.foglio,
                "particella": share.parcel.particella,
                "superficie_cat_ha": round(share.parcel_area_m2 / M2_PER_HA,
                                           4),
                "superficie_int_ha": round(
                    share.intersection_area_m2 / M2_PER_HA, 4),
                "percentuale": round(share.percent_of_parcel, 3)}))
        return self.draw("parcels", rows)

    def draw_zones(self, zone_set) -> int:
        if zone_set is None or not len(zone_set):
            self.clear("zones")
            return 0
        rows = []
        for zone in zone_set:
            spec = zone.spec
            rows.append((zone.geometry, {
                "zona": zone.name,
                "superficie_ha": round(zone.area_ha, 4),
                "sesto": ("--" if spec is None else
                          "{0:g} x {1:g} m".format(spec.plant_distance_m,
                                                   spec.row_distance_m)),
                "densita": round(zone.density_per_ha(), 1)}))
        return self.draw("zones", rows)

    def draw_glades(self, glades) -> int:
        if not glades:
            self.clear("glades")
            return 0
        return self.draw("glades", [
            (glade.geometry, {"radura": glade.label,
                              "raggio_m": round(glade.radius_m, 3),
                              "superficie_ha": round(glade.area_m2
                                                     / M2_PER_HA, 4)})
            for glade in glades])

    # -- looking at one feature -------------------------------------------

    def select(self, key: str, row: int) -> bool:
        """Select the n-th feature of a layer and show it on the canvas.

        Selection first, because that is what a QGIS user expects a table
        click to do; then a flash, which is what makes a 90 m2 parcel
        findable inside a 12 ha project.
        """
        layer = self.layers.get(key)
        if layer is None:
            return False
        ids = [feature.id() for feature in layer.getFeatures()]
        if row < 0 or row >= len(ids):
            return False
        feature_id = ids[row]
        try:
            layer.selectByIds([feature_id])
        except (AttributeError, RuntimeError):
            return False
        canvas = None
        if self.iface is not None:
            try:
                canvas = self.iface.mapCanvas()
            except AttributeError:
                canvas = None
        if canvas is not None:
            try:
                canvas.flashFeatureIds(layer, [feature_id])
            except (AttributeError, TypeError):
                pass
            try:
                canvas.zoomToFeatureIds(layer, [feature_id])
                canvas.zoomByFactor(4.0)
            except (AttributeError, TypeError):
                pass
        return True

    def zoom_to(self, key: str) -> bool:
        layer = self.layers.get(key)
        if layer is None or self.iface is None:
            return False
        try:
            extent = layer.extent()
            if extent.isEmpty():
                return False
            canvas = self.iface.mapCanvas()
            canvas.setExtent(extent.buffered(max(extent.width(),
                                                 extent.height()) * 0.1))
            canvas.refresh()
        except (AttributeError, RuntimeError):
            return False
        return True

    def refresh_canvas(self) -> None:
        if self.iface is None:
            return
        try:
            self.iface.mapCanvas().refresh()
        except (AttributeError, RuntimeError):
            pass
