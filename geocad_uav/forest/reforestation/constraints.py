"""
M03 -- constraints: a distance to keep, per kind of thing to keep away from.

    ConstraintSet({"strada": 5.0, "fosso": 10.0, "elettrodotto": 3.0})

The distances are data, not code: nothing in this module knows that a road is
five metres and a ditch is ten. The set is built from whatever dictionary the
operator (or a saved project) supplies, a distance can be changed afterwards,
and a key that was never declared is refused rather than silently buffered by
zero -- a typo that plants trees on a road is not the kind of bug that should
be quiet.

Buffering is ``QgsGeometry.buffer``: GEOS again, on the real geometry, so a
bend in a track is a bend in the exclusion. A distance of exactly zero keeps
the feature itself, which is what a polygonal constraint (a building, an
existing stand) usually wants.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...core.errors import InvalidInputError

#: Segments per quarter circle used when rounding a buffer end. The polygonal
#: cap is inscribed, so a buffer is always a hair smaller than the true
#: offset: at 12 segments a 10 m cap loses 0.13 % of the circle. A parameter,
#: not a constant of nature -- pass ``segments`` to trade area for vertices.
BUFFER_SEGMENTS = 12


@dataclass
class ConstraintRule:
    """One kind of constraint: its distance, its label, its features."""

    key: str
    buffer_m: float
    label: str = ""
    geometries: list = field(default_factory=list)
    sources: list = field(default_factory=list)

    @property
    def display(self) -> str:
        return self.label or self.key

    @property
    def n_features(self) -> int:
        return len(self.geometries)


class ConstraintSet:
    """Parametric buffers around the features they belong to."""

    def __init__(self, buffers=None, segments: int = BUFFER_SEGMENTS,
                 labels=None):
        self.segments = self._checked_segments(segments)
        self.rules: "dict[str, ConstraintRule]" = {}
        labels = dict(labels or {})
        for key, distance in dict(buffers or {}).items():
            self.declare(key, distance, label=labels.get(key, ""))

    # -- the parameters ----------------------------------------------------

    @staticmethod
    def _checked_segments(segments) -> int:
        value = int(segments)
        if value < 1:
            raise InvalidInputError(
                "buffer segments must be >= 1, got {0}".format(segments),
                user_message="Il numero di segmenti del buffer deve essere "
                             "almeno 1.")
        return value

    @staticmethod
    def _checked_distance(key: str, distance) -> float:
        value = float(distance)
        if not math.isfinite(value) or value < 0.0:
            raise InvalidInputError(
                "constraint {0!r} needs a distance >= 0, got {1!r}".format(
                    key, distance),
                user_message="La distanza del vincolo '{0}' deve essere un "
                             "numero maggiore o uguale a zero.".format(key))
        return value

    def declare(self, key: str, distance, label: str = "") -> ConstraintRule:
        """Add or replace one kind of constraint."""
        if not key:
            raise InvalidInputError(
                "a constraint needs a key",
                user_message="Ogni vincolo deve avere un nome.")
        distance = self._checked_distance(key, distance)
        existing = self.rules.get(key)
        if existing is None:
            self.rules[key] = ConstraintRule(key, distance, label=label)
        else:
            existing.buffer_m = distance
            if label:
                existing.label = label
        return self.rules[key]

    def set_buffer(self, key: str, distance) -> float:
        """Change one distance, keeping the features already collected."""
        rule = self._rule(key)
        rule.buffer_m = self._checked_distance(key, distance)
        return rule.buffer_m

    def buffer_for(self, key: str) -> float:
        return self._rule(key).buffer_m

    def keys(self) -> "list[str]":
        return list(self.rules)

    def _rule(self, key: str) -> ConstraintRule:
        rule = self.rules.get(key)
        if rule is None:
            raise InvalidInputError(
                "unknown constraint {0!r}; declared: {1}".format(
                    key, ", ".join(sorted(self.rules)) or "none"),
                user_message="Vincolo '{0}' non dichiarato.".format(key))
        return rule

    # -- the features ------------------------------------------------------

    def add_geometry(self, key: str, geometry, source: str = "") -> bool:
        rule = self._rule(key)
        if geometry is None or geometry.isEmpty():
            return False
        rule.geometries.append(geometry)
        rule.sources.append(source)
        return True

    def add_layer(self, key: str, layer, only_selected: bool = False) -> int:
        """Collect a whole layer (or its selection) under one constraint."""
        rule = self._rule(key)
        if layer is None:
            return 0
        if only_selected and layer.selectedFeatureCount() > 0:
            features = layer.selectedFeatures()
        else:
            features = list(layer.getFeatures())
        added = 0
        for feature in features:
            if self.add_geometry(key, feature.geometry(), source=layer.name()):
                added += 1
        if added and not rule.label:
            rule.label = layer.name()
        return added

    def clear_features(self, key: str = "") -> None:
        for rule in ([self._rule(key)] if key else self.rules.values()):
            rule.geometries = []
            rule.sources = []

    # -- the exclusions ----------------------------------------------------

    def buffered(self, key: str):
        """The features of one constraint, buffered and unioned. May be None.

        A distance of zero returns the features themselves: GEOS buffers a
        line by zero into an empty polygon, which would quietly drop a
        constraint the operator did declare.
        """
        from qgis.core import QgsGeometry                     # noqa: PLC0415

        rule = self._rule(key)
        parts = []
        for geometry in rule.geometries:
            grown = (geometry if rule.buffer_m == 0.0
                     else geometry.buffer(rule.buffer_m, self.segments))
            if grown is not None and not grown.isEmpty():
                parts.append(grown)
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else QgsGeometry.unaryUnion(parts)

    def exclusion_geometry(self):
        """Every constraint, buffered and unioned into one geometry."""
        from qgis.core import QgsGeometry                     # noqa: PLC0415

        parts = [g for g in (self.buffered(key) for key in self.rules)
                 if g is not None]
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else QgsGeometry.unaryUnion(parts)

    def apply_to(self, area) -> int:
        """Push each constraint onto a :class:`~.area.ReforestationArea`.

        One exclusion per constraint kind, not one per feature, so the report
        reads "strade: 0.42 ha" instead of listing forty road segments. What
        the area actually subtracts is still the union of everything.
        """
        added = 0
        for key, rule in self.rules.items():
            grown = self.buffered(key)
            if grown is None:
                continue
            if area.add_exclusion(grown, label=self.describe_rule(rule),
                                  source=key):
                added += 1
        return added

    # -- readout -----------------------------------------------------------

    @staticmethod
    def describe_rule(rule: ConstraintRule) -> str:
        if rule.buffer_m == 0.0:
            return "{0} (nessuna fascia)".format(rule.display)
        return "{0} (fascia {1:g} m)".format(rule.display, rule.buffer_m)

    def breakdown(self) -> "list[tuple]":
        """[(key, label, buffer_m, n_features, m2), ...]."""
        out = []
        for key, rule in self.rules.items():
            grown = self.buffered(key)
            out.append((key, rule.display, rule.buffer_m, rule.n_features,
                        0.0 if grown is None else float(grown.area())))
        return out

    def describe(self) -> "list[str]":
        lines = ["VINCOLI E FASCE DI RISPETTO"]
        if not self.rules:
            lines.append("  nessun vincolo dichiarato")
            return lines
        for key, label, distance, count, m2 in self.breakdown():
            lines.append("  {0:<18} fascia {1:>6.2f} m  {2:>4} elementi  "
                         "{3:>10,.4f} ha".format(label, distance, count,
                                                 m2 / 10_000.0))
        return lines
