"""
Mission report: HTML for the dock panel and for export.

Pure string generation -- no Qt, no matplotlib. The elevation profile is drawn
as inline SVG, which keeps the plugin's dependency list at "QGIS and Qt" (spec
P9) and makes the report a single self-contained file that can be emailed.

The report is written in Italian, matching the rest of the operator-facing UI.
"""

from __future__ import annotations

import html
import math
from datetime import datetime
from typing import Optional

import numpy as np

from ..core.models import AltitudeMode, Mission, VerticalDatum
from ..core.units import format_duration

_CSS = """
body { font-family: "Segoe UI", system-ui, sans-serif; font-size: 13px;
       color: #1c1c1c; background: #ffffff; margin: 0; padding: 16px; }
h1 { font-size: 19px; margin: 0 0 2px 0; }
h2 { font-size: 14px; margin: 22px 0 8px 0; padding-bottom: 4px;
     border-bottom: 1px solid #d8d8d8; text-transform: uppercase;
     letter-spacing: .04em; color: #444; }
.sub { color: #666; font-size: 12px; margin-bottom: 4px; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px 0; }
td, th { padding: 4px 8px; text-align: left; vertical-align: top;
         border-bottom: 1px solid #ededed; }
th { width: 42%; font-weight: 600; color: #333; }
td.v { font-variant-numeric: tabular-nums; }
.kpi { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
.kpi div { flex: 1 1 128px; border: 1px solid #e0e0e0; border-radius: 5px;
           padding: 8px 10px; background: #fafafa; }
.kpi .n { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
.kpi .l { font-size: 11px; color: #666; text-transform: uppercase;
          letter-spacing: .03em; }
ul { margin: 4px 0 4px 0; padding-left: 20px; }
li { margin: 3px 0; }
.err  { color: #a4262c; }
.warn { color: #8a6100; }
.ok   { color: #16704a; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
         font-size: 11px; font-weight: 600; }
.badge.ok   { background: #e3f4ec; color: #16704a; }
.badge.warn { background: #fdf3e0; color: #8a6100; }
.badge.err  { background: #fbe6e7; color: #a4262c; }
.note { background: #f6f7f9; border-left: 3px solid #b9c0cc; padding: 8px 10px;
        margin: 8px 0; font-size: 12px; color: #333; }
svg { border: 1px solid #e0e0e0; border-radius: 4px; background: #fcfcfc; }
.legend span { margin-right: 14px; font-size: 11px; color: #555; }
.sw { display: inline-block; width: 11px; height: 3px; vertical-align: middle;
      margin-right: 4px; }
"""


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def _fmt(value, decimals=2, suffix="", dash="n/d") -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return dash
    if not math.isfinite(value):
        return dash
    return "{0:,.{1}f}{2}".format(value, decimals, suffix)


def _rows(pairs) -> str:
    return "\n".join(
        "<tr><th>{0}</th><td class='v'>{1}</td></tr>".format(_e(k), _e(v))
        for k, v in pairs)


# --------------------------------------------------------------------------
# Elevation profile
# --------------------------------------------------------------------------

def elevation_profile_svg(mission: Mission, width: int = 900,
                          height: int = 240) -> str:
    """Terrain vs commanded height along the whole route, as inline SVG.

    This is the picture that shows terrain following actually happened: the two
    curves stay parallel. On a fixed-AMSL plan the flight line would be flat
    while the ground moves, and the gap between them (the AGL) would open and
    close -- which is exactly the GSD and overlap collapse the mode exists to
    prevent.
    """
    rows = mission.profile
    if len(rows) < 2:
        return "<p class='sub'>Profilo altimetrico non disponibile.</p>"

    # Legs restart their chainage at 0; accumulate into one continuous axis.
    s = np.array([r["s"] for r in rows], dtype=float)
    ground = np.array([r["z_ground"] for r in rows], dtype=float)
    flight = np.array([r["z_flight"] for r in rows], dtype=float)
    offset = 0.0
    cumulative = np.empty_like(s)
    previous = -math.inf
    for i, value in enumerate(s):
        if value < previous:
            offset += previous
        cumulative[i] = offset + value
        previous = value

    finite = np.isfinite(ground) & np.isfinite(flight)
    if not finite.any():
        return "<p class='sub'>Profilo altimetrico non disponibile.</p>"
    cumulative, ground, flight = cumulative[finite], ground[finite], flight[finite]

    # Downsample for a sane SVG size; keep the extremes of each bucket so a
    # ridge is never smoothed away.
    max_points = 1200
    if cumulative.size > max_points:
        idx = np.linspace(0, cumulative.size - 1, max_points).astype(int)
        cumulative, ground, flight = cumulative[idx], ground[idx], flight[idx]

    pad_l, pad_r, pad_t, pad_b = 52, 12, 12, 30
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    x_min, x_max = float(cumulative.min()), float(cumulative.max())
    y_min = float(min(ground.min(), flight.min()))
    y_max = float(max(ground.max(), flight.max()))
    span = max(y_max - y_min, 1.0)
    y_min -= span * 0.08
    y_max += span * 0.08
    x_span = max(x_max - x_min, 1.0)

    def px(v):
        return pad_l + (float(v) - x_min) / x_span * plot_w

    def py(v):
        return pad_t + (y_max - float(v)) / (y_max - y_min) * plot_h

    def path(values):
        return " ".join("{0}{1:.1f},{2:.1f}".format("M" if i == 0 else "L",
                                                    px(cumulative[i]), py(values[i]))
                        for i in range(values.size))

    ground_fill = ("M{0:.1f},{1:.1f} ".format(px(cumulative[0]), py(ground[0]))
                   + " ".join("L{0:.1f},{1:.1f}".format(px(cumulative[i]),
                                                        py(ground[i]))
                              for i in range(1, ground.size))
                   + " L{0:.1f},{1:.1f} L{2:.1f},{1:.1f} Z".format(
                       px(cumulative[-1]), pad_t + plot_h, px(cumulative[0])))

    ticks = []
    for k in range(5):
        value = y_min + (y_max - y_min) * k / 4.0
        y = py(value)
        ticks.append(
            "<line x1='{0}' y1='{1:.1f}' x2='{2}' y2='{1:.1f}' "
            "stroke='#ececec'/>"
            "<text x='{3}' y='{4:.1f}' font-size='10' fill='#777' "
            "text-anchor='end'>{5:.0f}</text>".format(
                pad_l, y, pad_l + plot_w, pad_l - 6, y + 3, value))
    for k in range(5):
        value = x_min + x_span * k / 4.0
        x = px(value)
        ticks.append(
            "<text x='{0:.1f}' y='{1}' font-size='10' fill='#777' "
            "text-anchor='middle'>{2:,.0f} m</text>".format(
                x, pad_t + plot_h + 18, value))

    return (
        "<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}' "
        "xmlns='http://www.w3.org/2000/svg' role='img' "
        "aria-label='Profilo altimetrico: terreno e quota di volo'>"
        "{ticks}"
        "<path d='{fill}' fill='#e8e2d6' stroke='none'/>"
        "<path d='{gnd}' fill='none' stroke='#8a7a5c' stroke-width='1.4'/>"
        "<path d='{fly}' fill='none' stroke='#1d6fb8' stroke-width='1.6'/>"
        "</svg>"
        "<div class='legend'>"
        "<span><i class='sw' style='background:#8a7a5c'></i>Terreno</span>"
        "<span><i class='sw' style='background:#1d6fb8'></i>Quota di volo</span>"
        "<span>AGL nominale {agl:.0f} m</span></div>".format(
            w=width, h=height, ticks="".join(ticks), fill=ground_fill,
            gnd=path(ground), fly=path(flight), agl=mission.h_agl_m))


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def build_html(mission: Mission, params=None, validation=None,
               geometry=None, title: str = "Piano di volo UAV") -> str:
    """Full mission report as a standalone HTML document."""
    st = mission.stats
    if geometry is None and params is not None:
        from ..uav import photogrammetry as pg
        geometry = pg.solve_survey_geometry(
            params.camera, params.overlap, h_agl_m=mission.h_agl_m,
            orientation=params.orientation)

    if validation is not None:
        if validation.errors:
            badge = "<span class='badge err'>{0}</span>".format(
                _e(validation.summary()))
        elif validation.warnings:
            badge = "<span class='badge warn'>{0}</span>".format(
                _e(validation.summary()))
        else:
            badge = "<span class='badge ok'>{0}</span>".format(
                _e(validation.summary()))
    else:
        badge = ""

    kpi = [
        ("{0:,.2f}".format(st.aoi_area_ha), "ettari AOI"),
        ("{0:,}".format(st.n_photos), "foto"),
        ("{0:,}".format(st.n_waypoints), "waypoint"),
        ("{0:,}".format(st.n_strips), "strip"),
        ("{0:,.0f}".format(st.total_length_m), "metri di volo"),
        (format_duration(st.flight_time_s), "tempo stimato"),
        ("{0}".format(st.n_batteries), "batterie"),
        ("{0:.2f}".format(mission.gsd_m * 100.0), "cm/px GSD"),
    ]
    kpi_html = "".join(
        "<div><div class='n'>{0}</div><div class='l'>{1}</div></div>".format(
            _e(n), _e(l)) for n, l in kpi)

    mission_rows = [
        ("Modalita' di quota", AltitudeMode.LABELS.get(
            mission.altitude_mode, mission.altitude_mode)),
        ("Quota di volo H_AGL", _fmt(mission.h_agl_m, 1, " m")),
        ("Margine di sicurezza", _fmt(mission.safety_margin_m, 1, " m")),
        ("Clearance vegetazione", _fmt(mission.vegetation_clearance_m, 1, " m")),
        ("GSD nominale", _fmt(mission.gsd_m * 100.0, 3, " cm/px")),
        ("GSD effettivo (min - max)", "{0} - {1} cm/px".format(
            _fmt(st.gsd_min_m * 100.0 if math.isfinite(st.gsd_min_m) else None, 3),
            _fmt(st.gsd_max_m * 100.0 if math.isfinite(st.gsd_max_m) else None, 3))),
        ("Sovrapposizione longitudinale", _fmt(mission.frontlap * 100.0, 0, " %")),
        ("Sovrapposizione laterale", _fmt(mission.sidelap * 100.0, 0, " %")),
        ("Azimut delle strip", _fmt(mission.azimuth_deg, 1, " deg")),
        ("Schema di volo", mission.pattern),
        ("Velocita' effettiva", _fmt(mission.speed_ms, 1, " m/s")),
    ]
    if geometry is not None:
        mission_rows[6:6] = [
            ("Impronta a terra W x L", "{0} x {1} m".format(
                _fmt(geometry.footprint_across_m, 1),
                _fmt(geometry.footprint_along_m, 1))),
            ("Interasse fra strip D_side", _fmt(geometry.d_side_m, 2, " m")),
            ("Base di presa D_front", _fmt(geometry.d_front_m, 2, " m")),
        ]

    camera_rows = []
    if params is not None:
        cam = params.camera
        camera_rows = [
            ("Camera", cam.name),
            ("Focale", _fmt(cam.focal_mm, 2, " mm")),
            ("Sensore", "{0} x {1} mm".format(_fmt(cam.sensor_w_mm, 2),
                                              _fmt(cam.sensor_h_mm, 2))),
            ("Immagine", "{0} x {1} px ({2:.1f} MP)".format(
                cam.image_w_px, cam.image_h_px, cam.megapixels)),
            ("Passo pixel", _fmt(cam.pitch_mm, 5, " mm")),
            ("Tempo di scatto", "1/{0:.0f} s".format(1.0 / cam.shutter_s)
             if cam.shutter_s > 0 else "n/d"),
            ("Intervallo minimo", _fmt(cam.min_interval_s, 2, " s")),
            ("Fonte parametri", cam.source or "non dichiarata"),
            ("Drone", params.drone.name),
            ("Autonomia utile", "{0:.0f} min su {1:.0f} min "
                                "(riserva RTH {2:.0f} %)".format(
                 params.drone.usable_endurance_s / 60.0,
                 params.drone.endurance_min, params.drone.rth_reserve_pct)),
            ("Limite waypoint", str(params.drone.waypoint_limit)),
        ]

    terrain_rows = [
        ("Quota terreno min - max", "{0} - {1} m".format(
            _fmt(st.terrain_z_min, 1), _fmt(st.terrain_z_max, 1))),
        ("Dislivello dell'area", _fmt(st.terrain_relief_m, 1, " m")),
        ("AGL min - max", "{0} - {1} m".format(_fmt(st.agl_min, 1),
                                               _fmt(st.agl_max, 1))),
        ("CRS orizzontale", mission.crs_authid or "n/d"),
        ("Datum verticale", VerticalDatum.label(mission.vertical_datum)),
        ("Ondulazione geoide applicata", _fmt(mission.geoid_undulation_m, 2, " m")),
    ]
    if math.isfinite(st.coverage_pct):
        terrain_rows.insert(0, ("Copertura AOI (su impronte drappeggiate)",
                                _fmt(st.coverage_pct, 2, " %")))
        terrain_rows.insert(1, ("Foto minime per punto",
                                str(st.min_photos_observed)))

    parts = [
        "<!DOCTYPE html><html lang='it'><head><meta charset='utf-8'>",
        "<title>{0}</title><style>{1}</style></head><body>".format(
            _e(title), _CSS),
        "<h1>{0}</h1>".format(_e(title)),
        "<div class='sub'>GeoCad UAV Toolkit &middot; {0} &nbsp; {1}</div>".format(
            _e(datetime.now().strftime("%Y-%m-%d %H:%M")), badge),
        "<div class='kpi'>{0}</div>".format(kpi_html),
        "<h2>Profilo altimetrico</h2>",
        elevation_profile_svg(mission),
        "<h2>Missione</h2><table>{0}</table>".format(_rows(mission_rows)),
    ]
    if camera_rows:
        parts.append("<h2>Camera e drone</h2><table>{0}</table>".format(
            _rows(camera_rows)))
    parts.append("<h2>Terreno e riferimenti</h2><table>{0}</table>".format(
        _rows(terrain_rows)))

    if validation is not None:
        parts.append("<h2>Validazione</h2>")
        blocking = validation.errors
        warn = validation.warnings
        if blocking:
            parts.append("<ul>" + "".join(
                "<li class='err'><b>{0}</b>: {1}</li>".format(
                    _e(c.label), _e(c.detail)) for c in blocking) + "</ul>")
        if warn:
            parts.append("<ul>" + "".join(
                "<li class='warn'><b>{0}</b>: {1}</li>".format(
                    _e(c.label), _e(c.detail)) for c in warn) + "</ul>")
        passed = [c for c in validation.checks if c.passed]
        parts.append("<table>" + "".join(
            "<tr><th>{0}</th><td class='v ok'>{1}</td></tr>".format(
                _e(c.label), _e(c.value or "superato")) for c in passed)
            + "</table>")

    if mission.warnings:
        parts.append("<h2>Avvisi di pianificazione</h2><ul>" + "".join(
            "<li class='warn'>{0}</li>".format(_e(w))
            for w in mission.warnings) + "</ul>")

    if mission.assumptions:
        parts.append("<h2>Assunzioni dichiarate</h2><ul>" + "".join(
            "<li>{0}</li>".format(_e(a)) for a in mission.assumptions) + "</ul>")

    parts.append(
        "<div class='note'><b>Prima del volo.</b> Verifica in campo il punto di "
        "decollo, la quota RTH, gli ostacoli non presenti nel modello di "
        "elevazione e le autorizzazioni dello spazio aereo. Le quote di questo "
        "piano sono espresse nel datum verticale dichiarato sopra: assicurati "
        "che coincida con quello atteso dal firmware del drone.</div>")
    parts.append("</body></html>")
    return "\n".join(parts)


def save_html(mission: Mission, path: str, params=None, validation=None,
              geometry=None, overwrite: bool = False) -> str:
    """Write the report. Refuses to overwrite unless told to."""
    import os

    from ..core.errors import ExportError

    if os.path.exists(path) and not overwrite:
        raise ExportError(
            "refusing to overwrite {0}".format(path),
            user_message="Il report esiste gia'.",
            hint="Conferma la sovrascrittura o scegli un altro nome.")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(build_html(mission, params, validation, geometry))
    except OSError as exc:
        raise ExportError("cannot write report: {0}".format(exc),
                          user_message="Impossibile scrivere il report.",
                          hint=str(exc)) from exc
    return path


def build_text_summary(mission: Mission, validation=None) -> "list[str]":
    """Compact Italian summary for the dock panel."""
    st = mission.stats
    lines = [
        "AOI {0:,.2f} ha  |  {1} strip  |  {2:,} foto  |  {3:,} waypoint".format(
            st.aoi_area_ha, st.n_strips, st.n_photos, st.n_waypoints),
        "H_AGL {0:.0f} m  |  GSD {1:.2f} cm/px  |  V {2:.1f} m/s".format(
            mission.h_agl_m, mission.gsd_m * 100.0, mission.speed_ms),
        "Volo {0:,.0f} m  |  {1}  |  {2} batteria/e".format(
            st.total_length_m, format_duration(st.flight_time_s), st.n_batteries),
        "Terreno {0:.0f} - {1:.0f} m (dislivello {2:.0f} m)  |  AGL {3:.0f} - "
        "{4:.0f} m".format(st.terrain_z_min, st.terrain_z_max,
                           st.terrain_relief_m, st.agl_min, st.agl_max),
    ]
    if math.isfinite(st.coverage_pct):
        lines.append("Copertura {0:.2f} % con almeno {1} foto per punto".format(
            st.coverage_pct, st.min_photos_observed))
    if validation is not None:
        lines.append(validation.summary())
    return lines
