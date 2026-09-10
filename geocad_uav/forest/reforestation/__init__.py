"""
Reforestation design: from an area, a terrain and a set of constraints to a
planted, verifiable project.

One module per responsibility, and none of them re-implements what the plugin
already owns:

* :mod:`area`        -- the project polygon, its exclusions and its surfaces;
* :mod:`terrain`     -- the DEM, read through ``core.z.TerrainModel``;
* :mod:`constraints` -- parametric buffers around linear or point features.

Every areal computation goes through GEOS (``QgsGeometry.difference``,
``buffer``, ``intersection``, ``unaryUnion``): there is no clipping arithmetic
written in Python here, because GEOS is the one that has to agree with what
QGIS shows on screen.
"""
