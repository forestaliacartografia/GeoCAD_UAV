"""
v1.15.0: zones that are planted, not merely listed.

Until this slice the Zones panel collected sub-areas and the generator then
planted the whole surface with one scheme regardless -- a panel an operator
would have read and believed. These checks are the ones that would have
caught it: each zone is generated with *its own* scheme and *its own* mix,
the plants carry the zone they belong to, and the numbers per zone are the
numbers on the layer.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_zones.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsGeometry, QgsProject, QgsWkbTypes)

QGS = QgsApplication([], True)
QGS.initQgis()

from geocad_uav.core import grid as grid_mod                    # noqa: E402
from geocad_uav.core.errors import (EmptyAoiError,              # noqa: E402
                                    InvalidInputError)
from geocad_uav.core.planar import azimuth_of                   # noqa: E402
from geocad_uav.forest.reforestation import composition as cp   # noqa: E402
from geocad_uav.forest.reforestation import spacing as sp       # noqa: E402
from geocad_uav.forest.reforestation import terrain as tr       # noqa: E402
from geocad_uav.forest.reforestation import zones as zn         # noqa: E402
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


AREA = rect(OX, OY, 300.0, 200.0)              # 6 ha
SPEC5 = sp.SlopeSpacing(plant_distance_m=5.0, row_distance_m=5.0,
                        row_azimuth_deg=90.0,
                        pattern=grid_mod.PATTERN_SQUARE)
SPEC10 = sp.SlopeSpacing(plant_distance_m=10.0, row_distance_m=10.0,
                         row_azimuth_deg=90.0,
                         pattern=grid_mod.PATTERN_SQUARE)

print("=" * 78)
print("Zone -- suddivisione, sesti indipendenti, impianto per zona")
print("=" * 78)

# --------------------------------------------------------------------------
# Z1 - a zone is a polygon, clipped to the surface it belongs to
# --------------------------------------------------------------------------
print("\n== Z1: la zona sta dentro l'area, e non sopra le altre ==")
zone_set = zn.ZoneSet(container=AREA)
inside = zn.Zone(name="Zona A", geometry=rect(OX, OY, 100.0, 200.0),
                 spec=SPEC5)
zone_set.add(inside)
check("una zona dentro l'area conserva la sua superficie",
      zone_set.get("Zona A").area_m2, 20_000.0, 1e-6)

straddling = zn.Zone(name="Zona B",
                     geometry=rect(OX + 250.0, OY, 200.0, 200.0),
                     spec=SPEC5)
zone_set.add(straddling)
check("una zona a cavallo del bordo viene ritagliata",
      zone_set.get("Zona B").area_m2, 50.0 * 200.0, 1e-6)

overlapping = zn.Zone(name="Zona C", geometry=rect(OX, OY, 150.0, 200.0),
                      spec=SPEC5)
zone_set.add(overlapping)
check("una zona che invade la prima prende solo il libero",
      zone_set.get("Zona C").area_m2, 50.0 * 200.0, 1e-6)
check("tre zone in tutto", len(zone_set), 3)
check_true("...e non si sovrappongono", not zone_set.validate()
           or all("sovrappongono" not in text
                  for text in zone_set.validate()))

left = zone_set.uncovered()
check_true("il resto dell'area e' dichiarato, non piantato di nascosto",
           left is not None and left.area() > 0.0)
check_true("...e la validazione lo dice",
           any("nessuna zona" in text for text in zone_set.validate()))

check_raises("due zone con lo stesso nome non convivono", InvalidInputError,
             zone_set.add, zn.Zone(name="Zona A", geometry=AREA, spec=SPEC5))
check_raises("una zona fuori dall'area non entra", EmptyAoiError,
             zone_set.add,
             zn.Zone(name="Lontana", geometry=rect(OX + 5000.0, OY, 10.0,
                                                   10.0), spec=SPEC5))
check_raises("una zona senza nome non esiste", InvalidInputError, zn.Zone,
             "", AREA)
check_raises("...ne' una senza geometria", EmptyAoiError, zn.Zone, "X",
             QgsGeometry())
check_true("rimuovere una zona la toglie",
           zone_set.remove("Zona C") and len(zone_set) == 2)

# --------------------------------------------------------------------------
# Z2 - splitting into bands
# --------------------------------------------------------------------------
print("\n== Z2: suddivisione in fasce ==")
bands = zn.split_bands(AREA, 3, 90.0, SPEC5)
check("tre fasce", len(bands), 3)
areas = [band.area_m2 for band in bands]
print("        superfici: {0}".format(
    ["{0:,.1f}".format(a) for a in areas]))
check("le fasce coprono tutta l'area", sum(areas), AREA.area(), 1e-6)
check("...in parti uguali su un rettangolo", max(areas) - min(areas), 0.0,
      1e-6)
check_true("ognuna ha il sesto che le e' stato dato",
           all(band.spec is not None
               and band.spec.plant_distance_m == 5.0 for band in bands))
check_true("...ma non lo stesso oggetto, cosi' si possono cambiare da sole",
           len({id(band.spec) for band in bands}) == 3)

along_rows = zn.split_bands(AREA, 2, 0.0, SPEC5)
box_ns = along_rows[0].geometry.boundingBox()
box_ew = zn.split_bands(AREA, 2, 90.0, SPEC5)[0].geometry.boundingBox()
print("        fasce a 0 deg: {0:.0f} x {1:.0f} m; a 90 deg: {2:.0f} x "
      "{3:.0f} m".format(box_ns.width(), box_ns.height(), box_ew.width(),
                         box_ew.height()))
check_true("l'azimut decide come si taglia",
           abs(box_ns.width() - box_ew.width()) > 1.0)

check_raises("zero fasce non sono una suddivisione", InvalidInputError,
             zn.split_bands, AREA, 0, 90.0)
check_raises("fasce piu' strette di un metro nemmeno", InvalidInputError,
             zn.split_bands, AREA, 400, 90.0)
check_raises("e non si divide il nulla", EmptyAoiError, zn.split_bands,
             QgsGeometry(), 3, 90.0)
single = zn.split_bands(AREA, 1, 90.0, SPEC5)
check("una sola fascia e' l'area intera", single[0].area_m2, AREA.area(),
      1e-6)

# --------------------------------------------------------------------------
# Z3 - each zone planted with its own scheme
# --------------------------------------------------------------------------
print("\n== Z3: ogni zona col suo sesto ==")
plan_set = zn.ZoneSet(container=AREA)
for band in zn.split_bands(AREA, 3, 90.0, SPEC5):
    plan_set.add(band)
middle = plan_set.get("Zona B")
middle.spec = SPEC10
plan = zn.plant(plan_set)
print("\n".join(plan.describe()[:6]))
check("tre zone nel piano", plan.n_zones, 3)
check_true("il piano ha piante", plan.count > 0)
counts = {name: entry["plants"] for name, entry in plan.per_zone.items()}
print("        piante per zona: {0}".format(counts))
check_true("la zona col sesto largo ne ha molte meno",
           counts["Zona B"] < counts["Zona A"] / 3.0)
check_true("...e le due col sesto stretto si somigliano",
           abs(counts["Zona A"] - counts["Zona C"])
           < 0.1 * counts["Zona A"])
check("il totale e' la somma delle zone", plan.count, sum(counts.values()))

check_true("ogni pianta sa a che zona appartiene",
           all(zn.zone_of(p) in counts for p in plan.plants))
by_zone = {}
for plant in plan.plants:
    by_zone[zn.zone_of(plant)] = by_zone.get(zn.zone_of(plant), 0) + 1
check_true("...e il conteggio per zona coincide", by_zone == counts)
check("gli identificativi sono unici su tutto il piano",
      len({p.plant_id for p in plan.plants}), plan.count)
check("...e le file pure", len({p.row_id for p in plan.plants}),
      len({(zn.zone_of(p), p.row_id) for p in plan.plants}))

for name, entry in plan.per_zone.items():
    zone = plan_set.get(name)
    plants = [p for p in plan.plants if zn.zone_of(p) == name]
    check_true("{0}: le piante stanno dentro la zona".format(name),
               all(zone.geometry.intersects(QgsGeometry.fromWkt(
                   "POINT({0} {1})".format(p.x, p.y)))
                   for p in plants[:60]))
    gap = sp.mean_spacing(plants)
    check("{0}: il passo e' quello del suo sesto".format(name), gap,
          zone.spec.plant_distance_m, 1e-6)

# --------------------------------------------------------------------------
# Z4 - each zone with its own mix
# --------------------------------------------------------------------------
print("\n== Z4: ogni zona con la sua composizione ==")
mixed = zn.ZoneSet(container=AREA)
for index, band in enumerate(zn.split_bands(AREA, 2, 90.0, SPEC5)):
    band.shares = ((("sp_a", 100.0),) if index == 0
                   else (("sp_b", 70.0), ("sp_c", 30.0)))
    mixed.add(band)
mixed_plan = zn.plant(mixed)
first_zone = [p for p in mixed_plan.plants if zn.zone_of(p) == "Zona A"]
second_zone = [p for p in mixed_plan.plants if zn.zone_of(p) == "Zona B"]
print("        Zona A: {0}".format(cp.achieved_percentages(first_zone)))
print("        Zona B: {0}".format(cp.achieved_percentages(second_zone)))
check_true("la prima zona ha una sola specie",
           set(cp.achieved_percentages(first_zone)) == {"sp_a"})
check_true("la seconda ne ha due", set(cp.achieved_percentages(second_zone))
           == {"sp_b", "sp_c"})
check("...nelle proporzioni chieste",
      cp.achieved_percentages(second_zone)["sp_b"],
      100.0 * mixed.get("Zona B").mix().counts_for(len(second_zone))["sp_b"]
      / len(second_zone), 1e-9)
check_true("nessuna specie della prima zona e' finita nella seconda",
           not any(cp.species_of(p) == "sp_a" for p in second_zone))
totals = mixed_plan.species_counts()
check("il piano somma le specie di tutte le zone",
      sum(totals.values()), mixed_plan.count)

print("\n-- una zona senza sesto viene saltata, e lo si dice --")
partial = zn.ZoneSet(container=AREA)
partial.add(zn.Zone(name="Zona A", geometry=rect(OX, OY, 100.0, 200.0),
                    spec=SPEC5))
partial.add(zn.Zone(name="Zona B", geometry=rect(OX + 150.0, OY, 100.0,
                                                 200.0)))
partial_plan = zn.plant(partial)
check("solo la zona col sesto e' stata piantata", partial_plan.n_zones, 1)
check_true("...e l'altra e' segnalata",
           any("Zona B" in text for text in partial_plan.warnings))
check_true("la validazione la segnala prima ancora",
           any("non ha un sesto" in text for text in partial.validate()))
check("un insieme vuoto non pianta nulla", zn.plant(zn.ZoneSet()).count, 0)

# --------------------------------------------------------------------------
# Z5 - the panel plants what it shows
# --------------------------------------------------------------------------
print("\n== Z5: il pannello pianta quello che mostra ==")
workspace = wf.Workspace(None)
state = workspace.state
panel = workspace.context.zones_panel
state.set_area(AREA, CRS, "Lotto")
workspace.context.scheme_panel.plant_distance.setValue(5.0)
workspace.context.scheme_panel.row_distance.setValue(5.0)
for name, percent in (("sp_a", 60.0), ("sp_b", 40.0)):
    workspace.context.scheme_panel.species_key.setCurrentText(name)
    workspace.context.scheme_panel.species_percent.setValue(percent)
    workspace.context.scheme_panel.on_add_species()

panel.band_count.setValue(3)
check("la suddivisione crea tre zone", panel.split(), 3)
check("...e la tabella le mostra tutte", panel.table.rowCount(), 3)
check_true("le superfici in tabella sono quelle delle zone",
           all(panel.table.item(row, 1).text()
               == wf.ha(list(state.zones)[row].area_m2)
               for row in range(3)))
check_true("ogni zona ha ereditato la composizione corrente",
           all(zone.shares == tuple(state.shares) for zone in state.zones))

state.zones.get("Zona B").spec = SPEC10
plan = workspace.context.generate_panel.preview()
check_true("l'anteprima ha generato per zone",
           plan is not None and hasattr(plan, "per_zone"))
check("tre zone nel risultato", plan.n_zones, 3)
zone_counts = {name: entry["plants"] for name, entry in plan.per_zone.items()}
print("        {0}".format(zone_counts))
check_true("la zona col sesto largo resta la meno fitta",
           zone_counts["Zona B"] < min(zone_counts["Zona A"],
                                       zone_counts["Zona C"]))

layer = workspace.context.generate_panel.generate()
check_true("il layer esiste ed e' PointZ",
           layer is not None and QgsWkbTypes.hasZ(layer.wkbType()))
on_layer = {}
for feature in layer.getFeatures():
    on_layer[feature["zona"]] = on_layer.get(feature["zona"], 0) + 1
print("        colonna zona: {0}".format(on_layer))
check_true("la colonna zona porta i nomi veri", on_layer == zone_counts)
check_true("...e la specie e' scritta accanto",
           all(feature["specie"] in ("sp_a", "sp_b")
               for feature in layer.getFeatures()))
check("una feature per pianta", layer.featureCount(), plan.count)

report = workspace.context.outputs_panel.build_report()
check_true("la relazione parla delle zone", "ZONE DI IMPIANTO" in report)
check_true("...e dell'impianto per zona", "IMPIANTO PER ZONE" in report)
for name in zone_counts:
    check_true("...nominando {0}".format(name), name in report)

anomalies = workspace.context.verify_panel.run()
check_true("la verifica gira anche su un piano per zone", state.verified)
print("        anomalie: {0}".format(anomalies))

print("\n-- cambiare area azzera le zone --")
state.set_area(rect(OX + 1000.0, OY, 100.0, 100.0), CRS, "Altro lotto")
check("le zone del lotto precedente non restano", len(state.zones), 0)
check_true("...e lo step torna da fare",
           state.status("zones") in (wf.NOT_STARTED, wf.ACTIVE))


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
