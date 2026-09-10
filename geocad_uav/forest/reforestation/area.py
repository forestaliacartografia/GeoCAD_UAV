"""
M01 -- the reforestation area: what is there, what comes off it, what is left.

    superficie utile = superficie lorda - superficie esclusa

The three surfaces are the first numbers on any planting report and the last
ones an operator will accept being approximate, so all three come from GEOS on
the real polygon: no bounding boxes, no Python clipping, and inner rings
(courtyards, rock outcrops, an existing stand inside the perimeter) are part
of the polygon and are therefore already out of the gross area.

Exclusions are kept as a list with their labels rather than being merged on
arrival, so the report can say *what* was taken off and the operator can drop
one without rebuilding the area. They are unioned only when a surface is
asked for, which is also what stops two overlapping exclusions being counted
twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ...core.errors import EmptyAoiError, GeometryError

#: Square metres in a hectare. Named because "10 000" in a formula reads as a
#: coordinate as easily as a conversion.
M2_PER_HA = 10_000.0


@dataclass
class Exclusion:
    """One thing taken off the gross area, with the reason it came off."""

    geometry: object                    # QgsGeometry
    label: str = ""
    source: str = ""

    def area_m2(self) -> float:
        if self.geometry is None or self.geometry.isEmpty():
            return 0.0
        return float(self.geometry.area())


class ReforestationArea:
    """The project polygon and everything subtracted from it.

    ``geometry`` is a ``QgsGeometry`` in a metric working CRS -- areas are read
    straight off GEOS, so a geographic CRS would return square degrees. The
    caller resolves the CRS (``core.crs`` already refuses degrees for the CAD
    tools); this class records the authid it was given and reports it.
    """

    def __init__(self, geometry, crs_authid: str = "", label: str = ""):
        if geometry is None or geometry.isEmpty():
            raise EmptyAoiError(
                "reforestation area built on an empty geometry",
                user_message="Nessun poligono di progetto selezionato.")
        self.geometry = self._validated(geometry)
        self.crs_authid = crs_authid
        self.label = label
        self.exclusions: "list[Exclusion]" = []

    # -- construction ------------------------------------------------------

    @staticmethod
    def _validated(geometry):
        """GEOS refuses to operate on an invalid ring; repair it once, here."""
        if geometry.isGeosValid():
            return geometry
        repaired = geometry.makeValid()
        if repaired is None or repaired.isEmpty():
            raise GeometryError(
                "geometry is invalid and cannot be repaired",
                user_message="Il poligono di progetto non e' valido e non "
                             "puo' essere corretto automaticamente.")
        return repaired

    @classmethod
    def from_layer(cls, layer, only_selected: bool = True, label: str = ""):
        """Build the area from a polygon layer, selection first.

        A selection is what an operator means by "this stand"; with nothing
        selected the whole layer is dissolved, which is what they mean by
        "the file I just loaded". Several polygons become one multipolygon
        through GEOS, so two disjoint parcels are one project.
        """
        from qgis.core import QgsGeometry, QgsWkbTypes        # noqa: PLC0415

        if layer is None:
            raise EmptyAoiError(
                "no layer given",
                user_message="Nessun layer poligonale selezionato.")
        if QgsWkbTypes.geometryType(layer.wkbType()) != \
                QgsWkbTypes.PolygonGeometry:
            raise GeometryError(
                "layer {0!r} is not polygonal".format(layer.name()),
                user_message="Il layer '{0}' non contiene poligoni.".format(
                    layer.name()))

        if only_selected and layer.selectedFeatureCount() > 0:
            features = layer.selectedFeatures()
            source = "{0} (selezione)".format(layer.name())
        else:
            features = list(layer.getFeatures())
            source = layer.name()

        parts = [f.geometry() for f in features
                 if f.geometry() is not None and not f.geometry().isEmpty()]
        if not parts:
            raise EmptyAoiError(
                "layer {0!r} yielded no polygon".format(layer.name()),
                user_message="Il layer '{0}' non contiene poligoni "
                             "utilizzabili.".format(layer.name()))
        merged = parts[0] if len(parts) == 1 else QgsGeometry.unaryUnion(parts)
        return cls(merged, crs_authid=layer.crs().authid(),
                   label=label or source)

    # -- exclusions --------------------------------------------------------

    def add_exclusion(self, geometry, label: str = "", source: str = "") -> bool:
        """Take something off the gross area.

        Returns False when the geometry does not touch the area at all: an
        exclusion that removes nothing is not an error, but it should not
        appear in the report as though it had done something.
        """
        if geometry is None or geometry.isEmpty():
            return False
        candidate = self._validated(geometry)
        clipped = self.geometry.intersection(candidate)
        if clipped is None or clipped.isEmpty() or clipped.area() <= 0.0:
            return False
        self.exclusions.append(Exclusion(clipped, label=label, source=source))
        return True

    def add_exclusions(self, geometries, label: str = "",
                       source: str = "") -> int:
        return sum(1 for geom in geometries
                   if self.add_exclusion(geom, label=label, source=source))

    def clear_exclusions(self) -> None:
        self.exclusions = []

    def esclusa(self):
        """The exclusions as one geometry, overlaps counted once.

        ``None`` when there is nothing to exclude -- an empty geometry would
        make ``difference()`` return a copy, which works, but ``None`` says
        plainly that no subtraction happened.
        """
        from qgis.core import QgsGeometry                     # noqa: PLC0415

        parts = [e.geometry for e in self.exclusions
                 if e.geometry is not None and not e.geometry.isEmpty()]
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        return QgsGeometry.unaryUnion(parts)

    # -- the three surfaces ------------------------------------------------

    def lorda(self):
        """The project polygon itself."""
        return self.geometry

    def utile(self):
        """``lorda - esclusa``, as a geometry. GEOS does the subtraction.

        The surface in hectares is :attr:`utile_ha`; this returns the shape,
        because everything downstream (the lattice, the rows, the report map)
        needs the polygon, not only its measure.
        """
        removed = self.esclusa()
        if removed is None:
            return self.geometry
        left = self.geometry.difference(removed)
        if left is None:
            raise GeometryError(
                "GEOS could not subtract the exclusions",
                user_message="Sottrazione delle aree escluse non riuscita.")
        return left

    @property
    def lorda_m2(self) -> float:
        return float(self.geometry.area())

    @property
    def esclusa_m2(self) -> float:
        removed = self.esclusa()
        return 0.0 if removed is None else float(removed.area())

    @property
    def utile_m2(self) -> float:
        return float(self.utile().area())

    @property
    def lorda_ha(self) -> float:
        return self.lorda_m2 / M2_PER_HA

    @property
    def esclusa_ha(self) -> float:
        return self.esclusa_m2 / M2_PER_HA

    @property
    def utile_ha(self) -> float:
        return self.utile_m2 / M2_PER_HA

    @property
    def perimeter_m(self) -> float:
        return float(self.geometry.length())

    @property
    def is_empty(self) -> bool:
        """True when the exclusions have eaten the whole project."""
        return self.utile_m2 <= 0.0

    # -- readout -----------------------------------------------------------

    def exclusion_breakdown(self) -> "list[tuple]":
        """[(label, m2), ...] as declared, before the union.

        These add up to more than :attr:`esclusa_m2` when two exclusions
        overlap, which is correct: each one really does cover that much, and
        the union is what actually comes off.
        """
        return [(e.label or e.source or "esclusione", e.area_m2())
                for e in self.exclusions]

    def summary(self) -> "list[str]":
        lines = []
        if self.label:
            lines.append("Area di progetto: {0}".format(self.label))
        if self.crs_authid:
            lines.append("Sistema di riferimento: {0}".format(self.crs_authid))
        lines.append("Superficie lorda:    {0:>12,.4f} ha".format(self.lorda_ha))
        lines.append("Superficie esclusa:  {0:>12,.4f} ha".format(
            self.esclusa_ha))
        lines.append("Superficie utile:    {0:>12,.4f} ha".format(self.utile_ha))
        lines.append("Perimetro:           {0:>12,.2f} m".format(
            self.perimeter_m))
        for label, m2 in self.exclusion_breakdown():
            lines.append("  - {0}: {1:,.4f} ha".format(label, m2 / M2_PER_HA))
        return lines
