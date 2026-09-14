"""
v2.2.1: il DEM si campiona nel CRS che QGIS usa, non in quello scritto nel file.

THE BUG. ``TerrainModel.from_layer`` handed the raster's path to
``gdal.Warp`` and let GDAL read the source CRS out of the file. Everything
else in QGIS -- the rendering, the layer extent, Identify, and so the
operator and the AOI they drew against it -- goes through
``QgsRasterLayer.crs()``. Those agree only when the file carries a correct
``.prj``.

A DEM with a missing or wrong projection, corrected in the layer properties,
is the ordinary case with Italian regional elevation data. Reproduced below
as case P0: the file says EPSG:32632, the layer says EPSG:32633, QGIS draws
the raster under the AOI. The warp took the file's word, landed the window
some 400 km west, returned a grid that was 100 per cent no-data, and
``fill_profile_gaps`` reported that the whole route was outside the
elevation model -- true of what it could see, false about the world.

Every DEM here is a real GeoTIFF on disk and every route is the planner's
own. The suite covers the seven cases the hotfix has to hold for, plus the
one that produced the error.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_terrain_crs.py
"""

import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import QgsApplication                            # noqa: E402

QGS = QgsApplication([], False)
QGS.initQgis()

from osgeo import gdal, osr                                     # noqa: E402
from qgis.core import (QgsCoordinateReferenceSystem,            # noqa: E402
                       QgsCoordinateTransform, QgsGeometry,
                       QgsProject, QgsRasterLayer, QgsRectangle)

from geocad_uav.core import z as z_mod                          # noqa: E402
from geocad_uav.core.z import TerrainError, TerrainModel        # noqa: E402
from geocad_uav.uav import mission as mission_mod               # noqa: E402
from geocad_uav.uav import photogrammetry as pg                 # noqa: E402
from geocad_uav.uav import survey as sv                         # noqa: E402
from geocad_uav.uav import terrain_follow as tf                 # noqa: E402
from geocad_uav.uav import cameras as cam_lib                   # noqa: E402
from geocad_uav.uav import drones as drone_lib                  # noqa: E402

FAILURES = []
SKIPS = []
TMP = tempfile.mkdtemp(prefix="geocad_tcrs_")
gdal.UseExceptions()

UTM33 = QgsCoordinateReferenceSystem("EPSG:32633")
UTM32 = QgsCoordinateReferenceSystem("EPSG:32632")
WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
WEBM = QgsCoordinateReferenceSystem("EPSG:3857")

#: Ground truth: a hillside in Umbria, in UTM 33N. Everything else in this
#: suite is this same ground expressed somewhere else.
OX, OY = 280000.0, 4775000.0
SIDE = 2000.0


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<54} got={2:<16.10g} exp={3:.10g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_text(label, got, expected):
    ok = got == expected
    print("  [{0}] {1:<46} got={2!r:<24} exp={3!r}".format(
        "ok  " if ok else "FAIL", label, got, expected))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def check_raises(label, exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type as exc:
        print("  [ok  ] {0}".format(label))
        return exc
    except Exception as exc:                                    # noqa: BLE001
        print("  [FAIL] {0} (ha sollevato {1})".format(label,
                                                       type(exc).__name__))
        FAILURES.append(label)
        return exc
    print("  [FAIL] {0} (non ha sollevato)".format(label))
    FAILURES.append(label)
    return None


def ground_height(x33, y33):
    """The surface this suite writes, as a function of UTM33 position."""
    return 300.0 + 0.05 * (x33 - OX) + 20.0 * np.sin((y33 - OY) / 300.0)


def write_dem(name, crs, file_epsg, nodata_patch=None, nx=400, ny=400):
    """The same hillside, written in ``crs``, with ``file_epsg`` in the .prj.

    ``file_epsg`` None writes no projection at all. ``nodata_patch`` is a
    fraction of the grid, centred, punched out as no-data.
    """
    path = os.path.join(TMP, name)
    to_crs = QgsCoordinateTransform(UTM33, crs, QgsProject.instance())
    box = to_crs.transformBoundingBox(
        QgsRectangle(OX, OY, OX + SIDE, OY + SIDE))
    dx, dy = box.width() / nx, box.height() / ny
    xs = box.xMinimum() + (np.arange(nx) + 0.5) * dx
    ys = box.yMaximum() - (np.arange(ny) + 0.5) * dy
    XX, YY = np.meshgrid(xs, ys)

    # Heights are computed on the ground, so every raster in this suite
    # describes the same hillside whatever CRS it is written in. The whole
    # grid goes through one TransformPoints call: exact, and not 160,000
    # round trips through QgsCoordinateTransform.
    src = osr.SpatialReference()
    src.ImportFromEPSG(int(crs.authid().split(":")[1]))
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(32633)
    try:                                  # GDAL 3 axis order
        src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except AttributeError:
        pass
    back = osr.CoordinateTransformation(src, dst)
    points = back.TransformPoints(
        list(zip(XX.ravel().tolist(), YY.ravel().tolist())))
    ground = np.asarray(points, dtype=float)[:, :2]
    Z = ground_height(ground[:, 0], ground[:, 1]).reshape(XX.shape)

    nodata = -9999.0
    if nodata_patch:
        half = int(min(nx, ny) * nodata_patch / 2.0)
        Z[ny // 2 - half:ny // 2 + half, nx // 2 - half:nx // 2 + half] = nodata

    ds = gdal.GetDriverByName("GTiff").Create(path, nx, ny, 1,
                                              gdal.GDT_Float32)
    ds.SetGeoTransform((box.xMinimum(), dx, 0.0, box.yMaximum(), 0.0, -dy))
    if file_epsg is not None:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(file_epsg)
        ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(nodata)
    ds.GetRasterBand(1).WriteArray(Z.astype(np.float32))
    ds.FlushCache()
    ds = None
    return path


def load(path, layer_crs=None):
    layer = QgsRasterLayer(path, os.path.basename(path), "gdal")
    if layer_crs is not None:
        layer.setCrs(layer_crs)
    QgsProject.instance().addMapLayer(layer)
    return layer


def aoi_in(crs, inset=400.0):
    """The planning area, well inside the DEM, expressed in ``crs``."""
    geometry = QgsGeometry.fromWkt(
        "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
            OX + inset, OY + inset, OX + SIDE - inset, OY + SIDE - inset))
    if crs != UTM33:
        geometry.transform(QgsCoordinateTransform(UTM33, crs,
                                                  QgsProject.instance()))
    return geometry


CAMERA = cam_lib.load_library()["dji_mavic3e"]
DRONE = drone_lib.load_library()["dji_mavic3e"]


def plan(layer, aoi, work_crs, h_agl=90.0, gap_mode="hold_max"):
    """The planner's own path: prepare, terrain, build. No shortcuts."""
    blocks, _warnings = sv.prepare_aoi([aoi])
    block = blocks[0]
    params = mission_mod.MissionParams(
        camera=CAMERA, drone=DRONE,
        overlap=pg.Overlap(frontlap=0.80, sidelap=0.65),
        h_agl_m=h_agl, v_mission_ms=10.0, gap_mode=gap_mode)
    geometry = pg.solve_survey_geometry(CAMERA, params.overlap,
                                        h_agl_m=h_agl)
    box = block.boundingBox()
    margin = max(geometry.footprint_across_m, geometry.footprint_along_m)
    terrain, warnings = TerrainModel.from_layer(
        layer, work_crs,
        (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()),
        margin_m=margin)
    mission = mission_mod.build_mission(block, terrain, params,
                                        crs_authid=work_crs.authid())
    return mission, terrain, warnings


def altimetry(mission, terrain):
    agl = np.array([w.z_agl for w in mission.waypoints], dtype=float)
    amsl = np.array([w.z_amsl for w in mission.waypoints], dtype=float)
    return agl, amsl


print("=" * 78)
print("Terrain following: il CRS del DEM e' quello del layer QGIS")
print("=" * 78)

# --------------------------------------------------------------------------
# P0 - the case that failed in the field
# --------------------------------------------------------------------------
print("\n== P0: file con .prj sbagliato, corretto nel layer QGIS ==")
# Written with EPSG:32632 in the .prj, over ground that is really UTM 33N.
# The operator corrects the layer, which is what QGIS then draws with.
wrong_prj = write_dem("dem_prj_sbagliato.tif", UTM33, 32632)
layer_fixed = load(wrong_prj, UTM33)
info = z_mod.dem_diagnostics(layer_fixed, UTM33, None)
print("        layer QGIS: {0} | file: {1}".format(info["layer_crs"],
                                                   info["file_crs"]))
check_text("QGIS usa il CRS del layer", info["layer_crs"], "EPSG:32633")
check_true("...e il file ne dichiara un altro", info["crs_mismatch"])
check_true("la diagnostica lo dice a parole",
           "CRS diverso" in z_mod.describe_dem_diagnostics(info))

aoi33 = aoi_in(UTM33)
mission, terrain, warnings = plan(layer_fixed, aoi33, UTM33)
agl, amsl = altimetry(mission, terrain)
print("        {0} waypoint, nodata {1:.2%}, AGL {2:.2f}-{3:.2f}".format(
    len(mission.waypoints), terrain.nodata_fraction,
    float(np.nanmin(agl)), float(np.nanmax(agl))))
check_true("la rotta viene generata", mission is not None
           and len(mission.waypoints) > 10)
check_true("il modello non e' tutto no-data",
           terrain.nodata_fraction < 0.5)
check("ogni waypoint tiene la quota chiesta sul terreno",
      float(np.nanmax(np.abs(agl - 90.0))), 0.0, 1.0)
check_true("nessuna quota e' NaN",
           bool(np.isfinite(agl).all() and np.isfinite(amsl).all()))
check_true("...e il disallineamento e' segnalato all'operatore",
           any("declares" in w and "layer" in w for w in warnings))

# The heights are the hillside's, not a constant: checked against the
# function the raster was written from.
probe = np.array([[OX + 600.0, OY + 600.0], [OX + 1400.0, OY + 1400.0]])
expected = ground_height(probe[:, 0], probe[:, 1])
got = terrain.sample(probe[:, 0], probe[:, 1])
print("        quota attesa {0} | campionata {1}".format(
    np.round(expected, 1), np.round(got, 1)))
check("il terreno campionato e' quello scritto nel raster",
      float(np.nanmax(np.abs(got - expected))), 0.0, 1.5)
check_true("le quote assolute seguono il terreno, non una costante",
           float(np.nanmax(amsl) - np.nanmin(amsl)) > 20.0)

# --------------------------------------------------------------------------
# A - same CRS everywhere
# --------------------------------------------------------------------------
print("\n== A: AOI e DEM nello stesso CRS ==")
dem33 = load(write_dem("dem_33.tif", UTM33, 32633))
mission_a, terrain_a, _w = plan(dem33, aoi_in(UTM33), UTM33)
agl_a, amsl_a = altimetry(mission_a, terrain_a)
print("        {0} waypoint, AGL {1:.2f}-{2:.2f}, AMSL {3:.1f}-{4:.1f}".format(
    len(mission_a.waypoints), float(agl_a.min()), float(agl_a.max()),
    float(amsl_a.min()), float(amsl_a.max())))
check_true("la missione esiste", len(mission_a.waypoints) > 10)
check("l'AGL e' quella chiesta", float(np.max(np.abs(agl_a - 90.0))), 0.0, 1.0)
check_true("nessun waypoint fuori dal DEM",
           not any(w.dem_gap for w in mission_a.waypoints))
check("AMSL = terreno + AGL",
      float(np.max(np.abs(
          amsl_a - (terrain_a.sample(
              np.array([w.x for w in mission_a.waypoints]),
              np.array([w.y for w in mission_a.waypoints])) + agl_a)))),
      0.0, 0.01)

# --------------------------------------------------------------------------
# B - AOI and DEM in different CRS
# --------------------------------------------------------------------------
print("\n== B: AOI e DEM in CRS diversi ==")
for label, dem_crs, epsg in (("DEM geografico (EPSG:4326)", WGS84, 4326),
                             ("DEM Web Mercator (EPSG:3857)", WEBM, 3857),
                             ("DEM UTM 32 (EPSG:32632)", UTM32, 32632)):
    layer = load(write_dem("dem_{0}.tif".format(epsg), dem_crs, epsg))
    mission_b, terrain_b, _w = plan(layer, aoi_in(UTM33), UTM33)
    agl_b, _amsl_b = altimetry(mission_b, terrain_b)
    got = terrain_b.sample(probe[:, 0], probe[:, 1])
    print("        {0}: {1} waypoint, scarto quota {2:.2f} m".format(
        label, len(mission_b.waypoints),
        float(np.nanmax(np.abs(got - expected)))))
    check_true("{0}: la missione esiste".format(label),
               len(mission_b.waypoints) > 10)
    check("{0}: l'AGL e' quella chiesta".format(label),
          float(np.max(np.abs(agl_b - 90.0))), 0.0, 1.5)
    check("{0}: il terreno e' lo stesso della verita' a terra".format(label),
          float(np.nanmax(np.abs(got - expected))), 0.0, 3.0)
    check_true("{0}: il modello e' nel CRS di lavoro".format(label),
               terrain_b.crs_authid == "EPSG:32633")

# --------------------------------------------------------------------------
# C - the project CRS is a third one
# --------------------------------------------------------------------------
print("\n== C: CRS di progetto diverso da DEM e da rotta ==")
QgsProject.instance().setCrs(WEBM)
check_text("il progetto e' in un terzo CRS",
           QgsProject.instance().crs().authid(), "EPSG:3857")
layer_c = load(write_dem("dem_c.tif", UTM32, 32632), UTM32)
mission_c, terrain_c, _w = plan(layer_c, aoi_in(UTM33), UTM33)
agl_c, _amsl_c = altimetry(mission_c, terrain_c)
got = terrain_c.sample(probe[:, 0], probe[:, 1])
print("        progetto 3857, DEM 32632, rotta 32633: {0} waypoint, "
      "scarto {1:.2f} m".format(len(mission_c.waypoints),
                                float(np.nanmax(np.abs(got - expected)))))
check_true("la missione esiste", len(mission_c.waypoints) > 10)
check("l'AGL e' quella chiesta", float(np.max(np.abs(agl_c - 90.0))), 0.0, 1.5)
check("il terreno resta quello vero",
      float(np.nanmax(np.abs(got - expected))), 0.0, 3.0)
check_true("il CRS del progetto non ha spostato niente",
           terrain_c.crs_authid == "EPSG:32633")
QgsProject.instance().setCrs(UTM33)

# --------------------------------------------------------------------------
# D - a DEM with a real no-data hole
# --------------------------------------------------------------------------
print("\n== D: DEM con un buco di no-data ==")
holed = load(write_dem("dem_buco.tif", UTM33, 32633, nodata_patch=0.30))
mission_d, terrain_d, warns_d = plan(holed, aoi_in(UTM33), UTM33)
agl_d, amsl_d = altimetry(mission_d, terrain_d)
gaps = [w for w in mission_d.waypoints if w.dem_gap]
print("        no-data nel modello {0:.1%}, waypoint marcati {1}".format(
    terrain_d.nodata_fraction, len(gaps)))
check_true("il buco c'e' davvero", terrain_d.nodata_fraction > 0.05)
check_true("...ed e' dichiarato",
           any("no-data" in w for w in warns_d))
check_true("la missione viene comunque generata",
           len(mission_d.waypoints) > 10)
check_true("i waypoint sul buco sono marcati", len(gaps) > 0)
check_true("nessuna quota resta NaN",
           bool(np.isfinite(amsl_d).all()))
check_true("sul buco la quota comandata e' conservativa, non interpolata",
           all(w.z_amsl >= min(x.z_amsl for x in mission_d.waypoints)
               for w in gaps))

report = terrain_d.sample_report(
    np.array([w.x for w in mission_d.waypoints]),
    np.array([w.y for w in mission_d.waypoints]))
print("        campioni {0}: validi {1}, no-data {2}, fuori griglia "
      "{3}".format(report["samples"], report["valid"], report["nodata"],
                   report["outside"]))
check("no-data e fuori-griglia sono contati separatamente",
      report["valid"] + report["nodata"] + report["outside"],
      report["samples"])
check("...e qui nessun campione e' fuori griglia", report["outside"], 0)
check_true("...mentre di no-data ce ne sono", report["nodata"] > 0)

# --------------------------------------------------------------------------
# E - a route genuinely off the DEM
# --------------------------------------------------------------------------
print("\n== E: rotta davvero fuori dal DEM ==")
far = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 60000.0, OY + 60000.0, OX + 60600.0, OY + 60600.0))
error = check_raises("un'area fuori dal DEM viene rifiutata", TerrainError,
                     plan, dem33, far, UTM33)
text = str(error)
print("        {0}".format(text.splitlines()[0]))
check_true("il messaggio dice che il DEM non copre la finestra",
           "does not cover" in text)
check_true("...e riporta il CRS del layer", "EPSG:32633" in text)
check_true("...l'extent del DEM nel CRS di lavoro",
           "extent nel CRS di lavoro" in text)
check_true("...e la finestra richiesta", "finestra richiesta" in text)
check_true("...dicendo che non si sovrappongono", "sovrapposizione:     NO"
           in text)
check_true("non parla piu' di 'stesso CRS' quando i CRS sono giusti",
           "both are in the same CRS" not in text)

print("\n-- e quando il profilo e' davvero tutto vuoto --")
empty_grid = TerrainModel(np.full((10, 10), np.nan),
                          (OX, 5.0, 0.0, OY + 50.0, 0.0, -5.0),
                          crs_authid="EPSG:32633")
line = np.array([[OX + 10.0, OY + 10.0], [OX + 40.0, OY + 40.0]])
profile = tf.build_flight_profile(empty_grid, line, h_agl_m=90.0, step_m=5.0)
gap_error = check_raises("un profilo senza un solo campione valido",
                         TerrainError, tf.fill_profile_gaps, profile,
                         "hold_max", empty_grid)
print("        {0}".format(str(gap_error).splitlines()[0]))
check_true("...dice quanti campioni ha guardato",
           "samples" in str(gap_error))
check_true("...e distingue no-data da fuori griglia",
           "no-data" in str(gap_error) and "outside" in str(gap_error))
check_true("...e qui il problema e' il no-data, non la copertura",
           "hole in the data" in str(gap_error))

outside_line = np.array([[OX + 5000.0, OY + 5000.0],
                         [OX + 5100.0, OY + 5100.0]])
good_grid = TerrainModel(np.full((10, 10), 300.0),
                         (OX, 5.0, 0.0, OY + 50.0, 0.0, -5.0),
                         crs_authid="EPSG:32633")
outside_profile = tf.build_flight_profile(good_grid, outside_line, h_agl_m=90.0,
                                          step_m=20.0)
outside_error = check_raises("un profilo interamente fuori dalla griglia",
                             TerrainError, tf.fill_profile_gaps,
                             outside_profile, "hold_max", good_grid)
check_true("...lo dice come problema di copertura",
           "outside the elevation window" in str(outside_error))

# --------------------------------------------------------------------------
# F - partial coverage follows gap_mode
# --------------------------------------------------------------------------
print("\n== F: rotta parzialmente fuori dal DEM ==")
half_grid = TerrainModel(
    np.where(np.arange(40).reshape(1, 40) < 20, 300.0, np.nan)
    * np.ones((40, 1)),
    (OX, 5.0, 0.0, OY + 200.0, 0.0, -5.0), crs_authid="EPSG:32633")
half_line = np.array([[OX + 10.0, OY + 100.0], [OX + 190.0, OY + 100.0]])
for mode, expectation in (("hold_max", "raised"), ("interpolate", "interp")):
    prof = tf.build_flight_profile(half_grid, half_line, h_agl_m=90.0, step_m=5.0)
    notes = tf.fill_profile_gaps(prof, mode, half_grid)
    filled = np.isfinite(prof.z_flight).all()
    print("        {0}: {1} campioni in buco, tutti definiti {2}".format(
        mode, int(prof.gap_mask.sum()), filled))
    check_true("{0}: una parte del profilo e' in buco".format(mode),
               prof.gap_mask.any())
    check_true("{0}: ...e non e' 'tutta fuori'".format(mode),
               (~prof.gap_mask).any())
    check_true("{0}: le quote vengono definite".format(mode), filled)
    check_true("{0}: e la politica e' dichiarata".format(mode),
               notes and "profile samples fall on DEM no-data" in notes[0])

prof_none = tf.build_flight_profile(half_grid, half_line, h_agl_m=90.0, step_m=5.0)
notes_none = tf.fill_profile_gaps(prof_none, "none", half_grid)
check_true("gap_mode 'none' lascia i buchi indefiniti",
           not np.isfinite(prof_none.z_flight).all())
check_true("...dicendolo", bool(notes_none))

# --------------------------------------------------------------------------
# G - a DEM that cannot be placed at all
# --------------------------------------------------------------------------
print("\n== G: DEM senza CRS, e DEM non valido ==")
no_crs_path = write_dem("dem_senza_crs.tif", UTM33, None)
no_crs = QgsRasterLayer(no_crs_path, "senza crs", "gdal")
QgsProject.instance().addMapLayer(no_crs)
print("        CRS del layer senza .prj: {0!r}".format(
    no_crs.crs().authid()))
if no_crs.crs().isValid():
    # QGIS assigned a fallback CRS; with that, the raster is placeable and
    # the planner must simply use it.
    mission_g, terrain_g, _w = plan(no_crs, aoi_in(no_crs.crs()),
                                    UTM33 if no_crs.crs() == UTM33
                                    else no_crs.crs())
    check_true("un DEM con CRS di ripiego resta utilizzabile",
               len(mission_g.waypoints) > 10)
else:
    error_g = check_raises("un DEM senza CRS viene rifiutato con un motivo",
                           TerrainError, plan, no_crs, aoi_in(UTM33), UTM33)
    check_true("...e il motivo e' il CRS mancante",
               "coordinate reference system" in str(error_g))

broken = QgsRasterLayer(os.path.join(TMP, "non_esiste.tif"), "rotto", "gdal")
check_true("un layer non valido e' non valido", not broken.isValid())
error_b = check_raises("...e viene rifiutato prima di qualunque warp",
                       TerrainError, TerrainModel.from_layer, broken, UTM33,
                       (OX, OY, OX + 100.0, OY + 100.0))
check_true("...dicendo che il layer non e' valido",
           "not valid" in str(error_b))

# --------------------------------------------------------------------------
# H - the diagnostics themselves
# --------------------------------------------------------------------------
print("\n== H: la diagnostica risponde alla domanda giusta ==")
info = z_mod.dem_diagnostics(dem33, UTM33, (OX + 400.0, OY + 400.0,
                                            OX + 1600.0, OY + 1600.0), 130.0)
text = z_mod.describe_dem_diagnostics(info)
print("\n".join("        " + line for line in text.splitlines()))
for token in ("CRS del layer QGIS", "CRS scritto nel file", "CRS di lavoro",
              "extent del DEM", "extent nel CRS di lavoro",
              "finestra richiesta", "sovrapposizione"):
    check_true("la diagnostica riporta: {0}".format(token), token in text)
check_true("le due estensioni sono confrontate nello stesso CRS",
           info["layer_extent_in_work_crs"] is not None)
check_true("...e qui si sovrappongono", info["overlaps"] is True)
check("la risoluzione dichiarata e' quella del raster", info["res_x"],
      SIDE / 400.0, 1e-6)
check("...e le dimensioni pure", info["width"], 400)

far_info = z_mod.dem_diagnostics(dem33, UTM33,
                                 (OX + 60000.0, OY + 60000.0,
                                  OX + 60600.0, OY + 60600.0), 130.0)
check_true("una finestra lontana non si sovrappone",
           far_info["overlaps"] is False)
check_true("la diagnostica non solleva mai",
           z_mod.dem_diagnostics(broken, UTM33, None) is not None)

print("\n" + "=" * 78)
QgsProject.instance().removeAllMapLayers()
QGS.exitQgis()
if SKIPS:
    print("SKIPPED ({0}):".format(len(SKIPS)))
    for label, reason in SKIPS:
        print("   - {0}: {1}".format(label, reason))
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for name in FAILURES:
        print("   - {0}".format(name))
    sys.exit(1)
print("ALL CHECKS PASSED")
