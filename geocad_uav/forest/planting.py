"""
Forest Planting Designer: plant positions inside an existing project polygon.

Spec P3: the operator already has the polygon, so nothing here asks them to
redraw it. The AOI is eroded by the edge margin, a lattice is generated over
its envelope, and only positions genuinely inside the eroded polygon survive.

Topographic filtering (slope, elevation, aspect) is implemented, not deferred.
Rejected positions are kept with the reason they were rejected, so the operator
can see *why* a slope came out empty instead of just seeing fewer plants.

The lattice arithmetic and the filters are pure numpy; the point-in-polygon
test arrives as a callable, so the whole planner is testable without QGIS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from ..core.errors import EmptyAoiError, InvalidInputError
from ..core.grid import GridResult, GridSpec, filter_result, generate_grid, renumber
from ..core.models import PlantingRecord

# Rejection reasons, kept as codes so the GUI can translate and group them.
REASON_OUTSIDE = "outside_aoi"
REASON_MARGIN = "inside_margin"
REASON_SLOPE = "slope_out_of_range"
REASON_ELEVATION = "elevation_out_of_range"
REASON_ASPECT = "aspect_out_of_range"
REASON_NO_DEM = "no_elevation_data"

REASON_LABELS = {
    REASON_OUTSIDE: "Fuori dal poligono di progetto",
    REASON_MARGIN: "Dentro il margine dal bordo",
    REASON_SLOPE: "Pendenza fuori intervallo",
    REASON_ELEVATION: "Quota fuori intervallo",
    REASON_ASPECT: "Esposizione fuori intervallo",
    REASON_NO_DEM: "Nessun dato di quota disponibile",
}


@dataclass
class TopographicFilter:
    """Slope / elevation / aspect acceptance criteria.

    ``aspect_ranges`` is a list of ``(from_deg, to_deg)`` compass sectors; a
    sector that wraps through north (e.g. ``(315, 45)``) is handled, because
    "north-facing" is the single most common thing an operator asks for and
    writing it as two sectors would be a trap.
    """

    slope_min_deg: Optional[float] = None
    slope_max_deg: Optional[float] = None
    elev_min_m: Optional[float] = None
    elev_max_m: Optional[float] = None
    aspect_ranges: "list[tuple]" = field(default_factory=list)
    #: Drop positions where the DEM has no data, rather than keeping them blind.
    require_elevation: bool = True

    @property
    def is_active(self) -> bool:
        return any(v is not None for v in (self.slope_min_deg, self.slope_max_deg,
                                           self.elev_min_m, self.elev_max_m)) \
            or bool(self.aspect_ranges)

    def describe(self) -> "list[str]":
        out = []
        if self.slope_min_deg is not None or self.slope_max_deg is not None:
            out.append("Pendenza {0} - {1} gradi".format(
                "-" if self.slope_min_deg is None else "{0:g}".format(self.slope_min_deg),
                "-" if self.slope_max_deg is None else "{0:g}".format(self.slope_max_deg)))
        if self.elev_min_m is not None or self.elev_max_m is not None:
            out.append("Quota {0} - {1} m".format(
                "-" if self.elev_min_m is None else "{0:g}".format(self.elev_min_m),
                "-" if self.elev_max_m is None else "{0:g}".format(self.elev_max_m)))
        for lo, hi in self.aspect_ranges:
            out.append("Esposizione {0:g} - {1:g} gradi".format(lo, hi))
        return out

    def evaluate(self, slope_deg, aspect_deg, elevation_m):
        """Return ``(keep_mask, reason_codes)`` for arrays of DEM samples."""
        slope = np.asarray(slope_deg, dtype=float)
        aspect = np.asarray(aspect_deg, dtype=float)
        elev = np.asarray(elevation_m, dtype=float)
        n = elev.shape[0]
        keep = np.ones(n, dtype=bool)
        reasons = np.array([""] * n, dtype=object)

        def reject(mask, code):
            fresh = mask & keep
            reasons[fresh] = code
            keep[fresh] = False

        if self.require_elevation:
            reject(~np.isfinite(elev), REASON_NO_DEM)

        if self.elev_min_m is not None:
            reject(np.isfinite(elev) & (elev < self.elev_min_m), REASON_ELEVATION)
        if self.elev_max_m is not None:
            reject(np.isfinite(elev) & (elev > self.elev_max_m), REASON_ELEVATION)

        if self.slope_min_deg is not None:
            reject(np.isfinite(slope) & (slope < self.slope_min_deg), REASON_SLOPE)
        if self.slope_max_deg is not None:
            reject(np.isfinite(slope) & (slope > self.slope_max_deg), REASON_SLOPE)

        if self.aspect_ranges:
            in_any = np.zeros(n, dtype=bool)
            for lo, hi in self.aspect_ranges:
                lo = float(lo) % 360.0
                hi = float(hi) % 360.0
                if lo <= hi:
                    in_any |= (aspect >= lo) & (aspect <= hi)
                else:                       # sector wrapping through north
                    in_any |= (aspect >= lo) | (aspect <= hi)
            # A flat cell has no aspect; it cannot satisfy an aspect filter.
            reject(~in_any, REASON_ASPECT)

        return keep, reasons


@dataclass
class PlantingResult:
    """Accepted plants, rejected candidates, and the numbers that describe them."""

    plants: "list[PlantingRecord]" = field(default_factory=list)
    excluded: "list[PlantingRecord]" = field(default_factory=list)
    rows: "list[np.ndarray]" = field(default_factory=list)
    spec: Optional[GridSpec] = None
    aoi_area_m2: float = 0.0
    usable_area_m2: float = 0.0
    theoretical_count: int = 0
    warnings: "list[str]" = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.plants)

    @property
    def n_rows(self) -> int:
        return len({p.row_id for p in self.plants})

    def exclusion_summary(self) -> "dict[str, int]":
        summary: "dict[str, int]" = {}
        for record in self.excluded:
            summary[record.excluded_reason] = summary.get(
                record.excluded_reason, 0) + 1
        return summary


def plan_planting(bounds, spec: GridSpec,
                  inside: Callable[[np.ndarray], np.ndarray],
                  aoi_area_m2: float = 0.0,
                  usable_area_m2: float = 0.0,
                  elevation: Optional[Callable[[np.ndarray], np.ndarray]] = None,
                  slope_aspect: Optional[Callable] = None,
                  topo_filter: Optional[TopographicFilter] = None,
                  edge_distance: Optional[Callable] = None,
                  keep_excluded: bool = True) -> PlantingResult:
    """Generate the planting scheme.

    ``inside`` receives an ``(N, 2)`` array and returns a boolean mask -- the
    caller supplies it so the spatial predicate can be a prepared GEOS geometry
    in production and a plain analytic test in the unit tests.

    ``elevation`` / ``slope_aspect`` are optional DEM samplers; when absent the
    topographic filter is skipped and that is recorded as a warning rather than
    silently ignored.
    """
    warnings: "list[str]" = []
    grid = generate_grid(bounds, spec)
    if len(grid) == 0:
        raise EmptyAoiError(
            "grid produced no candidate positions",
            user_message="Nessuna posizione generata per quest'area.",
            hint="Verifica distanze, margine e dimensioni del poligono.")

    xy = grid.xy
    n = xy.shape[0]
    keep = np.asarray(inside(xy), dtype=bool)
    if keep.shape[0] != n:
        raise InvalidInputError(
            "inside() returned {0} values for {1} points".format(
                keep.shape[0], n),
            user_message="Errore interno nel test di appartenenza al poligono.")
    reasons = np.array([""] * n, dtype=object)
    reasons[~keep] = REASON_MARGIN if spec.margin_m > 0 else REASON_OUTSIDE

    # -- DEM sampling and topographic filtering ---------------------------
    z = np.full(n, np.nan)
    slope = np.full(n, np.nan)
    aspect = np.full(n, np.nan)

    if elevation is not None:
        z = np.asarray(elevation(xy), dtype=float)
    if slope_aspect is not None:
        slope, aspect = (np.asarray(a, dtype=float) for a in slope_aspect(xy))

    if topo_filter is not None and topo_filter.is_active:
        if elevation is None and slope_aspect is None:
            warnings.append(
                "Filtri topografici richiesti ma nessun DEM fornito: i filtri "
                "non sono stati applicati.")
        else:
            topo_keep, topo_reasons = topo_filter.evaluate(slope, aspect, z)
            newly = keep & ~topo_keep
            reasons[newly] = topo_reasons[newly]
            keep &= topo_keep

    kept_grid = renumber(filter_result(grid, keep))

    dx, dy = spec.effective_spacing
    dist = np.zeros(kept_grid.xy.shape[0])
    if edge_distance is not None and kept_grid.xy.shape[0]:
        dist = np.asarray(edge_distance(kept_grid.xy), dtype=float)

    kept_index = np.flatnonzero(keep)
    plants = []
    for i in range(kept_grid.xy.shape[0]):
        src = kept_index[i]
        plants.append(PlantingRecord(
            plant_id=i + 1,
            row_id=int(kept_grid.row[i]) + 1,
            seq_in_row=int(kept_grid.col[i]) + 1,
            x=float(kept_grid.xy[i, 0]), y=float(kept_grid.xy[i, 1]),
            z=None if not math.isfinite(z[src]) else float(z[src]),
            spacing_x=dx, spacing_y=dy,
            azimuth_deg=spec.azimuth_deg,
            inside_aoi=True,
            dist_to_edge=float(dist[i]),
            slope_deg=None if not math.isfinite(slope[src]) else float(slope[src]),
            aspect_deg=None if not math.isfinite(aspect[src]) else float(aspect[src]),
        ))

    excluded = []
    if keep_excluded:
        for src in np.flatnonzero(~keep):
            excluded.append(PlantingRecord(
                plant_id=-1,
                row_id=int(grid.row[src]) + 1,
                seq_in_row=int(grid.col[src]) + 1,
                x=float(xy[src, 0]), y=float(xy[src, 1]),
                z=None if not math.isfinite(z[src]) else float(z[src]),
                spacing_x=dx, spacing_y=dy,
                azimuth_deg=spec.azimuth_deg,
                inside_aoi=False,
                slope_deg=None if not math.isfinite(slope[src]) else float(slope[src]),
                aspect_deg=None if not math.isfinite(aspect[src]) else float(aspect[src]),
                excluded_reason=str(reasons[src]) or REASON_OUTSIDE,
            ))

    area_for_theory = usable_area_m2 or aoi_area_m2
    theoretical = int(math.floor(area_for_theory / (dx * dy))) if area_for_theory else 0

    return PlantingResult(
        plants=plants, excluded=excluded, rows=kept_grid.row_lines(),
        spec=spec, aoi_area_m2=aoi_area_m2, usable_area_m2=usable_area_m2,
        theoretical_count=theoretical, warnings=warnings)


# --------------------------------------------------------------------------
# QGIS adapter
# --------------------------------------------------------------------------

def plan_planting_for_geometry(aoi_geom, spec: GridSpec, terrain=None,
                               topo_filter: Optional[TopographicFilter] = None,
                               compute_edge_distance: bool = True,
                               max_edge_distance_points: int = 50_000):
    """Plan a scheme against a real ``QgsGeometry``.

    The AOI is eroded by ``spec.margin_m`` first; a prepared GEOS engine does
    the containment test, which is what keeps 50 000 candidates tractable
    (an unprepared ``contains()`` per point is roughly an order of magnitude
    slower on a complex boundary).
    """
    from qgis.core import QgsGeometry, QgsPoint         # noqa: PLC0415

    if aoi_geom is None or aoi_geom.isEmpty():
        raise EmptyAoiError(
            "empty AOI geometry",
            user_message="Nessun poligono di progetto selezionato.")

    aoi = aoi_geom if aoi_geom.isGeosValid() else aoi_geom.makeValid()
    aoi_area = float(aoi.area())

    working = aoi
    warnings = []
    if spec.margin_m > 0:
        working = aoi.buffer(-spec.margin_m, 12)
        if working is None or working.isEmpty():
            raise EmptyAoiError(
                "margin {0} m erodes the AOI completely".format(spec.margin_m),
                user_message="Il margine dal bordo ({0:g} m) elimina "
                             "completamente l'area.".format(spec.margin_m),
                hint="Riduci il margine o scegli un poligono piu' grande.")
    usable_area = float(working.area())

    engine = QgsGeometry.createGeometryEngine(working.constGet())
    engine.prepareGeometry()

    def inside(points):
        # intersects(), not contains(): contains() is strictly interior, so a
        # position landing exactly on the (already eroded) boundary would be
        # dropped. The edge margin is the explicit control for standing off.
        mask = np.zeros(points.shape[0], dtype=bool)
        for i in range(points.shape[0]):
            mask[i] = engine.intersects(
                QgsPoint(float(points[i, 0]), float(points[i, 1])))
        return mask

    elevation = slope_aspect_fn = edge_distance = None
    if terrain is not None:
        from ..core.z import sample_slope_aspect                # noqa: PLC0415

        def elevation(points):                                  # noqa: F811
            return terrain.sample(points[:, 0], points[:, 1])

        def slope_aspect_fn(points):                            # noqa: F811
            return sample_slope_aspect(terrain, points[:, 0], points[:, 1])

    if compute_edge_distance:
        boundary = working.constGet().boundary()
        boundary_geom = QgsGeometry(boundary.clone()) if boundary else None

        if boundary_geom is not None:
            def edge_distance(points):                          # noqa: F811
                if points.shape[0] > max_edge_distance_points:
                    return np.zeros(points.shape[0])
                return np.array([
                    boundary_geom.distance(QgsGeometry(
                        QgsPoint(float(p[0]), float(p[1]))))
                    for p in points])

    box = aoi.boundingBox()
    result = plan_planting(
        bounds=(box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()),
        spec=spec, inside=inside, aoi_area_m2=aoi_area,
        usable_area_m2=usable_area, elevation=elevation,
        slope_aspect=slope_aspect_fn, topo_filter=topo_filter,
        edge_distance=edge_distance)
    result.warnings.extend(warnings)
    if compute_edge_distance and len(result.plants) > max_edge_distance_points:
        result.warnings.append(
            "Distanza dal bordo non calcolata: oltre {0} piante.".format(
                max_edge_distance_points))
    return result
