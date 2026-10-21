"""IC-7300 air capture: RX audio + time-aligned scope + radio state, then restore.

One clip = one tuned frequency/mode, recorded for a fixed time:

* the radio's USB audio in its native format (device default rate and channel
  count, int16) to a WAV, started on a UTC slot boundary when ``--slot-s`` is
  given (FT8: 15 s);
* the raw CI-V traffic, scope wave frames included, through the guarded
  recording bridge (``bridge.py``'s allow-list plus the few writes below) —
  its ``t`` axis is anchored to wall-clock time in the manifest, so audio and
  scope line up to within the audio callback latency;
* the radio state read before tuning, during the clip and after restore.

Receive side only. The only writes the bridge lets through are the ones this
script needs, each logged in the manifest: set frequency (05), set mode
(06 / 26), data mode (1A 06), the scope span and centre mode (27 14, 27 15)
and the scope panel / wave-output switches (27 10, 27 11). PTT, tuner, power,
CW send and every menu write stay refused. Whatever was changed is restored
and read back; a mismatch is reported and the exit code is 4.

Exit codes: 0 done, 3 connect failed, 4 radio state did not read back
identical, 5 the audio is not usable (callback overflow/underflow or a failed
WAV write; the radio is still restored).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import queue
import sys
import threading
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import winshim  # noqa: E402

winshim.apply()

from bridge import SCOPE_SWITCH_BODIES, make_guard  # noqa: E402

from rigplane.backends.config import SerialBackendConfig  # noqa: E402
from rigplane.backends.factory import create_radio  # noqa: E402
from rigplane.commands import CONTROLLER_ADDR  # noqa: E402
from rigplane_testkit.serial import CivFrameGuard, SerialCivRecorder  # noqa: E402

#: Seconds this host's clock runs AHEAD of true UTC (``--clock-offset-s``).
_CLOCK_OFFSET_S = 0.0


def _now() -> float:
    """Wall-clock seconds corrected to true UTC."""
    return time.time() - _CLOCK_OFFSET_S


#: Clip presets; every field can be overridden on the command line.
CLIPS: dict[str, dict[str, Any]] = {
    "ft8": {
        "freq_hz": 14_074_000,
        "mode": "USB",
        "data": True,
        "seconds": 345,
        "slot_s": 15,
    },
    "cw": {
        "freq_hz": 14_040_000,
        "mode": "CW",
        "data": False,
        "seconds": 240,
        "slot_s": 0,
    },
    "ssb": {
        "freq_hz": 14_250_000,
        "mode": "USB",
        "data": False,
        "seconds": 240,
        "slot_s": 0,
    },
}

_VALID_MODES = frozenset(range(0x00, 0x09))  # LSB .. RTTY-R
_HF_RANGE = (1_800_000, 54_000_000)


def _bcd_freq(data: bytes) -> int | None:
    """Decode a 5-byte little-endian BCD frequency, ``None`` if not BCD."""
    if len(data) != 5:
        return None
    value = 0
    for byte in reversed(data):
        hi, lo = byte >> 4, byte & 0x0F
        if hi > 9 or lo > 9:
            return None
        value = value * 100 + hi * 10 + lo
    return value


def write_allowed(body: bytes) -> bool:
    """Whether *body* (command byte onward) is one of the clip's tuning writes.

    Shape checks only; the manifest lists every write that actually passed.
    """
    if not body:
        return False
    cmd, rest = body[0], body[1:]
    if cmd == 0x05:  # set operating frequency
        freq = _bcd_freq(rest)
        return freq is not None and _HF_RANGE[0] <= freq <= _HF_RANGE[1]
    if cmd == 0x06:  # set operating mode (+ filter)
        return 1 <= len(rest) <= 2 and rest[0] in _VALID_MODES
    if cmd == 0x26:  # selected-VFO mode, data mode, filter
        return len(rest) == 4 and rest[0] == 0x00 and rest[1] in _VALID_MODES
    if cmd == 0x16 and rest[:1] == b"\x02":  # preamp: 00 off, 01 P.AMP1, 02 P.AMP2
        return len(rest) == 2 and rest[1] in (0x00, 0x01, 0x02)
    if cmd == 0x1A and rest[:1] == b"\x06":  # data mode (+ filter)
        return 2 <= len(rest) <= 3 and rest[1] in (0x00, 0x01)
    if cmd == 0x27 and rest[:1] == b"\x15":  # scope span
        return 1 <= len(rest) - 1 <= 7
    if cmd == 0x27 and rest[:1] == b"\x14":  # scope mode (centre / fixed)
        return rest[1:] in (b"\x00", b"\x00\x00")
    return False  # scope switches are already in bridge.make_guard


@dataclass
class AirGuard:
    """``bridge.make_guard`` plus the clip's tuning writes, logging the latter."""

    radio_addr: int
    base: CivFrameGuard
    writes: list[dict] = field(default_factory=list)
    t0: float = field(default_factory=time.monotonic)

    def allows(self, frame: bytes) -> bool:
        if len(frame) < 6 or frame[2] != self.radio_addr or frame[3] != CONTROLLER_ADDR:
            return False
        body = frame[4:-1]
        if self.base.allows(frame):
            if body in SCOPE_SWITCH_BODIES:  # state-changing: log it like the others
                self._log(body)
            return True
        if not write_allowed(body):
            return False
        self._log(body)
        return True

    def _log(self, body: bytes) -> None:
        self.writes.append(
            {"t": round(time.monotonic() - self.t0, 3), "body": body.hex()}
        )


class AudioRecorder(Protocol):
    def prepare(self) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> dict: ...


class UsbAudioRecorder:
    """Native-format USB audio capture to WAV (sounddevice).

    ``prepare()`` opens the writer and builds the input stream (the slow part);
    ``start()`` only starts it, so it can be called on the slot boundary.
    ``stop()`` reports problems instead of hiding them: PortAudio callback
    status flags (overflow, underflow) and a failed WAV write both make the
    returned stats ``valid: false``.
    """

    def __init__(
        self, device_index: int, wav_path: Path, *, sd_module: Any = None
    ) -> None:
        if sd_module is None:
            import sounddevice as sd_module
        info = sd_module.query_devices(device_index)
        self._sd = sd_module
        self._index = device_index
        self.device_name = str(info["name"])
        self.rate = int(info["default_samplerate"])
        self.channels = int(min(2, info["max_input_channels"]))
        self._path = wav_path
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._peak = 0
        self._frames = 0
        self._stream: Any = None
        self._thread: threading.Thread | None = None
        self._writer_error: str | None = None
        self._callback_status: dict[str, int] = {}
        self.first_callback_wall: float | None = None
        self.start_wall: float | None = None

    def _callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        if status:
            key = str(status)
            self._callback_status[key] = self._callback_status.get(key, 0) + 1
        if self._writer_error is not None:
            return  # the file is broken: do not pile up a queue nobody drains
        if self.first_callback_wall is None:
            self.first_callback_wall = _now()
        self._frames += frames
        samples = np.frombuffer(indata, dtype=np.int16)
        if samples.size:
            self._peak = max(self._peak, int(np.abs(samples.astype(np.int32)).max()))
        self._queue.put(bytes(indata))

    def _writer(self) -> None:
        try:
            with self._path.open("wb") as out, wave.open(out, "wb") as wav:
                wav.setnchannels(self.channels)
                wav.setsampwidth(2)
                wav.setframerate(self.rate)
                while (chunk := self._queue.get()) is not None:
                    wav.writeframes(chunk)
        except Exception as exc:  # disk full, NAS gone, bad path
            self._writer_error = f"{type(exc).__name__}: {exc}"

    def prepare(self) -> None:
        self._thread = threading.Thread(target=self._writer, daemon=True)
        self._thread.start()
        self._stream = self._sd.RawInputStream(
            device=self._index,
            samplerate=self.rate,
            channels=self.channels,
            dtype="int16",
            callback=self._callback,
        )

    def start(self) -> None:
        if self._stream is None:
            self.prepare()
        self.start_wall = _now()
        self._stream.start()

    def stop(self) -> dict:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                self._writer_error = (
                    self._writer_error or "writer thread did not finish"
                )
        valid = self._writer_error is None and not self._callback_status
        return {
            "device": self.device_name,
            "format": f"s16le {self.rate} Hz {self.channels} ch",
            "sample_rate_hz": self.rate,
            "channels": self.channels,
            "frames": self._frames,
            "peak": self._peak,
            "peak_fraction_of_full_scale": round(self._peak / 32768, 4),
            "start_wall_utc": _iso(self.start_wall),
            "first_callback_wall_utc": _iso(self.first_callback_wall),
            "callback_status": self._callback_status,
            "writer_error": self._writer_error,
            "valid": valid,
        }


def _iso(wall: float | None) -> str | None:
    return (
        None if wall is None else datetime.fromtimestamp(wall, timezone.utc).isoformat()
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


async def _snapshot(radio: Any, *, with_scope: bool) -> dict:
    mode, filt = await radio.get_mode()
    snap: dict[str, Any] = {
        "freq_hz": await radio.get_freq(),
        "mode": str(mode),
        "filter": filt,
    }
    with contextlib.suppress(Exception):
        snap["preamp"] = int(await radio.get_preamp())
    with contextlib.suppress(Exception):  # a radio that NAKs the read: leave it alone
        snap["data_mode"] = bool(await radio.get_data_mode())
    if with_scope:
        panel, output = await radio.get_scope_session_state()
        snap["scope_panel"], snap["scope_wave_output"] = panel, output
        for name in ("scope_span", "scope_mode"):
            with contextlib.suppress(Exception):
                snap[name] = await getattr(radio, f"get_{name}")()
    return snap


async def _restore(radio: Any, before: dict, tuned_preamp: int | None) -> list[str]:
    """Put every changed setting back; one failed step never stops the others.

    The preamp goes first, while the radio is still on the target band (it is
    stored per band); the rest follow in the order a mode set cannot undo.
    Returns one message per failed step (empty = all restored).
    """
    steps: list[tuple[str, Any]] = []
    if tuned_preamp is not None:
        steps.append(("preamp", lambda: radio.set_preamp(tuned_preamp)))
    steps.append(("mode", lambda: radio.set_mode(before["mode"], before["filter"])))
    if "data_mode" in before:  # after the mode: a mode set can clear it
        steps.append(("data_mode", lambda: radio.set_data_mode(before["data_mode"])))
    steps.append(("freq", lambda: radio.set_freq(before["freq_hz"])))
    for name in ("scope_span", "scope_mode"):
        if name in before:
            setter = getattr(radio, f"set_{name}")
            steps.append((name, lambda setter=setter, name=name: setter(before[name])))
    if "scope_panel" in before:
        steps.append(
            (
                "scope_session",
                lambda: radio.restore_scope_session_state(
                    (before["scope_panel"], before["scope_wave_output"])
                ),
            )
        )
    errors: list[str] = []
    for name, step in steps:
        try:
            await step()
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    return errors


async def _wait_for_slot(slot_s: int) -> float:
    """Sleep to the next UTC multiple of *slot_s* seconds; return that wall time."""
    now = _now()
    target = (int(now // slot_s) + 1) * slot_s
    await asyncio.sleep(max(0.0, target - now - 0.05))
    while _now() < target:
        await asyncio.sleep(0.001)
    return target


async def _wait_until(start_at: str) -> float:
    """Sleep to the given UTC instant (ISO 8601); refuse one already past."""
    target = datetime.fromisoformat(start_at).astimezone(timezone.utc).timestamp()
    now = _now()
    if target < now:
        raise ValueError(
            f"--start-at {start_at} is already {now - target:.1f} s in the past"
        )
    await asyncio.sleep(max(0.0, target - now - 0.05))
    while _now() < target:
        await asyncio.sleep(0.001)
    return target


async def run_clip(
    args: argparse.Namespace,
    *,
    opener: Any = None,
    audio_factory: Callable[[Path], AudioRecorder] | None = None,
) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"ic7300-{args.clip}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    rec_path = out_dir / f"{stem}.civ.jsonl"
    wav_path = out_dir / f"{stem}.wav"

    audio = audio_factory(wav_path) if audio_factory is not None else None
    guard = AirGuard(args.radio_addr, make_guard("IC-7300", args.radio_addr))
    rec = SerialCivRecorder(
        args.device,
        rec_path,
        baudrate=args.baud,
        opener=opener,
        client_filter=CivFrameGuard(guard.allows).new_filter,
    )
    await rec.start()
    wall_at_rec_t0 = _now() - (time.monotonic() - rec._t0)
    radio = create_radio(
        SerialBackendConfig(
            device=f"socket://127.0.0.1:{rec.listen_port}",
            baudrate=args.baud,
            radio_addr=args.radio_addr,
            model="IC-7300",
        )
    )
    manifest: dict[str, Any] = {
        "clip": args.clip,
        "radio": {"model": "IC-7300", "radio_addr": args.radio_addr},
        "target": {"freq_hz": args.freq_hz, "mode": args.mode, "data": args.data},
        "seconds": args.seconds,
        "slot_s": args.slot_s,
        "civ_t0_wall_utc": _iso(wall_at_rec_t0),
        "started_utc": _iso(_now()),
        "clock_offset_s": _CLOCK_OFFSET_S,
    }
    exit_code = 0
    try:
        try:
            await radio.connect()
        except Exception as exc:
            print(f"connect failed: {type(exc).__name__}", file=sys.stderr)
            return 3
        tuned_preamp: int | None = None
        before = await _snapshot(radio, with_scope=not args.no_scope)
        manifest["state_before"] = before
        print(json.dumps({"state_before": before}), flush=True)
        try:
            await radio.set_mode(args.mode)
            if "data_mode" in before:
                await radio.set_data_mode(args.data)
            await radio.set_freq(args.freq_hz)
            await asyncio.sleep(0.5)
            # The preamp is stored per band: change it only after tuning, and
            # put it back while still on the target band, before restoring.
            if args.preamp is not None:
                tuned_preamp = int(await radio.get_preamp())
                manifest["preamp_on_target_band_before"] = tuned_preamp
                await radio.set_preamp(args.preamp)
            if not args.no_scope:
                if args.scope_span:
                    await radio.set_scope_span(args.scope_span)
                await radio.enable_scope(timeout=15.0)
            await asyncio.sleep(1.0)  # let filters and the scope settle
            manifest["state_tuned"] = await _snapshot(
                radio, with_scope=not args.no_scope
            )
            if audio is not None and hasattr(audio, "prepare"):
                audio.prepare()  # the slow part happens before the boundary
            if args.start_at:
                manifest["slot_start_wall_utc"] = _iso(await _wait_until(args.start_at))
            elif args.slot_s:
                manifest["slot_start_wall_utc"] = _iso(
                    await _wait_for_slot(args.slot_s)
                )
            if audio is not None:
                audio.start()
            samples: list[dict] = []
            end = time.monotonic() + args.seconds
            while (left := end - time.monotonic()) > 0:
                await asyncio.sleep(min(5.0, left))
                with contextlib.suppress(Exception):  # a read, never a write
                    samples.append(
                        {
                            "t_wall_utc": _iso(_now()),
                            "s_meter": await radio.get_s_meter(),
                        }
                    )
            manifest["s_meter_samples"] = samples
            if audio is not None:
                manifest["audio"] = audio.stop()
                audio = None
                if manifest["audio"].get("valid") is False:
                    exit_code = 5  # radio restored below, but the audio is not usable
            manifest["state_end_of_clip"] = await _snapshot(
                radio, with_scope=not args.no_scope
            )
        finally:
            if audio is not None:
                with contextlib.suppress(Exception):
                    manifest["audio"] = audio.stop()
            if tuned_preamp is None:
                tuned_preamp = manifest.get("preamp_on_target_band_before")
            restore_errors = await _restore(radio, before, tuned_preamp)
            manifest["restore_errors"] = restore_errors
            if restore_errors:
                exit_code = 4
                print(json.dumps({"restore_errors": restore_errors}), flush=True)
        await asyncio.sleep(0.5)
        after = await _snapshot(radio, with_scope=not args.no_scope)
        manifest["state_after"] = after
        manifest["restored_identical"] = after == before
        if not manifest["restored_identical"]:
            exit_code = 4
    finally:
        with contextlib.suppress(Exception):
            await radio.disconnect()
        await rec.stop()
    manifest["writes_through_bridge"] = guard.writes
    manifest["blocked_chunks"] = rec.blocked_lines
    manifest["files"] = {
        p.name: {"sha256": _sha256(p), "bytes": p.stat().st_size}
        for p in (rec_path, wav_path)
        if p.exists()
    }
    (out_dir / f"{stem}.manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "manifest": f"{stem}.manifest.json",
                "restored_identical": manifest.get("restored_identical"),
                "blocked_chunks": rec.blocked_lines,
            }
        )
    )
    return exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--clip", choices=sorted(CLIPS), required=True)
    parser.add_argument("--device", required=True, help="serial device of the IC-7300")
    parser.add_argument("--out-dir", required=True, help="private output directory")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--radio-addr", type=lambda s: int(s, 0), default=0x94)
    parser.add_argument("--freq-hz", type=int)
    parser.add_argument("--mode")
    parser.add_argument("--data", type=lambda s: s.lower() in ("1", "true", "yes"))
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--slot-s", type=int)
    parser.add_argument(
        "--scope-span", type=int, help="scope span value for set_scope_span"
    )
    parser.add_argument(
        "--start-at",
        help="ISO 8601 UTC instant to start the audio (for a common start across radios)",
    )
    parser.add_argument(
        "--clock-offset-s",
        type=float,
        default=0.0,
        help="seconds this host's clock is ahead of true UTC (corrected in all anchors)",
    )
    parser.add_argument(
        "--preamp",
        type=int,
        choices=(0, 1, 2),
        help="preamp for the clip (restored after)",
    )
    parser.add_argument("--no-scope", action="store_true")
    parser.add_argument("--no-audio", action="store_true")
    args = parser.parse_args(argv)
    for key, value in CLIPS[args.clip].items():
        if getattr(args, key) is None:
            setattr(args, key, value)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    global _CLOCK_OFFSET_S
    _CLOCK_OFFSET_S = args.clock_offset_s
    factory: Callable[[Path], AudioRecorder] | None = None
    if not args.no_audio:
        from rigplane.audio._usb_resolve import resolve_audio_for_serial_port

        mapping = resolve_audio_for_serial_port(args.device)
        if mapping is None:
            print("no USB audio device resolved for this serial port", file=sys.stderr)
            return 3
        index = mapping.rx_device_index
        factory = lambda wav: UsbAudioRecorder(index, wav)  # noqa: E731
    return asyncio.run(run_clip(args, audio_factory=factory))


if __name__ == "__main__":
    sys.exit(main())
