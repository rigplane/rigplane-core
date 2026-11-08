"""The optional ``[timing]`` keys of ``rigplane-sim-profile/1`` on the CI-V fake.

Every test writes its own profile: a copy of the shipped ``ic7300.toml``
with the ``[timing]`` section rewritten, built through
``CivSerialFake.from_profile`` so the profile-to-fake wiring is what runs.
No assertion reads a wall clock: the fake's ``time()``/``call_at()``
scheduler (``CivSerialFake._clock``) is replaced with the manual
``_DeliveryClock`` the Yaesu CAT adapter tests already drive, tests advance
it by hand and assert the exact bytes delivered after each advance, and
``write_drain_ms`` only records the delay ``asyncio.sleep`` was asked for.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core.types import bcd_encode
from rigplane_testkit.serial import CivSerialFake

from testkit.ic705_fakes import RADIO_ADDR as IC705_ADDR, ic705_usb_fake

# The manual clock of the Yaesu CAT adapter tests, reused (not copied): a
# shared helper module would be a new file outside this change's scope.
from testkit.test_sim_yaesu_cat_adapter import _DeliveryClock

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE = REPO_ROOT / "rigplane_testkit" / "sim" / "profiles" / "ic7300.toml"
ADDR = 0x94  # rigs/ic7300.toml [identity].expected_ids
INITIAL_FREQ = 14_263_000


def _answer(cmd: int, sub: int | None = None, data: bytes = b"") -> bytes:
    return build_civ_frame(CONTROLLER_ADDR, ADDR, cmd, sub=sub, data=data)


ACK = _answer(0xFB)
NAK = _answer(0xFA)
PTT_OFF = _answer(0x1C, sub=0x00, data=b"\x00")
PTT_ON = _answer(0x1C, sub=0x00, data=b"\x01")
FREQ_ANSWER = _answer(0x03, data=bcd_encode(INITIAL_FREQ))
ID_READ = build_civ_frame(ADDR, CONTROLLER_ADDR, 0x19, sub=0x00)
# rigs/ic7300.toml [identity].expected_ids: 19 00 answers with the id 94.
ID_ANSWER = _answer(0x19, sub=0x00, data=bytes.fromhex("94"))

PTT_READ = build_civ_frame(ADDR, CONTROLLER_ADDR, 0x1C, sub=0x00)
FREQ_READ = build_civ_frame(ADDR, CONTROLLER_ADDR, 0x03)
FREQ_SET = build_civ_frame(ADDR, CONTROLLER_ADDR, 0x05, data=bcd_encode(14_074_000))
BARE_MEMORY_WRITE = build_civ_frame(ADDR, CONTROLLER_ADDR, 0x09)
BARE_TX_BAND_EDGE = build_civ_frame(ADDR, CONTROLLER_ADDR, 0x1E, sub=0x01)


def _ptt_write(on: bool) -> bytes:
    return build_civ_frame(ADDR, CONTROLLER_ADDR, 0x1C, sub=0x00, data=bytes([int(on)]))


def _timing_fake(tmp_path: Path, timing: str) -> CivSerialFake:
    """The shipped IC-7300 profile with ``[timing]`` replaced by *timing*."""
    text = re.sub(r"\[timing\]\nreply_latency_ms = [0-9.]+\n", "", PROFILE.read_text())
    custom = tmp_path / "timing.toml"
    custom.write_text(text + f"[timing]\n{timing}\n")
    return CivSerialFake.from_profile(REPO_ROOT, custom)


async def _scheduled(fake: CivSerialFake) -> tuple[object, object, _DeliveryClock]:
    """Open the fake with a manual clock mounted as its scheduler."""
    reader, writer = await fake.opener(url="socket://p")
    clock = _DeliveryClock()
    fake._clock = clock
    return reader, writer, clock


def _delivered(reader) -> bytes:
    """Every byte delivered to the reader so far."""
    return bytes(reader._buffer)


async def _drain_recording(writer, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """``await writer.drain()`` with ``asyncio.sleep`` recording the delay it
    was asked for instead of taking it."""
    requested: list[float] = []

    async def _record_sleep(delay: float, *args: object, **kwargs: object) -> None:
        requested.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _record_sleep)
    await writer.drain()
    return requested


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


async def test_without_new_keys_the_answers_take_the_reply_latency(
    tmp_path, monkeypatch
) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 60")
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(FREQ_SET)
        writer.write(PTT_READ)
        assert _delivered(reader) == b""  # nothing before the 60 ms latency
        clock.advance(0.059)
        assert _delivered(reader) == b""
        clock.advance(0.002)
        assert await _drain_recording(writer, monkeypatch) == []  # no drain key
    finally:
        writer.close()
    assert _delivered(reader) == ACK + PTT_OFF  # both answers at 60 ms, in order
    assert fake.ptt is False


async def test_without_switch_keys_ptt_flips_inside_the_write(tmp_path) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 60")
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(_ptt_write(True))  # synchronous: no timer, no sleep
        assert fake.ptt is True
        assert len(clock.active) == 1  # only the ACK delivery is scheduled
        clock.advance(0.061)
        assert _delivered(reader) == ACK  # the ACK still waits the latency
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# The identity-delay fault injection is not part of the [timing] order
# ---------------------------------------------------------------------------


async def test_an_instant_reply_does_not_wait_behind_the_delayed_identity_answer() -> (
    None
):
    """Legacy fault injection, no ``[timing]`` key set: slow identity,
    instant everything else. The frequency answer to a pipelined read is
    already readable while the delayed identity answer is still in flight."""
    fake = CivSerialFake(echo=False, frequency_hz=INITIAL_FREQ)
    fake.delay_identity_answer(0.15)
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(ID_READ + FREQ_READ)
        assert _delivered(reader) == FREQ_ANSWER  # synchronous, clock untouched
        clock.advance(0.149)
        assert _delivered(reader) == FREQ_ANSWER  # the identity answer not yet
        clock.advance(0.002)
        assert _delivered(reader) == FREQ_ANSWER + ID_ANSWER  # lands at 150 ms
    finally:
        writer.close()


async def test_a_positive_reply_latency_stays_behind_the_delayed_identity_answer() -> (
    None
):
    """With a configured reply latency the legacy order holds: the identity
    answer (latency + fault delay) still holds a later read's answer back."""
    fake = CivSerialFake(echo=False, frequency_hz=INITIAL_FREQ, reply_latency_s=0.05)
    fake.delay_identity_answer(0.15)
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(ID_READ + FREQ_READ)
        assert _delivered(reader) == b""  # both answers are in flight
        clock.advance(0.199)
        assert _delivered(reader) == b""  # the read's answer waits behind identity
        clock.advance(0.002)
        assert _delivered(reader) == ID_ANSWER + FREQ_ANSWER  # identity first
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# ack_ms_set / ptt_set_ack_ms: the ACK fallback chain
# ---------------------------------------------------------------------------


async def test_ack_ms_set_covers_the_ack_to_a_set_command(tmp_path) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 60\nack_ms_set = 20")
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(FREQ_SET)
        clock.advance(0.019)
        assert _delivered(reader) == b""
        clock.advance(0.002)
        assert _delivered(reader) == ACK  # 20 ms (ack_ms_set), not the 60 ms
        assert not clock.active  # nothing is left for the reply-latency time
    finally:
        writer.close()


async def test_ptt_set_ack_ms_wins_and_falls_back_to_ack_ms_set(tmp_path) -> None:
    fake = _timing_fake(
        tmp_path, "reply_latency_ms = 60\nack_ms_set = 55\nptt_set_ack_ms = 15"
    )
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(_ptt_write(True))
        writer.write(FREQ_SET)
        clock.advance(0.014)
        assert _delivered(reader) == b""
        clock.advance(0.002)
        assert _delivered(reader) == ACK  # 15 ms: ptt_set_ack_ms
        clock.advance(0.038)  # 54 ms: the set ACK (ack_ms_set, 55) not yet due
        assert _delivered(reader) == ACK
        clock.advance(0.002)
        assert _delivered(reader) == ACK + ACK  # 55 ms, before the 60 ms latency
    finally:
        writer.close()


async def test_ptt_write_ack_falls_back_to_ack_ms_set(tmp_path) -> None:
    """No ``ptt_set_ack_ms``: the PTT write's ACK answers at ``ack_ms_set``."""
    fake = _timing_fake(tmp_path, "reply_latency_ms = 60\nack_ms_set = 25")
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(_ptt_write(True))
        clock.advance(0.024)
        assert _delivered(reader) == b""  # not earlier than 25 ms
        clock.advance(0.002)
        assert _delivered(reader) == ACK  # at the ack_ms_set time
        assert not clock.active  # and not at the 60 ms reply latency
    finally:
        writer.close()


async def test_ptt_write_ack_falls_back_to_reply_latency(tmp_path) -> None:
    """Neither ACK override set: the PTT write's ACK waits the reply latency."""
    fake = _timing_fake(tmp_path, "reply_latency_ms = 60")
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(_ptt_write(True))
        clock.advance(0.059)
        assert _delivered(reader) == b""
        clock.advance(0.002)
        assert _delivered(reader) == ACK
    finally:
        writer.close()


async def test_a_zero_delay_ack_does_not_overtake_a_pending_reply(tmp_path) -> None:
    """Pipelined read (delayed) + set (zero-delay ACK): request order wins."""
    fake = _timing_fake(tmp_path, "reply_latency_ms = 60\nack_ms_set = 0")
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(FREQ_READ + FREQ_SET)
        assert _delivered(reader) == b""  # the instant ACK waits behind the read
        clock.advance(0.061)
        assert _delivered(reader) == FREQ_ANSWER + ACK  # read first, ACK after
        writer.write(FREQ_SET)  # nothing pending now: the ACK is synchronous
        assert _delivered(reader) == FREQ_ANSWER + ACK + ACK
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# readback_ms: the PTT read only
# ---------------------------------------------------------------------------


async def test_readback_ms_covers_only_the_ptt_read(tmp_path) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 60\nreadback_ms = 15")
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(PTT_READ)  # readback: 15 ms
        writer.write(FREQ_READ)  # every other read: 60 ms
        writer.write(_ptt_write(False))  # its ACK: no override, so 60 ms
        clock.advance(0.014)
        assert _delivered(reader) == b""
        clock.advance(0.002)
        assert _delivered(reader) == PTT_OFF  # the readback answer at 15 ms
        clock.advance(0.043)  # 59 ms
        assert _delivered(reader) == PTT_OFF
        clock.advance(0.002)  # 60 ms: the freq answer, then the PTT ACK, in order
        assert _delivered(reader) == PTT_OFF + FREQ_ANSWER + ACK
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# ACK/NAK frames without a write payload: the class follows the reply frame
# ---------------------------------------------------------------------------


async def test_bare_09_and_1e_01_naks_use_ack_ms_set(tmp_path) -> None:
    """A bare ``09`` and a bare ``1E 01`` (no payload, NAKed): their NAK
    frames are ACK/NAK answers and take ``ack_ms_set``, not the reply
    latency."""
    fake = _timing_fake(tmp_path, "reply_latency_ms = 60\nack_ms_set = 20")
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(BARE_MEMORY_WRITE)
        writer.write(BARE_TX_BAND_EDGE)
        clock.advance(0.019)
        assert _delivered(reader) == b""
        clock.advance(0.002)
        assert _delivered(reader) == NAK + NAK  # both at the ack_ms_set time
    finally:
        writer.close()


async def test_bare_09_nak_uses_ack_ms_set_on_the_ic705_table_too() -> None:
    """The classification is the shared mechanism, not an IC-7300 case: the
    same bare ``09`` NAK against the IC-705's recorded USB table. The only
    other serial profile with ``[answers]`` is ``ic705-usb.toml``, which
    ``from_profile`` cannot build (``rigs/ic705.toml`` declares no
    ``[identity].expected_ids``), so the repo's ``ic705_usb_fake`` mounts
    the table with the timing set directly."""
    fake = ic705_usb_fake(reply_latency_s=0.060, ack_set_s=0.020)
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(build_civ_frame(IC705_ADDR, CONTROLLER_ADDR, 0x09))
        clock.advance(0.019)
        assert _delivered(reader) == b""
        clock.advance(0.002)
        assert _delivered(reader) == build_civ_frame(CONTROLLER_ADDR, IC705_ADDR, 0xFA)
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# write_drain_ms
# ---------------------------------------------------------------------------


async def test_write_drain_ms_delays_the_drain(tmp_path, monkeypatch) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 0\nwrite_drain_ms = 30")
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(FREQ_SET)
        assert _delivered(reader) == ACK  # reply latency 0: already delivered
        assert await _drain_recording(writer, monkeypatch) == [0.03]  # 30 ms asked
    finally:
        writer.close()
    assert not clock.active


# ---------------------------------------------------------------------------
# tx_on_switch_ms / tx_off_switch_ms
# ---------------------------------------------------------------------------


async def test_tx_on_switch_flips_ptt_after_the_ack(tmp_path) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 0\ntx_on_switch_ms = 40")
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(_ptt_write(True))
        assert _delivered(reader) == ACK  # the ACK is not delayed by the switch
        assert fake.ptt is False  # the state has not flipped yet
        writer.write(PTT_READ)
        assert _delivered(reader) == ACK + PTT_OFF  # reads see the old value
        clock.advance(0.039)
        assert fake.ptt is False
        clock.advance(0.002)  # the 40 ms switch lands
        assert fake.ptt is True
        writer.write(PTT_READ)
        assert _delivered(reader) == ACK + PTT_OFF + PTT_ON
    finally:
        writer.close()


async def test_tx_off_switch_delays_the_drop(tmp_path) -> None:
    fake = _timing_fake(tmp_path, "reply_latency_ms = 0\ntx_off_switch_ms = 40")
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(_ptt_write(True))  # no on-delay: keys at once
        assert fake.ptt is True
        writer.write(_ptt_write(False))
        assert _delivered(reader) == ACK + ACK
        assert fake.ptt is True  # still on until the off switch lands
        clock.advance(0.039)
        assert fake.ptt is True
        clock.advance(0.002)
        assert fake.ptt is False
    finally:
        writer.close()


async def test_a_later_ptt_write_supersedes_a_pending_switch(tmp_path) -> None:
    fake = _timing_fake(
        tmp_path, "reply_latency_ms = 0\ntx_on_switch_ms = 60\ntx_off_switch_ms = 20"
    )
    reader, writer, clock = await _scheduled(fake)
    try:
        writer.write(_ptt_write(True))  # would flip at ~60 ms
        clock.advance(0.025)
        writer.write(_ptt_write(False))  # at ~25 ms: supersedes, flips at ~45 ms
        assert len(clock.active) == 1  # only the OFF switch remains
        assert fake.ptt is False  # never showed ON so far
        clock.advance(0.021)  # past the OFF switch (~45 ms)
        assert fake.ptt is False
        clock.advance(0.040)  # past the cancelled ON time (60 ms)
        assert fake.ptt is False  # the ON switch never fired
        writer.write(PTT_READ)
        assert _delivered(reader) == ACK + ACK + PTT_OFF
    finally:
        writer.close()


async def test_closing_mid_switch_tears_down_cleanly(tmp_path) -> None:
    fake = _timing_fake(
        tmp_path, "reply_latency_ms = 0\ntx_on_switch_ms = 60\ntx_off_switch_ms = 20"
    )
    reader, writer, clock = await _scheduled(fake)
    caught: list[dict] = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: caught.append(context))
    try:
        writer.write(_ptt_write(True))
        clock.advance(0.025)
        writer.write(_ptt_write(False))  # cancels the pending ON switch
        clock.advance(0.005)
        writer.close()  # the OFF switch is still pending here
        clock.advance(0.120)  # both switch times pass while closed
    finally:
        loop.set_exception_handler(previous)
    assert caught == []
    assert fake._ptt_switch_handle is None  # no dangling switch timer
    assert await reader.read() == ACK + ACK  # both ACKs landed before the close
    assert await reader.read() == b""  # EOF: the closed port stays closed
