"""
GeoCad UAV Toolkit -- QGIS plugin entry point.

CAD dimensional geometry, parametric grids, forest planting design and UAV
photogrammetric flight planning with mandatory DEM/DTM terrain following.
"""

__version__ = "1.4.8"
__author__ = "Niccolo Marco Mancini"


def classFactory(iface):                                    # noqa: N802
    """Called by QGIS to instantiate the plugin.

    The import is deliberately deferred to inside the function: at the time
    QGIS scans plugin folders, importing Qt widgets at module level in a plugin
    that fails to load leaves a half-initialised module behind and makes the
    real error much harder to read.
    """
    from .plugin import GeoCadUavPlugin
    return GeoCadUavPlugin(iface)
