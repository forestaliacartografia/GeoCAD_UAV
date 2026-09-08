# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/it/1.1.0/).

## [1.0.0] - 2026-09-08

Primo rilascio.

### CAD
- Primitive risolte algebricamente: punto, linea, polilinea, rettangolo
  (centro / angolo / angoli opposti / base+altezza), quadrato (lato, diagonale,
  area, perimetro), cerchio (centro+raggio, diametro, area, circonferenza,
  3 punti, 2 punti+raggio), poligono regolare (raggio, apotema, lato, area),
  ellisse.
- Input dinamico: `25`, `25ft`, `37d`, `@25<37`, `#x,y`, `@dx,dy`.
- Modificatori: offset (buffer e offsetCurve distinti), raccordo, smusso,
  taglia, estendi, esplodi, unisci, dividi, allinea.
- Serie rettangolari e polari; roto-traslazione, scala, specchiatura.
- Record parametrico con rilevamento delle modifiche manuali.

### Griglie e impianti
- Reticoli rettangolari, quadrati, esagonali e a quinconce, orientabili.
- Ancoraggio stabile del reticolo e rinumerazione dopo il ritaglio.
- Sesti d'impianto con erosione del margine.
- Filtri topografici su pendenza, quota ed esposizione (Horn 3×3), con
  gestione dei settori che attraversano il Nord.
- Posizioni scartate conservate con il motivo.

### UAV
- Terrain following continuo, quota AMSL per strip, quota AMSL unica
  (ammessa solo sotto il 10 % di dislivello).
- Modello fotogrammetrico completo: GSD, impronta, `D_side`, `D_front`.
- Budget di velocità: mosso, intervallo di scatto, rateo di salita, limiti del
  drone e normativi, con indicazione del vincolo determinante.
- Densificazione dei waypoint per tolleranza verticale.
- Strip boustrophedon, interlacciamento per ala fissa, doppia griglia,
  corridoi su offset curve.
- Impronte a terra proiettate sul DEM per ray casting.
- Divisione automatica in sotto-missioni su autonomia e limite waypoint.
- Validatore con errori bloccanti e avvisi.
- Report HTML con profilo altimetrico in SVG inline.

### Esportazione
- GeoPackage, GeoJSON, KML, KMZ, GPX, CSV waypoint, CSV centri di presa,
  Litchi Mission Hub CSV, Mission Planner `QGC WPL 110`.
- **WPML DJI nativo non implementato**: lo schema non è stato verificato, e il
  plugin lo dichiara invece di simularlo.

### Infrastruttura
- Tre algoritmi Processing.
- Pannello con anteprima live dei valori derivati.
- Nove suite di test, oltre 300 asserzioni numeriche.
- Script di pacchettizzazione con verifica dell'archivio.

### Note di compatibilità
- `QgsField` via `QMetaType` con ripiego su `QVariant` (3.34 → 4.0).
- Flag "avanzato" sul parametro, non sull'algoritmo.
- Nessun import diretto da `PyQt5`: sempre `qgis.PyQt`.
