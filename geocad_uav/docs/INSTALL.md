# Installazione

## Requisiti

* QGIS **3.34 LTR** o superiore (testato su 3.40.15 LTR; compatibile 4.0).
* Nient'altro. Qt, GDAL e numpy arrivano con QGIS.

Se un'installazione di QGIS non ha numpy (raro), installalo nell'ambiente di
QGIS, non in quello di sistema:

```
"C:\Program Files\QGIS 3.40.15\bin\python.exe" -m pip install numpy
```

## Da ZIP (consigliato)

1. Costruisci l'archivio:

   ```
   python zip_plugin.py
   ```

   Produce `dist/geocad_uav-1.0.0.zip` e ne verifica la struttura.

2. In QGIS: **Plugin → Gestisci e installa plugin → Installa da ZIP**,
   scegli il file, **Installa plugin**.

3. Compaiono:
   * la toolbar **GeoCad UAV Toolkit**;
   * il menu **Plugin → GeoCad UAV Toolkit**;
   * il gruppo **GeoCad UAV Toolkit** nel pannello Processing.

## Da sorgente (sviluppo)

Copia o collega la cartella `geocad_uav/` dentro il profilo QGIS:

* Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
* Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
* macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`

Un collegamento simbolico evita di ricopiare a ogni modifica:

```
mklink /D "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\geocad_uav" ^
          "C:\Users\nicco\Documents\Plugin_CAD_Qgis\geocad_uav"
```

Poi abilita il plugin in **Plugin → Gestisci e installa plugin → Installati**.
Per ricaricare dopo una modifica, usa il plugin *Plugin Reloader*.

## Verifica

```
python run_tests.py
"C:\Program Files\QGIS 3.40.15\bin\python-qgis-ltr.bat" run_tests.py --all
```

Atteso: `ALL SUITES PASSED`, nove suite.

## Personalizzare camere e droni

`geocad_uav/profiles/cameras.json` e `drones.json` sono modificabili. Clona una
voce, cambia la chiave e i parametri. Per lavoro metrico usa focale e sensore
del **tuo certificato di calibrazione**, non quelli di catalogo: il report
stampa sempre i valori usati e la fonte dichiarata.

Il plugin verifica che il passo pixel ricavato dalla larghezza del sensore
coincida con quello ricavato dall'altezza; se differiscono di oltre l'1 % ti
avvisa, perché significa che uno dei due numeri trascritti è sbagliato.

## Disinstallazione

**Plugin → Gestisci e installa plugin → Installati → Disinstalla**.
Toolbar, menu, pannello e algoritmi vengono rimossi simmetricamente: `unload()`
è lo specchio esatto di `initGui()`, quindi QGIS resta come lo hai trovato.
