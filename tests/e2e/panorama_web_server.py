"""Loopback simulator with writable CENTER geometry for the panorama journey."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rigplane.core.types import bcd_decode, bcd_encode  # noqa: E402
from rigplane.commands import parse_civ_frame  # noqa: E402
from rigplane.commands.scope import parse_scope_fixed_edge_response  # noqa: E402
from rigplane.profiles import resolve_radio_profile  # noqa: E402
from rigplane.runtime.radio import IcomRadio  # noqa: E402
from rigplane.web.server import WebConfig, WebServer  # noqa: E402
from rigplane_testkit.icom.simulator import MockIcomRadio  # noqa: E402


class PanoramaSimulator(MockIcomRadio):
    def __init__(self, model: str, evidence: Path) -> None:
        self.profile = resolve_radio_profile(model=model)
        super().__init__(
            host="127.0.0.1",
            radio_addr=self.profile.civ_addr,
            scope_stream=True,
            dual_receiver=self.profile.receiver_count > 1,
        )
        self.evidence = evidence
        if self.profile.expected_identity_ids:
            self.model_id = bytes.fromhex(self.profile.expected_identity_ids[0])
        self.span_index = self.profile.scope_span_presets_hz.index(100_000)
        self.scope_mode = 0
        self.fixed = (14_200_000, 14_300_000)
        self.set_frequency(14_250_000)

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
                        json.dumps(
                            {
                                "sub": sub,
                                "data": list(rest),
                                "span": self.profile.scope_span_presets_hz[
                                    self.span_index
                                ],
                            }
                        )
                        + "\n"
                    )
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
    args = parser.parse_args()
    logging.basicConfig(filename=args.evidence.with_suffix(".log"), level=logging.INFO)
    args.evidence.write_text("")
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
