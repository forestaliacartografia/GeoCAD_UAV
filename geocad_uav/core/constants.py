"""
Tolerances and defaults for the whole plugin.

Every tunable number lives here or in ``profiles/*.json``. Magic numbers
scattered through the code are forbidden (spec section 12): if a value shows up
in a report or changes a geometry, the operator has to be able to find it and
change it.

All lengths are metres in the working CRS, all angles are degrees unless the
name says otherwise.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

#: Coincidence tolerance for construction. Below this two points are the same
#: point. float64 metres hold ~1e-10 relative precision at UTM magnitudes
#: (~5e6 m), so 1e-6 m is comfortably above the noise floor.
GEOM_EPS_M = 1e-6

#: Topological tolerance: snapping, node merging, "on the boundary" tests.
#: Deliberately much coarser than GEOM_EPS_M -- survey data is not exact.
TOPO_EPS_M = 0.01

#: Angular tolerance for parallel/perpendicular constraint solving.
ANGLE_EPS_DEG = 1e-9

#: Segments used to approximate a full circle when a true curve cannot be
#: stored (Shapefile, most exchange formats). 72 gives a sagitta error of
#: r * (1 - cos(pi/72)) = 0.095 % of the radius, i.e. under 1 cm at r = 10 m.
CIRCLE_SEGMENTS = 72

#: Segments per quadrant for buffers and offset curves.
BUFFER_SEGMENTS = 12

#: Display rounding. Storage stays full float64; this is presentation only.
DECIMALS_LENGTH = 3
DECIMALS_ANGLE = 4
DECIMALS_AREA = 2


# --------------------------------------------------------------------------
# Snapping and interaction
# --------------------------------------------------------------------------

SNAP_TOLERANCE_PX = 12
RUBBERBAND_WIDTH_PX = 2
HUD_UPDATE_MS = 16          # ~60 fps; rubber band only, never a canvas refresh


# --------------------------------------------------------------------------
# CRS
# --------------------------------------------------------------------------

#: Beyond this extent a planar computation in a single UTM zone accumulates
#: unacceptable scale error, and geodesic measurement should be used instead.
GEODESIC_THRESHOLD_M = 20_000.0

#: A local tangent-plane fallback is only honest over very short distances.
#: Past this, refuse rather than approximate.
LOCAL_TANGENT_MAX_M = 2_000.0


# --------------------------------------------------------------------------
# Raster / terrain
# --------------------------------------------------------------------------

#: Vertical tolerance for waypoint densification: the flown straight-line path
#: stays within this many metres of the commanded terrain-following profile.
TERRAIN_DZ_M = 2.0

#: Hard ceiling on the DEM sampling step along a strip, before the DEM cell
#: size and D_front are also taken into account.
MAX_SAMPLE_STEP_M = 5.0

#: Extra height added when a bare-earth DTM is flown over ground that actually
#: carries canopy or buildings -- the DTM does not know they are there.
VEGETATION_CLEARANCE_M = 15.0


# --------------------------------------------------------------------------
# Mission
# --------------------------------------------------------------------------

#: Minimum number of photos every point inside the AOI must appear in.
MIN_PHOTOS_PER_POINT = 3

#: Motion blur budget, in pixels of ground smear during the exposure.
BLUR_PX_MAX = 1.5

#: Fraction of the battery treated as usable, RTH included.
ENDURANCE_USABLE_FRACTION = 0.70

#: Default legal ceiling (EU/EASA open category). Configurable per country.
MAX_LEGAL_AGL_M = 120.0

#: RTH altitude clearance above the highest terrain in the AOI.
RTH_CLEARANCE_M = 20.0

#: Acceptance band for the effective GSD: [target, target * (1 + this)].
GSD_TOLERANCE = 0.15

#: Ground speed below which a mission is considered impractical to fly.
MIN_SPEED_MS = 1.0

#: Turn overhead per strip change, used in the flight-time estimate.
TURN_PENALTY_S = 4.0

#: Take-off + landing overhead per sub-mission.
TAKEOFF_LANDING_S = 60.0

#: Ground time to change a battery between two legs: landing to take-off
#: with the aircraft in hand. Counted in the time on site, never in the
#: flight time, which is what the endurance is spent on.
BATTERY_SWAP_S = 180.0


# --------------------------------------------------------------------------
# Performance
# --------------------------------------------------------------------------

#: Features written per batch when generating grids and planting patterns.
CHUNK_SIZE = 5_000

#: Above this many features the caller must show a preview and ask before
#: committing (spec P8: no mass operation without confirmation).
CONFIRM_THRESHOLD_FEATURES = 20_000

#: Target for the live mission preview on an AOI below 5 km2.
PREVIEW_BUDGET_MS = 500
