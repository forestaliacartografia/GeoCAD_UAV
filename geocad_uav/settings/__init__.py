"""
Persistent plugin settings.

One system, one namespace. Everything the operator sets in the dock lives here
and survives a QGIS restart; nothing else in the plugin may open a ``QSettings``
of its own.
"""

from .store import (KEYS, NAMESPACE, SECRET, SettingsStore, Setting,
                    default_for, mask, settings)

__all__ = ["KEYS", "NAMESPACE", "SECRET", "SettingsStore", "Setting",
           "default_for", "mask", "settings"]
