# GeoCad UAV Toolkit

A QGIS plugin for integrated geospatial work: technical CAD drawing,
reforestation design, and UAV photogrammetric mission planning. The three
modules share one geometry engine, one terrain model and one cadastral
service.

Tested on **QGIS 3.40.15 LTR** (PyQt5) and **QGIS 4.0.0** (PyQt6) from the
same code base.

> The plugin's interface, reports and deliverables are in Italian; this
> README and the plugin metadata are in English.

---

## Modules

### CAD

Parametric geometry — square, rectangle, regular polygon, circle, arc, line,
polyline — with dimensional input (side, angle, radius) and native QGIS
snapping. Move, rotate and resize preserve the construction parameters, which
are stored beside the feature rather than in columns.

Every shape placed queries the Italian cadastre **on its own geometry**, not
on a point: the attribute table keeps exactly five columns (Area, Perimeter,
Municipality, Sheet, Parcel) and the full reading — every municipality, every
sheet, every parcel, with cadastral area, intersected area and both
percentages — stays in the panel, on the map and in the CSV export.

### Reforestation

A sixteen-step workflow: area definition, cadastral data, elevation model,
constraints and setbacks, homogeneous zones, species catalogue and
composition, density and planting pattern with trigonometric slope
correction, row orientation, plant generation, naturalistic pass (glades,
irregularity, per-species minimum distances), comparative optimisation,
anomaly checks, plant editing on the layer, map composition and deliverables.

### UAV

Photogrammetric planning in twelve steps: mission area from a polygon or from
a linear axis for corridors, drone and sensor libraries, bidirectional GSD
calculator (flight height and ground resolution are two ends of the same
optical relation), real terrain following on the DEM with an elevation
profile, acquisition parameters and overlaps, strip generation with a
measured azimuth, waypoints and exposure stations, safety checks against
obstacles and endurance, mission preview on the map, flight simulation with
ground footprints and live coverage, pre-flight validation and export.

---

## Cadastre

Queries the INSPIRE WFS service of the Italian *Agenzia delle Entrate*, with
**real geometric intersection** — never a centroid, never a nearest feature.
A geometry spanning several municipalities returns all of them, every sheet
and every parcel involved, each with:

| | |
|---|---|
| cadastral area | the area of the whole parcel |
| intersected area | the area actually covered by the project |
| % of parcel | intersected / cadastral |
| % of project | intersected / project area |

Belfiore codes are resolved to municipality names from a table shipped with
the plugin. Large bounding boxes are tiled, merged and deduplicated on the
cadastral identifier, so a provider that stops at its own feature ceiling
cannot silently truncate the answer.

## DEM and terrain

One terrain model for the whole plugin: slope and aspect for the planting
pattern, flight height for the UAV mission. The raster is reprojected from
the CRS **the QGIS layer declares** — the one QGIS draws it with — so a DEM
whose `.prj` is missing or wrong, corrected in the layer properties, is
sampled where the operator sees it. Waypoint heights really follow the
ground; no-data holes are flagged, never extrapolated.

## Cartography and export

Map sheets on `QgsPrintLayout` from A4 to A0, portrait and landscape, with
legend, scale bar, north arrow and grid. Reports in PDF, Word and Excel.
Missions in GeoPackage, GeoJSON, KML, KMZ, GPX, CSV, Litchi, MAVLink and
native DJI WPML — the last one restricted to the aircraft DJI documents as
compatible.

---

## Installation

**From a ZIP.** Download the release archive and use
*Plugins → Manage and Install Plugins → Install from ZIP*.

**From source.** Copy the `geocad_uav/` folder into your QGIS profile's
`python/plugins/` directory, then enable *GeoCad UAV Toolkit* in the plugin
manager.

Building the archive from a checkout:

```bash
python zip_plugin.py
```

It writes `dist/geocad_uav-<version>.zip` and refuses to package metadata
that QGIS or the official plugin repository would reject.

## Requirements

No pip dependencies beyond what QGIS already ships (NumPy, GDAL, Qt). The
cadastral module needs network access to the Agenzia delle Entrate WFS
service; every other module works offline.

## Tests

```bash
"C:\Program Files\QGIS 3.40.15\bin\python-qgis-ltr.bat" run_tests.py --all
"C:\Program Files\QGIS 4.0.0\bin\python-qgis.bat" run_tests.py --all
```

44 suites. The QGIS-dependent ones need the QGIS Python; `run_tests.py`
without `--all` runs only the pure-Python ones.

## Repository layout

```
geocad_uav/        the plugin itself — this folder is what ships
  cad/             parametric primitives, modifiers, map tools
  core/            geometry, CRS, terrain model, units, constants
  forest/          reforestation engine
  gui/             docks, panels, simulator, charts
  io/              cadastre, layers, documents, cartography, DEM sources
  processing/      QGIS Processing provider
  profiles/        drone and camera libraries
  tests/           the test suites (excluded from the packaged ZIP)
  uav/             survey, photogrammetry, terrain following, export
run_tests.py       the test runner
zip_plugin.py      the packager
CHANGELOG.md       release history — deliberately not in the plugin metadata
```

## Author

Created by **Cap. Niccolò Marco Mancini — RGPBIO**.

## Licence

Released under the **GNU General Public License, version 2 or (at your
option) any later version** — the full text is in [LICENSE](LICENSE), and a
copy ships inside the plugin package as required by the QGIS plugin
repository.

QGIS plugins link against PyQt and the QGIS API, both GPL, so a
GPL-compatible licence is not a preference here but a condition.

Copyright (C) 2026 Cap. Niccolò Marco Mancini — RGPBIO.
