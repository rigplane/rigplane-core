"""Characterize readback counters with a stale replay and an HTTP writer."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from scripts.soak import soak_driver

BASE_URL = "http://127.0.0.1:8080"
RIGCTLD_URL = "127.0.0.1:4532"
STATE_URL = f"{BASE_URL}/api/v1/state"
COMMANDS_URL = f"{BASE_URL}/api/v1/commands"

FREQ_A = 14_074_000  # _TUNE_FREQS[0], the cached/replayed ("old") target
FREQ_B = 14_074_500  # _TUNE_FREQS[1], the interleaved HTTP ("new") target
FREQ_C = 7_074_000  # _TUNE_FREQS[2], posted by the follow-up action
MODE_A = "USB"  # _MODES[0]
MODE_B = "LSB"  # _MODES[1]


class _FakeRadioState:
    """Synchronous fake radio state shared by the HTTP and rigctld fakes."""

    def __init__(self) -> None:
        self.freq = FREQ_A
        self.mode = MODE_A


class _FakeHttpResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self.status = 200
        self._body = body

    async def __aenter__(self) -> _FakeHttpResponse:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return self._body


class _FakeHttpSession:
    """Stands in for aiohttp.ClientSession; POSTs mutate state immediately."""

    def __init__(self, state: _FakeRadioState) -> None:
        self._state = state
        self.get_urls: list[str] = []
        self.get_observations: list[tuple[int, str]] = []
        self.post_calls: list[tuple[str, str, dict[str, Any]]] = []

    def get(self, url: str, timeout: Any = None) -> _FakeHttpResponse:
        self.get_urls.append(url)
        self.get_observations.append((self._state.freq, self._state.mode))
        body = {"main": {"freqHz": self._state.freq, "mode": self._state.mode}}
        return _FakeHttpResponse(body)

    def post(
        self,
        url: str,
        json: dict[str, Any] | None = None,
        timeout: Any = None,
    ) -> _FakeHttpResponse:
        assert json is not None
        self.post_calls.append((url, json["name"], json["params"]))
        params = json["params"]
        if json["name"] == "set_freq":
            self._state.freq = params["freq"]
        elif json["name"] == "set_mode":
            self._state.mode = params["mode"]
        return _FakeHttpResponse({"ok": True})


class _FakeRigctldWriter:
    """Applies F/M to the fake state and feeds protocol replies to the reader."""

    def __init__(
        self,
        state: _FakeRadioState,
        reader: asyncio.StreamReader,
        commands: list[str],
    ) -> None:
        self._state = state
        self._reader = reader
        self._commands = commands
        self.closed = False

    def write(self, data: bytes) -> None:
        cmd = data.decode("ascii").strip()
        self._commands.append(cmd)
        if cmd.startswith("F "):
            self._state.freq = int(cmd.split()[1])
            self._reader.feed_data(b"RPRT 0\n")
        elif cmd == "f":
            self._reader.feed_data(f"{self._state.freq}\n".encode("ascii"))
        elif cmd.startswith("M "):
            self._state.mode = cmd.split()[1]
            self._reader.feed_data(b"RPRT 0\n")
        elif cmd == "m":
            self._reader.feed_data(f"{self._state.mode}\n0\n".encode("ascii"))
        elif cmd == "t":
            self._reader.feed_data(b"0\n")
        else:
            self._reader.feed_data(b"RPRT 1\n")

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _make_driver(session: _FakeHttpSession) -> soak_driver.SoakDriver:
    driver = soak_driver.SoakDriver(
        config=soak_driver.SoakConfig(duration_s=60.0),
        base_url=BASE_URL,
        rigctld_url=RIGCTLD_URL,
        sim=cast(Any, None),  # unused by the methods under test
    )
    driver._session = cast(Any, session)
    driver._connection_state = "connected"
    driver._last_freq = FREQ_A  # cached "old" target (index 0)
    driver._last_mode = MODE_A
    driver._freq_i = 1  # next operator action HTTP-sets index 1 (B)
    driver._mode_i = 1
    return driver


@pytest.mark.parametrize("kind", ["freq", "mode"])
@pytest.mark.parametrize("interference", [True, False], ids=["interleaved", "control"])
async def test_rigctld_replay_interleaving_readback_mismatch(
    kind: str,
    interference: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _FakeRadioState()
    session = _FakeHttpSession(state)
    driver = _make_driver(session)
    commands: list[str] = []
    connect_calls: list[tuple[str, int]] = []
    writers: list[_FakeRigctldWriter] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_open_connection(host: str, port: int) -> tuple[Any, Any]:
        connect_calls.append((host, port))
        reader = asyncio.StreamReader()
        writer = _FakeRigctldWriter(state, reader, commands)
        writers.append(writer)
        if interference:
            # Barrier: replay has already captured both stale targets A into
            # its locals, but has not sent the first F yet.
            entered.set()
            await asyncio.wait_for(release.wait(), timeout=5.0)
        return reader, writer

    monkeypatch.setattr(soak_driver.asyncio, "open_connection", fake_open_connection)

    action = driver._tune_once if kind == "freq" else driver._mode_once
    key = "readbackMismatches" if kind == "freq" else "modeReadbackMismatches"
    other = "modeReadbackMismatches" if kind == "freq" else "readbackMismatches"

    task: asyncio.Task[None] | None = None
    try:
        if interference:
            task = asyncio.create_task(driver._rigctld_replay_once())
            await asyncio.wait_for(entered.wait(), timeout=2.0)
            # Real driver action while replay is parked: reads initial A
            # (matches cached A), then HTTP-sets B and caches B.
            await action()
            release.set()  # replay resumes and writes stale A over B
            await asyncio.wait_for(task, timeout=2.0)
            task = None
        else:
            # Positive control: full replay of A FIRST, then HTTP-set B.
            await asyncio.wait_for(driver._rigctld_replay_once(), timeout=2.0)
            await action()

        mismatches_before = driver._counts[key]
        await action()  # next real driver action observes the outcome
        mismatches_after = driver._counts[key]
    finally:
        release.set()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert mismatches_before == 0
    if interference:
        # Stale replay restored A after HTTP set B: observing A against
        # cached B is counted as exactly one selected mismatch.
        assert mismatches_after == 1
    else:
        # Control: state B matches cached B, no mismatch is counted.
        assert mismatches_after == 0
    assert driver._counts[other] == 0  # ONLY the selected counter moves

    assert driver._counts["rigctldReplays"] == 1
    assert driver._counts["rigctldFailures"] == 0
    assert driver._counts["rigctldSkipped"] == 0
    if kind == "freq":
        assert driver._counts["tunes"] == 2
        assert driver._counts["tuneFailures"] == 0
        assert driver._counts["readbackUnobserved"] == 0
        expected_posts = [
            (COMMANDS_URL, "set_freq", {"freq": FREQ_B}),
            (COMMANDS_URL, "set_freq", {"freq": FREQ_C}),
        ]
        expected_obs = (
            [(FREQ_A, MODE_A), (FREQ_A, MODE_A)]
            if interference
            else [(FREQ_A, MODE_A), (FREQ_B, MODE_A)]
        )
    else:
        assert driver._counts["modeChanges"] == 2
        assert driver._counts["modeFailures"] == 0
        assert driver._counts["modeReadbackUnobserved"] == 0
        expected_posts = [
            (COMMANDS_URL, "set_mode", {"mode": MODE_B}),
            (COMMANDS_URL, "set_mode", {"mode": MODE_A}),
        ]
        expected_obs = (
            [(FREQ_A, MODE_A), (FREQ_A, MODE_A)]
            if interference
            else [(FREQ_A, MODE_A), (FREQ_A, MODE_B)]
        )

    # Exact captured rigctld targets (stale A in both orders) and command order.
    assert commands == [f"F {FREQ_A}", "f", f"M {MODE_A} 0", "m", "t"]
    assert session.post_calls == expected_posts
    assert session.get_observations == expected_obs
    assert session.get_urls == [STATE_URL, STATE_URL]

    # All endpoints are loopback fakes; the replay writer was closed.
    assert connect_calls == [("127.0.0.1", 4532)]
    for url in [*session.get_urls, *[call[0] for call in session.post_calls]]:
        assert url.startswith("http://127.0.0.1:")
    assert len(writers) == 1
    assert writers[0].closed
