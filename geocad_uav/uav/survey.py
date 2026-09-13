"""
Route generation: AOI preparation, strip layout, flight ordering, corridors.

Everything happens in a :class:`~.geometry.StripFrame` aligned with the flight
azimuth, which turns "parallel lines at an arbitrary angle clipped to an
arbitrary polygon" into "horizontal lines", and keeps the trigonometry in one
tested place.

This module produces the *planimetric* route only. Heights, waypoint
densification and speeds are added afterwards by ``core.terrain`` and
``core.mission`` -- the split matters, because a route that looks fine in plan
can still be unflyable once the terrain profile is applied.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..core import constants as K
from ..core.planar import StripFrame, normalize_azimuth

# Strip orientation strategies.
AZIMUTH_LONGEST_SIDE = "longest_side"      # fewest turns (default)
AZIMUTH_ACROSS_SLOPE = "across_slope"      # along the contours, steadiest GSD
AZIMUTH_ALONG_WIND = "along_wind"          # least lateral drift
AZIMUTH_OPTIMISED = "optimised"            # swept and measured, see below
AZIMUTH_MANUAL = "manual"

# Flight patterns.
PATTERN_BOUSTROPHEDON = "boustrophedon"    # adjacent strips, U-turns
PATTERN_INTERLACED = "interlaced"          # skip-strip, for a wide turn radius

DEFAULT_MIN_PHOTOS_PER_POINT = 3

#: Sweep resolution of the azimuth optimiser, and the refinement around the
#: winner. 5 deg over a half turn is 36 layouts; the refinement then walks
#: the 1 deg neighbourhood of the best, which is finer than an operator can
#: fly and far finer than the wind will let them hold.
AZIMUTH_SWEEP_STEP_DEG = 5.0
AZIMUTH_REFINE_STEP_DEG = 1.0


class RoutingError(ValueError):
    """Raised when a route cannot be generated from the given AOI."""


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class RouteLeg:
    """One traversal of one strip, in flight order.

    The photo range and the flown range are kept separate: ``s_photo_*`` is
    where the shutter fires, ``s_fly_*`` extends beyond it so the aircraft is
    wings-level and up to speed before the first frame (and has room to start
    the turn after the last).
    """

    seq: int
    strip_index: int          # geometric index across track, not flight order
    t: float                  # across-track offset in the frame
    s_photo_start: float
    s_photo_end: float
    s_fly_start: float
    s_fly_end: float
    azimuth_deg: float
    frame: StripFrame = field(repr=False, default=None)

    @property
    def reversed(self) -> bool:
        return self.s_photo_end < self.s_photo_start

    @property
    def photo_length(self) -> float:
        return abs(self.s_photo_end - self.s_photo_start)

    def fly_endpoints_xy(self):
        """(2, 2) world coordinates of the flown segment."""
        x0, y0 = self.frame.to_world(self.s_fly_start, self.t)
        x1, y1 = self.frame.to_world(self.s_fly_end, self.t)
        return np.array([[float(x0), float(y0)], [float(x1), float(y1)]])

    def photo_endpoints_xy(self):
        x0, y0 = self.frame.to_world(self.s_photo_start, self.t)
        x1, y1 = self.frame.to_world(self.s_photo_end, self.t)
        return np.array([[float(x0), float(y0)], [float(x1), float(y1)]])


@dataclass
class RoutePlan:
    """A complete planimetric route for one block."""

    frame: StripFrame
    azimuth_deg: float
    legs: "list[RouteLeg]"
    d_side_m: float
    d_front_m: float
    pattern: str
    block_index: int = 0
    warnings: "list[str]" = field(default_factory=list)

    @property
    def n_strips(self) -> int:
        return len({leg.strip_index for leg in self.legs})

    @property
    def n_turns(self) -> int:
        return max(len(self.legs) - 1, 0)

    def survey_length_m(self) -> float:
        """Total length flown on-strip (excludes transits between strips)."""
        return sum(abs(leg.s_fly_end - leg.s_fly_start) for leg in self.legs)

    def transit_length_m(self) -> float:
        """Total length flown between strips."""
        total = 0.0
        for a, b in zip(self.legs, self.legs[1:]):
            end = a.fly_endpoints_xy()[-1]
            start = b.fly_endpoints_xy()[0]
            total += float(math.hypot(*(start - end)))
        return total

    def consecutive_offset_gaps(self):
        """Across-track distance between each pair of consecutively flown legs.

        This is the lateral room the aircraft has for the turn at the end of
        each strip. A 180 deg turn of radius R needs 2R of it.
        """
        return [abs(b.t - a.t) for a, b in zip(self.legs, self.legs[1:])]

    def tight_turns(self, turn_radius_m: float):
        """Indices of the turns that do not fit ``turn_radius_m``."""
        if turn_radius_m <= 0:
            return []
        needed = 2.0 * turn_radius_m
        return [i for i, gap in enumerate(self.consecutive_offset_gaps())
                if gap < needed - 1e-9]


# --------------------------------------------------------------------------
# AOI preparation
# --------------------------------------------------------------------------

def prepare_aoi(geometries, split_multipart: bool = False):
    """Validate and normalise AOI geometries into a list of planning blocks.

    Returns ``(blocks, warnings)``. Invalid geometries are repaired with
    ``makeValid()`` rather than silently accepted, because a self-intersecting
    polygon produces nonsense strip clipping.

    ``split_multipart`` treats each part of a multipolygon as its own block
    (its own strip orientation and its own take-off), instead of planning one
    mission across all of them.
    """
    from qgis.core import QgsGeometry, QgsWkbTypes    # noqa: PLC0415

    warnings: "list[str]" = []
    cleaned = []
    for geom in geometries:
        if geom is None or geom.isEmpty():
            continue
        if QgsWkbTypes.geometryType(geom.wkbType()) != QgsWkbTypes.PolygonGeometry:
            warnings.append(
                "Skipped a non-polygon AOI feature ({0}).".format(
                    QgsWkbTypes.displayString(geom.wkbType())))
            continue
        if not geom.isGeosValid():
            fixed = geom.makeValid()
            if fixed is None or fixed.isEmpty():
                warnings.append(
                    "An AOI feature is invalid and could not be repaired; it "
                    "was skipped.")
                continue
            warnings.append(
                "An AOI feature was invalid (self-intersection or similar) and "
                "was repaired with makeValid() before planning.")
            geom = fixed
        cleaned.append(geom)

    if not cleaned:
        raise RoutingError(
            "No usable polygon found in the AOI input. Select a polygon "
            "feature, or choose a polygon layer.")

    if split_multipart:
        blocks = []
        for geom in cleaned:
            if geom.isMultipart():
                for part in geom.asGeometryCollection():
                    if not part.isEmpty():
                        blocks.append(part)
            else:
                blocks.append(geom)
    else:
        blocks = [QgsGeometry.unaryUnion(cleaned)] if len(cleaned) > 1 else cleaned
        if blocks[0].isMultipart():
            warnings.append(
                "The AOI is a multipolygon planned as a single mission; the "
                "route will transit between the parts. Enable 'one mission per "
                "part' to plan them separately.")
    return blocks, warnings


def prepare_axis(geometries):
    """Validate and merge line geometries into one corridor axis each.

    The mirror of :func:`prepare_aoi` for the linear case. Returns
    ``(axes, warnings)``. Multipart lines are merged where their ends meet,
    because an offset curve of a broken axis is a broken corridor; where
    they do not meet, each part becomes its own axis and the warning says
    so.
    """
    from qgis.core import QgsGeometry, QgsWkbTypes                # noqa: PLC0415

    warnings: "list[str]" = []
    axes = []
    for geom in geometries or []:
        if geom is None or geom.isEmpty():
            continue
        if QgsWkbTypes.geometryType(geom.wkbType()) != \
                QgsWkbTypes.LineGeometry:
            warnings.append(
                "Skipped a non-line feature ({0}) on a corridor mission."
                .format(QgsWkbTypes.displayString(geom.wkbType())))
            continue
        if geom.isMultipart():
            merged = geom.mergeLines()
            if merged is not None and not merged.isEmpty():
                geom = merged
            if geom.isMultipart():
                parts = [part for part in geom.asGeometryCollection()
                         if not part.isEmpty()]
                warnings.append(
                    "The axis is in {0} pieces that do not join; each is "
                    "planned as its own corridor.".format(len(parts)))
                axes.extend(parts)
                continue
        axes.append(QgsGeometry(geom))

    if not axes:
        raise RoutingError(
            "No usable line found for the corridor axis. Select a line "
            "feature, or choose a line layer.")
    return axes, warnings


def photogrammetric_buffer(geom, footprint_across_m: float,
                           user_margin_m: float = 0.0, segments: int = 12):
    """Expand the AOI so its edges are imaged by complete, overlapped frames.

        buffer = 0.5 * footprint_across + user_margin

    This is a photogrammetric buffer, not a cartographic one. A point exactly
    on the AOI boundary is seen by every strip within W/2 of it; without the
    half-footprint expansion the outermost strips are missing, and the boundary
    ends up single-covered instead of carrying the nominal sidelap.
    """
    distance = 0.5 * footprint_across_m + user_margin_m
    if distance <= 0:
        return geom
    buffered = geom.buffer(distance, segments)
    if buffered is None or buffered.isEmpty():
        raise RoutingError(
            "Buffering the AOI by {0:.1f} m produced an empty geometry.".format(
                distance))
    return buffered


# --------------------------------------------------------------------------
# Orientation
# --------------------------------------------------------------------------

def choose_azimuth(geom, strategy: str = AZIMUTH_LONGEST_SIDE,
                   terrain=None, aoi_mask=None,
                   manual_azimuth_deg: Optional[float] = None,
                   wind_from_deg: Optional[float] = None):
    """Pick the strip azimuth. Returns ``(azimuth_deg, note)``.

    * ``longest_side`` -- along the long axis of the oriented minimum bounding
      box: the fewest turns, so the least time wasted and the fewest places to
      lose the shot rhythm.
    * ``across_slope`` -- perpendicular to the line of maximum slope, i.e.
      along the contours. Each strip then sits at a nearly constant elevation,
      so the AGL (and the GSD) barely varies within a strip and the climb rate
      never binds.
    * ``along_wind`` -- strips parallel to the wind, which keeps the crab angle
      out of the imagery and makes the ground speed symmetric on the two
      directions of travel.
    * ``manual`` -- an explicit azimuth.

    ``optimised`` is deliberately *not* handled here: measuring orientations
    needs the strip spacing and the footprint, which this function is not
    given. :func:`optimise_azimuth` does that work and its answer arrives
    here as ``manual``.
    """
    if strategy == AZIMUTH_MANUAL:
        if manual_azimuth_deg is None:
            raise RoutingError("manual azimuth strategy needs manual_azimuth_deg")
        return normalize_azimuth(manual_azimuth_deg, 180.0), "manual azimuth"

    if strategy == AZIMUTH_ALONG_WIND:
        if wind_from_deg is None:
            raise RoutingError("along_wind strategy needs wind_from_deg")
        return (normalize_azimuth(wind_from_deg, 180.0),
                "aligned with the wind ({0:.0f} deg)".format(wind_from_deg))

    if strategy == AZIMUTH_ACROSS_SLOPE:
        if terrain is None:
            raise RoutingError("across_slope strategy needs an elevation model")
        slope_az = terrain.dominant_slope_azimuth(aoi_mask)
        if slope_az is None:
            az, _ = _azimuth_from_obb(geom)
            return az, ("terrain is too flat for a dominant slope direction; "
                        "fell back to the longest AOI side")
        return (normalize_azimuth(slope_az + 90.0, 180.0),
                "perpendicular to the dominant slope ({0:.0f} deg), i.e. along "
                "the contours".format(slope_az))

    if strategy == AZIMUTH_OPTIMISED:
        # Reached only when nobody solved it first. The sweep needs the strip
        # spacing and the footprint, which this function is not given, so
        # mission.build_mission runs the optimiser before it plans and hands
        # the answer down as a manual azimuth.
        raise RoutingError(
            "the optimised strategy must be resolved by optimise_azimuth() "
            "before plan_route() is called")

    if strategy == AZIMUTH_LONGEST_SIDE:
        az, ratio = _azimuth_from_obb(geom)
        note = "along the longest side of the oriented bounding box"
        if ratio < 1.05:
            note += " (the AOI is nearly square, so orientation barely matters)"
        return az, note

    raise RoutingError("unknown azimuth strategy {0!r}".format(strategy))


# --------------------------------------------------------------------------
# Azimuth by measurement
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AzimuthScore:
    """One candidate orientation, and what flying it would cost.

    ``time_s`` is the same estimate the mission itself reports -- track
    length over ground speed, plus one turn penalty per strip change -- so
    the number the optimiser minimises is the number the operator later
    reads on the mission, not a private score that only this function
    understands.
    """

    azimuth_deg: float
    n_strips: int
    n_turns: int
    survey_length_m: float
    transit_length_m: float
    time_s: float

    @property
    def total_length_m(self) -> float:
        return self.survey_length_m + self.transit_length_m


def score_azimuth(plan: RoutePlan, v_ms: float) -> AzimuthScore:
    """Cost one already-planned orientation."""
    if v_ms <= 0:
        raise RoutingError("v_ms must be > 0, got {0!r}".format(v_ms))
    survey = plan.survey_length_m()
    transit = plan.transit_length_m()
    return AzimuthScore(
        azimuth_deg=plan.azimuth_deg,
        n_strips=plan.n_strips,
        n_turns=plan.n_turns,
        survey_length_m=survey,
        transit_length_m=transit,
        time_s=(survey + transit) / float(v_ms)
                + K.TURN_PENALTY_S * plan.n_turns)


def sweep_azimuths(aoi_geom, v_ms: float, step_deg: float,
                   candidates=None, **route_kwargs):
    """Lay the strips out at each orientation and cost every one.

    ``route_kwargs`` are :func:`plan_route`'s, minus the azimuth: the same
    spacing, buffer, pattern and turn radius the mission will be flown with,
    so the comparison is between orientations and nothing else.
    """
    if step_deg <= 0:
        raise RoutingError("step_deg must be > 0, got {0!r}".format(step_deg))
    if candidates is None:
        n = max(int(math.ceil(180.0 / float(step_deg))), 1)
        candidates = [i * float(step_deg) for i in range(n)]
    route_kwargs.pop("azimuth_strategy", None)
    route_kwargs.pop("manual_azimuth_deg", None)
    route_kwargs.pop("wind_from_deg", None)
    scores = []
    for azimuth in candidates:
        plan = plan_route(aoi_geom, azimuth_strategy=AZIMUTH_MANUAL,
                          manual_azimuth_deg=float(azimuth), **route_kwargs)
        if not plan.legs:
            continue        # an orientation that clips away to nothing
        scores.append(score_azimuth(plan, v_ms))
    if not scores:
        raise RoutingError("no orientation produced a single strip")
    return scores


def optimise_azimuth(aoi_geom, v_ms: float,
                     step_deg: float = AZIMUTH_SWEEP_STEP_DEG,
                     refine_step_deg: float = AZIMUTH_REFINE_STEP_DEG,
                     **route_kwargs):
    """The orientation that costs the least flying. ``(azimuth, note, scores)``.

    Strips are laid out at every ``step_deg`` over a half turn -- a half
    turn is all there is, since flying a strip north-to-south or
    south-to-north covers the same ground -- and each layout is costed on
    its real clipped geometry. The winner is then refined at
    ``refine_step_deg`` inside its own bracket.

    What this minimises, in order: the flight time, and on a tie the number
    of turns. Fewer turns is not a tie-break for elegance -- a turn is where
    the aircraft leaves the strip, the shot rhythm breaks and the wind gets
    a chance to push the next line out of place.
    """
    scores = sweep_azimuths(aoi_geom, v_ms, step_deg, **route_kwargs)
    best = min(scores, key=lambda s: (s.time_s, s.n_turns, s.azimuth_deg))

    if refine_step_deg > 0 and refine_step_deg < step_deg:
        low = best.azimuth_deg - step_deg + refine_step_deg
        n = max(int(round((2.0 * step_deg - refine_step_deg)
                          / refine_step_deg)), 1)
        around = [low + i * refine_step_deg for i in range(n)]
        around = [normalize_azimuth(a, 180.0) for a in around]
        refined = sweep_azimuths(aoi_geom, v_ms, refine_step_deg,
                                 candidates=around, **route_kwargs)
        scores = scores + refined
        best = min(scores, key=lambda s: (s.time_s, s.n_turns, s.azimuth_deg))

    worst = max(scores, key=lambda s: s.time_s)
    saved = worst.time_s - best.time_s
    note = ("swept {0} orientations: {1:.0f} deg is the cheapest at "
            "{2:.0f} strips, {3:.0f} turns and {4:.0f} s, {5:.0f} s less "
            "than the worst orientation tried".format(
                len(scores), best.azimuth_deg, best.n_strips, best.n_turns,
                best.time_s, saved))
    return best.azimuth_deg, note, scores


def _azimuth_from_obb(geom):
    """Azimuth of the long axis of the oriented minimum bounding box.

    ``QgsGeometry.orientedMinimumBoundingBox()`` returns
    ``(geometry, area, angle, width, height)`` where ``angle`` is the compass
    azimuth of the ``height`` side. QGIS normally reports height >= width, but
    that is not guaranteed by the API, so the longer side is identified
    explicitly rather than assumed.
    """
    result = geom.orientedMinimumBoundingBox()
    if result is None or result[0] is None or result[0].isEmpty():
        return 0.0, 1.0
    _, _, angle, width, height = result
    if height >= width:
        az, long_side, short_side = angle, height, width
    else:
        az, long_side, short_side = angle + 90.0, width, height
    ratio = (long_side / short_side) if short_side > 0 else math.inf
    return normalize_azimuth(az, 180.0), ratio


# --------------------------------------------------------------------------
# Strip layout
# --------------------------------------------------------------------------

def _geometry_vertices(geom):
    """All vertices of a geometry as an (N, 2) array."""
    pts = [(v.x(), v.y()) for v in geom.vertices()]
    if not pts:
        raise RoutingError("AOI geometry has no vertices.")
    return np.asarray(pts, dtype=float)


def strip_offsets(t_min: float, t_max: float, d_side_m: float):
    """Across-track offsets of the strips, centred on the AOI.

    Centring matters: an uncentred pattern leaves an uneven sliver on one side,
    which either wastes a whole strip or under-covers the far edge.
    """
    if d_side_m <= 0:
        raise RoutingError("d_side must be > 0, got {0!r}".format(d_side_m))
    width = t_max - t_min
    if width <= 1e-9:
        return np.array([0.5 * (t_min + t_max)])
    n = int(math.ceil(width / d_side_m)) + 1
    centre = 0.5 * (t_min + t_max)
    return centre + (np.arange(n) - (n - 1) / 2.0) * d_side_m


def generate_strip_segments(clip_geom, frame: StripFrame, d_side_m: float,
                            min_length_m: float = 0.0):
    """Clip parallel strips to the AOI. Returns a list of dicts.

    Each entry is ``{"strip_index", "t", "s0", "s1"}`` with ``s0 < s1``. A
    concave AOI can yield several disjoint segments at the same offset; each
    becomes its own entry so the aircraft does not fly across a hole.
    """
    from qgis.core import QgsGeometry, QgsPointXY     # noqa: PLC0415

    verts = _geometry_vertices(clip_geom)
    s_all, t_all = frame.to_frame(verts[:, 0], verts[:, 1])
    s_min, s_max = float(s_all.min()), float(s_all.max())
    t_min, t_max = float(t_all.min()), float(t_all.max())
    pad = max(10.0, 0.01 * (s_max - s_min))

    segments = []
    for idx, t in enumerate(strip_offsets(t_min, t_max, d_side_m)):
        x0, y0 = frame.to_world(s_min - pad, t)
        x1, y1 = frame.to_world(s_max + pad, t)
        cutter = QgsGeometry.fromPolylineXY(
            [QgsPointXY(float(x0), float(y0)), QgsPointXY(float(x1), float(y1))])
        clipped = cutter.intersection(clip_geom)
        if clipped is None or clipped.isEmpty():
            continue
        for part in _line_parts(clipped):
            if part.shape[0] < 2:
                continue
            s_part, _ = frame.to_frame(part[:, 0], part[:, 1])
            s0, s1 = float(s_part.min()), float(s_part.max())
            if (s1 - s0) < min_length_m:
                continue
            segments.append({"strip_index": idx, "t": float(t),
                             "s0": s0, "s1": s1})
    if not segments:
        raise RoutingError(
            "No strip intersects the AOI. The strip spacing ({0:.1f} m) may be "
            "larger than the area, or the AOI and the working CRS may not "
            "match.".format(d_side_m))
    return segments


def _line_parts(geom):
    """Yield (N, 2) arrays for every linestring part of a geometry."""
    from qgis.core import QgsWkbTypes     # noqa: PLC0415

    parts = []
    if geom.isMultipart():
        try:
            for line in geom.asMultiPolyline():
                parts.append(np.array([[p.x(), p.y()] for p in line], dtype=float))
        except (TypeError, ValueError):
            for sub in geom.asGeometryCollection():
                parts.extend(_line_parts(sub))
    else:
        gtype = QgsWkbTypes.geometryType(geom.wkbType())
        if gtype == QgsWkbTypes.LineGeometry:
            line = geom.asPolyline()
            if line:
                parts.append(np.array([[p.x(), p.y()] for p in line], dtype=float))
        elif gtype == QgsWkbTypes.PointGeometry:
            pass          # a line grazing a vertex; contributes no strip
        else:
            for sub in geom.asGeometryCollection():
                if sub.wkbType() != geom.wkbType():
                    parts.extend(_line_parts(sub))
    return parts


# --------------------------------------------------------------------------
# Flight ordering
# --------------------------------------------------------------------------

def interlace_step(turn_radius_m: float, d_side_m: float) -> int:
    """How many strips to skip so a turn fits between consecutive legs.

    A 180 deg turn of radius R needs 2R of lateral room. If adjacent strips are
    closer than that, flying them in order forces either an S-turn overshoot or
    a stall-speed pivot, so the aircraft instead flies every k-th strip and
    fills in the gaps on later passes.
    """
    if turn_radius_m <= 0 or d_side_m <= 0:
        return 1
    return max(1, int(math.ceil(2.0 * turn_radius_m / d_side_m)))


def order_legs(segments, frame: StripFrame, azimuth_deg: float,
               pattern: str = PATTERN_BOUSTROPHEDON,
               interlace: int = 1, lead_in_m: float = 0.0):
    """Order strip segments into a flyable sequence.

    Boustrophedon alternates direction on consecutive legs so the aircraft
    never deadheads. Interlacing visits every ``interlace``-th strip before
    coming back for the ones in between.
    """
    by_index = {}
    for seg in segments:
        by_index.setdefault(seg["strip_index"], []).append(seg)
    indices = sorted(by_index)

    if pattern == PATTERN_INTERLACED and interlace > 1:
        order = []
        for start in range(interlace):
            order.extend(indices[start::interlace])
    elif pattern in (PATTERN_BOUSTROPHEDON, PATTERN_INTERLACED):
        order = indices
    else:
        raise RoutingError("unknown flight pattern {0!r}".format(pattern))

    legs = []
    forward = True
    seq = 0
    for strip_index in order:
        group = sorted(by_index[strip_index], key=lambda s: s["s0"],
                       reverse=not forward)
        for seg in group:
            if forward:
                s_a, s_b = seg["s0"], seg["s1"]
            else:
                s_a, s_b = seg["s1"], seg["s0"]
            direction = 1.0 if s_b >= s_a else -1.0
            legs.append(RouteLeg(
                seq=seq,
                strip_index=strip_index,
                t=seg["t"],
                s_photo_start=s_a,
                s_photo_end=s_b,
                s_fly_start=s_a - direction * lead_in_m,
                s_fly_end=s_b + direction * lead_in_m,
                azimuth_deg=normalize_azimuth(
                    azimuth_deg if direction > 0 else azimuth_deg + 180.0),
                frame=frame,
            ))
            seq += 1
        forward = not forward
    return legs


# --------------------------------------------------------------------------
# Top-level planners
# --------------------------------------------------------------------------

def plan_route(aoi_geom, d_side_m: float, d_front_m: float,
               footprint_across_m: float, footprint_along_m: float,
               azimuth_strategy: str = AZIMUTH_LONGEST_SIDE,
               manual_azimuth_deg: Optional[float] = None,
               wind_from_deg: Optional[float] = None,
               terrain=None, user_margin_m: float = 0.0,
               pattern: str = PATTERN_BOUSTROPHEDON,
               turn_radius_m: float = 0.0,
               lead_in_m: float = 0.0,
               block_index: int = 0) -> RoutePlan:
    """Plan the strips for one AOI block."""
    warnings: "list[str]" = []

    azimuth, note = choose_azimuth(
        aoi_geom, azimuth_strategy, terrain=terrain,
        manual_azimuth_deg=manual_azimuth_deg, wind_from_deg=wind_from_deg)
    warnings.append("Strip azimuth {0:.1f} deg: {1}.".format(azimuth, note))

    clip = photogrammetric_buffer(aoi_geom, footprint_across_m, user_margin_m)

    centroid = aoi_geom.centroid().asPoint()
    frame = StripFrame(azimuth, centroid.x(), centroid.y())

    segments = generate_strip_segments(clip, frame, d_side_m)

    # The isotropic buffer already extends the AOI by W/2 along track. If the
    # sensor is mounted portrait, L/2 is the larger half-footprint and the
    # strip ends need the difference added so the end frames still cover the
    # AOI edge with full frontlap.
    extra_along = max(0.0, 0.5 * footprint_along_m - 0.5 * footprint_across_m)
    if extra_along > 0:
        for seg in segments:
            seg["s0"] -= extra_along
            seg["s1"] += extra_along

    interlace = 1
    if pattern == PATTERN_INTERLACED:
        interlace = interlace_step(turn_radius_m, d_side_m)
        if interlace > 1:
            warnings.append(
                "Turn radius {0:.0f} m needs {1:.0f} m of lateral room but the "
                "strips are {2:.1f} m apart, so the route flies every {3}th "
                "strip and fills in the gaps on later passes.".format(
                    turn_radius_m, 2 * turn_radius_m, d_side_m, interlace))
    elif turn_radius_m > 0.5 * d_side_m:
        warnings.append(
            "Turn radius {0:.0f} m exceeds half the strip spacing ({1:.1f} m). "
            "A fixed-wing aircraft cannot make this U-turn; switch the pattern "
            "to 'interlaced'.".format(turn_radius_m, 0.5 * d_side_m))

    legs = order_legs(segments, frame, azimuth, pattern, interlace, lead_in_m)
    plan = RoutePlan(frame=frame, azimuth_deg=azimuth, legs=legs,
                     d_side_m=d_side_m, d_front_m=d_front_m, pattern=pattern,
                     block_index=block_index, warnings=warnings)

    # Interlacing maximises the lateral room at each turn, but it cannot always
    # deliver 2R: with n strips and a required skip of k, an ordering where
    # every consecutive gap is at least k simply does not exist once k is large
    # relative to n (with 5 strips and k = 3 the walk dead-ends after one step).
    # Rather than pretend, name the turns that have to be flown as a circuit
    # outside the block.
    tight = plan.tight_turns(turn_radius_m)
    if tight:
        plan.warnings.append(
            "{0} of {1} turns have less than {2:.0f} m of lateral room "
            "(minimum {3:.0f} m). The aircraft must overshoot beyond the block "
            "to turn there; allow clear airspace outside the AOI, or increase "
            "the strip spacing by flying higher.".format(
                len(tight), len(plan.consecutive_offset_gaps()),
                2.0 * turn_radius_m,
                min(plan.consecutive_offset_gaps()) if plan.legs[1:] else 0.0))
    return plan


def plan_double_grid(aoi_geom, **kwargs):
    """Two orthogonal passes over the same AOI.

    Doubles the flight time, and is what makes the difference between a usable
    orthophoto and a usable 3-D model: a single grid sees every vertical
    surface from one direction only, so facades and steep faces reconstruct
    poorly. The second grid is flown at azimuth + 90 deg.
    """
    kwargs.pop("azimuth_strategy", None)
    kwargs.pop("manual_azimuth_deg", None)
    first = plan_route(aoi_geom, azimuth_strategy=AZIMUTH_LONGEST_SIDE, **kwargs)
    second = plan_route(aoi_geom, azimuth_strategy=AZIMUTH_MANUAL,
                        manual_azimuth_deg=first.azimuth_deg + 90.0, **kwargs)
    second.block_index = first.block_index
    for plan in (first, second):
        plan.warnings.append(
            "Double grid: this is one of two orthogonal passes.")
    return [first, second]


def plan_corridor(line_geom, d_side_m: float, d_front_m: float,
                  corridor_width_m: float, footprint_across_m: float,
                  user_margin_m: float = 0.0, block_index: int = 0,
                  join_segments: int = 12) -> RoutePlan:
    """Strips parallel to a linear feature (road, river, powerline).

    The strips are true offset curves of the axis, so they follow the bends
    instead of being straight lines clipped to a buffer -- which is what keeps
    the AGL and the sidelap constant around a curve.

    ``corridor_width_m`` is the total width to be imaged, centred on the axis.
    """
    from qgis.core import Qgis, QgsGeometry           # noqa: PLC0415

    warnings: "list[str]" = []
    half = 0.5 * corridor_width_m + 0.5 * footprint_across_m + user_margin_m
    n_side = int(math.ceil((half - 0.5 * d_side_m) / d_side_m)) if d_side_m > 0 else 0
    n_side = max(n_side, 0)
    offsets = [(i - n_side) * d_side_m for i in range(2 * n_side + 1)]

    verts = _geometry_vertices(line_geom)
    axis_az = math.degrees(math.atan2(verts[-1, 0] - verts[0, 0],
                                      verts[-1, 1] - verts[0, 1])) % 360.0
    frame = StripFrame(axis_az, float(verts[:, 0].mean()), float(verts[:, 1].mean()))

    legs = []
    seq = 0
    forward = True
    for offset in offsets:
        if abs(offset) < 1e-9:
            curve = QgsGeometry(line_geom)
        else:
            curve = line_geom.offsetCurve(offset, join_segments,
                                          Qgis.JoinStyle.Round, 2.0)
        if curve is None or curve.isEmpty():
            warnings.append(
                "Offset curve at {0:+.1f} m collapsed (the axis probably bends "
                "tighter than that offset); that strip was skipped.".format(
                    offset))
            continue
        for part in _line_parts(curve):
            if part.shape[0] < 2:
                continue
            pts = part if forward else part[::-1]
            legs.append(_CorridorLeg(seq=seq, strip_index=len(legs), t=offset,
                                     polyline=pts,
                                     azimuth_deg=axis_az, frame=frame))
            seq += 1
        forward = not forward

    if not legs:
        raise RoutingError("Corridor planning produced no usable strips.")
    warnings.append(
        "Corridor: {0} strips over a {1:.0f} m width, offsets {2:+.1f} to "
        "{3:+.1f} m from the axis.".format(
            len(legs), corridor_width_m, offsets[0], offsets[-1]))
    return RoutePlan(frame=frame, azimuth_deg=axis_az, legs=legs,
                     d_side_m=d_side_m, d_front_m=d_front_m,
                     pattern="corridor", block_index=block_index,
                     warnings=warnings)


@dataclass(init=False)
class _CorridorLeg(RouteLeg):
    """A curved leg. Overrides the straight-line endpoint accessors.

    ``init=False`` is required: @dataclass would otherwise generate an
    ``__init__`` that replaces the one defined below.
    """

    polyline: np.ndarray = None

    def __init__(self, seq, strip_index, t, polyline, azimuth_deg, frame):
        length = float(np.hypot(*np.diff(polyline[:, :2], axis=0).T).sum())
        super().__init__(seq=seq, strip_index=strip_index, t=t,
                         s_photo_start=0.0, s_photo_end=length,
                         s_fly_start=0.0, s_fly_end=length,
                         azimuth_deg=azimuth_deg, frame=frame)
        self.polyline = np.asarray(polyline, dtype=float)

    def fly_endpoints_xy(self):
        return self.polyline[:, :2]

    def photo_endpoints_xy(self):
        return self.polyline[:, :2]


def leg_polyline(leg: RouteLeg):
    """Planimetric polyline actually flown for a leg (straight or curved)."""
    return leg.fly_endpoints_xy()
