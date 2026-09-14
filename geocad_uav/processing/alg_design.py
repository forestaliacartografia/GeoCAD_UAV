"""
Processing algorithms for the Grid Designer and the Forest Planting Designer.

Both live here because they share the same :class:`~..core.grid.GridSpec`
parameterisation -- spacing, pattern, orientation, margin -- and splitting them
would duplicate that block twice with no benefit.
"""

from __future__ import annotations

import math

import numpy as np
from qgis.core import (QgsFeature, QgsFeatureSink, QgsGeometry, QgsPoint,
                       QgsPointXY, QgsProcessing, QgsProcessingAlgorithm,
                       QgsProcessingException, QgsProcessingParameterBoolean,
                       QgsProcessingParameterEnum,
                       QgsProcessingParameterFeatureSink,
                       QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterNumber,
                       QgsProcessingParameterRasterLayer, QgsWkbTypes)
from qgis.PyQt.QtCore import QCoreApplication

from . import mark_advanced
from ..core import crs as crs_svc
from ..core import grid as grid_mod
from ..core.errors import GeoCadError
from ..core.z import TerrainModel
from ..forest import planting as planting_mod
from ..forest import stats as stats_mod
from ..io import layer_factory as lf


class _GridBase(QgsProcessingAlgorithm):
    """Shared AOI + lattice parameters."""

    AOI = "AOI"
    SPACING_X = "SPACING_X"
    SPACING_Y = "SPACING_Y"
    PATTERN = "PATTERN"
    AZIMUTH = "AZIMUTH"
    MARGIN = "MARGIN"
    SERPENTINE = "SERPENTINE"

    _PATTERNS = [grid_mod.PATTERN_LABELS[k] for k in
                 (grid_mod.PATTERN_RECT, grid_mod.PATTERN_SQUARE,
                  grid_mod.PATTERN_QUINCUNX, grid_mod.PATTERN_HEX)]
    _PATTERN_KEYS = [grid_mod.PATTERN_RECT, grid_mod.PATTERN_SQUARE,
                     grid_mod.PATTERN_QUINCUNX, grid_mod.PATTERN_HEX]

    def tr(self, text):
        return QCoreApplication.translate("GeoCadUav", text)

    def _add_grid_parameters(self, dx_label, dy_label, dx=5.0, dy=5.0):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.AOI, self.tr("Area di progetto (poligono)"),
            [QgsProcessing.SourceType.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterNumber(
            self.SPACING_X, self.tr(dx_label),
            QgsProcessingParameterNumber.Type.Double, defaultValue=dx,
            minValue=0.001))
        self.addParameter(QgsProcessingParameterNumber(
            self.SPACING_Y, self.tr(dy_label),
            QgsProcessingParameterNumber.Type.Double, defaultValue=dy,
            minValue=0.001))
        self.addParameter(QgsProcessingParameterEnum(
            self.PATTERN, self.tr("Schema"), options=self._PATTERNS,
            defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.AZIMUTH, self.tr("Orientamento delle file [gradi da Nord]"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.0,
            minValue=0.0, maxValue=360.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MARGIN, self.tr("Margine dal bordo [m]"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.0,
            minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SERPENTINE,
            self.tr("Numerazione a serpentina (ordine di percorrenza)"),
            defaultValue=False))

    def _read_aoi(self, parameters, context):
        source = self.parameterAsSource(parameters, self.AOI, context)
        if source is None:
            raise QgsProcessingException(self.tr("Nessuna area valida."))
        crs = source.sourceCrs()
        if crs_svc.is_geographic(crs):
            raise QgsProcessingException(self.tr(
                "Il CRS dell'area ({0}) e' geografico: le distanze del sesto "
                "verrebbero interpretate in gradi. Riproietta in un CRS "
                "metrico (UTM) e riprova.").format(crs.authid()))
        geoms = [f.geometry() for f in source.getFeatures() if f.hasGeometry()]
        if not geoms:
            raise QgsProcessingException(
                self.tr("L'area di progetto non contiene geometrie."))
        union = geoms[0] if len(geoms) == 1 else QgsGeometry.unaryUnion(geoms)
        if not union.isGeosValid():
            union = union.makeValid()
        return union, crs

    def _read_spec(self, parameters, context):
        return grid_mod.GridSpec(
            spacing_x=self.parameterAsDouble(parameters, self.SPACING_X, context),
            spacing_y=self.parameterAsDouble(parameters, self.SPACING_Y, context),
            azimuth_deg=self.parameterAsDouble(parameters, self.AZIMUTH, context),
            pattern=self._PATTERN_KEYS[
                self.parameterAsEnum(parameters, self.PATTERN, context)],
            margin_m=self.parameterAsDouble(parameters, self.MARGIN, context),
            serpentine=self.parameterAsBool(parameters, self.SERPENTINE, context))


class GridAlgorithm(_GridBase):
    """Regular point lattice clipped to a polygon."""

    OUT_POINTS = "OUT_POINTS"
    OUT_ROWS = "OUT_ROWS"

    def name(self):
        return "creategrid"

    def displayName(self):
        return self.tr("Griglia parametrica")

    def group(self):
        return self.tr("Griglie e impianti")

    def groupId(self):
        return "design"

    def shortHelpString(self):
        return self.tr(
            "Genera una griglia regolare di punti dentro un poligono "
            "esistente, con passo, schema e orientamento liberi.\n\n"
            "La griglia e' ancorata a un'origine stabile: allargare l'area non "
            "sposta i punti gia' calcolati. Dopo il ritaglio, file e punti "
            "sono rinumerati in modo consecutivo.")

    def createInstance(self):
        return GridAlgorithm()

    def initAlgorithm(self, config=None):
        self._add_grid_parameters("Passo lungo le file (dx) [m]",
                                  "Distanza fra le file (dy) [m]")
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_POINTS, self.tr("Punti della griglia"),
            QgsProcessing.SourceType.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_ROWS, self.tr("Linee di file"),
            QgsProcessing.SourceType.TypeVectorLine, optional=True))

    def processAlgorithm(self, parameters, context, feedback):
        aoi, crs = self._read_aoi(parameters, context)
        spec = self._read_spec(parameters, context)

        working = aoi
        if spec.margin_m > 0:
            working = aoi.buffer(-spec.margin_m, 12)
            if working is None or working.isEmpty():
                raise QgsProcessingException(self.tr(
                    "Il margine di {0:g} m elimina completamente l'area."
                    ).format(spec.margin_m))

        box = working.boundingBox()
        result = grid_mod.generate_grid(
            (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()),
            spec)
        feedback.pushInfo(self.tr("Candidati generati: {0:,}").format(len(result)))

        # intersects(), not contains(): contains() is strictly interior and
        # drops every node sitting exactly on the boundary. On a 100 x 100 m
        # AOI at 5 m that turns the expected 21 x 21 = 441 nodes into 19 x 19.
        # Standing off the edge is what the margin parameter is for.
        engine = QgsGeometry.createGeometryEngine(working.constGet())
        engine.prepareGeometry()
        keep = np.zeros(len(result), dtype=bool)
        for i in range(len(result)):
            if feedback.isCanceled():
                return {}
            if i % 2000 == 0:
                feedback.setProgress(80.0 * i / max(len(result), 1))
            keep[i] = engine.intersects(
                QgsPoint(float(result.xy[i, 0]), float(result.xy[i, 1])))
        clipped = grid_mod.renumber(grid_mod.filter_result(result, keep))
        feedback.pushInfo(self.tr("Punti dentro l'area: {0:,} in {1} file")
                          .format(len(clipped), clipped.n_rows))

        fields = lf.make_fields([("id", "int"), ("row_id", "int"),
                                 ("seq_in_row", "int"), ("x", "double"),
                                 ("y", "double")])
        sink, dest = self.parameterAsSink(parameters, self.OUT_POINTS, context,
                                          fields, QgsWkbTypes.Type.Point, crs)
        results = {}
        if sink is not None:
            for i in range(len(clipped)):
                feat = QgsFeature(fields)
                feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(
                    float(clipped.xy[i, 0]), float(clipped.xy[i, 1]))))
                feat.setAttributes([i + 1, int(clipped.row[i]) + 1,
                                    int(clipped.col[i]) + 1,
                                    float(clipped.xy[i, 0]),
                                    float(clipped.xy[i, 1])])
                sink.addFeature(feat, QgsFeatureSink.Flag.FastInsert)
            results[self.OUT_POINTS] = dest

        row_fields = lf.make_fields([("row_id", "int"), ("n_points", "int"),
                                     ("length_m", "double")])
        sink, dest = self.parameterAsSink(
            parameters, self.OUT_ROWS, context,
            row_fields, QgsWkbTypes.Type.LineString, crs)
        if sink is not None:
            for i, line in enumerate(clipped.row_lines()):
                if line.shape[0] < 2:
                    continue
                geom = QgsGeometry.fromPolylineXY(
                    [QgsPointXY(float(p[0]), float(p[1])) for p in line])
                feat = QgsFeature(row_fields)
                feat.setGeometry(geom)
                feat.setAttributes([i + 1, int(line.shape[0]),
                                    float(geom.length())])
                sink.addFeature(feat, QgsFeatureSink.Flag.FastInsert)
            results[self.OUT_ROWS] = dest
        return results


class ForestPlantingAlgorithm(_GridBase):
    """Planting scheme with topographic filtering."""

    DEM = "DEM"
    SLOPE_MIN = "SLOPE_MIN"
    SLOPE_MAX = "SLOPE_MAX"
    ELEV_MIN = "ELEV_MIN"
    ELEV_MAX = "ELEV_MAX"
    ASPECT_FROM = "ASPECT_FROM"
    ASPECT_TO = "ASPECT_TO"
    OUT_PLANTS = "OUT_PLANTS"
    OUT_EXCLUDED = "OUT_EXCLUDED"
    OUT_ROWS = "OUT_ROWS"

    def name(self):
        return "forestplanting"

    def displayName(self):
        return self.tr("Sesto d'impianto forestale")

    def group(self):
        return self.tr("Griglie e impianti")

    def groupId(self):
        return "design"

    def shortHelpString(self):
        return self.tr(
            "Genera le posizioni d'impianto dentro un poligono esistente, "
            "erodendolo del margine dal bordo.\n\n"
            "Con un DEM collegato applica filtri reali di pendenza, quota ed "
            "esposizione. Le posizioni scartate non vengono buttate via: "
            "finiscono in un layer separato con il motivo dello scarto, cosi' "
            "si capisce perche' un versante e' rimasto vuoto.\n\n"
            "Un intervallo di esposizione che attraversa il Nord (es. 315-45) "
            "e' gestito correttamente.")

    def createInstance(self):
        return ForestPlantingAlgorithm()

    def initAlgorithm(self, config=None):
        self._add_grid_parameters("Distanza fra le piante [m]",
                                  "Distanza fra le file [m]", 3.0, 2.0)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, self.tr("Modello di elevazione (per i filtri)"),
            optional=True))
        for key, label, default in (
                (self.SLOPE_MIN, "Pendenza minima [gradi]", 0.0),
                (self.SLOPE_MAX, "Pendenza massima [gradi]", 0.0),
                (self.ELEV_MIN, "Quota minima [m]", 0.0),
                (self.ELEV_MAX, "Quota massima [m]", 0.0),
                (self.ASPECT_FROM, "Esposizione da [gradi]", 0.0),
                (self.ASPECT_TO, "Esposizione a [gradi]", 0.0)):
            self.addParameter(mark_advanced(QgsProcessingParameterNumber(
                key, self.tr(label + " (0 = non applicato)"),
                QgsProcessingParameterNumber.Type.Double, defaultValue=default,
                optional=True)))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_PLANTS, self.tr("Piante"),
            QgsProcessing.SourceType.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_EXCLUDED, self.tr("Posizioni scartate"),
            QgsProcessing.SourceType.TypeVectorPoint, optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_ROWS, self.tr("Linee di file"),
            QgsProcessing.SourceType.TypeVectorLine, optional=True))

    def processAlgorithm(self, parameters, context, feedback):
        aoi, crs = self._read_aoi(parameters, context)
        spec = self._read_spec(parameters, context)

        def optional(key):
            value = self.parameterAsDouble(parameters, key, context)
            return None if abs(value) < 1e-12 else value

        aspect_from = optional(self.ASPECT_FROM)
        aspect_to = optional(self.ASPECT_TO)
        topo = planting_mod.TopographicFilter(
            slope_min_deg=optional(self.SLOPE_MIN),
            slope_max_deg=optional(self.SLOPE_MAX),
            elev_min_m=optional(self.ELEV_MIN),
            elev_max_m=optional(self.ELEV_MAX),
            aspect_ranges=([(aspect_from, aspect_to)]
                           if aspect_from is not None and aspect_to is not None
                           else []))

        terrain = None
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        if dem_layer is not None:
            box = aoi.boundingBox()
            try:
                terrain, warnings = TerrainModel.from_layer(
                    dem_layer, crs,
                    (box.xMinimum(), box.yMinimum(), box.xMaximum(),
                     box.yMaximum()), margin_m=max(spec.spacing_x,
                                                   spec.spacing_y) * 2.0)
            except GeoCadError as exc:
                raise QgsProcessingException(exc.formatted()) from exc
            for warning in warnings:
                feedback.pushWarning(warning)
        elif topo.is_active:
            feedback.pushWarning(self.tr(
                "Filtri topografici richiesti ma nessun DEM collegato: non "
                "verranno applicati."))

        feedback.setProgressText(self.tr("Generazione delle posizioni..."))
        try:
            result = planting_mod.plan_planting_for_geometry(
                aoi, spec, terrain=terrain, topo_filter=topo,
                compute_edge_distance=True)
        except GeoCadError as exc:
            raise QgsProcessingException(exc.formatted()) from exc

        st = stats_mod.compute_stats(result)
        feedback.pushInfo("")
        for line in stats_mod.format_report(st, result):
            feedback.pushInfo("  " + line)

        results = {}
        plant_fields = lf.make_fields(lf.PLANT_FIELDS)
        sink, dest = self.parameterAsSink(parameters, self.OUT_PLANTS, context,
                                          plant_fields, QgsWkbTypes.Type.Point, crs)
        if sink is not None:
            for p in result.plants:
                feat = QgsFeature(plant_fields)
                feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(p.x, p.y)))
                feat.setAttributes([p.plant_id, p.row_id, p.seq_in_row, p.z,
                                    p.spacing_x, p.spacing_y, p.azimuth_deg,
                                    p.dist_to_edge, p.slope_deg, p.aspect_deg])
                sink.addFeature(feat, QgsFeatureSink.Flag.FastInsert)
            results[self.OUT_PLANTS] = dest

        excl_fields = lf.make_fields(lf.EXCLUDED_FIELDS)
        sink, dest = self.parameterAsSink(parameters, self.OUT_EXCLUDED,
                                          context, excl_fields,
                                          QgsWkbTypes.Type.Point, crs)
        if sink is not None:
            for p in result.excluded:
                feat = QgsFeature(excl_fields)
                feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(p.x, p.y)))
                feat.setAttributes([p.plant_id, p.row_id, p.seq_in_row, p.z,
                                    p.spacing_x, p.spacing_y, p.azimuth_deg,
                                    p.dist_to_edge, p.slope_deg, p.aspect_deg,
                                    planting_mod.REASON_LABELS.get(
                                        p.excluded_reason, p.excluded_reason)])
                sink.addFeature(feat, QgsFeatureSink.Flag.FastInsert)
            results[self.OUT_EXCLUDED] = dest

        row_fields = lf.make_fields([("row_id", "int"), ("n_plants", "int"),
                                     ("length_m", "double")])
        sink, dest = self.parameterAsSink(parameters, self.OUT_ROWS, context,
                                          row_fields, QgsWkbTypes.Type.LineString,
                                          crs)
        if sink is not None:
            for i, line in enumerate(result.rows):
                if line.shape[0] < 2:
                    continue
                geom = QgsGeometry.fromPolylineXY(
                    [QgsPointXY(float(p[0]), float(p[1])) for p in line])
                feat = QgsFeature(row_fields)
                feat.setGeometry(geom)
                feat.setAttributes([i + 1, int(line.shape[0]),
                                    float(geom.length())])
                sink.addFeature(feat, QgsFeatureSink.Flag.FastInsert)
            results[self.OUT_ROWS] = dest
        return results
