"""The enum spellings PyQt6 requires, written down once.

An enum member can be reached two ways: on the enum that owns it
(``QgsWkbTypes``, ``GeometryType``, then the member) or straight on the class
(``QgsWkbTypes`` then the member). PyQt5 answers to both, PyQt6 keeps only the
first as the real name -- QGIS adds the second back as an alias, which is why
a plugin can run perfectly on the LTR and still be refused on upload: the
official repository runs a Qt6 check on the package and reports every one.

Two things read this table, and they check different halves of the claim:

* ``geocad_uav/tests/test_qt6_enums.py`` asks the running QGIS whether each
  scoped name resolves and carries the value recorded here, and whether the
  sources have stopped writing the unscoped one;
* ``zip_plugin.py`` refuses to build an archive that still writes one, so the
  defect is caught before an upload is rejected rather than after.

Deliberately free of imports: the packager loads this file directly, without
starting QGIS and without importing the plugin package.

Each row is ``(class, enum, member, value)``. The values were measured on
QGIS 3.40.15 (PyQt5) and QGIS 4.0.0 (PyQt6) and agreed exactly; the test
asserts them rather than trusting them, so a renumbering would surface here
instead of in the field.
"""

SCOPED_ENUMS = [
    ("Qgis", "MessageLevel", "Info", 0),
    ("Qgis", "MessageLevel", "Warning", 1),
    ("Qgis", "MessageLevel", "Critical", 2),
    ("Qgis", "MessageLevel", "Success", 3),
    ("QgsFeatureSink", "Flag", "FastInsert", 2),
    ("QgsMapLayerProxyModel", "Filter", "VectorLayer", 30),
    ("QgsMapLayerProxyModel", "Filter", "RasterLayer", 1),
    ("QgsMapLayerProxyModel", "Filter", "PolygonLayer", 16),
    ("QgsMapTool", "Flag", "EditTool", 4),
    ("QgsProcessing", "SourceType", "TypeVectorPoint", 0),
    ("QgsProcessing", "SourceType", "TypeVectorLine", 1),
    ("QgsProcessing", "SourceType", "TypeVectorPolygon", 2),
    ("QgsProcessingParameterDefinition", "Flag", "FlagAdvanced", 2),
    ("QgsProcessingParameterNumber", "Type", "Double", 1),
    ("QgsRubberBand", "IconType", "ICON_BOX", 3),
    ("QgsRubberBand", "IconType", "ICON_CIRCLE", 4),
    ("QgsSnappingConfig", "SnappingMode", "AllLayers", 2),
    ("QgsTolerance", "UnitType", "Pixels", 1),
    ("QgsUnitTypes", "AreaUnit", "AreaSquareMeters", 0),
    ("QgsUnitTypes", "DistanceUnit", "DistanceMeters", 0),
    ("QgsVectorDataProvider", "Capability", "AddFeatures", 1),
    ("QgsVectorFileWriter", "ActionOnExistingFile", "CreateOrOverwriteFile", 0),
    ("QgsVectorFileWriter", "ActionOnExistingFile", "CreateOrOverwriteLayer", 1),
    ("QgsVectorFileWriter", "WriterError", "NoError", 0),
    ("QgsVertexMarker", "IconType", "ICON_CROSS", 1),
    ("QgsWkbTypes", "GeometryType", "PointGeometry", 0),
    ("QgsWkbTypes", "GeometryType", "LineGeometry", 1),
    ("QgsWkbTypes", "GeometryType", "PolygonGeometry", 2),
    ("QgsWkbTypes", "Type", "Point", 1),
    ("QgsWkbTypes", "Type", "LineString", 2),
    ("QgsWkbTypes", "Type", "PointZ", 1001),
    ("QgsWkbTypes", "Type", "LineStringZ", 1002),
    ("QgsWkbTypes", "Type", "PolygonZ", 1003),
]
