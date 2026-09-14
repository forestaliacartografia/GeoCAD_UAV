"""
Download a DEM into the project, without blocking QGIS.

The dialog owns three things and nothing else: which adapter, which extent,
and the credential for the adapter that needs one. The request schema, the
cache and the transport live in ``io.dem_source``; the elevation model is still
``core.z.TerrainModel``, untouched.

Every download runs in a :class:`DemDownloadTask` on the QGIS task manager, so
the interface stays responsive and the operator can cancel. Cancelling leaves
no file: ``dem_source.fetch`` writes to ``<dest>.part`` and renames only at the
end.

The finished raster is added to the project. The UAV tab needs no change to see
it: its DEM combo is a ``QgsMapLayerComboBox`` over the project's rasters.
"""

from __future__ import annotations

from qgis.core import (Qgis, QgsApplication, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsMessageLog, QgsProject,
                       QgsTask)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                                 QFormLayout, QGroupBox, QLabel, QLineEdit,
                                 QProgressBar, QPushButton, QTextBrowser,
                                 QVBoxLayout)

from ..core.errors import swallow
from ..io import dem_source as ds
from ..settings import mask as mask_secret
from ..settings import settings as app_settings

LOG_TAG = "GeoCad UAV"


def tr(text):
    return QCoreApplication.translate("GeoCadUav", text)


def log(message, level=Qgis.MessageLevel.Info):
    """Log a line that has already been through the maskers."""
    QgsMessageLog.logMessage(ds.mask_text(str(message)), LOG_TAG, level)


class DemDownloadTask(QgsTask):
    """One download, off the GUI thread, cancellable.

    ``QgsTask`` already provides ``isCanceled()`` and ``setProgress()``, which
    is exactly the feedback protocol ``dem_source.fetch`` expects, so the task
    hands itself in as the feedback object.
    """

    def __init__(self, adapter_id, bbox, key, transport=None,
                 cache_dir=None, description=""):
        super().__init__(description or tr("Scarico il DEM"),
                         QgsTask.Flag.CanCancel)
        self.adapter_id = adapter_id
        self.bbox = tuple(bbox)
        # The credential lives on the task only for the length of the request
        # and is never logged, never put in a message, never in the exception.
        self._key = key
        self.transport = transport or ds.urllib_transport
        self.cache_dir = cache_dir
        self.path = None
        self.error = ""

    def run(self):                                              # noqa: D102
        try:
            self.path = ds.fetch(
                self.adapter_id, self.bbox, key=self._key,
                transport=self.transport, feedback=self,
                cache_dir=self.cache_dir)
        except Exception as exc:                                # noqa: BLE001
            self.error = ds.mask_text(
                getattr(exc, "user_message", "") or str(exc))
            return False
        finally:
            self._key = None
        return self.path is not None

    def cancel(self):                                           # noqa: D102
        super().cancel()


class DemDownloadDialog(QDialog):
    """Adapter, extent, key, one download."""

    def __init__(self, iface, parent=None):
        super().__init__(parent or (iface.mainWindow() if iface else None))
        self.iface = iface
        self.setWindowTitle(tr("GeoCad UAV - Scarica un DEM"))
        self.setMinimumWidth(560)
        self._task = None
        self.last_layer = None
        self._build()
        self._on_adapter_changed()

    # -- construction ------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)

        source_box = QGroupBox(tr("Fonte"))
        form = QFormLayout(source_box)
        self.adapter_combo = QComboBox()
        for adapter_id in ds.ADAPTER_ORDER:
            spec = ds.ADAPTERS[adapter_id]
            self.adapter_combo.addItem(
                "{0}  [{1}]".format(spec.label, spec.status), adapter_id)
        form.addRow(tr("Servizio"), self.adapter_combo)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_label = QLabel(tr("Chiave API"))
        form.addRow(self.key_label, self.key_edit)
        self.key_hint = QLabel()
        self.key_hint.setWordWrap(True)
        self.key_hint.setStyleSheet("color:#666;")
        form.addRow(self.key_hint)
        layout.addWidget(source_box)

        self.status_view = QTextBrowser()
        self.status_view.setOpenExternalLinks(True)
        self.status_view.setMinimumHeight(150)
        layout.addWidget(self.status_view)

        self.extent_label = QLabel()
        self.extent_label.setWordWrap(True)
        layout.addWidget(self.extent_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.download_button = QPushButton(tr("Scarica"))
        self.buttons.addButton(self.download_button,
                               QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = QPushButton(tr("Annulla il download"))
        self.cancel_button.setEnabled(False)
        self.buttons.addButton(self.cancel_button,
                               QDialogButtonBox.ButtonRole.DestructiveRole)
        layout.addWidget(self.buttons)

        self.adapter_combo.currentIndexChanged.connect(self._on_adapter_changed)
        self.key_edit.editingFinished.connect(self._save_key)
        self.download_button.clicked.connect(self.start_download)
        self.cancel_button.clicked.connect(self.cancel_download)
        self.buttons.rejected.connect(self.reject)

    # -- adapter -----------------------------------------------------------

    def current_adapter(self) -> ds.DemAdapter:
        return ds.ADAPTERS[self.adapter_combo.currentData()]

    def _on_adapter_changed(self, *_args):
        spec = self.current_adapter()
        self.key_label.setVisible(spec.needs_key)
        self.key_edit.setVisible(spec.needs_key)
        self.key_hint.setVisible(spec.needs_key)
        if spec.needs_key:
            stored = app_settings.secret(spec.key_setting)
            # The stored key is shown masked and never re-populated in clear:
            # an empty field means "keep what is saved".
            self.key_edit.clear()
            self.key_edit.setPlaceholderText(
                tr("salvata: {0}").format(mask_secret(stored)) if stored
                else tr("nessuna chiave salvata"))
            self.key_hint.setText(tr(
                "La chiave resta nelle impostazioni di QGIS. Non compare nei "
                "log, nei messaggi di errore o negli URL mostrati."))

        self.download_button.setEnabled(spec.usable)
        self.status_view.setHtml(self._status_html(spec))
        self._refresh_extent()

    def _status_html(self, spec):
        colour = {ds.VERIFIED: "#1a7f37", ds.PARTIAL: "#8a6100",
                  ds.UNSUPPORTED: "#a4262c"}[spec.status]
        rows = [
            "<p><b style='color:{0}'>{1}</b> &mdash; {2}</p>".format(
                colour, spec.status, ds.STATUS_LABELS[spec.status]),
            "<p>{0}</p>".format(spec.note),
        ]
        if spec.licence:
            rows.append("<p style='color:#555'>{0} {1}</p>".format(
                tr("Licenza:"), spec.licence))
        if spec.source_url:
            rows.append("<p style='color:#555'>{0} <a href='{1}'>{1}</a></p>"
                        .format(tr("Fonte:"), spec.source_url))
        if not spec.usable:
            rows.append("<p style='color:{0}'>{1}</p>".format(
                colour, tr("Il download e' disattivato per questa fonte.")))
        return "".join(rows)

    def _save_key(self):
        spec = self.current_adapter()
        if not spec.needs_key:
            return
        typed = self.key_edit.text().strip()
        if not typed:
            return
        app_settings.set(spec.key_setting, typed)
        self.key_edit.clear()
        self.key_edit.setPlaceholderText(
            tr("salvata: {0}").format(mask_secret(typed)))
        log("API key stored for {0}".format(spec.id))

    # -- extent ------------------------------------------------------------

    def bbox(self):
        """The canvas extent in the adapter's CRS, as (west, south, east, north)."""
        spec = self.current_adapter()
        canvas = self.iface.mapCanvas() if self.iface else None
        if canvas is None:
            return None
        extent = canvas.extent()
        source = canvas.mapSettings().destinationCrs()
        target = QgsCoordinateReferenceSystem(spec.crs_authid or "EPSG:4326")
        if source.isValid() and target.isValid() and source != target:
            transform = QgsCoordinateTransform(source, target,
                                               QgsProject.instance())
            try:
                extent = transform.transformBoundingBox(extent)
            except Exception:                                   # noqa: BLE001
                return None
        return (extent.xMinimum(), extent.yMinimum(),
                extent.xMaximum(), extent.yMaximum())

    def _refresh_extent(self):
        box = self.bbox()
        if box is None:
            self.extent_label.setText(tr("Nessuna estensione disponibile."))
            return
        spec = self.current_adapter()
        self.extent_label.setText(tr(
            "Estensione richiesta ({0}): {1:.6f}, {2:.6f} - {3:.6f}, "
            "{4:.6f}").format(spec.crs_authid or "EPSG:4326", *box))

    # -- download ----------------------------------------------------------

    def start_download(self, *_args):
        spec = self.current_adapter()
        if not spec.usable:
            self._notify(tr("{0}: download disattivato. {1}").format(
                spec.label, spec.note), Qgis.MessageLevel.Warning)
            return
        box = self.bbox()
        if box is None:
            self._notify(tr("Nessuna estensione da scaricare."),
                         Qgis.MessageLevel.Warning)
            return

        self._save_key()
        key = app_settings.secret(spec.key_setting) if spec.needs_key else ""
        if spec.needs_key and not key:
            self._notify(tr(
                "{0} richiede una chiave API. Inseriscila qui sopra: viene "
                "salvata nelle Impostazioni e mai nei log.").format(
                    spec.label), Qgis.MessageLevel.Warning)
            return

        task = DemDownloadTask(spec.id, box, key,
                              description=tr("Scarico {0}").format(spec.label))
        task.taskCompleted.connect(lambda: self._on_finished(task, True))
        task.taskTerminated.connect(lambda: self._on_finished(task, False))
        self._task = task
        self.progress.show()
        self.download_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        log("DEM download started: {0} {1}".format(spec.id, box))
        QgsApplication.taskManager().addTask(task)

    def cancel_download(self, *_args):
        if self._task is not None:
            self._task.cancel()

    def _on_finished(self, task, ok):
        self.progress.hide()
        self.cancel_button.setEnabled(False)
        self.download_button.setEnabled(self.current_adapter().usable)
        self._task = None
        if not ok or not task.path:
            self._notify(task.error or tr("Download annullato."),
                         Qgis.MessageLevel.Warning)
            return
        try:
            layer = ds.raster_layer(task.path, self.current_adapter().label)
        except Exception as exc:                                # noqa: BLE001
            self._notify(ds.mask_text(getattr(exc, "user_message", "")
                                      or str(exc)), Qgis.MessageLevel.Critical)
            return
        QgsProject.instance().addMapLayer(layer)
        self.last_layer = layer
        self._notify(tr("DEM aggiunto al progetto: selezionalo nella scheda "
                        "UAV."), Qgis.MessageLevel.Success)

    def _notify(self, text, level=Qgis.MessageLevel.Info):
        log(text, level)
        if self.iface is None:
            return
        try:
            self.iface.messageBar().pushMessage(tr("GeoCad UAV"),
                                                ds.mask_text(text), level=level)
        except Exception as exc:                                # noqa: BLE001
            swallow(exc, "dem dialog: message bar")
