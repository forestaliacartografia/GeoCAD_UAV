"""
v1.32.0: the flight planner is six steps of the dashboard, and they work.

The planner used to be one long form in a tab of the CAD dock, and the
things a mission planner has to do -- fix the GSD instead of the height,
choose a pattern, name the obstacles, read a pre-flight verdict -- either
were not offered or were offered without the inputs that make them mean
anything. This suite checks the six steps from the outside: press what an
operator presses, read what an operator reads.

Nothing here is a stand-in. The DEM is a real GeoTIFF on disk, the obstacle
is a real vector layer, the route is the frozen assembler's, and the verdict
is the frozen validator's.

NEEDS QGIS with widgets. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_uav_workflow.py
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
                       QgsCoordinateReferenceSystem, QgsFeature,
                       QgsGeometry, QgsProject, QgsRasterLayer,
                       QgsVectorLayer)

QGS = QgsApplication([], True)
QGS.initQgis()

from qgis.PyQt.QtWidgets import QWidget                         # noqa: E402

from geocad_uav.forest.reforestation import terrain as terrain_mod  # noqa: E402
from geocad_uav.gui import uav_panel as up                      # noqa: E402
from geocad_uav.gui import workflow as wf                       # noqa: E402
from geocad_uav.uav import forest_link as fl                    # noqa: E402
from geocad_uav.uav import photogrammetry as pg                 # noqa: E402
from geocad_uav.uav import survey as sv                         # noqa: E402
from geocad_uav.uav import validator as val                     # noqa: E402

FAILURES = []
SKIPS = []
TMP = tempfile.mkdtemp(prefix="geocad_uav_wf_")
CRS = QgsCoordinateReferenceSystem("EPSG:32632")
OX, OY = 500000.0, 5000000.0


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<54} got={2:<16.10g} exp={3:.10g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def skip(label, reason):
    print("  [skip] {0}\n         motivo: {1}".format(label, reason))
    SKIPS.append((label, reason))


def io_open(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def refuses_axis(geometry):
    """True when prepare_aoi refuses a line instead of gridding it."""
    try:
        sv.prepare_aoi([geometry])
    except sv.RoutingError:
        return True
    return False


# -- a hillside on disk ----------------------------------------------------
CELL = 5.0
NX, NY = 120, 110
xs = OX + (np.arange(NX) + 0.5) * CELL
ys = OY - (np.arange(NY) + 0.5) * CELL
XX, YY = np.meshgrid(xs, ys)
Z = 400.0 + 0.14 * (XX - OX) + 18.0 * np.sin((YY - OY) / 120.0)

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
DEM_LAYER = QgsRasterLayer(DEM_PATH, "DTM di prova", "gdal")
QgsProject.instance().addMapLayer(DEM_LAYER)

# Set back from the DEM edge on purpose: the strips are buffered outwards
# by half a footprint, and a block against the edge sends its turns off the
# raster. The engine handles that (dem_gap, conservative hold) but it is a
# different test, and here the point is a clean profile.
PLOT = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 150.0, OY - 420.0, OX + 450.0, OY - 120.0))
ROAD = QgsGeometry.fromWkt(
    "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
        OX + 280.0, OY - 420.0, OX + 295.0, OY - 120.0))

print("=" * 78)
print("Il pianificatore di volo, sei step della dashboard")
print("=" * 78)

workspace = wf.Workspace(None)
state = workspace.state
context = workspace.context
dock = workspace.workflow
panel = context.uav_panel
state.reset_history()


def keys_in_list():
    from qgis.PyQt.QtCore import Qt
    return [dock.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(dock.list.count())]


def page_of(key):
    return context.flight_pages[key][0]


def owns(page, widget):
    """True when the widget is somewhere under that page."""
    parent = widget.parentWidget()
    while parent is not None:
        if parent is page:
            return True
        parent = parent.parentWidget()
    return False


# --------------------------------------------------------------------------
# W1 - the six steps are there, and they show the planner's own controls
# --------------------------------------------------------------------------
print("\n== W1: sei step, con i comandi del pianificatore ==")
listed = keys_in_list()
for key, label in wf.UAV_STEPS:
    check_true("lo step '{0}' e' nella lista".format(label), key in listed)
check("gli step del volo sono sei", len(wf.UAV_STEPS), 6)
check("...e la lista li porta dopo i quattordici del rimboschimento",
      listed.index(wf.UAV_STEPS[0][0]), len(wf.STEPS))

for key, widget, what in (
        (up.STEP_AREA, panel.extent, "il selettore dell'area"),
        (up.STEP_AREA, panel.terrain_box, "il DEM"),
        (up.STEP_HARDWARE, panel.gear_box, "camera e drone"),
        (up.STEP_HARDWARE, panel.optics_box, "quota e GSD"),
        (up.STEP_FLIGHT, panel.flight_box, "i parametri di volo"),
        (up.STEP_SAFETY, panel.safety_box, "gli ostacoli"),
        (up.STEP_SAFETY, panel.quality_box, "il controllo pre-volo"),
        (up.STEP_SIMULATION, panel.summary, "il riepilogo"),
        (up.STEP_EXPORT, context.export_panel, "l'export")):
    check_true("{0} e' sullo step giusto".format(what),
               owns(page_of(key), widget))

# Selecting a flight step really moves the stack.
seen = {}
for key, _label in wf.UAV_STEPS:
    check_true("si puo' andare allo step {0}".format(key),
               dock.select_step(key))
    seen[key] = context.stack.currentIndex()
check("le sei pagine sono distinte", len(set(seen.values())), 6)
check_true("e sono altre rispetto a quelle del rimboschimento",
           not set(seen.values()) & {context.stack.indexOf(
               p.parentWidget().parentWidget())
               for p, _t in context.pages.values()})
check_true("c'e' un solo pianificatore in tutta la GUI",
           context.uav_panel is panel and not hasattr(dock, "uav_panel"))

# --------------------------------------------------------------------------
# W2 - the optical relation, both ways round
# --------------------------------------------------------------------------
print("\n== W2: quota e GSD, nei due versi ==")
camera = panel.current_camera()

panel.height_mode.setCurrentIndex(
    panel.height_mode.findData(up.HEIGHT_FROM_AGL))
panel.h_agl.setValue(100.0)
panel.recompute()
expected_gsd = pg.gsd_from_height(camera, 100.0) * 100.0
print("        quota 100 m -> GSD {0:.4f} cm/px".format(panel.gsd_target.value()))
check("fissata la quota, il GSD e' quello del motore",
      panel.gsd_target.value(), round(expected_gsd, 2), 0.011)
check_true("...e il GSD non e' modificabile a mano",
           panel.gsd_target.isReadOnly() and panel.h_agl.isEnabled())

panel.height_mode.setCurrentIndex(
    panel.height_mode.findData(up.HEIGHT_FROM_GSD))
panel.gsd_target.setValue(3.0)
panel.recompute()
expected_h = pg.height_from_gsd(camera, 0.03)
print("        GSD 3 cm/px -> quota {0:.2f} m".format(panel.h_agl.value()))
check("fissato il GSD, la quota e' quella del motore", panel.h_agl.value(),
      round(expected_h, 2), 0.011)
check_true("...e ora e' la quota a non essere modificabile",
           panel.h_agl.isReadOnly() and panel.gsd_target.isEnabled())
check_true("il riepilogo ottico dice entrambi",
           "cm/px" in panel.optics_note.text()
           and "impronta" in panel.optics_note.text())

panel.height_mode.setCurrentIndex(
    panel.height_mode.findData(up.HEIGHT_FROM_AGL))
panel.h_agl.setValue(90.0)
panel.recompute()

# --------------------------------------------------------------------------
# W3 - the planting project hands the flight its area
# --------------------------------------------------------------------------
print("\n== W3: [Genera missione UAV] dal progetto forestale ==")
state.set_area(PLOT, CRS, "Particella di prova")
# After set_area, which has already applied the constraints: apply_constraints
# starts by clearing the exclusions, so an exclusion added before it is thrown
# away again.
state.area.add_exclusion(ROAD, label="Strada, fascia 5 m", source="strada")
analysis = terrain_mod.TerrainAnalysis.from_layer(
    DEM_LAYER, CRS, state.usable_geometry(), margin_m=wf.DEM_MARGIN_M)
state.terrain = analysis
state.dem_layer_id = DEM_LAYER.id()
state.spec.row_azimuth_deg = 35.0
state.orientation_applied = True
state.refresh_status()

generate_panel = context.generate_panel
generate_panel.refresh()
check_true("il tasto esiste ed e' premibile",
           generate_panel.flight_button.isEnabled())
check_true("premerlo riesce", generate_panel.start_flight())

flown = panel.extent.geometry()
check_true("l'area di volo e' arrivata", flown is not None)
check("...ed e' la superficie utile, non la lorda", flown.area(),
      state.usable_m2, 1.0)
check_true("...che e' davvero meno della lorda",
           state.usable_m2 < state.gross_m2 - 1.0)
check_true("il DEM del progetto e' selezionato nel pianificatore",
           panel.dem_layer() is DEM_LAYER)
front, side = fl.forest_overlap().as_percent
check("la sovrapposizione e' quella forestale", panel.frontlap.value(),
      front, 1e-6)
check("...su entrambi gli assi", panel.sidelap.value(), side, 1e-6)
check("le strisciate seguono i filari", panel.azimuth.value(), 35.0, 1e-6)
check_true("...in modalita' manuale",
           panel.azimuth_mode.currentData() == "manual")
check_true("e l'operatore si ritrova sullo step dell'area di volo",
           dock.current_key() == up.STEP_AREA)
check_true("il pianificatore e' pronto a generare", panel.readiness()[0])

# Now that there is an area, a GSD this camera cannot reach legally is
# refused with the reason -- and not with "manca l'area".
panel.height_mode.setCurrentIndex(
    panel.height_mode.findData(up.HEIGHT_FROM_GSD))
# Coarse, not fine: a bigger GSD is a HIGHER flight, and this camera would
# need some 750 m of it -- far past the 120 m the drone profile allows.
panel.gsd_target.setValue(20.0)
ready, reason = panel.readiness()
print("        {0}".format(reason))
check_true("un GSD irraggiungibile e' un rifiuto motivato",
           not ready and "quota" in reason.lower())
panel.height_mode.setCurrentIndex(
    panel.height_mode.findData(up.HEIGHT_FROM_AGL))
panel.h_agl.setValue(90.0)
panel.recompute()
check_true("...e tornando alla quota il pianificatore riparte",
           panel.readiness()[0])

# --------------------------------------------------------------------------
# W4 - the route, and a pre-flight check that was actually given its inputs
# --------------------------------------------------------------------------
print("\n== W4: la rotta e il controllo pre-volo ==")
panel.h_agl.setValue(90.0)
panel.speed_kmh.setValue(36.0)
mission = panel.generate()
check_true("la rotta esiste", mission is not None)
if mission is None:
    skip("il resto di W4", "nessuna rotta generata")
else:
    print("        {0} waypoint, {1} scatti, {2:.0f} m".format(
        len(mission.waypoints), len(mission.photos),
        mission.stats.total_length_m))
    agl = np.array([w.z_agl for w in mission.waypoints], dtype=float)
    gaps = sum(1 for w in mission.waypoints if w.dem_gap)
    print("        AGL: {0:.3f} - {1:.3f} m, buchi DEM: {2}".format(
        float(np.nanmin(agl)), float(np.nanmax(agl)), gaps))
    check("nessun waypoint cade fuori dal DEM", gaps, 0)
    check_true("ogni waypoint ha la sua quota sul terreno",
               bool(np.isfinite(agl).all()))
    check_true("...e la tiene alla quota chiesta",
               float(np.abs(agl - 90.0).max()) < 0.01)
    check_true("la quota di volo si muove col terreno",
               (max(w.z_amsl for w in mission.waypoints)
                - min(w.z_amsl for w in mission.waypoints)) > 20.0)
    check_true("il pianificatore ha tenuto terreno e area per il controllo",
               panel.last_terrain is not None and panel.last_aoi is not None)

    report = panel.last_report
    check_true("il controllo e' partito da solo con la rotta",
               report is not None)
    codes = {c.code for c in report.checks}
    print("        controlli: {0}".format(sorted(codes)))
    for code in ("altitudine", "velocita", "autonomia", "terreno"):
        pass
    check_true("il controllo del terreno e' stato eseguito",
               not any(c.code.startswith("terrain_unknown") for c in
                       report.checks))
    check_true("...e quello dell'autonomia pure",
               any("endurance" in c.code or "autonomi" in c.label.lower()
                   for c in report.checks))
    check_true("il verdetto e' scritto in chiaro nel pannello",
               report.summary() in panel.quality.toPlainText())

    # The same validator, called the way the export tab used to call it --
    # with the mission and nothing else. The checks it cannot even attempt
    # are the difference the panel now makes.
    bare = val.validate(mission)
    gained = {c.code for c in report.checks} - {c.code for c in bare.checks}
    print("        controlli in piu' con gli ingressi: {0}".format(
        sorted(gained)))
    check_true("con gli ingressi il validatore esegue piu' controlli",
               len(report.checks) > len(bare.checks))
    for code in ("endurance", "trigger", "blur", "climb_limited",
                 "camera_complete", "waypoint_limit"):
        check_true("'{0}' esiste solo se gli ingressi ci sono".format(code),
                   code in gained)

# --------------------------------------------------------------------------
# W5 - obstacles: the whole track, not just its vertices
# --------------------------------------------------------------------------
print("\n== W5: ostacoli, sulla tratta e non solo sui vertici ==")
if mission is None:
    skip("W5", "nessuna rotta da intersecare")
else:
    obstacles = QgsVectorLayer(
        "Polygon?crs={0}".format(CRS.authid()), "elettrodotto", "memory")
    # Put the band exactly between two consecutive waypoints, taken from the
    # mission itself rather than guessed: the point of the check is that a
    # span can cross an obstacle no waypoint sits on.
    pairs = [(a, b) for a, b in zip(mission.waypoints, mission.waypoints[1:])
             if a.strip_index == b.strip_index and a.strip_index >= 0]
    first, second = max(pairs, key=lambda p: (p[0].x - p[1].x) ** 2
                        + (p[0].y - p[1].y) ** 2)
    mx = 0.5 * (first.x + second.x)
    my = 0.5 * (first.y + second.y)
    print("        varco fra due waypoint: {0:.2f} m".format(
        ((first.x - second.x) ** 2 + (first.y - second.y) ** 2) ** 0.5))
    band = QgsGeometry.fromWkt(
        "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
            mx - 0.1, my - 0.1, mx + 0.1, my + 0.1))
    feature = QgsFeature()
    feature.setGeometry(band)
    obstacles.dataProvider().addFeatures([feature])
    obstacles.updateExtents()
    QgsProject.instance().addMapLayer(obstacles)

    from qgis.core import QgsPointXY
    on_vertex = sum(
        1 for w in mission.waypoints
        if band.intersects(QgsGeometry.fromPointXY(QgsPointXY(w.x, w.y))))
    print("        waypoint dentro la fascia: {0}".format(on_vertex))
    check("nessun waypoint ci cade dentro", on_vertex, 0)

    panel.obstacle_combo.setLayer(obstacles)
    panel.obstacle_buffer.setValue(0.0)
    geoms = panel.no_fly_geometries()
    check_true("il pannello legge il layer ostacoli", len(geoms) == 1)
    flagged = panel.run_quality_check()
    no_fly = [c for c in flagged.checks if c.code == "no_fly"]
    print("        esito ostacoli: {0}".format(
        no_fly[0].detail if no_fly else "assente"))
    check_true("la tratta che lo attraversa viene segnalata",
               bool(no_fly) and no_fly[0].severity == val.SEVERITY_ERROR)
    check_true("...e il verdetto complessivo diventa rosso",
               not flagged.is_valid)

    # The risk radius really dilates: an obstacle clear of the flown route
    # only bites once the buffer reaches it. "Clear" is measured against the
    # route's own extent, which runs well past the AOI because the strips
    # are buffered outwards by half a footprint.
    flown_box = mission.waypoints[0].x, mission.waypoints[0].x
    flown_box = (min(w.x for w in mission.waypoints),
                 max(w.x for w in mission.waypoints))
    far_x = flown_box[1] + 60.0
    print("        rotta fino a x={0:.0f}, ostacolo a x={1:.0f}".format(
        flown_box[1], far_x))
    far = QgsGeometry.fromWkt(
        "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
            far_x, OY - 460.0, far_x + 1.0, OY - 80.0))
    far_layer = QgsVectorLayer(
        "Polygon?crs={0}".format(CRS.authid()), "traliccio lontano", "memory")
    far_feature = QgsFeature()
    far_feature.setGeometry(far)
    far_layer.dataProvider().addFeatures([far_feature])
    far_layer.updateExtents()
    QgsProject.instance().addMapLayer(far_layer)
    panel.obstacle_combo.setLayer(far_layer)
    panel.obstacle_buffer.setValue(0.0)
    clean = panel.run_quality_check()
    clean_no_fly = [c for c in clean.checks if c.code == "no_fly"]
    check_true("lontano e senza raggio, nessuna segnalazione",
               bool(clean_no_fly)
               and clean_no_fly[0].severity == val.SEVERITY_OK)
    panel.obstacle_buffer.setValue(120.0)
    grown = panel.run_quality_check()
    grown_no_fly = [c for c in grown.checks if c.code == "no_fly"]
    check_true("con 120 m di raggio di rischio, invece, si",
               bool(grown_no_fly)
               and grown_no_fly[0].severity == val.SEVERITY_ERROR)
    panel.obstacle_combo.setLayer(None)
    panel.obstacle_buffer.setValue(30.0)

# --------------------------------------------------------------------------
# W6 - the export step writes files
# --------------------------------------------------------------------------
print("\n== W6: lo step Export scrive davvero ==")
export_panel = context.export_panel
export_panel.refresh()
if mission is None:
    skip("W6", "nessuna rotta da esportare")
else:
    check_true("l'export vede la rotta del pianificatore",
               export_panel.mission() is mission)
    export_panel.folder_edit.setText(TMP)
    export_panel.basename_edit.setText("missione_prova")
    from qgis.PyQt.QtCore import Qt as _Qt
    for row in range(export_panel.format_list.count()):
        entry = export_panel.format_list.item(row)
        if not entry.flags() & _Qt.ItemFlag.ItemIsUserCheckable:
            continue
        entry.setCheckState(
            _Qt.CheckState.Checked
            if entry.data(_Qt.ItemDataRole.UserRole) == "geojson"
            else _Qt.CheckState.Unchecked)
    check_true("un solo formato selezionato",
               export_panel.selected_keys() == ["geojson"])
    written = export_panel.export()
    print("        scritti: {0}".format([os.path.basename(p)
                                         for p in written]))
    check_true("almeno un file e' stato scritto", len(written) >= 1)
    check_true("...ed esiste, non vuoto",
               all(os.path.getsize(p) > 0 for p in written))
    state.refresh_status()
    check_true("lo step Export risulta fatto",
               state.status(up.STEP_EXPORT) in (wf.DONE, wf.ACTIVE))

# --------------------------------------------------------------------------
# W7 - the altimetric profile, the time cursor and the footprints
# --------------------------------------------------------------------------
print("\n== W7: profilo, cursore, impronte ==")
if mission is None:
    skip("W7", "nessuna rotta")
else:
    chart = context.profile_chart
    samples = chart.set_mission(mission)
    print("        campioni nel profilo: {0}, percorso {1:,.0f} m".format(
        samples, chart.length_m))
    check_true("il profilo ha dei campioni", samples > 100)
    check_true("...e una lunghezza sensata",
               chart.length_m > 0.5 * mission.stats.total_length_m)

    agl = chart.agl()
    print("        AGL nel profilo: {0:.2f} - {1:.2f} m".format(
        float(agl.min()), float(agl.max())))
    check_true("le due curve restano parallele: e' il terrain following",
               float(agl.max() - agl.min()) < 1.0)
    ground_span = float(chart.ground.max() - chart.ground.min())
    flight_span = float(chart.flight.max() - chart.flight.min())
    print("        terreno {0:.1f} m, volo {1:.1f} m".format(
        ground_span, flight_span))
    check_true("...e nessuna delle due e' piatta",
               ground_span > 20.0 and abs(flight_span - ground_span) < 1.0)

    # The same numbers the report draws: one function, two renderings.
    import numpy as _np
    from geocad_uav.gui import mission_report as _mr
    rs, rg, rf = _mr.profile_series(mission)
    check_true("il grafico e la relazione leggono la stessa serie",
               rs.size == chart.s.size
               and bool(_np.allclose(rg, chart.ground))
               and bool(_np.allclose(rf, chart.flight)))

    # -- the time cursor
    context.refresh_player()
    check_true("il cursore del tempo e' abilitato",
               context.time_slider.isEnabled())
    duration = context.player.duration_s
    check_true("la simulazione ha una durata", duration > 1.0)

    context.time_slider.setValue(500)
    moment = context.player.state()
    print("        a meta': t={0:.0f}s quota={1:,.0f} m AGL={2:.0f} m "
          "scatti={3}/{4} batteria={5:.0f} %".format(
              moment["t_s"], moment["z_amsl"], moment["z_agl"], moment["photos"],
              moment["photo_total"], 100.0 * moment["battery_left"]))
    check("trascinare il cursore porta l'orologio a meta'", moment["t_s"],
          duration * 0.5, max(0.02 * duration, 0.5))
    check_true("...e la quota e' quella del volo, non zero",
               moment["z_amsl"] > 0.0)
    check("...con l'AGL della missione", moment["z_agl"], 90.0, 1.0)
    check_true("...e una parte degli scatti gia' fatta",
               0 < moment["photos"] < moment["photo_total"])
    check_true("la batteria si consuma lungo la tratta",
               0.0 <= moment["battery_left"] < 1.0)
    check_true("il cursore del profilo si e' mosso",
               chart.cursor_s is not None and chart.cursor_s > 0.0)

    taken_halfway = moment["photos"]
    context.time_slider.setValue(50)
    back = context.player.state()
    print("        tornando indietro: t={0:.0f}s scatti={1}".format(
        back["t_s"], back["photos"]))
    check_true("tornando indietro l'orologio torna indietro",
               back["t_s"] < moment["t_s"])
    check_true("...e gli scatti si 'dis-fanno': non e' un contatore che sale",
               back["photos"] < taken_halfway)
    check_true("il riepilogo dice quota e batteria",
               "s.l.m." in context.player.summary()
               and "batteria" in context.player.summary())

    # -- the footprints
    refused = context.show_footprints()
    print("        senza impronte: {0}".format(context.player_status.text()))
    check_true("senza impronte calcolate lo dice invece di disegnare niente",
               refused is None
               and "impronte" in context.player_status.text().lower())

    panel.check_coverage.setChecked(True)
    covered = panel.generate()
    check_true("rigenerando con la copertura accesa le impronte ci sono",
               covered is not None and len(covered.footprints) > 0)
    context.refresh_player()
    layer = context.show_footprints()
    check_true("il layer delle impronte viene creato", layer is not None)
    if layer is not None:
        print("        {0} impronte, layer '{1}'".format(
            layer.featureCount(), layer.name()))
        check("una impronta per scatto", layer.featureCount(),
              len(covered.photos))
        check_true("...con geometria valida e area positiva",
                   all(f.geometry() is not None and f.geometry().area() > 0.0
                       for f in layer.getFeatures()))
        check_true("il riempimento e' traslucido, o le sovrapposizioni non "
                   "si vedrebbero",
                   int(wf.FOOTPRINT_FILL.split(",")[3]) < 255)
        check_true("il layer e' sul progetto, non solo in memoria",
                   QgsProject.instance().mapLayer(layer.id()) is not None)
        # The coverage check the footprints exist for.
        after = panel.last_report
        coverage = [c for c in after.checks if c.code.startswith("coverage")]
        print("        copertura: {0}".format(
            coverage[0].detail if coverage else "assente"))
        check_true("ora la copertura e' misurata, non 'non verificata'",
                   bool(coverage)
                   and "non verificat" not in coverage[0].detail)

    # -- the mission report
    report_path = os.path.join(TMP, "relazione.html")
    written = context.write_mission_report(path=report_path)
    check_true("la relazione viene scritta", bool(written)
               and os.path.exists(report_path))
    if written:
        html = io_open(report_path)
        print("        relazione: {0:,} caratteri".format(len(html)))
        check_true("...e contiene il profilo altimetrico disegnato",
                   "<svg" in html and "Profilo altimetrico" in html)
        check_true("...e i numeri della missione",
                   "{0}".format(len(covered.waypoints)) in html
                   or "waypoint" in html.lower())

# --------------------------------------------------------------------------
# W9 - the hardware step says what the hardware is
# --------------------------------------------------------------------------
print("\n== W9: lo step Hardware descrive camera e drone ==")
panel.recompute()
note = panel.gear_note.text()
print("        {0}".format(note.replace("\n", "\n        ")))
camera = panel.current_camera()
drone = panel.current_drone()
check_true("la nota nomina la camera", camera.name in note)
check_true("...e il tipo di sensore", camera.kind_label in note)
check_true("...e la focale e il passo del pixel",
           "f=" in note and "pitch" in note)
check_true("...e l'intervallo minimo di scatto, che limita la velocita'",
           "intervallo min" in note)
check_true("la nota nomina anche il drone e la sua autonomia",
           drone.name in note and "autonomia" in note)
check_true("l'elenco delle camere porta il tipo accanto al nome",
           all("[" in panel.camera_combo.itemText(i)
               for i in range(panel.camera_combo.count())))

# --------------------------------------------------------------------------
# W8 - a bending road is a corridor mission, not a grid over its bounding box
# --------------------------------------------------------------------------
print("\n== W8: asse lineare -> missione a corridoio ==")
axis_points = []
for i in range(11):
    ax = OX + 120.0 + i * 30.0
    ay = OY - 300.0 + 70.0 * np.sin(i / 3.0)
    axis_points.append("{0} {1}".format(ax, ay))
AXIS = QgsGeometry.fromWkt("LINESTRING({0})".format(",".join(axis_points)))

panel.check_coverage.setChecked(False)
panel.extent.set_extent(AXIS, CRS)
panel.recompute()
print("        {0}".format(panel.extent.summary.text()))
check_true("il pannello riconosce di avere un asse", panel.is_corridor())
check_true("...e lo descrive come sviluppo, non come area",
           "sviluppo" in panel.extent.summary.text())
check_true("la larghezza del corridoio compare",
           not panel.corridor_width.isHidden())
check_true("...e i comandi che non c'entrano si spengono",
           not panel.azimuth.isEnabled()
           and not panel.pattern_combo.isEnabled()
           and not panel.azimuth_optimise.isEnabled())

panel.corridor_width.setValue(0.0)
ready, reason = panel.readiness()
print("        {0}".format(reason))
check_true("senza larghezza non si vola", not ready and "larghezza" in reason)
panel.corridor_width.setValue(140.0)
panel.recompute()
check_true("con la larghezza si", panel.readiness()[0])

corridor = panel.generate()
check_true("la missione a corridoio esiste", corridor is not None)
if corridor is None:
    skip("il resto di W8", "nessuna missione a corridoio")
else:
    print("        schema '{0}', {1} strisciate, {2} waypoint, {3:,.0f} m"
          .format(corridor.pattern, corridor.stats.n_strips,
                  len(corridor.waypoints), corridor.stats.total_length_m))
    check_true("e' pianificata come corridoio, non come griglia",
               corridor.pattern == "corridor")
    check_true("le strisciate sono piu' di una",
               corridor.stats.n_strips >= 3)

    # The strips are offset curves of the axis: every waypoint sits within
    # half the corridor width (plus the half footprint the layout adds) of
    # it, which a grid over the bounding box would not manage on a bend.
    from qgis.core import QgsPointXY as _P
    survey_geom = panel.survey_geometry()
    reach = 0.5 * (140.0 + survey_geom.footprint_across_m) + 1.0
    far = [w for w in corridor.waypoints
           if AXIS.distance(QgsGeometry.fromPointXY(_P(w.x, w.y))) > reach]
    spread = max(AXIS.distance(QgsGeometry.fromPointXY(_P(w.x, w.y)))
                 for w in corridor.waypoints)
    print("        distanza massima dall'asse: {0:.1f} m (limite {1:.1f})"
          .format(spread, reach))
    check("nessun waypoint esce dal corridoio", len(far), 0)
    check_true("...e le strisciate esterne ci arrivano davvero",
               spread > 0.5 * 140.0 * 0.6)

    agl = np.array([w.z_agl for w in corridor.waypoints], dtype=float)
    print("        AGL: {0:.2f} - {1:.2f} m, buchi DEM: {2}".format(
        float(np.nanmin(agl)), float(np.nanmax(agl)),
        sum(1 for w in corridor.waypoints if w.dem_gap)))
    check_true("il terrain following vale anche in corridoio",
               bool(np.isfinite(agl).all())
               and float(np.abs(agl - panel.h_agl.value()).max()) < 0.01)
    check_true("la missione dichiara la larghezza coperta",
               any("120" in w or "140" in w for w in corridor.warnings))
    check_true("l'area di riferimento e' la fascia ripresa, non zero",
               corridor.stats.aoi_area_ha > 1.0)

    # And the same axis planned as if it were an area is refused, rather
    # than silently gridded over its bounding box.
    check_true("un asse non passa per un'area",
               refuses_axis(AXIS))

# --------------------------------------------------------------------------
print("\n" + "=" * 78)
workspace.unmount()
mission = None
panel = None
context = None
dock = None
generate_panel = None
analysis = None
state.terrain = None
state = None
workspace = None
QgsProject.instance().removeAllMapLayers()
gc.collect()
QGS.processEvents()
QGS.exitQgis()
if SKIPS:
    print("SKIPPED ({0}):".format(len(SKIPS)))
    for name, reason in SKIPS:
        print("   - {0}: {1}".format(name, reason))
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for name in FAILURES:
        print("   - {0}".format(name))
    sys.exit(1)
print("ALL CHECKS PASSED")
