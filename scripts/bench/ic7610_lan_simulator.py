"""Background IC-7610 LAN simulator for bench-nightly (loopback only).

Starts ``tests/testkit/ic7610_fakes.py: ic7610_lan_fake`` and writes the
control UDP port to a file so the workflow can set ``ICOM_PORT``. Runs until
SIGTERM/SIGINT, then stops cleanly.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from testkit.ic7610_fakes import ic7610_lan_fake  # noqa: E402


async def _run(port_file: Path) -> None:
    sim = ic7610_lan_fake(single_owner=True)
    await sim.start()
    port_file.write_text(str(sim.control_port), encoding="utf-8")
    stop = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_stop)
    await stop.wait()
    await sim.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port-file",
        type=Path,
        required=True,
        help="Path to write the simulator control port (single integer line)",
    )
    args = parser.parse_args()
    args.port_file.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(_run(args.port_file))


if __name__ == "__main__":
    main()
