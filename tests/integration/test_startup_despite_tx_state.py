"""MOR-4456: the server starts while PTT/split stay unanswered.

Real startup through the production poller + fake hardware, both vendors:

* Yaesu FTX-1 (bench CAT recording behind the real ``YaesuCatRadio``) and
  FT-991A (CAT simulator + served HTTP): the PTT verb (``TX;``) — and, on
  the FT-991A, the split verb (``FT;``) — stays silent or answers ``?;``
  while the rest answers;
* Icom IC-7300/IC-7610 (serial fakes on a PTY behind the production serial
  backend, poller and web gate): the PTT read (``1C 00``) and the split
  read (``0F``) stay silent or are NAKed while the rest answers.

Every case asserts the same contract: the server starts, the answering
criticals (frequency, mode) arrive, HTTP and WS report TX unknown, the key
is refused with the existing unknown-RF-state reason, and exactly one gate
WARNING names the path. A late answer then clears the warning and the key
path allows again — without ever keying the radio.

No radio transmits here: fakes only, and the key path is proven by the
interlock verdict on live poller state, never by executing a key.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.runtime.tx_interlock import evaluate_tx_interlock
from rigplane.testing.timing import await_until
from rigplane.web.radio_poller import CommandQueueEntry, PttOn, TxInterlockRefusal
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.serial.endpoints import PtyEndpoint, pty_supported
from support.yaesu_ft991a_http import (
    read_state as ft991a_read_state,
    refused_by as ft991a_refused_by,
    serve_ft991a_over_http,
    silent_on as ft991a_silent_on,
    wait_for_state_value as ft991a_wait_for_state_value,
)
from test_web_server import _close_ws, _ws_connect, _ws_recv_frame, _ws_send_text

pytestmark = [
    pytest.mark.mock_integration,
    pytest.mark.skipif(not pty_supported(), reason="requires a PTY"),
]

PTT = FieldPath.global_("tx_state", "ptt")
SPLIT = FieldPath.global_("tx_state", "split")


def _gate_warnings(
    caplog: pytest.LogCaptureFixture, path: FieldPath
) -> list[str]:
    """Gate WARNINGs naming *path*."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and record.name == "rigplane.web.web_startup"
        and str(path) in record.getMessage()
    ]


def _store_shows(store: Any, path: FieldPath, value: object) -> bool:
    """Whether the store currently carries *value* for *path*."""
    try:
        return store.snapshot().field(path).value is value
    except KeyError:
        return False


async def _ws_state_data(port: int) -> dict[str, Any]:
    """First full ``state_update`` baseline a fresh WS client receives."""
    reader, writer, _ = await _ws_connect("127.0.0.1", port, "/api/v1/ws")
    try:
        async with asyncio.timeout(15):
            while True:
                _, payload = await _ws_recv_frame(reader)
                message = json.loads(payload.decode("utf-8"))
                if message.get("type") == "state_update":
                    data = message.get("data")
                    assert isinstance(data, dict), message
                    return data
    finally:
        await _close_ws(writer)


# ---------------------------------------------------------------------------
# Yaesu: FTX-1 bench recording and FT-991A simulator, real backend + gate
# ---------------------------------------------------------------------------


def _ftx1_table_adverse_on_tx(fate: str):
    """Bench FTX-1 recording with every ``TX;`` answer dropped or refused.

    Mirrors the ``FT;`` surgery ``test_startup_state_gate`` pins: silence
    drops the recorded ``TX0;`` replies so the replay never answers the PTT
    read; refusal rewrites them to ``?;``.
    """
    import json as _json

    from rigplane_testkit.yaesu.replay import RecordedCatAnswerTable, reply_closes

    pairs = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "fixtures"
        / "ftx1"
        / "cat_pairs.jsonl"
    )
    rows = [_json.loads(line) for line in pairs.read_text().splitlines() if line]
    kept: list[dict[str, object]] = []
    pending: str | None = None
    for row in rows:
        if row["dir"] == "c2r":
            pending = str(row["line"])
            kept.append(row)
        elif pending == "TX;" and reply_closes(pending, str(row["line"])):
            pending = None
            if fate == "silent":
                continue  # swallow the TX0; reply: TX; stays silent
            kept.append({**row, "line": "?;"})
        else:
            pending = None
            kept.append(row)
    table = RecordedCatAnswerTable.from_cat_pairs(kept)
    tx_answer = table.lookup("TX;")
    assert tx_answer is not None, "bench recording must contain a TX; answer to drop"
    if fate == "silent":
        assert tx_answer.kind == "silence"
    else:
        assert tx_answer.reply == "?;"
    return table


def _gated_web_config() -> WebConfig:
    """Listener config with the production startup gate engaged."""
    return WebConfig(
        host="127.0.0.1",
        port=0,
        keepalive_interval=9999.0,
        await_initial_state=True,
    )


async def _serve_ft991a_gated(
    monkeypatch: pytest.MonkeyPatch, hook: Any
) -> Any:
    """The shared FT-991A harness with the startup gate engaged."""
    return await serve_ft991a_over_http(
        monkeypatch, web_config=_gated_web_config(), command_hook=hook
    )


def _ftx1_table_full():
    """The bench FTX-1 recording unmodified: every read answers."""
    import json as _json

    from rigplane_testkit.yaesu.replay import RecordedCatAnswerTable

    pairs = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "fixtures"
        / "ftx1"
        / "cat_pairs.jsonl"
    )
    rows = [_json.loads(line) for line in pairs.read_text().splitlines() if line]
    return RecordedCatAnswerTable.from_cat_pairs(rows)


async def _assert_served_unknown(host: str, port: int, leaf: str) -> dict[str, Any]:
    """The served state reports *leaf* unknown (null + unobserved)."""
    body = await ft991a_read_state(host, port)
    assert body[leaf] is None, body.get(leaf)
    assert body["fieldStatus"][leaf]["observed"] is False
    return body


async def _assert_yaesu_key_refused(station: Any) -> None:
    """The production Yaesu poller refuses a key on unknown RF state.

    The verdict is the seat both pollers call; the executed refusal proves
    the wiring. Neither reaches the wire.
    """
    from rigplane.web.radio_poller import PttOn

    poller = station.server._state_poller  # noqa: SLF001
    decision = evaluate_tx_interlock(PttOn(), rf_state=poller._current_rf_state())
    assert not decision.allowed
    assert "RF state is unknown" in decision.reason
    with pytest.raises(CommandError, match="RF state is unknown"):
        await poller._execute_command(PttOn())  # noqa: SLF001
    assert "TX1;" not in station.wire, "refused key must not reach the wire"


@pytest.mark.realtime(
    reason="real HTTP/WS socket and the production Yaesu poller/readback path"
)
@pytest.mark.parametrize("profile", ["ft991a", "ftx1"])
@pytest.mark.parametrize("fate", ["silent", "refused"])
async def test_yaesu_unanswered_ptt_still_serves_with_tx_unknown(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    profile: str,
    fate: str,
) -> None:
    """MOR-4456 (Yaesu PTT): a PTT read that never answers still serves.

    ``TX;`` stays silent (or answers ``?;``) while the rest answers, through
    the real backend, its real observation poller and ``WebServer.start()``
    with the startup gate on — on the FT-991A simulator and on the FTX-1
    bench recording. The server starts, the answering criticals arrive, the
    served state reports PTT unknown over HTTP and WS, the key is refused
    with the existing unknown-RF-state reason, and one gate WARNING names
    the path with its cause; a late PTT answer clears it and the key path
    allows again.
    """
    from rigplane_testkit.sim.engine import Ok, Refused
    from rigplane_testkit.yaesu.replay import REFUSAL

    shadow = {"on": True}

    def hook(line: str) -> Any:
        if shadow["on"] and line == "TX;":
            if fate == "silent":
                return (None, Ok())
            return (REFUSAL, Refused("native_refusal"))
        return None

    async def stop() -> None:
        raise AssertionError("stop is assigned per profile below")

    if profile == "ft991a":
        with caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"):
            station = await serve_ft991a_over_http(
                monkeypatch, web_config=_gated_web_config(), command_hook=hook
            )
        stop = station.aclose
        host, port = station.host, station.port

        def wire_has_tx() -> bool:
            return "TX;" in station.wire

        def count_tx() -> int:
            return station.wire.count("TX;")
    else:
        from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
        from rigplane_testkit.yaesu import FakeCatRadio
        from support.yaesu_ft991a_http import Ft991aHttpStation

        full_table = _ftx1_table_full()
        fake = FakeCatRadio(recorded_answers=_ftx1_table_adverse_on_tx(fate))
        await fake.start()
        radio = YaesuCatRadio(device=fake.device_url, profile="ftx1")
        await asyncio.wait_for(radio.connect(), 30)
        config = _gated_web_config()
        config.radio_model = "FTX-1"
        server = WebServer(radio, config)
        try:
            with caplog.at_level(logging.WARNING, logger="rigplane.web.web_startup"):
                await asyncio.wait_for(server.start(), 60)
        except BaseException:
            await server.stop()
            await radio.disconnect()
            await fake.stop()
            raise
        station = Ft991aHttpStation(radio=radio)
        station.server = server
        station.port = server.port

        async def stop() -> None:  # noqa: F811
            await server.stop()
            await radio.disconnect()
            await fake.stop()

        host, port = "127.0.0.1", server.port

        def wire_has_tx() -> bool:
            return "TX;" in fake.commands

        def count_tx() -> int:
            return sum(1 for line in fake.commands if line == "TX;")
    try:
        # Not vacuous: the production poller asked for the PTT read.
        await await_until(wire_has_tx, timeout=20.0, description="poller asks TX;")
        # The answering criticals arrive through the real poller.
        if profile == "ft991a":
            assert await ft991a_wait_for_state_value(host, port, "mode", "USB")
        else:
            from rigplane.backends.yaesu_cat.observations import _MAIN_FREQ

            assert (
                station.server.command_state_store.snapshot()
                .field(_MAIN_FREQ)
                .value
                == 14_074_000
            )
        await _assert_served_unknown(host, port, "ptt")
        assert (await _ws_state_data(port))["ptt"] is None
        await _assert_yaesu_key_refused(station)
        warnings = _gate_warnings(caplog, PTT)
        assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
        # No resolvable profile read for the CAT verb; the silent cause is
        # what the observation path counts (nothing) — "never answered".
        assert "no declared read" in warnings[0], warnings[0]
        assert "never answered" in warnings[0], warnings[0]
        # The poller keeps asking while the gate has released.
        tx_before = count_tx()
        await await_until(
            lambda: count_tx() > tx_before,
            timeout=15.0,
            description="poller keeps asking TX; after release",
        )
        # Phase 2 — the radio answers: the warning clears (no second
        # WARNING names the path), the served state reports PTT, and the
        # key path allows again. Nothing is keyed to prove it.
        if profile == "ft991a":
            shadow["on"] = False
        else:
            fake._recorded = full_table  # noqa: SLF001
        await await_until(
            lambda: _store_shows(
                station.server.command_state_store, PTT, False
            ),
            timeout=30.0,
            description="late TX; answer observed",
        )
        body = await ft991a_read_state(host, port)
        assert body["ptt"] is False
        assert body["fieldStatus"]["ptt"]["observed"] is True

        from rigplane.web.radio_poller import PttOn

        poller = station.server._state_poller  # noqa: SL001
        await await_until(
            lambda: evaluate_tx_interlock(
                PttOn(), rf_state=poller._current_rf_state()
            ).allowed,
            timeout=15.0,
            description="key path allows on fresh RX",
        )
        assert len(_gate_warnings(caplog, PTT)) == 1
    finally:
        await stop()
