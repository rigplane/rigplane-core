"""Scope grant replies for the frontend, produced by the real handler (MOR-4534).

Each case in ``tests/fixtures/scope-control/replies.json`` names the source
state (radio profile, tuned frequency, the radio's streamed window, whether
the last span write is settled or refused) and the subscribe document the
client sends. This test drives the real ``ScopeHandler`` over the real
``ScopeHub`` and ``RadioScopeSource`` with that state and asserts the grant
it sends is the fixture's ``granted`` document, byte for byte after JSON
parsing. The frontend component tests (``SpectrumPanel.component.test.ts``)
feed only these documents, so they never exercise a reply the handler
cannot produce. Regenerate with ``RIGPLANE_UPDATE_SCOPE_REPLIES=1``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from rigplane.core.exceptions import CommandError
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.radio_scope_source import RadioScopeSource
from rigplane.scope import ScopeFrame
from rigplane.testing.timing import await_until
from rigplane.web.handlers.scope import ScopeHandler
from rigplane.web.server import WebServer
from rigplane.ws import WS_OP_TEXT

REPLIES = Path(__file__).parents[1] / "fixtures" / "scope-control" / "replies.json"


class _Writer:
    def __init__(self, case: dict) -> None:
        self._case = case

    async def observed_scope_mode(self, receiver: int) -> int | None:
        return None

    def tuned_frequency(self, receiver: int) -> float:
        return self._case["tunedHz"]

    def span_settled(self, receiver: int) -> bool:
        return self._case["spanSettled"]

    async def set_output(self, enabled: bool, *, release: bool = True) -> None:
        pass

    async def set_fixed(self, edges, receiver: int) -> None:
        pass

    async def set_dual(self, enabled: bool, receiver: int) -> None:
        pass

    async def set_span(self, span_hz: float, receiver: int) -> None:
        pass

    async def set_center(self, center_hz: float, span_hz: float, receiver: int) -> None:
        if self._case.get("refused", False):
            raise CommandError("span refused")


class _Socket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.text: list[str] = []

    async def recv(self):
        message = await self.incoming.get()
        if message is None:
            raise EOFError
        return WS_OP_TEXT, message.encode()

    async def send_text(self, text: str) -> None:
        self.text.append(text)

    async def send_binary(self, data: bytes) -> None:
        pass


def _frame(receiver: int, window: list[int], width: int) -> ScopeFrame:
    return ScopeFrame(receiver, 0, window[0], window[1], bytes(width), False)


def _grants(socket: _Socket) -> list[dict]:
    documents = [json.loads(text) for text in socket.text]
    return [document for document in documents if document.get("type") == "granted"]


async def _reply(case: dict) -> dict:
    """The grant the real handler sends for ``case['subscribe']``."""
    server = WebServer(None)
    source = RadioScopeSource(resolve_radio_profile(model=case["model"]), _Writer(case))
    server.scope_hub.register(case["subscribe"]["sourceId"], source)
    socket = _Socket()
    handler = ScopeHandler(socket, server)
    task = asyncio.create_task(handler.run())
    receiver = case["subscribe"]["receiver"]
    try:
        await await_until(lambda: handler in server._scope_handlers)
        if "native" in case:
            # The radio streams its own window before the request: a passive
            # seq-0 subscription starts the source, then one frame.
            priming = {**case["subscribe"], "requestSeq": 0}
            await socket.incoming.put(json.dumps(priming))
            await await_until(lambda: len(_grants(socket)) == 1)
            source.feed_frame(_frame(receiver, case["native"], case["nativeWidth"]))
        for earlier in case.get("before", []):
            # Requests the client made earlier on the same connection.
            sent = len(_grants(socket))
            await socket.incoming.put(json.dumps(earlier))
            await await_until(lambda: len(_grants(socket)) > sent)
        sent = len(_grants(socket))
        await socket.incoming.put(json.dumps(case["subscribe"]))
        await await_until(lambda: len(_grants(socket)) > sent)
        if "after" in case:
            # A frame after the grant: the handler re-sends the grant when the
            # frame changes its width or request state.
            sent = len(_grants(socket))
            source.feed_frame(_frame(receiver, case["after"], case["afterWidth"]))
            await await_until(lambda: len(_grants(socket)) > sent)
        return _grants(socket)[-1]
    finally:
        await socket.incoming.put(None)
        await task


def _cases() -> dict:
    return json.loads(REPLIES.read_text())


@pytest.mark.parametrize("name", sorted(_cases()))
async def test_reply_fixture_is_what_the_real_handler_sends(name: str) -> None:
    case = _cases()[name]
    granted = await _reply(case["case"])
    if os.environ.get("RIGPLANE_UPDATE_SCOPE_REPLIES") == "1":
        documents = _cases()
        documents[name]["granted"] = granted
        REPLIES.write_text(json.dumps(documents, indent=2) + "\n")
        return
    assert granted == case["granted"]


def test_request_zero_is_always_settled_in_the_fixture() -> None:
    """No fixture reply pairs requestSeq 0 with pending: the handler never does."""
    for case in _cases().values():
        granted = case["granted"]
        if granted.get("requestSeq") == 0:
            assert granted["requestState"] == "settled"
