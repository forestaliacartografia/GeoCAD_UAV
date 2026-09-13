"""
M04 -- planting schemes measured on the ground, not on the map.

An operator paces a plantation with a tape held along the slope: "tre metri
fra una pianta e l'altra" means three metres *of ground*, not three metres of
map. On a 30 degree hillside those two are not the same number, and a lattice
laid out planimetrically plants 15 % more trees than the scheme asks for.

So the scheme is stated in real distances and the generator converts, cell by
cell, using the slope the DEM reports at the point it has reached::

    D_plan = D_real * cos(theta)

That makes the step a *function of position*, which is why the positions are
marched rather than tabulated: from each point the next one is one corrected
step away, so the layout follows the ground instead of assuming a plane. On
flat terrain every step is D_real and the result is the ordinary lattice
``core.grid`` produces -- this module does not replace that engine, it is what
to use when the ground is not flat.

Two ways of reading "the slope at this point" are offered, because the two
callers of this module need different ones:

* :data:`STEP_MAX_SLOPE` -- the local maximum slope, applied to every step
  whatever its direction. This is the convention a forester states a scheme
  in, and the default.
* :data:`STEP_DIRECTIONAL` -- the slope measured *along the step itself*, so
  a row running along a contour is not shortened at all. This is the honest
  one for a single line of known direction, and it is what :mod:`.curves`
  uses to space plants along a contour.

Orientation convention, as everywhere else in the plugin: 0 deg = North (+Y),
90 deg = East (+X), clockwise. ``row_azimuth_deg`` is the bearing the *rows
run*, which is the number an operator reads off a compass standing in one.
``core.grid.GridSpec.azimuth_deg`` is the bearing the rows are *stepped
along*, ninety degrees from it; :meth:`SlopeSpacing.lattice_azimuth` converts,
and a test pins the relation rather than trusting this paragraph.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ...core import grid as grid_mod
from ...core.constants import GEOM_EPS_M
from ...core.errors import EmptyAoiError, InvalidInputError
from ...core.models import PlantingRecord
from ...core.planar import along_track_unit

#: How the slope that corrects a step is read off the DEM.
STEP_MAX_SLOPE = "max"
STEP_DIRECTIONAL = "directional"
STEP_MODES = (STEP_MAX_SLOPE, STEP_DIRECTIONAL)

STEP_MODE_LABELS = {
    STEP_MAX_SLOPE: "Pendenza massima locale",
    STEP_DIRECTIONAL: "Pendenza lungo la direzione del passo",
}

#: Schemes this module adds to the five ``core.grid`` already generates. Both
#: are the rectangular lattice with the rows displaced: at random within a
#: bound, or by a list the operator supplies.
PATTERN_IRREGULAR = "irregular"
PATTERN_CUSTOM = "custom"

#: Not a lattice at all: the rows are the contour lines of the DEM, and the
#: plants are spaced along them. It lives in this list because it is what the
#: operator chooses in the same place they choose a quincunx, but what sets
#: the distance *between* rows is the contour interval and the slope, not
#: :attr:`SlopeSpacing.row_distance_m`. Everything that asks this module for
#: an a-priori density has to say so -- see ``curves.plant_along_contours``,
#: which is what actually generates it.
PATTERN_CONTOUR = "contour"

#: Which core pattern each added scheme is a displacement of.
BASE_PATTERN = {
    PATTERN_IRREGULAR: grid_mod.PATTERN_RECT,
    PATTERN_CUSTOM: grid_mod.PATTERN_RECT,
    PATTERN_CONTOUR: grid_mod.PATTERN_ROWS,
}

ALL_PATTERNS = grid_mod.ALL_PATTERNS + (PATTERN_IRREGULAR, PATTERN_CUSTOM,
                                        PATTERN_CONTOUR)

PATTERN_LABELS = dict(grid_mod.PATTERN_LABELS)
PATTERN_LABELS[PATTERN_IRREGULAR] = "Irregolare (naturaliforme)"
PATTERN_LABELS[PATTERN_CUSTOM] = "Personalizzato"
PATTERN_LABELS[PATTERN_CONTOUR] = "Lungo le curve di livello"

#: A jitter larger than this fraction of the step could put two plants on top
#: of each other, so an irregular scheme is clamped to it. Half the step is
#: the theoretical limit; 0.4 keeps a margin.
MAX_JITTER_FRACTION = 0.4

#: Marching has to stop even if the containment test never fails (a hole in
#: the DEM, a degenerate frame). Points per row and rows per field.
MAX_STEPS_PER_ROW = 20_000
MAX_ROWS = 5_000


def _positive(value, name: str, italian: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise InvalidInputError(
            "{0} must be > 0, got {1!r}".format(name, value),
            user_message="{0} deve essere maggiore di zero.".format(italian))
    return number


@dataclass
class SlopeSpacing:
    """A scheme stated in distances measured on the ground."""

    plant_distance_m: float             # along a row, on the slope
    row_distance_m: float               # between rows, on the slope
    row_azimuth_deg: float = 90.0       # bearing the rows run; 90 = West-East
    pattern: str = grid_mod.PATTERN_RECT
    margin_m: float = 0.0
    step_mode: str = STEP_MAX_SLOPE
    #: Irregular scheme: the largest displacement a plant may take, in metres.
    jitter_m: float = 0.0
    seed: int = 0
    #: Custom scheme: displacement of each row along itself, as a fraction of
    #: the plant distance. Cycled, so ``(0.0, 0.5)`` is a quincunx by hand.
    custom_offsets: tuple = ()

    def __post_init__(self) -> None:
        self.plant_distance_m = _positive(
            self.plant_distance_m, "plant_distance_m",
            "La distanza fra le piante")
        self.row_distance_m = _positive(
            self.row_distance_m, "row_distance_m", "La distanza fra le file")
        if self.pattern not in ALL_PATTERNS:
            raise InvalidInputError(
                "unknown planting pattern {0!r}".format(self.pattern),
                user_message="Schema d'impianto non riconosciuto: "
                             "'{0}'.".format(self.pattern),
                hint="Usa: {0}.".format(", ".join(ALL_PATTERNS)))
        if self.step_mode not in STEP_MODES:
            raise InvalidInputError(
                "unknown step mode {0!r}".format(self.step_mode),
                user_message="Modalita' di correzione non riconosciuta.")
        if self.margin_m < 0.0:
            raise InvalidInputError(
                "margin_m must be >= 0, got {0!r}".format(self.margin_m),
                user_message="Il margine dal bordo non puo' essere negativo.")
        if self.jitter_m < 0.0:
            raise InvalidInputError(
                "jitter_m must be >= 0, got {0!r}".format(self.jitter_m),
                user_message="L'irregolarita' non puo' essere negativa.")
        self.custom_offsets = tuple(float(v) for v in self.custom_offsets)
        if self.pattern == PATTERN_CUSTOM and not self.custom_offsets:
            raise InvalidInputError(
                "the custom pattern needs at least one row offset",
                user_message="Lo schema personalizzato richiede almeno uno "
                             "sfalsamento di fila.")

    # -- what the pattern makes of the two distances -----------------------

    @property
    def base_pattern(self) -> str:
        """The ``core.grid`` pattern this scheme is a displacement of."""
        return BASE_PATTERN.get(self.pattern, self.pattern)

    def as_grid_spec(self) -> grid_mod.GridSpec:
        """The equivalent ``core.grid`` spec, in *real* distances.

        Built so the pattern rules -- square forcing dy = dx, hexagonal
        forcing dy = dx sqrt(3)/2, quincunx staggering alternate rows -- come
        from the one place that already defines them.
        """
        return grid_mod.GridSpec(
            spacing_x=self.plant_distance_m, spacing_y=self.row_distance_m,
            azimuth_deg=self.lattice_azimuth(), pattern=self.base_pattern,
            margin_m=self.margin_m)

    @property
    def effective_real_spacing(self):
        """(along a row, between rows) on the ground, after the pattern."""
        return self.as_grid_spec().effective_spacing

    @property
    def row_offset_alternates(self) -> bool:
        return self.as_grid_spec().row_offset_alternates

    def lattice_azimuth(self) -> float:
        """``core.grid``'s azimuth: the bearing the rows are stepped along."""
        return (self.row_azimuth_deg - 90.0) % 360.0

    def row_offset_fraction(self, row_index: int) -> float:
        """Displacement of one row along itself, as a fraction of the step."""
        if self.pattern == PATTERN_CUSTOM:
            return self.custom_offsets[row_index % len(self.custom_offsets)]
        if self.row_offset_alternates:
            return 0.5 if row_index % 2 else 0.0
        return 0.0

    def effective_jitter(self) -> float:
        """The jitter actually applied, clamped so two plants cannot meet."""
        if self.pattern != PATTERN_IRREGULAR:
            return 0.0
        dx, dy = self.effective_real_spacing
        return min(self.jitter_m, MAX_JITTER_FRACTION * min(dx, dy))

    def describe(self) -> "list[str]":
        dx, dy = self.effective_real_spacing
        return [
            "SESTO (distanze reali sul terreno)",
            "  Schema:            {0}".format(
                PATTERN_LABELS.get(self.pattern, self.pattern)),
            "  Sulla fila:        {0:.3f} m".format(dx),
            "  Fra le file:       {0:.3f} m".format(dy),
            "  Azimut delle file: {0:.1f} deg".format(self.row_azimuth_deg),
            "  Correzione:        {0}".format(
                STEP_MODE_LABELS[self.step_mode]),
            "  Margine dal bordo: {0:.3f} m".format(self.margin_m),
        ]


class SlopeStepper:
    """Turns a real distance into the planimetric one, at a given point.

    Everything it knows about the ground it asks
    :class:`~.terrain.TerrainAnalysis`, which asks ``core.z``: there is one
    elevation model in this plugin and one Horn operator, and this is not a
    second one.
    """

    def __init__(self, terrain, mode: str = STEP_MAX_SLOPE):
        if mode not in STEP_MODES:
            raise InvalidInputError(
                "unknown step mode {0!r}".format(mode),
                user_message="Modalita' di correzione non riconosciuta.")
        self.terrain = terrain
        self.mode = mode
        #: Steps taken where the DEM could not say: counted, never guessed at.
        self.steps_without_slope = 0

    # -- what the DEM says at one point ------------------------------------

    def sample(self, x: float, y: float):
        """``(slope_deg, aspect_deg, z)`` at one point, any of them NaN."""
        if self.terrain is None:
            return float("nan"), float("nan"), float("nan")
        slope, aspect = self.terrain.slope_aspect_at(x, y)
        z = self.terrain.elevation_at(x, y)
        return float(slope), float(aspect), float(z)

    @staticmethod
    def gradient(slope_deg: float, aspect_deg: float):
        """Uphill gradient ``(dz/dEast, dz/dNorth)`` from slope and aspect.

        Aspect is the compass azimuth of the *downslope* direction, so the
        uphill gradient points the other way. A flat cell has no aspect and
        its gradient is zero, which is the right answer rather than a
        missing one.
        """
        if not math.isfinite(slope_deg):
            return float("nan"), float("nan")
        magnitude = math.tan(math.radians(slope_deg))
        if not math.isfinite(aspect_deg) or magnitude <= 0.0:
            return 0.0, 0.0
        down_e, down_n = along_track_unit(aspect_deg)
        return -magnitude * down_e, -magnitude * down_n

    def plan_step(self, x: float, y: float, real_distance_m: float,
                  ux: float = 0.0, uy: float = 0.0) -> float:
        """The planimetric length of one step of ``real_distance_m``.

        ``(ux, uy)`` is the direction the step is taken in, used only by
        :data:`STEP_DIRECTIONAL`. Where the DEM has nothing to say the step
        is left uncorrected -- a missing slope is not a slope of zero, but
        inventing one would be worse than planting on the flat assumption and
        saying so, which is what ``steps_without_slope`` records.
        """
        slope, aspect, _z = self.sample(x, y)
        if not math.isfinite(slope):
            self.steps_without_slope += 1
            return real_distance_m
        if self.mode == STEP_MAX_SLOPE:
            return real_distance_m * math.cos(math.radians(slope))
        gx, gy = self.gradient(slope, aspect)
        if not math.isfinite(gx):
            self.steps_without_slope += 1
            return real_distance_m
        along = gx * ux + gy * uy               # dz per unit of plan distance
        return real_distance_m * math.cos(math.atan(along))

    def real_step(self, x: float, y: float, plan_distance_m: float,
                  ux: float = 0.0, uy: float = 0.0) -> float:
        """The inverse: how much ground a planimetric step covers."""
        factor = self.plan_step(x, y, 1.0, ux, uy)
        if factor <= GEOM_EPS_M:
            return plan_distance_m
        return plan_distance_m / factor


@dataclass
class SlopeGridResult:
    """The plants, and the numbers needed to defend them."""

    plants: "list[PlantingRecord]" = field(default_factory=list)
    rows: "list[np.ndarray]" = field(default_factory=list)
    spec: Optional[SlopeSpacing] = None
    usable_area_m2: float = 0.0
    steps_without_slope: int = 0
    warnings: "list[str]" = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.plants)

    @property
    def n_rows(self) -> int:
        return len({p.row_id for p in self.plants})

    def mean_plan_spacing(self) -> float:
        """Mean planimetric distance between consecutive plants of a row."""
        return mean_spacing(self.plants, with_z=False)

    def mean_real_spacing(self) -> float:
        """The same distance measured on the ground, Z included."""
        return mean_spacing(self.plants, with_z=True)

    def density_per_ha(self) -> float:
        if self.usable_area_m2 <= 0.0:
            return 0.0
        return self.count / (self.usable_area_m2 / 10_000.0)


def mean_spacing(plants, with_z: bool = False) -> float:
    """Mean distance between consecutive plants of the same row.

    On the map with ``with_z=False``, on the ground with it True. Written
    once because a plan generated zone by zone measures itself the same way
    a single-area plan does, and two copies of this would drift.
    """
    by_row = {}
    for plant in plants:
        by_row.setdefault(plant.row_id, []).append(plant)
    gaps = []
    for row in by_row.values():
        row.sort(key=lambda p: p.seq_in_row)
        for first, second in zip(row, row[1:]):
            dz = 0.0
            if with_z and first.z is not None and second.z is not None:
                dz = second.z - first.z
            gaps.append(math.sqrt((second.x - first.x) ** 2
                                  + (second.y - first.y) ** 2 + dz * dz))
    return sum(gaps) / len(gaps) if gaps else 0.0


# --------------------------------------------------------------------------
# Marching
# --------------------------------------------------------------------------

def march(stepper: SlopeStepper, start, direction, real_distance_m: float,
          limit_m: float, max_steps: int = MAX_STEPS_PER_ROW):
    """Walk from ``start`` along ``direction``, one corrected step at a time.

    Returns the points reached, ``start`` first. The slope is read at the
    point the walk has *reached*, not at the one it is heading for: a step
    cannot use a slope it has not arrived at yet, and on the plane the two
    agree exactly.
    """
    ux, uy = direction
    norm = math.hypot(ux, uy)
    if norm <= GEOM_EPS_M:
        raise InvalidInputError(
            "cannot march along a zero-length direction",
            user_message="Direzione di generazione non valida.")
    ux, uy = ux / norm, uy / norm

    x, y = float(start[0]), float(start[1])
    points = [(x, y)]
    travelled = 0.0
    for _ in range(max_steps):
        step = stepper.plan_step(x, y, real_distance_m, ux, uy)
        if step <= GEOM_EPS_M:
            break
        travelled += step
        if travelled > limit_m:
            break
        x, y = x + step * ux, y + step * uy
        points.append((x, y))
    return points


def _frame(spec: SlopeSpacing):
    """(row direction, row-stepping direction) as unit vectors."""
    along = along_track_unit(spec.row_azimuth_deg)
    across = along_track_unit(spec.row_azimuth_deg + 90.0)
    return along, across


def generate(usable_geometry, spec: SlopeSpacing, terrain=None,
             anchor=None) -> SlopeGridResult:
    """Lay the scheme out over a polygon, correcting every step for slope.

    ``usable_geometry`` is the *superficie utile* -- what
    :meth:`~.area.ReforestationArea.utile` returns -- already net of the
    exclusions and of the constraint buffers. The margin in the spec erodes
    it once more, because a setback from the edge is a property of the
    planting scheme and not of the parcel.

    Containment is GEOS on a prepared geometry, the same test the planimetric
    planner uses; ``intersects`` and not ``contains``, so a plant landing
    exactly on the eroded boundary survives.
    """
    from qgis.core import QgsGeometry, QgsPoint                 # noqa: PLC0415

    if spec.pattern == PATTERN_CONTOUR:
        # It would "work": the base pattern is a row lattice and straight
        # rows would come out. They would not be contours, and nothing
        # downstream could tell the difference -- so it is refused here
        # rather than answered wrongly.
        raise InvalidInputError(
            "contour planting does not come from the lattice generator",
            user_message="Le file su curve di livello si generano dalle "
                         "curve estratte dal DEM, non da un reticolo.",
            hint="Usa curves.plant_along_contours.")
    if usable_geometry is None or usable_geometry.isEmpty():
        raise EmptyAoiError(
            "planting scheme on an empty area",
            user_message="Nessuna superficie utile su cui impostare il sesto.")

    warnings: "list[str]" = []
    working = usable_geometry
    if spec.margin_m > 0.0:
        working = usable_geometry.buffer(-spec.margin_m, 12)
        if working is None or working.isEmpty():
            raise EmptyAoiError(
                "margin {0} m erodes the area completely".format(
                    spec.margin_m),
                user_message="Il margine dal bordo ({0:g} m) elimina "
                             "completamente l'area.".format(spec.margin_m),
                hint="Riduci il margine.")

    engine = QgsGeometry.createGeometryEngine(working.constGet())
    engine.prepareGeometry()

    def inside(x: float, y: float) -> bool:
        return engine.intersects(QgsPoint(float(x), float(y)))

    box = working.boundingBox()
    if math.hypot(box.width(), box.height()) <= GEOM_EPS_M:
        raise EmptyAoiError(
            "the usable area has no extent",
            user_message="La superficie utile non ha estensione.")

    if anchor is None:
        centroid = working.centroid()
        anchor = (centroid.constGet().x(), centroid.constGet().y())

    # How far a march has to go to be sure of leaving the area: the farthest
    # corner of the bounding box from the anchor, plus one step of slack so
    # that corner is reached rather than stopped just short of. Marching the
    # whole diagonal from the centre instead would walk twice as far as the
    # area needs, and every one of those wasted steps would be asking the DEM
    # about ground that is not in the project.
    reach = max(math.hypot(cx - anchor[0], cy - anchor[1])
                for cx, cy in ((box.xMinimum(), box.yMinimum()),
                               (box.xMaximum(), box.yMinimum()),
                               (box.xMaximum(), box.yMaximum()),
                               (box.xMinimum(), box.yMaximum())))

    stepper = SlopeStepper(terrain, spec.step_mode)
    if terrain is None:
        warnings.append(
            "Nessun DEM: le distanze richieste sono state usate come "
            "planimetriche, senza correzione di pendenza.")

    real_along, real_across = spec.effective_real_spacing
    row_reach = reach + real_along
    field_reach = reach + real_across
    row_dir, step_dir = _frame(spec)
    jitter = spec.effective_jitter()
    rng = np.random.default_rng(spec.seed) if jitter > 0.0 else None

    # Row origins: march both ways from the anchor along the stepping
    # direction, so the anchor is a row and the field grows symmetrically.
    forward = march(stepper, anchor, step_dir, real_across, field_reach,
                    MAX_ROWS)
    backward = march(stepper, anchor, (-step_dir[0], -step_dir[1]),
                     real_across, field_reach, MAX_ROWS)
    origins = [(point, index) for index, point in enumerate(forward)]
    origins += [(point, -index) for index, point in enumerate(backward)
                if index > 0]
    origins.sort(key=lambda item: item[1])

    plants: "list[PlantingRecord]" = []
    rows: "list[np.ndarray]" = []
    plant_id = 0
    for row_id, (origin, row_index) in enumerate(origins):
        offset = spec.row_offset_fraction(row_index) * real_along
        start = origin
        if abs(offset) > GEOM_EPS_M:
            shift = stepper.plan_step(origin[0], origin[1], offset,
                                      row_dir[0], row_dir[1])
            start = (origin[0] + shift * row_dir[0],
                     origin[1] + shift * row_dir[1])

        line = march(stepper, start, row_dir, real_along, row_reach)
        line += [p for p in march(stepper, start,
                                  (-row_dir[0], -row_dir[1]), real_along,
                                  row_reach)[1:]]
        line.sort(key=lambda p: (p[0] - start[0]) * row_dir[0]
                  + (p[1] - start[1]) * row_dir[1])

        kept = []
        for x, y in line:
            if jitter > 0.0:
                x = x + float(rng.uniform(-jitter, jitter))
                y = y + float(rng.uniform(-jitter, jitter))
            if not inside(x, y):
                continue
            slope, aspect, z = stepper.sample(x, y)
            plant_id += 1
            kept.append((x, y))
            plants.append(PlantingRecord(
                plant_id=plant_id, row_id=row_id, seq_in_row=len(kept) - 1,
                x=float(x), y=float(y),
                z=None if not math.isfinite(z) else float(z),
                spacing_x=real_along, spacing_y=real_across,
                azimuth_deg=spec.row_azimuth_deg,
                slope_deg=None if not math.isfinite(slope) else float(slope),
                aspect_deg=None if not math.isfinite(aspect)
                else float(aspect)))
        if kept:
            rows.append(np.asarray(kept, dtype=float))

    # Rows that fell entirely outside leave gaps in the numbering; close them
    # so "fila 3" means the third row that exists.
    renumber = {old: new for new, old in enumerate(
        sorted({p.row_id for p in plants}))}
    for plant in plants:
        plant.row_id = renumber[plant.row_id]

    if terrain is not None and stepper.steps_without_slope:
        # Only worth saying when there *is* a DEM: with none at all the
        # sentence above already says so, and "the DEM does not cover the
        # area" about an absent DEM reads as a second, different fault.
        warnings.append(
            "{0} passi calcolati senza pendenza: il DEM non copre tutta "
            "l'area.".format(stepper.steps_without_slope))

    return SlopeGridResult(
        plants=plants, rows=rows, spec=spec,
        usable_area_m2=float(working.area()),
        steps_without_slope=stepper.steps_without_slope, warnings=warnings)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def point_geometry(record: PlantingRecord):
    """One plant as a ``PointZ`` geometry, Z taken from the DEM.

    A plant with no elevation comes back as a plain 2-D point rather than as
    a point at zero: sea level is not "unknown".
    """
    from qgis.core import QgsGeometry, QgsPoint, QgsPointXY     # noqa: PLC0415

    if record.z is None or not math.isfinite(record.z):
        return QgsGeometry.fromPointXY(QgsPointXY(record.x, record.y))
    return QgsGeometry(QgsPoint(float(record.x), float(record.y),
                                float(record.z)))


#: Columns of a plants layer. ``specie`` and ``zona`` are Italian because an
#: operator reads them in the attribute table; the record's own attributes are
#: English because the code is.
PLANT_FIELDS = (
    ("plant_id", "int"),
    ("row_id", "int"),
    ("seq_in_row", "int"),
    ("zona", "string"),
    ("specie", "string"),
    ("z", "double"),
    ("slope_deg", "double"),
    ("aspect_deg", "double"),
)


def point_features(result: SlopeGridResult, fields=None, zone: str = ""):
    """The plants as ``QgsFeature``, ready for a PointZ layer."""
    from qgis.core import QgsFeature                            # noqa: PLC0415

    from .composition import species_of                        # noqa: PLC0415
    from .zones import zone_of                                 # noqa: PLC0415

    out = []
    for record in result.plants:
        feature = QgsFeature(fields) if fields is not None else QgsFeature()
        feature.setGeometry(point_geometry(record))
        if fields is not None:
            # A plant that knows its own zone wins over the caller's label:
            # a plan generated zone by zone would otherwise come out with
            # every point stamped with the same name.
            for name, value in (("plant_id", record.plant_id),
                                ("row_id", record.row_id),
                                ("seq_in_row", record.seq_in_row),
                                ("zona", zone_of(record) or zone),
                                ("specie", species_of(record)),
                                ("z", record.z),
                                ("slope_deg", record.slope_deg),
                                ("aspect_deg", record.aspect_deg)):
                index = fields.indexOf(name)
                if index >= 0:
                    feature.setAttribute(index, value)
        out.append(feature)
    return out


def plants_from_layer(layer, terrain=None, zone: str = ""):
    """The plants as the layer holds them *now*, after an operator edited it.

    The inverse of :func:`point_features`. Once a plant can be moved, added
    or deleted on the map, the layer is the truth and the generator's memory
    is history: every number an operator is then shown -- the count, the
    density, the mix, the anomalies -- has to be measured on this.

    A plant that was moved is re-sampled on the DEM: its elevation, slope and
    aspect belong to where it is now, not to where it was generated. A plant
    that was digitised has no attributes at all, so it is given the next free
    id and marked as an added row; its species stays empty until someone
    assigns one, which is what makes it visible in the composition readout
    instead of silently counting as the majority species.

    Returns ``(records, added)`` -- the records in layer order, and how many
    of them the layer had no ``plant_id`` for.
    """
    from .composition import SPECIES_ATTRIBUTE                  # noqa: PLC0415
    from .zones import ZONE_ATTRIBUTE                           # noqa: PLC0415

    records = []
    added = 0
    if layer is None:
        return records, added
    fields = layer.fields()
    has = {name: fields.indexOf(name) >= 0 for name, _kind in PLANT_FIELDS}
    known_ids = set()
    added_row = ADDED_ROW_ID
    for feature in layer.getFeatures():
        value = feature["plant_id"] if has.get("plant_id") else None
        if value is not None:
            try:
                known_ids.add(int(value))
            except (TypeError, ValueError):
                pass
    next_id = (max(known_ids) + 1) if known_ids else 1

    for index, feature in enumerate(layer.getFeatures()):
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            continue
        vertex = geometry.constGet()
        try:
            x, y = float(vertex.x()), float(vertex.y())
        except (AttributeError, TypeError):
            point = geometry.asPoint()
            x, y = float(point.x()), float(point.y())

        def _value(name):
            return feature[name] if has.get(name) else None

        raw_id = _value("plant_id")
        try:
            plant_id = int(raw_id)
        except (TypeError, ValueError):
            plant_id = next_id
            next_id += 1
            added += 1
        row_id = _int_or(_value("row_id"), None)
        if row_id is None:
            # A row of its own, so that two plants digitised at opposite
            # ends of the parcel are not measured as consecutive positions
            # in one very strange row.
            row_id = added_row
            added_row -= 1
        record = PlantingRecord(
            plant_id=plant_id,
            row_id=row_id,
            seq_in_row=_int_or(_value("seq_in_row"), index),
            x=x, y=y)
        z = _float_or(_value("z"), None)
        slope = _float_or(_value("slope_deg"), None)
        aspect = _float_or(_value("aspect_deg"), None)
        if terrain is not None:
            # Re-read the ground: a plant that was dragged 40 m downhill has
            # a different slope, and keeping the old one would defend the
            # plan with a number from before the edit.
            measured_slope, measured_aspect, measured_z = _sample(terrain,
                                                                  x, y)
            if math.isfinite(measured_z):
                z = float(measured_z)
            if math.isfinite(measured_slope):
                slope = float(measured_slope)
            if math.isfinite(measured_aspect):
                aspect = float(measured_aspect)
        record.z = z
        record.slope_deg = slope
        record.aspect_deg = aspect
        setattr(record, SPECIES_ATTRIBUTE, str(_value("specie") or ""))
        setattr(record, ZONE_ATTRIBUTE, str(_value("zona") or zone or ""))
        records.append(record)
    return records, added


#: First row id given to a plant an operator digitised by hand; the next
#: one gets -2, and so on. Negative so they can never collide with a
#: generated row, and one each so a hand-placed plant is never measured as
#: the neighbour of another hand-placed plant.
ADDED_ROW_ID = -1


def added_plants(plants) -> int:
    """How many of these plants were placed by hand rather than generated."""
    return sum(1 for record in plants if record.row_id <= ADDED_ROW_ID)


def _int_or(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _float_or(value, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def _sample(terrain, x, y):
    """(slope, aspect, z) at a point, whatever kind of terrain this is."""
    stepper = SlopeStepper(terrain, STEP_DIRECTIONAL)
    return stepper.sample(x, y)


def plants_layer(result: SlopeGridResult, crs_authid: str,
                 name: str = "Piante", zone: str = "",
                 apply_symbology: bool = True):
    """A PointZ memory layer holding the plants, coloured by species.

    The geometry type is PointZ because the elevation is part of the answer,
    not decoration: a plant at 640 m on a slope is a different plant from one
    at 320 m, and an export that drops Z loses the only thing the DEM was
    read for.
    """
    from qgis.core import QgsVectorLayer                        # noqa: PLC0415

    from ...io.layer_factory import make_fields                # noqa: PLC0415
    from . import symbology as symbology_mod                   # noqa: PLC0415

    layer = QgsVectorLayer("PointZ?crs={0}".format(crs_authid), name,
                           "memory")
    if not layer.isValid():
        raise InvalidInputError(
            "could not create the plants layer",
            user_message="Impossibile creare il layer delle piante.")
    layer.dataProvider().addAttributes(list(make_fields(PLANT_FIELDS)))
    layer.updateFields()
    layer.dataProvider().addFeatures(
        point_features(result, layer.fields(), zone=zone))
    layer.updateExtents()
    if apply_symbology:
        symbology_mod.apply_species_symbology(layer)
    return layer
