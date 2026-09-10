"""
v1.6.0 (S3): the reforestation area, its constraints and its terrain.

M01 ``forest.reforestation.area``       -- lorda, esclusa, utile
M02 ``forest.reforestation.terrain``    -- quota, pendenza, esposizione, mask
M03 ``forest.reforestation.constraints`` -- fasce di rispetto parametriche

Every areal number is checked against GEOS on the real geometry or against a
closed form written out here (the inscribed cap of a buffer, the arctangent of
a known grade), never against a constant copied from a previous run.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_reforestation_m01_m03.py
"""

import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsFeature, QgsGeometry, QgsProject, QgsRasterLayer,
                       QgsVectorLayer)

QGS = QgsApplication([], False)
QGS.initQgis()

from osgeo import gdal, osr                                     # noqa: E402

from geocad_uav.core.errors import (EmptyAoiError,              # noqa: E402
                                    GeometryError, InvalidInputError,
                                    RasterError)
from geocad_uav.core.grid import GridSpec                       # noqa: E402
from geocad_uav.forest import planting as pl                    # noqa: E402
from geocad_uav.forest.planting import TopographicFilter        # noqa: E402
from geocad_uav.forest.reforestation import area as ar          # noqa: E402
from geocad_uav.forest.reforestation import constraints as cs   # noqa: E402
from geocad_uav.forest.reforestation import terrain as tr       # noqa: E402

FAILURES = []
SKIPS = []
CRS = QgsCoordinateReferenceSystem("EPSG:32632")
TMP = tempfile.mkdtemp(prefix="geocad_reforest_")
OX, OY = 500000.0, 5000000.0

#: Areal tolerance: square metres and hectares, per the slice brief.
TOL_AREA = 1e-6
#: Vertex tolerance: buffer widths and coordinates.
TOL_VERTEX = 1e-9


def check(label, got, expected, tol=TOL_VERTEX):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<56} got={2:<16.10g} exp={3:.10g}".format(
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


def rect(x, y, width, height):
    return QgsGeometry.fromWkt(
        "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
            x, y, x + width, y + height))


def polygon_layer(name, geometries):
    layer = QgsVectorLayer("Polygon?crs=EPSG:32632", name, "memory")
    for geometry in geometries:
        feature = QgsFeature()
        feature.setGeometry(geometry)
        layer.dataProvider().addFeature(feature)
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return layer


def line_layer(name, wkts):
    layer = QgsVectorLayer("LineString?crs=EPSG:32632", name, "memory")
    for wkt in wkts:
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        layer.dataProvider().addFeature(feature)
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return layer


print("=" * 78)
print("S3 -- M01 area, M02 terrain, M03 constraints")
print("=" * 78)

# --------------------------------------------------------------------------
# A1 - the three surfaces: 12.40 - 1.85 = 10.55 ha
# --------------------------------------------------------------------------
# 400 x 310 m is 124 000 m2 = 12.40 ha; 185 x 100 m is 18 500 m2 = 1.85 ha.
GROSS = rect(OX, OY, 400.0, 310.0)
EXCLUDED = rect(OX + 50.0, OY + 50.0, 185.0, 100.0)

print("\n== A1: superficie utile = lorda - esclusa ==")
a1 = ar.ReforestationArea(GROSS, CRS.authid(), label="Lotto di prova")
check("la lorda e' quella del poligono", a1.lorda_m2, 124_000.0, TOL_AREA)
check("...cioe' 12.40 ha", a1.lorda_ha, 12.40, TOL_AREA)
check_true("l'esclusione viene accettata",
           a1.add_exclusion(EXCLUDED, label="affioramento roccioso"))
check("la superficie esclusa e' 1.85 ha", a1.esclusa_ha, 1.85, TOL_AREA)
print("        {0:.6f} - {1:.6f} = {2:.6f} ha".format(
    a1.lorda_ha, a1.esclusa_ha, a1.utile_ha))
check("utile() restituisce 10.55 ha", a1.utile().area() / ar.M2_PER_HA, 10.55,
      TOL_AREA)
check("...e la proprieta' concorda", a1.utile_ha, 10.55, TOL_AREA)
check("in metri quadri sono 105 500", a1.utile_m2, 105_500.0, TOL_AREA)
check("lorda - esclusa fa esattamente l'utile",
      a1.lorda_m2 - a1.esclusa_m2, a1.utile_m2, TOL_AREA)
check_true("utile() e' una geometria, non un numero",
           hasattr(a1.utile(), "area") and not a1.utile().isEmpty())
check_true("...ed e' contenuta nella lorda", GROSS.contains(a1.utile()))
check_true("...e non tocca piu' l'esclusione",
           a1.utile().intersection(EXCLUDED).area() <= TOL_AREA)
check("il perimetro e' quello del rettangolo", a1.perimeter_m, 1420.0,
      TOL_VERTEX)
print("        " + " | ".join(a1.summary()[2:5]))

# --------------------------------------------------------------------------
# A2 - an exclusion that pokes outside only counts where it overlaps
# --------------------------------------------------------------------------
print("\n== A2: si sottrae solo quello che cade dentro ==")
a2 = ar.ReforestationArea(GROSS)
straddling = rect(OX - 100.0, OY + 100.0, 200.0, 50.0)   # half outside
check_true("l'esclusione a cavallo del bordo viene accettata",
           a2.add_exclusion(straddling, label="a cavallo"))
check("solo la meta' interna viene sottratta", a2.esclusa_m2, 100.0 * 50.0,
      TOL_AREA)
check("l'utile perde solo quella", a2.utile_m2, 124_000.0 - 5_000.0, TOL_AREA)
check_true("un'esclusione tutta fuori non viene registrata",
           not a2.add_exclusion(rect(OX + 5_000.0, OY, 10.0, 10.0), "lontana"))
check("...e non lascia traccia", len(a2.exclusions), 1)

# --------------------------------------------------------------------------
# A3 - overlapping exclusions are counted once
# --------------------------------------------------------------------------
print("\n== A3: due esclusioni sovrapposte non si contano due volte ==")
a3 = ar.ReforestationArea(GROSS)
a3.add_exclusion(rect(OX + 50.0, OY + 50.0, 100.0, 100.0), "prima")
a3.add_exclusion(rect(OX + 100.0, OY + 50.0, 100.0, 100.0), "seconda")
declared = sum(m2 for _label, m2 in a3.exclusion_breakdown())
print("        dichiarate {0:,.0f} m2, sottratte {1:,.0f} m2".format(
    declared, a3.esclusa_m2))
check("ciascuna dichiara i suoi 10 000 m2", declared, 20_000.0, TOL_AREA)
check("l'unione ne sottrae 15 000", a3.esclusa_m2, 15_000.0, TOL_AREA)
check("l'utile segue l'unione", a3.utile_m2, 124_000.0 - 15_000.0, TOL_AREA)
check("il dettaglio elenca entrambe", len(a3.exclusion_breakdown()), 2)
a3.clear_exclusions()
check("azzerare le esclusioni riporta l'utile alla lorda", a3.utile_m2,
      a3.lorda_m2, TOL_AREA)

# --------------------------------------------------------------------------
# A4 - holes, invalid rings, empty results
# --------------------------------------------------------------------------
print("\n== A4: buchi, anelli non validi, area azzerata ==")
donut = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}),"
    "({4} {5},{6} {5},{6} {7},{4} {7},{4} {5}))".format(
        OX, OY, OX + 400.0, OY + 310.0,
        OX + 100.0, OY + 100.0, OX + 200.0, OY + 200.0))
a4 = ar.ReforestationArea(donut)
check("un anello interno e' gia' fuori dalla lorda", a4.lorda_m2,
      124_000.0 - 10_000.0, TOL_AREA)
check("...senza bisogno di dichiararlo come esclusione", a4.esclusa_m2, 0.0,
      TOL_AREA)

bowtie = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {3},{2} {1},{0} {3},{0} {1}))".format(
        OX, OY, OX + 100.0, OY + 100.0))
check_true("il poligono a farfalla e' effettivamente non valido",
           not bowtie.isGeosValid())
a4b = ar.ReforestationArea(bowtie)
check_true("...e viene riparato invece che rifiutato",
           a4b.geometry.isGeosValid() and a4b.lorda_m2 > 0.0)

check_raises("una geometria vuota non e' un'area", EmptyAoiError,
             ar.ReforestationArea, QgsGeometry())
check_raises("...e nemmeno None", EmptyAoiError, ar.ReforestationArea, None)

a4c = ar.ReforestationArea(rect(OX, OY, 100.0, 100.0))
a4c.add_exclusion(rect(OX - 10.0, OY - 10.0, 200.0, 200.0), "tutto")
check("un'esclusione totale azzera l'utile", a4c.utile_m2, 0.0, TOL_AREA)
check_true("...e l'area lo dichiara", a4c.is_empty)

# --------------------------------------------------------------------------
# A5 - from a layer, selection first
# --------------------------------------------------------------------------
print("\n== A5: l'area arriva da un layer, la selezione ha la precedenza ==")
parcels = polygon_layer("particelle", [
    rect(OX, OY, 400.0, 310.0),
    rect(OX + 500.0, OY, 100.0, 100.0)])
whole = ar.ReforestationArea.from_layer(parcels)
check("senza selezione si prende tutto il layer", whole.lorda_m2,
      124_000.0 + 10_000.0, TOL_AREA)
parcels.selectByIds([next(parcels.getFeatures()).id()])
picked = ar.ReforestationArea.from_layer(parcels)
check("con una selezione si prende quella", picked.lorda_m2, 124_000.0,
      TOL_AREA)
check_true("l'etichetta dice che era una selezione",
           "selezione" in picked.label)
check_true("il CRS del layer viaggia con l'area",
           picked.crs_authid == "EPSG:32632")
tracks = line_layer("tracciati", [
    "LINESTRING({0} {1},{2} {1})".format(OX, OY, OX + 100.0)])
check_raises("un layer di linee non e' un'area", GeometryError,
             ar.ReforestationArea.from_layer, tracks)
check_raises("un layer vuoto nemmeno", EmptyAoiError,
             ar.ReforestationArea.from_layer,
             polygon_layer("vuoto", []))

# --------------------------------------------------------------------------
# C1 - buffers of 5, 10 and 3 metres, checked in closed form
# --------------------------------------------------------------------------
print("\n== C1: fasce di 5, 10 e 3 m, verificate in forma chiusa ==")
ROAD_LENGTH = 380.0
ROAD = QgsGeometry.fromWkt("LINESTRING({0} {1},{2} {1})".format(
    OX + 10.0, OY + 200.0, OX + 10.0 + ROAD_LENGTH, OY + 200.0))

constraint_distances = {"strada": 5.0, "fosso": 10.0, "elettrodotto": 3.0}
c1 = cs.ConstraintSet(constraint_distances)
check("il set dichiara i vincoli che gli sono stati dati",
      len(c1.keys()), 3)
check_true("...e nessun altro",
           set(c1.keys()) == set(constraint_distances))
for key, distance in constraint_distances.items():
    check("{0} conserva la sua distanza".format(key), c1.buffer_for(key),
          distance, TOL_VERTEX)

for key, distance in constraint_distances.items():
    c1.clear_features(key)
    c1.add_geometry(key, ROAD)
    grown = c1.buffered(key)
    box = grown.boundingBox()
    # A straight segment buffered by d: a rectangle 2d wide and L long, with
    # a half-disc at each end. GEOS approximates the discs with an inscribed
    # polygon of 4 * segments sides, whose area is (n/2) r^2 sin(2 pi / n).
    sides = 4 * c1.segments
    caps = 0.5 * sides * distance * distance * math.sin(2.0 * math.pi / sides)
    expected = 2.0 * distance * ROAD_LENGTH + caps
    print("        {0:<12} larghezza {1:.9f} m, area {2:.6f} m2 "
          "(attesa {3:.6f})".format(key, box.height(), grown.area(), expected))
    check("{0}: la fascia e' larga 2d".format(key), box.height(),
          2.0 * distance, TOL_VERTEX)
    check("{0}: ...e lunga L + 2d".format(key), box.width(),
          ROAD_LENGTH + 2.0 * distance, TOL_VERTEX)
    check("{0}: l'area e' quella della forma chiusa".format(key),
          grown.area(), expected, TOL_AREA)
    check_true("{0}: la fascia contiene il tracciato".format(key),
               grown.contains(ROAD))
    check("{0}: il tracciato dista 0 dalla fascia".format(key),
          grown.distance(ROAD), 0.0, TOL_VERTEX)

print("\n-- le tre fasce sono l'una dentro l'altra --")
b3 = c1.buffered("elettrodotto")
c1.clear_features("strada")
c1.add_geometry("strada", ROAD)
b5 = c1.buffered("strada")
c1.clear_features("fosso")
c1.add_geometry("fosso", ROAD)
b10 = c1.buffered("fosso")
check_true("3 m sta dentro 5 m", b5.contains(b3))
check_true("5 m sta dentro 10 m", b10.contains(b5))
check_true("e le aree crescono", b3.area() < b5.area() < b10.area())

# --------------------------------------------------------------------------
# C2 - the distances are parameters, and a wrong one is refused
# --------------------------------------------------------------------------
print("\n== C2: le distanze sono parametri, non numeri cablati ==")
c2 = cs.ConstraintSet({"strada": 5.0})
c2.add_geometry("strada", ROAD)
before = c2.buffered("strada").boundingBox().height()
c2.set_buffer("strada", 12.5)
after = c2.buffered("strada").boundingBox().height()
print("        {0:.3f} m -> {1:.3f} m cambiando solo il parametro".format(
    before, after))
check("cambiare la distanza cambia la fascia", after, 25.0, TOL_VERTEX)
check("...senza perdere gli elementi gia' raccolti",
      c2.rules["strada"].n_features, 1)
c2.declare("sentiero", 2.0, label="Sentieri")
check("un vincolo si puo' aggiungere dopo", c2.buffer_for("sentiero"), 2.0,
      TOL_VERTEX)
check_raises("una chiave non dichiarata e' un errore", InvalidInputError,
             c2.add_geometry, "fosso", ROAD)
check_raises("...anche solo per leggerne la distanza", InvalidInputError,
             c2.buffer_for, "fosso")
check_raises("una distanza negativa e' un errore", InvalidInputError,
             c2.set_buffer, "strada", -1.0)
check_raises("...e cosi' un valore non finito", InvalidInputError,
             c2.declare, "burrone", float("nan"))
check_raises("zero segmenti non fanno un buffer", InvalidInputError,
             cs.ConstraintSet, {"strada": 5.0}, 0)
check_raises("un vincolo senza nome non esiste", InvalidInputError,
             c2.declare, "", 5.0)

print("\n-- distanza zero: l'elemento stesso, non il vuoto --")
c2.declare("fabbricato", 0.0, label="Fabbricati")
c2.add_geometry("fabbricato", rect(OX + 300.0, OY + 20.0, 20.0, 15.0))
zero = c2.buffered("fabbricato")
check("la fascia nulla restituisce l'elemento", zero.area(), 300.0, TOL_AREA)
c2.set_buffer("fabbricato", 2.0)
check_true("con 2 m diventa piu' grande", c2.buffered("fabbricato").area()
           > 300.0)

# --------------------------------------------------------------------------
# C3 - a whole layer under one constraint, then applied to the area
# --------------------------------------------------------------------------
print("\n== C3: un layer intero sotto un vincolo, poi applicato all'area ==")
roads = line_layer("strade", [
    "LINESTRING({0} {1},{2} {1})".format(OX + 10.0, OY + 200.0,
                                         OX + 390.0, OY + 200.0),
    "LINESTRING({0} {1},{0} {2})".format(OX + 300.0, OY + 10.0, OY + 300.0)])
c3 = cs.ConstraintSet({"strada": 5.0, "fosso": 10.0})
check("il layer porta i suoi elementi", c3.add_layer("strada", roads), 2)
check("...e ne prende il nome se non ne aveva",
      1.0 if c3.rules["strada"].display == "strade" else 0.0, 1.0)
ditches = line_layer("fossi", [
    "LINESTRING({0} {1},{2} {1})".format(OX + 10.0, OY + 60.0,
                                         OX + 390.0, OY + 60.0)])
c3.add_layer("fosso", ditches)

c3_area = ar.ReforestationArea(GROSS)
check("apply_to aggiunge un'esclusione per tipo di vincolo",
      c3.apply_to(c3_area), 2)
union = c3.exclusion_geometry()
inside = GROSS.intersection(union)
print("        vincoli {0:,.2f} m2, dentro l'area {1:,.2f} m2, "
      "utile {2:.6f} ha".format(union.area(), inside.area(), c3_area.utile_ha))
check("l'area sottrae esattamente quello che i vincoli coprono al suo interno",
      c3_area.esclusa_m2, inside.area(), TOL_AREA)
check("...e l'utile e' la lorda meno quello",
      c3_area.utile_m2, 124_000.0 - inside.area(), TOL_AREA)
check_true("le due fasce si incrociano, e l'incrocio non si conta due volte",
           union.area() < sum(m2 for *_head, m2 in c3.breakdown()))
check_true("il prospetto nomina le fasce",
           any("5" in line for line in c3.describe())
           and any("10" in line for line in c3.describe()))

# --------------------------------------------------------------------------
# T1 - a synthetic DEM with a known grade
# --------------------------------------------------------------------------
print("\n== T1: DEM sintetico, pendenza nota ==")
CELL = 5.0
NX, NY = 120, 100
GRADE = 0.10                       # 10 % rising towards the north
# Upper-left corner of the upper-left pixel, placed so the 600 x 500 m window
# covers the whole 400 x 310 m parcel used above: I1 measures the suitable
# surface *of the project*, which needs the DEM to reach it.
DEM_TOP = OY + 410.0
DEM_GT = (OX, CELL, 0.0, DEM_TOP, 0.0, -CELL)
xs = OX + (np.arange(NX) + 0.5) * CELL
ys = DEM_TOP - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(xs, ys)
Z = 300.0 + GRADE * (YY - (DEM_TOP - NY * CELL))

t1 = tr.TerrainAnalysis.from_array(Z, DEM_GT, CRS.authid(), source="rampa")
slope, aspect = t1.grids()
inner_slope = slope[1:-1, 1:-1]
inner_aspect = aspect[1:-1, 1:-1]
expected_slope = math.degrees(math.atan(GRADE))
print("        pendenza attesa {0:.12f} deg, letta {1:.12f} / {2:.12f}".format(
    expected_slope, float(inner_slope.min()), float(inner_slope.max())))
check("la pendenza minima interna e' quella del piano", float(inner_slope.min()),
      expected_slope, TOL_VERTEX)
check("...e cosi' la massima", float(inner_slope.max()), expected_slope,
      TOL_VERTEX)
check("l'esposizione guarda a valle, cioe' a Sud",
      float(inner_aspect.min()), 180.0, TOL_VERTEX)
check("...ovunque", float(inner_aspect.max()), 180.0, TOL_VERTEX)
check("la cella e' 5 x 5 m", t1.cell_area_m2, 25.0, TOL_VERTEX)
check("...e la sua dimensione caratteristica 5 m", t1.cellsize_m, 5.0,
      TOL_VERTEX)
check("il DEM non ha buchi", t1.nodata_fraction, 0.0, TOL_VERTEX)

print("\n-- quota campionata, non interpolata a caso --")
mid_x, mid_y = OX + 300.0, DEM_TOP - 250.0
check("la quota al centro segue il piano", float(t1.elevation_at(mid_x, mid_y)),
      300.0 + GRADE * (mid_y - (DEM_TOP - NY * CELL)), TOL_VERTEX)
check_true("fuori dalla finestra la quota e' NaN",
           not np.isfinite(float(t1.elevation_at(OX - 500.0, mid_y))))
sampled_slope, sampled_aspect = t1.slope_aspect_at(mid_x, mid_y)
check("la pendenza campionata al centro", float(sampled_slope),
      expected_slope, TOL_VERTEX)
check("...con la sua esposizione", float(sampled_aspect), 180.0, TOL_VERTEX)

# --------------------------------------------------------------------------
# T2 - a compound grade, to pin the compass convention
# --------------------------------------------------------------------------
print("\n== T2: pendenza composta, per fissare la convenzione bussola ==")
GX, GY = 0.03, 0.04                # dz/dEast, dz/dNorth: rise 0.05
Z2 = 200.0 + GX * (XX - OX) + GY * (YY - (DEM_TOP - NY * CELL))
t2 = tr.TerrainAnalysis.from_array(Z2, DEM_GT, CRS.authid())
slope2, aspect2 = t2.grids()
expected_slope2 = math.degrees(math.atan(math.hypot(GX, GY)))
expected_aspect2 = math.degrees(math.atan2(-GX, -GY)) % 360.0
print("        pendenza {0:.9f} deg, esposizione {1:.9f} deg".format(
    expected_slope2, expected_aspect2))
check("la pendenza e' l'arcotangente del gradiente",
      float(slope2[1:-1, 1:-1].mean()), expected_slope2, TOL_VERTEX)
check("l'esposizione e' l'azimut di valle",
      float(aspect2[1:-1, 1:-1].mean()), expected_aspect2, TOL_VERTEX)
check_true("...ed e' nel terzo quadrante, come deve essere per un piano "
           "che sale verso Nord-Est",
           180.0 < expected_aspect2 < 270.0)

# --------------------------------------------------------------------------
# T3 - the same DEM read through a QgsRasterLayer
# --------------------------------------------------------------------------
print("\n== T3: lo stesso DEM letto come QgsRasterLayer ==")
DEM_PATH = os.path.join(TMP, "rampa.tif")
gdal.UseExceptions()
_ds = gdal.GetDriverByName("GTiff").Create(DEM_PATH, NX, NY, 1, gdal.GDT_Float64)
_ds.SetGeoTransform(DEM_GT)
_srs = osr.SpatialReference()
_srs.ImportFromEPSG(32632)
_ds.SetProjection(_srs.ExportToWkt())
_ds.GetRasterBand(1).WriteArray(Z)
_ds.FlushCache()
_ds = None
DEM_LAYER = QgsRasterLayer(DEM_PATH, "rampa", "gdal")
QgsProject.instance().addMapLayer(DEM_LAYER)
check_true("il raster di prova e' valido", DEM_LAYER.isValid())

AOI = rect(OX + 100.0, DEM_TOP - 400.0, 200.0, 200.0)
t3 = tr.TerrainAnalysis.from_layer(DEM_LAYER, CRS, AOI, margin_m=20.0)
slope3, _aspect3 = t3.grids()
print("        cella {0:.3f} m, pendenza mediana {1:.12f} deg".format(
    t3.cellsize_m, float(np.nanmedian(slope3))))
check("la finestra conserva la risoluzione nativa", t3.cellsize_m, CELL,
      TOL_VERTEX)
check("la pendenza letta dal layer e' quella del piano",
      float(np.nanmedian(slope3)), expected_slope, TOL_VERTEX)
check("la quota letta dal layer coincide con quella dell'array",
      float(t3.elevation_at(mid_x, mid_y)),
      float(t1.elevation_at(mid_x, mid_y)), 1e-6)
check_true("nessun avviso su una finestra piccola e piena", not t3.warnings)
check_raises("senza DEM non si analizza nulla", RasterError,
             tr.TerrainAnalysis.from_layer, None, CRS, AOI)
check_raises("...e senza area nemmeno", InvalidInputError,
             tr.TerrainAnalysis.from_layer, DEM_LAYER, CRS, None)

# --------------------------------------------------------------------------
# T4 - the suitability mask
# --------------------------------------------------------------------------
print("\n== T4: maschera di idoneita' ==")
gentle = t1.suitability(TopographicFilter(slope_max_deg=10.0))
check("un piano al 10 % passa un limite di 10 gradi",
      gentle.suitable_cells, NX * NY)
check("la superficie idonea e' celle x area di cella",
      gentle.suitable_area_m2, NX * NY * 25.0, TOL_AREA)
check("...in ettari", gentle.suitable_area_ha, NX * NY * 25.0 / 10_000.0,
      TOL_AREA)
check("la frazione idonea e' 1", gentle.suitable_fraction, 1.0, TOL_VERTEX)

steep = t1.suitability(TopographicFilter(slope_max_deg=4.0))
independent = int(np.count_nonzero(np.nan_to_num(slope, nan=1e9) <= 4.0))
print("        sotto 4 deg: {0:,} celle (conteggio indipendente {1:,})".format(
    steep.suitable_cells, independent))
check("il conteggio coincide con quello fatto a mano sul grigliato",
      steep.suitable_cells, independent)
check_true("e sono le celle di bordo, dove il kernel di Horn si appoggia "
           "al padding", steep.suitable_cells < NX * NY)
check_true("il motivo del rifiuto e' la pendenza",
           steep.rejection_counts().get(pl.REASON_SLOPE, 0) > 0)

band = t1.suitability(TopographicFilter(elev_min_m=320.0, elev_max_m=340.0))
independent_band = int(np.count_nonzero((Z >= 320.0) & (Z <= 340.0)))
check("una fascia altimetrica seleziona le celle giuste",
      band.suitable_cells, independent_band)
check_true("...e chi resta fuori lo fa per la quota",
           band.rejection_counts().get(pl.REASON_ELEVATION, 0)
           == NX * NY - independent_band)

north = t1.suitability(TopographicFilter(aspect_ranges=[(135.0, 225.0)]))
check("un versante esposto a Sud e' tutto dentro il settore 135-225",
      north.suitable_cells, NX * NY)
south = t1.suitability(TopographicFilter(aspect_ranges=[(315.0, 45.0)]))
check("...e nulla di esso guarda a Nord", south.suitable_cells, 0)
check_true("il prospetto della maschera dice quante celle e perche'",
           any("celle idonee" in line for line in gentle.describe()))

# --------------------------------------------------------------------------
# T5 - no-data is refused, not guessed
# --------------------------------------------------------------------------
print("\n== T5: i buchi del DEM non si indovinano ==")
HOLE = 20
Z_HOLE = Z.copy()
Z_HOLE[40:40 + HOLE, 40:40 + HOLE] = np.nan
t5 = tr.TerrainAnalysis.from_array(Z_HOLE, DEM_GT, CRS.authid())
check("la frazione senza dato e' quella del buco", t5.nodata_fraction,
      HOLE * HOLE / float(NX * NY), TOL_VERTEX)
holed = t5.suitability(TopographicFilter(slope_max_deg=10.0))
counts = holed.rejection_counts()
print("        rifiuti: {0}".format(counts))
check("ogni cella senza quota e' rifiutata come tale",
      counts.get(pl.REASON_NO_DEM, 0), HOLE * HOLE)
check_true("e la corona attorno al buco, dove Horn non puo' calcolare, "
           "e' rifiutata come pendenza non misurabile",
           counts.get(pl.REASON_SLOPE, 0) > 0)
lenient = t5.suitability(TopographicFilter(slope_max_deg=10.0),
                         require_slope=False)
check_true("senza require_slope quelle celle passerebbero non misurate",
           lenient.suitable_cells > holed.suitable_cells)
check("...esattamente quelle della corona",
      lenient.suitable_cells - holed.suitable_cells,
      counts.get(pl.REASON_SLOPE, 0))
check_true("nessuna cella idonea ha quota NaN",
           bool(np.all(np.isfinite(Z_HOLE[holed.mask]))))

# --------------------------------------------------------------------------
# T6 - the mask cut to the real polygon
# --------------------------------------------------------------------------
print("\n== T6: la maschera ritagliata sul poligono vero ==")
clipped = t1.restrict_to(gentle, AOI)
print("        AOI {0:.4f} ha, maschera ritagliata {1:.4f} ha".format(
    AOI.area() / 10_000.0, clipped.suitable_area_ha))
check("il ritaglio restituisce la superficie dell'AOI",
      clipped.suitable_area_ha, AOI.area() / 10_000.0, TOL_AREA)
check_true("...cioe' meno della finestra intera",
           clipped.suitable_cells < gentle.suitable_cells)
check_true("le celle cadute fuori dicono di esserlo",
           clipped.rejection_counts().get(pl.REASON_OUTSIDE, 0) > 0)
xx, yy = t1.cell_centres()
check_true("ogni cella rimasta ha il centro dentro l'AOI",
           bool(np.all([AOI.intersects(QgsGeometry.fromWkt(
               "POINT({0} {1})".format(x, y)))
               for x, y in zip(xx[clipped.mask][:40],
                               yy[clipped.mask][:40])])))
check_raises("una finestra troppo grande viene rifiutata, non attesa",
             RasterError, t1.restrict_to, gentle, AOI, 10)
untouched = t1.restrict_to(gentle, None)
check("senza poligono la maschera resta quella", untouched.suitable_cells,
      gentle.suitable_cells)

# --------------------------------------------------------------------------
# T7 - statistics, and the handshake with the frozen planner
# --------------------------------------------------------------------------
print("\n== T7: statistiche e aggancio al pianificatore ==")
stats = t1.statistics()
check("la quota minima e' quella del piano", stats["z_min_m"],
      float(np.nanmin(Z)), TOL_VERTEX)
check("...e la massima", stats["z_max_m"], float(np.nanmax(Z)), TOL_VERTEX)
check("la pendenza media interna", stats["slope_max_deg"], expected_slope,
      TOL_VERTEX)
check("la direzione di massima pendenza e' Nord-Sud",
      stats["dominant_slope_azimuth_deg"], 0.0, 1e-6)
masked = t1.statistics(clipped.mask)
check_true("le statistiche accettano una maschera",
           masked["z_min_m"] > stats["z_min_m"])
check_true("il prospetto morfologico e' leggibile",
           any("Pendenza" in line for line in t1.describe()))

check_true("as_planner_terrain restituisce il modello, non un secondo terreno",
           t1.as_planner_terrain() is t1.model)
plan = pl.plan_planting_for_geometry(
    AOI, GridSpec(spacing_x=10.0, spacing_y=10.0, margin_m=0.0),
    terrain=t1.as_planner_terrain(), compute_edge_distance=False)
print("        {0:,} piante, quote {1:.2f} - {2:.2f} m".format(
    len(plan.plants), min(p.z for p in plan.plants),
    max(p.z for p in plan.plants)))
check_true("il pianificatore congelato accetta questo terreno cosi' com'e'",
           len(plan.plants) > 0)
check_true("...e ogni pianta esce con la sua quota",
           all(p.z is not None and math.isfinite(p.z) for p in plan.plants))
check_true("...e con la sua pendenza",
           all(p.slope_deg is not None for p in plan.plants))
check("la quota della prima pianta e' quella del DEM",
      plan.plants[0].z,
      float(t1.elevation_at(plan.plants[0].x, plan.plants[0].y)), 1e-9)

# --------------------------------------------------------------------------
# I1 - the three modules together
# --------------------------------------------------------------------------
print("\n== I1: area, vincoli e terreno insieme ==")
project = ar.ReforestationArea(GROSS, CRS.authid(), label="Progetto S3")
project.add_exclusion(EXCLUDED, label="affioramento")
site = cs.ConstraintSet({"strada": 5.0, "fosso": 10.0})
site.add_layer("strada", roads)
site.add_layer("fosso", ditches)
site.apply_to(project)
print("        lorda {0:.4f} ha, esclusa {1:.4f} ha, utile {2:.4f} ha".format(
    project.lorda_ha, project.esclusa_ha, project.utile_ha))
check("la lorda non cambia mai", project.lorda_ha, 12.40, TOL_AREA)
check_true("l'esclusa cresce con i vincoli",
           project.esclusa_ha > 1.85)
check("lorda meno esclusa fa sempre l'utile",
      project.lorda_m2 - project.esclusa_m2, project.utile_m2, TOL_AREA)
check_true("e l'utile e' minore di quello del solo affioramento",
           project.utile_ha < 10.55)

useful_mask = t1.restrict_to(
    t1.suitability(TopographicFilter(slope_max_deg=10.0)), project.utile())
print("        superficie idonea sull'utile: {0:.4f} ha".format(
    useful_mask.suitable_area_ha))
check_true("la superficie idonea non supera l'utile",
           useful_mask.suitable_area_ha <= project.utile_ha + TOL_AREA)
check_true("...e non e' vuota", useful_mask.suitable_cells > 0)
check_true("il prospetto d'area elenca ogni esclusione",
           len(project.exclusion_breakdown()) == 3)


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
