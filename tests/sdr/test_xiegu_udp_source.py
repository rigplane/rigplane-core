"""Loopback UDP tests for the radio-LAN IQ source (MOR-4398)."""

from __future__ import annotations

import socket
import struct
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from rigplane.sdr import IqBlock, IqSource, UdpIqSourceSpec
from rigplane.sdr.xiegu_udp_source import XieguUdpIqSource, encode_tune
from rigplane.testing.timing import ManualClock, wait_until

_FIXTURE = Path(__file__).parents[1] / "fixtures/sdr/xiegu_udp_g7250_20_datagrams.bin"


def _fixture_datagrams() -> list[bytes]:
    raw = _FIXTURE.read_bytes()
    datagrams = []
    offset = 0
    while offset < len(raw):
        size = struct.unpack_from("<H", raw, offset)[0]
        offset += 2
        datagrams.append(raw[offset : offset + size])
        offset += size
    assert len(datagrams) == 20
    assert all(len(datagram) == 768 for datagram in datagrams)
    return datagrams


def _spec(radio_port: int, receive_port: int, host="127.0.0.1") -> UdpIqSourceSpec:
    return UdpIqSourceSpec(
        host=host,
        radio_port=radio_port,
        receive_port=receive_port,
        sample_rate_hz=48_000,
        samples_per_datagram=192,
        center_offset_hz=8000,
        usable_bandwidth_hz=40_000,
        tune_min_hz=100_000,
        tune_max_hz=54_000_000,
    )


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class _Harness:
    source: XieguUdpIqSource
    radio: socket.socket
    receive_port: int
    clock: ManualClock
    blocks: list[IqBlock]

    def send(self, payload: bytes, *, count: int = 1) -> None:
        for _ in range(count):
            self.radio.sendto(payload, ("127.0.0.1", self.receive_port))

    def command(self, timeout: float = 0.15) -> bytes | None:
        self.radio.settimeout(timeout)
        try:
            return self.radio.recvfrom(64)[0]
        except TimeoutError:
            return None

    def wait_for(self, count: int) -> list[IqBlock]:
        wait_until(lambda: len(self.blocks) >= count)
        return list(self.blocks)


@contextmanager
def _source(
    *,
    center_hz: int | None = None,
    opened: bool = True,
    start_grace_s: float = 0.1,
    start_settle_s: float = 0.1,
    stall_s: float = 1.0,
    host: str = "127.0.0.1",
) -> Iterator[_Harness]:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as radio:
        radio.bind(("127.0.0.1", 0))
        receive_port = _free_udp_port()
        clock = ManualClock()
        source = XieguUdpIqSource(
            _spec(int(radio.getsockname()[1]), receive_port, host),
            clock=clock,
            start_grace_s=start_grace_s,
            start_settle_s=start_settle_s,
            stall_s=stall_s,
        )
        blocks: list[IqBlock] = []
        source.on_block(blocks.append)
        if center_hz is not None:
            source.set_center_freq(center_hz)
        if opened:
            source.open()
        try:
            yield _Harness(source, radio, receive_port, clock, blocks)
        finally:
            source.close()


def test_encode_tune_matches_the_measured_wire_bytes() -> None:
    assert encode_tune(7_175_000) == bytes.fromhex("aa5555aa000100000000006d7b58")


def test_facts_and_protocol_are_slaved_without_opening() -> None:
    with _source(opened=False) as harness:
        source = harness.source
        assert isinstance(source, IqSource)
        assert source.facts.tuning == "slaved"
        assert source.facts.center_offset_hz == 8000
        assert source.facts.usable_bandwidth_hz == 40_000
        assert source.sample_rate_hz == 48_000
        assert source.frequency_range_hz() == (100_000, 54_000_000)
        assert source.center_freq_hz == 0


def test_fixture_replay_emits_scaled_complex64_blocks_at_recorded_centre() -> None:
    datagrams = _fixture_datagrams()
    with _source(center_hz=7_183_000) as harness:
        harness.source.open()  # idempotent
        for datagram in datagrams:
            harness.send(datagram)
        blocks = harness.wait_for(2)

    samples = np.concatenate([block.samples for block in blocks])
    assert len(samples) == 20 * 192
    assert all(block.center_freq_hz == 7_183_000 for block in blocks)
    assert all(block.sample_rate_hz == 48_000 for block in blocks)
    assert all(block.samples.dtype == np.complex64 for block in blocks)
    first_i, first_q = struct.unpack_from("<hh", datagrams[0])
    assert samples[0] == np.complex64((first_i + 1j * first_q) / 32768)


def test_no_block_is_emitted_before_a_centre_is_recorded() -> None:
    with _source() as harness:
        harness.send(bytes(768), count=10)
        harness.send(bytes(767))  # ordered behind the valid datagrams
        wait_until(lambda: harness.source.dropped_datagrams == 1)
        assert harness.blocks == []


def test_bad_length_and_other_sender_are_dropped_without_samples() -> None:
    with _source(center_hz=7_183_000) as harness:
        harness.send(bytes(767))
        wait_until(lambda: harness.source.dropped_datagrams == 1)
        assert harness.blocks == []
    with _source(center_hz=7_183_000, host="127.0.0.2") as harness:
        harness.send(bytes(768))
        wait_until(lambda: harness.source.dropped_datagrams == 1)
        assert harness.blocks == []


def test_already_flowing_stream_never_causes_a_tune_command() -> None:
    with _source(center_hz=7_183_000, stall_s=10.0) as harness:
        harness.send(bytes(768), count=10)
        harness.wait_for(1)
        harness.clock.advance(1.0)
        harness.send(bytes(768), count=10)
        harness.wait_for(2)
        assert harness.command() is None


def test_settled_centre_sends_exact_vfo_once_per_stall_interval() -> None:
    with _source(center_hz=7_183_000) as harness:
        harness.clock.advance(0.2)
        assert harness.command(timeout=2.0) == encode_tune(7_175_000)
        harness.clock.advance(0.5)
        assert harness.command() is None


def test_changing_centre_defers_the_vfo_write() -> None:
    with _source(start_grace_s=0.0, start_settle_s=0.25, stall_s=1.0) as harness:
        for index in range(6):
            harness.clock.advance(0.1)
            harness.source.set_center_freq(7_183_000 + index * 1000)
        assert harness.command() is None


def test_no_recorded_centre_means_no_vfo_write() -> None:
    with _source(start_grace_s=0.0, start_settle_s=0.0) as harness:
        harness.clock.advance(10.0)
        assert harness.command() is None


@pytest.mark.parametrize("center_hz", [99_999, 54_008_001])
def test_out_of_range_vfo_means_no_tune_command(center_hz: int) -> None:
    with _source(center_hz=center_hz, start_grace_s=0.0, start_settle_s=0.0) as harness:
        harness.clock.advance(10.0)
        assert harness.command() is None


def test_stalled_follows_datagram_gaps_and_recovery() -> None:
    with _source() as harness:
        assert harness.source.stalled is False
        harness.clock.advance(2.1)
        wait_until(lambda: harness.source.stalled)
        harness.send(bytes(768))
        harness.send(bytes(767))
        wait_until(lambda: harness.source.dropped_datagrams == 1)
        assert harness.source.stalled is False


def test_fixed_rate_and_idempotent_close_send_nothing() -> None:
    with _source(center_hz=7_183_000, start_grace_s=100.0) as harness:
        with pytest.raises(ValueError, match="sample_rate"):
            harness.source.set_sample_rate(96_000)
        harness.source.set_sample_rate(48_000)
        harness.source.set_gain(None)
        harness.source.close()
        harness.source.close()
        assert harness.source.center_freq_hz == 0
        assert harness.command() is None


@pytest.mark.parametrize("field", ["start_grace_s", "start_settle_s", "stall_s"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_timing_is_rejected(field: str, value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        with _source(opened=False, **{field: value}):
            pass


def test_stale_resolver_isolated_across_reopen(monkeypatch: pytest.MonkeyPatch) -> None:
    started, release = threading.Event(), threading.Event()
    calls = 0

    def resolve(host: str) -> str:
        nonlocal calls
        calls += 1
        assert threading.current_thread().name == "XieguUdpIqSource-reader"
        if calls == 1:
            started.set()
            release.wait(5)
        return "127.0.0.1"

    monkeypatch.setattr(socket, "gethostbyname", resolve)
    with _source(center_hz=7_183_000, host="localhost") as harness:
        wait_until(lambda: started.is_set())
        old_thread = harness.source._thread
        harness.source.close()
        harness.source.set_center_freq(7_183_000)
        harness.source.open()
        harness.send(bytes(768), count=10)
        harness.wait_for(1)

        release.set()
        old_thread.join(timeout=2)
        assert not old_thread.is_alive()
        harness.send(bytes(768), count=10)
        harness.wait_for(2)
        assert harness.source.is_open is True
        assert harness.source.center_freq_hz == 7_183_000


def test_malformed_radio_datagram_marks_next_block_overflowed() -> None:
    with _source(center_hz=7_183_000) as harness:
        harness.send(bytes(768))
        harness.send(bytes(767))
        harness.send(bytes(768), count=10)
        blocks = harness.wait_for(2)

    assert len(blocks[0].samples) == 192
    assert blocks[0].overflow is False
    assert blocks[1].overflow is True


def test_connection_reset_does_not_end_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    real_recvfrom = socket.socket.recvfrom
    reset, terminal = threading.Event(), threading.Event()

    def recvfrom(sock: socket.socket, size: int) -> tuple[bytes, tuple[str, int]]:
        if sock.getsockname()[0] == "0.0.0.0" and not reset.is_set():
            reset.set()
            raise ConnectionResetError("transient radio loss")
        if sock.getsockname()[0] == "0.0.0.0" and terminal.is_set():
            raise OSError("terminal radio loss")
        return real_recvfrom(sock, size)

    monkeypatch.setattr(socket.socket, "recvfrom", recvfrom)
    with _source(center_hz=7_183_000) as harness:
        wait_until(lambda: reset.is_set())
        harness.send(bytes(768), count=10)
        harness.wait_for(1)
        assert harness.source.is_open is True

        terminal.set()
        wait_until(lambda: not harness.source.is_open)
        assert harness.source.center_freq_hz == 0
        terminal.clear()
        harness.source.open()
        harness.clock.advance(10.0)
        assert harness.command() is None
        harness.source.set_center_freq(7_183_000)
        harness.send(bytes(768), count=10)
        harness.wait_for(2)
