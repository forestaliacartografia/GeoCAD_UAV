"""
Data model shared by every module.

These are plain dataclasses with no QGIS dependency, so they can be built,
compared and serialised in tests. The QGIS-facing conversion (to features, to
layers) lives in ``io/layer_factory.py``.

Angle convention throughout the plugin: **compass azimuth, degrees clockwise
from grid north** of the working CRS. There is deliberately only one angular
convention -- CAD tools, forest rows and flight strips all use it -- because
mixing "CCW from east" for shapes with "CW from north" for bearings is a
reliable source of mirrored geometry.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# --------------------------------------------------------------------------
# Vertical datum
# --------------------------------------------------------------------------

class VerticalDatum:
    """Vertical reference of an elevation model and of exported heights.

    This is metadata, not a transformation: the plugin never silently converts
    between orthometric and ellipsoidal heights (spec P6). It records what the
    DEM holds and what the target firmware expects, and applies a geoid
    undulation only when the operator supplies one.
    """

    ORTHOMETRIC_EGM96 = "orthometric_egm96"
    ORTHOMETRIC_EGM2008 = "orthometric_egm2008"
    ORTHOMETRIC_LOCAL = "orthometric_local"
    ELLIPSOIDAL = "ellipsoidal_wgs84"
    UNKNOWN = "unknown"

    LABELS = {
        ORTHOMETRIC_EGM96: "Ortometrico (EGM96)",
        ORTHOMETRIC_EGM2008: "Ortometrico (EGM2008)",
        ORTHOMETRIC_LOCAL: "Ortometrico (geoide locale)",
        ELLIPSOIDAL: "Ellissoidico (WGS84)",
        UNKNOWN: "Non dichiarato",
    }

    ALL = tuple(LABELS)

    @classmethod
    def label(cls, key: str) -> str:
        return cls.LABELS.get(key, cls.LABELS[cls.UNKNOWN])

    @classmethod
    def is_orthometric(cls, key: str) -> bool:
        return key in (cls.ORTHOMETRIC_EGM96, cls.ORTHOMETRIC_EGM2008,
                       cls.ORTHOMETRIC_LOCAL)


# --------------------------------------------------------------------------
# Altitude modes
# --------------------------------------------------------------------------

class AltitudeMode:
    """How waypoint heights are derived. Terrain following is the default."""

    #: Constant AGL: Z = Z_terrain + H_AGL + margin. The only mode that keeps
    #: GSD and overlap constant over relief.
    TERRAIN = "terrain"
    #: One AMSL per strip, from the mean terrain along that strip.
    STRIP_AMSL = "strip_amsl"
    #: A single AMSL for the whole mission. Only admissible on flat ground.
    SINGLE_AMSL = "single_amsl"

    LABELS = {
        TERRAIN: "Terrain following continuo (AGL costante)",
        STRIP_AMSL: "Quota AMSL per singola strip",
        SINGLE_AMSL: "Quota AMSL unica per la missione",
    }
    ALL = tuple(LABELS)


# --------------------------------------------------------------------------
# CAD
# --------------------------------------------------------------------------

@dataclass
class ParametricRecord:
    """The numbers a CAD shape was built from, kept alongside its geometry.

    This is what makes the Geometry Properties Editor possible: the shape can
    be regenerated from ``params`` instead of being re-drawn. ``broken`` is set
    when the geometry has been edited vertex-by-vertex outside the plugin, in
    which case the parameters no longer describe it and must not be silently
    reapplied (spec section 5).
    """

    tool: str
    params: "dict[str, Any]" = field(default_factory=dict)
    crs_authid: str = ""
    record_id: str = ""
    layer_id: str = ""
    created_at: str = ""
    broken: bool = False

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds")

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "ParametricRecord":
        if not text:
            raise ValueError("empty parametric record")
        return cls(**json.loads(text))

    def matches(self, geometry_signature: str) -> bool:
        """True when the stored signature still describes the live geometry."""
        return (not self.broken
                and self.params.get("_signature") == geometry_signature)


@dataclass
class PlantingRecord:
    """One planting position produced by the forest designer."""

    plant_id: int
    row_id: int
    seq_in_row: int
    x: float
    y: float
    z: Optional[float] = None
    spacing_x: float = 0.0
    spacing_y: float = 0.0
    azimuth_deg: float = 0.0
    inside_aoi: bool = True
    dist_to_edge: float = 0.0
    slope_deg: Optional[float] = None
    aspect_deg: Optional[float] = None
    excluded_reason: str = ""


# --------------------------------------------------------------------------
# UAV
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class DroneProfile:
    """Airframe limits. Loaded from ``profiles/drones.json``, never hardcoded."""

    key: str
    name: str
    kind: str = "multirotor"            # multirotor | fixedwing
    #: Maximum take-off weight in grams, 0 when the library does not say.
    #: Nothing in the route depends on it; the 250 g line does.
    weight_g: float = 0.0
    v_max_ms: float = 15.0
    v_cruise_ms: float = 8.0
    climb_ms: float = 4.0
    descent_ms: float = 3.0
    endurance_min: float = 25.0
    waypoint_limit: int = 99
    rth_reserve_pct: float = 30.0
    max_agl_m: float = 120.0
    battery_wh: Optional[float] = None
    turn_radius_m: float = 0.0          # 0 for a multirotor: it can pivot
    export_formats: tuple = ()
    source: str = ""
    notes: str = ""

    @property
    def is_sub_250g(self) -> bool:
        """Whether the airframe is in the lightest regulatory class.

        False when the weight is unknown: "not recorded" must never read as
        "light enough".
        """
        return 0.0 < self.weight_g <= 250.0

    @property
    def is_fixed_wing(self) -> bool:
        return self.kind == "fixedwing"

    @property
    def usable_endurance_s(self) -> float:
        """Flight time budget with the RTH reserve already taken out."""
        return self.endurance_min * 60.0 * (1.0 - self.rth_reserve_pct / 100.0)


@dataclass
class Waypoint:
    """One commanded position. ``z_amsl`` is in the mission vertical datum."""

    seq: int
    x: float
    y: float
    z_amsl: float
    z_agl: float
    heading_deg: float = 0.0
    gimbal_pitch_deg: float = -90.0
    speed_ms: float = 0.0
    kind: str = "nav"                   # nav | photo
    actions: "list[str]" = field(default_factory=list)
    strip_index: int = -1
    sub_mission: int = 0
    #: True when the DEM had no data here and the height was made conservative.
    dem_gap: bool = False
    #: True when the terrain is steeper than the airframe climb rate allows.
    climb_limited: bool = False

    @property
    def is_photo(self) -> bool:
        return self.kind == "photo"


@dataclass
class PhotoCenter:
    """An exposure station with approximate exterior orientation.

    omega/phi/kappa follow the convention documented in
    ``uav.photogrammetry.opk_from_yaw_pitch`` and are printed with the mission
    report, because SfM packages disagree on the signs.
    """

    photo_id: int
    seq: int
    x: float
    y: float
    z_amsl: float
    z_agl: float
    heading_deg: float
    gimbal_pitch_deg: float
    omega_deg: float
    phi_deg: float
    kappa_deg: float
    gsd_m: float
    strip_index: int = -1
    sub_mission: int = 0

    @property
    def gsd_cm(self) -> float:
        return self.gsd_m * 100.0


@dataclass
class MissionStats:
    """Everything the report and the KPI panel need, computed once."""

    aoi_area_m2: float = 0.0
    n_strips: int = 0
    n_photos: int = 0
    n_waypoints: int = 0
    n_turns: int = 0
    survey_length_m: float = 0.0
    transit_length_m: float = 0.0
    total_length_m: float = 0.0
    flight_time_s: float = 0.0
    n_batteries: int = 1
    terrain_z_min: float = math.nan
    terrain_z_max: float = math.nan
    agl_min: float = math.nan
    agl_max: float = math.nan
    gsd_min_m: float = math.nan
    gsd_max_m: float = math.nan
    coverage_pct: float = math.nan
    min_photos_observed: int = 0

    @property
    def aoi_area_ha(self) -> float:
        return self.aoi_area_m2 / 10_000.0

    @property
    def terrain_relief_m(self) -> float:
        return self.terrain_z_max - self.terrain_z_min


@dataclass
class Mission:
    """A complete, validated flight plan."""

    crs_authid: str
    vertical_datum: str = VerticalDatum.UNKNOWN
    altitude_mode: str = AltitudeMode.TERRAIN
    h_agl_m: float = 0.0
    gsd_m: float = 0.0
    frontlap: float = 0.0
    sidelap: float = 0.0
    azimuth_deg: float = 0.0
    pattern: str = ""
    speed_ms: float = 0.0
    camera_key: str = ""
    drone_key: str = ""
    safety_margin_m: float = 0.0
    vegetation_clearance_m: float = 0.0
    geoid_undulation_m: float = 0.0

    waypoints: "list[Waypoint]" = field(default_factory=list)
    photos: "list[PhotoCenter]" = field(default_factory=list)
    #: (N, 3) polylines: the flown route, one entry per leg or transit.
    lines: "list[Any]" = field(default_factory=list)
    #: Closed (M, 3) rings, one per photo, draped on the DEM.
    footprints: "list[Any]" = field(default_factory=list)
    #: Terrain vs flight elevation along the whole route.
    profile: "list[dict]" = field(default_factory=list)
    #: Every orientation the azimuth sweep costed, when one was run. Empty
    #: when the azimuth was chosen any other way.
    azimuth_scores: "list[Any]" = field(default_factory=list)

    stats: MissionStats = field(default_factory=MissionStats)
    warnings: "list[str]" = field(default_factory=list)
    errors: "list[str]" = field(default_factory=list)
    assumptions: "list[str]" = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def n_sub_missions(self) -> int:
        if not self.waypoints:
            return 0
        return len({wp.sub_mission for wp in self.waypoints})

    def waypoints_of(self, sub_mission: int) -> "list[Waypoint]":
        return [wp for wp in self.waypoints if wp.sub_mission == sub_mission]
