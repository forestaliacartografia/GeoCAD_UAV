"""
Elevation sources: one adapter table, one disk cache, one injectable transport.

The plugin already has exactly one elevation model, ``core.z.TerrainModel``,
and it is not touched here. This module only *obtains the file* that becomes a
``QgsRasterLayer``; from there the UAV tab picks it up in the DEM combo like
any other project raster. Nothing in this module reaches into the panels.

**Status is evidence, not optimism.** Each adapter carries the status of its
request schema, and only a ``VERIFIED`` one is allowed to build a request:

``VERIFIED``
    The request schema was read from the provider's own documentation, whose
    URL is in ``source_url``. The adapter builds and issues the request.
``PARTIAL``
    The service is documented and usable by a human, but one element of the
    request schema could not be read from an official source. The adapter
    refuses and says which element is missing. It does not guess a parameter
    name and it does not touch the network.
``UNSUPPORTED``
    Refused on purpose. No request is built, no byte is downloaded, no file is
    written.

**Credentials.** Keys live in ``settings.store`` and nowhere else: not in this
file, not in the tests, not in a log line. Every URL that can reach a human eye
goes through :func:`mask_url` first, which strips the key out of the query
string. The only thing that ever holds a key in this module is the local
variable inside :func:`fetch`.

**Cancellation.** Bytes land in ``<dest>.part`` and are renamed onto ``dest``
only after the last chunk. A cancelled download therefore leaves neither a
truncated DEM nor an orphan temporary file.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..core.errors import RasterError
from ..settings import settings as app_settings

# --------------------------------------------------------------------------
# Status vocabulary
# --------------------------------------------------------------------------

VERIFIED = "VERIFIED"
PARTIAL = "PARTIAL"
UNSUPPORTED = "UNSUPPORTED"

STATUS_LABELS = {
    VERIFIED: "Schema verificato",
    PARTIAL: "Schema non verificato per intero",
    UNSUPPORTED: "Non supportato",
}

#: Bytes per chunk when streaming a download. Small enough that a cancel is
#: felt immediately, large enough not to syscall per kilobyte.
CHUNK_BYTES = 64 * 1024

#: Cache key precision for the bounding box, in decimal degrees. 1e-6 deg is
#: about 0.1 m: finer than any DEM this plugin can use, so two requests that
#: differ only in floating-point noise share a cache entry.
BBOX_ROUNDING = 6


# --------------------------------------------------------------------------
# Credential hygiene
# --------------------------------------------------------------------------

#: Query parameters whose value is a credential, whatever the provider calls it.
SECRET_PARAMS = ("api_key", "apikey", "key", "token", "access_token")

_SECRET_IN_TEXT = re.compile(
    r"((?:api_?key|token|access_token)\s*[=:]\s*)([^\s&\"']+)", re.IGNORECASE)


def mask_url(url: str) -> str:
    """The URL with every credential parameter replaced by ``***``.

    Anything that logs, raises or shows a URL must call this. A key that
    reaches ``QgsMessageLog`` is a key in the user's log file forever.
    """
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return "***"
    query = [(name, "***" if name.lower() in SECRET_PARAMS else value)
             for name, value in parse_qsl(parts.query, keep_blank_values=True)]
    # safe="*" so the mask reads as *** in a message instead of %2A%2A%2A.
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(query, safe="*"), parts.fragment))


def mask_text(text: str) -> str:
    """Same idea for free text: an error string that quotes a request."""
    return _SECRET_IN_TEXT.sub(r"\1***", str(text))


# --------------------------------------------------------------------------
# The adapter table
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class DemAdapter:
    """One elevation source. A table row, not a class hierarchy."""

    id: str
    label: str
    status: str
    needs_key: bool = False
    #: ``settings.store`` key holding the credential, when one is needed.
    key_setting: str = ""
    #: Where the status comes from. Always a URL a human can open.
    source_url: str = ""
    #: What is verified, or what is missing, in the operator's language.
    note: str = ""
    licence: str = ""
    crs_authid: str = ""
    suffix: str = ".tif"
    #: ``(bbox, key) -> url``. Only a VERIFIED adapter may have one.
    url_builder: Optional[Callable] = field(default=None, repr=False)

    @property
    def usable(self) -> bool:
        return self.status == VERIFIED and self.url_builder is not None

    def describe(self) -> str:
        return "{0} [{1}] - {2}".format(self.label, self.status, self.note)


def _opentopography_url(demtype):
    """Build a globaldem request for one OpenTopography dataset code.

    Schema read from OpenTopography's own key announcement, which prints the
    request in full:
    https://opentopography.org/blog/introducing-api-keys-access-opentopography-global-datasets
    """
    def build(bbox, key):
        west, south, east, north = bbox
        query = urlencode([
            ("demtype", demtype),
            ("south", "{0:.6f}".format(south)),
            ("north", "{0:.6f}".format(north)),
            ("west", "{0:.6f}".format(west)),
            ("east", "{0:.6f}".format(east)),
            ("outputFormat", "GTiff"),
            ("API_Key", key or ""),
        ])
        return "https://portal.opentopography.org/API/globaldem?" + query

    return build


ADAPTERS = {a.id: a for a in (
    DemAdapter(
        id="nasadem",
        label="NASADEM (OpenTopography)",
        status=VERIFIED,
        needs_key=True,
        key_setting="dem/nasadem_key",
        source_url=("https://opentopography.org/blog/"
                    "introducing-api-keys-access-opentopography-global-datasets"),
        note=("Endpoint, nomi dei parametri e codice demtype=NASADEM presi "
              "dalla richiesta di esempio pubblicata da OpenTopography. "
              "Chiave gratuita dal proprio account; limite dichiarato 200 "
              "chiamate/24 h per uso accademico, 50 altrimenti."),
        licence="NASA, uso libero con citazione",
        crs_authid="EPSG:4326",
        url_builder=_opentopography_url("NASADEM"),
    ),
    DemAdapter(
        id="copernicus_glo30",
        label="Copernicus GLO-30 (OpenTopography)",
        status=PARTIAL,
        needs_key=True,
        key_setting="dem/copernicus_key",
        source_url="https://opentopography.org/developers",
        note=("Il dataset e' elencato fra quelli serviti dall'API e la "
              "chiave e' obbligatoria, ma il codice esatto del parametro "
              "demtype per il Copernicus 30 m non compare in nessuna pagina "
              "ufficiale leggibile: la documentazione interattiva e' una "
              "applicazione JavaScript e swagger.json risponde 404. "
              "L'adattatore non indovina il codice."),
        licence="Copernicus, uso libero con citazione",
        crs_authid="EPSG:4326",
    ),
    DemAdapter(
        id="tinitaly",
        label="TINITALY 1.1 (INGV)",
        status=PARTIAL,
        needs_key=False,
        source_url="https://tinitaly.pi.ingv.it/TINItaly_1_1/wcs?service=WCS&request=GetCapabilities",
        note=("Dal GetCapabilities pubblicato: WCS 2.0.1, endpoint "
              "tinitaly.pi.ingv.it/TINItaly_1_1/wcs, copertura TINItaly_DEM, "
              "licenza CC BY 4.0, UTM WGS84 32N a 10 m. Mancano le etichette "
              "degli assi richieste da GetCoverage 2.0.1 (DescribeCoverage su "
              "quel coverageId risponde 404), quindi la richiesta non e' "
              "costruibile senza inventarle."),
        licence="CC BY 4.0",
        crs_authid="EPSG:32632",
    ),
    DemAdapter(
        id="terrarium",
        label="Terrarium (tile PNG RGB)",
        status=PARTIAL,
        needs_key=False,
        source_url="https://github.com/tilezen/joerd/blob/master/docs/formats.md",
        note=("La codifica e' verificata sulla documentazione Tilezen: PNG in "
              "EPSG:3857, quota = (R*256 + G + B/256) - 32768. Quel documento "
              "non indica ne' lo schema di URL delle tile ne' la licenza del "
              "servizio, quindi non c'e' un endpoint da chiamare."),
        licence="dipende dal servizio che ospita le tile",
        crs_authid="EPSG:3857",
        suffix=".png",
    ),
    DemAdapter(
        id="google_elevation",
        label="Google Elevation API",
        status=UNSUPPORTED,
        needs_key=False,
        source_url="",
        note=("Rifiutato per scelta. Non esiste un download documentato di "
              "raster DEM: l'API restituisce quote punto per punto e le "
              "condizioni d'uso vietano di ricostruirci un modello. Il "
              "plugin non estrae dati per aggirare quel limite."),
    ),
)}

#: Presentation order: what works first, what is refused last.
ADAPTER_ORDER = ("nasadem", "copernicus_glo30", "tinitaly", "terrarium",
                 "google_elevation")


def adapter(adapter_id: str) -> DemAdapter:
    """One adapter by id. Raises loudly on a typo rather than returning None."""
    try:
        return ADAPTERS[adapter_id]
    except KeyError:
        raise KeyError(
            "unknown DEM adapter {0!r}; available: {1}".format(
                adapter_id, ", ".join(ADAPTER_ORDER))) from None


def status_table():
    """``[(id, label, status, note, source_url)]`` for the dialog and the docs."""
    return [(a.id, a.label, a.status, a.note, a.source_url)
            for a in (ADAPTERS[key] for key in ADAPTER_ORDER)]


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def default_cache_dir() -> str:
    """``<QGIS profile>/geocad_uav/dem_cache``, or the temp dir headless."""
    try:
        from qgis.core import QgsApplication                    # noqa: PLC0415

        root = QgsApplication.qgisSettingsDirPath()
    except Exception:                                           # noqa: BLE001
        root = ""
    if not root:
        import tempfile                                         # noqa: PLC0415

        root = tempfile.gettempdir()
    return os.path.join(root, "geocad_uav", "dem_cache")


def cache_key(adapter_id: str, bbox, zoom: int = 0) -> str:
    """Stable identity of one request: adapter, rounded bbox, zoom."""
    west, south, east, north = (round(float(v), BBOX_ROUNDING) for v in bbox)
    raw = "{0}|{1}|{2}|{3}|{4}|{5}".format(adapter_id, west, south, east,
                                           north, int(zoom))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def cache_path(adapter_id: str, bbox, zoom: int = 0,
               cache_dir: Optional[str] = None) -> str:
    spec = adapter(adapter_id)
    directory = cache_dir or default_cache_dir()
    return os.path.join(directory, "{0}_{1}{2}".format(
        adapter_id, cache_key(adapter_id, bbox, zoom), spec.suffix))


def cached_file(adapter_id: str, bbox, zoom: int = 0,
                cache_dir: Optional[str] = None) -> Optional[str]:
    """The cached file for this request, or None. Never touches the network."""
    path = cache_path(adapter_id, bbox, zoom, cache_dir)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    return None


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------

def urllib_transport(url: str, timeout: float = 120.0) -> Iterable[bytes]:
    """The real transport: chunks of the response body.

    Injected rather than called directly so the tests exercise every branch of
    :func:`fetch` without a socket. It is a generator, so nothing is requested
    until :func:`fetch` starts consuming -- a cancel before the first chunk
    costs one connection, not one download.
    """
    from urllib.request import urlopen                          # noqa: PLC0415

    with urlopen(url, timeout=timeout) as response:             # noqa: S310
        while True:
            chunk = response.read(CHUNK_BYTES)
            if not chunk:
                return
            yield chunk


# --------------------------------------------------------------------------
# The one entry point
# --------------------------------------------------------------------------

def resolve_key(spec: DemAdapter) -> str:
    """The credential for this adapter, from the settings store only."""
    if not spec.needs_key:
        return ""
    return app_settings.secret(spec.key_setting)


def fetch(adapter_id: str, bbox, dest_path: Optional[str] = None,
          key: Optional[str] = None, transport: Callable = urllib_transport,
          feedback=None, zoom: int = 0,
          cache_dir: Optional[str] = None) -> Optional[str]:
    """Obtain the DEM for ``bbox`` and return the file path.

    ``bbox`` is ``(west, south, east, north)`` in the adapter's own CRS --
    degrees for the OpenTopography datasets. A cached file short-circuits the
    whole function, so the transport is never called twice for the same
    request. Returns None when the operator cancelled; raises ``RasterError``
    with an Italian message for anything else.
    """
    spec = adapter(adapter_id)

    if spec.status == UNSUPPORTED:
        raise RasterError(
            "adapter {0} is unsupported by design".format(spec.id),
            user_message="{0}: non supportato. {1}".format(spec.label,
                                                           spec.note),
            hint="Scegli un'altra fonte di elevazione.")

    if not spec.usable:
        raise RasterError(
            "adapter {0} has status {1}".format(spec.id, spec.status),
            user_message="{0}: schema della richiesta non verificato per "
                         "intero, quindi il download e' disattivato. "
                         "{1}".format(spec.label, spec.note),
            hint="Fonte: {0}".format(spec.source_url))

    destination = dest_path or cache_path(spec.id, bbox, zoom, cache_dir)

    existing = destination if (os.path.isfile(destination)
                               and os.path.getsize(destination) > 0) else None
    if existing:
        return existing

    credential = key if key is not None else resolve_key(spec)
    if spec.needs_key and not str(credential).strip():
        raise RasterError(
            "no API key for {0}".format(spec.id),
            user_message="{0} richiede una chiave API, che non e' "
                         "impostata.".format(spec.label),
            hint="Inseriscila in Impostazioni; non viene mai scritta nei log "
                 "ne' nei messaggi di errore.")

    url = spec.url_builder(bbox, credential)
    directory = os.path.dirname(destination)
    if directory:
        os.makedirs(directory, exist_ok=True)

    partial = destination + ".part"
    written = 0
    try:
        with open(partial, "wb") as handle:
            for chunk in transport(url):
                if feedback is not None and feedback.isCanceled():
                    handle.close()
                    _discard(partial)
                    return None
                handle.write(chunk)
                written += len(chunk)
                if feedback is not None and hasattr(feedback, "setProgress"):
                    feedback.setProgress(min(99.0, written / 1024.0))
    except RasterError:
        _discard(partial)
        raise
    except Exception as exc:                                    # noqa: BLE001
        _discard(partial)
        raise RasterError(
            "download failed for {0}: {1}".format(spec.id,
                                                  mask_text(str(exc))),
            user_message="Download del DEM non riuscito da {0}.".format(
                spec.label),
            hint=mask_text(str(exc))) from None

    if written == 0:
        _discard(partial)
        raise RasterError(
            "empty response from {0}".format(mask_url(url)),
            user_message="{0} non ha restituito dati per quest'area.".format(
                spec.label),
            hint="Controlla che l'area ricada nella copertura del dataset.")

    if feedback is not None and feedback.isCanceled():
        _discard(partial)
        return None

    os.replace(partial, destination)
    return destination


def _discard(path: str) -> None:
    """Remove a partial file, and never fail while doing it."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------
# From file to layer
# --------------------------------------------------------------------------

def raster_layer(path: str, name: str = ""):
    """A ``QgsRasterLayer`` over a downloaded file. The caller registers it.

    Adding it to the project is the caller's decision, exactly as in
    ``io.layer_factory``: this module builds, it does not mutate the project.
    """
    from qgis.core import QgsRasterLayer                        # noqa: PLC0415

    layer = QgsRasterLayer(path, name or os.path.basename(path), "gdal")
    if not layer.isValid():
        raise RasterError(
            "GDAL cannot open {0}".format(path),
            user_message="Il file scaricato non e' un raster leggibile.",
            hint="Il servizio potrebbe aver restituito un messaggio di errore "
                 "invece dei dati.")
    return layer
