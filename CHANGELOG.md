# GeoCad UAV Toolkit - cronologia delle versioni

Questo file raccoglie la storia delle release. Non fa parte del
pacchetto distribuito e non compare nel Gestore dei plugin di QGIS:
la scheda del plugin descrive che cosa il plugin fa, non che cosa ha
fatto.

## 2.2.4 -- enum con lo scope, come PyQt6 li vuole

Il controllo Qt6 del repository ufficiale ha respinto la 2.2.3 con 131
segnalazioni, tutte della stessa forma: un membro di enum scritto sulla classe
che lo contiene invece che sul suo enum. PyQt5 accetta entrambe le grafie,
PyQt6 tiene solo la seconda come nome vero -- la prima resta un alias che QGIS
riaggiunge. Per questo il plugin girava senza un errore sulla LTR e veniva
comunque rifiutato in fase di caricamento.

- La tabella delle 33 grafie non e' stata dedotta a memoria: ogni nome e'
  stato interrogato sulle due QGIS installate e accettato solo dove risolve su
  entrambe con lo stesso valore numerico. Nessuna API inventata.
- 141 righe riscritte in 28 file. Il conteggio per file coincide con quello
  del repository (131 nei moduli distribuiti); le tre eccedenze erano prosa
  nei docstring, che il controllo giustamente ignora.
- I tre shim di compatibilita' (`rubber_band_geometry_type`, `flags()`,
  `mark_advanced`) interrogavano un nome e ne restituivano un altro dopo la
  riscrittura: ora la sonda e' sul nome che viene effettivamente usato, quindi
  il ramo di riserva scatta quando serve e non per caso.
- `qgisMinimumVersion` resta 3.34, ma per una ragione verificata e non per
  inerzia: in QGIS 3.34 `Qgis::GeometryType` e' dichiarato con
  `SIP_MONKEYPATCH_SCOPEENUM_UNNEST`, e il codice generato che 3.34 distribuisce
  assegna esplicitamente su `QgsWkbTypes.GeometryType.PointGeometry`. La grafia
  con lo scope e' quella nativa anche li'.
- Nuova suite `test_qt6_enums.py`: verifica che ogni nome risolva sulla QGIS in
  esecuzione col valore atteso, che coincida con l'alias che ha sostituito
  (quindi una riscrittura di grafia, non di comportamento), che nessun modulo
  scriva piu' la forma senza scope, e -- su build Qt6 -- interroga l'API su
  ogni `Classe.Membro` dei sorgenti, cosi' prende anche gli enum che nessuno
  ha messo in tabella. Su PyQt5 quella parte non puo' rispondere e lo dichiara
  invece di fingere un pass.
- `zip_plugin.py` rifiuta di impacchettare un sorgente che contenga ancora una
  forma senza scope, indicando file, riga e grafia richiesta. Provato in
  entrambe le direzioni. Tabella unica, letta dal packager e dal test.

## 2.2.3 -- LICENSE nel pacchetto

L'upload veniva rifiutato con: "Cannot find LICENSE in the plugin package.
This file is required".

- `geocad_uav/LICENSE`: il testo della GNU General Public License versione 2,
  verbatim, preso da gnu.org e verificato (17.984 byte, sha256
  edaef632...233f6) invece che riscritto a mano -- un documento legale non si
  parafrasa. Lo stesso file alla radice del repository, dove GitHub lo legge.
- La scelta della licenza non e' libera: il plugin usa PyQt e le API QGIS,
  entrambe GPL, quindi una licenza GPL-compatibile e' una condizione, non una
  preferenza. GPL v2 o successiva e' quella che il QGIS Plugin Builder genera
  ed e' lo standard di fatto dei plugin QGIS.
- L'avviso che la GPL chiede -- titolare, versione della licenza, assenza di
  garanzia -- e' in testa a `geocad_uav/__init__.py`, con `__license__` e
  `__copyright__` dichiarati.
- Il pacchettizzatore ha `LICENSE` fra i file obbligatori e ne guarda il
  contenuto: un file vuoto o troncato passa un controllo di esistenza e fa
  fallire l'upload. Provato in entrambe le direzioni.
- README: sezione sulla licenza, con il perche' del vincolo GPL.

## 2.2.2 -- Pubblicazione sul repository ufficiale QGIS

L'upload veniva rifiutato con: "Please provide valid url link for the
following key(s) in the metadata source: tracker, repository, homepage".
I tre campi erano vuoti di proposito -- non esisteva un repository pubblico,
e un campo vuoto lo dice onestamente dove un placeholder mente. Ora esiste.

- `repository`, `homepage` e `tracker` puntano a
  https://github.com/forestaliacartografia/GeoCAD_UAV (verificati: 200).
- `description` e `about` passano all'inglese, che e' quello che le linee
  guida del repository chiedono; l'italiano non si perde, diventa
  `description[it]` e `about[it]`, il meccanismo di localizzazione che QGIS
  usa gia': un QGIS italiano mostra l'italiano, la pagina del repository e
  tutti gli altri mostrano l'inglese.
- README.md in inglese alla radice del repository: e' la pagina che il link
  `homepage` apre.
- Il pacchettizzatore applica le stesse regole dell'upload: un pacchetto con
  uno dei tre link vuoto, con un placeholder, o con la descrizione rimasta
  in italiano non si costruisce piu'. La verifica avviene su questa macchina
  invece che dopo una sottomissione.

## 2.2.1 -- Il DEM si campiona nel CRS che QGIS usa

**Il difetto.** `TerrainModel.from_layer` passava il percorso del raster a
`gdal.Warp` e lasciava che GDAL leggesse il CRS sorgente dal file. Tutto il
resto di QGIS -- il disegno, l'extent del layer, Identifica, e quindi
l'operatore e l'area che ha disegnato sopra quel raster -- passa da
`QgsRasterLayer.crs()`. I due coincidono solo se il file porta un `.prj`
corretto.

Un DEM con proiezione mancante o sbagliata, corretta dall'operatore nelle
proprieta' del layer, e' il caso ordinario con i dati altimetrici regionali
italiani. Riprodotto: file scritto EPSG:32632, layer impostato a EPSG:32633,
AOI disegnata dove QGIS disegna il raster. L'extent QGIS e l'AOI coincidono;
il warp ha preso il 32632 del file, ha posato la finestra circa 400 km piu'
a ovest e ha restituito una griglia no-data al 100 percento. Ogni campione
del profilo era NaN e `fill_profile_gaps` riportava, correttamente per
quello che vedeva e falsamente sul mondo, che l'intera rotta era fuori dal
modello altimetrico.

**La correzione.** `srcSRS` e' il CRS del layer. Intorno:

- raster, CRS, dimensioni ed extent verificati prima di qualunque warp, e
  ogni rifiuto dice quale dei quattro non andava;
- la finestra richiesta e l'extent del DEM confrontati **nello stesso CRS**
  -- l'extent del layer trasformato nel CRS di lavoro -- quindi "il DEM non
  copre quest'area" si risponde prima del warp, con entrambi i rettangoli
  nel messaggio;
- una finestra che si riproietta tutta no-data viene rifiutata li', con la
  diagnostica, invece di riaffiorare quattro livelli piu' tardi come una
  frase sui CRS;
- un file la cui proiezione contraddice quella del layer produce un avviso:
  il layer vince, come ovunque in QGIS, ma chi sa quale sia giusta e'
  l'operatore.

**Diagnostica.** `core.z.dem_diagnostics` e `describe_dem_diagnostics`
mettono in fila CRS del layer, CRS del file, CRS di lavoro, dimensioni,
risoluzione, extent del DEM, extent nel CRS di lavoro, finestra richiesta e
sovrapposizione; finiscono nel log di QGIS a ogni generazione e dentro il
messaggio d'errore. `TerrainModel.sample_report` conta separatamente validi,
no-data e fuori griglia -- due problemi diversi con due risposte diverse --
e `fill_profile_gaps`, ricevuto il terreno, dice quale dei due e'.

Il pannello mostra il messaggio com'e', invece di rinominarlo "DEM non
leggibile" e buttare via i numeri.

**Attribuzione**: nelle tre sedi in cui il plugin dichiara l'autore --
`about`, `author=` e l'etichetta nella scheda Impostazioni -- resta
"Cap. Niccolo' Marco Mancini - RGPBIO".

## 2.2.0 -- Genera rotta dalla GUI, simulatore di missione, catasto a riquadri

**Genera rotta, da ogni step del volo.** Il comando c'era e funzionava, ma
stava sulla pagina dello step 8: chi aveva appena finito di impostare le
sovrapposizioni allo step 6 non aveva dove premerlo. Ora e' una barra sotto
le pagine del volo, visibile in tutti e dodici gli step e in nessuno dei
sedici del rimboschimento, con Genera rotta, Anteprima e Crea layer
missione, e una riga che dice perche' il primo e' spento quando lo e'. I
pulsanti sono quelli del pianificatore, riparentati: un secondo "Genera
rotta" sarebbe un secondo comando che puo' dissentire dal primo.

**Il player diventa un simulatore.** Velocita' da 0.25x a 4x (l'orologio e'
moltiplicato, la missione no); Inizio, waypoint indietro, Play, Pausa,
waypoint avanti, Fine; la traccia gia' volata dietro al drone; copertura che
cresce scatto per scatto, come unione delle impronte gia' acquisite
ritagliata sull'area di missione; strisciata corrente e percentuale di
missione nella telemetria, che erano nei waypoint e non venivano lette.
Misurato: 14,2 -> 30,8 -> 63,9 -> 96,9 -> 100,0 % lungo il volo, e coperto
piu' scoperto fa esattamente l'area di missione.

**Profilo altimetrico** con AGL e AMSL tenuti distinti, dislivello,
escursione di AGL e conteggio dei punti senza DEM -- contati sulle righe del
profilo, mai interpolati.

**Riserva di batteria configurabile**, e onorata dove conta: non solo
nell'avviso finale ma nel budget che divide la missione in tratte.
`validator.battery_plan` mette in un posto solo autonomia nominale, riserva,
utile per batteria, volo per batteria, margine, batterie necessarie e tempo
operativo coi cambi.

**Catasto a riquadri.** Un WFS che raggiunge il suo `count` si ferma li' e
la risposta sembra completa. `fetch_parcels` taglia il riquadro, tratta una
risposta esattamente al tetto del provider come il troncamento che e' e la
divide in quattro, ritenta una volta i riquadri che falliscono, unisce e
deduplica sull'identificativo catastale. Se non risponde nessun riquadro
resta un errore, non un risultato vuoto. Il primo taglio e' configurabile
(`cadastre/tile_span_deg`); la soglia di suddivisione viene dal tetto del
provider, non da un numero inventato.

**Quattro grandezze, quattro significati**: superficie catastale, superficie
interessata, percentuale sulla particella e percentuale sul progetto, tenute
separate nel modello, nella GUI e nell'export.

**Comune -> Foglio -> Particella** navigabile nel pannello CAD, con le
superfici e le percentuali a ogni livello e la selezione riflessa sulla
mappa; ed "Esporta", che scrive un CSV con ogni riga che il modello tiene,
geometrie comprese. La cella compatta con "(+N)" resta un modo di mostrare
il risultato e non e' mai il risultato.

**Metadati**: via i tre URL `example.invalid`, campi lasciati vuoti; il
pacchettizzatore rifiuta una build che rimetta un placeholder o una
cronologia nella descrizione. La chiave `changelog=` torna, vuota: QGIS la
legge e senza di lei registrava la mancanza in `error_details`.

Corretti mentre lo verificavo: `split_bbox` tagliava una riga di troppo
(`ceil(0.10/0.05)` in virgola mobile e' 3), cioe' il 50 percento di
richieste in piu'; `tick` faceva avanzare l'orologio anche a timer fermo.

## 2.1.0 -- Catasto CAD multicomune, rotta UAV e descrizione stabile

- Il CAD interroga il catasto sulla **geometria**, non sul centroide. Era
  `geometry.centroid()` passato a una query puntuale: un'unica particella
  per costruzione, qualunque cosa la forma coprisse davvero. Ora chiama
  `cadastre.area_task`, lo stesso task, la stessa chiamata WFS, lo stesso
  parsing GML, la stessa intersezione in CRS metrico e lo stesso registro
  Belfiore del modulo Rimboschimento. Misurato sul servizio vero: un
  quadrato di 6 m sopra Perugia passa da 1 particella a 4.
- Le cinque colonne della tabella CAD restano cinque. Quando la forma tocca
  piu' Comuni, `cadastre.cad_columns` le riempie con tutti i valori --
  foglio qualificato dal Belfiore solo se i Comuni sono piu' d'uno,
  particella qualificata dal foglio solo se i fogli sono piu' d'uno -- e per
  una particella sola scrive esattamente quello che scriveva prima.
- Pannello CAD: comando "Interroga catasto" con il suo stato di attesa,
  riepilogo per Comune, dettaglio per particella con superficie catastale,
  superficie interessata e percentuale, particelle sulla mappa, selezione
  per Comune e finestra di dettaglio.
- UAV: anteprima della missione sulla mappa (strisciate, waypoint, punti di
  scatto, impronte) con gli stessi layer che finiscono nell'export;
  anteprima a pixel con waypoint e punti di scatto; trasporto completo del
  simulatore (Inizio, Fine, un waypoint avanti e indietro); velocita', prua,
  distanza e tempo residui e waypoint corrente nel riepilogo; copertura,
  sovrapposizioni effettive e GSD min/max nel riepilogo del pianificatore.
- Le impronte a terra si calcolano a richiesta: premere "Mostra le impronte"
  su una rotta pianificata senza il controllo di copertura le proietta
  invece di spiegare quale casella spuntare.
- Corretto: aggiungere un layer poligonale al progetto cancellava l'area
  della missione. `QgsMapLayerComboBox` si ripopola ed emette `layerChanged`
  senza selezione; l'estensione che non viene da quel combo non e' del combo
  da buttare.
- Corretto: uno strumento CAD non piu' legato al pannello smette di
  pubblicare. Un task catastale ancora in volo sovrascriveva il pannello con
  una forma che l'operatore aveva gia' lasciato.
- La descrizione del plugin non contiene piu' la cronologia delle versioni:
  56 KB di changelog erano dentro `metadata.txt` e il Gestore dei plugin li
  mostrava come scheda del plugin. La storia e' qui; il pacchettizzatore
  rifiuta una build che la rimetta in `metadata.txt` o che narri versioni in
  `description`/`about`.

## Prima della 2.1.0

Le note che seguono erano nella chiave `changelog=` di
`metadata.txt`, dove QGIS le mostrava come scheda del plugin.

```
2.0.1 -- Release correttiva
- metadata.txt riscritto: il file precedente non era leggibile da
configparser (dodici chiavi changelog duplicate) e QGIS rifiutava lo
ZIP con "Errore durante la lettura dei metadati: general". Una sola
chiave changelog, nessun segno di percentuale isolato, nessun BOM.
qgisMaximumVersion portato a 4.99: con 3.99 QGIS 4.0.0 escludeva il
plugin anche a metadati corretti. Il pacchettizzatore ora rilegge i
metadati dall'interno dello ZIP con lo stesso parser di QGIS e
rifiuta di pubblicare un pacchetto che QGIS non saprebbe leggere.
- Descrizione tecnica e istituzionale, autore e icona ufficiale
visibili nel Gestore dei plugin.
- Separazione completa fra i due moduli: Rimboschimento in sedici
step e Volo UAV in dodici, ciascuno col proprio elenco. Il
selettore mostra un modulo alla volta, mai due elenchi insieme, e
il passaggio da un modulo all'altro e' esplicito.
- Catasto multicomune: l'intersezione conserva Comune, foglio,
particella e superfici di ogni Comune interessato. Riepilogo per
Comune accanto al dettaglio per particella, con superficie
catastale, superficie interessata e percentuale; scegliendo un
Comune si evidenziano sulla mappa tutte le sue particelle, e
"Visualizza particelle" le disegna tutte. Il riepilogo per Comune
entra anche nella relazione.

2.0.0 -- Gold Release
- Due moduli separati, ciascuno col proprio percorso di step nel dock
sinistro: Rimboschimento (14 step) e Volo UAV (6). Nessuna
sovrapposizione, nessuna schermata duplicata altrove.
- Serie DJI Mini in libreria per impostazione predefinita, col peso
al decollo e la classe sotto i 250 g.
- Tabella attributi CAD di cinque colonne: Area, Perimetro, Comune,
Foglio, Particella. Nient'altro, niente di nascosto.
- Export DJI WPML nativo, scritto sullo schema pubblicato da DJI.
- Cartografia su A4, A3, A2, A1 e A0, in verticale e in orizzontale:
foglio e riquadro mappa si ridimensionano insieme, verificato
formato per formato.


1.39.0
- Export DJI WPML: un .kmz con wpmz/template.kml e wpmz/waylines.wpml,
nel namespace ufficiale http://www.dji.com/wpmz/1.0.2. Rifiutato
dalla prima versione perche' lo schema non era stato letto; ora lo
e' stato, dalla documentazione DJI Cloud API (Template.kml,
Waylines.wpml, Common Elements, Product Supported), e ogni elemento
che quelle tabelle danno per obbligatorio viene scritto. Le quote
sono quelle del terrain following, e ogni punto di scatto porta il
gruppo di azioni gimbalRotate + takePhoto.
- Restano due rifiuti, ed entrambi vengono dalla documentazione: un
velivolo il cui profilo non dichiara gli enumerativi DJI (le serie
Mini, Air e Phantom non volano WPML, DJI non le elenca), e la quota
ortometrica, che wpml:executeHeightMode non sa esprimere.
- Il riferimento delle quote in export e' finalmente un comando del
pannello e non piu' solo un'impostazione salvata: serviva sceglierlo
perche' WPML ne accetta due su tre.


1.38.0
- La tabella attributi delle geometrie CAD ha cinque colonne, in
quest'ordine, e nient'altro: Area, Perimetro, Comune, Foglio,
Particella. Niente cad_id, niente colonne denormalizzate, niente
cad_params fra le colonne, e niente campi nascosti.
- Il record parametrico che Sposta, Ruota e Ridimensiona leggono non
e' sparito: vive accanto alla feature, nelle proprieta'
personalizzate del layer, e i tre strumenti continuano a
funzionare. Un layer scelto dall'operatore che porti gia' la
colonna cad_params continua a usarla.
- Il commit trova da solo la feature che ha appena scritto: l'id che
addFeature restituisce dentro il buffer di modifica e'
provvisorio (misurato: -2, -3, -4 mentre il layer tiene 1, 2, 3),
quindi si legge dalla differenza degli id presenti prima e dopo la
scrittura. Ed e' la feature del layer, non quella costruita prima
della scrittura, che commit() restituisce.


1.37.0
- Serie DJI Mini in libreria, disponibile senza doverla scrivere a
mano: Mini (1), Mini 2, Mini 3, Mini 3 Pro e Mini 3 Pro con
batteria Plus, con le rispettive camere. Peso, autonomia e
velocita' massima sono quelli dichiarati; l'ottica reale (focale e
area utile del sensore), che nessun costruttore pubblica, e'
ricavata dall'equivalente 35 mm e dal formato del sensore, e la
derivazione e' scritta in ogni scheda.
- Il profilo drone porta anche il peso al decollo, con la classe
sotto i 250 g calcolata da li'. Un peso non dichiarato non vale
"leggero".


1.36.0
- Due moduli, due percorsi. Il dock sinistro porta un selettore
Rimboschimento / Volo UAV e mostra gli step di uno solo alla volta:
quattordici o sei, non venti in un'unica lista. Il passaggio da un
modulo all'altro e' un clic, e nessuno quando e' un pannello a
chiederlo ("Genera missione UAV" parte da uno step di
rimboschimento e arriva su uno di volo).
- Il rimboschimento esiste solo li'. La scheda "Rimboschimento" del
pannello CAD e' stata rimossa insieme al modulo che la disegnava:
era una seconda progettazione piu' semplice, senza zone, mix di
specie, vincoli, naturaliforme ne' catasto, e due modi di
progettare lo stesso impianto sono uno di troppo.
- Tolti dal menu del plugin anche i due avvii che ripetevano il
lavoro dei moduli ("Piano di volo UAV...", "Sesto d'impianto..."):
gli algoritmi restano dove un utente QGIS li cerca, nella cassetta
degli strumenti di Processing.


1.35.0
- Relazione di missione in PDF, Word ed Excel, oltre all'HTML. E' la
stessa relazione: la missione viene descritta una volta sola
nell'oggetto report di io.documents e i tre scrittori la mettono su
carta. Il foglio di calcolo porta in piu' la tabella di tutti i
waypoint, che in un PDF sarebbero quaranta pagine illeggibili.
- Il profilo altimetrico viaggia dentro il PDF come figura,
disegnato dallo stesso grafico che si vede nel pannello. La strada
e' stata verificata su entrambe le versioni di QGIS prima di
usarla; Word ed Excel dicono dov'e' la figura invece di fingere di
portarla, perche' quella parte non e' verificata.


1.34.0
- Missioni a corridoio: strade, corsi d'acqua, elettrodotti. Scegli
una linea invece di un poligono e la missione diventa un corridoio,
con la sola larghezza da indicare. Le strisciate sono curve
parallele all'asse, non rette tagliate su un buffer: e' cio' che
tiene costanti la sovrapposizione laterale e l'AGL in curva.
Il motore lo sapeva fare dalla prima versione e nessuno poteva
chiederglielo.
- Il tipo di sensore fa parte del profilo camera: RGB,
multispettrale, termico, LiDAR. I dieci preset inclusi sono
fotocamere RGB e ora lo dichiarano; gli altri tipi sono un
vocabolario che una libreria personale puo' usare, con i numeri
presi dalla propria scheda tecnica.
- Lo step Hardware descrive davvero l'hardware: focale, sensore,
pixel, rapporto d'aspetto, FOV, otturatore, intervallo minimo di
scatto, autonomia e limite di waypoint del drone. Il riquadro
c'era e restava vuoto.


1.33.0
- Profilo altimetrico nello step Simulazione: terreno e quota di volo
sullo stesso grafico, disegnato con QPainter. E' l'immagine che
mostra che il terrain following e' successo davvero, perche' le
due curve restano parallele; su una quota AMSL fissa la linea di
volo sarebbe piatta mentre il terreno si muove.
- Cursore temporale: si trascina e si va a un istante del volo.
Quota, AGL, distanza percorsa, batteria della tratta e fotogrammi
seguono, e tornando indietro gli scatti si dis-fanno invece di
restare contati.
- "Mostra le impronte a terra": l'impronta di ogni scatto proiettata
sul DEM, con riempimento traslucido. Dove due fotogrammi si
sovrappongono il colore raddoppia: frontlap e sidelap si leggono
sulla mappa invece che su un numero.
- "Relazione di missione (HTML)": la relazione completa esisteva gia'
ma usciva solo da un algoritmo di Processing, dove nessuno la
cercava. Ora e' un tasto dello step Export.


1.32.0
- Il pianificatore di volo e' entrato nella dashboard, come sei step
con il loro stato: Area del volo, Hardware e GSD, Parametri di
volo, Sicurezza e ostacoli, Simulazione, Export. La scheda UAV e
la scheda Layer/Export del pannello CAD sono sparite: erano una
seconda casa per lo stesso lavoro.
- Quota e GSD si fissano nei due versi. Si sceglie quale dei due
vincolare; l'altro lo calcola l'ottica e non e' modificabile a
mano, perche' GSD = quota x passo del pixel / focale ha un solo
grado di liberta'.
- Ostacoli: un layer vettoriale qualsiasi, un raggio di rischio, e
l'intersezione con la rotta vera. Non solo con i waypoint: una
tratta puo' attraversare un traliccio su cui non cade nessun
vertice, e fino a ieri passava per buona.
- Controllo pre-volo con i suoi ingressi. Il validatore riceve
parametri, terreno e area, non piu' la sola missione: otto
controlli in piu' (autonomia, intervallo di scatto, mosso,
salita, camera, limite waypoint, CRS, franco sul DTM).
- Schema delle strisciate, doppia griglia ortogonale, margine
fotogrammetrico e franco sulla vegetazione: il motore li accettava
gia', il pannello non li offriva.
- "Genera missione UAV" sullo step Genera del rimboschimento: passa
al volo l'area utile, le esclusioni, il DEM del progetto e
l'orientamento dei filari, col preset forestale gia' impostato.


1.31.0
- L'azimut delle strisciate si puo' misurare invece che indovinarlo.
Il tasto "Ottimizza" della scheda UAV prova ogni orientamento a
passi di 5 gradi su mezzo giro, dispone le strisciate sull'area
vera e sceglie quello che costa meno volo. Su un rettangolo da' la
stessa risposta del lato piu' lungo; su un blocco concavo no: sul
caso di prova risparmia il 36 per cento del tempo e quasi meta' delle
virate.
- Un progetto di rimboschimento sa gia' tutto quello che serve a
volarci sopra. uav.forest_link legge la superficie utile, le
esclusioni e l'orientamento dei filari e ne ricava i parametri di
volo con i valori forestali: sovrapposizione 85/75 dal preset
"complex_terrain", terrain following sempre acceso, nessuna
maggiorazione per la vegetazione aggiunta di nascosto.


1.30.0
- La particella si legge dove si disegna. La scheda CAD porta un
riquadro "Dati catastali" che si compila da solo: superficie e
perimetro nell'istante in cui la figura viene posata, Comune,
Foglio e Particella appena il task catastale risponde. Nessun
pulsante da premere, nessuna scheda da cambiare.
- Quando il servizio non risponde o il terreno e' fuori copertura il
riquadro dice "N/D" e ne spiega il motivo; la figura, l'area e il
perimetro restano scritti.
- L'interruttore dell'interrogazione catastale e' finalmente sul
pannello: prima esisteva solo come impostazione, senza un comando
che potesse spegnerla.


1.29.0
- Disegnando un Quadrato, un Rettangolo o un Poligono col CAD, la
tabella degli attributi si compila da sola: Area e Perimetro
subito, alla posa della figura, e Comune, Foglio e Particella poco
dopo, scritti da un task in background sullo stesso identificativo
di feature. L'interfaccia non aspetta niente.
- L'interrogazione catastale e' accesa per impostazione predefinita.
Era opzionale e spenta: il meccanismo c'era e non partiva mai da
solo. L'interruttore resta, per chi lavora senza rete o fuori dal
territorio italiano.
- Il layer CAD nasce con le cinque colonne (Area, Perimetro, Comune,
Foglio, Particella), non le fa crescere se per caso una chiamata di
rete riesce: una colonna che compare soltanto a cose fatte e' una
colonna che nessuno sa di dover guardare.
- La colonna Area e' in metri quadri, non piu' in ettari, e si chiama
Area. Il perimetro si chiama Perimetro. E' una rinomina delle
colonne visibili: nessun dato perso, nessuna conversione aggiunta
-- anzi, una tolta.
- Quando il servizio non risponde, rifiuta, o non ha particelle su
quel terreno (Trento e Bolzano tengono il proprio catasto), le tre
colonne dicono "N/D". Lasciate vuote sarebbero indistinguibili da
un'interrogazione mai partita.
- La colonna Comune porta il nome del comune, non il codice Belfiore:
una colonna intitolata Comune che dice G478 non dice niente. Il
codice resta nell'etichetta della particella e nel record
parametrico.
1.28.0
- Un solo pulsante sulla toolbar di QGIS, "Suite GeoCad
Rimboschimento", e apre la dashboard. Erano cinque: il pulsante del
dock piu' le tre forme CAD e l'interruttore parametrico. Le quattro
sono tornate sulla toolbar del plugin, dentro il dock del plugin,
dove stavano gia' i modificatori: la toolbar di QGIS e' di QGIS e
degli altri plugin.
- Corretto, ed e' il difetto piu' grave trovato finora: la dashboard
del rimboschimento veniva montata nascosta e nessuno la mostrava
mai. Il metodo che l'avrebbe aperta esisteva e non era chiamato da
nessuna parte. I quattordici step, l'interrogazione catastale e
ogni pannello dietro di loro erano irraggiungibili da QGIS in
funzione, per quanto bene funzionassero sotto test.
- Tema scuro ad alta densita' sui due dock del plugin, e soltanto su
quelli: un foglio di stile messo sull'applicazione avrebbe
ri-vestito QGIS e i pannelli di ogni altro plugin. I colori stanno
in una tavolozza unica; il foglio non ne scrive a mano nessuno.
- Grafico della mescolanza nel pannello Specie: una barra per specie,
percentuale chiesta contro percentuale ottenuta, nei colori che le
piante avranno sulla mappa. Disegnato con QPainter, senza librerie
che l'operatore dovrebbe installare.
- [Genera Anteprima] disegna sulla mappa invece di scrivere un numero
in un'etichetta: una banda elastica attorno alla superficie con la
direzione delle file attraverso, e un layer temporaneo di piante
colorate per specie. Generare l'impianto toglie l'anteprima invece
di affiancarla, e [Togli anteprima] la porta via a mano.
1.27.0
- Il catalogo delle specie si apre, si corregge e si salva dal
pannello Specie. Il plugin non porta con se' nessuna lista di
specie -- sarebbe vecchia in una stagione e sbagliata in una valle
-- ma finora non c'era nemmeno il modo di caricarne una: una specie
digitata era un codice e un nome, senza distanza minima propria,
e il passaggio naturaliforme che rispetta la distanza di ciascuna
specie non aveva niente da rispettare.
- La tabella delle specie mostra il nome di catalogo e la distanza
minima di ognuna, e quella distanza si corregge digitandola: va nel
catalogo, che si risalva in GeoPackage per il progetto successivo.
- Verificato sul percorso intero: catalogo con leccio a 7 m e pino a
3 m, sesto di 4 m, dopo il diradamento restano 441 lecci a non meno
di 8,00 m l'uno dall'altro, e il pino -- che a 4 m da un leccio gli
sta in mezzo -- viene dichiarato come specie che non trova piu'
posto, invece di sparire in silenzio da meta' della mescolanza.
1.26.0
- Il limite di pendenza smette di essere una percentuale e diventa
superficie tolta. Fino a ieri l'operatore impostava "pendenza
massima 35 deg", leggeva "82 per cento idonea" e l'impianto veniva comunque
generato su tutta la superficie: il numero non entrava nel piano.
[Escludi le aree non idonee] trasforma le celle rifiutate in una
geometria, che viene sottratta come qualunque altro vincolo --
stessa meccanica, stessa riga in relazione, stesso colore sulla
mappa. Misurato su un versante di prova: 237.600 -> 35.640 m2 utili
a 11,5 deg.
- Il pannello dice se il limite e' gia' stato applicato o no, invece
di mostrare una percentuale che non si sa se conta.
- Un limite che non lascia nulla viene dichiarato subito, invece di
farlo scoprire tre passi dopo al generatore.
- La poligonizzazione usa gdal.Polygonize direttamente e non
l'algoritmo Processing omonimo: quello invoca uno script esterno
che, sull'installazione autonoma 3.40.15, produce un file che QGIS
non riesce a riaprire -- verificato su ogni formato di uscita.
Stessa funzione C, chiamata direttamente, senza scrivere niente su
disco.
1.25.0
- Tutti e sei i formati di esportazione vengono ora scritti e riletti
nella prova: prima soltanto il GeoPackage era mai stato provato, e
due degli altri non funzionavano.
- Corretto: l'esportazione in DXF non produceva alcun file. Il DXF e'
un formato di disegno e OGR rifiuta di creargli dei campi; il
pannello leggeva quel rifiuto come esportazione fallita e buttava
via un disegno gia' scritto correttamente. Adesso gli attributi non
vengono chiesti, il disegno viene scritto (misurato: 9.359 piante
rilette) e all'operatore viene detto che le specie e le quote
restano negli altri formati.
- Corretto: l'esportazione in CSV non scriveva le coordinate. Il
driver CSV non le scrive se non gliele si chiede: l'operatore
otteneva un elenco di numeri di pianta e di specie senza un posto
dove piantarle. Adesso il CSV porta le colonne X, Y, Z.
1.24.0
- Prova di accettazione: venti operazioni in fila, in una sessione
sola, dall'interfaccia vuota alla relazione scritta, ciascuna
superata solo se produce qualcosa che si vede -- un numero in un
pannello, una feature sulla mappa, un file su disco. Comprende
l'interrogazione del catasto vero, che viene saltata dichiarando il
motivo quando il servizio non risponde.
- Corretto: la tavola usciva a scala assurda (misurata 1:213.173.398)
quando il progetto QGIS non era nello stesso sistema di riferimento
del progetto di rimboschimento. Il riquadro della mappa seguiva il
CRS del progetto QGIS, e un'estensione UTM in un riquadro dichiarato
in gradi da' un foglio che mostra il pianeta con la particella
grande un pixel, senza alcun errore da nessuna parte. Adesso il
riquadro prende il sistema del progetto di rimboschimento.
1.23.0
- Il progetto si salva e si riapre. Fino a ieri ore di lavoro -- area
rilevata sul catasto, vincoli misurati, zone tagliate, specie
decise, impianto generato e poi corretto pianta per pianta --
finivano con la chiusura di QGIS.
- Il file (.gcuav) contiene le decisioni e il risultato che hanno
prodotto. Le geometrie escono in WKT col loro sistema di
riferimento accanto; le piante escono come record, perche' un
impianto corretto a mano non si riottiene rigenerandolo e un file
che rigenerasse in silenzio un impianto diverso sarebbe peggio di
nessun file.
- Il DEM non viene scritto nel file: e' un raster, puo' pesare
centinaia di megabyte e ha un percorso. Il percorso viene salvato e
riletto; se il file si e' spostato il progetto si apre senza
terreno e lo dichiara, invece di aprirsi con un terreno che non e'
quello su cui il piano e' stato fatto.
- Barra del progetto sopra gli step: Nuovo, Apri, Salva, Salva con
nome, Annulla, Ripeti, Aggiorna, Impostazioni, con le scorciatoie
consuete.
- Annulla e Ripeti lavorano su istantanee dell'intero progetto e
annullano un'azione dell'operatore, non un carattere digitato:
un'area presa, dei vincoli applicati, delle zone tagliate, un
impianto generato, una modifica confermata.
- Un piano riaperto, o ripristinato con Annulla, torna sulla mappa
come layer: prima il piano c'era e il layer no, oppure restavano
disegnate le piante di un piano che non esisteva piu'.
- Un file danneggiato, di un altro programma o di una versione di
formato piu' recente viene rifiutato dicendo quale, invece di
essere letto a meta'.
1.22.0
- L'impianto naturaliforme e' uno step del flusso, il nono, con un
pannello suo. Stava dentro una scheda del Sesto, dove tre
lavorazioni diverse (radure, diradamento casuale, distanza minima)
finivano in un'unica etichetta di esito: adesso il pannello dice
quante radure ha collocato, quante piante ha tolto e quale
distanza minima ha misurato.
- [Applica al progetto] rigenera l'impianto con le impostazioni
correnti, invece di diradare un impianto gia' diradato: chi
abbassa l'irregolarita' si aspetta di riavere delle piante, non di
perderne altre.
- I pannelli continuano a non chiamarsi fra loro: il progetto emette
la richiesta di rigenerazione e risponde chi possiede il
generatore.
- Il flusso ha quattordici step.
1.21.0
- La relazione tecnica si scrive in PDF, in Word (.docx) e in Excel
(.xlsx). Una sola relazione, costruita dal modello, e tre
scrittori: i numeri sui tre file non possono divergere di un
arrotondamento perche' sono gli stessi numeri.
- La relazione porta ora tabelle vere: particelle catastali, vincoli,
zone, composizione e scenari a confronto. L'elenco delle piante,
una riga per pianta, sta soltanto nel foglio di calcolo: in un PDF
sarebbero quaranta pagine illeggibili.
- PDF attraverso QTextDocument e QPdfWriter, che PyQt porta con se':
nessuna libreria da installare. Riaperto e riletto in prova.
- Foglio di calcolo con openpyxl, che QGIS distribuisce. Riletto con
la stessa libreria: un numero scritto come numero torna come
numero e si puo' sommare.
- Il documento Word e' scritto come pacchetto Open Packaging
Conventions: QGIS non distribuisce una libreria Word e il formato
e' stato composto a mano sui tipi di contenuto e sulle relazioni
letti da un .docx prodotto da Microsoft Word, con la
documentazione Microsoft del documento minimo alla mano. Provato
aprendolo con Microsoft Word, che ne rilegge testo, tabelle e
proprieta'. Contiene testo, titoli e tabelle: nessuna immagine,
nessuna intestazione, nessun campo, perche' non sono stati
verificati.
1.20.0
- Cartografia: la tavola di progetto, come dodicesimo step. Non uno
screenshot della mappa, ma un QgsPrintLayout vero, con titolo,
sottotitolo, legenda, scala grafica, freccia del nord e una nota
che dichiara scala, sistema di riferimento, formato e autore.
- Il foglio e' descritto come frazioni di pagina: la stessa
composizione vale su A4 e su A0, in verticale e in orizzontale,
senza una seconda serie di misure da tenere allineata.
- La legenda elenca soltanto i layer del progetto. Una legenda
agganciata all'albero dei layer avrebbe elencato la carta di base
dell'operatore, la sua ortofoto e ogni layer di appoggio aperto.
- La scala chiesta e' la scala impostata; chiedendo "adatta al
progetto" si ottiene l'inquadratura dell'area con un margine, e la
scala risultante viene scritta sulla tavola.
- Esportazione in PDF e in immagine alla risoluzione scelta,
verificate: il PDF comincia per PDF- e l'immagine misura i pixel
che formato e dpi impongono.
- La tavola finisce nel Gestore di layout di QGIS, dove l'operatore
puo' aprirla e modificarla come qualunque altra; ricomporla
sostituisce quella vecchia invece di lasciarne due che si
contraddicono.
- Una tavola senza niente da disegnare viene rifiutata con la
ragione, invece di produrre un foglio bianco con un titolo.
1.19.0
- Editing interattivo dell'impianto, come undicesimo step del flusso.
Un piano generato e' una proposta: l'operatore sa che dove e'
finita la pianta 412 c'e' un masso, che l'angolo verso la pista ne
vuole tre in piu' e che la fila lungo il fosso va a querce.
- Si modifica con gli strumenti di QGIS, non con un secondo
digitalizzatore: il layer entra in modifica e i comandi Aggiungi,
Sposta ed Elimina sono quelli che l'operatore gia' conosce, con lo
stesso snap e lo stesso annulla.
- Dopo ogni modifica il progetto riprende il piano dal layer. Una
pianta spostata viene riletta sul DEM: quota, pendenza ed
esposizione sono quelle di dove sta adesso, non di dove era stata
generata.
- Una pianta aggiunta a mano prende un identificativo libero e una
fila propria, e resta senza specie finche' qualcuno gliela assegna:
si vede nella composizione invece di contare in silenzio come
specie maggioritaria.
- Cambio specie sulla selezione, con ricolorazione del layer e
ricalcolo delle percentuali effettive.
- La validazione e' passata dal pannello Verifica al progetto, cosi'
che l'Editing possa chiedere lo stesso giudizio: due copie degli
stessi controlli avrebbero divergito alla prima correzione. I due
pannelli riportano ora anomalie identiche, verificato.
1.18.0
- Impianto su curve di livello, dal pannello alla mappa. Il modulo
calcolava le file a quota costante da M04 e nessun operatore
poteva arrivarci: nell'interfaccia non esisteva la parola curva.
- Il pannello Terreno estrae le curve (gdal:contour sulla finestra
DEM gia' riproiettata nel sistema del progetto), le taglia sulla
superficie utile e le disegna come layer, con quota, sviluppo e
numero di piante per ciascuna.
- "Lungo le curve di livello" e' uno dei sesti, scelto dove si
scelgono gli altri. La generazione pianta lungo le linee con la
correzione di pendenza direzionale, che su una curva e' piccola
e vera.
- Le piante attese e la densita' non vengono piu' da una formula di
reticolo quando il sesto e' su curve: vengono dallo sviluppo
delle curve estratte, e la relazione lo scrive.
- Il confronto scenari confronta passi sulle curve, generandoli, e
non reticoli che non verrebbero piantati.
- Un DEM gia' caricato nel progetto si puo' usare senza scaricarne
un altro: un DTM regionale e' il caso piu' comune sul campo e non
richiede la rete.
- Rifiuti espliciti invece di risposte sbagliate: il generatore a
reticolo rifiuta lo schema su curve (avrebbe prodotto file
dritte spacciandole per curve), e la generazione su curve rifiuta
di lavorare a zone dicendo perche'.
- Il DEM di lavoro viene rilasciato alla chiusura del plugin: era un
QgsRasterLayer che il progetto non adotta, e raccoglierlo dopo lo
spegnimento di QGIS fa cadere il processo senza messaggi.
1.17.0
- Il progetto si vede sulla mappa. Area, superficie utile, aree
escluse, zone, radure e piante diventano layer veri sulla tela,
aggiornati a ogni modifica e rimossi tutti insieme quando il
plugin si chiude: niente piu' progetto che esiste solo nelle
etichette di un pannello.
- Le particelle catastali si vedono e si scelgono. La risposta del
WFS porta adesso anche le geometrie, riportate nel sistema di
riferimento del progetto: [Visualizza particelle] le disegna,
e scegliere una riga della tabella seleziona ed evidenzia la
particella corrispondente sulla mappa.
- La tabella delle particelle elenca comune, foglio, particella e
percentuale di ciascuna, al posto del solo conteggio.
- Un'interrogazione catastale che non riesce lo dice: accanto allo
stato compare il motivo, e una tabella vuota non resta mai senza
spiegazione.
- Corretto: l'interrogazione avviata dal pulsante non arrivava mai
a termine. Il task veniva raccolto dal garbage collector appena
creato, perche' il gestore dei task di QGIS non ne mantiene un
riferimento e il pulsante scartava quello restituito: il pannello
restava su "interrogazione in corso..." all'infinito. Adesso il
progetto tiene il riferimento fino all'interrogazione successiva.
1.16.0
- Impianto naturaliforme: radure vere, irregolarita' controllata e
distanza minima garantita. Le prime due rendono il sesto meno
regolare, la terza garantisce che meno regolare non diventi
sbagliato.
- Le radure si collocano con un seme: lo stesso progetto stampato
domani ha le stesse radure. Dove non c'e' spazio se ne collocano
meno e lo si dice, invece di sovrapporle.
- L'irregolarita' dirada e basta: non inventa mai una posizione
fuori dal sesto, quindi non puo' violare quello che l'operatore
ha impostato.
- La distanza minima rispetta quella di ciascuna specie, non solo
una soglia unica, e viene applicata partendo dalle specie piu'
esigenti: prese in ordine d'impianto, una specie che chiede 7 m
su un sesto di 4 m perdeva ogni confronto e spariva del tutto.
Misurato: 132 assegnate, 0 sopravvissute prima, 68 dopo.
- Su un impianto diradato la verifica non segnala piu' la distanza
media: i vuoti la alzano per costruzione, e segnalarli sarebbe
denunciare come difetto la funzione appena chiesta.
1.15.0
- Le zone vengono piantate davvero. Fino a ieri il pannello le
raccoglieva e il generatore piantava tutta l'area con un solo
sesto: chi leggeva il pannello ci credeva, ed era peggio che non
offrire le zone. Adesso ogni zona ha il suo sesto, la sua
composizione e il suo orientamento, e viene generata da sola.
- Suddivisione in fasce: si taglia la superficie utile in N strisce
che corrono lungo le file, cosi' nessuna macchina deve
attraversare un confine a meta' fila.
- Una zona entra ritagliata sull'area e su quelle gia' presenti:
non puo' piantare fuori progetto ne' sopra un'altra zona. La
superficie che non appartiene a nessuna zona viene dichiarata,
non piantata di nascosto ne' persa in silenzio.
- Ogni pianta porta il nome della sua zona, nel layer e nella
relazione; identificativi e file sono unici su tutto il piano.
1.14.0
- Nuovo spazio di lavoro per il rimboschimento: a sinistra le
undici fasi del progetto con lo stato di ciascuna, a destra solo
i comandi della fase in corso, e in mezzo la mappa, che torna a
essere la cosa piu' grande sullo schermo.
- Nella barra degli strumenti restano quattro pulsanti: Quadrato,
Rettangolo, Poligono e l'interruttore fra disegno libero e
inserimento parametrico. Una forma, un pulsante; i modificatori
restano nel pannello, perche' non sono primitive.
- La barra di stato di QGIS mostra superficie utile, piante e
stato del progetto: niente piu' cruscotti che rubano la mappa.
- 1.13.1 - il catasto arriva davvero nell'interfaccia: il task
emette un segnale col risultato, il pannello Area lo riceve e
scrive comune, codice Belfiore, particelle e superfici. Il
codice diventa il nome del comune leggendo la tabella locale.
Se il servizio non risponde, il pannello lo dice e il progetto
resta intero.
1.13.0
- Il catasto non e' piu' un dato sul singolo poligono ma un
servizio sull'area di progetto: si interroga il WFS sul contorno
dell'area, si intersecano le particelle vere con la geometria
vera, e si ottiene l'elenco completo di comuni, fogli e
particelle interessate con la superficie di ciascuna e la
percentuale di particella coinvolta.
- Il codice Belfiore diventa il nome del comune grazie alla
tabella distribuita col plugin (data/belfiore.csv, 7.896 comuni,
fonte ISTAT). Ricerca senza distinzione di maiuscole, codice
sconosciuto o nullo che diventa "Non disponibile" invece di un
errore, e possibilita' di indicare una propria tabella.
- L'intersezione non passa dal centroide: usa GEOS sui poligoni
reali, in un CRS metrico scelto dal progetto, perche' un'area in
gradi non e' un'area. Geometrie multiparte e non valide gestite.
- Tutto in secondo piano, con avanzamento e annullamento: un
catasto che non risponde diventa uno stato del risultato, non un
progetto di rimboschimento perduto.
1.12.0
- Piu' specie nella stessa area, nelle percentuali chieste. I
conteggi sono esatti e sommano sempre al numero di piante
generate: 40/35/25 su mille piante fa 400, 350 e 250, non
"circa". Tre disposizioni: uniforme (mescolate ovunque, anche
nella prima meta' dell'area), a gruppi, per file intere.
- Le specie vengono dal catalogo dell'operatore: una specie non in
catalogo viene rifiutata invece di essere piantata alla cieca.
- Il layer delle piante e' PointZ con identificativo, fila, zona,
specie, quota e pendenza, e nasce gia' colorato per specie: un
colore distinto per ciascuna, generato e non pescato da una
tavolozza che si ripete dall'ottava specie in poi.
- Il DEM si scarica da solo sul contorno della superficie utile,
senza chiedere all'operatore di trovare un raster. Passa dagli
adattatori gia' presenti e ne rispetta lo stato dichiarato: una
fonte il cui schema non e' stato verificato fino in fondo
rifiuta di scaricare e dice dove leggere la sua documentazione.
- Tolte dal pannello Rimboschimento le due scorciatoie di
allineamento: scrivevano nello stesso campo dell'azimut due
click piu' tardi, e l'orientamento che serve su un versante lo
calcola il terreno, a girapoggio o a rittochino.
1.11.0
- L'inserimento parametrico ha invertito l'ordine: prima le misure,
poi la posizione. Si sceglie la forma e si scrivono i numeri, la
geometria esatta compare agganciata al cursore a grandezza vera,
e un solo click la posa dove serve. Lo snap del progetto vale
durante il posizionamento come per ogni altro strumento.
- Quello che si vede appeso al cursore E' la geometria che viene
scritta: stesso costruttore, stessi vertici, nessun ingombro
approssimato.
- Le misure restano fra una posa e l'altra: venti piazzole da 4x4 m
si scrivono una volta e si posano con venti click. Il tasto
destro riapre la finestra per cambiarle.
1.10.0
- Ogni poligono CAD puo' portarsi dietro i dati catastali della
particella su cui ricade: comune, foglio e particella, letti dal
servizio WFS INSPIRE dell'Agenzia delle Entrate (licenza CC BY
4.0). L'interrogazione parte in secondo piano dopo il commit: la
geometria e' scritta subito e non aspetta la rete.
- Il servizio parla un solo sistema di riferimento, EPSG:6706
(RDN2008 geografiche, latitudine per prima). La trasformazione
dal sistema del progetto la fa QGIS, col contesto del progetto.
- Il comune e' il codice Belfiore: il servizio non pubblica il
nome del comune, e un elenco codice-nome scritto nel plugin
sarebbe un dato cablato. Il foglio viene dal livello Mappe.
- La funzione e' SPENTA finche' non la si accende nelle
impostazioni: nessuno strumento di disegno contatta un servizio
esterno senza che sia stato chiesto.
- Le tre colonne catastali compaiono solo sui layer per cui
l'interrogazione e' stata fatta davvero.
1.9.0
- Densita' e distanze si convertono l'una nell'altra, in entrambi
i versi e per ogni schema: 1 200 piante per ettaro diventano il
sesto che le produce, e il sesto torna a dare quella densita'
senza perdere una cifra.
- Su un pendio il programma dice su quale ettaro sta contando:
quello di carta o quello di terreno. Su 30 gradi la correzione
isotropa pianta il 15.47 per cento in piu' di quanto la superficie
richiederebbe, e adesso quel numero si legge invece di
scoprirlo alla consegna del vivaio.
- Catalogo delle specie in GeoPackage: si apre in QGIS come una
normale tabella e si modifica li'. Nasce VUOTO: nessun elenco di
specie e' scritto nel plugin, perche' sarebbe sbagliato in una
stagione e in una valle.
- I limiti ecologici di una specie (quota, pendenza, esposizione)
diventano lo stesso filtro che accetta o scarta la singola
pianta e che disegna la maschera di idoneita'.
1.8.0
- Il sesto si indica in distanze REALI sul terreno, non sulla
carta. Il generatore chiede al DEM la pendenza nel punto in cui
si trova e accorcia il passo planimetrico del suo coseno:
3 m richiesti su una pendenza di 30 gradi diventano 2.598 m in
pianta. Su terreno orizzontale non cambia nulla.
- Le piante escono come punti quotati (PointZ): la Z viene dal
DEM, non da zero. Una pianta senza quota resta un punto 2D
invece di finire al livello del mare.
- Due modi di leggere la pendenza: quella massima locale, che e'
la convenzione con cui un sesto si dichiara, e quella misurata
lungo la direzione del passo, che non accorcia una fila che
corre di livello.
- File sulle curve di livello: le curve si estraggono col
contouring di GDAL (equidistanza a scelta), si ritagliano sulla
superficie utile e si piantano con la correzione longitudinale.
- Orientamento delle file: manuale, geometrico (asse maggiore
della particella) o morfologico, a girapoggio o a rittochino.
Su terreno piano il criterio morfologico dichiara di non avere
una risposta invece di inventarne una.
- Due schemi nuovi: irregolare (naturaliforme, con spostamento
casuale limitato e riproducibile dal seme) e personalizzato
(sfalsamento di ogni fila deciso dall'operatore).
1.7.0
- Il CAD offre esattamente tre primitive: Quadrato, Rettangolo e
Poligono. Linea, polilinea, cerchio, arco e poligono regolare
non sono piu' strumenti di disegno: chi li chiede per nome
riceve un errore, non una geometria.
- Ogni primitiva ha due modi di inserimento, entrambi sulla barra:
il disegno sulla mappa (con lo snap del progetto e l'anteprima)
e l'inserimento parametrico, che chiede le misure in una
finestra e poi solo il punto di inserimento. I due modi passano
dallo stesso costruttore: un quadrato di 40 m disegnato e uno
digitato hanno gli stessi vertici.
- Il poligono si digitalizza vertice per vertice e si chiude sul
primo vertice; il modo parametrico costruisce il poligono
regolare da lati e raggio, apotema, lato o area.
- L'area di progetto del rimboschimento si disegna ora col
poligono, che produce un poligono vero invece di un anello da
ricucire. La misura di una distanza sulla mappa continua a
funzionare: usa lo stesso strumento di prima, che pero' non
occupa piu' un posto in barra.
1.6.0
- Nuovo modulo di progettazione del rimboschimento: l'area di
progetto con le sue tre superfici (lorda, esclusa, utile), la
morfologia letta dal DEM e le fasce di rispetto parametriche.
- Superficie utile = lorda meno esclusa, calcolata da GEOS sul
poligono vero: i buchi interni sono gia' fuori dalla lorda, due
esclusioni sovrapposte si sottraggono una volta sola e cio' che
sporge dal confine conta solo per la parte che cade dentro.
- Le fasce di rispetto sono un dizionario, non un numero scritto
nel codice: strada 5 m, fosso 10 m, elettrodotto 3 m si
cambiano dal progetto. Una fascia di 0 m tiene l'elemento
stesso; un vincolo mai dichiarato viene rifiutato con un
messaggio invece di essere ignorato.
- La maschera di idoneita' usa gli stessi criteri di pendenza,
quota ed esposizione che poi accettano o scartano la singola
pianta, cosi' la mappa e il risultato non possono discordare.
Le celle dove la pendenza non e' calcolabile (bordo di un buco
del DEM) sono escluse come non misurate, non date per buone.
1.5.0
- Nuovo strumento «Poligono digitalizzato»: si clicca vertice per
vertice e si chiude cliccando di nuovo il primo. La tolleranza di
chiusura e' in pixel, quindi vale a qualsiasi scala. Il tasto
destro annulla un vertice per volta (Ctrl+Z), Ctrl+Y lo rimette;
solo a costruzione vuota il tasto destro annulla tutto.
- Nuovo strumento «Inserimento manuale»: si clicca dove va la forma
e si scrivono le misure in una finestra. Rettangolo, quadrato,
cerchio e poligono regolare, ciascuno dimensionabile con la misura
che si ha davvero: lato, diagonale, area, perimetro, raggio,
apotema, diametro o circonferenza.
- Nessuna geometria nuova: entrambi gli strumenti passano dalle
primitive gia' presenti, cosi' un cerchio scritto come area e' lo
stesso cerchio dello strumento Cerchio, vertice per vertice.
- La lettura di area e perimetro del poligono digitalizzato e'
calcolata rispetto al primo vertice: sulle coordinate UTM il
calcolo diretto perdeva 6e-4 m2 su 1908 m2.
1.4.9
- Il cerchio disegna i suoi due diametri come guide di costruzione:
Nord-Sud ed Est-Ovest, punteggiati, mentre il raggio si digita o si
insegue col mouse. Servono a centrare una piazzola o un impluvio
senza misurare a occhio.
- Le guide restano guide: non entrano nella geometria salvata, non
creano un secondo oggetto e spariscono con l'anteprima.
1.4.8
- La scheda si chiama «Rimboschimento», non piu' «Foresta»: e' il lavoro
che si fa, non l'argomento. Anche il suggerimento dell'icona in barra
elenca ora solo le schede che esistono davvero.
- Il menu degli schemi offre tutti e cinque i sesti che il motore sa
generare: rettangolare, quadrato, quinconce (triangolare), esagonale e
a file. Nessuno schema senza generatore dietro.
- Con lo schema quadrato la distanza fra le file segue quella fra le
piante ed e' bloccata, perche' e' cosi' che il motore lo costruisce:
prima si poteva digitare un valore che veniva ignorato.
- Lo schema predefinito resta quello rettangolare, come nel motore.
1.4.7
- Le due distanze del sesto ora si distinguono a colpo d'occhio:
«Distanza fra le piante (sulla fila)» e «Distanza fra le file». Il
motore le rispettava gia': misurate su un sesto 5x5 le piante cadono a
5.000000 m sia lungo la fila sia fra le file.
- Corretta l'etichetta dell'orientamento. L'azimut e' l'inclinazione del
reticolo misurata da Nord in senso orario, e le file corrono
perpendicolari: con azimut 0 le file vanno da Ovest a Est. Prima si
leggeva «Orientamento file», che diceva il contrario.
- Il tasto «Sesto da due click» si chiama ora «Misura distanza in mappa»:
misura una distanza sulla mappa e la scrive nella distanza fra le
piante, che e' quello che ha sempre fatto.
- Nuovo prospetto del sesto sotto l'anteprima: schema, distanze, margine,
azimut, superficie lorda e utile, piante effettive e teoriche,
riempimento, densita' teorica ed effettiva, numero e lunghezza totale
delle file. Con un DEM si aggiungono quota minima e massima,
dislivello, pendenza minima, media e massima e le piante escluse dai
filtri.
- Con un DEM il prospetto riporta la distanza fra le piante sia in
pianta sia sul terreno. La spaziatura resta planimetrica e il prospetto
lo dichiara: le quote vengono dal DEM, il reticolo no.
1.4.6
- La tabella attributi di un layer CAD mostra ora tre sole colonne, in
quest'ordine: cad_id, area_ha e perimeter_m. Sono quelle che si leggono
e si ordinano davvero.
- Le altre colonne restano nel file, semplicemente nascoste. cad_params
in particolare non viene mai cancellato: e' quello che Sposta, Ruota,
Ridimensiona e la ricostruzione leggono, e senza di lui quegli
strumenti diventerebbero ciechi sulla geometria.
- La visibilita' viene riapplicata a ogni conferma, cosi' anche un layer
creato con una versione precedente si presenta pulito.
1.4.5
- Ritirata la scheda Griglie. Il motore del reticolo resta e continua a
generare i sesti d'impianto: e' la scheda a sparire, non la funzione.
- Il selettore dell'area vive ora per conto suo, in gui/extent_source.py,
e resta uno solo per tutto il plugin: Rimboschimento e UAV usano lo
stesso, come prima.
- Ritirato lo strumento Ellisse dalla barra CAD, che torna a dieci
strumenti. La primitiva ellisse resta nel motore, quindi le ellissi
gia' disegnate si aprono, si spostano e si ridimensionano come prima.
1.4.4
- Mentre si disegna un'ellisse i due assi vengono tracciati sulla mappa
con una linea punteggiata, oltre al contorno: si vede subito quale
semiasse e' quale e come e' orientata la figura.
- Le guide vivono su una seconda banda elastica, separata dalla forma:
non entrano mai nella geometria salvata e non costano nulla al
passaggio del mouse.
- I diametri del cerchio non sono ancora disegnati: lo strumento cerchio
non pubblica i propri assi e in questa versione non e' stato toccato.
1.4.3
- Un'ellisse non sembra piu' un cerchio mentre la si disegna. Finche' il
semiasse minore non e' stato indicato l'anteprima mostrava una
circonferenza: ora mostra l'asse maggiore, che e' l'unica cosa davvero
decisa, e il pannello scrive «b = ?». Il motore era corretto: a=20 b=10
con azimut 0 occupa 20 m in Est e 40 m in Nord.
- Nuovo strumento Arco, undicesimo della barra CAD. Si costruisce in tre
modi: centro con raggio e due azimut, tre punti sulla circonferenza,
oppure due estremi e un raggio (viene scelto l'arco minore e il
pannello lo dichiara). L'arco e' una linea: finisce su un layer di
linee, con area zero e la lunghezza misurata in perimeter_m.
- Tre punti allineati, raggio nullo o ampiezza impossibile vengono
rifiutati dal motore con il motivo in chiaro.
- La rotazione puo' ora avvenire attorno a un vertice della geometria,
oltre che attorno al centro, all'ancora dei parametri o a un punto
indicato. Il vertice e' una scelta in piu': il centro resta il default.
- Corretta la precisione del cerchio per tre punti: il calcolo veniva
svolto rispetto all'origine del sistema di riferimento e alle coordinate
UTM perdeva sette cifre, sbagliando di 0,76 mm un raggio di 10 m. Ora
viene svolto rispetto al primo punto e l'errore scende a 1e-10 m.
1.4.2
- Ogni geometria creata con gli strumenti CAD porta ora tre colonne
leggibili nella tabella attributi, oltre ai parametri: cad_id
progressivo sul layer, area_ha in ettari con due decimali e
perimeter_m in metri (per una linea e' la lunghezza, per un punto zero).
- Le colonne vengono aggiunte al layer di destinazione se mancano, una
volta sola. Se il layer non le accetta, la geometria viene scritta lo
stesso e il messaggio dice quale layer ha rifiutato: non si perde una
forma per tre colonne.
- Il progressivo e' il massimo esistente piu' uno, non il numero di
feature: dopo una cancellazione nessun id viene riassegnato.
- Area e perimetro non vengono ricalcolati dagli strumenti: sono le
misure che il motore ha gia' preso alla costruzione, nel CRS metrico di
lavoro. Gli ettari non nascono mai da coordinate in gradi.
1.4.1
- Sposta e Ridimensiona: due strumenti che modificano la feature
selezionata, come Ruota, senza creare nulla di nuovo.
- Sposta accetta @dx,dy digitato, due lunghezze, un trascinamento oppure
un punto base e uno di destinazione. Area, forma e orientamento restano
quelli di prima: e' una traslazione.
- Anche i parametri CAD si spostano: l'ancora della forma segue la
geometria, altrimenti la prima ricostruzione la riporterebbe indietro.
Se i parametri non sono leggibili la geometria si sposta lo stesso e il
messaggio lo dice, invece di lasciarlo scoprire dopo.
- Ridimensiona lavora su misure assolute, non su fattori di scala: si
scrive la larghezza, il raggio o il semiasse che si vuole e la forma
viene ricostruita dal motore. Centro e orientamento non si muovono.
- Le misure modificabili dipendono dalla forma: rettangolo larghezza e
altezza, quadrato un lato solo, cerchio il raggio, ellisse i due
semiassi, poligono una misura fra raggio, apotema, lato e area. Una
polilinea non ha una misura sola e viene rifiutata dicendolo.
- Un semiasse minore piu' grande del maggiore resta un errore del motore:
la geometria non cambia e non viene scambiato nulla.
- Escape lascia geometria e parametri identici a prima. Il passaggio del
mouse non costruisce geometrie e non apre comandi di annullamento: un
solo comando per ogni conferma.
1.4.0
- Tre strumenti CAD nuovi sulla barra del pannello: Quadrato, Ellisse e
Poligono regolare. La barra passa da cinque a otto strumenti.
- Quadrato: si indica lato, diagonale, area oppure perimetro, e sono
quattro modi di dire lo stesso numero. Le conversioni sono quelle del
motore, quindi le quattro strade danno la stessa geometria.
- Poligono regolare: raggio circoscritto, apotema, lato o area, con il
primo vertice sull'azimut indicato. Il numero di lati e' un parametro
di costruzione, come i segmenti del cerchio.
- Ellisse: semiasse maggiore, semiasse minore e orientamento; il secondo
click fissa asse maggiore e direzione. Un semiasse minore piu' grande
del maggiore viene rifiutato dal motore e non viene scambiato di
nascosto: si vede il messaggio, non una forma diversa da quella chiesta.
- Come per gli altri strumenti, il passaggio del mouse non costruisce
nulla: una sola geometria, al momento della conferma.
1.3.5
- Scheda Layer/Export: il segnaposto e' sostituito dalla scheda vera. La
sorgente e' la missione gia' generata nella scheda UAV; senza missione
tutto resta disabilitato e il pannello spiega perche'.
- Ogni formato porta il proprio stato accanto al nome. I nove scrittori
esistenti (GeoPackage, GeoJSON, KML, KMZ, GPX, CSV waypoint, CSV centri
di presa, Litchi Mission Hub, QGC WPL 110) sono VERIFIED perche' la
suite li scrive e li rilegge contando i record, non perche' lo dice una
tabella. Il WPML DJI nativo e' elencato come UNSUPPORTED, non e'
selezionabile e mostra il motivo: lo schema non e' verificato.
- Cartella, nome base e anteprima: i percorsi si vedono prima di
scrivere, e non viene creato un solo byte finche' non si preme Esporta.
- Il riferimento di quota arriva dalle Impostazioni ed e' scritto per
esteso; se e' relativo al decollo viene dichiarata anche la quota di
riferimento usata.
- Prima di esportare gira il validatore: gli errori bloccano l'export e
vengono elencati in italiano, gli avvisi si leggono e si procede.
1.3.4
- Simulazione del volo nella scheda UAV: Play, Pausa, Stop e velocita' di
riproduzione 1x/2x/5x sulla rotta gia' generata.
- Un marker percorre il tracciato e un secondo marker lampeggia a ogni
centro di presa attraversato: a fine corsa i lampi sono esattamente
quanti gli scatti della missione.
- Il simulatore non ricalcola nulla. I tempi vengono dalla stessa funzione
che ha cronometrato le strip, uav.terrain_follow.segment_flight_time, e
quote, velocita' e waypoint sono letti dalla missione senza toccarli.
- Due orologi distinti e dichiarati: il tempo di percorso, che e' quello
che si guarda scorrere, e il tempo di missione riportato dal
pianificatore, che comprende anche decolli, atterraggi e virate.
- La velocita' di riproduzione moltiplica solo l'orologio: la missione
resta identica waypoint per waypoint.
- L'anteprima e' una rubber-band: nessuna feature scritta, nessun layer
creato, nessun ridisegno forzato della mappa. Stop ripulisce tutto.
- Senza una rotta generata il pulsante Play resta disabilitato e dice
perche'.
1.3.3
- Sorgenti DEM: un pulsante "Scarica un DEM..." nella scheda UAV apre una
finestra con le fonti disponibili. Il raster scaricato entra nel
progetto e compare nell'elenco DEM del pannello, senza altri passaggi.
- Ogni fonte dichiara lo stato del proprio schema di richiesta:
NASADEM via OpenTopography e' VERIFIED (endpoint e parametri letti
dalla documentazione ufficiale); Copernicus GLO-30, TINITALY e
Terrarium sono PARTIAL e il download resta disattivato, con scritto
quale elemento non e' stato verificato e il link alla fonte; la Google
Elevation API e' UNSUPPORTED e viene rifiutata.
- Uno stato PARTIAL non tenta la richiesta: nessun parametro viene
indovinato e nessun byte viene scaricato.
- Chiavi API solo nelle Impostazioni, con un tipo dedicato che non viene
mai stampato: non compaiono nei log, nei messaggi di errore, negli URL
mostrati ne' nell'elenco delle impostazioni.
- Cache su disco per coppia (fonte, area): una seconda richiesta identica
non riscarica nulla.
- Il download gira in un QgsTask annullabile. I byte vengono scritti in un
file .part e rinominati solo alla fine: un annullamento non lascia ne'
un DEM troncato ne' file temporanei.
1.3.2
- Scheda UAV: la missione si genera dentro il pannello, non piu' solo
passando dal dialogo dell'algoritmo. Area dallo stesso selettore delle
schede Griglie e Foresta (poligono del progetto oppure disegnato con
Rettangolo/Polilinea).
- Terrain following obbligatorio. Il DEM si sceglie fra i raster gia'
caricati nel progetto: niente download, niente chiavi API, nessun
servizio remoto. Senza DEM il pulsante Genera resta disabilitato e
spiega il motivo, invece di ripiegare su una quota AMSL unica.
- Parametri con un solo riferimento ciascuno: quota H_AGL, velocita' in
km/h (convertita in m/s una volta sola, il motore vede solo m/s),
sovrapposizioni in percento (frazioni al motore), margine di sicurezza,
camera e drone dai profili. L'intervallo di scatto e' in sola lettura
perche' e' derivato: D_front diviso la velocita'.
- Azimut delle strip da campo numerico, da due click sulla mappa o
parallelo al lato piu' lungo dell'area; il valore del lato maggiore e'
sempre mostrato fra i valori derivati.
- Anteprima della rotta a rubber-band: nessuna feature scritta. I layer
(linee di volo, waypoint, centri di presa) nascono solo alla conferma,
da io.layer_factory.build_mission_layers.
- Nessun export in questa scheda. Il WPML DJI resta non supportato.
1.3.1
- Scheda Griglie: si definisce l'estensione scegliendo un poligono gia'
presente nel progetto oppure disegnandolo sul momento con gli strumenti
Rettangolo e Polilinea gia' esistenti. Nessun nuovo digitalizzatore.
- Passo del reticolo da due click sulla mappa (misurati con lo strumento
Linea): la distanza compila il campo dx. Il campo numerico resta il
riferimento, digitare un valore sovrascrive la misura.
- Azimut da due click oppure parallelo al lato piu' lungo dell'estensione.
- Anteprima a rubber-band: i punti si vedono senza che nulla venga scritto.
Il layer nasce solo alla conferma, in un unico comando di annullamento.
- Scheda Foresta: stesso selettore di estensione, sesto d'impianto,
schema, orientamento delle file, margine dal bordo e filtro di pendenza
opzionale su un DEM del progetto. Gli indicatori (piante, file, densita'
per ettaro, riempimento) vengono da forest.stats.
- I generatori non sono stati toccati: il reticolo e' core.grid e
l'impianto e' forest.planting. I pannelli scelgono il dove, ritagliano
con lo stesso predicato del modulo Processing (intersects, non contains,
cosi' i nodi sul bordo restano) e scrivono con core.undo.
1.3.0
- Strumento Ruota: rotazione di una feature esistente con maniglia,
come si ruota un'immagine in un word processor. Trascinamento con
angolo live nell'HUD, Shift aggancia ogni 15 gradi, angolo digitabile
nel pannello, perno a scelta fra centro del rettangolo di selezione,
ancora dei parametri CAD o punto indicato sulla mappa.
- La rotazione non costruisce geometria propria: i numeri vengono da
core.transform2d.rotate e il commit da QgsGeometry.rotate, che conserva
buchi, parti multiple e quota Z. Le due funzioni usano la stessa
convenzione oraria, verificata su 3.40 e 4.0.
- I parametri CAD seguono la rotazione invece di essere invalidati:
l'ancora ruota attorno al perno e l'azimut memorizzato incassa il delta.
Il risultato viene ricostruito e confrontato con la rotazione geometrica:
se non coincide, il record viene marcato non valido e la geometria
ruotata viene comunque scritta. Un record non resta mai a descrivere una
forma che non descrive.
- Ruota modifica la feature selezionata sul layer attivo e non crea mai un
layer di lavoro.
1.2.0
- Una sola icona sulla toolbar di QGIS: apre e chiude il pannello.
Le action CAD non spariscono, si spostano su una toolbar interna
alla scheda CAD del pannello.
- Pannello riorganizzato in sei schede: CAD, Griglie, Foresta, UAV,
Layer/Export, Impostazioni. Le schede Griglie, Foresta e Layer/Export
sono segnaposto dichiarati: aprono gli algoritmi Processing esistenti
e verranno riempite nelle milestone 1.2.4 e 1.2.6.
- Impostazioni persistenti su QgsSettings (unita', decimali, aggancio,
camera e drone predefiniti, sovrapposizioni, quota, formato di
esportazione, ultima scheda aperta). Letture sempre tipizzate: su PyQt6
una value() senza type restituisce una stringa.
- Aggancio collegato agli strumenti CAD tramite
QgsMapMouseEvent.snapPoint(), cioe' lo stesso motore dell'editing nativo
di QGIS, configurato dal QgsSnappingConfig DEL PROGETTO. Prima i click
usavano il punto grezzo del mouse e non agganciavano nulla.
- unload() simmetrico verificato: una seconda initGui() non duplica ne'
pannello ne' toolbar.
1.1.1
- PolylineTool: catena di vertici illimitata su canvas.
- Token polare @25<37 RELATIVO al segmento precedente (dopo una tratta a
90 gradi diventa 127 assoluti); con un solo vertice resta da Nord, quindi
LineTool non cambia comportamento.
- Backspace toglie l'ultimo vertice e lascia viva la costruzione; solo
l'ultimo vertice riporta a IDLE. Esc annulla tutto senza scrivere.
- Doppio click o Invio chiudono la polilinea (>= 2 vertici).
- Parametri salvati nella forma "segments" [[lunghezza, azimut], ...] che
primitives.build(TOOL_POLYLINE) gia' accetta: il record resta parametrico
e una traversa chiusa richiude a float64.
- Chiusura opzionale: produce una LineString chiusa, NON un poligono.
Il poligono richiederebbe una primitiva che non esiste ancora e non
viene simulato.
1.1.0
- Strumenti CAD interattivi su canvas: Linea, Rettangolo, Cerchio.
- Macchina a stati unica (IDLE, PICK_ORIGIN, PICK_SECOND, TYPE_CONSTRAINT,
PREVIEW, COMMIT) con Escape che annulla senza scrivere nulla.
- Input dinamico sul canvas: 25, 25ft, 37d, @25<37, #x,y, @dx,dy.
- Anteprima a rubber band senza costruire geometrie e senza refresh del
canvas; la geometria nasce solo al commit.
- Ogni forma e' un singolo comando di undo (beginEditCommand).
- Pannello CAD contestuale generato dai vincoli dichiarati dallo strumento.
1.0.0
- Primo rilascio.
- CAD: punto, linea, polilinea, rettangolo, quadrato, cerchio, poligono
regolare, ellisse; tutti calcolati algebricamente con record parametrico.
- Modificatori: offset, raccordo, smusso, taglia, estendi, esplodi, unisci,
dividi, allinea, serie rettangolare e polare.
- Grid Designer con ancoraggio stabile e rinumerazione dopo il ritaglio.
- Forest Planting Designer con filtri di pendenza, quota ed esposizione.
- UAV: terrain following continuo, quota AMSL per strip, quota AMSL unica
(ammessa solo sotto il 10 per cento di dislivello), doppia griglia, corridoi,
interlacciamento per ala fissa.
- Impronte a terra proiettate sul DEM per ray casting: copertura e GSD
effettivo verificati sul terreno, non sul piano.
- Export GeoPackage, GeoJSON, KML, KMZ, GPX, CSV, Litchi Mission Hub CSV,
QGC WPL 110. Il WPML DJI nativo non e' implementato perche' lo schema non
e' stato verificato: viene dichiarato esplicitamente invece di essere
simulato.
```
