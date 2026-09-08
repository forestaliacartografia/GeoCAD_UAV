"""Processing provider for GeoCad UAV Toolkit."""

from __future__ import annotations

import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .alg_design import ForestPlantingAlgorithm, GridAlgorithm
from .alg_flight import PlanFlightAlgorithm


class GeoCadProvider(QgsProcessingProvider):
    """Registers every GeoCad algorithm with the Processing framework."""

    def id(self):
        return "geocaduav"

    def name(self):
        return "GeoCad UAV Toolkit"

    def longName(self):
        return "GeoCad UAV Toolkit - CAD, griglie, impianti, voli UAV"

    def icon(self):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "icon.svg")
        return QIcon(path) if os.path.exists(path) else QgsProcessingProvider.icon(self)

    def loadAlgorithms(self):
        for algorithm in (PlanFlightAlgorithm(), GridAlgorithm(),
                          ForestPlantingAlgorithm()):
            self.addAlgorithm(algorithm)
