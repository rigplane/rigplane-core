"""The IC-7300 simulation profile (``ic7300.toml``) against its recordings (MOR-3597).

The profile is data: every number in it is pinned here to the fixture it came
from (``tests/fixtures/ic7300/``), then ``CivSerialFake.from_profile`` is held to
what the recorded radio did: reply latency, the baud gate, the scope switches
and the wave stream, and ``python -m rigplane_testkit.sim serve --radio ic7300``
serves it end to end.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import pytest
from aiohttp import ClientSession

from rigplane.commands import parse_civ_frame
from rigplane_testkit.serial import CivSerialFake
from rigplane_testkit.serial.civ_serial import ScopeSource
from rigplane_testkit.serial.endpoints import TcpEndpoint
from rigplane_testkit.sim.profiles import load_sim_profile
from testkit.ic7300_fakes import FIXTURES, ic7300_profile_fake
from testkit.test_sim_serve import _kill_if_alive, _start_sim

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROFILE = REPO_ROOT / "rigplane_testkit" / "sim" / "profiles" / "ic7300.toml"
IDENTITY = bytes.fromhex("fefe94e01900fd")
WAVE_PREFIX = b"\xfe\xfe\xe0\x94\x27\x00"
ACK = bytes.fromhex("fefee094fbfd")
NAK = bytes.fromhex("fefee094fafd")


def _rows() -> list[dict]:
    lines = (FIXTURES / "civ_pairs.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line]


def _evidence() -> dict:
    return json.loads((FIXTURES / "evidence.json").read_text())


def _recorded_reply(request_hex: str) -> bytes:
    rows = _rows()
    for index, row in enumerate(rows):
        if row["dir"] == "c2r" and row["frame"] == request_hex:
            assert rows[index + 1]["dir"] == "r2c"
            return bytes.fromhex(rows[index + 1]["frame"])
    raise AssertionError(f"{request_hex} was not recorded")


def _recorded_sweeps() -> list[list[tuple[float, bytes]]]:
    """The recorded wave packets grouped by sweep: ``(offset_s, frame)``."""
    sweeps: list[list[tuple[float, bytes]]] = []
    for row in _rows():
        if row["window"] != "scope_session_scope":
            continue
        frame = bytes.fromhex(row["frame"])
        if frame[7] == 0x01:  # packet 1 of a sweep opens it
            sweeps.append([])
        sweeps[-1].append((row["t"], frame))
    return [[(t - s[0][0], f) for t, f in s] for s in sweeps]


def _bcd_le(data: bytes) -> int:
    return int(bytes(reversed(data)).hex())


async def _read_frames(reader, seconds: float) -> list[bytes]:
    """Every complete frame that arrives within ``seconds``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    frames: list[bytes] = []
    while (left := deadline - loop.time()) > 0:
        try:
            frames.append(
                bytes(await asyncio.wait_for(reader.readuntil(b"\xfd"), left))
            )
        except TimeoutError:
            break
    return frames


async def _ask(reader, writer, request: bytes, wait: float = 0.3) -> bytes | None:
    writer.write(request)
    await writer.drain()
    frames = await _read_frames(reader, wait)
    return next((f for f in frames if not f.startswith(WAVE_PREFIX)), None)


# ---------------------------------------------------------------------------
# The profile file against its recordings
# ---------------------------------------------------------------------------


def test_profile_loads_against_the_repo_root() -> None:
    profile = load_sim_profile(PROFILE, root=REPO_ROOT)
    assert profile.sim.id == "ic7300-usb"
    assert profile.sim.radio == "IC-7300"
    assert profile.sim.fidelity == "A"
    assert profile.sim.evidence == ("tests/fixtures/ic7300/civ_pairs.jsonl",)
    assert profile.transport.kind == "serial"
    assert profile.answers.recorded == (
        ("tests/fixtures/ic7300/civ_pairs.jsonl", "usb"),
    )


def test_transport_is_the_recorded_baud_without_echo_and_without_invented_identity() -> (
    None
):
    profile = load_sim_profile(PROFILE, root=REPO_ROOT)
    assert profile.transport.baud == _evidence()["baud"]["answers_at"][0] == 19200
    assert profile.transport.echo is False
    # Identity comes from rigs/ic7300.toml [identity].expected_ids; the
    # recorded `19 00` answer carries the same byte.
    assert profile.transport.identity is None
    assert parse_civ_frame(_recorded_reply(IDENTITY.hex())).data == b"\x94"


def test_initial_state_is_what_the_recorded_radio_reported() -> None:
    initial = load_sim_profile(PROFILE, root=REPO_ROOT).initial
    freq = parse_civ_frame(_recorded_reply("fefe94e003fd")).data
    mode = parse_civ_frame(_recorded_reply("fefe94e004fd")).data
    assert initial.frequency_hz == _bcd_le(freq) == 14_263_000
    assert (initial.mode, initial.filter) == ("USB", 2)
    assert mode == bytes([0x01, 0x02])  # USB, FIL2
    # Data mode off (1A 06) and preamp off (16 02) are the recorded answers.
    assert initial.data_mode is False
    assert parse_civ_frame(_recorded_reply("fefe94e01602fd")).data == b"\x00"


def test_reply_latency_is_the_recorded_median() -> None:
    profile = load_sim_profile(PROFILE, root=REPO_ROOT)
    answers = json.loads((FIXTURES / "probe_answers.json").read_text())["probes"]
    latencies = sorted(a["latency_ms"] for a in answers if a["class"] == "data")
    assert latencies[0] < profile.timing.reply_latency_ms < latencies[-1]
    assert abs(profile.timing.reply_latency_ms - statistics.median(latencies)) < 2


def test_scope_section_is_the_recorded_sweeps() -> None:
    scope = load_sim_profile(PROFILE, root=REPO_ROOT).scope
    sweeps = _recorded_sweeps()
    assert len(sweeps) == 5 and {len(s) for s in sweeps} == {11}
    # 475 bins: ten packets of 50 and the last of 25 after the 9-byte header.
    assert {sum(len(f) - 10 for _, f in s[1:]) for s in sweeps} == {475} == {scope.bins}
    span_read = parse_civ_frame(_recorded_reply("fefe94e0271500fd")).data
    assert scope.span_hz == _bcd_le(span_read[1:]) == 100_000
    assert scope.rate_hz == _evidence()["scope_stream_115200"]["sweeps_per_s"]
    assert scope.frame == "27 00"


def test_audio_is_the_bench_radios_native_usb_format() -> None:
    audio = load_sim_profile(PROFILE, root=REPO_ROOT).audio_rx
    # tests/fixtures/ic7300/README.md: Windows MME 44.1 kHz, 2 ch, s16.
    assert (audio.transport, audio.sample_rate_hz, audio.channels) == (
        "usb_device",
        44100,
        2,
    )
    assert audio.sample_format == "s16le"


# ---------------------------------------------------------------------------
# The fake built from the profile
# ---------------------------------------------------------------------------


async def test_fake_starts_in_the_profile_state() -> None:
    fake = ic7300_profile_fake()
    assert fake.echo is False
    assert fake.frequency_hz == 14_263_000
    assert fake.mode == 0x01 and fake.filter_width == 2
    assert fake.model == "IC-7300"
    reader, writer = await fake.opener(url="socket://p", baudrate=19200)
    try:
        assert await _ask(reader, writer, IDENTITY) == _recorded_reply(IDENTITY.hex())
    finally:
        writer.close()


async def test_replies_arrive_after_the_profile_latency() -> None:
    fake = ic7300_profile_fake()
    reader, writer = await fake.opener(url="socket://p", baudrate=19200)
    try:
        started = time.monotonic()
        writer.write(IDENTITY)
        await writer.drain()
        assert await _read_frames(reader, 0.015) == []  # nothing before ~30 ms
        frame = await asyncio.wait_for(reader.readuntil(b"\xfd"), 1.0)
        elapsed = time.monotonic() - started
    finally:
        writer.close()
    assert bytes(frame) == _recorded_reply(IDENTITY.hex())
    assert 0.025 <= elapsed < 0.25


async def test_latency_keeps_pipelined_replies_in_request_order() -> None:
    fake = ic7300_profile_fake()
    reader, writer = await fake.opener(url="socket://p", baudrate=19200)
    freq, mode = bytes.fromhex("fefe94e003fd"), bytes.fromhex("fefe94e004fd")
    try:
        writer.write(freq + mode + IDENTITY)
        await writer.drain()
        frames = await _read_frames(reader, 0.5)
    finally:
        writer.close()
    assert frames == [_recorded_reply(r.hex()) for r in (freq, mode, IDENTITY)]


@pytest.mark.parametrize("baud", [115200, 9600, 4800])
async def test_radio_is_silent_at_every_baud_but_the_recorded_one(baud: int) -> None:
    fake = ic7300_profile_fake()
    reader, writer = await fake.opener(url="socket://p", baudrate=baud)
    try:
        assert await _ask(reader, writer, IDENTITY) is None
    finally:
        writer.close()


async def test_a_link_without_a_baud_is_never_gated() -> None:
    fake = ic7300_profile_fake()
    reader, writer = await fake.opener(url="socket://p")
    try:
        assert await _ask(reader, writer, IDENTITY) == _recorded_reply(IDENTITY.hex())
    finally:
        writer.close()


async def test_scope_switches_at_19200_are_the_recorded_pairs() -> None:
    """evidence.json ``scope_enable_at_19200``: panel on ACKs, wave output NAKs."""
    fake = ic7300_profile_fake()
    reader, writer = await fake.opener(url="socket://p", baudrate=19200)
    try:
        for pair in _evidence()["scope_enable_at_19200"]["pairs"]:
            reply = await _ask(reader, writer, bytes.fromhex(pair["request"]))
            assert reply == bytes.fromhex(pair["reply"]), pair["note"]
        assert [
            f for f in await _read_frames(reader, 0.6) if f.startswith(WAVE_PREFIX)
        ] == []
    finally:
        writer.close()


async def test_scope_switches_at_115200_are_the_recorded_sequence() -> None:
    fake = ic7300_profile_fake(baud=115200)
    reader, writer = await fake.opener(url="socket://p", baudrate=115200)
    try:
        assert await _ask(reader, writer, bytes.fromhex("fefe94e02710fd")) == (
            bytes.fromhex("fefee094271001fd")
        )
        assert await _ask(reader, writer, bytes.fromhex("fefe94e02711fd")) == (
            bytes.fromhex("fefee094271100fd")
        )
        assert await _ask(reader, writer, bytes.fromhex("fefe94e0271001fd")) == ACK
        assert await _ask(reader, writer, bytes.fromhex("fefe94e0271101fd")) == ACK
        assert await _ask(reader, writer, bytes.fromhex("fefe94e02711fd")) == (
            bytes.fromhex("fefee094271101fd")
        )
    finally:
        writer.close()


async def _stream_sweeps(reader, seconds: float) -> list[tuple[float, bytes]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    packets: list[tuple[float, bytes]] = []
    while (left := deadline - loop.time()) > 0:
        try:
            frame = bytes(await asyncio.wait_for(reader.readuntil(b"\xfd"), left))
        except TimeoutError:
            break
        if frame.startswith(WAVE_PREFIX):
            packets.append((loop.time(), frame))
    return packets


async def test_wave_output_streams_the_recorded_sweeps_at_the_recorded_cadence() -> (
    None
):
    fake = ic7300_profile_fake(baud=115200)
    recorded = _recorded_sweeps()
    reader, writer = await fake.opener(url="socket://p", baudrate=115200)
    try:
        assert await _read_frames(reader, 0.35) == []  # wave output is off at start
        writer.write(bytes.fromhex("fefe94e0271101fd"))
        await writer.drain()
        packets = await _stream_sweeps(reader, 1.9)
    finally:
        writer.close()
    starts = [t for t, f in packets if f[7] == 0x01]
    sweeps = [
        [f for _, f in packets[i : i + 11]] for i in range(0, len(packets) - 10, 11)
    ]
    assert len(sweeps) >= 6
    bcd_seq = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x10, 0x11]
    for index, sweep in enumerate(sweeps):
        assert [f[7] for f in sweep] == bcd_seq
        # The recorded five sweeps loop: sweep 6 is sweep 1 again.
        assert sweep == [f for _, f in recorded[index % 5]]
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert abs(statistics.median(gaps) - 1 / 4.085) < 0.05


async def test_wave_output_off_stops_the_stream_and_close_ends_it() -> None:
    fake = ic7300_profile_fake(baud=115200)
    reader, writer = await fake.opener(url="socket://p", baudrate=115200)
    try:
        writer.write(bytes.fromhex("fefe94e0271101fd"))
        await writer.drain()
        assert await _stream_sweeps(reader, 0.6)
        writer.write(bytes.fromhex("fefe94e0271100fd"))
        await writer.drain()
        await _stream_sweeps(reader, 0.1)  # the ACK and any packet already in flight
        assert await _stream_sweeps(reader, 0.6) == []
    finally:
        writer.close()


async def test_a_link_without_a_baud_streams_the_scope() -> None:
    """socket:// and PTY report no baud: nothing to mismatch, the scope runs."""
    fake = ic7300_profile_fake()
    reader, writer = await fake.opener(url="socket://p")
    try:
        assert await _ask(reader, writer, bytes.fromhex("fefe94e0271101fd")) == ACK
        assert await _stream_sweeps(reader, 0.6)
    finally:
        writer.close()


async def test_usb_vanish_ends_the_stream() -> None:
    fake = ic7300_profile_fake(baud=115200)
    reader, writer = await fake.opener(url="socket://p", baudrate=115200)
    try:
        writer.write(bytes.fromhex("fefe94e0271101fd"))
        await writer.drain()
        assert await _stream_sweeps(reader, 0.4)
        fake.usb_vanish()
        await asyncio.sleep(0.05)  # the vanish lands on the next loop turn
        with pytest.raises(OSError):
            await asyncio.wait_for(reader.readuntil(b"\xfd"), 0.5)
    finally:
        writer.close()


async def test_scope_state_is_per_radio_not_per_connection() -> None:
    fake = ic7300_profile_fake(baud=115200)
    first_reader, first = await fake.opener(url="socket://p", baudrate=115200)
    try:
        assert await _ask(first_reader, first, bytes.fromhex("fefe94e0271101fd")) == ACK
    finally:
        first.close()
    reader, writer = await fake.opener(url="socket://p", baudrate=115200)
    try:
        # A reconnect finds the radio as the last session left it.
        reply = await _ask(reader, writer, bytes.fromhex("fefe94e02711fd"))
        assert reply == bytes.fromhex("fefee094271101fd")
    finally:
        writer.close()


@pytest.mark.parametrize("rate", ["inf", "-inf", "nan", "0.0"])
def test_a_profile_with_a_non_finite_or_zero_scope_rate_is_refused(
    rate: str, tmp_path: Path
) -> None:
    text = PROFILE.read_text().replace("rate_hz = 4.085", f"rate_hz = {rate}")
    custom = tmp_path / "ic7300-custom.toml"
    custom.write_text(text)
    with pytest.raises(ValueError, match="rate_hz"):
        CivSerialFake.from_profile(REPO_ROOT, custom)


@pytest.mark.parametrize("rate", [float("inf"), float("nan"), 0.0, -1.0])
def test_a_scope_source_refuses_a_rate_it_cannot_schedule(rate: float) -> None:
    with pytest.raises(ValueError, match="rate_hz"):
        ScopeSource(sweeps=(((0.0, b"x"),),), rate_hz=rate)


# The requests the fake answers from its own state, not from the table.
_NATIVE_REQUESTS = [
    "fefe94e003fd", "fefe94e004fd", "fefe94e02500fd", "fefe94e02600fd",
    "fefe94e01502fd", "fefe94e01c00fd", "fefe94e01900fd",
]  # fmt: skip


@pytest.mark.parametrize("request_hex", _NATIVE_REQUESTS)
async def test_native_handlers_answer_the_recorded_bytes(request_hex: str) -> None:
    """Tier A rests on payloads too, not only reply classes (S-meter: 00 00)."""
    fake = ic7300_profile_fake(reply_latency_s=0.0)
    reader, writer = await fake.opener(url="socket://p")
    try:
        reply = await _ask(reader, writer, bytes.fromhex(request_hex))
    finally:
        writer.close()
    assert reply == _recorded_reply(request_hex)


async def _loop_errors_during(seconds: float, body) -> list[dict]:
    """Run ``body`` and wait ``seconds``; return what the loop's handler caught."""
    loop = asyncio.get_running_loop()
    caught: list[dict] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: caught.append(context))
    try:
        await body()
        await asyncio.sleep(seconds)
    finally:
        loop.set_exception_handler(previous)
    return caught


async def test_closing_with_a_reply_pending_tears_down_cleanly() -> None:
    fake = ic7300_profile_fake()
    reader, writer = await fake.opener(url="socket://p", baudrate=19200)

    async def body() -> None:
        writer.write(IDENTITY)
        writer.close()  # before the 30 ms reply is due

    assert await _loop_errors_during(0.12, body) == []
    assert await reader.read() == b""  # EOF, and the late reply never arrived


async def test_a_tcp_client_leaving_with_a_reply_pending_tears_down_cleanly() -> None:
    fake = ic7300_profile_fake()
    endpoint = TcpEndpoint(fake)
    await endpoint.start()
    try:
        _, port = endpoint.device_url.removeprefix("socket://").rsplit(":", 1)
        _, client = await asyncio.open_connection("127.0.0.1", int(port))

        async def body() -> None:
            client.write(IDENTITY)
            await client.drain()
            client.close()

        assert await _loop_errors_during(0.15, body) == []
    finally:
        await endpoint.stop()


async def test_repeating_wave_enable_never_restarts_a_running_sweep() -> None:
    fake = ic7300_profile_fake(baud=115200)
    reader, writer = await fake.opener(url="socket://p", baudrate=115200)
    _, other = await fake.opener(url="socket://p", baudrate=115200)
    enable = bytes.fromhex("fefe94e0271101fd")
    try:
        writer.write(enable)

        async def repeat() -> None:
            for turn in range(14):  # every 100 ms: shorter than one 219 ms sweep
                await asyncio.sleep(0.1)
                (writer if turn % 2 else other).write(enable)

        repeater = asyncio.create_task(repeat())
        packets = await _stream_sweeps(reader, 1.5)
        await repeater
    finally:
        writer.close()
        other.close()
    seqs = [f[7] for _, f in packets]
    bcd_seq = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x10, 0x11]
    whole = len(seqs) // 11
    assert whole >= 5
    assert seqs[: whole * 11] == bcd_seq * whole


# ---------------------------------------------------------------------------
# serve --radio ic7300
# ---------------------------------------------------------------------------


async def test_serve_ic7300_serves_the_profile_end_to_end() -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX signal delivery")
    proc, line = await _start_sim(radio="ic7300")
    try:
        assert line["radio"] == "IC-7300" and line["fidelity"] == "A"
        assert line["evidence"] == "simulated"
        host, port = line["device"].removeprefix("socket://").rsplit(":", 1)
        reader, writer = await asyncio.open_connection(host, int(port))
        try:
            assert await _ask(reader, writer, IDENTITY) == _recorded_reply(
                IDENTITY.hex()
            )
            freq = await _ask(reader, writer, bytes.fromhex("fefe94e003fd"))
            assert _bcd_le(parse_civ_frame(freq).data) == 14_263_000
            assert await _ask(reader, writer, bytes.fromhex("fefe94e0271101fd")) == ACK
            assert await _stream_sweeps(reader, 0.8)
        finally:
            writer.close()
    finally:
        await _kill_if_alive(proc)


async def test_serve_selected_readbacks_follow_the_live_state() -> None:
    """`25 00` / `26 00` (what Core polls) track `03` / `04` after changes."""
    if sys.platform == "win32":
        pytest.skip("POSIX signal delivery")
    proc, line = await _start_sim(radio="ic7300")
    try:
        host, port = line["device"].removeprefix("socket://").rsplit(":", 1)
        reader, writer = await asyncio.open_connection(host, int(port))
        try:
            async with ClientSession() as http:
                resp = await http.post(
                    f"{line['control']}/event",
                    json={"event": "set_frequency", "hz": 7_100_000},
                )
                assert resp.status == 200
            bcd = bytes.fromhex(f"{7_100_000:010d}")[::-1]
            freq = await _ask(reader, writer, bytes.fromhex("fefe94e003fd"))
            assert parse_civ_frame(freq).data == bcd
            selected = await _ask(reader, writer, bytes.fromhex("fefe94e02500fd"))
            assert parse_civ_frame(selected).data == b"\x00" + bcd
            # A native mode/filter write (CW, FIL2) moves 04 and 26 00 together.
            assert await _ask(reader, writer, bytes.fromhex("fefe94e0060302fd")) == ACK
            mode = await _ask(reader, writer, bytes.fromhex("fefe94e004fd"))
            assert parse_civ_frame(mode).data == bytes([0x03, 0x02])
            selected_mode = await _ask(reader, writer, bytes.fromhex("fefe94e02600fd"))
            assert parse_civ_frame(selected_mode).data == bytes(
                [0x00, 0x03, 0x00, 0x02]
            )
        finally:
            writer.close()
    finally:
        await _kill_if_alive(proc)
