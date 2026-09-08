"""
Unit handling: storage is always SI, display is whatever the operator wants.

The hard rule (spec section 10) is *display is not storage*. Everything inside
the plugin is metres and decimal degrees in the working CRS; conversion happens
only at the edges, when a value is shown or parsed. That keeps rounding out of
the geometry: a rectangle entered as 25 ft is built from 7.62 m exactly, not
from a re-rounded 7.620 m.

Factors are exact by definition (international foot/inch since 1959), so a
round trip through any unit is lossless to float64.
"""

from __future__ import annotations

from typing import Optional

from .errors import UnitError

# --------------------------------------------------------------------------
# Length
# --------------------------------------------------------------------------

#: metres per unit; every factor is exact.
LENGTH_TO_METRES = {
    "m": 1.0,
    "cm": 0.01,
    "mm": 0.001,
    "km": 1000.0,
    "ft": 0.3048,          # international foot, exact since 1959
    "in": 0.0254,          # international inch, exact
}

LENGTH_ALIASES = {
    "metre": "m", "metres": "m", "meter": "m", "meters": "m", "metri": "m",
    "centimetre": "cm", "centimetres": "cm", "centimetri": "cm",
    "millimetre": "mm", "millimetres": "mm", "millimetri": "mm",
    "kilometre": "km", "kilometres": "km", "chilometri": "km",
    "foot": "ft", "feet": "ft", "piedi": "ft",
    "inch": "in", "inches": "in", "pollici": "in",
    '"': "in", "'": "ft",
}

#: Sensible display precision per unit, so 1 mm is not shown as 1.000 mm.
LENGTH_DECIMALS = {"m": 3, "cm": 1, "mm": 0, "km": 4, "ft": 3, "in": 2}


def normalise_length_unit(unit: str) -> str:
    """Canonical symbol for a length unit, accepting common spellings."""
    if unit is None:
        raise UnitError("length unit is None")
    key = str(unit).strip().lower()
    key = LENGTH_ALIASES.get(key, key)
    if key not in LENGTH_TO_METRES:
        raise UnitError(
            "unknown length unit {0!r}".format(unit),
            user_message="Unita' di lunghezza non riconosciuta: '{0}'.".format(unit),
            hint="Usa una fra: {0}.".format(", ".join(sorted(LENGTH_TO_METRES))))
    return key


def to_metres(value: float, unit: str = "m") -> float:
    """Convert a length from ``unit`` into metres (storage)."""
    return float(value) * LENGTH_TO_METRES[normalise_length_unit(unit)]


def from_metres(value_m: float, unit: str = "m") -> float:
    """Convert a length from metres (storage) into ``unit`` (display)."""
    return float(value_m) / LENGTH_TO_METRES[normalise_length_unit(unit)]


def convert_length(value: float, src: str, dst: str) -> float:
    return from_metres(to_metres(value, src), dst)


def format_length(value_m: float, unit: str = "m",
                  decimals: Optional[int] = None) -> str:
    """Human string for a stored length, e.g. ``"25.000 m"``."""
    unit = normalise_length_unit(unit)
    if decimals is None:
        decimals = LENGTH_DECIMALS[unit]
    return "{0:.{1}f} {2}".format(from_metres(value_m, unit), decimals, unit)


# --------------------------------------------------------------------------
# Area
# --------------------------------------------------------------------------

#: square metres per unit.
AREA_TO_SQ_METRES = {
    "m2": 1.0,
    "km2": 1.0e6,
    "ha": 10_000.0,
    "a": 100.0,                       # are
    "ft2": 0.3048 ** 2,
    "acre": 4046.8564224,             # exact: 4840 sq yd of 0.9144 m
}

AREA_ALIASES = {
    "sqm": "m2", "m^2": "m2", "mq": "m2",
    "sqkm": "km2", "km^2": "km2",
    "hectare": "ha", "hectares": "ha", "ettari": "ha", "ettaro": "ha",
    "sqft": "ft2", "ft^2": "ft2",
    "acres": "acre",
}


def normalise_area_unit(unit: str) -> str:
    key = str(unit).strip().lower()
    key = AREA_ALIASES.get(key, key)
    if key not in AREA_TO_SQ_METRES:
        raise UnitError(
            "unknown area unit {0!r}".format(unit),
            user_message="Unita' di superficie non riconosciuta: '{0}'.".format(unit),
            hint="Usa una fra: {0}.".format(", ".join(sorted(AREA_TO_SQ_METRES))))
    return key


def area_to_sq_metres(value: float, unit: str = "m2") -> float:
    return float(value) * AREA_TO_SQ_METRES[normalise_area_unit(unit)]


def area_from_sq_metres(value_m2: float, unit: str = "m2") -> float:
    return float(value_m2) / AREA_TO_SQ_METRES[normalise_area_unit(unit)]


# --------------------------------------------------------------------------
# Angle
# --------------------------------------------------------------------------

import math  # noqa: E402  (kept next to the angle helpers that use it)

#: degrees per unit.
ANGLE_TO_DEGREES = {
    "deg": 1.0,
    "rad": 180.0 / math.pi,
    "gon": 0.9,               # 400 gon == 360 deg
}

ANGLE_ALIASES = {
    "degree": "deg", "degrees": "deg", "gradi": "deg", "d": "deg", "°": "deg",
    "radian": "rad", "radians": "rad", "radianti": "rad", "r": "rad",
    "grad": "gon", "grads": "gon", "gradian": "gon", "gradians": "gon",
}


def normalise_angle_unit(unit: str) -> str:
    key = str(unit).strip().lower()
    key = ANGLE_ALIASES.get(key, key)
    if key not in ANGLE_TO_DEGREES:
        raise UnitError(
            "unknown angle unit {0!r}".format(unit),
            user_message="Unita' angolare non riconosciuta: '{0}'.".format(unit),
            hint="Usa una fra: deg, rad, gon.")
    return key


def to_degrees(value: float, unit: str = "deg") -> float:
    return float(value) * ANGLE_TO_DEGREES[normalise_angle_unit(unit)]


def from_degrees(value_deg: float, unit: str = "deg") -> float:
    return float(value_deg) / ANGLE_TO_DEGREES[normalise_angle_unit(unit)]


def wrap_degrees(value_deg: float, period: float = 360.0) -> float:
    """Wrap an angle into [0, period). ``period=180`` for undirected lines."""
    return float(value_deg) % period


def wrap_signed(value_deg: float) -> float:
    """Wrap an angle into (-180, 180]."""
    wrapped = (float(value_deg) + 180.0) % 360.0 - 180.0
    return 180.0 if wrapped == -180.0 else wrapped


# --------------------------------------------------------------------------
# Speed
# --------------------------------------------------------------------------

SPEED_TO_MS = {
    "m/s": 1.0,
    "km/h": 1.0 / 3.6,
    "kt": 1852.0 / 3600.0,        # exact: 1 nautical mile = 1852 m
    "mph": 1609.344 / 3600.0,     # exact: 1 statute mile = 1609.344 m
}


def speed_to_ms(value: float, unit: str = "m/s") -> float:
    key = str(unit).strip().lower()
    if key not in SPEED_TO_MS:
        raise UnitError(
            "unknown speed unit {0!r}".format(unit),
            user_message="Unita' di velocita' non riconosciuta: '{0}'.".format(unit),
            hint="Usa una fra: m/s, km/h, kt, mph.")
    return float(value) * SPEED_TO_MS[key]


def speed_from_ms(value_ms: float, unit: str = "m/s") -> float:
    key = str(unit).strip().lower()
    if key not in SPEED_TO_MS:
        raise UnitError("unknown speed unit {0!r}".format(unit))
    return float(value_ms) / SPEED_TO_MS[key]


def format_duration(seconds: float) -> str:
    """``"1h 04m 30s"`` / ``"12m 05s"`` / ``"45s"`` for mission times."""
    seconds = max(0.0, float(seconds))
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return "{0}h {1:02d}m {2:02d}s".format(hours, minutes, secs)
    if minutes:
        return "{0}m {1:02d}s".format(minutes, secs)
    return "{0}s".format(secs)
