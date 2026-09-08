# Quickstart

Prima missione in cinque minuti.

## Cosa serve

* un **poligono** dell'area, in CRS proiettato metrico (UTM);
* un **DEM o DTM** che la copra.

## Cinque passi

1. **Plugin → GeoCad UAV Toolkit → Piano di volo UAV...**

2. Compila:

   | Campo | Valore |
   |---|---|
   | Area di progetto | il tuo poligono |
   | Modello di elevazione | il tuo DEM/DTM |
   | Camera | DJI Mavic 3E (o la tua) |
   | Drone | DJI Mavic 3 Enterprise |
   | Definisci tramite | Quota di volo H_AGL |
   | Valore | `80` |
   | Sovrapposizione longitudinale | `80` |
   | Sovrapposizione laterale | `70` |
   | Modalita' di quota | Terrain following continuo |

3. **Esegui.**

4. Leggi il log. Deve comparire:

   ```
   VALIDAZIONE: Valida con N avvisi
   AGL 80 - 80 m
   Copertura 100.00 % con almeno 3 foto per punto
   ```

   `AGL 80 - 80` su terreno mosso significa che l'inseguimento del terreno ha
   funzionato: l'altezza sul suolo non è cambiata.

5. Apri il report HTML. Nel **profilo altimetrico** le due curve — terreno e
   quota di volo — devono restare parallele.

## Se qualcosa non va

| Messaggio | Cosa fare |
|---|---|
| *Il CRS dell'area è geografico* | riproietta il poligono in UTM |
| *Impossibile aprire il raster con GDAL* | salva il DEM come GeoTIFF |
| *Quota AMSL unica non ammessa* | usa il terrain following: il dislivello è troppo grande |
| *Velocità ridotta dal vincolo 'v_blur'* | rallenta o accorcia il tempo di scatto |
| *DTM senza clearance vegetazione* | in bosco o città, imposta la clearance |

## Poi

* `USER_GUIDE.md` — i quattro flussi completi.
* `README.md` — le formule e perché il terrain following è obbligatorio.
