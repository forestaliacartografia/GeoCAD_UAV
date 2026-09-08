"""
v1.3.3: DEM adapters, disk cache, credential hygiene.

No socket is opened anywhere in this file. The transport is a callable that
``dem_source.fetch`` receives as an argument, so every branch -- cache hit,
missing key, refusal, cancellation, a real GeoTIFF arriving -- is exercised
against a counter instead of a network.

The API key used below is a literal invented for this file. It is not a
credential and it never leaves the process; the point of D4 is to prove that
it does not appear in anything a human or a log file can read.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_dem_source.py
"""

import os
import re
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import QgsApplication                            # noqa: E402

QGS = QgsApplication([], False)
QGS.initQgis()

from osgeo import gdal, osr                                     # noqa: E402
from qgis.core import (QgsCoordinateReferenceSystem, QgsProject,  # noqa: E402
                       QgsRasterLayer, QgsRectangle)
from qgis.gui import QgsMapCanvas                               # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow                     # noqa: E402

from geocad_uav.core.errors import RasterError                  # noqa: E402
from geocad_uav.gui.uav_panel import UavPanel                   # noqa: E402
from geocad_uav.io import dem_source as ds                      # noqa: E402
from geocad_uav.settings import mask as mask_secret             # noqa: E402
from geocad_uav.settings import settings as app_settings        # noqa: E402

FAILURES = []
SKIPS = []
TMP = tempfile.mkdtemp(prefix="geocad_dem_")
CACHE = os.path.join(TMP, "cache")

#: Not a credential: an invented literal, so D4 has something to hunt for.
FAKE_KEY = "secret-TEST-xyz"
BBOX = (9.10, 45.40, 9.20, 45.50)


def check(label, got, expected, tol=1e-9):
    ok = abs(float(got) - float(expected)) <= tol
    print("  [{0}] {1:<54} got={2:<14.10g} exp={3:.10g}".format(
        "ok  " if ok else "FAIL", label, float(got), float(expected)))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def skip(label, reason):
    print("  [skip] {0}\n         reason: {1}".format(label, reason))
    SKIPS.append((label, reason))


def _refused(call):
    """True when the call raises instead of returning something unusable."""
    try:
        call()
    except Exception:                                           # noqa: BLE001
        return True
    return False


def _selects(panel, layer):
    """The combo can be pointed at this layer (it is in the project model)."""
    panel.dem_combo.setLayer(layer)
    return panel.dem_combo.currentLayer() is layer


def geotiff_bytes(nx=8, ny=6):
    """The smallest believable DEM: a real GeoTIFF, built in memory."""
    path = os.path.join(TMP, "seed_{0}x{1}.tif".format(nx, ny))
    gdal.UseExceptions()
    dataset = gdal.GetDriverByName("GTiff").Create(path, nx, ny, 1,
                                                   gdal.GDT_Float32)
    dataset.SetGeoTransform((9.10, 0.0125, 0.0, 45.50, 0.0, -0.0166666))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    dataset.SetProjection(srs.ExportToWkt())
    grid = 200.0 + np.arange(nx * ny, dtype=np.float32).reshape(ny, nx)
    dataset.GetRasterBand(1).WriteArray(grid)
    dataset.FlushCache()
    dataset = None
    with open(path, "rb") as handle:
        return handle.read()


class CountingTransport:
    """Records every call. The only thing standing in for a network."""

    def __init__(self, payload=b"elevation-bytes", chunks=1):
        self.payload = payload
        self.chunks = max(1, chunks)
        self.call_count = 0
        self.urls = []

    def __call__(self, url):
        self.call_count += 1
        self.urls.append(url)
        size = max(1, len(self.payload) // self.chunks)
        for start in range(0, len(self.payload), size):
            yield self.payload[start:start + size]


class Feedback:
    """The protocol dem_source.fetch expects, and QgsTask happens to satisfy."""

    def __init__(self, cancel_after=None):
        self.cancel_after = cancel_after
        self.calls = 0
        self.progress = 0.0
        self._cancelled = False

    def isCanceled(self):                                       # noqa: N802
        self.calls += 1
        if (self.cancel_after is not None
                and self.calls > self.cancel_after):
            self._cancelled = True
        return self._cancelled

    def setProgress(self, value):                               # noqa: N802
        self.progress = value


# --------------------------------------------------------------------------
# The adapter table, before anything else
# --------------------------------------------------------------------------
print("\n== adapter status table ==")
for adapter_id, label, status, note, url in ds.status_table():
    print("  {0:<18} {1:<12} {2}".format(adapter_id, status, label))
check("five adapters declared", len(ds.ADAPTER_ORDER), 5)
check_true("only a VERIFIED adapter can build a request",
           all(ds.ADAPTERS[a].url_builder is None
               for a in ds.ADAPTER_ORDER
               if ds.ADAPTERS[a].status != ds.VERIFIED))
check_true("every non-VERIFIED adapter says why",
           all(ds.ADAPTERS[a].note.strip()
               for a in ds.ADAPTER_ORDER
               if ds.ADAPTERS[a].status != ds.VERIFIED))
check_true("every remote adapter cites a source URL",
           all(ds.ADAPTERS[a].source_url.startswith("http")
               for a in ds.ADAPTER_ORDER if a != "google_elevation"))
_module_source = open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "io", "dem_source.py"), encoding="utf-8").read()
check_true("no credential literal is baked into the module",
           not re.search(
               r"(?i)(api_?key|token)\s*[=:]\s*['\"][A-Za-z0-9_\-]{8,}['\"]",
               _module_source))
check_true("the module reads keys only through the settings store",
           "app_settings.secret(" in _module_source)

# --------------------------------------------------------------------------
# D1 - the cache answers the second identical request
# --------------------------------------------------------------------------
print("\n== D1: two identical fetches, one transport call ==")
transport = CountingTransport()
first = ds.fetch("nasadem", BBOX, key=FAKE_KEY, transport=transport,
                 cache_dir=CACHE)
check("transport called once", transport.call_count, 1)
check_true("the file exists", os.path.isfile(first))

second = ds.fetch("nasadem", BBOX, key=FAKE_KEY, transport=transport,
                  cache_dir=CACHE)
check("the second fetch calls nothing", transport.call_count, 1)
check_true("...and returns the same file", second == first)

# floating-point noise below the rounding must not miss the cache
noisy = (BBOX[0] + 1e-12, BBOX[1], BBOX[2], BBOX[3])
third = ds.fetch("nasadem", noisy, key=FAKE_KEY, transport=transport,
                 cache_dir=CACHE)
check("noise below the rounding still hits the cache", transport.call_count, 1)
check_true("...same file again", third == first)

other = ds.fetch("nasadem", (10.0, 45.4, 10.1, 45.5), key=FAKE_KEY,
                 transport=transport, cache_dir=CACHE)
check("a different bbox does download", transport.call_count, 2)
check_true("...into a different file", other != first)
check("the cache key is stable across calls",
      1.0 if ds.cache_key("nasadem", BBOX) == ds.cache_key("nasadem", BBOX)
      else 0.0, 1.0)
check_true("cached_file finds it without a transport",
           ds.cached_file("nasadem", BBOX, cache_dir=CACHE) == first)

# --------------------------------------------------------------------------
# D2 - Google is refused, with a reason, without touching anything
# --------------------------------------------------------------------------
print("\n== D2: Google Elevation is refused ==")
before = sorted(os.listdir(CACHE))
transport_google = CountingTransport()
raised = None
try:
    ds.fetch("google_elevation", BBOX, transport=transport_google,
             cache_dir=CACHE)
except RasterError as exc:
    raised = exc
check_true("it raises RasterError", isinstance(raised, RasterError))
check("no transport call", transport_google.call_count, 0)
check_true("no file written", sorted(os.listdir(CACHE)) == before)
check_true("the message is Italian and explains the refusal",
           "non supportato" in raised.user_message.lower())
check_true("the refusal names the reason, not just a code",
           len(ds.ADAPTERS["google_elevation"].note) > 60)
check_true("the adapter is declared UNSUPPORTED",
           ds.ADAPTERS["google_elevation"].status == ds.UNSUPPORTED)
print("        {0}".format(raised.user_message[:96]))

# a PARTIAL adapter refuses too, and says what is missing
for partial_id in ("copernicus_glo30", "tinitaly", "terrarium"):
    transport_partial = CountingTransport()
    try:
        ds.fetch(partial_id, BBOX, key=FAKE_KEY, transport=transport_partial,
                 cache_dir=CACHE)
        check_true("{0} refuses".format(partial_id), False)
    except RasterError as exc:
        check_true("{0} refuses and names the gap".format(partial_id),
                   transport_partial.call_count == 0
                   and "non verificato" in exc.user_message.lower())

# --------------------------------------------------------------------------
# D3 - no key, no request
# --------------------------------------------------------------------------
print("\n== D3: a keyed adapter without a key ==")
app_settings.reset("dem/nasadem_key")
app_settings.reset("dem/copernicus_key")
check_true("the store starts without a key",
           not app_settings.has_secret("dem/nasadem_key"))

transport_nokey = CountingTransport()
raised = None
try:
    ds.fetch("nasadem", (11.0, 45.0, 11.1, 45.1), transport=transport_nokey,
             cache_dir=CACHE)
except RasterError as exc:
    raised = exc
check_true("it refuses", isinstance(raised, RasterError))
check("no transport call without a key", transport_nokey.call_count, 0)
check_true("the message is in Italian",
           "chiave api" in raised.user_message.lower())
check_true("the hint points at the settings",
           "impostazioni" in raised.formatted().lower())
print("        {0}".format(raised.formatted()[:110]))

check_true("secret() refuses to read a non-secret key",
           _refused(lambda: app_settings.secret("uav/h_agl_m")))

# --------------------------------------------------------------------------
# D4 - the key never reaches a human-readable surface
# --------------------------------------------------------------------------
print("\n== D4: the credential does not leak ==")
app_settings.set("dem/nasadem_key", FAKE_KEY)
check_true("the store returns it to the one caller that asks",
           app_settings.secret("dem/nasadem_key") == FAKE_KEY)

url = ds.ADAPTERS["nasadem"].url_builder(BBOX, FAKE_KEY)
check_true("the raw URL does contain it (that is the request)",
           FAKE_KEY in url)
masked = ds.mask_url(url)
check_true("mask_url removes it", FAKE_KEY not in masked)
check_true("...and keeps the rest readable",
           "demtype=NASADEM" in masked and "***" in masked)
print("        {0}".format(masked))

check_true("mask_text removes it from free text",
           FAKE_KEY not in ds.mask_text(
               "richiesta fallita: API_Key={0}&x=1".format(FAKE_KEY)))
check_true("settings.mask never shows the whole value",
           FAKE_KEY not in mask_secret(FAKE_KEY))
check_true("settings.all() masks the secret",
           FAKE_KEY not in str(app_settings.all()))

# an error carrying the URL must carry the masked one
failing = CountingTransport()


def exploding(url_arg):
    failing.call_count += 1
    raise OSError("HTTP 500 for {0}".format(url_arg))
    yield b""                                                   # pragma: no cover


raised = None
try:
    ds.fetch("nasadem", (12.0, 45.0, 12.1, 45.1), key=FAKE_KEY,
             transport=exploding, cache_dir=CACHE)
except RasterError as exc:
    raised = exc
check_true("a transport failure is reported", isinstance(raised, RasterError))
for surface, text in (("str(exc)", str(raised)),
                      ("user_message", raised.user_message),
                      ("formatted()", raised.formatted()),
                      ("repr", repr(raised))):
    check_true("the key is absent from {0}".format(surface),
               FAKE_KEY not in text)

logged = []
try:
    from geocad_uav.gui import dem_dialog as dd

    original = dd.QgsMessageLog.logMessage
    dd.QgsMessageLog.logMessage = staticmethod(
        lambda message, tag=None, level=None: logged.append(message))
    dd.log("richiesta: {0}".format(url))
    dd.QgsMessageLog.logMessage = original
    check_true("the dialog's logger masks what it writes",
               logged and FAKE_KEY not in logged[0])
except ImportError as exc:                                      # noqa: BLE001
    skip("the dialog's logger masks what it writes", str(exc))

# --------------------------------------------------------------------------
# D5 - a real GeoTIFF becomes a project layer
# --------------------------------------------------------------------------
print("\n== D5: the downloaded bytes become a valid raster ==")
tiff_transport = CountingTransport(payload=geotiff_bytes(), chunks=4)
path = ds.fetch("nasadem", (13.0, 45.0, 13.1, 45.1), key=FAKE_KEY,
                transport=tiff_transport, cache_dir=CACHE)
check("the whole payload was written", os.path.getsize(path),
      len(tiff_transport.payload))
check("more than one chunk was streamed",
      1.0 if tiff_transport.chunks > 1 else 0.0, 1.0)

layer = ds.raster_layer(path, "dem scaricato")
check_true("QgsRasterLayer is valid", layer.isValid())
check_true("the CRS is not empty", bool(layer.crs().authid()))
check_true("the CRS is the one the file declares",
           layer.crs() == QgsCoordinateReferenceSystem("EPSG:4326"))
print("        {0}, {1} x {2}".format(layer.crs().authid(), layer.width(),
                                      layer.height()))

QgsProject.instance().addMapLayer(layer)
check_true("the layer is in the project",
           layer.id() in QgsProject.instance().mapLayers())

garbage = ds.fetch("nasadem", (14.0, 45.0, 14.1, 45.1), key=FAKE_KEY,
                   transport=CountingTransport(payload=b"<html>error</html>"),
                   cache_dir=CACHE)
check_true("a service error page is rejected, not registered",
           _refused(lambda: ds.raster_layer(garbage, "spazzatura")))

# --------------------------------------------------------------------------
# D6 - cancelling leaves nothing behind
# --------------------------------------------------------------------------
print("\n== D6: cancel mid-write ==")
cancel_bbox = (15.0, 45.0, 15.1, 45.1)
dest = ds.cache_path("nasadem", cancel_bbox, cache_dir=CACHE)
feedback = Feedback(cancel_after=2)
slow = CountingTransport(payload=b"x" * 4096, chunks=16)
result = ds.fetch("nasadem", cancel_bbox, key=FAKE_KEY, transport=slow,
                  feedback=feedback, cache_dir=CACHE)
check_true("fetch reports the cancellation", result is None)
check_true("the transport had started", slow.call_count == 1)
check_true("the destination does not exist", not os.path.exists(dest))
check_true("no orphan .part file",
           not any(name.endswith(".part") for name in os.listdir(CACHE)))
check_true("a cancelled request is not cached",
           ds.cached_file("nasadem", cancel_bbox, cache_dir=CACHE) is None)

# and the retry after a cancel does download
retry = ds.fetch("nasadem", cancel_bbox, key=FAKE_KEY,
                 transport=CountingTransport(payload=b"complete"),
                 cache_dir=CACHE)
check_true("a retry completes", retry is not None and os.path.isfile(retry))
check("the retry wrote the whole payload", os.path.getsize(retry), 8)

# an empty response is a failure, not an empty DEM
empty_bbox = (16.0, 45.0, 16.1, 45.1)
check_true("an empty response is refused",
           _refused(lambda: ds.fetch(
               "nasadem", empty_bbox, key=FAKE_KEY,
               transport=CountingTransport(payload=b""), cache_dir=CACHE)))
check_true("...and leaves no file",
           ds.cached_file("nasadem", empty_bbox, cache_dir=CACHE) is None)

# --------------------------------------------------------------------------
# R1 - the UAV panel sees the new DEM with no change of its own
# --------------------------------------------------------------------------
print("\n== R1: the DEM combo picks the layer up unchanged ==")


class FakeIface:
    def __init__(self):
        self._window = QMainWindow()
        self._canvas = QgsMapCanvas()
        self._canvas.setDestinationCrs(
            QgsCoordinateReferenceSystem("EPSG:32632"))
        self._canvas.setExtent(QgsRectangle(500000, 4990000, 501000, 4991000))

    def mainWindow(self):                                       # noqa: N802
        return self._window

    def mapCanvas(self):                                        # noqa: N802
        return self._canvas

    def messageBar(self):                                       # noqa: N802
        return None


panel = UavPanel(FakeIface())
before_count = panel.dem_combo.count()
fresh = ds.raster_layer(path, "dem aggiunto dopo il pannello")
QgsProject.instance().addMapLayer(fresh)
check("the combo lists the new raster", panel.dem_combo.count(),
      before_count + 1)
check_true("the panel can select it",
           _selects(panel, fresh))

panel_source = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "gui", "uav_panel.py"), encoding="utf-8").read()
check_true("uav_panel imports nothing from the download side",
           "dem_source" not in panel_source
           and "dem_dialog" not in panel_source
           and "ADAPTERS" not in panel_source)
check_true("there is still one DEM combo",
           panel_source.count("QgsMapLayerComboBox()") == 1)
panel.teardown()

print("\n" + "=" * 78)
QgsProject.instance().removeAllMapLayers()
app_settings.reset("dem/nasadem_key")
app_settings.reset("dem/copernicus_key")
QGS.exitQgis()
if SKIPS:
    print("SKIPPED ({0}):".format(len(SKIPS)))
    for label, reason in SKIPS:
        print("   - {0}: {1}".format(label, reason))
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for name in FAILURES:
        print("   - {0}".format(name))
    sys.exit(1)
print("ALL CHECKS PASSED")
