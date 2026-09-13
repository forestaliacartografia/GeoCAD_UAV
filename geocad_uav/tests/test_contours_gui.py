"""
v1.18.0: contour planting, from the button to the plants on the hillside.

``curves.py`` has done the arithmetic since M04 and no operator could reach
it: nothing in the interface mentioned a contour. This suite presses what an
operator presses -- use the DEM already loaded, extract the contours, choose
the contour scheme, generate -- and then looks at the map and at the numbers.

What is checked is the chain, not the arithmetic (``test_reforestation_s5``
already measures the spacing): the lines come out of GDAL, get cut to the
project, become a layer with their heights on it, and the plants that follow
sit on those lines, at the distance asked for, inside the usable surface.

NEEDS QGIS with widgets and the Processing framework. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_contours_gui.py
"""

import gc
import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from osgeo import gdal, osr                                      # noqa: E402
from qgis.core import (QgsApplication,                           # noqa: E402
                       QgsCoordinateReferenceSystem, QgsGeometry, QgsProject,
                       QgsRasterLayer, QgsWkbTypes)

QGS = QgsApplication([], True)
QGS.initQgis()

# After QgsApplication exists, never before: importing qgis.analysis first
# takes the interpreter down with no traceback, and the bundled `processing`
# package lives under QGIS's own plugins directory.
from qgis.analysis import QgsNativeAlgorithms                    # noqa: E402

_plugins_dir = os.path.join(QgsApplication.prefixPath(), "python", "plugins")
if os.path.isdir(_plugins_dir) and _plugins_dir not in sys.path:
    sys.path.append(_plugins_dir)

from processing.core.Processing import Processing                # noqa: E402

Processing.initialize()
NATIVE = QgsNativeAlgorithms()
QgsApplication.processingRegistry().addProvider(NATIVE)

from geocad_uav.forest.reforestation import curves as curves_mod  # noqa: E402
from geocad_uav.forest.reforestation import spacing as spacing_mod  # noqa: E402
from geocad_uav.gui import workflow as wf                        # noqa: E402

FAILURES = []
TMP = tempfile.mkdtemp(prefix="geocad_curve_")
CRS32632 = QgsCoordinateReferenceSystem("EPSG:32632")
OX, OY = 500000.0, 5000000.0


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


# -- a hillside with a real, known shape ------------------------------------
# A plane tilted 1:5 to the east with a gentle north-south undulation: the
# contours are long, curved and roughly parallel, which is what a contour
# planting scheme has to cope with.
CELL = 4.0
NX, NY = 180, 160
xs = OX + (np.arange(NX) + 0.5) * CELL
ys = OY - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(xs, ys)
Z = 400.0 + 0.20 * (XX - OX) + 18.0 * np.sin((YY - OY) / 120.0)

DEM_PATH = os.path.join(TMP, "collina.tif")
gdal.UseExceptions()
_ds = gdal.GetDriverByName("GTiff").Create(DEM_PATH, NX, NY, 1,
                                           gdal.GDT_Float32)
_ds.SetGeoTransform((OX, CELL, 0.0, OY, 0.0, -CELL))
_srs = osr.SpatialReference()
_srs.ImportFromEPSG(32632)
_ds.SetProjection(_srs.ExportToWkt())
_ds.GetRasterBand(1).WriteArray(Z.astype(np.float32))
_ds.FlushCache()
_ds = None

DEM_LAYER = QgsRasterLayer(DEM_PATH, "DTM regionale", "gdal")
QgsProject.instance().addMapLayer(DEM_LAYER)

AREA = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 80.0, OY - 540.0, OX + 620.0, OY - 100.0))

print("=" * 78)
print("Curve di livello -- dal pulsante alle piante sul versante")
print("=" * 78)
print("  DEM: {0} x {1} celle da {2:g} m, quote {3:.0f}-{4:.0f} m".format(
    NX, NY, CELL, Z.min(), Z.max()))

workspace = wf.Workspace(None)
state = workspace.state
terrain_panel = workspace.context.terrain_panel
scheme = workspace.context.scheme_panel
generate_panel = workspace.context.generate_panel
layers = state.layers

# --------------------------------------------------------------------------
# C1 - a DEM already in the project can be used, without the internet
# --------------------------------------------------------------------------
print("\n== C1: il DEM gia' caricato si puo' usare ==")
state.set_area(AREA, CRS32632, "Versante di prova")
check_true("il combo elenca il raster del progetto",
           terrain_panel.reload_rasters() >= 1)
check_true("...e ci sta proprio quello caricato",
           terrain_panel.local_dem.findData(DEM_LAYER.id()) >= 0)
terrain_panel.local_dem.setCurrentIndex(
    terrain_panel.local_dem.findData(DEM_LAYER.id()))

check_true("senza DEM non ci sono curve da estrarre",
           terrain_panel.extract_contours() == 0)

analysis = terrain_panel.use_local_dem()
check_true("il pulsante legge il DEM del progetto", analysis is not None)
check_true("...e diventa il terreno del progetto",
           state.terrain is analysis)
stats = state.terrain.statistics()
print("        quote lette: {0:.0f} - {1:.0f} m, pendenza {2:.1f}-{3:.1f} deg"
      .format(stats["z_min_m"], stats["z_max_m"], stats["slope_min_deg"],
              stats["slope_max_deg"]))
# from_layer warps the window covering the project, not the whole raster,
# so the range read is a slice of the DEM's -- inside it, and not a sliver.
check_true("le quote lette stanno dentro quelle del DEM",
           Z.min() - 1.0 <= stats["z_min_m"] <= stats["z_max_m"]
           <= Z.max() + 1.0)
check_true("...e coprono il dislivello del versante di progetto",
           stats["z_max_m"] - stats["z_min_m"] > 100.0)
check_text("lo step Terreno risulta completato", state.status("terrain"),
           wf.DONE)

# --------------------------------------------------------------------------
# C2 - the contours come out of GDAL and land on the map
# --------------------------------------------------------------------------
print("\n== C2: [Estrai curve di livello] -> layer sulla mappa ==")
check("prima non c'e' nessun layer di curve",
      1.0 if layers.layers.get("contours") is None else 0.0, 1.0)

terrain_panel.contour_interval.setValue(10.0)
terrain_panel.contour_min_length.setValue(20.0)
count = terrain_panel.extract_contours()
print("        {0}".format(terrain_panel.contour_label.text()))
check_true("il pulsante ha prodotto delle curve", count > 0)
check("il progetto le tiene", len(state.contours), count)
check("...e l'equidistanza e' quella chiesta", state.contour_interval_m, 10.0)

contour_layer = layers.layers.get("contours")
check_true("le curve sono un layer sulla mappa", contour_layer is not None)
check("...una feature per curva", contour_layer.featureCount(), count)
check_text("...nel CRS del progetto", contour_layer.crs().authid(),
           "EPSG:32632")
check_true("...e sono linee, non poligoni",
           contour_layer.geometryType() == QgsWkbTypes.LineGeometry)
check_true("il layer sta nel progetto QGIS",
           QgsProject.instance().mapLayer(contour_layer.id()) is not None)

heights = sorted({round(float(f["quota_m"]), 3)
                  for f in contour_layer.getFeatures()
                  if f["quota_m"] is not None})
print("        quote sulle curve: {0}".format(heights))
check_true("ogni curva porta la sua quota", len(heights) >= 3)
check_true("...e le quote sono multipli dell'equidistanza",
           all(abs(h / 10.0 - round(h / 10.0)) < 1e-6 for h in heights))
gaps = {round(b - a, 3) for a, b in zip(heights, heights[1:])}
check_true("...separate proprio dall'equidistanza", gaps == {10.0}, )

usable = state.usable_geometry()
outside = [f.id() for f in contour_layer.getFeatures()
           if not usable.buffer(1e-6, 1).contains(f.geometry())]
check("nessuna curva esce dall'area di progetto", len(outside), 0)
check_true("nessuna curva piu' corta del minimo chiesto",
           all(f["sviluppo_m"] >= 20.0 for f in contour_layer.getFeatures()))
check("lo sviluppo sul layer e' quello del modello",
      sum(f["sviluppo_m"] for f in contour_layer.getFeatures()),
      state.contour_length_m(), 0.5)

print("\n-- cambiare equidistanza cambia le curve --")
terrain_panel.contour_interval.setValue(5.0)
denser = terrain_panel.extract_contours()
print("        10 m -> {0} curve, 5 m -> {1} curve".format(count, denser))
check_true("con 5 m di equidistanza le curve sono di piu'", denser > count)
check("...e il layer segue", layers.layers["contours"].featureCount(), denser)

# --------------------------------------------------------------------------
# C3 - the scheme, chosen where the others are chosen
# --------------------------------------------------------------------------
print("\n== C3: 'Lungo le curve di livello' e' uno dei sesti ==")
index = scheme.pattern.findData(spacing_mod.PATTERN_CONTOUR)
check_true("il sesto compare nell'elenco dei sesti", index >= 0)
check_text("...col nome che un operatore legge",
           scheme.pattern.itemText(index), "Lungo le curve di livello")
scheme.plant_distance.setValue(4.0)
scheme.row_distance.setValue(4.0)
scheme.pattern.setCurrentIndex(index)
scheme.apply_scheme()
check_text("il progetto ha adottato il sesto", state.spec.pattern,
           spacing_mod.PATTERN_CONTOUR)
check_true("...e il progetto lo sa", state.along_contours)

expected = state.expected_plants()
print("        sviluppo {0:,.0f} m / passo 4 m -> attese {1:,.0f} piante"
      .format(state.contour_length_m(), expected))
check("le piante attese vengono dallo sviluppo delle curve, non da un "
      "reticolo", expected, state.contour_length_m() / 4.0, 1e-6)
check_true("...e la densita' ne discende",
           abs(state.density_per_ha()
               - expected / (state.usable_m2 / 10_000.0)) < 1e-6)

# --------------------------------------------------------------------------
# C4 - generate, and the plants are on the lines
# --------------------------------------------------------------------------
print("\n== C4: [Genera] pianta lungo le curve ==")
plan = generate_panel.preview()
check_true("l'impianto e' stato generato", plan is not None)
print("        {0:,} piante su {1:,} curve".format(
    plan.count, len({p.row_id for p in plan.plants})))
check_true("...e ci sono piante", plan.count > 0)
check_true("le file sono le curve",
           len({p.row_id for p in plan.plants}) <= len(state.contours))

step = state.spec.plant_distance_m
measured = plan.mean_real_spacing()
print("        passo reale medio: {0:.4f} m contro {1:.2f} m chiesti".format(
    measured, step))
check("il passo lungo la linea e' quello chiesto", measured, step, 0.02)

check_true("ogni pianta ha la sua quota, letta dal DEM",
           all(p.z is not None and math.isfinite(p.z) for p in plan.plants))
zs = [p.z for p in plan.plants]
check_true("...e le quote stanno dentro quelle del DEM",
           Z.min() - 1.0 <= min(zs) and max(zs) <= Z.max() + 1.0)

by_row = {}
for plant in plan.plants:
    by_row.setdefault(plant.row_id, []).append(plant)
spreads = [max(p.z for p in row) - min(p.z for p in row)
           for row in by_row.values() if len(row) > 2]
print("        dislivello dentro una fila: mediana {0:.2f} m, max {1:.2f} m"
      .format(sorted(spreads)[len(spreads) // 2], max(spreads)))
check_true("una fila resta a quota, che e' tutto il punto di una curva",
           max(spreads) < 5.0)

margin = usable.buffer(0.5, 4)
stray = [p for p in plan.plants
         if not margin.intersects(QgsGeometry.fromWkt(
             "POINT({0} {1})".format(p.x, p.y)))]
check("nessuna pianta fuori dalla superficie utile", len(stray), 0)

plants_layer = generate_panel.generate()
check_true("il layer delle piante e' sulla mappa",
           layers.layers.get("plants") is plants_layer)
check("...e porta tutte le piante", plants_layer.featureCount(), plan.count)
check_true("...ed e' PointZ", QgsWkbTypes.hasZ(plants_layer.wkbType()))
check_true("le curve restano disegnate accanto alle piante",
           layers.layers["contours"].featureCount() == len(state.contours))
planted = sum(f["piante"] for f in layers.layers["contours"].getFeatures())
check("il layer delle curve dice quante piante ha ciascuna", planted,
      plan.count)

# --------------------------------------------------------------------------
# C5 - the numbers an operator reads, and the refusals
# --------------------------------------------------------------------------
print("\n== C5: numeri onesti e rifiuti leggibili ==")
report = workspace.context.outputs_panel.build_report()
check_true("la relazione parla delle curve",
           "FILE SU CURVE DI LIVELLO" in report)
check_true("...e dice l'equidistanza usata", "Equidistanza:" in report)
check_true("...e la densita' risultante, misurata",
           "Densita' risultante:" in report)
check_true("...senza spacciare per densita' quella di un reticolo",
           "DENSITA' DA SESTO" not in report.upper()
           or "curve" in report.lower())

anomalies = workspace.context.verify_panel.run()
print("        anomalie: {0}".format(anomalies or "nessuna"))
check("un impianto su curve passa la verifica", len(anomalies), 0)

scenarios = workspace.context.optimise_panel.run()
print("        scenari: {0}".format(
    [(s["name"], s["sesto"], s["plants"]) for s in scenarios]))
check("tre scenari confrontati", len(scenarios), 3)
check_true("...e sono confronti di passo sulle curve, non di reticoli",
           all("curve" in s["sesto"] for s in scenarios))
check_true("un passo piu' fitto da' piu' piante",
           scenarios[0]["plants"] > scenarios[2]["plants"])
check("il confronto non ha rubato le piante alle curve disegnate", planted,
      sum(f["piante"] for f in layers.layers["contours"].getFeatures()))

print("\n-- e quando non si puo' fare, lo dice --")
messages = []
generate_panel.warn = lambda exc: messages.append(exc.formatted())
state.contours = []
check_true("senza curve estratte non genera", generate_panel.preview() is None)
check_true("...e dice di estrarle",
           messages and "curve di livello" in messages[-1])

terrain_panel.contour_interval.setValue(10.0)
terrain_panel.extract_contours()
zones_panel = workspace.context.zones_panel
zones_panel.band_count.setValue(2)
zones_panel.split()
check_true("con le zone non genera sulle curve",
           generate_panel.preview() is None)
check_true("...e spiega perche'", "zone" in messages[-1].lower())
print("        rifiuto: {0}".format(messages[-1]))

workspace.unmount()
check("chiudendo, i layer del plugin spariscono", len(layers.layers), 0)
check_true("...e il DEM di lavoro e' stato rilasciato",
           state.terrain._raster_layer is None)

print("\n" + "=" * 78)
# gdal:contour hands back layers the project never adopted, and so does the
# working DEM. Collected after exitQgis() they are a use-after-teardown that
# segfaults with no traceback, every check above having passed.
contour_layer = plants_layer = DEM_LAYER = None
workspace = state = terrain_panel = scheme = generate_panel = None
layers = plan = analysis = None
gc.collect()
QgsProject.instance().removeAllMapLayers()
QGS.exitQgis()
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for name in FAILURES:
        print("   - {0}".format(name))
    sys.exit(1)
print("ALL CHECKS PASSED")
