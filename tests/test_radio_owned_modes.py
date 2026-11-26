"""Profile-driven operating contexts and SET-only CAT refusal attribution."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

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
from rigplane.core.radio_protocol import StateStoreCapable
from rigplane.core.state_store import StateStore
from rigplane.runtime._poller_types import (
    CommandQueueEntry,
    execute_command_queue_entry,
)
from rigplane.profiles.rig_loader import load_rig
from rigplane.web.runtime_helpers import _camel_case_state, _project_snapshot_state_dict
from rigplane.web.server import _command_error_code
from rigplane.testing.timing import VirtualClock
from rigplane_testkit.yaesu.cat_fake import FakeCatRadio

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("model", ["ftx1", "ft991a"])
@pytest.mark.asyncio
async def test_refusal_evidence_does_not_attach_a_canonical_store(model: str) -> None:
    from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
    from rigplane.core.state_pipeline_contracts import FieldPath

    fake = FakeCatRadio(profile=model)
    await fake.start()
    try:
        radio = YaesuCatRadio(
            fake.device_url,
            profile=model,
            audio_driver=create_autospec(UsbAudioDriver, instance=True),
        )
        assert not isinstance(radio, StateStoreCapable)
        assert not hasattr(radio, "_state_store")
        assert not hasattr(radio, "state_store")
        adapter = YaesuObservationAdapter.from_radio(radio)
        power = FieldPath.global_("meters", "power")
        if model == "ft991a":
            assert adapter.profile.policy_for(power).available_when
        assert adapter._available(power)
        try:
            await radio.connect()
            assert not isinstance(radio, StateStoreCapable)
            assert not hasattr(radio, "_state_store")
            assert not hasattr(radio, "state_store")
            fake.reject_set("set_mode")
            with pytest.raises(CommandRejectedError):
                await radio.set_mode("FM")
            if model == "ftx1":
                assert radio._mode_write_evidence.project()[0].evidence
            else:
                assert radio._mode_write_evidence.project() == ()
            assert not isinstance(radio, StateStoreCapable)
            assert not hasattr(radio, "_state_store")
            assert not hasattr(radio, "state_store")
            assert adapter._available(power)
        finally:
            await radio.disconnect()
    finally:
        await fake.stop()


def test_radio_state_projection_does_not_mutate_cached_state() -> None:
    radio = YaesuCatRadio(
        "/dev/null",
        profile="ftx1",
        audio_driver=create_autospec(UsbAudioDriver, instance=True),
    )
    before = radio._state.to_dict()
    projected = _project_snapshot_state_dict(StateStore().snapshot(), radio=radio)
    assert projected["radio_owned_modes"][0]["id"] == "wires_x_hri"
    assert radio.radio_state is radio._state
    assert radio.radio_state.diagnostics is radio._state.diagnostics
    assert radio._state.to_dict() == before


@pytest.mark.parametrize("model", ["ftx1", "ft991a"])
def test_radio_state_keeps_live_identity_and_scalar_writes(model: str) -> None:
    radio = YaesuCatRadio(
        "/dev/null",
        profile=model,
        audio_driver=create_autospec(UsbAudioDriver, instance=True),
    )
    state = radio.radio_state
    assert state is radio._state
    state.ptt = True
    state.key_speed = 37
    assert radio.radio_state is state
    assert radio.radio_state.ptt is True
    assert radio.radio_state.key_speed == 37


@pytest.mark.asyncio
async def test_refusal_projection_keeps_live_state_unchanged(
    virtual_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rigplane.core.radio_owned_modes as modes
    import rigplane.core.state_store as state_store

    monkeypatch.setattr(modes, "time", SimpleNamespace(monotonic=virtual_clock))
    monkeypatch.setattr(state_store, "time", SimpleNamespace(monotonic=virtual_clock))
    radio = YaesuCatRadio(
        "/dev/null",
        profile="ftx1",
        audio_driver=create_autospec(UsbAudioDriver, instance=True),
    )
    state = radio.radio_state
    before = state.to_dict()
    monkeypatch.setattr(
        radio, "_write", AsyncMock(side_effect=CommandRejectedError("Refused"))
    )
    with pytest.raises(CommandRejectedError):
        await radio.set_mode("FM")
    snapshot = StateStore().snapshot()
    projected = _project_snapshot_state_dict(snapshot, radio=radio)
    assert projected["radio_owned_modes"][0]["evidence"]
    assert not hasattr(radio, "_state_store")
    assert radio.radio_state is state
    assert state.to_dict() == before
    virtual_clock.advance(31.0)
    expired = _project_snapshot_state_dict(snapshot, radio=radio)
    assert expired["radio_owned_modes"][0]["evidence"] == ()
    assert expired["radio_owned_modes"][0]["message"] is None
    assert radio.radio_state is state
    assert state.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["ftx1", "ft991a"])
@pytest.mark.parametrize("recording_fails", [False, True])
async def test_mode_refusal_preserves_original_error(
    model: str, recording_fails: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rigplane.core.radio_owned_modes import ModeWriteAttempt

    radio = YaesuCatRadio(
        "/dev/null",
        profile=model,
        audio_driver=create_autospec(UsbAudioDriver, instance=True),
    )
    original = CommandRejectedError("Radio rejected 'MD04;'")
    monkeypatch.setattr(radio, "_write", AsyncMock(side_effect=original))
    if recording_fails:

        def fail_finish(*args: object, **kwargs: object) -> str:
            raise RuntimeError("evidence unavailable")

        monkeypatch.setattr(ModeWriteAttempt, "finish", fail_finish)
    with pytest.raises(CommandRejectedError) as caught:
        await radio.set_mode("FM")
    assert caught.value is original
    assert caught.value.args == ("Radio rejected 'MD04;'",)


@pytest.mark.asyncio
async def test_successful_set_clears_refusal_before_failed_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = YaesuCatRadio(
        "/dev/null",
        profile="ftx1",
        audio_driver=create_autospec(UsbAudioDriver, instance=True),
    )
    write = AsyncMock(side_effect=CommandRejectedError("Radio rejected 'MD04;'"))
    monkeypatch.setattr(radio, "_write", write)
    with pytest.raises(CommandRejectedError):
        await radio.set_mode("FM")
    assert radio._mode_write_evidence.project()[0].evidence
    write.side_effect = None
    read_error = CatParseError("MD0{mode};", "MD0G;", "Readback failed")
    monkeypatch.setattr(radio, "get_mode", AsyncMock(side_effect=read_error))
    with pytest.raises(CatParseError) as caught:
        await radio.set_mode("FM")
    assert caught.value is read_error
    assert radio._mode_write_evidence.project()[0].evidence == ()


@pytest.mark.asyncio
async def test_isolated_evidence_expires_and_fences_old_attempts(
    virtual_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rigplane.core.radio_owned_modes as modes
    import rigplane.core.state_store as state_store

    monkeypatch.setattr(modes, "time", SimpleNamespace(monotonic=virtual_clock))
    monkeypatch.setattr(state_store, "time", SimpleNamespace(monotonic=virtual_clock))
    definitions = load_rig(ROOT / "rigs/ftx1.toml").to_profile().radio_owned_modes
    evidence = modes.ModeWriteEvidence(definitions)
    evidence.begin(0).finish(refused=True)
    assert evidence.project()[0].evidence
    virtual_clock.advance(31.0)
    assert evidence.project()[0].evidence == ()
    stale_attempt = evidence.begin(0)
    evidence.reset()
    stale_attempt.finish(refused=True)
    assert evidence.project()[0].evidence == ()
    evidence.begin(0).finish(refused=True)
    assert evidence.project()[0].observation["provider_generation"] == 1
    evidence.reset()
    assert evidence.project()[0].evidence == ()


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
            spec = radio._config.commands["set_mode"]
            assert isinstance(spec, CatCommandSpec) and spec.write is not None
            wire_command = format_command(spec.write, mode=radio._mode_to_code["FM"])
            assert message == (
                f"Radio rejected command {wire_command!r} (returned '?;')"
            )
            assert fake.commands[before:] == (wire_command,)
            states = radio._mode_write_evidence.project()
            if model == "ftx1":
                (state,) = states
                assert "WIRES-X/HRI mode may be active" in state.message
                assert "hold GM/X" in state.message
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
                    radio._mode_write_evidence.project()[0].evidence[0].command_id
                    == command.id
                )
                assert state.observation["provider_generation"] == 1
                assert store.snapshot().fields == ()
                fake.accept_set("set_mode")
                await radio.set_mode("FM")
                assert radio._mode_write_evidence.project()[0].evidence == ()
                fake.reject_set("set_mode")
                with pytest.raises(CommandRejectedError):
                    await radio.set_mode("USB")
                # A new provider generation cannot retain the old refusal.
                radio._mode_write_evidence.reset()
                (state,) = radio._mode_write_evidence.project()
                assert state.evidence == ()
            else:
                assert states == ()
                assert "HRI" not in message
            assert mode_write_correlation.get() is None
        finally:
            await radio.disconnect()
            assert all(
                not state.evidence for state in radio._mode_write_evidence.project()
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
        (state,) = radio._mode_write_evidence.project()
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
        assert radio._mode_write_evidence.project()[0].evidence == ()
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
