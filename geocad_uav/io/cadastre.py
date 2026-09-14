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
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

from ..core.errors import GeoCadError, swallow
from .net import require_web_url

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

#: A project area can genuinely touch many parcels, so the area query has a
#: far larger ceiling -- but still a ceiling, because an operator who drew a
#: box round a province should get a refusal, not a download.
MAX_AREA_FEATURES = 2000

#: Coverage below which the answer is called PARTIAL: the project reaches
#: ground the service has no parcel for (Trento and Bolzano, the sea, a gap
#: in the map).
FULL_COVERAGE = 0.999

DEFAULT_TIMEOUT_S = 20.0

#: Largest side, in degrees, of a bounding box sent to the service in one
#: request. Overridden by the ``cadastre/tile_span_deg`` setting. About 2 km
#: at Italian latitudes: not a guess about parcel density, just a first cut
#: small enough that most projects need one request and big ones start
#: divided instead of starting truncated.
DEFAULT_TILE_SPAN_DEG = 0.02

#: How many times a truncated tile may be quartered before the answer is
#: reported partial. Four levels turn one tile into at most 256.
MAX_TILE_DEPTH = 4

#: How many tiles one query may send in total. A box drawn round a region
#: is a refusal, not a download.
MAX_TILES = 400

#: Attempts per tile. A government WFS drops a connection now and then, and
#: one retry costs a second and saves a whole query.
TILE_ATTEMPTS = 2


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

    def comune(self):
        """The comune behind the Belfiore code, resolved locally.

        The service publishes the code and nothing else; the name comes from
        the table shipped in ``data/``. An unknown code comes back as a
        comune that says so rather than as an exception: this runs inside a
        background task.
        """
        from . import belfiore                                 # noqa: PLC0415

        return belfiore.resolve(self.comune_code)

    def label(self) -> str:
        """``G478 / 252 / 1016``, the way a surveyor writes it."""
        return " / ".join(part for part in (self.comune_code, self.foglio,
                                            self.particella) if part)

    def as_attributes(self) -> dict:
        """The three columns as they go into an attribute table.

        The comune is the *name*, resolved locally through the Belfiore
        table: a column headed "Comune" reading G478 tells an operator
        nothing, and the code they might want is still in the label and in
        the parametric record. An unresolved code falls back to itself,
        which is better than an empty cell.
        """
        from .layer_factory import (CAT_COMUNE_FIELD,          # noqa: PLC0415
                                    CAT_FOGLIO_FIELD,
                                    CAT_PARTICELLA_FIELD)

        name = ""
        try:
            resolved = self.comune()
            name = resolved.label() if resolved is not None else ""
        except Exception:                                      # noqa: BLE001
            name = ""                   # a register that will not load
        return {CAT_COMUNE_FIELD: name or self.comune_code,
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


def build_area_query(south: float, west: float, north: float, east: float,
                     type_name: str = TYPE_PARCEL,
                     count: int = MAX_AREA_FEATURES) -> str:
    """GetFeature over a whole bounding box, in the service's axis order."""
    for value in (south, west, north, east):
        if not math.isfinite(value):
            raise CadastreError(
                "cadastral query over a non-finite box",
                user_message="Area non valida per l'interrogazione catastale.")
    bbox = "{0:.8f},{1:.8f},{2:.8f},{3:.8f},{4}".format(
        south, west, north, east, SERVICE_CRS_URN)
    params = [
        ("service", "WFS"),
        ("version", SERVICE_VERSION),
        ("request", "GetFeature"),
        ("typeNames", type_name),
        ("count", str(max(1, min(int(count), MAX_AREA_FEATURES)))),
        ("bbox", bbox),
    ]
    return SERVICE_URL + "?" + urlencode(params)


def service_bbox(geometry, source_crs, margin_deg: float = 0.0):
    """``(south, west, north, east)`` of a geometry, in EPSG:6706."""
    from qgis.core import (QgsCoordinateReferenceSystem,        # noqa: PLC0415
                           QgsCoordinateTransform, QgsProject)

    if geometry is None or geometry.isEmpty():
        raise CadastreError(
            "cadastral query on an empty geometry",
            user_message="Nessuna area su cui interrogare il catasto.")
    target = QgsCoordinateReferenceSystem(SERVICE_CRS)
    if source_crs is None or not source_crs.isValid():
        raise CadastreError(
            "cadastral query from an unknown CRS",
            user_message="Sistema di riferimento del progetto non definito.")
    box = geometry.boundingBox()
    if source_crs != target:
        transform = QgsCoordinateTransform(source_crs, target,
                                           QgsProject.instance())
        box = transform.transformBoundingBox(box)
    margin = abs(float(margin_deg))
    return (box.yMinimum() - margin, box.xMinimum() - margin,
            box.yMaximum() + margin, box.xMaximum() + margin)


def tile_span_deg() -> float:
    """The configured first-cut tile side, or the default."""
    try:
        from ..settings import settings as _settings            # noqa: PLC0415

        value = float(_settings.get("cadastre/tile_span_deg") or 0.0)
    except Exception:                                           # noqa: BLE001
        value = 0.0
    return value if value > 0.0 else DEFAULT_TILE_SPAN_DEG


def split_bbox(box, max_span_deg: float) -> list:
    """``(south, west, north, east)`` cut into tiles no wider than the span.

    Returns the box itself when it already fits. The grid is regular and
    covers the box exactly: tiles share edges, and a parcel on an edge comes
    back from both -- which is what the deduplication is for.
    """
    south, west, north, east = (float(v) for v in box)
    span = float(max_span_deg)
    if span <= 0.0:
        return [(south, west, north, east)]
    # The epsilon matters: 0.10 / 0.05 is 2.0000000000000004 in binary
    # floating point, and ceil() of that is three rows where two do. Half a
    # million extra requests a year start here.
    rows = max(1, int(math.ceil((north - south) / span - 1e-9)))
    cols = max(1, int(math.ceil((east - west) / span - 1e-9)))
    if rows * cols <= 1:
        return [(south, west, north, east)]
    dy = (north - south) / rows
    dx = (east - west) / cols
    return [(south + r * dy, west + c * dx,
             south + (r + 1) * dy, west + (c + 1) * dx)
            for r in range(rows) for c in range(cols)]


def quarter_bbox(box) -> list:
    """One box into four. What a truncated answer is answered with."""
    south, west, north, east = (float(v) for v in box)
    mid_y = 0.5 * (south + north)
    mid_x = 0.5 * (west + east)
    return [(south, west, mid_y, mid_x), (south, mid_x, mid_y, east),
            (mid_y, west, north, mid_x), (mid_y, mid_x, north, east)]


def parcel_key(parcel, geometry=None):
    """What makes two parcels the same parcel.

    The national reference when the service published one -- it is unique
    and it is what the register is keyed on. Failing that, the triple an
    operator reads. Failing even that, the geometry itself, so a feature
    with no identity at all is still not counted twice.
    """
    reference = (parcel.national_reference or "").strip()
    if reference:
        return ("ref", reference)
    triple = (parcel.comune_code or "", parcel.foglio or "",
              parcel.particella or "")
    if any(triple):
        return ("cfp",) + triple
    if geometry is not None and not geometry.isEmpty():
        return ("wkb", geometry.asWkb().toHex().data().decode("ascii"))
    return ("none", id(parcel))


def dedup_pairs(pairs) -> list:
    """Parcel/geometry pairs with each parcel once, in the order seen.

    A tile query returns whole geometries, not clipped ones, so a parcel on
    a tile boundary arrives twice identically. Where two entries do differ,
    the one with the larger geometry is kept: a truncated ring is a worse
    answer than a whole one.
    """
    out = {}
    order = []
    for parcel, geometry in pairs:
        key = parcel_key(parcel, geometry)
        if key not in out:
            out[key] = (parcel, geometry)
            order.append(key)
            continue
        _kept, kept_geom = out[key]
        if geometry is not None and (kept_geom is None
                                     or geometry.area() > kept_geom.area()):
            out[key] = (parcel, geometry)
    return [out[key] for key in order]


def fetch_parcels(box, fetch, timeout: float = DEFAULT_TIMEOUT_S,
                  type_name: str = TYPE_PARCEL, parser=None,
                  span_deg: Optional[float] = None, progress=None):
    """Every feature in a bounding box, tiling around the service's ceiling.

    ``fetch`` is the transport; ``parser`` turns one response into a list
    (``parse_parcel_geometries`` by default). Returns ``(items, warnings)``:
    a tile the service would not answer for is a warning and the rest of the
    query goes on, because a cadastral answer missing one tile is worth more
    than no answer at all -- as long as it says so.
    """
    parse = parser or parse_parcel_geometries
    span = tile_span_deg() if span_deg is None else float(span_deg)
    pending = [(tile, 0) for tile in split_bbox(box, span)]
    if len(pending) > MAX_TILES:
        raise CadastreError(
            "cadastral query over {0} tiles".format(len(pending)),
            user_message="Area troppo estesa per l'interrogazione catastale.",
            hint="Riduci l'area di progetto o interrogala a blocchi.")

    items = []
    warnings = []
    sent = 0
    answered = 0
    first_error = None
    total = len(pending)
    while pending:
        tile, depth = pending.pop(0)
        if sent >= MAX_TILES:
            warnings.append(
                "Interrogazione interrotta a {0} riquadri: il risultato "
                "potrebbe essere incompleto.".format(MAX_TILES))
            break
        body = None
        last = None
        for _attempt in range(TILE_ATTEMPTS):
            try:
                body = fetch(build_area_query(tile[0], tile[1], tile[2],
                                              tile[3], type_name), timeout)
                break
            except CadastreError as exc:
                last = exc
            except Exception as exc:                            # noqa: BLE001
                last = CadastreError(str(exc),
                                     user_message="Interrogazione catastale "
                                                  "non riuscita.")
        sent += 1
        if progress is not None:
            try:
                progress(min(1.0, sent / float(max(total, 1))))
            except Exception as exc:                            # noqa: BLE001
                swallow(exc, "cadastre: progress callback")
        if body is None:
            if first_error is None:
                first_error = last
            warnings.append(
                "Riquadro {0:.4f},{1:.4f} non interrogato: {2}".format(
                    tile[0], tile[1],
                    last.user_message if last is not None else "errore"))
            continue
        answered += 1
        found = parse(body)
        # The provider stopped at its own ceiling: that is a truncation, not
        # a count. Quarter the tile and ask again.
        if len(found) >= MAX_AREA_FEATURES and depth < MAX_TILE_DEPTH:
            pending.extend((piece, depth + 1) for piece in quarter_bbox(tile))
            total += 4
            continue
        if len(found) >= MAX_AREA_FEATURES:
            warnings.append(
                "Un riquadro resta al limite di {0} particelle dopo {1} "
                "suddivisioni: il risultato potrebbe essere incompleto."
                .format(MAX_AREA_FEATURES, MAX_TILE_DEPTH))
        items.extend(found)
    # Not one tile answered: that is the service being unreachable, and it
    # must stay distinguishable from the service answering "no parcels
    # here". A partial answer is a warning; no answer is an error.
    if answered == 0 and first_error is not None:
        raise first_error
    return items, warnings


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


def _rings(block: str) -> "list[list]":
    """Every ``posList`` in one Polygon block, exterior first.

    The coordinates arrive **latitude first** -- EPSG:6706 axis order, the
    same order the bbox is written in -- so each pair is swapped on the way
    into a ``QgsPointXY``, which is (x, y) and therefore (lon, lat). Getting
    this backwards puts Italian parcels in the Indian Ocean, and does it
    quietly.
    """
    from qgis.core import QgsPointXY                           # noqa: PLC0415

    rings = []
    for raw in re.findall(r"<gml:posList[^>]*>(.*?)</gml:posList>", block,
                          re.S):
        numbers = raw.split()
        if len(numbers) < 8 or len(numbers) % 2:
            continue
        points = []
        for index in range(0, len(numbers), 2):
            try:
                latitude = float(numbers[index])
                longitude = float(numbers[index + 1])
            except ValueError:
                points = []
                break
            points.append(QgsPointXY(longitude, latitude))
        if len(points) >= 4:
            rings.append(points)
    return rings


def parse_parcel_geometries(xml_text: str) -> "list[tuple]":
    """``[(CadastralParcel, QgsGeometry), ...]`` in the service CRS.

    One geometry per feature, multipart when the feature has several
    polygons -- a parcel really can be in two pieces, and collapsing it to
    the first one would lose half its surface.
    """
    from qgis.core import QgsGeometry                          # noqa: PLC0415

    message = _service_exception(xml_text or "")
    if message:
        raise CadastreError(
            "WFS service exception: {0}".format(message),
            user_message="Il servizio catastale ha rifiutato la richiesta.",
            hint=message)

    out = []
    for block in re.findall(r"<CP:CadastralParcel\b.*?</CP:CadastralParcel>",
                            xml_text or "", re.S):
        values = {}
        for name in PARCEL_ATTRIBUTES:
            found = re.search(r"<CP:{0}>(.*?)</CP:{0}>".format(name), block,
                              re.S)
            values[name] = found.group(1).strip() if found else ""
        parcel = CadastralParcel.from_attributes(values)
        if parcel.is_empty:
            continue

        parts = []
        for polygon in re.findall(r"<gml:Polygon\b.*?</gml:Polygon>", block,
                                  re.S):
            rings = _rings(polygon)
            if not rings:
                continue
            piece = QgsGeometry.fromPolygonXY(rings)
            if piece is not None and not piece.isEmpty():
                parts.append(piece)
        if not parts:
            continue
        geometry = parts[0] if len(parts) == 1 else QgsGeometry.collectGeometry(
            parts)
        if geometry is None or geometry.isEmpty():
            continue
        if not geometry.isGeosValid():
            repaired = geometry.makeValid()
            if repaired is not None and not repaired.isEmpty():
                geometry = repaired
        out.append((parcel, geometry))
    return out


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

    require_web_url(url)
    request = urllib.request.Request(
        url, headers={"User-Agent": "GeoCadUavToolkit/1.0 (QGIS plugin)"})
    try:
        # The scheme was checked above, which is what B310 asks for.
        with urllib.request.urlopen(                             # nosec B310
                request, timeout=timeout) as response:
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
            except Exception as exc:                            # noqa: BLE001
                swallow(exc, "cadastre: result callback")

    task = _CadastreTask()
    QgsApplication.taskManager().addTask(task)
    return task


# --------------------------------------------------------------------------
# The project's cadastral situation
# --------------------------------------------------------------------------

#: Every parcel the service knows was found, and they cover the project.
STATUS_OK = "OK"
#: Parcels were found, but part of the project is on ground the service has
#: no parcel for. Both provinces of Trento and Bolzano keep their own
#: cadastre and are not in this service at all.
STATUS_PARTIAL = "PARZIALE"
#: The service answered, and there is no parcel here.
STATUS_UNAVAILABLE = "NON DISPONIBILE"
#: The service could not be asked, or could not be understood.
STATUS_ERROR = "ERRORE"

STATUS_LABELS = {
    STATUS_OK: "Dati catastali completi",
    STATUS_PARTIAL: "Dati catastali parziali",
    STATUS_UNAVAILABLE: "Nessuna particella catastale sull'area",
    STATUS_ERROR: "Interrogazione catastale non riuscita",
}

M2_PER_HA = 10_000.0


@dataclass
class ParcelShare:
    """One parcel, and how much of it the project takes."""

    parcel: CadastralParcel
    comune: object = None                       # belfiore.Comune
    parcel_area_m2: float = 0.0
    intersection_area_m2: float = 0.0
    #: The parcel and the part of it the project takes, both in the CRS the
    #: caller asked with -- the project's own. Kept because a cadastral
    #: answer an operator cannot see on the map is a table, not a result.
    geometry: object = None                     # QgsGeometry
    intersection: object = None                 # QgsGeometry
    #: The whole project's area, in the same metric CRS. Carried here so a
    #: share can answer both questions on its own: what it takes of the
    #: parcel, and what it is of the project.
    project_area_m2: float = 0.0

    @property
    def percent_of_project(self) -> float:
        """How much of the project lies on this parcel, as a percentage.

        The companion of :attr:`percent_of_parcel` and not a rewording of
        it: the denominators are different, and a parcel can be 3 per cent
        of the project while the project is 100 per cent of the parcel.
        """
        if self.project_area_m2 <= 0.0:
            return 0.0
        return 100.0 * self.intersection_area_m2 / self.project_area_m2

    @property
    def percent_of_parcel(self) -> float:
        """How much of the parcel the project covers, as a percentage.

        ``area(intersection) / area(parcel) * 100``, both measured in the
        metric CRS the interpolation resolved -- never in degrees, where a
        hectare in Sicily and a hectare in Sudtirol are different numbers.
        """
        if self.parcel_area_m2 <= 0.0:
            return 0.0
        return 100.0 * self.intersection_area_m2 / self.parcel_area_m2

    @property
    def comune_name(self) -> str:
        return self.comune.label() if self.comune is not None else ""

    def as_row(self) -> dict:
        return {
            "comune": self.comune_name,
            "belfiore": self.parcel.comune_code,
            "foglio": self.parcel.foglio,
            "particella": self.parcel.particella,
            "riferimento": self.parcel.national_reference,
            "superficie_catastale_m2": round(self.parcel_area_m2, 2),
            "superficie_interessata_m2": round(self.intersection_area_m2, 2),
            # Two percentages, two denominators, both kept.
            "percentuale": round(self.percent_of_parcel, 3),
            "percentuale_particella": round(self.percent_of_parcel, 3),
            "percentuale_progetto": round(self.percent_of_project, 3),
        }


@dataclass
class CadastralResult:
    """What the cadastre says about one project area."""

    shares: list = field(default_factory=list)
    project_area_m2: float = 0.0
    covered_area_m2: float = 0.0
    status: str = STATUS_UNAVAILABLE
    message: str = ""
    work_crs_authid: str = ""
    warnings: list = field(default_factory=list)

    # -- what it contains --------------------------------------------------

    @property
    def n_parcels(self) -> int:
        return len(self.shares)

    @property
    def cadastral_area_m2(self) -> float:
        """Total surface of every parcel touched, whole parcels."""
        return sum(share.parcel_area_m2 for share in self.shares)

    @property
    def covered_fraction(self) -> float:
        if self.project_area_m2 <= 0.0:
            return 0.0
        return self.covered_area_m2 / self.project_area_m2

    @property
    def is_usable(self) -> bool:
        return self.status in (STATUS_OK, STATUS_PARTIAL)

    def belfiore_codes(self) -> list:
        seen = []
        for share in self.shares:
            code = share.parcel.comune_code
            if code and code not in seen:
                seen.append(code)
        return seen

    def comuni(self) -> list:
        """One entry per comune, in order of surface taken."""
        by_code = {}
        for share in self.shares:
            code = share.parcel.comune_code
            by_code.setdefault(code, []).append(share)
        ordered = sorted(
            by_code.items(),
            key=lambda item: -sum(s.intersection_area_m2 for s in item[1]))
        return [(code, group[0].comune, group) for code, group in ordered]

    def by_comune(self) -> dict:
        """``{belfiore: [ParcelShare, ...]}`` -- every parcel kept.

        A project across two comuni keeps both, and every parcel of each:
        overwriting with the last feature found is exactly the bug this
        structure exists to prevent.
        """
        out = {}
        for share in self.shares:
            out.setdefault(share.parcel.comune_code, []).append(share)
        return out

    def by_foglio(self) -> dict:
        """``{belfiore: {foglio: [ParcelShare, ...]}}`` -- nothing collapsed.

        The shape a cadastral reading actually has. Two comuni may both have
        a "foglio 12" holding a "particella 45" and they are different
        ground: the belfiore code is the outer key precisely so that those
        never meet.
        """
        out = {}
        for share in self.shares:
            code = share.parcel.comune_code
            foglio = share.parcel.foglio or ""
            out.setdefault(code, {}).setdefault(foglio, []).append(share)
        return out

    def fogli(self, code: str) -> list:
        """The sheets of one comune, ordered by the surface taken."""
        groups = self.by_foglio().get(code, {})
        ordered = sorted(
            groups.items(),
            key=lambda item: -sum(s.intersection_area_m2 for s in item[1]))
        return [(foglio, group) for foglio, group in ordered]

    def comune_rows(self) -> list:
        """One row per comune: what the project takes there, in total.

        The companion of :meth:`rows`, which is one row per parcel. Together
        they are the two readings a cadastral annex carries -- the detail and
        the summary -- and they come from the same shares, so they cannot
        disagree.
        """
        out = []
        for code, comune, group in self.comuni():
            cadastral = sum(share.parcel_area_m2 for share in group)
            taken = sum(share.intersection_area_m2 for share in group)
            out.append({
                "comune": (comune.label() if comune is not None else code),
                "belfiore": code,
                "fogli": len({share.parcel.foglio or "" for share in group}),
                "particelle": len(group),
                "superficie_catastale_m2": round(cadastral, 2),
                "superficie_interessata_m2": round(taken, 2),
                "percentuale": round(100.0 * taken / cadastral, 3)
                if cadastral > 0 else 0.0,
                "quota_progetto": round(
                    100.0 * taken / self.covered_area_m2, 3)
                if self.covered_area_m2 > 0 else 0.0,
            })
        return out

    def comune_labels(self) -> list:
        """Every comune the project touches, named, in order of surface."""
        return [(comune.label() if comune is not None else code)
                for code, comune, _group in self.comuni()]

    def shares_of(self, code: str) -> list:
        """The parcels of one comune, in the order :meth:`rows` lists them."""
        return [share for share in self.shares
                if share.parcel.comune_code == code]

    # -- what the project stores -------------------------------------------

    def as_attributes(self) -> dict:
        """One row summarising the project, for the panel and the report."""
        comuni = self.comuni()
        first = comuni[0] if comuni else None
        labels = self.comune_labels()
        return {
            # The comune with the most ground in the project, and -- since
            # a project can sit across several -- the whole list beside it.
            # A single "comune" field that silently named one of three is
            # the loss this pair exists to prevent.
            "comune": (first[1].label() if first and first[1] is not None
                       else ""),
            "comuni_elenco": "; ".join(labels),
            "belfiore": first[0] if first else "",
            "belfiore_elenco": "; ".join(self.belfiore_codes()),
            "comuni": len(comuni),
            "particelle": self.n_parcels,
            "superficie_catastale_ha": round(
                self.cadastral_area_m2 / M2_PER_HA, 4),
            "superficie_interessata_ha": round(
                self.covered_area_m2 / M2_PER_HA, 4),
            "percentuale_coperta": round(100.0 * self.covered_fraction, 3),
            "stato": self.status,
        }

    def rows(self) -> list:
        return [share.as_row() for share in self.shares]

    #: Column order of :meth:`export_rows`, and of the file it writes.
    EXPORT_COLUMNS = ("comune", "belfiore", "foglio", "particella",
                      "riferimento", "superficie_catastale_m2",
                      "superficie_interessata_m2", "percentuale_particella",
                      "percentuale_progetto", "geometria_wkt",
                      "intersezione_wkt")

    def export_rows(self, with_geometry: bool = True) -> list:
        """One row per parcel, with everything the model holds.

        The compact cell in the CAD table and the "(+N)" that ends a long
        one are ways of showing a result, never of storing it: this is what
        the result actually contains, and it is what an export writes.
        """
        rows = []
        for share in self.shares:
            row = dict(share.as_row())
            row["percentuale_particella"] = row["percentuale"]
            if with_geometry:
                row["geometria_wkt"] = (
                    share.geometry.asWkt() if share.geometry is not None
                    else "")
                row["intersezione_wkt"] = (
                    share.intersection.asWkt()
                    if share.intersection is not None else "")
            else:
                row["geometria_wkt"] = row["intersezione_wkt"] = ""
            rows.append({key: row.get(key, "")
                         for key in self.EXPORT_COLUMNS})
        return rows

    def describe(self) -> "list[str]":
        lines = ["DATI CATASTALI",
                 "  Stato:      {0}".format(
                     STATUS_LABELS.get(self.status, self.status))]
        if self.message:
            lines.append("  {0}".format(self.message))
        if self.work_crs_authid:
            lines.append("  Calcoli in: {0}".format(self.work_crs_authid))
        lines.append("  Particelle: {0}".format(self.n_parcels))
        lines.append("  Superficie catastale:   {0:,.4f} ha".format(
            self.cadastral_area_m2 / M2_PER_HA))
        lines.append("  Superficie interessata: {0:,.4f} ha ({1:.2f} %)".format(
            self.covered_area_m2 / M2_PER_HA, 100.0 * self.covered_fraction))
        for code, comune, group in self.comuni():
            name = comune.label() if comune is not None else code
            taken = sum(share.intersection_area_m2 for share in group)
            lines.append("  {0} [{1}]: {2} particelle, {3:,.4f} ha".format(
                name, code, len(group), taken / M2_PER_HA))
            for share in sorted(group,
                                key=lambda s: -s.intersection_area_m2):
                lines.append(
                    "      foglio {0:<6} particella {1:<8} "
                    "{2:>10,.2f} m2  {3:>6.2f} %".format(
                        share.parcel.foglio or "-",
                        share.parcel.particella or "-",
                        share.intersection_area_m2, share.percent_of_parcel))
        lines.extend("  " + text for text in self.warnings)
        return lines


def interpolate_cadastral_data(project_geometry, project_crs, parcels,
                               work_crs=None):
    """Which parcels the project really touches, and by how much.

    ``parcels`` is what :func:`parse_parcel_geometries` returns: pairs of
    parcel and geometry in the service CRS. The bounding-box query that
    produced them is deliberately generous, so most of them do not touch the
    project at all -- this is where that is decided, with GEOS on the real
    polygons and not on a centroid: a centroid test says a project is in one
    parcel when it straddles four, and says it is in none when the parcel is
    an L and the centroid falls in the notch.

    Areas are measured in a metric CRS resolved by ``core.crs`` from the
    project's own, because an area in degrees is not an area.
    """
    from qgis.core import (QgsCoordinateReferenceSystem,        # noqa: PLC0415
                           QgsCoordinateTransform, QgsGeometry, QgsProject)

    from ..core import crs as crs_svc                           # noqa: PLC0415

    if project_geometry is None or project_geometry.isEmpty():
        raise CadastreError(
            "cadastral interpolation on an empty project geometry",
            user_message="Nessuna area di progetto da confrontare col "
                         "catasto.")
    if project_crs is None or not project_crs.isValid():
        raise CadastreError(
            "cadastral interpolation from an unknown CRS",
            user_message="Sistema di riferimento del progetto non definito.")

    warnings = []
    decision = crs_svc.resolve_work_crs(project_crs, project_geometry)
    metric = work_crs if work_crs is not None else decision.work_crs
    if decision.transform_required and work_crs is None:
        warnings.append(decision.reason)

    project = QgsGeometry(project_geometry)
    if project_crs != metric:
        project = crs_svc.transform_geometry(project, project_crs, metric)
    if not project.isGeosValid():
        repaired = project.makeValid()
        if repaired is not None and not repaired.isEmpty():
            project = repaired
            warnings.append("La geometria di progetto e' stata corretta "
                            "prima del confronto catastale.")
    project_area = float(project.area())

    service_crs = QgsCoordinateReferenceSystem(SERVICE_CRS)
    to_metric = None
    if service_crs != metric:
        to_metric = QgsCoordinateTransform(service_crs, metric,
                                           QgsProject.instance())

    from . import belfiore                                      # noqa: PLC0415

    register = belfiore.registry()
    shares = []
    pieces = []
    for parcel, geometry in parcels:
        if geometry is None or geometry.isEmpty():
            continue
        shape = QgsGeometry(geometry)
        if to_metric is not None:
            shape = QgsGeometry(geometry)
            if shape.transform(to_metric) != 0:
                warnings.append(
                    "Particella {0}: trasformazione di coordinate non "
                    "riuscita.".format(parcel.label()))
                continue
        if not shape.isGeosValid():
            repaired = shape.makeValid()
            if repaired is None or repaired.isEmpty():
                warnings.append(
                    "Particella {0}: geometria non valida, esclusa.".format(
                        parcel.label()))
                continue
            shape = repaired
        if not shape.intersects(project):
            continue
        overlap = shape.intersection(project)
        if overlap is None or overlap.isEmpty():
            continue
        area = float(overlap.area())
        if area <= 0.0:
            continue
        pieces.append(overlap)
        # Back into the caller's CRS: the areas were measured in the metric
        # one, but what goes on the map has to line up with the project.
        shown, shown_overlap = shape, overlap
        if project_crs != metric:
            shown = crs_svc.transform_geometry(QgsGeometry(shape), metric,
                                               project_crs)
            shown_overlap = crs_svc.transform_geometry(QgsGeometry(overlap),
                                                       metric, project_crs)
        shares.append(ParcelShare(parcel=parcel,
                                  comune=register.resolve(parcel.comune_code),
                                  parcel_area_m2=float(shape.area()),
                                  intersection_area_m2=area,
                                  geometry=shown,
                                  intersection=shown_overlap,
                                  project_area_m2=project_area))

    shares.sort(key=lambda share: -share.intersection_area_m2)
    covered = 0.0
    if pieces:
        # Union, not sum: two parcels that overlap by a sliver would
        # otherwise cover more of the project than the project has.
        merged = pieces[0] if len(pieces) == 1 else QgsGeometry.unaryUnion(
            pieces)
        if merged is not None and not merged.isEmpty():
            covered = float(merged.area())

    if not shares:
        status = STATUS_UNAVAILABLE
        message = ("Il servizio non riporta particelle sull'area indicata.")
    elif project_area > 0.0 and covered / project_area < FULL_COVERAGE:
        status = STATUS_PARTIAL
        message = ("Il catasto copre il {0:.1f} % dell'area: il resto non e' "
                   "in questo servizio.".format(100.0 * covered
                                                / project_area))
    else:
        status = STATUS_OK
        message = ""

    if not register.available:
        warnings.append(register.error or
                        "Tabella dei comuni non disponibile: i codici "
                        "Belfiore restano senza nome.")

    return CadastralResult(shares=shares, project_area_m2=project_area,
                           covered_area_m2=covered, status=status,
                           message=message,
                           work_crs_authid=metric.authid(),
                           warnings=warnings)


def query_area(project_geometry, project_crs, transport=None,
               timeout: float = DEFAULT_TIMEOUT_S, work_crs=None,
               progress=None):
    """The whole workflow for one area: ask, parse, intersect, aggregate.

    ``progress`` is any callable taking a percentage, so a task can report
    without this function knowing what a task is.
    """
    fetch = transport or urllib_transport

    def step(value):
        if progress is not None:
            try:
                progress(value)
            except Exception as exc:                            # noqa: BLE001
                swallow(exc, "cadastre: progress callback")

    step(5.0)
    box = service_bbox(project_geometry, project_crs)
    step(15.0)
    # Tiled, merged and deduplicated before anything is intersected: the
    # service stops at its own feature ceiling without saying so, and a
    # truncated answer looks exactly like a complete one.
    found, tile_warnings = fetch_parcels(
        box, fetch, timeout,
        progress=lambda fraction: step(15.0 + 40.0 * fraction))
    parcels = dedup_pairs(found)
    duplicates = len(found) - len(parcels)
    step(70.0)
    result = interpolate_cadastral_data(project_geometry, project_crs,
                                        parcels, work_crs)
    result.warnings.extend(tile_warnings)
    if duplicates:
        result.warnings.append(
            "Particelle ricevute piu' volte dai riquadri contigui: {0} "
            "duplicati scartati.".format(duplicates))
    step(90.0)

    # The sheet numbers the zoning layer publishes beat the ones derived from
    # the parcel reference; one extra request covers every parcel at once.
    if result.shares:
        try:
            sheets, _zone_warnings = fetch_parcels(
                box, fetch, timeout, type_name=TYPE_ZONING,
                parser=lambda body: [parse_zoning_labels(body)])
            labels = {}
            for chunk in sheets:
                labels.update(chunk)
        except CadastreError:
            labels = {}
        for share in result.shares:
            published = labels.get(zoning_key(share.parcel.national_reference))
            if published:
                share.parcel.foglio = published
    step(100.0)
    return result


#: How many entries a CAD cell carries before it says "and N more". A shape
#: over forty parcels would otherwise make one unreadable cell; the panel
#: and the report carry all of them either way.
CAD_COLUMN_LIMIT = 12


def _joined(values) -> str:
    """``"a; b; c"``, truncated with a count when there are too many."""
    if len(values) <= CAD_COLUMN_LIMIT:
        return "; ".join(values)
    kept = values[:CAD_COLUMN_LIMIT]
    return "{0}; (+{1})".format("; ".join(kept), len(values) - len(kept))


def cad_columns(result) -> dict:
    """The three cadastral CAD columns for a whole intersection.

    A CAD shape can sit on nine parcels across two comuni, and the table it
    goes in has exactly three cells to say so. The rule:

    * a **single** parcel writes what it has always written -- comune name,
      foglio, particella -- so nothing that worked changes;
    * a foglio is qualified with its Belfiore code only when the shape meets
      **more than one comune**, because "foglio 12" then names two places;
    * a particella is qualified with its foglio only when the shape meets
      **more than one foglio**, for the same reason.

    Ordered by the surface the shape takes, so the first entry in each cell
    is the ground the shape is mostly on. The full reading -- surfaces,
    percentages, geometries -- is in the result itself and in the panel;
    this is the summary that fits a cell.

    Returns an empty dict for an empty or missing result: the caller then
    writes "N/D", which is what it already did for a lookup with no answer.
    """
    from .layer_factory import (CAT_COMUNE_FIELD,               # noqa: PLC0415
                                CAT_FOGLIO_FIELD,
                                CAT_PARTICELLA_FIELD)

    if result is None or not getattr(result, "shares", None):
        return {}

    comuni = result.comuni()
    many_comuni = len(comuni) > 1
    many_fogli = len({(share.parcel.comune_code, share.parcel.foglio)
                      for share in result.shares}) > 1

    comune_values, foglio_values, parcel_values = [], [], []

    def add(bucket, value):
        if value and value not in bucket:
            bucket.append(value)

    for code, comune, _group in comuni:
        add(comune_values, (comune.label() if comune is not None else "")
            or code)
        for foglio, shares in result.fogli(code):
            sheet = foglio or "-"
            add(foglio_values,
                "{0} {1}".format(code, sheet) if many_comuni else sheet)
            for share in sorted(shares,
                                key=lambda s: -s.intersection_area_m2):
                label = share.parcel.particella or "-"
                if many_fogli:
                    label = "{0}/{1}".format(sheet, label)
                if many_comuni:
                    label = "{0} {1}".format(code, label)
                add(parcel_values, label)

    return {CAT_COMUNE_FIELD: _joined(comune_values),
            CAT_FOGLIO_FIELD: _joined(foglio_values),
            CAT_PARTICELLA_FIELD: _joined(parcel_values)}


def payload(result) -> dict:
    """The result as a plain dict, for a Qt signal and for a report.

    A signal carrying a dict is a signal anything can connect to -- a panel,
    a log, a test -- without importing this module's classes. The full object
    rides along under ``result`` for callers that want the parcel list.
    """
    if result is None:
        return {"stato": STATUS_ERROR, "messaggio": "nessun risultato",
                "righe": [], "result": None}
    data = dict(result.as_attributes())
    data.update({
        "messaggio": result.message,
        "righe": result.rows(),
        # One row per comune beside the one row per parcel: the summary and
        # the detail travel together, so a panel cannot show one and lose
        # the other.
        "comuni_righe": result.comune_rows(),
        "avvisi": list(result.warnings),
        "crs_calcolo": result.work_crs_authid,
        "result": result,
    })
    return data


_TASK_CLASS = None


def task_class():
    """The cadastral ``QgsTask``, with the signals the GUI listens to.

    Built on first use rather than at import: defining a QObject subclass
    needs Qt, and importing this module must not require a running QGIS.
    """
    global _TASK_CLASS
    if _TASK_CLASS is not None:
        return _TASK_CLASS

    from qgis.core import QgsTask                               # noqa: PLC0415
    from qgis.PyQt.QtCore import pyqtSignal                     # noqa: PLC0415

    class CadastralTask(QgsTask):
        """WFS -> parse -> intersect -> Belfiore -> signal."""

        #: Emitted on success with :func:`payload`.
        cadastralDataReady = pyqtSignal(dict)
        #: Emitted when the cadastre could not answer. The project is fine.
        cadastralFailed = pyqtSignal(str)

        def __init__(self, project_geometry, project_crs, transport=None,
                     timeout=DEFAULT_TIMEOUT_S, work_crs=None):
            super().__init__("GeoCad UAV: dati catastali",
                             QgsTask.Flag.CanCancel)
            self._geometry = project_geometry
            self._crs = project_crs
            self._transport = transport
            self._timeout = timeout
            self._work_crs = work_crs
            self.result = None

        def run(self):
            def progress(value):
                if self.isCanceled():
                    raise CadastreError(
                        "cancelled",
                        user_message="Interrogazione catastale annullata.")
                self.setProgress(float(value))

            try:
                self.result = query_area(self._geometry, self._crs,
                                         self._transport, self._timeout,
                                         self._work_crs, progress)
            except CadastreError as exc:
                self.result = CadastralResult(
                    status=STATUS_ERROR, message=exc.formatted())
                return not self.isCanceled()
            except Exception as exc:                            # noqa: BLE001
                self.result = CadastralResult(status=STATUS_ERROR,
                                              message=str(exc))
                return False
            return True

        def finished(self, ok):
            """Back on the GUI thread: emit, then call the plain callback.

            Both, because a signal is what a panel wants and a callback is
            what a test or a script wants, and neither should have to learn
            the other's idiom.
            """
            data = payload(self.result)
            try:
                if self.result is not None and self.result.is_usable:
                    self.cadastralDataReady.emit(data)
                else:
                    self.cadastralFailed.emit(
                        data.get("messaggio")
                        or STATUS_LABELS.get(data.get("stato"), ""))
                    self.cadastralDataReady.emit(data)
            except Exception as exc:                            # noqa: BLE001
                swallow(exc, "cadastre: result signal")

    _TASK_CLASS = CadastralTask
    return _TASK_CLASS


def area_task(project_geometry, project_crs, on_result=None, transport=None,
              timeout: float = DEFAULT_TIMEOUT_S, work_crs=None):
    """Run :func:`query_area` on QGIS's task manager. Returns the task.

    The interface stays live while a government server thinks about it, the
    operator can cancel, and a cadastral failure arrives as a result with
    status ERRORE instead of an exception: the reforestation project does not
    depend on the cadastre and must not be lost with it.

    Connect to ``task.cadastralDataReady`` for the Qt way; pass ``on_result``
    for the plain one.
    """
    from qgis.core import QgsApplication                        # noqa: PLC0415

    task = task_class()(project_geometry, project_crs, transport, timeout,
                        work_crs)
    if on_result is not None:
        def _forward(_data, _task=task, _callback=on_result):
            try:
                _callback(_task.result)
            except Exception as exc:                            # noqa: BLE001
                swallow(exc, "cadastre: result callback")

        task.cadastralDataReady.connect(_forward)
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
