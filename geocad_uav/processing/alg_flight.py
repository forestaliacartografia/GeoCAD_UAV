"""
Processing algorithm: UAV photogrammetric flight plan with terrain following.

Exposes the whole ``uav`` pipeline as a ``QgsProcessingAlgorithm`` so it can be
run from the toolbox, batched over many AOIs, or called from a model or a
script -- which is also what makes it testable head-lessly through
``qgis_process``.
"""

from __future__ import annotations

import math
import os

import numpy as np
from qgis.core import (QgsCoordinateReferenceSystem, QgsFeature, QgsFeatureSink,
                       QgsField, QgsFields, QgsGeometry, QgsLineString,
                       QgsPoint, QgsPolygon, QgsProcessing,
                       QgsProcessingAlgorithm, QgsProcessingException,
                       QgsProcessingParameterBoolean,
                       QgsProcessingParameterEnum,
                       QgsProcessingParameterFeatureSink,
                       QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterFileDestination,
                       QgsProcessingParameterNumber,
                       QgsProcessingParameterRasterLayer, QgsWkbTypes)
from qgis.PyQt.QtCore import QCoreApplication

from . import mark_advanced
from ..core import crs as crs_svc
from ..core.errors import GeoCadError
from ..core.models import AltitudeMode, VerticalDatum
from ..core.z import TerrainModel
from ..gui import mission_report as report_mod
from ..io import layer_factory as lf
from ..uav import cameras as cam_lib
from ..uav import drones as drone_lib
from ..uav import mission as mission_mod
from ..uav import photogrammetry as pg
from ..uav import survey as sv
from ..uav import validator as validator_mod


class PlanFlightAlgorithm(QgsProcessingAlgorithm):
    """Plan a photogrammetric survey inside an existing polygon."""

    AOI = "AOI"
    DEM = "DEM"
    DEM_IS_DSM = "DEM_IS_DSM"
    CAMERA = "CAMERA"
    DRONE = "DRONE"
    TARGET_MODE = "TARGET_MODE"
    TARGET_VALUE = "TARGET_VALUE"
    FRONTLAP = "FRONTLAP"
    SIDELAP = "SIDELAP"
    ALT_MODE = "ALT_MODE"
    AZIMUTH_MODE = "AZIMUTH_MODE"
    AZIMUTH = "AZIMUTH"
    PATTERN = "PATTERN"
    DOUBLE_GRID = "DOUBLE_GRID"
    SPEED = "SPEED"
    SAFETY_MARGIN = "SAFETY_MARGIN"
    VEG_CLEARANCE = "VEG_CLEARANCE"
    DZ_TOLERANCE = "DZ_TOLERANCE"
    EDGE_MARGIN = "EDGE_MARGIN"
    VERTICAL_DATUM = "VERTICAL_DATUM"
    GEOID_UNDULATION = "GEOID_UNDULATION"
    FOOTPRINTS = "FOOTPRINTS"

    OUT_LINES = "OUT_LINES"
    OUT_WAYPOINTS = "OUT_WAYPOINTS"
    OUT_PHOTOS = "OUT_PHOTOS"
    OUT_FOOTPRINTS = "OUT_FOOTPRINTS"
    OUT_REPORT = "OUT_REPORT"

    _TARGET_MODES = ["Quota di volo H_AGL [m]", "GSD target [cm/px]"]
    _ALT_MODES = [AltitudeMode.LABELS[AltitudeMode.TERRAIN],
                  AltitudeMode.LABELS[AltitudeMode.STRIP_AMSL],
                  AltitudeMode.LABELS[AltitudeMode.SINGLE_AMSL]]
    _ALT_KEYS = [AltitudeMode.TERRAIN, AltitudeMode.STRIP_AMSL,
                 AltitudeMode.SINGLE_AMSL]
    _AZ_MODES = ["Automatico (lato maggiore dell'area)",
                 "Perpendicolare alla massima pendenza (lungo le curve di livello)",
                 "Manuale"]
    _AZ_KEYS = [sv.AZIMUTH_LONGEST_SIDE, sv.AZIMUTH_ACROSS_SLOPE,
                sv.AZIMUTH_MANUAL]
    _PATTERNS = ["Boustrophedon (multirotore)",
                 "Interlacciato (ala fissa)"]
    _PATTERN_KEYS = [sv.PATTERN_BOUSTROPHEDON, sv.PATTERN_INTERLACED]
    _DATUM_KEYS = [VerticalDatum.UNKNOWN, VerticalDatum.ORTHOMETRIC_EGM96,
                   VerticalDatum.ORTHOMETRIC_EGM2008,
                   VerticalDatum.ORTHOMETRIC_LOCAL, VerticalDatum.ELLIPSOIDAL]

    def __init__(self):
        super().__init__()
        self._cameras = cam_lib.load_library()
        self._drones = drone_lib.load_library()
        self._camera_keys = sorted(self._cameras)
        self._drone_keys = sorted(self._drones)

    # -- identity ---------------------------------------------------------

    def tr(self, text):
        return QCoreApplication.translate("GeoCadUav", text)

    def name(self):
        return "planflight"

    def displayName(self):
        return self.tr("Piano di volo UAV (terrain following)")

    def group(self):
        return self.tr("UAV")

    def groupId(self):
        return "uav"

    def shortHelpString(self):
        return self.tr(
            "Genera un piano di volo fotogrammetrico dentro un poligono "
            "esistente, con quota che segue il terreno:\n\n"
            "    Z_waypoint = Z_DEM(x, y) + H_AGL + margine\n\n"
            "Calcola GSD, impronta a terra, interasse fra strip (D_side) e base "
            "di presa (D_front) dai parametri della camera, e limita la "
            "velocita' effettiva al minimo fra richiesta, mosso, intervallo di "
            "scatto, rateo di salita e massima del drone.\n\n"
            "Le impronte a terra sono proiettate sul DEM per ray casting, "
            "quindi la copertura e il GSD effettivo sono verificati sul "
            "terreno reale e non sul piano.\n\n"
            "Il CRS di lavoro deve essere proiettato metrico: con un CRS "
            "geografico le distanze sarebbero calcolate in gradi e "
            "l'algoritmo si ferma.")

    def createInstance(self):
        return PlanFlightAlgorithm()

    # -- parameters -------------------------------------------------------

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.AOI, self.tr("Area di progetto (poligono)"),
            [QgsProcessing.SourceType.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, self.tr("Modello di elevazione (DTM preferito, o DSM)")))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DEM_IS_DSM,
            self.tr("Il raster e' un DSM (superficie: vegetazione ed edifici)"),
            defaultValue=False))

        self.addParameter(QgsProcessingParameterEnum(
            self.CAMERA, self.tr("Camera"),
            options=[self._cameras[k].name for k in self._camera_keys],
            defaultValue=self._camera_keys.index("dji_mavic3e")
            if "dji_mavic3e" in self._camera_keys else 0))
        self.addParameter(QgsProcessingParameterEnum(
            self.DRONE, self.tr("Drone"),
            options=[self._drones[k].name for k in self._drone_keys],
            defaultValue=self._drone_keys.index("dji_mavic3e")
            if "dji_mavic3e" in self._drone_keys else 0))

        self.addParameter(QgsProcessingParameterEnum(
            self.TARGET_MODE, self.tr("Definisci la missione tramite"),
            options=self._TARGET_MODES, defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.TARGET_VALUE, self.tr("Valore (quota in m, oppure GSD in cm/px)"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=80.0,
            minValue=0.01))

        self.addParameter(QgsProcessingParameterNumber(
            self.FRONTLAP, self.tr("Sovrapposizione longitudinale [%]"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=80.0,
            minValue=1.0, maxValue=95.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SIDELAP, self.tr("Sovrapposizione laterale [%]"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=70.0,
            minValue=1.0, maxValue=95.0))

        self.addParameter(QgsProcessingParameterEnum(
            self.ALT_MODE, self.tr("Modalita' di quota"),
            options=self._ALT_MODES, defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.AZIMUTH_MODE, self.tr("Orientamento delle strip"),
            options=self._AZ_MODES, defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.AZIMUTH, self.tr("Azimut manuale [gradi]"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.0,
            minValue=0.0, maxValue=360.0, optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.PATTERN, self.tr("Schema di volo"),
            options=self._PATTERNS, defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DOUBLE_GRID, self.tr("Doppia griglia a 90 gradi (modelli 3D)"),
            defaultValue=False))

        self.addParameter(QgsProcessingParameterNumber(
            self.SPEED, self.tr("Velocita' richiesta [m/s] (0 = crociera del drone)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=0.0, minValue=0.0))

        for key, label, default in (
                (self.SAFETY_MARGIN, "Margine di sicurezza [m]", 0.0),
                (self.VEG_CLEARANCE, "Clearance vegetazione su DTM [m]", 0.0),
                (self.DZ_TOLERANCE, "Tolleranza verticale waypoint [m]", 2.0),
                (self.EDGE_MARGIN, "Margine esterno aggiuntivo [m]", 0.0),
                (self.GEOID_UNDULATION, "Ondulazione del geoide [m]", 0.0)):
            self.addParameter(mark_advanced(QgsProcessingParameterNumber(
                key, self.tr(label), QgsProcessingParameterNumber.Type.Double,
                defaultValue=default)))

        self.addParameter(mark_advanced(QgsProcessingParameterEnum(
            self.VERTICAL_DATUM, self.tr("Datum verticale del DEM"),
            options=[VerticalDatum.label(k) for k in self._DATUM_KEYS],
            defaultValue=0)))

        footprints = QgsProcessingParameterBoolean(
            self.FOOTPRINTS,
            self.tr("Calcola le impronte a terra sul DEM (necessarie per "
                    "verificare copertura e GSD effettivo)"),
            defaultValue=True)
        self.addParameter(footprints)

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_LINES, self.tr("Rotta di volo"),
            QgsProcessing.SourceType.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_WAYPOINTS, self.tr("Waypoint"),
            QgsProcessing.SourceType.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_PHOTOS, self.tr("Centri di presa"),
            QgsProcessing.SourceType.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_FOOTPRINTS, self.tr("Impronte a terra"),
            QgsProcessing.SourceType.TypeVectorPolygon, optional=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUT_REPORT, self.tr("Report di missione (HTML)"),
            fileFilter="HTML (*.html)", optional=True))

    # -- execution --------------------------------------------------------

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.AOI, context)
        if source is None:
            raise QgsProcessingException(
                self.tr("Nessun layer di area valido."))
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        if dem_layer is None:
            raise QgsProcessingException(
                self.tr("Nessun modello di elevazione valido."))

        work_crs = source.sourceCrs()
        if crs_svc.is_geographic(work_crs):
            raise QgsProcessingException(self.tr(
                "Il CRS dell'area ({0}) e' geografico: distanze, sovrapposizioni "
                "e impronte a terra verrebbero calcolate in gradi. Riproietta "
                "l'area in un CRS metrico (UTM) e riprova.").format(
                    work_crs.authid()))

        geoms = [f.geometry() for f in source.getFeatures() if f.hasGeometry()]
        blocks, aoi_warnings = sv.prepare_aoi(geoms)
        aoi = blocks[0]
        if len(blocks) > 1:
            feedback.pushWarning(self.tr(
                "Sono presenti piu' blocchi: viene pianificato solo il primo."))

        camera = self._cameras[self._camera_keys[
            self.parameterAsEnum(parameters, self.CAMERA, context)]]
        drone = self._drones[self._drone_keys[
            self.parameterAsEnum(parameters, self.DRONE, context)]]
        for warning in cam_lib.check_camera(camera) + drone_lib.check_drone(drone):
            feedback.pushWarning(warning)

        target_mode = self.parameterAsEnum(parameters, self.TARGET_MODE, context)
        target_value = self.parameterAsDouble(parameters, self.TARGET_VALUE,
                                              context)
        h_agl = target_value if target_mode == 0 else None
        gsd = None if target_mode == 0 else target_value / 100.0

        overlap = pg.Overlap(
            frontlap=self.parameterAsDouble(parameters, self.FRONTLAP, context) / 100.0,
            sidelap=self.parameterAsDouble(parameters, self.SIDELAP, context) / 100.0)

        speed = self.parameterAsDouble(parameters, self.SPEED, context)
        alt_mode = self._ALT_KEYS[
            self.parameterAsEnum(parameters, self.ALT_MODE, context)]
        az_mode = self._AZ_KEYS[
            self.parameterAsEnum(parameters, self.AZIMUTH_MODE, context)]
        pattern = self._PATTERN_KEYS[
            self.parameterAsEnum(parameters, self.PATTERN, context)]
        datum = self._DATUM_KEYS[
            self.parameterAsEnum(parameters, self.VERTICAL_DATUM, context)]

        # -- terrain ------------------------------------------------------
        feedback.setProgressText(self.tr("Lettura del modello di elevazione..."))
        provisional = pg.solve_survey_geometry(camera, overlap, h_agl_m=h_agl,
                                               gsd_m_px=gsd)
        margin = (0.6 * provisional.footprint_across_m
                  + self.parameterAsDouble(parameters, self.EDGE_MARGIN, context))
        box = aoi.boundingBox()
        try:
            terrain, terrain_warnings = TerrainModel.from_layer(
                dem_layer, work_crs,
                (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()),
                margin_m=margin,
                is_surface_model=self.parameterAsBool(parameters,
                                                      self.DEM_IS_DSM, context),
                vertical_datum=datum)
        except GeoCadError as exc:
            raise QgsProcessingException(exc.formatted()) from exc
        for warning in aoi_warnings + terrain_warnings:
            feedback.pushWarning(warning)
        if feedback.isCanceled():
            return {}

        # -- mission ------------------------------------------------------
        feedback.setProgressText(self.tr("Generazione della missione..."))
        params = mission_mod.MissionParams(
            camera=camera, drone=drone, overlap=overlap,
            h_agl_m=h_agl, gsd_m=gsd, altitude_mode=alt_mode,
            azimuth_strategy=az_mode,
            manual_azimuth_deg=self.parameterAsDouble(parameters, self.AZIMUTH,
                                                      context),
            pattern=pattern,
            double_grid=self.parameterAsBool(parameters, self.DOUBLE_GRID,
                                             context),
            v_mission_ms=speed if speed > 0 else None,
            safety_margin_m=self.parameterAsDouble(parameters,
                                                   self.SAFETY_MARGIN, context),
            vegetation_clearance_m=self.parameterAsDouble(
                parameters, self.VEG_CLEARANCE, context),
            dz_tolerance_m=self.parameterAsDouble(parameters,
                                                  self.DZ_TOLERANCE, context),
            user_margin_m=self.parameterAsDouble(parameters, self.EDGE_MARGIN,
                                                 context),
            vertical_datum=datum,
            geoid_undulation_m=self.parameterAsDouble(
                parameters, self.GEOID_UNDULATION, context),
            compute_footprints=self.parameterAsBool(parameters,
                                                    self.FOOTPRINTS, context))
        try:
            mission = mission_mod.build_mission(aoi, terrain, params,
                                                work_crs.authid(), feedback)
        except GeoCadError as exc:
            raise QgsProcessingException(exc.formatted()) from exc

        for warning in mission.warnings:
            feedback.pushWarning(warning)

        # -- validation ---------------------------------------------------
        feedback.setProgressText(self.tr("Validazione..."))
        validation = validator_mod.validate(mission, params, terrain, aoi,
                                            work_crs)
        feedback.pushInfo("")
        feedback.pushInfo(self.tr("VALIDAZIONE: {0}").format(validation.summary()))
        for check in validation.errors:
            feedback.reportError("  [ERRORE] {0}: {1}".format(check.label,
                                                              check.detail))
        for check in validation.warnings:
            feedback.pushWarning("  [avviso] {0}: {1}".format(check.label,
                                                              check.detail))

        feedback.pushInfo("")
        for line in report_mod.build_text_summary(mission, validation):
            feedback.pushInfo("  " + line)
        feedback.pushInfo("")
        feedback.pushInfo(self.tr("ASSUNZIONI DICHIARATE:"))
        for line in mission.assumptions:
            feedback.pushInfo("  - " + line)

        # -- outputs ------------------------------------------------------
        results = self._write_sinks(parameters, context, mission, work_crs)

        report_path = self.parameterAsFileOutput(parameters, self.OUT_REPORT,
                                                 context)
        if report_path:
            geometry = pg.solve_survey_geometry(camera, overlap,
                                                h_agl_m=mission.h_agl_m)
            report_mod.save_html(mission, report_path, params, validation,
                                 geometry, overwrite=True)
            results[self.OUT_REPORT] = report_path
        return results

    def _write_sinks(self, parameters, context, mission, work_crs):
        results = {}

        lines_fields = lf.make_fields(lf.LINE_FIELDS)
        sink, dest = self.parameterAsSink(
            parameters, self.OUT_LINES, context, lines_fields,
            QgsWkbTypes.Type.LineStringZ, work_crs)
        if sink is not None:
            for i, line in enumerate(mission.lines):
                arr = np.asarray(line, dtype=float)
                if arr.shape[0] < 2:
                    continue
                geom = QgsGeometry(QgsLineString(
                    [QgsPoint(float(p[0]), float(p[1]), float(p[2]))
                     for p in arr]))
                feat = QgsFeature(lines_fields)
                feat.setGeometry(geom)
                feat.setAttributes([i, "strip", float(geom.length())])
                sink.addFeature(feat, QgsFeatureSink.Flag.FastInsert)
            results[self.OUT_LINES] = dest

        wp_fields = lf.make_fields(lf.WAYPOINT_FIELDS)
        sink, dest = self.parameterAsSink(
            parameters, self.OUT_WAYPOINTS, context, wp_fields,
            QgsWkbTypes.Type.PointZ, work_crs)
        if sink is not None:
            for wp in mission.waypoints:
                feat = QgsFeature(wp_fields)
                feat.setGeometry(QgsGeometry(QgsPoint(wp.x, wp.y, wp.z_amsl)))
                feat.setAttributes([
                    wp.seq, wp.sub_mission, wp.strip_index, wp.z_amsl,
                    wp.z_agl, wp.heading_deg, wp.gimbal_pitch_deg, wp.speed_ms,
                    wp.kind, ";".join(wp.actions), int(wp.dem_gap),
                    int(wp.climb_limited)])
                sink.addFeature(feat, QgsFeatureSink.Flag.FastInsert)
            results[self.OUT_WAYPOINTS] = dest

        ph_fields = lf.make_fields(lf.PHOTO_FIELDS)
        sink, dest = self.parameterAsSink(
            parameters, self.OUT_PHOTOS, context, ph_fields,
            QgsWkbTypes.Type.PointZ, work_crs)
        if sink is not None:
            for ph in mission.photos:
                feat = QgsFeature(ph_fields)
                feat.setGeometry(QgsGeometry(QgsPoint(ph.x, ph.y, ph.z_amsl)))
                feat.setAttributes([
                    ph.photo_id, ph.sub_mission, ph.strip_index, ph.z_amsl,
                    ph.z_agl, ph.omega_deg, ph.phi_deg, ph.kappa_deg,
                    ph.heading_deg, ph.gimbal_pitch_deg, ph.gsd_cm])
                sink.addFeature(feat, QgsFeatureSink.Flag.FastInsert)
            results[self.OUT_PHOTOS] = dest

        if mission.footprints:
            fp_fields = lf.make_fields(lf.FOOTPRINT_FIELDS)
            sink, dest = self.parameterAsSink(
                parameters, self.OUT_FOOTPRINTS, context, fp_fields,
                QgsWkbTypes.Type.PolygonZ, work_crs)
            if sink is not None:
                for ring, photo in zip(mission.footprints, mission.photos):
                    arr = np.asarray(ring, dtype=float)
                    if arr.shape[0] < 4 or not np.all(np.isfinite(arr)):
                        continue
                    pts = [QgsPoint(float(p[0]), float(p[1]), float(p[2]))
                           for p in arr]
                    if pts[0] != pts[-1]:
                        pts.append(pts[0])
                    poly = QgsPolygon()
                    poly.setExteriorRing(QgsLineString(pts))
                    geom = QgsGeometry(poly)
                    feat = QgsFeature(fp_fields)
                    feat.setGeometry(geom)
                    feat.setAttributes([photo.photo_id, photo.strip_index,
                                        photo.z_amsl, photo.gsd_cm,
                                        float(geom.area())])
                    sink.addFeature(feat, QgsFeatureSink.Flag.FastInsert)
                results[self.OUT_FOOTPRINTS] = dest
        return results
