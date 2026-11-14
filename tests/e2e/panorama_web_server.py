"""Loopback simulator with writable CENTER geometry for the panorama journey."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rigplane.core.types import bcd_decode, bcd_encode  # noqa: E402
from rigplane.commands import parse_civ_frame  # noqa: E402
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
    def __init__(self, model: str, evidence: Path, ack_delay: float = 0.0) -> None:
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
        if self.profile.expected_identity_ids:
            self.model_id = bytes.fromhex(self.profile.expected_identity_ids[0])
        self.span_index = self.profile.scope_span_presets_hz.index(100_000)
        self.scope_mode = 0
        self.fixed = (14_200_000, 14_300_000)
        self.set_frequency(14_250_000)

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

    def pump_scope_stream(self) -> int:
        if self.evidence.with_suffix(".pause").exists():
            return 0
        span = self.profile.scope_span_presets_hz[self.span_index]
        first, last = self.fixed if self.scope_mode == 1 else (self._frequency, span)
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
    args = parser.parse_args()
    logging.basicConfig(filename=args.evidence.with_suffix(".log"), level=logging.INFO)
    args.evidence.write_text("")
    simulator = PanoramaSimulator(args.model, args.evidence, ack_delay=args.ack_delay)
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
