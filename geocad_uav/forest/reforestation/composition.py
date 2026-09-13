"""
M05 -- more than one species in the same stand, in the proportions asked for.

A reforestation is rarely one species. The operator says "40 % leccio, 35 %
roverella, 25 % orniello" and expects the plan to contain exactly that, not
approximately that: the nursery order is built from these counts and a
percentage that rounds the wrong way three times is three hundred plants.

So the counts are computed by largest remainder and **always sum to the plants
actually generated**. Then they are laid out, which is a separate decision:

* :data:`LAYOUT_UNIFORM` -- the mix repeats along every row, so any corner of
  the stand has the stated composition. The default, and what a mixed
  plantation usually means.
* :data:`LAYOUT_GROUPS`  -- contiguous clumps of one species. Closer to how a
  natural stand grows, and what a nursery lifts in one go.
* :data:`LAYOUT_ROWS`    -- one species per row, for machine work.

The assignment is deterministic: the same seed on the same plants gives the
same plan, which is what makes a printed layout worth anything.

Species keys come from :mod:`.species` -- the operator's own catalogue -- and
are never invented here. A key not in the catalogue is refused rather than
planted as a mystery.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ...core.errors import InvalidInputError

#: Attribute the species key is written to, on the record and on the layer.
SPECIES_ATTRIBUTE = "species"

LAYOUT_UNIFORM = "uniform"
LAYOUT_GROUPS = "groups"
LAYOUT_ROWS = "rows"
LAYOUTS = (LAYOUT_UNIFORM, LAYOUT_GROUPS, LAYOUT_ROWS)

LAYOUT_LABELS = {
    LAYOUT_UNIFORM: "Uniforme (mescolate su tutta l'area)",
    LAYOUT_GROUPS: "A gruppi",
    LAYOUT_ROWS: "Per file intere",
}

#: Percentages are compared to this before being called wrong. A share typed
#: as 33.33 three times is 99.99, and refusing that would be pedantry.
PERCENT_TOLERANCE = 0.5

DEFAULT_GROUP_SIZE = 9


@dataclass
class SpeciesShare:
    """One species and the slice of the stand it is to occupy."""

    key: str
    percent: float

    def __post_init__(self) -> None:
        self.key = str(self.key or "").strip()
        if not self.key:
            raise InvalidInputError(
                "a share needs a species key",
                user_message="Ogni quota deve indicare una specie.")
        value = float(self.percent)
        if not math.isfinite(value) or value <= 0.0:
            raise InvalidInputError(
                "share for {0!r} must be > 0, got {1!r}".format(self.key,
                                                                self.percent),
                user_message="La percentuale di '{0}' deve essere maggiore "
                             "di zero.".format(self.key))
        self.percent = value


@dataclass
class Composition:
    """What was decided, and what came out of it."""

    counts: dict = field(default_factory=dict)
    assigned: dict = field(default_factory=dict)     # plant_id -> species key
    layout: str = LAYOUT_UNIFORM
    warnings: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def percentages(self) -> dict:
        """What the plan actually contains, species by species."""
        total = float(self.total)
        if total <= 0.0:
            return {}
        return {key: 100.0 * count / total
                for key, count in self.counts.items()}

    def describe(self, shares=()) -> "list[str]":
        wanted = {share.key: share.percent for share in shares}
        lines = ["COMPOSIZIONE",
                 "  Disposizione: {0}".format(
                     LAYOUT_LABELS.get(self.layout, self.layout)),
                 "  Piante:       {0:,}".format(self.total)]
        for key, count in sorted(self.counts.items(),
                                 key=lambda item: -item[1]):
            achieved = self.percentages().get(key, 0.0)
            if key in wanted:
                lines.append("  {0:<16} {1:>7,} piante  {2:>6.2f} % "
                             "(richiesto {3:.2f} %)".format(
                                 key, count, achieved, wanted[key]))
            else:
                lines.append("  {0:<16} {1:>7,} piante  {2:>6.2f} %".format(
                    key, count, achieved))
        lines.extend("  " + text for text in self.warnings)
        return lines


class Mix:
    """The species of one zone and their proportions."""

    def __init__(self, shares, catalog=None, seed: int = 0):
        self.shares = [s if isinstance(s, SpeciesShare) else SpeciesShare(*s)
                       for s in shares]
        if not self.shares:
            raise InvalidInputError(
                "a mix needs at least one species",
                user_message="Indica almeno una specie.")
        keys = [share.key for share in self.shares]
        if len(set(keys)) != len(keys):
            raise InvalidInputError(
                "a species appears twice in the mix: {0}".format(keys),
                user_message="La stessa specie compare piu' di una volta "
                             "nella composizione.")
        if catalog is not None:
            missing = [key for key in keys if not catalog.has(key)]
            if missing:
                raise InvalidInputError(
                    "species not in the catalogue: {0}".format(missing),
                    user_message="Specie non in catalogo: {0}.".format(
                        ", ".join(missing)),
                    hint="Aggiungile al catalogo prima di usarle.")
        self.catalog = catalog
        self.seed = int(seed)

    # -- the proportions ---------------------------------------------------

    @property
    def declared_total(self) -> float:
        return sum(share.percent for share in self.shares)

    def check_total(self) -> Optional[str]:
        """A warning when the shares do not add up, or None when they do."""
        total = self.declared_total
        if abs(total - 100.0) <= PERCENT_TOLERANCE:
            return None
        return ("Le percentuali sommano a {0:.2f} %, non a 100: sono state "
                "riscalate.".format(total))

    def weights(self) -> dict:
        """Each share as a fraction of the declared total, summing to 1."""
        total = self.declared_total
        return {share.key: share.percent / total for share in self.shares}

    def counts_for(self, n_plants: int) -> dict:
        """How many plants of each species, summing to exactly ``n_plants``.

        Largest remainder: every species gets the whole part of its share and
        the leftovers go to the species that were rounded down hardest. It is
        the method used to apportion seats, for the same reason -- the total
        has to come out right.
        """
        count = max(0, int(n_plants))
        if count == 0:
            return {share.key: 0 for share in self.shares}
        weights = self.weights()
        exact = {key: weights[key] * count for key in weights}
        counts = {key: int(math.floor(value)) for key, value in exact.items()}
        remaining = count - sum(counts.values())
        if remaining:
            order = sorted(exact, key=lambda key: (-(exact[key]
                                                     - counts[key]), key))
            for key in order[:remaining]:
                counts[key] += 1
        return counts

    # -- the layout --------------------------------------------------------

    def sequence(self, n_plants: int, layout: str = LAYOUT_UNIFORM) -> list:
        """The species of each plant, in order, as a flat list.

        ``LAYOUT_UNIFORM`` interleaves by largest remaining count, so the mix
        is even at every point of the sequence rather than only at its end --
        a stand whose first half is all one species is not a mixed stand.
        """
        if layout not in LAYOUTS:
            raise InvalidInputError(
                "unknown layout {0!r}".format(layout),
                user_message="Disposizione non riconosciuta.")
        counts = self.counts_for(n_plants)
        if layout == LAYOUT_UNIFORM:
            return self._interleave(counts)
        out = []
        for key in sorted(counts, key=lambda k: (-counts[k], k)):
            out.extend([key] * counts[key])
        return out

    @staticmethod
    def _interleave(counts: dict) -> list:
        """Spread each species evenly through the sequence.

        The divisor method: at each position the species chosen is the one
        with the largest ``count / (2 * placed + 1)``, which is Webster's
        rule for apportioning seats over time. Taking "whoever has most
        left" instead front-loads the majority species -- the first half of
        the stand comes out at 47 % where 40 % was asked for, and a mixed
        plantation whose first half is not mixed is not one.
        """
        remaining = dict(counts)
        placed = {key: 0 for key in counts}
        total = sum(remaining.values())
        out = []
        for _ in range(total):
            candidates = [k for k in sorted(remaining) if remaining[k] > 0]
            if not candidates:
                break
            key = max(candidates,
                      key=lambda k: counts[k] / (2.0 * placed[k] + 1.0))
            out.append(key)
            remaining[key] -= 1
            placed[key] += 1
        return out


def assign(plants, mix: Mix, layout: str = LAYOUT_UNIFORM,
           group_size: int = DEFAULT_GROUP_SIZE) -> Composition:
    """Give every plant a species, in the proportions the mix asks for.

    The species key is written onto the record as ``species`` -- the record
    is the planner's own dataclass and carries no such field, so this adds it
    rather than pretending ``core.models`` knows about composition -- and the
    same mapping comes back in the result for anything that would rather not
    read it off the records.
    """
    records = list(plants)
    composition = Composition(layout=layout)
    warning = mix.check_total()
    if warning:
        composition.warnings.append(warning)
    if not records:
        composition.counts = mix.counts_for(0)
        return composition

    if layout == LAYOUT_ROWS:
        order = _by_row(records)
    elif layout == LAYOUT_GROUPS:
        order = _by_group(records, group_size, mix.seed)
    else:
        order = list(records)

    sequence = mix.sequence(len(order), layout)
    for record, key in zip(order, sequence):
        setattr(record, SPECIES_ATTRIBUTE, key)
        composition.assigned[record.plant_id] = key

    counts = {}
    for key in composition.assigned.values():
        counts[key] = counts.get(key, 0) + 1
    for share in mix.shares:
        counts.setdefault(share.key, 0)
    composition.counts = counts
    return composition


def _by_row(records) -> list:
    """Row by row, each row in planting order: whole rows of one species."""
    return sorted(records, key=lambda r: (r.row_id, r.seq_in_row))


def _by_group(records, group_size: int, seed: int) -> list:
    """Clumps of neighbours, so one species lands as a patch.

    Neighbours are taken along the rows -- the plants really are adjacent
    there -- and the clumps are then shuffled with the seed so the patches do
    not march across the stand in the order the species were typed.
    """
    size = max(1, int(group_size))
    ordered = _by_row(records)
    groups = [ordered[i:i + size] for i in range(0, len(ordered), size)]
    rng = np.random.default_rng(int(seed))
    order = rng.permutation(len(groups))
    out = []
    for index in order:
        out.extend(groups[int(index)])
    return out


def from_records(plants, layout: str = LAYOUT_UNIFORM) -> Composition:
    """The composition a set of plants already carries.

    :func:`assign` decides; this one only reads. It exists because once an
    operator can change a plant's species on the map, the composition shown
    afterwards has to be the one on the ground and not the one that was
    decided -- and counting it in two places would let the two disagree.
    """
    counts = {}
    assigned = {}
    for record in plants:
        key = species_of(record)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        assigned[record.plant_id] = key
    return Composition(counts=counts, assigned=assigned, layout=layout)


def achieved_percentages(plants) -> dict:
    """What a set of already-assigned plants actually contains."""
    counts = {}
    for record in plants:
        key = getattr(record, SPECIES_ATTRIBUTE, "")
        if key:
            counts[key] = counts.get(key, 0) + 1
    total = float(sum(counts.values()))
    if total <= 0.0:
        return {}
    return {key: 100.0 * value / total for key, value in counts.items()}


def species_of(record) -> str:
    """The species assigned to one plant, or an empty string."""
    return str(getattr(record, SPECIES_ATTRIBUTE, "") or "")
