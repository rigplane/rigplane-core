"""Regression tests for MOR-3010 — CLI output must survive ``os._exit`` on a pipe.

``main()`` ends radio commands with ``os._exit(exit_code)`` (added against
orphaned PortAudio threads). ``os._exit`` skips buffer flushing, and a piped
stdout is block-buffered, so a command like ``rigplane ... status > out``
exits 0 with ``out`` empty.
"""

import subprocess
import sys
import textwrap


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
