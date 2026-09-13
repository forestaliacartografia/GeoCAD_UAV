"""
M05 -- a plantation that does not look like a car park.

A lattice is how a plantation is *planned*; it is not how a wood grows. Three
things turn one into the other, and all three are bounded and reproducible,
because "naturalistic" must not mean "nobody can say what will be planted":

* **radure** -- real openings. They are placed first, on the surface, and the
  plants that fall inside them are then removed whole: a glade is a hole in
  the plan and not a thin patch of it. Removing rather than generating around
  them keeps one path through the generator, zones included, and the count
  that comes out is reported rather than inferred;
* **irregolarita' controllata** -- a slow density variation across the stand,
  which *thins* and never adds, so the scheme's own geometry is never
  violated by it;
* **distanza minima garantita** -- the check that survives all of the above:
  after jitter, after mixing, after thinning, no two plants are closer than
  the minimum their species allow.

The minimum distance is the one hard promise. Everything else here makes the
plan less regular; this makes sure that less regular never becomes wrong. It
is enforced by removing plants, never by moving them: a plant moved to fix
one conflict creates another, and the operator asked for a scheme, not for a
relaxation.

Nothing here places a plant. Positions come from :mod:`.spacing`; this module
decides which of them survive, and says how many did not and why.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ...core.errors import EmptyAoiError, InvalidInputError
from . import composition as composition_mod

M2_PER_HA = 10_000.0

#: How hard to try before admitting a glade does not fit. Placement is
#: rejection sampling; a bounded number of misses is normal, an unbounded
#: loop on a full parcel is not.
MAX_PLACEMENT_ATTEMPTS = 200

#: Side of the cell the density field is drawn on, as a multiple of the
#: planting step. Smaller makes the variation nervous, larger makes it
#: invisible; four steps is about a clump.
DENSITY_CELL_STEPS = 4.0

#: Ceiling on the irregularity, as a fraction. Above this the thinning eats
#: so much of the stand that the density asked for stops meaning anything.
MAX_AMPLITUDE = 0.6

REMOVED_GLADE = "radura"
REMOVED_DENSITY = "irregolarita"
REMOVED_MIN_DISTANCE = "distanza_minima"

REMOVAL_LABELS = {
    REMOVED_GLADE: "In una radura",
    REMOVED_DENSITY: "Diradata dall'irregolarita' controllata",
    REMOVED_MIN_DISTANCE: "Sotto la distanza minima",
}


@dataclass
class Glade:
    """One opening in the stand."""

    geometry: object                            # QgsGeometry
    label: str = ""
    radius_m: float = 0.0

    @property
    def area_m2(self) -> float:
        return float(self.geometry.area()) if self.geometry else 0.0


@dataclass
class NaturalSettings:
    """What the operator asked for, in the operator's terms."""

    #: Openings: how many, and how big.
    glade_count: int = 0
    glade_radius_m: float = 0.0
    #: Keep glades this far from the edge and from each other.
    glade_margin_m: float = 0.0
    glade_gap_m: float = 0.0
    #: Density variation, as a fraction: 0.25 means plus or minus a quarter.
    amplitude: float = 0.0
    #: The promise. Zero means "whatever the species say"; a number here
    #: overrides them upwards, never downwards.
    min_distance_m: float = 0.0
    seed: int = 0

    def __post_init__(self) -> None:
        self.glade_count = max(0, int(self.glade_count))
        for name in ("glade_radius_m", "glade_margin_m", "glade_gap_m",
                     "min_distance_m"):
            value = float(getattr(self, name) or 0.0)
            if not math.isfinite(value) or value < 0.0:
                raise InvalidInputError(
                    "{0} must be >= 0, got {1!r}".format(
                        name, getattr(self, name)),
                    user_message="I parametri naturaliformi non possono "
                                 "essere negativi.")
            setattr(self, name, value)
        amplitude = float(self.amplitude or 0.0)
        if not math.isfinite(amplitude) or amplitude < 0.0:
            raise InvalidInputError(
                "amplitude must be >= 0, got {0!r}".format(self.amplitude),
                user_message="L'irregolarita' non puo' essere negativa.")
        self.amplitude = min(amplitude, MAX_AMPLITUDE)
        if self.glade_count and self.glade_radius_m <= 0.0:
            raise InvalidInputError(
                "glades need a radius",
                user_message="Indica il raggio delle radure.")
        self.seed = int(self.seed)

    @property
    def is_active(self) -> bool:
        return bool(self.glade_count or self.amplitude
                    or self.min_distance_m)

    def describe(self) -> "list[str]":
        lines = ["DISTRIBUZIONE NATURALIFORME"]
        if self.glade_count:
            lines.append("  Radure:              {0} da {1:g} m di "
                         "raggio".format(self.glade_count,
                                         self.glade_radius_m))
        if self.amplitude:
            lines.append("  Irregolarita':       +/- {0:.0%}".format(
                self.amplitude))
        if self.min_distance_m:
            lines.append("  Distanza minima:     {0:g} m".format(
                self.min_distance_m))
        lines.append("  Seme:                {0}".format(self.seed))
        return lines


@dataclass
class NaturalOutcome:
    """What survived, and what did not."""

    plants: list = field(default_factory=list)
    glades: list = field(default_factory=list)
    removed: dict = field(default_factory=dict)     # reason -> count
    min_distance_m: float = 0.0
    measured_min_distance_m: float = 0.0
    warnings: list = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.plants)

    @property
    def removed_total(self) -> int:
        return sum(self.removed.values())

    @property
    def glade_area_m2(self) -> float:
        return sum(glade.area_m2 for glade in self.glades)

    def describe(self) -> "list[str]":
        lines = ["ESITO NATURALIFORME",
                 "  Piante rimaste:      {0:,}".format(self.count),
                 "  Piante tolte:        {0:,}".format(self.removed_total)]
        for reason, count in sorted(self.removed.items(),
                                    key=lambda item: -item[1]):
            lines.append("    {0:<40} {1:>7,}".format(
                REMOVAL_LABELS.get(reason, reason), count))
        if self.glades:
            lines.append("  Radure:              {0}, {1:,.4f} ha".format(
                len(self.glades), self.glade_area_m2 / M2_PER_HA))
        if self.min_distance_m:
            lines.append("  Distanza minima chiesta: {0:.3f} m".format(
                self.min_distance_m))
            lines.append("  Distanza minima misurata: {0:.3f} m".format(
                self.measured_min_distance_m))
        lines.extend("  " + text for text in self.warnings)
        return lines


# --------------------------------------------------------------------------
# Glades
# --------------------------------------------------------------------------

def generate_glades(geometry, settings: NaturalSettings) -> "list[Glade]":
    """Place the openings inside a surface, reproducibly.

    Rejection sampling on a seeded generator: a centre is drawn in the
    bounding box, kept if the whole disc fits inside the eroded surface and
    is far enough from the glades already placed. The same seed gives the
    same clearings, which is what makes a printed plan worth anything.

    Fewer glades than asked for is a reported outcome, not a silent one: on a
    narrow parcel there may genuinely be no room for six.
    """
    from qgis.core import QgsGeometry, QgsPointXY                # noqa: PLC0415

    if settings.glade_count <= 0:
        return []
    if geometry is None or geometry.isEmpty():
        raise EmptyAoiError(
            "no surface to put glades in",
            user_message="Nessuna superficie in cui aprire le radure.")

    radius = settings.glade_radius_m
    inner = geometry
    setback = radius + settings.glade_margin_m
    if setback > 0.0:
        inner = geometry.buffer(-setback, 12)
    if inner is None or inner.isEmpty():
        return []

    box = inner.boundingBox()
    rng = np.random.default_rng(settings.seed)
    glades: "list[Glade]" = []
    attempts = 0
    while (len(glades) < settings.glade_count
           and attempts < MAX_PLACEMENT_ATTEMPTS * settings.glade_count):
        attempts += 1
        x = float(rng.uniform(box.xMinimum(), box.xMaximum()))
        y = float(rng.uniform(box.yMinimum(), box.yMaximum()))
        centre = QgsGeometry.fromPointXY(QgsPointXY(x, y))
        if not inner.intersects(centre):
            continue
        if any(math.hypot(x - g.geometry.centroid().constGet().x(),
                          y - g.geometry.centroid().constGet().y())
               < 2.0 * radius + settings.glade_gap_m for g in glades):
            continue
        disc = centre.buffer(radius, 24)
        if disc is None or disc.isEmpty():
            continue
        glades.append(Glade(geometry=disc,
                            label="Radura {0}".format(len(glades) + 1),
                            radius_m=radius))
    return glades


def apply_glades(geometry, glades):
    """The surface with the openings taken out of it."""
    from qgis.core import QgsGeometry                            # noqa: PLC0415

    if not glades or geometry is None or geometry.isEmpty():
        return geometry
    discs = [glade.geometry for glade in glades if glade.geometry]
    if not discs:
        return geometry
    merged = discs[0] if len(discs) == 1 else QgsGeometry.unaryUnion(discs)
    left = geometry.difference(merged)
    if left is None or left.isEmpty():
        raise EmptyAoiError(
            "the glades cover the whole surface",
            user_message="Le radure coprono tutta la superficie utile.",
            hint="Riduci il numero o il raggio delle radure.")
    return left


def plants_in_glades(plants, glades) -> "list":
    """Which plants fall inside an opening. Used when the glades arrive late."""
    from qgis.core import QgsGeometry, QgsPointXY                # noqa: PLC0415

    if not glades:
        return []
    discs = [glade.geometry for glade in glades if glade.geometry]
    merged = discs[0] if len(discs) == 1 else QgsGeometry.unaryUnion(discs)
    inside = []
    for record in plants:
        point = QgsGeometry.fromPointXY(QgsPointXY(record.x, record.y))
        if merged.intersects(point):
            inside.append(record)
    return inside


# --------------------------------------------------------------------------
# Controlled irregularity
# --------------------------------------------------------------------------

def density_field(plants, amplitude: float, cell_m: float, seed: int):
    """A keep-probability per plant, smooth over the stand.

    A coarse grid of seeded multipliers, read at each plant's cell. Slow
    variation on purpose: plant-by-plant randomness is noise, and noise looks
    like a mistake rather than like ground.
    """
    if amplitude <= 0.0 or not plants:
        return np.ones(len(plants), dtype=float)
    xs = np.array([p.x for p in plants], dtype=float)
    ys = np.array([p.y for p in plants], dtype=float)
    cell = max(float(cell_m), 1e-6)
    cols = np.floor((xs - xs.min()) / cell).astype(np.int64)
    rows = np.floor((ys - ys.min()) / cell).astype(np.int64)
    n_cols = int(cols.max()) + 1
    n_rows = int(rows.max()) + 1
    rng = np.random.default_rng(int(seed))
    grid = rng.uniform(1.0 - amplitude, 1.0 + amplitude, size=(n_rows,
                                                               n_cols))
    return np.clip(grid[rows, cols], 0.0, 1.0)


def thin_by_density(plants, settings: NaturalSettings, step_m: float):
    """Drop plants where the density field says the stand is thinner.

    Returns ``(kept, removed)``. Only ever removes: the scheme's geometry is
    the operator's decision and this is not allowed to invent positions.
    """
    if settings.amplitude <= 0.0 or not plants:
        return list(plants), []
    keep_probability = density_field(
        plants, settings.amplitude, DENSITY_CELL_STEPS * max(step_m, 1e-6),
        settings.seed)
    rng = np.random.default_rng(settings.seed + 1)
    draw = rng.random(len(plants))
    kept, removed = [], []
    for record, probability, value in zip(plants, keep_probability, draw):
        (kept if value <= probability else removed).append(record)
    return kept, removed


# --------------------------------------------------------------------------
# The promise
# --------------------------------------------------------------------------

def required_distance(settings: NaturalSettings, catalog=None) -> float:
    """The floor every pair of plants must clear."""
    return max(0.0, float(settings.min_distance_m))


def species_distance(first, second, catalog, floor: float) -> float:
    """The minimum between two particular plants.

    The larger of the two species' own minima, never below the project's
    floor: a species that needs four metres needs them next to any
    neighbour, not only next to its own kind.
    """
    distance = floor
    if catalog is None:
        return distance
    for record in (first, second):
        key = composition_mod.species_of(record)
        if not key:
            continue
        found = catalog.get(key) if hasattr(catalog, "get") else None
        if found is not None:
            distance = max(distance, float(found.min_distance_m or 0.0))
    return distance


def enforce_min_distance(plants, floor: float, catalog=None):
    """Remove whatever breaks the minimum distance. Returns ``(kept, removed)``.

    Greedy, with a ``QgsSpatialIndex`` holding what has been kept so far:
    the first plant of a conflicting pair stays, the second goes.

    The order is **the demanding species first**, and that is the whole
    difference between this working and this being useless. Taken in plain
    planting order, a species needing seven metres in a four-metre lattice
    loses every conflict to whatever tolerant neighbour happened to be
    placed before it, and disappears from the stand completely -- measured:
    132 assigned, 0 surviving. Sorted by required distance, the demanding
    species claims its positions and the tolerant ones fill in around it,
    which is also how a forester lays out a mixed stand. Within one
    requirement the order is still the planting order, so the result does
    not depend on anything the engine happened to emit.

    Greedy is not optimal -- a cleverer pass would keep a few more -- but it
    is stable and explicable, and a plan an operator cannot explain is not a
    plan.
    """
    from qgis.core import (QgsFeature, QgsGeometry,              # noqa: PLC0415
                           QgsPointXY, QgsRectangle, QgsSpatialIndex)

    if floor <= 0.0 or not plants:
        return list(plants), []

    # The search window has to be the *largest* distance any pair could
    # need, not the floor: a species asking for four metres inside a
    # project whose floor is two would have its conflicts fall outside a
    # two-metre window and never be seen.
    limit = floor
    if catalog is not None:
        for record in plants:
            key = composition_mod.species_of(record)
            found = catalog.get(key) if key and hasattr(catalog, "get") else None
            if found is not None:
                limit = max(limit, float(found.min_distance_m or 0.0))

    order = list(plants)
    if catalog is not None:
        def demand(item):
            key = composition_mod.species_of(item[1])
            found = catalog.get(key) if key and hasattr(catalog, "get") else None
            return -float(found.min_distance_m or 0.0) if found else 0.0

        order = [record for _index, record in
                 sorted(enumerate(plants), key=lambda item: (demand(item),
                                                             item[0]))]

    index = QgsSpatialIndex()
    kept, removed = [], []
    positions = {}
    for record in order:
        conflict = False
        window = QgsRectangle(record.x - limit, record.y - limit,
                              record.x + limit, record.y + limit)
        for other_id in index.intersects(window):
            other = positions[other_id]
            needed = species_distance(record, other, catalog, floor)
            if math.hypot(record.x - other.x,
                          record.y - other.y) < needed - 1e-9:
                conflict = True
                break
        if conflict:
            removed.append(record)
            continue
        feature = QgsFeature(len(kept) + 1)
        feature.setGeometry(QgsGeometry.fromPointXY(
            QgsPointXY(record.x, record.y)))
        index.addFeature(feature)
        positions[feature.id()] = record
        kept.append(record)
    # Back into planting order: the caller numbers rows and writes a layer
    # from this list, and "demanding species first" is a decision about who
    # keeps a spot, not about who is planted first.
    rank = {id(record): number for number, record in enumerate(plants)}
    kept.sort(key=lambda record: rank.get(id(record), 0))
    removed.sort(key=lambda record: rank.get(id(record), 0))
    return kept, removed


def _species_wiped_out(before, after) -> "list[str]":
    """Species that were assigned and then removed down to the last plant."""
    had = {composition_mod.species_of(record) for record in before}
    left = {composition_mod.species_of(record) for record in after}
    return sorted(key for key in had - left if key)


def measure_min_distance(plants, sample_limit: int = 20_000) -> float:
    """The closest pair actually present, measured not assumed."""
    from qgis.core import (QgsFeature, QgsGeometry,              # noqa: PLC0415
                           QgsPointXY, QgsSpatialIndex)

    if len(plants) < 2:
        return float("inf")
    subset = plants[:sample_limit]
    index = QgsSpatialIndex()
    positions = {}
    for number, record in enumerate(subset, start=1):
        feature = QgsFeature(number)
        feature.setGeometry(QgsGeometry.fromPointXY(
            QgsPointXY(record.x, record.y)))
        index.addFeature(feature)
        positions[number] = record
    closest = float("inf")
    for number, record in positions.items():
        for other_id in index.nearestNeighbor(
                QgsPointXY(record.x, record.y), 2):
            if other_id == number:
                continue
            other = positions[other_id]
            closest = min(closest, math.hypot(record.x - other.x,
                                              record.y - other.y))
    return closest


# --------------------------------------------------------------------------
# The whole pass
# --------------------------------------------------------------------------

def naturalise(plants, settings: NaturalSettings, step_m: float,
               glades=(), catalog=None) -> NaturalOutcome:
    """Thin a generated stand into a naturalistic one, and prove it holds.

    Order matters and is fixed: glades first (they are places, not
    probabilities), then the density variation, then the minimum distance --
    which comes last precisely because the passes before it are the ones that
    could break it.
    """
    outcome = NaturalOutcome(glades=list(glades),
                             min_distance_m=required_distance(settings,
                                                              catalog))
    surviving = list(plants)

    if glades:
        inside = set(id(record) for record in plants_in_glades(surviving,
                                                               glades))
        if inside:
            surviving = [r for r in surviving if id(r) not in inside]
            outcome.removed[REMOVED_GLADE] = len(inside)

    surviving, thinned = thin_by_density(surviving, settings, step_m)
    if thinned:
        outcome.removed[REMOVED_DENSITY] = len(thinned)

    surviving, crowded = enforce_min_distance(
        surviving, outcome.min_distance_m, catalog)
    if crowded:
        outcome.removed[REMOVED_MIN_DISTANCE] = len(crowded)

    lost = _species_wiped_out(plants, surviving)
    for key in lost:
        outcome.warnings.append(
            "La specie '{0}' non trova posto con questa distanza minima: "
            "nessuna pianta e' sopravvissuta.".format(key))

    outcome.plants = surviving
    outcome.measured_min_distance_m = (
        measure_min_distance(surviving) if outcome.min_distance_m else 0.0)
    if (outcome.min_distance_m
            and outcome.measured_min_distance_m < outcome.min_distance_m - 1e-6
            and math.isfinite(outcome.measured_min_distance_m)):
        outcome.warnings.append(
            "La distanza minima misurata ({0:.3f} m) e' inferiore a quella "
            "richiesta: segnalare.".format(outcome.measured_min_distance_m))
    if settings.glade_count and len(outcome.glades) < settings.glade_count:
        outcome.warnings.append(
            "Radure collocate: {0} su {1} richieste; non c'era spazio per le "
            "altre.".format(len(outcome.glades), settings.glade_count))
    return outcome
