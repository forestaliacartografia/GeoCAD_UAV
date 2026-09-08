"""
Mission validator: the gate in front of export.

Errors block export. Warnings do not, but every one of them is something the
pilot has to have seen and accepted. The distinction is deliberate: a mission
with a 30 cm GSD overrun is still flyable and might be exactly what was wanted,
while a waypoint below the minimum AGL is not a matter of taste.

Every check returns a :class:`Check` so the report can show what was verified
and what the measured value actually was -- a validator that only speaks when
something is wrong gives no confidence that it looked at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..core import constants as K
from ..core.models import AltitudeMode, DroneProfile, Mission, VerticalDatum
from . import photogrammetry as pg

SEVERITY_OK = "ok"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"

#: Two waypoints closer than this confuse most flight controllers.
MIN_WAYPOINT_SPACING_M = 0.5


@dataclass
class Check:
    """One verified condition, with the number that was measured."""

    code: str
    label: str
    severity: str = SEVERITY_OK
    detail: str = ""
    value: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.severity == SEVERITY_OK


@dataclass
class ValidationReport:
    checks: "list[Check]" = field(default_factory=list)

    def add(self, code, label, severity=SEVERITY_OK, detail="", value=None):
        self.checks.append(Check(code, label, severity, detail, value))

    @property
    def errors(self) -> "list[Check]":
        return [c for c in self.checks if c.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> "list[Check]":
        return [c for c in self.checks if c.severity == SEVERITY_WARNING]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.errors:
            return "NON VALIDA: {0} errori, {1} avvisi".format(
                len(self.errors), len(self.warnings))
        if self.warnings:
            return "Valida con {0} avvisi".format(len(self.warnings))
        return "Valida: tutti i controlli superati"


def validate(mission: Mission, params=None, terrain=None, aoi_geom=None,
             crs=None, no_fly_geoms=None) -> ValidationReport:
    """Run every check. ``params``/``terrain``/``aoi_geom`` widen the coverage."""
    report = ValidationReport()
    _check_crs(report, crs, mission)
    _check_waypoints(report, mission)
    _check_altitude(report, mission, params)
    _check_speed(report, mission, params)
    _check_photogrammetry(report, mission, params)
    _check_endurance(report, mission, params)
    _check_terrain(report, mission, params, terrain)
    _check_coverage(report, mission, aoi_geom, params)
    _check_no_fly(report, mission, no_fly_geoms)
    _check_datum(report, mission, params)
    return report


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------

def _check_crs(report, crs, mission):
    if crs is None:
        report.add("crs_unknown", "CRS di lavoro", SEVERITY_WARNING,
                   "Nessun CRS fornito al validatore; impossibile verificare "
                   "che le distanze siano metriche.")
        return
    try:
        geographic = crs.isGeographic()
        authid = crs.authid()
    except AttributeError:
        report.add("crs_unknown", "CRS di lavoro", SEVERITY_WARNING,
                   "Oggetto CRS non riconosciuto.")
        return
    if geographic:
        report.add("crs_geographic", "CRS di lavoro", SEVERITY_ERROR,
                   "Il CRS {0} e' geografico: distanze, aree e spaziature "
                   "sarebbero calcolate in gradi.".format(authid),
                   value=authid)
    else:
        report.add("crs_projected", "CRS di lavoro", SEVERITY_OK,
                   "CRS proiettato metrico.", value=authid)


def _check_waypoints(report, mission: Mission):
    wps = mission.waypoints
    if not wps:
        report.add("no_waypoints", "Waypoint presenti", SEVERITY_ERROR,
                   "La missione non contiene alcun waypoint.", value="0")
        return
    report.add("waypoint_count", "Waypoint presenti", SEVERITY_OK,
               value=str(len(wps)))

    coords = np.array([[w.x, w.y, w.z_amsl] for w in wps], dtype=float)
    if not np.all(np.isfinite(coords)):
        bad = int(np.count_nonzero(~np.isfinite(coords).all(axis=1)))
        report.add("non_finite", "Coordinate finite", SEVERITY_ERROR,
                   "{0} waypoint hanno coordinate o quote non finite "
                   "(NaN/inf).".format(bad), value=str(bad))
    else:
        report.add("non_finite", "Coordinate finite", SEVERITY_OK)

    zero_z = int(np.count_nonzero(np.abs(coords[:, 2]) < 1e-9))
    if zero_z:
        report.add("zero_z", "Quote assegnate", SEVERITY_ERROR,
                   "{0} waypoint hanno quota nulla: il terrain following non "
                   "e' stato applicato.".format(zero_z), value=str(zero_z))
    else:
        report.add("zero_z", "Quote assegnate", SEVERITY_OK)

    if len(wps) > 1:
        d = np.hypot(*np.diff(coords[:, :2], axis=0).T)
        close = int(np.count_nonzero(d < MIN_WAYPOINT_SPACING_M))
        if close:
            report.add("waypoints_too_close", "Distanza fra waypoint",
                       SEVERITY_WARNING,
                       "{0} coppie di waypoint sono a meno di {1:g} m: molti "
                       "controller li rifiutano o li fondono.".format(
                           close, MIN_WAYPOINT_SPACING_M),
                       value="{0:.2f} m".format(float(d.min())))
        else:
            report.add("waypoints_too_close", "Distanza fra waypoint",
                       SEVERITY_OK, value="{0:.2f} m".format(float(d.min())))

        exact = int(np.count_nonzero(d < 1e-9))
        if exact:
            report.add("duplicate_waypoints", "Waypoint duplicati",
                       SEVERITY_WARNING,
                       "{0} waypoint coincidono.".format(exact), value=str(exact))

    seqs = [w.seq for w in wps]
    if len(set(seqs)) != len(seqs):
        report.add("seq_duplicates", "Numerazione waypoint", SEVERITY_WARNING,
                   "La sequenza contiene numeri ripetuti.")


def _check_altitude(report, mission: Mission, params):
    wps = mission.waypoints
    if not wps:
        return
    if mission.h_agl_m <= 0:
        report.add("h_agl_invalid", "Quota di volo", SEVERITY_ERROR,
                   "H_AGL deve essere maggiore di zero.",
                   value="{0:g}".format(mission.h_agl_m))
        return

    agl = np.array([w.z_agl for w in wps], dtype=float)
    finite = agl[np.isfinite(agl)]
    if finite.size == 0:
        report.add("agl_unknown", "AGL dei waypoint", SEVERITY_ERROR,
                   "Nessun waypoint ha un AGL calcolabile.")
        return

    # In terrain-following mode every waypoint must hold the nominal AGL.
    # The other two modes trade AGL for a constant AMSL by design, so a dip
    # below nominal is expected there and is reported as a warning instead.
    tolerance = 0.01 * mission.h_agl_m + 1e-6
    below = int(np.count_nonzero(finite < mission.h_agl_m - tolerance))
    if below:
        severity = (SEVERITY_ERROR
                    if mission.altitude_mode == AltitudeMode.TERRAIN
                    else SEVERITY_WARNING)
        report.add("agl_below_min", "AGL minimo", severity,
                   "{0} waypoint volano sotto l'AGL nominale di {1:g} m "
                   "(minimo {2:.1f} m).".format(below, mission.h_agl_m,
                                                float(finite.min())),
                   value="{0:.1f} m".format(float(finite.min())))
    else:
        report.add("agl_below_min", "AGL minimo", SEVERITY_OK,
                   value="{0:.1f} m".format(float(finite.min())))

    max_agl = params.max_legal_agl_m if params else K.MAX_LEGAL_AGL_M
    above = int(np.count_nonzero(finite > max_agl))
    if above:
        report.add("agl_over_legal", "Quota massima legale", SEVERITY_ERROR,
                   "{0} waypoint superano il limite di {1:g} m AGL "
                   "(massimo {2:.1f} m).".format(above, max_agl,
                                                 float(finite.max())),
                   value="{0:.1f} m".format(float(finite.max())))
    else:
        report.add("agl_over_legal", "Quota massima legale", SEVERITY_OK,
                   value="{0:.1f} m su {1:g} m".format(float(finite.max()),
                                                       max_agl))


def _check_speed(report, mission: Mission, params):
    if mission.speed_ms <= 0:
        report.add("speed_invalid", "Velocita' di missione", SEVERITY_ERROR,
                   "La velocita' deve essere maggiore di zero.",
                   value="{0:g}".format(mission.speed_ms))
        return
    report.add("speed_valid", "Velocita' di missione", SEVERITY_OK,
               value="{0:.1f} m/s".format(mission.speed_ms))

    if params is None:
        return
    geometry = pg.solve_survey_geometry(
        params.camera, params.overlap, h_agl_m=mission.h_agl_m,
        orientation=params.orientation)
    v_blur = pg.blur_speed_limit(geometry.gsd_m_px, params.camera.shutter_s,
                                 params.blur_px_max)
    if mission.speed_ms > v_blur + 1e-9:
        report.add("blur", "Mosso (motion blur)", SEVERITY_WARNING,
                   "A {0:.1f} m/s lo strisciamento supera {1:g} px "
                   "(limite {2:.1f} m/s).".format(
                       mission.speed_ms, params.blur_px_max, v_blur),
                   value="{0:.1f} m/s".format(v_blur))
    else:
        report.add("blur", "Mosso (motion blur)", SEVERITY_OK,
                   value="limite {0:.1f} m/s".format(v_blur))

    if params.use_interval_trigger:
        v_trigger = pg.trigger_speed_limit(geometry.d_front_m,
                                           params.camera.min_interval_s)
        if mission.speed_ms > v_trigger + 1e-9:
            report.add("trigger", "Intervallo di scatto", SEVERITY_WARNING,
                       "A {0:.1f} m/s servirebbe uno scatto ogni {1:.2f} s, "
                       "ma la camera regge {2:g} s.".format(
                           mission.speed_ms,
                           geometry.d_front_m / mission.speed_ms,
                           params.camera.min_interval_s),
                       value="{0:.1f} m/s".format(v_trigger))
        else:
            report.add("trigger", "Intervallo di scatto", SEVERITY_OK,
                       value="limite {0:.1f} m/s".format(v_trigger))

    slow = [w for w in mission.waypoints if w.climb_limited]
    if slow:
        report.add("climb_limited", "Rateo di salita", SEVERITY_WARNING,
                   "{0} waypoint hanno la velocita' ridotta dalla pendenza del "
                   "terreno.".format(len(slow)), value=str(len(slow)))
    else:
        report.add("climb_limited", "Rateo di salita", SEVERITY_OK)


def _check_photogrammetry(report, mission: Mission, params):
    for name, value in (("frontlap", mission.frontlap),
                        ("sidelap", mission.sidelap)):
        if not (0.0 < value < 1.0):
            report.add("overlap_invalid", "Sovrapposizioni", SEVERITY_ERROR,
                       "{0} = {1!r} non e' compreso fra 0 e 1.".format(
                           name, value), value=str(value))
            return
    report.add("overlap_valid", "Sovrapposizioni", SEVERITY_OK,
               value="F {0:.0f} % / S {1:.0f} %".format(
                   100 * mission.frontlap, 100 * mission.sidelap))

    if params is not None:
        camera = params.camera
        missing = [n for n in ("focal_mm", "sensor_w_mm", "sensor_h_mm",
                               "image_w_px", "image_h_px")
                   if not getattr(camera, n, None)]
        if missing:
            report.add("camera_incomplete", "Parametri camera", SEVERITY_ERROR,
                       "Mancano: {0}.".format(", ".join(missing)))
        else:
            report.add("camera_complete", "Parametri camera", SEVERITY_OK,
                       value=camera.name)
        if camera.pitch_mismatch_pct > 1.0:
            report.add("camera_pitch_mismatch", "Coerenza sensore/pixel",
                       SEVERITY_WARNING,
                       "Il passo pixel da larghezza e da altezza differisce del "
                       "{0:.2f} %.".format(camera.pitch_mismatch_pct))

    gsd = np.array([p.gsd_m for p in mission.photos], dtype=float)
    gsd = gsd[np.isfinite(gsd)]
    if gsd.size and mission.gsd_m > 0:
        ceiling = mission.gsd_m * (1.0 + K.GSD_TOLERANCE)
        over = int(np.count_nonzero(gsd > ceiling))
        if over:
            report.add("gsd_tolerance", "GSD effettivo", SEVERITY_WARNING,
                       "{0} foto su {1} superano il GSD target di oltre il "
                       "{2:.0f} % (max {3:.2f} cm/px contro {4:.2f} cm/px)."
                       .format(over, gsd.size, 100 * K.GSD_TOLERANCE,
                               100 * float(gsd.max()), 100 * ceiling),
                       value="{0:.2f} cm/px".format(100 * float(gsd.max())))
        else:
            report.add("gsd_tolerance", "GSD effettivo", SEVERITY_OK,
                       value="{0:.2f} - {1:.2f} cm/px".format(
                           100 * float(gsd.min()), 100 * float(gsd.max())))


def _check_endurance(report, mission: Mission, params):
    if params is None:
        return
    drone: DroneProfile = params.drone
    per_sub = {}
    for wp in mission.waypoints:
        per_sub[wp.sub_mission] = per_sub.get(wp.sub_mission, 0) + 1
    worst = max(per_sub.values()) if per_sub else 0
    if worst > drone.waypoint_limit:
        report.add("waypoint_limit", "Limite waypoint del controller",
                   SEVERITY_WARNING,
                   "Una sotto-missione contiene {0} waypoint, oltre il limite "
                   "di {1}.".format(worst, drone.waypoint_limit),
                   value=str(worst))
    else:
        report.add("waypoint_limit", "Limite waypoint del controller",
                   SEVERITY_OK,
                   value="{0} su {1}".format(worst, drone.waypoint_limit))

    budget = drone.usable_endurance_s
    per_battery = (mission.stats.flight_time_s / mission.stats.n_batteries
                   if mission.stats.n_batteries else mission.stats.flight_time_s)
    if per_battery > budget + 1e-6:
        report.add("endurance", "Autonomia", SEVERITY_WARNING,
                   "Una sotto-missione dura {0:.1f} min, oltre i {1:.1f} min "
                   "utili con riserva RTH del {2:g} %.".format(
                       per_battery / 60.0, budget / 60.0, drone.rth_reserve_pct),
                   value="{0:.1f} min".format(per_battery / 60.0))
    else:
        report.add("endurance", "Autonomia", SEVERITY_OK,
                   value="{0:.1f} / {1:.1f} min per batteria".format(
                       per_battery / 60.0, budget / 60.0))


def _check_terrain(report, mission: Mission, params, terrain):
    gaps = [w for w in mission.waypoints if w.dem_gap]
    if gaps:
        report.add("dem_gaps", "Buchi nel DEM", SEVERITY_WARNING,
                   "{0} waypoint ricadono su celle senza dato: la quota "
                   "comandata e' stata resa conservativa e va verificata prima "
                   "del volo.".format(len(gaps)), value=str(len(gaps)))
    else:
        report.add("dem_gaps", "Buchi nel DEM", SEVERITY_OK)

    if terrain is not None and params is not None:
        if not terrain.is_surface_model and params.vegetation_clearance_m <= 0:
            report.add("dtm_no_clearance", "DTM senza clearance",
                       SEVERITY_WARNING,
                       "Volo pianificato su un DTM (terreno nudo) senza "
                       "clearance vegetazione. In area boscata o urbana la "
                       "quota reale sopra le chiome sara' inferiore.",
                       value="0 m")
        elif not terrain.is_surface_model:
            report.add("dtm_clearance", "DTM con clearance", SEVERITY_OK,
                       value="{0:g} m".format(params.vegetation_clearance_m))

    if math.isfinite(mission.stats.terrain_relief_m) and mission.h_agl_m > 0:
        ratio = mission.stats.terrain_relief_m / mission.h_agl_m
        if (mission.altitude_mode != AltitudeMode.TERRAIN and ratio > 0.10):
            report.add("relief_vs_mode", "Dislivello e modalita' di quota",
                       SEVERITY_WARNING,
                       "Il dislivello e' il {0:.0f} % della quota di volo ma la "
                       "modalita' non e' il terrain following: GSD e "
                       "sovrapposizione varieranno.".format(100 * ratio),
                       value="{0:.1f} m".format(mission.stats.terrain_relief_m))
        else:
            report.add("relief_vs_mode", "Dislivello e modalita' di quota",
                       SEVERITY_OK,
                       value="{0:.1f} m".format(mission.stats.terrain_relief_m))


def _check_coverage(report, mission: Mission, aoi_geom, params):
    """Coverage is judged on the DEM-draped footprints, never on the plan."""
    if aoi_geom is None or not mission.footprints:
        report.add("coverage_unknown", "Copertura dell'AOI", SEVERITY_WARNING,
                   "Copertura non verificata: impronte a terra non calcolate.")
        return
    min_photos = params.min_photos_per_point if params else K.MIN_PHOTOS_PER_POINT
    pct, min_seen = coverage_fraction(mission, aoi_geom, min_photos)
    mission.stats.coverage_pct = pct
    mission.stats.min_photos_observed = min_seen

    if pct >= 99.999:
        report.add("coverage", "Copertura dell'AOI", SEVERITY_OK,
                   "Ogni punto dell'AOI ricade in almeno {0} foto.".format(
                       min_photos),
                   value="{0:.2f} %".format(pct))
    else:
        report.add("coverage", "Copertura dell'AOI", SEVERITY_ERROR,
                   "Solo il {0:.2f} % dell'AOI ricade in almeno {1} foto "
                   "(minimo osservato: {2}).".format(pct, min_photos, min_seen),
                   value="{0:.2f} %".format(pct))


def coverage_fraction(mission: Mission, aoi_geom, min_photos: int,
                      cell_m: Optional[float] = None):
    """Fraction of the AOI seen by at least ``min_photos`` exposures.

    Sampled on a regular grid of AOI points, each tested against the draped
    footprints through a spatial index. Returns ``(percent, min_count)``.
    """
    from qgis.core import (QgsFeature, QgsGeometry, QgsPointXY,  # noqa: PLC0415
                           QgsSpatialIndex)

    box = aoi_geom.boundingBox()
    if cell_m is None:
        cell_m = max(min(box.width(), box.height()) / 200.0, 1.0)

    polys = []
    index = QgsSpatialIndex()
    for i, ring in enumerate(mission.footprints):
        pts = [QgsPointXY(float(p[0]), float(p[1])) for p in np.asarray(ring)]
        geom = QgsGeometry.fromPolygonXY([pts])
        if geom.isEmpty():
            continue
        polys.append(geom)
        feat = QgsFeature(i)
        feat.setGeometry(geom)
        index.addFeature(feat)

    if not polys:
        return 0.0, 0

    engine = QgsGeometry.createGeometryEngine(aoi_geom.constGet())
    engine.prepareGeometry()

    xs = np.arange(box.xMinimum() + cell_m / 2.0, box.xMaximum(), cell_m)
    ys = np.arange(box.yMinimum() + cell_m / 2.0, box.yMaximum(), cell_m)
    inside_total = 0
    covered = 0
    min_count = 10 ** 9
    for y in ys:
        for x in xs:
            pt = QgsGeometry.fromPointXY(QgsPointXY(float(x), float(y)))
            if not engine.contains(pt.constGet()):
                continue
            inside_total += 1
            count = 0
            for fid in index.intersects(pt.boundingBox()):
                if polys[fid].contains(pt):
                    count += 1
                    if count >= min_photos:
                        break
            min_count = min(min_count, count)
            if count >= min_photos:
                covered += 1
    if inside_total == 0:
        return 0.0, 0
    return 100.0 * covered / inside_total, (0 if min_count == 10 ** 9 else min_count)


def _check_no_fly(report, mission: Mission, no_fly_geoms):
    if not no_fly_geoms:
        report.add("no_fly_absent", "Aree vietate", SEVERITY_OK,
                   "Nessun layer di vincoli fornito: verifica non eseguita.")
        return
    from qgis.core import QgsGeometry, QgsPointXY                # noqa: PLC0415

    hits = 0
    for wp in mission.waypoints:
        pt = QgsGeometry.fromPointXY(QgsPointXY(wp.x, wp.y))
        if any(g.intersects(pt) for g in no_fly_geoms):
            hits += 1
    if hits:
        report.add("no_fly", "Aree vietate", SEVERITY_ERROR,
                   "{0} waypoint ricadono in un'area vietata.".format(hits),
                   value=str(hits))
    else:
        report.add("no_fly", "Aree vietate", SEVERITY_OK,
                   "Nessun waypoint in area vietata.")


def _check_datum(report, mission: Mission, params):
    if mission.vertical_datum == VerticalDatum.UNKNOWN:
        report.add("vertical_datum", "Datum verticale", SEVERITY_WARNING,
                   "Datum verticale non dichiarato. Le quote esportate "
                   "potrebbero non corrispondere a quelle attese dal firmware.",
                   value=VerticalDatum.label(mission.vertical_datum))
    else:
        report.add("vertical_datum", "Datum verticale", SEVERITY_OK,
                   value=VerticalDatum.label(mission.vertical_datum))
