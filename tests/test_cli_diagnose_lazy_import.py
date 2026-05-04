"""Regression test for issue #1413: ``aiohttp`` must not be imported at CLI load.

``aiohttp`` is a dev-only / optional dependency (declared in
``[dependency-groups].dev``, not in ``[project].dependencies``). It is only
needed when the user actually invokes ``icom-lan diagnose --upload``. Importing
it eagerly would break every other CLI command (``icom-lan status``,
``icom-lan freq`` …) for users who installed only the runtime requirements
— that's the bug Codex flagged on PR #1413.

This test runs in an **isolated subprocess** (so already-imported modules in
the parent pytest process can't paper over the issue), blocks ``aiohttp`` via
``sys.meta_path``, and asserts that ``icom_lan.cli`` loads and ``_build_parser``
returns a usable parser.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_cli_module_loads_without_aiohttp() -> None:
    """Importing ``icom_lan.cli`` must not pull in ``aiohttp`` transitively."""
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
        import icom_lan.cli
        parser = icom_lan.cli._build_parser()
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
