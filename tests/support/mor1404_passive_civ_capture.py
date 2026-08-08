"""Passive inbound CI-V capture and offline replay for MOR-1404.

The live hook only tees the existing inbound frame iterator. It has no serial,
radio-command, polling, or control surface and never derives VFO identity from
frequency values.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable, Iterator, Sequence
from functools import wraps
from pathlib import Path
from typing import Any, TextIO

CAPTURE_SCHEMA = "rigplane.mor1404.passive-civ.v1"
CAPTURE_PATH_ENV = "RIGPLANE_MOR1404_CAPTURE_PATH"
_CAPTURE_MARKER = "__mor1404_passive_capture__"


class PassiveCivCapture:
    """Append-only evidence sink for exact inbound CI-V frame bytes."""

    def __init__(
        self,
        path: Path,
        *,
        wall_time_ns: Callable[[], int] = time.time_ns,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._wall_time_ns = wall_time_ns
        self._monotonic_ns = monotonic_ns
        self._seq = 0
        self._closed = False
        self._restore: Callable[[], None] | None = None
        self._file: TextIO = path.open("x", encoding="utf-8", newline="\n")
        self._append({"kind": "session", "schema": CAPTURE_SCHEMA})

    def record(self, frame_bytes: bytes) -> None:
        if self._closed:
            raise RuntimeError("capture is closed")
        self._append(
            {
                "frame_hex": bytes(frame_bytes).hex(),
                "kind": "frame",
                "monotonic_ns": self._monotonic_ns(),
                "seq": self._seq,
                "wall_time_ns": self._wall_time_ns(),
            }
        )
        self._seq += 1

    def close(self) -> None:
        if self._closed:
            return
        if self._restore is not None:
            self._restore()
        self._file.close()
        self._closed = True

    def _append(self, row: dict[str, object]) -> None:
        self._file.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        self._file.flush()


def install_capture(
    path: Path,
    *,
    civ_rx_module: Any | None = None,
    wall_time_ns: Callable[[], int] = time.time_ns,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> PassiveCivCapture:
    """Tee complete frames before parsing; never open or control a radio."""
    if civ_rx_module is None:
        from rigplane.runtime import _civ_rx as civ_rx_module

    original = civ_rx_module.iter_civ_frames
    if getattr(original, _CAPTURE_MARKER, False):
        raise RuntimeError("MOR-1404 passive capture is already installed")
    capture = PassiveCivCapture(
        path, wall_time_ns=wall_time_ns, monotonic_ns=monotonic_ns
    )

    @wraps(original)
    def tee(payload: bytes) -> Iterator[bytes]:
        for frame_bytes in original(payload):
            capture.record(frame_bytes)
            yield frame_bytes

    def restore() -> None:
        if civ_rx_module.iter_civ_frames is tee:
            civ_rx_module.iter_civ_frames = original

    setattr(tee, _CAPTURE_MARKER, True)
    capture._restore = restore
    civ_rx_module.iter_civ_frames = tee
    return capture


def install_from_environment() -> PassiveCivCapture | None:
    value = os.environ.get(CAPTURE_PATH_ENV)
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{CAPTURE_PATH_ENV} must be an absolute path")
    return install_capture(path)


def _load_rows(path: Path) -> list[dict[str, Any]]:
    try:
        decoded = [json.loads(line) for line in path.read_text().splitlines()]
    except json.JSONDecodeError as exc:
        raise ValueError("capture contains invalid JSON") from exc
    if not decoded or decoded[0] != {"kind": "session", "schema": CAPTURE_SCHEMA}:
        raise ValueError("capture header is missing or incompatible")
    frames = decoded[1:]
    previous_monotonic = -1
    for expected_seq, row in enumerate(frames):
        if not isinstance(row, dict) or row.get("kind") != "frame":
            raise ValueError(f"invalid frame row at index {expected_seq}")
        if row.get("seq") != expected_seq:
            raise ValueError(f"non-contiguous frame sequence at index {expected_seq}")
        for name in ("wall_time_ns", "monotonic_ns"):
            value = row.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"invalid {name} at seq {expected_seq}")
        if row["monotonic_ns"] < previous_monotonic:
            raise ValueError(f"monotonic time regressed at seq {expected_seq}")
        previous_monotonic = row["monotonic_ns"]
        try:
            raw = bytes.fromhex(row.get("frame_hex", ""))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid frame hex at seq {expected_seq}") from exc
        if len(raw) < 6 or not raw.startswith(b"\xfe\xfe") or not raw.endswith(b"\xfd"):
            raise ValueError(f"invalid CI-V framing at seq {expected_seq}")
    return frames


def replay_capture(path: Path) -> dict[str, object]:
    """Return ordered raw evidence and a direction-aware command-07 verdict."""
    from rigplane.commands import CONTROLLER_ADDR, parse_civ_frame

    rows = _load_rows(path)
    timeline: list[dict[str, object]] = []
    command_07: list[dict[str, object]] = []
    selector_07: list[dict[str, object]] = []
    first_monotonic = rows[0]["monotonic_ns"] if rows else 0
    for row in rows:
        raw = bytes.fromhex(row["frame_hex"])
        try:
            frame = parse_civ_frame(raw)
        except ValueError as exc:
            raise ValueError(f"invalid CI-V frame at seq {row['seq']}") from exc
        delta = row["monotonic_ns"] - first_monotonic
        event = {
            "command_hex": f"{frame.command:02x}",
            "data_hex": frame.data.hex(),
            "delta_ns": delta,
            "frame_hex": raw.hex(),
            "from_addr": f"{frame.from_addr:02x}",
            "seq": row["seq"],
            "to_addr": f"{frame.to_addr:02x}",
        }
        timeline.append(
            event
            | {
                "monotonic_ns": row["monotonic_ns"],
                "wall_time_ns": row["wall_time_ns"],
            }
        )
        if frame.command == 0x07:
            command_07.append(event)
            if (
                frame.from_addr == 0x94
                and frame.to_addr in (CONTROLLER_ADDR, 0x00)
                and frame.sub is None
                and frame.data in (b"\x00", b"\x01")
            ):
                selector_07.append(event)
    present = bool(selector_07)
    return {
        "command_07_frames": command_07,
        "command_07_present": bool(command_07),
        "frame_count": len(timeline),
        "schema": CAPTURE_SCHEMA,
        "selector_07_frames": selector_07,
        "selector_07_present": present,
        "timeline": timeline,
        "verdict": "selector_07_present" if present else "selector_07_absent",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    rendered = json.dumps(replay_capture(args.capture), indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
