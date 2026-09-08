# GeoCad UAV Toolkit

Plugin QGIS per geometria CAD dimensionale, griglie parametriche, progettazione
di impianti forestali e pianificazione di voli UAV fotogrammetrici con
**inseguimento del terreno obbligatorio**.

Target: QGIS 3.34 LTR – 3.40+ (Qt5 e Qt6 tramite `qgis.PyQt`).
Dipendenze: **nessuna oltre a QGIS** (Qt, GDAL e numpy sono già inclusi).

---

## Perché esiste il terrain following

È la ragione per cui questo plugin non è una griglia di linee.

Volando a quota AMSL costante su un versante, l'altezza reale sopra il terreno
cambia lungo la strip. Poiché

    GSD = H_AGL · pitch / f

il GSD **peggiora dove il terreno scende** e migliora dove sale: la stessa
ortofoto finisce con risoluzioni diverse. Peggio, l'impronta a terra cambia
con la quota, quindi

    D_side = W · (1 − sidelap)     con  W = (Sw / f) · H_AGL

resta fissa in volo mentre `W` varia: dove il terreno sale, `W` si riduce e la
**sovrapposizione laterale collassa**, fino a lasciare buchi fra le strip.
Un dislivello del 30 % della quota di volo basta a portare la sovrapposizione
nominale del 70 % sotto il 55 % reale.

Il terrain following elimina entrambi i problemi mantenendo `H_AGL` costante:

    Z_waypoint(x, y) = Z_raster(x, y) + H_AGL + margine_sicurezza

Nel report di missione il profilo altimetrico mostra le due curve — terreno e
quota di volo — che restano parallele. Se non lo sono, il piano è sbagliato.

---

## Cosa fa

### CAD
Primitive **calcolate**, non disegnate a occhio: punto, linea, polilinea,
rettangolo (4 modalità di costruzione), quadrato (da lato, diagonale, area o
perimetro), cerchio (centro+raggio, 3 punti, 2 punti+raggio, da area o
circonferenza), poligono regolare (da raggio, apotema, lato o area), ellisse.

Input dinamico: `25`, `25ft`, `37d`, `@25<37`, `#100,200`, `@10,-5`.

Modificatori: offset (buffer o offsetCurve, distinti), raccordo, smusso,
taglia, estendi, esplodi, unisci, dividi, allinea, serie rettangolare e polare.

Ogni forma conserva un **record parametrico**: si modifica "larghezza 25 → 30"
e la geometria viene rigenerata. Se la geometria è stata editata a mano fuori
dal plugin, il record viene marcato *broken* e non viene riapplicato senza
conferma esplicita.

### Grid Designer
Griglie rettangolari, quadrate, esagonali e a quinconce, orientabili a
qualunque azimut, ritagliate sull'area di progetto. La griglia è **ancorata**:
allargare l'AOI non sposta i punti già calcolati. Dopo il ritaglio, file e
punti sono rinumerati consecutivamente.

### Forest Planting Designer
Sesti d'impianto dentro un poligono esistente, eroso del margine dal bordo.
Filtri topografici **reali** su pendenza, quota ed esposizione, ricavati dal DEM
con l'operatore di Horn 3×3 (lo stesso di `gdaldem`). Un intervallo di
esposizione che attraversa il Nord (es. 315–45) è gestito correttamente.

Le posizioni scartate non vengono buttate: finiscono in un layer separato con
il **motivo** dello scarto, così si capisce perché un versante è rimasto vuoto.

### UAV Flight Planner
Pianificazione fotogrammetrica completa: GSD, impronta a terra, `D_side`,
`D_front`, limiti di velocità, ottimizzazione delle strip, divisione per
batteria, validatore e report.

---

## Il modello fotogrammetrico

Tutte le grandezze in SI; `H` in metri, `GSD` in m/px, focale e sensore in mm.

    pitch    = Sw / Px                      [mm/px]
    GSD      = H_AGL · pitch / f            [m/px]
    H_AGL    = GSD_target · f / pitch       [m]

    W  = (Sw / f) · H_AGL                   impronta trasversale
    L  = (Sh / f) · H_AGL                   impronta longitudinale

    D_side   = W · (1 − sidelap)            interasse fra strip
    D_front  = L · (1 − frontlap)           base di presa

**Velocità effettiva** = il minimo di tutti i vincoli, e il plugin dice sempre
quale ha tagliato:

    V_blur    = GSD · blur_px_max / t_shutter        (default 1.5 px)
    V_trigger = D_front / t_intervallo_minimo
    V_climb   = rateo · d_orizzontale / |Δz|         (per segmento)
    V_eff     = min(V_richiesta, V_blur, V_trigger, V_climb, V_drone, V_norma)

Dove il terreno è più ripido di quanto il drone possa salire, **la velocità
viene ridotta, mai il rilievo tagliato**. I tratti che resterebbero infattibili
anche a velocità minima sono segnalati con il suggerimento operativo (alzare
`H_AGL`, o orientare le strip lungo le curve di livello).

### Valori di riferimento verificati

DJI Mavic 3E (f = 12.29 mm, sensore 17.3 × 13.0 mm, 5280 × 3956 px):

| Quota | GSD | W | L | D_side (70 %) | D_front (80 %) |
|---|---|---|---|---|---|
| 80 m | 2.1328 cm/px | 112.612 m | 84.622 m | 33.784 m | 16.924 m |
| 100 m | 2.6660 cm/px | 140.765 m | 105.777 m | 42.229 m | 21.155 m |

Questi sono i *golden values* verificati dai test a 1e-9.

---

## Copertura: verificata sul DEM, non sul piano

Le impronte a terra sono proiettate sul modello di elevazione per **ray
casting**: da ogni centro di presa si lanciano i raggi del bordo immagine e si
cerca la prima intersezione con il terreno.

Su un pendio la vera impronta è un trapezio, molto più grande a valle del
rettangolo che si otterrebbe su un piano orizzontale. Usare il rettangolo
piano **sovrastima la copertura proprio sul terreno dove il terrain following
serve di più**. La prima intersezione (non l'ultima) è quella corretta: è ciò
che fa sì che un crinale occluda la valle dietro, su un DSM.

La validazione campiona l'AOI su griglia regolare e conta quante impronte
contengono ogni punto: il criterio di accettazione è **100 % dell'AOI in almeno
3 foto**.

---

## Formati di esportazione

**Regola P7: un formato è dichiarato compatibile solo se lo schema è
documentato e l'output è stato verificato.**

| Formato | Stato | Schema |
|---|---|---|
| GeoPackage | ✅ dichiarato | OGC GeoPackage 1.3 |
| GeoJSON | ✅ dichiarato | RFC 7946 |
| KML | ✅ dichiarato | OGC KML 2.2 |
| KMZ waypoint | ✅ dichiarato | KML 2.2 compresso |
| GPX | ✅ dichiarato | Topografix GPX 1.1 |
| CSV waypoint / centri di presa | ✅ dichiarato | intestazione documentata nel file |
| Litchi Mission Hub CSV | ✅ dichiarato | intestazione CSV di Mission Hub |
| Mission Planner / ArduPilot | ✅ dichiarato | MAVLink `QGC WPL 110` |
| **DJI WPML nativo** | ❌ **non implementato** | schema non verificato |

Il WPML DJI **non** è implementato di proposito. Lo schema
(`wpmz/template.kml` + `wpmz/waylines.wpml`) non è stato verificato contro la
documentazione ufficiale DJI in questa build, e scrivere un file che *sembra*
una missione DJI senza averne validato lo schema è esattamente il rischio che
la regola P7 esiste per evitare. Chiedendo quel formato si ottiene un errore
che spiega il motivo, non un file silenziosamente sbagliato.

Alternative reali: **Litchi CSV** se voli con Litchi, **QGC WPL 110** per
ArduPilot, **KMZ** per lo scambio e la visualizzazione.

---

## Sicurezza numerica

* **Mai metrica in CRS geografico.** Con un CRS in gradi l'algoritmo si ferma e
  propone la zona UTM corretta. Un test verifica che 1000 m in UTM misurino
  1000 m e che gli stessi 1000 m in EPSG:4326 **non** diventino 0.009 di
  qualcosa.
* **Mai estrapolare il DEM.** Fuori extent o su no-data il campionamento
  restituisce NaN, e la quota comandata attraverso un buco viene resa
  conservativa e **segnalata**, non inventata.
* **Campionamento bilineare.** `QgsRasterLayer.sample()` è nearest-neighbour e
  produrrebbe un profilo a gradini alla dimensione della cella DEM; il plugin
  usa un campionamento bilineare proprio, con propagazione del no-data ai
  quattro vicini.
* **Datum verticale dichiarato, mai convertito in silenzio.** Il report indica
  sempre quale datum contiene il DEM e quale ondulazione del geoide è stata
  applicata (default 0.00 m, esplicitato).

---

## Documentazione

| File | Contenuto |
|---|---|
| `INSTALL.md` | installazione da ZIP e da sorgente |
| `QUICKSTART.md` | prima missione in cinque minuti |
| `USER_GUIDE.md` | i quattro flussi di lavoro A–D |
| `ARCHITECTURE.md` | moduli, contratti, scelte progettuali |
| `API.md` | API pubblica per script e modelli |
| `CHECKLIST.md` | checklist di accettazione compilata |
| `CHANGELOG.md` | storico delle versioni |

## Test

    python run_tests.py                                        # suite pure
    "C:\Program Files\QGIS 3.40.15\bin\python-qgis-ltr.bat" run_tests.py --all

Nove suite, oltre 300 asserzioni numeriche. I moduli di calcolo non importano
QGIS, quindi la matematica è verificabile senza un runtime QGIS.
