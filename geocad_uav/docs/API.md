# API pubblica

Per script della console Python, modelli Processing e integrazioni.
I moduli di calcolo **non importano QGIS**: si possono usare da soli.

## Fotogrammetria

```python
from geocad_uav.uav import cameras, photogrammetry as pg

camera = cameras.load_library()["dji_mavic3e"]
overlap = pg.Overlap(frontlap=0.80, sidelap=0.70)

geom = pg.solve_survey_geometry(camera, overlap, h_agl_m=80.0)
geom.gsd_m_px            # 0.0213280072 m/px
geom.footprint_across_m  # 112.611879 m
geom.d_side_m            # 33.783564 m
geom.d_front_m           # 16.924329 m

# oppure partendo dal GSD
geom = pg.solve_survey_geometry(camera, overlap, gsd_m_px=0.02)
geom.h_agl_m
```

Fornire entrambi `h_agl_m` e `gsd_m_px` è un errore, non una preferenza: se non
coincidono è un errore di pianificazione da vedere.

```python
budget = pg.build_speed_budget(geom, v_mission_ms=8.0, v_drone_max_ms=21.0)
budget.effective   # velocità applicabile
budget.binding     # quale vincolo l'ha determinata
```

## Terreno

```python
from geocad_uav.core.z import TerrainModel, slope_aspect

terrain, warnings = TerrainModel.from_layer(
    raster_layer, work_crs, (xmin, ymin, xmax, ymax), margin_m=100.0)

z = terrain.sample(x, y)          # NaN fuori extent o su no-data, mai estrapolato
slope, aspect = slope_aspect(terrain)      # Horn 3x3, gradi
terrain.dominant_slope_azimuth()           # orientamento della massima pendenza
```

`TerrainModel` accetta anche una griglia numpy diretta, utile nei test:

```python
model = TerrainModel(array, (x0, dx, 0, y0, 0, -dy), "EPSG:32632")
```

## Inseguimento del terreno

```python
from geocad_uav.uav import terrain_follow as tf

profile = tf.build_flight_profile(terrain, polyline, h_agl_m=80.0, step_m=5.0)
profile.agl                       # costante per costruzione

keep = tf.densify_waypoints(profile, dz_tolerance_m=2.0)   # indici da tenere
kin = tf.apply_climb_limits(xy, z, v_target_ms=8.0, climb_rate_ms=4.0)
kin.speed_ms, kin.climb_limited, kin.infeasible

ring = tf.drape_footprint(terrain, (x, y, z), azimuth_deg, camera)
```

## Missione completa

```python
from geocad_uav.uav import drones, mission as mi
from geocad_uav.core.models import AltitudeMode, VerticalDatum

params = mi.MissionParams(
    camera=camera,
    drone=drones.load_library()["dji_mavic3e"],
    overlap=overlap,
    h_agl_m=80.0,
    altitude_mode=AltitudeMode.TERRAIN,
    v_mission_ms=8.0,
    vertical_datum=VerticalDatum.ORTHOMETRIC_EGM96)

flight = mi.build_mission(aoi_geometry, terrain, params, "EPSG:32632")
flight.stats.n_photos, flight.stats.flight_time_s, flight.stats.n_batteries
```

## Validazione ed esportazione

```python
from geocad_uav.uav import export, validator

report = validator.validate(flight, params, terrain, aoi_geometry, crs)
report.is_valid, report.errors, report.warnings

export.write(flight, "litchi", "/percorso/missione.csv",
             transform=to_wgs84, overwrite=True)
export.write(flight, "mavlink", "/percorso/missione.waypoints",
             transform=to_wgs84, altitude_mode=export.ALT_RELATIVE_HOME,
             home_z=317.0, overwrite=True)
```

`export.FORMATS` elenca i writer con il loro schema dichiarato;
`export.describe_unimplemented()` spiega cosa non è offerto e perché.

## Geometria CAD

```python
from geocad_uav.core import geometry_engine as ge
from geocad_uav.cad import primitives as pr, modifiers as md

ring = ge.rectangle_from_center((100, 200), 25.0, 12.0, azimuth_deg=15.0)
ge.polygon_area(ring)          # 300.0 esatti

geom, record = pr.build(pr.TOOL_CIRCLE,
                        {"mode": "three_points", "x": 10, "y": 0,
                         "x2": 0, "y2": 10, "x3": -10, "y3": 0}, "EPSG:32632")

filleted, skipped = md.fillet_polyline(ring, radius_m=2.0, closed=True)
```

## Griglie e impianti

```python
from geocad_uav.core import grid
from geocad_uav.forest import planting, stats

spec = grid.GridSpec(spacing_x=3.0, spacing_y=2.0, azimuth_deg=15.0,
                     pattern=grid.PATTERN_QUINCUNX, margin_m=2.0)

result = planting.plan_planting_for_geometry(
    aoi_geometry, spec, terrain=terrain,
    topo_filter=planting.TopographicFilter(slope_max_deg=30.0,
                                           aspect_ranges=[(135, 225)]))

kpi = stats.compute_stats(result)
kpi.density_per_ha, kpi.fill_ratio
```

## Errori

Tutte le eccezioni derivano da `core.errors.GeoCadError` e portano un messaggio
utente in italiano più un suggerimento operativo:

```python
from geocad_uav.core.errors import GeoCadError

try:
    ...
except GeoCadError as exc:
    print(exc.user_message)   # cosa è andato storto
    print(exc.hint)           # cosa fare
    print(str(exc))           # dettaglio tecnico, per il log
```

## Algoritmi Processing

```python
import processing

processing.run("geocaduav:planflight", {
    "AOI": aoi_layer, "DEM": dem_layer,
    "TARGET_MODE": 0, "TARGET_VALUE": 80.0,
    "FRONTLAP": 80.0, "SIDELAP": 70.0,
    "OUT_WAYPOINTS": "TEMPORARY_OUTPUT",
    "OUT_PHOTOS": "TEMPORARY_OUTPUT",
    "OUT_LINES": "TEMPORARY_OUTPUT",
})
```

Disponibili: `geocaduav:planflight`, `geocaduav:creategrid`,
`geocaduav:forestplanting`.
