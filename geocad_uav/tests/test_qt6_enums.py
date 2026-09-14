"""
v2.2.4: scoped enums, the spelling PyQt6 requires.

The official plugin repository runs a Qt6 compatibility check on the uploaded
package and rejected 2.2.3 with 131 findings, all of the same shape: an enum
member written on its owning class (``Qgis`` then the member) instead of on
its enum (``Qgis``, ``MessageLevel``, then the member). PyQt5 accepts both;
PyQt6 keeps the second as the real name and the first only as an alias QGIS
adds back for compatibility.

Note that this file never writes an unscoped spelling, not even in prose or in
an expected value: R3 below reads every module in the package, this one
included, and a quoted example would be indistinguishable from a relapse.

This suite guards the fix from three sides:

* every scoped spelling the plugin uses resolves on *this* QGIS, and still
  means the same number as the alias it replaced -- so the rewrite cannot have
  changed behaviour silently;
* no shipped module writes an unscoped spelling any more (a plain text rule,
  so it bites on either Qt major);
* on a Qt6 build, a sweep asks the API itself about every ``Class.Member``
  the sources write, which catches enums nobody thought to list here.

NEEDS QGIS. Run with:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" ^
        geocad_uav\\tests\\test_qt6_enums.py
"""

import enum
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from qgis.core import (Qgis, QgsApplication,                     # noqa: E402
                       QgsProcessingParameterNumber, QgsWkbTypes)
import qgis.core as qgis_core                                    # noqa: E402

QGS = QgsApplication([], False)
QGS.initQgis()

import qgis.gui as qgis_gui                                      # noqa: E402
from qgis.gui import QgsMapCanvas                                # noqa: E402

from geocad_uav.cad import tools as cad_tools                    # noqa: E402
from geocad_uav.cad.tools import base as tb                      # noqa: E402
from geocad_uav.processing import mark_advanced                  # noqa: E402
from geocad_uav.tests import qt6_enum_table                      # noqa: E402

FAILURES = []
SKIPS = []

PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The rows come from the packager's table rather than a copy of it, so a
#: spelling can never be enforced in one place and forgotten in the other.
SCOPED = qt6_enum_table.SCOPED_ENUMS

#: What the Qt6 sweep reads out of the sources. Deliberately the same shape
#: the repository's checker looks for: a QGIS class, a dot, a capitalised name.
ACCESS = re.compile(r"\b(Qgis|Qgs[A-Za-z0-9_]+)\.([A-Z][A-Za-z0-9_]*)")


def check(label, got, expected):
    ok = got == expected
    print("  [{0}] {1:<58} got={2!s:<10} exp={3!s}".format(
        "ok  " if ok else "FAIL", label, got, expected))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    print("  [{0}] {1}".format("ok  " if condition else "FAIL", label))
    if not condition:
        FAILURES.append(label)


def resolve(name):
    """The class, from wherever QGIS keeps it."""
    for module in (qgis_core, qgis_gui):
        found = getattr(module, name, None)
        if found is not None:
            return found
    return None


def unscoped_hits(text, rel="<probe>"):
    """Members written on their class instead of on their enum.

    Returns ``(rel, line, wrong, right)`` per finding. Empty on a Qt5 build:
    there the members are plain integers and the API holds no record of which
    enum owns them, so the question cannot be asked at all.
    """
    hits = []
    for line_no, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        for cls_name, member in ACCESS.findall(code):
            cls = resolve(cls_name)
            if cls is None:
                continue
            value = getattr(cls, member, None)
            if not isinstance(value, enum.Enum):
                continue
            owner = type(value).__name__
            if owner == member or getattr(cls, owner, None) is not type(value):
                continue
            hits.append((rel, line_no, "{0}.{1}".format(cls_name, member),
                         "{0}.{1}.{2}".format(cls_name, owner, member)))
    return hits


def shipped_sources(include_tests=True):
    """Every .py the plugin owns, as (relative path, text)."""
    out = []
    for base, dirs, names in os.walk(PACKAGE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, PACKAGE).replace(os.sep, "/")
            if not include_tests and rel.startswith("tests/"):
                continue
            with open(path, encoding="utf-8") as handle:
                out.append((rel, handle.read()))
    return out


print("=" * 78)
print("R1 -- every scoped spelling resolves here, with the value recorded")
print("=" * 78)
print("QGIS {0}, Qt {1}".format(Qgis.QGIS_VERSION, Qgis.QT_VERSION_STR
                                if hasattr(Qgis, "QT_VERSION_STR") else "?"))

missing_class = []
for cls_name, enum_name, member, value in SCOPED:
    cls = resolve(cls_name)
    if cls is None:
        missing_class.append(cls_name)
        continue
    label = "{0}.{1}.{2}".format(cls_name, enum_name, member)
    try:
        got = int(getattr(getattr(cls, enum_name), member))
    except Exception as exc:                                    # noqa: BLE001
        got = "{0}: {1}".format(type(exc).__name__, exc)
    check(label, got, value)

check_true("...and every class in the table exists on this QGIS",
           not missing_class)

print()
print("=" * 78)
print("R2 -- the scoped name means what the alias it replaced meant")
print("=" * 78)
# Where QGIS still ships the old unscoped alias, the two must be the same
# object's value. This is what makes the rewrite a spelling change and not a
# behaviour change; where the alias is already gone, there is nothing to
# compare and the name is simply the only one left.
compared = 0
for cls_name, enum_name, member, value in SCOPED:
    cls = resolve(cls_name)
    if cls is None:
        continue
    alias = getattr(cls, member, None)
    if alias is None:
        continue
    compared += 1
    check("{0}.{1} == {0}.{2}.{1}".format(cls_name, member, enum_name),
          int(alias), value)
check_true("...and the aliases were actually there to compare against",
           compared >= 20)

print()
print("=" * 78)
print("R3 -- no module writes an unscoped spelling any more")
print("=" * 78)
offenders = []
for rel, text in shipped_sources():
    for cls_name, enum_name, member, _value in SCOPED:
        pattern = re.compile(r"\b" + cls_name + r"\." + member + r"\b")
        for line_no, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                offenders.append("{0}:{1}  {2}.{3}".format(
                    rel, line_no, cls_name, member))
for entry in offenders[:20]:
    print("      " + entry)
check("unscoped spellings left in the package", len(offenders), 0)

print()
print("=" * 78)
print("R4 -- ask the API itself, over every Class.Member the sources write")
print("=" * 78)
# On a Qt6 build every QGIS enum member is a Python enum object, so the API
# can be asked directly: what enum owns this name? Any member whose owning
# enum is not spelled out in the source is exactly what the repository
# flagged. On a Qt5 build the members are plain integers, the question has no
# answer, and this sweep reports that instead of pretending to pass.
sweep = []
for rel, text in shipped_sources():
    sweep.extend(unscoped_hits(text, rel))
for rel, line_no, wrong, right in sweep[:20]:
    print("      {0}:{1}  {2} -> {3}".format(rel, line_no, wrong, right))
check("members the API says are written on the wrong scope", len(sweep), 0)

# Zero findings mean nothing until the detector is shown to find something.
# The probe line is assembled from pieces so that this file still contains no
# unscoped spelling for R3 to trip over.
probe = unscoped_hits("level = Qgis." + "Warning")
print("      build exposes QGIS enums as Python enums: {0}".format(bool(probe)))
if probe:
    check_true("...proved by a detector that flags the bad spelling",
               len(probe) == 1 and probe[0][3].endswith("MessageLevel.Warning"))
else:
    print("  [ok  ] sweep inert on this build: PyQt5 exposes enum members as "
          "plain ints, so the")
    print("         API cannot be asked. R3 guards this build, R4 the Qt6 one")

print()
print("=" * 78)
print("R5 -- the three compatibility shims still hand back the right value")
print("=" * 78)
# These three probe the API at run time instead of hard-coding one spelling.
# The rewrite had to move the probe onto the scoped name as well: a shim that
# tests one name and returns another would look fine until the day the tested
# name disappears.
check("rubber_band_geometry_type(True)",
      int(tb.rubber_band_geometry_type(True)),
      int(QgsWkbTypes.GeometryType.PolygonGeometry))
check("rubber_band_geometry_type(False)",
      int(tb.rubber_band_geometry_type(False)),
      int(QgsWkbTypes.GeometryType.LineGeometry))

param = QgsProcessingParameterNumber(
    "probe", "probe", QgsProcessingParameterNumber.Type.Double)
before = int(param.flags())
mark_advanced(param)
check_true("mark_advanced() sets the advanced bit",
           int(param.flags()) & 2 == 2 and before & 2 == 0)
check_true("...and returns the parameter for inline use",
           mark_advanced(param) is param)

canvas = QgsMapCanvas()
tool = cad_tools.create_tool("digitize", canvas)
check_true("CadMapTool.flags() carries EditTool",
           int(tool.flags()) & 4 == 4)
tool.deactivate()

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
