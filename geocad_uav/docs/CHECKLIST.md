# Checklist di accettazione — compilata

Stato al 2026-09-08, versione 1.0.0.
Ogni voce rimanda al test che la verifica. Nove suite, `ALL SUITES PASSED` in
45 s.

    "C:\Program Files\QGIS 3.40.15\bin\python-qgis-ltr.bat" run_tests.py --all

---

## Criteri della specifica (§20)

| # | Criterio | Stato | Verifica |
|---|---|---|---|
| 1 | Si installa da ZIP e compare in toolbar/menu/Processing | ✅ | `zip_plugin.py` costruisce e **verifica** l'archivio (root `geocad_uav/`, file obbligatori presenti); `test_processing.py` conferma la registrazione dei tre algoritmi |
| 2 | Le primitive CAD rispettano i numeri a `geom_eps` | ✅ | `test_geometry.py`: quadrato lato 10 → area 100.000000, perimetro 40.000000; rettangolo 25×12 → area 300, lati esattamente 25/12/25/12 |
| 3 | Le rotazioni conservano forma e area | ✅ | `test_geometry.py`: rotazione a 45° conserva area e perimetro; quattro rotazioni da 90° sono l'identità a 1.8e-15 |
| 4 | Le griglie hanno il passo richiesto e clipano l'AOI | ✅ | `test_grid.py`: AOI 100×100 a passo 5 → **21×21 = 441**; `test_processing.py`: 440×360 a passo 20 → **437** (23×19) |
| 5 | Forest genera solo punti interni, KPI corretti | ✅ | `test_forest.py`: 2 ha, sesto 3×2, 15°, margine 2 m → 3 135 piante su 3 136 teoriche, tutte dentro l'area erosa, densità entro il 15 % del teorico, `row_id` monotono senza salti |
| 6 | **Ogni waypoint ha Z = DEM + H_AGL** (modo terrain) | ✅ | `test_mission.py`: `max \|Z_wp − (Z_DEM + H_AGL)\| = 0.0` su 450 waypoint con 68 m di dislivello; `test_processing.py` lo riverifica end-to-end |
| 7 | L'AOI è coperta al 100 % dai footprint | ✅ | `test_mission.py`: copertura **100.00 %**, minimo 3 foto per punto, misurata sulle impronte drappeggiate sul DEM |
| 8 | GSD effettivo entro +15 % del target | ✅ | `test_mission.py`: su AGL costante il GSD effettivo coincide col nominale a 1e-9; il validatore applica comunque la banda +15 % |
| 9 | `V_eff` rispetta mosso, trigger e climb | ✅ | `test_photogrammetry.py` (formule), `test_terrain_follow.py` (cap per segmento), `test_mission.py` (integrazione) |
| 10 | Nessun waypoint con AGL < minimo | ✅ | `test_mission.py`: `AGL 80 − 80 m`, spread 0.0; il validatore lo tratta come **errore bloccante** in modo terrain following |
| 11 | Profilo altimetrico coerente col raster | ✅ | `test_mission.py`: il profilo contiene più campioni dei waypoint e le curve restano parallele; reso in SVG inline nel report |
| 12 | Il validatore blocca gli errori ed elenca gli avvisi | ✅ | `test_mission.py`: CRS geografico → errore bloccante; 20+ controlli riportati con il valore misurato |
| 13 | Export verificati, nessuna compatibilità falsa | ✅ | `test_mission.py`: 9 formati scritti e **riletti** (intestazione Litchi confrontata colonna per colonna, `QGC WPL 110` con 12 campi e RTL finale, GeoJSON valido, KMZ con disclaimer). WPML DJI **rifiutato**, non simulato |
| 14 | Undo/redo QGIS funziona | ⚠️ parziale | `core/undo.py` implementa le transazioni (`edit_command`, inserimenti a lotti, rollback su eccezione) e `cad/parametric.update_feature` le usa. **Non coperto da test automatico**: servirebbe un layer editabile in un progetto reale |
| 15 | Un CRS geografico non produce distanze false | ✅ | `test_cad.py`: 1000 m in UTM misurano 1000.00 m; gli stessi in EPSG:4326 danno < 0.02 (gradi); round trip conserva 1000 m. Gli algoritmi si fermano su CRS geografico |
| 16 | Test automatici tutti verdi | ✅ | 9 suite, oltre 300 asserzioni, `ALL SUITES PASSED` |
| 17 | Nessun TODO su funzioni dichiarate complete | ✅ | Nessuna occorrenza di `TODO`/`FIXME`/`NotImplementedError` nel package. Le quattro `pass` presenti sono tutte in blocchi `except` di pulizia in `unload()`/`teardown()`, dove il fallimento di una disconnessione non deve impedire le successive |
| 18 | QGIS nativo non è rotto | ⚠️ parziale | `unload()` è lo specchio esatto di `initGui()` (azioni, toolbar, dock, provider, segnali); `snap.scoped_snapping` ripristina la configurazione di snapping dell'utente. **Non verificato in una sessione GUI reale** |

---

## Principi non negoziabili (§0)

| # | Principio | Stato | Dove |
|---|---|---|---|
| P0 | Geometria calcolata, mai "a occhio" | ✅ | `core/geometry_engine.py`, tutto risolto algebricamente |
| P1 | Nessuna metrica in CRS geografico | ✅ | `core/crs.py` + guardia in tutti e tre gli algoritmi |
| P2 | Terrain following obbligatorio | ✅ | `uav/terrain_follow.py`, default `AltitudeMode.TERRAIN` |
| P3 | Si lavora dentro un poligono esistente | ✅ | AOI come `QgsProcessingParameterFeatureSource` ovunque |
| P4 | Ottimizzazione da quota/GSD, camera, velocità, overlap, cinematica, autonomia, DEM | ✅ | `uav/mission.py` |
| P5 | DEM vs DTM distinti | ✅ | flag `DEM_IS_DSM`, `vegetation_clearance_m`, avviso del validatore |
| P6 | Datum verticale dichiarato | ✅ | `VerticalDatum` in report ed export; mai convertito in silenzio |
| P7 | Compatibilità solo se documentata | ✅ | `export.FORMATS` con `verified` e `schema_source`; WPML in `NOT_IMPLEMENTED` |
| P8 | Undo su ogni mutazione, conferma sulle operazioni massive | ⚠️ parziale | `core/undo.py` completo, incluso `needs_confirmation`; non testato in GUI |
| P9 | Solo API QGIS/Qt, niente pip extra | ✅ | Import esterni: solo `numpy` e `osgeo`, entrambi inclusi in QGIS |
| P10 | Nessun codice arbitrario, nessun overwrite silenzioso | ✅ | Ogni writer rifiuta di sovrascrivere senza `overwrite=True` (`test_mission.py`) |

---

## Cosa NON è incluso, e perché

Dichiarato esplicitamente invece di essere lasciato intendere.

### 1. WPML DJI nativo — non implementato di proposito
Lo schema (`wpmz/template.kml` + `wpmz/waylines.wpml`) non è stato verificato
contro la documentazione ufficiale DJI in questa build. Per la regola P7 il
plugin **rifiuta** quel formato con la spiegazione, invece di scrivere un file
plausibile e mai validato. Alternative reali: Litchi CSV, QGC WPL 110, KMZ.

### 2. Strumenti CAD interattivi su canvas (`cad/tools/`)
La cartella esiste ma è vuota. Sono implementati e testati:
* il **motore** geometrico completo (primitive, vincoli, modificatori);
* il **parser** dell'input dinamico (`@25<37`, `#x,y`, `@dx,dy`);
* il **wrapper** dello snapping (`core/snap.py`) con snap di costruzione
  aggiuntivi (griglia, perpendicolare, tangente);
* il **record parametrico** con rilevamento delle modifiche manuali.

Manca lo strato `QgsMapTool` che li lega al canvas con rubber band e HUD: le
primitive oggi si creano da Processing e dal pannello, non tracciandole col
mouse. È il pezzo più corposo rimasto e richiede verifica interattiva, non
automatizzabile con la suite headless.

### 3. Verifica in sessione GUI
Tutto è verificato headless. Il caricamento del plugin nella GUI di QGIS, il
comportamento del dock e l'undo su layer reali non sono coperti dai test
automatici e vanno provati a mano dopo l'installazione.

### 4. Traduzioni `.ts` compilate
Le stringhe utente sono in italiano e passano da `QCoreApplication.translate`
con contesto `GeoCadUav`, quindi sono estraibili con `pylupdate`. I file `.ts`
e `.qm` non sono generati.

---

## Numeri di riferimento verificati

DJI Mavic 3E, f = 12.29 mm, sensore 17.3 × 13.0 mm, 5280 × 3956 px:

| Quota | GSD | W | L | D_side (70 %) | D_front (80 %) |
|---|---|---|---|---|---|
| 80 m | 2.13280072 cm/px | 112.611880 m | 84.621644 m | 33.783564 m | 16.924329 m |
| 100 m | 2.66600090 cm/px | 140.764850 m | 105.777050 m | 42.229455 m | 21.155411 m |

Verificati a 1e-9 in `test_photogrammetry.py` e `test_mission.py`.

Controprova indipendente: l'impronta calcolata per **ray casting** sul DEM
riproduce quella in forma chiusa `W = (Sw/f)·H` a otto cifre significative su
terreno piano — due percorsi di codice indipendenti che concordano.
