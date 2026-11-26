"""Profile-driven operating contexts and SET-only CAT refusal attribution."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import create_autospec

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.backends.yaesu_cat.parser import CatParseError, format_command
from rigplane.commands.command_spec import CatCommandSpec
from rigplane.core.command_service import command_intent_from_request
from rigplane.core.exceptions import CommandRejectedError
from rigplane.core.radio_owned_modes import (
    RadioOwnedModeState,
    mode_write_correlation,
    project_radio_owned_modes,
)
from rigplane.core.radio_state import RadioState
from rigplane.core.state_store import StateStore
from rigplane.runtime._poller_types import (
    CommandQueueEntry,
    execute_command_queue_entry,
)
from rigplane.profiles.rig_loader import load_rig
from rigplane.web.runtime_helpers import _camel_case_state, _project_snapshot_state_dict
from rigplane.web.server import _command_error_code
from rigplane_testkit.yaesu.cat_fake import FakeCatRadio

ROOT = Path(__file__).resolve().parents[1]
GENERIC = "The radio refused the mode change. Check the operating mode on the radio."


def test_profile_context_is_unknown_and_panel_only() -> None:
    profile = load_rig(ROOT / "rigs/ftx1.toml").to_profile()
    (definition,) = profile.radio_owned_modes
    (state,) = project_radio_owned_modes(
        profile.radio_owned_modes, StateStore().snapshot()
    )
    assert state.id == "wires_x_hri"
    assert state.activity == "unknown"
    assert state.certainty is None
    assert state.evidence == ()
    assert definition.enter_capability == definition.leave_capability == "unavailable"
    assert "GM/X" in definition.leave_instruction
    assert definition.leave_provenance == "owner_observed"
    assert definition.restrictions[0].operation == "mode_write"
    assert definition.restrictions[0].certainty == "suspected"
    assert (
        RadioState(radio_owned_modes=(state,)).to_dict()["radio_owned_modes"][0]["id"]
        == state.id
    )


@pytest.mark.parametrize("model", ["ft991a", "ic705", "ic7300", "ic9700"])
def test_undeclared_contexts_remain_absent(model: str) -> None:
    profile = load_rig(ROOT / f"rigs/{model}.toml").to_profile()
    assert profile.radio_owned_modes == ()
    assert (
        project_radio_owned_modes(profile.radio_owned_modes, StateStore().snapshot())
        == ()
    )


def test_future_context_values_are_tolerated() -> None:
    state = RadioOwnedModeState.from_dict(
        {
            "id": "future_context",
            "label": "Future",
            "scope": "global",
            "activity": "future_activity",
            "certainty": "future_certainty",
            "future": True,
        }
    )
    assert state.id == "future_context"
    assert state.activity == "unknown"
    assert state.certainty is None


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["ftx1", "ft991a"])
async def test_simulator_refused_mode_write_has_scoped_explanation(model: str) -> None:
    fake = FakeCatRadio(profile=model)
    await fake.start()
    try:
        radio = YaesuCatRadio(
            fake.device_url,
            profile=model,
            audio_driver=create_autospec(UsbAudioDriver, instance=True),
        )
        store = StateStore()
        radio._state_store = store
        try:
            await radio.connect()
            fake.reject_set("set_mode")
            assert (await radio.get_mode())[0] == "USB"
            await radio.set_freq(7_100_000)
            assert await radio.get_freq() == 7_100_000
            command = command_intent_from_request(
                "set_mode", {"mode": "FM", "receiver": 0}, source="test"
            )

            async def execute(entry: CommandQueueEntry) -> None:
                await radio.set_mode("FM")

            before = len(fake.commands)
            with pytest.raises(CommandRejectedError) as caught:
                await execute_command_queue_entry(
                    CommandQueueEntry(
                        command=command,
                        command_id=command.id,
                        source=command.source,
                        session_id="client-1",
                    ),
                    execute,
                )
            message = str(caught.value)
            assert _command_error_code(caught.value) == "radio_nak"
            assert message.startswith(GENERIC)
            spec = radio._config.commands["set_mode"]
            assert isinstance(spec, CatCommandSpec) and spec.write is not None
            assert fake.commands[before:] == (
                format_command(spec.write, mode=radio._mode_to_code["FM"]),
            )
            states = project_radio_owned_modes(
                radio.profile.radio_owned_modes, store.snapshot()
            )
            if model == "ftx1":
                assert "WIRES-X/HRI mode may be active" in message
                assert "hold GM/X" in message
                (state,) = states
                assert state.activity == "unknown"
                assert state.certainty is None
                assert state.evidence[0].command_id == command.id
                assert state.evidence[0].target == "receiver.main.active.freq_mode.mode"
                assert state.evidence[0].kind == "write_refusal"
                assert state.evidence[0].command_source == "test"
                assert state.evidence[0].session_id == "client-1"
                public = _project_snapshot_state_dict(store.snapshot(), radio=radio)
                assert public["radio_owned_modes"][0]["activity"] == "unknown"
                assert (
                    public["radio_owned_modes"][0]["evidence"][0]["command_id"]
                    == command.id
                )
                assert (
                    _camel_case_state(public)["radioOwnedModes"][0]["evidence"][0][
                        "commandId"
                    ]
                    == command.id
                )
                assert (
                    radio.radio_state.radio_owned_modes[0].evidence[0].command_id
                    == command.id
                )
                assert (
                    state.observation["provider_generation"]
                    == store.provider_generation
                )
                fake.accept_set("set_mode")
                await radio.set_mode("FM")
                assert radio.radio_state.radio_owned_modes[0].evidence == ()
                fake.reject_set("set_mode")
                with pytest.raises(CommandRejectedError):
                    await radio.set_mode("USB")
                # A new provider generation cannot retain the old refusal.
                store.begin_provider_generation()
                (state,) = project_radio_owned_modes(
                    radio.profile.radio_owned_modes, store.snapshot()
                )
                assert state.evidence == ()
            else:
                assert message == GENERIC
                assert states == ()
                assert "HRI" not in message
            assert mode_write_correlation.get() is None
        finally:
            await radio.disconnect()
            assert all(
                not state.evidence for state in radio.radio_state.radio_owned_modes
            )
    finally:
        await fake.stop()


@pytest.mark.asyncio
async def test_refusal_expires_and_success_clears_only_its_receiver() -> None:
    from rigplane.core.radio_owned_modes import record_mode_write_result
    from rigplane.core.state_store import FreshnessClock

    clock = FreshnessClock(start=100.0)
    store = StateStore(freshness_clock=clock)
    (definition,) = load_rig(ROOT / "rigs/ftx1.toml").to_profile().radio_owned_modes
    definitions = (
        definition,
        replace(
            definition,
            scope="sub",
            restrictions=(replace(definition.restrictions[0], receiver="sub"),),
        ),
    )
    main = command_intent_from_request(
        "set_mode", {"mode": "FM", "receiver": 0}, source="test"
    )
    sub = command_intent_from_request(
        "set_mode", {"mode": "FM", "receiver": 1}, source="test"
    )
    for command in (main, sub):
        record_mode_write_result(
            definitions,
            store,
            receiver="main" if command is main else "sub",
            command_id=command.id,
            refused=True,
            now=100.0,
            generation=0,
        )
    record_mode_write_result(
        definitions,
        store,
        receiver="main",
        command_id=main.id,
        refused=False,
        now=100.0,
        generation=0,
    )
    a, b = project_radio_owned_modes(definitions, store.snapshot())
    assert a.evidence == ()
    assert b.evidence[0].command_id == sub.id
    clock.advance(31.0)
    store.mark_stale_due()
    a, b = project_radio_owned_modes(definitions, store.snapshot())
    assert a.evidence == b.evidence == ()
    assert a.activity == b.activity == "unknown"


@pytest.mark.parametrize(
    "activity,certainty",
    [
        ("unknown", None),
        ("inactive", None),
        ("active", "probable"),
        ("active", "certain"),
    ],
)
def test_synthetic_context_model_preserves_activity_until_expiry(
    activity, certainty
) -> None:
    from rigplane.core.state_pipeline_contracts import Observation, SourceMetadata
    from rigplane.core.state_store import FreshnessClock

    clock = FreshnessClock(start=100.0)
    store = StateStore(freshness_clock=clock)
    (definition,) = load_rig(ROOT / "rigs/ftx1.toml").to_profile().radio_owned_modes
    # Synthetic model sample, not an enabled radio detector or hardware evidence.
    store.apply(
        Observation(
            path=definition.path,
            value={
                "id": definition.id,
                "scope": definition.scope,
                "activity": activity,
                "certainty": certainty,
            },
            source=SourceMetadata(source="test", provider="synthetic_context"),
            timestamp_monotonic=100.0,
            max_age=5.0,
            provider_generation=0,
        )
    )
    (state,) = project_radio_owned_modes((definition,), store.snapshot())
    assert (state.activity, state.certainty) == (activity, certainty)
    clock.advance(6)
    # Projection also checks time if the freshness sweep has not run yet.
    (state,) = project_radio_owned_modes((definition,), store.snapshot())
    assert state.activity == "unknown"
    assert state.certainty is None


def test_mode_writes_cannot_leave_stale_generation_or_infer_normal_mode() -> None:
    from rigplane.core.radio_owned_modes import record_mode_write_result
    from rigplane.core.state_store import FreshnessClock

    store = StateStore(freshness_clock=FreshnessClock(start=100.0))
    definitions = load_rig(ROOT / "rigs/ftx1.toml").to_profile().radio_owned_modes
    store.begin_provider_generation()
    record_mode_write_result(
        definitions,
        store,
        receiver="main",
        command_id="old",
        refused=True,
        now=100.0,
        generation=0,
    )
    (state,) = project_radio_owned_modes(definitions, store.snapshot())
    assert state.evidence == ()
    assert state.activity == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["H", "I", "0", "G", "J"])
async def test_simulator_modulation_never_identifies_hri(code: str) -> None:
    fake = FakeCatRadio(profile="ftx1")
    await fake.start()
    radio = YaesuCatRadio(
        fake.device_url,
        profile="ftx1",
        audio_driver=create_autospec(UsbAudioDriver, instance=True),
    )
    try:
        await radio.connect()
        fake.hold("MD0", code)
        name, _ = await radio.get_mode()
        if code in ("0", "G", "J"):
            assert name == "UNKNOWN"
        (state,) = radio.radio_state.radio_owned_modes
        assert state.activity == "unknown"
        assert state.certainty is None
        assert state.evidence == ()
    finally:
        try:
            await radio.disconnect()
        finally:
            await fake.stop()


@pytest.mark.asyncio
async def test_simulator_readback_parse_failure_keeps_its_original_error() -> None:
    fake = FakeCatRadio(profile="ftx1")
    await fake.start()
    radio = YaesuCatRadio(
        fake.device_url,
        profile="ftx1",
        audio_driver=create_autospec(UsbAudioDriver, instance=True),
    )
    try:
        await radio.connect()
        fake.hold("MD0", "2", stored={"4": "G"})
        with pytest.raises(CatParseError) as caught:
            await radio.set_mode("FM")
        assert "HRI" not in str(caught.value)
        assert radio.radio_state.radio_owned_modes[0].evidence == ()
    finally:
        try:
            await radio.disconnect()
        finally:
            await fake.stop()


@pytest.mark.asyncio
async def test_queue_correlation_isolated_between_concurrent_commands() -> None:
    import asyncio

    barrier = asyncio.Barrier(2)
    seen = {}

    async def execute(entry: CommandQueueEntry) -> None:
        await barrier.wait()
        correlation = mode_write_correlation.get()
        assert correlation is not None
        seen[entry.command_id] = (correlation.command_id, correlation.session_id)

    await asyncio.gather(
        *(
            execute_command_queue_entry(
                CommandQueueEntry(
                    command=None, command_id=key, source="test", session_id=key
                ),
                execute,
            )
            for key in ("first", "second")
        )
    )
    assert seen == {key: (key, key) for key in ("first", "second")}
    assert mode_write_correlation.get() is None


def test_global_context_clear_preserves_other_receiver_refusal() -> None:
    from rigplane.core.radio_owned_modes import record_mode_write_result
    from rigplane.core.state_store import FreshnessClock

    store = StateStore(freshness_clock=FreshnessClock(start=100.0))
    (original,) = load_rig(ROOT / "rigs/ftx1.toml").to_profile().radio_owned_modes
    # Synthetic scope tests do not declare additional hardware support.
    definition = replace(
        original,
        scope="global",
        restrictions=(replace(original.restrictions[0], receiver="any"),),
    )
    for receiver in ("main", "sub"):
        record_mode_write_result(
            (definition,),
            store,
            receiver=receiver,
            command_id=receiver,
            refused=True,
            now=100.0,
            generation=0,
        )
    record_mode_write_result(
        (definition,),
        store,
        receiver="main",
        command_id="success",
        refused=False,
        now=100.0,
        generation=0,
    )
    (state,) = project_radio_owned_modes((definition,), store.snapshot())
    assert [item.command_id for item in state.evidence] == ["sub"]
    assert state.activity == "unknown"


def test_contexts_can_coexist_at_one_scope() -> None:
    from rigplane.core.radio_owned_modes import record_mode_write_result
    from rigplane.core.state_store import FreshnessClock

    store = StateStore(freshness_clock=FreshnessClock(start=100.0))
    (original,) = load_rig(ROOT / "rigs/ftx1.toml").to_profile().radio_owned_modes
    definitions = tuple(replace(original, id=key) for key in ("context_a", "context_b"))
    record_mode_write_result(
        definitions,
        store,
        receiver="main",
        command_id="refusal",
        refused=True,
        now=100.0,
        generation=0,
    )
    a, b = project_radio_owned_modes(definitions, store.snapshot())
    assert (a.id, b.id) == ("context_a", "context_b")
    assert a.evidence[0].command_id == b.evidence[0].command_id == "refusal"


def test_public_context_schema_tolerates_future_values() -> None:
    from rigplane.web.state_schema import RadioOwnedModePublic

    value = RadioOwnedModePublic.model_validate(
        {
            "id": "future_context",
            "label": "Future context",
            "scope": "global",
            "activity": "future_activity",
            "certainty": "future_certainty",
            "future": True,
        }
    )
    assert value.activity == "future_activity"
    assert value.model_dump()["future"] is True
