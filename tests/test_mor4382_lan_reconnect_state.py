"""MOR-4382: a full LAN reconnect behaves exactly like a fresh connect.

Production stack over the LAN simulator: ``IcomRadio`` (auto reconnect on) +
``WebServer``, driven through the product's own entry points: HTTP
``/api/v1/radio/disconnect`` and ``/connect``, HTTP commands, the browser's
``audio_start`` over ``/api/v1/audio``, and ``/api/v1/state``. After a full
reconnect the published state must hold the frequency and the mode again, a
command must work and the audio start must not fail.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import aiohttp
import pytest

from rigplane.profiles import RadioProfile, resolve_radio_profile
from rigplane.runtime.radio import IcomRadio
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom import MockIcomRadio
from rigplane_testkit.sim.serve import build_lan_personality
from test_web_server import _http_get, _ws_connect, _ws_recv_frame, _ws_send_text
from test_web_split_writes import _request
from testkit.ic705_fakes import ic705_lan_fake

_DEADLINE_S = 40.0

_REALTIME = pytest.mark.realtime(reason="real UDP simulator, real reconnect timing")


class _SilentIdentity(MockIcomRadio):
    """A radio that never answers the ``19 00`` identity read."""

    def _dispatch_civ(self, cmd: int, payload: bytes, from_addr: int) -> bytes | None:
        if cmd == 0x19 and payload == b"\x00":
            return None
        return super()._dispatch_civ(cmd, payload, from_addr)


async def _get_json(web: WebServer, path: str) -> dict[str, Any]:
    status, _, body = await _http_get("127.0.0.1", web.port, path)
    assert status == 200
    return json.loads(body)


async def _public_state(web: WebServer) -> dict[str, Any]:
    return await _get_json(web, "/api/v1/state")


async def _wait_state(
    web: WebServer,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    deadline_s: float,
    what: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + deadline_s
    state = await _public_state(web)
    while not predicate(state):
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"timed out after {deadline_s}s waiting for: {what}; "
                f"freq={state['main']['vfoA']['freqHz']!r} "
                f"mode={state['main']['mode']!r} "
                f"connection={state.get('connection')!r}"
            )
        await asyncio.sleep(0.2)
        state = await _public_state(web)
    return state


@contextlib.asynccontextmanager
async def _counting_stack(
    sim: MockIcomRadio, *, model: str
) -> AsyncIterator[tuple[WebServer, IcomRadio, MockIcomRadio, list[float]]]:
    """Like ``_stack``, but records every ``_fetch_initial_state`` call time.

    The late-connect tail (``radio.py``) and the web reconnect refetch
    (``server.py``) both call the same radio method, so one counter on the
    radio instance sees every initial fetch on every path.
    """
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="mor4382",
        password="mor4382",
        timeout=1.0,
        model=model,
        auto_reconnect=True,
        reconnect_delay=0.5,
    )
    fetch_at: list[float] = []
    orig_fetch = radio._fetch_initial_state

    async def _counting_fetch() -> None:
        fetch_at.append(time.monotonic())
        await orig_fetch()

    setattr(radio, "_fetch_initial_state", _counting_fetch)
    web: WebServer | None = None
    try:
        await radio.connect()
        web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
        await web.start()
        yield web, radio, sim, fetch_at
    finally:
        if web is not None:
            await web.stop()
        with contextlib.suppress(Exception):
            await radio.disconnect()
        await sim.stop()


async def _wait_fetch_quiescent(
    fetch_at: list[float], *, quiet_s: float, deadline_s: float
) -> None:
    """Wait until at least one fetch ran and none started for ``quiet_s``."""
    deadline = time.monotonic() + deadline_s
    while True:
        if fetch_at and time.monotonic() - fetch_at[-1] >= quiet_s:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"no quiescent fetch sequence after {deadline_s}s "
                f"({len(fetch_at)} fetches so far)"
            )
        await asyncio.sleep(0.2)


@contextlib.asynccontextmanager
async def _stack(
    sim: MockIcomRadio, *, model: str
) -> AsyncIterator[tuple[WebServer, IcomRadio, MockIcomRadio]]:
    """The ``rigplane web`` order: connect first, then serve the web layer."""
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="mor4382",
        password="mor4382",
        timeout=1.0,
        model=model,
        auto_reconnect=True,
        reconnect_delay=0.5,
    )
    web: WebServer | None = None
    try:
        await radio.connect()
        web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
        await web.start()
        yield web, radio, sim
    finally:
        if web is not None:
            await web.stop()
        with contextlib.suppress(Exception):
            await radio.disconnect()
        await sim.stop()


def _ic705() -> contextlib.AbstractAsyncContextManager[Any]:
    sim = ic705_lan_fake(single_owner=True, keepalive_hold_s=30.0)
    return _stack(sim, model="IC-705")


def _has_freq_and_mode(state: dict[str, Any]) -> bool:
    return (
        state["main"]["vfoA"]["freqHz"] is not None
        and state["main"]["mode"] is not None
    )


def _ready_with_freq_and_mode(state: dict[str, Any]) -> bool:
    conn = state["connection"]
    return (
        conn["rigConnected"] is True
        and conn["radioReady"] is True
        and _has_freq_and_mode(state)
    )


async def _assert_recovered(web: WebServer, freq_hz: int) -> None:
    """freq/mode are published again and an HTTP command works."""
    await _wait_state(
        web,
        _ready_with_freq_and_mode,
        deadline_s=_DEADLINE_S,
        what="ready, with freq and mode re-acquired and published",
    )
    status, result = await _request(web, "http", "set_freq", {"freq": freq_hz})
    assert status == 200
    assert result["ok"] is True
    await _wait_state(
        web,
        lambda s: s["main"]["vfoA"]["freqHz"] == freq_hz,
        deadline_s=10.0,
        what="the commanded frequency is published",
    )


async def _post_radio(web: WebServer, action: str) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{web.port}/api/v1/radio/{action}",
            data=b"",
            timeout=aiohttp.ClientTimeout(total=30.0),
        ) as resp:
            assert resp.status == 200


@pytest.mark.asyncio
@_REALTIME
async def test_first_connect_to_a_radio_silent_on_civ_completes_when_it_answers() -> (
    None
):
    """The first connect (the ``rigplane web`` order) must not abort.

    The IC-705 can hold a stale session and answer nothing on CI-V for a few
    seconds after the link opens. ``connect()`` used to abort with "radio is
    not fully ready" and take the whole startup down; it must return, and the
    post-connect tail (initial state, VFO selection) must run once the radio
    answers, so freq/mode appear and a command works.
    """
    sim = ic705_lan_fake(
        single_owner=True, keepalive_hold_s=30.0, civ_silent_after_open_s=7.0
    )
    async with _stack(sim, model="IC-705") as (web, _r, _s):
        await _assert_recovered(web, 7_074_000)


@pytest.mark.asyncio
@_REALTIME
async def test_silent_start_completes_even_when_the_radio_ignores_the_identity_read() -> (
    None
):
    """CI-V readiness, not the ``19 00`` answer, completes the connect.

    The radio answers ordinary CI-V late and never answers ``19 00``: after the
    identity retries are spent the connect tail must still have run, so
    freq/mode appear and commands work.
    """
    sim = ic705_lan_fake(
        cls=_SilentIdentity,
        single_owner=True,
        keepalive_hold_s=30.0,
        civ_silent_after_open_s=3.0,
    )
    async with _stack(sim, model="IC-705") as (web, _r, _s):
        await _assert_recovered(web, 7_074_000)


@pytest.mark.asyncio
@_REALTIME
@pytest.mark.parametrize("model", ["IC-9700", "IC-7610"])
async def test_late_first_answer_fetches_initial_state_exactly_once(
    model: str,
) -> None:
    """One late first CI-V answer completes the connect with a single fetch.

    The radio answers nothing for 3 s after login, so ``connect()`` returns
    with the tail pending and the web registers its reconnect callback before
    the first answer arrives. The tail's fetch and the callback's refetch must
    not both run: exactly one initial fetch, then freq/mode published.
    """
    # The personality builder the smoke uses: the profile drives the CI-V
    # address, the receiver layout and the identity answer, so the sim
    # answers the profile's own state queries.
    profile: RadioProfile = resolve_radio_profile(model=model)
    sim = build_lan_personality(profile, civ_silent_after_open_s=3.0)
    async with _counting_stack(sim, model=model) as (web, _radio, _sim, fetch_at):
        await _wait_state(
            web,
            _ready_with_freq_and_mode,
            deadline_s=_DEADLINE_S,
            what="ready, with freq and mode after the late first answer",
        )
        await _wait_fetch_quiescent(fetch_at, quiet_s=4.0, deadline_s=30.0)
        assert len(fetch_at) == 1, (
            f"initial state fetch ran {len(fetch_at)} times "
            f"after one late first answer (model {model})"
        )


@pytest.mark.asyncio
@_REALTIME
@pytest.mark.parametrize("model", ["IC-9700", "IC-7610"])
async def test_instant_first_answer_fetches_initial_state_exactly_once(
    model: str,
) -> None:
    """Regression guard: an instantly answering radio also fetches exactly once."""
    profile = resolve_radio_profile(model=model)
    sim = build_lan_personality(profile)
    async with _counting_stack(sim, model=model) as (web, _radio, _sim, fetch_at):
        await _wait_state(
            web,
            _ready_with_freq_and_mode,
            deadline_s=_DEADLINE_S,
            what="ready, with freq and mode after an instant answer",
        )
        await _wait_fetch_quiescent(fetch_at, quiet_s=4.0, deadline_s=30.0)
        assert len(fetch_at) == 1, (
            f"initial state fetch ran {len(fetch_at)} times "
            f"after one instant first answer (model {model})"
        )


@pytest.mark.asyncio
@_REALTIME
async def test_civ_stream_stop_recovers_freq_and_mode() -> None:
    """The CI-V stream stops for 3 s and comes back."""
    async with _ic705() as (web, _radio, sim):
        await _wait_state(web, _has_freq_and_mode, deadline_s=20.0, what="initial")
        sim.civ_standby(True)
        await asyncio.sleep(3.0)
        sim.civ_standby(False)
        await _assert_recovered(web, 7_074_000)


@pytest.mark.asyncio
@_REALTIME
async def test_resident_reconnect_loop_republishes_freq_and_mode() -> None:
    """The radio vanishes until the lifecycle gives up and the resident loop
    logs in again (the Wi-Fi blip of MOR-4382)."""
    async with _ic705() as (web, radio, sim):
        await _wait_state(web, _has_freq_and_mode, deadline_s=20.0, what="initial")
        # One soft attempt, then the lifecycle gives up and hands over to the
        # resident reconnect loop.
        radio._session_lifecycle._max_recovery_attempts = 1
        radio._session_lifecycle._recovery_backoff_s = (0.0,)
        sim.reboot(at=0.0, down_for=20.0, busy_for=0.0)
        await _wait_state(
            web,
            lambda s: s["connection"]["rigConnected"] is False,
            deadline_s=20.0,
            what="the outage is published",
        )
        await _assert_recovered(web, 7_074_000)


@pytest.mark.asyncio
@_REALTIME
async def test_owner_disconnect_connect_republishes_freq_and_mode() -> None:
    """DISCONNECT then CONNECT in the UI behaves like a fresh connect."""
    async with _ic705() as (web, _radio, _sim):
        await _wait_state(web, _has_freq_and_mode, deadline_s=20.0, what="initial")
        await _post_radio(web, "disconnect")
        await _wait_state(
            web,
            lambda s: s["connection"]["rigConnected"] is False,
            deadline_s=10.0,
            what="disconnected",
        )
        await _post_radio(web, "connect")
        await _assert_recovered(web, 14_074_000)


@pytest.mark.asyncio
@_REALTIME
async def test_browser_audio_start_right_after_a_reconnect_does_not_fail() -> None:
    """The browser's ``audio_start`` lands right after a reconnect while the
    radio has not answered its ``19 00`` yet.

    The RX start used to be refused ("commands are refused until the radio
    answers its 19 00 model identification") and the UI raised the "Radio
    audio capture could not start" toast. Now audio flows and the published
    audio-receive failure stays empty.
    """
    sim = _SilentIdentity(single_owner=True, keepalive_hold_s=30.0, enable_audio=True)
    async with _stack(sim, model="IC-7610") as (web, _radio, _sim):
        await _post_radio(web, "disconnect")
        await _post_radio(web, "connect")
        reader, writer, _ = await _ws_connect("127.0.0.1", web.port, "/api/v1/audio")
        try:
            await _ws_send_text(
                writer,
                json.dumps(
                    {
                        "type": "audio_start",
                        "direction": "rx",
                        "preferred_rx_codec": "pcm16",
                        "client_id": "mor4382",
                    }
                ),
            )
            binary_frames = 0
            deadline = time.monotonic() + 15.0
            while binary_frames == 0 and time.monotonic() < deadline:
                opcode, payload = await _ws_recv_frame(reader, timeout=10.0)
                if opcode == 0x1:
                    message = json.loads(payload)
                    assert message.get("type") != "error", message
                elif opcode == 0x2:
                    binary_frames += 1
            assert binary_frames > 0, "no RX audio reached the browser"
        finally:
            writer.close()
        info = await _get_json(web, "/api/v1/info")
        assert info["audioReceive"]["failure"] is None
