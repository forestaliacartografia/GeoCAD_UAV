"""
Interactive QgsMapTool implementations.

Each tool is a thin canvas adapter over the frozen engine: the state machine
lives in :mod:`.base`, the numbers come from ``core.geometry_engine`` through
``cad.primitives``, and nothing here computes geometry of its own.

Import is deferred through :func:`create_tool` so that merely importing the
package does not pull in ``qgis.gui`` before a ``QgsApplication`` exists.

v1.7.0 -- the CAD toolset is exactly three primitives: **Quadrato**,
**Rettangolo**, **Poligono**. Each is offered in both input modes, which is
what the two entries per shape are: one draws on the map, the other asks for
the dimensions and then only for the insertion point. Both entries of a pair
end at the same builder in ``cad.primitives``, so a square drawn and a square
typed are the same square.
"""

from __future__ import annotations

from .. import primitives as pr

#: tool key -> (module name, Italian label, suggested shortcut, options)
#:
#: ``options`` are handed to the module's ``create()``. That is what lets one
#: module serve three entries: ``manual_input`` is the parametric mode of
#: whichever shape it is pointed at, and the registry -- not the plugin -- is
#: where that pointing is written down.
TOOL_REGISTRY = {
    "square": ("square", "Quadrato", "Alt+Shift+S", {}),
    "square_params": ("manual_input", "Quadrato parametrico", "Alt+Shift+Q",
                      {"shape": pr.TOOL_SQUARE}),
    "rectangle": ("rectangle", "Rettangolo", "Alt+Shift+R", {}),
    "rectangle_params": ("manual_input", "Rettangolo parametrico",
                         "Alt+Shift+E", {"shape": pr.TOOL_RECTANGLE}),
    "digitize": ("digitize", "Poligono", "Alt+Shift+D", {}),
    "polygon_params": ("manual_input", "Poligono parametrico", "Alt+Shift+G",
                       {"shape": pr.TOOL_POLYGON}),
    "rotate": ("rotate", "Ruota", "Alt+Shift+T", {}),
    "move": ("move", "Sposta", "Alt+Shift+M", {}),
    "resize": ("resize", "Ridimensiona", "Alt+Shift+Z", {}),
}

#: Map tools withdrawn in v1.7.0. Their sessions are still in the package and
#: still under test -- the engine primitives behind them are used elsewhere,
#: and ``line`` in particular is how ``gui.extent_source`` measures a distance
#: on the map -- but none of them is offered as a CAD tool any more, because
#: the toolset is three primitives and no others.
#:
#: What each one leaves behind:
#:   line, polyline  -> not a closed figure; the polygon tool digitises
#:   circle, arc     -> withdrawn outright
#:   ellipse         -> withdrawn in v1.4.5, module already deleted
#:   regular_polygon -> its primitive is the "Poligono parametrico" mode
WITHDRAWN_TOOLS = ("line", "polyline", "circle", "arc", "ellipse",
                   "regular_polygon", "manual_input")

#: The three primitives an operator may draw, and the two keys each one has.
ADMITTED_SHAPES = {
    "Quadrato": ("square", "square_params"),
    "Rettangolo": ("rectangle", "rectangle_params"),
    "Poligono": ("digitize", "polygon_params"),
}


def create_tool(key: str, canvas, iface=None, layer_provider=None, **options):
    """Instantiate one map tool by key.

    Raises ``KeyError`` for an unknown key rather than returning None, so a
    typo in the toolbar wiring fails loudly at connect time. The registry's
    own options are applied first; anything the caller passes wins, which is
    what keeps a live setting (a pivot, a segment count) able to override a
    default without editing the table.
    """
    if key not in TOOL_REGISTRY:
        raise KeyError(
            "unknown CAD tool {0!r}; available: {1}".format(
                key, ", ".join(sorted(TOOL_REGISTRY))))
    module_name = TOOL_REGISTRY[key][0]
    from importlib import import_module

    merged = dict(tool_options(key))
    merged.update(options)
    module = import_module("." + module_name, __name__)
    return module.create(canvas, iface=iface, layer_provider=layer_provider,
                         **merged)


def tool_label(key: str) -> str:
    return TOOL_REGISTRY[key][1]


def tool_shortcut(key: str) -> str:
    return TOOL_REGISTRY[key][2]


def tool_options(key: str) -> dict:
    """Default keyword arguments for this entry's ``create()``."""
    return dict(TOOL_REGISTRY[key][3])
