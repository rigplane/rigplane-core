"""Loopback simulator with writable CENTER geometry for the panorama journey."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rigplane.core.types import bcd_decode, bcd_encode  # noqa: E402
from rigplane.commands import parse_civ_frame  # noqa: E402
from rigplane.commands._codec import bcd_encode_value  # noqa: E402
from rigplane.commands.scope import parse_scope_fixed_edge_response  # noqa: E402
from rigplane.profiles import resolve_radio_profile  # noqa: E402
from rigplane.runtime.radio import IcomRadio  # noqa: E402
from rigplane.web.server import WebConfig, WebServer  # noqa: E402
from rigplane_testkit.icom.simulator import (  # noqa: E402
    _CIV_HEADER_SIZE,
    _CIV_PREAMBLE,
    _CIV_TERM,
    _CMD_FREQ_GET,
    _CMD_FREQ_SET,
    MockIcomRadio,
)


@dataclass(frozen=True)
class ScopeWriteReceipt:
    sub: int
    data: list[int]
    span: int

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class PanoramaSimulator(MockIcomRadio):
    def __init__(
        self,
        model: str,
        evidence: Path,
        ack_delay: float = 0.0,
        response_delay: float = 0.0,
        response_jitter: float = 0.0,
    ) -> None:
        self.profile = resolve_radio_profile(model=model)
        super().__init__(
            host="127.0.0.1",
            radio_addr=self.profile.civ_addr,
            scope_stream=True,
            dual_receiver=self.profile.receiver_count > 1,
        )
        self.evidence = evidence
        # MOR-4393 slow-link knob: seconds the FB/01 ACK of a frequency SET
        # lags behind the write itself. The write applies instantly, so the
        # next poll readback carries the new frequency while the client-side
        # command lifecycle is still pending — the owner's IC-705-over-Wi-Fi
        # readback-lag shape.
        self.ack_delay = ack_delay
        # Downlink-lag knob: seconds every simulator CI-V response (writes,
        # transceive echoes, poll readbacks) is held before dispatch, so the
        # client observes older own-target frequencies while newer tuning
        # intent already exists. Unlike ack_delay (one frame late), this lags
        # the whole downlink the frontend reads.
        self.response_delay = response_delay
        self._response_jitter = response_jitter
        if self.profile.expected_identity_ids:
            self.model_id = bytes.fromhex(self.profile.expected_identity_ids[0])
        self.span_index = self.profile.scope_span_presets_hz.index(100_000)
        self.scope_mode = 0
        self._active_slot = 0  # single-receiver A/B: 0 = A, 1 = B
        self.fixed = (14_200_000, 14_300_000)
        # SCROLL-C window, radio-owned: the radio scrolls its own edges
        # when the VFO leaves the window (CI-V 27 15 SPAN applies in
        # Center and SCROLL-C). None until the first SCROLL-C pump.
        self.scroll: tuple[int, int] | None = None
        # The span preset the scroll window was built at: an acknowledged
        # span change must resize the streamed window (#4565 pre-review
        # item 5) — None until the first SCROLL-C pump.
        self.scroll_span: int | None = None
        self.set_frequency(14_250_000)

    def _receive(self, data, addr, label, proto) -> None:
        # Per-datagram uniform jitter on top of the fixed downlink lag. The
        # base class reads self.response_delay synchronously at schedule
        # time, so a temporary adjustment is race-free on the event loop.
        if self._response_jitter <= 0:
            super()._receive(data, addr, label, proto)
            return
        original = self.response_delay
        try:
            self.response_delay = original + random.uniform(0.0, self._response_jitter)
            super()._receive(data, addr, label, proto)
        finally:
            self.response_delay = original

    def _handle_civ_data(self, data, addr, sender_id, proto):
        if self.ack_delay > 0:
            datalen = int.from_bytes(data[0x11:0x13], "little")
            end = _CIV_HEADER_SIZE + datalen
            civ = data[_CIV_HEADER_SIZE:end]
            if (
                end <= len(data)
                and len(civ) == 11
                and civ[:2] == _CIV_PREAMBLE
                and civ[2] == self._radio_addr
                and civ[4] == _CMD_FREQ_SET
                and civ[-1:] == _CIV_TERM
            ):
                # Same effect as the native 0x05 handler (apply, then ACK),
                # but the ACK leaves ack_delay seconds late — and a
                # transceive-style unsolicited freq report leaves NOW, so
                # the client observes the new frequency while the command
                # lifecycle is still pending (the owner's IC-705-over-Wi-Fi
                # readback-lag shape, MOR-4393).
                self._frequency = bcd_decode(civ[5:10])
                echo = self._civ_frame(
                    0x00,
                    self._radio_addr,
                    _CMD_FREQ_GET,
                    data=bcd_encode(self._frequency),
                )
                self._send(proto, self._wrap_civ(echo, sender_id), addr, "civ")
                ack = self._civ_ack(civ[3], self._radio_addr)
                asyncio.get_running_loop().call_later(
                    self.ack_delay,
                    self._send,
                    proto,
                    self._wrap_civ(ack, sender_id),
                    addr,
                    "civ",
                )
                return
        super()._handle_civ_data(data, addr, sender_id, proto)

    def _dispatch_scope(self, payload: bytes, from_addr: int) -> bytes | None:
        reply = self._dispatch_scope_reply(payload, from_addr)
        lengths = {
            0x10: 2,
            0x11: 2,
            0x12: 2,
            0x13: 2,
            0x14: 3,
            0x15: 7,
            0x16: 3,
            0x1E: 13,
        }
        if payload and len(payload) == lengths.get(payload[0]):
            with self.evidence.open("a") as stream:
                stream.write(
                    ScopeWriteReceipt(
                        payload[0],
                        list(payload[1:]),
                        self.profile.scope_span_presets_hz[self.span_index],
                    ).to_json()
                    + "\n"
                )
        return reply

    def _dispatch_scope_reply(self, payload: bytes, from_addr: int) -> bytes | None:
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
            return reply
        return super()._dispatch_scope(payload, from_addr)

    # Passband geometry the radio reports (#4565 journey): the filter width
    # and PBT reads, with the wire bytes taken from the profile's command
    # map. Without them the panel never gets a passband tuple, so its
    # frequency is undefined and radio-owned windows are never exercised
    # against a VFO-centred branch. Values: a mid filter index, PBT centred.
    _PASSBAND_READS = (
        ("get_filter_width", 10),
        ("get_pbt_inner", 128),
        ("get_pbt_outer", 128),
    )

    def _passband_read_reply(
        self, wire: tuple[int, ...], from_addr: int, receiver: int | None
    ) -> bytes | None:
        """The reply to a passband read, wrapped in Command29 when it was."""
        command_map = self.profile.command_map
        if command_map is None:
            return None
        for name, value in self._PASSBAND_READS:
            if command_map.has(name) and command_map.get(name) == wire:
                if name == "get_filter_width" and (
                    self.profile.filter_width_encoding == "raw_byte_index"
                ):
                    data = bytes([value])
                else:
                    data = bcd_encode_value(value, byte_count=2)
                if receiver is None:
                    return self._civ_frame(
                        from_addr,
                        self._radio_addr,
                        wire[0],
                        data=bytes(wire[1:]) + data,
                    )
                return self._civ_frame(
                    from_addr,
                    self._radio_addr,
                    0x29,
                    data=bytes([receiver, *wire]) + data,
                )
        return None

    def _dispatch_civ_native(
        self, cmd: int, payload: bytes, from_addr: int
    ) -> bytes | None:
        if cmd == 0x29 and len(payload) >= 2:
            reply = self._passband_read_reply(
                (payload[1], *payload[2:]), from_addr, payload[0]
            )
        else:
            reply = self._passband_read_reply((cmd, *payload), from_addr, None)
        if reply is not None:
            return reply
        if cmd == 0x07 and payload in (b"\x00", b"\x01") and not self._dual_receiver:
            # A/B slot select on a single-receiver radio: the connection
            # policy selects VFO A and the passband identity needs the
            # confirmed slot. Exchanging slots swaps the selected and
            # unselected sets, exactly as the dual-receiver select does.
            target = payload[0]
            if target != self._active_slot:
                self._frequency, self._unselected_frequency = (
                    self._unselected_frequency,
                    self._frequency,
                )
                self._mode, self._unselected_mode = self._unselected_mode, self._mode
                self._data_mode, self._unselected_data_mode = (
                    self._unselected_data_mode,
                    self._data_mode,
                )
                self._filter, self._unselected_filter = (
                    self._unselected_filter,
                    self._filter,
                )
                self._active_slot = target
            return self._civ_ack(from_addr, self._radio_addr)
        return super()._dispatch_civ_native(cmd, payload, from_addr)

    def pump_scope_stream(self) -> int:
        if self.evidence.with_suffix(".pause").exists():
            return 0
        span = self.profile.scope_span_presets_hz[self.span_index]
        if self.scope_mode in (1, 3):
            # Edge-number modes (FIX, SCROLL-F): the window is the selected
            # EDGE pair; the radio never moves it on its own.
            first, last = self.fixed
        elif self.scope_mode == 2:
            # SCROLL-C: the radio owns the window and scrolls it to keep
            # the VFO inside, at the SPAN width. Recentering on exit is
            # the test-infra model of that scroll, and an acknowledged
            # span change rebuilds the window at the new width so the
            # following waveform edges carry the change (#4565 pre-review
            # item 5).
            if (
                self.scroll is None
                or self.scroll_span != span
                or not self.scroll[0] <= self._frequency < self.scroll[1]
            ):
                self.scroll = (self._frequency - span, self._frequency + span)
                self.scroll_span = span
            first, last = self.scroll
        else:
            first, last = self._frequency, span
        for receiver, templates in self._scope_templates.items():
            shaped = []
            for template in templates:
                pixels = bytes(15 + index % 30 for index in range(len(template) - 22))
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
        "--ack-delay",
        type=float,
        default=0.0,
        help="seconds the ACK of a frequency SET lags the write itself "
        "(PanoramaSimulator.ack_delay) — a readback-lag knob: the frequency "
        "applies instantly and polls carry it while the command lifecycle "
        "is still pending (MOR-4393)",
    )
    parser.add_argument(
        "--response-delay",
        type=float,
        default=0.0,
        help="seconds every simulator CI-V response is held before dispatch "
        "(PanoramaSimulator.response_delay) — a downlink-lag knob: writes, "
        "transceive echoes and poll readbacks all arrive late, so the "
        "client observes older own-target frequencies while newer tuning "
        "intent exists (MOR-4393)",
    )
    parser.add_argument(
        "--response-jitter",
        type=float,
        default=0.0,
        help="per-datagram uniform [0, jitter] seconds added to "
        "--response-delay (PanoramaSimulator jitter) — nondeterministic; "
        "the slow-link row keeps it at zero",
    )
    args = parser.parse_args()
    logging.basicConfig(filename=args.evidence.with_suffix(".log"), level=logging.INFO)
    args.evidence.write_text("")
    # Lag knobs stay off through the handshake: the client connect phase has
    # sub-second deadlines a delayed simulator cannot meet. They shape the
    # test phase only, activated once the radio link is up.
    simulator = PanoramaSimulator(args.model, args.evidence)
    await simulator.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=simulator.control_port,
        username="testuser",
        password="testpass",
        model=args.model,
    )
    try:
        await radio.connect()
        await radio.get_freq()
        simulator.ack_delay = args.ack_delay
        simulator.response_delay = args.response_delay
        simulator._response_jitter = args.response_jitter
        server = WebServer(
            radio,
            WebConfig(
                host="127.0.0.1",
                port=args.port,
                static_dir=Path("frontend/dist"),
                radio_model=args.model,
                discovery=False,
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
