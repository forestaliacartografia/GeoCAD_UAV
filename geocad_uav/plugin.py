"""
Plugin entry point: one toolbar icon, one dock, map tools, Processing provider.

The QGIS main toolbar carries **exactly one** action: the dock toggle. The CAD
map-tool actions still exist and behave as they did in 1.1.x, but they are
mounted on a toolbar *inside* the dock's CAD tab, so the host toolbar is not
colonised by eight icons. The three Processing launchers live in the plugin
menu and as buttons in the dock's tabs.

``initGui`` and ``unload`` are strict mirrors of one another. Everything
created in one is destroyed in the other -- actions, toolbars, the dock, the
map tools, the provider and every signal connection -- so unloading or
reloading leaves QGIS exactly as it was found, and a second ``initGui`` after
an ``unload`` does not duplicate anything.
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
                 "rectangle": "Polygon", "circle": "Polygon",
                 "square": "Polygon", "regular_polygon": "Polygon",
                 "arc": "LineString"}

#: Tools that edit an existing feature instead of creating one. They must
#: never be handed a scratch layer: they rotate what the operator selected.
EDIT_IN_PLACE_TOOLS = ("rotate", "move", "resize")

#: CAD tools mounted on the dock's toolbar, in display order.
CAD_TOOL_ORDER = ("line", "polyline", "rectangle", "square", "circle",
                  "arc", "regular_polygon", "rotate", "move", "resize")

# Where an action is mounted.
HOST_TOOLBAR = "toolbar"      # the QGIS main toolbar -- one action only
HOST_DOCK = "dock"            # the CAD toolbar inside the dock
HOST_NONE = "none"


class GeoCadUavPlugin:
    """QGIS plugin shell."""

    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self._menu_actions = []
        self.toolbar = None
        self.dock = None
        self.dock_action = None
        self.provider = None
        self.tool_actions = {}
        self.map_tools = {}
        self.tool_group = None
        self._scratch_layers = {}
        self._shortcut_notes = []
        self._connections = []

    # -- helpers ----------------------------------------------------------

    def tr(self, text):
        from qgis.PyQt.QtCore import QCoreApplication
        return QCoreApplication.translate("GeoCadUav", text)

    def _icon(self, name="icon.png"):
        path = os.path.join(PLUGIN_DIR, name)
        return QIcon(path) if os.path.exists(path) else QIcon()

    def _make_action(self, text, callback, checkable=False, tip="",
                     host=HOST_NONE, to_menu=True):
        """Create one action and mount it where it belongs.

        ``host=HOST_TOOLBAR`` is the QGIS *main* toolbar and is granted to
        exactly one action: the dock toggle. CAD tools take ``HOST_DOCK``.
        """
        action = QAction(self._icon(), text, self._main_window())
        action.triggered.connect(callback)
        action.setCheckable(checkable)
        if tip:
            action.setStatusTip(tip)
            action.setToolTip(tip)

        if host == HOST_TOOLBAR and self.toolbar is not None:
            self.toolbar.addAction(action)
        elif host == HOST_DOCK:
            cad_toolbar = getattr(self.dock, "cad_toolbar", None)
            if cad_toolbar is not None:
                cad_toolbar.addAction(action)

        if to_menu:
            self.iface.addPluginToMenu(MENU_TITLE, action)
            self._menu_actions.append(action)

        self.actions.append(action)
        self._connections.append((action.triggered, callback))
        return action

    def _main_window(self):
        try:
            return self.iface.mainWindow()
        except (AttributeError, RuntimeError):
            return None

    def _assign_shortcut(self, action, sequence_text):
        """Assign a shortcut only if nothing else already owns it.

        Do not force a binding that collides with a native QGIS one.
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
        try:
            self.toolbar.setObjectName("GeoCadUavToolbar")
        except AttributeError:
            pass

        # THE single icon on the QGIS toolbar.
        self.dock_action = self._make_action(
            self.tr("GeoCad UAV Toolkit"), self.toggle_dock, checkable=True,
            tip=self.tr("Apre il pannello CAD / Rimboschimento / UAV"),
            host=HOST_TOOLBAR)

        # The dock is built now, hidden, because it hosts the CAD toolbar.
        self._ensure_dock()
        self._build_cad_actions()

        # Menu only: the same algorithms are reachable from the dock tabs.
        self._make_action(self.tr("Piano di volo UAV..."), self.open_flight_alg,
                          tip=self.tr("Algoritmo di pianificazione volo"))
        self._make_action(self.tr("Griglia parametrica..."), self.open_grid_alg,
                          tip=self.tr("Algoritmo griglia"))
        self._make_action(self.tr("Sesto d'impianto..."), self.open_forest_alg,
                          tip=self.tr("Algoritmo di impianto forestale"))

        for note in self._shortcut_notes:
            QgsApplication.messageLog().logMessage(note, MENU_TITLE, Qgis.Info)

    def _ensure_dock(self):
        """Create the dock once, hidden, and keep the toggle in step with it."""
        if self.dock is not None:
            return self.dock
        from .gui.dock import GeoCadDock

        self.dock = GeoCadDock(self.iface)
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,
                                 self.dock)
        self.dock.setVisible(False)
        # Closing the dock from its own title bar must un-check the toolbar
        # icon, otherwise the toggle and the panel disagree.
        try:
            self.dock.visibilityChanged.connect(self._on_dock_visibility)
            self._connections.append((self.dock.visibilityChanged,
                                      self._on_dock_visibility))
        except (AttributeError, TypeError):
            pass
        return self.dock

    def _on_dock_visibility(self, visible):
        if self.dock_action is not None and \
                self.dock_action.isChecked() != bool(visible):
            self.dock_action.setChecked(bool(visible))

    def _build_cad_actions(self):
        """CAD map tools: mounted on the dock's toolbar, not on the QGIS one."""
        from .cad import tools as cad_tools                     # noqa: PLC0415

        self.tool_group = QActionGroup(self._main_window())
        self.tool_group.setExclusive(True)
        for key in CAD_TOOL_ORDER:
            if key not in cad_tools.TOOL_REGISTRY:
                continue                    # tool not shipped in this build
            label = cad_tools.tool_label(key)
            action = self._make_action(
                label, lambda checked, k=key: self._toggle_tool(k, checked),
                checkable=True,
                tip=self.tr("Strumento CAD: {0}").format(label),
                host=HOST_DOCK, to_menu=False)
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

        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._connections = []

        # Map tools first: a tool still set on the canvas outlives the plugin.
        canvas = None
        try:
            canvas = self.iface.mapCanvas()
        except (AttributeError, RuntimeError):
            canvas = None
        for tool in self.map_tools.values():
            try:
                tool.deactivate()
                if canvas is not None:
                    canvas.unsetMapTool(tool)
            except Exception:                                   # noqa: BLE001
                pass
        self.map_tools = {}

        cad_toolbar = getattr(self.dock, "cad_toolbar", None) if self.dock else None
        for action in self.tool_actions.values():
            if cad_toolbar is not None:
                try:
                    cad_toolbar.removeAction(action)
                except (RuntimeError, TypeError):
                    pass
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
            if action in self._menu_actions:
                self.iface.removePluginMenu(MENU_TITLE, action)
            if self.toolbar is not None:
                try:
                    self.toolbar.removeAction(action)
                except (RuntimeError, TypeError):
                    pass
            action.deleteLater()
        self.actions = []
        self._menu_actions = []
        self.dock_action = None

        if self.toolbar is not None:
            self.toolbar.deleteLater()
            self.toolbar = None
        self._scratch_layers = {}
        self._shortcut_notes = []

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
        if key in EDIT_IN_PLACE_TOOLS:
            # Rotate works on the layer the operator is editing, so the active
            # layer wins and no scratch layer is ever created for it.
            try:
                return self.iface.activeLayer()
            except AttributeError:
                return None
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
        """Show or hide the single dock. Never creates a second one."""
        dock = self._ensure_dock()
        dock.setVisible(bool(checked))
        if checked:
            try:
                dock.raise_()
            except (AttributeError, RuntimeError):
                pass

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
