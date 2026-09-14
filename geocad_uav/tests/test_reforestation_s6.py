"""
v1.12.0: several species in one stand, the layer that shows them, and a DEM
that arrives on its own.

The percentages are the part that has to be exact: a nursery order is built
from these counts, and a share that rounds the wrong way three times is three
hundred plants. So the counts are checked against largest-remainder
apportionment and against the total, every time, with no rounding slack.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_reforestation_s6.py
"""

import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsGeometry, QgsProject, QgsWkbTypes)

QGS = QgsApplication([], False)
QGS.initQgis()

from geocad_uav.core import grid as grid_mod                    # noqa: E402
from geocad_uav.core.errors import (InvalidInputError,          # noqa: E402
                                    RasterError)
from geocad_uav.forest.reforestation import composition as cp   # noqa: E402
from geocad_uav.forest.reforestation import spacing as sp       # noqa: E402
from geocad_uav.forest.reforestation import species as spx      # noqa: E402
from geocad_uav.forest.reforestation import symbology as sym    # noqa: E402
from geocad_uav.forest.reforestation import terrain as tr       # noqa: E402
from geocad_uav.io import dem_source                            # noqa: E402

FAILURES = []
SKIPS = []
CRS = QgsCoordinateReferenceSystem("EPSG:32632")
TMP = tempfile.mkdtemp(prefix="geocad_s6_")
OX, OY = 500000.0, 5000000.0
TOL = 1e-9


def check(label, got, expected, tol=TOL):
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


def skip(label, reason):
    print("  [skip] {0}\n         reason: {1}".format(label, reason))
    SKIPS.append((label, reason))


# ------------------------------------------------------------- fixtures ---
CELL = 2.0
NX, NY = 200, 200
GRADE = math.tan(math.radians(20.0))
_xs = OX + (np.arange(NX) + 0.5) * CELL
_ys = OY - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(_xs, _ys)
Z = 400.0 - GRADE * (XX - OX)
TERRAIN = tr.TerrainAnalysis.from_array(Z, (OX, CELL, 0.0, OY, 0.0, -CELL),
                                        CRS.authid())
AOI = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 60.0, OY - 300.0, OX + 240.0, OY - 120.0))
SPEC = sp.SlopeSpacing(plant_distance_m=6.0, row_distance_m=6.0,
                       row_azimuth_deg=90.0,
                       pattern=grid_mod.PATTERN_SQUARE)
RESULT = sp.generate(AOI, SPEC, terrain=TERRAIN)

print("=" * 78)
print("S6 -- multi-specie, simbologia, DEM automatico")
print("=" * 78)
print("impianto di prova: {0:,} piante su {1} file".format(
    RESULT.count, RESULT.n_rows))

# --------------------------------------------------------------------------
# M1 - the counts are exact
# --------------------------------------------------------------------------
print("\n== M1: le percentuali diventano conteggi esatti ==")
SHARES = [("sp_a", 40.0), ("sp_b", 35.0), ("sp_c", 25.0)]
mix = cp.Mix(SHARES)
check("le quote dichiarate sommano a 100", mix.declared_total, 100.0, TOL)
check_true("...quindi nessun avviso", mix.check_total() is None)

for population in (1, 7, 100, 999, RESULT.count):
    counts = mix.counts_for(population)
    check("{0:,} piante si dividono senza avanzi".format(population),
          sum(counts.values()), population)
    check_true("{0:,}: ogni specie ha un conteggio".format(population),
               set(counts) == {"sp_a", "sp_b", "sp_c"})

counts = mix.counts_for(1000)
print("        1 000 piante -> {0}".format(sorted(counts.items())))
check("il 40 % di 1 000 e' 400", counts["sp_a"], 400)
check("il 35 % e' 350", counts["sp_b"], 350)
check("il 25 % e' 250", counts["sp_c"], 250)

awkward = cp.Mix([("sp_a", 33.3333), ("sp_b", 33.3333), ("sp_c", 33.3334)])
odd = awkward.counts_for(100)
print("        tre terzi di 100 -> {0}".format(sorted(odd.items())))
check("cento in tre parti fanno comunque cento", sum(odd.values()), 100)
check_true("...e nessuna parte e' vuota", all(v > 0 for v in odd.values()))
check_true("la differenza fra le parti e' al piu' una pianta",
           max(odd.values()) - min(odd.values()) <= 1)

print("\n-- quote che non sommano a 100 --")
loose = cp.Mix([("sp_a", 30.0), ("sp_b", 30.0)])
check_true("vengono riscalate, con un avviso", loose.check_total() is not None)
loose_counts = loose.counts_for(200)
check("e il totale resta quello richiesto", sum(loose_counts.values()), 200)
check("...spartito a meta'", loose_counts["sp_a"], 100)

check_raises("una quota negativa e' un errore", InvalidInputError,
             cp.SpeciesShare, "sp_a", -1.0)
check_raises("una quota nulla pure", InvalidInputError, cp.SpeciesShare,
             "sp_a", 0.0)
check_raises("una specie senza codice", InvalidInputError, cp.SpeciesShare,
             "", 50.0)
check_raises("una composizione vuota", InvalidInputError, cp.Mix, [])
check_raises("la stessa specie due volte", InvalidInputError, cp.Mix,
             [("sp_a", 50.0), ("sp_a", 50.0)])

print("\n-- e le specie devono esistere in catalogo --")
catalog = spx.SpeciesCatalog.from_layer(spx.SpeciesCatalog.memory_layer())
catalog.add(spx.Species(key="sp_a", name="Specie A"))
catalog.add(spx.Species(key="sp_b", name="Specie B"))
check_raises("una specie non in catalogo e' rifiutata", InvalidInputError,
             cp.Mix, SHARES, catalog)
catalog.add(spx.Species(key="sp_c", name="Specie C"))
check_true("con tutte e tre in catalogo la composizione si costruisce",
           cp.Mix(SHARES, catalog) is not None)

# --------------------------------------------------------------------------
# M2 - the assignment, and what it actually produced
# --------------------------------------------------------------------------
print("\n== M2: assegnazione alle piante ==")
for layout in cp.LAYOUTS:
    fresh = sp.generate(AOI, SPEC, terrain=TERRAIN)
    outcome = cp.assign(fresh.plants, cp.Mix(SHARES, seed=3), layout=layout)
    wanted = cp.Mix(SHARES).counts_for(fresh.count)
    print("        {0:<10} {1}".format(
        layout, {k: outcome.counts[k] for k in sorted(outcome.counts)}))
    check("{0}: ogni pianta ha una specie".format(layout),
          len(outcome.assigned), fresh.count)
    check("{0}: i conteggi sommano al totale".format(layout),
          outcome.total, fresh.count)
    check_true("{0}: e sono esattamente quelli richiesti".format(layout),
               outcome.counts == wanted)
    achieved = cp.achieved_percentages(fresh.plants)
    for key, percent in sorted(achieved.items()):
        check("{0}: {1} arriva alla sua quota".format(layout, key), percent,
              100.0 * wanted[key] / fresh.count, 1e-9)
    check_true("{0}: la specie e' leggibile sul record".format(layout),
               all(cp.species_of(p) for p in fresh.plants))

print("\n-- uniforme vuol dire mescolata anche nella prima meta' --")
uniform = sp.generate(AOI, SPEC, terrain=TERRAIN)
cp.assign(uniform.plants, cp.Mix(SHARES), layout=cp.LAYOUT_UNIFORM)
half = uniform.plants[:len(uniform.plants) // 2]
half_share = cp.achieved_percentages(half)
print("        prima meta': {0}".format(
    {k: round(v, 1) for k, v in sorted(half_share.items())}))
check_true("tutte e tre le specie sono gia' nella prima meta'",
           set(half_share) == {"sp_a", "sp_b", "sp_c"})
check("...e sp_a e' gia' vicina al suo 40 %", half_share["sp_a"], 40.0,
      1.5)

grouped = sp.generate(AOI, SPEC, terrain=TERRAIN)
cp.assign(grouped.plants, cp.Mix(SHARES, seed=5), layout=cp.LAYOUT_GROUPS,
          group_size=12)
by_row = {}
for plant in grouped.plants:
    by_row.setdefault(plant.row_id, []).append(plant)
runs = 0
for plants in by_row.values():
    plants.sort(key=lambda p: p.seq_in_row)
    for first, second in zip(plants, plants[1:]):
        if cp.species_of(first) != cp.species_of(second):
            runs += 1
uniform_runs = 0
for plants in by_row.values():
    for first, second in zip(plants, plants[1:]):
        pass
print("        cambi di specie fra vicini: {0} a gruppi".format(runs))
check_true("a gruppi le specie cambiano meno spesso che a caso",
           runs < len(grouped.plants) / 2)

rows_mode = sp.generate(AOI, SPEC, terrain=TERRAIN)
cp.assign(rows_mode.plants, cp.Mix([("sp_a", 50.0), ("sp_b", 50.0)]),
          layout=cp.LAYOUT_ROWS)
row_species = {}
for plant in rows_mode.plants:
    row_species.setdefault(plant.row_id, set()).add(cp.species_of(plant))
pure_rows = sum(1 for names in row_species.values() if len(names) == 1)
print("        file di una sola specie: {0} su {1}".format(
    pure_rows, len(row_species)))
check_true("per file, quasi ogni fila e' di una specie sola",
           pure_rows >= len(row_species) - 2)

print("\n-- la stessa semente da lo stesso impianto --")
first_run = sp.generate(AOI, SPEC, terrain=TERRAIN)
second_run = sp.generate(AOI, SPEC, terrain=TERRAIN)
cp.assign(first_run.plants, cp.Mix(SHARES, seed=42), cp.LAYOUT_GROUPS)
cp.assign(second_run.plants, cp.Mix(SHARES, seed=42), cp.LAYOUT_GROUPS)
check_true("assegnazione riproducibile",
           [cp.species_of(p) for p in first_run.plants]
           == [cp.species_of(p) for p in second_run.plants])
third_run = sp.generate(AOI, SPEC, terrain=TERRAIN)
cp.assign(third_run.plants, cp.Mix(SHARES, seed=7), cp.LAYOUT_GROUPS)
check_true("...e una semente diversa da una disposizione diversa",
           [cp.species_of(p) for p in third_run.plants]
           != [cp.species_of(p) for p in first_run.plants])
check_raises("una disposizione inesistente e' un errore", InvalidInputError,
             cp.assign, first_run.plants, cp.Mix(SHARES), "a caso")
empty = cp.assign([], cp.Mix(SHARES))
check("nessuna pianta, nessuna assegnazione", empty.total, 0)

# --------------------------------------------------------------------------
# M3 - the layer: PointZ, one column per thing, coloured by species
# --------------------------------------------------------------------------
print("\n== M3: il layer delle piante ==")
plan = sp.generate(AOI, SPEC, terrain=TERRAIN)
outcome = cp.assign(plan.plants, cp.Mix(SHARES, seed=11),
                    layout=cp.LAYOUT_UNIFORM)
layer = sp.plants_layer(plan, CRS.authid(), name="Piante di prova",
                        zone="Zona 1")
QgsProject.instance().addMapLayer(layer)
check("una feature per pianta", layer.featureCount(), plan.count)
check_true("il layer e' PointZ",
           QgsWkbTypes.hasZ(layer.wkbType())
           and QgsWkbTypes.geometryType(layer.wkbType())
           == QgsWkbTypes.PointGeometry)
for name, _kind in sp.PLANT_FIELDS:
    check_true("la colonna {0} c'e'".format(name),
               layer.fields().indexOf(name) >= 0)
sample = next(layer.getFeatures())
check_true("ogni pianta porta il suo identificativo, la fila e la zona",
           sample["plant_id"] is not None and sample["row_id"] is not None
           and sample["zona"] == "Zona 1")
check_true("...e la sua specie", sample["specie"] in {"sp_a", "sp_b", "sp_c"})
check_true("la Z e' quella del DEM",
           abs(sample.geometry().constGet().z()
               - float(TERRAIN.elevation_at(sample.geometry().constGet().x(),
                                            sample.geometry().constGet().y())))
           < 1e-9)
layer_counts = {}
for feature in layer.getFeatures():
    key = feature["specie"]
    layer_counts[key] = layer_counts.get(key, 0) + 1
check_true("i conteggi sul layer sono quelli della composizione",
           layer_counts == outcome.counts)

print("\n-- e sono colorate una per specie --")
check_true("il renderer e' categorizzato",
           type(layer.renderer()).__name__ == "QgsCategorizedSymbolRenderer")
check_true("...sulla colonna specie",
           layer.renderer().classAttribute() == sym.SPECIES_FIELD)
entries = sym.legend(layer)
print("        legenda: {0}".format(entries))
check("tre specie piu' la voce 'non assegnata'", len(entries), 4)
colours = [colour for value, colour in entries if value]
check("ogni specie ha un colore diverso", len(set(colours)), 3)
check_true("nessun colore e' vuoto", all(colours))
check_true("le tonalita' sono distanti sul cerchio cromatico",
           abs(sym.hue_for(0) - sym.hue_for(1)) > 0.2)
check_true("il prospetto della simbologia si legge",
           any("specie" in line for line in sym.describe(layer)))

again = sp.plants_layer(plan, CRS.authid(), name="Piante di nuovo",
                        zone="Zona 1")
check_true("gli stessi dati danno gli stessi colori",
           sym.legend(again) == entries)
check("una colonna che non esiste non viene colorata",
      sym.apply_species_symbology(layer, "colonna_inesistente"), 0)

# --------------------------------------------------------------------------
# M4 - the DEM that fetches itself
# --------------------------------------------------------------------------
print("\n== M4: DEM automatico sul bbox della superficie utile ==")
check_true("gli adattatori DEM esistono gia', con il loro stato",
           len(dem_source.ADAPTER_ORDER) >= 4)
for adapter_id, label, status, note, url in dem_source.status_table():
    print("        {0:<18} {1:<12} {2}".format(adapter_id, status, label))
    if status == dem_source.UNSUPPORTED:
        check_true("{0} e' rifiutato e spiega perche'".format(adapter_id),
                   bool(note))
    else:
        check_true("{0} dichiara la fonte con un URL".format(adapter_id),
                   url.startswith("http"))

REQUESTED = []


def fake_transport(url, timeout=120.0):
    """Stands in for the network: records the URL, returns a real GeoTIFF."""
    REQUESTED.append(url)
    with open(DEM_PATH, "rb") as handle:
        yield handle.read()


from osgeo import gdal, osr                                     # noqa: E402

DEM_PATH = os.path.join(TMP, "srtm.tif")
gdal.UseExceptions()
_ds = gdal.GetDriverByName("GTiff").Create(DEM_PATH, 60, 60, 1,
                                           gdal.GDT_Float32)
_ds.SetGeoTransform((8.98, 0.0005, 0.0, 45.16, 0.0, -0.0005))
_srs = osr.SpatialReference()
_srs.ImportFromEPSG(4326)
_ds.SetProjection(_srs.ExportToWkt())
_ds.GetRasterBand(1).WriteArray(
    np.linspace(300.0, 400.0, 60 * 60).reshape(60, 60).astype(np.float32))
_ds.FlushCache()
_ds = None

check_raises("un'area senza CRS non scarica niente", InvalidInputError,
             tr.TerrainAnalysis.auto_download, AOI, None)
# KeyError, loudly, from dem_source.adapter(): a typo in an adapter id is a
# programming mistake, not something to soften into a message.
check_raises("un adattatore inesistente fallisce rumorosamente", KeyError,
             tr.TerrainAnalysis.auto_download, AOI, CRS, "inventato")
for adapter_id in dem_source.ADAPTER_ORDER:
    spec = dem_source.adapter(adapter_id)
    if spec.status == dem_source.VERIFIED:
        continue
    check_raises(
        "{0} e' {1}: non scarica, e dice perche'".format(adapter_id,
                                                          spec.status),
        RasterError, tr.TerrainAnalysis.auto_download, AOI, CRS, adapter_id,
        None, 50.0, "chiave-finta", fake_transport)

verified = [a for a in dem_source.ADAPTER_ORDER
            if dem_source.adapter(a).status == dem_source.VERIFIED]
check_true("almeno un adattatore e' verificato", bool(verified))
analysis = tr.TerrainAnalysis.auto_download(
    AOI, CRS, verified[0], work_crs=CRS, margin_m=50.0,
    key="chiave-finta", transport=fake_transport,
    cache_dir=os.path.join(TMP, "cache"))
check_true("il DEM e' stato scaricato e aperto", analysis is not None)
check("una sola richiesta e' stata fatta", len(REQUESTED), 1)
requested = REQUESTED[0]
print("        richiesta: {0}".format(dem_source.mask_url(requested)[:110]))
check_true("la chiave non compare mai in chiaro in un log",
           "chiave-finta" not in dem_source.mask_url(requested))
check_true("la finestra chiesta e' quella dell'area, in gradi",
           "8.9" in requested or "9.0" in requested)
check_true("...e non l'intero pianeta",
           "-180" not in requested and "90.0" not in requested)
check_true("il modello ha quote vere",
           np.isfinite(analysis.model.z).any())
check_true("...e si puo' interrogare come qualunque altro",
           analysis.cell_area_m2 > 0.0)

REQUESTED[:] = []
again_analysis = tr.TerrainAnalysis.auto_download(
    AOI, CRS, verified[0], work_crs=CRS, margin_m=50.0,
    key="chiave-finta", transport=fake_transport,
    cache_dir=os.path.join(TMP, "cache"))
check_true("la seconda volta arriva dalla cache", again_analysis is not None)
check("...senza toccare la rete", len(REQUESTED), 0)

# --------------------------------------------------------------------------
# M5 - the two alignment options are gone
# --------------------------------------------------------------------------
# v1.36.0: quelle due opzioni stavano su ForestPanel, il pannello che
# rifaceva il rimboschimento in piccolo dentro il dock CAD. Il pannello non
# c'e' piu': il modulo vive solo come percorso di step, e questo e' cio' che
# va verificato adesso.
print("\n== M5: il pannello rimboschimento duplicato non esiste piu' ==")
import os as _os                                                # noqa: E402

_gui_dir = _os.path.join(_os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))), "gui")
check_true("gui/forest_panel.py e' stato rimosso",
           not _os.path.exists(_os.path.join(_gui_dir, "forest_panel.py")))
_dock_source = open(_os.path.join(_gui_dir, "dock.py"),
                    encoding="utf-8").read()
check_true("il dock CAD non lo importa ne' lo nomina",
           "forest_panel" not in _dock_source
           and "ForestPanel" not in _dock_source)
check_true("...e non ha piu' una scheda Rimboschimento",
           'tr("Rimboschimento")' not in _dock_source)
_imported = True
try:
    from geocad_uav.gui import forest_panel                     # noqa: F401
except ImportError:
    _imported = False
check_true("...e il modulo non e' nemmeno importabile", not _imported)


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
