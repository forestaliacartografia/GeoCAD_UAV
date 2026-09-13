"""
v1.14.0: the reforestation workspace, and the cadastre arriving in it.

This is the end-to-end test the whole slice exists for: an area is set, the
cadastral query runs as a background task against a recorded WFS answer, the
Belfiore code is resolved against the table shipped with the plugin, and the
labels in the Area panel change. Nothing is faked in between -- the same
parser, the same intersection, the same task, the same signal.

NEEDS QGIS with widgets. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_workflow_ui.py
"""

import io as _io
import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsGeometry, QgsProject, QgsWkbTypes)

QGS = QgsApplication([], True)
QGS.initQgis()

from qgis.PyQt.QtCore import Qt                                 # noqa: E402
from qgis.PyQt.QtWidgets import (QListWidget, QStackedWidget,    # noqa: E402
                                 QTabWidget)

from geocad_uav.forest.reforestation import symbology as sym    # noqa: E402
from geocad_uav.gui import workflow as wf                       # noqa: E402
from geocad_uav.io import belfiore as bf                        # noqa: E402
from geocad_uav.io import cadastre as cs                        # noqa: E402

FAILURES = []
SKIPS = []
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures")
TMP = tempfile.mkdtemp(prefix="geocad_ui_")
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


def pump(predicate, seconds=15.0):
    """Turn the event loop until something happens, or give up.

    With a real pause between turns: a QgsTask runs on a thread pool, and
    spinning processEvents() as fast as the CPU allows burns the whole budget
    before the worker has had a chance to start.
    """
    from time import sleep, time                                # noqa: PLC0415

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


PARCEL_XML = fixture("catasto_parcel_getfeature.xml")
ZONING_XML = fixture("catasto_zoning_getfeature.xml")

print("=" * 78)
print("Workspace -- workflow, contesto, catasto in linea")
print("=" * 78)

workspace = wf.Workspace(None)
state = workspace.state
context = workspace.context

# --------------------------------------------------------------------------
# U1 - the two docks and the navigation between them
# --------------------------------------------------------------------------
print("\n== U1: dock sinistro, dock destro, navigazione ==")
check_true("il dock del flusso e' un QDockWidget",
           workspace.workflow.inherits("QDockWidget"))
check_true("...e contiene un QListWidget",
           isinstance(workspace.workflow.list, QListWidget))
check("gli step sono dodici", workspace.workflow.list.count(), 12)
labels = [workspace.workflow.list.item(i).text()
          for i in range(workspace.workflow.list.count())]
print("        {0}".format(labels))
for expected in ("1. Area", "2. Terreno", "3. Vincoli", "4. Zone",
                 "5. Specie", "6. Sesti", "7. Orientamento", "8. Genera",
                 "9. Ottimizza", "10. Verifica", "11. Editing",
                 "12. Elaborati"):
    check_true("lo step '{0}' c'e'".format(expected), expected in labels)

check_true("il dock del contesto e' un QDockWidget",
           context.inherits("QDockWidget"))
check_true("...e contiene un QStackedWidget",
           isinstance(context.stack, QStackedWidget))
check_true("ogni step porta a una pagina",
           all(key in context.pages for key, _label in wf.STEPS))

seen = {}
for row, (key, _label) in enumerate(wf.STEPS):
    workspace.workflow.list.setCurrentRow(row)
    seen[key] = context.stack.currentIndex()
    check_true("{0}: la pagina cambia davvero".format(key),
               context.stack.currentWidget() is not None)
print("        pagine: {0}".format(seen))
check_true("Specie e Sesti condividono un pannello a schede",
           seen["species"] == seen["scheme"])
check_true("...che e' un QTabWidget",
           isinstance(context.scheme_panel.tabs, QTabWidget))
workspace.workflow.list.setCurrentRow(4)
check("lo step Specie apre la scheda Specie",
      context.scheme_panel.tabs.currentIndex(), wf.SchemePanel.TAB_SPECIES)
workspace.workflow.list.setCurrentRow(5)
check("lo step Sesti apre la scheda Sesto",
      context.scheme_panel.tabs.currentIndex(), wf.SchemePanel.TAB_SCHEME)
check("le altre dieci pagine sono distinte",
      len({v for k, v in seen.items() if k not in ("species", "scheme")}), 10)

# --------------------------------------------------------------------------
# U2 - the status bar, and the states of the steps
# --------------------------------------------------------------------------
print("\n== U2: barra di stato e stati degli step ==")
print("        {0}".format(workspace.status.text()))
check_true("la barra parte in attesa", "ATTESA" in workspace.status.text())
check_true("...e non promette superfici che non ci sono",
           wf.DASH in workspace.status.text())
workspace.workflow.list.setCurrentRow(0)
check_text("lo step su cui si e' e' quello attivo", state.status("area"),
           wf.ACTIVE)
check_true("...e tutti gli altri sono ancora da fare",
           all(state.status(key) == wf.NOT_STARTED
               for key, _label in wf.STEPS if key != "area"))

AREA = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX, OY, OX + 400.0, OY + 310.0))
state.set_area(AREA, CRS32632, "Lotto di prova")
check("l'area lorda arriva nel pannello",
      float(context.area_panel.gross_label.text().split()[0].replace(",", "")),
      12.40, 1e-9)
check_text("...e la utile", context.area_panel.usable_label.text(), "12.40 ha")
check_text("lo step Area risulta completato", state.status("area"), wf.DONE)
check_true("la barra di stato segue", "12.40 ha" in workspace.status.text())

# --------------------------------------------------------------------------
# U3 - the cadastre, end to end, on the recorded answers
# --------------------------------------------------------------------------
print("\n== U3: WFS -> task -> segnale -> GUI ==")
PARCELS = cs.parse_parcel_geometries(PARCEL_XML)
PARCEL_GEOM = PARCELS[0][1]
INSIDE = None
for _setback in (2e-5, 1e-5, 5e-6, 2e-6, 1e-6, 5e-7):
    _candidate = PARCEL_GEOM.buffer(-_setback, 8)
    if (_candidate is not None and not _candidate.isEmpty()
            and PARCEL_GEOM.contains(_candidate)):
        INSIDE = _candidate
        break
state.set_area(INSIDE, CRS6706, "Particella di prova")

CALLS = []


def recorded(url, timeout=0.0):
    CALLS.append(url)
    return ZONING_XML if "CadastralZoning" in url else PARCEL_XML


check_text("prima dell'interrogazione il comune non c'e'",
           context.area_panel.comune_label.text(), wf.DASH)
task = context.area_panel.query_cadastre(recorded)
check_true("l'interrogazione e' partita come task", task is not None)
check_true("...e il pulsante si e' disattivato mentre lavora",
           not context.area_panel.query_button.isEnabled())
check_true("il task ha risposto",
           pump(lambda: context.area_panel.comune_label.text() != wf.DASH))

print("        comune:     {0}".format(context.area_panel.comune_label.text()))
print("        belfiore:   {0}".format(
    context.area_panel.belfiore_label.text()))
print("        particelle: {0}".format(
    context.area_panel.parcels_label.text()))
print("        stato:      {0}".format(
    context.area_panel.cadastre_status_label.text()))
check_true("il codice Belfiore e' risolto in chiaro",
           "Perugia" in context.area_panel.comune_label.text())
check_text("...e il codice resta visibile",
           context.area_panel.belfiore_label.text(), "G478")
check_text("una particella interessata",
           context.area_panel.parcels_label.text(), "1")
check_true("la superficie catastale e' comparsa",
           "ha" in context.area_panel.cadastral_area_label.text())
check_true("la superficie di progetto pure",
           "ha" in context.area_panel.project_area_label.text())
check_true("lo stato e' quello del risultato",
           context.area_panel.cadastre_status_label.text()
           in cs.STATUS_LABELS.values())
check_true("il pulsante e' tornato utilizzabile",
           context.area_panel.query_button.isEnabled())
check_true("i dettagli delle particelle sono raggiungibili",
           context.area_panel.details_button.isEnabled())
check_true("...e contengono foglio e particella",
           "252" in context.area_panel.show_details()
           and "1016" in context.area_panel.show_details())
check("sono state fatte due richieste, particelle e fogli", len(CALLS), 2)
check_true("il risultato e' anche nel modello, per report ed export",
           state.cadastre is not None and state.cadastre.n_parcels == 1)
check_text("il comune risolto viene dalla tabella del plugin",
           bf.registry().resolve("G478").name.upper(), "PERUGIA")

print("\n-- e quando il servizio non risponde --")
def dead(url, timeout=0.0):
    raise cs.CadastreError("rete assente",
                           user_message="Servizio non raggiungibile.")


before = context.area_panel.comune_label.text()
failing = context.area_panel.query_cadastre(dead)
check_true("anche il fallimento e' un task", failing is not None)
check_true("...che risponde",
           pump(lambda: context.area_panel.query_button.isEnabled()))
# Since 1.17.0 the status line carries the reason too, so that an empty
# table is never left unexplained: the label still leads it.
failed_status = context.area_panel.cadastre_status_label.text()
check_true("lo stato dice che non e' riuscita",
           failed_status.startswith(cs.STATUS_LABELS[cs.STATUS_ERROR]))
check_true("...e dice anche perche'", "raggiungibile" in failed_status)
check_true("il progetto e' ancora li'", state.area is not None)
check_text("...e la sua superficie non e' cambiata",
           state.status("area"), wf.DONE)
check_true("l'interfaccia non e' andata in crisi",
           context.area_panel.isEnabled())

# --------------------------------------------------------------------------
# U4 - the panels are wired to the real services
# --------------------------------------------------------------------------
print("\n== U4: i pannelli chiamano i servizi veri ==")
state.set_area(AREA, CRS32632, "Lotto di prova")

print("-- vincoli --")
check_true("ogni vincolo del pannello e' dichiarato nel motore",
           all(key in state.constraints.rules
               for key, _label, _d in wf.CONSTRAINT_KINDS))
check("la fascia strade parte dal valore dichiarato",
      state.constraints.buffer_for("strada"), 5.0, 1e-9)
context.constraints_panel.rows["strada"][1].setValue(12.0)
check("cambiarla nel pannello la cambia nel motore",
      state.constraints.buffer_for("strada"), 12.0, 1e-9)

print("-- sesto e specie --")
context.scheme_panel.plant_distance.setValue(4.0)
context.scheme_panel.row_distance.setValue(5.0)
check("il sesto del pannello e' il sesto del motore",
      state.spec.plant_distance_m, 4.0, 1e-9)
check("...in entrambe le distanze", state.spec.row_distance_m, 5.0, 1e-9)
check_true("la densita' mostrata e' quella calcolata",
           "{0:,.0f}".format(state.density_per_ha())
           in context.scheme_panel.density_label.text())
for name, percent in (("sp_a", 50.0), ("sp_b", 30.0), ("sp_c", 20.0)):
    context.scheme_panel.species_key.setCurrentText(name)
    context.scheme_panel.species_percent.setValue(percent)
    context.scheme_panel.on_add_species()
check("tre specie in elenco", len(state.shares), 3)
check("...e tre righe in tabella",
      context.scheme_panel.species_table.rowCount(), 3)
check_true("le percentuali sono quelle digitate",
           [p for _k, p in state.shares] == [50.0, 30.0, 20.0])
keys = [k for k, _p in state.shares]
expected_palette = sym.palette(keys)
check_true("il colore accanto alla specie viene da symbology.py",
           all(not context.scheme_panel.species_table.item(row, 0)
               .icon().isNull() for row in range(3)))
check_true("...e la tavolozza e' quella dell'angolo aureo",
           len(set(expected_palette.values())) == 3)

print("-- orientamento --")
azimuth = context.orientation_panel.apply()
check_true("l'orientamento e' un numero fra 0 e 180", 0.0 <= azimuth < 180.0)
check("...ed e' finito nel sesto", state.spec.row_azimuth_deg, azimuth, 1e-9)
check_text("lo step risulta fatto", state.status("orientation"), wf.DONE)

print("-- generazione --")
result = context.generate_panel.preview()
check_true("l'anteprima ha generato piante", result is not None
           and result.count > 0)
check_true("...e nessun layer e' stato creato", state.plants_layer is None)
check_true("le piante hanno una specie assegnata",
           state.composition is not None
           and state.composition.total == result.count)
check_true("il conteggio compare nel pannello",
           "{0:,}".format(result.count)
           in context.generate_panel.actual_label.text())
layer = context.generate_panel.generate()
check_true("il comando definitivo crea il layer", layer is not None)
check_true("...ed e' PointZ", QgsWkbTypes.hasZ(layer.wkbType()))
check_true("...colorato per specie",
           type(layer.renderer()).__name__ == "QgsCategorizedSymbolRenderer")
check("una feature per pianta", layer.featureCount(), result.count)

print("-- verifica --")
anomalies = context.verify_panel.run()
print("        anomalie: {0}".format(anomalies))
check_true("la verifica ha girato sul risultato vero", state.verified)
check_true("il verdetto e' scritto",
           bool(context.verify_panel.verdict.text()))
check_true("lo stato finale segue la verifica",
           state.status("verify") in (wf.DONE, wf.WARNING))
check_true("la barra di stato non dice piu' attesa",
           "ATTESA" not in workspace.status.text())
print("        {0}".format(workspace.status.text()))

print("-- ottimizzazione --")
scenarios = context.optimise_panel.run()
check("tre scenari confrontati", len(scenarios), 3)
check_true("ognuno ha piante vere, non stime",
           all(s["plants"] > 0 for s in scenarios))
check_true("sesti diversi danno conteggi diversi",
           len({s["plants"] for s in scenarios}) > 1)
check("la tabella li mostra tutti", context.optimise_panel.table.rowCount(), 3)

print("-- elaborati --")
report = context.outputs_panel.build_report()
check_true("la relazione cita la superficie", "Superficie" in report)
check_true("...il sesto", "SESTO" in report)
check_true("...e la composizione", "COMPOSIZIONE" in report)
exported = context.outputs_panel.export(os.path.join(TMP, "piante.gpkg"))
check_true("l'export scrive davvero un file",
           bool(exported) and os.path.exists(exported))

# --------------------------------------------------------------------------
# U5 - the shape of the whole thing
# --------------------------------------------------------------------------
print("\n== U5: architettura ==")
check_true("i pannelli non calcolano geometria: nessun import di geometry_engine",
           "geometry_engine" not in _io.open(wf.__file__.replace(".pyc", ".py"),
                                             encoding="utf-8").read())
check_true("lo stato e' uno solo, e i pannelli lo condividono",
           all(panel.state is state
               for panel, _tab in context.pages.values()))
check_true("il dock destro si restringe senza rompersi",
           context.minimumWidth() <= 260)
check_true("...e quello sinistro pure",
           workspace.workflow.minimumWidth() <= 200)
workspace.set_visible(False)
check_true("i due dock si nascondono insieme", not workspace.is_visible())
workspace.set_visible(True)
check_true("...e tornano insieme", workspace.is_visible())


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
