"""
Play back a mission that has already been planned.

The player computes no geometry and no physics. It receives a ``Mission``
built by ``uav.mission.build_mission`` and reads three things out of it: where
the aircraft goes (``mission.waypoints``), how fast it goes there
(``Waypoint.speed_ms``), and where the shutter fires (``kind == "photo"``).
The duration of each segment comes from the frozen
``uav.terrain_follow.segment_flight_time`` -- the same function the mission
assembler used to time its legs -- called once per segment. There is no second
kinematic model here, and this module imports neither ``uav.survey`` nor
``uav.mission``.

Two clocks, kept apart on purpose:

``duration_s``
    Time to fly the drawn path, summed from the frozen timing function. This
    is what the marker actually traverses.
``mission.stats.flight_time_s``
    The mission time the planner reports, which additionally carries take-off,
    landing and turn penalties. It is shown next to the first so the operator
    never reads the animation as the whole sortie.

Playback rate changes the clock and nothing else: ``t_sim += dt * rate``. The
mission object is never written to.
"""

from __future__ import annotations

import math

import numpy as np
from qgis.core import QgsGeometry, QgsPointXY, QgsWkbTypes
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtCore import QCoreApplication, QObject, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor

from ..core.planar import cumulative_distance
from ..core.units import format_duration
from ..uav import terrain_follow as tf


def tr(text):
    return QCoreApplication.translate("GeoCadUav", text)


#: Wall-clock milliseconds per tick. 20 fps: smooth enough to read, cheap
#: enough that the canvas never queues up behind it.
TICK_MS = 50

#: Playback speeds offered. The clock is multiplied, the mission is not:
#: at 0.25x the same flight takes four times as long to watch and the
#: telemetry reads the same numbers at the same chainages.
RATES = (0.25, 0.5, 1.0, 2.0, 4.0)

#: How many ticks a shutter flash stays on screen.
FLASH_TICKS = 3


class MissionPlayer(QObject):
    """A marker walking a planned route, in simulated time."""

    #: (t_sim, index, flashes) after every tick, for whoever draws a readout.
    ticked = pyqtSignal(float, int, int)
    finished = pyqtSignal()

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.mission = None
        self.rate = 1
        self.t_sim = 0.0
        self.index = 0
        self.flash_count = 0
        self.flashed = []
        self.message = ""
        self._xy = None
        self._z = None
        self._agl = None
        self._sub = None
        self._times = None
        self._chainage = None
        self._photo_at = None
        self._flash_left = 0
        self._marker = None
        self._flash = None
        #: The route already flown, as a line band behind the aircraft.
        self._track = None
        #: Ground already imaged, exposure by exposure. Built by ``load``
        #: when the mission has footprints and an AOI to clip them to, and
        #: by ``refresh_coverage`` when they are draped later.
        self.coverage = None
        self._aoi = None
        self._strip = None
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)

    # -- state -------------------------------------------------------------

    @property
    def is_playing(self) -> bool:
        return self._timer.isActive()

    @property
    def duration_s(self) -> float:
        """Time to fly the path, from the frozen timing function."""
        if self._times is None or self._times.size == 0:
            return 0.0
        return float(self._times[-1])

    @property
    def photo_count(self) -> int:
        return len(self.mission.photos) if self.mission is not None else 0

    def progress(self) -> float:
        """0..1 along the simulated flight."""
        total = self.duration_s
        return 0.0 if total <= 0 else min(1.0, self.t_sim / total)

    # -- loading -----------------------------------------------------------

    def load(self, mission, aoi_geom=None) -> bool:
        """Read a mission into a timeline. Returns False and says why if not.

        ``aoi_geom`` is the area the mission was planned over; given it, the
        simulator also tracks how much of that area is already imaged.
        """
        self.stop()
        self.mission = None
        self._xy = None
        self._z = None
        self._agl = None
        self._sub = None
        self._times = None
        self._chainage = None
        self._photo_at = None
        if mission is None:
            self.message = tr(
                "Nessuna missione da simulare: genera prima la rotta nella "
                "scheda UAV.")
            return False
        waypoints = list(getattr(mission, "waypoints", []) or [])
        if len(waypoints) < 2:
            self.message = tr(
                "La missione non ha abbastanza waypoint per una simulazione.")
            return False

        xy = np.array([[wp.x, wp.y] for wp in waypoints], dtype=float)
        z = np.array([wp.z_amsl for wp in waypoints], dtype=float)
        speeds = np.array([wp.speed_ms for wp in waypoints], dtype=float)

        # One call to the frozen timing function per segment. Summing these
        # reproduces the same function applied to the whole polyline, because
        # it is a sum of per-segment terms -- verified in the tests.
        steps = np.zeros(len(waypoints), dtype=float)
        for i in range(len(waypoints) - 1):
            steps[i + 1] = tf.segment_flight_time(xy[i:i + 2], z[i:i + 2],
                                                  speeds[i:i + 1])
        self._times = np.cumsum(steps)
        self._xy = xy
        self._z = z
        self._agl = np.array([wp.z_agl for wp in waypoints], dtype=float)
        self._sub = np.array([wp.sub_mission for wp in waypoints], dtype=int)
        self._chainage = cumulative_distance(xy)
        self._photo_at = [wp.kind == "photo" for wp in waypoints]
        self._strip = np.array([wp.strip_index for wp in waypoints],
                               dtype=int)
        # Which exposure each photo waypoint is, so the coverage can union
        # the right footprint when the shutter fires.
        self._photo_index = {}
        seen = 0
        for i, is_photo in enumerate(self._photo_at):
            if is_photo:
                self._photo_index[i] = seen
                seen += 1
        self._aoi = aoi_geom
        self.coverage = self._build_coverage(mission, aoi_geom)
        self.mission = mission
        self.message = ""
        return True

    def refresh_coverage(self, aoi_geom=None) -> bool:
        """Pick up footprints draped after the mission was loaded.

        The planner casts them only when asked, so a route can be loaded
        into the simulator before it has any. When they arrive, this rebuilds
        the tracking and puts it back where the clock is.
        """
        if self.mission is None:
            return False
        area = aoi_geom if aoi_geom is not None else self._aoi
        self._aoi = area
        self.coverage = self._build_coverage(self.mission, area)
        if self.coverage is None:
            return False
        self.coverage.set_taken(self._photo_index[i] for i in self.flashed)
        return True

    @staticmethod
    def _build_coverage(mission, aoi_geom):
        """Coverage tracking, when there is something to track it on."""
        footprints = list(getattr(mission, "footprints", None) or [])
        if not footprints or aoi_geom is None:
            return None
        from ..uav.photogrammetry import CoverageProgress        # noqa: PLC0415

        return CoverageProgress(aoi_geom, footprints)

    # -- transport controls ------------------------------------------------

    def play(self, mission=None, aoi_geom=None) -> bool:
        """Start, or resume if the same mission is already loaded."""
        if mission is not None and mission is not self.mission:
            if not self.load(mission, aoi_geom):
                return False
        if self.mission is None:
            if not self.load(mission, aoi_geom):
                return False
        if self.t_sim >= self.duration_s and self.duration_s > 0:
            self.rewind()
        # An exposure sitting on the very first waypoint is crossed before the
        # first tick, so it is fired here rather than being lost.
        if (self.t_sim == 0.0 and self.flash_count == 0
                and self._photo_at and self._photo_at[0]):
            self._fire_flash(0)
        self._draw_route()
        self._timer.start()
        return True

    def pause(self):
        """Freeze the clock. The index and the marker stay where they are."""
        self._timer.stop()

    def stop(self):
        """Stop and clear: no marker, no flash, nothing left on the canvas."""
        self._timer.stop()
        self.rewind()
        self._clear_bands()

    def rewind(self):
        self.t_sim = 0.0
        self.index = 0
        self.flash_count = 0
        self.flashed = []
        self._flash_left = 0
        if self.coverage is not None:
            self.coverage.reset()
        if self._track is not None:
            self._track.reset(QgsWkbTypes.LineGeometry)

    def to_start(self) -> float:
        """Back to take-off, without stopping the clock if it is running."""
        return self.seek(0.0)

    def to_end(self) -> float:
        """To the last waypoint: the state the mission finishes in."""
        return self.seek(self.duration_s)

    def step(self, delta: int = 1) -> int:
        """Move one waypoint forward or back. Returns the waypoint index.

        Not one tick: a tick is a fortieth of a second and says nothing. A
        waypoint is where the route actually does something -- turns, climbs,
        takes a photograph -- so that is the unit this steps in.
        """
        if self.mission is None or self._times is None \
                or not self._times.size:
            return 0
        self.pause()
        last = len(self._times) - 1
        # ``index`` is the last waypoint crossed. Forward means the next one;
        # backward from the middle of a leg means the one just crossed, which
        # is where an operator expects a step back to land.
        mid_leg = self.t_sim > float(self._times[self.index])
        if int(delta) < 0 and mid_leg:
            target = self.index + int(delta) + 1
        else:
            target = self.index + int(delta)
        target = int(min(max(target, 0), last))
        self.seek(float(self._times[target]))
        return self.index

    def seek(self, t_sim: float) -> float:
        """Put the clock at a moment without playing. Returns where it landed.

        The whole state is rebuilt from that instant rather than nudged:
        dragging the cursor backwards has to un-take the exposures, and a
        flash count that only ever grew would be a lie after one drag.
        """
        if self.mission is None or self._times is None \
                or not self._times.size:
            return 0.0
        total = self.duration_s
        moment = min(max(float(t_sim), 0.0), total)
        self.t_sim = moment
        index = int(np.searchsorted(self._times, moment, side="right")) - 1
        self.index = int(min(max(index, 0), len(self._times) - 1))
        self.flashed = [i for i in range(self.index + 1) if self._photo_at[i]]
        self.flash_count = len(self.flashed)
        self._flash_left = 0
        # Dragging backwards has to un-take the ground as well as the
        # exposures: a coverage that only ever grew would be a lie after
        # one drag.
        if self.coverage is not None:
            self.coverage.set_taken(self._photo_index[i]
                                    for i in self.flashed)
        self._draw_track()
        if self._flash is not None:
            self._flash.reset(QgsWkbTypes.PointGeometry)
        self._move_marker()
        self.ticked.emit(self.t_sim, self.index, self.flash_count)
        return self.t_sim

    def _fraction(self) -> float:
        """How far into the current segment the clock is, 0..1."""
        if self._times is None or self.index >= len(self._times) - 1:
            return 0.0
        span = self._times[self.index + 1] - self._times[self.index]
        if span <= 0:
            return 0.0
        return min(max((self.t_sim - self._times[self.index]) / span, 0.0),
                   1.0)

    def state(self) -> dict:
        """Where the aircraft is and what it has done, as numbers.

        The battery figure is what is left of **this** leg between take-off
        and landing, read off the sub-mission the planner already split the
        flight into -- not a charge model. A mission the planner cut into
        three legs is three batteries, and this says how far through the
        current one the clock is.
        """
        empty = {"t_s": 0.0, "duration_s": 0.0, "distance_m": 0.0,
                 "length_m": 0.0, "z_amsl": float("nan"),
                 "z_agl": float("nan"), "photos": 0, "photo_total": 0,
                 "sub_mission": 0, "sub_total": 0, "battery_left": 1.0,
                 "x": float("nan"), "y": float("nan"), "speed_ms": 0.0,
                 "heading_deg": float("nan"), "remaining_m": 0.0,
                 "remaining_s": 0.0, "waypoint": 0, "waypoint_total": 0,
                 "strip": -1, "progress_pct": 0.0, "coverage_pct": 0.0,
                 "covered_m2": 0.0, "uncovered_m2": 0.0}
        if self.mission is None or self._times is None \
                or not self._times.size:
            return empty

        i = self.index
        last = len(self._times) - 1
        fraction = self._fraction()
        nxt = min(i + 1, last)

        def blend(values):
            return float(values[i] + fraction * (values[nxt] - values[i]))

        sub = int(self._sub[i]) if self._sub is not None else 0
        subs = sorted({int(v) for v in self._sub}) if self._sub is not None \
            else []
        battery = 1.0
        if self._sub is not None and subs:
            same = np.where(self._sub == sub)[0]
            t0 = float(self._times[same[0]])
            t1 = float(self._times[same[-1]])
            if t1 > t0:
                battery = 1.0 - min(max((self.t_sim - t0) / (t1 - t0), 0.0),
                                    1.0)
        point = self.position() or (float("nan"), float("nan"))
        distance = blend(self._chainage)
        length = float(self._chainage[-1])
        waypoints = list(getattr(self.mission, "waypoints", []) or [])
        speed = float(waypoints[i].speed_ms) if i < len(waypoints) else 0.0
        # The heading of the leg being flown, from the geometry itself: the
        # commanded heading of a waypoint is where the camera looks, and on a
        # turn the two differ.
        heading = float("nan")
        if nxt != i:
            dx = float(self._xy[nxt, 0] - self._xy[i, 0])
            dy = float(self._xy[nxt, 1] - self._xy[i, 1])
            if dx or dy:
                heading = math.degrees(math.atan2(dx, dy)) % 360.0
        elif waypoints:
            heading = float(waypoints[min(i, len(waypoints) - 1)].heading_deg)
        return {
            "t_s": float(self.t_sim),
            "duration_s": float(self.duration_s),
            "distance_m": distance,
            "length_m": length,
            "remaining_m": max(0.0, length - distance),
            "remaining_s": max(0.0, float(self.duration_s) - self.t_sim),
            "x": float(point[0]),
            "y": float(point[1]),
            "speed_ms": speed,
            "heading_deg": heading,
            "waypoint": int(i) + 1,
            "waypoint_total": int(len(self._times)),
            "strip": int(self._strip[i]) if self._strip is not None else -1,
            "progress_pct": 100.0 * self.progress(),
            "coverage_pct": (self.coverage.percent
                             if self.coverage is not None else 0.0),
            "covered_m2": (self.coverage.covered_area_m2
                           if self.coverage is not None else 0.0),
            "uncovered_m2": (self.coverage.remaining_area_m2
                             if self.coverage is not None else 0.0),
            "z_amsl": blend(self._z),
            "z_agl": blend(self._agl),
            "photos": int(self.flash_count),
            "photo_total": int(self.photo_count),
            "sub_mission": subs.index(sub) + 1 if sub in subs else 0,
            "sub_total": len(subs),
            "battery_left": float(battery),
        }

    def distance_fraction(self) -> float:
        """0..1 along the flown distance -- not along the clock.

        The two differ wherever the climb rate caps the ground speed, and the
        altimetric profile has distance on its axis, so its cursor is placed
        with this one.
        """
        state = self.state()
        total = state["length_m"]
        return 0.0 if total <= 0 else min(1.0, state["distance_m"] / total)

    def set_rate(self, rate) -> float:
        """Change the clock multiplier. Never touches the mission."""
        try:
            wanted = float(rate)
        except (TypeError, ValueError):
            wanted = 1.0
        self.rate = wanted if wanted in RATES else 1.0
        return self.rate

    # -- the clock ---------------------------------------------------------

    def tick(self):
        """One frame. The tests call this directly instead of sleeping.

        A tick with the timer stopped does nothing: pause has to freeze the
        clock whoever calls this, not only when the caller is the timer.
        """
        if self.mission is None or self._times is None:
            return
        if not self._timer.isActive():
            return
        self.t_sim += (TICK_MS / 1000.0) * self.rate
        total = self.duration_s
        at_end = self.t_sim >= total
        if at_end:
            self.t_sim = total

        # Cross every waypoint whose time has passed, flashing the exposures.
        while (self.index < len(self._times) - 1
               and self._times[self.index + 1] <= self.t_sim):
            self.index += 1
            if self._photo_at[self.index]:
                self._fire_flash(self.index)
        self._move_marker()
        self._draw_track()
        if self._flash_left > 0:
            self._flash_left -= 1
            if self._flash_left == 0 and self._flash is not None:
                self._flash.reset(QgsWkbTypes.PointGeometry)

        self.ticked.emit(self.t_sim, self.index, self.flash_count)
        if at_end:
            self._timer.stop()
            self.finished.emit()

    def _fire_flash(self, index):
        self.flash_count += 1
        self.flashed.append(int(index))
        if self.coverage is not None and index in self._photo_index:
            self.coverage.add(self._photo_index[index])
        self._flash_left = FLASH_TICKS
        band = self._ensure_flash()
        if band is not None:
            band.reset(QgsWkbTypes.PointGeometry)
            band.addPoint(QgsPointXY(float(self._xy[index, 0]),
                                     float(self._xy[index, 1])), False)
            band.updatePosition()
            band.show()

    def position(self):
        """Interpolated (x, y) at ``t_sim``: linear in time along the segment.

        Constant speed on a segment means the position is linear in time, so
        this is drawing, not a second motion model.
        """
        if self._xy is None or self._times is None:
            return None
        i = self.index
        if i >= len(self._xy) - 1:
            return (float(self._xy[-1, 0]), float(self._xy[-1, 1]))
        span = self._times[i + 1] - self._times[i]
        fraction = 0.0 if span <= 0 else (self.t_sim - self._times[i]) / span
        fraction = min(max(fraction, 0.0), 1.0)
        return (float(self._xy[i, 0] + fraction * (self._xy[i + 1, 0]
                                                   - self._xy[i, 0])),
                float(self._xy[i, 1] + fraction * (self._xy[i + 1, 1]
                                                   - self._xy[i, 1])))

    # -- canvas ------------------------------------------------------------

    def _canvas(self):
        return self.iface.mapCanvas() if self.iface else None

    def _ensure_marker(self):
        canvas = self._canvas()
        if canvas is None:
            return None
        if self._marker is None:
            self._marker = QgsRubberBand(canvas, QgsWkbTypes.PointGeometry)
            self._marker.setColor(QColor(20, 90, 200, 235))
            self._marker.setIcon(QgsRubberBand.ICON_CIRCLE)
            self._marker.setIconSize(11)
            self._marker.setWidth(2)
        return self._marker

    def _ensure_flash(self):
        canvas = self._canvas()
        if canvas is None:
            return None
        if self._flash is None:
            self._flash = QgsRubberBand(canvas, QgsWkbTypes.PointGeometry)
            self._flash.setColor(QColor(250, 200, 40, 245))
            self._flash.setIcon(QgsRubberBand.ICON_BOX)
            self._flash.setIconSize(17)
            self._flash.setWidth(3)
        return self._flash

    def _draw_route(self):
        self._move_marker()
        self._draw_track()

    def _ensure_track(self):
        canvas = self._canvas()
        if canvas is None:
            return None
        if self._track is None:
            self._track = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
            self._track.setColor(QColor(20, 90, 200, 170))
            self._track.setWidth(3)
        return self._track

    def track_points(self):
        """The route already flown, as points, up to where the aircraft is.

        Computed whether or not there is a canvas to draw it on: how far the
        aircraft has come is a fact about the flight, and a headless test
        has to be able to ask.
        """
        if self._xy is None:
            return []
        points = [(float(self._xy[i, 0]), float(self._xy[i, 1]))
                  for i in range(self.index + 1)]
        here = self.position()
        if here is not None and (not points or here != points[-1]):
            points.append((float(here[0]), float(here[1])))
        return points

    def _draw_track(self):
        """Put the flown track on the canvas. Returns its point count."""
        points = self.track_points()
        band = self._ensure_track()
        if band is None or len(points) < 2:
            return len(points)
        band.reset(QgsWkbTypes.LineGeometry)
        band.addGeometry(QgsGeometry.fromPolylineXY(
            [QgsPointXY(x, y) for x, y in points]), None)
        band.show()
        return len(points)

    def track_vertices(self) -> int:
        """Vertices of the flown track. Counted with or without a canvas."""
        if self._track is not None and self._track.numberOfVertices():
            return int(self._track.numberOfVertices())
        return len(self.track_points())

    def _move_marker(self):
        band = self._ensure_marker()
        point = self.position()
        if band is None or point is None:
            return
        band.reset(QgsWkbTypes.PointGeometry)
        band.addPoint(QgsPointXY(point[0], point[1]), False)
        band.updatePosition()
        band.show()

    def _clear_bands(self):
        for band in (self._marker, self._flash):
            if band is not None:
                band.reset(QgsWkbTypes.PointGeometry)
                band.hide()
        if self._track is not None:
            self._track.reset(QgsWkbTypes.LineGeometry)
            self._track.hide()

    def band_vertices(self) -> int:
        """Marker plus flash vertices currently drawn. For the tests."""
        total = 0
        for band in (self._marker, self._flash):
            if band is not None:
                total += band.numberOfVertices()
        return total

    # -- readout -----------------------------------------------------------

    def summary(self) -> str:
        """What the operator is watching, and what it is not."""
        if self.mission is None:
            return self.message or tr("Nessuna missione caricata.")
        state = self.state()
        battery = tr("batteria {0}/{1} al {2:.0f} %").format(
            state["sub_mission"], state["sub_total"],
            100.0 * state["battery_left"]) if state["sub_total"] > 1 else \
            tr("batteria al {0:.0f} %").format(100.0 * state["battery_left"])
        heading = ("--" if state["heading_deg"] != state["heading_deg"]
                   else "{0:.0f} deg".format(state["heading_deg"]))
        coverage = ("" if self.coverage is None else
                    tr("\ncopertura {0:.1f} % - {1:,.0f} m2 ripresi, "
                       "{2:,.0f} m2 ancora scoperti").format(
                           state["coverage_pct"], state["covered_m2"],
                           state["uncovered_m2"]))
        return tr(
            "missione al {0:.1f} % - t {1} / {2} (restano {3}) - {4} di "
            "missione con decolli, atterraggi e virate\n"
            "percorso {5:,.0f} m, restano {6:,.0f} m - "
            "velocita' {7:.1f} m/s ({8:.0f} km/h) - prua {9}\n"
            "quota {10:,.0f} m s.l.m. ({11:.0f} m AGL) - {12}\n"
            "strisciata {13} - waypoint {14}/{15} - fotogramma {16}/{17} - "
            "{18}x").format(
                state["progress_pct"],
                format_duration(self.t_sim), format_duration(self.duration_s),
                format_duration(state["remaining_s"]),
                format_duration(self.mission.stats.flight_time_s),
                state["distance_m"], state["remaining_m"],
                state["speed_ms"], state["speed_ms"] * 3.6, heading,
                state["z_amsl"], state["z_agl"], battery,
                state["strip"] if state["strip"] >= 0 else "-",
                state["waypoint"], state["waypoint_total"],
                self.flash_count, self.photo_count,
                "{0:g}".format(self.rate)) + coverage

    def teardown(self):
        self._timer.stop()
        try:
            self._timer.timeout.disconnect(self.tick)
        except (TypeError, RuntimeError):
            pass
        self._clear_bands()
        self.coverage = None
        self.mission = None
