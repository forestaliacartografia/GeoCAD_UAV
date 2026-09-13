"""
Mission assembly: route + terrain + camera + airframe -> a flyable plan.

This is where the pieces meet. The route (``uav.survey``) is planimetric; the
terrain model (``core.z``) knows elevations; the photogrammetric model
(``uav.photogrammetry``) knows spacing and speed ceilings. Here they become
waypoints with heights, headings and per-segment speeds, split into battery-
sized sub-missions, with the numbers that the report and the validator need.

Order of operations matters and is deliberate:

1. Solve the survey geometry (GSD, footprints, D_side, D_front).
2. Lay out the strips.
3. Drape each strip on the DEM at constant AGL.
4. Place exposures at exact D_front chainages.
5. Thin the waypoints to the vertical tolerance, never dropping an exposure.
6. Cap the speed segment by segment against the terrain gradient.
7. Split into sub-missions on endurance and waypoint count.

Steps 5 and 6 come after 4 so that thinning can never move an exposure, and
speed capping sees the waypoints that will actually be flown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..core import constants as K
from ..core.errors import MissionError
from ..core.models import (AltitudeMode, DroneProfile, Mission, MissionStats,
                           PhotoCenter, VerticalDatum, Waypoint)
from ..core.planar import cumulative_distance, headings_along
from ..core.z import TerrainModel
from . import photogrammetry as pg
from . import survey as sv
from . import terrain_follow as tf


@dataclass
class MissionParams:
    """Everything the operator chooses. Defaults are the documented ones."""

    camera: pg.Camera
    drone: DroneProfile
    overlap: pg.Overlap

    # Exactly one of these two.
    h_agl_m: Optional[float] = None
    gsd_m: Optional[float] = None

    orientation: str = pg.ORIENT_ACROSS
    altitude_mode: str = AltitudeMode.TERRAIN
    safety_margin_m: float = K.TERRAIN_DZ_M * 0.0
    vegetation_clearance_m: float = 0.0
    dz_tolerance_m: float = K.TERRAIN_DZ_M
    gap_mode: str = "hold_max"

    azimuth_strategy: str = sv.AZIMUTH_LONGEST_SIDE
    manual_azimuth_deg: Optional[float] = None
    #: Sweep resolution when the strategy is ``optimised``.
    azimuth_sweep_step_deg: float = sv.AZIMUTH_SWEEP_STEP_DEG
    wind_from_deg: Optional[float] = None
    pattern: str = sv.PATTERN_BOUSTROPHEDON
    double_grid: bool = False
    #: Width of the swath to image, centred on the axis, for a corridor
    #: mission. Zero -- the default -- means the AOI is an area and the
    #: strips are a grid over it.
    corridor_width_m: float = 0.0
    user_margin_m: float = 0.0
    lead_in_m: float = 0.0

    v_mission_ms: Optional[float] = None
    blur_px_max: float = K.BLUR_PX_MAX
    v_regulatory_ms: Optional[float] = None
    use_interval_trigger: bool = True

    gimbal_pitch_deg: float = -90.0
    min_photos_per_point: int = K.MIN_PHOTOS_PER_POINT
    max_legal_agl_m: float = K.MAX_LEGAL_AGL_M

    vertical_datum: str = VerticalDatum.UNKNOWN
    geoid_undulation_m: float = 0.0

    home_xy: Optional[tuple] = None
    compute_footprints: bool = True
    footprint_edge_samples: int = 2
    #: Above this many exposures, footprint ray casting is skipped rather than
    #: freezing the GUI; the report says so.
    max_footprints: int = 4000

    def __post_init__(self) -> None:
        if (self.h_agl_m is None) == (self.gsd_m is None):
            raise MissionError(
                "provide exactly one of h_agl_m or gsd_m",
                user_message="Indica la quota di volo oppure il GSD target, "
                             "non entrambi.")
        if self.altitude_mode not in AltitudeMode.ALL:
            raise MissionError(
                "unknown altitude mode {0!r}".format(self.altitude_mode),
                user_message="Modalita' di quota non riconosciuta.")

    @property
    def v_target_ms(self) -> float:
        return (self.v_mission_ms if self.v_mission_ms is not None
                else self.drone.v_cruise_ms)


@dataclass
class _LegResult:
    waypoints: "list[Waypoint]"
    photos: "list[PhotoCenter]"
    polyline: np.ndarray
    profile_rows: "list[dict]"
    time_s: float
    warnings: "list[str]" = field(default_factory=list)


def build_mission(aoi_geom, terrain: TerrainModel, params: MissionParams,
                  crs_authid: str = "", feedback=None) -> Mission:
    """Assemble a complete mission for one AOI block."""
    warnings: "list[str]" = []
    assumptions: "list[str]" = []

    # -- 1. photogrammetric geometry --------------------------------------
    geometry = pg.solve_survey_geometry(
        params.camera, params.overlap, h_agl_m=params.h_agl_m,
        gsd_m_px=params.gsd_m, orientation=params.orientation)

    budget = pg.build_speed_budget(
        geometry, v_mission_ms=params.v_target_ms,
        v_drone_max_ms=params.drone.v_max_ms, blur_px_max=params.blur_px_max,
        v_regulatory_ms=params.v_regulatory_ms,
        use_interval_trigger=params.use_interval_trigger)

    if budget.is_capped_below_request:
        warnings.append(
            "Velocita' ridotta da {0:.1f} a {1:.1f} m/s dal vincolo '{2}'."
            .format(params.v_target_ms, budget.effective, budget.binding))

    # -- 2. route ----------------------------------------------------------
    route_kwargs = dict(
        d_side_m=geometry.d_side_m, d_front_m=geometry.d_front_m,
        footprint_across_m=geometry.footprint_across_m,
        footprint_along_m=geometry.footprint_along_m,
        terrain=terrain, user_margin_m=params.user_margin_m,
        pattern=params.pattern, turn_radius_m=params.drone.turn_radius_m,
        lead_in_m=params.lead_in_m)

    is_corridor = params.corridor_width_m > 0.0
    strategy = params.azimuth_strategy
    manual_azimuth = params.manual_azimuth_deg
    azimuth_scores = []
    if is_corridor:
        # The axis IS the orientation: there is nothing to choose and
        # nothing to sweep.
        strategy = sv.AZIMUTH_MANUAL
    elif strategy == sv.AZIMUTH_OPTIMISED and not params.double_grid:
        # Solved here and not inside plan_route: the sweep needs the strip
        # spacing and the footprint, which only exist once the
        # photogrammetric geometry above has been solved.
        manual_azimuth, note, azimuth_scores = sv.optimise_azimuth(
            aoi_geom, budget.effective,
            step_deg=params.azimuth_sweep_step_deg, **route_kwargs)
        strategy = sv.AZIMUTH_MANUAL
        assumptions.append("Azimut ottimizzato: {0}.".format(note))
    elif strategy == sv.AZIMUTH_OPTIMISED:
        strategy = sv.AZIMUTH_LONGEST_SIDE
        warnings.append(
            "Doppia griglia: l'ottimizzazione dell'azimut non si applica, "
            "le due passate sono ortogonali per definizione.")

    if is_corridor:
        plans = [sv.plan_corridor(
            aoi_geom, d_side_m=geometry.d_side_m,
            d_front_m=geometry.d_front_m,
            corridor_width_m=params.corridor_width_m,
            footprint_across_m=geometry.footprint_across_m,
            user_margin_m=params.user_margin_m)]
        if params.double_grid:
            warnings.append(
                "Corridoio: la doppia griglia non si applica, le strisciate "
                "seguono l'asse.")
    elif params.double_grid:
        plans = sv.plan_double_grid(aoi_geom, **route_kwargs)
    else:
        plans = [sv.plan_route(
            aoi_geom, azimuth_strategy=strategy,
            manual_azimuth_deg=manual_azimuth,
            wind_from_deg=params.wind_from_deg, **route_kwargs)]
    for plan in plans:
        warnings.extend(plan.warnings)

    # Everything downstream that asks "how much ground is this" -- the
    # terrain statistics, the coverage check, the hectares in the report --
    # needs a surface. A corridor's surface is the swath it images, which is
    # the axis widened by the width asked for plus the half footprint the
    # strips are laid out with.
    survey_geom = aoi_geom
    if is_corridor:
        swath = 0.5 * (params.corridor_width_m + geometry.footprint_across_m)
        widened = aoi_geom.buffer(swath + params.user_margin_m, 12)
        if widened is not None and not widened.isEmpty():
            survey_geom = widened

    # -- 3. altitude reference --------------------------------------------
    aoi_z_min, aoi_z_max, aoi_z_mean = _terrain_stats_over(terrain,
                                                           survey_geom)
    relief = aoi_z_max - aoi_z_min
    lift = params.h_agl_m if params.h_agl_m is not None else geometry.h_agl_m
    lift += params.safety_margin_m + params.vegetation_clearance_m

    if params.altitude_mode == AltitudeMode.SINGLE_AMSL:
        if not math.isfinite(relief):
            raise MissionError(
                "cannot evaluate terrain relief for a single-AMSL mission",
                user_message="Impossibile valutare il dislivello dell'area.")
        if relief >= 0.10 * geometry.h_agl_m:
            raise MissionError(
                "relief {0:.1f} m is {1:.0f} % of H_AGL".format(
                    relief, 100.0 * relief / geometry.h_agl_m),
                user_message=(
                    "Quota AMSL unica non ammessa: il dislivello dell'area e' "
                    "{0:.1f} m, pari al {1:.0f} % della quota di volo "
                    "({2:.0f} m). Il limite e' il 10 %.".format(
                        relief, 100.0 * relief / geometry.h_agl_m,
                        geometry.h_agl_m)),
                hint=("Usa il terrain following, oppure una quota AMSL per "
                      "singola strip."))
        assumptions.append(
            "Quota AMSL unica ammessa: dislivello {0:.1f} m < 10 % di H_AGL."
            .format(relief))

    sample_step = tf.sample_step_for(geometry.d_front_m, terrain.cellsize)

    # -- 4. legs -----------------------------------------------------------
    all_waypoints: "list[Waypoint]" = []
    all_photos: "list[PhotoCenter]" = []
    lines: "list[np.ndarray]" = []
    profile_rows: "list[dict]" = []
    leg_times: "list[float]" = []
    #: (waypoint_start, waypoint_count, photo_start, photo_count) per leg.
    leg_spans: "list[tuple]" = []
    photo_id = 0
    total_legs = sum(len(p.legs) for p in plans)
    done = 0

    for plan in plans:
        for leg in plan.legs:
            if feedback is not None and feedback.isCanceled():
                raise MissionError(
                    "cancelled by the operator",
                    user_message="Operazione annullata.")
            result = _build_leg(leg, plan, terrain, geometry, params, budget,
                                sample_step, lift, aoi_z_mean, photo_id,
                                len(all_waypoints))
            leg_spans.append((len(all_waypoints), len(result.waypoints),
                              len(all_photos), len(result.photos)))
            photo_id += len(result.photos)
            all_waypoints.extend(result.waypoints)
            all_photos.extend(result.photos)
            lines.append(result.polyline)
            profile_rows.extend(result.profile_rows)
            leg_times.append(result.time_s)
            warnings.extend(result.warnings)
            done += 1
            if feedback is not None and total_legs:
                feedback.setProgress(60.0 * done / total_legs)

    if not all_waypoints:
        raise MissionError(
            "no waypoints were generated",
            user_message="La missione non contiene waypoint.",
            hint="Verifica che l'area sia coperta dal DEM e dalle strip.")

    # -- 5. transits, timing, battery split -------------------------------
    transit_time, transit_length = _transit_cost(lines, budget.effective,
                                                 params.drone)
    flight_time = sum(leg_times) + transit_time \
        + K.TURN_PENALTY_S * max(len(lines) - 1, 0)

    n_sub = _assign_sub_missions(all_waypoints, all_photos, leg_spans,
                                 leg_times, params.drone, warnings)
    flight_time += K.TAKEOFF_LANDING_S * n_sub

    # -- 6. footprints -----------------------------------------------------
    footprints: "list[np.ndarray]" = []
    if params.compute_footprints and all_photos:
        if len(all_photos) > params.max_footprints:
            warnings.append(
                "Impronte a terra non calcolate: {0} foto superano il limite "
                "di {1}. Copertura e GSD effettivo non verificati sul DEM."
                .format(len(all_photos), params.max_footprints))
        else:
            footprints = _drape_all(terrain, all_photos, params, geometry,
                                    feedback)

    # -- 7. statistics -----------------------------------------------------
    stats = _compute_stats(survey_geom, plans, all_waypoints, all_photos,
                           lines, geometry, flight_time, n_sub,
                           aoi_z_min, aoi_z_max, transit_length)

    assumptions.extend(_assumptions(params, geometry, terrain, budget,
                                    sample_step, aoi_z_max))

    mission = Mission(
        crs_authid=crs_authid or terrain.crs_authid,
        vertical_datum=params.vertical_datum,
        altitude_mode=params.altitude_mode,
        h_agl_m=geometry.h_agl_m,
        gsd_m=geometry.gsd_m_px,
        frontlap=params.overlap.frontlap,
        sidelap=params.overlap.sidelap,
        azimuth_deg=plans[0].azimuth_deg,
        pattern=plans[0].pattern,
        speed_ms=budget.effective,
        camera_key=params.camera.name,
        drone_key=params.drone.key,
        safety_margin_m=params.safety_margin_m,
        vegetation_clearance_m=params.vegetation_clearance_m,
        geoid_undulation_m=params.geoid_undulation_m,
        waypoints=all_waypoints, photos=all_photos, lines=lines,
        footprints=footprints, profile=profile_rows, stats=stats,
        azimuth_scores=azimuth_scores,
        warnings=warnings, assumptions=assumptions)
    return mission


# --------------------------------------------------------------------------
# Leg construction
# --------------------------------------------------------------------------

def _build_leg(leg, plan, terrain, geometry, params, budget, sample_step,
               lift, aoi_z_mean, photo_id_start, seq_start) -> _LegResult:
    """Drape one strip, place its exposures, thin and speed-limit it."""
    warnings: "list[str]" = []
    polyline = leg.fly_endpoints_xy()
    chain = cumulative_distance(polyline)
    total = float(chain[-1])
    if total <= 0:
        return _LegResult([], [], polyline, [], 0.0)

    # Exposure chainages: exact multiples of D_front inside the photo range.
    lead = abs(leg.s_photo_start - leg.s_fly_start)
    photo_span = leg.photo_length
    n_photos = int(math.floor(photo_span / geometry.d_front_m + 1e-9)) + 1
    photo_chain = lead + np.arange(n_photos) * geometry.d_front_m
    photo_chain = photo_chain[(photo_chain >= -1e-9) & (photo_chain <= total + 1e-9)]

    profile = tf.build_flight_profile(
        terrain, polyline, geometry.h_agl_m, sample_step,
        safety_margin_m=params.safety_margin_m,
        vegetation_clearance_m=params.vegetation_clearance_m,
        extra_chainages=photo_chain)

    # -- altitude mode ----------------------------------------------------
    if params.altitude_mode == AltitudeMode.STRIP_AMSL:
        mean_z = float(np.nanmean(profile.z_terrain))
        if math.isfinite(mean_z):
            profile.z_flight = np.full_like(profile.z_flight, mean_z + lift)
    elif params.altitude_mode == AltitudeMode.SINGLE_AMSL:
        profile.z_flight = np.full_like(profile.z_flight, aoi_z_mean + lift)

    warnings.extend(tf.fill_profile_gaps(profile, params.gap_mode))

    # -- thin, never dropping an exposure ---------------------------------
    photo_idx = np.searchsorted(profile.s, photo_chain)
    photo_idx = np.clip(photo_idx, 0, profile.s.size - 1)
    keep = tf.densify_waypoints(profile, params.dz_tolerance_m,
                                forced_indices=photo_idx)

    xy = profile.xy[keep]
    z = profile.z_flight[keep]
    z_terr = profile.z_terrain[keep]
    gaps = profile.gap_mask[keep] if profile.gap_mask is not None \
        else np.zeros(keep.size, dtype=bool)

    # -- speed capped by the terrain gradient -----------------------------
    kin = tf.apply_climb_limits(xy, z, budget.effective,
                                params.drone.climb_ms, params.drone.descent_ms,
                                K.MIN_SPEED_MS)
    if kin.infeasible.any():
        warnings.append(
            "Strip {0}: {1} tratti richiedono una velocita' inferiore a "
            "{2:g} m/s per rispettare il rateo di salita. Alza H_AGL oppure "
            "orienta le strip lungo le curve di livello.".format(
                leg.strip_index, int(kin.infeasible.sum()), K.MIN_SPEED_MS))

    speeds = np.concatenate([kin.speed_ms, kin.speed_ms[-1:]]) \
        if kin.speed_ms.size else np.full(xy.shape[0], budget.effective)
    climb_flag = np.concatenate([kin.climb_limited, kin.climb_limited[-1:]]) \
        if kin.climb_limited.size else np.zeros(xy.shape[0], dtype=bool)
    headings = headings_along(xy)

    # densify_waypoints is guaranteed to retain every forced index, so each
    # exposure maps to exactly one waypoint. Build the reverse map in O(n)
    # rather than searching the kept array once per exposure.
    position_of = {int(v): i for i, v in enumerate(keep)}
    photo_positions = {position_of[int(i)] for i in photo_idx
                       if int(i) in position_of}

    waypoints = []
    for i in range(xy.shape[0]):
        is_photo = i in photo_positions
        waypoints.append(Waypoint(
            seq=seq_start + i,
            x=float(xy[i, 0]), y=float(xy[i, 1]),
            z_amsl=float(z[i]),
            z_agl=float(z[i] - z_terr[i]) if math.isfinite(z_terr[i]) else math.nan,
            heading_deg=float(headings[i]),
            gimbal_pitch_deg=params.gimbal_pitch_deg,
            speed_ms=float(speeds[i]),
            kind="photo" if is_photo else "nav",
            actions=["takePhoto"] if is_photo else [],
            strip_index=int(leg.strip_index),
            dem_gap=bool(gaps[i]),
            climb_limited=bool(climb_flag[i]),
        ))

    # -- exposure stations -------------------------------------------------
    photos = []
    for k, idx in enumerate(photo_idx):
        idx = int(idx)
        z_cam = float(profile.z_flight[idx])
        z_gnd = float(profile.z_terrain[idx])
        agl = z_cam - z_gnd if math.isfinite(z_gnd) else geometry.h_agl_m
        omega, phi, kappa = pg.opk_from_yaw_pitch(
            leg.azimuth_deg, params.gimbal_pitch_deg)
        photos.append(PhotoCenter(
            photo_id=photo_id_start + k,
            seq=k,
            x=float(profile.xy[idx, 0]), y=float(profile.xy[idx, 1]),
            z_amsl=z_cam, z_agl=agl,
            heading_deg=float(leg.azimuth_deg),
            gimbal_pitch_deg=params.gimbal_pitch_deg,
            omega_deg=omega, phi_deg=phi, kappa_deg=kappa,
            gsd_m=geometry.gsd_at_agl(agl) if agl > 0 else math.nan,
            strip_index=int(leg.strip_index),
        ))

    rows = [{"s": float(profile.s[i]), "x": float(profile.xy[i, 0]),
             "y": float(profile.xy[i, 1]),
             "z_ground": float(profile.z_terrain[i]),
             "z_flight": float(profile.z_flight[i]),
             "agl": float(profile.z_flight[i] - profile.z_terrain[i]),
             "strip": int(leg.strip_index)}
            for i in range(0, profile.s.size)]

    time_s = tf.segment_flight_time(xy, z, kin.speed_ms) if kin.speed_ms.size else 0.0
    poly3 = np.column_stack([xy[:, 0], xy[:, 1], z])
    return _LegResult(waypoints, photos, poly3, rows, time_s, warnings)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _terrain_stats_over(terrain: TerrainModel, geom):
    """(z_min, z_max, z_mean) of the terrain inside the AOI bounding box."""
    box = geom.boundingBox()
    x0, dx, _, y0, _, dy = terrain.gt
    c0 = max(int(math.floor((box.xMinimum() - x0) / dx)), 0)
    c1 = min(int(math.ceil((box.xMaximum() - x0) / dx)), terrain.cols)
    r0 = max(int(math.floor((y0 - box.yMaximum()) / (-dy))), 0)
    r1 = min(int(math.ceil((y0 - box.yMinimum()) / (-dy))), terrain.rows)
    if c1 <= c0 or r1 <= r0:
        return math.nan, math.nan, math.nan
    window = terrain.z[r0:r1, c0:c1]
    finite = window[np.isfinite(window)]
    if finite.size == 0:
        return math.nan, math.nan, math.nan
    return float(finite.min()), float(finite.max()), float(finite.mean())


def _transit_cost(lines, speed_ms, drone: DroneProfile):
    """Time and distance flown between the end of a leg and the next start."""
    total = 0.0
    for a, b in zip(lines, lines[1:]):
        end, start = a[-1], b[0]
        total += float(math.sqrt((start[0] - end[0]) ** 2
                                 + (start[1] - end[1]) ** 2
                                 + (start[2] - end[2]) ** 2))
    speed = max(speed_ms, 0.1)
    return total / speed, total


def _assign_sub_missions(waypoints, photos, leg_spans, leg_times,
                         drone: DroneProfile, warnings) -> int:
    """Split on endurance and on the controller waypoint limit.

    Cuts fall on strip boundaries, never mid-strip. ``leg_spans`` is recorded
    while the legs are built rather than re-derived from ``strip_index``: a
    concave AOI splits one strip into two consecutive legs that share a strip
    index, and grouping by that index would merge them and misalign the leg
    timings.
    """
    budget_s = drone.usable_endurance_s
    limit = drone.waypoint_limit

    sub = 0
    acc_time = 0.0
    acc_wp = 0
    for i, (wp_start, wp_count, ph_start, ph_count) in enumerate(leg_spans):
        leg_time = leg_times[i] if i < len(leg_times) else 0.0
        over_time = acc_time + leg_time > budget_s
        over_wp = acc_wp + wp_count > limit
        if acc_wp > 0 and (over_time or over_wp):
            sub += 1
            acc_time, acc_wp = leg_time, wp_count
        else:
            acc_time += leg_time
            acc_wp += wp_count
        for wp in waypoints[wp_start:wp_start + wp_count]:
            wp.sub_mission = sub
        for photo in photos[ph_start:ph_start + ph_count]:
            photo.sub_mission = sub

    n_sub = sub + 1
    if n_sub > 1:
        warnings.append(
            "Missione divisa in {0} sotto-missioni ({1:.0f} min utili per "
            "batteria, limite {2} waypoint). Prevedi una strip in comune fra "
            "blocchi consecutivi per la ricucitura.".format(
                n_sub, budget_s / 60.0, limit))
    return n_sub


def _drape_all(terrain, photos, params, geometry, feedback):
    """Ray-cast every exposure footprint onto the DEM."""
    footprints = []
    for i, photo in enumerate(photos):
        if feedback is not None and feedback.isCanceled():
            break
        footprints.append(tf.drape_footprint(
            terrain, (photo.x, photo.y, photo.z_amsl), photo.heading_deg,
            params.camera, params.orientation, photo.gimbal_pitch_deg,
            samples_per_edge=params.footprint_edge_samples))
        if feedback is not None and i % 50 == 0 and photos:
            feedback.setProgress(60.0 + 30.0 * i / len(photos))
    return footprints


def _compute_stats(aoi_geom, plans, waypoints, photos, lines, geometry,
                   flight_time, n_sub, z_min, z_max, transit_length):
    agl = np.array([wp.z_agl for wp in waypoints], dtype=float)
    agl = agl[np.isfinite(agl)]
    gsd = np.array([p.gsd_m for p in photos], dtype=float)
    gsd = gsd[np.isfinite(gsd)]
    survey_len = sum(p.survey_length_m() for p in plans)

    return MissionStats(
        aoi_area_m2=float(aoi_geom.area()),
        n_strips=sum(p.n_strips for p in plans),
        n_photos=len(photos),
        n_waypoints=len(waypoints),
        n_turns=sum(p.n_turns for p in plans),
        survey_length_m=survey_len,
        transit_length_m=transit_length,
        total_length_m=survey_len + transit_length,
        flight_time_s=flight_time,
        n_batteries=n_sub,
        terrain_z_min=z_min, terrain_z_max=z_max,
        agl_min=float(agl.min()) if agl.size else math.nan,
        agl_max=float(agl.max()) if agl.size else math.nan,
        gsd_min_m=float(gsd.min()) if gsd.size else math.nan,
        gsd_max_m=float(gsd.max()) if gsd.size else math.nan,
    )


def _assumptions(params, geometry, terrain, budget, sample_step, z_max):
    """Everything the operator must be able to check after the fact."""
    out = [
        "CRS orizzontale di lavoro: {0}.".format(terrain.crs_authid or "n/d"),
        "Datum verticale dichiarato: {0}.".format(
            VerticalDatum.label(params.vertical_datum)),
        "Modello di elevazione: {0} ({1}), cella {2:.2f} m.".format(
            "DSM (superficie)" if terrain.is_surface_model else "DTM (terreno nudo)",
            terrain.source or "n/d", terrain.cellsize),
        "Passo di campionamento DEM lungo le strip: {0:.2f} m "
        "= min(D_front, cella DEM, {1:g} m).".format(sample_step,
                                                     K.MAX_SAMPLE_STEP_M),
        "Tolleranza verticale per la densificazione dei waypoint: {0:g} m."
        .format(params.dz_tolerance_m),
        "Camera: {0}.".format(params.camera.name),
        "Sorgente parametri camera: {0}".format(
            params.camera.source or "non dichiarata"),
        "Velocita' effettiva {0:.1f} m/s, vincolo determinante '{1}'.".format(
            budget.effective, budget.binding),
        "Orientamento esterno omega/phi/kappa: convenzione ENU destrorsa, "
        "R = Rx(omega) Ry(phi) Rz(kappa), asse z immagine opposto alla "
        "direzione di vista. Verificare contro il software fotogrammetrico.",
        "Quota RTH consigliata: {0:.0f} m (max terreno + {1:g} m).".format(
            z_max + K.RTH_CLEARANCE_M if math.isfinite(z_max) else math.nan,
            K.RTH_CLEARANCE_M),
    ]
    if not terrain.is_surface_model and params.vegetation_clearance_m <= 0:
        out.append(
            "ATTENZIONE: volo su DTM senza clearance vegetazione. In area "
            "boscata o urbana il DTM non conosce chiome ed edifici.")
    if params.geoid_undulation_m == 0.0 and VerticalDatum.is_orthometric(
            params.vertical_datum):
        out.append(
            "Ondulazione del geoide impostata a 0.00 m: le quote ellissoidiche "
            "esportate coincidono con quelle ortometriche. Inserire il valore "
            "locale se il firmware richiede quote ellissoidiche.")
    return out
