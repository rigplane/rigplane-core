import ast
import asyncio
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.core.radio_protocol import ManagedTxApi
from rigplane.core.state_store import StateStore
from rigplane.core.tx_safety import TxOwner, TxSource
from rigplane.cli import _cmd_web
from rigplane.runtime.managed_tx_effect_lane import ManagedTxActuator
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.runtime.radio import (
    ManagedTxComposition,
    ManagedTxCompositionPort,
    ManagedTxProviderEvent,
    install_managed_tx_composition,
)
from rigplane.radio_state import RadioState
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import attach_managed_tx_composition


class FakeActuator:
    async def actuate(
        self,
        token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult:
        return ActuationResult.ACCEPTED


class _FakeSocket:
    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", 4242)


class _FakeAsyncServer:
    sockets = [_FakeSocket()]

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


class _BoundaryAwareObservationPoller:
    def __init__(self) -> None:
        self._boundary = None
        self.started = False

    @property
    def managed_queue_boundary(self):
        return self._boundary

    def attach_managed_queue_boundary(self, boundary, *, required: bool) -> None:
        assert required
        self._boundary = boundary

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False


class _ManagedObservationRadio:
    backend_id = model = "managed-observation-test"
    capabilities: set[str] = set()
    connected = control_connected = radio_ready = True

    def __init__(self, poller: object) -> None:
        self.radio_state = RadioState()
        self.state_store = StateStore()
        self.poller = poller

    def supports_command(self, _name: str, *, receiver: int | None = None) -> bool:
        del receiver
        return False

    def create_observation_poller(self, **_kwargs: object) -> object:
        return self.poller


def test_source_constructor_census_is_one_graph_and_zero_reachable_predecessor() -> (
    None
):
    source_root = Path(__file__).parents[1] / "src" / "rigplane"
    constructors = {
        "ManagedTxAuthority": [],
        "TxAbortFence": [],
        "ManagedTxEffectLane": [],
        "LocalTxWorkRunner": [],
        "ManagedRadioRuntime": [],
        "TxSafetySupervisor": [],
    }
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in constructors
            ):
                constructors[node.func.id].append(
                    path.relative_to(source_root).as_posix()
                )

    assert constructors["ManagedTxAuthority"] == ["runtime/radio.py"]
    assert constructors["TxAbortFence"] == ["runtime/radio.py"]
    assert constructors["ManagedTxEffectLane"] == ["runtime/radio.py"]
    assert constructors["LocalTxWorkRunner"] == ["runtime/radio.py"]
    assert constructors["ManagedRadioRuntime"] == []
    # Phase 3 removes this dead legacy implementation. Phase 1 proves that no
    # production root can construct the ManagedRadioRuntime that reaches it.
    assert constructors["TxSafetySupervisor"] == ["runtime/managed_radio_runtime.py"]


@pytest.mark.asyncio
async def test_managed_web_entrypoints_fail_closed_before_startup_side_effects(
    tmp_path, capsys
) -> None:
    assert await _cmd_web(LegacyRadio(), SimpleNamespace()) == 1
    assert "managed TX composition is required" in capsys.readouterr().err

    server = WebServer(
        None,
        WebConfig(managed_tx_required=True, discovery=False),
    )
    attachments: list[str] = []
    server._attach_audio_session_listener = lambda: attachments.append("audio")
    server._attach_reconnect_status_listener = lambda: attachments.append("reconnect")
    with pytest.raises(RuntimeError, match="managed TX composition is required"):
        await server.start()
    assert attachments == []

    composition = ManagedTxComposition(
        FakeActuator(), config_path=tmp_path / "managed-tx.json"
    )
    attach_managed_tx_composition(server, composition, ManagedTxProviderEvent(1, 1))
    with pytest.raises(RuntimeError, match="managed TX provider must be active"):
        await server.start()
    assert attachments == []
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_builds_one_authority_fence_lane_and_tot_store(tmp_path) -> None:
    actuator = FakeActuator()
    composition = ManagedTxComposition(
        actuator,
        config_path=tmp_path / "managed-tx.json",
    )
    assert isinstance(actuator, ManagedTxActuator)
    assert isinstance(composition, ManagedTxCompositionPort)
    assert composition.authority._lane is composition._lane
    assert composition.authority._abort_fence is composition.abort_fence
    assert composition.authority._config_store is composition._config_store
    assert composition._lane._actuator is actuator
    assert composition.local_tx_work_runner._fence is composition.abort_fence
    server = SimpleNamespace()
    attach_managed_tx_composition(server, composition, ManagedTxProviderEvent(1, 1))
    assert server._production_managed_tx_port is composition

    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_managed_boundary_attaches_by_identity_before_listener(tmp_path) -> None:
    event = ManagedTxProviderEvent(1, 1)
    composition = ManagedTxComposition(
        FakeActuator(), config_path=tmp_path / "managed-tx.json"
    )
    await composition.activate_provider(event)
    poller = _BoundaryAwareObservationPoller()
    radio = _ManagedObservationRadio(poller)
    server = WebServer(
        radio,
        WebConfig(
            host="127.0.0.1",
            port=0,
            discovery=False,
            managed_tx_required=True,
        ),
    )
    attach_managed_tx_composition(server, composition, event)

    async def start_listener(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        assert (
            poller.managed_queue_boundary is server._production_managed_queue_boundary
        )
        assert poller.managed_queue_boundary.port is composition
        assert not poller.started
        return _FakeAsyncServer()

    try:
        with patch(
            "rigplane.web.web_startup.asyncio.start_server",
            new=AsyncMock(side_effect=start_listener),
        ):
            await server.start()
            await asyncio.sleep(0)
        assert poller.started
        await server.stop()
    finally:
        await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_managed_startup_rejects_missing_consumer_before_listener(
    tmp_path,
) -> None:
    event = ManagedTxProviderEvent(1, 1)
    composition = ManagedTxComposition(
        FakeActuator(), config_path=tmp_path / "managed-tx.json"
    )
    await composition.activate_provider(event)
    radio = _ManagedObservationRadio(SimpleNamespace())
    server = WebServer(
        radio,
        WebConfig(
            host="127.0.0.1",
            port=0,
            discovery=False,
            managed_tx_required=True,
        ),
    )
    attach_managed_tx_composition(server, composition, event)
    start_listener = AsyncMock(return_value=_FakeAsyncServer())

    try:
        with (
            patch(
                "rigplane.web.web_startup.asyncio.start_server",
                new=start_listener,
            ),
            pytest.raises(RuntimeError, match="does not accept the queue boundary"),
        ):
            await server.start()
        start_listener.assert_not_awaited()
    finally:
        await composition.shutdown(asyncio.Event())


class LegacyRadio:
    def __init__(self) -> None:
        self.raw_writes: list[bool] = []

    async def set_ptt(self, on: bool) -> None:
        self.raw_writes.append(on)


@pytest.mark.asyncio
async def test_install_blocks_legacy_raw_ptt_fallback(tmp_path) -> None:
    radio = LegacyRadio()
    composition = ManagedTxComposition(
        FakeActuator(), config_path=tmp_path / "managed-tx.json"
    )
    install_managed_tx_composition(radio, composition)

    api = ManagedTxApi.bind(radio, TxOwner(TxSource.SDK, "cutover"))
    assert api is not None
    with pytest.raises(RuntimeError, match="legacy PTT ingress is blocked"):
        await api.set_ptt(True)
    assert radio.raw_writes == []
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_duplicate_install_is_rejected(tmp_path) -> None:
    radio = LegacyRadio()
    composition = ManagedTxComposition(
        FakeActuator(), config_path=tmp_path / "managed-tx.json"
    )
    install_managed_tx_composition(radio, composition)
    with pytest.raises(RuntimeError, match="already installed"):
        install_managed_tx_composition(radio, composition)
    await composition.shutdown(asyncio.Event())
