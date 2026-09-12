"""
v1.9.0 (S5a): density <-> spacing, and the species catalogue.

Two things a scheme needs before anything is generated: the arithmetic that
turns "1 200 piante per ettaro" into "tre metri per tre" and back without
losing a digit, and a species table the operator owns rather than one shipped
inside the plugin.

Every conversion here is checked against its closed form and against its own
inverse, to 1e-9 -- and the density the generator actually produces on a
known slope is measured, not assumed.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_reforestation_s5.py
"""

import ast
import gc
import inspect
import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsGeometry, QgsProject, QgsVectorDataProvider)

QGS = QgsApplication([], False)
QGS.initQgis()

from geocad_uav.core import grid as grid_mod                    # noqa: E402
from geocad_uav.core.errors import (GeoCadError,                # noqa: E402
                                    InvalidInputError)
from geocad_uav.forest.planting import TopographicFilter        # noqa: E402
from geocad_uav.forest.reforestation import density as dn       # noqa: E402
from geocad_uav.forest.reforestation import spacing as sp       # noqa: E402
from geocad_uav.forest.reforestation import species as spx      # noqa: E402
from geocad_uav.forest.reforestation import terrain as tr       # noqa: E402

FAILURES = []
SKIPS = []
CRS = QgsCoordinateReferenceSystem("EPSG:32632")
TMP = tempfile.mkdtemp(prefix="geocad_s5_")
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


print("=" * 78)
print("S5a -- densita' e catalogo delle specie")
print("=" * 78)

# --------------------------------------------------------------------------
# D1 - the conversion is its own inverse, for every scheme
# --------------------------------------------------------------------------
print("\n== D1: densita' e distanze, andata e ritorno ==")
for pattern in sp.ALL_PATTERNS:
    area = dn.area_per_plant_m2(pattern, 3.0, 3.0)
    density = dn.density_from_spacing(pattern, 3.0, 3.0)
    plant, row = dn.spacing_from_density(density, pattern)
    back = dn.density_from_spacing(pattern, plant, row)
    print("        {0:<12} area {1:8.6f} m2  D {2:10.6f}/ha  -> {3:.6f} x "
          "{4:.6f}".format(pattern, area, density, plant, row))
    check("{0}: la densita' e' un ettaro diviso l'area per pianta".format(
        pattern), density, dn.M2_PER_HA / area, TOL)
    check("{0}: e il ritorno e' esatto".format(pattern), back, density, TOL)
    check("{0}: l'area ricostruita coincide".format(pattern),
          dn.area_per_plant_m2(pattern, plant, row), area, TOL)

# --------------------------------------------------------------------------
# D2 - against the closed forms, not against a previous run
# --------------------------------------------------------------------------
print("\n== D2: forme chiuse ==")
check("un sesto quadrato di 3 m fa 10 000 / 9 piante per ettaro",
      dn.density_from_spacing(grid_mod.PATTERN_SQUARE, 3.0), 10_000.0 / 9.0,
      TOL)
check("un rettangolare 3 x 4 ne fa 10 000 / 12",
      dn.density_from_spacing(grid_mod.PATTERN_RECT, 3.0, 4.0),
      10_000.0 / 12.0, TOL)
check("un esagonale di 3 m ne fa 20 000 / (9 radice di 3)",
      dn.density_from_spacing(grid_mod.PATTERN_HEX, 3.0),
      20_000.0 / (9.0 * math.sqrt(3.0)), TOL)
check("...cioe' piu' del quadrato di pari distanza, del 15.47 %",
      dn.density_from_spacing(grid_mod.PATTERN_HEX, 3.0)
      / dn.density_from_spacing(grid_mod.PATTERN_SQUARE, 3.0),
      2.0 / math.sqrt(3.0), TOL)
check("il quinconce ha la cella del rettangolare: sfalsa, non stringe",
      dn.density_from_spacing(grid_mod.PATTERN_QUINCUNX, 3.0, 4.0),
      dn.density_from_spacing(grid_mod.PATTERN_RECT, 3.0, 4.0), TOL)
check("1 111.111 piante/ha su schema quadrato danno il lato di 3 m",
      dn.plant_distance_from_density(10_000.0 / 9.0,
                                     grid_mod.PATTERN_SQUARE), 3.0, TOL)
check("un ettaro a 1 200 piante/ha ne vuole 1 200",
      dn.plants_for_area(1_200.0, 10_000.0), 1_200.0, TOL)
check("...e mezzo ettaro ne vuole 600", dn.plants_for_area(1_200.0, 5_000.0),
      600.0, TOL)

print("\n-- il rapporto fra le distanze, quando lo schema lo concede --")
plant, row = dn.spacing_from_density(1_000.0, grid_mod.PATTERN_RECT, 2.0)
print("        1 000 piante/ha, interfila doppia: {0:.6f} x {1:.6f}".format(
    plant, row))
check("il rapporto richiesto e' rispettato", row / plant, 2.0, TOL)
check("...e la densita' esce esatta",
      dn.density_from_spacing(grid_mod.PATTERN_RECT, plant, row), 1_000.0,
      TOL)
check("uno schema quadrato ignora il rapporto: sarebbe un rettangolo",
      dn.pattern_ratio(grid_mod.PATTERN_SQUARE, 3.7), 1.0, TOL)
check("...e l'esagonale impone radice di 3 mezzi",
      dn.pattern_ratio(grid_mod.PATTERN_HEX, 3.7), math.sqrt(3.0) / 2.0, TOL)
check("un rettangolare invece ascolta",
      dn.pattern_ratio(grid_mod.PATTERN_RECT, 3.7), 3.7, TOL)

# --------------------------------------------------------------------------
# D3 - which hectare is being counted
# --------------------------------------------------------------------------
print("\n== D3: ettaro di carta ed ettaro di terreno ==")
SLOPE = 30.0
COS = math.cos(math.radians(SLOPE))
D_REAL = 10_000.0 / 9.0                     # 3 x 3 m sul terreno
print("        {0:.4f} piante/ha reali su pendenza {1:.0f} deg".format(
    D_REAL, SLOPE))
check("la superficie reale sotto un ettaro di carta e' 1/cos",
      dn.surface_area_m2(10_000.0, SLOPE), 10_000.0 / COS, TOL)
check("la densita' sulla carta prodotta dalla correzione isotropa e' D/cos^2",
      dn.map_density_from_real(D_REAL, SLOPE), D_REAL / (COS * COS), TOL)
check("...e il suo inverso torna indietro",
      dn.real_density_from_map(dn.map_density_from_real(D_REAL, SLOPE),
                               SLOPE), D_REAL, TOL)
check("quella implicata dalla superficie e' invece D/cos",
      dn.surface_density_from_real(D_REAL, SLOPE), D_REAL / COS, TOL)
check_true("la isotropa e' la piu' fitta delle due",
           dn.map_density_from_real(D_REAL, SLOPE)
           > dn.surface_density_from_real(D_REAL, SLOPE))
check("l'eccesso e' 1/cos - 1, cioe' il 15.47 % a 30 gradi",
      dn.isotropy_excess(SLOPE), 1.0 / COS - 1.0, TOL)
check("in piano non c'e' nessun eccesso", dn.isotropy_excess(0.0), 0.0, TOL)
check("...e nessuna correzione", dn.map_density_from_real(D_REAL, 0.0),
      D_REAL, TOL)

print("\n-- e il generatore produce davvero quella densita' --")
CELL = 2.0
NX, NY = 300, 300
GRADE = math.tan(math.radians(SLOPE))
_xs = OX + (np.arange(NX) + 0.5) * CELL
_ys = OY - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(_xs, _ys)
Z = 500.0 - GRADE * (XX - OX)
TERRAIN = tr.TerrainAnalysis.from_array(Z, (OX, CELL, 0.0, OY, 0.0, -CELL),
                                        CRS.authid())
AOI = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 100.0, OY - 400.0, OX + 220.0, OY - 280.0))
spec = sp.SlopeSpacing(plant_distance_m=3.0, row_distance_m=3.0,
                       row_azimuth_deg=90.0,
                       pattern=grid_mod.PATTERN_SQUARE)
result = sp.generate(AOI, spec, terrain=TERRAIN)
rows = {}
for plant_record in result.plants:
    rows.setdefault(plant_record.row_id, []).append(plant_record)
sample = sorted(rows[min(rows)], key=lambda p: p.seq_in_row)
measured_step = math.hypot(sample[1].x - sample[0].x,
                           sample[1].y - sample[0].y)
starts = sorted((min(v, key=lambda p: p.seq_in_row) for v in rows.values()),
                key=lambda p: p.y)
measured_row = math.hypot(starts[1].x - starts[0].x,
                          starts[1].y - starts[0].y)
measured_cell = measured_step * measured_row
print("        cella misurata {0:.9f} m2, attesa {1:.9f}".format(
    measured_cell, 9.0 * COS * COS))
check("la cella planimetrica misurata e' l'area reale per cos^2",
      measured_cell, 9.0 * COS * COS, TOL)
check("la densita' sulla carta che ne segue e' quella calcolata",
      dn.M2_PER_HA / measured_cell, dn.map_density_from_real(D_REAL, SLOPE),
      1e-6)
check_true("il conteggio reale ci sta intorno, a meno dei bordi",
           abs(result.density_per_ha()
               - dn.map_density_from_real(D_REAL, SLOPE)) < 60.0)

print("\n-- parametri impossibili --")
check_raises("densita' nulla", InvalidInputError, dn.spacing_from_density,
             0.0, grid_mod.PATTERN_SQUARE)
check_raises("densita' negativa", InvalidInputError, dn.spacing_from_density,
             -5.0, grid_mod.PATTERN_SQUARE)
check_raises("densita' non finita", InvalidInputError,
             dn.spacing_from_density, float("nan"), grid_mod.PATTERN_SQUARE)
check_raises("schema sconosciuto", InvalidInputError, dn.density_from_spacing,
             "a caso", 3.0)
check_raises("distanza nulla", InvalidInputError, dn.area_per_plant_m2,
             grid_mod.PATTERN_SQUARE, 0.0)
check_raises("pendenza verticale", InvalidInputError, dn.surface_area_m2,
             10_000.0, 90.0)
check_raises("pendenza non finita", InvalidInputError,
             dn.map_density_from_real, 1_000.0, float("inf"))
check_true("il prospetto dice su quale ettaro sta contando",
           any("carta" in line for line in dn.describe(
               grid_mod.PATTERN_SQUARE, 3.0, 3.0, SLOPE)))

# --------------------------------------------------------------------------
# S1 - a new catalogue is empty: no species ships with the plugin
# --------------------------------------------------------------------------
print("\n== S1: il catalogo nasce vuoto ==")
CATALOGUE = os.path.join(TMP, "specie.gpkg")
catalog = spx.SpeciesCatalog.create(CATALOGUE)
check("un catalogo nuovo non contiene nessuna specie", catalog.count, 0)
check_true("il file esiste ed e' un GeoPackage",
           os.path.isfile(CATALOGUE) and CATALOGUE.endswith(".gpkg"))
shipped = [name for name, value in vars(spx).items()
           if isinstance(value, (list, tuple))
           and any(isinstance(item, spx.Species) for item in value)]
check("nessun elenco di specie e' cablato nel modulo", len(shipped), 0)
# The scan is on the code with its docstrings removed: prose may name a
# species as an example, and grepping prose is how a test ends up asserting
# the wording of a comment instead of the behaviour of the module.
_tree = ast.parse(inspect.getsource(spx))
for _node in ast.walk(_tree):
    if isinstance(_node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
        if (_node.body and isinstance(_node.body[0], ast.Expr)
                and isinstance(_node.body[0].value, ast.Constant)
                and isinstance(_node.body[0].value.value, str)):
            _node.body.pop(0)
code_only = ast.dump(_tree).lower()
check_true("...e nessun nome di specie compare nel codice",
           not any(word in code_only
                   for word in ("leccio", "faggio", "quercus", "pinus",
                                "abies", "castanea", "specie di prova")))
check("le colonne dichiarate sono quelle attese", len(spx.FIELD_NAMES), 11)
check_true("ogni colonna ha un'etichetta italiana",
           all(spx.FIELD_LABELS.get(name) for name in spx.FIELD_NAMES))

# --------------------------------------------------------------------------
# S2 - the table is editable, in QGIS as well as from here
# --------------------------------------------------------------------------
print("\n== S2: la tabella e' modificabile ==")
capabilities = catalog.layer.dataProvider().capabilities()
for flag, label in (
        (QgsVectorDataProvider.Capability.AddFeatures, "aggiungere righe"),
        (QgsVectorDataProvider.Capability.DeleteFeatures, "eliminarle"),
        (QgsVectorDataProvider.Capability.ChangeAttributeValues,
         "modificarne i valori")):
    check_true("il provider permette di {0}".format(label),
               bool(capabilities & flag))
check_true("la tabella non ha geometria: una specie non e' un luogo",
           not catalog.layer.isSpatial())
for name in spx.FIELD_NAMES:
    check_true("la colonna {0} e' nel file".format(name),
               catalog.layer.fields().indexOf(name) >= 0)

# --------------------------------------------------------------------------
# S3 - write, read back, change, delete, reopen
# --------------------------------------------------------------------------
print("\n== S3: scrittura, rilettura, modifica, cancellazione ==")
first = spx.Species(key="sp1", name="Specie di prova",
                    scientific_name="Genus species",
                    min_distance_m=2.5, recommended_density_per_ha=1_100.0,
                    elev_max_m=900.0, slope_max_deg=35.0,
                    notes="riga scritta dal test")
second = spx.Species(key="sp2", name="Seconda specie",
                     recommended_density_per_ha=1_600.0,
                     elev_min_m=800.0, elev_max_m=1_700.0,
                     aspect_from_deg=315.0, aspect_to_deg=45.0)
catalog.add(first)
catalog.add(second)
check("due specie in catalogo", catalog.count, 2)
check_true("...e i loro codici sono quelli scritti",
           set(catalog.keys()) == {"sp1", "sp2"})

read_back = catalog.get("sp1")
check("la distanza minima e' quella scritta", read_back.min_distance_m, 2.5,
      TOL)
check("la densita' consigliata pure",
      read_back.recommended_density_per_ha, 1_100.0, TOL)
check("il limite di quota massima e' tornato indietro",
      read_back.elev_max_m, 900.0, TOL)
check_true("un limite non impostato torna None, non zero",
           read_back.elev_min_m is None
           and read_back.aspect_from_deg is None)
check_true("il nome scientifico e le note sopravvivono",
           read_back.scientific_name == "Genus species"
           and "test" in read_back.notes)

read_back.min_distance_m = 4.0
read_back.elev_max_m = 1_000.0
catalog.update(read_back)
check("la modifica e' salvata", catalog.get("sp1").min_distance_m, 4.0, TOL)
check("...su tutte le colonne toccate", catalog.get("sp1").elev_max_m,
      1_000.0, TOL)

catalog.remove("sp2")
check("la cancellazione toglie una riga", catalog.count, 1)
check_true("...quella giusta", catalog.keys() == ["sp1"])

reopened = spx.SpeciesCatalog.open(CATALOGUE)
check("riaprendo il file si ritrova quello che c'era", reopened.count, 1)
check("...con i valori modificati", reopened.get("sp1").min_distance_m, 4.0,
      TOL)
check_true("il prospetto elenca le specie",
           any("Specie di prova" in line for line in reopened.describe()))

print("\n-- quello che il catalogo rifiuta --")
check_raises("una specie senza codice", InvalidInputError, spx.Species, "")
check_raises("una distanza minima negativa", InvalidInputError, spx.Species,
             "x", "X", "", -1.0)
check_raises("un intervallo di quota rovesciato", InvalidInputError,
             spx.Species, "x", "X", "", 0.0, 0.0, 900.0, 100.0)
check_raises("un codice gia' in catalogo", InvalidInputError, catalog.add,
             spx.Species(key="sp1"))
check_raises("una specie che non c'e'", InvalidInputError, catalog.get, "sp9")
check_raises("...anche a cancellarla", InvalidInputError, catalog.remove,
             "sp9")
check_raises("un catalogo che non esiste", GeoCadError,
             spx.SpeciesCatalog.open, os.path.join(TMP, "assente.gpkg"))
check_raises("sovrascrivere senza dirlo", GeoCadError,
             spx.SpeciesCatalog.create, CATALOGUE)
check_true("dirlo invece funziona",
           spx.SpeciesCatalog.create(
               os.path.join(TMP, "secondo.gpkg"),
               species=[spx.Species(key="sp3")]).count == 1)
check_true("...e sovrascrivere un file che c'e' gia', avendolo detto",
           spx.SpeciesCatalog.create(
               os.path.join(TMP, "secondo.gpkg"),
               species=[spx.Species(key="sp3"), spx.Species(key="sp4")],
               overwrite=True).count == 2)

# --------------------------------------------------------------------------
# S4 - a species' limits are the planner's own filter
# --------------------------------------------------------------------------
print("\n== S4: i limiti ecologici parlano la lingua del pianificatore ==")
limited = spx.Species(key="sp4", name="Specie di quota",
                      elev_min_m=800.0, elev_max_m=1_200.0,
                      slope_max_deg=25.0,
                      aspect_from_deg=315.0, aspect_to_deg=45.0)
criteria = limited.topographic_filter()
check_true("i limiti diventano un TopographicFilter",
           isinstance(criteria, TopographicFilter) and criteria.is_active)
check("la quota minima e' passata", criteria.elev_min_m, 800.0, TOL)
check("la massima anche", criteria.elev_max_m, 1_200.0, TOL)
check("e la pendenza massima", criteria.slope_max_deg, 25.0, TOL)
check("il settore di esposizione e' uno solo", len(criteria.aspect_ranges), 1)
check_true("...e attraversa il nord senza spezzarsi",
           criteria.aspect_ranges[0] == (315.0, 45.0))

keep, reasons = criteria.evaluate(
    np.array([10.0, 10.0, 40.0, 10.0]),          # pendenze
    np.array([0.0, 0.0, 0.0, 180.0]),            # esposizioni
    np.array([1_000.0, 500.0, 1_000.0, 1_000.0]))  # quote
check("una cella dentro tutti i limiti passa", float(keep[0]), 1.0)
check("una troppo bassa no", float(keep[1]), 0.0)
check("una troppo ripida no", float(keep[2]), 0.0)
check("una esposta a sud no", float(keep[3]), 0.0)
check_true("e ognuna dice perche'",
           reasons[1] and reasons[2] and reasons[3])

mask = TERRAIN.suitability(spx.Species(
    key="sp5", slope_max_deg=20.0).topographic_filter())
check_true("gli stessi limiti disegnano una maschera sul DEM",
           mask.n_cells == NX * NY)
check("...e su un piano a 30 gradi non e' idoneo quasi nulla",
      mask.suitable_cells, int(np.count_nonzero(
          np.nan_to_num(TERRAIN.grids()[0], nan=1e9) <= 20.0)))

print("\n-- e la densita' consigliata diventa un sesto --")
dense = spx.Species(key="sp6", recommended_density_per_ha=1_600.0)
plant, row = dense.spacing(grid_mod.PATTERN_SQUARE)
print("        1 600 piante/ha -> {0:.6f} x {1:.6f} m".format(plant, row))
check("il lato e' la radice di 10 000 diviso la densita'", plant,
      math.sqrt(10_000.0 / 1_600.0), TOL)
check("...ed e' quello che direbbe il modulo densita'", plant,
      dn.plant_distance_from_density(1_600.0, grid_mod.PATTERN_SQUARE), TOL)
check_true("la distanza minima e' una soglia, non un suggerimento",
           spx.Species(key="sp7", min_distance_m=3.0
                       ).respects_min_distance(3.0)
           and not spx.Species(key="sp7", min_distance_m=3.0
                               ).respects_min_distance(2.99))
check_raises("una specie senza densita' consigliata non inventa un sesto",
             InvalidInputError, spx.Species(key="sp8").spacing,
             grid_mod.PATTERN_SQUARE)

# --------------------------------------------------------------------------
# S5 - a catalogue that never touches the disk
# --------------------------------------------------------------------------
print("\n== S5: catalogo in memoria, senza file ==")
memory = spx.SpeciesCatalog.from_layer(spx.SpeciesCatalog.memory_layer())
check("nasce vuoto anche questo", memory.count, 0)
memory.add(spx.Species(key="m1", name="Provvisoria",
                       recommended_density_per_ha=900.0))
check("accetta una specie", memory.count, 1)
check("...e la restituisce intera",
      memory.get("m1").recommended_density_per_ha, 900.0, TOL)
check_true("il prospetto dice che non c'e' un archivio",
           any("memoria" in line for line in memory.describe()))
check_true("la tabella in memoria ha le stesse colonne del file",
           [f.name() for f in memory.layer.fields()] == list(spx.FIELD_NAMES))


print("\n" + "=" * 78)
catalog = reopened = memory = None
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
