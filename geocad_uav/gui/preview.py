"""
The plan shown on the map before it is committed.

``[Genera Anteprima]`` used to compute a plan and put a number in a label.
The operator's question at that moment -- does this scheme land where I want
it, at the density I want, with the species mixed the way I asked -- is a
question about the map, and it was being answered with arithmetic.

Two things go on the canvas, and they are different on purpose:

* a **QgsRubberBand** around the surface the scheme will fill, with the
  bearing of the rows drawn across it. It is a rubber band and not a layer
  because it is furniture: it belongs to the canvas, never to the project,
  it cannot be clicked, exported or saved by accident, and it disappears
  the moment the preview is dropped;

* a **temporary memory layer** carrying the plants, coloured by species with
  the same renderer the committed layer gets. Not rubber bands: a rubber
  band per plant is a scene item per plant, and a plan is routinely several
  thousand of them. The layer is marked as the preview it is, is never the
  one the export writes, and is removed when the plan is committed or the
  preview is cleared.

Nothing here computes a plan. It is handed one and draws it.
"""

from __future__ import annotations

from qgis.core import QgsProject, QgsWkbTypes
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from ..forest.reforestation import spacing as spacing_mod

#: What the preview is called on the map. An operator has to be able to tell
#: it from the committed layer at a glance, in the legend.
PREVIEW_LAYER_NAME = "Anteprima impianto"

#: The outline of the surface the scheme fills.
BAND_COLOR = "#4c9f70"
BAND_WIDTH = 2
BAND_FILL_ALPHA = 28

#: The row-bearing marks drawn across the surface: how many, and their
#: colour. Few, because they are a direction indicator and not a plan.
BEARING_LINES = 7
BEARING_COLOR = "#d8a657"


class PlanPreview:
    """The rubber band and the temporary layer, owned as one thing."""

    def __init__(self, iface=None):
        self.iface = iface
        self.band = None                    # QgsRubberBand
        self.bearing = None                 # QgsRubberBand
        self.layer = None                   # QgsVectorLayer, temporary
        self.count = 0
        #: Whether the bands currently hold anything. The bands themselves
        #: outlive a clear(), so their existence is not the question.
        self.showing = False

    # -- the canvas --------------------------------------------------------

    def canvas(self):
        if self.iface is None:
            return None
        try:
            return self.iface.mapCanvas()
        except (AttributeError, RuntimeError):
            return None

    def _band(self, existing, colour: str, width: int, filled: bool):
        from qgis.core import QgsWkbTypes as _Wkb                # noqa: PLC0415
        from qgis.gui import QgsRubberBand                       # noqa: PLC0415

        canvas = self.canvas()
        if canvas is None:
            return None
        if existing is not None:
            try:
                existing.reset(_Wkb.GeometryType.PolygonGeometry if filled
                               else _Wkb.GeometryType.LineGeometry)
                return existing
            except (AttributeError, RuntimeError):
                pass
        band = QgsRubberBand(canvas,
                             _Wkb.GeometryType.PolygonGeometry if filled
                             else _Wkb.GeometryType.LineGeometry)
        pen = QColor(colour)
        band.setColor(pen)
        band.setWidth(width)
        if filled:
            fill = QColor(colour)
            fill.setAlpha(BAND_FILL_ALPHA)
            band.setFillColor(fill)
        else:
            band.setLineStyle(Qt.PenStyle.DashLine)
        return band

    # -- showing a plan ----------------------------------------------------

    def show(self, result, geometry, crs, azimuth_deg: float = 0.0) -> int:
        """Draw this plan. Returns how many plants went on the canvas.

        Returns 0 without complaint off a canvas: the panels call this on
        every preview, and a headless run is not a failure.
        """
        self.clear()
        if result is None or geometry is None or crs is None:
            return 0
        self.draw_outline(geometry, azimuth_deg)
        self.count = self.draw_plants(result, crs)
        self.refresh()
        return self.count

    def draw_outline(self, geometry, azimuth_deg: float = 0.0) -> bool:
        """The surface the scheme fills, and which way its rows will run."""
        canvas = self.canvas()
        if canvas is None or geometry is None or geometry.isEmpty():
            return False
        self.band = self._band(self.band, BAND_COLOR, BAND_WIDTH, True)
        if self.band is None:
            return False
        try:
            self.band.setToGeometry(geometry, None)
            self.showing = True
        except (AttributeError, RuntimeError, TypeError):
            return False
        self.bearing = self._band(self.bearing, BEARING_COLOR, 1, False)
        if self.bearing is not None:
            self._draw_bearing(geometry, azimuth_deg)
        return True

    def _draw_bearing(self, geometry, azimuth_deg: float) -> int:
        """A few lines across the surface, on the bearing the rows take.

        Drawn from the geometry's own box on the compass convention the rest
        of the package uses -- 0 is North, clockwise -- so what the operator
        sees slanting across the parcel is the direction the rows will slant.
        """
        import math                                             # noqa: PLC0415

        from qgis.core import QgsPointXY                        # noqa: PLC0415

        box = geometry.boundingBox()
        if box.isEmpty():
            return 0
        radians = math.radians(float(azimuth_deg))
        ux, uy = math.sin(radians), math.cos(radians)           # along a row
        px, py = -uy, ux                                        # across them
        cx, cy = box.center().x(), box.center().y()
        reach = max(box.width(), box.height())
        drawn = 0
        for index in range(BEARING_LINES):
            offset = (index - (BEARING_LINES - 1) / 2.0) * reach / BEARING_LINES
            mx, my = cx + px * offset, cy + py * offset
            start = QgsPointXY(mx - ux * reach, my - uy * reach)
            end = QgsPointXY(mx + ux * reach, my + uy * reach)
            try:
                self.bearing.addPoint(start, False)
                self.bearing.addPoint(end, index == BEARING_LINES - 1)
                drawn += 1
            except (AttributeError, RuntimeError):
                break
        return drawn

    def draw_plants(self, result, crs) -> int:
        """The plants, on a temporary layer, coloured by species."""
        if result is None or not getattr(result, "plants", None):
            return 0
        try:
            layer = spacing_mod.plants_layer(
                result, crs.authid() if crs else "",
                name=PREVIEW_LAYER_NAME, zone="Anteprima")
        except Exception:                                       # noqa: BLE001
            return 0
        # Not in the layer tree's saved state: a preview that survived a
        # project save would be a second plan on disk.
        try:
            layer.setCustomProperty("geocad/preview", True)
        except (AttributeError, RuntimeError):
            pass
        QgsProject.instance().addMapLayer(layer)
        self.layer = layer
        return layer.featureCount()

    # -- taking it away ----------------------------------------------------

    def clear(self) -> int:
        """Empty the preview. The bands stay, emptied; the layer goes.

        Emptied and not destroyed: a QgsRubberBand is a graphics item the
        canvas scene owns, and taking one out of the scene on every preview
        -- then letting Python collect it -- is a double ownership that
        brings the process down later, somewhere else, with no traceback.
        Created once, reset as often as you like, removed only at unload.
        """
        removed = self.count
        self._reset_bands()
        if self.layer is not None:
            try:
                QgsProject.instance().removeMapLayer(self.layer.id())
            except (AttributeError, RuntimeError):
                pass
            self.layer = None
        self.count = 0
        self.showing = False
        self.refresh()
        return removed

    def _reset_bands(self) -> None:
        for name, filled in (("band", True), ("bearing", False)):
            band = getattr(self, name)
            if band is None:
                continue
            try:
                band.reset(QgsWkbTypes.GeometryType.PolygonGeometry if filled
                           else QgsWkbTypes.GeometryType.LineGeometry)
            except (AttributeError, RuntimeError):
                pass

    def dispose(self) -> None:
        """Give the bands back to the scene. Called when the plugin unloads."""
        self.clear()
        canvas = self.canvas()
        for name in ("band", "bearing"):
            band = getattr(self, name)
            if band is not None and canvas is not None:
                try:
                    canvas.scene().removeItem(band)
                except (AttributeError, RuntimeError):
                    pass
            setattr(self, name, None)

    def is_showing(self) -> bool:
        return bool(self.layer is not None or self.showing)

    def refresh(self) -> None:
        canvas = self.canvas()
        if canvas is None:
            return
        try:
            canvas.refresh()
        except (AttributeError, RuntimeError):
            pass


__all__ = ["PlanPreview", "PREVIEW_LAYER_NAME", "BEARING_LINES"]
