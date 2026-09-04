"""External provider polling through the production managed composition."""

import asyncio

from fake_rigctld import FakeRigctldServer
from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import (
    attach_managed_tx_composition,
    prepare_managed_tx_observation_generation,
)


import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from rigplane.backends.rigctld_client import radio as provider_module
from rigplane.core.command_dispatch import (
    enqueue_command_intent,
    prepare_command_intent,
)
from rigplane.exceptions import CommandError
from rigplane.runtime._poller_types import CommandQueue, PttOff, PttOn
from rigplane.runtime.managed_tx_state import ManagedTxOutcome


async def test_external_provider_managed_web_startup(tmp_path):
    async with FakeRigctldServer() as fake:
        radio = RigctldClientRadio(host=fake.host, port=fake.port)
        composition = ManagedTxComposition(radio, config_path=tmp_path / "tx.json")
        install_managed_tx_composition(radio, composition)
        await radio.connect()
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
        prepare_managed_tx_observation_generation(server)
        await composition.transport_ready(radio)
        await composition.bind_state_store(server.command_state_store)
        attach_managed_tx_composition(server, composition)
        try:
            await server.start()
            assert server._state_poller._managed_tx_authority is composition.authority
            assert (
                server.command_queue.capture_connection_generation()
                is radio._transport._writer
            )
        finally:
            await composition.shutdown(asyncio.Event())
            await server.stop()
            await radio.disconnect()


@pytest.fixture
async def managed(tmp_path):
    async with FakeRigctldServer() as fake:
        radio = RigctldClientRadio(host=fake.host, port=fake.port)
        composition = ManagedTxComposition(radio, config_path=tmp_path / "tx.json")
        install_managed_tx_composition(radio, composition)
        await radio.connect()
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
        prepare_managed_tx_observation_generation(server)
        await composition.transport_ready(radio)
        await composition.bind_state_store(server.command_state_store)
        attach_managed_tx_composition(server, composition)
        queue = server.command_queue
        queue.register_session("operator")
        poller = radio.create_observation_poller(
            callback=lambda _: None, command_queue=queue
        )
        poller.bind_managed_tx_authority(composition.authority)
        poller.bind_provider_generation(
            capture=lambda: server.command_state_store.provider_generation
        )
        rig = SimpleNamespace(
            fake=fake,
            radio=radio,
            composition=composition,
            authority=composition.authority,
            server=server,
            queue=queue,
            poller=poller,
        )
        try:
            yield rig
        finally:
            await composition.shutdown(asyncio.Event())
            await poller.stop()
            await radio.disconnect()


def _enqueue_intent(rig):
    intent = replace(
        prepare_command_intent(
            rig.radio,
            "set_af_level",
            {"level": 128, "receiver": 0},
            source="websocket",
            command_id="rich-envelope",
            session_id="operator",
        ),
        timeout=10.0,
    )
    reply = asyncio.get_running_loop().create_future()
    enqueue_command_intent(
        rig.queue,
        intent,
        future=reply,
        command_id=intent.id,
        source=intent.source,
        session_id="operator",
        command_service=rig.server.command_service,
        timeout=intent.timeout,
        expires_at_monotonic=time.monotonic() + 10,
        provider_generation=rig.server.command_state_store.provider_generation,
        connection_generation=rig.queue.capture_connection_generation(),
    )
    return intent, reply


@pytest.mark.parametrize(
    "change", ["none", "deny", "provider", "connection", "session", "expiry"]
)
async def test_rich_intent_shared_leaf_rechecks_currency(managed, monkeypatch, change):
    rig = managed
    intent, reply = _enqueue_intent(rig)
    seen = []
    leaf = provider_module.execute_command_intent
    admit = rig.authority.admit_managed_write

    async def admission(received):
        assert received is intent
        if change == "provider":
            rig.server.command_state_store.begin_provider_generation()
        elif change == "connection":
            await rig.radio.disconnect()
            await rig.radio.connect()
        elif change == "session":
            rig.queue.unregister_session("operator")
        elif change == "expiry":
            rig.poller._clock = lambda: time.monotonic() + 20
        return False if change == "deny" else await admit(received)

    async def observed_leaf(radio, received, **kwargs):
        seen.append((radio, received, kwargs))
        await leaf(radio, received, **kwargs)

    monkeypatch.setattr(rig.authority, "admit_managed_write", admission)
    monkeypatch.setattr(provider_module, "execute_command_intent", observed_leaf)
    await rig.poller._drain_commands()
    if change == "none":
        await reply
        assert (
            len([cmd for cmd in rig.fake.commands_seen if cmd.startswith("L AF ")]) == 1
        )
    else:
        with pytest.raises(CommandError):
            await reply
        assert not any(cmd.startswith("L AF ") for cmd in rig.fake.commands_seen)
    assert len(seen) == 1
    assert seen[0][0] is rig.radio and seen[0][1] is intent
    assert seen[0][2]["managed_tx_authority"] is rig.authority
    assert callable(seen[0][2]["validate_currency"])


@pytest.mark.parametrize(
    "change", ["none", "provider", "connection", "session", "expiry", "poison"]
)
async def test_positive_submission_uses_current_shared_executor(managed, change):
    rig = managed
    loop = asyncio.get_running_loop()
    ready = loop.create_future()
    expiry = loop.time() + 10
    submission = rig.authority.start_ptt_submission(
        True, "operator", ready=ready, expires_at_monotonic=expiry
    )
    joined = asyncio.create_task(
        rig.server.enqueue_managed_positive_tx(
            ready=ready,
            submission=submission,
            source="websocket",
            session_id="operator",
            expires_at_monotonic=expiry,
            connection_generation=rig.queue.capture_connection_generation(),
        )
    )
    await rig.queue.wait(1)
    if change == "provider":
        rig.server.command_state_store.begin_provider_generation()
    elif change == "connection":
        await rig.radio.disconnect()
        await rig.radio.connect()
    elif change == "session":
        rig.queue.unregister_session("operator")
    elif change == "expiry":
        rig.poller._clock = lambda: time.monotonic() + 20
    elif change == "poison":
        await rig.authority.provider_unavailable()
        await rig.composition.transport_unavailable(rig.radio)
        await rig.composition.transport_ready(object())
    await rig.poller._drain_commands()
    await rig.poller._drain_commands()
    if change == "none":
        receipt = await joined
        assert receipt.outcome is ManagedTxOutcome.ACCEPTED
        assert rig.fake.commands_seen.count("T 1") == 1
        off = await rig.authority.submit_ptt(False, "operator")
        await off.wait_settlement()
        assert rig.fake.commands_seen.count("T 0") == 1
        await rig.composition.shutdown(asyncio.Event())
        assert rig.fake.commands_seen.count("T 0") == 2
    else:
        with pytest.raises((CommandError, asyncio.CancelledError)):
            await joined
        assert "T 1" not in rig.fake.commands_seen
    assert submission.done()


async def test_managed_legacy_on_denied_and_off_reachable(managed):
    rig = managed
    with pytest.raises(RuntimeError, match="already bound"):
        rig.poller.bind_managed_tx_authority(rig.authority)
    on, off = (asyncio.get_running_loop().create_future() for _ in range(2))
    rig.queue.put_ordered(PttOn(), future=on)
    rig.queue.put_ordered(PttOff(), future=off)
    await rig.poller._drain_commands()
    with pytest.raises(CommandError, match="positive TX queue submission"):
        await on
    await off
    assert "T 1" not in rig.fake.commands_seen
    assert "T 0" in rig.fake.commands_seen


async def test_unmanaged_provider_ptt_and_connection_binding_lifecycle():
    async with FakeRigctldServer() as fake:
        radio = RigctldClientRadio(host=fake.host, port=fake.port)
        await radio.connect()
        queue = CommandQueue()
        poller = radio.create_observation_poller(
            callback=lambda _: None, command_queue=queue
        )
        try:
            assert queue.capture_connection_generation() is radio._transport._writer
            queue.put_ordered(PttOn())
            queue.put_ordered(PttOff())
            await poller._drain_commands()
            assert [cmd for cmd in fake.commands_seen if cmd.startswith("T ")] == [
                "T 1",
                "T 0",
            ]
            await poller.stop()
            with pytest.raises(RuntimeError, match="not bound"):
                queue.capture_connection_generation()
            await poller.start()
            assert queue.capture_connection_generation() is radio._transport._writer
        finally:
            await poller.stop()
            await radio.disconnect()
