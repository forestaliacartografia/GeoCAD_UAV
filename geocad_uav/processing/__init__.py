"""Processing provider and algorithms."""

from __future__ import annotations


def mark_advanced(parameter):
    """Flag a parameter as advanced, across QGIS 3.34 -> 4.0.

    The flag belongs to the *parameter definition*, not to the algorithm.
    ``QgsProcessingParameterDefinition.Flag.FlagAdvanced`` is the scoped
    spelling PyQt6 requires; ``Qgis.ProcessingParameterFlag.Advanced`` is its
    replacement in newer releases. Both resolve to the same value, so the
    scoped name is tried first and the newer one is the fallback, rather than
    branching on a version number.

    Returns the parameter so it can be used inline in ``addParameter``.
    """
    from qgis.core import QgsProcessingParameterDefinition

    flag = getattr(getattr(QgsProcessingParameterDefinition, "Flag", None),
                   "FlagAdvanced", None)
    if flag is None:
        from qgis.core import Qgis
        flag = Qgis.ProcessingParameterFlag.Advanced
    parameter.setFlags(parameter.flags() | flag)
    return parameter
