"""
Typed exceptions carrying an actionable, translatable user message.

Spec section 15: the user never sees a bare traceback. Every failure raised
here carries two things:

* ``user_message`` -- what went wrong, in Italian, in the operator's terms.
* ``hint`` -- what to actually do about it ("Seleziona un poligono"), which is
  what makes an error message worth showing at all.

The developer-facing ``str(exc)`` keeps the technical detail for
``QgsMessageLog``; the GUI shows ``user_message``/``hint`` in the message bar.

Italian strings are wrapped in :func:`tr` so they can be lifted into a Qt
translation file without touching call sites. When Qt is unavailable (headless
tests) ``tr`` is the identity function.
"""

from __future__ import annotations

from typing import Optional

try:                                        # pragma: no cover - GUI runtime
    from qgis.PyQt.QtCore import QCoreApplication

    def tr(text: str, context: str = "GeoCadUav") -> str:
        """Translate through Qt when it is available."""
        return QCoreApplication.translate(context, text)

except ImportError:                         # pragma: no cover - headless tests

    def tr(text: str, context: str = "GeoCadUav") -> str:
        """Identity fallback so core modules import without Qt."""
        return text


class GeoCadError(Exception):
    """Base class for every error this plugin raises deliberately.

    ``detail`` is for the log, ``user_message``/``hint`` are for the operator.
    """

    #: Fallback shown when a subclass does not set one.
    default_message = "Si e' verificato un errore."
    default_hint = ""

    def __init__(self, detail: str = "", user_message: Optional[str] = None,
                 hint: Optional[str] = None):
        self.detail = detail
        self._user_message = user_message
        self._hint = hint
        super().__init__(detail or user_message or self.default_message)

    @property
    def user_message(self) -> str:
        return tr(self._user_message or self.default_message)

    @property
    def hint(self) -> str:
        return tr(self._hint if self._hint is not None else self.default_hint)

    def formatted(self) -> str:
        """Single-line message for the QGIS message bar."""
        return "{0} {1}".format(self.user_message, self.hint).strip()


# --------------------------------------------------------------------------
# CRS and units
# --------------------------------------------------------------------------

class CrsError(GeoCadError):
    default_message = "Sistema di riferimento non utilizzabile per questa operazione."
    default_hint = "Imposta un CRS proiettato metrico (es. UTM) per il progetto."


class GeographicCrsError(CrsError):
    default_message = ("Il CRS e' geografico (gradi): le distanze e le aree "
                       "calcolate non sarebbero corrette.")
    default_hint = "Trasforma i dati in un CRS metrico di lavoro (UTM) e riprova."


class UnitError(GeoCadError):
    default_message = "Unita' di misura non riconosciuta."
    default_hint = "Usa una fra: m, cm, mm, km, ft, in."


# --------------------------------------------------------------------------
# Geometry and constraints
# --------------------------------------------------------------------------

class GeometryError(GeoCadError):
    default_message = "Geometria non valida."
    default_hint = "Controlla i vertici o usa la riparazione automatica."


class InvalidInputError(GeoCadError):
    default_message = "Valore di ingresso non valido."
    default_hint = "Correggi il parametro evidenziato."


class ConstraintError(GeoCadError):
    default_message = "Il vincolo richiesto non e' risolvibile."
    default_hint = "Verifica lunghezze, angoli e raggi inseriti."


class EmptyAoiError(GeoCadError):
    default_message = "Nessun poligono di area disponibile."
    default_hint = "Seleziona un poligono, oppure scegli un layer poligonale."


# --------------------------------------------------------------------------
# Raster / terrain
# --------------------------------------------------------------------------

class RasterError(GeoCadError):
    default_message = "Modello di elevazione non utilizzabile."
    default_hint = "Verifica che il raster copra l'area e abbia un CRS definito."


class NoDataError(RasterError):
    default_message = "Il modello di elevazione non ha dati in quest'area."
    default_hint = ("Estendi il DEM/DTM, oppure scegli come gestire i buchi "
                    "nelle opzioni del terreno.")


# --------------------------------------------------------------------------
# Mission
# --------------------------------------------------------------------------

class MissionError(GeoCadError):
    default_message = "Impossibile generare la missione."
    default_hint = "Controlla area, camera, quota e sovrapposizioni."


class ValidationError(GeoCadError):
    default_message = "La missione non supera la validazione."
    default_hint = "Risolvi gli errori elencati nel report prima di esportare."


class ExportError(GeoCadError):
    default_message = "Esportazione non riuscita."
    default_hint = "Verifica il percorso di destinazione e i permessi di scrittura."


class UnsupportedFormatError(ExportError):
    default_message = "Formato di esportazione non supportato."
    default_hint = "Scegli un formato fra quelli elencati."


# --------------------------------------------------------------------------
# Layers and I/O
# --------------------------------------------------------------------------

class LayerError(GeoCadError):
    default_message = "Layer non utilizzabile."
    default_hint = "Verifica che il layer sia valido e modificabile."


class NotEditableError(LayerError):
    default_message = "Il layer non e' in modalita' di modifica."
    default_hint = "Attiva la modifica sul layer di destinazione."
