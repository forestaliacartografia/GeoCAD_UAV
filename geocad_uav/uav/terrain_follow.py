"""
Terrain following: AGL-constant flight profiles, waypoint densification,
climb-rate limiting and terrain-draped footprints.

Raster access itself lives in ``core.z``; this module is the flight mechanics
layered on top of it.

The governing rule (P2) is that the route is NOT flown at a constant AMSL:

    Z_waypoint(x, y) = Z_raster(x, y) + H_AGL + safety_margin

Holding a fixed AMSL over a slope makes both the GSD and the overlap collapse
in the valleys and degrade on the ridges; terrain following exists precisely to
keep them constant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..core.planar import cumulative_distance, densify_ring, resample_polyline
from ..core.z import TerrainError, TerrainModel, raycast_to_terrain
from .photogrammetry import Camera

#: Terrain-following defaults. All overridable; every one is printed in the
#: mission report, so nothing here is a hidden magic number.
DEFAULT_DZ_TOLERANCE_M = 2.0            # vertical densification tolerance
DEFAULT_SAFETY_MARGIN_M = 0.0           # added on top of H_AGL
DEFAULT_VEGETATION_CLEARANCE_M = 15.0   # added when flying a DTM under canopy
DEFAULT_CLIMB_RATE_MS = 4.0
DEFAULT_DESCENT_RATE_MS = 3.0
DEFAULT_MAX_SAMPLE_STEP_M = 5.0
DEFAULT_MIN_SPEED_MS = 1.0              # below this the mission is impractical


# --------------------------------------------------------------------------
# Flight profile
# --------------------------------------------------------------------------

@dataclass
class FlightProfile:
    """A terrain-following profile along one polyline.

    All arrays share the same length N and the same chainage ``s``.
    """

    xy: np.ndarray               # (N, 2) world coordinates
    s: np.ndarray                # (N,) chainage [m]
    z_terrain: np.ndarray        # (N,) sampled elevation, NaN over no-data
    z_flight: np.ndarray         # (N,) commanded elevation, same datum
    h_agl_nominal: float
    gap_mask: np.ndarray = field(default=None)   # (N,) True where DEM is no-data

    @property
    def agl(self):
        """Actual AGL along the profile (NaN where the DEM has no data)."""
        return self.z_flight - self.z_terrain

    @property
    def has_gaps(self) -> bool:
        return bool(self.gap_mask is not None and self.gap_mask.any())

    def terrain_range(self):
        finite = self.z_terrain[np.isfinite(self.z_terrain)]
        if finite.size == 0:
            return (math.nan, math.nan)
        return (float(finite.min()), float(finite.max()))


def sample_step_for(d_front_m: float, cellsize_m: float,
                    hard_cap_m: float = DEFAULT_MAX_SAMPLE_STEP_M) -> float:
    """DEM sampling step along a strip: min(D_front, DEM cell, 5 m).

    Sampling any coarser than the DEM cell throws away the terrain detail the
    aircraft actually has to fly over; sampling coarser than D_front means a
    photo could sit between two samples with an unmodelled AGL.
    """
    step = min(d_front_m, cellsize_m, hard_cap_m)
    return max(step, 0.1)


def build_flight_profile(terrain: TerrainModel, polyline, h_agl_m: float,
                         step_m: float, safety_margin_m: float = 0.0,
                         vegetation_clearance_m: float = 0.0,
                         extra_chainages=None) -> FlightProfile:
    """Sample the DEM along ``polyline`` and lift it to a constant AGL.

        Z_flight(x, y) = Z_terrain(x, y) + H_AGL + safety_margin + clearance

    ``vegetation_clearance_m`` is the extra height added when flying a bare
    earth DTM over terrain that actually has canopy or buildings on it -- the
    DTM does not know they are there.

    ``extra_chainages`` forces exact samples at the given distances along the
    line; photo stations use it so an exposure is never snapped to the nearest
    terrain sample.
    """
    pts = resample_polyline(np.asarray(polyline, dtype=float)[:, :2], step_m,
                            extra_chainages=extra_chainages)
    z_terr = terrain.sample(pts[:, 0], pts[:, 1])
    lift = h_agl_m + safety_margin_m + vegetation_clearance_m
    z_flight = z_terr + lift
    return FlightProfile(
        xy=pts,
        s=cumulative_distance(pts),
        z_terrain=z_terr,
        z_flight=z_flight,
        h_agl_nominal=h_agl_m,
        gap_mask=~np.isfinite(z_terr),
    )


def fill_profile_gaps(profile: FlightProfile, mode: str = "hold_max",
                      terrain=None):
    """Give the aircraft a defined height across DEM holes.

    The DEM is *not* extrapolated; instead the commanded height across a gap is
    made explicitly conservative and the gap stays flagged in ``gap_mask`` so
    the report and the QA layers can show it.

    ``terrain`` is the model the profile was sampled from. Given it, a
    profile with nothing usable in it can say whether the samples fell off
    the grid or onto no-data inside it -- two different problems with two
    different answers, and the message used to assume the first.

    ``mode``:
        ``"hold_max"``   -- fly the highest commanded height of the surrounding
                            valid data across the whole gap (default, safest).
        ``"interpolate"`` -- linear ramp between the gap edges. Only sensible
                            over small holes in otherwise smooth terrain.
        ``"none"``       -- leave NaN; the mission will refuse to export.

    Returns a list of warnings.
    """
    z = profile.z_flight
    gaps = ~np.isfinite(z)
    if not gaps.any():
        return []
    if mode == "none":
        return ["{0} profile samples fall on DEM no-data and were left "
                "undefined.".format(int(gaps.sum()))]

    valid = ~gaps
    if not valid.any():
        raise TerrainError(_no_usable_terrain(profile, terrain))

    if mode == "interpolate":
        z[gaps] = np.interp(profile.s[gaps], profile.s[valid], z[valid])
        note = "linearly interpolated across"
    elif mode == "hold_max":
        z[gaps] = float(np.nanmax(z[valid]))
        note = "raised to the local maximum flight height over"
    else:
        raise TerrainError("unknown gap fill mode {0!r}".format(mode))

    return ["{0} profile samples fall on DEM no-data; commanded height was {1} "
            "them. These segments are flagged in the output layers and must be "
            "checked before flying.".format(int(gaps.sum()), note)]


def _no_usable_terrain(profile: FlightProfile, terrain=None) -> str:
    """Why not one sample of this profile has a height under it.

    Counted, not assumed: off the grid and no-data inside it are different
    problems, and the operator is the one who has to fix whichever it is.
    """
    total = int(profile.z_flight.size)
    if terrain is None:
        return ("No sample of the route has an elevation under it "
                "({0} samples, none valid). The elevation model could not be "
                "read for this route: check that the DEM covers the area and "
                "that the layer's CRS is the right one.".format(total))
    report = terrain.sample_report(profile.xy[:, 0], profile.xy[:, 1])
    xs, ys = profile.xy[:, 0], profile.xy[:, 1]
    route_box = (float(np.nanmin(xs)), float(np.nanmin(ys)),
                 float(np.nanmax(xs)), float(np.nanmax(ys)))
    head = ("No sample of the route has an elevation under it: "
            "{0} samples, {1} valid, {2} on DEM no-data, {3} outside the "
            "grid.".format(report["samples"], report["valid"],
                           report["nodata"], report["outside"]))
    where = ("\n  route extent ({0}): ({1:,.1f}, {2:,.1f}) - "
             "({3:,.1f}, {4:,.1f})"
             "\n  DEM window    ({0}): ({5:,.1f}, {6:,.1f}) - "
             "({7:,.1f}, {8:,.1f})".format(
                 report["crs"] or "CRS di lavoro", route_box[0], route_box[1],
                 route_box[2], route_box[3], *report["extent"]))
    if report["outside"] and not report["nodata"]:
        why = ("\nThe route lies outside the elevation window. Both "
               "rectangles above are in the same CRS.")
    elif report["nodata"] and not report["outside"]:
        why = ("\nThe route lies inside the elevation window, but every "
               "cell under it is no-data: this is a hole in the data, "
               "not a coverage problem.")
    else:
        why = ("\nPart of the route is off the grid and part is on "
               "no-data.")
    return head + where + why


# --------------------------------------------------------------------------
# Waypoint densification
# --------------------------------------------------------------------------

def simplify_vertical(s, z, tolerance_m: float):
    """Douglas-Peucker on a (chainage, height) profile, VERTICAL deviation.

    Classic Douglas-Peucker measures perpendicular distance, which mixes metres
    of chainage with metres of height and under-reports the height error on
    steep ground. What actually matters here is different: the aircraft flies a
    straight line between consecutive waypoints, so the error at a dropped
    sample is exactly its *vertical* distance from that chord. That is what is
    measured, so ``tolerance_m`` means precisely "the flown path stays within
    this many metres of the commanded terrain-following profile".

    Returns a sorted index array of the samples to keep (always including the
    first and last).
    """
    s = np.asarray(s, dtype=float)
    z = np.asarray(z, dtype=float)
    n = s.size
    if n <= 2:
        return np.arange(n)
    if tolerance_m <= 0:
        return np.arange(n)

    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        sa, sb = s[a], s[b]
        za, zb = z[a], z[b]
        span = sb - sa
        idx = np.arange(a + 1, b)
        if span <= 0:
            chord = np.full(idx.size, za)
        else:
            chord = za + (zb - za) * (s[idx] - sa) / span
        dev = np.abs(z[idx] - chord)
        dev = np.where(np.isfinite(dev), dev, np.inf)   # keep gap edges
        if dev.size == 0:
            continue
        k = int(np.argmax(dev))
        if dev[k] > tolerance_m:
            pivot = a + 1 + k
            keep[pivot] = True
            stack.append((a, pivot))
            stack.append((pivot, b))
    return np.flatnonzero(keep)


def densify_waypoints(profile: FlightProfile, dz_tolerance_m: float,
                      forced_indices=None):
    """Pick the waypoint subset that reproduces the profile within dz.

    Flat ground collapses to two waypoints; broken ground keeps as many as it
    needs. ``forced_indices`` are always retained -- used to pin photo stations
    and strip ends so they never get simplified away.
    """
    keep = simplify_vertical(profile.s, profile.z_flight, dz_tolerance_m)
    if forced_indices is not None and len(forced_indices) > 0:
        keep = np.union1d(keep, np.asarray(forced_indices, dtype=np.int64))
    return keep.astype(np.int64)


# --------------------------------------------------------------------------
# Kinematics
# --------------------------------------------------------------------------

@dataclass
class SegmentKinematics:
    """Per-segment speed after applying the vertical-rate constraint."""

    speed_ms: np.ndarray          # (N-1,) commanded ground speed
    climb_limited: np.ndarray     # (N-1,) True where the terrain capped it
    infeasible: np.ndarray        # (N-1,) True where even v_min is too fast
    required_rate_ms: np.ndarray  # (N-1,) vertical rate at the commanded speed


def apply_climb_limits(xy, z, v_target_ms: float,
                       climb_rate_ms: float = DEFAULT_CLIMB_RATE_MS,
                       descent_rate_ms: float = DEFAULT_DESCENT_RATE_MS,
                       v_min_ms: float = DEFAULT_MIN_SPEED_MS
                       ) -> SegmentKinematics:
    """Cap ground speed per segment so the vertical rate stays achievable.

    Where the terrain is steeper than the airframe can climb, the speed is
    reduced -- the survey is never cut short and no waypoint is dropped, per
    the terrain-following requirement. Segments that would still be infeasible
    at ``v_min_ms`` are flagged so the report can tell the operator to raise
    H_AGL or turn the strips along the contours instead.
    """
    xy = np.asarray(xy, dtype=float)[:, :2]
    z = np.asarray(z, dtype=float)
    if xy.shape[0] < 2:
        empty = np.zeros(0)
        return SegmentKinematics(empty, empty.astype(bool),
                                 empty.astype(bool), empty)

    d = np.hypot(*np.diff(xy, axis=0).T)
    dz = np.diff(z)
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = np.where(dz > 0, climb_rate_ms, descent_rate_ms)
        v_climb = np.where(np.abs(dz) < 1e-9, np.inf, rate * d / np.abs(dz))
    v_climb = np.where(d <= 0, np.inf, v_climb)
    v_climb = np.where(np.isfinite(v_climb), v_climb, np.inf)

    speed = np.minimum(v_target_ms, v_climb)
    climb_limited = v_climb < v_target_ms - 1e-9
    infeasible = speed < v_min_ms - 1e-9
    speed = np.maximum(speed, v_min_ms)

    with np.errstate(divide="ignore", invalid="ignore"):
        required = np.where(d > 0, speed * np.abs(dz) / d, 0.0)
    return SegmentKinematics(speed, climb_limited, infeasible,
                             np.nan_to_num(required))


def segment_flight_time(xy, z, speed_ms) -> float:
    """Time [s] to fly a polyline at per-segment speeds, using 3-D length."""
    xy = np.asarray(xy, dtype=float)[:, :2]
    z = np.asarray(z, dtype=float)
    if xy.shape[0] < 2:
        return 0.0
    d3 = np.sqrt(np.sum(np.diff(xy, axis=0) ** 2, axis=1) + np.diff(z) ** 2)
    speed = np.asarray(speed_ms, dtype=float)
    speed = np.where(speed > 1e-6, speed, 1e-6)
    return float(np.sum(d3 / speed))


# --------------------------------------------------------------------------
# Terrain-draped footprints
# --------------------------------------------------------------------------


def camera_axes(azimuth_deg: float, gimbal_pitch_deg: float,
                roll_deg: float = 0.0):
    """World-frame (x_img, y_img, z_img) unit vectors for a gimballed camera.

    Same convention as :func:`photogrammetry.opk_from_yaw_pitch`: +z_img points
    away from the scene, so the viewing direction is ``-z_img``.
    """
    tau = math.radians(90.0 + gimbal_pitch_deg)
    az = math.radians(azimuth_deg)
    rol = math.radians(roll_deg)

    z_img = np.array([-math.sin(tau) * math.sin(az),
                      -math.sin(tau) * math.cos(az),
                      math.cos(tau)])
    y_img = np.array([math.cos(tau) * math.sin(az),
                      math.cos(tau) * math.cos(az),
                      math.sin(tau)])
    x_img = np.cross(y_img, z_img)

    cr, sr = math.cos(rol), math.sin(rol)
    return (cr * x_img + sr * y_img, -sr * x_img + cr * y_img, z_img)


def drape_footprint(terrain: TerrainModel, cam_xyz, azimuth_deg: float,
                    camera: Camera, orientation: str = "across",
                    gimbal_pitch_deg: float = -90.0,
                    samples_per_edge: int = 2,
                    flat_fallback_z: Optional[float] = None):
    """Ground footprint of one frame, projected onto the DEM by ray casting.

    Returns a closed (M, 3) ring. This is the polygon that coverage and overlap
    are judged on: on sloping ground the true footprint is a trapezoid that can
    be far larger downhill than the flat-plane rectangle, and using the flat
    rectangle would silently over-report coverage on exactly the terrain where
    terrain following matters most.

    Rays that miss the terrain fall back to a horizontal plane at
    ``flat_fallback_z`` (default: the elevation under the camera) so the
    footprint always closes; those vertices are geometrically approximate and
    only occur at the edge of the DEM.
    """
    cam = np.asarray(cam_xyz, dtype=float).reshape(3)
    s_across, s_along = camera.sensor_across_along_mm(orientation)
    tan_across = (s_across * 0.5) / camera.focal_mm
    tan_along = (s_along * 0.5) / camera.focal_mm

    # Unit square boundary of the image, densified along each edge.
    corners = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0],
                        [-1.0, -1.0]])
    ring_uv = densify_ring(corners, samples_per_edge)

    x_img, y_img, z_img = camera_axes(azimuth_deg, gimbal_pitch_deg)
    dirs = (ring_uv[:, 0:1] * tan_across * x_img[None, :] +
            ring_uv[:, 1:2] * tan_along * y_img[None, :] -
            z_img[None, :])

    z_ground = flat_fallback_z
    if z_ground is None:
        z_under = float(terrain.sample(cam[0], cam[1]))
        z_ground = z_under if math.isfinite(z_under) else cam[2]
    height = max(cam[2] - z_ground, 1.0)

    # March far enough to cross the terrain even on a strong downslope.
    t_max = height * 6.0 + 4.0 * terrain.cellsize
    origins = np.repeat(cam[None, :], dirs.shape[0], axis=0)
    hits = raycast_to_terrain(terrain, origins, dirs, t_max)

    # Fallback: intersect the horizontal plane z = z_ground.
    missing = ~np.isfinite(hits[:, 0])
    if missing.any():
        d = dirs[missing]
        d = d / np.linalg.norm(d, axis=1, keepdims=True)
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (z_ground - cam[2]) / d[:, 2]
        t = np.where(np.isfinite(t) & (t > 0), t, height)
        hits[missing] = cam[None, :] + d * t[:, None]

    hits[-1] = hits[0]        # close the ring exactly
    return hits
