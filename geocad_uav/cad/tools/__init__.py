"""
Interactive QgsMapTool implementations.

Each tool is a thin canvas adapter over the frozen engine: the state machine
lives in :mod:`.base`, the numbers come from ``core.geometry_engine`` through
``cad.primitives``, and nothing here computes geometry of its own.

Import is deferred through :func:`create_tool` so that merely importing the
package does not pull in ``qgis.gui`` before a ``QgsApplication`` exists.
"""

from __future__ import annotations

#: tool key -> (module name, Italian label, suggested shortcut)
TOOL_REGISTRY = {
    "line": ("line", "Linea", "Alt+Shift+L"),
    "polyline": ("polyline", "Polilinea", "Alt+Shift+P"),
    "rectangle": ("rectangle", "Rettangolo", "Alt+Shift+R"),
    "circle": ("circle", "Cerchio", "Alt+Shift+C"),
    "arc": ("arc", "Arco", "Alt+Shift+A"),
    "square": ("square", "Quadrato", "Alt+Shift+S"),
    "regular_polygon": ("regular_polygon", "Poligono regolare", "Alt+Shift+G"),
    "rotate": ("rotate", "Ruota", "Alt+Shift+T"),
    "move": ("move", "Sposta", "Alt+Shift+M"),
    "resize": ("resize", "Ridimensiona", "Alt+Shift+Z"),
}


def create_tool(key: str, canvas, iface=None, layer_provider=None, **options):
    """Instantiate one map tool by key.

    Raises ``KeyError`` for an unknown key rather than returning None, so a
    typo in the toolbar wiring fails loudly at connect time.
    """
    if key not in TOOL_REGISTRY:
        raise KeyError(
            "unknown CAD tool {0!r}; available: {1}".format(
                key, ", ".join(sorted(TOOL_REGISTRY))))
    module_name = TOOL_REGISTRY[key][0]
    from importlib import import_module

    module = import_module("." + module_name, __name__)
    return module.create(canvas, iface=iface, layer_provider=layer_provider,
                         **options)


def tool_label(key: str) -> str:
    return TOOL_REGISTRY[key][1]


def tool_shortcut(key: str) -> str:
    return TOOL_REGISTRY[key][2]
