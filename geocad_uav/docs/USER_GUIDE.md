# Guida utente

Quattro flussi di lavoro, dal più semplice al completo.

---

## A. Rettangolo dimensionale

**Obiettivo:** un rettangolo di 25.00 × 12.00 m ruotato di 15°, esatto.

1. Apri il pannello **GeoCad UAV** dalla toolbar.
2. Strumento **Rettangolo**, riferimento **Centro**.
3. Inserisci `Larghezza 25.00`, `Altezza 12.00`, `Azimut 15.00`.
4. Clicca sulla mappa per il centro, oppure digita `#500000,5000000`.

Il click fornisce il *punto di riferimento*, non la forma: i lati vengono
calcolati, non approssimati dai pixel. Area 300.000000 m², perimetro
74.000000 m, a float64.

L'azimut è la direzione dell'asse **altezza**, in gradi orari da Nord — la
stessa convenzione usata ovunque nel plugin, incluse strip di volo e file
d'impianto.

**Modificare dopo:** apri l'editor delle proprietà, cambia `width_m` da 25 a
30, applica. La geometria viene rigenerata dai parametri. Se hai spostato un
vertice a mano nel frattempo, il record risulta *broken* e il plugin chiede
conferma prima di sovrascrivere la tua modifica.

---

## B. Cerchio

Il cerchio si costruisce in cinque modi, tutti risolti algebricamente:

| Modalità | Ingressi |
|---|---|
| centro + raggio | centro, `radius_m` |
| centro + diametro | centro, `diameter_m` |
| centro + area | centro, `area_m2` → `r = √(A/π)` |
| centro + circonferenza | centro, `circumference_m` → `r = C/(2π)` |
| 3 punti | circocentro dei tre punti |
| 2 punti + raggio | due soluzioni speculari, `side` sceglie |

Il cerchio è memorizzato come poligono regolare a 72 lati (configurabile).
A 72 segmenti l'area è dello 0.127 % inferiore a `πr²`: dentro la tolleranza
di progetto dello 0.5 %, e ogni vertice è esattamente a `r` dal centro.

Tre punti allineati non definiscono un cerchio: il plugin lo dice, invece di
restituire un raggio enorme.

---

## C. Rimboschimento

**Obiettivo:** sesto 3 × 2 m orientato a 15°, margine 2 m dal bordo, solo su
pendenze sotto i 30° ed esposizioni da Sud.

1. Seleziona il poligono di progetto (**non serve ridisegnarlo**).
2. Toolbar → **Sesto d'impianto...**
3. Area: il tuo layer. DEM: il raster di quota.
4. `Distanza fra le piante 3.0`, `Distanza fra le file 2.0`.
5. `Orientamento file 15`, `Margine dal bordo 2.0`.
6. Sezione avanzata: `Pendenza massima 30`, `Esposizione da 135` `a 225`.
7. Esegui.

**Risultati.** Tre layer: piante, posizioni scartate, linee di file. Il log
riporta i KPI:

```
Superficie AOI: 20,000 m2 (2.000 ha)
Superficie utile (al netto del margine): 18,816 m2 (1.882 ha)
Sesto: 3 x 2 m
Piante effettive: 3,135
Piante teoriche (superficie / sesto): 3,136
Grado di riempimento: 100.0%
File: 71  |  lunghezza totale 9,192.0 m  |  media 129.5 m
Densita': 1,666.1 piante/ha (utile), 1,567.5 piante/ha (lorda)
```

La densità è riferita alla superficie **utile**, quella davvero piantata.
Rapportarla all'AOI lorda la sottostima dell'anello di margine, che su una
particella stretta è una frazione grande — il plugin riporta entrambe.

**Perché un versante è vuoto?** Guarda il layer delle posizioni scartate: ogni
punto porta il motivo (fuori area, dentro il margine, pendenza, quota,
esposizione, nessun dato di quota). Un'esposizione che attraversa il Nord si
scrive direttamente `315` → `45`.

---

## D. Missione UAV fotogrammetrica

**Obiettivo:** rilievo a 80 m di quota, GSD ≈ 2 cm/px, sovrapposizioni 80/70,
8 m/s, con inseguimento del terreno.

### D.1 Seleziona il poligono
Un layer poligonale esistente, in **CRS proiettato metrico** (UTM). Con un CRS
geografico l'algoritmo si ferma e ti dice quale zona UTM usare: in gradi le
distanze non hanno senso.

### D.2 Assegna il DEM/DTM
Preferisci un **DTM** (terreno nudo) e aggiungi la clearance vegetazione, o usa
un **DSM** e spunta la casella. Il report dichiara sempre quale hai usato.

> In area boscata o urbana, un DTM senza clearance produce un piano che vola
> più basso del previsto sopra le chiome. Il validatore lo segnala sempre.

### D.3 Profilo drone e camera
Scegli dalla libreria, oppure clona una voce di `profiles/*.json` e modifica.
Per lavoro metrico, sostituisci focale e dimensioni sensore con quelle del tuo
**certificato di calibrazione**: il report stampa sempre i valori usati e la
loro fonte.

### D.4 Quota o GSD
`Quota H_AGL = 80`, oppure passa a `GSD target` e inserisci `2.0` cm/px — il
plugin ricava l'altra grandezza.

### D.5 Sovrapposizioni e velocità
`Longitudinale 80`, `Laterale 70`, `Velocità 8`.

L'anteprima del pannello si aggiorna a ogni modifica e mostra il vincolo
determinante. Se la velocità richiesta viene tagliata, dice **da cosa**: mosso,
intervallo di scatto, rateo di salita o limite del drone.

### D.6 Terrain following
Lascia `Terrain following continuo (AGL costante)`. Le altre due modalità
esistono ma costano:

| Modalità | Quando |
|---|---|
| Terrain following continuo | **default**, sempre corretto |
| AMSL per singola strip | firmware che non accetta quote variabili in strip |
| AMSL unica | solo su terreno piatto — rifiutata se il dislivello supera il 10 % di `H_AGL`, con il numero in chiaro |

### D.7 Azimut
`Automatico` allinea le strip al lato maggiore: meno virate, meno tempo.

`Lungo le curve di livello` è la scelta giusta in pendio: ogni strip resta a
quota quasi costante, quindi l'AGL varia poco *dentro* la strip e il rateo di
salita non diventa mai il vincolo.

### D.8 Genera, valida, leggi
Esegui. Il log riporta la validazione, i KPI e le **assunzioni dichiarate**:

```
AOI 14.40 ha  |  13 strip  |  401 foto  |  450 waypoint
H_AGL 80 m  |  GSD 2.13 cm/px  |  V 8.0 m/s
Volo 7,051 m  |  21m 40s  |  6 batteria/e
Terreno 317 - 385 m (dislivello 68 m)  |  AGL 80 - 80 m
Copertura 100.00 % con almeno 3 foto per punto
```

`AGL 80 - 80 m` su 68 m di dislivello è la prova che il terrain following ha
funzionato: l'altezza sul terreno non si è mossa.

Nel report HTML, il **profilo altimetrico** mostra terreno e quota di volo
paralleli. Se le due curve divergono, il piano è sbagliato.

> Sei batterie per 21 minuti di volo non è un errore: la missione è divisa sul
> **limite di 99 waypoint** del controller, non sull'autonomia. Il validatore
> distingue i due casi.

### D.9 Esporta
GeoPackage con tutti i layer, HTML per il report, più il formato di volo:

* **Litchi CSV** se voli con Litchi.
* **QGC WPL 110** per Mission Planner / ArduPilot.
* **KMZ** per scambio e visualizzazione — non è una missione DJI nativa, ed è
  scritto sia nel file sia nel `LEGGIMI.txt` dentro l'archivio.

Il **WPML DJI nativo non è disponibile**: lo schema non è stato verificato, e
il plugin preferisce dirlo piuttosto che produrre un file plausibile e
sbagliato.

Prima di volare, apri comunque una missione esportata nell'app di volo e
controllala.

---

## Lettura del validatore

Gli **errori** bloccano l'esportazione. Gli **avvisi** no, ma vanno visti.

| Controllo | Errore quando |
|---|---|
| CRS di lavoro | è geografico |
| Quote assegnate | un waypoint ha quota nulla |
| Coordinate finite | c'è un NaN o un infinito |
| AGL minimo | sotto il nominale in modalità terrain following |
| Quota massima legale | oltre 120 m AGL (configurabile) |
| Copertura AOI | sotto il 100 % con almeno 3 foto |
| Aree vietate | un waypoint dentro un vincolo |

| Avviso tipico | Significato |
|---|---|
| Mosso | la velocità supera il limite di strisciamento |
| Intervallo di scatto | la camera non tiene il passo |
| Rateo di salita | la pendenza ha ridotto la velocità |
| Buchi nel DEM | quota resa conservativa, da verificare |
| DTM senza clearance | rischio reale sopra chiome ed edifici |
| Limite waypoint | una sotto-missione supera il controller |

## Unità e input dinamico

Lunghezze in `m`, `cm`, `mm`, `km`, `ft`, `in`; angoli in `deg`, `rad`, `gon`.
Lo stoccaggio interno è **sempre** in metri: un rettangolo inserito in piedi è
costruito da 7.62 m esatti, non da un 7.620 riarrotondato.

| Input | Significato |
|---|---|
| `25` | lunghezza nell'unità corrente |
| `25ft` | lunghezza con unità esplicita |
| `37d` | angolo / azimut |
| `@25<37` | polare: 25 m a 37° dal segmento precedente |
| `#100,200` | coordinate assolute nel CRS di lavoro |
| `@10,-5` | delta cartesiano dall'ultimo punto |

Il separatore decimale è il punto: la virgola separa le coordinate.
