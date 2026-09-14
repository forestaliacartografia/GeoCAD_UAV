"""
Export tab: what is in the dock ends up in the file, or the format says it does not.

Every writer already exists in ``uav.export`` and is frozen. This panel picks
the mission, the destination and the formats, shows the paths *before* writing
anything, runs ``uav.validator`` and refuses to export a mission that has
errors. It writes through ``uav.export.write`` and nowhere else.

Three rules the UI has to make visible rather than assume:

* **The badge is the format's own claim, and the suite proves it.**
  ``ExportFormat.verified`` is set in the frozen table; the panel prints it as
  VERIFIED, and ``tests/test_export_panel.py`` round-trips every format the
  panel offers. Anything in ``uav.export.NOT_IMPLEMENTED`` -- DJI WPML today --
  is listed as UNSUPPORTED with its reason, and cannot be selected.
* **Nothing is written until Esporta.** The preview is a list of paths.
* **The altitude reference comes from Settings**, is shown in full, and when it
  is relative to take-off the reference elevation is named, not guessed
  silently.
"""

from __future__ import annotations

import os

from qgis.core import (Qgis, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsProject)
from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtWidgets import (QComboBox, QFileDialog, QFormLayout,
                                 QGroupBox,
                                 QHBoxLayout, QLabel, QLineEdit, QListWidget,
                                 QListWidgetItem, QPushButton, QTextBrowser,
                                 QVBoxLayout, QWidget)

from ..core.errors import GeoCadError, swallow
from ..settings import settings as app_settings
from ..uav import export as ex
from ..uav import validator as val


def tr(text):
    return QCoreApplication.translate("GeoCadUav", text)


VERIFIED = "VERIFIED"
PARTIAL = "PARTIAL"
UNSUPPORTED = "UNSUPPORTED"

#: Altitude references offered by uav.export, in the operator's words.
ALT_LABELS = {
    ex.ALT_AMSL: "Quota assoluta (AMSL, datum della missione)",
    ex.ALT_RELATIVE_HOME: "Quota relativa al punto di decollo",
    ex.ALT_ELLIPSOIDAL: "Quota ellissoidica (AMSL + ondulazione del geoide)",
}


def format_status(key: str) -> str:
    """VERIFIED / PARTIAL / UNSUPPORTED for one format key."""
    if key in ex.NOT_IMPLEMENTED:
        return UNSUPPORTED
    fmt = ex.FORMATS.get(key)
    if fmt is None:
        return UNSUPPORTED
    return VERIFIED if fmt.verified else PARTIAL


class ExportPanel(QWidget):
    """Destination, formats, validation, one write per format."""

    def __init__(self, iface, mission_provider=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        #: Callable returning the mission to export. The UAV panel owns it;
        #: this panel only reads it, and never writes it back.
        self.mission_provider = mission_provider or (lambda: None)
        self._connections = []
        self.last_report = None
        self.last_written = []
        self._build()
        self.refresh()

    # -- construction ------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        source_box = QGroupBox(tr("Missione"))
        source_form = QFormLayout(source_box)
        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        source_form.addRow(self.source_label)
        self.altitude_label = QLabel()
        self.altitude_label.setWordWrap(True)
        source_form.addRow(tr("Riferimento di quota"), self.altitude_label)
        layout.addWidget(source_box)

        dest_box = QGroupBox(tr("Destinazione"))
        dest_form = QFormLayout(dest_box)
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.browse_button = QPushButton(tr("Sfoglia..."))
        folder_row.addWidget(self.folder_edit)
        folder_row.addWidget(self.browse_button)
        dest_form.addRow(tr("Cartella"), folder_row)
        self.basename_edit = QLineEdit("missione")
        dest_form.addRow(tr("Nome base"), self.basename_edit)

        # The altitude reference. Read from the store as before, but
        # settable here: WPML accepts only two of the three, so the choice
        # has to be reachable.
        self.altitude_combo = QComboBox()
        for key in (ex.ALT_AMSL, ex.ALT_RELATIVE_HOME, ex.ALT_ELLIPSOIDAL):
            self.altitude_combo.addItem(tr(ALT_LABELS[key]), key)
        index = self.altitude_combo.findData(
            app_settings.get("export/altitude_mode"))
        if index >= 0:
            self.altitude_combo.setCurrentIndex(index)
        self.altitude_combo.setToolTip(tr(
            "Riferimento delle quote nei file esportati. DJI WPML non ha un "
            "riferimento ortometrico: con 'quota assoluta' lo rifiuta."))
        dest_form.addRow(tr("Riferimento quote"), self.altitude_combo)
        layout.addWidget(dest_box)

        formats_box = QGroupBox(tr("Formati"))
        formats_layout = QVBoxLayout(formats_box)
        self.format_list = QListWidget()
        self.format_list.setMinimumHeight(190)
        self._fill_formats()
        formats_layout.addWidget(self.format_list)
        self.format_note = QLabel()
        self.format_note.setWordWrap(True)
        self.format_note.setStyleSheet("color:#666;")
        formats_layout.addWidget(self.format_note)
        layout.addWidget(formats_box)

        self.report = QTextBrowser()
        self.report.setMinimumHeight(170)
        layout.addWidget(self.report)

        buttons = QHBoxLayout()
        self.preview_button = QPushButton(tr("Anteprima dei file"))
        self.export_button = QPushButton(tr("Esporta"))
        buttons.addWidget(self.preview_button)
        buttons.addWidget(self.export_button)
        layout.addLayout(buttons)

        for widget, signal_name, slot in (
                (self.folder_edit, "textChanged", self.refresh),
                (self.basename_edit, "textChanged", self.refresh),
                (self.format_list, "itemChanged", self.refresh),
                (self.altitude_combo, "currentIndexChanged",
                 self._on_altitude_changed),
                (self.format_list, "currentRowChanged", self._show_note),
                (self.browse_button, "clicked", self._pick_folder),
                (self.preview_button, "clicked", self.refresh),
                (self.export_button, "clicked", self.export)):
            signal = getattr(widget, signal_name)
            signal.connect(slot)
            self._connections.append((signal, slot))

    def _fill_formats(self):
        """Every writer the engine really has, plus what it refuses, with why."""
        for key in sorted(ex.FORMATS):
            fmt = ex.FORMATS[key]
            item = QListWidgetItem("{0}  [{1}]  {2}".format(
                fmt.label, format_status(key), fmt.extension))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setToolTip("{0}\n{1}".format(fmt.schema_source, fmt.notes))
            self.format_list.addItem(item)

        for key, (label, reason) in sorted(ex.NOT_IMPLEMENTED.items()):
            item = QListWidgetItem("{0}  [{1}]".format(label, UNSUPPORTED))
            item.setData(Qt.ItemDataRole.UserRole, key)
            # Not checkable and not selectable for export: the reason is the
            # product here, not a disabled-looking button.
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setToolTip(reason)
            self.format_list.addItem(item)

        default = app_settings.get("export/format")
        for row in range(self.format_list.count()):
            item = self.format_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == default \
                    and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Checked)

    # -- state -------------------------------------------------------------

    def mission(self):
        try:
            return self.mission_provider()
        except Exception:                                       # noqa: BLE001
            return None

    def selected_keys(self):
        """Checked, writable format keys, in list order."""
        keys = []
        for row in range(self.format_list.count()):
            item = self.format_list.item(row)
            key = item.data(Qt.ItemDataRole.UserRole)
            if key in ex.NOT_IMPLEMENTED:
                continue
            if item.checkState() == Qt.CheckState.Checked:
                keys.append(key)
        return keys

    def altitude_mode(self) -> str:
        """What the operator chose, falling back to the stored default."""
        chosen = self.altitude_combo.currentData()
        if chosen in ALT_LABELS:
            return chosen
        mode = app_settings.get("export/altitude_mode")
        return mode if mode in ALT_LABELS else ex.ALT_AMSL

    def _on_altitude_changed(self, *_args) -> None:
        """Remember the choice, and re-read what it changes."""
        chosen = self.altitude_combo.currentData()
        if chosen in ALT_LABELS:
            app_settings.set("export/altitude_mode", chosen)
        self.refresh()

    def home_z(self):
        """Take-off elevation for a relative-altitude export, or None.

        The mission stores no home point, so the reference is the ground under
        the first waypoint -- ``z_amsl - z_agl``. That is an assumption, and it
        is printed next to the altitude mode rather than applied silently.
        """
        mission = self.mission()
        if mission is None or not mission.waypoints:
            return None
        first = mission.waypoints[0]
        try:
            ground = float(first.z_amsl) - float(first.z_agl)
        except (TypeError, ValueError):
            return None
        return ground if ground == ground else None             # not NaN

    def target_folder(self) -> str:
        return self.folder_edit.text().strip()

    def basename(self) -> str:
        return (self.basename_edit.text().strip() or "missione")

    def planned_files(self):
        """``[(key, path)]`` exactly as they will be written. Writes nothing."""
        folder = self.target_folder()
        base = self.basename()
        planned = []
        for key in self.selected_keys():
            fmt = ex.FORMATS[key]
            name = "{0}_{1}{2}".format(base, key, fmt.extension)
            planned.append((key, os.path.join(folder, name)))
        return planned

    # -- validation --------------------------------------------------------

    def validate(self):
        """Run the frozen validator over the current mission, or None."""
        mission = self.mission()
        if mission is None:
            return None
        crs = (QgsCoordinateReferenceSystem(mission.crs_authid)
               if mission.crs_authid else None)
        return val.validate(mission, crs=crs)

    def _transform(self, mission):
        """Working CRS -> WGS84, for the formats that require degrees."""
        if not mission.crs_authid:
            return None
        source = QgsCoordinateReferenceSystem(mission.crs_authid)
        target = QgsCoordinateReferenceSystem("EPSG:4326")
        if not source.isValid() or source == target:
            return None
        return QgsCoordinateTransform(source, target, QgsProject.instance())

    # -- readiness and readout --------------------------------------------

    def readiness(self):
        """``(can_export, italian_reason)``. The reason is never empty."""
        mission = self.mission()
        if mission is None:
            return False, tr(
                "Nessuna missione da esportare: genera prima la rotta nella "
                "scheda UAV.")
        if not mission.waypoints:
            return False, tr("La missione non contiene waypoint.")
        if not self.selected_keys():
            return False, tr("Seleziona almeno un formato.")
        if not self.target_folder():
            return False, tr("Scegli la cartella di destinazione.")
        report = self.last_report
        if report is not None and report.errors:
            return False, tr(
                "La missione non supera la validazione: {0} errori. "
                "Risolvili prima di esportare.").format(len(report.errors))
        return True, ""

    def refresh(self, *_args):
        """Recompute the preview, the validation and the button state."""
        mission = self.mission()
        self.last_report = self.validate()

        if mission is None:
            self.source_label.setText(tr(
                "Nessuna missione. Genera la rotta nella scheda UAV."))
        else:
            self.source_label.setText(tr(
                "{0:,} waypoint, {1:,} scatti, CRS {2}.").format(
                    len(mission.waypoints), len(mission.photos),
                    mission.crs_authid or "?"))

        mode = self.altitude_mode()
        text = ALT_LABELS.get(mode, mode)
        if mode == ex.ALT_RELATIVE_HOME:
            reference = self.home_z()
            text += tr(" - riferimento: terreno sotto il primo waypoint"
                       " ({0}).").format(
                           "{0:.1f} m".format(reference) if reference is not None
                           else tr("non determinabile"))
        text += tr("  (si cambia in Impostazioni)")
        self.altitude_label.setText(text)

        ready, reason = self.readiness()
        self.export_button.setEnabled(ready)
        self.report.setHtml(self._report_html(ready, reason))
        return self.planned_files()

    def _report_html(self, ready, reason):
        parts = []
        planned = self.planned_files()
        if planned:
            parts.append("<p><b>{0}</b></p><ul>{1}</ul>".format(
                tr("Verranno scritti questi file:"),
                "".join("<li>{0}</li>".format(path) for _key, path in planned)))
        else:
            parts.append("<p style='color:#8a6100'>{0}</p>".format(
                tr("Nessun file selezionato.")))

        report = self.last_report
        if report is not None:
            colour = "#a4262c" if report.errors else (
                "#8a6100" if report.warnings else "#1a7f37")
            parts.append("<p style='color:{0}'><b>{1}</b></p>".format(
                colour, report.summary()))
            if report.errors:
                parts.append("<p><b>{0}</b></p><ul style='color:#a4262c'>{1}"
                             "</ul>".format(
                                 tr("Errori che bloccano l'export:"),
                                 "".join("<li>{0}: {1}</li>".format(
                                     check.label, check.detail)
                                     for check in report.errors)))
            if report.warnings:
                parts.append("<p><b>{0}</b></p><ul style='color:#8a6100'>{1}"
                             "</ul>".format(
                                 tr("Avvisi (l'export procede):"),
                                 "".join("<li>{0}: {1}</li>".format(
                                     check.label, check.detail)
                                     for check in report.warnings)))
        if not ready and reason:
            parts.append("<p style='color:#a4262c'>{0}</p>".format(reason))

        parts.append("<p style='color:#777;font-size:11px'>{0}</p>".format(
            "<br>".join(ex.describe_unimplemented())))
        return "".join(parts)

    def _show_note(self, *_args):
        item = self.format_list.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key in ex.NOT_IMPLEMENTED:
            label, reason = ex.NOT_IMPLEMENTED[key]
            self.format_note.setText("{0}: {1}".format(label, reason))
            return
        fmt = ex.FORMATS.get(key)
        if fmt is not None:
            self.format_note.setText("{0} - schema: {1}. {2}".format(
                fmt.label, fmt.schema_source, fmt.notes))

    def _pick_folder(self, *_args):
        folder = QFileDialog.getExistingDirectory(
            self, tr("Cartella di destinazione"), self.target_folder())
        if folder:
            self.folder_edit.setText(folder)

    # -- the write ---------------------------------------------------------

    def export(self, *_args):
        """Write every checked format. Returns the paths actually written."""
        self.last_written = []
        ready, reason = self.readiness()
        if not ready:
            self._notify(reason, Qgis.MessageLevel.Warning)
            self.refresh()
            return []

        mission = self.mission()
        transform = self._transform(mission)
        mode = self.altitude_mode()
        home = self.home_z() if mode == ex.ALT_RELATIVE_HOME else None

        written = []
        failures = []
        for key, path in self.planned_files():
            options = {"crs": mission.crs_authid} if key == "gpkg" else {}
            try:
                result = ex.write(mission, key, path, transform=transform,
                                  altitude_mode=mode, home_z=home,
                                  overwrite=True, **options)
                written.append(result)
            except GeoCadError as exc:
                failures.append("{0}: {1}".format(
                    ex.FORMATS[key].label, exc.formatted()))
            except Exception as exc:                            # noqa: BLE001
                failures.append("{0}: {1}".format(ex.FORMATS[key].label, exc))

        self.last_written = written
        if failures:
            self._notify(tr("Export incompleto: {0}").format(
                " | ".join(failures)), Qgis.MessageLevel.Critical)
        elif written:
            self._notify(tr("Scritti {0} file in {1}.").format(
                len(written), self.target_folder()), Qgis.MessageLevel.Success)
        self.refresh()
        return written

    def _notify(self, text, level=None):
        if self.iface is None:
            return
        try:
            self.iface.messageBar().pushMessage(
                tr("GeoCad UAV"), text,
                level=level if level is not None else Qgis.MessageLevel.Info)
        except Exception as exc:                                # noqa: BLE001
            swallow(exc, "export panel: message bar")

    def teardown(self):
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._connections = []
