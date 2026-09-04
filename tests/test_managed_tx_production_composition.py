import ast
import asyncio
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from rigplane.core.radio_protocol import ManagedTxApi
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
