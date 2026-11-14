"""Loopback simulator with writable CENTER geometry for the panorama journey."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rigplane.core.types import bcd_decode, bcd_encode  # noqa: E402
from rigplane.commands._codec import bcd_encode_value  # noqa: E402


def _bcd_level_decode(data: bytes) -> int:
    value = 0
    for byte in data:
        value = value * 100 + ((byte >> 4) & 0x0F) * 10 + (byte & 0x0F)
    return value


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
    _MockProtocol,
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
        span_hz: int = 100_000,
        seed: int = 0xC0FFEE,
    ) -> None:
        self.profile = resolve_radio_profile(model=model)
        super().__init__(
            host="127.0.0.1",
            radio_addr=self.profile.civ_addr,
            scope_stream=True,
            dual_receiver=self.profile.receiver_count > 1,
            vfo_ab=self.profile.vfo_scheme == "ab",
        )
        self.evidence = evidence
        # MOR-4393 slow-link knob: seconds the FB/01 ACK of a frequency SET
        # lags behind the write itself. The write applies instantly, so the
        # next poll readback carries the new frequency while the client-side
        # command lifecycle is still pending — the owner's IC-705-over-Wi-Fi
        # readback-lag shape.
        self.ack_delay = ack_delay
        # MOR held-key repro: realistic CI-V readback latency. EVERY reply or
        # unsolicited frame on the CI-V port leaves
        # ``response_delay + uniform(0, response_jitter)`` seconds late, ORDER
        # PRESERVED via a per-connection send queue — a real link delays but
        # does not reorder. Seeded (``seed``) so a latency case replays
        # exactly. The base simulator's own knobs do not fit: ``FaultPlan``
        # delay/jitter schedules each packet on its own ``call_later`` (so
        # jitter can reorder) and ``response_delay`` delays inbound dispatch,
        # not outbound replies. Default 0 keeps today's behaviour byte for
        # byte (the override below passes through untouched).
        self.response_delay = response_delay
        self.response_jitter = response_jitter
        self._response_rng = random.Random(seed)
        self._civ_queue: deque[tuple[float, _MockProtocol, bytes, tuple[str, int]]] = (
            deque()
        )
        self._civ_next_due = 0.0
        if self.profile.expected_identity_ids:
            self.model_id = bytes.fromhex(self.profile.expected_identity_ids[0])
        self.span_index = self.profile.scope_span_presets_hz.index(span_hz)
        self.scope_mode = 0
        self.fixed = (14_200_000, 14_300_000)
        self.set_frequency(14_250_000)
        # CI-V 1A 03 (DSP filter width): the shared simulator does not model
        # it, so the panorama harness never observed main.filterWidth and no
        # passband overlay could render. The byte is the profile-encoded
        # index; 0x28 decodes to 2400 Hz SSB on the Icom tables.
        self._filter_width_index = 0x28
        # CI-V 14 07/08 (PBT inner/outer, BCD level 0-255): the passband
        # tuple's shift leg. Both pots parked at center (128 = no shift).
        self._pbt = {0x07: 128, 0x08: 128}

    def _send(
        self,
        proto: _MockProtocol,
        pkt: bytes,
        addr: tuple[str, int],
        label: str,
    ) -> None:
        if label != "civ" or (self.response_delay <= 0 and self.response_jitter <= 0):
            super()._send(proto, pkt, addr, label)
            return
        delay = self.response_delay + self._response_rng.uniform(
            0, max(0.0, self.response_jitter)
        )
        loop = asyncio.get_running_loop()
        now = loop.time()
        # Monotonic due times keep emission order identical to send order no
        # matter how the jitter draws fall.
        due = max(now + delay, self._civ_next_due)
        self._civ_next_due = due
        self._civ_queue.append((due, proto, pkt, addr))
        loop.call_later(max(0.0, due - now), self._pump_civ_queue)

    def _pump_civ_queue(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        while self._civ_queue and self._civ_queue[0][0] <= now:
            _, proto, pkt, addr = self._civ_queue.popleft()
            super()._send(proto, pkt, addr, "civ")
        if self._civ_queue:
            head_due = self._civ_queue[0][0]
            loop.call_later(max(0.0, head_due - now), self._pump_civ_queue)

    def _dispatch_filter_width(
        self, rest: bytes, from_addr: int, reply
    ) -> bytes | None:
        if len(rest) == 0:  # GET: the stored profile-encoded index byte
            return reply(bytes([self._filter_width_index]))
        if len(rest) == 1:  # SET: store and ACK
            self._filter_width_index = rest[0]
            return self._civ_ack(from_addr, self._radio_addr)
        return self._civ_nak(from_addr, self._radio_addr)

    def _dispatch_pbt(
        self, sub: int, rest: bytes, from_addr: int, reply
    ) -> bytes | None:
        if len(rest) == 0:  # GET: sub echo + BCD level (two bytes)
            return reply(sub, bcd_encode_value(self._pbt[sub], byte_count=2))
        if len(rest) != 2:
            return self._civ_nak(from_addr, self._radio_addr)
        self._pbt[sub] = _bcd_level_decode(rest)
        return self._civ_ack(from_addr, self._radio_addr)

    def _dispatch_civ_native(
        self, cmd: int, payload: bytes, from_addr: int
    ) -> bytes | None:
        if cmd == 0x1A and payload[:1] == b"\x03":
            return self._dispatch_filter_width(
                payload[1:],
                from_addr,
                lambda data: self._civ_frame(
                    from_addr, self._radio_addr, 0x1A, sub=0x03, data=data
                ),
            )
        if cmd == 0x14 and payload and payload[0] in self._pbt:
            return self._dispatch_pbt(
                payload[0],
                payload[1:],
                from_addr,
                lambda sub, data: self._civ_frame(
                    from_addr, self._radio_addr, 0x14, sub=sub, data=data
                ),
            )
        return super()._dispatch_civ_native(cmd, payload, from_addr)

    def _dispatch_cmd29(
        self, real_cmd: int, inner: bytes, from_addr: int, receiver: int
    ) -> bytes | None:
        if real_cmd == 0x1A and inner[:1] == b"\x03":
            return self._dispatch_filter_width(
                inner[1:],
                from_addr,
                lambda data: self._civ_frame(
                    from_addr,
                    self._radio_addr,
                    0x29,
                    data=bytes([receiver, 0x1A, 0x03]) + data,
                ),
            )
        if real_cmd == 0x14 and inner and inner[0] in self._pbt:
            return self._dispatch_pbt(
                inner[0],
                inner[1:],
                from_addr,
                lambda sub, data: self._civ_frame(
                    from_addr,
                    self._radio_addr,
                    0x29,
                    data=bytes([receiver, 0x14, sub]) + data,
                ),
            )
        return super()._dispatch_cmd29(real_cmd, inner, from_addr, receiver)

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
    parser.add_argument(
        "--response-delay",
        type=float,
        default=0.0,
        help="seconds EVERY CI-V reply/unsolicited frame on the CI-V port "
        "leaves late (PanoramaSimulator.response_delay) — a readback-latency "
        "knob: order is preserved via a per-connection send queue, so this "
        "models a slow link rather than a reordering one",
    )
    parser.add_argument(
        "--response-jitter",
        type=float,
        default=0.0,
        help="uniform random extra delay in [0, response_jitter] seconds "
        "added to every CI-V reply/unsolicited frame on top of "
        "--response-delay (seeded by --seed; order still preserved)",
    )
    parser.add_argument(
        "--span-hz",
        type=int,
        default=100_000,
        help="radio scope span preset the simulator starts on "
        "(PanoramaSimulator.span_index) — must be one of the profile's "
        "scope_span_presets_hz",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0xC0FFEE,
        help="seed for the CI-V response jitter RNG so a latency case replays exactly",
    )
    args = parser.parse_args()
    logging.basicConfig(filename=args.evidence.with_suffix(".log"), level=logging.INFO)
    args.evidence.write_text("")
    simulator = PanoramaSimulator(
        args.model,
        args.evidence,
        ack_delay=args.ack_delay,
        response_delay=args.response_delay,
        response_jitter=args.response_jitter,
        span_hz=args.span_hz,
        seed=args.seed,
    )
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
