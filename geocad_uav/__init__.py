"""
GeoCad UAV Toolkit -- QGIS plugin entry point.

CAD dimensional geometry, parametric grids, forest planting design and UAV
photogrammetric flight planning with mandatory DEM/DTM terrain following.

Copyright (C) 2026 Cap. Niccolo Marco Mancini -- RGPBIO

This program is free software; you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the Free
Software Foundation; either version 2 of the License, or (at your option)
any later version.

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
more details.

You should have received a copy of the GNU General Public License along with
this program; if not, see <https://www.gnu.org/licenses/>. The full text is
in the LICENSE file shipped beside this one.
"""

__version__ = "2.2.3"
__author__ = "Niccolo Marco Mancini"
__license__ = "GPL-2.0-or-later"
__copyright__ = "Copyright (C) 2026 Cap. Niccolo Marco Mancini -- RGPBIO"


def classFactory(iface):                                    # noqa: N802
    """Called by QGIS to instantiate the plugin.

    The import is deliberately deferred to inside the function: at the time
    QGIS scans plugin folders, importing Qt widgets at module level in a plugin
    that fails to load leaves a half-initialised module behind and makes the
    real error much harder to read.
    """
    from .plugin import GeoCadUavPlugin
    return GeoCadUavPlugin(iface)
