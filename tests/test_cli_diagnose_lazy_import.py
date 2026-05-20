"""Regression test for issue #1413: ``aiohttp`` must not be imported at CLI load.

``aiohttp`` is a dev-only / optional dependency (declared in
``[dependency-groups].dev``, not in ``[project].dependencies``). It is only
needed when the user actually invokes ``rigplane diagnose --upload``. Importing
it eagerly would break every other CLI command (``rigplane status``,
``rigplane freq`` …) for users who installed only the runtime requirements
— that's the bug Codex flagged on PR #1413.

This test runs in an **isolated subprocess** (so already-imported modules in
the parent pytest process can't paper over the issue), blocks ``aiohttp`` via
``sys.meta_path``, and asserts that ``rigplane.cli`` loads and ``_build_parser``
returns a usable parser.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


def test_cli_module_loads_without_aiohttp() -> None:
    """Importing ``rigplane.cli`` must not pull in ``aiohttp`` transitively."""
    script = textwrap.dedent(
        """
        import sys

        # Block aiohttp at import time, mimicking a runtime-only install.
        class _AiohttpBlocker:
            def find_spec(self, name, path=None, target=None):
                if name == "aiohttp" or name.startswith("aiohttp."):
                    raise ImportError(
                        f"aiohttp blocked by lazy-import regression test: {name}"
                    )
                return None

        sys.meta_path.insert(0, _AiohttpBlocker())

        # Importing the CLI package must succeed — ``_diagnose`` is imported
        # at module scope by ``cli/__init__.py``, so this exercises the bug.
        import rigplane.cli
        parser = rigplane.cli._build_parser()
        assert parser is not None

        # Sanity-check: ``aiohttp`` truly never made it into ``sys.modules``.
        leaked = [m for m in sys.modules if m == "aiohttp" or m.startswith("aiohttp.")]
        assert not leaked, f"aiohttp leaked into sys.modules: {leaked}"

        # ``add_subparser`` must also work — the diagnose subcommand needs to
        # be registrable without aiohttp; only its run-path needs it.
        ns = parser.parse_args(["diagnose", "--output", "/tmp/x.zip"])
        assert ns.command == "diagnose"

        print("OK")
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"CLI import failed without aiohttp.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout


def test_diagnose_save_only_runs_without_aiohttp(tmp_path: Path) -> None:
    """``rigplane diagnose --output ...`` (no --upload) must run without aiohttp.

    Regression for the follow-up bug on PR-A (#1417): the lazy-import work
    moved diagnostics symbols out of module level into ``_run_async``, but
    the imports happened at the TOP of ``_run_async``, BEFORE checking
    ``args.upload``. Result: even ``rigplane diagnose`` (save-only) would
    pull ``aiohttp`` via the lazy ``upload_bundle`` re-export and crash for
    ``pip install rigplane`` users (aiohttp is dev-only).

    This test executes the full save-only path in a subprocess with
    ``aiohttp`` blocked on ``sys.meta_path`` and asserts:

    - exit code 0 (the bundle was built),
    - the output zip exists,
    - ``aiohttp`` was never imported.
    """
    out_zip = tmp_path / "diag.zip"

    script = textwrap.dedent(
        f"""
        import sys

        # Block aiohttp BEFORE any rigplane import.
        class _AiohttpBlocker:
            def find_spec(self, name, path=None, target=None):
                if name == "aiohttp" or name.startswith("aiohttp."):
                    raise ImportError(
                        f"aiohttp blocked by lazy-import regression test: {{name}}"
                    )
                return None

        sys.meta_path.insert(0, _AiohttpBlocker())

        from rigplane.cli import _build_parser
        from rigplane.cli._diagnose import run as run_diagnose

        parser = _build_parser()
        args = parser.parse_args(
            ["diagnose", "--output", {str(out_zip)!r}, "--no-confirm"]
        )
        rc = run_diagnose(args)
        if rc != 0:
            print(f"FAIL rc={{rc}}", file=sys.stderr)
            sys.exit(rc)

        leaked = [m for m in sys.modules if m == "aiohttp" or m.startswith("aiohttp.")]
        if leaked:
            print(f"FAIL aiohttp leaked: {{leaked}}", file=sys.stderr)
            sys.exit(2)

        print("OK")
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"diagnose save-only crashed without aiohttp.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout
    assert out_zip.exists(), f"bundle was not created at {out_zip}"


def test_diagnose_upload_without_aiohttp_emits_friendly_error(tmp_path: Path) -> None:
    """``rigplane diagnose --upload`` without aiohttp must NOT show a traceback.

    Regression for the L1.8 UX finding: when ``aiohttp`` is missing and the
    user explicitly passes ``--upload``, the command should print a short
    actionable message (install hint + path of the locally-saved bundle)
    and exit with a documented non-zero code, rather than dump a Python
    traceback. The bundle is built before the upload import and remains
    on disk so the user can attach it to a GitHub issue manually.

    Asserts:

    - exit code is 9 (the dedicated "aiohttp missing" code),
    - stderr contains the install hint,
    - stderr contains the bundle path,
    - stderr contains NO ``Traceback`` line,
    - the bundle was created on disk despite the upload failure.
    """
    out_zip = tmp_path / "diag-upload.zip"

    script = textwrap.dedent(
        f"""
        import sys

        class _AiohttpBlocker:
            def find_spec(self, name, path=None, target=None):
                if name == "aiohttp" or name.startswith("aiohttp."):
                    raise ImportError(
                        f"aiohttp blocked by upload UX regression test: {{name}}"
                    )
                return None

        sys.meta_path.insert(0, _AiohttpBlocker())

        from rigplane.cli import _build_parser
        from rigplane.cli._diagnose import run as run_diagnose

        parser = _build_parser()
        args = parser.parse_args(
            [
                "diagnose",
                "--upload",
                "--no-confirm",
                "--description", "upload-without-aiohttp UX test",
                "--output", {str(out_zip)!r},
            ]
        )
        rc = run_diagnose(args)
        sys.exit(rc)
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 9, (
        f"expected exit code 9 (aiohttp missing), got {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "Upload requires the 'aiohttp' package" in result.stderr
    assert "Repair the installation" in result.stderr
    assert str(out_zip) in result.stderr
    assert "Traceback" not in result.stderr, (
        f"a friendly error must not include a Python traceback.\n"
        f"stderr:\n{result.stderr}"
    )
    assert out_zip.exists(), (
        f"bundle was not created at {out_zip} despite upload failure; "
        f"the local file should be preserved so the user can attach manually"
    )
