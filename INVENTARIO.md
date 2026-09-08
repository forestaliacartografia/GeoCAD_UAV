# INVENTARIO — GeoCad UAV Toolkit, stato reale prima di 1.2.x

Fase 0. Nessuna riga di produzione scritta in questo turno.
Rilevato il 2026-09-08 sul working tree, non sulla documentazione.

---

## G. Suite (eseguita PRIMA di qualsiasi ispezione)

```
10 suite in 20.4 s — fail = 0

test_photogrammetry  0.17s   test_survey        1.02s
test_terrain_follow  0.26s   test_cad           1.04s
test_geometry        0.63s   test_mission       5.82s
test_grid            0.64s   test_processing    8.54s
test_forest          0.98s   test_map_tools     1.33s
```

Contratto rispettato: si può procedere.

Dimensioni: **49 moduli sorgente / 12 094 LOC**, 10 file di test / 3 556 LOC,
8 documenti. Versione corrente in `metadata.txt`: **1.1.1**.

---

## A. Albero reale e responsabilità

Questo è l'albero **esistente**, non uno ideale. Le milestone 1.2.x aggiungono
dentro questi package.

```
geocad_uav/
  __init__.py      21   classFactory
  plugin.py       272   toolbar, menu, map tool lifecycle, provider
  metadata.txt     75   1.1.1, qgisMinimumVersion 3.34
  icon.svg         28

  core/                 SERVIZI — nessuna dipendenza da Qt widgets
    constants.py    128 tolleranze e default, unica sede
    errors.py       161 eccezioni tipizzate, messaggi IT + hint
    units.py        216 m/cm/mm/km/ft/in, deg/rad/gon, m2/ha, m/s/kt
    models.py       316 ParametricRecord, Mission, Waypoint, DroneProfile...
    planar.py       241 StripFrame + utilità polilinea
    geometry_engine.py 480 primitive CAD risolte algebricamente
    transform2d.py  199 roto-traslazione, scala, specchiatura, serie
    grid.py         275 reticoli rect/square/quincunx/hex
    crs.py          195 guardia P1, UTM suggerita, transform
    geodesy.py       84 misura ellissoidica (QgsDistanceArea)
    z.py            473 campionamento DEM bilineare, ray casting, Horn 3x3
    snap.py         184 wrapper QgsSnappingUtils + snap di costruzione
    undo.py         120 transazioni edit buffer, inserimenti a lotti

  cad/
    primitives.py   259 engine -> QgsGeometry + ParametricRecord
    modifiers.py    438 offset, raccordo, smusso, taglia, estendi, dividi
    parametric.py   150 editor proprietà, rilevamento "broken"
    dynamic_input.py 222 parser 25 / 37d / @25<37 / #x,y / @dx,dy
    tools/
      base.py       820 macchina a stati + QgsMapTool + rubber band + HUD
      line.py        86
      polyline.py   235 catena N vertici, polare relativa
      rectangle.py  141
      circle.py      95
      __init__.py    46 TOOL_REGISTRY (4 tool)

  forest/
    planting.py     350 sesti + filtri pendenza/quota/esposizione
    stats.py        143 KPI, densità

  uav/
    photogrammetry.py 509 GSD, impronta, D_side, D_front, budget velocità
    terrain_follow.py 377 profili AGL costante, densificazione, drape
    survey.py         652 strip, boustrophedon, interlacciato, corridoio
    mission.py        555 assemblaggio, split batterie, statistiche
    validator.py      499 gate di esportazione
    export.py         558 writer con schema dichiarato
    cameras.py        114 / drones.py 132  librerie da profiles/*.json

  io/layer_factory.py 483 layer QGIS, GeoPackage, raster copertura/GSD
  gui/dock.py         564 pannello parametri + anteprima live
  gui/mission_report.py 390 report HTML + profilo altimetrico SVG inline
  gui/dialogs/            VUOTO (solo __init__)
  processing/
    provider.py      34
    alg_flight.py   457 geocaduav:planflight
    alg_design.py   389 geocaduav:creategrid, geocaduav:forestplanting
  profiles/cameras.json 162 / drones.json 168
  tests/  10 file      docs/  8 file
```

**Package assenti** da creare in 1.2.x: `settings/` (nessuna persistenza oggi).
`gui/dialogs/` esiste ma è vuoto.

---

## B. Entry point

| Elemento | Stato |
|---|---|
| `classFactory(iface)` | `__init__.py`, import differito di `plugin.py` |
| `initGui()` / `unload()` | speculari, verificati; unset dei map tool prima delle action |
| Toolbar | `iface.addToolBar("GeoCad UAV Toolkit")` — **8 action** |
| Dock | 1 `QDockWidget` (`GeoCadDock`), **0 QTabWidget** |
| Processing provider | `geocaduav`, **3 algoritmi** |
| Map tool | **4** (line, polyline, rectangle, circle) in `QActionGroup` esclusivo |
| Shortcut | Alt+Shift+L/P/R/C, con guardia `objectForSequence` |

**Le 8 action sulla toolbar madre di QGIS**: 1 toggle dock + 4 CAD + 3 lanci
Processing. La specifica 1.2 ne vuole **una**.

La dock è oggi una **colonna unica scorrevole** con 6 `QGroupBox`
(Area, CAD, Terreno, Camera/drone, Missione, Valori derivati). Nessun tab.

---

## C. Verde / Parziale / Assente

| Area | Stato | Evidenza |
|---|---|---|
| CAD primitives (engine) | **VERDE** | `test_geometry`, `test_cad`; quadrato lato 10 → area 100.000000 |
| CAD map tools | **PARZIALE** | 4 su 8 (manca square, ellipse, poligono regolare, arc) |
| **Snapping nei map tool** | **ASSENTE** | vedi criticità 1 |
| Modify su canvas | **ASSENTE** | motore c'è, tool no; vedi criticità 2 |
| Grips parametriche | **ASSENTE** | nessun `QgsVertexMarker` su feature selezionata |
| Grid | **VERDE (engine)** / dock ASSENTE | `test_grid` 21×21=441; nella dock solo un bottone che apre Processing |
| Forest | **VERDE (engine)** / dock ASSENTE | `test_forest` 3 135 piante su 3 136 teoriche |
| Photogrammetry | **VERDE** | golden Mavic 3E a 1e-9 |
| Terrain following | **VERDE** | `max\|Z_wp − (Z_DEM + H_AGL)\| = 0.0` |
| Validator | **VERDE** | 20+ controlli, errori bloccanti |
| Export adapter | **VERDE** | 9 formati scritti e riletti; WPML rifiutato |
| Report HTML | **VERDE, non collegato** | `mission_report.build_html` esiste; la dock non lo mostra |
| Pick AOI dalla dock | **ASSENTE** | la dock ha un combo AOI ma "Genera" apre il dialog Processing |
| Settings persistenti | **ASSENTE** | 0 occorrenze di `QgsSettings`/`QSettings` |
| i18n | **PARZIALE** | stringhe in `QCoreApplication.translate`, nessun `.ts` |
| Sessione GUI reale | **NON VERIFICATA** | tutto headless |

Moduli **non raggiungibili da un utente** oggi:

| Modulo | LOC | Importato da produzione |
|---|---|---|
| `cad/modifiers.py` | 438 | **0** |
| `core/snap.py` | 184 | **0** |
| `core/geodesy.py` | 84 | **0** |

`core/transform2d.py` è raggiunto solo indirettamente. `alg_flight.py` e
`alg_design.py` non compaiono per nome nei test ma sono eseguiti davvero via
`processing.run("geocaduav:planflight", ...)`.

---

## D. Duplicazioni

**Nessuna.** Verificato, non assunto:

| Rischio | Riscontro |
|---|---|
| Due dock | 1 sola sottoclasse `QDockWidget` |
| Due sampler Z | `def sample(` solo in `core/z.py` |
| Due parser polar | `dynamic_input.py`; `base.py` **delega** (`di.parse`/`di.resolve`) |
| Due writer export | `_WRITERS` solo in `uav/export.py` |
| Due fabbriche layer | `plugin.py` chiama `lf.memory_layer`, non ricrea |
| `print()` in release | 0 |

L'architettura è già unificata. 1.2.x deve **collegare**, non consolidare.

---

## E. Conflitti con QGIS

1. **Toolbar affollata** — 8 icone sulla toolbar madre. Da ridurre a 1 + il
   gruppo CAD dentro una toolbar del plugin.
2. **Snapping non integrato** — `core/snap.py` non è mai chiamato; i click
   usano `event.mapPoint()` grezzo. Nessun motore parallelo (bene), ma nemmeno
   quello nativo (male).
3. **Edit buffer** — corretto: solo `core/undo.py` apre `beginEditCommand`.
4. **Shortcut** — la guardia `objectForSequence` c'è; da verificare in GUI reale
   che Alt+Shift+L/P/R/C siano liberi.
5. **Processing** — provider isolato, nessuna collisione di id.
6. **Documentazione divergente** — `docs/CHECKLIST.md` è ferma alla 1.0.0 e
   dichiara ancora *"cad/tools/ La cartella esiste ma è vuota"*: falso da 1.1.0.

---

## Le due criticità da chiudere per prime

### 1. I map tool non fanno snap (correttezza, non estetica)

`base.py` righe 698 e 716 usano `event.mapPoint()`, che è il punto grezzo del
mouse. `QgsMapMouseEvent.snapPoint()` non viene mai chiamato e `core/snap.py`
non è importato da nessun modulo di produzione.

Conseguenza: **un vertice cliccato non aggancia il vertice esistente**. Su un
plugin che si dichiara CAD dimensionale è un difetto di correttezza, non di
comodità, e va prima delle nuove primitive.

### 2. `cad/modifiers.py` è codice morto per l'utente

438 righe testate (offset, raccordo, smusso, taglia, estendi, dividi, allinea)
raggiungibili da zero interfacce: né map tool, né algoritmo Processing, né
dock. Il motore c'è; manca la porta.

---

## F. Piano milestone 1.2.x

Una milestone per turno. `run_tests.py --all` verde prima della successiva.

| # | Contenuto | Note rispetto allo stato reale |
|---|---|---|
| **1.2.0** | Una icona + dock a tab (CAD/GRIGLIE/FORESTA/UAV/LAYER-EXPORT/IMPOSTAZIONI), package `settings/` con `QgsSettings`, riaggancio delle 4 action CAD dentro la dock, **wiring dello snapping esistente nei map tool**. Zero geometria nuova. | Lo snap entra qui perché è correttezza, e perché la dock deve esporne toggle/tolleranza/tipi |
| **1.2.1** | ~~PolylineTool~~ | **GIÀ VERDE** (1.1.0-c): 235 righe, P1–P8 passano. **Milestone saltata**, non rifatta |
| **1.2.2** | SquareTool + EllipseTool + RegularPolygonTool | `primitives.py` READ-ONLY; stesso schema di rectangle/circle |
| **1.2.3** | Modify su canvas: Move, Rotate, Resize/Scale, Offset, Mirror | apre la porta a `cad/modifiers.py`, oggi irraggiungibile |
| **1.2.4** | Tab GRIGLIE + FORESTA operative su poligono esistente (pick layer/feature, pick direzione a 2 click) | chiama `core/grid.py` e `forest/*` esistenti |
| **1.2.5** | Tab UAV: pick AOI, combo DEM/camera/drone, generate che chiama `survey`/`mission`, preview layer + profilo + KPI + validator in dock | nessun ricalcolo fotogrammetrico |
| **1.2.6** | Tab LAYER/EXPORT unificato con stato VERIFIED/PARTIAL/UNSUPPORTED per adapter; report HTML mostrato nella dock | `uav/export.py` READ-ONLY |
| **1.2.7** | Grips parametriche (opz.), simulatore (opz.), `.ts`, packaging, CHANGELOG, USER_GUIDE, **aggiornamento di CHECKLIST.md** e checklist GUI reale compilata | |

Fuori ambito 1.2: riscrivere GSD/drape/coverage/validator, dichiarare WPML
VERIFIED, database droni, login/cloud.

---

## Vincoli portati avanti

Golden intoccabili: `|ΔZ| = 0.0`, copertura 100 % con ≥ 3 foto/punto,
Mavic 3E @ 80 m (GSD 2.13280072 cm/px, W 112.611880 m, D_side 33.783564 m,
D_front 16.924329 m) a 1e-9, rettangolo 30×20@15° area 600.000000,
T7 (100 move → 0 geometrie, 0 refresh), T6 (EPSG:4326 non produce gradi).

READ-ONLY: `core/`, `uav/`, `forest/`, `cad/primitives|modifiers|parametric|
dynamic_input`, `cad/tools/{line,rectangle,circle,polyline}.py`, `profiles/`.
`cad/tools/base.py` solo con hook documentati.

API: forma **scoped** obbligatoria (`Qt.Key.Key_Escape`,
`Qt.DockWidgetArea.RightDockWidgetArea`, `QEvent.Type.MouseButtonDblClick`);
`Qgis.MapToolFlag` e `QgsProcessingAlgorithm.FlagAdvanced` non esistono.
Ogni API nuova va sondata su 3.40.15 e 4.0.0 prima dell'uso.
