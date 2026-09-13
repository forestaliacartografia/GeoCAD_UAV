"""
The printed map: a real QGIS layout, with everything a drawing needs.

A reforestation project is delivered on paper. Not a screenshot of the
canvas: a sheet with a title, a scale that can be measured with a ruler, a
legend that names what the colours mean, a north arrow, and a note saying
which coordinate system the coordinates are in. QGIS has all of that in
``QgsPrintLayout``, so nothing here draws anything -- it composes the layout
and hands it to ``QgsLayoutExporter``.

The sheet is described as a table of fractions of the page rather than a list
of millimetre coordinates, so the same composition holds on A4 and on A3, in
portrait and in landscape, without a second set of numbers to keep in step.

Two things this module refuses to do. It will not compose a map of nothing:
a layout with no layers and no extent is a blank sheet with a title, and
handing that to an operator as "the cartography" would be worse than saying
no. And it will not invent a scale: asked for a fixed one, it sets exactly
that and lets the frame show what fits; asked for none, it fits the project
and reports the scale that came out.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from ..core.errors import ExportError, GeoCadError, InvalidInputError

#: Page sizes, as QGIS names them. The key is what the panel stores.
PAGE_SIZES = (
    ("A4", "A4 (210 x 297 mm)"),
    ("A3", "A3 (297 x 420 mm)"),
    ("A2", "A2 (420 x 594 mm)"),
    ("A1", "A1 (594 x 841 mm)"),
    ("A0", "A0 (841 x 1189 mm)"),
)

ORIENTATION_LANDSCAPE = "landscape"
ORIENTATION_PORTRAIT = "portrait"

ORIENTATIONS = (
    (ORIENTATION_LANDSCAPE, "Orizzontale"),
    (ORIENTATION_PORTRAIT, "Verticale"),
)

#: Item ids. The panel and the tests look items up by these, never by index.
ITEM_MAP = "geocad_map"
ITEM_TITLE = "geocad_title"
ITEM_SUBTITLE = "geocad_subtitle"
ITEM_LEGEND = "geocad_legend"
ITEM_SCALEBAR = "geocad_scalebar"
ITEM_NORTH = "geocad_north"
ITEM_CREDITS = "geocad_credits"

#: The sheet, as fractions of the printable page: (x, y, width, height).
#: One table, so A4 portrait and A0 landscape are the same drawing.
FRAME = {
    ITEM_TITLE: (0.04, 0.03, 0.92, 0.06),
    ITEM_SUBTITLE: (0.04, 0.09, 0.92, 0.04),
    ITEM_MAP: (0.04, 0.15, 0.70, 0.72),
    ITEM_LEGEND: (0.76, 0.15, 0.20, 0.50),
    ITEM_NORTH: (0.76, 0.68, 0.08, 0.08),
    ITEM_SCALEBAR: (0.04, 0.89, 0.40, 0.05),
    ITEM_CREDITS: (0.46, 0.89, 0.50, 0.07),
}

#: Margin left around the project inside the map frame, as a fraction of the
#: bigger side. A drawing whose subject touches the neat line looks cut.
EXTENT_MARGIN = 0.06

DEFAULT_DPI = 300

#: Where QGIS keeps its own north arrows. Preferred in this order; the first
#: one that exists is used, and if none do the arrow is left out and said so.
NORTH_ARROW_CANDIDATES = ("arrows/NorthArrow_02.svg", "arrows/NorthArrow_01.svg",
                          "arrows/NorthArrow_03.svg")


@dataclass
class LayoutSpec:
    """What the sheet says and how big it is."""

    title: str = ""
    subtitle: str = ""
    author: str = ""
    page: str = "A3"
    orientation: str = ORIENTATION_LANDSCAPE
    #: 0 means "fit the project and report what came out".
    scale: float = 0.0
    dpi: int = DEFAULT_DPI
    legend: bool = True
    scalebar: bool = True
    north: bool = True
    grid_interval_m: float = 0.0
    warnings: list = field(default_factory=list)

    def page_label(self) -> str:
        for key, label in PAGE_SIZES:
            if key == self.page:
                return label
        return self.page


def north_arrow_path() -> Optional[str]:
    """The path of a north arrow QGIS ships, or None if it ships none."""
    from qgis.core import QgsApplication                        # noqa: PLC0415

    for base in QgsApplication.svgPaths():
        for candidate in NORTH_ARROW_CANDIDATES:
            path = os.path.join(base, *candidate.split("/"))
            if os.path.isfile(path):
                return path
    return None


def _unit():
    from qgis.core import QgsUnitTypes                          # noqa: PLC0415

    return QgsUnitTypes.LayoutUnit.LayoutMillimeters


def _place(item, page_size, fraction) -> None:
    """Put an item where the table says, in millimetres on this page."""
    from qgis.core import QgsLayoutPoint, QgsLayoutSize         # noqa: PLC0415

    width, height = page_size
    x, y, w, h = fraction
    unit = _unit()
    item.attemptMove(QgsLayoutPoint(x * width, y * height, unit))
    item.attemptResize(QgsLayoutSize(w * width, h * height, unit))


def _page_size(layout):
    page = layout.pageCollection().page(0)
    size = page.pageSize()
    return float(size.width()), float(size.height())


def project_extent(geometries, margin: float = EXTENT_MARGIN):
    """The rectangle the drawing has to show, with air around it."""
    from qgis.core import QgsRectangle                          # noqa: PLC0415

    box = None
    for geometry in geometries:
        if geometry is None or geometry.isEmpty():
            continue
        rectangle = geometry.boundingBox()
        if box is None:
            box = QgsRectangle(rectangle)
        else:
            box.combineExtentWith(rectangle)
    if box is None or box.isEmpty():
        return None
    grow = max(box.width(), box.height()) * float(margin)
    box.grow(grow)
    return box


def layers_extent(layers, margin: float = EXTENT_MARGIN):
    """The rectangle covering every layer given, with air around it.

    Layer extents rather than geometries, because that is what the drawing
    has to frame: a plants layer knows where its plants are and asking it is
    cheaper and truer than unioning two thousand points.
    """
    from qgis.core import QgsRectangle                          # noqa: PLC0415

    box = None
    for layer in layers:
        if layer is None:
            continue
        try:
            rectangle = layer.extent()
        except (AttributeError, RuntimeError):
            continue
        if rectangle is None or rectangle.isEmpty():
            continue
        if box is None:
            box = QgsRectangle(rectangle)
        else:
            box.combineExtentWith(rectangle)
    if box is None or box.isEmpty():
        return None
    box.grow(max(box.width(), box.height()) * float(margin))
    return box


def build_layout(project, spec: LayoutSpec, layers, extent, name: str,
                 crs=None):
    """Compose the sheet. Returns the ``QgsPrintLayout``, not yet registered.

    ``layers`` are drawn, in the order given, in the map frame and in the
    legend: the legend is pinned to exactly those, because a legend that
    follows the QGIS layer tree would list the operator's basemap, their
    orthophoto and every scratch layer they happen to have open.
    """
    from qgis.core import (QgsLayerTreeLayer,                   # noqa: PLC0415
                           QgsLayoutItemLabel, QgsLayoutItemLegend,
                           QgsLayoutItemMap, QgsLayoutItemPage,
                           QgsLayoutItemPicture, QgsLayoutItemScaleBar,
                           QgsPrintLayout)

    drawn = [layer for layer in layers if layer is not None]
    if not drawn:
        raise InvalidInputError(
            "no layers to compose a map from",
            user_message="Nessun elaborato da cartografare: genera prima "
                         "l'impianto o definisci l'area.")
    if extent is None or extent.isEmpty():
        raise InvalidInputError(
            "no extent to compose a map from",
            user_message="Il progetto non ha estensione: definisci prima "
                         "l'area di intervento.")

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(name)
    orientation = (QgsLayoutItemPage.Orientation.Landscape
                   if spec.orientation == ORIENTATION_LANDSCAPE
                   else QgsLayoutItemPage.Orientation.Portrait)
    layout.pageCollection().page(0).setPageSize(spec.page, orientation)
    size = _page_size(layout)

    item_map = QgsLayoutItemMap(layout)
    item_map.setId(ITEM_MAP)
    item_map.setFrameEnabled(True)
    layout.addLayoutItem(item_map)
    _place(item_map, size, FRAME[ITEM_MAP])
    item_map.setLayers(drawn)
    # The frame has to be in the project's own CRS, not in whatever the QGIS
    # project happens to be set to. A UTM extent handed to a frame declared
    # in degrees comes out at 1:213,000,000 -- a sheet showing the planet
    # with the parcel as one pixel, and no error anywhere.
    if crs is not None and crs.isValid():
        item_map.setCrs(crs)
    item_map.zoomToExtent(extent)
    if spec.scale and spec.scale > 0.0:
        item_map.setScale(float(spec.scale))
    if spec.grid_interval_m and spec.grid_interval_m > 0.0:
        grid = item_map.grid()
        grid.setEnabled(True)
        grid.setIntervalX(float(spec.grid_interval_m))
        grid.setIntervalY(float(spec.grid_interval_m))

    title = QgsLayoutItemLabel(layout)
    title.setId(ITEM_TITLE)
    title.setText(spec.title or name)
    _bigger(title, 1.8)
    layout.addLayoutItem(title)
    _place(title, size, FRAME[ITEM_TITLE])

    subtitle = QgsLayoutItemLabel(layout)
    subtitle.setId(ITEM_SUBTITLE)
    subtitle.setText(spec.subtitle)
    layout.addLayoutItem(subtitle)
    _place(subtitle, size, FRAME[ITEM_SUBTITLE])

    if spec.legend:
        legend = QgsLayoutItemLegend(layout)
        legend.setId(ITEM_LEGEND)
        legend.setTitle("Legenda")
        legend.setLinkedMap(item_map)
        legend.setAutoUpdateModel(False)
        root = legend.model().rootGroup()
        wanted = {layer.id() for layer in drawn}
        for node in list(root.children()):
            if (isinstance(node, QgsLayerTreeLayer)
                    and node.layerId() not in wanted):
                root.removeChildNode(node)
        legend.setFrameEnabled(True)
        layout.addLayoutItem(legend)
        _place(legend, size, FRAME[ITEM_LEGEND])

    if spec.scalebar:
        bar = QgsLayoutItemScaleBar(layout)
        bar.setId(ITEM_SCALEBAR)
        bar.setStyle("Single Box")
        bar.setLinkedMap(item_map)
        bar.applyDefaultSize()
        layout.addLayoutItem(bar)
        _place(bar, size, FRAME[ITEM_SCALEBAR])

    if spec.north:
        path = north_arrow_path()
        if path is None:
            spec.warnings.append(
                "QGIS non fornisce una freccia del nord: simbolo omesso.")
        else:
            arrow = QgsLayoutItemPicture(layout)
            arrow.setId(ITEM_NORTH)
            arrow.setPicturePath(path)
            layout.addLayoutItem(arrow)
            _place(arrow, size, FRAME[ITEM_NORTH])

    credits = QgsLayoutItemLabel(layout)
    credits.setId(ITEM_CREDITS)
    credits.setText(credits_text(spec, item_map, project))
    layout.addLayoutItem(credits)
    _place(credits, size, FRAME[ITEM_CREDITS])
    return layout


def credits_text(spec: LayoutSpec, item_map, project) -> str:
    """The note under the sheet: scale, CRS, author. Read off the layout."""
    crs = ""
    try:
        crs = item_map.crs().authid()
    except (AttributeError, RuntimeError):
        crs = ""
    if not crs:
        try:
            crs = project.crs().authid()
        except (AttributeError, RuntimeError):
            crs = ""
    parts = ["Scala 1:{0:,.0f}".format(item_map.scale()).replace(",", "."),
             "Sistema di riferimento: {0}".format(crs or "non definito"),
             "Formato {0}, {1}".format(
                 spec.page, dict(ORIENTATIONS).get(spec.orientation,
                                                   spec.orientation).lower())]
    if spec.author:
        parts.append(spec.author)
    return "\n".join(parts)


def _bigger(label, factor: float) -> None:
    """Scale a label's type without knowing which font API this QGIS has."""
    try:
        text_format = label.textFormat()
        text_format.setSize(text_format.size() * float(factor))
        label.setTextFormat(text_format)
        return
    except (AttributeError, RuntimeError, TypeError):
        pass
    try:                                                # QGIS 3.x fallback
        font = label.font()
        font.setPointSizeF(font.pointSizeF() * float(factor))
        font.setBold(True)
        label.setFont(font)
    except (AttributeError, RuntimeError, TypeError):
        pass


def register(project, layout):
    """Put the layout in the project, replacing one of the same name.

    Replacing, because pressing the button twice should give one drawing
    that is up to date and not two that disagree.
    """
    manager = project.layoutManager()
    existing = manager.layoutByName(layout.name())
    if existing is not None:
        manager.removeLayout(existing)
    manager.addLayout(layout)
    return layout


def export_pdf(layout, path: str) -> str:
    """Write the sheet to PDF. Returns the path, raises on refusal."""
    from qgis.core import QgsLayoutExporter                     # noqa: PLC0415

    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.PdfExportSettings()
    result = exporter.exportToPdf(path, settings)
    _check(result, path, "PDF")
    return path


def export_image(layout, path: str, dpi: int = DEFAULT_DPI) -> str:
    """Write the sheet to a raster image at the given resolution."""
    from qgis.core import QgsLayoutExporter                     # noqa: PLC0415

    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = int(dpi)
    result = exporter.exportToImage(path, settings)
    _check(result, path, "immagine")
    return path


def _check(result, path: str, what: str) -> None:
    from qgis.core import QgsLayoutExporter                     # noqa: PLC0415

    if result != QgsLayoutExporter.ExportResult.Success:
        raise ExportError(
            "layout export to {0} failed with code {1}".format(path, result),
            user_message="Esportazione della cartografia in {0} non "
                         "riuscita.".format(what),
            hint="Codice {0}.".format(int(result)))
    if not os.path.exists(path) or os.path.getsize(path) <= 0:
        raise ExportError(
            "layout export produced nothing at {0}".format(path),
            user_message="La cartografia non ha prodotto alcun file.")


def describe(spec: LayoutSpec, layout=None) -> "list[str]":
    lines = ["CARTOGRAFIA",
             "  Formato:   {0}, {1}".format(
                 spec.page_label(),
                 dict(ORIENTATIONS).get(spec.orientation, spec.orientation)),
             "  Risoluzione: {0} dpi".format(spec.dpi)]
    if layout is not None:
        item = layout.itemById(ITEM_MAP)
        if item is not None:
            lines.append("  Scala:     1:{0:,.0f}".format(
                item.scale()).replace(",", "."))
    lines.append("  Elementi:  {0}".format(", ".join(
        name for name, on in (("legenda", spec.legend),
                              ("scala grafica", spec.scalebar),
                              ("nord", spec.north),
                              ("reticolo", spec.grid_interval_m > 0.0))
        if on) or "nessuno"))
    for warning in spec.warnings:
        lines.append("  ! {0}".format(warning))
    return lines


__all__ = ["LayoutSpec", "PAGE_SIZES", "ORIENTATIONS", "ORIENTATION_LANDSCAPE",
           "ORIENTATION_PORTRAIT", "ITEM_MAP", "ITEM_TITLE", "ITEM_SUBTITLE",
           "ITEM_LEGEND", "ITEM_SCALEBAR", "ITEM_NORTH", "ITEM_CREDITS",
           "build_layout", "register", "export_pdf", "export_image",
           "project_extent", "layers_extent", "north_arrow_path", "describe",
           "DEFAULT_DPI", "GeoCadError"]
