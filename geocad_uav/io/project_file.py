"""
The reforestation project, saved and reopened.

A project is hours of work: an area traced on the cadastre, constraints
measured, a DEM read, zones cut, a mix decided, a stand generated and then
edited plant by plant. Until it can be written to a file and opened again
tomorrow, none of that survives closing QGIS.

What is written is the *decisions*, plus the result they produced. Geometry
goes out as WKT with its CRS beside it, never as a bare coordinate pair;
plants go out as records, because an edited stand is not reproducible by
re-running the generator and a file that silently regenerated a different
stand would be worse than no file.

What is deliberately not written: the DEM grid itself. It is a raster, it can
be hundreds of megabytes, and it has a path. The path is saved and re-read on
open; when the file has moved the project opens without terrain and says so,
instead of opening with a terrain that is not the one the plan was made on.

The format is JSON with a version stamp. Reading a file from a later version
is refused with the two numbers in the message, rather than half-read.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from ..core.errors import GeoCadError, InvalidInputError

#: Bumped when a change would make an older reader misread a newer file.
FORMAT_VERSION = 1

#: What the first bytes of one of our files look like.
FORMAT_MARKER = "geocad-uav-reforestation"

SUFFIX = ".gcuav"


class ProjectFileError(GeoCadError):
    default_message = "Il file di progetto non e' utilizzabile."


# --------------------------------------------------------------------------
# geometry, in and out
# --------------------------------------------------------------------------

def geometry_out(geometry) -> Optional[str]:
    if geometry is None:
        return None
    try:
        if geometry.isEmpty():
            return None
        return geometry.asWkt()
    except (AttributeError, RuntimeError):
        return None


def geometry_in(wkt):
    from qgis.core import QgsGeometry                            # noqa: PLC0415

    if not wkt:
        return None
    geometry = QgsGeometry.fromWkt(str(wkt))
    if geometry is None or geometry.isEmpty():
        return None
    return geometry


def _number(value, fallback=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


# --------------------------------------------------------------------------
# the parts
# --------------------------------------------------------------------------

def area_out(state) -> dict:
    area = state.area
    if area is None:
        return {}
    return {"geometry": geometry_out(area.geometry),
            "label": area.label,
            "crs": state.crs.authid() if state.crs is not None else ""}


def constraints_out(state) -> list:
    out = []
    for key, rule in state.constraints.rules.items():
        out.append({
            "key": key,
            "label": rule.label,
            "buffer_m": _number(rule.buffer_m),
            "geometries": [geometry_out(geometry)
                           for geometry in rule.geometries
                           if geometry_out(geometry)],
            "sources": list(rule.sources)})
    return out


def spec_out(spec) -> dict:
    if spec is None:
        return {}
    return {"plant_distance_m": _number(spec.plant_distance_m),
            "row_distance_m": _number(spec.row_distance_m),
            "row_azimuth_deg": _number(spec.row_azimuth_deg),
            "pattern": spec.pattern,
            "margin_m": _number(spec.margin_m),
            "step_mode": spec.step_mode,
            "jitter_m": _number(spec.jitter_m),
            "seed": getattr(spec, "seed", 0),
            "custom_offsets": list(getattr(spec, "custom_offsets", ()) or ())}


def spec_in(data):
    from ..forest.reforestation import spacing as spacing_mod    # noqa: PLC0415

    if not data:
        return spacing_mod.SlopeSpacing(plant_distance_m=3.0,
                                        row_distance_m=3.0)
    return spacing_mod.SlopeSpacing(
        plant_distance_m=_number(data.get("plant_distance_m"), 3.0),
        row_distance_m=_number(data.get("row_distance_m"), 3.0),
        row_azimuth_deg=_number(data.get("row_azimuth_deg")),
        pattern=str(data.get("pattern") or "rect"),
        margin_m=_number(data.get("margin_m")),
        step_mode=str(data.get("step_mode") or "max_slope"),
        jitter_m=_number(data.get("jitter_m")),
        seed=int(data.get("seed") or 0),
        custom_offsets=tuple(data.get("custom_offsets") or ()))


def zones_out(state) -> list:
    return [{"name": zone.name,
             "geometry": geometry_out(zone.geometry),
             "spec": spec_out(zone.spec),
             "shares": [list(share) for share in (zone.shares or ())]}
            for zone in state.zones]


def natural_out(settings) -> dict:
    if settings is None:
        return {}
    return {"glade_count": int(settings.glade_count),
            "glade_radius_m": _number(settings.glade_radius_m),
            "glade_margin_m": _number(settings.glade_margin_m),
            "glade_gap_m": _number(settings.glade_gap_m),
            "amplitude": _number(settings.amplitude),
            "min_distance_m": _number(settings.min_distance_m),
            "seed": int(settings.seed)}


def natural_in(data):
    from ..forest.reforestation import natural as natural_mod    # noqa: PLC0415

    if not data:
        return natural_mod.NaturalSettings()
    return natural_mod.NaturalSettings(
        glade_count=int(data.get("glade_count") or 0),
        glade_radius_m=_number(data.get("glade_radius_m")),
        glade_margin_m=_number(data.get("glade_margin_m")),
        glade_gap_m=_number(data.get("glade_gap_m")),
        amplitude=_number(data.get("amplitude")),
        min_distance_m=_number(data.get("min_distance_m")),
        seed=int(data.get("seed") or 0))


def plants_out(result) -> list:
    """Every plant, as the record it is. An edited stand is not a formula."""
    from ..forest.reforestation.composition import (              # noqa: PLC0415
        species_of)
    from ..forest.reforestation.zones import zone_of              # noqa: PLC0415

    if result is None:
        return []
    return [{"plant_id": record.plant_id, "row_id": record.row_id,
             "seq_in_row": record.seq_in_row,
             "x": record.x, "y": record.y, "z": record.z,
             "slope_deg": record.slope_deg, "aspect_deg": record.aspect_deg,
             "spacing_x": record.spacing_x, "spacing_y": record.spacing_y,
             "azimuth_deg": record.azimuth_deg,
             "specie": species_of(record), "zona": zone_of(record)}
            for record in result.plants]


def plants_in(rows):
    from ..core.models import PlantingRecord                      # noqa: PLC0415
    from ..forest.reforestation.composition import (              # noqa: PLC0415
        SPECIES_ATTRIBUTE)
    from ..forest.reforestation.zones import ZONE_ATTRIBUTE       # noqa: PLC0415

    records = []
    for row in rows or ():
        record = PlantingRecord(
            plant_id=int(row.get("plant_id") or 0),
            row_id=int(row.get("row_id") or 0),
            seq_in_row=int(row.get("seq_in_row") or 0),
            x=_number(row.get("x")), y=_number(row.get("y")),
            z=None if row.get("z") is None else _number(row.get("z")),
            spacing_x=_number(row.get("spacing_x")),
            spacing_y=_number(row.get("spacing_y")),
            azimuth_deg=_number(row.get("azimuth_deg")))
        record.slope_deg = (None if row.get("slope_deg") is None
                            else _number(row.get("slope_deg")))
        record.aspect_deg = (None if row.get("aspect_deg") is None
                             else _number(row.get("aspect_deg")))
        setattr(record, SPECIES_ATTRIBUTE, str(row.get("specie") or ""))
        setattr(record, ZONE_ATTRIBUTE, str(row.get("zona") or ""))
        records.append(record)
    return records


def species_out(state) -> list:
    """The catalogue entries the project actually uses, with their names."""
    out = []
    for key, _percent in state.shares:
        entry = {"key": key, "name": "", "min_distance_m": 0.0}
        try:
            record = state.catalog.get(key)
            entry["name"] = record.name or ""
            entry["min_distance_m"] = _number(
                getattr(record, "min_distance_m", 0.0))
        except GeoCadError:
            pass
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# the whole thing
# --------------------------------------------------------------------------

def to_dict(state, with_plants: bool = True) -> dict:
    """The project as a plain dict, ready for json.dump."""
    data = {
        "format": FORMAT_MARKER,
        "version": FORMAT_VERSION,
        "area": area_out(state),
        "constraints": constraints_out(state),
        "terrain": {"source": ("" if state.terrain is None
                               else str(state.terrain.model.source or "")),
                    "contour_interval_m": _number(state.contour_interval_m)},
        "contours": [{"elevation_m": _number(row.elevation_m),
                      "geometry": geometry_out(row.geometry),
                      "length_m": _number(row.length_m)}
                     for row in state.contours],
        "shares": [[key, _number(percent)] for key, percent in state.shares],
        "species": species_out(state),
        "spec": spec_out(state.spec),
        "zones": zones_out(state),
        "natural": natural_out(state.natural),
        "glades": [{"label": glade.label,
                    "radius_m": _number(glade.radius_m),
                    "geometry": geometry_out(glade.geometry)}
                   for glade in state.glades],
        "flags": {"scheme_chosen": bool(state.scheme_chosen),
                  "orientation_applied": bool(state.orientation_applied),
                  "glades_placed": bool(state.glades_placed),
                  "edited": bool(state.edited)},
        "plants": plants_out(state.result) if with_plants else [],
        "usable_area_m2": (_number(state.result.usable_area_m2)
                           if state.result is not None else 0.0),
    }
    return data


def _check(data: dict) -> None:
    if not isinstance(data, dict) or data.get("format") != FORMAT_MARKER:
        raise ProjectFileError(
            "not a reforestation project file",
            user_message="Questo file non e' un progetto di rimboschimento.")
    version = int(data.get("version") or 0)
    if version > FORMAT_VERSION:
        raise ProjectFileError(
            "file version {0} is newer than {1}".format(version,
                                                        FORMAT_VERSION),
            user_message="Il file e' stato scritto da una versione piu' "
                         "recente del plugin (formato {0}, questo plugin "
                         "legge il {1}).".format(version, FORMAT_VERSION),
            hint="Aggiorna il plugin.")


def from_dict(state, data: dict) -> "list[str]":
    """Rebuild the project on ``state``. Returns the warnings it collected."""
    from qgis.core import (QgsCoordinateReferenceSystem,          # noqa: PLC0415
                           QgsRasterLayer)

    from ..forest.reforestation import curves as curves_mod       # noqa: PLC0415
    from ..forest.reforestation import natural as natural_mod     # noqa: PLC0415
    from ..forest.reforestation import spacing as spacing_mod     # noqa: PLC0415
    from ..forest.reforestation import species as species_mod     # noqa: PLC0415
    from ..forest.reforestation import terrain as terrain_mod     # noqa: PLC0415
    from ..forest.reforestation import zones as zones_mod         # noqa: PLC0415

    _check(data)
    warnings = []
    state.clear()

    area = data.get("area") or {}
    geometry = geometry_in(area.get("geometry"))
    crs = QgsCoordinateReferenceSystem(str(area.get("crs") or ""))
    if geometry is not None and crs.isValid():
        state.set_area(geometry, crs, str(area.get("label") or ""))
    elif geometry is not None:
        warnings.append("Il file non dichiara un sistema di riferimento "
                        "valido: l'area non e' stata caricata.")

    for rule in data.get("constraints") or ():
        key = str(rule.get("key") or "")
        if not key:
            continue
        state.constraints.declare(key, _number(rule.get("buffer_m")),
                                  label=str(rule.get("label") or ""))
        for wkt in rule.get("geometries") or ():
            shape = geometry_in(wkt)
            if shape is not None:
                state.constraints.add_geometry(key, shape)
    state.apply_constraints()

    terrain = data.get("terrain") or {}
    source = str(terrain.get("source") or "")
    state.contour_interval_m = _number(terrain.get("contour_interval_m"),
                                       curves_mod.DEFAULT_INTERVAL_M)
    if source and state.area is not None:
        if os.path.exists(source.split("|", 1)[0]):
            layer = QgsRasterLayer(source, "DEM")
            try:
                state.terrain = terrain_mod.TerrainAnalysis.from_layer(
                    layer, state.crs, state.usable_geometry(), margin_m=50.0)
            except GeoCadError as exc:
                warnings.append("DEM non rileggibile: {0}".format(
                    exc.user_message))
        else:
            warnings.append(
                "Il DEM del progetto non si trova piu' in {0}: il progetto "
                "e' stato aperto senza terreno.".format(source))

    state.contours = [
        curves_mod.ContourRow(elevation_m=_number(row.get("elevation_m")),
                              geometry=geometry_in(row.get("geometry")),
                              length_m=_number(row.get("length_m")))
        for row in data.get("contours") or ()
        if geometry_in(row.get("geometry")) is not None]

    for entry in data.get("species") or ():
        key = str(entry.get("key") or "")
        if key and not state.catalog.has(key):
            state.catalog.add(species_mod.Species(
                key=key, name=str(entry.get("name") or ""),
                min_distance_m=_number(entry.get("min_distance_m"))))
    state.shares = [(str(key), _number(percent))
                    for key, percent in data.get("shares") or ()]

    state.spec = spec_in(data.get("spec"))
    state.natural = natural_in(data.get("natural"))

    if state.area is not None:
        state.zones.container = state.usable_geometry()
    for entry in data.get("zones") or ():
        shape = geometry_in(entry.get("geometry"))
        if shape is None:
            continue
        try:
            state.zones.add(zones_mod.Zone(
                name=str(entry.get("name") or ""), geometry=shape,
                spec=spec_in(entry.get("spec")),
                shares=tuple(tuple(share) for share in
                             entry.get("shares") or ())))
        except GeoCadError as exc:
            warnings.append("Zona non ricaricata: {0}".format(
                exc.user_message))

    state.glades = [
        natural_mod.Glade(geometry=geometry_in(entry.get("geometry")),
                          label=str(entry.get("label") or ""),
                          radius_m=_number(entry.get("radius_m")))
        for entry in data.get("glades") or ()
        if geometry_in(entry.get("geometry")) is not None]

    plants = plants_in(data.get("plants"))
    if plants:
        state.result = spacing_mod.SlopeGridResult(
            plants=plants, spec=state.spec,
            usable_area_m2=_number(data.get("usable_area_m2")))
        state.composition = None
    flags = data.get("flags") or {}
    state.scheme_chosen = bool(flags.get("scheme_chosen"))
    state.orientation_applied = bool(flags.get("orientation_applied"))
    state.glades_placed = bool(flags.get("glades_placed"))
    state.edited = bool(flags.get("edited"))
    return warnings


def save(state, path: str, with_plants: bool = True) -> str:
    """Write the project. Adds the suffix when the operator left it off."""
    if not path:
        raise InvalidInputError(
            "no path to save to",
            user_message="Nessun percorso in cui salvare il progetto.")
    if not os.path.splitext(path)[1]:
        path += SUFFIX
    data = to_dict(state, with_plants=with_plants)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
    except OSError as exc:
        raise ProjectFileError(
            "cannot write {0}: {1}".format(path, exc),
            user_message="Impossibile scrivere il progetto.",
            hint=str(exc)) from exc
    return path


def load(state, path: str) -> "list[str]":
    """Read a project onto ``state``. Returns the warnings."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise ProjectFileError(
            "cannot read {0}: {1}".format(path, exc),
            user_message="Impossibile leggere il progetto.",
            hint=str(exc)) from exc
    except ValueError as exc:
        raise ProjectFileError(
            "{0} is not valid JSON: {1}".format(path, exc),
            user_message="Il file di progetto e' danneggiato.",
            hint=str(exc)) from exc
    return from_dict(state, data)


def describe(path: str, warnings=()) -> "list[str]":
    lines = ["PROGETTO", "  File: {0}".format(path)]
    for warning in warnings:
        lines.append("  ! {0}".format(warning))
    return lines


__all__ = ["FORMAT_VERSION", "FORMAT_MARKER", "SUFFIX", "ProjectFileError",
           "to_dict", "from_dict", "save", "load", "describe",
           "geometry_out", "geometry_in", "spec_out", "spec_in",
           "plants_out", "plants_in"]
