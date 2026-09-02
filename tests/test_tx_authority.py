"""Unit contracts for the dormant transmit-authority classifier."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from dataclasses import MISSING, FrozenInstanceError, fields
from typing import get_type_hints

import pytest

from rigplane.core.tx_observation import (
    RADIO_READBACK_SOURCES,
    TX_READ_DEADLINE_SECONDS,
    TxStateReading,
)
from rigplane.core import tx_authority
from rigplane.core.tx_authority import (
    FAMILY_WRITE_CLASS,
    TransmitAuthority,
    TxFamily,
    TxMethodEntry,
    TxWriteClass,
)


def test_tx_observation_contract_is_canonical_and_authority_reexports_it() -> None:
    """The observation value has one canonical definition during migration."""
    import rigplane.core.tx_observation as tx_observation

    reading_type = tx_observation.TxStateReading
    reading_fields = fields(reading_type)
    assert [field.name for field in reading_fields] == [
        "value",
        "attributed",
        "source",
        "verified_readback",
        "failure",
    ]
    assert get_type_hints(reading_type) == {
        "value": bool | None,
        "attributed": str | None,
        "source": str | None,
        "verified_readback": bool,
        "failure": str | None,
    }
    assert reading_fields[0].default is MISSING
    assert [field.default for field in reading_fields[1:]] == [None, None, False, None]

    reading = reading_type(value=False)
    assert not hasattr(reading, "__dict__")
    with pytest.raises(FrozenInstanceError):
        reading.value = True  # type: ignore[misc]

    assert tx_observation.TX_READ_DEADLINE_SECONDS == 0.3
    assert tx_observation.RADIO_READBACK_SOURCES == frozenset(
        {"poll_response", "civ_unsolicited", "hamlib_response", "yaesu_poll_response"}
    )
    assert tx_authority.TxStateReading is reading_type
    assert (
        tx_authority.TX_READ_DEADLINE_SECONDS is tx_observation.TX_READ_DEADLINE_SECONDS
    )
    assert tx_authority.RADIO_READBACK_SOURCES is tx_observation.RADIO_READBACK_SOURCES


METHOD_MAP: Mapping[str, TxMethodEntry] = {
    "set_ptt": TxMethodEntry(family=TxFamily.PTT_ON),
    "set_powerstat": TxMethodEntry(family=TxFamily.POWER_ON),
    "set_freq": TxMethodEntry(TxFamily.FREQUENCY),
    "set_mode": TxMethodEntry(TxFamily.MODE),
    "set_split": TxMethodEntry(TxFamily.VFO_TOPOLOGY),
    "set_power": TxMethodEntry(TxFamily.LEVELS),
    "stop_cw_text": TxMethodEntry(TxFamily.CW_STOP),
    "send_cw_text": TxMethodEntry(TxFamily.CW_TEXT),
    "set_tuner_status": TxMethodEntry(TxFamily.TUNER),
    "set_antenna_1": TxMethodEntry(TxFamily.ANTENNA),
    "set_band": TxMethodEntry(TxFamily.BAND),
    "set_vfo_slot": TxMethodEntry(TxFamily.VFO_SELECT),
    "memory_to_vfo": TxMethodEntry(TxFamily.BAND),
}


def build_authority(
    method_map: Mapping[str, TxMethodEntry] | None = None,
) -> TransmitAuthority:
    return TransmitAuthority(
        method_map=METHOD_MAP if method_map is None else method_map,
    )


# --------------------------------------------------------------------------
# Pinned literals (INV-8)
# --------------------------------------------------------------------------


def test_radio_readback_sources_pin() -> None:
    """Only radio-readback provenance feeds transmit truth (INV-8).

    Mutation: adding ``"state_poller"`` (or any producer-side tag) to the
    frozenset must make this test go red.
    """
    assert RADIO_READBACK_SOURCES == frozenset(
        {
            "poll_response",
            "civ_unsolicited",
            "hamlib_response",
            "yaesu_poll_response",
        }
    )
    for excluded in ("command_response", "state_poller", "local_reconcile", "test"):
        assert excluded not in RADIO_READBACK_SOURCES


def test_family_class_table_is_total_and_pinned() -> None:
    assert set(FAMILY_WRITE_CLASS) == set(TxFamily)
    hazard = {f for f, c in FAMILY_WRITE_CLASS.items() if c is TxWriteClass.HAZARD}
    assert hazard == {
        TxFamily.BAND,
        TxFamily.TUNER,
        TxFamily.ANTENNA,
        TxFamily.VFO_SELECT,
    }
    keying = {f for f, c in FAMILY_WRITE_CLASS.items() if c is TxWriteClass.KEYING}
    assert keying == {TxFamily.PTT_ON, TxFamily.CW_TEXT}
    assert FAMILY_WRITE_CLASS[TxFamily.FREQUENCY] is TxWriteClass.PASS
    assert FAMILY_WRITE_CLASS[TxFamily.MODE] is TxWriteClass.PASS
    assert FAMILY_WRITE_CLASS[TxFamily.VFO_TOPOLOGY] is TxWriteClass.PASS
    assert FAMILY_WRITE_CLASS[TxFamily.POWER_ON] is TxWriteClass.PASS
    assert FAMILY_WRITE_CLASS[TxFamily.CW_STOP] is TxWriteClass.PASS


def test_read_deadline_is_its_own_named_bound() -> None:
    assert TX_READ_DEADLINE_SECONDS == 0.3


def test_transmit_truth_projection_is_absent() -> None:
    from rigplane.core import tx_authority

    for name in (
        "TransmitTruth",
        "EMPTY_TRANSMIT_TRUTH",
        "build_transmit_truth",
    ):
        assert not hasattr(tx_authority, name)


def test_dormant_authority_watchdog_surface_is_absent() -> None:
    for name in ("_Hold", "TxDeadlineExpiry"):
        assert not hasattr(tx_authority, name)
    assert not hasattr(TransmitAuthority, "poll")
    assert not hasattr(TransmitAuthority, "fire_last_resort_unkey")

    parameters = inspect.signature(TransmitAuthority).parameters
    for name in (
        "last_resort_unkey",
        "lease_active",
        "cw_hold_duration",
        "max_key_down_seconds",
    ):
        assert name not in parameters

    authority = build_authority()
    for name in ("_last_resort_unkey", "_holds", "_deadline", "_lease_active"):
        assert not hasattr(authority, name)


def test_dormant_decision_surface_is_absent() -> None:
    for name in (
        "DECISION_LOG_CAPACITY",
        "TX_ENGINE_FAILURE_TAGS",
        "RAW_EXCLUDED",
        "TxDecisionRecord",
        "TxAuthorityView",
    ):
        assert not hasattr(tx_authority, name)

    assert "provider_generation" not in inspect.signature(TransmitAuthority).parameters
    assert not hasattr(TransmitAuthority, "view")
    assert not hasattr(TransmitAuthority, "_commit")
    assert not hasattr(TransmitAuthority, "_record")

    authority = build_authority()
    for name in ("_provider_generation", "_records"):
        assert not hasattr(authority, name)

    assert get_type_hints(tx_authority.TxAdmission)["family"] is TxFamily


def test_dormant_argument_resolution_surface_is_absent() -> None:
    for name in (
        "_UnresolvedArgument",
        "UNRESOLVED_ARGUMENT",
        "SIGNATURE_CACHE_SIZE",
        "_first_parameter_name",
        "first_parameter_name",
        "TxArgumentContext",
        "ArgumentPredicate",
        "ptt_family",
        "powerstat_family",
        "TX_ARGUMENT_PREDICATES",
        "ARGUMENT_SHORT_CIRCUIT_METHODS",
        "short_circuit_family",
    ):
        assert not hasattr(tx_authority, name)

    assert [field.name for field in fields(TxMethodEntry)] == ["family"]
    assert "target" not in inspect.signature(TransmitAuthority.admit).parameters
    assert "UNKEY" not in TxWriteClass.__members__
    assert "PTT_OFF" not in TxFamily.__members__
    assert "POWER_OFF" not in TxFamily.__members__


@pytest.mark.parametrize(
    ("method", "args", "kwargs", "family", "write_class"),
    (
        ("set_ptt", (False,), {}, TxFamily.PTT_ON, TxWriteClass.KEYING),
        ("set_ptt", (), {"on": False}, TxFamily.PTT_ON, TxWriteClass.KEYING),
        ("set_powerstat", (False,), {}, TxFamily.POWER_ON, TxWriteClass.PASS),
        ("set_powerstat", (), {"on": False}, TxFamily.POWER_ON, TxWriteClass.PASS),
    ),
)
async def test_fixed_ptt_and_powerstat_families_ignore_arguments(
    method: str,
    args: tuple[object, ...],
    kwargs: Mapping[str, object],
    family: TxFamily,
    write_class: TxWriteClass,
) -> None:
    authority = build_authority()

    async with authority.admit(method, args, kwargs) as admission:
        assert admission.family is family
        assert admission.write_class is write_class


def test_dormant_band_classification_surface_is_absent() -> None:
    """Frequency remains a PASS family without a second band classifier."""
    for name in (
        "BandRelation",
        "resolve_band",
        "band_relation",
        "frequency_family",
    ):
        assert not hasattr(tx_authority, name)

    parameters = inspect.signature(TransmitAuthority).parameters
    assert "bands" not in parameters
    assert "current_frequency_hz" not in parameters

    authority = build_authority()
    assert not hasattr(authority, "_bands")
    assert not hasattr(authority, "_current_frequency_hz")


@pytest.mark.parametrize(
    "method",
    ("set_band", "set_tuner_status", "set_antenna_1", "set_vfo_slot"),
)
@pytest.mark.parametrize(
    "answer",
    (
        TxStateReading(
            value=True,
            attributed="tx_other",
            source="poll_response",
            verified_readback=True,
        ),
        TxStateReading(value=None, failure="no-capability"),
        TxStateReading(
            value=False,
            attributed="rx",
            source="hamlib_response",
            verified_readback=False,
        ),
        OSError("transport is down"),
    ),
)
async def test_dormant_hazard_admission_ignores_every_observation_input(
    method: str,
    answer: TxStateReading | OSError,
) -> None:
    authority = build_authority()
    reads = 0
    body_executed = False

    async def read_observation() -> TxStateReading:
        nonlocal reads
        reads += 1
        if isinstance(answer, OSError):
            raise answer
        return answer

    setattr(authority, "_read_transmit_state", read_observation)
    async with authority.admit(method):
        body_executed = True

    assert body_executed
    assert reads == 0


async def test_dormant_hazard_admission_bodies_can_overlap() -> None:
    """The dormant skeleton does not serialize unrelated backend writes."""
    authority = build_authority()
    both_entered = asyncio.Event()
    release = asyncio.Event()
    entered = 0

    async def hazard(method: str) -> None:
        nonlocal entered
        async with authority.admit(method):
            entered += 1
            if entered == 2:
                both_entered.set()
            await release.wait()

    tasks = [
        asyncio.create_task(hazard("set_antenna_1")),
        asyncio.create_task(hazard("set_tuner_status")),
    ]
    overlapped = False
    try:
        await asyncio.wait_for(both_entered.wait(), 0.1)
        overlapped = True
    except TimeoutError:
        pass
    finally:
        release.set()
        await asyncio.gather(*tasks)

    assert overlapped, "hazard admission bodies were serialized"


def test_observation_driven_admission_surface_is_absent() -> None:
    for name in ("TxRefusalCode", "TxEvidence", "TxRefusal"):
        assert not hasattr(tx_authority, name)

    assert list(inspect.signature(TransmitAuthority).parameters) == ["method_map"]
    authority = build_authority()
    assert set(vars(authority)) == {"_method_map"}
    for name in (
        "note_transmit_observation",
        "_admit_keying",
        "_admit_hazard",
        "_refuse",
    ):
        assert not hasattr(TransmitAuthority, name)
    for name in (
        "_read_transmit_state",
        "_clock",
        "_read_deadline_seconds",
        "_lock",
        "_transmit_epoch",
    ):
        assert not hasattr(authority, name)

    assert [field.name for field in fields(tx_authority.TxAdmission)] == [
        "family",
        "write_class",
    ]
    assert get_type_hints(tx_authority.TxAdmission) == {
        "family": TxFamily,
        "write_class": TxWriteClass,
    }


@pytest.mark.parametrize(
    ("method", "family", "write_class"),
    tuple(
        (method, entry.family, FAMILY_WRITE_CLASS[entry.family])
        for method, entry in METHOD_MAP.items()
    ),
)
async def test_every_mapped_method_yields_classification_metadata(
    method: str,
    family: TxFamily,
    write_class: TxWriteClass,
) -> None:
    authority = build_authority()

    async with authority.admit(method) as admission:
        assert admission.family is family
        assert admission.write_class is write_class
