"""Loopback simulator with writable CENTER geometry for the panorama journey.

Latency knobs (MOR-4451): a real LAN/Wi-Fi link delays both directions, so
the harness models each leg with its own one-way knob. ``--response-delay``
/ ``--response-jitter`` delay the DOWNLINK only: every CI-V reply or
unsolicited frame on the CI-V port leaves late through the shared outbound
CI-V FaultPlan (``civ_to_client``), order preserved. ``--uplink-delay``
delays the UPLINK only: every inbound request waits in the simulator's
inherited ``response_delay`` dispatch delay (applied in ``_receive``) before
it is parsed. Round-trip time is the sum of the two legs, each applied
exactly once — never doubled.

Adoption-order knob (MOR-4451): ``--external-span-skew-s`` staggers the two
observable legs of the front-panel ``--external-span-after-s`` change — the
streamed wave and the 0x15 span readback — so the client sees one order
deterministically (positive skew: frames first; negative: readback first).
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
from rigplane.commands._codec import bcd_encode_value, filter_index_to_hz  # noqa: E402
from rigplane.core.types import Mode  # noqa: E402


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
from rigplane_testkit.icom.faults import FaultPlan, FaultRule  # noqa: E402
from rigplane_testkit.icom.simulator import (  # noqa: E402
    _CIV_HEADER_SIZE,
    _CIV_PREAMBLE,
    _CIV_TERM,
    _CMD_FREQ_GET,
    _CMD_FREQ_SET,
    MockIcomRadio,
)


# CI-V 27 1C scope center type (MOR-4451): Filter Center (0) streams the
# CENTER wave centred on the passband centre, not the carrier. Only SSB has
# a carrier-offset passband — LSB below, USB above the carrier (CI-V mode
# bytes match rigplane.core.types.Mode: LSB 0x00, USB 0x01).
_SCOPE_CENTER_PASSBAND_SIGN = {Mode.LSB: -1, Mode.USB: +1}


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
        response_delay: float = 0.0,
        response_jitter: float = 0.0,
        uplink_delay: float = 0.0,
        span_hz: int = 100_000,
        seed: int = 0xC0FFEE,
        scope_center_type: int = 1,
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
        self.set_link_lag(response_delay, response_jitter, uplink_delay, seed)
        # MOR-4393 slow-link knob: seconds the FB/01 ACK of a frequency SET
        # lags behind the write itself. The write applies instantly, so the
        # next poll readback carries the new frequency while the client-side
        # command lifecycle is still pending — the owner's IC-705-over-Wi-Fi
        # readback-lag shape.
        self.ack_delay = 0.0
        if self.profile.expected_identity_ids:
            self.model_id = bytes.fromhex(self.profile.expected_identity_ids[0])
        # CI-V 27 1C scope center type (MOR-4451): Carrier Point Center (1,
        # the default) streams centred on the VFO, so the default keeps every
        # existing row's wave geometry byte-identical. Filter Center (0)
        # offsets the CENTER wave to the passband centre (see
        # _scope_wave_center_hz).
        if scope_center_type not in (0, 1):
            raise ValueError(
                f"scope_center_type must be 0 (Filter Center) or 1 (Carrier Point Center), got {scope_center_type}"
            )
        self._scope_center_type = scope_center_type
        self.span_index = self.profile.scope_span_presets_hz.index(span_hz)
        # MOR-4451 adoption-order knob: the lagging leg of a front-panel
        # external span change keeps reporting the old preset until its
        # switch time — (leg, old preset, switch at monotonic s). The
        # leading leg switches span_index at once; both legs stay views
        # of the same scheduled change, never a second mechanism.
        self._external_hold: tuple[str, int, float] | None = None
        self.scope_mode = 0
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
        # CI-V 1A 03 (DSP filter width): the shared simulator does not model
        # it, so the panorama harness never observed main.filterWidth and no
        # passband overlay could render. The byte is the profile-encoded
        # index; 0x28 decodes to 2400 Hz SSB on the Icom tables.
        self._filter_width_index = 0x28
        # CI-V 14 07/08 (PBT inner/outer, BCD level 0-255): the passband
        # tuple's shift leg. Both pots parked at center (128 = no shift).
        self._pbt = {0x07: 128, 0x08: 128}

    def set_link_lag(
        self,
        response_delay: float,
        response_jitter: float,
        uplink_delay: float,
        seed: int,
    ) -> None:
        # MOR-4451 held-key acceptance: realistic CI-V readback latency.
        # EVERY reply or unsolicited frame on the CI-V port leaves
        # ``response_delay + uniform(0, response_jitter)`` seconds late, ORDER
        # PRESERVED (``preserve_order``) — a real link delays but does not
        # reorder. Seeded (``seed``) so a latency case replays exactly.
        # This is the shared outbound CI-V FaultPlan (the same mechanism
        # ``simulator.py``'s funnel already serves), never a parallel queue:
        # the inherited ``response_delay`` carries only the uplink knob
        # (``uplink_delay`` below), never a second copy of this downlink delay.
        self.fault_plan = FaultPlan(
            civ_to_client=FaultRule(
                delay_s=response_delay,
                jitter_s=response_jitter,
                preserve_order=True,
            ),
            seed=seed,
        )
        # MOR-4451 one-way UPLINK latency: the inherited ``response_delay``
        # dispatch delay (applied in ``_receive``) holds every inbound
        # datagram before parsing — the request leg. That mechanism has no
        # per-port scoping (one float for every label), so this also holds
        # control-port session traffic, not just CI-V requests; the panorama
        # path only depends on the CI-V request leg. Kept as is — no second
        # mechanism beside it.
        self.response_delay = uplink_delay

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

    def _external_leg_span_hz(self, leg: str) -> int:
        """Preset the given leg (``readback`` or ``wave``) reports right now.

        While the external change's lagging leg has not reached its switch
        time it keeps the old preset; every other case (no change, leading
        leg, expired hold) reports ``span_index`` — the single source of
        truth the client span writes also update.
        """
        hold = self._external_hold
        if hold is not None and hold[0] == leg and time.monotonic() < hold[2]:
            return hold[1]
        return self.profile.scope_span_presets_hz[self.span_index]

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
                    else bcd_encode(self._external_leg_span_hz("readback"))
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

    def _scope_wave_center_hz(self) -> int:
        """CENTER-mode wave centre under the modeled scope center type.

        Filter Center (27 1C = 0) centres the streamed wave on the passband
        centre: the current mode's filter width decoded from the profile
        segments (the stored 1A 03 byte is BCD-encoded on the wire, so it is
        BCD-decoded first — 0x28 is slot 28, 2400 Hz SSB), halved, signed by
        sideband (LSB negative, USB positive). Carrier Point Center (= 1),
        any non-SSB mode, or an undecodable width streams centred on the VFO.
        """
        if self._scope_center_type != 0:
            return self._frequency
        try:
            mode = Mode(self._mode)
        except ValueError:
            return self._frequency
        sign = _SCOPE_CENTER_PASSBAND_SIGN.get(mode)
        if sign is None:
            return self._frequency
        rule = self.profile.resolve_filter_rule(mode.name)
        if rule is None or not rule.segments:
            return self._frequency
        try:
            width_hz = filter_index_to_hz(
                _bcd_level_decode(bytes([self._filter_width_index])),
                segments=rule.segments,
            )
        except ValueError:
            return self._frequency
        return self._frequency + sign * (width_hz // 2)

    def pump_scope_stream(self) -> int:
        if self.evidence.with_suffix(".pause").exists():
            return 0
        span = self._external_leg_span_hz("wave")
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
            first, last = self._scope_wave_center_hz(), span
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
        help="one-way DOWNLINK seconds: EVERY CI-V reply/unsolicited frame "
        "on the CI-V port leaves late (shared outbound CI-V FaultPlan) — a "
        "readback-latency knob: order is preserved, so this models a slow "
        "link rather than a reordering one; round-trip time is "
        "--uplink-delay + --response-delay, each applied once",
    )
    parser.add_argument(
        "--uplink-delay",
        type=float,
        default=0.0,
        help="one-way UPLINK seconds: EVERY inbound request waits this long "
        "in the simulator's inherited response_delay dispatch delay "
        "(applied in _receive) before it is parsed — a request-leg latency "
        "knob. The inherited mechanism has no per-port scoping, so this "
        "also holds control-port session traffic, never a second mechanism; "
        "round-trip time is --uplink-delay + --response-delay, each once",
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
    parser.add_argument(
        "--scope-center-type",
        type=int,
        choices=(0, 1),
        default=1,
        help="simulated CI-V 27 1C scope center type: 0 Filter Center streams "
        "the CENTER wave centred on the passband centre (LSB negative / USB "
        "positive by half the current filter width, from profile/filter "
        "data); 1 Carrier Point Center (default) streams centred on the VFO "
        "— the default keeps every existing row's geometry byte-identical",
    )
    parser.add_argument(
        "--external-span-after-s",
        type=float,
        default=None,
        help="front-panel span change: seconds after simulator start, set the "
        "radio span preset to --external-span-hz WITHOUT any client message "
        "(reuses PanoramaSimulator.span_index, never a second mechanism)",
    )
    parser.add_argument(
        "--external-span-hz",
        type=int,
        default=None,
        help="radio scope span preset the external change narrows to — must be "
        "one of the profile's scope_span_presets_hz",
    )
    parser.add_argument(
        "--external-span-skew-s",
        type=float,
        default=0.0,
        help="MOR-4451 adoption-order knob: staggers the two observable legs "
        "of the --external-span-after-s change so the arrival order at the "
        "client is deterministic. Positive: the streamed wave switches "
        "first and the span readback (0x15) keeps the old preset that many "
        "seconds longer (frames-first order). Negative: the span readback "
        "switches first and the wave keeps the old width |skew| seconds "
        "longer (readback-first order). Zero: both switch together (the "
        "previous behaviour). One scheduled change, two switch times — "
        "never a second mechanism.",
    )
    args = parser.parse_args()
    logging.basicConfig(filename=args.evidence.with_suffix(".log"), level=logging.INFO)
    args.evidence.write_text("")
    # Lag knobs stay off through the handshake: the client connect phase has
    # sub-second deadlines a delayed simulator cannot meet (MOR-4393). They
    # shape the test phase only, activated once the radio link is up.
    simulator = PanoramaSimulator(
        args.model,
        args.evidence,
        span_hz=args.span_hz,
        seed=args.seed,
        scope_center_type=args.scope_center_type,
    )
    # --freq was parsed but never applied: every row ran at the hardcoded
    # 14.25 MHz. Rows that pin a band preset need the VFO inside that band.
    simulator.set_frequency(args.freq)
    await simulator.start()
    if args.external_span_after_s is not None:
        if args.external_span_hz is None:
            raise SystemExit("--external-span-after-s needs --external-span-hz")
        presets = simulator.profile.scope_span_presets_hz
        if args.external_span_hz not in presets:
            raise SystemExit(
                f"--external-span-hz {args.external_span_hz} is not one of {list(presets)}"
            )

        async def _external_span_change() -> None:
            await asyncio.sleep(args.external_span_after_s)
            old_preset = simulator.profile.scope_span_presets_hz[simulator.span_index]
            simulator.span_index = presets.index(args.external_span_hz)
            skew = args.external_span_skew_s
            if skew > 0:
                simulator._external_hold = (
                    "readback",
                    old_preset,
                    time.monotonic() + skew,
                )
            elif skew < 0:
                simulator._external_hold = ("wave", old_preset, time.monotonic() - skew)
            logging.info(
                "external span change: now %d Hz (skew %.1f s)",
                args.external_span_hz,
                skew,
            )

        asyncio.create_task(_external_span_change())
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
        simulator.set_link_lag(
            args.response_delay, args.response_jitter, args.uplink_delay, args.seed
        )
        simulator.ack_delay = args.ack_delay
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
