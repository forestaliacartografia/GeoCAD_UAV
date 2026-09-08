"""
Camera preset library.

Presets live in ``resources/cameras.json`` so an operator can add their own
calibrated payload without touching code. A user-level file at
``<QGIS profile>/uav_flight_planner_cameras.json`` (or any path passed to
``load_library``) is merged on top and wins on key collision.

PURE PYTHON: no QGIS import, so the library is testable standalone.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from .photogrammetry import Camera, PhotogrammetryError

_BUNDLED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "profiles", "cameras.json")

#: Above this disagreement between Sw/Px and Sh/Py the published sensor size and
#: pixel count are mutually inconsistent and the operator must be told.
PITCH_MISMATCH_WARN_PCT = 1.0

_REQUIRED = ("key", "name", "focal_mm", "sensor_w_mm", "sensor_h_mm",
             "image_w_px", "image_h_px")


class CameraLibraryError(ValueError):
    """Raised when a camera definition file is malformed."""


def camera_from_dict(entry: dict) -> Camera:
    """Build a :class:`Camera` from one JSON entry, validating required keys."""
    missing = [k for k in _REQUIRED if k not in entry]
    if missing:
        raise CameraLibraryError(
            "Camera entry {0!r} is missing required keys: {1}".format(
                entry.get("name", entry.get("key", "<unnamed>")),
                ", ".join(missing)))
    try:
        return Camera(
            name=str(entry["name"]),
            focal_mm=float(entry["focal_mm"]),
            sensor_w_mm=float(entry["sensor_w_mm"]),
            sensor_h_mm=float(entry["sensor_h_mm"]),
            image_w_px=int(entry["image_w_px"]),
            image_h_px=int(entry["image_h_px"]),
            shutter_s=float(entry.get("shutter_s", 1.0 / 1000.0)),
            min_interval_s=float(entry.get("min_interval_s", 2.0)),
            mechanical_shutter=bool(entry.get("mechanical_shutter", False)),
            source=str(entry.get("source", "")),
            notes=str(entry.get("notes", "")),
        )
    except (TypeError, ValueError) as exc:
        raise CameraLibraryError(
            "Camera entry {0!r} has an invalid value: {1}".format(
                entry.get("name", "<unnamed>"), exc)) from exc


def load_library(user_path: Optional[str] = None) -> "dict[str, Camera]":
    """Load bundled presets, then merge ``user_path`` on top if it exists."""
    library: "dict[str, Camera]" = {}
    for path in (_BUNDLED, user_path):
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise CameraLibraryError(
                "Cannot read camera library {0}: {1}".format(path, exc)) from exc
        for entry in payload.get("cameras", []):
            library[str(entry["key"])] = camera_from_dict(entry)
    if not library:
        raise CameraLibraryError(
            "No camera presets found. Expected {0}".format(_BUNDLED))
    return library


def check_camera(camera: Camera) -> "list[str]":
    """Return human-readable warnings about a camera definition.

    Currently one check: the two independent pixel-pitch estimates must agree.
    They are computed from numbers that come from different rows of a spec
    sheet, so a mismatch is a transcription error, and it silently biases every
    GSD in the mission.
    """
    warnings: "list[str]" = []
    mismatch = camera.pitch_mismatch_pct
    if mismatch > PITCH_MISMATCH_WARN_PCT:
        warnings.append(
            "Camera '{0}': pixel pitch from sensor width ({1:.4f} mm) and from "
            "sensor height ({2:.4f} mm) disagree by {3:.2f} %. Sensor size and "
            "pixel count are inconsistent -- check the spec sheet, the GSD is "
            "only as good as these numbers.".format(
                camera.name, camera.pitch_w_mm, camera.pitch_h_mm, mismatch))
    if not camera.source:
        warnings.append(
            "Camera '{0}': no 'source' recorded. Mission reports will not be "
            "able to state where the camera parameters came from.".format(
                camera.name))
    return warnings


def describe(camera: Camera) -> str:
    """One-line human description used in reports and the GUI."""
    return ("{0} | f={1:g} mm | sensor {2:g}x{3:g} mm | {4}x{5} px "
            "({6:.1f} MP) | pitch {7:.4f} mm | FOV_diag {8:.1f} deg").format(
        camera.name, camera.focal_mm, camera.sensor_w_mm, camera.sensor_h_mm,
        camera.image_w_px, camera.image_h_px, camera.megapixels,
        camera.pitch_mm, camera.diagonal_fov_deg)
