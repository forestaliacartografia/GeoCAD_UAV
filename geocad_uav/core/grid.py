"""
Grid Designer: regular point and row patterns, oriented and clipped.

PURE PYTHON + NUMPY. Generation happens in a :class:`~.planar.StripFrame`
aligned with the requested orientation, so an oriented grid is built as an
axis-aligned one and rotated exactly once -- there is no per-point trigonometry
to drift.

Clipping against a real polygon needs GEOS, so it is kept out of here: this
module produces candidate positions plus their row/column indices, and
``forest.planting`` / the Processing algorithms apply the spatial predicate.
That split is what lets the lattice arithmetic be tested without QGIS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .constants import GEOM_EPS_M
from .errors import InvalidInputError
from .planar import StripFrame

# Lattice patterns.
PATTERN_RECT = "rect"              # aligned rows and columns
PATTERN_SQUARE = "square"          # rect with dy forced to dx
PATTERN_QUINCUNX = "quincunx"      # alternate rows offset by dx/2 (triangular)
PATTERN_HEX = "hex"                # quincunx with dy = dx * sqrt(3)/2
PATTERN_ROWS = "rows"              # continuous rows, points only along them

ALL_PATTERNS = (PATTERN_RECT, PATTERN_SQUARE, PATTERN_QUINCUNX,
                PATTERN_HEX, PATTERN_ROWS)

PATTERN_LABELS = {
    PATTERN_RECT: "Rettangolare",
    PATTERN_SQUARE: "Quadrato",
    PATTERN_QUINCUNX: "Quinconce (triangolare)",
    PATTERN_HEX: "Esagonale",
    PATTERN_ROWS: "A file",
}


@dataclass
class GridSpec:
    """Everything that defines a lattice. Serialisable into a ParametricRecord."""

    spacing_x: float                    # across the rows (plant to plant)
    spacing_y: float                    # between rows
    azimuth_deg: float = 0.0            # bearing of the rows
    pattern: str = PATTERN_RECT
    origin: Optional[tuple] = None      # anchor point; default = AOI corner
    offset_x: float = 0.0               # shift of the first column
    offset_y: float = 0.0               # shift of the first row
    margin_m: float = 0.0               # inward setback from the AOI edge
    serpentine: bool = False            # number rows alternately (walking order)

    def __post_init__(self) -> None:
        if self.pattern not in ALL_PATTERNS:
            raise InvalidInputError(
                "unknown grid pattern {0!r}".format(self.pattern),
                user_message="Schema di griglia non riconosciuto: '{0}'.".format(
                    self.pattern),
                hint="Usa: {0}.".format(", ".join(ALL_PATTERNS)))
        for name, italian in (("spacing_x", "La distanza fra le piante"),
                              ("spacing_y", "La distanza fra le file")):
            value = getattr(self, name)
            if not (isinstance(value, (int, float)) and math.isfinite(value)
                    and value > 0.0):
                raise InvalidInputError(
                    "{0} must be > 0, got {1!r}".format(name, value),
                    user_message="{0} deve essere maggiore di zero.".format(italian))
        if self.margin_m < 0:
            raise InvalidInputError(
                "margin_m must be >= 0, got {0!r}".format(self.margin_m),
                user_message="Il margine dal bordo non puo' essere negativo.")

    @property
    def effective_spacing(self):
        """(dx, dy) after the pattern has had its say."""
        if self.pattern == PATTERN_SQUARE:
            return self.spacing_x, self.spacing_x
        if self.pattern == PATTERN_HEX:
            return self.spacing_x, self.spacing_x * math.sqrt(3.0) / 2.0
        return self.spacing_x, self.spacing_y

    @property
    def row_offset_alternates(self) -> bool:
        return self.pattern in (PATTERN_QUINCUNX, PATTERN_HEX)

    def area_per_point_m2(self) -> float:
        """Ground area each lattice point represents, for density figures."""
        dx, dy = self.effective_spacing
        return dx * dy


@dataclass
class GridResult:
    """Candidate positions with their lattice indices."""

    xy: np.ndarray            # (N, 2) world coordinates
    row: np.ndarray           # (N,) row index, 0-based
    col: np.ndarray           # (N,) column index within the row, 0-based
    frame: StripFrame
    spec: GridSpec
    n_rows: int = 0
    n_cols: int = 0

    def __len__(self) -> int:
        return int(self.xy.shape[0])

    def row_lines(self) -> "list[np.ndarray]":
        """One polyline per row, through that row's points in order."""
        lines = []
        for r in range(self.n_rows):
            sel = self.row == r
            if np.count_nonzero(sel) < 2:
                continue
            pts = self.xy[sel]
            order = np.argsort(self.col[sel])
            lines.append(pts[order])
        return lines


def _counts(extent: float, spacing: float) -> int:
    """Number of lattice positions spanning ``extent`` inclusive of both ends.

    A 100 m extent at 5 m spacing gives 21 positions, not 20: both the 0 m and
    the 100 m ends carry a point. The epsilon guards the case where the extent
    is an exact multiple and floating point would otherwise drop the far edge.
    """
    if extent < -GEOM_EPS_M:
        return 0
    return int(math.floor(extent / spacing + 1e-9)) + 1


def generate_grid(bounds, spec: GridSpec,
                  origin_xy: Optional[tuple] = None) -> GridResult:
    """Build the lattice covering ``bounds`` = ``(xmin, ymin, xmax, ymax)``.

    The bounds are the AOI envelope *in world coordinates*; the lattice is laid
    out in the rotated frame and always covers the envelope completely, so
    clipping afterwards cannot leave an uncovered sliver.
    """
    xmin, ymin, xmax, ymax = (float(v) for v in bounds)
    if not (xmax > xmin and ymax > ymin):
        raise InvalidInputError(
            "empty bounds {0!r}".format(bounds),
            user_message="L'area di riferimento e' vuota.")

    dx, dy = spec.effective_spacing
    anchor = origin_xy if origin_xy is not None else spec.origin
    if anchor is None:
        anchor = (xmin, ymin)
    frame = StripFrame(spec.azimuth_deg, float(anchor[0]), float(anchor[1]))

    # Envelope corners in frame coordinates; the rotated lattice must cover the
    # whole rotated bounding box of the envelope.
    corners = np.array([[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]])
    s, t = frame.to_frame(corners[:, 0], corners[:, 1])
    s_min, s_max = float(s.min()), float(s.max())
    t_min, t_max = float(t.min()), float(t.max())

    # Snap the start back to a lattice multiple so the pattern is stable when
    # the AOI is edited: the grid must not shift because a corner moved.
    s_start = math.floor((s_min - spec.offset_y) / dy) * dy + spec.offset_y
    t_start = math.floor((t_min - spec.offset_x) / dx) * dx + spec.offset_x

    n_rows = _counts(s_max - s_start, dy)
    n_cols = _counts(t_max - t_start, dx)
    if n_rows <= 0 or n_cols <= 0:
        empty = np.zeros((0, 2))
        return GridResult(empty, np.zeros(0, dtype=int), np.zeros(0, dtype=int),
                          frame, spec, 0, 0)

    # One extra column absorbs the half-step shift of the offset rows.
    if spec.row_offset_alternates:
        n_cols += 1

    rows = np.arange(n_rows)
    cols = np.arange(n_cols)
    row_grid, col_grid = np.meshgrid(rows, cols, indexing="ij")

    s_vals = s_start + row_grid * dy
    t_vals = t_start + col_grid * dx
    if spec.row_offset_alternates:
        t_vals = t_vals - np.where(row_grid % 2 == 1, dx * 0.5, 0.0)

    x, y = frame.to_world(s_vals.ravel(), t_vals.ravel())
    xy = np.column_stack([np.asarray(x).ravel(), np.asarray(y).ravel()])

    row_flat = row_grid.ravel()
    col_flat = col_grid.ravel()
    if spec.serpentine:
        # Walking order: even rows left to right, odd rows right to left.
        col_flat = np.where(row_flat % 2 == 1, n_cols - 1 - col_flat, col_flat)

    return GridResult(xy=xy, row=row_flat.astype(int), col=col_flat.astype(int),
                      frame=frame, spec=spec, n_rows=n_rows, n_cols=n_cols)


def filter_result(result: GridResult, keep_mask) -> GridResult:
    """Subset a :class:`GridResult`, preserving lattice indices."""
    mask = np.asarray(keep_mask, dtype=bool)
    if mask.shape[0] != len(result):
        raise InvalidInputError(
            "mask length {0} does not match {1} points".format(
                mask.shape[0], len(result)),
            user_message="Filtro non applicabile: dimensioni incoerenti.")
    return GridResult(xy=result.xy[mask], row=result.row[mask],
                      col=result.col[mask], frame=result.frame,
                      spec=result.spec, n_rows=result.n_rows,
                      n_cols=result.n_cols)


def renumber(result: GridResult) -> GridResult:
    """Compact row and column indices after clipping.

    Clipping a concave AOI leaves gaps in the numbering; a field crew wants
    rows numbered 1..n over the rows that actually exist, and plants numbered
    consecutively along each surviving row.
    """
    if len(result) == 0:
        return result
    order = np.lexsort((result.col, result.row))
    rows_sorted = result.row[order]
    unique_rows, row_new = np.unique(rows_sorted, return_inverse=True)

    col_new = np.zeros(len(result), dtype=int)
    for value in range(unique_rows.size):
        sel = row_new == value
        col_new[sel] = np.arange(np.count_nonzero(sel))

    inverse = np.empty_like(order)
    inverse[order] = np.arange(order.size)
    return GridResult(xy=result.xy, row=row_new[inverse], col=col_new[inverse],
                      frame=result.frame, spec=result.spec,
                      n_rows=int(unique_rows.size),
                      n_cols=int(col_new.max()) + 1 if col_new.size else 0)


# --------------------------------------------------------------------------
# Orientation helpers
# --------------------------------------------------------------------------

def azimuth_from_two_points(p1, p2) -> float:
    """Bearing of the line p1 -> p2. Backs 'pick direction from map'."""
    a = np.asarray(p1, dtype=float).reshape(-1)[:2]
    b = np.asarray(p2, dtype=float).reshape(-1)[:2]
    d = b - a
    if float(np.hypot(*d)) < GEOM_EPS_M:
        raise InvalidInputError(
            "the two picked points coincide",
            user_message="I due punti indicati coincidono.",
            hint="Indica due punti distinti per definire la direzione.")
    return math.degrees(math.atan2(float(d[0]), float(d[1]))) % 360.0


def azimuth_of_longest_edge(ring) -> float:
    """Bearing of the longest edge of a ring -- 'align rows to a polygon side'."""
    pts = np.asarray(ring, dtype=float)[:, :2]
    if pts.shape[0] < 2:
        raise InvalidInputError(
            "ring has too few vertices",
            user_message="Il poligono non ha lati utilizzabili.")
    d = np.diff(pts, axis=0)
    lengths = np.hypot(d[:, 0], d[:, 1])
    k = int(np.argmax(lengths))
    if lengths[k] < GEOM_EPS_M:
        raise InvalidInputError(
            "all edges are degenerate",
            user_message="Il poligono non ha lati di lunghezza utile.")
    return math.degrees(math.atan2(float(d[k, 0]), float(d[k, 1]))) % 360.0
