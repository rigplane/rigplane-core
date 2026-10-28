from __future__ import annotations

import asyncio
from collections import deque

import pytest
from integration.test_tx_under_fault_matrix import (
    _FrozenClock,
    _WsKeyClient,
    _http_json,
    _stand_up_stack,
    _tear_down_stack,
    _wait_ptt,
    _wait_ptt_observed,
    _wait_released,
)

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.runtime import radio as radio_module
from rigplane.runtime._civ_rx import _PttReadContext
from rigplane.runtime.radio import CoreRadio

pytestmark = pytest.mark.mock_integration


@pytest.fixture
async def api(tmp_path, monkeypatch):
    stack = await _stand_up_stack(
        tmp_path, loss="hold", tot_seconds=30, unsolicited=False
    )
    client = None
    try:
        await _wait_ptt_observed(stack.web, 20)
        stack.web._controller._clock = _FrozenClock()
        status, grant = await _http_json(
            stack.web.port, "POST", "/api/v1/controller", {"kind": "pro"}
        )
        assert status == 201
        client = _WsKeyClient("127.0.0.1", stack.web.port, grant["controller_key"])
        await client.connect()
        stack.radio._civ_get_timeout = 0.5
        monkeypatch.setattr(radio_module, "TX_READ_DEADLINE_SECONDS", 1)
        monkeypatch.setattr(CoreRadio, "_PTT_READBACK_SETTLE_S", 0.001)
        script = {
            "case": None,
            "armed": False,
            "b": None,
            "a_owner": lambda: True,
            "held": [],
        }
        read_sent, off_sent = asyncio.Event(), asyncio.Event()
        runtime, pending = stack.radio._civ_runtime, deque()
        async with asyncio.timeout(5):
            while not runtime._ptt_reply_ledger.eligible():
                await asyncio.sleep(0)
        note_dispatch = runtime._note_ptt_dispatch

        def observe(frame, is_current=None):
            note_dispatch(frame, is_current)
            if frame.command == 0x1C and frame.sub == 0 and not frame.data:
                entry = runtime._ptt_reply_ledger._owed[-1]
                pending.append(is_current)
                if is_current is script["a_owner"]:
                    script["a"] = entry
                if isinstance(is_current, _PttReadContext):
                    script["b"] = entry

        monkeypatch.setattr(runtime, "_note_ptt_dispatch", observe)
        dispatch = stack.sim._dispatch_civ

        def scripted(cmd, payload, sender):
            response = dispatch(cmd, payload, sender)
            owner = pending.popleft() if cmd == 0x1C and payload == b"\x00" else None
            if script["armed"] and cmd == 0x1C:
                if payload == b"\x00\x00":
                    off_sent.set()
                    return None
                if payload == b"\x00":
                    if isinstance(owner, _PttReadContext) or owner is script["a_owner"]:
                        read_sent.set()
                    if script["case"] == "earlier-silent-read" or (
                        script["case"] == "key-during-confirmation"
                        and script["b"] is not None
                    ):
                        if not isinstance(owner, _PttReadContext):
                            script["held"].append(response)
                        return None
            return response

        monkeypatch.setattr(stack.sim, "_dispatch_civ", scripted)
        yield stack, client, script, read_sent, off_sent
    finally:
        if client is not None:
            await client.close()
        await _tear_down_stack(stack)


@pytest.mark.parametrize(
    "case", ["clean-rx", "earlier-silent-read", "key-during-confirmation"]
)
async def test_release_readback_through_http_and_websocket(api, case, monkeypatch):
    stack, client, script, read_sent, off_sent = api
    runtime, authority = stack.radio._civ_runtime, stack.composition.authority
    assert await client.key("first")
    await _wait_ptt(stack.sim, True, 1)
    script["armed"] = True
    script["case"] = case
    if case == "earlier-silent-read":
        ledger = runtime._ptt_reply_ledger
        await runtime.execute_civ_raw(
            build_civ_frame(stack.radio._radio_addr, CONTROLLER_ADDR, 0x1C, sub=0),
            wait_response=False,
            is_current=script["a_owner"],
        )
        a = script["a"]
        monkeypatch.setattr(ledger, "_clock", lambda: a.dispatched_at)
        monkeypatch.setattr(authority._wakeup, "_clock", ledger._clock)
        monkeypatch.setattr(authority, "_clock", ledger._clock)
        await asyncio.wait_for(read_sent.wait(), 1)
        read_sent.clear()
    assert (await client.command("unkey", "ptt_off")).get("ok") is True
    await asyncio.wait_for(off_sent.wait(), 1)
    if case == "key-during-confirmation":
        await asyncio.wait_for(read_sent.wait(), 5)
        b = script["b"]
        assert b is not None and b.answered_by is None
        response = await asyncio.wait_for(client.command("held", "ptt_on"), 0.2)
        assert response.get("ok") is False
        assert authority.snapshot_nowait().state.release_required
        frame = parse_civ_frame(
            build_civ_frame(
                CONTROLLER_ADDR, stack.radio._radio_addr, 0x1C, sub=0, data=b"\x00"
            )
        )
        await runtime._route_civ_frame(frame, generation=stack.radio._civ_epoch)
        assert b.answered_by is frame
        script["case"] = None
        for reply in script["held"]:
            await runtime._route_civ_frame(
                parse_civ_frame(reply), generation=stack.radio._civ_epoch
            )
    assert stack.sim.ptt_on is False
    if case == "earlier-silent-read":
        async with asyncio.timeout(1):
            while authority.snapshot_nowait().state.pending_effect:
                await asyncio.sleep(0)
        assert authority.snapshot_nowait().state.release_required
        assert not await client.key("blocked")
        assert authority._retry_due is not None
        authority._retry_due = authority._clock()
        off_sent.clear()
        await authority._process_due()
        assert off_sent.is_set()
        assert authority.snapshot_nowait().state.release_required
        assert not await client.key("blocked-after-retry")
        assert any(entry is a for entry in ledger._owed) and a.answered_by is None
        assert script["b"] is None
    else:
        await _wait_released(stack.composition)
        script["armed"] = False
        assert await client.key("next")
        await _wait_ptt(stack.sim, True, 1)
    assert [on for _, on in stack.sim.ptt_set_writes].count(True) == (
        1 if case == "earlier-silent-read" else 2
    )
    assert any(not on for _, on in stack.sim.ptt_set_writes)
