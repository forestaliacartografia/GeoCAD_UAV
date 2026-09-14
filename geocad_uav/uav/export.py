"""
Mission writers.

Spec P7 governs this module: **a format is only declared compatible if its
schema is documented and the output has been verified against it.** Each writer
therefore carries a :class:`ExportFormat` descriptor stating what it claims and
where the schema came from, and ``verified=False`` formats emit a disclaimer
both in the file and in the report.

What is claimed here:

* GeoJSON, GPKG, KML, GPX, CSV -- open, fully specified formats. Produced to
  spec and claimed as compatible.
* Litchi Mission Hub CSV -- the column set is the published Mission Hub CSV
  header. Claimed, with the standing instruction to load one exported file in
  Mission Hub and check it before flying.
* QGC WPL 110 (Mission Planner / ArduPilot) -- the documented MAVLink waypoint
  text format. Claimed.
* **DJI WPML / KMZ is deliberately NOT implemented as a native DJI mission.**
  The WPML schema could not be verified against DJI's published documentation
  in this build, and emitting something shaped like a DJI mission that has not
  been checked against the real schema is exactly the failure mode P7 forbids.
  What is offered instead is a plain waypoint KMZ (zipped OGC KML), clearly
  labelled as *not* a native DJI Fly / Pilot mission file.

Nothing is ever overwritten without the caller passing ``overwrite=True``.
"""

from __future__ import annotations

import csv
import math
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ..core.errors import ExportError, UnsupportedFormatError
from ..core.models import Mission, VerticalDatum

# Altitude references understood by the writers.
ALT_AMSL = "amsl"                      # absolute, in the mission vertical datum
ALT_RELATIVE_HOME = "relative_home"    # height above the take-off point
ALT_ELLIPSOIDAL = "ellipsoidal"        # AMSL + geoid undulation


@dataclass(frozen=True)
class ExportFormat:
    """What a writer produces, and on what authority."""

    key: str
    label: str
    extension: str
    #: True only when the schema is documented AND the output follows it.
    verified: bool
    schema_source: str
    notes: str = ""
    needs_wgs84: bool = False


FORMATS = {
    "geojson": ExportFormat(
        "geojson", "GeoJSON (waypoint + rotta)", ".geojson", True,
        "RFC 7946 GeoJSON", "Coordinate in WGS84 come richiesto da RFC 7946.",
        needs_wgs84=True),
    "gpkg": ExportFormat(
        "gpkg", "GeoPackage (tutti i layer)", ".gpkg", True,
        "OGC GeoPackage 1.3", "Scritto tramite QgsVectorFileWriter."),
    "kml": ExportFormat(
        "kml", "KML (visualizzazione)", ".kml", True,
        "OGC KML 2.2", "Per ispezione in Google Earth, non per il volo.",
        needs_wgs84=True),
    "kmz": ExportFormat(
        "kmz", "KMZ waypoint (generico)", ".kmz", True,
        "OGC KML 2.2 compresso in ZIP",
        "NON e' un file di missione DJI nativo: non e' caricabile in DJI Fly "
        "o Pilot come waylines. Serve per visualizzazione e scambio.",
        needs_wgs84=True),
    "gpx": ExportFormat(
        "gpx", "GPX (waypoint + rotta)", ".gpx", True,
        "Topografix GPX 1.1", "Quote in metri sul geoide WGS84 per specifica.",
        needs_wgs84=True),
    "csv_waypoints": ExportFormat(
        "csv_waypoints", "CSV waypoint", ".csv", True,
        "Formato proprio, intestazione documentata nel file."),
    "csv_photos": ExportFormat(
        "csv_photos", "CSV centri di presa (EO)", ".csv", True,
        "Formato proprio, intestazione documentata nel file."),
    "litchi": ExportFormat(
        "litchi", "Litchi Mission Hub CSV", ".csv", True,
        "Intestazione CSV di Litchi Mission Hub",
        "Verifica sempre una missione esportata in Mission Hub prima di volare.",
        needs_wgs84=True),
    "mavlink": ExportFormat(
        "mavlink", "Mission Planner / ArduPilot (QGC WPL 110)", ".waypoints",
        True, "MAVLink waypoint file, intestazione QGC WPL 110",
        "Frame 0 = MAV_FRAME_GLOBAL (AMSL), frame 3 = quota relativa al decollo.",
        needs_wgs84=True),
    "dji_wpml": ExportFormat(
        "dji_wpml", "DJI WPML (.kmz nativo)", ".kmz", True,
        "DJI Cloud API > DJI WPML: Template.kml, Waylines.wpml, Common "
        "Elements (namespace http://www.dji.com/wpmz/1.0.2)",
        "Solo per i velivoli enterprise che DJI elenca e il cui profilo "
        "dichiara gli enumerativi. Quota relativa al decollo o "
        "ellissoidica: WPML non ha un riferimento ortometrico.",
        needs_wgs84=True),
}

#: Formats that were considered and deliberately not implemented, with why.
#: Empty since 1.39.0, when WPML was read from DJI's own documentation and
#: written. What remains refused is not a format but a *case*: see
#: :func:`_wpml_identity` and :func:`_wpml_height_mode`.
NOT_IMPLEMENTED = {}

# --------------------------------------------------------------------------
# DJI WPML
# --------------------------------------------------------------------------

#: The namespaces of the two files, verbatim from the published samples.
WPML_KML_NS = "http://www.opengis.net/kml/2.2"
WPML_NS = "http://www.dji.com/wpmz/1.0.2"

#: Where the two files sit inside the archive. DJI: "A KMZ file contains a
#: wpmz folder with res, template.kml and waylines.wpml".
WPML_TEMPLATE_PATH = "wpmz/template.kml"
WPML_WAYLINES_PATH = "wpmz/waylines.wpml"

#: executeHeightMode values, and which of this plugin's altitude modes each
#: one expresses. There is no orthometric option in the enum.
WPML_HEIGHT_MODES = {
    ALT_RELATIVE_HOME: "relativeToStartPoint",
    ALT_ELLIPSOIDAL: "WGS84",
}

#: Safe take-off height, metres. DJI: remote controller range [1.2, 1500].
WPML_TAKEOFF_SECURITY_M = 20.0


def _wpml_identity(mission: Mission):
    """The aircraft and payload enumeration values, or a refusal.

    Read from the drone profile, which is where DJI data belongs. A profile
    without them is an aircraft this plugin will not write a WPML for.
    """
    from . import drones as drone_lib                           # noqa: PLC0415

    key = mission.drone_key or ""
    try:
        profile = drone_lib.load_library().get(key)
    except Exception:                                           # noqa: BLE001
        profile = None
    block = dict(getattr(profile, "wpml", ()) or ())
    required = ("drone_enum", "drone_sub_enum", "payload_enum",
                "payload_position")
    if not all(name in block for name in required):
        raise UnsupportedFormatError(
            "drone {0!r} declares no WPML enumeration".format(key),
            user_message="Il profilo drone '{0}' non dichiara gli "
                         "identificativi DJI richiesti da WPML.".format(
                             profile.name if profile is not None else key),
            hint="WPML e' riservato ai velivoli enterprise che DJI elenca "
                 "(M300/M350 RTK, M30/M30T, Mavic 3 Enterprise, M3D/M3TD, "
                 "M4D/M4TD, M4E/M4T, Matrice 400). Le serie Mini, Air e "
                 "Phantom non volano WPML. Per un velivolo supportato, "
                 "aggiungi il blocco 'wpml' al suo profilo in "
                 "profiles/drones.json.")
    return block


def _wpml_height_mode(alt_mode: str) -> str:
    mode = WPML_HEIGHT_MODES.get(alt_mode)
    if mode is None:
        raise ExportError(
            "WPML has no orthometric executeHeightMode",
            user_message="WPML non ha un riferimento di quota ortometrico.",
            hint="Esporta con quota relativa al punto di decollo oppure "
                 "ellissoidica: wpml:executeHeightMode ammette solo "
                 "relativeToStartPoint, WGS84 e realTimeFollowSurface.")
    return mode


def _wpml(tag: str, value=None, indent: int = 0) -> str:
    pad = "  " * indent
    if value is None:
        return "{0}<wpml:{1}>".format(pad, tag)
    return "{0}<wpml:{1}>{2}</wpml:{1}>".format(pad, tag, value)


def _wpml_mission_config(mission: Mission, identity, speed: float) -> str:
    """<wpml:missionConfig>, with every element DJI marks required."""
    out = ["  <wpml:missionConfig>"]
    for tag, value in (("flyToWaylineMode", "safely"),
                       ("finishAction", "goHome"),
                       ("exitOnRCLost", "executeLostAction"),
                       ("executeRCLostAction", "goBack"),
                       ("takeOffSecurityHeight",
                        "{0:g}".format(WPML_TAKEOFF_SECURITY_M)),
                       ("globalTransitionalSpeed", "{0:.2f}".format(speed)),
                       ("globalRTHHeight",
                        "{0:.0f}".format(max(mission.h_agl_m, 30.0)))):
        out.append(_wpml(tag, value, 2))
    out.append("    <wpml:droneInfo>")
    out.append(_wpml("droneEnumValue", identity["drone_enum"], 3))
    out.append(_wpml("droneSubEnumValue", identity["drone_sub_enum"], 3))
    out.append("    </wpml:droneInfo>")
    out.append("    <wpml:payloadInfo>")
    out.append(_wpml("payloadEnumValue", identity["payload_enum"], 3))
    out.append(_wpml("payloadPositionIndex", identity["payload_position"], 3))
    out.append("    </wpml:payloadInfo>")
    out.append("  </wpml:missionConfig>")
    return "\n".join(out)


def _wpml_points(mission, transform, alt_mode, home_z):
    """``[(index, lon, lat, height, speed, is_photo)]`` for every waypoint."""
    rows = []
    for index, wp in enumerate(mission.waypoints):
        lon, lat = _to_wgs84(transform, wp.x, wp.y)
        height = _altitude(mission, wp.z_amsl, alt_mode, home_z)
        speed = wp.speed_ms if wp.speed_ms > 0 else mission.speed_ms
        rows.append((index, lon, lat, height, speed, wp.is_photo))
    return rows


def _wpml_action_group(index: int, payload_position: int,
                       gimbal_pitch: float) -> "list[str]":
    """One action group on a waypoint: aim the gimbal, then take the photo."""
    return [
        "      <wpml:actionGroup>",
        _wpml("actionGroupId", index, 4),
        _wpml("actionGroupStartIndex", index, 4),
        _wpml("actionGroupEndIndex", index, 4),
        _wpml("actionGroupMode", "sequence", 4),
        "        <wpml:actionTrigger>",
        _wpml("actionTriggerType", "reachPoint", 5),
        "        </wpml:actionTrigger>",
        "        <wpml:action>",
        _wpml("actionId", 0, 5),
        _wpml("actionActuatorFunc", "gimbalRotate", 5),
        "          <wpml:actionActuatorFuncParam>",
        _wpml("gimbalRotateMode", "absoluteAngle", 6),
        _wpml("gimbalPitchRotateEnable", 1, 6),
        _wpml("gimbalPitchRotateAngle", "{0:.1f}".format(gimbal_pitch), 6),
        _wpml("gimbalRollRotateEnable", 0, 6),
        _wpml("gimbalRollRotateAngle", 0, 6),
        _wpml("gimbalYawRotateEnable", 0, 6),
        _wpml("gimbalYawRotateAngle", 0, 6),
        _wpml("gimbalRotateTimeEnable", 0, 6),
        _wpml("gimbalRotateTime", 0, 6),
        _wpml("payloadPositionIndex", payload_position, 6),
        "          </wpml:actionActuatorFuncParam>",
        "        </wpml:action>",
        "        <wpml:action>",
        _wpml("actionId", 1, 5),
        _wpml("actionActuatorFunc", "takePhoto", 5),
        "          <wpml:actionActuatorFuncParam>",
        _wpml("fileSuffix", "wp{0}".format(index), 6),
        _wpml("payloadPositionIndex", payload_position, 6),
        _wpml("useGlobalPayloadLensIndex", 1, 6),
        "          </wpml:actionActuatorFuncParam>",
        "        </wpml:action>",
        "      </wpml:actionGroup>",
    ]


def _wpml_waylines(mission, rows, identity, speed, height_mode,
                   gimbal_pitch) -> str:
    """waylines.wpml: the file the aircraft executes."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<kml xmlns="{0}" xmlns:wpml="{1}">'.format(WPML_KML_NS, WPML_NS),
           "<Document>",
           _wpml_mission_config(mission, identity, speed),
           "  <Folder>",
           _wpml("templateId", 0, 2),
           _wpml("executeHeightMode", height_mode, 2),
           _wpml("waylineId", 0, 2),
           _wpml("autoFlightSpeed", "{0:.2f}".format(speed), 2)]
    for index, lon, lat, height, wp_speed, is_photo in rows:
        out.append("    <Placemark>")
        out.append("      <Point>")
        out.append("        <coordinates>{0:.8f},{1:.8f}</coordinates>".format(
            lon, lat))
        out.append("      </Point>")
        out.append(_wpml("index", index, 3))
        out.append(_wpml("executeHeight", "{0:.2f}".format(height), 3))
        out.append(_wpml("waypointSpeed", "{0:.2f}".format(wp_speed), 3))
        out.append("      <wpml:waypointHeadingParam>")
        out.append(_wpml("waypointHeadingMode", "followWayline", 4))
        out.append(_wpml("waypointHeadingPathMode", "followBadArc", 4))
        out.append("      </wpml:waypointHeadingParam>")
        out.append("      <wpml:waypointTurnParam>")
        out.append(_wpml("waypointTurnMode",
                         "toPointAndStopWithDiscontinuityCurvature", 4))
        out.append(_wpml("waypointTurnDampingDist", 0, 4))
        out.append("      </wpml:waypointTurnParam>")
        if is_photo:
            out.extend(_wpml_action_group(
                index, identity["payload_position"], gimbal_pitch))
        out.append("    </Placemark>")
    out.extend(["  </Folder>", "</Document>", "</kml>", ""])
    return "\n".join(out)


def _wpml_template(mission, rows, identity, speed, height_mode,
                   gimbal_pitch) -> str:
    """template.kml: the same mission as an editable waypoint template."""
    stamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<kml xmlns="{0}" xmlns:wpml="{1}">'.format(WPML_KML_NS, WPML_NS),
           "<Document>",
           _wpml("author", "GeoCad UAV Toolkit", 1),
           _wpml("createTime", stamp, 1),
           _wpml("updateTime", stamp, 1),
           _wpml_mission_config(mission, identity, speed),
           "  <Folder>",
           _wpml("templateType", "waypoint", 2),
           _wpml("templateId", 0, 2),
           "    <wpml:waylineCoordinateSysParam>",
           _wpml("coordinateMode", "WGS84", 3),
           _wpml("heightMode", height_mode, 3),
           _wpml("positioningType", "GPS", 3),
           "    </wpml:waylineCoordinateSysParam>",
           _wpml("autoFlightSpeed", "{0:.2f}".format(speed), 2),
           _wpml("gimbalPitchMode", "usePointSetting", 2),
           _wpml("globalHeight", "{0:.2f}".format(
               rows[0][3] if rows else mission.h_agl_m), 2),
           "    <wpml:globalWaypointHeadingParam>",
           _wpml("waypointHeadingMode", "followWayline", 3),
           _wpml("waypointHeadingPathMode", "followBadArc", 3),
           "    </wpml:globalWaypointHeadingParam>",
           _wpml("globalWaypointTurnMode",
                 "toPointAndStopWithDiscontinuityCurvature", 2),
           _wpml("globalUseStraightLine", 0, 2)]
    for index, lon, lat, height, _speed, _is_photo in rows:
        out.append("    <Placemark>")
        out.append("      <Point>")
        out.append("        <coordinates>{0:.8f},{1:.8f}</coordinates>".format(
            lon, lat))
        out.append("      </Point>")
        out.append(_wpml("index", index, 3))
        out.append(_wpml("ellipsoidHeight", "{0:.2f}".format(
            height + mission.geoid_undulation_m), 3))
        out.append(_wpml("height", "{0:.2f}".format(height), 3))
        out.append(_wpml("useGlobalHeight", 0, 3))
        out.append(_wpml("useGlobalSpeed", 1, 3))
        out.append(_wpml("useGlobalHeadingParam", 1, 3))
        out.append(_wpml("useGlobalTurnParam", 1, 3))
        out.append(_wpml("gimbalPitchAngle", "{0:.1f}".format(gimbal_pitch), 3))
        out.append("    </Placemark>")
    out.extend(["  </Folder>", "</Document>", "</kml>", ""])
    return "\n".join(out)


def _write_wpml(mission, path, transform, alt_mode, home_z, fmt, opts):
    """The KMZ DJI Pilot and FlightHub read: wpmz/template.kml + waylines."""
    identity = _wpml_identity(mission)
    height_mode = _wpml_height_mode(alt_mode)
    rows = _wpml_points(mission, transform, alt_mode, home_z)
    speed = mission.speed_ms if mission.speed_ms > 0 else 5.0
    gimbal = float(opts.get("gimbal_pitch_deg", -90.0))
    template = _wpml_template(mission, rows, identity, speed, height_mode,
                              gimbal)
    waylines = _wpml_waylines(mission, rows, identity, speed, height_mode,
                              gimbal)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(WPML_TEMPLATE_PATH, template)
        archive.writestr(WPML_WAYLINES_PATH, waylines)
    return path


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _guard_path(path: str, overwrite: bool) -> str:
    if not path:
        raise ExportError("empty output path",
                          user_message="Percorso di destinazione mancante.")
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        raise ExportError(
            "directory does not exist: {0}".format(directory),
            user_message="La cartella di destinazione non esiste.",
            hint=directory)
    if os.path.exists(path) and not overwrite:
        raise ExportError(
            "refusing to overwrite {0}".format(path),
            user_message="Il file esiste gia'.",
            hint="Conferma la sovrascrittura o scegli un altro nome.")
    if directory and not os.access(directory, os.W_OK):
        raise ExportError(
            "directory not writable: {0}".format(directory),
            user_message="La cartella di destinazione non e' scrivibile.",
            hint=directory)
    return path


def _altitude(mission: Mission, z_amsl: float, mode: str,
              home_z: Optional[float]) -> float:
    """Convert a stored AMSL height into the requested export reference."""
    if mode == ALT_AMSL:
        return z_amsl
    if mode == ALT_ELLIPSOIDAL:
        return z_amsl + mission.geoid_undulation_m
    if mode == ALT_RELATIVE_HOME:
        if home_z is None:
            raise ExportError(
                "relative altitude requested without a home elevation",
                user_message="Quota relativa richiesta senza quota del punto "
                             "di decollo.",
                hint="Indica il punto di decollo o esporta in quota assoluta.")
        return z_amsl - home_z
    raise ExportError("unknown altitude mode {0!r}".format(mode),
                      user_message="Riferimento di quota non riconosciuto.")


def _to_wgs84(transform, x: float, y: float):
    """Project one point to (lon, lat). ``transform`` may be None (already 4326)."""
    if transform is None:
        return x, y
    from qgis.core import QgsPointXY                            # noqa: PLC0415
    pt = transform.transform(QgsPointXY(float(x), float(y)))
    return pt.x(), pt.y()


def _header_comment(mission: Mission, fmt: ExportFormat) -> "list[str]":
    lines = [
        "GeoCad UAV Toolkit - {0}".format(fmt.label),
        "Generato: {0}".format(datetime.now(timezone.utc).isoformat(
            timespec="seconds")),
        "Schema: {0}".format(fmt.schema_source),
        "CRS orizzontale sorgente: {0}".format(mission.crs_authid or "n/d"),
        "Datum verticale: {0}".format(
            VerticalDatum.label(mission.vertical_datum)),
        "Camera: {0} | H_AGL {1:.1f} m | GSD {2:.2f} cm/px".format(
            mission.camera_key, mission.h_agl_m, mission.gsd_m * 100.0),
    ]
    if not fmt.verified:
        lines.append("ATTENZIONE: schema non verificato. " + fmt.notes)
    elif fmt.notes:
        lines.append("Nota: {0}".format(fmt.notes))
    return lines


def available_formats(mission: Mission) -> "list[ExportFormat]":
    return list(FORMATS.values())


def describe_unimplemented() -> "list[str]":
    """Italian explanation of what is intentionally not offered, and why."""
    return ["{0}: {1}".format(label, reason)
            for label, reason in NOT_IMPLEMENTED.values()]


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

def write(mission: Mission, key: str, path: str, transform=None,
          altitude_mode: str = ALT_AMSL, home_z: Optional[float] = None,
          overwrite: bool = False, **options) -> str:
    """Write ``mission`` in format ``key``. Returns the path written."""
    if key in NOT_IMPLEMENTED:
        label, reason = NOT_IMPLEMENTED[key]
        raise UnsupportedFormatError(
            "format {0} is deliberately not implemented".format(key),
            user_message="{0} non e' disponibile.".format(label), hint=reason)
    fmt = FORMATS.get(key)
    if fmt is None:
        raise UnsupportedFormatError(
            "unknown export format {0!r}".format(key),
            user_message="Formato di esportazione sconosciuto: '{0}'.".format(key),
            hint="Disponibili: {0}.".format(", ".join(sorted(FORMATS))))
    if not mission.waypoints:
        raise ExportError("mission has no waypoints",
                          user_message="La missione non contiene waypoint.")
    if fmt.needs_wgs84 and transform is None and mission.crs_authid \
            and mission.crs_authid.upper() not in ("EPSG:4326", ""):
        raise ExportError(
            "format {0} needs WGS84 but no transform was supplied".format(key),
            user_message="Questo formato richiede coordinate WGS84.",
            hint="Fornisci la trasformazione dal CRS di lavoro a EPSG:4326.")

    _guard_path(path, overwrite)
    writer = _WRITERS[key]
    return writer(mission, path, transform, altitude_mode, home_z, fmt, options)


def _write_csv_waypoints(mission, path, transform, alt_mode, home_z, fmt, opts):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        for line in _header_comment(mission, fmt):
            handle.write("# {0}\n".format(line))
        writer = csv.writer(handle)
        writer.writerow(["seq", "sub_mission", "strip", "x", "y", "lon", "lat",
                         "z_export", "z_amsl", "z_agl", "heading_deg",
                         "gimbal_pitch_deg", "speed_ms", "type", "actions",
                         "dem_gap", "climb_limited"])
        for wp in mission.waypoints:
            lon, lat = _to_wgs84(transform, wp.x, wp.y)
            writer.writerow([
                wp.seq, wp.sub_mission, wp.strip_index,
                "{0:.4f}".format(wp.x), "{0:.4f}".format(wp.y),
                "{0:.8f}".format(lon), "{0:.8f}".format(lat),
                "{0:.3f}".format(_altitude(mission, wp.z_amsl, alt_mode, home_z)),
                "{0:.3f}".format(wp.z_amsl), "{0:.3f}".format(wp.z_agl),
                "{0:.2f}".format(wp.heading_deg),
                "{0:.1f}".format(wp.gimbal_pitch_deg),
                "{0:.2f}".format(wp.speed_ms), wp.kind,
                ";".join(wp.actions), int(wp.dem_gap), int(wp.climb_limited)])
    return path


def _write_csv_photos(mission, path, transform, alt_mode, home_z, fmt, opts):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        for line in _header_comment(mission, fmt):
            handle.write("# {0}\n".format(line))
        handle.write("# omega/phi/kappa: ENU destrorso, R = Rx(o) Ry(p) Rz(k), "
                     "asse z immagine opposto alla direzione di vista.\n")
        writer = csv.writer(handle)
        writer.writerow(["photo_id", "sub_mission", "strip", "x", "y", "lon",
                         "lat", "z_amsl", "z_agl", "omega_deg", "phi_deg",
                         "kappa_deg", "heading_deg", "gimbal_pitch_deg",
                         "gsd_cm_px"])
        for ph in mission.photos:
            lon, lat = _to_wgs84(transform, ph.x, ph.y)
            writer.writerow([
                ph.photo_id, ph.sub_mission, ph.strip_index,
                "{0:.4f}".format(ph.x), "{0:.4f}".format(ph.y),
                "{0:.8f}".format(lon), "{0:.8f}".format(lat),
                "{0:.3f}".format(ph.z_amsl), "{0:.3f}".format(ph.z_agl),
                "{0:.5f}".format(ph.omega_deg), "{0:.5f}".format(ph.phi_deg),
                "{0:.5f}".format(ph.kappa_deg),
                "{0:.2f}".format(ph.heading_deg),
                "{0:.1f}".format(ph.gimbal_pitch_deg),
                "{0:.3f}".format(ph.gsd_cm)])
    return path


#: Litchi Mission Hub CSV column order. Only these columns are written; a
#: column whose meaning is not certain is left at its documented neutral value
#: rather than guessed at.
LITCHI_HEADER = [
    "latitude", "longitude", "altitude(m)", "heading(deg)", "curvesize(m)",
    "rotationdir", "gimbalmode", "gimbalpitchangle",
    "actiontype1", "actionparam1", "actiontype2", "actionparam2",
    "actiontype3", "actionparam3", "actiontype4", "actionparam4",
    "actiontype5", "actionparam5", "actiontype6", "actionparam6",
    "actiontype7", "actionparam7", "actiontype8", "actionparam8",
    "actiontype9", "actionparam9", "actiontype10", "actionparam10",
    "actiontype11", "actionparam11", "actiontype12", "actionparam12",
    "actiontype13", "actionparam13", "actiontype14", "actionparam14",
    "actiontype15", "actionparam15",
    "altitudemode", "speed(m/s)", "poi_latitude", "poi_longitude",
    "poi_altitude(m)", "poi_altitudemode", "photo_timeinterval",
    "photo_distinterval",
]

# Litchi action codes: -1 = no action, 1 = take photo.
_LITCHI_NO_ACTION = -1
_LITCHI_TAKE_PHOTO = 1
# Litchi altitude modes: 0 = above take-off point, 1 = AMSL.
_LITCHI_ALT_RELATIVE = 0
_LITCHI_ALT_AMSL = 1


def _write_litchi(mission, path, transform, alt_mode, home_z, fmt, opts):
    if alt_mode == ALT_RELATIVE_HOME:
        litchi_alt_mode = _LITCHI_ALT_RELATIVE
    elif alt_mode == ALT_AMSL:
        litchi_alt_mode = _LITCHI_ALT_AMSL
    else:
        raise ExportError(
            "Litchi supports only AMSL or take-off-relative altitudes",
            user_message="Litchi accetta solo quota assoluta (AMSL) o relativa "
                         "al punto di decollo.",
            hint="Cambia il riferimento di quota nelle opzioni di esportazione.")

    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(LITCHI_HEADER)
        for wp in mission.waypoints:
            lon, lat = _to_wgs84(transform, wp.x, wp.y)
            row = [
                "{0:.8f}".format(lat), "{0:.8f}".format(lon),
                "{0:.2f}".format(_altitude(mission, wp.z_amsl, alt_mode, home_z)),
                "{0:.1f}".format(wp.heading_deg),
                "0.2",                       # curvesize: near stop-and-go
                "0",                         # rotationdir: shortest
                "2",                         # gimbalmode: interpolate
                "{0:.1f}".format(wp.gimbal_pitch_deg),
            ]
            for slot in range(15):
                if slot == 0 and wp.is_photo:
                    row.extend([_LITCHI_TAKE_PHOTO, 0])
                else:
                    row.extend([_LITCHI_NO_ACTION, 0])
            row.extend([
                litchi_alt_mode, "{0:.2f}".format(wp.speed_ms),
                "0", "0", "0", "0",          # no POI
                "0",                         # photo_timeinterval: off
                "0",                         # photo_distinterval: off
            ])
            writer.writerow(row)
    return path


# QGC WPL 110 command and frame codes (MAVLink common message set).
_MAV_CMD_NAV_WAYPOINT = 16
_MAV_CMD_NAV_TAKEOFF = 22
_MAV_CMD_NAV_RETURN_TO_LAUNCH = 20
_MAV_CMD_DO_SET_CAM_TRIGG_DIST = 206
_MAV_FRAME_GLOBAL = 0                  # altitude is AMSL
_MAV_FRAME_GLOBAL_RELATIVE_ALT = 3     # altitude is relative to home


def _write_mavlink(mission, path, transform, alt_mode, home_z, fmt, opts):
    frame = (_MAV_FRAME_GLOBAL_RELATIVE_ALT if alt_mode == ALT_RELATIVE_HOME
             else _MAV_FRAME_GLOBAL)
    trigger_distance = float(opts.get("trigger_distance_m", 0.0))

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("QGC WPL 110\n")
        index = 0
        first = mission.waypoints[0]
        lon, lat = _to_wgs84(transform, first.x, first.y)
        alt = _altitude(mission, first.z_amsl, alt_mode, home_z)

        # Item 0 is the home position by convention; current = 1 on it.
        handle.write(_wpl_row(index, 1, frame, _MAV_CMD_NAV_WAYPOINT,
                              0, 0, 0, 0, lat, lon, alt))
        index += 1

        if trigger_distance > 0:
            handle.write(_wpl_row(index, 0, _MAV_FRAME_GLOBAL,
                                  _MAV_CMD_DO_SET_CAM_TRIGG_DIST,
                                  trigger_distance, 0, 0, 0, 0, 0, 0))
            index += 1

        for wp in mission.waypoints:
            lon, lat = _to_wgs84(transform, wp.x, wp.y)
            alt = _altitude(mission, wp.z_amsl, alt_mode, home_z)
            handle.write(_wpl_row(index, 0, frame, _MAV_CMD_NAV_WAYPOINT,
                                  0, 0, 0, 0, lat, lon, alt))
            index += 1

        if trigger_distance > 0:
            handle.write(_wpl_row(index, 0, _MAV_FRAME_GLOBAL,
                                  _MAV_CMD_DO_SET_CAM_TRIGG_DIST,
                                  0, 0, 0, 0, 0, 0, 0))
            index += 1
        handle.write(_wpl_row(index, 0, _MAV_FRAME_GLOBAL,
                              _MAV_CMD_NAV_RETURN_TO_LAUNCH,
                              0, 0, 0, 0, 0, 0, 0))
    return path


def _wpl_row(index, current, frame, command, p1, p2, p3, p4, x, y, z,
             autocontinue=1) -> str:
    """One tab-separated QGC WPL 110 line."""
    return ("{0}\t{1}\t{2}\t{3}\t{4:.8f}\t{5:.8f}\t{6:.8f}\t{7:.8f}\t"
            "{8:.8f}\t{9:.8f}\t{10:.6f}\t{11}\n").format(
        index, current, frame, command, p1, p2, p3, p4, x, y, z, autocontinue)


def _write_gpx(mission, path, transform, alt_mode, home_z, fmt, opts):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="GeoCad UAV Toolkit" '
             'xmlns="http://www.topografix.com/GPX/1/1">',
             "  <metadata><name>{0}</name><time>{1}</time></metadata>".format(
                 _xml_escape(mission.camera_key or "missione"), stamp)]
    for wp in mission.waypoints:
        lon, lat = _to_wgs84(transform, wp.x, wp.y)
        alt = _altitude(mission, wp.z_amsl, alt_mode, home_z)
        parts.append(
            '  <wpt lat="{0:.8f}" lon="{1:.8f}"><ele>{2:.2f}</ele>'
            "<name>WP{3}</name><type>{4}</type></wpt>".format(
                lat, lon, alt, wp.seq, wp.kind))
    parts.append("  <rte><name>rotta</name>")
    for wp in mission.waypoints:
        lon, lat = _to_wgs84(transform, wp.x, wp.y)
        alt = _altitude(mission, wp.z_amsl, alt_mode, home_z)
        parts.append(
            '    <rtept lat="{0:.8f}" lon="{1:.8f}"><ele>{2:.2f}</ele>'
            "<name>WP{3}</name></rtept>".format(lat, lon, alt, wp.seq))
    parts.append("  </rte>")
    parts.append("</gpx>")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(parts))
    return path


def _kml_document(mission, transform, alt_mode, home_z, fmt) -> str:
    alt_tag = ("absolute" if alt_mode in (ALT_AMSL, ALT_ELLIPSOIDAL)
               else "relativeToGround")
    coords = []
    for wp in mission.waypoints:
        lon, lat = _to_wgs84(transform, wp.x, wp.y)
        alt = _altitude(mission, wp.z_amsl, alt_mode, home_z)
        coords.append("{0:.8f},{1:.8f},{2:.2f}".format(lon, lat, alt))

    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<kml xmlns="http://www.opengis.net/kml/2.2">', "<Document>",
             "  <name>GeoCad UAV Toolkit</name>",
             "  <description><![CDATA[{0}]]></description>".format(
                 "<br/>".join(_header_comment(mission, fmt))),
             '  <Style id="wp"><IconStyle><scale>0.7</scale></IconStyle></Style>',
             "  <Placemark><name>Rotta</name>",
             "    <LineString><tessellate>0</tessellate>",
             "      <altitudeMode>{0}</altitudeMode>".format(alt_tag),
             "      <coordinates>{0}</coordinates>".format(" ".join(coords)),
             "    </LineString></Placemark>"]
    for wp, coord in zip(mission.waypoints, coords):
        parts.append(
            "  <Placemark><name>WP{0}</name><styleUrl>#wp</styleUrl>"
            "<description>{1}</description>"
            "<Point><altitudeMode>{2}</altitudeMode>"
            "<coordinates>{3}</coordinates></Point></Placemark>".format(
                wp.seq,
                _xml_escape("{0} | AGL {1:.1f} m | {2:.1f} m/s".format(
                    wp.kind, wp.z_agl, wp.speed_ms)),
                alt_tag, coord))
    parts.append("</Document></kml>")
    return "\n".join(parts)


def _write_kml(mission, path, transform, alt_mode, home_z, fmt, opts):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_kml_document(mission, transform, alt_mode, home_z, fmt))
    return path


def _write_kmz(mission, path, transform, alt_mode, home_z, fmt, opts):
    """Zipped OGC KML. Explicitly NOT a DJI native mission file."""
    doc = _kml_document(mission, transform, alt_mode, home_z, fmt)
    readme = (
        "GeoCad UAV Toolkit - KMZ waypoint generico\n"
        "\n"
        "Questo file NON e' una missione DJI nativa (WPML). Non e' caricabile\n"
        "come wayline in DJI Fly o DJI Pilot. Contiene un documento KML 2.2\n"
        "standard con la rotta e i waypoint, per visualizzazione e scambio.\n"
        "\n" + "\n".join(_header_comment(mission, fmt)) + "\n")
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("doc.kml", doc)
            archive.writestr("LEGGIMI.txt", readme)
    except OSError as exc:
        raise ExportError("cannot write KMZ: {0}".format(exc),
                          user_message="Impossibile scrivere il file KMZ.",
                          hint=str(exc)) from exc
    return path


def _write_geojson(mission, path, transform, alt_mode, home_z, fmt, opts):
    import json                                                 # noqa: PLC0415

    features = []
    line = []
    for wp in mission.waypoints:
        lon, lat = _to_wgs84(transform, wp.x, wp.y)
        alt = _altitude(mission, wp.z_amsl, alt_mode, home_z)
        line.append([round(lon, 8), round(lat, 8), round(alt, 3)])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(lon, 8), round(lat, 8),
                                         round(alt, 3)]},
            "properties": {
                "seq": wp.seq, "sub_mission": wp.sub_mission,
                "strip": wp.strip_index, "type": wp.kind,
                "z_amsl": round(wp.z_amsl, 3), "z_agl": round(wp.z_agl, 3),
                "heading_deg": round(wp.heading_deg, 2),
                "gimbal_pitch_deg": wp.gimbal_pitch_deg,
                "speed_ms": round(wp.speed_ms, 2),
                "dem_gap": wp.dem_gap, "climb_limited": wp.climb_limited,
            }})
    features.append({
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": line},
        "properties": {"role": "flight_route"}})

    payload = {
        "type": "FeatureCollection",
        "metadata": {"generator": "GeoCad UAV Toolkit",
                     "notes": _header_comment(mission, fmt)},
        "features": features,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    return path


def _write_gpkg(mission, path, transform, alt_mode, home_z, fmt, opts):
    """Delegate to the layer factory so GPKG and the QGIS layers agree."""
    from ..io.layer_factory import write_mission_gpkg                # noqa: PLC0415
    return write_mission_gpkg(mission, path, opts.get("crs"))


def _xml_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_WRITERS = {
    "csv_waypoints": _write_csv_waypoints,
    "csv_photos": _write_csv_photos,
    "litchi": _write_litchi,
    "mavlink": _write_mavlink,
    "dji_wpml": _write_wpml,
    "gpx": _write_gpx,
    "kml": _write_kml,
    "kmz": _write_kmz,
    "geojson": _write_geojson,
    "gpkg": _write_gpkg,
}
