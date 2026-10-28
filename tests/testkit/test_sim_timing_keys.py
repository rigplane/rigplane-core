"""The optional ``[timing]`` keys of ``rigplane-sim-profile/1`` on the CI-V fake.

Every test writes its own profile: a copy of the shipped ``ic7300.toml``
with the ``[timing]`` section rewritten, built through
``CivSerialFake.from_profile`` so the profile-to-fake wiring is what runs.
Times stay small (10-60 ms); assertions compare ordering or measured
elapsed time with at least 20 ms of margin on each side, so a loaded
machine cannot flip them.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane_testkit.icom import _bcd_encode_freq
from rigplane_testkit.serial import CivSerialFake

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE = REPO_ROOT / "rigplane_testkit" / "sim" / "profiles" / "ic7300.toml"
ADDR = 0x94  # rigs/ic7300.toml [identity].expected_ids
ACK = bytes.fromhex("fefee094fbfd")
INITIAL_FREQ = 14_263_000

PTT_READ = build_civ_frame(ADDR, CONTROLLER_ADDR, 0x1C, sub=0x00)
FREQ_READ = build_civ_frame(ADDR, CONTROLLER_ADDR, 0x03)
FREQ_SET = build_civ_frame(
    ADDR, CONTROLLER_ADDR, 0x05, data=_bcd_encode_freq(14_074_000)
)


def _ptt_write(on: bool) -> bytes:
    return build_civ_frame(ADDR, CONTROLLER_ADDR, 0x1C, sub=0x00, data=bytes([int(on)]))


def _timing_fake(tmp_path: Path, timing: str) -> CivSerialFake:
    """The shipped IC-7300 profile with ``[timing]`` replaced by *timing*."""
    text = re.sub(r"\[timing\]\nreply_latency_ms = [0-9.]+\n", "", PROFILE.read_text())
    custom = tmp_path / "timing.toml"
    custom.write_text(text + f"[timing]\n{timing}\n")
    return CivSerialFake.from_profile(REPO_ROOT, custom)


async def _read(reader, timeout: float = 0.5) -> bytes:
    return bytes(await asyncio.wait_for(reader.readuntil(b"\xfd"), timeout))


async def _ask(
    reader, writer, request: bytes, timeout: float = 0.5
) -> tuple[float, bytes]:
    """Write *request*; return the elapsed seconds and the reply frame."""
    started = time.monotonic()
    writer.write(request)
    await writer.drain()
    return time.monotonic() - started, await _read(reader, timeout)


# ---------------------------------------------------------------------------
# The loader and the from_profile wiring
# ---------------------------------------------------------------------------


def test_the_loader_accepts_every_timing_key(tmp_path) -> None:
    fake = _timing_fake(
        tmp_path,
        "reply_latency_ms = 60\n"
        "ack_ms_set = 30\n"
        "ptt_set_ack_ms = 10\n"
        "readback_ms = 15\n"
        "write_drain_ms = 5\n"
        "tx_on_switch_ms = 40\n"
        "tx_off_switch_ms = 20",
    )
    profile = fake.sim_profile
    assert profile is not None
    timing = profile.timing
    assert (
        timing.reply_latency_ms,
        timing.ack_ms_set,
        timing.ptt_set_ack_ms,
        timing.readback_ms,
        timing.write_drain_ms,
        timing.tx_on_switch_ms,
        timing.tx_off_switch_ms,
    ) == (60.0, 30.0, 10.0, 15.0, 5.0, 40.0, 20.0)


# ---------------------------------------------------------------------------
# Unset keys: today's behaviour
# ---------------------------------------------------------------------------


async def test_without_new_keys_the_answers_take_the_reply_latency(tmp_path) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 60")
    reader, writer = await fake.opener(url="socket://p")
    try:
        set_elapsed, ack = await _ask(reader, writer, FREQ_SET)
        assert ack == ACK
        read_started = time.monotonic()
        writer.write(PTT_READ)
        drain_started = time.monotonic()
        await writer.drain()
        drain_elapsed = time.monotonic() - drain_started
        readback = await _read(reader)
        read_elapsed = time.monotonic() - read_started
    finally:
        writer.close()
    assert parse_civ_frame(readback).data == b"\x00"
    assert set_elapsed >= 0.04  # the set ACK keeps reply_latency_ms (60 ms)
    assert read_elapsed >= 0.04  # the PTT read keeps reply_latency_ms too
    assert drain_elapsed < 0.02  # drain is instant without write_drain_ms
    assert fake.ptt is False


async def test_without_switch_keys_ptt_flips_inside_the_write(tmp_path) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 60")
    reader, writer = await fake.opener(url="socket://p")
    try:
        writer.write(_ptt_write(True))  # synchronous: no timer, no sleep
        assert fake.ptt is True
        assert await _read(reader) == ACK
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# ack_ms_set / ptt_set_ack_ms: the ACK fallback chain
# ---------------------------------------------------------------------------


async def test_ack_ms_set_covers_the_ack_to_a_set_command(tmp_path) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 60\nack_ms_set = 20")
    reader, writer = await fake.opener(url="socket://p")
    try:
        elapsed, ack = await _ask(reader, writer, FREQ_SET)
    finally:
        writer.close()
    assert ack == ACK
    assert elapsed < 0.04  # 20 ms (ack_ms_set), not the 60 ms reply latency


async def test_ptt_set_ack_ms_wins_and_falls_back_to_ack_ms_set(tmp_path) -> None:
    fake = _timing_fake(
        tmp_path, "reply_latency_ms = 60\nack_ms_set = 55\nptt_set_ack_ms = 15"
    )
    reader, writer = await fake.opener(url="socket://p")
    try:
        ptt_elapsed, ptt_ack = await _ask(reader, writer, _ptt_write(True))
        set_elapsed, set_ack = await _ask(reader, writer, FREQ_SET)
    finally:
        writer.close()
    assert ptt_ack == set_ack == ACK
    assert ptt_elapsed < 0.035  # ptt_set_ack_ms (15), not ack_ms_set (55)
    assert set_elapsed >= 0.035  # ack_ms_set (55) still covers other writes


# ---------------------------------------------------------------------------
# readback_ms: the PTT read only
# ---------------------------------------------------------------------------


async def test_readback_ms_covers_only_the_ptt_read(tmp_path) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 60\nreadback_ms = 15")
    reader, writer = await fake.opener(url="socket://p")
    try:
        readback_elapsed, ptt = await _ask(reader, writer, PTT_READ)
        freq_elapsed, freq = await _ask(reader, writer, FREQ_READ)
        write_elapsed, ack = await _ask(reader, writer, _ptt_write(False))
    finally:
        writer.close()
    assert parse_civ_frame(ptt).data == b"\x00"
    assert parse_civ_frame(freq).data == _bcd_encode_freq(INITIAL_FREQ)
    assert ack == ACK
    assert readback_elapsed < 0.035  # readback_ms (15), not reply latency (60)
    assert freq_elapsed >= 0.04  # every other read keeps reply_latency_ms
    assert write_elapsed >= 0.04  # and so does the ACK to a PTT write


# ---------------------------------------------------------------------------
# write_drain_ms
# ---------------------------------------------------------------------------


async def test_write_drain_ms_delays_the_drain(tmp_path) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 0\nwrite_drain_ms = 30")
    reader, writer = await fake.opener(url="socket://p")
    try:
        writer.write(FREQ_SET)
        started = time.monotonic()
        await writer.drain()
        elapsed = time.monotonic() - started
        assert await _read(reader) == ACK
    finally:
        writer.close()
    assert 0.01 <= elapsed < 0.15  # a 30 ms drain, >= 20 ms margins


# ---------------------------------------------------------------------------
# tx_on_switch_ms / tx_off_switch_ms
# ---------------------------------------------------------------------------


async def test_tx_on_switch_flips_ptt_after_the_ack(tmp_path) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 0\ntx_on_switch_ms = 40")
    reader, writer = await fake.opener(url="socket://p")
    try:
        ack_elapsed, ack = await _ask(reader, writer, _ptt_write(True))
        assert ack == ACK
        assert ack_elapsed < 0.02  # the ACK is not delayed by the switch
        assert fake.ptt is False  # the state has not flipped yet
        _, old = await _ask(reader, writer, PTT_READ)
        assert parse_civ_frame(old).data == b"\x00"  # reads see the old value
        await asyncio.sleep(0.06)  # past the 40 ms switch
        assert fake.ptt is True
        _, new = await _ask(reader, writer, PTT_READ)
        assert parse_civ_frame(new).data == b"\x01"
    finally:
        writer.close()


async def test_tx_off_switch_delays_the_drop(tmp_path) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 0\ntx_off_switch_ms = 40")
    reader, writer = await fake.opener(url="socket://p")
    try:
        await _ask(reader, writer, _ptt_write(True))  # no on-delay: keys at once
        assert fake.ptt is True
        ack_elapsed, ack = await _ask(reader, writer, _ptt_write(False))
        assert ack == ACK
        assert ack_elapsed < 0.02
        assert fake.ptt is True  # still on until the off switch lands
        await asyncio.sleep(0.06)  # past the 40 ms switch
        assert fake.ptt is False
    finally:
        writer.close()


async def test_a_later_ptt_write_supersedes_a_pending_switch(tmp_path) -> None:
    fake = _timing_fake(
        tmp_path, "reply_latency_ms = 0\ntx_on_switch_ms = 60\ntx_off_switch_ms = 20"
    )
    reader, writer = await fake.opener(url="socket://p")
    try:
        await _ask(reader, writer, _ptt_write(True))  # would flip at ~60 ms
        await asyncio.sleep(0.025)
        await _ask(reader, writer, _ptt_write(False))  # at ~25 ms: supersedes
        assert fake.ptt is False  # never showed ON so far
        await asyncio.sleep(0.03)  # past the OFF switch (~45 ms)
        assert fake.ptt is False
        await asyncio.sleep(0.04)  # past the cancelled ON time (60 ms)
        assert fake.ptt is False  # the ON switch never fired
        _, readback = await _ask(reader, writer, PTT_READ)
        assert parse_civ_frame(readback).data == b"\x00"
    finally:
        writer.close()


async def test_closing_mid_switch_tears_down_cleanly(tmp_path) -> None:
    fake = _timing_fake(
        tmp_path, "reply_latency_ms = 0\ntx_on_switch_ms = 60\ntx_off_switch_ms = 20"
    )
    reader, writer = await fake.opener(url="socket://p")
    loop = asyncio.get_running_loop()
    caught: list[dict] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: caught.append(context))

    async def body() -> None:
        writer.write(_ptt_write(True))
        await asyncio.sleep(0.025)
        writer.write(_ptt_write(False))  # cancels the pending ON switch
        await asyncio.sleep(0.005)
        writer.close()  # the OFF switch is still pending here

    try:
        await body()
        await asyncio.sleep(0.12)  # both switch times pass while closed
    finally:
        loop.set_exception_handler(previous)
    assert caught == []
    assert fake._ptt_switch_handle is None  # no dangling switch timer
    assert await reader.read() == ACK + ACK  # both ACKs landed before the close
    assert await reader.read() == b""  # EOF: the closed port stays closed
