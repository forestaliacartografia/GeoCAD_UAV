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

import numpy as np
from qgis.core import QgsPointXY, QgsWkbTypes
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtCore import QCoreApplication, QObject, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor

from ..core.units import format_duration
from ..uav import terrain_follow as tf


def tr(text):
    return QCoreApplication.translate("GeoCadUav", text)


#: Wall-clock milliseconds per tick. 20 fps: smooth enough to read, cheap
#: enough that the canvas never queues up behind it.
TICK_MS = 50

#: Playback speeds offered. The clock is multiplied, the mission is not.
RATES = (1, 2, 5)

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
        self._times = None
        self._photo_at = None
        self._flash_left = 0
        self._marker = None
        self._flash = None
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

    def load(self, mission) -> bool:
        """Read a mission into a timeline. Returns False and says why if not."""
        self.stop()
        self.mission = None
        self._xy = None
        self._times = None
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
        self._photo_at = [wp.kind == "photo" for wp in waypoints]
        self.mission = mission
        self.message = ""
        return True

    # -- transport controls ------------------------------------------------

    def play(self, mission=None) -> bool:
        """Start, or resume if the same mission is already loaded."""
        if mission is not None and mission is not self.mission:
            if not self.load(mission):
                return False
        if self.mission is None:
            if not self.load(mission):
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

    def set_rate(self, rate: int) -> int:
        """Change the clock multiplier. Never touches the mission."""
        self.rate = int(rate) if int(rate) in RATES else 1
        return self.rate

    # -- the clock ---------------------------------------------------------

    def tick(self):
        """One frame. The tests call this directly instead of sleeping."""
        if self.mission is None or self._times is None:
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
        return tr(
            "t {0} / {1} percorso - {2} di missione con decolli, atterraggi "
            "e virate - scatti {3}/{4} - {5}x").format(
                format_duration(self.t_sim), format_duration(self.duration_s),
                format_duration(self.mission.stats.flight_time_s),
                self.flash_count, self.photo_count, self.rate)

    def teardown(self):
        self._timer.stop()
        try:
            self._timer.timeout.disconnect(self.tick)
        except (TypeError, RuntimeError):
            pass
        self._clear_bands()
        self.mission = None
