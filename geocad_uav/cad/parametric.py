"""
Geometry Properties Editor: read, edit and reapply a shape's parameters.

A CAD shape carries the numbers it was built from in a ``cad_params`` attribute
(JSON). That is what lets the operator change "width 25 -> 30" and have the
rectangle rebuilt exactly, instead of redrawing it.

The safety rule (spec section 5): if the geometry has been edited vertex by
vertex outside the plugin, the stored parameters no longer describe it. The
record is then marked **broken** and is never silently reapplied -- doing so
would throw away the operator's manual edit without asking.
"""

from __future__ import annotations

from typing import Optional

from ..core.errors import GeometryError, InvalidInputError, LayerError
from ..core.models import ParametricRecord
from . import primitives as pr

#: Attribute holding the JSON record.
PARAMS_FIELD = "cad_params"

#: Denormalised columns kept alongside it, so they are filterable in QGIS.
METADATA_FIELDS = [
    ("tool", "string"), (PARAMS_FIELD, "string"), ("width", "double"),
    ("height", "double"), ("radius", "double"), ("rotation", "double"),
    ("area", "double"), ("perimeter", "double"), ("created", "string"),
]


def record_to_attributes(record: ParametricRecord) -> dict:
    """Map a record onto the denormalised attribute columns."""
    p = record.params
    return {
        "tool": record.tool,
        PARAMS_FIELD: record.to_json(),
        "width": p.get("width_m") or p.get("side_m") or p.get("semi_major_m"),
        "height": p.get("height_m") or p.get("side_m") or p.get("semi_minor_m"),
        "radius": p.get("radius_m") or p.get("apothem_m"),
        "rotation": p.get("azimuth_deg", 0.0),
        "area": p.get("measured_area_m2"),
        "perimeter": p.get("measured_perimeter_m") or p.get("measured_length_m"),
        "created": record.created_at,
    }


#: Where the record lives on a layer whose attribute table does not carry
#: it. A layer custom property, keyed by feature id: saved with the QGIS
#: project, invisible in the attribute table, and read by the same three
#: tools that used to read the column.
PARAMS_PROPERTY = "geocad/cad_params"


def property_key(feature_id) -> str:
    return "{0}/{1}".format(PARAMS_PROPERTY, int(feature_id))


def store_record(layer, feature_id, record) -> bool:
    """Keep a record beside the feature. Returns False when it could not."""
    if layer is None or feature_id is None or record is None:
        return False
    try:
        layer.setCustomProperty(property_key(feature_id), record.to_json())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    return True


def stored_record(layer, feature_id) -> Optional[ParametricRecord]:
    """The record kept beside a feature, or None."""
    if layer is None or feature_id is None:
        return None
    try:
        raw = layer.customProperty(property_key(feature_id), "")
    except (AttributeError, RuntimeError, TypeError):
        return None
    return _parse(raw)


def drop_record(layer, feature_id) -> None:
    """Forget a record, for a feature that is gone."""
    if layer is None or feature_id is None:
        return
    try:
        layer.removeCustomProperty(property_key(feature_id))
    except (AttributeError, RuntimeError, TypeError):
        pass


def _parse(raw) -> Optional[ParametricRecord]:
    if raw is None or str(raw).strip() in ("", "NULL"):
        return None
    try:
        return ParametricRecord.from_json(str(raw))
    except (ValueError, TypeError) as exc:
        raise GeometryError(
            "corrupt parametric record: {0}".format(exc),
            user_message="I parametri memorizzati non sono leggibili.",
            hint="La geometria puo' essere modificata solo manualmente.") from exc


def read_record(feature, layer=None) -> Optional[ParametricRecord]:
    """Load the record for a feature, from wherever this layer keeps it.

    The column first, for a layer that still has one -- an operator may be
    drawing into a layer of their own that carries ``cad_params`` -- then
    the side store, which is where the plugin's own CAD layers keep it
    since 1.38.0.
    """
    raw = None
    try:
        raw = feature[PARAMS_FIELD]
    except (KeyError, IndexError):
        raw = None
    record = _parse(raw)
    if record is not None:
        return record
    try:
        feature_id = feature.id()
    except (AttributeError, RuntimeError):
        return None
    return stored_record(layer, feature_id)


def write_record(layer, feature_id, record) -> dict:
    """Persist a record and return the attributes a caller should write.

    The attributes are empty for a layer without the columns, which is the
    normal case now: the record went to the side store instead.
    """
    attributes = record_to_attributes(record) if record is not None else {}
    try:
        names = {field.name() for field in layer.fields()}
    except (AttributeError, RuntimeError):
        names = set()
    if PARAMS_FIELD in names:
        return attributes
    store_record(layer, feature_id, record)
    return {name: value for name, value in attributes.items()
            if name in names}


def check_integrity(feature, record: ParametricRecord) -> ParametricRecord:
    """Mark the record broken when the live geometry no longer matches it."""
    signature = pr.geometry_signature(feature.geometry())
    stored = record.params.get("_signature")
    if stored and signature and stored != signature:
        record.broken = True
    return record


def describe(record: ParametricRecord) -> "list[str]":
    """Italian lines for the properties panel."""
    label = pr.TOOL_LABELS.get(record.tool, record.tool)
    lines = ["Strumento: {0}".format(label)]
    if record.broken:
        lines.append(
            "ATTENZIONE: la geometria e' stata modificata a mano dopo la "
            "creazione. I parametri qui sotto non la descrivono piu' e non "
            "verranno riapplicati senza conferma esplicita.")
    skip = ("_signature",)
    for key, value in sorted(record.params.items()):
        if key in skip:
            continue
        lines.append("{0}: {1}".format(key, value))
    return lines


def editable_parameters(record: ParametricRecord) -> dict:
    """The inputs the properties editor should expose as fields."""
    return pr.input_params(record)


def apply_changes(feature, record: ParametricRecord, changes: dict,
                  allow_broken: bool = False, z: Optional[float] = None):
    """Rebuild the geometry with ``changes`` applied to its parameters.

    Returns ``(QgsGeometry, ParametricRecord)``. Refuses to act on a broken
    record unless the caller has confirmed with the operator that the manual
    edit may be discarded.
    """
    if record.broken and not allow_broken:
        raise GeometryError(
            "refusing to reapply parameters to a manually edited geometry",
            user_message="La geometria e' stata modificata manualmente: "
                         "riapplicare i parametri cancellerebbe quelle "
                         "modifiche.",
            hint="Conferma esplicitamente per rigenerare la forma dai parametri.")

    params = pr.input_params(record)
    unknown = set(changes) - set(params) - {"x", "y", "azimuth_deg"}
    if unknown:
        raise InvalidInputError(
            "unknown parameters for tool {0}: {1}".format(
                record.tool, ", ".join(sorted(unknown))),
            user_message="Parametri non validi per questo strumento: {0}."
                         .format(", ".join(sorted(unknown))))
    params.update(changes)

    updated = ParametricRecord(tool=record.tool, params=params,
                               crs_authid=record.crs_authid,
                               record_id=record.record_id,
                               layer_id=record.layer_id,
                               created_at=record.created_at)
    geom, updated = pr.rebuild(updated, z)
    return geom, updated


def update_feature(layer, feature, geom, record: ParametricRecord,
                   label: str = "Modifica parametri"):
    """Write geometry and attributes back inside one undo command."""
    from ..core.undo import edit_command                         # noqa: PLC0415

    if layer is None:
        raise LayerError("no layer", user_message="Nessun layer di destinazione.")
    attributes = record_to_attributes(record)
    fields = layer.fields()
    with edit_command(layer, label):
        if not layer.changeGeometry(feature.id(), geom):
            raise LayerError(
                "changeGeometry failed for feature {0}".format(feature.id()),
                user_message="Aggiornamento della geometria non riuscito.")
        for name, value in attributes.items():
            index = fields.indexOf(name)
            if index >= 0:
                layer.changeAttributeValue(feature.id(), index, value)
    return True
