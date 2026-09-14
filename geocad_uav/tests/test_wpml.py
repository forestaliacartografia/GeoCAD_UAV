"""
v1.39.0: DJI WPML, checked against the schema it was written from.

This format was refused from the first version of the plugin until now, on
the rule that a format may be called supported only with the official
documentation open and a round trip to show for it. The documentation was
read for this slice -- DJI Cloud API > API Reference > DJI WPML, the pages
"Template.kml", "Waylines.wpml" and "Common Elements", with "Product
Supported" for the enumeration values -- and this suite is the round trip:
write a mission, open the archive, parse both files, and check every element
those pages mark required for a waypoint mission.

It also checks the two refusals that remain, because both are the
documentation's own and not an omission:

* an aircraft whose profile declares no DJI enumeration values, which
  includes every consumer Mini, Air and Phantom -- DJI does not list them as
  able to fly WPML at all;
* an export in orthometric height, which wpml:executeHeightMode has no value
  for.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_wpml.py
"""

import os
import sys
import tempfile
import zipfile
from xml.etree import ElementTree as ET

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import QgsApplication                            # noqa: E402

QGS = QgsApplication([], False)
QGS.initQgis()

from qgis.core import (QgsCoordinateReferenceSystem,            # noqa: E402
                       QgsCoordinateTransform, QgsGeometry, QgsProject)

from geocad_uav.core import z as zc                             # noqa: E402
from geocad_uav.core.errors import (ExportError,                # noqa: E402
                                    UnsupportedFormatError)
from geocad_uav.core.models import AltitudeMode, VerticalDatum  # noqa: E402
from geocad_uav.uav import cameras as cam_lib                   # noqa: E402
from geocad_uav.uav import drones as drone_lib                  # noqa: E402
from geocad_uav.uav import export as ex                         # noqa: E402
from geocad_uav.uav import mission as mi                        # noqa: E402
from geocad_uav.uav import photogrammetry as pg                 # noqa: E402
from geocad_uav.uav import survey as sv                         # noqa: E402

FAILURES = []
SKIPS = []
TMP = tempfile.mkdtemp(prefix="geocad_wpml_")
NS = {"kml": "http://www.opengis.net/kml/2.2",
      "wpml": "http://www.dji.com/wpmz/1.0.2"}


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<54} got={2:<16.10g} exp={3:.10g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_text(label, got, expected):
    ok = got == expected
    print("  [{0}] {1:<44} got={2!r:<26} exp={3!r}".format(
        "ok  " if ok else "FAIL", label, got, expected))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def refuses(call, kind):
    try:
        call()
    except kind:
        return True
    except Exception:                                           # noqa: BLE001
        return False
    return False


# -- a hillside, and a mission over it -------------------------------------
OX, OY, CELL = 500000.0, 5000000.0, 5.0
NX, NY = 120, 100
xs = OX + (np.arange(NX) + 0.5) * CELL
ys = OY - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(xs, ys)
Z = 300.0 + 0.10 * (XX - OX)
TERRAIN = zc.TerrainModel(Z, (OX, CELL, 0.0, OY, 0.0, -CELL), "EPSG:32632",
                          source="DTM sintetico", is_surface_model=False,
                          vertical_datum=VerticalDatum.ORTHOMETRIC_EGM96)
AOI = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 100.0, OY - 300.0, OX + 300.0, OY - 100.0))

CAMERA = cam_lib.load_library()["dji_mavic3e"]
DRONES = drone_lib.load_library()
TRANSFORM = QgsCoordinateTransform(
    QgsCoordinateReferenceSystem("EPSG:32632"),
    QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())


def mission_for(drone_key):
    return mi.build_mission(
        sv.prepare_aoi([AOI])[0][0], TERRAIN,
        mi.MissionParams(camera=CAMERA, drone=DRONES[drone_key],
                         overlap=pg.Overlap(0.8, 0.7), h_agl_m=80.0,
                         altitude_mode=AltitudeMode.TERRAIN,
                         azimuth_strategy=sv.AZIMUTH_MANUAL,
                         manual_azimuth_deg=0.0, v_mission_ms=8.0,
                         compute_footprints=False),
        crs_authid="EPSG:32632")


print("=" * 78)
print("DJI WPML -- lo schema pubblicato, scritto e riletto")
print("=" * 78)

MISSION = mission_for("dji_mavic3e")
print("  missione: {0} waypoint, {1} scatti".format(
    len(MISSION.waypoints), len(MISSION.photos)))

# --------------------------------------------------------------------------
# W1 - the format is offered, and says what it is
# --------------------------------------------------------------------------
print("\n== W1: il formato e' offerto, e dichiara la sua fonte ==")
check_true("dji_wpml e' fra i formati", "dji_wpml" in ex.FORMATS)
fmt = ex.FORMATS["dji_wpml"]
check_text("l'estensione e' .kmz", fmt.extension, ".kmz")
check_true("e' dichiarato verificato", fmt.verified)
print("        fonte: {0}".format(fmt.schema_source))
check_true("...citando le pagine da cui viene",
           "Waylines.wpml" in fmt.schema_source
           and "Template.kml" in fmt.schema_source
           and "wpmz/1.0.2" in fmt.schema_source)
check_true("non e' piu' fra i formati rifiutati",
           "dji_wpml" not in ex.NOT_IMPLEMENTED)

# --------------------------------------------------------------------------
# W2 - the archive has the two files DJI names, where DJI puts them
# --------------------------------------------------------------------------
print("\n== W2: l'archivio ha wpmz/template.kml e wpmz/waylines.wpml ==")
path = os.path.join(TMP, "missione.kmz")
written = ex.write(MISSION, "dji_wpml", path, transform=TRANSFORM,
                   altitude_mode=ex.ALT_RELATIVE_HOME, home_z=300.0,
                   overwrite=True)
check_true("il file e' stato scritto",
           os.path.exists(written) and os.path.getsize(written) > 2000)
check_true("...ed e' uno zip", zipfile.is_zipfile(written))
with zipfile.ZipFile(written) as archive:
    names = archive.namelist()
    template_xml = archive.read("wpmz/template.kml").decode("utf-8")
    waylines_xml = archive.read("wpmz/waylines.wpml").decode("utf-8")
print("        {0}".format(names))
check_true("wpmz/template.kml c'e'", "wpmz/template.kml" in names)
check_true("wpmz/waylines.wpml c'e'", "wpmz/waylines.wpml" in names)
check("...e non c'e' altro", len(names), 2)

TEMPLATE = ET.fromstring(template_xml)
WAYLINES = ET.fromstring(waylines_xml)
for label, root in (("template.kml", TEMPLATE), ("waylines.wpml", WAYLINES)):
    check_text("{0}: la radice e' kml".format(label), root.tag,
               "{http://www.opengis.net/kml/2.2}kml")
check_true("il namespace wpml e' quello ufficiale",
           'xmlns:wpml="http://www.dji.com/wpmz/1.0.2"' in waylines_xml
           and 'xmlns:wpml="http://www.dji.com/wpmz/1.0.2"' in template_xml)

# --------------------------------------------------------------------------
# W3 - missionConfig carries every element the tables mark required
# --------------------------------------------------------------------------
print("\n== W3: missionConfig, elemento per elemento ==")
REQUIRED_CONFIG = ("flyToWaylineMode", "finishAction", "exitOnRCLost",
                   "takeOffSecurityHeight", "globalTransitionalSpeed",
                   "globalRTHHeight")
for label, root in (("template.kml", TEMPLATE), ("waylines.wpml", WAYLINES)):
    config = root.find(".//wpml:missionConfig", NS)
    check_true("{0}: c'e' missionConfig".format(label), config is not None)
    for tag in REQUIRED_CONFIG:
        node = config.find("wpml:" + tag, NS)
        check_true("{0}: {1}".format(label, tag),
                   node is not None and (node.text or "").strip() != "")

config = WAYLINES.find(".//wpml:missionConfig", NS)
check_text("flyToWaylineMode e' un valore dell'enum",
           config.find("wpml:flyToWaylineMode", NS).text, "safely")
check_text("finishAction pure", config.find("wpml:finishAction", NS).text,
           "goHome")
check_text("exitOnRCLost pure",
           config.find("wpml:exitOnRCLost", NS).text, "executeLostAction")
check_true("...e con executeLostAction l'azione di fuori controllo e' data",
           config.find("wpml:executeRCLostAction", NS).text
           in ("goBack", "landing", "hover"))
check("takeOffSecurityHeight e' nel campo ammesso dal telecomando",
      float(config.find("wpml:takeOffSecurityHeight", NS).text), 20.0, 1e-9)
check_true("...cioe' fra 1.2 e 1500 m",
           1.2 <= float(config.find("wpml:takeOffSecurityHeight", NS).text)
           <= 1500.0)
check_true("globalTransitionalSpeed e' in (0, 15]",
           0.0 < float(config.find("wpml:globalTransitionalSpeed", NS).text)
           <= 15.0)
check_true("globalRTHHeight e' in [2, 1500]",
           2.0 <= float(config.find("wpml:globalRTHHeight", NS).text)
           <= 1500.0)

# --------------------------------------------------------------------------
# W4 - the aircraft and the payload are named by DJI's own numbers
# --------------------------------------------------------------------------
print("\n== W4: il velivolo e il payload, per enumerativo DJI ==")
declared = dict(DRONES["dji_mavic3e"].wpml)
print("        profilo: {0}".format(
    {k: v for k, v in declared.items() if k != "source"}))
check_text("droneEnumValue viene dal profilo",
           WAYLINES.find(".//wpml:droneEnumValue", NS).text,
           str(declared["drone_enum"]))
check_text("droneSubEnumValue pure",
           WAYLINES.find(".//wpml:droneSubEnumValue", NS).text,
           str(declared["drone_sub_enum"]))
check_text("payloadEnumValue pure",
           WAYLINES.find(".//wpml:payloadEnumValue", NS).text,
           str(declared["payload_enum"]))
check_text("payloadPositionIndex pure",
           WAYLINES.find(".//wpml:payloadPositionIndex", NS).text,
           str(declared["payload_position"]))
check("il Mavic 3 Enterprise e' il tipo 77 della tabella DJI",
      declared["drone_enum"], 77)
check("...con la camera M3E, tipo 66", declared["payload_enum"], 66)
check_true("il profilo cita la fonte degli enumerativi",
           "Cloud API" in declared.get("source", ""))

# --------------------------------------------------------------------------
# W5 - the Folder and the waypoints of waylines.wpml
# --------------------------------------------------------------------------
print("\n== W5: la rotta eseguibile ==")
folder = WAYLINES.find(".//kml:Folder", NS)
for tag in ("templateId", "waylineId", "autoFlightSpeed",
            "executeHeightMode"):
    node = folder.find("wpml:" + tag, NS)
    check_true("Folder: {0}".format(tag),
               node is not None and (node.text or "").strip() != "")
check_text("executeHeightMode e' un valore dell'enum",
           folder.find("wpml:executeHeightMode", NS).text,
           "relativeToStartPoint")
check_true("autoFlightSpeed e' positiva",
           float(folder.find("wpml:autoFlightSpeed", NS).text) > 0.0)

placemarks = WAYLINES.findall(".//kml:Placemark", NS)
check("un Placemark per waypoint", len(placemarks), len(MISSION.waypoints))
indexes = [int(p.find("wpml:index", NS).text) for p in placemarks]
check_true("gli indici partono da 0 e crescono di uno",
           indexes == list(range(len(placemarks))))
for tag in ("index", "executeHeight", "waypointSpeed", "waypointHeadingParam",
            "waypointTurnParam"):
    check_true("ogni waypoint ha {0}".format(tag),
               all(p.find("wpml:" + tag, NS) is not None for p in placemarks))
check_true("ogni waypoint ha un punto con longitudine,latitudine",
           all(len((p.find(".//kml:coordinates", NS).text or "").split(",")) == 2
               for p in placemarks))

lons, lats = [], []
for placemark in placemarks:
    lon, lat = placemark.find(".//kml:coordinates", NS).text.split(",")
    lons.append(float(lon))
    lats.append(float(lat))
print("        lon {0:.5f}..{1:.5f}  lat {2:.5f}..{3:.5f}".format(
    min(lons), max(lons), min(lats), max(lats)))
check_true("le longitudini sono in [-180, 180]",
           all(-180.0 <= v <= 180.0 for v in lons))
check_true("le latitudini sono in [-90, 90]",
           all(-90.0 <= v <= 90.0 for v in lats))
check_true("...e non sono le coordinate metriche non proiettate",
           max(abs(v) for v in lons) < 180.0)

heading = placemarks[0].find("wpml:waypointHeadingParam", NS)
check_text("il modo di prua e' dell'enum",
           heading.find("wpml:waypointHeadingMode", NS).text, "followWayline")
check_text("...e il verso di rotazione pure",
           heading.find("wpml:waypointHeadingPathMode", NS).text,
           "followBadArc")
turn = placemarks[0].find("wpml:waypointTurnParam", NS)
check_text("il modo di virata e' dell'enum",
           turn.find("wpml:waypointTurnMode", NS).text,
           "toPointAndStopWithDiscontinuityCurvature")
check_true("...e la distanza di smorzamento c'e'",
           turn.find("wpml:waypointTurnDampingDist", NS) is not None)

# --------------------------------------------------------------------------
# W6 - the terrain-following heights reach the file, and the shots
# --------------------------------------------------------------------------
print("\n== W6: le quote del terrain following, e gli scatti ==")
heights = [float(p.find("wpml:executeHeight", NS).text) for p in placemarks]
expected = [wp.z_amsl - 300.0 for wp in MISSION.waypoints]
print("        executeHeight {0:.2f}..{1:.2f} m sul decollo".format(
    min(heights), max(heights)))
check("ogni quota e' quella della rotta, relativa al decollo",
      max(abs(a - b) for a, b in zip(heights, expected)), 0.0, 0.005)
check_true("...e non sono tutte uguali: il terreno si muove",
           max(heights) - min(heights) > 10.0)
agl = [wp.z_agl for wp in MISSION.waypoints]
check_true("l'AGL della missione e' invece costante",
           max(agl) - min(agl) < 0.01)

groups = WAYLINES.findall(".//wpml:actionGroup", NS)
photo_waypoints = sum(1 for wp in MISSION.waypoints if wp.is_photo)
check("un gruppo di azioni per ogni waypoint di scatto",
      len(groups), photo_waypoints)
first_group = groups[0]
funcs = [n.text for n in first_group.findall(".//wpml:actionActuatorFunc", NS)]
print("        azioni: {0}".format(funcs))
check_true("il gruppo punta il gimbal e scatta", funcs == ["gimbalRotate",
                                                           "takePhoto"])
check_text("il gruppo e' sequenziale",
           first_group.find("wpml:actionGroupMode", NS).text, "sequence")
check_text("...e scatta all'arrivo sul punto",
           first_group.find(".//wpml:actionTriggerType", NS).text,
           "reachPoint")
starts = [int(g.find("wpml:actionGroupStartIndex", NS).text) for g in groups]
ends = [int(g.find("wpml:actionGroupEndIndex", NS).text) for g in groups]
check_true("start e end coincidono: il gruppo vale su quel punto",
           starts == ends)
check_true("...e ogni indice e' un waypoint che esiste",
           all(0 <= i < len(placemarks) for i in starts))
pitches = [float(n.text) for n in
           WAYLINES.findall(".//wpml:gimbalPitchRotateAngle", NS)]
check_true("il gimbal e' puntato a nadir", all(abs(p + 90.0) < 1e-6
                                               for p in pitches))

# --------------------------------------------------------------------------
# W7 - template.kml is the same mission, as an editable template
# --------------------------------------------------------------------------
print("\n== W7: template.kml, la stessa missione da rieditare ==")
t_folder = TEMPLATE.find(".//kml:Folder", NS)
check_text("il tipo di template e' waypoint",
           t_folder.find("wpml:templateType", NS).text, "waypoint")
check_text("...e il suo id lega i due file",
           t_folder.find("wpml:templateId", NS).text,
           folder.find("wpml:templateId", NS).text)
for tag in ("waylineCoordinateSysParam", "autoFlightSpeed", "gimbalPitchMode",
            "globalWaypointTurnMode", "globalUseStraightLine",
            "globalHeight"):
    check_true("template: {0}".format(tag),
               t_folder.find("wpml:" + tag, NS) is not None)
check_text("il sistema di coordinate e' WGS84",
           t_folder.find(".//wpml:coordinateMode", NS).text, "WGS84")
t_placemarks = TEMPLATE.findall(".//kml:Placemark", NS)
check("stessi waypoint del file eseguibile", len(t_placemarks),
      len(placemarks))
for tag in ("index", "ellipsoidHeight", "height", "useGlobalHeight",
            "useGlobalSpeed", "useGlobalHeadingParam", "useGlobalTurnParam",
            "gimbalPitchAngle"):
    check_true("ogni waypoint del template ha {0}".format(tag),
               all(p.find("wpml:" + tag, NS) is not None
                   for p in t_placemarks))
t_heights = [float(p.find("wpml:height", NS).text) for p in t_placemarks]
check("le quote del template sono quelle della rotta",
      max(abs(a - b) for a, b in zip(t_heights, heights)), 0.0, 1e-9)
check_true("createTime e updateTime sono timestamp in millisecondi",
           int(TEMPLATE.find(".//wpml:createTime", NS).text) > 1_600_000_000_000)

# --------------------------------------------------------------------------
# W8 - the two refusals, both from the documentation
# --------------------------------------------------------------------------
print("\n== W8: cosa resta rifiutato, e perche' ==")
for key in ("dji_mini1", "dji_mini2", "dji_mini3", "dji_mini3pro",
            "dji_p4p", "dji_air3", "autel_evo2", "generic_multirotor"):
    other = mission_for(key)
    check_true("{0}: niente WPML, non lo dichiara".format(key),
               refuses(lambda m=other, k=key: ex.write(
                   m, "dji_wpml", os.path.join(TMP, k + ".kmz"),
                   transform=TRANSFORM, altitude_mode=ex.ALT_RELATIVE_HOME,
                   home_z=300.0, overwrite=True), UnsupportedFormatError))

try:
    ex.write(mission_for("dji_mini2"), "dji_wpml",
             os.path.join(TMP, "mini_reason.kmz"), transform=TRANSFORM,
             altitude_mode=ex.ALT_RELATIVE_HOME, home_z=300.0, overwrite=True)
    reason = ""
except UnsupportedFormatError as exc:
    reason = exc.formatted()
print("        {0}".format(reason))
check_true("il rifiuto dice quale profilo e cosa manca",
           "Mini 2" in reason and "wpml" in reason.lower())
check_true("...e dice che le serie consumer non volano WPML",
           "Mini" in reason and "Phantom" in reason)

check_true("quota ortometrica: rifiutata, non rietichettata",
           refuses(lambda: ex.write(
               MISSION, "dji_wpml", os.path.join(TMP, "amsl.kmz"),
               transform=TRANSFORM, altitude_mode=ex.ALT_AMSL,
               overwrite=True), ExportError))
try:
    ex.write(MISSION, "dji_wpml", os.path.join(TMP, "amsl2.kmz"),
             transform=TRANSFORM, altitude_mode=ex.ALT_AMSL, overwrite=True)
    amsl_reason = ""
except ExportError as exc:
    amsl_reason = exc.formatted()
print("        {0}".format(amsl_reason))
check_true("...spiegando che l'enum non ha un valore ortometrico",
           "relativeToStartPoint" in amsl_reason and "WGS84" in amsl_reason)

# The ellipsoidal export is the other accepted reference.
ell = ex.write(MISSION, "dji_wpml", os.path.join(TMP, "ellissoidica.kmz"),
               transform=TRANSFORM, altitude_mode=ex.ALT_ELLIPSOIDAL,
               overwrite=True)
with zipfile.ZipFile(ell) as archive:
    ell_root = ET.fromstring(
        archive.read("wpmz/waylines.wpml").decode("utf-8"))
check_text("in quota ellissoidica il modo diventa WGS84",
           ell_root.find(".//wpml:executeHeightMode", NS).text, "WGS84")

print("\n" + "=" * 78)
QgsProject.instance().removeAllMapLayers()
QGS.exitQgis()
if SKIPS:
    print("SKIPPED ({0}):".format(len(SKIPS)))
    for name, why in SKIPS:
        print("   - {0}: {1}".format(name, why))
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for name in FAILURES:
        print("   - {0}".format(name))
    sys.exit(1)
print("ALL CHECKS PASSED")
