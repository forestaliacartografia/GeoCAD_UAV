"""
Build an installable QGIS plugin ZIP.

    python zip_plugin.py                  # dist/geocad_uav-1.0.0.zip
    python zip_plugin.py --with-tests     # include the test suite
    python zip_plugin.py --out build/     # choose the output directory

The archive root is a single ``geocad_uav/`` folder, which is what
"Install from ZIP" expects. Tests, docs sources and caches are excluded by
default to keep the download small; ``--with-tests`` puts them back for anyone
who wants to run the suite from an installed copy.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = "geocad_uav"
SOURCE = os.path.join(HERE, PACKAGE)

EXCLUDE_DIRS = {"__pycache__", ".git", ".idea", ".vscode", ".pytest_cache",
                ".mypy_cache", ".ruff_cache"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".pyd", ".orig", ".rej", ".log", ".tmp",
                    ".swp", "~")
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db"}

#: Directories dropped unless --with-tests is given.
OPTIONAL_DIRS = {"tests"}

#: Files that must be present, or the archive is not installable.
REQUIRED = ["metadata.txt", "__init__.py", "plugin.py", "icon.svg",
            os.path.join("profiles", "cameras.json"),
            os.path.join("profiles", "drones.json")]


def read_version() -> str:
    path = os.path.join(SOURCE, "metadata.txt")
    with open(path, "r", encoding="utf-8") as handle:
        match = re.search(r"^version=(.+)$", handle.read(), re.M)
    if not match:
        raise SystemExit("version= not found in metadata.txt")
    return match.group(1).strip()


def check_metadata() -> "list[str]":
    """Fail early on the metadata mistakes that break plugin installation."""
    problems = []
    path = os.path.join(SOURCE, "metadata.txt")
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    for key in ("name", "qgisMinimumVersion", "description", "version",
                "author", "email"):
        if not re.search(r"^{0}=\s*\S".format(key), text, re.M):
            problems.append("metadata.txt is missing a non-empty '{0}='".format(key))
    for relative in REQUIRED:
        if not os.path.isfile(os.path.join(SOURCE, relative)):
            problems.append("missing required file: {0}".format(relative))
    return problems


def should_skip(path: str, name: str, with_tests: bool) -> bool:
    if name in EXCLUDE_NAMES or name.endswith(EXCLUDE_SUFFIXES):
        return True
    parts = os.path.relpath(path, SOURCE).split(os.sep)
    if any(part in EXCLUDE_DIRS for part in parts):
        return True
    if not with_tests and parts and parts[0] in OPTIONAL_DIRS:
        return True
    return False


def build(out_dir: str, with_tests: bool) -> str:
    problems = check_metadata()
    if problems:
        for problem in problems:
            print("  ERROR: {0}".format(problem))
        raise SystemExit("metadata check failed; archive not written")

    version = read_version()
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "{0}-{1}.zip".format(PACKAGE, version))

    count = 0
    total = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk(SOURCE):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for name in sorted(files):
                full = os.path.join(root, name)
                if should_skip(full, name, with_tests):
                    continue
                relative = os.path.relpath(full, HERE)
                archive.write(full, relative.replace(os.sep, "/"))
                count += 1
                total += os.path.getsize(full)

    print("  package : {0}".format(PACKAGE))
    print("  version : {0}".format(version))
    print("  files   : {0} ({1:,} bytes uncompressed)".format(count, total))
    print("  tests   : {0}".format("included" if with_tests else "excluded"))
    print("  archive : {0} ({1:,} bytes)".format(target,
                                                 os.path.getsize(target)))
    return target


def verify(archive_path: str) -> None:
    """Re-open the archive and confirm it looks installable."""
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        bad = archive.testzip()
    if bad is not None:
        raise SystemExit("corrupt entry in archive: {0}".format(bad))
    roots = {name.split("/")[0] for name in names}
    if roots != {PACKAGE}:
        raise SystemExit(
            "archive root must be exactly '{0}/', found {1}".format(
                PACKAGE, sorted(roots)))
    for relative in REQUIRED:
        entry = "{0}/{1}".format(PACKAGE, relative.replace(os.sep, "/"))
        if entry not in names:
            raise SystemExit("archive is missing {0}".format(entry))
    print("  verified: archive root is {0}/ and all required files present"
          .format(PACKAGE))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the plugin ZIP")
    parser.add_argument("--out", default=os.path.join(HERE, "dist"))
    parser.add_argument("--with-tests", action="store_true")
    args = parser.parse_args()

    print("Building GeoCad UAV Toolkit plugin archive")
    archive = build(args.out, args.with_tests)
    verify(archive)
    print("\nInstall in QGIS with:  Plugins > Manage and Install Plugins >")
    print("                       Install from ZIP > {0}".format(archive))
    return 0


if __name__ == "__main__":
    sys.exit(main())
