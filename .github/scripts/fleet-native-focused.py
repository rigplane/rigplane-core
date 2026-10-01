"""Fixed Core checks over owner-pinned native runtime, dependencies and metadata."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET

SOURCE = "37b814a8cf7df0d74cab7c88f8d2401df7867bba"
LOCK = "a1e0f250ec508aa8f48d55bf123882c093b5ea1eb9ef69e16c093fe267fbbc1b"
PYPROJECT = "47dfdbce6bab3a56d1c1f83f31ba63723e6a0316d3afb7e2be956b20fce8483c"
PINS = {
    "dev-environments/manifest.json": "576602b37ca6947c40010db5abad7cf2c337264c092b4bc79c71bab6a330bcb8",
    "scripts/dev-environment-materialize.py": "45e856938dd709253f7f619391a04914b43ea97a947b206694a8c96aa5787823",
    "scripts/dev-environment-cache.py": "0ce74692ea091190900493a007785125decee1f8032feb78e9e1c81746a8f58b",
    "scripts/dev-environment-common.py": "e2a6ea4a01d5545ad11ba0080a141a776f2d68ddf008a5dfaba003966308b5f3",
    "scripts/dev-environment-ci.py": "4e4cb402fd4a7ef2caeed213218256584673650889afb1128a11c5972a68686e",
}
PLATFORMS = {"mac-home": "macos-aarch64", "mac-server": "macos-aarch64",
             "gtr7": "windows-x86_64", "nsu": "windows-x86_64"}
FIELDS = {"bootstrap_python", "bootstrap_sha256", "controls_root", "profile_path", "profile_sha256"}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def absolute(value, exists=True):
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, "absolute path required")
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                "link or reparse input refused")
    require(not exists or path.exists(), "required immutable input absent")
    return path


def document(path, expected):
    require(re.fullmatch(r"[0-9a-f]{64}", expected) and sha(path) == expected, "independent input pin mismatch")
    require(path.stat().st_size <= 65536, "input document exceeds bound")
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def junit(path):
    require(path.is_file() and path.stat().st_size <= 16 * 1024**2, "JUnit absent or oversized")
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    require(suites, "JUnit has no suites")
    counts = {name: sum(int(suite.attrib[name]) for suite in suites)
              for name in ("tests", "failures", "errors", "skipped")}
    require(all(value >= 0 for value in counts.values()) and counts["tests"] > 0, "invalid or empty JUnit")
    counts["differs_from_reference_200"] = counts["tests"] != 200
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=PLATFORMS, required=True)
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--report-dir", required=True)
    args = parser.parse_args()
    out = absolute(args.report_dir, exists=False)
    require(not out.exists(), "output must be fresh")
    out.mkdir(mode=0o700, parents=True)
    receipt = {"status": "blocked", "host": args.host, "runner_name": os.environ.get("FLEET_RUNNER_NAME", ""),
               "source_revision": SOURCE, "lockfile_sha256": LOCK,
               "lockfile_hashes": {"uv.lock": LOCK, "pyproject.toml": PYPROJECT}, "commands": [], "junit": None,
               "postflight_verified": False, "full_product_acceptance": False}
    receipt_path = out / "result.json"
    write(receipt_path, receipt)
    started, deadline = time.monotonic(), time.monotonic() + 32 * 60
    admitted = False
    postflight = None
    rc = 2
    try:
        rows = json.loads(os.environ["FLEET_NATIVE_PROFILES"])
        require(isinstance(rows, dict) and set(rows) == set(PLATFORMS), "exact four native profiles required")
        require(all(isinstance(row, dict) and set(row) == FIELDS for row in rows.values()), "unknown native profile fields")
        require(all(isinstance(value, str) and 0 < len(value) <= 4096 for row in rows.values() for value in row.values()),
                "native profile values must be bounded strings")
        require(re.fullmatch(r"[0-9a-f]{40}", os.environ["FLEET_HELPER_REVISION"]), "exact trusted helper revision required")
        row = rows[args.host]
        checkout = absolute(args.checkout)
        controls = absolute(row["controls_root"])
        profile_path = absolute(row["profile_path"])
        bootstrap = absolute(row["bootstrap_python"])
        require(bootstrap == absolute(sys.executable) and sha(bootstrap) == row["bootstrap_sha256"], "bootstrap identity mismatch")
        profile = document(profile_path, row["profile_sha256"])
        require(profile.get("repository") == "rigplane/rigplane-core" and profile.get("source_revision") == SOURCE
                and profile.get("lockfile_hashes") == {"uv.lock": LOCK, "pyproject.toml": PYPROJECT}
                and profile.get("require_product_cache") is True,
                "Core source/lock/product contract mismatch")
        require(profile.get("platform") == PLATFORMS[args.host] and profile.get("bootstrap_sha256") == row["bootstrap_sha256"],
                "native profile identity mismatch")
        require(profile.get("system_root") == ("C:\\Windows" if args.host in ("gtr7", "nsu") else None), "native system root mismatch")
        roots = (checkout, controls, absolute(profile["profile_root"]))
        require(not any(out == path or out in path.parents or path in out.parents for path in roots), "output overlaps immutable input")
        helper = Path(__file__).resolve()
        helper_sha = sha(helper)
        receipt.update(helper_sha256=helper_sha, profile_sha256=row["profile_sha256"], platform=profile["platform"])

        def pins():
            for name, expected in PINS.items():
                require(sha(absolute(controls / name)) == expected, "control input changed")
            require(sha(profile_path) == row["profile_sha256"] and sha(helper) == helper_sha, "profile/helper changed")

        pins()
        home, temp = out / "home", out / "tmp"
        home.mkdir(mode=0o700)
        temp.mkdir(mode=0o700)
        env = {"HOME": str(home), "TMPDIR": str(temp), "TMP": str(temp), "TEMP": str(temp),
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "LC_ALL": "C"}
        if os.name == "nt":
            env.update(SYSTEMROOT="C:\\Windows", WINDIR="C:\\Windows", USERPROFILE=str(home))
            for key, relative in (("APPDATA", "AppData/Roaming"), ("LOCALAPPDATA", "AppData/Local")):
                path = home / relative
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                env[key] = str(path)

        def run(name, argv, limit):
            record = {"name": name, "argv": [str(value) for value in argv], "status": "not-completed", "returncode": None}
            receipt["commands"].append(record)
            write(receipt_path, receipt)
            remaining = deadline - time.monotonic()
            require(remaining > 0, "overall deadline elapsed")
            with (out / (name + ".log")).open("wb") as log:
                try:
                    completed = subprocess.run(record["argv"], cwd=checkout, env=env, stdin=subprocess.DEVNULL,
                                               stdout=log, stderr=subprocess.STDOUT, timeout=min(limit, remaining), check=False)
                except subprocess.TimeoutExpired:
                    record["status"] = "timeout"
                    write(receipt_path, receipt)
                    raise
            record.update(status="natural-exit", returncode=completed.returncode)
            write(receipt_path, receipt)
            return completed.returncode

        def verify(name):
            path = out / name
            command = [bootstrap, "-I", "-S", "-B", controls / "scripts/dev-environment-ci.py",
                       "--profile", profile_path, "--expected-profile-sha256", row["profile_sha256"],
                       "--checkout", checkout, "--report-dir", path]
            require(run(name, command, 650) == 0, name + " refused")
            result = json.loads((path / "preflight.json").read_bytes())
            require(result.get("status") == "verified" and result.get("product_cache_owner_accepted") is True
                    and result.get("source_revision") == SOURCE and result.get("platform") == profile["platform"],
                    name + " result mismatch")
            return result

        before = verify("preflight")
        postflight = verify
        admitted = True
        products = [cache for cache in before["caches"].values() if cache["kind"] == "product"]
        require(len(products) == 1, "one Core product dependency payload required")
        payload = absolute(products[0]["path"])
        site = absolute(payload / "site-packages")
        ruff = absolute(payload / "bin" / ("ruff.exe" if os.name == "nt" else "ruff"))
        python = absolute(before["tools"]["python"])
        project_path = absolute(checkout / "pyproject.toml")
        require(sha(project_path) == PYPROJECT, "frozen pyproject identity mismatch")
        project = tomllib.loads(project_path.read_text(encoding="utf-8"))["project"]
        require(project.get("name") == "rigplane" and project.get("version") == "3.0.0b10"
                and "name" not in project.get("dynamic", []) and "version" not in project.get("dynamic", []),
                "static Core metadata fields required")
        require(not any(dist.metadata.get("Name", "").lower().replace("_", "-") == "rigplane"
                        for dist in importlib.metadata.distributions(path=[str(site)])), "dependency payload shadows Core metadata")
        metadata_root = out / "source-metadata"
        metadata_dir = metadata_root / "rigplane-3.0.0b10.dist-info"
        metadata_dir.mkdir(mode=0o700, parents=True)
        metadata = metadata_dir / "METADATA"
        metadata.write_text("Metadata-Version: 2.1\nName: rigplane\nVersion: 3.0.0b10\n", encoding="utf-8")
        metadata_sha = sha(metadata)
        receipt.update(runtime_receipt_sha256=before["runtime_receipt_sha256"], dependency=products[0],
                       metadata_sha256=metadata_sha, mode="source-only-metadata-bootstrap", pyproject_sha256=PYPROJECT,
                       metadata_name=project["name"], metadata_version=project["version"], metadata_fixture=str(metadata),
                       core_installed=False, hatch_executed=False, frontend_build_executed=False)
        env["PATH"] = os.pathsep.join((str(python.parent), str(ruff.parent)))
        require(run("ruff-version", [ruff, "--version"], 10) == 0 and (out / "ruff-version.log").read_text().strip() == "ruff 0.15.2",
                "locked Ruff version mismatch")
        bootstrap_code = """import hashlib, importlib.metadata as m, json, sys
from pathlib import Path
site, source, tests, metadata, output, expected_sha = sys.argv[1:]
sys.path[:0] = [metadata, source, site, tests]
distributions = [d for d in m.distributions(path=sys.path) if d.metadata.get('Name', '').lower().replace('_', '-') == 'rigplane']
assert len(distributions) == 1, 'duplicate or missing Core metadata'
dist = distributions[0]
origin = Path(dist.locate_file('')).resolve()
expected = 'Metadata-Version: 2.1\\nName: rigplane\\nVersion: 3.0.0b10\\n'
assert origin == Path(metadata).resolve() and dist.read_text('METADATA') == expected
assert dist.metadata['Name'] == 'rigplane' and dist.version == m.version('rigplane') == '3.0.0b10'
assert hashlib.sha256(Path(metadata, 'rigplane-3.0.0b10.dist-info', 'METADATA').read_bytes()).hexdigest() == expected_sha
import rigplane
assert Path(rigplane.__file__).resolve() == Path(source, 'rigplane', '__init__.py').resolve()
assert rigplane.__version__ == '3.0.0b10'
Path(output, 'origins.json').write_text(json.dumps({'metadata_root': str(origin), 'core_module': str(Path(rigplane.__file__).resolve())})+'\\n')
import pytest
Path(output, 'pytest-version.txt').write_text(pytest.__version__+'\\n')
raise SystemExit(pytest.main(['-p', 'no:cacheprovider', '-p', 'pytest_asyncio.plugin', '-p', 'pytest_timeout', '-o', 'addopts=', '--basetemp', str(Path(output, 'pytest-tmp')), '--junitxml', str(Path(output, 'junit.xml')), 'tests/test_cli.py']))
"""
        pytest_rc = run("pytest", [python, "-I", "-S", "-B", "-c", bootstrap_code, site, checkout / "src",
                                   checkout / "tests", metadata_root, out, metadata_sha], 600)
        ruff_rc = run("ruff-check", [ruff, "check", "--no-cache", "src/rigplane/cli/__init__.py", "tests/test_cli.py"], 180)
        format_rc = run("ruff-format", [ruff, "format", "--check", "--no-cache", "src/rigplane/cli/__init__.py", "tests/test_cli.py"], 180)
        receipt["junit"] = junit(out / "junit.xml")
        receipt["origins"] = json.loads((out / "origins.json").read_bytes())
        require(sha(metadata) == metadata_sha, "Core metadata changed during checks")
        receipt["checks_natural_zero"] = pytest_rc == ruff_rc == format_rc == 0
        rc = 0 if receipt["checks_natural_zero"] and receipt["junit"]["failures"] == receipt["junit"]["errors"] == 0 else 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, ET.ParseError):
        receipt["status"] = "blocked-or-check-failed"
        rc = 2
    except KeyboardInterrupt:
        receipt["status"] = "cancelled"
        rc = 125
    finally:
        if admitted:
            try:
                after = postflight("postflight")
                require(after["caches"] == before["caches"] and after["tools"] == before["tools"]
                        and after["runtime_receipt_sha256"] == before["runtime_receipt_sha256"], "pre/post identity changed")
                pins()
                receipt["postflight_verified"] = True
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, KeyboardInterrupt):
                rc = 125 if rc == 125 else 2
        receipt["status"] = "checks-passed" if rc == 0 and receipt["postflight_verified"] else "nonacceptance"
        receipt["exit_code"] = rc
        receipt["duration_seconds"] = round(time.monotonic() - started, 3)
        write(receipt_path, receipt)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
