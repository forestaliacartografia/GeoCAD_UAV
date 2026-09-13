"""
v1.8.0 (S4): planting schemes measured on the ground.

The scheme is stated in real distances and the generator asks the DEM, at
every step, how much of that distance is map. On a plane of known dip the
answer is a closed form -- D_plan = D_real cos(theta) -- and that is what is
checked here, to 1e-9, not against a number copied from a previous run.

NEEDS QGIS (and the Processing framework, for gdal:contour). Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_reforestation_s4.py
"""

import gc
import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsGeometry, QgsProject, QgsRasterLayer,
                       QgsWkbTypes)

QGS = QgsApplication([], False)
QGS.initQgis()

# After QgsApplication, never before: qgis.analysis pulled in first crashes
# the interpreter, and the bundled `processing` package is not on sys.path
# outside the QGIS GUI.
from osgeo import gdal, osr                                     # noqa: E402
from qgis.analysis import QgsNativeAlgorithms                   # noqa: E402

_plugins_dir = os.path.join(QgsApplication.prefixPath(), "python", "plugins")
if os.path.isdir(_plugins_dir) and _plugins_dir not in sys.path:
    sys.path.append(_plugins_dir)

from processing.core.Processing import Processing               # noqa: E402

Processing.initialize()
NATIVE = QgsNativeAlgorithms()
QgsApplication.processingRegistry().addProvider(NATIVE)

from geocad_uav.core import grid as grid_mod                    # noqa: E402
from geocad_uav.core import z as z_mod                          # noqa: E402
from geocad_uav.core.errors import (EmptyAoiError,              # noqa: E402
                                    InvalidInputError, RasterError)
from geocad_uav.core.planar import azimuth_of                   # noqa: E402
from geocad_uav.forest.reforestation import area as ar          # noqa: E402
from geocad_uav.forest.reforestation import constraints as cs   # noqa: E402
from geocad_uav.forest.reforestation import curves as cv        # noqa: E402
from geocad_uav.forest.reforestation import orient as orn       # noqa: E402
from geocad_uav.forest.reforestation import spacing as sp       # noqa: E402
from geocad_uav.forest.reforestation import terrain as tr       # noqa: E402

FAILURES = []
SKIPS = []
CRS = QgsCoordinateReferenceSystem("EPSG:32632")
TMP = tempfile.mkdtemp(prefix="geocad_s4_")
OX, OY = 500000.0, 5000000.0

TOL = 1e-9


def check(label, got, expected, tol=TOL):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<58} got={2:<18.12g} exp={3:.12g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def check_raises(label, exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        print("  [ok  ] {0}".format(label))
        return
    except Exception as exc:                                    # noqa: BLE001
        print("  [FAIL] {0} (raised {1})".format(label, type(exc).__name__))
        FAILURES.append(label)
        return
    print("  [FAIL] {0} (did not raise)".format(label))
    FAILURES.append(label)


def point(x, y):
    return QgsGeometry.fromWkt("POINT({0} {1})".format(x, y))


# ------------------------------------------------------------- fixtures ---
# A plane dipping 30 degrees to the East: slope is 30 everywhere, aspect is
# 90 everywhere, and both closed forms below are exact on it.
SLOPE_DEG = 30.0
GRADE = math.tan(math.radians(SLOPE_DEG))
CELL = 2.0
NX, NY = 400, 400
DEM_TOP = OY
DEM_GT = (OX, CELL, 0.0, DEM_TOP, 0.0, -CELL)
_xs = OX + (np.arange(NX) + 0.5) * CELL
_ys = DEM_TOP - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(_xs, _ys)
Z = 500.0 - GRADE * (XX - OX)
TERRAIN = tr.TerrainAnalysis.from_array(Z, DEM_GT, CRS.authid(),
                                        source="piano 30 deg")

DEM_PATH = os.path.join(TMP, "piano30.tif")
gdal.UseExceptions()
_ds = gdal.GetDriverByName("GTiff").Create(DEM_PATH, NX, NY, 1,
                                           gdal.GDT_Float64)
_ds.SetGeoTransform(DEM_GT)
_srs = osr.SpatialReference()
_srs.ImportFromEPSG(32632)
_ds.SetProjection(_srs.ExportToWkt())
_ds.GetRasterBand(1).WriteArray(Z)
_ds.FlushCache()
_ds = None
DEM_LAYER = QgsRasterLayer(DEM_PATH, "piano 30", "gdal")
QgsProject.instance().addMapLayer(DEM_LAYER)

# Well inside the DEM: Horn pads the outermost ring, which halves the
# gradient there, so the edge cells are not on the plane.
AOI = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 120.0, DEM_TOP - 420.0, OX + 300.0, DEM_TOP - 240.0))

print("=" * 78)
print("S4 -- spacing on the slope, contour rows, morphological orientation")
print("=" * 78)
print("piano: pendenza {0:.1f} deg, cella {1:.1f} m, DEM {2}x{3}".format(
    SLOPE_DEG, CELL, NX, NY))

# --------------------------------------------------------------------------
# T0 - the ground really is the plane the closed forms assume
# --------------------------------------------------------------------------
print("\n== T0: il piano di prova ==")
_slope, _aspect = TERRAIN.grids()
check("la pendenza interna e' 30 gradi",
      float(np.nanmedian(_slope[2:-2, 2:-2])), SLOPE_DEG, TOL)
check("l'esposizione interna e' 90 gradi (verso Est)",
      float(np.nanmedian(_aspect[2:-2, 2:-2])), 90.0, TOL)
_probe_x, _probe_y = OX + 200.0, DEM_TOP - 300.0
_cached = TERRAIN.slope_aspect_at(_probe_x, _probe_y)
_frozen = z_mod.sample_slope_aspect(TERRAIN.model, _probe_x, _probe_y)
check("la lettura in cache e' quella del motore congelato",
      float(_cached[0]), float(_frozen[0]), 0.0)
check("...esposizione compresa", float(_cached[1]), float(_frozen[1]), 0.0)

# --------------------------------------------------------------------------
# S4-1 - 3 x 3 m of ground on a 30 degree slope is 2.598 m of map
# --------------------------------------------------------------------------
print("\n== S4-1: sesto 3 x 3 m reale su pendenza 30 gradi ==")
D_REAL = 3.0
D_PLAN = D_REAL * math.cos(math.radians(SLOPE_DEG))
print("        atteso D_plan = {0:.3f} * cos({1:.0f} deg) = {2:.12f} m".format(
    D_REAL, SLOPE_DEG, D_PLAN))

stepper = sp.SlopeStepper(TERRAIN, sp.STEP_MAX_SLOPE)
check("il passo corretto in un punto", stepper.plan_step(_probe_x, _probe_y,
                                                         D_REAL), D_PLAN, TOL)
check("...e l'inverso torna alla distanza reale",
      stepper.real_step(_probe_x, _probe_y, D_PLAN), D_REAL, TOL)

spec = sp.SlopeSpacing(plant_distance_m=D_REAL, row_distance_m=D_REAL,
                       row_azimuth_deg=90.0, pattern=grid_mod.PATTERN_SQUARE)
result = sp.generate(AOI, spec, terrain=TERRAIN)
print("        {0:,} piante su {1} file".format(result.count, result.n_rows))
check_true("il sesto ha prodotto qualcosa", result.count > 100)
check("nessun passo e' stato calcolato senza pendenza",
      result.steps_without_slope, 0)
check_true("...quindi nessun avviso", not result.warnings)

gaps = []
by_row = {}
for plant in result.plants:
    by_row.setdefault(plant.row_id, []).append(plant)
for plants in by_row.values():
    plants.sort(key=lambda p: p.seq_in_row)
    for first, second in zip(plants, plants[1:]):
        gaps.append(math.hypot(second.x - first.x, second.y - first.y))
gaps = np.asarray(gaps, dtype=float)
print("        distanza planimetrica: min {0:.12f}  max {1:.12f}".format(
    gaps.min(), gaps.max()))
check("ogni passo sulla fila e' D_real * cos(theta)", float(gaps.min()),
      D_PLAN, TOL)
check("...ogni singolo passo, non solo la media", float(gaps.max()), D_PLAN,
      TOL)
check("il sesto e' uniforme sul piano", float(gaps.max() - gaps.min()), 0.0,
      TOL)
check("la media coincide", result.mean_plan_spacing(), D_PLAN, TOL)
check("misurata sul terreno torna a 3 m", result.mean_real_spacing(), D_REAL,
      1e-6)

row_starts = sorted((min(v, key=lambda p: p.seq_in_row)
                     for v in by_row.values()), key=lambda p: p.y)
row_gaps = [math.hypot(b.x - a.x, b.y - a.y)
            for a, b in zip(row_starts, row_starts[1:])]
print("        distanza fra le file: min {0:.12f}  max {1:.12f}".format(
    min(row_gaps), max(row_gaps)))
check("anche l'interfila e' corretta", min(row_gaps), D_PLAN, TOL)
check("...uniformemente", max(row_gaps), D_PLAN, TOL)

print("\n-- senza DEM non si corregge nulla, e lo si dice --")
flat = sp.generate(AOI, spec, terrain=None)
flat_gaps = []
flat_rows = {}
for plant in flat.plants:
    flat_rows.setdefault(plant.row_id, []).append(plant)
for plants in flat_rows.values():
    plants.sort(key=lambda p: p.seq_in_row)
    for first, second in zip(plants, plants[1:]):
        flat_gaps.append(math.hypot(second.x - first.x, second.y - first.y))
check("senza DEM il passo resta quello richiesto", min(flat_gaps), D_REAL,
      TOL)
check_true("...e l'assenza di correzione e' dichiarata",
           any("DEM" in text for text in flat.warnings))
check_true("...e sul piano il sesto planimetrico pianta di piu'",
           flat.count < result.count)

print("\n-- la correzione direzionale non accorcia una fila di livello --")
contour_spec = sp.SlopeSpacing(
    plant_distance_m=D_REAL, row_distance_m=D_REAL, row_azimuth_deg=0.0,
    pattern=grid_mod.PATTERN_SQUARE, step_mode=sp.STEP_DIRECTIONAL)
contour_res = sp.generate(AOI, contour_spec, terrain=TERRAIN)
check("file Nord-Sud, cioe' di livello: passo non corretto",
      contour_res.mean_plan_spacing(), D_REAL, TOL)
downhill = sp.SlopeStepper(TERRAIN, sp.STEP_DIRECTIONAL)
check("lungo la massima pendenza la correzione torna piena",
      downhill.plan_step(_probe_x, _probe_y, D_REAL, 1.0, 0.0), D_PLAN, TOL)
check("...e lungo la curva di livello e' nulla",
      downhill.plan_step(_probe_x, _probe_y, D_REAL, 0.0, 1.0), D_REAL, TOL)

# --------------------------------------------------------------------------
# S4-2 - every PointZ falls inside the useful surface
# --------------------------------------------------------------------------
print("\n== S4-2: ogni pianta PointZ cade dentro la superficie utile ==")
IRREGULAR = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {3},{4} {5},{6} {7},{8} {9},{10} {11},{0} {1}))"
    .format(OX + 130.0, DEM_TOP - 410.0,
            OX + 290.0, DEM_TOP - 395.0,
            OX + 250.0, DEM_TOP - 320.0,
            OX + 295.0, DEM_TOP - 255.0,
            OX + 180.0, DEM_TOP - 250.0,
            OX + 145.0, DEM_TOP - 330.0))
check_true("il poligono di prova e' valido e concavo",
           IRREGULAR.isGeosValid()
           and IRREGULAR.area() < IRREGULAR.convexHull().area())

project = ar.ReforestationArea(IRREGULAR, CRS.authid(), label="Lotto S4")
project.add_exclusion(
    QgsGeometry.fromWkt("POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))"
                        .format(OX + 200.0, DEM_TOP - 360.0,
                                OX + 240.0, DEM_TOP - 320.0)),
    label="affioramento")
tracks = cs.ConstraintSet({"strada": 6.0})
tracks.add_geometry("strada", QgsGeometry.fromWkt(
    "LINESTRING({0} {1},{2} {3})".format(OX + 140.0, DEM_TOP - 300.0,
                                         OX + 290.0, DEM_TOP - 300.0)))
tracks.apply_to(project)
useful = project.utile()
print("        lorda {0:.4f} ha, esclusa {1:.4f} ha, utile {2:.4f} ha".format(
    project.lorda_ha, project.esclusa_ha, project.utile_ha))
check("lorda meno esclusa fa l'utile",
      project.lorda_m2 - project.esclusa_m2, project.utile_m2, 1e-6)

s2_spec = sp.SlopeSpacing(plant_distance_m=4.0, row_distance_m=5.0,
                          row_azimuth_deg=90.0,
                          pattern=grid_mod.PATTERN_QUINCUNX, margin_m=2.0)
s2 = sp.generate(useful, s2_spec, terrain=TERRAIN)
print("        {0:,} piante su {1} file".format(s2.count, s2.n_rows))
check_true("il sesto ha prodotto qualcosa sull'area irregolare", s2.count > 50)

features = sp.point_features(s2)
check("una feature per pianta", len(features), s2.count)
non_pointz = [f for f in features
              if f.geometry().wkbType() != QgsWkbTypes.PointZ]
check("ogni feature e' un PointZ", len(non_pointz), 0)
missing_z = [p for p in s2.plants if p.z is None]
check("ogni pianta ha la sua quota dal DEM", len(missing_z), 0)

outside = [f for f in features if not useful.intersects(f.geometry())]
print("        fuori dalla superficie utile: {0}".format(len(outside)))
check("nessuna pianta cade fuori dalla superficie utile", len(outside), 0)
eroded = useful.buffer(-2.0, 12)
outside_margin = [f for f in features if not eroded.intersects(f.geometry())]
check("...ne' dentro il margine dal bordo", len(outside_margin), 0)
in_exclusion = [f for f in features
                if project.esclusa().intersects(f.geometry())]
check("nessuna pianta finisce in un'area esclusa o in una fascia",
      len(in_exclusion), 0)

z_from_dem = [abs(f.geometry().constGet().z()
                  - float(TERRAIN.elevation_at(f.geometry().constGet().x(),
                                               f.geometry().constGet().y())))
              for f in features[:200]]
check("la quota della feature e' quella che il DEM da' in quel punto",
      max(z_from_dem), 0.0, TOL)

# --------------------------------------------------------------------------
# S4-3 - morphological orientation on a plane dipping East
# --------------------------------------------------------------------------
print("\n== S4-3: orientamento morfologico su falda inclinata verso Est ==")
descent = orn.steepest_descent_azimuth(TERRAIN)
print("        massima pendenza verso {0:.12f} deg".format(descent))
check("la discesa massima punta a Est", descent, 90.0, TOL)
contour_azimuth = orn.morphological_azimuth(TERRAIN,
                                            alignment=orn.ALIGN_CONTOUR)
slope_azimuth = orn.morphological_azimuth(TERRAIN, alignment=orn.ALIGN_SLOPE)
print("        file a girapoggio {0:.12f} deg, a rittochino {1:.12f} deg"
      .format(contour_azimuth, slope_azimuth))
check("le file di livello corrono Nord-Sud: azimut 0", contour_azimuth, 0.0,
      TOL)
check("le file di massima pendenza corrono Est-Ovest: azimut 90",
      slope_azimuth, 90.0, TOL)
check_true("entrambe le risposte sono esattamente 0 o 90",
           contour_azimuth in (0.0, 90.0) and slope_azimuth in (0.0, 90.0))
check("un'orientazione e' modulo 180: 200 gradi sono 20",
      orn.fold(200.0), 20.0, TOL)
check("...e la perpendicolare di 10 e' 100", orn.perpendicular(10.0), 100.0,
      TOL)

print("\n-- terreno piano: nessuna direzione, e lo si ammette --")
FLAT = tr.TerrainAnalysis.from_array(np.full((40, 40), 250.0),
                                     (OX, CELL, 0.0, DEM_TOP, 0.0, -CELL),
                                     CRS.authid())
check_true("su un piano orizzontale non c'e' massima pendenza",
           orn.steepest_descent_azimuth(FLAT) is None)
check_true("...e l'orientamento morfologico non inventa un nord",
           orn.morphological_azimuth(FLAT) is None)

print("\n-- gli altri due criteri --")
check("il criterio geometrico segue il lato lungo della particella",
      orn.geometric_azimuth(AOI), 90.0, TOL)
check("il criterio manuale restituisce quello che gli si da'",
      orn.resolve(orn.MODE_MANUAL, manual_deg=37.5), 37.5, TOL)
check("il morfologico su terreno piano ripiega sulla geometria",
      orn.resolve(orn.MODE_MORPHOLOGIC, terrain=FLAT, geometry=AOI,
                  manual_deg=12.0), 90.0, TOL)
check("...e senza nemmeno quella, sul valore manuale",
      orn.resolve(orn.MODE_MORPHOLOGIC, terrain=FLAT, manual_deg=12.0), 12.0,
      TOL)
check("il morfologico sul piano inclinato usa il terreno",
      orn.resolve(orn.MODE_MORPHOLOGIC, terrain=TERRAIN, geometry=AOI,
                  manual_deg=12.0), 0.0, TOL)
check_raises("un criterio inesistente e' un errore", InvalidInputError,
             orn.resolve, "a occhio")
check_raises("...e cosi' un allineamento inesistente", InvalidInputError,
             orn.morphological_azimuth, TERRAIN, None, "diagonale")

# --------------------------------------------------------------------------
# T1 - the row azimuth convention, pinned against core.grid
# --------------------------------------------------------------------------
print("\n== T1: convenzione dell'azimut, verificata sul motore congelato ==")
for row_azimuth in (0.0, 30.0, 90.0, 135.0):
    spec_az = sp.SlopeSpacing(plant_distance_m=10.0, row_distance_m=25.0,
                              row_azimuth_deg=row_azimuth)
    lattice = grid_mod.generate_grid((0.0, 0.0, 200.0, 200.0),
                                     spec_az.as_grid_spec())
    line = lattice.row_lines()[0]
    bearing = azimuth_of(line[1][0] - line[0][0], line[1][1] - line[0][1])
    check("file a {0:g} deg: il reticolo congelato le disegna li'".format(
        row_azimuth), orn.fold(bearing), orn.fold(row_azimuth), 1e-9)
    check("...e l'azimut del reticolo e' 90 gradi meno",
          spec_az.lattice_azimuth(), (row_azimuth - 90.0) % 360.0, TOL)

s4_east = sp.generate(AOI, sp.SlopeSpacing(plant_distance_m=10.0,
                                           row_distance_m=10.0,
                                           row_azimuth_deg=90.0),
                      terrain=TERRAIN)
east_row = sorted([p for p in s4_east.plants if p.row_id == 0],
                  key=lambda p: p.seq_in_row)
check("con azimut 90 le file corrono davvero da Ovest a Est",
      azimuth_of(east_row[1].x - east_row[0].x,
                 east_row[1].y - east_row[0].y), 90.0, 1e-9)

# --------------------------------------------------------------------------
# T2 - every pattern, including the two this module adds
# --------------------------------------------------------------------------
print("\n== T2: tutti gli schemi, compresi irregolare e personalizzato ==")
check("gli schemi sono i cinque del motore piu' tre",
      len(sp.ALL_PATTERNS), len(grid_mod.ALL_PATTERNS) + 3)
check_true("...e i cinque del motore ci sono tutti",
           set(grid_mod.ALL_PATTERNS) <= set(sp.ALL_PATTERNS))
check_true("ognuno ha un'etichetta in italiano",
           all(sp.PATTERN_LABELS.get(key) for key in sp.ALL_PATTERNS))

LATTICE_PATTERNS = tuple(p for p in sp.ALL_PATTERNS
                         if p != sp.PATTERN_CONTOUR)
for pattern in LATTICE_PATTERNS:
    extra = {}
    if pattern == sp.PATTERN_IRREGULAR:
        extra = {"jitter_m": 0.8, "seed": 7}
    if pattern == sp.PATTERN_CUSTOM:
        extra = {"custom_offsets": (0.0, 0.5)}
    spec_p = sp.SlopeSpacing(plant_distance_m=6.0, row_distance_m=6.0,
                             row_azimuth_deg=90.0, pattern=pattern, **extra)
    out = sp.generate(AOI, spec_p, terrain=TERRAIN)
    print("        {0:<12} {1:>5} piante, {2:>3} file".format(
        pattern, out.count, out.n_rows))
    check_true("{0}: pianta qualcosa".format(pattern), out.count > 0)
    check_true("{0}: nessuna pianta fuori area".format(pattern),
               all(AOI.intersects(point(p.x, p.y)) for p in out.plants[:80]))
    check_true("{0}: ogni pianta ha una quota".format(pattern),
               all(p.z is not None for p in out.plants[:80]))

print("\n-- l'esagonale stringe le file, come da motore --")
# Generating the contour scheme here would produce straight rows and call
# them contours, which nothing downstream could catch: the lattice generator
# refuses instead, and points at the module that does it properly.
contour_spec = sp.SlopeSpacing(plant_distance_m=6.0, row_distance_m=6.0,
                               pattern=sp.PATTERN_CONTOUR)
try:
    sp.generate(AOI, contour_spec, terrain=TERRAIN)
    check_true("il reticolo rifiuta lo schema su curve", False)
except InvalidInputError as exc:
    print("        rifiuto: {0}".format(exc.user_message))
    check_true("il reticolo rifiuta lo schema su curve",
               "curve di livello" in exc.user_message)

hexspec = sp.SlopeSpacing(plant_distance_m=6.0, row_distance_m=6.0,
                          row_azimuth_deg=90.0, pattern=grid_mod.PATTERN_HEX)
check("l'interfila esagonale e' dx * sqrt(3)/2",
      hexspec.effective_real_spacing[1], 6.0 * math.sqrt(3.0) / 2.0, TOL)
check_true("...e lo sfalsamento alterna", hexspec.row_offset_alternates)

print("\n-- l'irregolare sposta le piante, ma non le sovrappone --")
irr = sp.SlopeSpacing(plant_distance_m=6.0, row_distance_m=6.0,
                      pattern=sp.PATTERN_IRREGULAR, jitter_m=50.0)
check("l'irregolarita' e' limitata a una frazione del passo",
      irr.effective_jitter(), sp.MAX_JITTER_FRACTION * 6.0, TOL)
reg = sp.SlopeSpacing(plant_distance_m=6.0, row_distance_m=6.0,
                      pattern=grid_mod.PATTERN_RECT, jitter_m=50.0)
check("uno schema regolare non ne applica nessuna", reg.effective_jitter(),
      0.0, TOL)
same_seed = [sp.generate(AOI, sp.SlopeSpacing(
    plant_distance_m=6.0, row_distance_m=6.0, row_azimuth_deg=90.0,
    pattern=sp.PATTERN_IRREGULAR, jitter_m=1.0, seed=11),
    terrain=TERRAIN) for _ in range(2)]
check_true("lo stesso seme da' lo stesso impianto: e' riproducibile",
           [(p.x, p.y) for p in same_seed[0].plants]
           == [(p.x, p.y) for p in same_seed[1].plants])

print("\n-- il personalizzato sfalsa le file come gli si dice --")
custom = sp.SlopeSpacing(plant_distance_m=6.0, row_distance_m=6.0,
                         pattern=sp.PATTERN_CUSTOM,
                         custom_offsets=(0.0, 0.25, 0.5))
check("la prima fila non e' sfalsata", custom.row_offset_fraction(0), 0.0, TOL)
check("la seconda di un quarto", custom.row_offset_fraction(1), 0.25, TOL)
check("la terza di mezzo passo", custom.row_offset_fraction(2), 0.5, TOL)
check("e la quarta ricomincia", custom.row_offset_fraction(3), 0.0, TOL)
check_raises("uno schema personalizzato senza sfalsamenti non esiste",
             InvalidInputError, sp.SlopeSpacing, 6.0, 6.0,
             pattern=sp.PATTERN_CUSTOM)

print("\n-- e i parametri impossibili si fermano subito --")
check_raises("distanza nulla", InvalidInputError, sp.SlopeSpacing, 0.0, 3.0)
check_raises("distanza non finita", InvalidInputError, sp.SlopeSpacing,
             float("inf"), 3.0)
check_raises("schema sconosciuto", InvalidInputError, sp.SlopeSpacing, 3.0,
             3.0, pattern="a caso")
check_raises("correzione sconosciuta", InvalidInputError, sp.SlopeSpacing,
             3.0, 3.0, step_mode="a occhio")
check_raises("margine negativo", InvalidInputError, sp.SlopeSpacing, 3.0, 3.0,
             margin_m=-1.0)
check_raises("area vuota", EmptyAoiError, sp.generate, QgsGeometry(), spec)
check_raises("margine che divora l'area", EmptyAoiError, sp.generate, AOI,
             sp.SlopeSpacing(plant_distance_m=3.0, row_distance_m=3.0,
                             margin_m=500.0))

# --------------------------------------------------------------------------
# T3 - contour rows, from gdal:contour
# --------------------------------------------------------------------------
print("\n== T3: file su curve di livello estratte da gdal:contour ==")
INTERVAL = 20.0
contour_layer = cv.extract_contours(DEM_LAYER, interval_m=INTERVAL)
check_true("gdal:contour ha prodotto un layer valido",
           contour_layer is not None and contour_layer.isValid())
check_true("...con il campo della quota",
           contour_layer.fields().indexOf(cv.ELEVATION_FIELD) >= 0)
elevations = sorted({round(float(f[cv.ELEVATION_FIELD]), 6)
                     for f in contour_layer.getFeatures()})
print("        {0} curve, quote da {1:.1f} a {2:.1f} m".format(
    contour_layer.featureCount(), elevations[0], elevations[-1]))
check_true("le quote sono multipli dell'equidistanza",
           all(abs(e / INTERVAL - round(e / INTERVAL)) < 1e-9
               for e in elevations))
steps = [b - a for a, b in zip(elevations, elevations[1:])]
check("...e distano esattamente l'equidistanza", max(steps), INTERVAL, TOL)

rows = cv.contour_rows(contour_layer, clip_geometry=AOI, min_length_m=5.0)
print("        {0} file dentro l'area, sviluppo {1:,.1f} m".format(
    len(rows), sum(r.length_m for r in rows)))
check_true("le curve tagliate sull'area sono almeno due", len(rows) >= 2)
check_true("ogni fila sta dentro l'area",
           all(AOI.buffer(1e-6, 4).contains(r.geometry) for r in rows))
check_true("le curve di un piano inclinato sono rette Nord-Sud",
           all(abs(r.geometry.boundingBox().width()) < 1e-6 for r in rows))

contour_plants = cv.plant_along_contours(rows, TERRAIN, D_REAL, stagger=False)
print("        {0:,} piante sulle curve".format(len(contour_plants)))
check_true("le curve sono state piantate", len(contour_plants) > 20)
first_row = [p for p in contour_plants if p.row_id == 0]
first_row.sort(key=lambda p: p.seq_in_row)
row_steps = [math.hypot(b.x - a.x, b.y - a.y)
             for a, b in zip(first_row, first_row[1:])]
print("        passo lungo la curva: min {0:.12f} max {1:.12f}".format(
    min(row_steps), max(row_steps)))
check("su una curva di livello il passo reale e' anche planimetrico",
      min(row_steps), D_REAL, TOL)
check("...ogni passo", max(row_steps), D_REAL, TOL)
check_true("ogni pianta sulla curva ha la sua quota",
           all(p.z is not None for p in contour_plants))
check_true("...e la quota e' quella della curva",
           max(abs(p.z - rows[p.row_id].elevation_m)
               for p in contour_plants) < 0.5)
check_true("il prospetto delle curve e' leggibile",
           any("Equidistanza" in line
               for line in cv.describe(rows, INTERVAL)))

print("\n-- sfalsamento e filtri --")
staggered = cv.plant_along_contours(rows, TERRAIN, D_REAL, stagger=True)
even = [p for p in staggered if p.row_id == 0]
odd = [p for p in staggered if p.row_id == 1]
check_true("lo sfalsamento sposta le file dispari di mezzo passo",
           len(odd) > 0 and abs(min(p.y for p in odd)
                                - min(q.y for q in even)) > 1e-6)
clipped = cv.plant_along_contours(
    rows, TERRAIN, D_REAL, stagger=False,
    inside=lambda x, y: x < OX + 200.0)
check_true("un filtro di appartenenza toglie piante, non le sposta",
           0 < len(clipped) < len(contour_plants))
check_raises("un DEM assente non si contorna", RasterError,
             cv.extract_contours, None)
check_raises("...ne' con equidistanza nulla", InvalidInputError,
             cv.extract_contours, DEM_LAYER, 0.0)
check_raises("una distanza nulla fra le piante e' un errore",
             InvalidInputError, cv.plant_along_contours, rows, TERRAIN, 0.0)

# --------------------------------------------------------------------------
# T4 - the readouts
# --------------------------------------------------------------------------
print("\n== T4: prospetti ==")
check_true("il sesto si racconta in italiano",
           any("Sulla fila" in line for line in spec.describe()))
check_true("...dicendo quale correzione applica",
           any("Pendenza massima" in line for line in spec.describe()))
check_true("l'orientamento pure",
           any("Azimut file" in line
               for line in orn.describe(orn.MODE_MORPHOLOGIC, 0.0)))
check_true("la densita' si legge sul risultato",
           result.density_per_ha() > 0.0)
check("...ed e' le piante sull'area utile in ettari",
      result.density_per_ha(),
      result.count / (result.usable_area_m2 / 10_000.0), 1e-9)


print("\n" + "=" * 78)
# gdal:contour hands back a layer that was never added to the project, so
# removeAllMapLayers() does not own it. Left to the interpreter it is
# collected *after* exitQgis(), which is a use-after-teardown and segfaults
# with no traceback -- the checks above having already all passed.
contour_layer = None
rows = []
staggered = clipped = contour_plants = first_row = None
gc.collect()
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
