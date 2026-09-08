"""
Typed, persistent settings on top of ``QgsSettings``.

**Every read passes an explicit ``type=``.** This is not defensive style, it is
required: on PyQt6 (QGIS 4.0) ``QgsSettings.value("k")`` returns the string
``'12'`` where PyQt5 (QGIS 3.40) returns the int ``12``. Verified on both. A
store that reads untyped works on 3.40 and silently hands strings to arithmetic
on 4.0, so the typed accessor is the only public way in.

Defaults are taken from ``core.constants`` wherever that module already owns
the number, so there is exactly one place to change a tolerance. Where the
constant does not exist the literal is written here and its provenance is noted
in the ``Setting`` description.

Namespace: every key is stored under ``GeoCadUav/``. Nothing else in the plugin
opens a ``QSettings``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..core import constants as K

#: Root of every key. QgsSettings is shared across QGIS, so the prefix matters.
NAMESPACE = "GeoCadUav"

BOOL, INT, FLOAT, STR = "bool", "int", "float", "str"
#: A string that must never be printed: API keys. Stored like STR,
#: excluded from all() and masked by mask().
SECRET = "secret"
_PY_TYPES = {BOOL: bool, INT: int, FLOAT: float, STR: str,
             SECRET: str}


@dataclass(frozen=True)
class Setting:
    """One persisted preference."""

    key: str
    kind: str
    default: Any
    description: str = ""

    @property
    def full_key(self) -> str:
        return "{0}/{1}".format(NAMESPACE, self.key)


def _s(key, kind, default, description=""):
    return Setting(key, kind, default, description)


#: The complete set. Adding a preference means adding a line here, nowhere else.
KEYS = {s.key: s for s in (
    # -- units and display --------------------------------------------------
    _s("units/length", STR, "m", "Unita' di lunghezza dell'interfaccia"),
    _s("units/angle", STR, "deg", "Unita' angolare dell'interfaccia"),
    _s("display/decimals", INT, K.DECIMALS_LENGTH,
       "Decimali mostrati (core.constants.DECIMALS_LENGTH)"),

    # -- CRS ----------------------------------------------------------------
    _s("crs/preferred", STR, "",
       "CRS metrico preferito; vuoto = deduci dai dati"),

    # -- snapping (mirrors the project's QgsSnappingConfig) -----------------
    _s("snap/enabled", BOOL, True, "Aggancio attivo per gli strumenti CAD"),
    _s("snap/tolerance_px", INT, K.SNAP_TOLERANCE_PX,
       "Tolleranza di aggancio in pixel (core.constants.SNAP_TOLERANCE_PX)"),
    _s("snap/types", STR, "vertex,segment",
       "Tipi di aggancio attivi, separati da virgola"),

    # -- UAV defaults -------------------------------------------------------
    _s("uav/camera", STR, "dji_mavic3e", "Chiave camera predefinita"),
    _s("uav/drone", STR, "dji_mavic3e", "Chiave drone predefinita"),
    _s("uav/frontlap", FLOAT, 0.80,
       "Sovrapposizione longitudinale; corrisponde al preset dsm_3d"),
    _s("uav/sidelap", FLOAT, 0.70,
       "Sovrapposizione laterale; corrisponde al preset dsm_3d"),
    _s("uav/target_mode", STR, "h_agl", "'h_agl' oppure 'gsd'"),
    _s("uav/h_agl_m", FLOAT, 80.0, "Quota di volo predefinita"),
    _s("uav/gsd_cm", FLOAT, 2.0, "GSD target predefinito in cm/px"),
    _s("uav/max_legal_agl_m", FLOAT, K.MAX_LEGAL_AGL_M,
       "Quota massima legale (core.constants.MAX_LEGAL_AGL_M)"),

    # -- CAD ----------------------------------------------------------------
    _s("cad/reference", STR, "corner", "Riferimento del rettangolo"),
    _s("cad/circle_segments", INT, K.CIRCLE_SEGMENTS,
       "Segmenti per cerchi ed ellissi (core.constants.CIRCLE_SEGMENTS)"),

    # -- DEM sources --------------------------------------------------------
    # Both are the same OpenTopography key in practice; they are declared
    # separately so revoking one adapter's access does not disable the other.
    _s("dem/copernicus_key", SECRET, "",
       "Chiave API OpenTopography per Copernicus GLO-30 (mai nei log)"),
    _s("dem/nasadem_key", SECRET, "",
       "Chiave API OpenTopography per NASADEM (mai nei log)"),

    # -- export -------------------------------------------------------------
    _s("export/format", STR, "gpkg", "Formato di esportazione predefinito"),
    _s("export/altitude_mode", STR, "amsl", "'amsl' oppure 'relative_home'"),

    # -- interface ----------------------------------------------------------
    _s("ui/last_tab", INT, 0, "Indice dell'ultima scheda aperta nel pannello"),
)}


def mask(value: Any) -> str:
    """A credential rendered for human eyes: never the value itself.

    Everything that can print -- logs, exception text, the message bar, the
    settings dump -- goes through this. Four leading characters are kept so an
    operator can tell two keys apart without the key being recoverable.
    """
    text = str(value or "")
    if not text:
        return ""
    return text[:4] + "*" * max(len(text) - 4, 4) if len(text) > 8 else "****"


def default_for(key: str) -> Any:
    """Default of a declared key. Raises KeyError on a typo, loudly."""
    return KEYS[key].default


class SettingsStore:
    """Typed accessor over ``QgsSettings``.

    ``QgsSettings`` is constructed per call rather than held: it is a thin
    handle over the shared QGIS settings and holding one across a plugin reload
    is a way to keep a stale object alive.
    """

    def __init__(self, namespace: str = NAMESPACE):
        self.namespace = namespace

    # -- internals ---------------------------------------------------------

    def _settings(self):
        from qgis.core import QgsSettings                       # noqa: PLC0415
        return QgsSettings()

    def _full(self, key: str) -> str:
        return "{0}/{1}".format(self.namespace, key)

    def _spec(self, key: str) -> Setting:
        try:
            return KEYS[key]
        except KeyError:
            raise KeyError(
                "unknown setting {0!r}. Declare it in settings.store.KEYS "
                "instead of inventing a key at the call site.".format(key)
            ) from None

    # -- public API --------------------------------------------------------

    def get(self, key: str) -> Any:
        """Read a setting, always typed, falling back to the declared default."""
        spec = self._spec(key)
        py_type = _PY_TYPES[spec.kind]
        try:
            value = self._settings().value(self._full(key), spec.default,
                                           type=py_type)
        except (TypeError, ValueError):
            # A value written by an older build in the wrong type: prefer the
            # documented default over propagating garbage into geometry.
            return spec.default
        if value is None:
            return spec.default
        return value

    def set(self, key: str, value: Any) -> Any:
        """Write a setting, coercing to the declared type first."""
        spec = self._spec(key)
        py_type = _PY_TYPES[spec.kind]
        if spec.kind == BOOL:
            coerced = bool(value)
        else:
            try:
                coerced = py_type(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "setting {0!r} expects {1}, got {2!r}".format(
                        key, spec.kind, value)) from exc
        self._settings().setValue(self._full(key), coerced)
        return coerced

    def reset(self, key: Optional[str] = None) -> None:
        """Remove one key, or every key in the namespace."""
        settings_obj = self._settings()
        if key is not None:
            self._spec(key)
            settings_obj.remove(self._full(key))
            return
        for name in KEYS:
            settings_obj.remove(self._full(name))

    def all(self) -> dict:
        """Every declared setting. Secrets come back masked, never in clear."""
        return {name: (mask(self.get(name)) if KEYS[name].kind == SECRET
                       else self.get(name))
                for name in KEYS}

    def secret(self, key: str) -> str:
        """Read an API key. The only way one leaves the store.

        Raises on a key that was not declared SECRET, so a credential can
        never be read through the ordinary accessor by accident.
        """
        spec = self._spec(key)
        if spec.kind != SECRET:
            raise KeyError(
                "setting {0!r} is not a secret; use get()".format(key))
        return str(self.get(key) or "")

    def has_secret(self, key: str) -> bool:
        return bool(self.secret(key).strip())

    def snap_types(self) -> "list":
        """``snap/types`` parsed into a clean list of lowercase names."""
        raw = self.get("snap/types") or ""
        return [part.strip().lower() for part in raw.split(",") if part.strip()]


#: Shared instance. The dock and the map tools both read through this.
settings = SettingsStore()
