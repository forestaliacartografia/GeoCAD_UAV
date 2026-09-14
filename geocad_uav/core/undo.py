"""
Edit-buffer helpers: every mutation lands in QGIS's own undo stack.

Spec P8: undo/redo must work, and no mass operation runs without a preview and
a confirmation. This module provides the transaction wrapper, so a plugin
operation is one entry in the layer's undo history rather than N separate ones
(which would force the operator to press Ctrl+Z five thousand times to reverse
a planting scheme).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterable, Optional

from .constants import CHUNK_SIZE, CONFIRM_THRESHOLD_FEATURES
from .errors import LayerError, NotEditableError


def ensure_editable(layer, start: bool = True) -> bool:
    """Put ``layer`` into edit mode. Returns True if this call started it."""
    if layer is None:
        raise LayerError("no layer", user_message="Nessun layer di destinazione.")
    if not layer.isValid():
        raise LayerError(
            "layer is not valid: {0}".format(layer.name()),
            user_message="Il layer '{0}' non e' valido.".format(layer.name()))
    caps = layer.dataProvider().capabilities()
    from qgis.core import QgsVectorDataProvider                  # noqa: PLC0415
    if not caps & QgsVectorDataProvider.Capability.AddFeatures:
        raise LayerError(
            "provider cannot add features to {0}".format(layer.name()),
            user_message="Il layer '{0}' non accetta nuove geometrie.".format(
                layer.name()),
            hint="Scegli un layer modificabile (GeoPackage, memoria, ...).")
    if layer.isEditable():
        return False
    if not start:
        raise NotEditableError(
            "layer {0} is not in edit mode".format(layer.name()),
            user_message="Il layer '{0}' non e' in modifica.".format(layer.name()))
    if not layer.startEditing():
        raise NotEditableError(
            "could not start editing {0}".format(layer.name()),
            user_message="Impossibile attivare la modifica sul layer "
                         "'{0}'.".format(layer.name()))
    return True


@contextmanager
def edit_command(layer, label: str, commit: bool = True):
    """Group everything inside into a single undo entry.

    On any exception the command is destroyed, so a half-applied operation
    never reaches the layer -- the operator sees the error with their data
    untouched rather than a partial edit they have to unpick by hand.
    """
    started = ensure_editable(layer)
    layer.beginEditCommand(label)
    try:
        yield layer
    except Exception:
        layer.destroyEditCommand()
        if started:
            layer.rollBack()
        raise
    layer.endEditCommand()
    if commit and started:
        if not layer.commitChanges():
            errors = "; ".join(layer.commitErrors())
            layer.rollBack()
            raise LayerError(
                "commit failed on {0}: {1}".format(layer.name(), errors),
                user_message="Salvataggio delle modifiche non riuscito sul "
                             "layer '{0}'.".format(layer.name()),
                hint=errors)


def add_features(layer, features: Iterable, label: str,
                 chunk_size: int = CHUNK_SIZE, feedback=None,
                 commit: bool = True) -> int:
    """Add features in batches inside one undo command.

    Batching matters at scale: adding 50 000 planting points one at a time
    through the edit buffer is orders of magnitude slower than chunked
    ``addFeatures`` calls, and it floods the undo stack.
    """
    features = list(features)
    total = len(features)
    if total == 0:
        return 0

    with edit_command(layer, label, commit=commit):
        for start in range(0, total, chunk_size):
            if feedback is not None:
                if feedback.isCanceled():
                    raise LayerError(
                        "cancelled by the operator",
                        user_message="Operazione annullata.")
                feedback.setProgress(100.0 * start / total)
            batch = features[start:start + chunk_size]
            if not layer.addFeatures(batch):
                raise LayerError(
                    "addFeatures failed on {0}".format(layer.name()),
                    user_message="Inserimento delle geometrie non riuscito sul "
                                 "layer '{0}'.".format(layer.name()))
    layer.updateExtents()
    return total


def needs_confirmation(count: int,
                       threshold: int = CONFIRM_THRESHOLD_FEATURES) -> bool:
    """True when an operation is big enough to require explicit confirmation."""
    return count >= threshold


def confirmation_message(count: int, target_layer_name: str) -> str:
    return ("Stai per inserire {0:,} geometrie nel layer '{1}'. "
            "L'operazione e' annullabile con Ctrl+Z, ma su grandi numeri puo' "
            "richiedere tempo. Continuare?".format(count, target_layer_name))
