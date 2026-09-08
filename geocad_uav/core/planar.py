"""
Planar geometry helpers in a projected metric CRS.

Strip frames and polyline utilities, shared by uav/ and forest/.

Angle convention used consistently across the whole plugin:

    azimuth = compass degrees, CLOCKWISE from North (grid north of the working
              CRS), 0 = N, 90 = E, 180 = S, 270 = W.

That is also what ``QgsGeometry.orientedMinimumBoundingBox()`` returns for the
longer box side, and what DJI/ArduPilot expect for a heading, so no conversion
is needed anywhere in the chain.

Everything here operates on plain numpy arrays of projected coordinates in
metres. Nothing in this module is valid on geographic (degree) coordinates.

PURE PYTHON + NUMPY: no QGIS import, so it is testable standalone.
"""

from __future__ import annotations

import math

import numpy as np


def normalize_azimuth(azimuth_deg: float, period: float = 360.0) -> float:
    """Wrap an azimuth into [0, period).

    ``period=180`` collapses a direction and its reverse, which is what strip
    *orientation* needs (flying a line north or south is the same line).
    """
    return float(azimuth_deg) % period


def azimuth_of(dx_east: float, dy_north: float) -> float:
    """Compass azimuth [0, 360) of the vector (dEast, dNorth)."""
    return math.degrees(math.atan2(dx_east, dy_north)) % 360.0


def along_track_unit(azimuth_deg: float):
    """Unit vector (East, North) pointing along the flight direction."""
    a = math.radians(azimuth_deg)
    return math.sin(a), math.cos(a)


def across_track_unit(azimuth_deg: float):
    """Unit vector (East, North) 90 deg clockwise from the flight direction.

    This is the aircraft's right-hand side, and the direction in which strips
    are stepped by D_side.
    """
    a = math.radians(azimuth_deg)
    return math.cos(a), -math.sin(a)


class StripFrame:
    """Rotated cartesian frame aligned with the flight direction.

    ``s`` runs along track (the direction the aircraft flies), ``t`` runs across
    track (positive to the aircraft's right). Strips are lines of constant
    ``t`` spaced by D_side; shots along a strip are spaced by D_front in ``s``.

    Working in this frame turns "generate parallel lines at an arbitrary angle,
    clipped to an arbitrary polygon" into "generate horizontal lines", which
    removes a whole class of trigonometry bugs from the router.
    """

    __slots__ = ("azimuth_deg", "origin_x", "origin_y", "_ux", "_uy", "_vx", "_vy")

    def __init__(self, azimuth_deg: float, origin_x: float = 0.0,
                 origin_y: float = 0.0):
        self.azimuth_deg = normalize_azimuth(azimuth_deg)
        self.origin_x = float(origin_x)
        self.origin_y = float(origin_y)
        self._ux, self._uy = along_track_unit(self.azimuth_deg)
        self._vx, self._vy = across_track_unit(self.azimuth_deg)

    def __repr__(self) -> str:
        return "StripFrame(azimuth={0:.3f}, origin=({1:.3f}, {2:.3f}))".format(
            self.azimuth_deg, self.origin_x, self.origin_y)

    # -- transforms --------------------------------------------------------

    def to_frame(self, x, y):
        """World (E, N) -> frame (s, t). Accepts scalars or numpy arrays."""
        dx = np.asarray(x, dtype=float) - self.origin_x
        dy = np.asarray(y, dtype=float) - self.origin_y
        s = dx * self._ux + dy * self._uy
        t = dx * self._vx + dy * self._vy
        return s, t

    def to_world(self, s, t):
        """Frame (s, t) -> world (E, N). Accepts scalars or numpy arrays."""
        s = np.asarray(s, dtype=float)
        t = np.asarray(t, dtype=float)
        x = self.origin_x + s * self._ux + t * self._vx
        y = self.origin_y + s * self._uy + t * self._vy
        return x, y

    def bounds_in_frame(self, xs, ys):
        """(s_min, s_max, t_min, t_max) of a point cloud, in frame axes."""
        s, t = self.to_frame(np.asarray(xs), np.asarray(ys))
        return float(s.min()), float(s.max()), float(t.min()), float(t.max())


# --------------------------------------------------------------------------
# Polyline utilities
# --------------------------------------------------------------------------

def polyline_length(points) -> float:
    """Total 2D length of an (N, 2) polyline."""
    pts = np.asarray(points, dtype=float)
    if pts.shape[0] < 2:
        return 0.0
    return float(np.hypot(*np.diff(pts[:, :2], axis=0).T).sum())


def cumulative_distance(points):
    """Chainage (N,) along an (N, 2+) polyline, starting at 0."""
    pts = np.asarray(points, dtype=float)
    if pts.shape[0] == 0:
        return np.zeros(0)
    if pts.shape[0] == 1:
        return np.zeros(1)
    seg = np.hypot(*np.diff(pts[:, :2], axis=0).T)
    return np.concatenate([[0.0], np.cumsum(seg)])


def resample_polyline(points, step_m: float, keep_vertices: bool = True,
                      extra_chainages=None):
    """Resample an (N, 2) polyline at a fixed chainage step.

    ``keep_vertices`` also injects the original vertices, so a corner is never
    cut off by the sampling grid. ``extra_chainages`` injects further exact
    positions -- photo stations use it, so an exposure lands on a real sample
    instead of being snapped to the nearest one. Returns an (M, 2) array; the
    first and last points of the input are always present.
    """
    pts = np.asarray(points, dtype=float)[:, :2]
    if pts.shape[0] < 2:
        return pts.copy()
    if step_m <= 0:
        raise ValueError("step_m must be > 0, got {0!r}".format(step_m))

    chain = cumulative_distance(pts)
    total = chain[-1]
    if total <= 0:
        return pts[:1].copy()

    n_steps = int(math.floor(total / step_m))
    targets = np.arange(0, n_steps + 1, dtype=float) * step_m
    if targets[-1] < total - 1e-9:
        targets = np.concatenate([targets, [total]])
    if keep_vertices:
        targets = np.concatenate([targets, chain])
    if extra_chainages is not None and len(extra_chainages):
        extra = np.asarray(extra_chainages, dtype=float)
        targets = np.concatenate([targets, extra[(extra >= 0.0) & (extra <= total)]])
    targets = np.unique(targets)

    xs = np.interp(targets, chain, pts[:, 0])
    ys = np.interp(targets, chain, pts[:, 1])
    return np.column_stack([xs, ys])


def points_along(points, spacing_m: float, offset_m: float = 0.0):
    """Points at fixed chainage intervals along an (N, 2) polyline.

    Used to place photo stations along a strip. ``offset_m`` shifts the first
    station, which is how the along-track lead-in for edge coverage is applied.
    Returns ``(coords (M, 2), chainage (M,))``.
    """
    pts = np.asarray(points, dtype=float)[:, :2]
    if pts.shape[0] < 2 or spacing_m <= 0:
        return pts[:0].copy(), np.zeros(0)
    chain = cumulative_distance(pts)
    total = chain[-1]
    if total <= 0:
        return pts[:0].copy(), np.zeros(0)

    first = offset_m
    if first > total:
        return pts[:0].copy(), np.zeros(0)
    targets = np.arange(first, total + 1e-9, spacing_m)
    if targets.size == 0:
        return pts[:0].copy(), np.zeros(0)
    xs = np.interp(targets, chain, pts[:, 0])
    ys = np.interp(targets, chain, pts[:, 1])
    return np.column_stack([xs, ys]), targets


def headings_along(points):
    """Per-vertex compass heading [deg] of an (N, 2) polyline.

    The heading at a vertex is the bearing of the segment that *leaves* it; the
    last vertex inherits the bearing of the segment that arrives.
    """
    pts = np.asarray(points, dtype=float)[:, :2]
    if pts.shape[0] < 2:
        return np.zeros(pts.shape[0])
    d = np.diff(pts, axis=0)
    head = (np.degrees(np.arctan2(d[:, 0], d[:, 1]))) % 360.0
    return np.concatenate([head, head[-1:]])


def rectangle_corners(cx: float, cy: float, half_across: float,
                      half_along: float, azimuth_deg: float):
    """Corners of a footprint rectangle centred at (cx, cy).

    The rectangle is axis-aligned with the flight direction: ``half_along``
    extends fore/aft, ``half_across`` extends left/right. Returned closed
    (5 points), counter-clockwise in map orientation.
    """
    ux, uy = along_track_unit(azimuth_deg)
    vx, vy = across_track_unit(azimuth_deg)
    signs = ((+1, -1), (+1, +1), (-1, +1), (-1, -1))
    corners = [(cx + sa * half_along * ux + sc * half_across * vx,
                cy + sa * half_along * uy + sc * half_across * vy)
               for sa, sc in signs]
    corners.append(corners[0])
    return np.asarray(corners, dtype=float)


def densify_ring(ring, samples_per_edge: int = 1):
    """Insert ``samples_per_edge`` extra points on every edge of a closed ring.

    A footprint draped over broken terrain is not a quadrilateral; sampling
    only the four corners can miss a ridge crossing the middle of an edge.
    """
    ring = np.asarray(ring, dtype=float)
    if samples_per_edge < 1 or ring.shape[0] < 2:
        return ring.copy()
    out = []
    for i in range(ring.shape[0] - 1):
        a, b = ring[i], ring[i + 1]
        for k in range(samples_per_edge + 1):
            out.append(a + (b - a) * (k / (samples_per_edge + 1.0)))
    out.append(ring[-1])
    return np.asarray(out, dtype=float)
