"""Regression tests for MOR-3010 — CLI output must survive ``os._exit`` on a pipe.

``main()`` ends radio commands with ``os._exit(exit_code)`` (added against
orphaned PortAudio threads). ``os._exit`` skips buffer flushing, and a piped
stdout is block-buffered, so a command like ``rigplane ... status > out``
exits 0 with ``out`` empty.
"""

import asyncio
import subprocess
import sys
import textwrap
from unittest import mock

import pytest


def test_radio_command_output_survives_piped_stdout() -> None:
    """A radio command run with stdout as a pipe must still emit its output.

    Runs the CLI in a subprocess (``capture_output=True`` makes stdout a
    pipe), replaces ``rigplane.cli._run`` with a stub that prints a marker,
    and asserts the marker reaches the parent through the pipe with exit
    code 0.
    """
    script = textwrap.dedent(
        """
        import sys

        import rigplane.cli

        async def _fake_run(args):
            print("MOR3010_MARKER status-ok")
            return 0

        rigplane.cli._run = _fake_run
        sys.argv = [
            "rigplane",
            "--model", "IC-7300",
            "--serial-port", "/dev/null",
            "status",
        ]
        rigplane.cli.main()
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"CLI exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "MOR3010_MARKER status-ok" in result.stdout, (
        f"marker lost through the pipe.\n"
        f"stdout:\n{result.stdout!r}\n"
        f"stderr:\n{result.stderr!r}"
    )


@pytest.mark.asyncio
async def test_backstop_forced_exit_skips_stdio_flush() -> None:
    """The backstop's forced exit must not flush stdio (MOR-3010 review).

    ``_ShutdownBackstop._enforce`` runs on the event-loop thread, where an
    unbounded ``flush()`` can block forever (a full pipe whose reader
    stalled, or another thread stuck in a write holding the BufferedWriter
    lock). That would keep the backstop from exiting and break MOR-2875's
    bounded shutdown. Only ``main()``'s two normal ``os._exit`` calls flush.
    """
    from rigplane import cli

    stop = asyncio.Event()

    async def ignores_cancel() -> None:
        while not stop.is_set():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                continue

    shutdown = asyncio.create_task(ignores_cancel())
    backstop = cli._ShutdownBackstop(shutdown, bound_s=0)
    try:
        with (
            mock.patch.object(cli, "_flush_stdio_before_forced_exit") as flush_mock,
            mock.patch.object(cli.os, "_exit") as exit_mock,
        ):
            await asyncio.wait_for(backstop._enforce(), timeout=30)  # noqa: SLF001
    finally:
        stop.set()
        shutdown.cancel()
        await asyncio.gather(shutdown, return_exceptions=True)

    flush_mock.assert_not_called()
    exit_mock.assert_called_once_with(130)
