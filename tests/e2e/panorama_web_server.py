"""Loopback simulator with writable CENTER geometry for the panorama journey.

Also the entry point of the MOR-4388 acceptance matrix: the extra optional
flags (``--scope-source``, ``--static-dir``, ``--enable-audio``,
``--signal-amplitude``, ``--iq``, ``--slow-civ-ms``, ``--refuse-once``) only
add knobs the matrix cells need; the journey spec's invocation is unchanged.

MOR-4388 NAK evidence: ``_civ_nak`` records every NAK with the refused CI-V
frame when the refused packet arrived on the CI-V port (a real refused
command); NAKs answering handshake/control packets keep an empty frame and
the matrix tolerates them as choreography. ``--slow-civ-ms`` defers whole
CI-V command processing past the ack-tracker grace (a slow radio link), and
``--refuse-once CMD[:SUB]`` answers the next matching command with one FA
(a refused background read like the IC-705 tuner-status read 1C 01). The
``<evidence>.extchange.json`` trigger file applies external radio changes
(VFO, span, scope mode, fixed edges) directly to simulator state — never
via CI-V, never with scope-write receipts — modelling a front-panel or
second-controller change for the external-change matrix row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rigplane.core.types import bcd_decode, bcd_encode  # noqa: E402
from rigplane.commands import parse_civ_frame  # noqa: E402
from rigplane.commands.scope import parse_scope_fixed_edge_response  # noqa: E402
from rigplane.profiles import resolve_radio_profile  # noqa: E402
from rigplane.runtime.radio import IcomRadio  # noqa: E402
from rigplane.sdr.fake import FakeIqSource  # noqa: E402
from rigplane.sdr.types import SdrConfig  # noqa: E402
from rigplane.web.server import WebConfig, WebServer  # noqa: E402
from rigplane_testkit.icom.simulator import MockIcomRadio  # noqa: E402
from rigplane_testkit.icom.simulator import _CIV_HEADER_SIZE  # noqa: E402


@dataclass(frozen=True)
class ScopeWriteReceipt:
    sub: int
    data: list[int]
    span: int

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass(frozen=True)
class NakReceipt:
    """Every CI-V NAK the simulator sent, with the refused frame.

    ``civ_hex`` is the CI-V frame bytes when the refused packet carried one
    (preamble 0xFE 0xFE); an empty string means the NAK answered a
    non-CI-V/handshake packet, which the connection choreography tolerates.
    """

    ts: float
    to_addr: int
    frm_addr: int
    civ_hex: str

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class PanoramaSimulator(MockIcomRadio):
    def __init__(
        self,
        model: str,
        evidence: Path,
        *,
        enable_audio: bool = False,
        signal_amplitude: int = 0,
        slow_civ_ms: int = 0,
        refuse_once: tuple[int, int | None] | None = None,
    ) -> None:
        self.profile = resolve_radio_profile(model=model)
        super().__init__(
            host="127.0.0.1",
            radio_addr=self.profile.civ_addr,
            scope_stream=True,
            dual_receiver=self.profile.receiver_count > 1,
            enable_audio=enable_audio,
        )
        self.evidence = evidence
        self.nak_evidence = evidence.with_suffix(".nak.jsonl")
        self.nak_evidence.write_text("")
        if self.profile.expected_identity_ids:
            self.model_id = bytes.fromhex(self.profile.expected_identity_ids[0])
        self.span_index = self.profile.scope_span_presets_hz.index(100_000)
        self.scope_mode = 0
        self.scope_controls: dict[int, int] = {}
        self.fixed = (14_200_000, 14_300_000)
        self.signal_amplitude = signal_amplitude
        self.slow_civ_ms = slow_civ_ms
        self.refuse_once = refuse_once
        self._last_nak_payload = b""
        self._last_nak_was_civ = False
        self.set_frequency(14_250_000)

    def _civ_nak(self, to: int, frm: int) -> bytes:
        # Only a packet that arrived on the CI-V port can carry a refused
        # CI-V command. The CI-V frame starts past the LAN header (the
        # testkit's own _CIV_HEADER_SIZE, 0x15 bytes) — searching the whole
        # packet for FE FE matched header bytes and invented refusals like
        # rc8's "cmd 0xe0" (D7), so the frame is sliced at the header and
        # must validate as FE FE … FD. Handshake/control NAKs keep an empty
        # frame and stay tolerated by invariant 5.
        frame = b""
        if self._last_nak_was_civ and len(self._last_nak_payload) > _CIV_HEADER_SIZE:
            candidate = bytes(self._last_nak_payload[_CIV_HEADER_SIZE:])
            if (
                len(candidate) >= 6
                and candidate.startswith(b"\xfe\xfe")
                and candidate.endswith(b"\xfd")
            ):
                frame = candidate
        with self.nak_evidence.open("a") as stream:
            stream.write(NakReceipt(time.time(), to, frm, frame.hex()).to_json() + "\n")
        return super()._civ_nak(to, frm)

    def _dispatch(
        self,
        data: bytes,
        addr: tuple[str, int],
        ptype: int,
        sender_id: int,
        seq: int,
        label: str,
        proto: object,
    ) -> None:
        # Remember the packet being dispatched so a NAK it triggers carries
        # the refused payload (MOR-4388 invariant 5 evidence).
        self._last_nak_payload = data if label == "civ" else b""
        self._last_nak_was_civ = label == "civ"
        super()._dispatch(data, addr, ptype, sender_id, seq, label, proto)

    def _handle_civ_data(
        self,
        data: bytes,
        addr: tuple[str, int],
        sender_id: int,
        proto: object,
    ) -> None:
        # MOR-4388 slow-link mode: defer whole CI-V command processing past
        # the ack-tracker grace (default 120 ms in Core), so every FB/FA
        # answer lands late — the base _handle_civ already routed by packet
        # type, so anything reaching here is a real CI-V command packet and
        # the deferred wrap+send keeps LAN sequence order intact.
        if self.slow_civ_ms > 0:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                # Bound here: zero-arg super() cannot run inside the nested
                # _fire closure (it has no instance argument).
                base_handle = super()._handle_civ_data

                def _fire() -> None:
                    # Re-stamp the refused payload at fire time (D6): _dispatch
                    # stored the most recently arrived packet, but on a slow
                    # link many packets land during the deferral — the NAK
                    # must cite the frame being answered, not a later arrival.
                    # Synchronous dispatch makes the stamp exact.
                    self._last_nak_payload = data
                    self._last_nak_was_civ = True
                    base_handle(data, addr, sender_id, proto)

                loop.call_later(self.slow_civ_ms / 1000.0, _fire)
                return
        super()._handle_civ_data(data, addr, sender_id, proto)

    def _dispatch_civ(self, cmd: int, payload: bytes, from_addr: int) -> bytes | None:
        # MOR-4388 one-shot refusal: the next matching background read gets
        # one FA (a refused background read like the IC-705 tuner-status
        # read 1C 01). The receipt is written by _civ_nak as usual.
        armed = self.refuse_once
        if (
            armed is not None
            and cmd == armed[0]
            and (armed[1] is None or payload[:1] == bytes([armed[1]]))
        ):
            self.refuse_once = None
            return self._civ_nak(from_addr, self._radio_addr)
        return super()._dispatch_civ(cmd, payload, from_addr)

    def _dispatch_scope(self, payload: bytes, from_addr: int) -> bytes | None:
        if payload and payload[0] == 0x1E and len(payload) == 3:
            return self._civ_frame(
                from_addr,
                self._radio_addr,
                0x27,
                data=payload + bcd_encode(self.fixed[0]) + bcd_encode(self.fixed[1]),
            )
        if payload and payload[0] == 0x1E and len(payload) == 13:
            frame = parse_civ_frame(
                self._civ_frame(from_addr, self._radio_addr, 0x27, data=payload)
            )
            edges = parse_scope_fixed_edge_response(frame)
            self.fixed = (edges.start_hz, edges.end_hz)
            return self._civ_ack(from_addr, self._radio_addr)
        if payload and payload[0] == 0x16:
            if len(payload) == 2:
                return self._civ_frame(
                    from_addr, self._radio_addr, 0x27, data=payload + b"\x01"
                )
            if len(payload) == 3:
                return self._civ_ack(from_addr, self._radio_addr)
        # The one-byte scope controls the profiles declare (ic705.toml
        # [scope_controls]: hold 0x17, ref 0x19, speed 0x1A, center type
        # 0x1C, vbw 0x1D). A real radio answers these GETs and ACKs the
        # SETs; the base mock only models the recorded 7610 forms and NAKs
        # the rest, which Core's control reads would misread as "radio
        # refuses" — a simulator gap, not radio behaviour (MOR-4388).
        if payload and payload[0] in (0x17, 0x19, 0x1A, 0x1C, 0x1D):
            sub, rest = payload[0], payload[1:]
            value = bytes([self.scope_controls.get(sub, 0)])
            if len(rest) == 1:
                return self._civ_frame(
                    from_addr,
                    self._radio_addr,
                    0x27,
                    data=bytes([sub, rest[0]]) + value,
                )
            if len(rest) == 2:
                self.scope_controls[sub] = rest[1]
                reply = self._civ_ack(from_addr, self._radio_addr)
                with self.evidence.open("a") as stream:
                    stream.write(
                        ScopeWriteReceipt(
                            sub,
                            list(rest),
                            self.profile.scope_span_presets_hz[self.span_index],
                        ).to_json()
                        + "\n"
                    )
                return reply
            return self._civ_nak(from_addr, self._radio_addr)
        if payload and payload[0] in (0x12, 0x14, 0x15):
            sub, rest = payload[0], payload[1:]
            if sub in (0x14, 0x15):
                value = (
                    bytes([self.scope_mode])
                    if sub == 0x14
                    else bcd_encode(self.profile.scope_span_presets_hz[self.span_index])
                )
                if len(rest) == 1:
                    return self._civ_frame(
                        from_addr,
                        self._radio_addr,
                        0x27,
                        data=bytes([sub, rest[0]]) + value,
                    )
                if len(rest) == (2 if sub == 0x14 else 6):
                    if sub == 0x14:
                        self.scope_mode = rest[1]
                    else:
                        self.span_index = self.profile.scope_span_presets_hz.index(
                            bcd_decode(rest[1:])
                        )
                    reply = self._civ_ack(from_addr, self._radio_addr)
                else:
                    return self._civ_nak(from_addr, self._radio_addr)
            else:
                reply = super()._dispatch_scope(payload, from_addr)
            if rest:
                with self.evidence.open("a") as stream:
                    stream.write(
                        ScopeWriteReceipt(
                            sub,
                            list(rest),
                            self.profile.scope_span_presets_hz[self.span_index],
                        ).to_json()
                        + "\n"
                    )
            return reply
        return super()._dispatch_scope(payload, from_addr)

    def _signal_pixels(self, pixels: bytes) -> bytes:
        """Overlay one gaussian bump of a known bin position (MOR-4388).

        The bump's bin index inside the frame's own pixel row is stable per
        scope mode, so a browser-side test can locate it in the received
        bins and check where the renderers draw it. The base pattern (a
        sawtooth 15..44) stays the floor on both sides of the bump.
        """
        if self.signal_amplitude <= 0:
            return pixels
        count = len(pixels)
        center = int(count * (0.625 if self.scope_mode == 0 else 0.40))
        sigma = max(2.0, count / 200.0)
        shaped = bytearray(pixels)
        for index in range(
            max(0, center - int(5 * sigma)), min(count, center + int(5 * sigma) + 1)
        ):
            offset = (index - center) / sigma
            shaped[index] = min(
                255,
                max(
                    shaped[index],
                    int(
                        self.signal_amplitude * pow(2.718281828, -offset * offset / 2.0)
                    ),
                ),
            )
        return bytes(shaped)

    def _apply_external_change(self, change: dict[str, object]) -> None:
        # An external change (front panel, second controller over its own
        # CI-V) bypasses Core entirely: direct simulator state, no CI-V
        # traffic, no scope-write receipts. Unknown keys and out-of-preset
        # spans are ignored with a log line, never applied half-way.
        applied: list[str] = []
        freq = change.get("freq_hz")
        if isinstance(freq, int) and freq > 0:
            self.set_frequency(freq)
            applied.append(f"freq={freq}")
        span = change.get("span_hz")
        if isinstance(span, int) and span in self.profile.scope_span_presets_hz:
            self.span_index = self.profile.scope_span_presets_hz.index(span)
            applied.append(f"span={span}")
        mode = change.get("scope_mode")
        if mode in (0, 1):
            self.scope_mode = mode
            applied.append(f"mode={mode}")
        fixed = change.get("fixed")
        if (
            isinstance(fixed, list)
            and len(fixed) == 2
            and all(isinstance(edge, int) for edge in fixed)
        ):
            self.fixed = (fixed[0], fixed[1])
            applied.append(f"fixed={fixed[0]}..{fixed[1]}")
        logging.info("panorama external change applied: %s", ",".join(applied) or "none")

    def pump_scope_stream(self) -> int:
        trigger = self.evidence.with_suffix(".extchange.json")
        if trigger.exists():
            try:
                change = json.loads(trigger.read_text())
            except (OSError, ValueError):
                change = {}
            finally:
                trigger.unlink(missing_ok=True)
            if isinstance(change, dict):
                self._apply_external_change(change)
        if self.evidence.with_suffix(".pause").exists():
            return 0
        span = self.profile.scope_span_presets_hz[self.span_index]
        first, last = self.fixed if self.scope_mode == 1 else (self._frequency, span)
        for receiver, templates in self._scope_templates.items():
            shaped = []
            for template in templates:
                pixels = self._signal_pixels(
                    bytes(15 + index % 30 for index in range(len(template) - 22))
                )
                shaped.append(
                    template[:9]
                    + bytes([self.scope_mode])
                    + bcd_encode(first)
                    + bcd_encode(last)
                    + b"\x00"
                    + pixels
                    + template[-1:]
                )
            self._scope_templates[receiver] = shaped
        return super().pump_scope_stream()


async def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--freq", type=int, default=14_250_000)
    parser.add_argument("--model", default="IC-705")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--scope-source",
        default="auto",
        choices=["auto", "hardware", "audio_fft", "sdr"],
    )
    parser.add_argument("--static-dir", type=Path, default=Path("frontend/dist"))
    parser.add_argument("--enable-audio", action="store_true")
    parser.add_argument("--signal-amplitude", type=int, default=0)
    parser.add_argument(
        "--iq",
        action="store_true",
        help="register the deterministic FakeIqSource (MOR-4388 IQ cells)",
    )
    parser.add_argument(
        "--slow-civ-ms",
        type=int,
        default=0,
        help="defer CI-V command processing by this many ms (MOR-4388 "
        "slow-link mode: every FB/FA answer lands past the ack-tracker grace)",
    )
    parser.add_argument(
        "--refuse-once",
        default=None,
        help="one-shot FA for the next matching CI-V command, CMD[:SUB] hex "
        "(MOR-4388 refused background read, e.g. 1C:01 tuner status)",
    )
    args = parser.parse_args()
    logging.basicConfig(filename=args.evidence.with_suffix(".log"), level=logging.INFO)
    args.evidence.write_text("")
    refuse_once: tuple[int, int | None] | None = None
    if args.refuse_once is not None:
        parts = args.refuse_once.split(":")
        refuse_once = (
            int(parts[0], 16),
            int(parts[1], 16) if len(parts) > 1 else None,
        )
    simulator = PanoramaSimulator(
        args.model,
        args.evidence,
        enable_audio=args.enable_audio,
        signal_amplitude=args.signal_amplitude,
        slow_civ_ms=args.slow_civ_ms,
        refuse_once=refuse_once,
    )
    await simulator.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=simulator.control_port,
        username="testuser",
        password="testpass",
        model=args.model,
    )
    sdr_config = None
    sdr_source_factory = None
    if args.iq:
        sdr_config = SdrConfig(device_args="fake", sample_rate_hz=960_000)
        sdr_source_factory = lambda _config: FakeIqSource(  # noqa: E731
            center_freq_hz=14_250_000,
            sample_rate_hz=960_000,
            tones=((96_000, -20.0),),
            noise_floor_dbfs=-75.0,
            background_interval_s=0.05,
        )
    try:
        await radio.connect()
        await radio.get_freq()
        server = WebServer(
            radio,
            WebConfig(
                host="127.0.0.1",
                port=args.port,
                static_dir=args.static_dir,
                radio_model=args.model,
                discovery=False,
                scope_source=args.scope_source,
                sdr_config=sdr_config,
                sdr_source_factory=sdr_source_factory,
            ),
        )
        await server.serve_forever()
    finally:
        try:
            await radio.disconnect()
        finally:
            await simulator.stop()


if __name__ == "__main__":
    asyncio.run(run())
