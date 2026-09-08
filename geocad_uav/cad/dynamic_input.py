"""
Dynamic input parser: type a constraint instead of clicking one.

Grammar (spec section 4)::

    25          length in the current unit
    25m 40cm    length with an explicit unit
    37d         angle / azimuth
    0.6rad      angle in another unit
    @25<37      polar: 25 along a bearing 37 deg off the previous segment
    #100,200    absolute coordinates in the working CRS
    @10,-5      cartesian delta from the last point

The parser is pure text -> value; it never touches the canvas. Resolving a
parsed token into a coordinate needs the drawing state (last point, last
bearing), which is passed in explicitly by :func:`resolve`, so the whole thing
stays testable without a QGIS runtime.

Decimal separator is the point. The comma is the coordinate separator, so it
cannot also be a decimal mark.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..core.errors import InvalidInputError
from ..core.planar import along_track_unit
from ..core.units import (ANGLE_ALIASES, ANGLE_TO_DEGREES, LENGTH_ALIASES,
                          LENGTH_TO_METRES, to_degrees, to_metres)

# Token kinds.
KIND_LENGTH = "length"
KIND_ANGLE = "angle"
KIND_POLAR = "polar"
KIND_ABSOLUTE = "absolute"
KIND_RELATIVE = "relative"

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_LENGTH_UNITS = sorted(set(list(LENGTH_TO_METRES) + list(LENGTH_ALIASES)),
                       key=len, reverse=True)
_ANGLE_UNITS = sorted(set(list(ANGLE_TO_DEGREES) + list(ANGLE_ALIASES)),
                      key=len, reverse=True)
_LEN_ALT = "|".join(re.escape(u) for u in _LENGTH_UNITS)
_ANG_ALT = "|".join(re.escape(u) for u in _ANGLE_UNITS)

_RE_ABSOLUTE = re.compile(
    r"^#\s*(?P<x>{n})\s*,\s*(?P<y>{n})\s*$".format(n=_NUM), re.I)
_RE_POLAR = re.compile(
    r"^@\s*(?P<d>{n})\s*(?:{lu})?\s*<\s*(?P<a>{n})\s*(?P<au>{au})?\s*$".format(
        n=_NUM, lu=_LEN_ALT, au=_ANG_ALT), re.I)
_RE_RELATIVE = re.compile(
    r"^@\s*(?P<dx>{n})\s*,\s*(?P<dy>{n})\s*$".format(n=_NUM), re.I)
_RE_ANGLE = re.compile(
    r"^(?P<v>{n})\s*(?P<u>{au})\s*$".format(n=_NUM, au=_ANG_ALT), re.I)
_RE_LENGTH = re.compile(
    r"^(?P<v>{n})\s*(?P<u>{lu})?\s*$".format(n=_NUM, lu=_LEN_ALT), re.I)


@dataclass(frozen=True)
class DynamicInput:
    """One parsed constraint token. Lengths are metres, angles degrees."""

    kind: str
    raw: str
    length_m: Optional[float] = None
    angle_deg: Optional[float] = None
    dx_m: Optional[float] = None
    dy_m: Optional[float] = None
    x_m: Optional[float] = None
    y_m: Optional[float] = None
    #: True when the polar angle is measured from the previous segment rather
    #: than from grid north.
    angle_is_relative: bool = False

    def describe(self) -> str:
        """Short Italian echo for the HUD, so the operator can confirm it."""
        if self.kind == KIND_LENGTH:
            return "Lunghezza {0:.3f} m".format(self.length_m)
        if self.kind == KIND_ANGLE:
            return "Angolo {0:.4f} deg".format(self.angle_deg)
        if self.kind == KIND_POLAR:
            ref = "dal segmento precedente" if self.angle_is_relative else "da Nord"
            return "Polare {0:.3f} m a {1:.4f} deg {2}".format(
                self.length_m, self.angle_deg, ref)
        if self.kind == KIND_ABSOLUTE:
            return "Assoluto ({0:.3f}, {1:.3f})".format(self.x_m, self.y_m)
        return "Delta ({0:+.3f}, {1:+.3f})".format(self.dx_m, self.dy_m)


def parse(text: str, length_unit: str = "m",
          angle_unit: str = "deg") -> DynamicInput:
    """Parse one input token. Raises :class:`InvalidInputError` if malformed.

    ``length_unit`` / ``angle_unit`` are the panel's current units, used when
    the token carries no explicit unit of its own.
    """
    if text is None:
        raise InvalidInputError(
            "empty dynamic input",
            user_message="Nessun valore inserito.")
    raw = str(text).strip()
    if not raw:
        raise InvalidInputError(
            "empty dynamic input",
            user_message="Nessun valore inserito.")

    match = _RE_ABSOLUTE.match(raw)
    if match:
        return DynamicInput(
            kind=KIND_ABSOLUTE, raw=raw,
            x_m=to_metres(float(match.group("x")), length_unit),
            y_m=to_metres(float(match.group("y")), length_unit))

    match = _RE_POLAR.match(raw)
    if match:
        unit = match.group("au") or angle_unit
        return DynamicInput(
            kind=KIND_POLAR, raw=raw,
            length_m=to_metres(float(match.group("d")), length_unit),
            angle_deg=to_degrees(float(match.group("a")), unit),
            angle_is_relative=True)

    match = _RE_RELATIVE.match(raw)
    if match:
        return DynamicInput(
            kind=KIND_RELATIVE, raw=raw,
            dx_m=to_metres(float(match.group("dx")), length_unit),
            dy_m=to_metres(float(match.group("dy")), length_unit))

    match = _RE_ANGLE.match(raw)
    if match:
        return DynamicInput(
            kind=KIND_ANGLE, raw=raw,
            angle_deg=to_degrees(float(match.group("v")), match.group("u")))

    match = _RE_LENGTH.match(raw)
    if match:
        unit = match.group("u") or length_unit
        value = to_metres(float(match.group("v")), unit)
        if value <= 0.0:
            raise InvalidInputError(
                "length must be > 0, got {0!r}".format(value),
                user_message="La lunghezza deve essere maggiore di zero.")
        return DynamicInput(kind=KIND_LENGTH, raw=raw, length_m=value)

    raise InvalidInputError(
        "unparseable dynamic input {0!r}".format(raw),
        user_message="Input '{0}' non riconosciuto.".format(raw),
        hint="Formati validi: 25 | 25m | 37d | @25<37 | #100,200 | @10,-5")


def resolve(token: DynamicInput, last_point=None,
            last_azimuth_deg: Optional[float] = None,
            pending_length_m: Optional[float] = None) -> np.ndarray:
    """Turn a parsed token into an absolute ``(x, y)`` in the working CRS.

    ``last_azimuth_deg`` is the bearing of the previously drawn segment, needed
    for polar input. When there is no previous segment the polar angle is taken
    from grid north instead, which is the only sensible reading of "37 deg off
    nothing" -- and it is reported as such by :meth:`DynamicInput.describe`.

    ``pending_length_m`` lets a bare angle token complete a length typed
    earlier (the Tab-cycling workflow: length, then angle, then Enter).
    """
    if token.kind == KIND_ABSOLUTE:
        return np.array([token.x_m, token.y_m], dtype=float)

    if last_point is None:
        raise InvalidInputError(
            "{0} input needs a previous point".format(token.kind),
            user_message="Serve un punto di partenza per questo tipo di input.",
            hint="Indica prima un punto sulla mappa, oppure usa #x,y.")
    origin = np.asarray(last_point, dtype=float).reshape(-1)[:2]

    if token.kind == KIND_RELATIVE:
        return origin + np.array([token.dx_m, token.dy_m], dtype=float)

    if token.kind == KIND_POLAR:
        base = last_azimuth_deg if (token.angle_is_relative
                                    and last_azimuth_deg is not None) else 0.0
        ux, uy = along_track_unit(base + token.angle_deg)
        return origin + token.length_m * np.array([ux, uy])

    if token.kind == KIND_LENGTH:
        if last_azimuth_deg is None:
            raise InvalidInputError(
                "a bare length needs a direction",
                user_message="Una lunghezza da sola non basta: serve una direzione.",
                hint="Muovi il cursore per fissare la direzione, oppure usa @25<37.")
        ux, uy = along_track_unit(last_azimuth_deg)
        return origin + token.length_m * np.array([ux, uy])

    if token.kind == KIND_ANGLE:
        if pending_length_m is None:
            raise InvalidInputError(
                "a bare angle needs a length",
                user_message="Un angolo da solo non basta: serve una lunghezza.",
                hint="Inserisci prima la lunghezza, poi l'angolo (Tab).")
        ux, uy = along_track_unit(token.angle_deg)
        return origin + float(pending_length_m) * np.array([ux, uy])

    raise InvalidInputError(
        "unknown token kind {0!r}".format(token.kind),
        user_message="Tipo di input non gestito.")


def segment_azimuth(p_from, p_to) -> float:
    """Compass bearing of a drawn segment, for use as the polar reference."""
    a = np.asarray(p_from, dtype=float).reshape(-1)[:2]
    b = np.asarray(p_to, dtype=float).reshape(-1)[:2]
    d = b - a
    if float(np.hypot(*d)) < 1e-12:
        raise InvalidInputError(
            "cannot take the bearing of a zero-length segment",
            user_message="Segmento di lunghezza nulla: direzione non definita.")
    return math.degrees(math.atan2(float(d[0]), float(d[1]))) % 360.0
