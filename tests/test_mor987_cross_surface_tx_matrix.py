"""MOR-987 evidence: the cross-surface TX matrix, on production identities.

The existing suites prove the matrix one surface at a time, and
``test_managed_tx_assembly``/``test_tx_safety_diagnostics`` prove three doors
land on one supervisor -- but they bind *hand-written stand-ins* for the CLI
and SDK owners (``TxOwner(TxSource.SDK, "cli-0e1d...")``) and never bind the
validation harness's owner at all. A stand-in of the right shape proves the
supervisor's semantics; it cannot prove that the constants the shipping
ingresses actually mint are five distinct, lease-matchable identities over one
runtime.

That distinction is the whole cross-surface claim. ``release_owner`` matches on
owner *equality*: two ingresses that collapsed onto one identity would let
either release the other's lease (and every stand-in-based test would stay
green), while an ingress whose identity is minted per request would fail to
match its own lease and strand the rig keyed.

So this binds the five real ones -- ``cli._CLI_TX_OWNER`` (MOR-1170),
``SyncIcomRadio._tx_owner`` (MOR-1171), ``validation.hardware.
_VALIDATION_TX_OWNER`` (MOR-1222), and the websocket/rigctld session owners
``bind_managed_tx`` mints (MOR-1013/MOR-1014) -- against one real armed
``CoreRadio``, and drives acquire / contention / owner-matched release /
authoritative-state through all of them.

Evidence only: no production code is touched by this file.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from rigplane.cli import _CLI_TX_OWNER
from rigplane.core.radio_protocol import ManagedTxApi
from rigplane.core.tx_safety import TxOutcome, TxOwner, TxSource
from rigplane.runtime.managed_tx_ingress import bind_managed_tx
from rigplane.runtime.sync import IcomRadio as SyncIcomRadio
from rigplane.validation.hardware import _VALIDATION_TX_OWNER
from rigplane.web.tx_safety_view import build_tx_safety_payload
from test_managed_tx_assembly import _AssembledRadio
from test_web_recovery_durable_off import _Provider

_WEB_SESSION = "ws-control-mor987"
_RIGCTLD_SESSION = "rigctld-client-9"

_LIVE: list[_AssembledRadio] = []


@pytest.fixture(autouse=True)
def _release_assembled_radios() -> Iterator[None]:
    """Keep ``CoreRadio.__del__``'s forgotten-teardown WARN out of the log."""
    yield
    while _LIVE:
        _LIVE.pop()._connected = False  # noqa: SLF001


async def _connected() -> _AssembledRadio:
    radio = _AssembledRadio(_Provider([]))
    _LIVE.append(radio)
    await radio.connect()
    return radio


@pytest.fixture
def sdk_owner() -> Iterator[TxOwner]:
    """The owner a real ``SyncIcomRadio`` session mints for itself."""
    wrapper = SyncIcomRadio("127.0.0.1")
    try:
        yield wrapper._tx_owner  # noqa: SLF001
    finally:
        wrapper._loop.close()  # noqa: SLF001


def _facades(radio: _AssembledRadio, sdk_owner: TxOwner) -> dict[str, ManagedTxApi]:
    """One bound facade per shipping managed ingress, keyed by surface name.

    Each is bound the way its own production call site binds it: the two
    session ingresses through ``bind_managed_tx`` with a session id of the
    shape their server mints, the three process-scoped ones through
    ``ManagedTxApi.bind`` with the module-level constant that ingress uses.
    """
    web = bind_managed_tx(radio, "websocket", _WEB_SESSION)
    rigctld = bind_managed_tx(radio, "rigctld", _RIGCTLD_SESSION)
    cli = ManagedTxApi.bind(radio, _CLI_TX_OWNER)
    sdk = ManagedTxApi.bind(radio, sdk_owner)
    validation = ManagedTxApi.bind(radio, _VALIDATION_TX_OWNER)
    assert web and rigctld and cli and sdk and validation
    return {
        "web": web,
        "rigctld": rigctld,
        "cli": cli,
        "sdk": sdk,
        "validation": validation,
    }


_SURFACES = ("web", "rigctld", "cli", "sdk", "validation")


# --- acquire: one supervisor, five distinct identities ---------------------


async def test_every_shipping_ingress_identity_binds_the_one_supervisor(
    sdk_owner: TxOwner,
) -> None:
    """Five doors, one supervisor object -- and five identities that differ.

    Identity (``is``), not equality: two runtimes over one rig compare equal on
    ``target_id``, so ``==`` would pass on exactly the failure that matters --
    two independent leases over one transmitter.
    """
    radio = await _connected()
    facades = _facades(radio, sdk_owner)

    for surface, facade in facades.items():
        assert facade.supervisor is radio.managed_tx, surface
        assert facade.supervisor is radio._managed_tx_runtime, surface  # noqa: SLF001

    owners = [facade.owner for facade in facades.values()]
    assert len(set(owners)) == len(_SURFACES)
    # The two automation surfaces borrow ``SDK`` deliberately (no validation
    # member exists) and separate on the owner id alone -- so the id is what
    # keeps a validation release off the CLI's lease.
    assert _CLI_TX_OWNER.source is _VALIDATION_TX_OWNER.source is TxSource.SDK
    assert _CLI_TX_OWNER.session_id != _VALIDATION_TX_OWNER.session_id


# --- contention: whoever holds it, the other four are refused --------------


@pytest.mark.parametrize("holder", _SURFACES)
async def test_a_lease_from_any_surface_denies_the_other_four(
    holder: str, sdk_owner: TxOwner
) -> None:
    """The BUSY that proves there is one lease and not five.

    A second supervisor behind any one door would answer ACCEPTED here and put
    a second key-down on the same PTT line.
    """
    radio = await _connected()
    facades = _facades(radio, sdk_owner)

    assert (await facades[holder].set_ptt(True)).outcome is TxOutcome.ACCEPTED

    for surface in _SURFACES:
        if surface == holder:
            continue
        assert (await facades[surface].set_ptt(True)).outcome is TxOutcome.BUSY, surface

    # One key request, one write on the wire -- the four refusals reached no
    # provider at all.
    assert radio.provider.log.count("ptt(on)") == 1
    assert radio.provider._keyed is True  # noqa: SLF001

    await facades[holder].set_ptt(False)


# --- release: owner-matched, so no surface can de-key another --------------


@pytest.mark.parametrize("holder", _SURFACES)
async def test_no_surface_can_release_a_lease_another_surface_holds(
    holder: str, sdk_owner: TxOwner
) -> None:
    """A foreign release is STALE and reaches no provider; the holder's works.

    This is the asymmetry the whole owner-identity design exists for. If two
    ingresses shared an identity, a routine ``finally: set_ptt(False)`` in one
    process would cut another operator's transmission -- and the rig would go
    to RX with the other surface still believing it is on the air.
    """
    radio = await _connected()
    facades = _facades(radio, sdk_owner)
    assert (await facades[holder].set_ptt(True)).outcome is TxOutcome.ACCEPTED
    writes_after_key = len(radio.provider.log)

    for surface in _SURFACES:
        if surface == holder:
            continue
        outcome = (await facades[surface].set_ptt(False)).outcome
        assert outcome is TxOutcome.STALE, surface

    assert radio.provider.log[writes_after_key:] == []
    assert radio.provider._keyed is True  # noqa: SLF001

    assert (await facades[holder].set_ptt(False)).outcome is TxOutcome.ACCEPTED
    assert "ptt(off)" in radio.provider.log[writes_after_key:]
    assert radio.provider._keyed is False  # noqa: SLF001


# --- one authoritative state: the payload names whoever holds it -----------


@pytest.mark.parametrize("holder", _SURFACES)
async def test_the_authoritative_payload_names_whichever_surface_holds_the_lease(
    holder: str, sdk_owner: TxOwner
) -> None:
    """MOR-987's "every surface consumes one authoritative TX/fault state".

    ``test_tx_safety_diagnostics`` pins this for a rigctld lease; the two
    automation surfaces (CLI, validation) were never checked, and they are the
    ones whose owner carries no distinguishing ``TxSource``. A payload that
    read a second supervisor would report no owner at all while the rig
    transmits.
    """
    radio = await _connected()
    facades = _facades(radio, sdk_owner)
    owner = facades[holder].owner

    assert (await facades[holder].set_ptt(True)).outcome is TxOutcome.ACCEPTED
    payload = build_tx_safety_payload(radio)

    assert payload["status"] == "managed"
    assert payload["owner"] == {
        "source": owner.source.value,
        "sessionId": owner.session_id,
    }
    assert payload["lease"] == {"held": True, "id": radio.tx_snapshot.lease_id}
    assert payload["keyRequested"] is True
    # Ingress view is a property of the gate, not of who holds the lease: the
    # two session doors stay supervised, the two ownerless ones stay refused.
    assert payload["ingress"]["websocket"] == {"supervised": True, "keyRefused": False}
    assert payload["ingress"]["rigctld"] == {"supervised": True, "keyRefused": False}
    assert payload["ingress"]["http"] == {"supervised": False, "keyRefused": True}
    assert payload["ingress"]["public_api"] == {"supervised": False, "keyRefused": True}

    await facades[holder].set_ptt(False)


# --- the process-scoped identities are minted once, not per request --------


def test_the_process_scoped_owners_are_stable_for_the_life_of_the_process() -> None:
    """Re-importing must not mint a second CLI/validation identity.

    A per-request owner is the failure mode ``_CLI_TX_OWNER``'s and
    ``_VALIDATION_TX_OWNER``'s module-level minting exists to prevent: the
    release would not match the lease it took, and the rig would stay keyed
    until the watchdog. Module-level is only half the guarantee -- these assert
    the value is carried, not recomputed, on a second read.
    """
    import rigplane.cli as cli_module
    import rigplane.validation.hardware as validation_module

    assert cli_module._CLI_TX_OWNER is _CLI_TX_OWNER  # noqa: SLF001
    assert validation_module._VALIDATION_TX_OWNER is _VALIDATION_TX_OWNER  # noqa: SLF001
    # Shape, so a refactor that swapped the mint for something session-derived
    # (and therefore not stable) is visible here.
    assert _CLI_TX_OWNER.session_id.startswith("cli-")
    assert _VALIDATION_TX_OWNER.session_id.startswith("validation:")
    uuid.UUID(hex=_CLI_TX_OWNER.session_id.removeprefix("cli-"))
    uuid.UUID(hex=_VALIDATION_TX_OWNER.session_id.removeprefix("validation:"))


def test_two_sdk_sessions_in_one_process_get_two_identities() -> None:
    """The SDK owner is per-session, unlike the CLI's process-wide one.

    Two ``SyncIcomRadio`` objects are two TX sessions; sharing an identity
    would let one wrapper's defensive unkey release the other's lease.
    """
    first, second = SyncIcomRadio("127.0.0.1"), SyncIcomRadio("127.0.0.1")
    try:
        assert first._tx_owner != second._tx_owner  # noqa: SLF001
        assert first._tx_owner.source is TxSource.SDK  # noqa: SLF001
        assert second._tx_owner.source is TxSource.SDK  # noqa: SLF001
        assert first._tx_owner != _CLI_TX_OWNER  # noqa: SLF001
    finally:
        first._loop.close()  # noqa: SLF001
        second._loop.close()  # noqa: SLF001
