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
import configparser
import io
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
#: Files without which the package is refused -- by QGIS, by the official
#: plugin repository ("Cannot find LICENSE in the plugin package"), or by
#: this plugin's own loader. Checked before anything is zipped.
REQUIRED = ["metadata.txt", "__init__.py", "plugin.py", "icon.png",
            "icon.svg", "LICENSE",
            os.path.join("profiles", "cameras.json"),
            os.path.join("profiles", "drones.json")]


def read_version() -> str:
    path = os.path.join(SOURCE, "metadata.txt")
    with open(path, "r", encoding="utf-8") as handle:
        match = re.search(r"^version=(.+)$", handle.read(), re.M)
    if not match:
        raise SystemExit("version= not found in metadata.txt")
    return match.group(1).strip()


#: Keys QGIS reads off an installed plugin. Every one is fetched through
#: configparser below, so a value that cannot be fetched is caught here and
#: not by an operator staring at "plugin corrotto".
METADATA_KEYS = ("name", "qgisMinimumVersion", "qgisMaximumVersion",
                 "description", "about", "version", "author", "email",
                 "icon", "category", "tags", "experimental", "deprecated",
                 "supportsQt6", "changelog", "homepage", "tracker",
                 "repository")

#: Keys the Plugin Manager renders inside the plugin's own entry. A release
#: history there is history where a description belongs: it goes to
#: CHANGELOG.md, which is not shipped.
DESCRIPTION_KEYS = ("description", "about")


def parse_metadata(text: str):
    """Read metadata.txt the way QGIS reads it. Returns (values, problems).

    Deliberately identical to pyplugin_installer/installer_data.py:
    ``ConfigParser()`` -- strict, with BasicInterpolation -- then
    ``read_file`` and ``get("general", key)``. A regex cannot see a repeated
    option or a bare ``%``; configparser refuses both, and so does QGIS.
    """
    problems = []
    values = {}
    parser = configparser.ConfigParser()
    try:
        parser.read_file(io.StringIO(text))
    except configparser.Error as exc:
        return values, ["metadata.txt is not a valid QGIS metadata file: "
                        "{0}: {1}".format(type(exc).__name__, exc)]
    if not parser.has_section("general"):
        return values, ["metadata.txt has no [general] section"]
    for key in METADATA_KEYS:
        try:
            values[key] = parser.get("general", key)
        except configparser.NoOptionError:
            if key in ("name", "qgisMinimumVersion", "description", "version",
                       "author", "email", "icon"):
                problems.append("metadata.txt is missing '{0}='".format(key))
        except configparser.Error as exc:
            problems.append(
                "metadata.txt: '{0}' cannot be read ({1}: {2}). A bare '%' in "
                "a value is an interpolation escape; write '%%' or avoid "
                "it.".format(key, type(exc).__name__, exc))
    for key in ("name", "qgisMinimumVersion", "description", "version",
                "author", "email", "icon"):
        if key in values and not values[key].strip():
            problems.append("metadata.txt has an empty '{0}='".format(key))
    return values, problems


#: Hosts that mean "we had to write something here". A metadata URL
#: pointing at one of these is worse than an empty field: it tells whoever
#: clicks it that there is a repository.
PLACEHOLDER_HOSTS = ("example.invalid", "example.com", "example.org",
                     "example.net", "localhost", "127.0.0.1", "changeme",
                     "your-domain", "yourdomain", "TODO")


#: Keys the official plugin repository requires to be valid links. Its
#: rejection reads "Please provide valid url link for the following key(s)
#: in the metadata source: tracker, repository, homepage", so the build
#: fails on exactly those three rather than letting an upload discover it.
LINK_KEYS = ("homepage", "tracker", "repository")


def _placeholders_in(values):
    """Complaints about the three metadata links the repository reads."""
    problems = []
    for key in LINK_KEYS:
        url = (values.get(key) or "").strip()
        if not url:
            problems.append(
                "{0}= is empty: the official plugin repository refuses a "
                "package whose tracker, repository and homepage are not "
                "valid links.".format(key))
            continue
        lowered = url.lower()
        if any(host in lowered for host in PLACEHOLDER_HOSTS):
            problems.append(
                "{0}= is a placeholder ({1}): it has to be a real, publicly "
                "reachable URL.".format(key, url))
        elif not lowered.startswith(("http://", "https://")):
            problems.append(
                "{0}= is not a URL ({1}).".format(key, url))
    return problems


#: Words that mean the description was left in Italian. The repository asks
#: for English, and the Italian text lives in description[it]/about[it].
ITALIAN_MARKERS = (" per ", " con ", " della ", " delle ", " degli ",
                   " che ", " sono ", " questo ", " un'", "progettazione",
                   "rilievo", "impianto")


def _english_description(values):
    """Complaints about a description the repository would not accept."""
    problems = []
    text = (values.get("description") or "").strip()
    if not text:
        problems.append("description= is empty.")
        return problems
    lowered = " " + text.lower() + " "
    hits = [word for word in ITALIAN_MARKERS if word in lowered]
    if hits:
        problems.append(
            "description= looks like Italian ({0}): the plugin repository "
            "asks for English. Put the Italian in description[it]."
            .format(", ".join(sorted(set(w.strip() for w in hits))[:3])))
    return problems


def _history_in(values):
    """Complaints about version history found in the plugin's description.

    Two shapes, both of which have been in this file: a ``changelog`` key,
    and a description that narrates releases ("2.0.1 -- ...", "dalla 1.39").
    """
    import re                                                   # noqa: PLC0415

    problems = []
    # The key itself has to be there -- QGIS reads it and records the miss in
    # error_details -- but it has to be empty. Content in it is the release
    # history showing up on the plugin's public page.
    if (values.get("changelog") or "").strip():
        problems.append(
            "metadata.txt carries a non-empty changelog=: the Plugin Manager "
            "shows it inside the plugin's entry. Leave the key empty and put "
            "the history in CHANGELOG.md.")
    for key in DESCRIPTION_KEYS:
        text = values.get(key) or ""
        versions = re.findall(r"\b\d+\.\d+\.\d+\b", text)
        # The compatibility statement names QGIS versions, which is a fact
        # about what it runs on, not a history of itself.
        narrated = [v for v in versions
                    if "QGIS" not in text[max(0, text.find(v) - 24):
                                          text.find(v)]]
        if narrated:
            problems.append(
                "{0}= narrates version history ({1}): the description says "
                "what the plugin does, CHANGELOG.md says what changed."
                .format(key, ", ".join(sorted(set(narrated))[:4])))
    return problems


def check_metadata() -> "list[str]":
    """Fail early on the metadata mistakes that break plugin installation."""
    path = os.path.join(SOURCE, "metadata.txt")
    with open(path, "r", encoding="utf-8-sig") as handle:
        text = handle.read()
    raw = open(path, "rb").read()
    values, problems = parse_metadata(text)
    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append("metadata.txt starts with a UTF-8 BOM; QGIS opens it "
                        "as plain utf8 and the first key becomes unreadable")

    # The version has to be one version. A package whose metadata and whose
    # __init__ disagree installs as one and reports as the other.
    init_path = os.path.join(SOURCE, "__init__.py")
    with open(init_path, "r", encoding="utf-8") as handle:
        found = re.search(r'__version__\s*=\s*["\']([^"\']+)', handle.read())
    if found is None:
        problems.append("__init__.py declares no __version__")
    elif values.get("version", "").strip() != found.group(1):
        problems.append(
            "version mismatch: metadata.txt says {0!r}, __init__.py says "
            "{1!r}".format(values.get("version", ""), found.group(1)))

    problems.extend(_history_in(values))
    problems.extend(_placeholders_in(values))
    problems.extend(_english_description(values))

    icon = values.get("icon", "").strip()
    if icon and not os.path.isfile(os.path.join(SOURCE, icon)):
        problems.append("icon= names {0}, which is not in the package".format(
            icon))

    for relative in REQUIRED:
        if not os.path.isfile(os.path.join(SOURCE, relative)):
            problems.append("missing required file: {0}".format(relative))

    # An empty or truncated LICENSE passes a file-exists check and fails the
    # upload, so the content is looked at as well.
    licence_path = os.path.join(SOURCE, "LICENSE")
    if os.path.isfile(licence_path):
        with open(licence_path, "r", encoding="utf-8") as handle:
            licence = handle.read()
        if "GNU GENERAL PUBLIC LICENSE" not in licence:
            problems.append(
                "LICENSE does not contain a GPL licence text. QGIS plugins "
                "link against PyQt and the QGIS API and have to be "
                "GPL-compatible.")
        elif len(licence) < 10000:
            problems.append(
                "LICENSE is only {0} characters: that is not the full "
                "licence text.".format(len(licence)))
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

        # The metadata that matters is the one inside the archive: read it
        # from there, with QGIS's own parser.
    with zipfile.ZipFile(archive_path) as archive:
        packed = archive.read("{0}/metadata.txt".format(PACKAGE))
        icon_bytes = archive.read("{0}/icon.png".format(PACKAGE))
    values, problems = parse_metadata(packed.decode("utf-8"))
    if problems:
        raise SystemExit("the metadata inside the archive is not readable:\n"
                         + "\n".join("  - " + p for p in problems))
    if not icon_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise SystemExit("icon.png in the archive is not a PNG")
    print("  verified: archive root is {0}/ and all required files present"
          .format(PACKAGE))
    print("  verified: metadata.txt inside the archive parses as QGIS parses "
          "it ({0} {1}, icon {2}, {3} bytes)".format(
              values.get("name", "?"), values.get("version", "?"),
              values.get("icon", "?"), len(icon_bytes)))


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
