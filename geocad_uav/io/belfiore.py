"""
Belfiore code -> comune, from a table shipped with the plugin.

The cadastral WFS answers with ``ADMINISTRATIVEUNIT = H501``. That is a
Belfiore code, and the service publishes no name to go with it, so the plugin
has to resolve it locally. The table is **data, in a data file** --
``data/belfiore.csv``, 7 896 rows -- and not a dictionary in a module: a
mapping of every Italian comune written into Python would be unreadable, would
diff badly and would put maintenance of a national register inside the source
code.

Provenance of the shipped file, so the next person can rebuild it:

* source:  ISTAT, "Codici delle unita' amministrative territoriali", file
  ``Elenco-comuni-italiani.csv``
  (https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv)
* columns taken: "Codice Catastale del comune" -> ``codice_belfiore``,
  "Denominazione in italiano" -> ``nome_comune``, "Denominazione dell'Unita'
  territoriale sovracomunale" -> ``provincia``, "Sigla automobilistica" ->
  ``sigla``, "Denominazione Regione" -> ``regione``.

The reader is schema-driven: it takes the columns it finds by name, needs the
four the rest of the plugin depends on, and ignores any others, so a
replacement table with more columns works without a code change. An operator
who has their own file points the setting at it.

Nothing here raises at lookup time. A missing file, an unreadable row, a code
that is not in the register: all of them come back as a comune that says it is
not known, because this runs inside a background task and a plantation is not
going to be abandoned over a municipality name.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from typing import Optional

#: Shipped table. ``geocad_uav/data/belfiore.csv``.
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data")
DEFAULT_PATH = os.path.join(DATA_DIR, "belfiore.csv")

#: Columns the rest of the plugin reads. Anything else in the file is kept
#: available on the record but nothing depends on it.
CODE_COLUMN = "codice_belfiore"
NAME_COLUMN = "nome_comune"
PROVINCE_COLUMN = "provincia"
REGION_COLUMN = "regione"
REQUIRED_COLUMNS = (CODE_COLUMN, NAME_COLUMN, PROVINCE_COLUMN, REGION_COLUMN)

#: The smallest table that still answers the only question that matters:
#: which comune is this code. A file with just these two columns is read
#: too, so an operator can drop in a two-column list without having to
#: invent a province for every row.
MINIMAL_COLUMNS = ("codice", "comune")
COLUMN_ALIASES = {
    "codice": CODE_COLUMN, "code": CODE_COLUMN,
    "codice_catastale": CODE_COLUMN, "belfiore": CODE_COLUMN,
    "comune": NAME_COLUMN, "denominazione": NAME_COLUMN,
    "nome": NAME_COLUMN,
    "sigla_provincia": "sigla", "prov": PROVINCE_COLUMN,
}

SOURCE_URL = ("https://www.istat.it/storage/codici-unita-amministrative/"
              "Elenco-comuni-italiani.csv")
SOURCE_NAME = "ISTAT - Codici delle unita' amministrative territoriali"

#: What an unknown code reads as, in the attribute table and in a report.
UNKNOWN_LABEL = "Non disponibile"


@dataclass
class Comune:
    """One row of the register, or a stand-in for a code that is not in it."""

    code: str = ""
    name: str = ""
    province: str = ""
    sigla: str = ""
    region: str = ""
    known: bool = True
    extra: dict = None

    @property
    def is_known(self) -> bool:
        return bool(self.known and self.name)

    def label(self) -> str:
        """``Roma (RM)`` -- what a panel or a report prints."""
        if not self.is_known:
            return "{0} - {1}".format(self.code or "?", UNKNOWN_LABEL)
        if self.sigla:
            return "{0} ({1})".format(self.name, self.sigla)
        return self.name

    def as_attributes(self) -> dict:
        return {"comune": self.name if self.is_known else UNKNOWN_LABEL,
                "belfiore": self.code,
                "provincia": self.province,
                "regione": self.region}


def unknown(code: str = "") -> Comune:
    """The answer for a code the register does not carry."""
    return Comune(code=(code or "").strip().upper(), name="", known=False,
                  extra={})


class BelfioreRegistry:
    """The lookup table, read once and kept.

    Reading is lazy: importing this module must not touch the disk, because
    a plugin that reads 300 kB at import time slows every QGIS start for a
    feature most sessions never use.
    """

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        self._by_code = None
        self.error = ""

    # -- loading -----------------------------------------------------------

    def load(self) -> dict:
        """Read the table. Returns the mapping; never raises."""
        if self._by_code is not None:
            return self._by_code
        self._by_code = {}
        self.error = ""
        if not self.path or not os.path.isfile(self.path):
            self.error = "tabella Belfiore non trovata: {0}".format(self.path)
            return self._by_code
        try:
            with io.open(self.path, encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                headers = [str(name or "").strip().lower()
                           for name in (reader.fieldnames or ())]
                # A header written any of the accepted ways is normalised
                # once, here, so the rest of the reader sees one schema.
                normalised = {name: COLUMN_ALIASES.get(name, name)
                              for name in headers}
                present = set(normalised.values())
                if not {CODE_COLUMN, NAME_COLUMN} <= present:
                    self.error = ("colonne mancanti nella tabella Belfiore: "
                                  "{0}".format(", ".join(
                                      name for name in (CODE_COLUMN,
                                                        NAME_COLUMN)
                                      if name not in present)))
                    return self._by_code
                for raw in reader:
                    row = {normalised.get(str(k or "").strip().lower(),
                                          str(k or "").strip().lower()): v
                           for k, v in raw.items()}
                    code = (row.get(CODE_COLUMN) or "").strip().upper()
                    if not code:
                        continue
                    self._by_code[code] = Comune(
                        code=code,
                        name=(row.get(NAME_COLUMN) or "").strip(),
                        province=(row.get(PROVINCE_COLUMN) or "").strip(),
                        sigla=(row.get("sigla") or "").strip(),
                        region=(row.get(REGION_COLUMN) or "").strip(),
                        known=True,
                        extra={k: v for k, v in row.items()
                               if k not in REQUIRED_COLUMNS and k != "sigla"})
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            self.error = "tabella Belfiore illeggibile: {0}".format(exc)
            self._by_code = {}
        return self._by_code

    def reload(self) -> dict:
        self._by_code = None
        return self.load()

    # -- lookup ------------------------------------------------------------

    def get(self, code) -> Optional[Comune]:
        """The comune for a code, or None. Case and spaces do not matter."""
        if code is None:
            return None
        try:
            key = str(code).strip().upper()
        except (TypeError, ValueError):
            return None
        if not key:
            return None
        return self.load().get(key)

    def resolve(self, code) -> Comune:
        """Always a :class:`Comune`: the real one, or one that says it is not.

        The caller is a background task writing into an attribute table. It
        needs something to write, and "Non disponibile" is an answer where an
        exception is a crash.
        """
        found = self.get(code)
        return found if found is not None else unknown(
            "" if code is None else str(code))

    def has(self, code) -> bool:
        return self.get(code) is not None

    @property
    def count(self) -> int:
        return len(self.load())

    @property
    def available(self) -> bool:
        return bool(self.load()) and not self.error

    def describe(self) -> "list[str]":
        lines = ["REGISTRO BELFIORE",
                 "  Tabella: {0}".format(self.path),
                 "  Comuni:  {0:,}".format(self.count),
                 "  Fonte:   {0}".format(SOURCE_NAME),
                 "           {0}".format(SOURCE_URL)]
        if self.error:
            lines.append("  Avviso:  {0}".format(self.error))
        return lines


_REGISTRY = None


def registry(path: Optional[str] = None) -> BelfioreRegistry:
    """The shared register. One read per session, not one per parcel.

    A path opens a different table without disturbing the shared one, which
    is what a test -- or an operator with their own file -- needs.
    """
    global _REGISTRY
    if path is not None:
        return BelfioreRegistry(path)
    if _REGISTRY is None:
        _REGISTRY = BelfioreRegistry(_configured_path())
    return _REGISTRY


def _configured_path() -> str:
    """The operator's own table if they set one, the shipped one otherwise."""
    try:
        from ..settings import settings                         # noqa: PLC0415

        chosen = str(settings.get("cadastre/belfiore_path") or "").strip()
    except Exception:                                           # noqa: BLE001
        chosen = ""
    return chosen or DEFAULT_PATH


def resolve(code) -> Comune:
    """Shortcut on the shared register."""
    return registry().resolve(code)
