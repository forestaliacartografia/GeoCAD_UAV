# Architettura

## Principio guida

**La matematica non importa QGIS.**

Ogni modulo di calcolo è puro Python + numpy, con un adattatore QGIS sottile
sopra. Non è una preferenza stilistica: è ciò che rende verificabile il valore
di questo plugin. Le oltre 300 asserzioni numeriche della suite girano
sull'interprete di sistema, senza un runtime QGIS, e coprono fotogrammetria,
inseguimento del terreno, ray casting, geometria CAD, reticoli e filtri
topografici.

Dove QGIS serve davvero — clipping GEOS, lettura raster, scrittura layer — la
dipendenza è confinata in funzioni identificabili, spesso con l'import dentro
la funzione stessa.

```
                 puro numpy                 |   adattatore QGIS
  ---------------------------------------------------------------------
  core/photogrammetry, planar, geometry_    |   cad/primitives
  engine, transform2d, grid, z (numerica),  |   forest/planting_for_geometry
  uav/terrain_follow, forest/planting       |   uav/survey, io/layer_factory
  (algoritmi), forest/stats                 |   core/crs, snap, undo, geodesy
```

## Struttura

```
geocad_uav/
  __init__.py            classFactory
  metadata.txt           QGIS 3.34 – 3.99, hasProcessingProvider
  plugin.py              toolbar, menu, dock, provider (initGui/unload simmetrici)
  icon.svg

  core/
    constants.py         ogni tolleranza e default, in un posto solo
    errors.py            eccezioni tipizzate con messaggio utente italiano + hint
    units.py             m/cm/mm/km/ft/in, deg/rad/gon, m2/ha/acre, m/s/km/h/kt
    models.py            ParametricRecord, PlantingRecord, DroneProfile,
                         Waypoint, PhotoCenter, Mission, MissionStats,
                         VerticalDatum, AltitudeMode
    planar.py            StripFrame + utilità polilinea (condiviso uav/ e forest/)
    geometry_engine.py   primitive CAD risolte algebricamente
    transform2d.py       roto-traslazione, scala, specchiatura, serie
    grid.py              reticoli: rect, square, quincunx, hex
    crs.py               guardia P1: nessuna metrica in CRS geografico
    geodesy.py           misura ellissoidica (QgsDistanceArea)
    z.py                 campionamento DEM bilineare, ray casting, Horn 3×3
    snap.py              wrapper su QgsSnappingUtils + snap di costruzione
    undo.py              transazioni sull'edit buffer, inserimenti a lotti

  cad/
    primitives.py        engine → QgsGeometry + ParametricRecord
    modifiers.py         offset, raccordo, smusso, taglia, estendi, dividi...
    parametric.py        editor delle proprietà, rilevamento "broken"
    dynamic_input.py     parser di @25<37, #x,y, @dx,dy, 25m, 37d

  forest/
    planting.py          sesti + filtri topografici
    stats.py             KPI e densità

  uav/
    photogrammetry.py    GSD, impronta, spaziature, budget di velocità, OPK
    cameras.py           libreria camere (profiles/cameras.json)
    drones.py            libreria droni (profiles/drones.json)
    terrain_follow.py    profili AGL costante, densificazione, cinematica, drape
    survey.py            strip, boustrophedon, interlacciamento, corridoi
    mission.py           assemblaggio, divisione batterie, statistiche
    validator.py         gate di esportazione
    export.py            writer, ciascuno con la propria dichiarazione di schema

  io/layer_factory.py    layer QGIS, GeoPackage, raster di copertura e GSD
  gui/dock.py            pannello con anteprima live
  gui/mission_report.py  report HTML + profilo altimetrico in SVG inline
  processing/            provider + tre algoritmi
  profiles/              cameras.json, drones.json (editabili)
  tests/                 nove suite
  docs/
```

### Aggiunta rispetto alla struttura richiesta

`core/planar.py` non era nella struttura originale. Contiene `StripFrame` e le
utilità per polilinee, usate da `uav/survey`, `uav/terrain_follow` e
`forest/planting`. Distribuirle fra `transform2d.py` e `geometry_engine.py`
avrebbe duplicato lo stesso codice in due punti, che la specifica vieta
esplicitamente.

## Contratti fra moduli

```
GeometryEngine.<primitiva>(...)      -> ndarray (N, 2)
cad.primitives.build(tool, params)   -> (QgsGeometry, ParametricRecord)
CrsService.resolve_work_crs(crs, ..) -> WorkCrsDecision(work_crs, transform_required)
TerrainModel.sample(x, y)            -> ndarray | NaN   (NaN = no-data / fuori)
TerrainModel.from_layer(...)         -> (TerrainModel, warnings)
grid.generate_grid(bounds, spec)     -> GridResult(xy, row, col)
planting.plan_planting(...)          -> PlantingResult(plants, excluded, rows)
photogrammetry.solve_survey_geometry -> SurveyGeometry(gsd, W, L, D_side, D_front)
survey.plan_route(aoi, ...)          -> RoutePlan(legs, azimuth, warnings)
terrain_follow.build_flight_profile  -> FlightProfile(xy, s, z_terrain, z_flight)
mission.build_mission(aoi, terrain)  -> Mission
validator.validate(mission, ...)     -> ValidationReport(checks, errors, warnings)
export.write(mission, key, path)     -> path
```

## Ordine delle operazioni in `mission.build_mission`

L'ordine è deliberato e non è intercambiabile:

1. Risolvi la geometria fotogrammetrica (GSD, impronte, spaziature).
2. Disponi le strip nel piano.
3. Drappeggia ogni strip sul DEM ad AGL costante.
4. Colloca le prese a multipli **esatti** di `D_front`.
5. Assottiglia i waypoint alla tolleranza verticale, **senza mai** togliere una
   presa.
6. Limita la velocità segmento per segmento sulla pendenza reale.
7. Dividi in sotto-missioni su autonomia e limite waypoint.

I passi 5 e 6 vengono **dopo** il 4 perché l'assottigliamento non deve poter
spostare una presa, e il limite di velocità deve vedere i waypoint realmente
volati. Invertirli produce un piano che sembra corretto e non lo è.

## Scelte progettuali degne di nota

**Campionamento bilineare proprio.** `QgsRasterDataProvider.sample()` è
nearest-neighbour: restituisce il valore della cella che contiene il punto.
Un profilo di volo campionato così sale a gradini della dimensione della cella
DEM. Il plugin legge il DEM con `gdal.Warp` nel CRS di lavoro e interpola
bilinearmente, propagando il no-data ai quattro vicini (un alone di una cella,
conservativo nella direzione giusta).

**Douglas–Peucker a deviazione verticale.** L'algoritmo classico misura la
distanza perpendicolare, che mescola metri di progressiva e metri di quota e
sottostima l'errore di quota sui tratti ripidi. Qui l'aeromobile vola in linea
retta fra due waypoint, quindi l'errore su un campione scartato è esattamente
la sua distanza **verticale** dalla corda: è quella che viene misurata. La
tolleranza significa dunque letteralmente "la traiettoria volata resta entro
tanti metri dal profilo comandato".

**Direzione dominante della pendenza per tensore di struttura.** Mediare i
*vettori* gradiente su un'area li annulla su una forma simmetrica (una conca
media a zero). Il plugin usa l'autovettore principale del tensore di struttura
dei gradienti, che restituisce un *orientamento* invece di una direzione ed è
stabile sul terreno reale.

**Raccordi: verifica per spigolo E per lato.** Controllare solo che il raggio
stia dentro i due lati dello spigolo non basta: due raccordi adiacenti possono
rivendicare entrambi la stessa metà del lato condiviso, e gli archi si
intersecano. Il controllo per lato individua i casi e scarta lo spigolo più
esigente, contandolo, invece di produrre un anello auto-intersecante che GEOS
rifiuterà tre passaggi dopo.

**Interlacciamento onesto.** Con `n` strip e un salto richiesto `k`, un ordine
in cui *ogni* transizione mantenga `k` può semplicemente non esistere (con 5
strip e k = 3 il cammino si blocca dopo un passo). Il pianificatore non finge:
massimizza gli spazi e **dichiara** quante virate dovranno essere volate
uscendo dal blocco.

**`intersects` invece di `contains`.** `contains` è strettamente interno e
scarta ogni nodo esattamente sul bordo: su un'AOI di 100 × 100 m a passo 5 m i
441 nodi attesi diventerebbero 361. Lo stacco dal bordo si governa con il
parametro margine, non con un predicato che perde i punti in silenzio.

## Compatibilità 3.34 → 4.0

* `QgsField` accetta `QMetaType` dalla 3.38 e `QVariant` prima: `layer_factory.
  make_field` sonda una volta e memorizza il risultato.
* Il flag "avanzato" appartiene al **parametro**, non all'algoritmo:
  `processing.mark_advanced` prova `QgsProcessingParameterDefinition.
  FlagAdvanced` e ripiega su `Qgis.ProcessingParameterFlag.Advanced`.
* Nessun import da `PyQt5` diretto: sempre `qgis.PyQt`.

## Performance

* Lettura raster a blocco unico in numpy, mai `identify()` per pixel.
* `QgsSpatialIndex` + geometria GEOS preparata per copertura e ritaglio.
* Inserimenti a lotti dentro un solo comando di undo (`core/undo.py`).
* Ray casting vettorizzato: marcia + bisezione su tutti i raggi insieme.
* Anteprima del dock: sola aritmetica sul modello camera, nessun I/O.
