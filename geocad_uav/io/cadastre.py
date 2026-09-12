"""
Cadastral parcel lookup against the Agenzia delle Entrate INSPIRE WFS.

**Status: VERIFIED.** Not from memory: the capabilities document, the feature
schema and a real GetFeature round trip were all opened while this module was
written, and the responses are kept in ``tests/fixtures`` so the parser is
tested against what the service actually sends rather than against what it is
supposed to send. What was verified, and what it corrected:

* endpoint ``owfs01.php``, WFS 2.0.0, licence CC BY 4.0, provider Agenzia
  delle Entrate -- Direzione Centrale Servizi Catastali;
* feature types ``CP:CadastralParcel`` (particelle) and ``CP:CadastralZoning``
  (fogli / mappe);
* the **only** CRS the service advertises is
  ``urn:ogc:def:crs:EPSG::6706`` -- RDN2008 geographic. Not 4258, not 4326.
  Coordinates come back **latitude first**, and a ``bbox`` in the 2.0.0 urn
  form is read the same way;
* the parcel carries exactly five attributes, and none of them is called
  Comune, Foglio or Particella::

      INSPIREID_LOCALID          IT.AGE.PLA.G478_025200.1016
      INSPIREID_NAMESPACE        IT.AGE.PLA.
      LABEL                      1016
      NATIONALCADASTRALREFERENCE G478_025200.1016
      ADMINISTRATIVEUNIT         G478

  so the three columns an operator wants are *derived*: the particella is
  ``LABEL``, the comune is the Belfiore code in ``ADMINISTRATIVEUNIT`` (the
  service does not publish the comune's name -- shipping a code-to-name table
  would be exactly the hardcoded data this plugin refuses), and the foglio is
  the middle segment of the reference, ``025200``, which the zoning layer
  publishes as ``LABEL = 252``.

Network is opt-in. Every other network path in this plugin is, and a CAD tool
that quietly called a government service on every commit would be a surprise;
``settings`` carries the switch and it is off until an operator turns it on.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

from ..core.errors import GeoCadError

#: The service, its version and the one CRS it speaks.
SERVICE_URL = ("https://wfs.cartografia.agenziaentrate.gov.it"
               "/inspire/wfs/owfs01.php")
SERVICE_VERSION = "2.0.0"
SERVICE_CRS = "EPSG:6706"
SERVICE_CRS_URN = "urn:ogc:def:crs:EPSG::6706"

TYPE_PARCEL = "CP:CadastralParcel"
TYPE_ZONING = "CP:CadastralZoning"

#: Attribute names, exactly as ``DescribeFeatureType`` declares them.
ATTR_LOCALID = "INSPIREID_LOCALID"
ATTR_NAMESPACE = "INSPIREID_NAMESPACE"
ATTR_LABEL = "LABEL"
ATTR_REFERENCE = "NATIONALCADASTRALREFERENCE"
ATTR_ZONING_REFERENCE = "NATIONALCADASTRALZONINGREFERENCE"
ATTR_ADMIN_UNIT = "ADMINISTRATIVEUNIT"

PARCEL_ATTRIBUTES = (ATTR_LOCALID, ATTR_NAMESPACE, ATTR_LABEL,
                     ATTR_REFERENCE, ATTR_ADMIN_UNIT)

#: Where the status comes from. URLs a human can open and check.
EVIDENCE = (
    SERVICE_URL + "?service=WFS&request=GetCapabilities&version=2.0.0",
    SERVICE_URL + "?service=WFS&version=2.0.0&request=DescribeFeatureType"
                  "&typeNames=CP:CadastralParcel",
)

#: Half-width of the query window around the point, in degrees. About 11 m at
#: Italian latitudes: wide enough to survive a metre of CRS wobble, narrow
#: enough that the parcel under the point is the one that comes back.
QUERY_HALF_SPAN_DEG = 1e-4

#: Never ask the service for more than this. A point lands in one parcel; a
#: bigger answer means the window was wrong, not that the data is richer.
MAX_FEATURES = 10

DEFAULT_TIMEOUT_S = 20.0


class CadastreError(GeoCadError):
    """The cadastral service could not answer."""

    default_message = "Interrogazione del catasto non riuscita."


@dataclass
class CadastralParcel:
    """One parcel, as the three columns an operator reads plus its provenance."""

    comune_code: str = ""
    foglio: str = ""
    particella: str = ""
    national_reference: str = ""
    inspire_id: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.comune_code or self.foglio or self.particella)

    def label(self) -> str:
        """``G478 / 252 / 1016``, the way a surveyor writes it."""
        return " / ".join(part for part in (self.comune_code, self.foglio,
                                            self.particella) if part)

    def as_attributes(self) -> dict:
        from .layer_factory import (CAT_COMUNE_FIELD,          # noqa: PLC0415
                                    CAT_FOGLIO_FIELD,
                                    CAT_PARTICELLA_FIELD)

        return {CAT_COMUNE_FIELD: self.comune_code,
                CAT_FOGLIO_FIELD: self.foglio,
                CAT_PARTICELLA_FIELD: self.particella}

    @classmethod
    def from_attributes(cls, values: dict) -> "CadastralParcel":
        reference = str(values.get(ATTR_REFERENCE) or "")
        return cls(
            comune_code=str(values.get(ATTR_ADMIN_UNIT) or ""),
            foglio=foglio_from_reference(reference),
            particella=str(values.get(ATTR_LABEL) or ""),
            national_reference=reference,
            inspire_id=str(values.get(ATTR_LOCALID) or ""))


# --------------------------------------------------------------------------
# Pure string work: no network, no QGIS, fully testable offline
# --------------------------------------------------------------------------

def foglio_from_reference(reference: str) -> str:
    """``G478_025200.1016`` -> ``252``.

    The middle segment is six characters: four for the sheet number, then the
    allegato and the sviluppo letters (``0`` when there is none). The zoning
    layer publishes the same sheet as ``LABEL = 252``, which is what this
    returns; a reference that does not have the expected shape comes back
    empty rather than half-parsed.
    """
    if not reference or "_" not in reference:
        return ""
    middle = reference.split("_", 1)[1].split(".", 1)[0]
    if len(middle) < 5 or not middle[:4].isdigit():
        return ""
    sheet = middle[:4].lstrip("0") or "0"
    suffix = "".join(ch for ch in middle[4:] if ch.isalpha())
    return sheet + suffix


def bbox_around(latitude: float, longitude: float,
                half_span_deg: float = QUERY_HALF_SPAN_DEG) -> str:
    """The ``bbox`` parameter for a point, **latitude first**.

    WFS 2.0.0 with a urn CRS uses the CRS's own axis order, and EPSG:6706 is
    latitude, longitude. Writing it the other way round returns parcels in
    the sea off Libya, quietly.
    """
    span = abs(float(half_span_deg))
    return "{0:.8f},{1:.8f},{2:.8f},{3:.8f},{4}".format(
        latitude - span, longitude - span,
        latitude + span, longitude + span, SERVICE_CRS_URN)


def build_query(latitude: float, longitude: float,
                type_name: str = TYPE_PARCEL,
                half_span_deg: float = QUERY_HALF_SPAN_DEG,
                count: int = MAX_FEATURES) -> str:
    """The full GetFeature URL for a point. No request is made here."""
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        raise CadastreError(
            "cadastral query at a non-finite point",
            user_message="Punto non valido per l'interrogazione catastale.")
    params = [
        ("service", "WFS"),
        ("version", SERVICE_VERSION),
        ("request", "GetFeature"),
        ("typeNames", type_name),
        ("count", str(max(1, min(int(count), MAX_FEATURES)))),
        ("bbox", bbox_around(latitude, longitude, half_span_deg)),
    ]
    return SERVICE_URL + "?" + urlencode(params)


def _service_exception(text: str) -> Optional[str]:
    match = re.search(r"<ServiceException[^>]*>(.*?)</ServiceException>",
                      text, re.S)
    if match is None:
        return None
    return re.sub(r"<!\[CDATA\[|\]\]>", "", match.group(1)).strip()


def parse_parcels(xml_text: str) -> "list[CadastralParcel]":
    """Every parcel in a GetFeature response, in the order it arrived.

    Parsed with a regular expression on purpose: the response is MapServer's
    own flat GML, the five elements are single-valued strings in a fixed
    namespace prefix, and pulling in an XML parser to read five tags would be
    a dependency for nothing. A malformed answer yields no parcels; a service
    exception is raised, because "no parcel here" and "the service refused
    the request" must not look the same to the caller.
    """
    if not xml_text:
        return []
    message = _service_exception(xml_text)
    if message:
        raise CadastreError(
            "WFS service exception: {0}".format(message),
            user_message="Il servizio catastale ha rifiutato la richiesta.",
            hint=message)

    parcels = []
    for block in re.findall(r"<CP:CadastralParcel\b.*?</CP:CadastralParcel>",
                            xml_text, re.S):
        values = {}
        for name in PARCEL_ATTRIBUTES:
            found = re.search(r"<CP:{0}>(.*?)</CP:{0}>".format(name), block,
                              re.S)
            values[name] = found.group(1).strip() if found else ""
        parcel = CadastralParcel.from_attributes(values)
        if not parcel.is_empty:
            parcels.append(parcel)
    return parcels


def parse_zoning_labels(xml_text: str) -> "dict[str, str]":
    """``{'G478_025200': '252'}`` from a CadastralZoning response.

    The authoritative sheet number, straight from the layer that publishes
    it, for when the derivation from the parcel reference is not trusted.
    """
    message = _service_exception(xml_text or "")
    if message:
        raise CadastreError(
            "WFS service exception: {0}".format(message),
            user_message="Il servizio catastale ha rifiutato la richiesta.",
            hint=message)
    out = {}
    for block in re.findall(r"<CP:CadastralZoning\b.*?</CP:CadastralZoning>",
                            xml_text or "", re.S):
        reference = re.search(
            r"<CP:{0}>(.*?)</CP:{0}>".format(ATTR_ZONING_REFERENCE), block,
            re.S)
        label = re.search(r"<CP:{0}>(.*?)</CP:{0}>".format(ATTR_LABEL), block,
                          re.S)
        if reference and label:
            out[reference.group(1).strip()] = label.group(1).strip()
    return out


def zoning_key(reference: str) -> str:
    """``G478_025200.1016`` -> ``G478_025200``, the sheet the parcel is on."""
    return reference.split(".", 1)[0] if reference else ""


# --------------------------------------------------------------------------
# Coordinates
# --------------------------------------------------------------------------

def to_service_point(x: float, y: float, source_crs):
    """Project CRS -> ``(latitude, longitude)`` in EPSG:6706.

    The project may be in any metric CRS; the service speaks one geographic
    one. The transform is QGIS's own, with the project's transform context,
    so a datum shift configured for the project is the datum shift used here.
    """
    from qgis.core import (QgsCoordinateReferenceSystem,        # noqa: PLC0415
                           QgsCoordinateTransform, QgsPointXY, QgsProject)

    target = QgsCoordinateReferenceSystem(SERVICE_CRS)
    if not target.isValid():
        raise CadastreError(
            "EPSG:6706 is not available in this QGIS installation",
            user_message="Il sistema di riferimento del catasto (EPSG:6706) "
                         "non e' disponibile.")
    if source_crs is None or not source_crs.isValid():
        raise CadastreError(
            "cadastral query from an unknown CRS",
            user_message="Sistema di riferimento del progetto non definito.")
    if source_crs == target:
        return float(y), float(x)
    transform = QgsCoordinateTransform(source_crs, target,
                                       QgsProject.instance())
    point = transform.transform(QgsPointXY(float(x), float(y)))
    return float(point.y()), float(point.x())


# --------------------------------------------------------------------------
# The request itself
# --------------------------------------------------------------------------

def urllib_transport(url: str, timeout: float = DEFAULT_TIMEOUT_S) -> str:
    """Fetch one URL and return its text. The only place this module opens a
    socket, and the seam a test replaces to stay offline."""
    import urllib.error                                        # noqa: PLC0415
    import urllib.request                                      # noqa: PLC0415

    request = urllib.request.Request(
        url, headers={"User-Agent": "GeoCadUavToolkit/1.0 (QGIS plugin)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise CadastreError(
            "cadastral WFS returned HTTP {0}".format(exc.code),
            user_message="Il servizio catastale ha risposto con un errore "
                         "({0}).".format(exc.code)) from exc
    except Exception as exc:                                    # noqa: BLE001
        raise CadastreError(
            "cadastral WFS unreachable: {0}".format(exc),
            user_message="Servizio catastale non raggiungibile.",
            hint="Controlla la connessione di rete.") from exc


def query_point(x: float, y: float, source_crs, transport=None,
                timeout: float = DEFAULT_TIMEOUT_S,
                resolve_foglio: bool = True) -> Optional[CadastralParcel]:
    """The parcel under one point, or ``None`` where the service knows none.

    ``None`` is a real answer: the Provinces of Trento and Bolzano keep their
    own cadastre and are not in this service at all, and a point at sea is in
    no parcel. It is not an error and must not be reported as one.
    """
    fetch = transport or urllib_transport
    latitude, longitude = to_service_point(x, y, source_crs)
    parcels = parse_parcels(fetch(build_query(latitude, longitude), timeout))
    if not parcels:
        return None
    parcel = parcels[0]
    if resolve_foglio:
        try:
            labels = parse_zoning_labels(
                fetch(build_query(latitude, longitude, TYPE_ZONING), timeout))
        except CadastreError:
            labels = {}                 # the derived sheet number stands
        published = labels.get(zoning_key(parcel.national_reference))
        if published:
            parcel.foglio = published
    return parcel


# --------------------------------------------------------------------------
# In the background, where a commit cannot wait for a government server
# --------------------------------------------------------------------------

def lookup_task(x: float, y: float, source_crs, on_result,
                transport=None, timeout: float = DEFAULT_TIMEOUT_S):
    """A ``QgsTask`` that asks the service and hands the answer back.

    Returns the task, already started on QGIS's own task manager. The commit
    that triggered it is long finished by the time it answers: a CAD tool
    must never block on a network round trip, and an operator drawing a
    second polygon must not be waiting on the first one's parcel.

    ``on_result(parcel_or_none, error_message)`` runs when the task finishes.
    """
    from qgis.core import QgsApplication, QgsTask               # noqa: PLC0415

    class _CadastreTask(QgsTask):
        def __init__(self):
            super().__init__("GeoCad UAV: catasto", QgsTask.Flag.CanCancel)
            self.parcel = None
            self.error = ""

        def run(self):
            try:
                self.parcel = query_point(x, y, source_crs, transport,
                                          timeout)
            except CadastreError as exc:
                self.error = exc.formatted()
                return False
            except Exception as exc:                            # noqa: BLE001
                self.error = str(exc)
                return False
            return True

        def finished(self, ok):
            try:
                on_result(self.parcel if ok else None, self.error)
            except Exception:                                   # noqa: BLE001
                pass            # a callback that throws must not kill QGIS

    task = _CadastreTask()
    QgsApplication.taskManager().addTask(task)
    return task


def describe() -> "list[str]":
    return [
        "CATASTO (Agenzia delle Entrate, WFS INSPIRE)",
        "  Servizio:  {0}".format(SERVICE_URL),
        "  Versione:  WFS {0}, licenza CC BY 4.0".format(SERVICE_VERSION),
        "  Sistema:   {0} (RDN2008 geografiche, latitudine per prima)".format(
            SERVICE_CRS),
        "  Livelli:   {0}, {1}".format(TYPE_PARCEL, TYPE_ZONING),
        "  Comune:    codice Belfiore (il servizio non pubblica il nome)",
        "  Foglio:    dal livello Mappe; in mancanza, dal riferimento",
        "  Particella: LABEL della particella",
    ]
