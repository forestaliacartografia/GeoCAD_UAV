"""
Plugin entry point: toolbar, menu, dock, map tools and Processing provider.

``initGui`` and ``unload`` are strict mirrors of one another (spec section 18).
Everything created in one is destroyed in the other -- actions, the toolbar,
the dock, the map tools, the provider and every signal connection -- so
unloading or reloading the plugin leaves QGIS exactly as it was found. A plugin
that leaks a toolbar, a map tool or a dangling signal breaks the host, which
fails the acceptance criterion "QGIS nativo non e' rotto".
"""

from __future__ import annotations

import os

from qgis.core import Qgis, QgsApplication, QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon, QKeySequence
from qgis.PyQt.QtWidgets import QAction, QActionGroup

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
MENU_TITLE = "GeoCad UAV Toolkit"

#: Geometry type each CAD tool writes, and therefore which scratch layer it
#: needs. Keys match ``cad.tools.TOOL_REGISTRY``.
TOOL_GEOMETRY = {"line": "LineString", "polyline": "LineString",
                 "rectangle": "Polygon", "circle": "Polygon"}


class GeoCadUavPlugin:
    """QGIS plugin shell."""

    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self.toolbar = None
        self.dock = None
        self.provider = None
        self.tool_actions = {}
        self.map_tools = {}
        self.tool_group = None
        self._scratch_layers = {}
        self._shortcut_notes = []

    # -- helpers ----------------------------------------------------------

    def tr(self, text):
        from qgis.PyQt.QtCore import QCoreApplication
        return QCoreApplication.translate("GeoCadUav", text)

    def _icon(self, name="icon.svg"):
        path = os.path.join(PLUGIN_DIR, name)
        return QIcon(path) if os.path.exists(path) else QIcon()

    def _add_action(self, text, callback, checkable=False, tip=""):
        action = QAction(self._icon(), text, self.iface.mainWindow())
        action.triggered.connect(callback)
        action.setCheckable(checkable)
        if tip:
            action.setStatusTip(tip)
            action.setToolTip(tip)
        self.toolbar.addAction(action)
        self.iface.addPluginToMenu(MENU_TITLE, action)
        self.actions.append(action)
        return action

    def _assign_shortcut(self, action, sequence_text):
        """Assign a shortcut only if nothing else already owns it.

        Spec: do not force a binding that collides with a native QGIS one.
        ``QgsGui.shortcutsManager().objectForSequence()`` is the authoritative
        check and exists on 3.34, 3.40 and 4.0 (verified).
        """
        if not sequence_text:
            return
        sequence = QKeySequence(sequence_text)
        try:
            from qgis.gui import QgsGui

            manager = QgsGui.shortcutsManager()
            existing = manager.objectForSequence(sequence) if manager else None
        except Exception:                                       # noqa: BLE001
            existing = None
        if existing is not None and existing is not action:
            name = getattr(existing, "text", lambda: "?")()
            self._shortcut_notes.append(
                "{0} non assegnata: gia' usata da '{1}'.".format(
                    sequence_text, name))
            return
        action.setShortcut(sequence)

    # -- lifecycle --------------------------------------------------------

    def initGui(self):                                          # noqa: N802
        self.provider = self._register_provider()

        self.toolbar = self.iface.addToolBar(MENU_TITLE)
        self.toolbar.setObjectName("GeoCadUavToolbar")

        self._add_action(self.tr("Pannello GeoCad UAV"), self.toggle_dock,
                         checkable=True,
                         tip=self.tr("Apre il pannello di pianificazione"))

        self.toolbar.addSeparator()
        self._build_cad_actions()

        self.toolbar.addSeparator()
        self._add_action(self.tr("Piano di volo UAV..."), self.open_flight_alg,
                         tip=self.tr("Apre l'algoritmo di pianificazione volo"))
        self._add_action(self.tr("Griglia parametrica..."), self.open_grid_alg,
                         tip=self.tr("Apre l'algoritmo griglia"))
        self._add_action(self.tr("Sesto d'impianto..."), self.open_forest_alg,
                         tip=self.tr("Apre l'algoritmo di impianto forestale"))

        for note in self._shortcut_notes:
            QgsApplication.messageLog().logMessage(
                note, MENU_TITLE, Qgis.Info)

    def _build_cad_actions(self):
        """Toolbar group CAD: Line, Rectangle, Circle. Mutually exclusive."""
        from .cad import tools as cad_tools                     # noqa: PLC0415

        self.tool_group = QActionGroup(self.iface.mainWindow())
        self.tool_group.setExclusive(True)
        for key in ("line", "polyline", "rectangle", "circle"):
            label = cad_tools.tool_label(key)
            action = self._add_action(
                label, lambda checked, k=key: self._toggle_tool(k, checked),
                checkable=True,
                tip=self.tr("Strumento CAD: {0}").format(label))
            self._assign_shortcut(action, cad_tools.tool_shortcut(key))
            self.tool_group.addAction(action)
            self.tool_actions[key] = action

    def unload(self):
        if self.provider is not None:
            try:
                QgsApplication.processingRegistry().removeProvider(self.provider)
            except Exception:                                   # noqa: BLE001
                pass
            self.provider = None

        # Map tools first: a tool still set on the canvas outlives the plugin.
        canvas = self.iface.mapCanvas() if self.iface else None
        for tool in self.map_tools.values():
            try:
                tool.deactivate()
                if canvas is not None:
                    canvas.unsetMapTool(tool)
            except Exception:                                   # noqa: BLE001
                pass
        self.map_tools = {}
        self.tool_actions = {}
        if self.tool_group is not None:
            self.tool_group.deleteLater()
            self.tool_group = None

        if self.dock is not None:
            try:
                self.dock.teardown()
            except Exception:                                   # noqa: BLE001
                pass
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

        for action in self.actions:
            try:
                action.triggered.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.iface.removePluginMenu(MENU_TITLE, action)
            if self.toolbar is not None:
                self.toolbar.removeAction(action)
            action.deleteLater()
        self.actions = []

        if self.toolbar is not None:
            self.toolbar.deleteLater()
            self.toolbar = None
        self._scratch_layers = {}

    # -- CAD map tools ----------------------------------------------------

    def _toggle_tool(self, key, checked):
        canvas = self.iface.mapCanvas()
        if not checked:
            tool = self.map_tools.get(key)
            if tool is not None and canvas.mapTool() is tool:
                canvas.unsetMapTool(tool)
            return
        tool = self.map_tools.get(key)
        if tool is None:
            from .cad import tools as cad_tools                 # noqa: PLC0415

            options = {}
            if key == "polyline" and self.dock is not None:
                options["close"] = self.dock.polyline_close_requested()
            tool = cad_tools.create_tool(
                key, canvas, iface=self.iface,
                layer_provider=lambda k=key: self._target_layer(k), **options)
            self.map_tools[key] = tool
        canvas.setMapTool(tool)
        if self.dock is not None:
            self.dock.bind_cad_tool(key, tool)

    def _target_layer(self, key):
        """Where a committed shape goes.

        The dock's chosen layer wins; otherwise a scratch layer is created
        through the existing ``io.layer_factory`` with the same schema the
        Processing algorithms write, so both paths stay interchangeable.
        """
        geometry_type = TOOL_GEOMETRY.get(key, "Polygon")
        if self.dock is not None:
            chosen = self.dock.current_cad_layer(geometry_type)
            if chosen is not None:
                return chosen
        return self._scratch_layer(geometry_type)

    def _scratch_layer(self, geometry_type):
        layer = self._scratch_layers.get(geometry_type)
        if layer is not None:
            try:
                if layer.isValid():
                    return layer
            except RuntimeError:
                pass                    # deleted by the user; make a new one
        from .cad import parametric as parametric_mod           # noqa: PLC0415
        from .io import layer_factory as lf                     # noqa: PLC0415

        crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        layer = lf.memory_layer(
            geometry_type, "GeoCad {0}".format(geometry_type),
            crs.authid(), parametric_mod.METADATA_FIELDS)
        QgsProject.instance().addMapLayer(layer)
        self._scratch_layers[geometry_type] = layer
        return layer

    # -- other actions ----------------------------------------------------

    def _register_provider(self):
        from .processing.provider import GeoCadProvider
        provider = GeoCadProvider()
        QgsApplication.processingRegistry().addProvider(provider)
        return provider

    def toggle_dock(self, checked):
        if self.dock is None:
            from .gui.dock import GeoCadDock
            self.dock = GeoCadDock(self.iface)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,
                                     self.dock)
        self.dock.setVisible(bool(checked))

    def _open_alg(self, alg_id):
        try:
            from processing import execAlgorithmDialog
            execAlgorithmDialog("geocaduav:" + alg_id, {})
        except Exception as exc:                                # noqa: BLE001
            self.iface.messageBar().pushCritical(
                MENU_TITLE,
                self.tr("Impossibile aprire l'algoritmo: {0}").format(exc))

    def open_flight_alg(self):
        self._open_alg("planflight")

    def open_grid_alg(self):
        self._open_alg("creategrid")

    def open_forest_alg(self):
        self._open_alg("forestplanting")
