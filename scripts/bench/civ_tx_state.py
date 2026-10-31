#!/usr/bin/env python3
"""Raw CI-V TX-state read (and emergency unkey) for the owner-present TX bench.

Used by scripts/bench/tx_bench_windows.ps1 as the last teardown step after the
server is stopped: reads raw CI-V 1C 00 on the serial port and sends a raw
unkey if the radio reads TX. Needs pyserial; everything else is the standard
library. The port is opened with DTR/RTS held low BEFORE open() so the open
itself cannot key the radio.
"""

from __future__ import annotations

import argparse
import sys
import time


def read_frame(addr):
    """CI-V TX-state read (1C 00) frame for controller address ``addr``."""
    return bytes((0xFE, 0xFE, addr, 0xE0, 0x1C, 0x00, 0xFD))


def unkey_frame(addr):
    """CI-V force-RX (1C 00 00) frame for controller address ``addr``."""
    return bytes((0xFE, 0xFE, addr, 0xE0, 0x1C, 0x00, 0x00, 0xFD))


def parse_tx_state(reply, addr):
    """Return 1 (TX), 0 (RX), or None (no answer) from a raw 1C 00 reply."""
    marker = bytes((0xFE, 0xFE, 0xE0, addr, 0x1C, 0x00))
    at = reply.find(marker)
    if at < 0 or len(reply) <= at + len(marker):
        return None
    return reply[at + len(marker)]


def _exchange(port, addr, baud, frame):
    """Write one frame and read the reply; DTR/RTS are low before open."""
    import serial

    dev = serial.Serial()
    dev.port = port
    dev.baudrate = baud
    dev.timeout = 0.8
    dev.dtr = False
    dev.rts = False
    dev.open()
    try:
        dev.reset_input_buffer()
        dev.write(frame)
        time.sleep(0.5)
        return dev.read(256)
    finally:
        dev.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="serial port, e.g. COM3")
    parser.add_argument("--addr", required=True,
                        help="CI-V controller address, hex without 0x (e.g. 94)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--unkey-if-keyed", action="store_true",
                        help="send a raw unkey and re-read when the radio reads TX")
    args = parser.parse_args(argv)
    try:
        addr = int(args.addr, 16)
    except ValueError:
        parser.error("--addr must be hex without 0x, e.g. 94")
    if not 0 <= addr <= 0xFF:
        parser.error("--addr must fit in one byte")
    try:
        state = parse_tx_state(_exchange(args.port, addr, args.baud,
                                         read_frame(addr)), addr)
    except Exception as exc:
        print(f"raw CI-V read failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"tx 1C 00 state byte: {state} (0=RX, 1=TX, None=no answer)")
    if state == 1 and args.unkey_if_keyed:
        try:
            _exchange(args.port, addr, args.baud, unkey_frame(addr))
            state = parse_tx_state(_exchange(args.port, addr, args.baud,
                                             read_frame(addr)), addr)
        except Exception as exc:
            print(f"raw CI-V unkey failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1
        print(f"after raw unkey, state byte: {state}")
    return 0 if state == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
