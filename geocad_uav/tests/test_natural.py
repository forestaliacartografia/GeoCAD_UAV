"""
v1.16.0: glades, controlled irregularity, and the minimum distance that has
to survive both.

The last of the three is the one that matters: everything else in this module
makes the plan less regular, and this is the check that less regular never
became wrong. So it is verified pair by pair, against each species' own
minimum, on the stand that actually comes out -- not on the settings that
went in.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_natural.py
"""

import gc
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsGeometry, QgsPointXY, QgsProject)

QGS = QgsApplication([], True)
QGS.initQgis()

from geocad_uav.core import grid as grid_mod                    # noqa: E402
from geocad_uav.core.errors import (EmptyAoiError,              # noqa: E402
                                    InvalidInputError)
from geocad_uav.forest.reforestation import composition as cp   # noqa: E402
from geocad_uav.forest.reforestation import natural as nt       # noqa: E402
from geocad_uav.forest.reforestation import spacing as sp       # noqa: E402
from geocad_uav.forest.reforestation import species as spx      # noqa: E402
from geocad_uav.gui import workflow as wf                       # noqa: E402

FAILURES = []
SKIPS = []
CRS = QgsCoordinateReferenceSystem("EPSG:32632")
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


def rect(x, y, width, height):
    return QgsGeometry.fromWkt(
        "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
            x, y, x + width, y + height))


def closest_violation(plants, catalog, floor):
    """The first pair that breaks its own minimum, or None. Brute force.

    Deliberately not the module's own index: a promise checked with the same
    machinery that made it is not checked.
    """
    points = [(p.x, p.y, cp.species_of(p)) for p in plants]
    for index, (x1, y1, key1) in enumerate(points):
        for x2, y2, key2 in points[index + 1:]:
            distance = math.hypot(x1 - x2, y1 - y2)
            needed = floor
            for key in (key1, key2):
                found = catalog.get(key) if (key and catalog) else None
                if found is not None:
                    needed = max(needed, float(found.min_distance_m or 0.0))
            if distance < needed - 1e-9:
                return (x1, y1, x2, y2, distance, needed)
    return None


AREA = rect(OX, OY, 160.0, 160.0)
SPEC = sp.SlopeSpacing(plant_distance_m=4.0, row_distance_m=4.0,
                       row_azimuth_deg=90.0, pattern=grid_mod.PATTERN_SQUARE)

print("=" * 78)
print("Naturaliforme -- radure, irregolarita' controllata, distanza minima")
print("=" * 78)

# --------------------------------------------------------------------------
# N1 - glades
# --------------------------------------------------------------------------
print("\n== N1: radure ==")
settings = nt.NaturalSettings(glade_count=4, glade_radius_m=10.0,
                              glade_margin_m=5.0, glade_gap_m=8.0, seed=3)
glades = nt.generate_glades(AREA, settings)
print("        {0} radure, {1:,.1f} m2".format(
    len(glades), sum(g.area_m2 for g in glades)))
check("quattro radure collocate", len(glades), 4)
check_true("ognuna ha un'area vicina a pi r quadro",
           all(abs(g.area_m2 - math.pi * 100.0) < 2.0 for g in glades))
check_true("stanno tutte dentro l'area", all(AREA.contains(g.geometry)
                                             for g in glades))
check_true("...e lontano dal bordo",
           all(AREA.buffer(-5.0, 12).contains(g.geometry) for g in glades))
centres = [(g.geometry.centroid().constGet().x(),
            g.geometry.centroid().constGet().y()) for g in glades]
gaps = [math.hypot(a[0] - b[0], a[1] - b[1])
        for i, a in enumerate(centres) for b in centres[i + 1:]]
check_true("...e lontane fra loro", min(gaps) >= 2 * 10.0 + 8.0 - 1e-6)

again = nt.generate_glades(AREA, nt.NaturalSettings(
    glade_count=4, glade_radius_m=10.0, glade_margin_m=5.0, glade_gap_m=8.0,
    seed=3))
check_true("lo stesso seme da le stesse radure",
           [(g.geometry.centroid().constGet().x(),
             g.geometry.centroid().constGet().y()) for g in again] == centres)
different = nt.generate_glades(AREA, nt.NaturalSettings(
    glade_count=4, glade_radius_m=10.0, glade_margin_m=5.0, glade_gap_m=8.0,
    seed=99))
check_true("...e un seme diverso ne da altre",
           [(g.geometry.centroid().constGet().x(),
             g.geometry.centroid().constGet().y())
            for g in different] != centres)

crowded = nt.generate_glades(AREA, nt.NaturalSettings(
    glade_count=50, glade_radius_m=30.0, glade_margin_m=5.0, glade_gap_m=20.0,
    seed=1))
print("        50 radure da 30 m su 160 x 160: collocate {0}".format(
    len(crowded)))
check_true("dove non c'e' spazio se ne collocano meno, non di piu'",
           0 <= len(crowded) < 50)
check("nessuna radura chiesta, nessuna collocata",
      len(nt.generate_glades(AREA, nt.NaturalSettings())), 0)
check_raises("radure senza raggio non si collocano", InvalidInputError,
             nt.NaturalSettings, 3, 0.0)
check_raises("...ne' su una superficie vuota", EmptyAoiError,
             nt.generate_glades, QgsGeometry(), settings)

plantable = nt.apply_glades(AREA, glades)
check("la superficie piantabile perde l'area delle radure",
      plantable.area(), AREA.area() - sum(g.area_m2 for g in glades), 1e-6)
check_raises("radure che coprono tutto sono un errore", EmptyAoiError,
             nt.apply_glades, rect(OX, OY, 12.0, 12.0),
             [nt.Glade(geometry=rect(OX - 5.0, OY - 5.0, 40.0, 40.0))])

# --------------------------------------------------------------------------
# N2 - the plants inside a glade go, whole
# --------------------------------------------------------------------------
print("\n== N2: le piante dentro una radura spariscono ==")
base = sp.generate(AREA, SPEC)
print("        impianto regolare: {0:,} piante".format(base.count))
inside = nt.plants_in_glades(base.plants, glades)
check_true("qualche pianta cade nelle radure", len(inside) > 0)
expected = math.pi * 100.0 * 4 / (4.0 * 4.0)
print("        {0} piante nelle radure, attese ~{1:.0f}".format(
    len(inside), expected))
check_true("...quante ne sta un sesto di 4 m in quattro dischi da 10 m",
           abs(len(inside) - expected) < 0.4 * expected)

outcome = nt.naturalise(list(base.plants), settings, SPEC.plant_distance_m,
                        glades=glades)
check("le piante tolte sono quelle dentro le radure",
      outcome.removed.get(nt.REMOVED_GLADE, 0), len(inside))
check("e le rimaste sono il resto", outcome.count,
      base.count - len(inside))
check_true("nessuna pianta rimasta e' in una radura",
           not nt.plants_in_glades(outcome.plants, glades))
check("l'area delle radure e' riportata", outcome.glade_area_m2,
      sum(g.area_m2 for g in glades), 1e-9)

# --------------------------------------------------------------------------
# N3 - controlled irregularity thins, and only thins
# --------------------------------------------------------------------------
print("\n== N3: irregolarita' controllata ==")
for amplitude in (0.0, 0.15, 0.35):
    thin_settings = nt.NaturalSettings(amplitude=amplitude, seed=5)
    kept, removed = nt.thin_by_density(list(base.plants), thin_settings,
                                       SPEC.plant_distance_m)
    print("        +/- {0:.0%}: restano {1:,}, tolte {2:,}".format(
        amplitude, len(kept), len(removed)))
    check("{0:.0%}: nessuna pianta e' stata inventata".format(amplitude),
          len(kept) + len(removed), base.count)
    check_true("{0:.0%}: le rimaste sono un sottoinsieme".format(amplitude),
               set(id(p) for p in kept) <= set(id(p) for p in base.plants))
    if amplitude == 0.0:
        check("senza irregolarita' non si tocca niente", len(removed), 0)
    else:
        check_true("con irregolarita' si dirada", len(removed) > 0)

strong = nt.thin_by_density(list(base.plants),
                            nt.NaturalSettings(amplitude=0.35, seed=5),
                            SPEC.plant_distance_m)[1]
mild = nt.thin_by_density(list(base.plants),
                          nt.NaturalSettings(amplitude=0.15, seed=5),
                          SPEC.plant_distance_m)[1]
check_true("piu' irregolarita', piu' diradamento", len(strong) > len(mild))
check("l'ampiezza e' limitata per non svuotare l'impianto",
      nt.NaturalSettings(amplitude=5.0).amplitude, nt.MAX_AMPLITUDE, 1e-9)
check_raises("un'ampiezza negativa e' un errore", InvalidInputError,
             nt.NaturalSettings, 0, 0.0, 0.0, 0.0, -0.5)

first = nt.thin_by_density(list(base.plants),
                           nt.NaturalSettings(amplitude=0.25, seed=11),
                           SPEC.plant_distance_m)[0]
second = nt.thin_by_density(list(base.plants),
                            nt.NaturalSettings(amplitude=0.25, seed=11),
                            SPEC.plant_distance_m)[0]
check_true("lo stesso seme dirada allo stesso modo",
           [(p.x, p.y) for p in first] == [(p.x, p.y) for p in second])

# --------------------------------------------------------------------------
# N4 - the promise: minimum distance, verified by brute force
# --------------------------------------------------------------------------
print("\n== N4: distanza minima garantita ==")
TIGHT = sp.SlopeSpacing(plant_distance_m=2.0, row_distance_m=2.0,
                        row_azimuth_deg=90.0,
                        pattern=grid_mod.PATTERN_SQUARE)
tight = sp.generate(rect(OX, OY, 60.0, 60.0), TIGHT)
print("        sesto fitto: {0:,} piante a 2 m".format(tight.count))
check("il sesto fitto viola gia' i 3 m",
      nt.measure_min_distance(tight.plants), 2.0, 1e-6)

kept, removed = nt.enforce_min_distance(list(tight.plants), 3.0)
print("        dopo il vincolo a 3 m: {0:,} rimaste, {1:,} tolte".format(
    len(kept), len(removed)))
check_true("qualcosa e' stato tolto", len(removed) > 0)
check("nessuna pianta e' stata inventata", len(kept) + len(removed),
      tight.count)
measured = nt.measure_min_distance(kept)
print("        minima misurata: {0:.6f} m".format(measured))
check_true("la distanza minima e' rispettata", measured >= 3.0 - 1e-9)
check_true("...e verificata a forza bruta, non con lo stesso indice",
           closest_violation(kept, None, 3.0) is None)
check_true("la prima di ogni coppia in conflitto resta",
           kept[0] is tight.plants[0])

print("\n-- e quando le specie chiedono distanze diverse --")
catalog = spx.SpeciesCatalog.from_layer(spx.SpeciesCatalog.memory_layer())
catalog.add(spx.Species(key="sp_stretto", min_distance_m=2.0))
catalog.add(spx.Species(key="sp_largo", min_distance_m=7.0))
mixed = sp.generate(rect(OX, OY, 80.0, 80.0), SPEC)
cp.assign(mixed.plants, cp.Mix([("sp_stretto", 70.0), ("sp_largo", 30.0)]))
kept_mixed, removed_mixed = nt.enforce_min_distance(
    list(mixed.plants), 2.0, catalog)
print("        {0:,} rimaste su {1:,}".format(len(kept_mixed),
                                              mixed.count))
violation = closest_violation(kept_mixed, catalog, 2.0)
check_true("nessuna coppia viola il minimo della propria specie",
           violation is None)
if violation is not None:
    print("        violazione: {0}".format(violation))
wide_before = [p for p in mixed.plants if cp.species_of(p) == "sp_largo"]
wide = [p for p in kept_mixed if cp.species_of(p) == "sp_largo"]
print("        sp_largo: {0} assegnate, {1} rimaste".format(
    len(wide_before), len(wide)))
check_true("la specie esigente e' rimasta, ma diradata",
           0 < len(wide) < len(wide_before))
for plant in wide[:40]:
    nearest = min((math.hypot(plant.x - other.x, plant.y - other.y)
                   for other in kept_mixed if other is not plant),
                  default=float("inf"))
    if nearest < 7.0 - 1e-9:
        check_true("una pianta esigente ha un vicino troppo vicino", False)
        break
else:
    check_true("ogni pianta della specie esigente ha 7 m liberi", True)

check("senza vincolo non si tocca niente",
      len(nt.enforce_min_distance(list(mixed.plants), 0.0)[1]), 0)

# --------------------------------------------------------------------------
# N5 - the whole pass, in order
# --------------------------------------------------------------------------
print("\n== N5: la passata completa ==")
full = nt.NaturalSettings(glade_count=3, glade_radius_m=10.0,
                          glade_margin_m=5.0, glade_gap_m=8.0,
                          amplitude=0.2, min_distance_m=3.0, seed=13)
full_glades = nt.generate_glades(AREA, full)
full_outcome = nt.naturalise(list(base.plants), full,
                             SPEC.plant_distance_m, glades=full_glades,
                             catalog=catalog)
print("\n".join(full_outcome.describe()))
check("il totale torna", full_outcome.count + full_outcome.removed_total,
      base.count)
check_true("tutte e tre le cause di rimozione sono possibili",
           set(full_outcome.removed) <= {nt.REMOVED_GLADE,
                                         nt.REMOVED_DENSITY,
                                         nt.REMOVED_MIN_DISTANCE})
check_true("nessuna pianta e' rimasta in una radura",
           not nt.plants_in_glades(full_outcome.plants, full_glades))
check_true("la distanza minima regge dopo tutto il resto",
           nt.measure_min_distance(full_outcome.plants) >= 3.0 - 1e-9)
check_true("...e nessuna coppia viola il proprio minimo",
           closest_violation(full_outcome.plants, catalog, 3.0) is None)
check_true("l'esito riporta la minima misurata",
           full_outcome.measured_min_distance_m >= 3.0 - 1e-9)
check_true("una passata spenta non toglie niente",
           nt.naturalise(list(base.plants), nt.NaturalSettings(),
                         SPEC.plant_distance_m).count == base.count)
check_true("il prospetto dice cosa e' stato tolto e perche'",
           any("radura" in line.lower() for line in full_outcome.describe()))

# --------------------------------------------------------------------------
# N6 - through the panels
# --------------------------------------------------------------------------
print("\n== N6: dai pannelli ==")
workspace = wf.Workspace(None)
state = workspace.state
scheme = workspace.context.scheme_panel
state.set_area(AREA, CRS, "Lotto")
scheme.plant_distance.setValue(4.0)
scheme.row_distance.setValue(4.0)
plain = workspace.context.generate_panel.preview()
check_true("senza naturaliforme non c'e' esito", state.natural_outcome is None)
regular_count = plain.count

# v1.22.0: the naturaliform controls are a step of their own.
natural = workspace.context.natural_panel
natural.glade_count.setValue(3)
natural.glade_radius.setValue(10.0)
natural.irregularity.setValue(20.0)
natural.min_distance.setValue(3.0)
natural.natural_seed.setValue(13)
check_true("le impostazioni arrivano al modello", state.natural.is_active)
check("...con i valori digitati", state.natural.glade_radius_m, 10.0, 1e-9)
check("...e l'irregolarita' in frazione", state.natural.amplitude, 0.20,
      1e-9)

natural_plan = natural.apply_to_project()
check_true("[Applica al progetto] ha rigenerato l'impianto",
           natural_plan is not None and natural_plan is state.result)
print("        regolare {0:,} -> naturaliforme {1:,}".format(
    regular_count, natural_plan.count))
check_true("l'impianto e' stato diradato", natural_plan.count < regular_count)
check_true("l'esito e' nel modello", state.natural_outcome is not None)
check("il conto torna anche qui",
      natural_plan.count + state.natural_outcome.removed_total,
      regular_count)
check_true("le radure sono state collocate", len(state.glades) == 3)
check_true("il pannello dice quante piante sono state tolte",
           "piante tolte" in natural.natural_label.text())
check_true("...e quante radure ha collocato",
           natural.glades_label.text() == "3")
check_true("...e la distanza minima misurata",
           " m" in natural.measured_label.text())
check_true("lo step Naturaliforme risulta fatto",
           state.status("natural") == wf.DONE)

layer = workspace.context.generate_panel.generate()
check("il layer porta le piante rimaste", layer.featureCount(),
      natural_plan.count)
anomalies = workspace.context.verify_panel.run()
print("        anomalie: {0}".format(anomalies))
check_true("la distanza media non viene piu' segnalata su un impianto diradato",
           not any("media" in text for text in anomalies))
check_true("...ma la minima sarebbe segnalata se cadesse",
           state.verified)
report = workspace.context.outputs_panel.build_report()
check_true("la relazione riporta le impostazioni naturaliformi",
           "DISTRIBUZIONE NATURALIFORME" in report)
check_true("...e l'esito", "ESITO NATURALIFORME" in report)

# --------------------------------------------------------------------------
# N7 - the catalogue, which is where a species' own distance comes from
# --------------------------------------------------------------------------
print("\n== N7: il catalogo delle specie, dal pannello ==")


def _count_by_species(plants) -> dict:
    counts = {}
    for record in plants:
        key = cp.species_of(record)
        counts[key] = counts.get(key, 0) + 1
    return counts


import tempfile                                                  # noqa: E402

CATALOG_DIR = tempfile.mkdtemp(prefix="geocad_cat_")
catalog_path = os.path.join(CATALOG_DIR, "specie.gpkg")
spx.SpeciesCatalog.create(catalog_path, [
    spx.Species(key="leccio", name="Leccio", min_distance_m=7.0),
    spx.Species(key="pino", name="Pino", min_distance_m=3.0),
], overwrite=True)
print("        catalogo scritto: {0:,} byte".format(
    os.path.getsize(catalog_path)))

check_true("prima di aprirlo il catalogo e' vuoto",
           scheme.state.catalog.count == 0
           or "vuoto" in scheme.catalog_label.text())
opened = scheme.open_catalog(path=catalog_path)
print("        {0}".format(scheme.catalog_label.text()))
check_true("il pannello apre il catalogo", opened is not None)
check("...e il progetto ne ha due specie", state.catalog.count, 2)
check_true("...che compaiono nel menu a tendina",
           scheme.species_key.findData("leccio") >= 0
           and scheme.species_key.findData("pino") >= 0)
check_true("...e il pannello dice quante hanno una distanza propria",
           "2 con una distanza minima" in scheme.catalog_label.text())
check("la distanza del leccio e' quella scritta a catalogo",
      state.catalog.get("leccio").min_distance_m, 7.0)

state.shares = []
for key, percent in (("leccio", 50.0), ("pino", 50.0)):
    scheme.species_key.setCurrentIndex(scheme.species_key.findData(key))
    scheme.species_percent.setValue(percent)
    scheme.on_add_species()
scheme.refresh()
check("la tabella ha una riga per specie", scheme.species_table.rowCount(), 2)
check_true("...e mostra il nome del catalogo, non il codice",
           scheme.species_table.item(0, 0).text() == "Leccio")
check_true("...e la distanza minima della specie",
           scheme.species_table.item(0, 2).text() == "7")

print("\n-- la distanza si corregge in tabella --")
scheme.species_table.item(0, 2).setText("9.5")
check("il catalogo ha preso il valore digitato",
      state.catalog.get("leccio").min_distance_m, 9.5, 1e-9)
scheme.species_table.item(0, 2).setText("7")
check("...e si puo' rimettere com'era",
      state.catalog.get("leccio").min_distance_m, 7.0, 1e-9)
scheme.species_table.item(0, 2).setText("non un numero")
check("una sciocchezza digitata non tocca il catalogo",
      state.catalog.get("leccio").min_distance_m, 7.0, 1e-9)

print("\n-- e il naturaliforme la usa davvero --")
state.set_area(AREA, CRS, "Lotto catalogo")
scheme.plant_distance.setValue(4.0)
scheme.row_distance.setValue(4.0)
scheme.apply_scheme()
natural.glade_count.setValue(0)
natural.irregularity.setValue(0.0)
natural.min_distance.setValue(0.0)
natural.apply_settings()
plan = workspace.context.generate_panel.preview()
before_species = _count_by_species(plan.plants)
print("        prima: {0}".format(before_species))
outcome = nt.naturalise(plan.plants, nt.NaturalSettings(min_distance_m=0.5),
                         4.0, catalog=state.catalog)
after_species = _count_by_species(outcome.plants)
print("        dopo:  {0}".format(after_species))
check_true("il leccio, che chiede 7 m su un sesto di 4, viene diradato",
           after_species.get("leccio", 0) < before_species.get("leccio", 0))
check_true("...ma non sparisce: le specie esigenti sono servite per prime",
           after_species.get("leccio", 0) > 0)
worst = nt.measure_min_distance(
    [p for p in outcome.plants if cp.species_of(p) == "leccio"])
print("        distanza minima fra lecci: {0:.2f} m".format(worst))
check_true("...e fra due lecci restano i 7 m che il catalogo chiede",
           worst >= 7.0 - 1e-6)
# A leccio needing 7 m needs it from any neighbour, so a pino at 4 m is in
# its way: on a 4 m scheme the pino has nowhere left. That is the right
# answer, and the outcome has to say it out loud rather than hand back a
# stand quietly missing half its mix.
check_true("la specie che non trova piu' posto viene dichiarata",
           any("pino" in text for text in outcome.warnings))
print("        avviso: {0}".format(outcome.warnings[0]))

print("\n-- il catalogo si risalva --")
again_path = os.path.join(CATALOG_DIR, "specie_bis.gpkg")
written_catalog = scheme.save_catalog(path=again_path)
check_true("il catalogo e' stato scritto", bool(written_catalog)
           and os.path.exists(written_catalog))
reopened = spx.SpeciesCatalog.open(written_catalog)
check("...con le stesse specie", reopened.count, 2)
check("...e le stesse distanze", reopened.get("leccio").min_distance_m, 7.0)


print("\n" + "=" * 78)
# A SpeciesCatalog holds a QgsVectorLayer opened on a GeoPackage that the
# project never adopted. Collected after exitQgis() it is a
# use-after-teardown that takes the process down with every check passed.
workspace.unmount()
reopened = opened = None
state.catalog = None
workspace = state = scheme = natural = plan = outcome = None
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
