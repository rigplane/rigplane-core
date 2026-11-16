"""The remote E2E sidecar exposes the simulator's own PTT journal (MOR-4493).

``tests/e2e/remote_web_server.py`` serves ``GET /sim`` to the Playwright
rows. The PTT transitions come from ``MockIcomRadio.ptt_transitions`` (the
simulator records every real flip), never from a sampling observer: a key
and unkey inside one sampling interval must still show both transitions.
The wire shape is the producer's frozen ``SimSnapshot``; the Node consumer
(frontend/tests/e2e/remote/station.ts ``parseSimState``) checks responses
against the annotations served at ``/sim/contract``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from testkit.ic7610_fakes import ic7610_lan_fake

_SERVER_PATH = Path(__file__).resolve().parents[1] / "e2e" / "remote_web_server.py"

# What frontend/tests/e2e/remote/station.ts SIM_CONTRACT expects; the two
# literals change together or this test and the Node parser both fail.
_CONSUMER_CONTRACT = {
    "ptt_on": "bool",
    "freq_hz": "int",
    "ptt_transitions": "tuple[tuple[float, bool], ...]",
}


def _load_server() -> ModuleType:
    spec = importlib.util.spec_from_file_location("remote_web_server", _SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves string annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.realtime(reason="asserts the journal's stamps are on the wall clock")
def test_key_and_unkey_inside_one_sampling_interval_record_both_transitions() -> None:
    server = _load_server()
    sim = ic7610_lan_fake(stateful_ptt=True)
    before = time.time()
    # Back to back, microseconds apart: a 25 ms sampler would see only OFF.
    sim._set_simulated_ptt(True, wire_set=False)  # noqa: SLF001
    sim._set_simulated_ptt(False, wire_set=False)  # noqa: SLF001
    after = time.time()

    snapshot = server.sim_snapshot(sim)

    assert snapshot.ptt_on is False
    assert [keyed for _, keyed in snapshot.ptt_transitions] == [True, False]
    stamps = [stamp for stamp, _ in snapshot.ptt_transitions]
    assert stamps == sorted(stamps)
    # Wall-clock seconds on the clock the Node side compares.
    assert all(before - 0.5 <= stamp <= after + 0.5 for stamp in stamps)


def test_contract_matches_the_dataclass_and_the_consumer() -> None:
    server = _load_server()
    sim = ic7610_lan_fake(stateful_ptt=True)

    import dataclasses

    payload = dataclasses.asdict(server.sim_snapshot(sim))

    assert server.sim_contract() == _CONSUMER_CONTRACT
    assert set(payload) == set(_CONSUMER_CONTRACT)


def test_sidecar_serves_the_snapshot_and_its_contract_over_http() -> None:
    server = _load_server()
    sim = ic7610_lan_fake(stateful_ptt=True)
    sim._set_simulated_ptt(True, wire_set=False)  # noqa: SLF001
    sim._set_simulated_ptt(False, wire_set=False)  # noqa: SLF001

    async def get(port: int, path: str) -> Any:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        await writer.drain()
        raw = await reader.read()
        writer.close()
        head, _, body = raw.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 200")
        return json.loads(body)

    async def scenario() -> tuple[Any, Any]:
        sidecar = server._SimSidecar(sim)  # noqa: SLF001
        listener = await sidecar.serve("127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        try:
            return await get(port, "/sim"), await get(port, "/sim/contract")
        finally:
            listener.close()

    state, contract = asyncio.run(scenario())

    assert contract == _CONSUMER_CONTRACT
    assert set(state) == set(_CONSUMER_CONTRACT)
    assert state["ptt_on"] is False
    assert [keyed for _, keyed in state["ptt_transitions"]] == [True, False]
    assert isinstance(state["freq_hz"], int)
