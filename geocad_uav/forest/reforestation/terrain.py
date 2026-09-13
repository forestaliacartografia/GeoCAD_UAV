"""
M02 -- morphology: elevation, slope, aspect and the suitability mask.

This module reads a DEM and answers questions about it. It does not compute
terrain: ``core.z`` already owns the elevation grid (``TerrainModel``, warped
into the working CRS through the QGIS raster layer and sampled bilinearly,
no-data carried as NaN) and the Horn 3x3 slope/aspect operator -- the same
kernel ``gdaldem slope`` uses, so a number here can be checked against GDAL.
Writing a second slope here would mean two answers to one question.

What this module adds is the reforestation reading of that grid: a suitability
mask, cell by cell, from criteria the operator sets. The acceptance rules
themselves are ``forest.planting.TopographicFilter``, already used to accept
or reject individual plants, so the mask an operator looks at and the filter
that later drops a plant cannot disagree.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ...core import z as z_mod
from ...core.errors import InvalidInputError, RasterError
from ..planting import REASON_LABELS, REASON_SLOPE, TopographicFilter

M2_PER_HA = 10_000.0

#: Cells beyond which restricting a mask to a polygon is refused rather than
#: run for minutes. A parameter on the call; this is only the default.
DEFAULT_MAX_MASK_CELLS = 2_000_000


@dataclass
class SuitabilityMask:
    """Which cells of the DEM window a plant could stand on, and why not."""

    mask: np.ndarray                    # 2-D bool
    reasons: np.ndarray                 # 2-D object, "" where accepted
    cell_area_m2: float
    criteria: Optional[TopographicFilter] = None
    warnings: "list[str]" = field(default_factory=list)

    @property
    def shape(self):
        return self.mask.shape

    @property
    def n_cells(self) -> int:
        return int(self.mask.size)

    @property
    def suitable_cells(self) -> int:
        return int(np.count_nonzero(self.mask))

    @property
    def suitable_area_m2(self) -> float:
        return self.suitable_cells * self.cell_area_m2

    @property
    def suitable_area_ha(self) -> float:
        return self.suitable_area_m2 / M2_PER_HA

    @property
    def suitable_fraction(self) -> float:
        return 0.0 if not self.n_cells else self.suitable_cells / self.n_cells

    def rejection_counts(self) -> "dict[str, int]":
        counts: "dict[str, int]" = {}
        for code in self.reasons[~self.mask].ravel():
            if code:
                counts[code] = counts.get(code, 0) + 1
        return counts

    def describe(self) -> "list[str]":
        lines = ["MASCHERA DI IDONEITA'"]
        if self.criteria is not None:
            lines.extend("  criterio: {0}".format(text)
                         for text in self.criteria.describe())
        lines.append("  celle idonee: {0:,} su {1:,} ({2:.1%})".format(
            self.suitable_cells, self.n_cells, self.suitable_fraction))
        lines.append("  superficie idonea: {0:,.4f} ha".format(
            self.suitable_area_ha))
        for code, count in sorted(self.rejection_counts().items(),
                                  key=lambda item: -item[1]):
            lines.append("  escluse {0:,}: {1}".format(
                count, REASON_LABELS.get(code, code)))
        lines.extend("  " + text for text in self.warnings)
        return lines


class TerrainAnalysis:
    """A DEM window, read for reforestation purposes.

    Built from a ``QgsRasterLayer`` (the operator's DEM) or directly from an
    elevation array (a synthetic surface, a test, a DEM already in memory).
    Both paths end at the same ``core.z.TerrainModel``.
    """

    def __init__(self, model, warnings=()):
        if model is None:
            raise RasterError(
                "terrain analysis without an elevation model",
                user_message="Nessun modello digitale del terreno caricato.")
        self.model = model
        self.warnings = list(warnings)
        self._grids = None
        self._raster_layer = None

    # -- construction ------------------------------------------------------

    @classmethod
    def from_layer(cls, raster_layer, work_crs, bounds, margin_m: float = 0.0,
                   band: int = 1, is_surface_model: bool = False,
                   vertical_datum: str = "unknown"):
        """Warp the DEM window covering ``bounds`` into the working CRS.

        ``bounds`` is a ``QgsGeometry``, a ``QgsRectangle`` or a plain
        ``(xmin, ymin, xmax, ymax)``; a geometry is reduced to its bounding
        box because a raster window is rectangular whatever the AOI looks
        like. The clipping to the real polygon happens later, on the mask
        (:meth:`restrict_to`), where it can be done honestly.
        """
        if raster_layer is None:
            raise RasterError(
                "no DEM layer given",
                user_message="Nessun layer DEM selezionato.")
        model, warnings = z_mod.TerrainModel.from_layer(
            raster_layer, work_crs, cls._bbox(bounds), margin_m=margin_m,
            band=band, is_surface_model=is_surface_model,
            vertical_datum=vertical_datum)
        return cls(model, warnings)

    @classmethod
    def auto_download(cls, bounds, source_crs, adapter_id: str = "nasadem",
                      work_crs=None, margin_m: float = 50.0, key=None,
                      transport=None, feedback=None, cache_dir=None):
        """Fetch a DEM covering ``bounds`` and open it, without asking anyone.

        The point of this is that an operator with a parcel should not have to
        go and find a raster first. The window is the bounding box of what
        they already drew -- the *superficie utile* -- grown by ``margin_m``
        so the slope at the very edge is computed from real neighbours rather
        than from the padding.

        Nothing about the download is new here: ``io.dem_source`` already owns
        the adapters, the disk cache and, above all, the declared status of
        each source. An adapter whose request schema was never verified end to
        end refuses to run, and says so with the URL of its documentation --
        this method does not talk it into working. Today that means the
        OpenTopography-hosted NASADEM is the one that downloads, and it wants
        the key an operator puts in the settings; the others are listed,
        described and honest about being PARTIAL.

        Returns a :class:`TerrainAnalysis`, or None if the operator cancelled.
        """
        from qgis.core import (QgsCoordinateReferenceSystem,    # noqa: PLC0415
                               QgsCoordinateTransform, QgsProject,
                               QgsRectangle)

        from ...io import dem_source                           # noqa: PLC0415

        if source_crs is None or not source_crs.isValid():
            raise InvalidInputError(
                "cannot download a DEM without knowing the area's CRS",
                user_message="Sistema di riferimento dell'area non definito.")
        xmin, ymin, xmax, ymax = cls._bbox(bounds)
        margin = max(0.0, float(margin_m))
        rectangle = QgsRectangle(xmin - margin, ymin - margin,
                                 xmax + margin, ymax + margin)

        # The adapters speak degrees; the project almost never does.
        geographic = QgsCoordinateReferenceSystem("EPSG:4326")
        if source_crs != geographic:
            transform = QgsCoordinateTransform(source_crs, geographic,
                                               QgsProject.instance())
            rectangle = transform.transformBoundingBox(rectangle)
        bbox = (rectangle.xMinimum(), rectangle.yMinimum(),
                rectangle.xMaximum(), rectangle.yMaximum())

        kwargs = {"feedback": feedback, "cache_dir": cache_dir}
        if key is not None:
            kwargs["key"] = key
        if transport is not None:
            kwargs["transport"] = transport
        path = dem_source.fetch(adapter_id, bbox, **kwargs)
        if path is None:
            return None

        layer = dem_source.raster_layer(path, "DEM {0}".format(adapter_id))
        target = work_crs if work_crs is not None else source_crs
        return cls.from_layer(layer, target, bounds, margin_m=margin)

    @classmethod
    def from_array(cls, elevation, transform, crs_authid: str = "",
                   source: str = "", is_surface_model: bool = False):
        """A surface already in memory: an array plus a geotransform."""
        model = z_mod.TerrainModel(elevation, transform,
                                   crs_authid=crs_authid, source=source,
                                   is_surface_model=is_surface_model)
        return cls(model)

    @staticmethod
    def _bbox(bounds):
        """(xmin, ymin, xmax, ymax) from a geometry, a rectangle or a tuple."""
        if bounds is None:
            raise InvalidInputError(
                "no bounds for the DEM window",
                user_message="Nessuna area indicata per il ritaglio del DEM.")
        if hasattr(bounds, "boundingBox"):
            bounds = bounds.boundingBox()
        if hasattr(bounds, "xMinimum"):
            return (bounds.xMinimum(), bounds.yMinimum(),
                    bounds.xMaximum(), bounds.yMaximum())
        xmin, ymin, xmax, ymax = (float(v) for v in bounds)
        return (xmin, ymin, xmax, ymax)

    # -- the grid ----------------------------------------------------------

    @property
    def cellsize_m(self) -> float:
        return float(self.model.cellsize)

    @property
    def cell_area_m2(self) -> float:
        return float(self.model.cellsize_x * self.model.cellsize_y)

    @property
    def nodata_fraction(self) -> float:
        return float(self.model.nodata_fraction)

    def grids(self):
        """``(slope_deg, aspect_deg)`` for the whole window, computed once.

        Horn's operator, straight from ``core.z``; cells whose 3x3 window
        touches no-data come back NaN rather than being guessed from a partial
        window, and a flat cell has NaN aspect because a flat surface faces
        nowhere.
        """
        if self._grids is None:
            self._grids = z_mod.slope_aspect(self.model)
        return self._grids

    def cell_centres(self):
        """``(X, Y)`` world coordinates of every cell centre."""
        x0, dx, _, y0, _, dy = self.model.gt
        xs = x0 + (np.arange(self.model.cols) + 0.5) * dx
        ys = y0 + (np.arange(self.model.rows) + 0.5) * dy
        return np.meshgrid(xs, ys)

    # -- point queries -----------------------------------------------------

    def elevation_at(self, x, y):
        """Bilinear elevation; NaN outside the window or on no-data."""
        return self.model.sample(x, y)

    def slope_aspect_at(self, x, y):
        """Slope and aspect of the cell each point falls in.

        The same answer ``core.z.sample_slope_aspect`` gives -- a test pins
        that, cell by cell -- but read off the grids this object already
        holds. The frozen helper recomputes the whole Horn operator on every
        call, which is free for a handful of waypoints and ruinous for a
        plantation: laying out 30 000 plants asks this question 30 000 times,
        once per corrected step.
        """
        slope, aspect = self.grids()
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        x0, dx, _, y0, _, dy = self.model.gt
        col = np.floor((x - x0) / dx).astype(np.int64)
        row = np.floor((y0 - y) / (-dy)).astype(np.int64)
        ok = ((col >= 0) & (row >= 0)
              & (col < self.model.cols) & (row < self.model.rows))
        colc = np.clip(col, 0, self.model.cols - 1)
        rowc = np.clip(row, 0, self.model.rows - 1)
        return (np.where(ok, slope[rowc, colc], np.nan),
                np.where(ok, aspect[rowc, colc], np.nan))

    def as_planner_terrain(self):
        """The object ``forest.planting`` expects as its ``terrain``.

        It is the model itself: the planner calls ``terrain.sample(...)`` and
        ``core.z.sample_slope_aspect(terrain, ...)``, so handing it anything
        else would mean maintaining a second terrain interface.
        """
        return self.model

    # -- the mask ----------------------------------------------------------

    def suitability(self, criteria: Optional[TopographicFilter] = None,
                    require_slope: bool = True) -> SuitabilityMask:
        """Evaluate the acceptance criteria over every cell of the window.

        ``criteria`` is a ``forest.planting.TopographicFilter`` -- the same
        object that later accepts or rejects each individual plant. With no
        criteria every cell with a real elevation is suitable.

        ``require_slope`` closes the one gap between a per-plant filter and a
        map: the filter compares only finite slopes, so a cell whose slope
        could not be computed (its 3x3 window touches a hole) would pass a
        slope criterion untested. On a mask that reads as "suitable", which it
        is not; here such a cell is rejected as unmeasured. Set it False to
        get exactly the frozen filter's behaviour.
        """
        criteria = criteria or TopographicFilter()
        slope, aspect = self.grids()
        elevation = self.model.z
        shape = elevation.shape

        keep, reasons = criteria.evaluate(slope.ravel(), aspect.ravel(),
                                          elevation.ravel())
        keep = keep.reshape(shape)
        reasons = reasons.reshape(shape)

        if require_slope and (criteria.slope_min_deg is not None
                              or criteria.slope_max_deg is not None):
            unmeasured = keep & ~np.isfinite(slope)
            reasons[unmeasured] = REASON_SLOPE
            keep = keep & ~unmeasured

        return SuitabilityMask(mask=keep, reasons=reasons,
                               cell_area_m2=self.cell_area_m2,
                               criteria=criteria,
                               warnings=list(self.warnings))

    def restrict_to(self, suitability: SuitabilityMask, geometry,
                    max_cells: int = DEFAULT_MAX_MASK_CELLS) -> SuitabilityMask:
        """Drop every cell whose centre falls outside ``geometry``.

        The DEM window is a rectangle and the project is not, so the suitable
        surface only means something once it has been cut to the real polygon.
        The test is GEOS on a prepared geometry -- the same one the planner
        uses on candidate positions -- run on cell centres.
        """
        from qgis.core import QgsGeometry, QgsPoint            # noqa: PLC0415

        if geometry is None or geometry.isEmpty():
            return suitability
        if suitability.n_cells > max_cells:
            raise RasterError(
                "mask has {0} cells, over the {1} cap".format(
                    suitability.n_cells, max_cells),
                user_message="La finestra DEM e' troppo grande per essere "
                             "ritagliata sull'area ({0:,} celle).".format(
                                 suitability.n_cells),
                hint="Ritaglia il DEM sull'area di progetto.")

        engine = QgsGeometry.createGeometryEngine(geometry.constGet())
        engine.prepareGeometry()
        xx, yy = self.cell_centres()
        inside = np.zeros(suitability.shape, dtype=bool)
        rows, cols = suitability.shape
        for row in range(rows):
            for col in range(cols):
                if not suitability.mask[row, col]:
                    continue        # already rejected; no need to ask GEOS
                inside[row, col] = engine.intersects(
                    QgsPoint(float(xx[row, col]), float(yy[row, col])))

        reasons = suitability.reasons.copy()
        dropped = suitability.mask & ~inside
        reasons[dropped] = "outside_aoi"
        return SuitabilityMask(mask=suitability.mask & inside, reasons=reasons,
                               cell_area_m2=suitability.cell_area_m2,
                               criteria=suitability.criteria,
                               warnings=list(suitability.warnings))

    # -- the DEM, as a layer -----------------------------------------------

    def raster_layer(self, path: Optional[str] = None, name: str = "DEM"):
        """The working grid as a ``QgsRasterLayer``, written once to disk.

        The contour algorithm is GDAL's and wants a raster, and the raster it
        should read is *this* one: the window already warped into the working
        CRS, with the same no-data cells the rest of the package sees. Handing
        it the original source file instead would contour a grid in another
        CRS and another resolution, and the contours would not line up with
        the slope the plants are spaced by.

        Written to a temporary GeoTIFF on first use and kept, so contouring
        twice at two intervals does not warp and write twice.
        """
        from qgis.core import (QgsCoordinateReferenceSystem,      # noqa: PLC0415
                               QgsRasterLayer)

        from ...io import layer_factory as lf                     # noqa: PLC0415

        if self._raster_layer is not None and path is None:
            try:
                if self._raster_layer.isValid():
                    return self._raster_layer
            except RuntimeError:
                pass                        # deleted under us; write another
        target = path
        if target is None:
            handle, target = tempfile.mkstemp(prefix="geocad_dem_",
                                              suffix=".tif")
            os.close(handle)
        crs = QgsCoordinateReferenceSystem(self.model.crs_authid)
        lf.write_geotiff(target, self.model.z, self.model.gt,
                         crs.toWkt() if crs.isValid() else "")
        layer = QgsRasterLayer(target, name, "gdal")
        if not layer.isValid():
            raise RasterError(
                "the DEM window could not be reopened from {0}".format(target),
                user_message="Impossibile rileggere il DEM di lavoro.")
        if path is None:
            self._raster_layer = layer
        return layer

    def release(self) -> None:
        """Let go of the raster written for GDAL, while QGIS is still up.

        That layer was never added to the project, so nothing else owns it.
        Left to the interpreter it is collected when the module globals go,
        which on shutdown is *after* QGIS has torn itself down: a
        use-after-free that takes the process with it and prints nothing.
        Called from the plugin's unload, where QGIS is still alive.
        """
        self._raster_layer = None

    # -- readout -----------------------------------------------------------

    def statistics(self, mask=None) -> dict:
        """Elevation, slope and aspect statistics over the window or a mask."""
        slope, aspect = self.grids()
        z_min, z_max, z_mean, nodata = self.model.stats_in_mask(mask)
        selected_slope = slope if mask is None else slope[mask]
        finite_slope = selected_slope[np.isfinite(selected_slope)]
        selected_aspect = aspect if mask is None else aspect[mask]
        finite_aspect = selected_aspect[np.isfinite(selected_aspect)]
        return {
            "cellsize_m": self.cellsize_m,
            "cell_area_m2": self.cell_area_m2,
            "z_min_m": z_min, "z_max_m": z_max, "z_mean_m": z_mean,
            "nodata_fraction": nodata,
            "slope_min_deg": float(finite_slope.min()) if finite_slope.size
                             else float("nan"),
            "slope_max_deg": float(finite_slope.max()) if finite_slope.size
                             else float("nan"),
            "slope_mean_deg": float(finite_slope.mean()) if finite_slope.size
                              else float("nan"),
            "aspect_cells": int(finite_aspect.size),
            "dominant_slope_azimuth_deg":
                self.model.dominant_slope_azimuth(mask),
        }

    def describe(self, mask=None) -> "list[str]":
        stats = self.statistics(mask)
        lines = ["MORFOLOGIA"]
        lines.append("  Cella:            {0:.2f} m ({1:.2f} m2)".format(
            stats["cellsize_m"], stats["cell_area_m2"]))
        lines.append("  Quota min/media/max: {0:.1f} / {1:.1f} / {2:.1f} m"
                     .format(stats["z_min_m"], stats["z_mean_m"],
                             stats["z_max_m"]))
        lines.append("  Pendenza min/media/max: {0:.2f} / {1:.2f} / {2:.2f} deg"
                     .format(stats["slope_min_deg"], stats["slope_mean_deg"],
                             stats["slope_max_deg"]))
        azimuth = stats["dominant_slope_azimuth_deg"]
        lines.append("  Direzione di massima pendenza: {0}".format(
            "indeterminata (superficie piana)" if azimuth is None
            else "{0:.1f} deg".format(azimuth)))
        if stats["nodata_fraction"] > 0.0:
            lines.append("  Celle senza dato: {0:.2%}".format(
                stats["nodata_fraction"]))
        lines.extend("  " + text for text in self.warnings)
        return lines
