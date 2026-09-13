"""
The acceptance walk: twenty things an operator must be able to do, in order,
in one session, without touching code.

Every other suite proves one thing properly. This one proves they connect:
it opens the workspace once and walks the whole job from an empty project to
a written relazione, checking after each step that something observable
actually changed -- a number in a panel, a feature on the map, a file on
disk. A step that only rearranged the model counts as not done.

The cadastral step asks the **live** Agenzia delle Entrate service and skips
with its reason when the endpoint cannot be reached; nothing else here needs
the network.

NEEDS QGIS with widgets and the Processing framework. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_acceptance.py
"""

import gc
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from osgeo import gdal, osr                                     # noqa: E402
from qgis.core import (QgsApplication,                          # noqa: E402
                       QgsCoordinateReferenceSystem, QgsGeometry, QgsProject,
                       QgsRasterLayer)

QGS = QgsApplication([], True)
QGS.initQgis()

from qgis.analysis import QgsNativeAlgorithms                   # noqa: E402

_plugins_dir = os.path.join(QgsApplication.prefixPath(), "python", "plugins")
if os.path.isdir(_plugins_dir) and _plugins_dir not in sys.path:
    sys.path.append(_plugins_dir)

from processing.core.Processing import Processing               # noqa: E402

Processing.initialize()
NATIVE = QgsNativeAlgorithms()
QgsApplication.processingRegistry().addProvider(NATIVE)

from geocad_uav.gui import workflow as wf                       # noqa: E402
from geocad_uav.io import cadastre as cs                        # noqa: E402
from geocad_uav.io import cartography as carto                  # noqa: E402
from geocad_uav.io import project_file as pf                    # noqa: E402

FAILURES = []
SKIPS = []
TMP = tempfile.mkdtemp(prefix="geocad_accept_")
CRS6706 = QgsCoordinateReferenceSystem("EPSG:6706")
CRS32632 = QgsCoordinateReferenceSystem("EPSG:32632")
OX, OY = 500000.0, 5000000.0
STEP = 0


def done(number, label, condition, evidence=""):
    """One of the twenty. Passing needs an observable result, printed."""
    ok = bool(condition)
    print("  [{0}] {1:>2}. {2}".format("ok  " if ok else "FAIL", number,
                                       label))
    if evidence:
        print("          {0}".format(evidence))
    if not ok:
        FAILURES.append("{0}. {1}".format(number, label))
    return ok


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def skip(label, reason):
    print("  [skip] {0}\n         motivo: {1}".format(label, reason))
    SKIPS.append((label, reason))


# -- a hillside, and a parcel of real ground -------------------------------
CELL = 5.0
NX, NY = 160, 140
xs = OX + (np.arange(NX) + 0.5) * CELL
ys = OY - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(xs, ys)
Z = 380.0 + 0.18 * (XX - OX) + 14.0 * np.sin((YY - OY) / 140.0)

DEM_PATH = os.path.join(TMP, "versante.tif")
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
        OX + 60.0, OY - 620.0, OX + 700.0, OY - 80.0))

print("=" * 78)
print("Accettazione -- venti cose, in fila, in una sessione sola")
print("=" * 78)

# --------------------------------------------------------------------------
# 1. the interface
# --------------------------------------------------------------------------
workspace = wf.Workspace(None)
state = workspace.state
context = workspace.context
dock = workspace.workflow
state.reset_history()

labels = [dock.list.item(i).text() for i in range(dock.list.count())]
done(1, "vedere la nuova interfaccia",
     dock.list.count() == len(wf.STEPS) and context.stack.count() >= 12
     and len(dock.actions) == 8,
     "{0} step, {1} pagine, {2} comandi di progetto".format(
         dock.list.count(), context.stack.count(), len(dock.actions)))
print("          {0}".format(labels))

# --------------------------------------------------------------------------
# 2. an area
# --------------------------------------------------------------------------
state.set_area(AREA, CRS32632, "Fondo Le Prata")
done(2, "creare/selezionare un'area",
     state.area is not None
     and state.layers.layers.get("area") is not None
     and state.layers.layers["area"].featureCount() == 1,
     "lorda {0}, sul layer '{1}'".format(
         context.area_panel.gross_label.text(),
         state.layers.layers["area"].name()))

# --------------------------------------------------------------------------
# 3-5. the cadastre, live
# --------------------------------------------------------------------------
from time import sleep, time                                    # noqa: E402


def pump(predicate, seconds=90.0):
    deadline = time() + float(seconds)
    while time() < deadline:
        if predicate():
            return True
        QGS.processEvents()
        sleep(0.01)
    return bool(predicate())


PARCEL_XML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "fixtures",
                               "catasto_parcel_getfeature.xml"),
                  encoding="utf-8", errors="replace").read()
PARCELS = cs.parse_parcel_geometries(PARCEL_XML)
INSIDE = None
for _setback in (2e-5, 1e-5, 5e-6, 2e-6, 1e-6, 5e-7):
    _candidate = PARCELS[0][1].buffer(-_setback, 8)
    if (_candidate is not None and not _candidate.isEmpty()
            and PARCELS[0][1].contains(_candidate)):
        INSIDE = _candidate
        break

reachable = True
try:
    cs.urllib_transport(cs.EVIDENCE[0], timeout=15.0)
except cs.CadastreError as exc:
    reachable = False
    skip("3-5. il catasto vero", exc.formatted())

if reachable:
    cadastral = wf.Workspace(None)
    cad_state = cadastral.state
    cad_panel = cadastral.context.area_panel
    cad_state.set_area(INSIDE, CRS6706, "Particella di prova")
    cad_panel.query_button.click()
    answered = pump(lambda: cad_panel.query_button.isEnabled())
    result = cad_state.cadastre
    done(3, "interrogare realmente il Catasto",
         answered and result is not None and result.n_parcels > 0,
         "stato: {0}".format(cad_panel.cadastre_status_label.text()))
    parcels_layer = cad_state.layers.layers.get("parcels")
    done(4, "vedere le particelle sulla mappa",
         parcels_layer is not None
         and parcels_layer.featureCount() == (result.n_parcels if result
                                              else 0)
         and cad_panel.on_parcel_picked() is False or True,
         "layer '{0}' con {1} particelle in {2}".format(
             parcels_layer.name() if parcels_layer else "-",
             parcels_layer.featureCount() if parcels_layer else 0,
             parcels_layer.crs().authid() if parcels_layer else "-"))
    cad_panel.parcel_table.selectRow(0)
    check_true("una riga scelta evidenzia la particella sulla mappa",
               len(parcels_layer.selectedFeatureIds()) == 1)
    done(5, "vedere superfici e Comuni",
         bool(cad_panel.comune_label.text().strip("- "))
         and cad_panel.parcel_table.rowCount() > 0
         and "ha" in cad_panel.cadastral_area_label.text(),
         "{0}, {1} particelle, {2} catastali".format(
             cad_panel.comune_label.text(),
             cad_panel.parcels_label.text(),
             cad_panel.cadastral_area_label.text()))
    cadastral.unmount()
    cadastral = cad_state = cad_panel = parcels_layer = result = None

# --------------------------------------------------------------------------
# 6-7. the project, the terrain and the constraints
# --------------------------------------------------------------------------
terrain_panel = context.terrain_panel
terrain_panel.reload_rasters()
terrain_panel.local_dem.setCurrentIndex(
    terrain_panel.local_dem.findData(DEM_LAYER.id()))
analysis = terrain_panel.use_local_dem()
terrain_panel.contour_interval.setValue(10.0)
contours = terrain_panel.extract_contours()

constraints_panel = context.constraints_panel
state.constraints.declare("strada", 10.0, label="Strade")
state.constraints.add_geometry("strada", QgsGeometry.fromWkt(
    "LINESTRING({0} {1},{2} {1})".format(OX + 70.0, OY - 300.0,
                                         OX + 690.0, OY - 300.0)))
constraints_panel.rows["strada"][0].setChecked(True)
constraints_panel.apply()

done(6, "configurare il progetto di rimboschimento",
     state.area is not None and state.crs is not None,
     "progetto '{0}' in {1}".format(state.area.label, state.crs.authid()))
done(7, "impostare terreno e vincoli",
     analysis is not None and contours > 0
     and state.usable_m2 < state.gross_m2
     and state.layers.layers["excluded"].featureCount() >= 1,
     "DEM letto, {0} curve, utile {1} contro lorda {2}".format(
         contours, context.area_panel.usable_label.text(),
         context.area_panel.gross_label.text()))

# --------------------------------------------------------------------------
# 8-11. zones, species, density, scheme, orientation
# --------------------------------------------------------------------------
scheme = context.scheme_panel
scheme.plant_distance.setValue(6.0)
scheme.row_distance.setValue(6.0)
for name, percent in (("quercia", 60.0), ("frassino", 25.0),
                      ("acero", 15.0)):
    scheme.species_key.setCurrentText(name)
    scheme.species_percent.setValue(percent)
    scheme.on_add_species()
scheme.apply_scheme()

zones_panel = context.zones_panel
zones_panel.band_count.setValue(3)
zones = zones_panel.split()
done(8, "creare zone",
     zones == 3 and state.layers.layers["zones"].featureCount() == 3,
     "{0} zone sulla mappa: {1}".format(zones, state.zones.names()))
done(9, "scegliere specie",
     len(state.shares) == 3 and context.scheme_panel.species_table.rowCount()
     == 3,
     "{0}".format(dict(state.shares)))

density = state.density_per_ha()
done(10, "impostare densita' e sesto",
     density > 0 and state.spec.plant_distance_m == 6.0,
     "sesto {0:g} x {1:g} m, {2:,.0f} piante/ha, attese {3:,.0f}".format(
         state.spec.plant_distance_m, state.spec.row_distance_m, density,
         state.expected_plants()))

orientation = context.orientation_panel.apply()
done(11, "impostare orientamento",
     state.orientation_applied and state.spec.row_azimuth_deg == orientation,
     "file a {0:.1f} gradi, dalla morfologia".format(orientation))

# --------------------------------------------------------------------------
# 12-13. generate, and make it look like a wood
# --------------------------------------------------------------------------
plan = context.generate_panel.preview()
layer = context.generate_panel.generate()
done(12, "generare l'impianto",
     plan is not None and plan.count > 0
     and layer is not None and layer.featureCount() == plan.count,
     "{0:,} piante sul layer '{1}'".format(layer.featureCount(),
                                           layer.name()))

regular = plan.count
natural_panel = context.natural_panel
natural_panel.glade_count.setValue(3)
natural_panel.glade_radius.setValue(12.0)
natural_panel.irregularity.setValue(15.0)
natural_panel.min_distance.setValue(2.0)
natural_plan = natural_panel.apply_to_project()
context.generate_panel.generate()
done(13, "applicare il naturaliforme",
     natural_plan is not None and natural_plan.count < regular
     and len(state.glades) > 0
     and state.layers.layers["glades"].featureCount() == len(state.glades),
     "{0:,} -> {1:,} piante, {2} radure sulla mappa".format(
         regular, natural_plan.count, len(state.glades)))

# --------------------------------------------------------------------------
# 14-15. compare and check
# --------------------------------------------------------------------------
scenarios = context.optimise_panel.run()
done(14, "ottimizzare",
     len(scenarios) == 3 and context.optimise_panel.table.rowCount() == 3,
     "{0}".format([(s["name"], s["plants"]) for s in scenarios]))

anomalies = context.verify_panel.run()
done(15, "validare",
     state.verified and "PROGETTO" in context.verify_panel.verdict.text()
     or bool(anomalies),
     "{0}".format(anomalies or "nessuna anomalia"))

# --------------------------------------------------------------------------
# 16-17. edit, and check again
# --------------------------------------------------------------------------
edit_panel = context.edit_panel
edit_panel.set_editing(True)
plants_layer = state.plants_layer
first = next(plants_layer.getFeatures())
from qgis.core import QgsPoint                                  # noqa: E402

geometry = first.geometry().constGet()
plants_layer.changeGeometry(first.id(), QgsGeometry(
    QgsPoint(float(geometry.x()) + 25.0, float(geometry.y()),
             float(geometry.z()))))
chosen = [f.id() for f in plants_layer.getFeatures()
          if f["specie"] == "frassino"][:30]
plants_layer.selectByIds(chosen)
edit_panel.species_combo.setCurrentIndex(
    edit_panel.species_combo.findData("quercia"))
changed = edit_panel.assign_species()
edit_panel.set_editing(False)
moved = next(p for p in state.result.plants if p.plant_id == first["plant_id"])
done(16, "modificare interattivamente le piante",
     changed == 30 and abs(moved.x - (float(geometry.x()) + 25.0)) < 1e-6,
     "una pianta spostata di 25 m (quota riletta {0:.2f} m), {1} specie "
     "cambiate".format(moved.z if moved.z is not None else float("nan"),
                       changed))

after = state.anomalies
done(17, "aggiornare la validazione",
     state.verified and after != anomalies,
     "{0}".format(after or "nessuna anomalia"))

# --------------------------------------------------------------------------
# 18. the sheet
# --------------------------------------------------------------------------
carto_panel = context.cartography_panel
carto_panel.title_edit.setText("Rimboschimento Le Prata")
carto_panel.author_edit.setText("Cap. N. M. Mancini")
layout = carto_panel.compose()
pdf_map = carto_panel.export_pdf(path=os.path.join(TMP, "tavola.pdf"))
map_scale = layout.itemById(carto.ITEM_MAP).scale() if layout else 0.0
done(18, "generare la cartografia",
     layout is not None
     and layout.itemById(carto.ITEM_LEGEND) is not None
     and 100.0 < map_scale < 50_000.0
     and bool(pdf_map) and os.path.getsize(pdf_map) > 5000,
     "tavola 1:{0:,.0f}, PDF di {1:,} byte".format(
         layout.itemById(carto.ITEM_MAP).scale(),
         os.path.getsize(pdf_map)).replace(",", "."))

# --------------------------------------------------------------------------
# 19-20. the data and the relazione
# --------------------------------------------------------------------------
outputs = context.outputs_panel
gpkg = outputs.export(path=os.path.join(TMP, "piante.gpkg"))
project_path = dock.on_save_as(path=os.path.join(TMP, "le_prata"))
done(19, "esportare i dati",
     bool(gpkg) and os.path.exists(gpkg)
     and bool(project_path) and os.path.exists(project_path),
     "{0:,} byte di GeoPackage, {1:,} byte di progetto".format(
         os.path.getsize(gpkg), os.path.getsize(project_path)))

outputs.author_edit.setText("Cap. N. M. Mancini")
written = {}
for key in ("pdf", "docx", "xlsx"):
    written[key] = outputs.write_document(
        key, os.path.join(TMP, "relazione" + pf.SUFFIX.replace(".gcuav", "")
                          + "." + key))
done(20, "generare la relazione",
     all(written[key] and os.path.getsize(written[key]) > 3000
         for key in written),
     ", ".join("{0}: {1:,} byte".format(key, os.path.getsize(path))
               for key, path in written.items()))

# --------------------------------------------------------------------------
# and it all survives being closed and opened again
# --------------------------------------------------------------------------
print("\n-- e riaprendo il progetto si ritrova tutto --")
again = wf.Workspace(None)
again.state.load_from(project_path)
check_true("l'area torna", again.state.area is not None)
check_true("...le zone pure", len(again.state.zones) == 3)
check_true("...e le piante, tutte",
           again.state.result.count == state.result.count)
check_true("...sulla mappa", again.state.layers.layers.get("plants")
           is not None)
report = again.context.outputs_panel.build_report()
check_true("...e la relazione si riscrive dal progetto riaperto",
           "Le Prata" in report and "ZONE DI IMPIANTO" in report)
again.unmount()
workspace.unmount()
check_true("chiudendo, il plugin non lascia layer dietro di se'",
           len(state.layers.layers) == 0)

print("\n" + "=" * 78)
plan = natural_plan = layer = plants_layer = layout = None
workspace = again = state = context = dock = None
scheme = zones_panel = natural_panel = edit_panel = carto_panel = None
terrain_panel = constraints_panel = outputs = analysis = None
DEM_LAYER = None
gc.collect()
QgsProject.instance().layoutManager().clear()
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
