"""
M07 -- the plants layer, coloured by species, without anyone touching a dialog.

A mixed plantation is unreadable as one colour: the whole point of planning a
mix is seeing where each species went. So the point layer gets a
``QgsCategorizedSymbolRenderer`` on the species column as soon as it is
built, with one distinct colour per species.

The colours are generated, not listed. A fixed palette runs out at its eighth
species and starts repeating -- silently, which on a map is worse than ugly.
Hues are spread by the golden angle instead: consecutive categories land far
apart on the colour wheel at any count, and the sequence is deterministic, so
the same project always prints the same colours.
"""

from __future__ import annotations

from .composition import SPECIES_ATTRIBUTE

#: Column the renderer categorises on. ``specie`` in the layer, because the
#: attribute table is read by Italian-speaking operators; ``species`` on the
#: record, because the code is English.
SPECIES_FIELD = "specie"

#: Golden angle on the colour wheel. Any two consecutive hues are 137.5 deg
#: apart, and the sequence never revisits a hue for a very long time.
GOLDEN_ANGLE = 0.6180339887498949

#: Saturation and value of every category. Kept constant so the categories
#: differ by hue alone -- a legend where one entry is also darker reads as a
#: second dimension that is not there.
SATURATION = 0.62
VALUE = 0.88

#: Where a plant has no species: grey, and labelled as such rather than left
#: to fall into whichever category sorts first.
UNASSIGNED_COLOR = "#9e9e9e"
UNASSIGNED_LABEL = "Non assegnata"


def hue_for(index: int) -> float:
    """Hue in [0, 1) for the n-th category."""
    return (0.08 + index * GOLDEN_ANGLE) % 1.0


def color_for(index: int):
    """A distinct ``QColor`` for the n-th category."""
    from qgis.PyQt.QtGui import QColor                          # noqa: PLC0415

    return QColor.fromHsvF(hue_for(index), SATURATION, VALUE)


def palette(keys) -> dict:
    """``{species key: '#rrggbb'}``, in the order the keys are given.

    Deterministic for a given order, which is why the caller is expected to
    hand in a sorted list: the same plan re-rendered tomorrow keeps its
    colours.
    """
    out = {}
    for index, key in enumerate(keys):
        out[key] = ("" if not key else color_for(index).name())
    return out


def categorized_renderer(layer, field: str = SPECIES_FIELD, keys=None):
    """A renderer with one category per species present in ``layer``.

    ``keys`` overrides what is in the layer, for a legend that has to show a
    species that happens to have no plants in this zone.
    """
    from qgis.core import (QgsCategorizedSymbolRenderer,        # noqa: PLC0415
                           QgsRendererCategory, QgsSymbol)
    from qgis.PyQt.QtGui import QColor                          # noqa: PLC0415

    index = layer.fields().indexOf(field)
    if index < 0:
        return None
    if keys is None:
        values = {str(value) for value in layer.uniqueValues(index)
                  if value not in (None, "")}
        keys = sorted(values)

    categories = []
    for position, key in enumerate(keys):
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        if symbol is None:
            return None
        symbol.setColor(color_for(position))
        categories.append(QgsRendererCategory(key, symbol, str(key)))

    # Everything else -- a plant the composition never reached -- shown as
    # itself rather than folded into the first category.
    fallback = QgsSymbol.defaultSymbol(layer.geometryType())
    if fallback is not None:
        fallback.setColor(QColor(UNASSIGNED_COLOR))
        categories.append(QgsRendererCategory("", fallback, UNASSIGNED_LABEL))

    return QgsCategorizedSymbolRenderer(field, categories)


def apply_species_symbology(layer, field: str = SPECIES_FIELD,
                            keys=None) -> int:
    """Colour the layer by species. Returns how many categories were made.

    Zero means nothing was applied -- no such column, or no symbol for this
    geometry type -- and the layer keeps whatever styling it had. A layer
    that refuses to be styled is not a reason to lose the plan.
    """
    renderer = categorized_renderer(layer, field, keys)
    if renderer is None:
        return 0
    try:
        layer.setRenderer(renderer)
        layer.triggerRepaint()
    except (AttributeError, RuntimeError):
        return 0
    return len(renderer.categories())


def legend(layer, field: str = SPECIES_FIELD) -> "list[tuple]":
    """``[(species, '#rrggbb'), ...]`` as the map legend will show it."""
    renderer = layer.renderer() if layer is not None else None
    if renderer is None or not hasattr(renderer, "categories"):
        return []
    out = []
    for category in renderer.categories():
        symbol = category.symbol()
        colour = symbol.color().name() if symbol is not None else ""
        out.append((str(category.value()), colour))
    return out


def describe(layer, field: str = SPECIES_FIELD) -> "list[str]":
    lines = ["SIMBOLOGIA PER SPECIE",
             "  Colonna: {0} (attributo {1} sul record)".format(
                 field, SPECIES_ATTRIBUTE)]
    for key, colour in legend(layer, field):
        lines.append("  {0:<20} {1}".format(key or UNASSIGNED_LABEL, colour))
    return lines
