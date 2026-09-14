"""
v2.2.5: what the published security scan found, and what it now cannot find.

The plugin repository runs bandit, flake8 and detect-secrets over every
uploaded package. On 2.2.4 they reported 34 findings between them. Most were
one pattern: an exception caught and discarded with a bare ``pass``, which is
how a plugin survives a message bar that no longer exists -- and also how a
failure in the field becomes impossible to explain afterwards.

The fixes are behavioural, so this suite is too: it does not check that a
scanner is quiet, it checks that the code does the thing the scanner was
worried about. The one structural rule here (R5) restates bandit's B110 as an
AST walk, so it keeps working on a machine where bandit is not installed.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_hardening.py
"""

import ast
import hashlib
import os
import sys
import xml.sax.saxutils as saxutils

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import QgsApplication                            # noqa: E402

QGS = QgsApplication([], False)
QGS.initQgis()

from geocad_uav.cad import primitives as pr                     # noqa: E402
from geocad_uav.core import errors as er                        # noqa: E402
from geocad_uav.io import cadastre as cad                       # noqa: E402
from geocad_uav.io import dem_source as ds                      # noqa: E402
from geocad_uav.io import documents as doc                      # noqa: E402
from geocad_uav.io.net import require_web_url                   # noqa: E402
from geocad_uav.settings import store as st                     # noqa: E402

FAILURES = []
SKIPS = []

PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(label, got, expected):
    ok = got == expected
    print("  [{0}] {1:<56} got={2!s:<14} exp={3!s}".format(
        "ok  " if ok else "FAIL", label, got, expected))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def shipped_modules():
    """Every .py that ships, tests excluded -- what the scanner reads."""
    out = []
    for base, dirs, names in os.walk(PACKAGE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, PACKAGE).replace(os.sep, "/")
            if rel.startswith("tests/"):
                continue
            with open(path, encoding="utf-8") as handle:
                out.append((rel, handle.read()))
    return out


print("=" * 78)
print("R1 -- a swallowed exception is recorded, and swallow() never raises")
print("=" * 78)
er.clear_swallowed()
try:
    raise ValueError("il servizio ha risposto male")
except Exception as exc:                                        # noqa: BLE001
    er.swallow(exc, "prova: trasporto")
history = er.swallowed()
check("una eccezione registrata", len(history), 1)
check_true("...con posto, tipo e messaggio",
           history[0].startswith("prova: trasporto: ValueError:")
           and "risposto male" in history[0])


class Nasty(Exception):
    """An exception whose message cannot be rendered."""

    def __str__(self):
        raise RuntimeError("anche __str__ esplode")


# The point of the helper: it is called from except blocks, including during
# teardown. If it could raise it would replace a cosmetic failure with a
# crash -- the exact outcome it exists to prevent.
raised = None
try:
    try:
        raise Nasty()
    except Exception as exc:                                    # noqa: BLE001
        er.swallow(exc, "prova: str rotto")
except BaseException as exc:                                    # noqa: BLE001
    raised = exc
check("swallow() su una eccezione impossibile da stampare", raised, None)
check_true("...registrata comunque, per tipo",
           er.swallowed()[-1] == "prova: str rotto: Nasty")

er.clear_swallowed()
for n in range(er.SWALLOW_LIMIT + 50):
    try:
        raise OSError("ripetuto {0}".format(n))
    except Exception as exc:                                    # noqa: BLE001
        er.swallow(exc, "prova: ripetizione")
check("la storia resta limitata", len(er.swallowed()), er.SWALLOW_LIMIT)
check_true("...e tiene le ultime, non le prime",
           "ripetuto {0}".format(er.SWALLOW_LIMIT + 49) in er.swallowed()[-1])
er.clear_swallowed()

print()
print("=" * 78)
print("R2 -- i trasporti rifiutano uno schema che non sia http/https")
print("=" * 78)
for good in ("https://example.org/wfs?x=1", "http://127.0.0.1:8080/a"):
    check_true("ammesso: " + good, require_web_url(good) == good)

for bad in ("file:///C:/Windows/win.ini", "ftp://host/x", "jar:x", ""):
    refused = False
    try:
        require_web_url(bad)
    except er.UnsafeUrlError:
        refused = True
    check_true("rifiutato: " + (bad or "<vuoto>"), refused)

# urlopen honours file:, so a DEM template or a cadastral endpoint edited to
# point at the disk would have read it and returned the bytes as if they had
# come from the network. Both transports are checked on the real function.
for label, call in (
        ("cadastre.urllib_transport",
         lambda: cad.urllib_transport("file:///C:/Windows/win.ini")),
        ("dem_source.urllib_transport",
         lambda: next(iter(ds.urllib_transport("file:///C:/Windows/win.ini")))),
):
    kind = "nessun errore"
    try:
        call()
    except er.UnsafeUrlError:
        kind = "UnsafeUrlError"
    except Exception as exc:                                    # noqa: BLE001
        kind = type(exc).__name__
    check(label + " su file://", kind, "UnsafeUrlError")

print()
print("=" * 78)
print("R3 -- l'escape locale e' quello della libreria standard")
print("=" * 78)
# The import was dropped, not the behaviour: the report must escape exactly as
# before. Compared against the stdlib on the characters that matter and on
# text this plugin really produces.
CORPUS = [
    "", "niente", "a & b", "<tag>", "</tag>", "&amp;", "&&&", "<<>>",
    "Foglio 12 & particella 3 <parziale>", "R&D <lotto> \"virgolette\" 'apici'",
    "5 < 7 > 3", "Comune di Sant'Angelo & C.", "già scritto &lt;",
]
mismatches = [text for text in CORPUS
              if doc.escape(text) != saxutils.escape(text)]
check("stringhe che divergono dalla stdlib", len(mismatches), 0)
check_true("...su un corpus che contiene davvero i caratteri critici",
           any("&" in t for t in CORPUS) and any("<" in t for t in CORPUS)
           and any(">" in t for t in CORPUS))
check("l'ampersand e' sostituito per primo", doc.escape("&<"), "&amp;&lt;")

print()
print("=" * 78)
print("R4 -- la chiave API resta mascherata dopo il rinomino del tipo")
print("=" * 78)
check("il tipo si chiama per quello che fa", st.MASKED, "masked")
secret_keys = [name for name, spec in st.KEYS.items()
               if spec.kind == st.MASKED]
check_true("le due chiavi DEM sono ancora dichiarate cosi'",
           "dem/copernicus_key" in secret_keys
           and "dem/nasadem_key" in secret_keys)

store = st.SettingsStore()
store.set("dem/copernicus_key", "chiave-finta-1234567890")
check("secret() la restituisce in chiaro a chi la chiede",
      store.secret("dem/copernicus_key"), "chiave-finta-1234567890")
shown = store.all()["dem/copernicus_key"]
check_true("all() no: la maschera",
           shown != "chiave-finta-1234567890" and "1234567890" not in shown)
denied = False
try:
    store.secret("units/length")
except KeyError:
    denied = True
check_true("secret() rifiuta una chiave non dichiarata mascherata", denied)
store.set("dem/copernicus_key", "")

print()
print("=" * 78)
print("R5 -- nessun modulo scarta piu' un'eccezione senza lasciare traccia")
print("=" * 78)
# bandit's B110, as an AST walk, so the rule survives on a machine without
# bandit. Deliberately the same scope bandit uses by default: a handler whose
# whole body is `pass` AND which catches everything. A narrow
# `except AttributeError: pass` after a capability probe is a different thing
# -- it names the one failure it expects -- and neither bandit nor this rule
# objects to it.
BROAD = ("Exception", "BaseException")


def catches_everything(handler):
    """True for a bare `except:` or one that catches (Base)Exception."""
    if handler.type is None:
        return True
    parts = (handler.type.elts if isinstance(handler.type, ast.Tuple)
             else [handler.type])
    return any(isinstance(p, ast.Name) and p.id in BROAD for p in parts)


def silent_handlers(text, rel):
    """(place, ...) for every handler that catches all and records nothing."""
    out = []
    for node in ast.walk(ast.parse(text, rel)):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if (catches_everything(handler)
                    and all(isinstance(s, ast.Pass) for s in handler.body)):
                out.append("{0}:{1}".format(rel, handler.lineno))
    return out


silent = []
for rel, text in shipped_modules():
    silent.extend(silent_handlers(text, rel))
for entry in silent[:15]:
    print("      " + entry)
check("handler che scartano tutto in silenzio", len(silent), 0)

# Not vacuous: it flags the broad shape, and leaves the narrow one alone.
check("...riconosce except Exception: pass",
      len(silent_handlers("try:\n    x()\nexcept Exception:\n    pass\n",
                          "<probe>")), 1)
check("...e un except: nudo",
      len(silent_handlers("try:\n    x()\nexcept:\n    pass\n",
                          "<probe>")), 1)
check("...ma non un except tipizzato, che bandit non contesta",
      len(silent_handlers("try:\n    x()\nexcept AttributeError:\n    pass\n",
                          "<probe>")), 0)

print()
print("=" * 78)
print("R6 -- le impronte non cambiano valore")
print("=" * 78)
# usedforsecurity=False says what the hash is for; it must not alter the
# digest, or every cached DEM tile and every stored shape fingerprint would
# silently stop matching.
payload = b"geocad;1,2;3,4"
check("stesso digest con e senza il flag",
      hashlib.sha1(payload, usedforsecurity=False).hexdigest(),
      hashlib.sha1(payload).hexdigest())      # noqa: S324
key_a = ds.cache_key("copernicus", (1.0, 2.0, 3.0, 4.0), 9)
key_b = ds.cache_key("copernicus", (1.0, 2.0, 3.0, 4.0), 9)
check_true("cache_key e' stabile e lungo 16", key_a == key_b and len(key_a) == 16)
check_true("fingerprint di una geometria vuota resta vuoto",
           pr.geometry_signature(None) == "")

print("\n" + "=" * 78)
QGS.exitQgis()
if SKIPS:
    print("SKIPPED ({0}):".format(len(SKIPS)))
    for label, reason in SKIPS:
        print("   - {0}: {1}".format(label, reason))
if FAILURES:
    print("FAILED ({0}):".format(len(FAILURES)))
    for name in FAILURES:
        print("   - {0}".format(name))
    sys.exit(1)
print("ALL CHECKS PASSED")
