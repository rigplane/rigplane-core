"""The eight behaviours the transmit-authority cutover must preserve.

ADR row 2 (``docs/plans/2026-08-20-transmit-authority.md`` §4 row 2, §3.10
item 6, §5 "Retained deliberately"). A later epic deletes seven enforcement
seats, six RF-truth resolvers and three classification tables. Eight
behaviours must survive that deletion. Until now "they will be preserved" was
an assertion; this file is the one citable list that makes it a CI fact, each
pin naming inline the exact mutation that must turn it red.

**Characterisation, not aspiration.** Every row below pins what the tree does
*today*, verified by running it. Where today's mechanism differs from the
ADR's description of it, the tree wins and the divergence is stated in the
pin's own docstring — three such divergences are recorded here (pins 1, 4 and
6). Nothing in this file is a fix; the old interlock mechanism is observed,
never edited.

Several of these behaviours already have coverage elsewhere
(``tests/test_web_teardown_unkey_gate.py``, ``tests/test_rigctld_tx_interlock.py``,
``tests/test_tx_safety.py``, ``tests/test_tx_safety_diagnostics.py``,
``tests/test_rigctld_managed_tx.py``). Those files stay exactly as they are.
The value this file adds is that the eight live under one name, in one place,
that the cutover rows can point at.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from rigplane.capabilities import CAP_ANTENNA, CAP_POWER_CONTROL, CAP_RIT, CAP_TUNER
from rigplane.core.radio_protocol import PrivilegedTxApi
from rigplane.core.state_diagnostics import StateDiagnosticsRecorder
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.core.tx_interlock_contract import TxInterlockDisposition
from rigplane.core.tx_safety import (
    ProviderAttempt,
    ProviderAttemptKind,
    ProviderPttObservation,
    RadioTx,
    TxOutcome,
    TxOwner,
    TxPhase,
    TxReleaseReason,
    TxSafetySnapshot,
    TxSafetySupervisor,
    TxSource,
    TxTransition,
)
from rigplane.profiles import resolve_radio_profile
from rigplane.radio_state import RadioState
from rigplane.rigctld.contract import (
    ClientSession,
    HamlibError,
    RigctldConfig,
    RigctldResponse,
)
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.protocol import format_response, parse_line
from rigplane.runtime import tx_interlock
from rigplane.runtime._poller_types import PttOff, PttOn, ScanStop, SendCiv
from rigplane.runtime.tx_interlock import (
    RfState,
    TxInterlockRefusal,
    classify_tx_interlock,
    evaluate_tx_interlock,
)
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import CommandQueue, RadioPoller
from rigplane.web.tx_safety_view import build_tx_safety_payload

_PTT_PATH = FieldPath.global_("tx_state", "ptt")


# ---------------------------------------------------------------------------
# Harnesses — the house patterns, unchanged
# ---------------------------------------------------------------------------


def _ptt_store(value: bool | None) -> StateStore:
    """A canonical store whose PTT field is fresh TX / fresh RX / absent.

    ``None`` leaves the field absent, which every resolver in the tree reads
    as UNKNOWN. The clock/age shape mirrors ``tests/test_rigctld_tx_interlock.py``
    so the observation is unambiguously inside its own max-age.
    """
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    if value is None:
        return store
    store.apply(
        Observation(
            path=_PTT_PATH,
            value=value,
            source=SourceMetadata(source="poll_response", provider="tests"),
            timestamp_monotonic=9.0,
            max_age=2.0,
        )
    )
    return store


def _rigctld_handler(
    store: StateStore | None, *, config: RigctldConfig | None = None, ptt: bool = False
) -> tuple[RigctldHandler, AsyncMock, Mock]:
    """A handler over an AsyncMock radio — ``tests/test_rigctld_tx_interlock.py``.

    ``store=None`` builds a radio with **no** canonical store at all, which is
    what drives ``_has_canonical_state_store`` (``handler.py:700``) false — the
    fail-open branch pin 2 characterises. ``ptt`` seeds the legacy
    ``RadioState`` mirror the ``t`` fallback reads (pin 6); it is deliberately
    *not* the canonical store.
    """
    radio = AsyncMock()
    radio.capabilities = {CAP_RIT}
    radio._state_diagnostics = StateDiagnosticsRecorder(enabled=True)
    state = RadioState()
    state.ptt = ptt
    radio.radio_state = state
    routing = Mock()
    routing.set_func = AsyncMock(return_value=RigctldResponse())
    routing.set_level = AsyncMock(return_value=RigctldResponse())
    radio.rigctld_routing = Mock(return_value=routing)
    radio._send_civ_raw = AsyncMock(return_value=None)
    if store is not None:
        radio.state_store = store
    else:
        del radio.state_store
    handler = RigctldHandler(
        radio, config if config is not None else RigctldConfig(), state_store=store
    )
    return handler, radio, routing


def _web_radio() -> SimpleNamespace:
    """SimpleNamespace + per-method AsyncMock — the pattern used by the
    interlock suite, and the one CLAUDE.md's no-MagicMock-near-authority rule
    points at: every attribute the seat touches is named explicitly."""
    return SimpleNamespace(
        profile=resolve_radio_profile(model="IC-7300"),
        capabilities={CAP_ANTENNA, CAP_POWER_CONTROL, CAP_TUNER},
        managed_tx=None,
        send_civ=AsyncMock(),
        set_ptt=AsyncMock(),
        set_mode=AsyncMock(),
        set_freq=AsyncMock(),
    )


def _web_poller() -> tuple[RadioPoller, SimpleNamespace, StateStore]:
    radio, store = _web_radio(), StateStore()
    store.begin_provider_generation()
    return RadioPoller(radio, CommandQueue(), state_store=store), radio, store


def _observe_web_ptt(store: StateStore, value: bool) -> None:
    store.apply(
        Observation(
            path=_PTT_PATH,
            value=value,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=time.monotonic(),
            max_age=1.0,
            provider_generation=store.provider_generation,
        )
    )


def _snapshot(**overrides: object) -> TxSafetySnapshot:
    """A supervisor snapshot at rest, with named overrides per pin-8 rule."""
    base: dict[str, object] = {
        "phase": TxPhase.IDLE,
        "radio_tx": RadioTx.UNKNOWN,
        "provider_generation": 1,
        "provider_ready": True,
        "lease_id": None,
        "owner": None,
        "release_reason": None,
        "terminal_release_reason": None,
        "release_attempt_count": 0,
        "release_last_error": None,
        "active_attempt": None,
        "watchdog_deadline_monotonic": None,
        "watchdog_enabled": False,
        "external_conflict": False,
    }
    base.update(overrides)
    return TxSafetySnapshot(**base)  # type: ignore[arg-type]


class _SnapshotHost:
    """The minimal ``ManagedTxCapable`` shape ``resolve_supervisor`` accepts.

    A real frozen ``TxSafetySnapshot`` behind a real attribute — no MagicMock,
    so a renamed or retyped field fails here rather than answering whatever it
    was asked for.
    """

    def __init__(self, snapshot: TxSafetySnapshot) -> None:
        self.managed_tx = SimpleNamespace(tx_snapshot=snapshot)


class _UnmanagedRadio:
    """A radio publishing no supervisor at all (Yaesu CAT, rigctld-client)."""


class _AsyncSupervisorFacade:
    """The async supervisor shape ``PrivilegedTxApi`` awaits.

    ``TxSafetySupervisor`` is a synchronous reducer; the object a radio
    publishes as ``managed_tx`` in production is ``ManagedRadioRuntime``,
    whose ``force_unkey`` is a coroutine. This wraps the real reducer in that
    one shape and adds nothing else — the decision under test is still the
    real supervisor's.
    """

    def __init__(self, supervisor: TxSafetySupervisor) -> None:
        self._supervisor = supervisor

    async def force_unkey(
        self, owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        return self._supervisor.force_unkey(owner, reason=reason)

    async def release_owner(
        self, owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        return self._supervisor.release_owner(owner, reason=reason)

    async def request_on(self, owner: TxOwner) -> TxTransition:
        return self._supervisor.request_on(owner)


# ---------------------------------------------------------------------------
# CHARACTERISATION PIN 1 (ADR §3.10 item 6, §5): the rigctld ``RPRT 0``
# rendering for a write that did not reach the radio during confirmed TX.
# ---------------------------------------------------------------------------


class TestPin1RprtZeroDuringConfirmedTx:
    """A write withheld during confirmed TX renders as success on the wire.

    Hamlib's own core answers OK without writing for mode/split during PTT
    (``rig.c``); a non-zero RPRT mid-sequence tears WSJT-X down. That
    *rendering* is what the cutover must preserve.

    **Divergence from the ADR, pinned as found.** §3.10 item 6 describes this
    as "a refusal *by the radio itself* on mode/split renders as success".
    No such seat exists in the tree today. What exists is
    ``RigctldHandler._defer_write_gate`` (``src/rigplane/rigctld/handler.py:770-833``):
    a *policy* drop taken before ``CommandService`` is entered, so the radio
    never sees the write and never refuses anything. The observable wire
    behaviour is identical, and it is the wire behaviour that is retained; the
    trigger changes at row 10. Pinned here as it actually is.
    """

    async def test_defer_write_during_confirmed_tx_answers_rprt_zero(self) -> None:
        # CHARACTERISATION PIN 1 (ADR §3.10 item 6, §5): during confirmed TX a
        # DEFER-classified write is answered RPRT 0 and never reaches the rig.
        # MUTATION: at src/rigplane/rigctld/handler.py:833 replace the known-TX
        # `return _ok()` with `return _err(HamlibError.ERJCTED)`
        # -> this test goes red.
        handler, radio, _routing = _rigctld_handler(_ptt_store(True))
        command = parse_line(b"M USB 2400")

        response = await handler.execute(command)

        assert response.ok
        assert response.error == HamlibError.OK
        assert format_response(command, response, ClientSession()) == b"RPRT 0\n"
        radio.set_mode.assert_not_awaited()

    async def test_unknown_rf_truth_is_not_laundered_into_the_same_success(
        self,
    ) -> None:
        """The discriminator: only *confirmed* TX may render as success.

        Without this row a mutation that answers RPRT 0 unconditionally would
        still pass the row above. Unknown RF is an unbounded fault, not a
        self-clearing policy state, so it refuses honestly.
        """
        handler, radio, _routing = _rigctld_handler(_ptt_store(None))
        command = parse_line(b"M USB 2400")

        response = await handler.execute(command)

        assert response.error is HamlibError.ERJCTED
        assert format_response(command, response, ClientSession()) == b"RPRT -9\n"
        radio.set_mode.assert_not_awaited()

    async def test_fresh_rx_still_dispatches_the_write(self) -> None:
        """And the third arm: known RX writes normally."""
        handler, radio, _routing = _rigctld_handler(_ptt_store(False))

        response = await handler.execute(parse_line(b"M USB 2400"))

        assert response.ok
        radio.set_mode.assert_awaited_once()


# ---------------------------------------------------------------------------
# CHARACTERISATION PIN 2 (ADR §3.10 item 6, §5, Q12): the raw-during-TX
# refusal — the one behaviour ruled *retained and rewired* rather than
# deleted, at both of its seats.
# ---------------------------------------------------------------------------


class TestPin2RawDuringTxIsRefused:
    """``RAW_CIV`` is refused at the web immediate-block seat and at the
    rigctld executor pre-gate, at TX **and** at unknown/stale truth.

    Both arms matter: §5 retains this "refusing when truth reads TX *or is
    unknown/stale* (fail-closed, today's web semantics)". A pin that only
    covered TX would let the fail-closed half be dropped silently.
    """

    @pytest.mark.parametrize(
        ("ptt", "reason_code"),
        [(True, "radio_transmitting"), (None, "rf_state_unknown")],
        ids=["confirmed-tx", "unknown-truth"],
    )
    async def test_web_send_civ_is_refused_before_the_transport(
        self, ptt: bool | None, reason_code: str
    ) -> None:
        # CHARACTERISATION PIN 2a (ADR §3.10 item 6, §5): raw CI-V is blocked
        # at the web seat unless RF truth positively reads RX.
        # MUTATION: delete `TxInterlockCommandFamily.RAW_CIV,` from
        # _WEB_IMMEDIATE_BLOCK_FAMILIES at
        # src/rigplane/web/radio_poller.py:219 -> this test goes red.
        poller, radio, store = _web_poller()
        if ptt is not None:
            _observe_web_ptt(store, ptt)

        with pytest.raises(TxInterlockRefusal) as refusal:
            poller._enforce_tx_interlock(SendCiv(command=0x1A, data=b"\x01"))

        assert refusal.value.reason_code == reason_code
        radio.send_civ.assert_not_awaited()

    async def test_web_send_civ_passes_on_positively_observed_rx(self) -> None:
        poller, radio, store = _web_poller()
        _observe_web_ptt(store, False)

        poller._enforce_tx_interlock(SendCiv(command=0x1A, data=b"\x01"))
        await poller._execute(SendCiv(command=0x1A, data=b"\x01"))

        radio.send_civ.assert_awaited_once()

    @pytest.mark.parametrize("ptt", [True, None], ids=["confirmed-tx", "unknown-truth"])
    async def test_rigctld_raw_frame_is_refused_inside_the_executor(
        self, ptt: bool | None
    ) -> None:
        # CHARACTERISATION PIN 2b (ADR §3.10 item 6, §5): the rigctld executor
        # pre-gate refuses a raw CI-V frame with ERJCTED unless truth reads RX.
        # MUTATION: drop the
        # `classification.disposition is TxInterlockDisposition.BLOCK` conjunct
        # at src/rigplane/rigctld/handler.py:465 (e.g. replace the whole
        # condition with `False`) -> this test goes red.
        handler, radio, _routing = _rigctld_handler(_ptt_store(ptt))
        command = parse_line(b"w FE FE 98 E0 03 FD")

        response = await handler.execute(command)

        assert response.error is HamlibError.ERJCTED
        assert format_response(command, response, ClientSession()) == b"RPRT -9\n"
        radio._send_civ_raw.assert_not_awaited()

    async def test_rigctld_raw_frame_passes_on_positively_observed_rx(self) -> None:
        handler, radio, _routing = _rigctld_handler(_ptt_store(False))

        response = await handler.execute(parse_line(b"w FE FE 98 E0 03 FD"))

        assert response.ok
        radio._send_civ_raw.assert_awaited_once_with(b"\xfe\xfe\x98\xe0\x03\xfd")

    async def test_a_radio_with_no_canonical_store_is_fail_open_today(self) -> None:
        """The latent no-store fail-open, pinned as an observed fact.

        ``_has_canonical_state_store`` (``handler.py:700``) is a conjunct of
        the pre-gate, so a backend publishing no ``StateStore`` sends the raw
        frame unconditionally. §5 records this branch as "the one declared
        flip" the cutover deletes — it is characterised here so the flip is a
        visible diff rather than an unremarked change of behaviour.
        """
        handler, radio, _routing = _rigctld_handler(None)
        assert handler._has_canonical_state_store is False

        response = await handler.execute(parse_line(b"w FE FE 98 E0 03 FD"))

        assert response.ok
        radio._send_civ_raw.assert_awaited_once()


# ---------------------------------------------------------------------------
# CHARACTERISATION PIN 3 (ADR §3.10 item 6, §5): teardown biased toward OFF.
# ---------------------------------------------------------------------------


class TestPin3TeardownIsBiasedTowardOff:
    """Every failure of the teardown consultation falls through to the unkey,
    and the keyer record is cleared on the *attempt*, not on success.

    Dropping a transmission is recoverable; a stuck transmitter is not.
    """

    def test_a_raising_teardown_gate_still_enqueues_the_unkey(self) -> None:
        # CHARACTERISATION PIN 3a (ADR §3.10 item 6, §5): teardown biases OFF.
        # MUTATION: at src/rigplane/web/handlers/control.py:1367 change the
        # `except Exception:` fall-through `permitted = True` to `False`
        # -> this test goes red.
        poller, radio, _store = _web_poller()
        poller.teardown_unkey_permitted = MagicMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("resolver exploded")
        )
        queue = CommandQueue()
        server = SimpleNamespace(command_queue=queue, _radio_poller=poller)
        handler = ControlHandler(
            ws=MagicMock(),
            radio=radio,
            server_version="test",
            radio_model="IC-7300",
            server=server,
            session_id="ws-a",
        )

        handler._release_ptt_on_teardown()

        assert queue.drain() == [PttOff()]

    async def test_a_raising_unkey_write_still_clears_the_keyer_record(self) -> None:
        # CHARACTERISATION PIN 3b (ADR §3.10 item 6, §5): the record is voided
        # on the attempt, so a failed OFF cannot withhold the next teardown.
        # MUTATION: move `self._last_keyer = None`
        # (src/rigplane/web/radio_poller.py:2542) out of the `finally:` at
        # :2538 and into the success path below the write -> red.
        poller, radio, store = _web_poller()
        _observe_web_ptt(store, False)
        await poller._execute(PttOn(), source="websocket", session_id="ws-a")
        _observe_web_ptt(store, True)
        assert poller._last_keyer == ("websocket", "ws-a")

        radio.set_ptt = AsyncMock(side_effect=RuntimeError("unkey never landed"))
        with pytest.raises(RuntimeError):
            await poller._execute(PttOff(), source="websocket", session_id="ws-a")

        assert poller._last_keyer is None
        assert poller.teardown_unkey_permitted("websocket", "ws-b") is True


# ---------------------------------------------------------------------------
# CHARACTERISATION PIN 4 (ADR §3.10 item 6, §5): the one-sided unkey — a
# de-key is never refused by the transmit-interlock machinery.
# ---------------------------------------------------------------------------


class TestPin4TheUnkeyIsOneSided:
    """KEY and UNKEY are split at the classification root and stay split at
    every seat: no RF-state gate ever refuses, defers or delays a de-key.

    **Divergence from the ADR, pinned as found.** "a de-key is never refused"
    is true of the *interlock*, and only of the interlock. A different,
    ownership-based mechanism can decline an unkey today: on a managed radio,
    ``RigctldHandler._route_ptt`` (``src/rigplane/rigctld/handler.py:1991-2003``)
    answers ``RPRT 0`` and writes nothing when the lease belongs to another
    owner (``TxOutcome.STALE``) — deliberate, MOR-1175, and pinned by
    ``tests/test_rigctld_managed_tx.py::test_a_declined_unkey_reports_ok_and_writes_nothing``.
    The web twin discards the same outcome at ``web/radio_poller.py:2528-2532``.
    The pin below therefore states the true scope rather than the wider claim.
    """

    @pytest.mark.parametrize(
        "rf_state", [RfState.TX, RfState.UNKNOWN, RfState.RX], ids=lambda s: s.value
    )
    def test_the_policy_admits_an_unkey_in_every_rf_state(
        self, rf_state: RfState
    ) -> None:
        # CHARACTERISATION PIN 4a (ADR §3.10 item 6, §5): PttOff is
        # ALWAYS_PASS by isinstance, ahead of every disruptive table.
        # MUTATION: at src/rigplane/runtime/tx_interlock.py:255 change
        # `_ALWAYS_PASS_TYPES = (PttOff, ScanStop)` to `(ScanStop,)`
        # -> this test goes red.
        assert classify_tx_interlock(PttOff()) is TxInterlockDisposition.ALWAYS_PASS
        decision = evaluate_tx_interlock(PttOff(), rf_state=rf_state)
        assert decision.allowed is True
        # ...and the key is the opposite, so "everything passes" cannot pass.
        assert classify_tx_interlock(PttOn()) is TxInterlockDisposition.BLOCK
        assert evaluate_tx_interlock(PttOn(), rf_state=rf_state).allowed is (
            rf_state is RfState.RX
        )

    @pytest.mark.parametrize("ptt", [True, None], ids=["confirmed-tx", "unknown-truth"])
    async def test_the_web_seat_never_consults_rf_truth_for_an_unkey(
        self, ptt: bool | None
    ) -> None:
        # CHARACTERISATION PIN 4b (ADR §3.10 item 6, §5): the web seat returns
        # before the resolver runs, so no RF truth can withhold a de-key.
        # MUTATION: same mutation as 4a (tx_interlock.py:255) -> red; or add
        # TxInterlockCommandFamily.PTT_OFF to _WEB_IMMEDIATE_BLOCK_FAMILIES at
        # src/rigplane/web/radio_poller.py:219 -> this test goes red.
        poller, radio, store = _web_poller()
        if ptt is not None:
            _observe_web_ptt(store, ptt)
        poller._current_rf_state = MagicMock(  # type: ignore[method-assign]
            side_effect=AssertionError("RF truth must not gate an unkey")
        )

        poller._enforce_tx_interlock(PttOff())
        poller._enforce_tx_interlock(ScanStop())

        # The tripwire is live, so the two calls above returning is a real
        # finding and not a resolver that was never installed: the KEY in the
        # same state does reach it.
        with pytest.raises(AssertionError, match="must not gate an unkey"):
            poller._enforce_tx_interlock(PttOn())
        radio.set_ptt.assert_not_awaited()

    @pytest.mark.parametrize(
        "ptt", [True, None, False], ids=["confirmed-tx", "unknown-truth", "rx"]
    )
    async def test_the_rigctld_seat_writes_the_unkey_in_every_rf_state(
        self, ptt: bool | None
    ) -> None:
        # CHARACTERISATION PIN 4c (ADR §3.10 item 6, §5): `T 0` is written on
        # the rigctld wire whatever RF truth says.
        # MUTATION: same mutation as 4a (tx_interlock.py:255) makes PttOff
        # BLOCK-classified, so the executor pre-gate at handler.py:462-469
        # refuses it -> this test goes red.
        handler, radio, _routing = _rigctld_handler(_ptt_store(ptt))
        command = parse_line(b"T 0")

        response = await handler.execute(command)

        assert response.ok
        assert format_response(command, response, ClientSession()) == b"RPRT 0\n"
        radio.set_ptt.assert_awaited_once_with(False)


# ---------------------------------------------------------------------------
# CHARACTERISATION PIN 5 (ADR §3.10 item 6, §5): read-only EACCESS.
# ---------------------------------------------------------------------------


class TestPin5ReadOnlyAnswersEaccess:
    """A read-only rigctld server refuses every *set* command with EACCESS,
    ahead of dispatch, and answers every *get* normally.

    The predicate is the static ``CommandDef.is_set`` flag, not TX state and
    not the interlock — which is exactly why deleting the interlock seats must
    not disturb it.
    """

    @pytest.mark.parametrize(
        "wire", [b"F 14074000", b"M USB 2400", b"T 1", b"T 0"], ids=lambda w: w.decode()
    )
    async def test_a_set_command_is_refused_with_eaccess_before_dispatch(
        self, wire: bytes
    ) -> None:
        # CHARACTERISATION PIN 5 (ADR §3.10 item 6, §5): read-only EACCESS.
        # MUTATION: at src/rigplane/rigctld/handler.py:1308 change
        # `if self._config.read_only and cmd.is_set:` to `if False:`
        # -> this test goes red.
        handler, radio, _routing = _rigctld_handler(
            _ptt_store(False), config=RigctldConfig(read_only=True)
        )
        command = parse_line(wire)

        response = await handler.execute(command)

        assert response.error is HamlibError.EACCESS
        assert format_response(command, response, ClientSession()) == b"RPRT -22\n"
        radio.set_freq.assert_not_awaited()
        radio.set_mode.assert_not_awaited()
        radio.set_ptt.assert_not_awaited()

    async def test_a_get_command_is_unaffected_by_read_only(self) -> None:
        """The discriminator: read-only refuses writes, not reads."""
        handler, _radio, _routing = _rigctld_handler(
            _ptt_store(True), config=RigctldConfig(read_only=True)
        )

        response = await handler.execute(parse_line(b"t"))

        assert response.ok
        assert response.values == ["1"]


# ---------------------------------------------------------------------------
# CHARACTERISATION PIN 6 (ADR §3.10 item 6, §5): ``t`` answered from the
# mirror when the canonical projection misses.
# ---------------------------------------------------------------------------


class TestPin6PttPollFallsBackToTheMirror:
    """``t`` answers the client from the legacy mirror rather than erroring —
    and never publishes that answer as canonical truth (MOR-1900).

    **Divergence from the ADR, pinned as found.** §5 attributes this rendering
    to ``_FallbackRigState`` (``handler.py:397-436``). That object does define
    ``update_ptt``, but nothing in ``src/`` calls it — its ``ptt`` is dead.
    The mirror ``t`` actually reads is ``RadioState.ptt`` on the radio itself,
    via ``RigctldHandler._radio_state()`` (``handler.py:924-926``), consumed at
    ``handler.py:1746-1768``. Pinned against the object that is really read.
    """

    async def test_the_mirror_answers_when_the_canonical_field_is_absent(
        self,
    ) -> None:
        # CHARACTERISATION PIN 6 (ADR §3.10 item 6, §5): `t` renders from the
        # legacy RadioState mirror when the canonical projection misses.
        # MUTATION: at src/rigplane/rigctld/handler.py:1746 replace
        # `state = self._radio_state()` with `state = None` -> this test goes
        # red (the answer collapses to the hardcoded "0").
        handler, radio, _routing = _rigctld_handler(_ptt_store(None), ptt=True)

        response = await handler.execute(parse_line(b"t"))

        assert response.ok
        assert response.values == ["1"]
        # ...and the mirror is never laundered into canonical truth.
        with pytest.raises(KeyError):
            handler._state_store.snapshot().field(_PTT_PATH)  # type: ignore[union-attr]
        assert handler._resolve_rigctld_rf_state() is tx_interlock.RfState.UNKNOWN
        # The fallback stays visible: a silent miss must not read as coverage.
        kinds = [event.kind for event in radio._state_diagnostics.events()]
        assert "rigctld_ptt_mirror_fallback" in kinds

    async def test_a_fresh_canonical_field_wins_over_the_mirror(self) -> None:
        """The discriminator: the mirror is a fallback, not the source."""
        handler, radio, _routing = _rigctld_handler(_ptt_store(True), ptt=False)

        response = await handler.execute(parse_line(b"t"))

        assert response.values == ["1"]
        kinds = [event.kind for event in radio._state_diagnostics.events()]
        assert "rigctld_ptt_mirror_fallback" not in kinds


# ---------------------------------------------------------------------------
# CHARACTERISATION PIN 7 (ADR §3.10 item 6, §5): ungated ``force_unkey``.
# ---------------------------------------------------------------------------


class TestPin7ForceUnkeyIsUngated:
    """``force_unkey`` adopts a key nobody owns and drives an OFF, in exactly
    the states the ordinary release refuses.

    "Ungated" is scoped: no ownership match, no lease requirement, no fresh
    PTT read first (MOR-1182). The reason whitelist and the never-preempt-a-
    live-lease rule are *not* skipped, and are pinned alongside so a mutation
    that removes them cannot hide behind this pin's name.
    """

    @staticmethod
    def _supervisor() -> tuple[TxSafetySupervisor, list[float]]:
        now = [10.0]
        counter = iter(f"lease-{n}" for n in range(1, 100))
        supervisor = TxSafetySupervisor(
            clock=lambda: now[0], id_factory=lambda: next(counter)
        )
        return supervisor, now

    def test_it_adopts_an_external_unowned_key_the_ordinary_release_refuses(
        self,
    ) -> None:
        # CHARACTERISATION PIN 7 (ADR §3.10 item 6, §5): force_unkey is not
        # gated on holding the lease.
        # MUTATION: at src/rigplane/core/tx_safety.py:437 insert
        # `if self._lease is None: return self._result(TxOutcome.STALE)`
        # ahead of the reason check -> this test goes red.
        supervisor, now = self._supervisor()
        owner = TxOwner(TxSource.WEBSOCKET, "web-1")
        supervisor.replace_provider(1, ready=True)
        supervisor.observe_ptt(ProviderPttObservation(RadioTx.ON, 1, 1, now[0]))
        assert supervisor.snapshot.phase is TxPhase.EXTERNAL_UNOWNED

        # The ordinary, owner-matched path refuses: nobody holds a lease.
        ordinary = supervisor.release_owner(
            owner, reason=TxReleaseReason.OPERATOR_RELEASE
        )
        assert ordinary.outcome is TxOutcome.STALE
        assert ordinary.effects == ()

        forced = supervisor.force_unkey(
            owner, reason=TxReleaseReason.OPERATOR_FORCED_UNKEY
        )

        assert forced.outcome is TxOutcome.ACCEPTED
        assert [effect.kind for effect in forced.effects] == [
            ProviderAttemptKind.WRITE_OFF
        ]
        assert forced.snapshot.owner == owner
        assert forced.snapshot.phase is TxPhase.RELEASE_REQUIRED

    def test_it_forces_an_off_with_no_observation_history_at_all(self) -> None:
        """No fresh PTT read is required first — the MOR-1182 case."""
        supervisor, _now = self._supervisor()
        supervisor.replace_provider(1, ready=True)
        assert supervisor.snapshot.radio_tx is RadioTx.UNKNOWN

        forced = supervisor.force_unkey(
            TxOwner(TxSource.WEBSOCKET, "web-1"),
            reason=TxReleaseReason.OPERATOR_FORCED_UNKEY,
        )

        assert forced.outcome is TxOutcome.ACCEPTED
        assert len(forced.effects) == 1

    def test_the_gates_it_does_keep(self) -> None:
        """Ungated on ownership, not on everything: the reason whitelist and
        the never-preempt-a-live-lease rule stand."""
        supervisor, now = self._supervisor()
        owner = TxOwner(TxSource.WEBSOCKET, "web-1")
        supervisor.replace_provider(1, ready=True)
        supervisor.observe_ptt(ProviderPttObservation(RadioTx.OFF, 1, 1, now[0]))

        with pytest.raises(ValueError):
            supervisor.force_unkey(owner, reason=TxReleaseReason.OPERATOR_RELEASE)

        supervisor.request_on(owner)
        assert (
            supervisor.force_unkey(
                owner, reason=TxReleaseReason.OPERATOR_FORCED_UNKEY
            ).outcome
            is TxOutcome.BUSY
        )

    async def test_the_privileged_facade_hardcodes_the_operator_reason(self) -> None:
        # CHARACTERISATION PIN 7b (ADR §3.10 item 6, §5): PrivilegedTxApi is
        # the only surface reaching force_unkey, and it never lets a caller
        # choose the attribution.
        # MUTATION: at src/rigplane/core/radio_protocol.py:711 change the
        # hardcoded `reason=TxReleaseReason.OPERATOR_FORCED_UNKEY` to
        # `reason=TxReleaseReason.OPERATOR_RELEASE` -> this test goes red
        # (force_unkey rejects the untrusted reason with ValueError).
        supervisor, now = self._supervisor()
        owner = TxOwner(TxSource.WEBSOCKET, "web-1")
        supervisor.replace_provider(1, ready=True)
        supervisor.observe_ptt(ProviderPttObservation(RadioTx.ON, 1, 1, now[0]))
        radio = SimpleNamespace(managed_tx=_AsyncSupervisorFacade(supervisor))

        privileged = PrivilegedTxApi.bind(radio, owner)
        assert privileged is not None

        transition = await privileged.force_unkey()

        assert transition.outcome is TxOutcome.ACCEPTED
        assert (
            transition.snapshot.terminal_release_reason
            is TxReleaseReason.OPERATOR_FORCED_UNKEY
        )
        # A supervisor publishing no force surface binds to None instead, so
        # an ordinary ingress cannot reach this path at all.
        assert (
            PrivilegedTxApi.bind(SimpleNamespace(managed_tx=SimpleNamespace()), owner)
            is None
        )


# ---------------------------------------------------------------------------
# CHARACTERISATION PIN 8 (ADR §3.10 item 6, §5): the watchdog-honesty view
# rules in ``web/tx_safety_view.py``.
# ---------------------------------------------------------------------------


class TestPin8WatchdogHonestyViewRules:
    """The four rules the ``txSafety`` projection exists to state:
    an ACK is not RF; an unarmed watchdog is not coverage; unmanaged is
    stated, not implied; the uncertain shutdown is named.
    """

    def test_an_ack_is_not_rf(self) -> None:
        # CHARACTERISATION PIN 8a (ADR §3.10 item 6, §5): rfConfirmed follows
        # TxPhase.KEYED, never the lease and never the ACCEPTED outcome.
        # MUTATION: at src/rigplane/web/tx_safety_view.py:124 change
        # `"rfConfirmed": snapshot.phase is TxPhase.KEYED` to
        # `"rfConfirmed": snapshot.lease_id is not None` -> this test goes red.
        payload = build_tx_safety_payload(
            _SnapshotHost(
                _snapshot(
                    phase=TxPhase.KEY_PENDING,
                    lease_id="lease-1",
                    owner=TxOwner(TxSource.WEBSOCKET, "web-1"),
                    radio_tx=RadioTx.UNKNOWN,
                )
            ),
            now=100.0,
        )

        assert payload["status"] == "managed"
        assert payload["keyRequested"] is True
        assert payload["rfConfirmed"] is False
        assert payload["radioTx"] == "unknown"

        # ...and the confirmed case is genuinely different, so a constant
        # `False` cannot pass either.
        confirmed = build_tx_safety_payload(
            _SnapshotHost(
                _snapshot(phase=TxPhase.KEYED, lease_id="lease-1", radio_tx=RadioTx.ON)
            ),
            now=100.0,
        )
        assert confirmed["rfConfirmed"] is True

    def test_a_watchdog_nobody_armed_is_not_coverage(self) -> None:
        # CHARACTERISATION PIN 8b (ADR §3.10 item 6, §5): watchdog.armed
        # follows the live deadline, never the configured-and-driven flag.
        # MUTATION: at src/rigplane/web/tx_safety_view.py:132 change
        # `"armed": deadline is not None` to
        # `"armed": snapshot.watchdog_enabled` -> this test goes red.
        payload = build_tx_safety_payload(
            _SnapshotHost(
                _snapshot(
                    phase=TxPhase.RELEASE_REQUIRED,
                    lease_id="lease-1",
                    release_reason=TxReleaseReason.OPERATOR_RELEASE,
                    terminal_release_reason=TxReleaseReason.OPERATOR_RELEASE,
                    watchdog_enabled=True,
                    watchdog_deadline_monotonic=None,
                )
            ),
            now=100.0,
        )

        assert payload["watchdog"]["armed"] is False
        assert payload["watchdog"]["driven"] is True
        assert payload["watchdog"]["secondsRemaining"] is None

    def test_unmanaged_is_stated_not_implied(self) -> None:
        # CHARACTERISATION PIN 8c (ADR §3.10 item 6, §5): a radio publishing no
        # supervisor says so, and publishes no safety fields to be misread as
        # a supervised rig at rest; a raising accessor is "unreadable".
        # MUTATION: at src/rigplane/web/tx_safety_view.py:80 change
        # `"status": "unmanaged"` to `"status": "managed"` -> this test goes
        # red (an unmanaged rig would read as a supervised rig at rest).
        unmanaged = build_tx_safety_payload(_UnmanagedRadio())

        assert unmanaged["status"] == "unmanaged"
        assert "phase" not in unmanaged
        assert "watchdog" not in unmanaged

        class _Broken:
            @property
            def managed_tx(self) -> object:
                raise AttributeError("nested typo")

        broken = build_tx_safety_payload(_Broken())
        assert broken["status"] == "unreadable"
        assert broken["error"] == "AttributeError"

        assert build_tx_safety_payload(None)["status"] == "no_radio"

    def test_the_uncertain_shutdown_is_named(self) -> None:
        # CHARACTERISATION PIN 8d (ADR §3.10 item 6, §5): an owed durable OFF
        # with nothing left that can complete it is reported, with a reason.
        # MUTATION: at src/rigplane/web/tx_safety_view.py:176 change
        # `return "unsettled_attempt"` to `return None` -> this test goes red.
        overdue = build_tx_safety_payload(
            _SnapshotHost(
                _snapshot(
                    phase=TxPhase.RELEASE_CONFIRMING,
                    lease_id="lease-1",
                    release_reason=TxReleaseReason.CLIENT_DISCONNECTED,
                    terminal_release_reason=TxReleaseReason.CLIENT_DISCONNECTED,
                    active_attempt=ProviderAttempt(
                        id="attempt-1",
                        kind=ProviderAttemptKind.WRITE_OFF,
                        provider_generation=1,
                        lease_id="lease-1",
                        started_at_monotonic=10.0,
                        timeout_seconds=2.0,
                    ),
                )
            ),
            now=100.0,
        )
        assert overdue["uncertainShutdown"] is True
        assert overdue["uncertainReason"] == "unsettled_attempt"
        assert overdue["activeAttempt"]["overdue"] is True

        # The second terminal: no attempt in flight and nothing ticking.
        no_driver = build_tx_safety_payload(
            _SnapshotHost(
                _snapshot(
                    phase=TxPhase.RELEASE_REQUIRED,
                    lease_id="lease-1",
                    release_reason=TxReleaseReason.SERVER_SHUTDOWN,
                    terminal_release_reason=TxReleaseReason.SERVER_SHUTDOWN,
                    watchdog_enabled=False,
                )
            ),
            now=100.0,
        )
        assert no_driver["uncertainReason"] == "no_driver"

        # ...and an obligation that still has a path forward is NOT named, so
        # "always uncertain during a release" cannot pass for the rule.
        driven = build_tx_safety_payload(
            _SnapshotHost(
                _snapshot(
                    phase=TxPhase.RELEASE_REQUIRED,
                    lease_id="lease-1",
                    release_reason=TxReleaseReason.SERVER_SHUTDOWN,
                    terminal_release_reason=TxReleaseReason.SERVER_SHUTDOWN,
                    watchdog_enabled=True,
                )
            ),
            now=100.0,
        )
        assert driven["uncertainShutdown"] is False
        assert driven["uncertainReason"] is None


def test_the_eight_pins_are_all_present() -> None:
    """One list, eight rows — the cutover rows cite this name.

    A group deleted or renamed without updating the ledger fails here rather
    than silently reducing the pinned set (§3.10 item 6 names eight).
    """
    groups = [
        name
        for name, obj in sorted(globals().items())
        if name.startswith("TestPin") and isinstance(obj, type)
    ]
    assert groups == [
        "TestPin1RprtZeroDuringConfirmedTx",
        "TestPin2RawDuringTxIsRefused",
        "TestPin3TeardownIsBiasedTowardOff",
        "TestPin4TheUnkeyIsOneSided",
        "TestPin5ReadOnlyAnswersEaccess",
        "TestPin6PttPollFallsBackToTheMirror",
        "TestPin7ForceUnkeyIsUngated",
        "TestPin8WatchdogHonestyViewRules",
    ]
