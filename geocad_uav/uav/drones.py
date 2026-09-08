"""
Drone profile library.

Mirrors ``uav.cameras``: presets live in ``profiles/drones.json`` so an
operator can add their own airframe without touching code, and a user file
merges on top.

PURE PYTHON: no QGIS import.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from ..core.models import DroneProfile

_BUNDLED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "profiles", "drones.json")

_REQUIRED = ("key", "name")


class DroneLibraryError(ValueError):
    """Raised when a drone definition file is malformed."""


def drone_from_dict(entry: dict) -> DroneProfile:
    missing = [k for k in _REQUIRED if k not in entry]
    if missing:
        raise DroneLibraryError(
            "Drone entry {0!r} is missing required keys: {1}".format(
                entry.get("name", "<unnamed>"), ", ".join(missing)))
    kind = str(entry.get("kind", "multirotor"))
    if kind not in ("multirotor", "fixedwing"):
        raise DroneLibraryError(
            "Drone {0!r}: kind must be 'multirotor' or 'fixedwing', got "
            "{1!r}".format(entry["name"], kind))
    try:
        profile = DroneProfile(
            key=str(entry["key"]),
            name=str(entry["name"]),
            kind=kind,
            v_max_ms=float(entry.get("v_max_ms", 15.0)),
            v_cruise_ms=float(entry.get("v_cruise_ms", 8.0)),
            climb_ms=float(entry.get("climb_ms", 4.0)),
            descent_ms=float(entry.get("descent_ms", 3.0)),
            endurance_min=float(entry.get("endurance_min", 25.0)),
            waypoint_limit=int(entry.get("waypoint_limit", 99)),
            rth_reserve_pct=float(entry.get("rth_reserve_pct", 30.0)),
            max_agl_m=float(entry.get("max_agl_m", 120.0)),
            battery_wh=(float(entry["battery_wh"])
                        if entry.get("battery_wh") is not None else None),
            turn_radius_m=float(entry.get("turn_radius_m", 0.0)),
            export_formats=tuple(entry.get("export_formats", ())),
            source=str(entry.get("source", "")),
            notes=str(entry.get("notes", "")),
        )
    except (TypeError, ValueError) as exc:
        raise DroneLibraryError(
            "Drone entry {0!r} has an invalid value: {1}".format(
                entry.get("name", "<unnamed>"), exc)) from exc

    for field_name in ("v_max_ms", "v_cruise_ms", "climb_ms", "descent_ms",
                       "endurance_min"):
        if getattr(profile, field_name) <= 0:
            raise DroneLibraryError(
                "Drone {0!r}: {1} must be > 0".format(profile.name, field_name))
    if not 0.0 <= profile.rth_reserve_pct < 100.0:
        raise DroneLibraryError(
            "Drone {0!r}: rth_reserve_pct must be in [0, 100)".format(profile.name))
    if profile.waypoint_limit < 2:
        raise DroneLibraryError(
            "Drone {0!r}: waypoint_limit must be at least 2".format(profile.name))
    return profile


def load_library(user_path: Optional[str] = None) -> "dict[str, DroneProfile]":
    """Load bundled profiles, then merge ``user_path`` on top if it exists."""
    library: "dict[str, DroneProfile]" = {}
    for path in (_BUNDLED, user_path):
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise DroneLibraryError(
                "Cannot read drone library {0}: {1}".format(path, exc)) from exc
        for entry in payload.get("drones", []):
            library[str(entry["key"])] = drone_from_dict(entry)
    if not library:
        raise DroneLibraryError(
            "No drone profiles found. Expected {0}".format(_BUNDLED))
    return library


def check_drone(profile: DroneProfile) -> "list[str]":
    """Warnings about a profile that would otherwise bite during a flight."""
    warnings: "list[str]" = []
    if profile.v_cruise_ms > profile.v_max_ms:
        warnings.append(
            "Drone '{0}': la velocita' di crociera ({1:g} m/s) supera la "
            "massima dichiarata ({2:g} m/s).".format(
                profile.name, profile.v_cruise_ms, profile.v_max_ms))
    if profile.is_fixed_wing and profile.turn_radius_m <= 0:
        warnings.append(
            "Drone '{0}': ala fissa senza raggio di virata. Le virate a U "
            "verranno pianificate come per un multirotore, e non saranno "
            "eseguibili.".format(profile.name))
    if not profile.is_fixed_wing and profile.turn_radius_m > 0:
        warnings.append(
            "Drone '{0}': raggio di virata impostato su un multirotore; verra' "
            "usato per allargare le virate.".format(profile.name))
    if profile.rth_reserve_pct < 20.0:
        warnings.append(
            "Drone '{0}': riserva RTH del {1:g} % piu' bassa del 20 % "
            "raccomandato.".format(profile.name, profile.rth_reserve_pct))
    if not profile.source:
        warnings.append(
            "Drone '{0}': nessuna fonte dichiarata per i parametri.".format(
                profile.name))
    return warnings


def describe(profile: DroneProfile) -> str:
    return ("{0} | {1} | Vmax {2:g} m/s | salita {3:g} / discesa {4:g} m/s | "
            "autonomia {5:g} min (utile {6:.0f} min) | max {7} waypoint").format(
        profile.name, profile.kind, profile.v_max_ms, profile.climb_ms,
        profile.descent_ms, profile.endurance_min,
        profile.usable_endurance_s / 60.0, profile.waypoint_limit)
