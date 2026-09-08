"""
Planting KPIs: density, row lengths, spacing checks.

Density is reported against the **usable** area (the AOI eroded by the edge
margin), because that is the area actually planted. Reporting plants/ha against
the gross AOI silently understates density by the margin ring, which on a small
or narrow parcel is a large fraction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .planting import PlantingResult


@dataclass
class PlantingStats:
    """Numbers for the panel, the report and the layer metadata."""

    n_plants: int = 0
    n_rows: int = 0
    n_excluded: int = 0
    theoretical_count: int = 0
    aoi_area_m2: float = 0.0
    usable_area_m2: float = 0.0
    spacing_x: float = 0.0
    spacing_y: float = 0.0
    row_length_total_m: float = 0.0
    row_length_mean_m: float = 0.0
    nearest_neighbour_mean_m: float = math.nan
    nearest_neighbour_min_m: float = math.nan

    @property
    def aoi_area_ha(self) -> float:
        return self.aoi_area_m2 / 10_000.0

    @property
    def usable_area_ha(self) -> float:
        return self.usable_area_m2 / 10_000.0

    @property
    def density_per_ha(self) -> float:
        """Plants per hectare of usable (planted) area."""
        return self.n_plants / self.usable_area_ha if self.usable_area_ha > 0 else 0.0

    @property
    def density_per_ha_gross(self) -> float:
        """Plants per hectare of gross AOI, for comparison with the above."""
        return self.n_plants / self.aoi_area_ha if self.aoi_area_ha > 0 else 0.0

    @property
    def theoretical_density_per_ha(self) -> float:
        cell = self.spacing_x * self.spacing_y
        return 10_000.0 / cell if cell > 0 else 0.0

    @property
    def fill_ratio(self) -> float:
        """Actual / theoretical count. Below 1 because of edges and filters."""
        return (self.n_plants / self.theoretical_count
                if self.theoretical_count else 0.0)


def _nearest_neighbour(xy: np.ndarray, sample_limit: int = 4000):
    """Mean and minimum nearest-neighbour distance.

    Brute force is O(n^2), so beyond ``sample_limit`` points a random sample is
    used; the figure is a sanity check on the lattice, not a precise statistic,
    and the caller is told when it was sampled.
    """
    n = xy.shape[0]
    if n < 2:
        return math.nan, math.nan
    if n > sample_limit:
        rng = np.random.default_rng(0)
        xy = xy[rng.choice(n, sample_limit, replace=False)]
    d2 = ((xy[:, None, :] - xy[None, :, :]) ** 2).sum(axis=2)
    np.fill_diagonal(d2, np.inf)
    nearest = np.sqrt(d2.min(axis=1))
    return float(nearest.mean()), float(nearest.min())


def compute_stats(result: PlantingResult) -> PlantingStats:
    """Derive every KPI from a :class:`~.planting.PlantingResult`."""
    dx, dy = (result.spec.effective_spacing if result.spec else (0.0, 0.0))
    xy = np.array([[p.x, p.y] for p in result.plants], dtype=float) \
        if result.plants else np.zeros((0, 2))

    lengths = [float(np.hypot(*np.diff(line[:, :2], axis=0).T).sum())
               for line in result.rows if line.shape[0] >= 2]
    mean_nn, min_nn = _nearest_neighbour(xy)

    return PlantingStats(
        n_plants=len(result.plants),
        n_rows=result.n_rows,
        n_excluded=len(result.excluded),
        theoretical_count=result.theoretical_count,
        aoi_area_m2=result.aoi_area_m2,
        usable_area_m2=result.usable_area_m2 or result.aoi_area_m2,
        spacing_x=dx, spacing_y=dy,
        row_length_total_m=float(sum(lengths)),
        row_length_mean_m=float(np.mean(lengths)) if lengths else 0.0,
        nearest_neighbour_mean_m=mean_nn,
        nearest_neighbour_min_m=min_nn,
    )


def format_report(stats: PlantingStats, result: PlantingResult) -> "list[str]":
    """Italian summary lines for the panel and the exported report."""
    lines = [
        "Superficie AOI: {0:,.0f} m2 ({1:.3f} ha)".format(
            stats.aoi_area_m2, stats.aoi_area_ha),
        "Superficie utile (al netto del margine): {0:,.0f} m2 ({1:.3f} ha)".format(
            stats.usable_area_m2, stats.usable_area_ha),
        "Sesto: {0:g} x {1:g} m".format(stats.spacing_x, stats.spacing_y),
        "Piante effettive: {0:,}".format(stats.n_plants),
        "Piante teoriche (superficie / sesto): {0:,}".format(stats.theoretical_count),
        "Grado di riempimento: {0:.1%}".format(stats.fill_ratio),
        "File: {0:,}  |  lunghezza totale {1:,.1f} m  |  media {2:,.1f} m".format(
            stats.n_rows, stats.row_length_total_m, stats.row_length_mean_m),
        "Densita': {0:,.1f} piante/ha (utile), {1:,.1f} piante/ha (lorda)".format(
            stats.density_per_ha, stats.density_per_ha_gross),
        "Densita' teorica del sesto: {0:,.1f} piante/ha".format(
            stats.theoretical_density_per_ha),
    ]
    if math.isfinite(stats.nearest_neighbour_mean_m):
        lines.append(
            "Distanza media al vicino piu' prossimo: {0:.2f} m (minima {1:.2f} m)"
            .format(stats.nearest_neighbour_mean_m, stats.nearest_neighbour_min_m))

    excl = result.exclusion_summary()
    if excl:
        from .planting import REASON_LABELS
        lines.append("Posizioni scartate: {0:,}".format(stats.n_excluded))
        for code, count in sorted(excl.items(), key=lambda kv: -kv[1]):
            lines.append("   - {0}: {1:,}".format(
                REASON_LABELS.get(code, code), count))
    for warning in result.warnings:
        lines.append("Avviso: {0}".format(warning))
    return lines
