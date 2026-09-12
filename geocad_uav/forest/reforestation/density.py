"""
M05 -- density and spacing, each computable from the other.

A scheme can be stated two ways and a forester uses both in the same
sentence: "milleduecento piante per ettaro" and "tre metri per tre". They are
the same statement, and this module is the conversion, in both directions,
with the pattern taken into account -- a triangular scheme at 3 m carries
more plants per hectare than a square one at 3 m, because its cell is not a
square.

The cell area itself is not redefined here: ``core.grid.GridSpec`` already
knows what area one lattice point represents under each pattern, and this
module asks it. What it adds is the inverse -- the spacing that produces a
wanted density -- and the one thing that only matters on a hillside: a map
hectare and a ground hectare are not the same hectare, so a density is
meaningless until it says which one it is counted on.

Two slope relations, deliberately both offered and separately named, because
they answer different questions and disagree:

* :func:`map_density_from_real` -- what the generator actually produces. It
  shortens *both* steps by cos(theta) (``spacing.STEP_MAX_SLOPE``, the
  convention a scheme is stated in), so each cell covers ``A cos^2(theta)``
  of map and the density per map hectare is ``D / cos^2(theta)``.
* :func:`surface_density_from_real` -- what the ground surface implies. A
  plane dipping theta has ``A / cos(theta)`` of surface under every map
  hectare, so planting it at D per ground hectare needs ``D / cos(theta)``
  per map hectare.

The first is larger: the isotropic correction shortens the across-slope step
as well, which the ground does not. That is a property of the stated
convention, not an error, and it is why both numbers are reported rather than
one being quietly picked.
"""

from __future__ import annotations

import math
from typing import Optional

from ...core import grid as grid_mod
from ...core.errors import InvalidInputError

M2_PER_HA = 10_000.0

#: dy/dx forced by the pattern, or None when the operator chooses it.
PATTERN_RATIO = {
    grid_mod.PATTERN_SQUARE: 1.0,
    grid_mod.PATTERN_HEX: math.sqrt(3.0) / 2.0,
}


def _positive(value, name: str, italian: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise InvalidInputError(
            "{0} must be > 0, got {1!r}".format(name, value),
            user_message="{0} deve essere maggiore di zero.".format(italian))
    return number


def _checked_pattern(pattern: str) -> str:
    from .spacing import ALL_PATTERNS, BASE_PATTERN          # noqa: PLC0415

    if pattern not in ALL_PATTERNS:
        raise InvalidInputError(
            "unknown pattern {0!r}".format(pattern),
            user_message="Schema d'impianto non riconosciuto: '{0}'.".format(
                pattern),
            hint="Usa: {0}.".format(", ".join(ALL_PATTERNS)))
    return BASE_PATTERN.get(pattern, pattern)


def pattern_ratio(pattern: str, ratio: float = 1.0) -> float:
    """The dy/dx a pattern imposes, or the one the operator asked for.

    Square and hexagonal schemes fix it -- a square whose rows are 4 m apart
    and whose plants are 3 m apart is not a square -- so a ratio passed for
    those is ignored rather than silently producing something that is not the
    scheme its name claims.
    """
    base = _checked_pattern(pattern)
    forced = PATTERN_RATIO.get(base)
    if forced is not None:
        return forced
    return _positive(ratio, "ratio", "Il rapporto fra le distanze")


# --------------------------------------------------------------------------
# Spacing -> density
# --------------------------------------------------------------------------

def area_per_plant_m2(pattern: str, plant_distance_m: float,
                      row_distance_m: Optional[float] = None) -> float:
    """Area one plant occupies, under this pattern, in the stated units.

    Straight from ``core.grid.GridSpec.area_per_point_m2``: the pattern rules
    live there, and a second copy of "hexagonal means dy = dx sqrt(3)/2" is
    exactly the kind of thing that drifts.
    """
    base = _checked_pattern(pattern)
    plant = _positive(plant_distance_m, "plant_distance_m",
                      "La distanza fra le piante")
    row = _positive(row_distance_m if row_distance_m is not None
                    else plant_distance_m, "row_distance_m",
                    "La distanza fra le file")
    spec = grid_mod.GridSpec(spacing_x=plant, spacing_y=row, pattern=base)
    return spec.area_per_point_m2()


def density_from_spacing(pattern: str, plant_distance_m: float,
                         row_distance_m: Optional[float] = None) -> float:
    """Plants per hectare, counted on the same surface the distances are."""
    return M2_PER_HA / area_per_plant_m2(pattern, plant_distance_m,
                                         row_distance_m)


# --------------------------------------------------------------------------
# Density -> spacing
# --------------------------------------------------------------------------

def spacing_from_density(density_per_ha: float, pattern: str,
                         ratio: float = 1.0):
    """``(plant_distance, row_distance)`` giving exactly this density.

    ``ratio`` is dy/dx, and only a pattern that does not fix it will listen:
    at 1.0 a rectangular scheme comes back square, which is the sensible
    thing to hand an operator who has only said "1 200 piante per ettaro".
    """
    density = _positive(density_per_ha, "density_per_ha",
                        "La densita' d'impianto")
    k = pattern_ratio(pattern, ratio)
    area = M2_PER_HA / density
    plant = math.sqrt(area / k)
    return plant, k * plant


def plant_distance_from_density(density_per_ha: float, pattern: str,
                                ratio: float = 1.0) -> float:
    return spacing_from_density(density_per_ha, pattern, ratio)[0]


def plants_for_area(density_per_ha: float, area_m2: float) -> float:
    """How many plants an area takes at a density. Not rounded: the caller
    decides whether a third of a plant is rounded up or down, and why."""
    return _positive(density_per_ha, "density_per_ha",
                     "La densita' d'impianto") * (float(area_m2) / M2_PER_HA)


# --------------------------------------------------------------------------
# The hillside: which hectare is being counted
# --------------------------------------------------------------------------

def _cos(slope_deg: float) -> float:
    slope = float(slope_deg)
    if not math.isfinite(slope):
        raise InvalidInputError(
            "slope must be a finite angle, got {0!r}".format(slope_deg),
            user_message="La pendenza deve essere un angolo valido.")
    if not -90.0 < slope < 90.0:
        raise InvalidInputError(
            "slope must be between -90 and 90 degrees, got {0!r}".format(
                slope_deg),
            user_message="La pendenza deve essere compresa fra -90 e 90 "
                         "gradi.")
    return math.cos(math.radians(slope))


def surface_area_m2(map_area_m2: float, slope_deg: float) -> float:
    """Ground surface under a map area, on a plane of this dip."""
    return float(map_area_m2) / _cos(slope_deg)


def map_density_from_real(density_per_ha: float, slope_deg: float) -> float:
    """Plants per *map* hectare produced by the isotropic correction.

    Both steps are shortened by cos(theta), so the cell covers cos^2(theta)
    of the map area it covers of ground.
    """
    factor = _cos(slope_deg)
    return _positive(density_per_ha, "density_per_ha",
                     "La densita' d'impianto") / (factor * factor)


def real_density_from_map(density_per_ha: float, slope_deg: float) -> float:
    """The inverse of :func:`map_density_from_real`."""
    factor = _cos(slope_deg)
    return _positive(density_per_ha, "density_per_ha",
                     "La densita' d'impianto") * factor * factor


def surface_density_from_real(density_per_ha: float,
                              slope_deg: float) -> float:
    """Plants per map hectare implied by the ground surface itself.

    The honest geometry of a plane: only the down-slope axis is foreshortened
    on the map, so a map hectare carries 1/cos(theta) hectares of ground.
    Smaller than :func:`map_density_from_real`, and the difference is the
    price of stating a scheme isotropically.
    """
    return _positive(density_per_ha, "density_per_ha",
                     "La densita' d'impianto") / _cos(slope_deg)


def isotropy_excess(slope_deg: float) -> float:
    """How much denser the isotropic convention plants than the surface.

    ``1/cos(theta) - 1`` as a fraction: zero on the flat, 15.5 % at 30
    degrees. Reported so an operator can see the number rather than discover
    it in the nursery invoice.
    """
    return 1.0 / _cos(slope_deg) - 1.0


# --------------------------------------------------------------------------
# Readout
# --------------------------------------------------------------------------

def describe(pattern: str, plant_distance_m: float,
             row_distance_m: Optional[float] = None,
             slope_deg: float = 0.0) -> "list[str]":
    from .spacing import PATTERN_LABELS                      # noqa: PLC0415

    area = area_per_plant_m2(pattern, plant_distance_m, row_distance_m)
    real = M2_PER_HA / area
    lines = [
        "DENSITA' D'IMPIANTO",
        "  Schema:                {0}".format(
            PATTERN_LABELS.get(pattern, pattern)),
        "  Area per pianta:       {0:,.3f} m2".format(area),
        "  Densita' sul terreno:  {0:,.1f} piante/ha".format(real),
    ]
    if abs(float(slope_deg)) > 1e-9:
        lines.append("  Pendenza:              {0:.1f} deg".format(slope_deg))
        lines.append("  Densita' sulla carta:  {0:,.1f} piante/ha".format(
            map_density_from_real(real, slope_deg)))
        lines.append("  Superficie reale:      {0:,.1f} m2 per ettaro di "
                     "carta".format(surface_area_m2(M2_PER_HA, slope_deg)))
        lines.append("  Eccesso isotropo:      {0:+.2%}".format(
            isotropy_excess(slope_deg)))
    return lines
