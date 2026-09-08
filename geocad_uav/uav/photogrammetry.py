"""
Photogrammetric model for UAV survey planning.

PURE PYTHON: this module deliberately imports nothing from QGIS, GDAL or numpy
so that the numerics can be unit-tested standalone (tests/test_photogrammetry.py).

Units (SI unless stated):
    focal length, sensor size, pixel pitch : mm
    flight height (AGL), footprints, spacing : m
    GSD : m/px  (converted to cm/px only at presentation time)
    speed : m/s
    shutter : s
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


class PhotogrammetryError(ValueError):
    """Raised when the requested survey geometry is physically impossible."""


# --------------------------------------------------------------------------
# Camera
# --------------------------------------------------------------------------

#: Sensor long axis lies across the flight direction (classic "landscape" nadir
#: mount). Footprint width comes from sensor_w, footprint length from sensor_h.
ORIENT_ACROSS = "across"
#: Sensor long axis lies along the flight direction ("portrait" / rotated mount).
ORIENT_ALONG = "along"


@dataclass(frozen=True)
class Camera:
    """A metric frame camera.

    ``source``/``notes`` exist so that every number used in a mission report can
    be traced back to a manufacturer spec sheet. Nothing here is guessed at run
    time: if a value is unknown the caller must supply it explicitly.
    """

    name: str
    focal_mm: float
    sensor_w_mm: float
    sensor_h_mm: float
    image_w_px: int
    image_h_px: int
    #: Typical exposure time used for the motion-blur speed limit.
    shutter_s: float = 1.0 / 1000.0
    #: Fastest sustained capture interval the payload can hold, in seconds.
    min_interval_s: float = 2.0
    mechanical_shutter: bool = False
    source: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        for field_name in ("focal_mm", "sensor_w_mm", "sensor_h_mm",
                           "shutter_s", "min_interval_s"):
            value = getattr(self, field_name)
            if not (isinstance(value, (int, float)) and value > 0
                    and math.isfinite(value)):
                raise PhotogrammetryError(
                    "Camera {0}: {1} must be a finite positive number, "
                    "got {2!r}".format(self.name, field_name, value))
        for field_name in ("image_w_px", "image_h_px"):
            value = getattr(self, field_name)
            if not (isinstance(value, int) and value > 0):
                raise PhotogrammetryError(
                    "Camera {0}: {1} must be a positive integer, "
                    "got {2!r}".format(self.name, field_name, value))

    # -- pixel pitch -------------------------------------------------------

    @property
    def pitch_w_mm(self) -> float:
        """Pixel pitch derived from the sensor width: pitch = Sw / Px."""
        return self.sensor_w_mm / self.image_w_px

    @property
    def pitch_h_mm(self) -> float:
        """Pixel pitch derived from the sensor height: pitch = Sh / Py."""
        return self.sensor_h_mm / self.image_h_px

    @property
    def pitch_mm(self) -> float:
        """Mean pixel pitch. Equals both of the above for square pixels."""
        return 0.5 * (self.pitch_w_mm + self.pitch_h_mm)

    @property
    def pitch_mismatch_pct(self) -> float:
        """Disagreement between the two pitch estimates, in percent.

        Above ~1 % the quoted sensor size and pixel count are inconsistent
        (non-square pixels are very rare on UAV payloads), i.e. one of the two
        published numbers is wrong. The mission report surfaces this rather
        than silently averaging it away.
        """
        mean = self.pitch_mm
        if mean <= 0:
            return 0.0
        return 100.0 * abs(self.pitch_w_mm - self.pitch_h_mm) / mean

    @property
    def megapixels(self) -> float:
        return self.image_w_px * self.image_h_px / 1.0e6

    @property
    def diagonal_fov_deg(self) -> float:
        diag_mm = math.hypot(self.sensor_w_mm, self.sensor_h_mm)
        return math.degrees(2.0 * math.atan(diag_mm / (2.0 * self.focal_mm)))

    def sensor_across_along_mm(self, orientation: str = ORIENT_ACROSS):
        """Return (across-track, along-track) sensor dimensions in mm."""
        if orientation == ORIENT_ACROSS:
            return self.sensor_w_mm, self.sensor_h_mm
        if orientation == ORIENT_ALONG:
            return self.sensor_h_mm, self.sensor_w_mm
        raise PhotogrammetryError(
            "orientation must be {0!r} or {1!r}, got {2!r}".format(
                ORIENT_ACROSS, ORIENT_ALONG, orientation))

    def pixels_across_along(self, orientation: str = ORIENT_ACROSS):
        """Return (across-track, along-track) pixel counts."""
        if orientation == ORIENT_ACROSS:
            return self.image_w_px, self.image_h_px
        if orientation == ORIENT_ALONG:
            return self.image_h_px, self.image_w_px
        raise PhotogrammetryError(
            "orientation must be {0!r} or {1!r}, got {2!r}".format(
                ORIENT_ACROSS, ORIENT_ALONG, orientation))


# --------------------------------------------------------------------------
# Overlap
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Overlap:
    """Forward (along-track) and side (across-track) overlap, fractions 0..1."""

    frontlap: float
    sidelap: float

    def __post_init__(self) -> None:
        for name in ("frontlap", "sidelap"):
            value = getattr(self, name)
            if not (0.0 <= value < 1.0):
                raise PhotogrammetryError(
                    "{0} must be in [0, 1), got {1!r}. Use fractions (0.75), "
                    "not percent (75).".format(name, value))

    @property
    def as_percent(self):
        return 100.0 * self.frontlap, 100.0 * self.sidelap


#: Reference overlap presets. Planning conventions, not hard physics; they are
#: exposed in the UI and always printed in the mission report.
OVERLAP_PRESETS = {
    # Orthophoto / 2D mapping: frontlap 60-70 %, sidelap 50-60 %
    "ortho_2d": Overlap(frontlap=0.70, sidelap=0.60),
    # DSM / 3D reconstruction: frontlap 70-80 %, sidelap 60-70 %
    "dsm_3d": Overlap(frontlap=0.80, sidelap=0.70),
    # Complex / steep / vegetated terrain: frontlap 80-90 %, sidelap 70-80 %
    "complex_terrain": Overlap(frontlap=0.85, sidelap=0.75),
}


# --------------------------------------------------------------------------
# Core relations
# --------------------------------------------------------------------------

def gsd_from_height(camera: Camera, h_agl_m: float,
                    orientation: str = ORIENT_ACROSS) -> float:
    """GSD [m/px] = H_AGL [m] * pitch [mm] / f [mm].

    The across-track pitch governs the resolution that matters for the
    orthophoto, so it is the one used here.
    """
    if h_agl_m <= 0:
        raise PhotogrammetryError("h_agl_m must be > 0, got {0!r}".format(h_agl_m))
    sensor_across, _ = camera.sensor_across_along_mm(orientation)
    px_across, _ = camera.pixels_across_along(orientation)
    pitch_mm = sensor_across / px_across
    return h_agl_m * pitch_mm / camera.focal_mm


def height_from_gsd(camera: Camera, gsd_m_px: float,
                    orientation: str = ORIENT_ACROSS) -> float:
    """H_AGL [m] = GSD [m/px] * f [mm] / pitch [mm]. Inverse of gsd_from_height."""
    if gsd_m_px <= 0:
        raise PhotogrammetryError("gsd_m_px must be > 0, got {0!r}".format(gsd_m_px))
    sensor_across, _ = camera.sensor_across_along_mm(orientation)
    px_across, _ = camera.pixels_across_along(orientation)
    pitch_mm = sensor_across / px_across
    return gsd_m_px * camera.focal_mm / pitch_mm


def footprint(camera: Camera, h_agl_m: float,
              orientation: str = ORIENT_ACROSS):
    """Ground footprint of one nadir frame at h_agl_m.

    Returns ``(across_track_m, along_track_m)``::

        W = (S_across / f) * H
        L = (S_along  / f) * H

    This is the footprint on a *horizontal plane* through the nadir ground
    point. The terrain-draped footprint -- which is what coverage is actually
    judged on -- is computed by ``core.terrain.drape_footprint`` by ray
    casting against the DEM.
    """
    if h_agl_m <= 0:
        raise PhotogrammetryError("h_agl_m must be > 0, got {0!r}".format(h_agl_m))
    s_across, s_along = camera.sensor_across_along_mm(orientation)
    return ((s_across / camera.focal_mm) * h_agl_m,
            (s_along / camera.focal_mm) * h_agl_m)


def strip_spacing(footprint_across_m: float, sidelap: float) -> float:
    """D_side = W * (1 - sidelap)  [m] -- distance between adjacent strips."""
    return footprint_across_m * (1.0 - sidelap)


def shot_spacing(footprint_along_m: float, frontlap: float) -> float:
    """D_front = L * (1 - frontlap)  [m] -- air base between consecutive shots."""
    return footprint_along_m * (1.0 - frontlap)


# --------------------------------------------------------------------------
# Speed budget
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SpeedBudget:
    """Every speed ceiling that applies to a mission, plus the binding one.

    ``v_climb`` is the *worst-case* terrain-driven ceiling over the whole route;
    the per-segment value is recomputed in ``core.terrain`` because it depends
    on the local DEM slope.
    """

    v_mission: float
    v_blur: float
    v_trigger: Optional[float]
    v_drone_max: float
    v_regulatory: Optional[float]
    v_climb: Optional[float] = None

    @property
    def candidates(self):
        out = {
            "v_mission": self.v_mission,
            "v_blur": self.v_blur,
            "v_drone_max": self.v_drone_max,
        }
        if self.v_trigger is not None:
            out["v_trigger"] = self.v_trigger
        if self.v_regulatory is not None:
            out["v_regulatory"] = self.v_regulatory
        if self.v_climb is not None:
            out["v_climb"] = self.v_climb
        return out

    @property
    def effective(self) -> float:
        """V_effettiva = min(all applicable ceilings)."""
        return min(self.candidates.values())

    @property
    def binding(self) -> str:
        """Name of the constraint that actually caps the mission speed."""
        return min(self.candidates.items(), key=lambda kv: kv[1])[0]

    @property
    def is_capped_below_request(self) -> bool:
        return self.effective < self.v_mission - 1e-9

    def with_climb(self, v_climb: Optional[float]) -> "SpeedBudget":
        return SpeedBudget(self.v_mission, self.v_blur, self.v_trigger,
                           self.v_drone_max, self.v_regulatory, v_climb)


def blur_speed_limit(gsd_m_px: float, shutter_s: float,
                     blur_px_max: float = 1.5) -> float:
    """V_blur = GSD * blur_px_max / t_shutter  [m/s].

    Ground smear during the exposure must stay under ``blur_px_max`` pixels.
    Default 1.5 px is the usual mapping tolerance; tighten to 1.0 px for
    photogrammetric control work.
    """
    if shutter_s <= 0:
        raise PhotogrammetryError("shutter_s must be > 0, got {0!r}".format(shutter_s))
    if blur_px_max <= 0:
        raise PhotogrammetryError(
            "blur_px_max must be > 0, got {0!r}".format(blur_px_max))
    return gsd_m_px * blur_px_max / shutter_s


def trigger_speed_limit(d_front_m: float, min_interval_s: float) -> float:
    """V_trigger = D_front / t_interval  [m/s].

    The payload cannot shoot faster than ``min_interval_s``; flying quicker
    would stretch the air base beyond D_front and break the frontlap.
    """
    if min_interval_s <= 0:
        raise PhotogrammetryError(
            "min_interval_s must be > 0, got {0!r}".format(min_interval_s))
    return d_front_m / min_interval_s


def climb_speed_limit(delta_z_m: float, horizontal_m: float,
                      climb_rate_ms: float, descent_rate_ms: float) -> float:
    """Ground-speed ceiling imposed by the vertical rate on one route segment.

    Flying a segment of horizontal length ``d`` with height change ``dz`` at
    ground speed V requires a vertical speed of ``V * |dz| / d``. Capping that
    at the airframe's climb/descent rate gives::

        V_max = rate * d / |dz|

    A level segment is unconstrained (returns +inf).
    """
    if horizontal_m <= 0:
        return math.inf
    if abs(delta_z_m) < 1e-9:
        return math.inf
    rate = climb_rate_ms if delta_z_m > 0 else descent_rate_ms
    if rate <= 0:
        raise PhotogrammetryError(
            "climb_rate_ms and descent_rate_ms must be > 0, got {0!r} / "
            "{1!r}".format(climb_rate_ms, descent_rate_ms))
    return rate * horizontal_m / abs(delta_z_m)


# --------------------------------------------------------------------------
# Exterior orientation
# --------------------------------------------------------------------------

def opk_from_yaw_pitch(azimuth_deg: float, gimbal_pitch_deg: float,
                       roll_deg: float = 0.0):
    """Approximate omega/phi/kappa [deg] for a gimballed frame.

    Convention (stated explicitly because SfM packages disagree):

    * Object frame is ENU, right-handed, Z up.
    * Image frame is x -> right, y -> up in the picture, z -> *away from the
      scene* (out of the back of the camera). This is the usual photogrammetric
      convention, and it is the one that makes R the identity for a nadir shot
      flown due North -- the standard sanity check. The optical (viewing) axis
      is therefore -z_img.
    * ``azimuth_deg`` : aircraft/camera yaw, compass degrees clockwise from
      North.
    * ``gimbal_pitch_deg`` : DJI convention, 0 = horizon, -90 = straight down.
    * The rotation matrix maps image coordinates to object coordinates and is
      decomposed as R = Rx(omega) . Ry(phi) . Rz(kappa).

    With this convention a nadir frame gives omega = phi = 0 and kappa =
    -azimuth. This is a rigid derivation from the yaw/pitch/roll triad, not a
    lookup, but the caller must still confirm the sign convention against the
    target SfM package before using these as fixed exterior orientations. The
    mission report prints the convention next to the values.
    """
    # Tilt away from nadir; 0 for a straight-down shot.
    tau = math.radians(90.0 + gimbal_pitch_deg)
    az = math.radians(azimuth_deg)
    rol = math.radians(roll_deg)

    # +z_img: opposite the viewing direction, so it is straight up at nadir.
    z_img = (-math.sin(tau) * math.sin(az),
             -math.sin(tau) * math.cos(az),
             math.cos(tau))

    # +y_img ("up" in the picture): forward horizontal direction, tilted to
    # stay perpendicular to the optical axis.
    up = (math.cos(tau) * math.sin(az),
          math.cos(tau) * math.cos(az),
          math.sin(tau))

    # +x_img completes the right-handed triad: x = y cross z.
    right = (up[1] * z_img[2] - up[2] * z_img[1],
             up[2] * z_img[0] - up[0] * z_img[2],
             up[0] * z_img[1] - up[1] * z_img[0])

    # Camera roll about the optical axis.
    cr, sr = math.cos(rol), math.sin(rol)
    right_r = tuple(cr * right[i] + sr * up[i] for i in range(3))
    up_r = tuple(-sr * right[i] + cr * up[i] for i in range(3))

    # Columns of R are the image axes expressed in the object frame.
    r11, r12, r13 = right_r[0], up_r[0], z_img[0]
    r21, r22, r23 = right_r[1], up_r[1], z_img[1]
    r33 = z_img[2]

    # Decompose R = Rx(omega) . Ry(phi) . Rz(kappa).
    phi = math.asin(max(-1.0, min(1.0, r13)))
    if abs(math.cos(phi)) < 1e-9:            # gimbal lock, phi = +/-90 deg
        omega = math.atan2(r21, r22)
        kappa = 0.0
    else:
        omega = math.atan2(-r23, r33)
        kappa = math.atan2(-r12, r11)
    return math.degrees(omega), math.degrees(phi), math.degrees(kappa)


# --------------------------------------------------------------------------
# Solved survey geometry
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SurveyGeometry:
    """Everything the router needs, derived once from camera + height + overlap."""

    camera: Camera
    orientation: str
    h_agl_m: float
    gsd_m_px: float
    footprint_across_m: float
    footprint_along_m: float
    d_side_m: float
    d_front_m: float
    overlap: Overlap

    @property
    def gsd_cm_px(self) -> float:
        return 100.0 * self.gsd_m_px

    def interval_at_speed(self, speed_ms: float) -> float:
        """Shot interval [s] needed to hold D_front at the given ground speed."""
        if speed_ms <= 0:
            raise PhotogrammetryError("speed must be > 0")
        return self.d_front_m / speed_ms

    def gsd_at_agl(self, agl_m: float) -> float:
        """Effective GSD for a frame whose actual AGL differs from nominal.

        Used to verify the acceptance criterion that every photo stays within
        [GSD_target, GSD_target * 1.15].
        """
        return gsd_from_height(self.camera, agl_m, self.orientation)

    def max_agl_for_gsd_tolerance(self, tolerance: float = 0.15) -> float:
        """Highest AGL still inside the GSD tolerance band."""
        return self.h_agl_m * (1.0 + tolerance)


def solve_survey_geometry(camera: Camera,
                          overlap: Overlap,
                          h_agl_m: Optional[float] = None,
                          gsd_m_px: Optional[float] = None,
                          orientation: str = ORIENT_ACROSS) -> SurveyGeometry:
    """Resolve the survey geometry from either a target height or a target GSD.

    Exactly one of ``h_agl_m`` / ``gsd_m_px`` must be given; the other is
    derived. Supplying both is rejected rather than silently preferring one,
    because a mismatch there is a planning error the operator needs to see.
    """
    if (h_agl_m is None) == (gsd_m_px is None):
        raise PhotogrammetryError(
            "Provide exactly one of h_agl_m or gsd_m_px (the other is derived).")

    if h_agl_m is None:
        h_agl_m = height_from_gsd(camera, gsd_m_px, orientation)
    else:
        gsd_m_px = gsd_from_height(camera, h_agl_m, orientation)

    across, along = footprint(camera, h_agl_m, orientation)
    return SurveyGeometry(
        camera=camera,
        orientation=orientation,
        h_agl_m=h_agl_m,
        gsd_m_px=gsd_m_px,
        footprint_across_m=across,
        footprint_along_m=along,
        d_side_m=strip_spacing(across, overlap.sidelap),
        d_front_m=shot_spacing(along, overlap.frontlap),
        overlap=overlap,
    )


def build_speed_budget(geometry: SurveyGeometry,
                       v_mission_ms: float,
                       v_drone_max_ms: float,
                       blur_px_max: float = 1.5,
                       v_regulatory_ms: Optional[float] = None,
                       use_interval_trigger: bool = True) -> SpeedBudget:
    """Assemble every speed ceiling for the mission.

    ``use_interval_trigger`` False means the payload is triggered by distance
    (the flight controller fires on odometry), so the interval ceiling does not
    apply -- but the payload's own minimum interval still does, which is why it
    is folded into the same term.
    """
    v_blur = blur_speed_limit(geometry.gsd_m_px,
                              geometry.camera.shutter_s, blur_px_max)
    v_trigger = (trigger_speed_limit(geometry.d_front_m,
                                     geometry.camera.min_interval_s)
                 if use_interval_trigger else None)
    return SpeedBudget(
        v_mission=v_mission_ms,
        v_blur=v_blur,
        v_trigger=v_trigger,
        v_drone_max=v_drone_max_ms,
        v_regulatory=v_regulatory_ms,
    )
