"""
v1.17.0: the project on the map, and the cadastre reaching it.

The rule this suite exists to enforce: a function that computes correctly but
never reaches the canvas is not a feature. So nothing here asserts that a
module returns the right number -- other suites do that. These checks press
what an operator presses and then look at the map: is there a layer, does it
hold the right features, are they in the project's CRS, does picking a row in
the table select the parcel on the canvas, and does it all go away when the
plugin unloads.

One block asks the **live** Agenzia delle Entrate service and skips when it
cannot be reached.

NEEDS QGIS with widgets. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_map_end_to_end.py
"""

import io as _io
import os
import sys
from time import sleep, time

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (Qgis, QgsApplication,                     # noqa: E402
                       QgsCoordinateReferenceSystem, QgsGeometry, QgsProject,
                       QgsWkbTypes)

QGS = QgsApplication([], True)
QGS.initQgis()

from geocad_uav.gui import workflow as wf                        # noqa: E402
from geocad_uav.io import cadastre as cs                         # noqa: E402

FAILURES = []
SKIPS = []
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures")
CRS6706 = QgsCoordinateReferenceSystem("EPSG:6706")
CRS32632 = QgsCoordinateReferenceSystem("EPSG:32632")
OX, OY = 500000.0, 5000000.0


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<56} got={2:<16.10g} exp={3:.10g}".format(
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


def skip(label, reason):
    print("  [skip] {0}\n         motivo: {1}".format(label, reason))
    SKIPS.append((label, reason))


def pump(predicate, seconds=30.0):
    """Let the task manager run until the GUI shows what we are waiting for."""
    deadline = time() + float(seconds)
    while time() < deadline:
        if predicate():
            return True
        QGS.processEvents()
        sleep(0.01)
    return bool(predicate())


def fixture(name):
    with _io.open(os.path.join(FIXTURES, name), encoding="utf-8",
                  errors="replace") as handle:
        return handle.read()


def rect(x, y, width, height):
    return QgsGeometry.fromWkt(
        "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
            x, y, x + width, y + height))


PARCEL_XML = fixture("catasto_parcel_getfeature.xml")
ZONING_XML = fixture("catasto_zoning_getfeature.xml")

print("=" * 78)
print("Mappa -- il progetto e il catasto sulla tela")
print("=" * 78)

workspace = wf.Workspace(None)
state = workspace.state
area_panel = workspace.context.area_panel
layers = state.layers

# --------------------------------------------------------------------------
# M1 - the project geometry reaches the canvas
# --------------------------------------------------------------------------
print("\n== M1: l'area di progetto diventa un layer ==")
check("prima di tutto non c'e' nessun layer del plugin", len(layers.layers), 0)

AREA = rect(OX, OY, 400.0, 310.0)
state.set_area(AREA, CRS32632, "Lotto di prova")
check_true("l'area e' stata disegnata", "area" in layers.layers)
check("il layer dell'area ha una feature",
      layers.layer("area").featureCount(), 1)
check("...e quello della superficie utile pure",
      layers.layer("usable").featureCount(), 1)
check_text("i layer sono nel CRS del progetto",
           layers.layer("area").crs().authid(), "EPSG:32632")
drawn = next(layers.layer("area").getFeatures())
check("la geometria disegnata e' quella del progetto",
      drawn.geometry().area(), AREA.area(), 1e-6)
check("...e l'attributo dice la superficie", drawn["superficie_ha"], 12.40,
      1e-9)
check_true("i layer stanno nel progetto QGIS, non solo in un dizionario",
           QgsProject.instance().mapLayer(layers.layer("area").id())
           is not None)
check_true("ognuno ha un simbolo, non quello casuale di QGIS",
           layers.layer("area").renderer().symbol() is not None)

print("\n-- i vincoli cambiano la superficie utile, e la mappa lo mostra --")
before = next(layers.layer("usable").getFeatures()).geometry().area()
state.constraints.declare("strada", 10.0, label="Strade")
state.constraints.add_geometry("strada", QgsGeometry.fromWkt(
    "LINESTRING({0} {1},{2} {1})".format(OX + 10.0, OY + 150.0,
                                         OX + 390.0, OY + 150.0)))
state.apply_constraints()
after = next(layers.layer("usable").getFeatures()).geometry().area()
print("        utile {0:,.0f} -> {1:,.0f} m2".format(before, after))
check_true("la superficie utile disegnata si e' ridotta", after < before)
check_true("...e l'esclusione e' sulla mappa",
           layers.layer("excluded").featureCount() >= 1)
excluded = next(layers.layer("excluded").getFeatures())
print("        motivo: {0}".format(excluded["motivo"]))
check_true("l'attributo dice di che esclusione si tratta",
           "Strade" in str(excluded["motivo"]))
check_true("...e quanta superficie toglie", excluded["superficie_ha"] > 0.0)

state.constraints.clear_features("strada")
state.apply_constraints()
check("tolto il vincolo, la mappa torna com'era",
      next(layers.layer("usable").getFeatures()).geometry().area(), before,
      1e-6)
check("...e nessuna esclusione resta disegnata",
      layers.layer("excluded").featureCount(), 0)

# --------------------------------------------------------------------------
# M2 - the cadastre: button -> task -> table -> layer -> canvas
# --------------------------------------------------------------------------
print("\n== M2: Interroga Catasto, e le particelle compaiono ==")
PARCELS = cs.parse_parcel_geometries(PARCEL_XML)
check("il documento di prova porta due particelle", len(PARCELS), 2)
PARCEL_GEOM = PARCELS[0][1]
# That parcel is a 93 m2 sliver: shrink until there is something it contains.
INSIDE = None
for _setback in (2e-5, 1e-5, 5e-6, 2e-6, 1e-6, 5e-7):
    _candidate = PARCEL_GEOM.buffer(-_setback, 8)
    if (_candidate is not None and not _candidate.isEmpty()
            and _candidate.area() > 0.0 and PARCEL_GEOM.contains(_candidate)):
        INSIDE = _candidate
        break
check_true("l'area di prova sta davvero dentro una particella vera",
           INSIDE is not None)

state.set_area(INSIDE, CRS6706, "Particella di prova")
check_text("cambiando CRS i layer lo seguono",
           layers.layer("area").crs().authid(), "EPSG:6706")

CALLS = []


def recorded(url, timeout=0.0):
    CALLS.append(url)
    return ZONING_XML if cs.TYPE_ZONING.split(":")[-1] in url else PARCEL_XML


check("prima dell'interrogazione la tabella e' vuota",
      area_panel.parcel_table.rowCount(), 0)
check_true("...e il pulsante di visualizzazione e' spento",
           not area_panel.show_button.isEnabled())

task = area_panel.query_cadastre(recorded)
check_true("il comando ha avviato un task", task is not None)
check_true("il pulsante si spegne mentre il servizio pensa",
           not area_panel.query_button.isEnabled())
check_true("il task ha risposto",
           pump(lambda: area_panel.parcel_table.rowCount() > 0))

print("        comune: {0}, particelle: {1}".format(
    area_panel.comune_label.text(), area_panel.parcels_label.text()))
check("una sola particella interseca l'area", state.cadastre.n_parcels, 1)
check("la tabella ha una riga per particella",
      area_panel.parcel_table.rowCount(), state.cadastre.n_parcels)
check_true("...e la riga porta comune, foglio, particella e percentuale",
           all(area_panel.parcel_table.item(0, column) is not None
               for column in range(4)))
check_true("il comune in tabella e' quello risolto dal Belfiore",
           "Perugia" in area_panel.parcel_table.item(0, 0).text())
check_text("il foglio", area_panel.parcel_table.item(0, 1).text(), "252")
check_text("la particella", area_panel.parcel_table.item(0, 2).text(), "1016")
check_true("...e una percentuale che si legge",
           0.0 < float(area_panel.parcel_table.item(0, 3).text()) <= 100.0)

parcels = layers.layers.get("parcels")
check_true("le particelle sono un layer sulla mappa", parcels is not None)
check("...con una feature per particella", parcels.featureCount(),
      state.cadastre.n_parcels)
check_text("...nel CRS del progetto", parcels.crs().authid(), "EPSG:6706")
feature = next(parcels.getFeatures())
print("        feature: {0}".format(
    {name: feature[name] for name in ("comune", "belfiore", "foglio",
                                      "particella")}))
check_true("la geometria della particella e' valida e non vuota",
           feature.geometry().isGeosValid()
           and not feature.geometry().isEmpty())
check_true("...ed e' un poligono, non un punto",
           feature.geometry().type() == Qgis.GeometryType.Polygon)
check_true("...e sta dove sta il progetto",
           feature.geometry().intersects(INSIDE))
check_text("gli attributi portano il codice Belfiore", feature["belfiore"],
           "G478")
check_true("...e le due superfici",
           feature["superficie_cat_ha"] > 0.0
           and feature["superficie_int_ha"] > 0.0)
check("la percentuale sul layer e' quella del modello",
      feature["percentuale"], state.cadastre.shares[0].percent_of_parcel,
      1e-3)
check_true("il pulsante di visualizzazione si e' acceso",
           area_panel.show_button.isEnabled())
check_true("il servizio e' stato interrogato per particelle e per fogli",
           len(CALLS) == 2 and any(cs.TYPE_ZONING.split(":")[-1] in url for url in CALLS))

print("\n-- scegliere una riga evidenzia la particella sulla mappa --")
parcels.removeSelection()
check("nessuna particella selezionata", len(parcels.selectedFeatureIds()), 0)
area_panel.parcel_table.selectRow(0)
selected = parcels.selectedFeatureIds()
print("        selezionate: {0}".format(list(selected)))
check("scegliere la prima riga seleziona una particella", len(selected), 1)
check_true("...ed e' la prima del layer",
           selected[0] == next(parcels.getFeatures()).id())
check("premere Visualizza ridisegna le particelle",
      area_panel.show_parcels(), state.cadastre.n_parcels)

# --------------------------------------------------------------------------
# M3 - an error is explained, never an empty table with no reason
# --------------------------------------------------------------------------
print("\n== M3: quando il servizio non risponde, si sa perche' ==")


def dead(url, timeout=0.0):
    raise cs.CadastreError("rete assente",
                           user_message="Servizio non raggiungibile.")


area_panel.query_cadastre(dead)
check_true("il task fallito ha risposto",
           pump(lambda: area_panel.query_button.isEnabled()))
status = area_panel.cadastre_status_label.text()
print("        stato: {0}".format(status))
check_true("lo stato dice che non e' riuscita",
           cs.STATUS_LABELS[cs.STATUS_ERROR] in status)
check_true("...e dice anche perche'",
           "raggiungibile" in status
           or "raggiungibile" in area_panel.cadastre_status_label.toolTip())
check("la tabella e' vuota, ma spiegata",
      area_panel.parcel_table.rowCount(), 0)
check_true("il pulsante di visualizzazione si e' rispento",
           not area_panel.show_button.isEnabled())
check_true("le particelle non restano disegnate da un'interrogazione vecchia",
           layers.layers.get("parcels") is None
           or layers.layers["parcels"].featureCount() == 0)
check_true("il progetto e' ancora li': il catasto non se lo porta via",
           state.area is not None)


def nothing_here(url, timeout=0.0):
    return ('<?xml version="1.0"?><wfs:FeatureCollection '
            'xmlns:wfs="http://www.opengis.net/wfs/2.0" '
            'numberReturned="0"></wfs:FeatureCollection>')


area_panel.query_cadastre(nothing_here)
check_true("anche 'nessuna particella' e' una risposta",
           pump(lambda: area_panel.query_button.isEnabled()))
empty_status = area_panel.cadastre_status_label.text()
print("        stato: {0}".format(empty_status))
check_true("...e si distingue da un errore di rete",
           cs.STATUS_LABELS[cs.STATUS_UNAVAILABLE] in empty_status)
check_true("...con la ragione scritta accanto",
           "non riporta particelle" in empty_status)

# --------------------------------------------------------------------------
# M4 - zones, glades and plants, all on the canvas
# --------------------------------------------------------------------------
print("\n== M4: zone, radure e piante sulla mappa ==")
state.set_area(AREA, CRS32632, "Lotto di prova")
scheme = workspace.context.scheme_panel
scheme.plant_distance.setValue(8.0)
scheme.row_distance.setValue(8.0)
for name, percent in (("sp_a", 60.0), ("sp_b", 40.0)):
    scheme.species_key.setCurrentText(name)
    scheme.species_percent.setValue(percent)
    scheme.on_add_species()
scheme.apply_scheme()

zones_panel = workspace.context.zones_panel
zones_panel.band_count.setValue(3)
check("tre zone create", zones_panel.split(), 3)
zone_layer = layers.layers.get("zones")
check_true("le zone sono un layer", zone_layer is not None)
check("...con una feature per zona", zone_layer.featureCount(), 3)
zone_feature = next(zone_layer.getFeatures())
print("        zona: {0}".format(
    {name: zone_feature[name] for name in ("zona", "superficie_ha", "sesto",
                                           "densita")}))
check_true("...che porta nome, superficie, sesto e densita'",
           bool(zone_feature["zona"]) and zone_feature["superficie_ha"] > 0.0
           and "x" in str(zone_feature["sesto"])
           and zone_feature["densita"] > 0.0)

scheme.glade_count.setValue(3)
scheme.glade_radius.setValue(12.0)
scheme.min_distance.setValue(2.0)
scheme.apply_scheme()
plan = workspace.context.generate_panel.preview()
check_true("l'impianto e' stato generato", plan is not None and plan.count > 0)
check_true("le radure sono state aperte davvero", len(state.glades) > 0)
glade_layer = layers.layers.get("glades")
check_true("le radure sono un layer", glade_layer is not None)
check("...con una feature per radura", glade_layer.featureCount(),
      len(state.glades))
check_true("...e ognuna ha raggio e superficie",
           all(f["raggio_m"] > 0.0 and f["superficie_ha"] > 0.0
               for f in glade_layer.getFeatures()))

plants = workspace.context.generate_panel.generate()
check_true("il layer delle piante e' adottato dal servizio mappa",
           layers.layers.get("plants") is plants)
check("...e porta tutte le piante", plants.featureCount(), plan.count)
check_true("...ed e' PointZ", QgsWkbTypes.hasZ(plants.wkbType()))

print("\n-- e tutto sparisce quando il plugin si chiude --")
ids = [layer.id() for layer in layers.layers.values()]
print("        {0} layer del plugin: {1}".format(
    len(ids), sorted(layers.layers)))
check_true("ci sono layer da togliere", len(ids) >= 5)
workspace.unmount()
check("il servizio non tiene piu' nessun layer", len(layers.layers), 0)
check_true("...e nessuno resta nel progetto QGIS",
           all(QgsProject.instance().mapLayer(layer_id) is None
               for layer_id in ids))

# --------------------------------------------------------------------------
# M5 - the real service, from the real button, skipped when unreachable
# --------------------------------------------------------------------------
print("\n== M5: il servizio vero, dal pulsante ==")
live_workspace = wf.Workspace(None)
live_state = live_workspace.state
live_panel = live_workspace.context.area_panel
live_state.set_area(INSIDE, CRS6706, "Particella di prova")

try:
    cs.urllib_transport(cs.EVIDENCE[0], timeout=15.0)
    reachable = True
except cs.CadastreError as exc:
    reachable = False
    skip("il pulsante interroga il servizio vero",
         "endpoint non raggiungibile: {0}".format(exc.formatted()))

if reachable:
    live_panel.query_button.click()
    answered = pump(lambda: live_panel.query_button.isEnabled(), 90.0)
    check_true("il pulsante, senza trasporto finto, ha interrogato il "
               "servizio vero", answered)
    print("        stato:  {0}".format(
        live_panel.cadastre_status_label.text()))
    print("        comune: {0}".format(live_panel.comune_label.text()))
    print("        righe:  {0}".format(live_panel.parcel_table.rowCount()))
    if live_state.cadastre is not None and live_state.cadastre.n_parcels:
        live_parcels = live_state.layers.layers.get("parcels")
        check_true("le particelle vere sono sulla mappa",
                   live_parcels is not None)
        check("...una per particella riportata", live_parcels.featureCount(),
              live_state.cadastre.n_parcels)
        live_feature = next(live_parcels.getFeatures())
        print("        feature: {0}".format(
            {name: live_feature[name]
             for name in ("comune", "belfiore", "foglio", "particella")}))
        check_true("...con geometria valida",
                   live_feature.geometry().isGeosValid())
        check_true("...e il comune risolto in chiaro",
                   bool(live_feature["comune"])
                   and live_feature["comune"] != "--")
        check_true("...e il codice Belfiore", bool(live_feature["belfiore"]))
        check("la tabella nel pannello le elenca",
              live_panel.parcel_table.rowCount(),
              live_state.cadastre.n_parcels)
    else:
        skip("le particelle vere sono sulla mappa",
             "il servizio non ha riportato particelle sull'area di prova: "
             + live_panel.cadastre_status_label.text())
live_workspace.unmount()


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
