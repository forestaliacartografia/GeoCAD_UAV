"""
Run the GeoCad UAV Toolkit test suite.

Two families, deliberately kept separate:

* **pure**  -- numpy only, runs on any Python 3.9+ with numpy.
* **qgis**  -- needs GEOS, layers, writers and the Processing framework, so it
  must run under the QGIS Python.

    python run_tests.py                 # pure tests, current interpreter
    python run_tests.py --list          # show what would run
    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" run_tests.py --all

Exit code is non-zero if any suite fails, so this is CI-usable as-is.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.join(HERE, "geocad_uav", "tests")

PURE = ["test_photogrammetry.py", "test_terrain_follow.py", "test_geometry.py",
        "test_grid.py", "test_forest.py"]
QGIS = ["test_survey.py", "test_cad.py", "test_mission.py",
        "test_processing.py", "test_map_tools.py", "test_ui_shell.py",
        "test_panels.py", "test_uav_panel.py"]


def has_qgis() -> bool:
    try:
        import qgis.core                                        # noqa: F401
        return True
    except ImportError:
        return False


def run(name: str) -> tuple:
    path = os.path.join(TESTS, name)
    if not os.path.isfile(path):
        return name, False, 0.0, "file not found"
    started = time.time()
    proc = subprocess.run([sys.executable, "-u", path], capture_output=True,
                          text=True, cwd=HERE)
    elapsed = time.time() - started
    output = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0 and "ALL CHECKS PASSED" in output
    detail = ""
    if not ok:
        tail = [ln for ln in output.splitlines() if "FAIL" in ln or "Error" in ln]
        detail = " | ".join(tail[-4:]) or "exit {0}".format(proc.returncode)
    return name, ok, elapsed, detail


def main() -> int:
    parser = argparse.ArgumentParser(description="GeoCad UAV Toolkit tests")
    parser.add_argument("--all", action="store_true",
                        help="also run the QGIS-dependent suites")
    parser.add_argument("--qgis-only", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    qgis_available = has_qgis()
    if args.qgis_only:
        suites = QGIS
    elif args.all:
        suites = PURE + QGIS
    else:
        suites = PURE + (QGIS if qgis_available else [])

    if args.list:
        for name in suites:
            print("  " + name)
        return 0

    if any(s in QGIS for s in suites) and not qgis_available:
        print("QGIS is not importable from this interpreter; run the QGIS-"
              "dependent suites with python-qgis-ltr.bat.\n")
        suites = [s for s in suites if s not in QGIS]

    print("GeoCad UAV Toolkit - test suite")
    print("interpreter: {0}".format(sys.executable))
    print("QGIS available: {0}".format("yes" if qgis_available else "no"))
    print("-" * 68)

    failures = []
    total = 0.0
    for name in suites:
        label, ok, elapsed, detail = run(name)
        total += elapsed
        print("  [{0}] {1:<28} {2:6.2f}s  {3}".format(
            "PASS" if ok else "FAIL", label, elapsed, detail))
        if not ok:
            failures.append(label)

    print("-" * 68)
    print("{0} suite(s) in {1:.1f}s".format(len(suites), total))
    if failures:
        print("FAILED: {0}".format(", ".join(failures)))
        return 1
    print("ALL SUITES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
