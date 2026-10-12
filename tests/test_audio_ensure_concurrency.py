"""Concurrency regressions for ``_ensure_audio_transport`` (MOR-3400).

The rebuild used to publish a fresh ``IcomTransport`` into
``_audio_transport`` BEFORE awaiting its ``connect``, so a second
``_ensure_audio_transport`` caller (``start_rx``/``start_tx``, the
soft-reconnect audio tail, the audio-error watchdog) or a teardown
(``_teardown_audio_transport`` and the inline teardown sites) could see a
half-built transport: tear it down mid-handshake (orphaning or half-closing
whatever ended up in the slot), record its reset ``send_seq`` — 0 — over the
session's carried ``_audio_resume_seq``, or let the post-connect code build
``AudioStream`` on a slot a teardown had already emptied.

Each test drives the REAL ``AudioRuntimeMixin._ensure_audio_transport`` over
the REAL ``IcomTransport`` (no MagicMock): ``connect`` is replaced by a patch
that performs the real numbering reset, parks on an ``asyncio.Event`` exactly
where the radio handshake would suspend, then claims the FD like production.
"""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING

import pytest

from rigplane.core.exceptions import ConnectionError
from rigplane.core.transport import IcomTransport

from test_audio_reconnect_rearm import (
    _connect_fd_only,
    _reserve_audio_socket,
    _StaleFdHost,
)

if TYPE_CHECKING:
    from pytest import MonkeyPatch


class _EnsureHost(_StaleFdHost):
    """Hand-built host reaching the real ``_ensure_audio_transport``.

    Adds what ``_StaleFdHost`` leaves out: the single-flight lock (present
    but inert on an unfixed tree, load-bearing after the fix), the ownership
    epochs and the carried audio numbering.
    """

    def __init__(self) -> None:
        super().__init__()
        self._audio_ensure_lock = asyncio.Lock()
        self._civ_epoch = 0
        self._owner_stop_epoch = 0
        self._audio_resume_seq: int | None = None


class _ConnectGate:
    """``IcomTransport.connect`` patch that parks inside the handshake.

    Mirrors the real ``IcomTransport.connect`` prologue (the numbering reset
    that a racing teardown observes as 0), parks the caller on an event where
    the radio discovery handshake would suspend, then claims the FD exactly
    like production via ``_connect_fd_only``. Instances that complete the
    claim are recorded so a test can tell the published transport from
    orphaned ones.
    """

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.claimed: list[IcomTransport] = []

    def install(self, monkeypatch: MonkeyPatch) -> None:
        gate = self

        async def gated_connect(
            transport: IcomTransport,
            host: str,
            port: int,
            *,
            local_host: str | None = None,
            local_port: int = 0,
            sock: socket.socket | None = None,
        ) -> None:
            transport.send_seq = 0
            await gate.release.wait()
            await _connect_fd_only(
                transport,
                host,
                port,
                local_host=local_host,
                local_port=local_port,
                sock=sock,
            )
            gate.claimed.append(transport)

        monkeypatch.setattr(IcomTransport, "connect", gated_connect, raising=True)


@pytest.mark.asyncio
async def test_concurrent_ensure_builds_exactly_one_transport(
    monkeypatch: MonkeyPatch,
) -> None:
    """Two overlapping ensures must connect exactly one transport.

    Pre-fix, the second caller tore the first caller's half-built transport
    down mid-handshake and both raced to claim the same reserved socket FD:
    one caller failed and the survivor published a stream whose transport no
    longer matched the slot.
    """
    gate = _ConnectGate()
    gate.install(monkeypatch)

    host = _EnsureHost()
    sock, port = _reserve_audio_socket()
    host._audio_sock_pending = sock
    host._audio_local_port = port

    first = asyncio.create_task(host._ensure_audio_transport())
    second = asyncio.create_task(host._ensure_audio_transport())
    await asyncio.sleep(0.01)  # both callers run until they park

    gate.release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    # Neither caller failed.
    assert results == [None, None]
    # Exactly one transport was ever connected...
    assert len(gate.claimed) == 1
    published = host._audio_transport
    assert published is gate.claimed[0]
    assert published is not None
    assert published._udp_transport is not None
    # ...the stream rides that transport (the slot matches the stream)...
    assert host._audio_stream is not None
    assert host._audio_stream.transport is published
    # ...and no connected transport was left behind undisconnected.
    for transport in gate.claimed:
        if transport is published:
            continue
        assert transport._udp_transport is None

    await host._teardown_audio_transport()
    sock.close()


@pytest.mark.asyncio
async def test_teardown_during_rebuild_keeps_carried_seq(
    monkeypatch: MonkeyPatch,
) -> None:
    """A teardown landing inside the rebuild's connect must not zero the seq.

    Pre-fix, ``_teardown_audio_transport`` saw the half-built transport in
    the slot and recorded its ``send_seq`` — reset to 0 by ``connect`` — over
    the session's carried ``_audio_resume_seq`` (MOR-3366).
    """
    gate = _ConnectGate()
    gate.install(monkeypatch)

    host = _EnsureHost()
    sock, port = _reserve_audio_socket()
    host._audio_sock_pending = sock
    host._audio_local_port = port
    host._audio_resume_seq = 1234

    ensure_task = asyncio.create_task(host._ensure_audio_transport())
    await asyncio.sleep(0.01)  # park inside connect

    # A teardown lands while the rebuild's connect is parked (the audio
    # watchdog or the soft-reconnect tail racing the rebuild).
    await host._teardown_audio_transport()

    gate.release.set()
    results = await asyncio.gather(ensure_task, return_exceptions=True)

    # The carried numbering was never overwritten with the reset 0...
    assert host._audio_resume_seq == 1234
    # ...the rebuild completed...
    assert results == [None]
    # ...and the rebuilt transport resumes at the carried numbering.
    published = host._audio_transport
    assert published is not None
    assert published.send_seq == 1234

    await host._teardown_audio_transport()
    sock.close()


@pytest.mark.asyncio
async def test_owner_stop_during_rebuild_supersedes(
    monkeypatch: MonkeyPatch,
) -> None:
    """An owner stop during the handshake must discard the private transport.

    Pre-fix there was no ownership check: the rebuild published its
    transport onto a radio this session no longer owned.
    """
    gate = _ConnectGate()
    gate.install(monkeypatch)

    host = _EnsureHost()
    sock, port = _reserve_audio_socket()
    host._audio_sock_pending = sock
    host._audio_local_port = port
    host._audio_resume_seq = 1234

    ensure_task = asyncio.create_task(host._ensure_audio_transport())
    await asyncio.sleep(0.01)  # park inside connect

    host._owner_stop_epoch += 1  # owner stop lands during the handshake

    gate.release.set()
    with pytest.raises(ConnectionError):
        await ensure_task

    # The half-built transport was closed and never published...
    assert host._audio_transport is None
    assert host._audio_stream is None
    # ...and its reset numbering was never recorded.
    assert host._audio_resume_seq == 1234
    for transport in gate.claimed:
        assert transport._udp_transport is None
    sock.close()


@pytest.mark.asyncio
async def test_queued_ensure_after_owner_stop_raises_before_building(
    monkeypatch: MonkeyPatch,
) -> None:
    """A caller that waited on the lock while the owner stopped must fail.

    Pre-fix, the queued caller read the epochs only after acquiring the
    lock — the post-stop values — passed every check and published a live
    transport (keep-alive loops running) after ``disconnect()``.
    """
    gate = _ConnectGate()
    gate.install(monkeypatch)

    host = _EnsureHost()
    sock, port = _reserve_audio_socket()
    host._audio_sock_pending = sock
    host._audio_local_port = port
    host._audio_resume_seq = 1234

    first = asyncio.create_task(host._ensure_audio_transport())
    await asyncio.sleep(0.01)  # first parks inside connect, holding the lock

    second = asyncio.create_task(host._ensure_audio_transport())
    await asyncio.sleep(0.01)  # second parks queued on the lock

    host._owner_stop_epoch += 1  # owner stop lands while both are parked

    gate.release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    # Both callers fail...
    assert all(isinstance(result, ConnectionError) for result in results)
    # ...nothing was published or built...
    assert host._audio_transport is None
    assert host._audio_stream is None
    # ...the carried numbering stands...
    assert host._audio_resume_seq == 1234
    # ...and every transport that was connected has been disconnected.
    for transport in gate.claimed:
        assert transport._udp_transport is None
    sock.close()
