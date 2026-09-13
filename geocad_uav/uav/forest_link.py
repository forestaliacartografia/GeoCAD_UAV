"""
From a planting plan to a flight plan.

A reforestation project already knows everything a photogrammetric flight
over it needs: the surface that will actually be planted, the things taken
off it and why, the direction the rows run, and the DEM the whole design was
draped on. Re-entering all of that in a flight panel is not just tedious --
it is where the two drift apart, and a flight flown over last week's area is
worse than no flight at all.

This module is the join. It is pure model code: it reads a
``ReforestationArea`` and returns the AOI, the candidate obstacles and a
:class:`~geocad_uav.uav.mission.MissionParams` built on the forestry
defaults. It imports no widget and knows nothing about panels -- the GUI
calls it, never the other way round (spec section 2).

What makes the defaults "forestry":

* **Overlap** -- ``complex_terrain`` from the frozen preset table
  (85 % / 75 %). Broken canopy is the textbook case for high overlap: a
  crown seen in two frames reconstructs, one seen in one frame does not.
* **Terrain following** -- always. A planting block that was worth designing
  on a DEM has the relief that makes a single AMSL height meaningless.
* **Azimuth** -- the planting rows by default, so the imagery runs along
  what is on the ground and a row can be followed across frames; the
  measured sweep is one argument away for whoever wants the shortest flight
  instead.

Nothing here guesses a vegetation clearance. A bare-earth DTM under standing
canopy needs one and only the operator knows whether the DEM is bare earth,
so the parameter is passed through and left at zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..core.models import AltitudeMode
from . import photogrammetry as pg
from . import survey as sv
from .mission import MissionParams

#: Overlap preset used for a forestry block. A key into the frozen table in
#: ``photogrammetry.OVERLAP_PRESETS``, not a pair of numbers written here.
FOREST_OVERLAP_KEY = "complex_terrain"


class ForestLinkError(ValueError):
    """Raised when a planting project cannot be turned into a flight."""


@dataclass
class ForestObstacle:
    """One thing the planting design took off the area, offered to the flight.

    Whether it is a flight hazard is the operator's call and not this
    module's: a buffer round a watercourse is a place not to plant, an
    overhead line is a place not to fly, and both arrive here identically as
    exclusions. Each carries the constraint kind it came from so a panel can
    show it and let the operator tick it.
    """

    source: str                         # the constraint kind key
    label: str
    geometry: object                    # QgsGeometry
    area_m2: float = 0.0


@dataclass
class ForestSurvey:
    """What a planting project hands to a flight planner."""

    aoi_geom: object                    # QgsGeometry: the usable surface
    crs_authid: str = ""
    obstacles: "list[ForestObstacle]" = field(default_factory=list)
    #: Azimuth the planting rows run at, when the design settled on one.
    row_azimuth_deg: Optional[float] = None
    #: Plants the design places, for the report. Never used in a computation.
    plant_count: int = 0
    label: str = ""
    notes: "list[str]" = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return (self.aoi_geom is None
                or bool(getattr(self.aoi_geom, "isEmpty", bool)()))

    @property
    def area_m2(self) -> float:
        if self.is_empty:
            return 0.0
        return float(self.aoi_geom.area())

    def obstacle_geometries(self, sources=None):
        """The obstacle geometries, optionally only the kinds named."""
        return [ob.geometry for ob in self.obstacles
                if ob.geometry is not None
                and (sources is None or ob.source in sources)]

    def describe(self) -> "list[str]":
        lines = ["Area utile: {0:,.0f} m2".format(self.area_m2)]
        if self.row_azimuth_deg is not None:
            lines.append("Orientamento dei filari: {0:.1f} deg"
                         .format(self.row_azimuth_deg))
        if self.plant_count:
            lines.append("Piante previste: {0:,}".format(self.plant_count))
        if self.obstacles:
            lines.append("Esclusioni disponibili come ostacoli: {0}"
                         .format(len(self.obstacles)))
        lines.extend(self.notes)
        return lines


def survey_from_area(area, crs_authid: str = "",
                     row_azimuth_deg: Optional[float] = None,
                     plant_count: int = 0,
                     label: str = "") -> ForestSurvey:
    """Read a ``ReforestationArea`` into a :class:`ForestSurvey`.

    The AOI is the **usable** surface, not the gross one: the usable surface
    is what the plan plants and therefore what has to be photographed, and
    it is already cut by every constraint the operator set.
    """
    if area is None:
        raise ForestLinkError("no reforestation area")
    usable = area.utile()
    if usable is None or usable.isEmpty():
        raise ForestLinkError("the usable surface is empty")

    obstacles = []
    for exclusion in getattr(area, "exclusions", []) or []:
        if exclusion.geometry is None or exclusion.geometry.isEmpty():
            continue
        obstacles.append(ForestObstacle(
            source=exclusion.source or "",
            label=exclusion.label or exclusion.source or "",
            geometry=exclusion.geometry,
            area_m2=exclusion.area_m2()))

    notes = []
    if area.esclusa_m2 > 0.0:
        notes.append(
            "L'area di volo e' la superficie utile ({0:,.0f} m2): le "
            "esclusioni del progetto ({1:,.0f} m2) ne sono gia' fuori."
            .format(float(area.utile_m2), float(area.esclusa_m2)))

    return ForestSurvey(
        aoi_geom=usable,
        crs_authid=crs_authid or getattr(area, "crs_authid", "") or "",
        obstacles=obstacles,
        row_azimuth_deg=row_azimuth_deg,
        plant_count=int(plant_count or 0),
        label=label or getattr(area, "label", "") or "",
        notes=notes)


def forest_overlap() -> pg.Overlap:
    """The forestry overlap, from the frozen preset table."""
    try:
        return pg.OVERLAP_PRESETS[FOREST_OVERLAP_KEY]
    except KeyError:                                            # pragma: no cover
        raise ForestLinkError(
            "overlap preset {0!r} is missing".format(FOREST_OVERLAP_KEY))


def mission_params(survey: ForestSurvey, camera, drone,
                   h_agl_m: Optional[float] = None,
                   gsd_m: Optional[float] = None,
                   follow_rows: bool = True,
                   vegetation_clearance_m: float = 0.0,
                   safety_margin_m: float = 0.0,
                   v_mission_ms: Optional[float] = None,
                   overlap: Optional[pg.Overlap] = None) -> MissionParams:
    """Flight parameters for one planting block, on the forestry defaults.

    Exactly one of ``h_agl_m`` and ``gsd_m`` is given, as everywhere else in
    this plugin: height and ground resolution are two ends of the same
    optical relation and fixing both over-determines it.

    ``follow_rows`` orients the strips along the planting rows when the
    design has an orientation to give; with it off -- or with no row
    orientation on the project -- the azimuth is the measured one, swept and
    costed by :func:`survey.optimise_azimuth`.
    """
    if survey is None or survey.is_empty:
        raise ForestLinkError("the survey has no area")
    if (h_agl_m is None) == (gsd_m is None):
        raise ForestLinkError(
            "give exactly one of h_agl_m and gsd_m")

    azimuth_strategy = sv.AZIMUTH_OPTIMISED
    manual_azimuth = None
    if follow_rows and survey.row_azimuth_deg is not None:
        azimuth_strategy = sv.AZIMUTH_MANUAL
        manual_azimuth = float(survey.row_azimuth_deg)

    return MissionParams(
        camera=camera,
        drone=drone,
        overlap=overlap if overlap is not None else forest_overlap(),
        h_agl_m=h_agl_m,
        gsd_m=gsd_m,
        # A planting block designed on a DEM is by definition not flat
        # enough to photograph from one height.
        altitude_mode=AltitudeMode.TERRAIN,
        safety_margin_m=float(safety_margin_m),
        vegetation_clearance_m=float(vegetation_clearance_m),
        azimuth_strategy=azimuth_strategy,
        manual_azimuth_deg=manual_azimuth,
        v_mission_ms=v_mission_ms)


def describe_defaults() -> "list[str]":
    """What the forestry preset is, in the operator's words."""
    overlap = forest_overlap()
    front, side = overlap.as_percent
    return [
        "Sovrapposizione {0:.0f} % longitudinale e {1:.0f} % laterale "
        "(preset '{2}'): la chioma spezzata e' il caso da manuale per "
        "l'alta sovrapposizione.".format(front, side, FOREST_OVERLAP_KEY),
        "Terrain following sempre attivo: un impianto progettato su DEM ha "
        "il dislivello che rende insensata una quota AMSL unica.",
        "Strisciate lungo i filari quando il progetto ne ha un "
        "orientamento; altrimenti azimut misurato con la scansione.",
        "Nessuna maggiorazione per la vegetazione viene aggiunta da sola: "
        "se il DEM e' un DTM sotto chioma, la quota va alzata a mano.",
    ]
