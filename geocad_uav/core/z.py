"""
Raster elevation sampling: bilinear DEM/DTM access, slope and aspect.

:class:`TerrainModel` holds an elevation grid resampled into the *working*
(projected, metric) CRS. Its numerics are plain numpy, so the module can be
exercised without QGIS; only :meth:`TerrainModel.from_layer` touches
QGIS/GDAL, and it imports them lazily.

Two rules are enforced throughout, because violating either is a flight-safety
problem rather than a cosmetic one:

1. **No silent extrapolation.** A query outside the raster, or over a no-data
   cell (or one of its four bilinear neighbours), returns NaN. Callers must
   handle NaN explicitly; nothing here invents an elevation.
2. **Nearest-neighbour is not good enough.** ``QgsRasterDataProvider.sample()``
   returns the containing cell value, which stair-steps a flight profile at DEM
   cell size. Sampling here is bilinear.

Vertical datum is never transformed. Whatever the DEM stores (orthometric or
ellipsoidal) is what comes out; declaring and carrying that through to export
is the caller job (see ``core.models.VerticalDatum``).
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

#: Hard cap on the resampled grid, so a country-wide DEM cannot exhaust RAM.
DEFAULT_MAX_PIXELS = 60_000_000


class TerrainError(RuntimeError):
    """Raised when the elevation model cannot support the requested mission."""


# --------------------------------------------------------------------------
# Elevation grid
# --------------------------------------------------------------------------

class TerrainModel:
    """An elevation grid in the working CRS, sampled bilinearly.

    ``transform`` is a GDAL-style geotransform ``(x0, dx, 0, y0, 0, -dy)`` where
    ``(x0, y0)`` is the *upper-left corner* of the upper-left pixel and dy > 0
    (north-up grid). Elevations are float; no-data is stored as NaN.
    """

    def __init__(self, elevation, transform, crs_authid: str = "",
                 source: str = "", is_surface_model: bool = False,
                 vertical_datum: str = "unknown"):
        arr = np.asarray(elevation, dtype=np.float64)
        if arr.ndim != 2:
            raise TerrainError("elevation grid must be 2-D, got shape "
                               "{0!r}".format(arr.shape))
        self.z = arr
        self.gt = tuple(float(v) for v in transform)
        if self.gt[1] <= 0 or self.gt[5] >= 0:
            raise TerrainError(
                "expected a north-up geotransform with dx > 0 and dy < 0, "
                "got {0!r}".format(self.gt))
        self.crs_authid = crs_authid
        self.source = source
        #: True for a DSM (canopy/roofs included), False for a bare-earth DTM.
        self.is_surface_model = bool(is_surface_model)
        self.vertical_datum = vertical_datum

    # -- grid metadata -----------------------------------------------------

    @property
    def rows(self) -> int:
        return self.z.shape[0]

    @property
    def cols(self) -> int:
        return self.z.shape[1]

    @property
    def cellsize_x(self) -> float:
        return self.gt[1]

    @property
    def cellsize_y(self) -> float:
        return -self.gt[5]

    @property
    def cellsize(self) -> float:
        """Representative cell size [m] -- the smaller axis, conservatively."""
        return min(self.cellsize_x, self.cellsize_y)

    @property
    def extent(self):
        """(xmin, ymin, xmax, ymax) of the grid in the working CRS."""
        x0, dx, _, y0, _, dy = self.gt
        return (x0, y0 + dy * self.rows, x0 + dx * self.cols, y0)

    @property
    def nodata_fraction(self) -> float:
        if self.z.size == 0:
            return 1.0
        return float(np.count_nonzero(~np.isfinite(self.z))) / self.z.size

    def __repr__(self) -> str:
        return ("TerrainModel({0}x{1} px, cell {2:.2f} m, {3}, "
                "nodata {4:.2%})".format(self.cols, self.rows, self.cellsize,
                                         self.crs_authid, self.nodata_fraction))

    # -- sampling ----------------------------------------------------------

    def sample(self, x, y):
        """Bilinear elevation at world coordinates. NaN outside / on no-data.

        Accepts scalars or arrays; returns an array of the broadcast shape.
        If *any* of the four surrounding cells is no-data the result is NaN:
        a one-cell no-data halo is the safe direction to err in, because the
        alternative is flying a height derived from a hole in the DEM.
        """
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        x0, dx, _, y0, _, dy = self.gt

        # Continuous coordinates in "cell centre" space.
        col = (x - x0) / dx - 0.5
        row = (y0 - y) / (-dy) - 0.5

        # Ray casting can hand us NaN coordinates for rays that left the grid.
        # Casting NaN to int64 is undefined and raises a RuntimeWarning, so the
        # non-finite entries are parked on cell 0 and masked out below instead.
        finite = np.isfinite(col) & np.isfinite(row)
        col = np.where(finite, col, 0.0)
        row = np.where(finite, row, 0.0)

        c0 = np.floor(col).astype(np.int64)
        r0 = np.floor(row).astype(np.int64)
        fc = col - c0
        fr = row - r0

        inside = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < self.cols) & (r0 + 1 < self.rows)
        # Edge half-cell: clamp so the outermost half pixel is still usable.
        c0c = np.clip(c0, 0, max(self.cols - 2, 0))
        r0c = np.clip(r0, 0, max(self.rows - 2, 0))
        edge = ((col >= -0.5) & (col <= self.cols - 0.5) &
                (row >= -0.5) & (row <= self.rows - 0.5))
        usable = inside | edge
        fc = np.clip(fc + (c0 - c0c), 0.0, 1.0)
        fr = np.clip(fr + (r0 - r0c), 0.0, 1.0)

        z00 = self.z[r0c, c0c]
        z01 = self.z[r0c, c0c + 1] if self.cols > 1 else z00
        z10 = self.z[r0c + 1, c0c] if self.rows > 1 else z00
        z11 = (self.z[r0c + 1, c0c + 1]
               if (self.rows > 1 and self.cols > 1) else z00)

        top = z00 * (1.0 - fc) + z01 * fc
        bot = z10 * (1.0 - fc) + z11 * fc
        out = top * (1.0 - fr) + bot * fr

        # Propagate no-data: any invalid contributor poisons the result.
        valid = (np.isfinite(z00) & np.isfinite(z01) &
                 np.isfinite(z10) & np.isfinite(z11) & usable & finite)
        return np.where(valid, out, np.nan)

    def stats_in_mask(self, mask=None):
        """(z_min, z_max, z_mean, nodata_fraction) over the grid or a mask."""
        z = self.z if mask is None else self.z[mask]
        finite = z[np.isfinite(z)]
        if finite.size == 0:
            return (math.nan, math.nan, math.nan, 1.0)
        nod = 1.0 - finite.size / float(z.size)
        return (float(finite.min()), float(finite.max()),
                float(finite.mean()), float(nod))

    def dominant_slope_azimuth(self, mask=None):
        """Compass azimuth [0, 180) of the dominant slope (line of max slope).

        Averaging gradient *vectors* over an area cancels out on a symmetric
        landform (a bowl averages to zero). Instead this takes the principal
        eigenvector of the gradient structure tensor, which yields an
        *orientation* rather than a direction and is stable on real terrain.

        Returns ``None`` if the surface is too flat for the direction to mean
        anything.
        """
        if self.rows < 3 or self.cols < 3:
            return None
        z = self.z
        # d/drow and d/dcol; y decreases with row, so dz/dy = -dz/drow / dy.
        gr, gc = np.gradient(np.where(np.isfinite(z), z, np.nan))
        gx = gc / self.cellsize_x                 # dz/dEast
        gy = -gr / self.cellsize_y                # dz/dNorth
        ok = np.isfinite(gx) & np.isfinite(gy)
        if mask is not None:
            ok &= mask
        if np.count_nonzero(ok) < 10:
            return None
        gx, gy = gx[ok], gy[ok]

        sxx = float(np.sum(gx * gx))
        syy = float(np.sum(gy * gy))
        sxy = float(np.sum(gx * gy))
        trace = sxx + syy
        if trace <= 1e-12:
            return None
        # Principal eigenvector of [[sxx, sxy], [sxy, syy]].
        theta = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
        ex, ey = math.cos(theta), math.sin(theta)   # (East, North) components
        return math.degrees(math.atan2(ex, ey)) % 180.0

    # -- construction from QGIS -------------------------------------------

    @classmethod
    def from_layer(cls, raster_layer, work_crs, bbox, margin_m: float = 0.0,
                   band: int = 1, max_pixels: int = DEFAULT_MAX_PIXELS,
                   is_surface_model: bool = False,
                   vertical_datum: str = "unknown",
                   resample: str = "bilinear"):
        """Warp a ``QgsRasterLayer`` window into the working CRS.

        ``bbox`` is ``(xmin, ymin, xmax, ymax)`` in ``work_crs``; ``margin_m``
        expands it (use the photogrammetric buffer, so footprints near the AOI
        edge still land on real data).

        The grid resolution follows the source resolution expressed in the
        working CRS, coarsened only if the window would exceed ``max_pixels``
        (which is reported as a warning, never applied silently).

        Returns ``(model, warnings)``.
        """
        from osgeo import gdal, osr                     # noqa: PLC0415
        from qgis.core import (QgsCoordinateReferenceSystem,  # noqa: PLC0415
                               QgsCoordinateTransform,
                               QgsProject, QgsRectangle)

        gdal.UseExceptions()
        osr.UseExceptions()
        warnings: "list[str]" = []

        source = raster_layer.source()
        # Strip QGIS provider decorations that GDAL will not understand.
        if "|" in source:
            source = source.split("|", 1)[0]
        try:
            src_ds = gdal.Open(source, gdal.GA_ReadOnly)
        except Exception as exc:                        # noqa: BLE001
            raise TerrainError(
                "Cannot open the elevation raster with GDAL: {0}\n"
                "Source: {1}\n"
                "Save the DEM/DTM to a GeoTIFF and load that instead."
                .format(exc, source)) from exc
        if src_ds is None:
            raise TerrainError(
                "GDAL returned no dataset for {0!r}. Save the DEM/DTM to a "
                "GeoTIFF and load that instead.".format(source))
        if band < 1 or band > src_ds.RasterCount:
            raise TerrainError(
                "Band {0} does not exist (raster has {1} band(s)).".format(
                    band, src_ds.RasterCount))

        xmin, ymin, xmax, ymax = bbox
        xmin -= margin_m
        ymin -= margin_m
        xmax += margin_m
        ymax += margin_m
        if not (xmax > xmin and ymax > ymin):
            raise TerrainError("Requested elevation window is empty: "
                               "{0!r}".format(bbox))

        # Source resolution expressed in the working CRS.
        src_gt = src_ds.GetGeoTransform()
        src_crs = QgsCoordinateReferenceSystem(raster_layer.crs())
        res_work = _source_resolution_in_crs(src_ds, src_gt, src_crs, work_crs)
        if not (res_work > 0 and math.isfinite(res_work)):
            raise TerrainError(
                "Could not determine the DEM resolution in the working CRS.")

        n_px = ((xmax - xmin) / res_work) * ((ymax - ymin) / res_work)
        if n_px > max_pixels:
            factor = math.sqrt(n_px / float(max_pixels))
            warnings.append(
                "DEM window would be {0:.0f} Mpx at native resolution "
                "({1:.2f} m); resampled to {2:.2f} m to stay under the "
                "{3:.0f} Mpx memory cap. Terrain detail is reduced -- clip the "
                "DEM to the AOI for full resolution.".format(
                    n_px / 1e6, res_work, res_work * factor, max_pixels / 1e6))
            res_work *= factor

        nodata_out = -3.4028234663852886e+38
        warp_opts = gdal.WarpOptions(
            format="MEM",
            dstSRS=work_crs.toWkt(),
            outputBounds=(xmin, ymin, xmax, ymax),
            xRes=res_work, yRes=res_work,
            resampleAlg=resample,
            dstNodata=nodata_out,
            outputType=gdal.GDT_Float64,
            srcBands=[band], dstBands=[1],
        )
        try:
            dst = gdal.Warp("", src_ds, options=warp_opts)
        except Exception as exc:                        # noqa: BLE001
            raise TerrainError(
                "Reprojecting the DEM into the working CRS failed: {0}".format(
                    exc)) from exc
        if dst is None:
            raise TerrainError("Reprojecting the DEM into the working CRS "
                               "produced no data.")

        arr = dst.GetRasterBand(1).ReadAsArray().astype(np.float64)
        gt = dst.GetGeoTransform()
        arr = np.where(arr <= nodata_out * 0.999, np.nan, arr)
        src_nodata = src_ds.GetRasterBand(band).GetNoDataValue()
        if src_nodata is not None and math.isfinite(src_nodata):
            arr = np.where(np.isclose(arr, src_nodata), np.nan, arr)
        dst = None
        src_ds = None

        model = cls(arr, gt, crs_authid=work_crs.authid(), source=source,
                    is_surface_model=is_surface_model,
                    vertical_datum=vertical_datum)
        if model.nodata_fraction > 0.0:
            warnings.append(
                "Elevation model has {0:.2%} no-data over the planning window. "
                "The route is NOT extrapolated across holes; affected "
                "waypoints are flagged.".format(model.nodata_fraction))
        return model, warnings


def _source_resolution_in_crs(src_ds, src_gt, src_crs, work_crs):
    """Approximate one source pixel's size, measured in the working CRS."""
    from qgis.core import (QgsCoordinateTransform, QgsProject,  # noqa: PLC0415
                           QgsRectangle)
    if src_crs == work_crs:
        return min(abs(src_gt[1]), abs(src_gt[5]))
    x0, dx, _, y0, _, dy = src_gt
    x1 = x0 + dx * src_ds.RasterXSize
    y1 = y0 + dy * src_ds.RasterYSize
    tr = QgsCoordinateTransform(src_crs, work_crs, QgsProject.instance())
    rect = tr.transformBoundingBox(
        QgsRectangle(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    return min(rect.width() / src_ds.RasterXSize,
               rect.height() / src_ds.RasterYSize)


def raycast_to_terrain(terrain: TerrainModel, origins, directions,
                       t_max: float, step_m: Optional[float] = None,
                       refine_iters: int = 20):
    """First intersection of each ray with the terrain surface.

    Vectorised march-then-bisect:

    1. March along every ray together in fixed steps, evaluating
       ``f(t) = z_ray(t) - z_terrain(t)``.
    2. Take the first step where ``f`` changes from positive (above ground) to
       negative (below ground) -- taking the *first* crossing is what makes a
       ridge correctly occlude the valley behind it on a DSM.
    3. Bisect inside that bracket to converge on the surface.

    Rays that never cross (they leave the grid, or run over no-data) come back
    as NaN. ``origins`` and ``directions`` are (N, 3); returns (N, 3) points.
    """
    origins = np.asarray(origins, dtype=float).reshape(-1, 3)
    dirs = np.asarray(directions, dtype=float).reshape(-1, 3)
    norms = np.linalg.norm(dirs, axis=1, keepdims=True)
    dirs = dirs / np.where(norms > 0, norms, 1.0)

    if step_m is None:
        step_m = max(terrain.cellsize * 0.5, 0.05)
    n_steps = int(math.ceil(t_max / step_m)) + 1
    n = origins.shape[0]

    t_lo = np.full(n, np.nan)
    t_hi = np.full(n, np.nan)
    found = np.zeros(n, dtype=bool)
    prev_t = np.zeros(n)
    prev_f = np.full(n, np.nan)

    for i in range(n_steps + 1):
        t = min(i * step_m, t_max)
        p = origins + dirs * t
        zt = terrain.sample(p[:, 0], p[:, 1])
        f = p[:, 2] - zt
        cross = (~found) & np.isfinite(prev_f) & np.isfinite(f) & \
                (prev_f > 0) & (f <= 0)
        if cross.any():
            t_lo[cross] = prev_t[cross]
            t_hi[cross] = t
            found |= cross
        prev_t = np.full(n, t)
        prev_f = f
        if found.all() or t >= t_max:
            break

    lo = np.where(found, t_lo, np.nan)
    hi = np.where(found, t_hi, np.nan)
    for _ in range(refine_iters):
        mid = 0.5 * (lo + hi)
        p = origins + dirs * mid[:, None]
        zt = terrain.sample(p[:, 0], p[:, 1])
        f = p[:, 2] - zt
        above = np.isfinite(f) & (f > 0)
        lo = np.where(above, mid, lo)
        hi = np.where(above, hi, mid)

    t_hit = 0.5 * (lo + hi)
    hit = origins + dirs * t_hit[:, None]
    hit[~found] = np.nan
    return hit


# --------------------------------------------------------------------------
# Slope and aspect (Horn 1981, 3x3)
# --------------------------------------------------------------------------

def slope_aspect(model):
    """Slope [deg] and aspect [compass deg] grids, by the Horn 3x3 operator.

    Horn is the kernel GDAL and ArcGIS use, so results are directly comparable
    with ``gdaldem slope`` / ``gdaldem aspect``. Cells whose 3x3 window touches
    no-data come back as NaN rather than being computed from a partial window.

    Aspect is the compass azimuth of the *downslope* direction. Flat cells get
    NaN aspect, because the direction of a flat surface is meaningless and
    returning 0 would be read as "due north".

    Returns ``(slope_deg, aspect_deg)``, both the shape of the grid.
    """
    z = model.z
    if z.shape[0] < 3 or z.shape[1] < 3:
        nan = np.full(z.shape, np.nan)
        return nan, nan.copy()

    p = np.pad(z, 1, mode="edge")
    a, b, c = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]       # north row
    d, f = p[1:-1, :-2], p[1:-1, 2:]                      # middle row
    g, h, i = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]          # south row

    dzdx = ((c + 2.0 * f + i) - (a + 2.0 * d + g)) / (8.0 * model.cellsize_x)
    # Row index grows southward, so north-minus-south is dz/dNorth.
    dzdy = ((a + 2.0 * b + c) - (g + 2.0 * h + i)) / (8.0 * model.cellsize_y)

    rise = np.hypot(dzdx, dzdy)
    slope = np.degrees(np.arctan(rise))

    # The gradient points uphill; negate it for the downslope azimuth.
    aspect = np.degrees(np.arctan2(-dzdx, -dzdy)) % 360.0
    aspect = np.where(rise < 1e-9, np.nan, aspect)

    valid = np.isfinite(np.stack([a, b, c, d, f, g, h, i])).all(axis=0)
    slope = np.where(valid, slope, np.nan)
    aspect = np.where(valid, aspect, np.nan)
    return slope, aspect


def sample_slope_aspect(model, x, y):
    """Slope and aspect at world coordinates, taken from the nearest cell.

    Slope and aspect are derivatives, so bilinear resampling of the derivative
    grid would smooth them beyond what Horn already does; the nearest cell is
    the honest answer.
    """
    slope, aspect = slope_aspect(model)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    x0, dx, _, y0, _, dy = model.gt
    col = np.floor((x - x0) / dx).astype(np.int64)
    row = np.floor((y0 - y) / (-dy)).astype(np.int64)
    ok = (col >= 0) & (row >= 0) & (col < model.cols) & (row < model.rows)
    colc = np.clip(col, 0, model.cols - 1)
    rowc = np.clip(row, 0, model.rows - 1)
    return (np.where(ok, slope[rowc, colc], np.nan),
            np.where(ok, aspect[rowc, colc], np.nan))
