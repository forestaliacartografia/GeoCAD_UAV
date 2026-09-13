"""
M05 -- zones: sub-areas of one project, each planted its own way.

A parcel is rarely uniform. The north slope takes a different species from
the south one, the flat strip along the track takes a wider scheme because a
machine has to turn there, and the wet corner takes nothing at all. So the
usable surface is cut into zones, and each zone carries its **own** scheme,
its own species mix and its own row orientation.

The important part is that a zone is not a label: it is a polygon the
generator actually plants separately. Until this module existed the plan
collected zones and then generated over the whole area regardless, which is
worse than not offering zones -- an operator would have read the panel and
believed it.

Zones are clipped to the surface they belong to on the way in, so a zone can
never plant outside the project, and they are checked against each other so
two of them cannot claim the same ground. What is left over -- surface in no
zone -- is reported rather than silently planted or silently dropped: it is
the operator's decision whether a gap is a mistake or a clearing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from ...core.errors import EmptyAoiError, InvalidInputError
from ...core.planar import along_track_unit
from . import composition as composition_mod
from . import density as density_mod
from . import spacing as spacing_mod

M2_PER_HA = 10_000.0

#: Attribute the zone name is written to, on the record and on the layer.
ZONE_ATTRIBUTE = "zone"

#: Overlap below this is rounding, not a conflict: two zones cut from the
#: same polygon share a boundary, and GEOS gives that boundary a sliver of
#: area now and then.
OVERLAP_TOLERANCE_M2 = 1e-6

#: A band narrower than this is not a band. Splitting a 40 m parcel into
#: thirty strips is a mistake, and the split says so instead of returning
#: thirty slivers.
MIN_BAND_WIDTH_M = 1.0


@dataclass
class Zone:
    """One sub-area, with everything needed to plant it."""

    name: str
    geometry: object                            # QgsGeometry
    spec: Optional[spacing_mod.SlopeSpacing] = None
    shares: tuple = ()
    layout: str = composition_mod.LAYOUT_UNIFORM
    seed: int = 0

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip()
        if not self.name:
            raise InvalidInputError(
                "a zone needs a name",
                user_message="Ogni zona deve avere un nome.")
        if self.geometry is None or self.geometry.isEmpty():
            raise EmptyAoiError(
                "zone {0!r} has no geometry".format(self.name),
                user_message="La zona '{0}' non ha una geometria.".format(
                    self.name))
        self.shares = tuple(self.shares or ())

    @property
    def area_m2(self) -> float:
        return float(self.geometry.area())

    @property
    def area_ha(self) -> float:
        return self.area_m2 / M2_PER_HA

    def mix(self, catalog=None):
        """The species mix of this zone, or None when it has none."""
        if not self.shares:
            return None
        return composition_mod.Mix(self.shares, catalog, seed=self.seed)

    def density_per_ha(self) -> float:
        if self.spec is None:
            return 0.0
        return density_mod.density_from_spacing(
            self.spec.pattern, self.spec.plant_distance_m,
            self.spec.row_distance_m)

    def describe(self) -> "list[str]":
        lines = ["{0}: {1:,.4f} ha".format(self.name, self.area_ha)]
        if self.spec is not None:
            lines.append("  sesto {0:g} x {1:g} m, azimut {2:.1f} deg, "
                         "{3:,.0f} piante/ha".format(
                             self.spec.plant_distance_m,
                             self.spec.row_distance_m,
                             self.spec.row_azimuth_deg,
                             self.density_per_ha()))
        for key, percent in self.shares:
            lines.append("  {0}: {1:g} %".format(key, percent))
        return lines


class ZoneSet:
    """The zones of one project, kept apart from each other."""

    def __init__(self, zones=(), container=None):
        #: The surface the zones must stay inside -- the *superficie utile*.
        self.container = container
        self.zones = []
        for zone in zones:
            self.add(zone)

    def __len__(self) -> int:
        return len(self.zones)

    def __iter__(self):
        return iter(self.zones)

    def names(self) -> "list[str]":
        return [zone.name for zone in self.zones]

    # -- building ----------------------------------------------------------

    def add(self, zone: Zone, clip: bool = True) -> Zone:
        """Add a zone, clipped to the container and free of the others.

        Both clips happen here rather than at planting time, so what the
        panel shows as the zone's surface is the surface that will be
        planted. A zone that is entirely swallowed by the clip is refused,
        because an empty zone in a list is a lie.
        """
        if any(existing.name == zone.name for existing in self.zones):
            raise InvalidInputError(
                "zone {0!r} is already in the set".format(zone.name),
                user_message="Esiste gia' una zona '{0}'.".format(zone.name))
        geometry = zone.geometry
        if clip and self.container is not None:
            geometry = geometry.intersection(self.container)
        if clip:
            for existing in self.zones:
                if geometry is None or geometry.isEmpty():
                    break
                geometry = geometry.difference(existing.geometry)
        if geometry is None or geometry.isEmpty() or geometry.area() <= 0.0:
            raise EmptyAoiError(
                "zone {0!r} is empty after clipping".format(zone.name),
                user_message="La zona '{0}' non lascia superficie libera: "
                             "e' fuori dall'area o gia' coperta da "
                             "un'altra zona.".format(zone.name))
        zone.geometry = geometry
        self.zones.append(zone)
        return zone

    def remove(self, name: str) -> bool:
        for index, zone in enumerate(self.zones):
            if zone.name == name:
                self.zones.pop(index)
                return True
        return False

    def clear(self) -> None:
        self.zones = []

    def get(self, name: str) -> Optional[Zone]:
        for zone in self.zones:
            if zone.name == name:
                return zone
        return None

    # -- what the set is -------------------------------------------------

    @property
    def total_area_m2(self) -> float:
        return sum(zone.area_m2 for zone in self.zones)

    def uncovered(self):
        """The part of the container no zone claims, or None."""
        from qgis.core import QgsGeometry                       # noqa: PLC0415

        if self.container is None or not self.zones:
            return None
        left = QgsGeometry(self.container)
        for zone in self.zones:
            left = left.difference(zone.geometry)
            if left is None or left.isEmpty():
                return None
        return None if left.area() <= OVERLAP_TOLERANCE_M2 else left

    def validate(self) -> "list[str]":
        """Everything wrong with the set, in the operator's words."""
        problems = []
        for index, first in enumerate(self.zones):
            if first.spec is None:
                problems.append(
                    "La zona '{0}' non ha un sesto.".format(first.name))
            for second in self.zones[index + 1:]:
                overlap = first.geometry.intersection(second.geometry)
                if (overlap is not None and not overlap.isEmpty()
                        and overlap.area() > OVERLAP_TOLERANCE_M2):
                    problems.append(
                        "Le zone '{0}' e '{1}' si sovrappongono per "
                        "{2:,.2f} m2.".format(first.name, second.name,
                                              overlap.area()))
            if self.container is not None:
                outside = first.geometry.difference(self.container)
                if (outside is not None and not outside.isEmpty()
                        and outside.area() > OVERLAP_TOLERANCE_M2):
                    problems.append(
                        "La zona '{0}' esce dalla superficie utile per "
                        "{1:,.2f} m2.".format(first.name, outside.area()))
        left = self.uncovered()
        if left is not None:
            problems.append(
                "{0:,.4f} ha della superficie utile non appartengono a "
                "nessuna zona.".format(left.area() / M2_PER_HA))
        return problems

    def describe(self) -> "list[str]":
        lines = ["ZONE DI IMPIANTO",
                 "  Zone: {0}".format(len(self.zones)),
                 "  Superficie coperta: {0:,.4f} ha".format(
                     self.total_area_m2 / M2_PER_HA)]
        for zone in self.zones:
            lines.extend("  " + text for text in zone.describe())
        for problem in self.validate():
            lines.append("  ! {0}".format(problem))
        return lines


# --------------------------------------------------------------------------
# Cutting an area into zones
# --------------------------------------------------------------------------

def split_bands(geometry, count: int, azimuth_deg: float = 90.0,
                spec=None, prefix: str = "Zona") -> "list[Zone]":
    """Cut a polygon into ``count`` strips of equal width.

    The strips run along the rows -- ``azimuth_deg`` is the row bearing, the
    same convention as :class:`~.spacing.SlopeSpacing` -- so a band is a run
    of whole rows and a machine never has to cross a boundary mid-row.

    Equal *width*, not equal area: a band of a wedge-shaped parcel is
    narrower at the point, and pretending otherwise would mean bands whose
    edges are not straight. The areas are reported, so the operator can see
    what they got.
    """
    from qgis.core import QgsGeometry, QgsPointXY                # noqa: PLC0415

    if geometry is None or geometry.isEmpty():
        raise EmptyAoiError(
            "nothing to split",
            user_message="Nessuna superficie da suddividere.")
    bands = int(count)
    if bands < 1:
        raise InvalidInputError(
            "band count must be >= 1, got {0!r}".format(count),
            user_message="Il numero di fasce deve essere almeno 1.")
    if bands == 1:
        return [Zone(name="{0} A".format(prefix),
                     geometry=QgsGeometry(geometry), spec=spec)]

    # Frame: u along the rows, v across them.
    ux, uy = along_track_unit(azimuth_deg)
    vx, vy = along_track_unit(azimuth_deg + 90.0)

    vertices = [(point.x(), point.y())
                for point in geometry.vertices()]
    if not vertices:
        raise EmptyAoiError(
            "the geometry has no vertices",
            user_message="La superficie non ha vertici.")
    along = [x * ux + y * uy for x, y in vertices]
    across = [x * vx + y * vy for x, y in vertices]
    a_min, a_max = min(along), max(along)
    c_min, c_max = min(across), max(across)
    width = (c_max - c_min) / bands
    if width < MIN_BAND_WIDTH_M:
        raise InvalidInputError(
            "{0} bands would be {1:.3f} m wide".format(bands, width),
            user_message="{0} fasce sarebbero larghe {1:.2f} m: troppo "
                         "poco.".format(bands, width),
            hint="Riduci il numero di fasce.")

    pad = max(a_max - a_min, c_max - c_min)
    out = []
    for index in range(bands):
        low = c_min + index * width
        high = low + width
        # A rectangle in the rotated frame, written back in world coordinates.
        corners = []
        for a_value, c_value in ((a_min - pad, low), (a_max + pad, low),
                                 (a_max + pad, high), (a_min - pad, high)):
            corners.append(QgsPointXY(a_value * ux + c_value * vx,
                                      a_value * uy + c_value * vy))
        cutter = QgsGeometry.fromPolygonXY([corners + [corners[0]]])
        piece = geometry.intersection(cutter)
        if piece is None or piece.isEmpty() or piece.area() <= 0.0:
            continue
        band_spec = spec
        if spec is not None:
            band_spec = spacing_mod.SlopeSpacing(
                plant_distance_m=spec.plant_distance_m,
                row_distance_m=spec.row_distance_m,
                row_azimuth_deg=spec.row_azimuth_deg, pattern=spec.pattern,
                margin_m=spec.margin_m, step_mode=spec.step_mode,
                jitter_m=spec.jitter_m, seed=spec.seed,
                custom_offsets=spec.custom_offsets)
        out.append(Zone(name="{0} {1}".format(prefix,
                                              chr(ord("A") + len(out))),
                        geometry=piece, spec=band_spec))
    if not out:
        raise EmptyAoiError(
            "splitting produced no band",
            user_message="La suddivisione non ha prodotto nessuna fascia.")
    return out


# --------------------------------------------------------------------------
# Planting them
# --------------------------------------------------------------------------

@dataclass
class ZonePlan:
    """Every plant of every zone, and the numbers per zone."""

    plants: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    per_zone: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.plants)

    @property
    def n_zones(self) -> int:
        return len(self.per_zone)

    @property
    def usable_area_m2(self) -> float:
        return sum(entry["area_m2"] for entry in self.per_zone.values())

    def density_per_ha(self) -> float:
        area = self.usable_area_m2
        return 0.0 if area <= 0.0 else self.count / (area / M2_PER_HA)

    def mean_plan_spacing(self) -> float:
        return spacing_mod.mean_spacing(self.plants, with_z=False)

    def mean_real_spacing(self) -> float:
        return spacing_mod.mean_spacing(self.plants, with_z=True)

    def species_counts(self) -> dict:
        counts = {}
        for entry in self.per_zone.values():
            for key, value in entry.get("species", {}).items():
                counts[key] = counts.get(key, 0) + value
        return counts

    def describe(self) -> "list[str]":
        lines = ["IMPIANTO PER ZONE",
                 "  Zone:   {0}".format(self.n_zones),
                 "  Piante: {0:,}".format(self.count)]
        for name, entry in self.per_zone.items():
            lines.append("  {0:<12} {1:>8,.4f} ha  {2:>7,} piante  "
                         "{3:>8,.1f}/ha".format(
                             name, entry["area_m2"] / M2_PER_HA,
                             entry["plants"], entry["density_per_ha"]))
            for key, count in sorted(entry.get("species", {}).items()):
                lines.append("      {0:<14} {1:>7,}".format(key, count))
        lines.extend("  " + text for text in self.warnings)
        return lines


def plant(zone_set: ZoneSet, terrain=None, catalog=None) -> ZonePlan:
    """Generate each zone with its own scheme, and keep them apart.

    Every plant carries the name of the zone it belongs to, so the layer, the
    validation and the report can all speak of zones without a second pass
    to work out which plant went where. Plant ids are renumbered across the
    whole plan: two zones must not both contain plant number 1.
    """
    plan = ZonePlan()
    if not len(zone_set):
        return plan

    next_id = 0
    next_row = 0
    for zone in zone_set:
        if zone.spec is None:
            plan.warnings.append(
                "Zona '{0}' saltata: nessun sesto.".format(zone.name))
            continue
        try:
            result = spacing_mod.generate(zone.geometry, zone.spec,
                                          terrain=terrain)
        except (EmptyAoiError, InvalidInputError) as exc:
            plan.warnings.append("Zona '{0}': {1}".format(
                zone.name, exc.user_message))
            continue

        mix = zone.mix(catalog)
        species_counts = {}
        if mix is not None:
            outcome = composition_mod.assign(result.plants, mix,
                                             layout=zone.layout)
            species_counts = dict(outcome.counts)
            plan.warnings.extend(
                "Zona '{0}': {1}".format(zone.name, text)
                for text in outcome.warnings)

        offset = next_row
        for record in result.plants:
            next_id += 1
            record.plant_id = next_id
            record.row_id = offset + record.row_id
            setattr(record, ZONE_ATTRIBUTE, zone.name)
        if result.plants:
            next_row = max(record.row_id for record in result.plants) + 1

        plan.plants.extend(result.plants)
        plan.rows.extend(result.rows)
        plan.per_zone[zone.name] = {
            "area_m2": zone.area_m2,
            "plants": result.count,
            "density_per_ha": (result.count / (zone.area_m2 / M2_PER_HA)
                               if zone.area_m2 > 0.0 else 0.0),
            "species": species_counts,
            "spec": zone.spec,
            "warnings": list(result.warnings),
        }
        plan.warnings.extend("Zona '{0}': {1}".format(zone.name, text)
                             for text in result.warnings)
    return plan


def zone_of(record) -> str:
    """The zone a plant belongs to, or an empty string."""
    return str(getattr(record, ZONE_ATTRIBUTE, "") or "")
