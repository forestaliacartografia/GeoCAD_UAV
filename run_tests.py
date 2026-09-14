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
        "test_panels.py", "test_uav_panel.py", "test_dem_source.py",
        "test_player.py", "test_export_panel.py",
        "test_reforestation_m01_m03.py",
        "test_reforestation_s4.py",
        "test_reforestation_s5.py",
        "test_cadastre.py",
        "test_reforestation_s6.py",
        "test_cadastre_spatial.py",
        "test_cadastre_multicomune.py",
        "test_cad_cadastre_multicomune.py",
        "test_uav_route_end_to_end.py",
        "test_workflow_ui.py",
        "test_zones.py",
        "test_natural.py",
        "test_map_end_to_end.py",
        "test_contours_gui.py",
        "test_editing.py",
        "test_cartography.py",
        "test_documents.py",
        "test_project_file.py",
        "test_acceptance.py",
        "test_dashboard.py",
        "test_cad_cadastre.py",
        "test_cad_panel.py",
        "test_uav_planner.py",
        "test_uav_workflow.py",
        "test_wpml.py"]


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
    return name, ok, elapsed, detail, tally(output)


def tally(output: str) -> tuple:
    """``(passed, failed, skipped)`` individual checks in one suite's output.

    Every suite prints its checks in the same three shapes, so counting them
    here costs nothing and turns "35 suites passed" into a number that says
    how much was actually verified.
    """
    passed = failed = skipped = 0
    for line in output.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("[ok  ]"):
            passed += 1
        elif stripped.startswith("[FAIL]"):
            failed += 1
        elif stripped.startswith("[skip]"):
            skipped += 1
    return passed, failed, skipped


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
    checks = [0, 0, 0]
    for name in suites:
        label, ok, elapsed, detail, counted = run(name)
        total += elapsed
        for index, value in enumerate(counted):
            checks[index] += value
        print("  [{0}] {1:<28} {2:6.2f}s  {3:>5} ok  {4}".format(
            "PASS" if ok else "FAIL", label, elapsed, counted[0], detail))
        if not ok:
            failures.append(label)

    print("-" * 68)
    print("{0} suite(s) in {1:.1f}s".format(len(suites), total))
    print("verifiche ok={0} fail={1} skip={2}".format(*checks))
    if failures:
        print("FAILED: {0}".format(", ".join(failures)))
        return 1
    print("ALL SUITES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
