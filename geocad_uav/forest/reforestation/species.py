"""
M05 -- the species catalogue: a file the operator owns, not a list in the code.

Which species a nursery supplies, how far apart they are planted and where
they will grow is local knowledge that changes with the region, the year and
the funding programme. Hard-coding a list would be wrong within a season, so
this module defines the *table* -- its columns and their meaning -- and never
its rows. A new catalogue is created empty; what goes in it comes from the
operator, or from a file they already have.

The store is a GeoPackage, which is SQLite: one file, opens in QGIS as an
ordinary attribute table, editable there with no plugin involved, and
readable by anything that reads OGR. The table is aspatial -- a species is
not a place.

The ecological limits are not a second filtering language: they are turned
into a ``forest.planting.TopographicFilter``, the same object that accepts or
rejects a planting position and that ``terrain.suitability`` maps. So "not
above 900 m, not over 35 degrees, north-facing only" is evaluated by the code
that already decides where a plant may stand -- and by the same code that
draws the suitability map the operator is looking at.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional

from ...core.errors import GeoCadError, InvalidInputError
from ..planting import TopographicFilter

#: Layer (table) name inside the GeoPackage.
LAYER_NAME = "specie"

#: The schema, in one place, as a memory-layer URI fragment. Types are OGR's
#: own names, so the GeoPackage that comes out is an ordinary table.
FIELD_SPEC = (
    ("key", "string"),
    ("name", "string"),
    ("scientific_name", "string"),
    ("min_distance_m", "double"),
    ("recommended_density_per_ha", "double"),
    ("elev_min_m", "double"),
    ("elev_max_m", "double"),
    ("slope_max_deg", "double"),
    ("aspect_from_deg", "double"),
    ("aspect_to_deg", "double"),
    ("notes", "string"),
)

FIELD_NAMES = tuple(name for name, _type in FIELD_SPEC)

#: Italian headings, for a report or a form. Not a translation table for the
#: data: the column names are the data's own.
FIELD_LABELS = {
    "key": "Codice",
    "name": "Nome comune",
    "scientific_name": "Nome scientifico",
    "min_distance_m": "Distanza minima (m)",
    "recommended_density_per_ha": "Densita' consigliata (piante/ha)",
    "elev_min_m": "Quota minima (m)",
    "elev_max_m": "Quota massima (m)",
    "slope_max_deg": "Pendenza massima (deg)",
    "aspect_from_deg": "Esposizione da (deg)",
    "aspect_to_deg": "Esposizione a (deg)",
    "notes": "Note",
}

MEMORY_URI = "None?" + "&".join(
    "field={0}:{1}".format(name, kind) for name, kind in FIELD_SPEC)


def _optional(value) -> Optional[float]:
    """A number, or None for a limit the operator did not set.

    OGR hands back NULL as ``None`` on one version and as a QVariant that is
    falsy on another; both mean "not set", and so does NaN.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(number) else number


@dataclass
class Species:
    """One row of the catalogue."""

    key: str
    name: str = ""
    scientific_name: str = ""
    min_distance_m: float = 0.0
    recommended_density_per_ha: float = 0.0
    elev_min_m: Optional[float] = None
    elev_max_m: Optional[float] = None
    slope_max_deg: Optional[float] = None
    aspect_from_deg: Optional[float] = None
    aspect_to_deg: Optional[float] = None
    notes: str = ""

    def __post_init__(self) -> None:
        self.key = str(self.key or "").strip()
        if not self.key:
            raise InvalidInputError(
                "a species needs a key",
                user_message="Ogni specie deve avere un codice.")
        self.name = str(self.name or self.key)
        for field_name in ("min_distance_m", "recommended_density_per_ha"):
            value = float(getattr(self, field_name) or 0.0)
            if not math.isfinite(value) or value < 0.0:
                raise InvalidInputError(
                    "{0} must be >= 0, got {1!r}".format(
                        field_name, getattr(self, field_name)),
                    user_message="{0}: il valore non puo' essere "
                                 "negativo.".format(
                                     FIELD_LABELS[field_name]))
            setattr(self, field_name, value)
        for field_name in ("elev_min_m", "elev_max_m", "slope_max_deg",
                           "aspect_from_deg", "aspect_to_deg"):
            setattr(self, field_name, _optional(getattr(self, field_name)))
        if (self.elev_min_m is not None and self.elev_max_m is not None
                and self.elev_min_m > self.elev_max_m):
            raise InvalidInputError(
                "elevation range is inverted for {0!r}".format(self.key),
                user_message="{0}: la quota minima supera la massima.".format(
                    self.name))

    # -- what the rest of the plugin needs from a species ------------------

    def topographic_filter(self) -> TopographicFilter:
        """The ecological limits, as the filter the planner already speaks.

        An aspect range is given as a single sector; one that wraps through
        north (300 to 60) is handled by the filter itself, which is why it is
        stored as two numbers and not as two rows.
        """
        sectors = []
        if self.aspect_from_deg is not None and self.aspect_to_deg is not None:
            sectors.append((self.aspect_from_deg, self.aspect_to_deg))
        return TopographicFilter(
            slope_max_deg=self.slope_max_deg,
            elev_min_m=self.elev_min_m, elev_max_m=self.elev_max_m,
            aspect_ranges=sectors)

    def spacing(self, pattern, ratio: float = 1.0):
        """The spacing its recommended density implies, under a pattern."""
        from . import density as density_mod                  # noqa: PLC0415

        if self.recommended_density_per_ha <= 0.0:
            raise InvalidInputError(
                "{0!r} has no recommended density".format(self.key),
                user_message="{0}: nessuna densita' consigliata in "
                             "catalogo.".format(self.name))
        return density_mod.spacing_from_density(
            self.recommended_density_per_ha, pattern, ratio)

    def respects_min_distance(self, distance_m: float) -> bool:
        return float(distance_m) + 1e-9 >= self.min_distance_m

    def as_attributes(self) -> list:
        return [getattr(self, name) for name in FIELD_NAMES]

    def fill(self, feature) -> None:
        """Write the record into a feature, by column name.

        By name and not by position: a GeoPackage table carries an ``fid``
        the memory table does not, so the same list of values lands one
        column out and the provider refuses the whole feature.
        """
        fields = feature.fields()
        for name in FIELD_NAMES:
            index = fields.indexOf(name)
            if index >= 0:
                feature.setAttribute(index, getattr(self, name))

    @classmethod
    def from_feature(cls, feature) -> "Species":
        values = {}
        for name in FIELD_NAMES:
            try:
                values[name] = feature[name]
            except KeyError:
                values[name] = None
        text_defaults = ("key", "name", "scientific_name", "notes")
        for name in text_defaults:
            values[name] = "" if values[name] is None else str(values[name])
        for name in ("min_distance_m", "recommended_density_per_ha"):
            values[name] = _optional(values[name]) or 0.0
        return cls(**values)

    def describe(self) -> "list[str]":
        lines = ["{0} ({1})".format(self.name, self.key)]
        if self.scientific_name:
            lines.append("  {0}".format(self.scientific_name))
        if self.min_distance_m:
            lines.append("  Distanza minima: {0:.2f} m".format(
                self.min_distance_m))
        if self.recommended_density_per_ha:
            lines.append("  Densita' consigliata: {0:,.0f} piante/ha".format(
                self.recommended_density_per_ha))
        limits = []
        if self.elev_min_m is not None or self.elev_max_m is not None:
            limits.append("quota {0} - {1} m".format(
                "-" if self.elev_min_m is None else "{0:g}".format(
                    self.elev_min_m),
                "-" if self.elev_max_m is None else "{0:g}".format(
                    self.elev_max_m)))
        if self.slope_max_deg is not None:
            limits.append("pendenza max {0:g} deg".format(self.slope_max_deg))
        if self.aspect_from_deg is not None and self.aspect_to_deg is not None:
            limits.append("esposizione {0:g} - {1:g} deg".format(
                self.aspect_from_deg, self.aspect_to_deg))
        if limits:
            lines.append("  Limiti: {0}".format("; ".join(limits)))
        if self.notes:
            lines.append("  {0}".format(self.notes))
        return lines


class SpeciesCatalog:
    """An editable species table, stored in a GeoPackage."""

    def __init__(self, layer, path: str = ""):
        if layer is None or not layer.isValid():
            raise GeoCadError(
                "species catalogue layer is not valid",
                user_message="Il catalogo delle specie non e' leggibile.")
        self.layer = layer
        self.path = path

    # -- creating and opening ---------------------------------------------

    @staticmethod
    def memory_layer(name: str = LAYER_NAME):
        """An empty, in-memory catalogue with the right columns.

        Useful on its own: a project that never saves a catalogue still needs
        somewhere to put the species it is using.
        """
        from qgis.core import QgsVectorLayer                  # noqa: PLC0415

        layer = QgsVectorLayer(MEMORY_URI, name, "memory")
        if not layer.isValid():
            raise GeoCadError(
                "could not build the in-memory species table",
                user_message="Impossibile creare la tabella delle specie.")
        return layer

    @classmethod
    def create(cls, path: str, species=(), overwrite: bool = False):
        """Write a new catalogue. Empty unless rows are supplied.

        No default list: what a catalogue contains is the operator's, and a
        list shipped in the code would be out of date in one season and wrong
        in one valley.
        """
        from qgis.core import (QgsCoordinateTransformContext,  # noqa: PLC0415
                               QgsFeature, QgsVectorFileWriter,
                               QgsVectorLayer)

        if not path:
            raise InvalidInputError(
                "a catalogue needs a path",
                user_message="Indica dove salvare il catalogo delle specie.")
        if os.path.exists(path) and not overwrite:
            raise GeoCadError(
                "{0} already exists".format(path),
                user_message="Il file '{0}' esiste gia'.".format(
                    os.path.basename(path)),
                hint="Scegli un altro nome o apri il catalogo esistente.")

        staging = cls.memory_layer()
        features = []
        for record in species:
            feature = QgsFeature(staging.fields())
            record.fill(feature)
            features.append(feature)
        if features:
            staging.dataProvider().addFeatures(features)

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = LAYER_NAME
        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            staging, path, QgsCoordinateTransformContext(), options)
        if result[0] != QgsVectorFileWriter.WriterError.NoError:
            raise GeoCadError(
                "writing {0} failed: {1}".format(path, result),
                user_message="Salvataggio del catalogo delle specie non "
                             "riuscito.")
        # The writer normalises the extension -- a name that does not end in
        # .gpkg comes back with one appended -- and reports where it actually
        # wrote. Trusting the requested path instead would open nothing.
        written = result[2] if len(result) > 2 and result[2] else path
        return cls.open(written)

    @classmethod
    def open(cls, path: str):
        from qgis.core import QgsVectorLayer                  # noqa: PLC0415

        if not path or not os.path.exists(path):
            raise GeoCadError(
                "no species catalogue at {0!r}".format(path),
                user_message="Catalogo delle specie non trovato.")
        layer = QgsVectorLayer("{0}|layername={1}".format(path, LAYER_NAME),
                               LAYER_NAME, "ogr")
        if not layer.isValid():
            raise GeoCadError(
                "{0} does not hold a {1!r} table".format(path, LAYER_NAME),
                user_message="Il file indicato non contiene la tabella delle "
                             "specie.")
        missing = [name for name in FIELD_NAMES
                   if layer.fields().indexOf(name) < 0]
        if missing:
            raise GeoCadError(
                "species table is missing {0}".format(", ".join(missing)),
                user_message="La tabella delle specie non ha le colonne "
                             "attese: {0}.".format(", ".join(missing)))
        return cls(layer, path)

    @classmethod
    def from_layer(cls, layer):
        """Adopt a layer that already has the columns -- a memory one, say."""
        return cls(layer, "")

    # -- reading -----------------------------------------------------------

    def all(self) -> "list[Species]":
        return [Species.from_feature(f) for f in self.layer.getFeatures()]

    def keys(self) -> "list[str]":
        return [record.key for record in self.all()]

    def get(self, key: str) -> Species:
        for record in self.all():
            if record.key == key:
                return record
        raise InvalidInputError(
            "no species {0!r} in the catalogue".format(key),
            user_message="La specie '{0}' non e' in catalogo.".format(key))

    def has(self, key: str) -> bool:
        return key in self.keys()

    @property
    def count(self) -> int:
        return int(self.layer.featureCount())

    def _feature_id(self, key: str) -> int:
        index = self.layer.fields().indexOf("key")
        for feature in self.layer.getFeatures():
            if str(feature[index]) == key:
                return feature.id()
        raise InvalidInputError(
            "no species {0!r} in the catalogue".format(key),
            user_message="La specie '{0}' non e' in catalogo.".format(key))

    # -- writing -----------------------------------------------------------

    def add(self, record: Species) -> bool:
        from qgis.core import QgsFeature                      # noqa: PLC0415

        if self.has(record.key):
            raise InvalidInputError(
                "species {0!r} is already in the catalogue".format(record.key),
                user_message="La specie '{0}' e' gia' in catalogo.".format(
                    record.key))
        feature = QgsFeature(self.layer.fields())
        record.fill(feature)
        ok, _added = self.layer.dataProvider().addFeatures([feature])
        if not ok:
            raise GeoCadError(
                "could not add {0!r}: {1}".format(
                    record.key, self.layer.dataProvider().lastError()),
                user_message="Inserimento della specie non riuscito.")
        self.layer.updateExtents()
        return True

    def update(self, record: Species) -> bool:
        feature_id = self._feature_id(record.key)
        fields = self.layer.fields()
        changes = {fields.indexOf(name): getattr(record, name)
                   for name in FIELD_NAMES if fields.indexOf(name) >= 0}
        if not self.layer.dataProvider().changeAttributeValues(
                {feature_id: changes}):
            raise GeoCadError(
                "could not update {0!r}".format(record.key),
                user_message="Aggiornamento della specie non riuscito.")
        return True

    def remove(self, key: str) -> bool:
        feature_id = self._feature_id(key)
        if not self.layer.dataProvider().deleteFeatures([feature_id]):
            raise GeoCadError(
                "could not delete {0!r}".format(key),
                user_message="Eliminazione della specie non riuscita.")
        return True

    # -- readout -----------------------------------------------------------

    def describe(self) -> "list[str]":
        lines = ["CATALOGO DELLE SPECIE",
                 "  Archivio: {0}".format(self.path or "in memoria"),
                 "  Specie:   {0}".format(self.count)]
        for record in self.all():
            lines.extend("  " + text for text in record.describe())
        return lines
