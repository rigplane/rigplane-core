"""Transmit-authority vocabulary, classification table and pure engine.

The authority answers one question per write: *would this command throw a
contact under RF?* — not "are we transmitting". Only the four owner-ruled
hazard families (band, tuner, antenna, VFO select) and the keying commands
consult transmit truth; everything the manufacturers permit passes without
consulting it at all.

The engine performs no I/O of its own: a backend injects the solicited
transmit-state read and a last-resort unkey, drives :meth:`poll` from a loop it
already owns, and receives due effects as data. Nothing in the product consumes
this module yet — it lands ahead of the backend admission rows so the contracts
exist before any call site cites them.

One requirement those rows inherit: an admission that passes its argument by
keyword must also pass ``target``, so classification reads the method's real
signature rather than a guessed parameter name.
:meth:`TransmitAuthority.admit` states it and :data:`UNRESOLVED_ARGUMENT`
records what failing it costs.

Design: ``docs/plans/2026-08-20-transmit-authority.md`` §3.3-§3.7.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field as dataclass_field, replace
from enum import StrEnum
from functools import lru_cache
from types import MappingProxyType
from typing import Final, Literal, NoReturn

from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import StateSnapshot
from rigplane.core.tx_safety import BACKEND_MAX_KEY_DOWN_SECONDS

_LOGGER = logging.getLogger(__name__)

#: Ceiling on the one solicited transmit-state read a hazard admission makes.
#: Deliberately not the backend's generic 2.0 s GET bound: a relay throw waits
#: for this, and an order of magnitude under the measured unkey barrier keeps
#: a same-connection command from queueing behind it.
TX_READ_DEADLINE_SECONDS: float = 0.3

#: Decision records retained per radio.
DECISION_LOG_CAPACITY: int = 256

#: The only observation provenance that may feed :class:`TransmitTruth`. Our
#: own command responses, the state poller, local reconciliation and test
#: fixtures have no intake — a write outcome is never evidence of receiving.
RADIO_READBACK_SOURCES: frozenset[str] = frozenset(
    {"poll_response", "civ_unsolicited", "hamlib_response", "yaesu_poll_response"}
)

#: Every ``TxEvidence.failure`` tag this engine produces itself. A backend's
#: read primitive may supply its own through ``TxStateReading.failure`` (for
#: example ``"no-capability"``); those pass through untouched.
TX_ENGINE_FAILURE_TAGS: frozenset[str] = frozenset(
    {"timeout", "transport", "read-error", "unverifiable-provenance", "unclassified"}
)

#: Raw byte writes, excluded by name. Bytes cannot be classified, so the
#: authority does not classify them; the totality test covers map ∪ this set,
#: so an unmapped method can never hide as "raw".
RAW_EXCLUDED: frozenset[str] = frozenset(
    {"send_civ", "send_civ_transaction", "send_civ_raw_fire_and_forget"}
)

_PTT_FIELD_PATH = FieldPath.global_("tx_state", "ptt")


class TxWriteClass(StrEnum):
    """The closed vocabulary of write classes."""

    PASS = "pass"
    HAZARD = "hazard"
    KEYING = "keying"
    UNKEY = "unkey"


class TxFamily(StrEnum):
    """Neutral, radio-independent command families."""

    FREQUENCY = "frequency"
    RIT_XIT = "rit-xit"
    MODE = "mode"
    VFO_TOPOLOGY = "vfo-topology"
    LEVELS = "levels"
    RX_PATH = "rx-path"
    MEMORY_WRITE = "memory-write"
    SCAN_START = "scan-start"
    SCAN_STOP = "scan-stop"
    POWER_ON = "power-on"
    POWER_OFF = "power-off"
    CW_STOP = "cw-stop"
    BAND = "band"
    TUNER = "tuner"
    ANTENNA = "antenna"
    VFO_SELECT = "vfo-select"
    PTT_ON = "ptt-on"
    CW_TEXT = "cw-text"
    PTT_OFF = "ptt-off"


#: The neutral family → class table. Pinned literal, never computed: PASS is
#: an explicit entry for every permitted family, and hazard membership is a
#: code-level constant because the four-family rule rests on universal
#: evidence (relay physics, both vendors, the measured band-change relay
#: throw under RF) rather than on any one radio.
FAMILY_WRITE_CLASS: Mapping[TxFamily, TxWriteClass] = MappingProxyType(
    {
        TxFamily.FREQUENCY: TxWriteClass.PASS,
        TxFamily.RIT_XIT: TxWriteClass.PASS,
        TxFamily.MODE: TxWriteClass.PASS,
        TxFamily.VFO_TOPOLOGY: TxWriteClass.PASS,
        TxFamily.LEVELS: TxWriteClass.PASS,
        TxFamily.RX_PATH: TxWriteClass.PASS,
        TxFamily.MEMORY_WRITE: TxWriteClass.PASS,
        TxFamily.SCAN_START: TxWriteClass.PASS,
        TxFamily.SCAN_STOP: TxWriteClass.PASS,
        TxFamily.POWER_ON: TxWriteClass.PASS,
        TxFamily.POWER_OFF: TxWriteClass.PASS,
        TxFamily.CW_STOP: TxWriteClass.PASS,
        TxFamily.BAND: TxWriteClass.HAZARD,
        TxFamily.TUNER: TxWriteClass.HAZARD,
        TxFamily.ANTENNA: TxWriteClass.HAZARD,
        TxFamily.VFO_SELECT: TxWriteClass.HAZARD,
        TxFamily.PTT_ON: TxWriteClass.KEYING,
        TxFamily.CW_TEXT: TxWriteClass.KEYING,
        TxFamily.PTT_OFF: TxWriteClass.UNKEY,
    }
)


class TxRefusalCode(StrEnum):
    """Every reason the authority declines a write."""

    REFUSED_WHILE_TRANSMITTING = "refused-while-transmitting"
    TX_TRUTH_UNAVAILABLE = "tx-truth-unavailable"


@dataclass(frozen=True, slots=True)
class TxEvidence:
    """What a decision observed. Required on every refusal, no exceptions."""

    value: bool | None = None
    attributed: str | None = None
    age_seconds: float | None = None
    source: str | None = None
    solicited: bool = False
    verified_readback: bool = False
    failure: str | None = None
    own_transmit_hold: str | None = None


@dataclass(frozen=True, slots=True)
class TxDecisionRecord:
    """One record per non-PASS admission."""

    monotonic: float
    provider_generation: int
    method: str
    family: str
    write_class: TxWriteClass
    action: Literal["sent", "refused"]
    code: TxRefusalCode | None
    evidence: TxEvidence | None


class TxRefusal(Exception):
    """The one exception consumers of the authority handle."""

    def __init__(self, code: TxRefusalCode, evidence: TxEvidence) -> None:
        super().__init__(str(code))
        self.code = code
        self.evidence = evidence


@dataclass(frozen=True, slots=True)
class TxStateReading:
    """One answer from the injected solicited transmit-state read."""

    value: bool | None
    attributed: str | None = None
    source: str | None = None
    verified_readback: bool = False
    failure: str | None = None


@dataclass(frozen=True, slots=True)
class TransmitTruth:
    """Display/UX-grade transmit truth. Never used for a hazard decision."""

    value: bool | None
    attributed: str | None
    age_seconds: float | None
    source: str | None
    generation_current: bool


EMPTY_TRANSMIT_TRUTH = TransmitTruth(
    value=None,
    attributed=None,
    age_seconds=None,
    source=None,
    generation_current=False,
)


def build_transmit_truth(
    snapshot: StateSnapshot, *, provider_generation: int
) -> TransmitTruth:
    """Project ``global.tx_state.ptt`` through the provenance pin.

    Freshness is deliberately not pre-collapsed: the age travels out and each
    consumer applies its own tolerance.
    """
    try:
        field = snapshot.field(_PTT_FIELD_PATH)
    except KeyError:
        return EMPTY_TRANSMIT_TRUTH
    if str(field.source.source) not in RADIO_READBACK_SOURCES:
        return EMPTY_TRANSMIT_TRUTH
    if type(field.value) is not bool:
        return EMPTY_TRANSMIT_TRUTH
    age = snapshot.generated_at_monotonic - field.last_observed_monotonic
    return TransmitTruth(
        value=field.value,
        # Row 6 routes Yaesu's ``tx_state_map`` attribution into this field.
        attributed=None,
        age_seconds=age if age >= 0.0 else None,
        source=str(field.source.source),
        generation_current=field.provider_generation == provider_generation,
    )


# Band relation — data in, no profile import


class BandRelation(StrEnum):
    """How a target frequency relates to the current one."""

    SAME_BAND = "same-band"
    CROSS_BAND = "cross-band"
    UNRESOLVED = "unresolved"


def resolve_band(
    frequency_hz: float | None, bands: Sequence[tuple[int, int]]
) -> int | None:
    """Index of the declared band holding ``frequency_hz``, else ``None``.

    ``bands`` arrives as plain ``(low_hz, high_hz)`` tuples built by the
    backend from its profile — this module imports nothing from ``profiles``.
    """
    if frequency_hz is None:
        return None
    for index, (low, high) in enumerate(bands):
        if low <= frequency_hz <= high:
            return index
    return None


def band_relation(
    current_hz: float | None,
    target_hz: float | None,
    bands: Sequence[tuple[int, int]],
) -> BandRelation:
    """Cross-band detection is best-effort extra strictness, never a new gate.

    A gap value, a missing current frequency or a profile with no band data
    resolves to :attr:`BandRelation.UNRESOLVED` — the manufacturer-permitted
    floor, because a rule that re-refused whenever the relation is murky would
    re-enter fail-closed through the back door.
    """
    current = resolve_band(current_hz, bands)
    target = resolve_band(target_hz, bands)
    if current is None or target is None:
        return BandRelation.UNRESOLVED
    return BandRelation.SAME_BAND if current == target else BandRelation.CROSS_BAND


# Argument predicates — named and pure


class _UnresolvedArgument:
    """The value of an argument the engine could not determine."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNRESOLVED_ARGUMENT"


#: Returned by :meth:`TxArgumentContext.first` when the engine cannot say what
#: the caller passed. Every predicate that meets it must fail *closed*: the
#: permissive branch — the unkey short-circuit, a PASS retune — is exactly what
#: a mis-spelled admission must never reach (MOR-1954).
#:
#: **This path is the expensive one, deliberately.** Measured in this tree
#: against a de-key that would otherwise have taken the UNKEY branch:
#:
#: * it classifies KEYING, so it enters the admission lock that the UNKEY
#:   branch never takes — 0.199 s behind a single in-flight hazard read where
#:   the resolvable spelling waited 0.000 s, bounded by that admission's
#:   solicited read (:data:`TX_READ_DEADLINE_SECONDS`) *plus its write body*,
#:   which the lock also spans;
#: * it neither clears the live deadline nor the live hold, and stacks a
#:   second one: holds ``("key", "key")`` and a whole
#:   :data:`BACKEND_MAX_KEY_DOWN_SECONDS` of deadline, for whose duration
#:   hazard writes are then refused with ``own_transmit_hold="key"`` — after a
#:   de-key that really did happen.
#:
#: That is the correct direction to be expensive in. The other reading of an
#: argument nobody can read is that a key-down passes as an unkey, and RF into
#: a load nobody chose has no such bound. Latency and a stuck hold expire; that
#: does not. The way to not pay it is to admit positionally, or to pass
#: ``target`` — see :meth:`TransmitAuthority.admit`.
UNRESOLVED_ARGUMENT: Final = _UnresolvedArgument()

#: How many gated signatures the resolver keeps. A backend gates on the order
#: of a hundred methods, so one entry per method fits many times over.
SIGNATURE_CACHE_SIZE: Final = 512

#: Methods the T5 short-circuit resolves from their argument rather than from
#: any table. Named once so the engine and the short-circuit agree on which
#: admissions actually consult an argument.
ARGUMENT_SHORT_CIRCUIT_METHODS: frozenset[str] = frozenset({"set_ptt", "set_powerstat"})


@lru_cache(maxsize=SIGNATURE_CACHE_SIZE)
def _first_parameter_name(function: Callable[..., object]) -> str | None:
    try:
        parameters = list(inspect.signature(function).parameters.values())
    except (TypeError, ValueError):  # pragma: no cover - builtins, C callables
        return None
    for index, parameter in enumerate(parameters):
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if index == 0 and parameter.name in ("self", "cls"):
            continue
        return parameter.name
    return None


def first_parameter_name(target: Callable[..., object] | None) -> str | None:
    """The gated method's first caller-supplied parameter, from its signature.

    The point of reading the *real* signature is that classification cannot
    depend on how a call site happened to spell its argument. Reflection runs
    once per underlying function and is cached; a bound method is normalised
    onto its function so the cache holds no radio instances.
    """
    if target is None:
        return None
    function = getattr(target, "__func__", target)
    try:
        return _first_parameter_name(function)
    except TypeError:  # pragma: no cover - unhashable callable
        return None


@dataclass(frozen=True, slots=True)
class TxArgumentContext:
    """Everything a predicate may look at. No I/O, no globals."""

    args: tuple[object, ...]
    kwargs: Mapping[str, object]
    current_frequency_hz: float | None
    bands: tuple[tuple[int, int], ...]
    target: Callable[..., object] | None = None

    def first(self) -> object:
        """The gated method's first argument, however the call spelled it.

        Positionally it is simply the first of :attr:`args`; by keyword it is
        read under the name :attr:`target`'s own signature declares. With no
        signature to read, or a keyword the signature does not name, it is
        :data:`UNRESOLVED_ARGUMENT` — never a guessed name and never ``None``,
        which a predicate cannot tell from a genuine falsy argument.

        A predicate for a method whose interesting argument is *not* the first
        one must read :attr:`args` / :attr:`kwargs` directly.
        """
        if self.args:
            return self.args[0]
        if not self.kwargs:
            return UNRESOLVED_ARGUMENT
        name = first_parameter_name(self.target)
        if name is not None and name in self.kwargs:
            return self.kwargs[name]
        return UNRESOLVED_ARGUMENT


ArgumentPredicate = Callable[[TxArgumentContext], TxFamily]


def ptt_family(context: TxArgumentContext) -> TxFamily:
    """``set_ptt(True)`` keys; ``set_ptt(False)`` is the one-sided unkey.

    An unresolved argument is read as the key, not the unkey: PTT_ON is the
    KEYING branch, which arms the watchdog and refuses nothing, so failing
    that way costs at most a spurious deadline — while failing the other way
    would walk a key-down straight through the gate.
    """
    value = context.first()
    if value is UNRESOLVED_ARGUMENT:
        return TxFamily.PTT_ON
    return TxFamily.PTT_ON if bool(value) else TxFamily.PTT_OFF


def powerstat_family(context: TxArgumentContext) -> TxFamily:
    """``set_powerstat(False)`` joins the short-circuit set, never gated."""
    value = context.first()
    if value is UNRESOLVED_ARGUMENT:
        return TxFamily.POWER_ON
    return TxFamily.POWER_ON if bool(value) else TxFamily.POWER_OFF


def frequency_family(context: TxArgumentContext) -> TxFamily:
    """HAZARD only when both endpoints resolve to declared bands and differ.

    An unresolved *argument* is a hazard: a retune whose target the engine
    cannot read may be the cross-band one. An unresolved *band relation* is
    not — a gap, a missing current frequency or a profile with no band data
    stays PASS, or the fail-closed direction would re-enter through the back
    door (see :func:`band_relation`).
    """
    target = context.first()
    if target is UNRESOLVED_ARGUMENT:
        return TxFamily.BAND
    target_hz = float(target) if isinstance(target, (int, float)) else None
    relation = band_relation(context.current_frequency_hz, target_hz, context.bands)
    return TxFamily.BAND if relation is BandRelation.CROSS_BAND else TxFamily.FREQUENCY


def is_tune_start(context: TxArgumentContext) -> bool:
    """``set_tuner_status(2)`` starts a tune cycle — a transmission we asked for.

    Unresolved counts as a tune start: holding the key-down bound over a write
    that turned out to be a plain tuner toggle is the survivable error.
    """
    value = context.first()
    return value is UNRESOLVED_ARGUMENT or value == 2


TX_ARGUMENT_PREDICATES: Mapping[str, ArgumentPredicate] = MappingProxyType(
    {
        "ptt": ptt_family,
        "powerstat": powerstat_family,
        "frequency": frequency_family,
    }
)


def short_circuit_family(method: str, context: TxArgumentContext) -> TxFamily | None:
    """T5: resolve de-key / power-off / stop-CW ahead of every table.

    A corrupt or incomplete classification table must never make an unkey
    harder, so this consults no map and no profile data.

    Exactly which admissions bypass the table, stated narrowly because the
    code is narrow: ``stop_cw_text`` always; ``set_ptt`` / ``set_powerstat``
    when their argument reads falsy, and when it cannot be read at all. A
    *readable truthy* argument returns ``None`` and goes on to the table like
    any other write — so a poisoned or absent map still refuses a key-down
    (``unclassified``, INV-1's fail direction), which is deliberate and worth
    keeping. The claim this function supports is therefore only this: **no
    table can turn a de-key, or an argument the engine could not read, into a
    refusal.**
    """
    if method == "stop_cw_text":
        return TxFamily.CW_STOP
    if method in ARGUMENT_SHORT_CIRCUIT_METHODS:
        value = context.first()
        if value is UNRESOLVED_ARGUMENT:
            # An unreadable argument takes the strict twin, still ahead of the
            # table, so a key-down can never hide in the unkey branch merely
            # because of how its argument was spelled (MOR-1954). Each is safe
            # for its own reason, and the two reasons are not interchangeable:
            #   set_ptt       — PTT_ON is KEYING, a branch with no refusal path
            #                   at all; the strict answer arms the watchdog and
            #                   cannot become a refusal.
            #   set_powerstat — POWER_ON and POWER_OFF are *both* PASS in
            #                   FAMILY_WRITE_CLASS, so the branch is inert here
            #                   whichever way it answers. The KEYING argument
            #                   above says nothing about powerstat: anyone who
            #                   reclassifies POWER_ON must re-derive this, not
            #                   inherit it.
            return TxFamily.PTT_ON if method == "set_ptt" else TxFamily.POWER_ON
        if not bool(value):
            return TxFamily.PTT_OFF if method == "set_ptt" else TxFamily.POWER_OFF
    return None


@dataclass(frozen=True, slots=True)
class TxMethodEntry:
    """One row of a per-backend method-name → family map.

    The per-backend maps themselves land beside the methods they pin.
    """

    family: TxFamily
    predicate: str | None = None


# Effects and view


@dataclass(frozen=True, slots=True)
class TxDeadlineExpiry:
    """A due key-down deadline, returned as data for a driver to execute."""

    monotonic: float
    reason: str = "backend_max_key_down"


@dataclass(frozen=True, slots=True)
class TxAuthorityView:
    """Read-only debuggability surface: why did it decide that."""

    records: tuple[TxDecisionRecord, ...]
    truth: TransmitTruth | None
    own_transmit_holds: tuple[str, ...]
    deadline_monotonic: float | None
    lease_active: bool


@dataclass
class _Hold:
    kind: str
    expires_at: float | None = None


@dataclass
class TxAdmission:
    """Handed to the caller for the duration of an admitted write.

    ``family`` is ``None`` only for a ``RAW_EXCLUDED`` method: bytes are not
    classified, and the admission says so rather than borrowing a family.
    """

    family: TxFamily | None
    write_class: TxWriteClass
    evidence: TxEvidence | None = None
    holds: list[_Hold] = dataclass_field(default_factory=list)


class TransmitAuthority:
    """One instance per radio. Pure: no sockets, no tasks, no module state."""

    def __init__(
        self,
        *,
        read_transmit_state: Callable[[], Awaitable[TxStateReading]],
        last_resort_unkey: Callable[[], Awaitable[None]],
        method_map: Mapping[str, TxMethodEntry],
        clock: Callable[[], float] = time.monotonic,
        bands: Sequence[tuple[int, int]] = (),
        current_frequency_hz: Callable[[], float | None] | None = None,
        lease_active: Callable[[], bool] | None = None,
        truth_provider: Callable[[], TransmitTruth] | None = None,
        provider_generation: Callable[[], int] | None = None,
        cw_hold_duration: Callable[[TxArgumentContext], float] | None = None,
        read_deadline_seconds: float = TX_READ_DEADLINE_SECONDS,
        max_key_down_seconds: float = BACKEND_MAX_KEY_DOWN_SECONDS,
    ) -> None:
        self._read_transmit_state = read_transmit_state
        self._last_resort_unkey = last_resort_unkey
        self._method_map = dict(method_map)
        self._clock = clock
        self._bands = tuple(bands)
        self._current_frequency_hz = current_frequency_hz
        self._lease_active = lease_active
        self._truth_provider = truth_provider
        self._provider_generation = provider_generation
        self._cw_hold_duration = cw_hold_duration
        self._read_deadline_seconds = read_deadline_seconds
        self._max_key_down_seconds = max_key_down_seconds

        self._lock = asyncio.Lock()
        self._records: deque[TxDecisionRecord] = deque(maxlen=DECISION_LOG_CAPACITY)
        self._holds: list[_Hold] = []
        self._deadline: float | None = None
        self._transmit_epoch = 0

    # -- intake ------------------------------------------------------------

    def note_transmit_observation(self, transmitting: bool) -> None:
        """Record that the radio was seen transmitting.

        Only the transmitting direction has an effect: it invalidates any read
        an in-flight hazard admission is holding. A receive report clears
        nothing — inferring *receive* from anything but a fresh solicited read
        is what the programme forbids.
        """
        if transmitting:
            self._transmit_epoch += 1

    async def fire_last_resort_unkey(self) -> None:
        """The last-resort OFF rail, for a driver with no better one."""
        await self._last_resort_unkey()

    # -- inspection --------------------------------------------------------

    def view(self) -> TxAuthorityView:
        return TxAuthorityView(
            records=tuple(self._records),
            truth=self._truth_provider() if self._truth_provider else None,
            own_transmit_holds=tuple(
                hold.kind for hold in self._active_holds(self._clock())
            ),
            deadline_monotonic=self._deadline,
            lease_active=bool(self._lease_active and self._lease_active()),
        )

    def poll(self, now: float) -> tuple[TxDeadlineExpiry, ...]:
        """Return due effects and disarm. Drivers execute them on their rails."""
        deadline = self._deadline
        if deadline is None or now < deadline:
            return ()
        self._deadline = None
        self._holds = [hold for hold in self._holds if hold.kind == "cw"]
        return (TxDeadlineExpiry(monotonic=deadline),)

    # -- admission ---------------------------------------------------------

    @asynccontextmanager
    async def admit(
        self,
        method: str,
        args: Sequence[object] = (),
        kwargs: Mapping[str, object] | None = None,
        *,
        target: Callable[..., object] | None = None,
    ) -> AsyncIterator[TxAdmission]:
        """Gate one write. The body performs the write; the lock spans both.

        ``target`` is the gated method itself. It is how a keyword admission
        resolves its argument by the method's real signature instead of a name
        guessed in the predicate table (MOR-1954).

        **Requirement on every call site that admits an argument by keyword**,
        row 7 onward: pass ``target``. INV-2 form A — the bare ``@tx_admit``
        decorator — passes the function it wraps; INV-2 form B — the in-body
        ``async with ... admit(...)`` block — passes ``self.<method>``, or
        forwards the argument positionally and needs no ``target`` at all.
        Failing to is not silent: the argument resolves to
        :data:`UNRESOLVED_ARGUMENT`, a warning names the method and the
        keywords it saw, and every predicate fails closed on it — that
        constant records what the fail-closed path costs, and it is not free.
        """
        if method in RAW_EXCLUDED:
            yield TxAdmission(None, TxWriteClass.PASS)
            return

        context = TxArgumentContext(
            args=tuple(args),
            kwargs=dict(kwargs or {}),
            current_frequency_hz=(
                self._current_frequency_hz() if self._current_frequency_hz else None
            ),
            bands=self._bands,
            target=target,
        )
        if self._consults_argument(method) and context.first() is UNRESOLVED_ARGUMENT:
            # Loud, because the predicates below now fail closed on it and a
            # silently mis-spelled admission is what MOR-1954 closed.
            _LOGGER.warning(
                "transmit authority could not resolve the admission argument",
                extra={
                    "method": method,
                    "keywords": sorted(context.kwargs),
                    "signature": first_parameter_name(target),
                },
            )

        family = short_circuit_family(method, context)
        if family is None:
            family = self._classify(method, context)
        if family is None:
            # INV-1's fail direction: nothing defaults to PASS by omission.
            self._refuse(
                method,
                "unclassified",
                TxWriteClass.HAZARD,
                TxRefusalCode.TX_TRUTH_UNAVAILABLE,
                TxEvidence(failure="unclassified"),
            )

        write_class = FAMILY_WRITE_CLASS[family]

        if write_class is TxWriteClass.PASS:
            yield TxAdmission(family, write_class)
            return

        if write_class is TxWriteClass.UNKEY:
            self._deadline = None
            self._holds = []
            yield TxAdmission(family, write_class)
            self._record(method, family, write_class, "sent", None, None)
            return

        async with self._lock:
            if write_class is TxWriteClass.KEYING:
                ticket = self._admit_keying(family, context)
            else:
                ticket = await self._admit_hazard(method, family, context)
            try:
                yield ticket
            finally:
                # A write that raised may already have reached the radio, so
                # the hold, the deadline and the record land either way.
                self._commit(method, ticket)

    # -- internals ---------------------------------------------------------

    def _consults_argument(self, method: str) -> bool:
        """Does classifying this method read an argument at all?"""
        if method in ARGUMENT_SHORT_CIRCUIT_METHODS:
            return True
        entry = self._method_map.get(method)
        if entry is None:
            return False
        # TUNER carries no predicate but `is_tune_start` reads the argument.
        return entry.predicate is not None or entry.family is TxFamily.TUNER

    def _classify(self, method: str, context: TxArgumentContext) -> TxFamily | None:
        entry = self._method_map.get(method)
        if entry is None:
            return None
        if entry.predicate is None:
            return entry.family
        return TX_ARGUMENT_PREDICATES[entry.predicate](context)

    def _admit_keying(
        self, family: TxFamily, context: TxArgumentContext
    ) -> TxAdmission:
        self._transmit_epoch += 1
        if family is TxFamily.CW_TEXT:
            duration = (
                self._cw_hold_duration(context) if self._cw_hold_duration else None
            )
            hold = _Hold(
                "cw", None if duration is None else self._clock() + float(duration)
            )
        else:
            hold = _Hold("key")
        return TxAdmission(family, TxWriteClass.KEYING, holds=[hold])

    async def _admit_hazard(
        self, method: str, family: TxFamily, context: TxArgumentContext
    ) -> TxAdmission:
        def refuse(code: TxRefusalCode, evidence: TxEvidence) -> NoReturn:
            self._refuse(method, family, TxWriteClass.HAZARD, code, evidence)

        unavailable = TxRefusalCode.TX_TRUTH_UNAVAILABLE

        # Step 1 — our own transmission, refused with no wire read at all.
        held = self._own_transmit_hold(self._clock())
        if held is not None:
            refuse(
                TxRefusalCode.REFUSED_WHILE_TRANSMITTING,
                TxEvidence(own_transmit_hold=held),
            )

        # Step 2 — one solicited read on this admission's own deadline.
        epoch = self._transmit_epoch
        started = self._clock()
        try:
            reading = await asyncio.wait_for(
                self._read_transmit_state(), self._read_deadline_seconds
            )
        except asyncio.TimeoutError:
            refuse(unavailable, TxEvidence(solicited=True, failure="timeout"))
        except (OSError, RuntimeError):
            refuse(unavailable, TxEvidence(solicited=True, failure="transport"))
        except Exception:
            # TxRefusal is the one exception a consumer of the gate handles, so
            # a parser fault in the read primitive becomes one too (§3.4).
            refuse(unavailable, TxEvidence(solicited=True, failure="read-error"))

        evidence = TxEvidence(
            value=reading.value,
            attributed=reading.attributed,
            age_seconds=self._clock() - started,
            source=reading.source,
            solicited=True,
            verified_readback=reading.verified_readback,
            failure=reading.failure,
        )
        if reading.value is None:
            refuse(unavailable, evidence)
        if not reading.verified_readback:
            # The rigctld-client answer is an upstream cache, not radio truth.
            refuse(
                unavailable,
                replace(evidence, failure="unverifiable-provenance"),
            )
        if reading.value or epoch != self._transmit_epoch:
            refuse(TxRefusalCode.REFUSED_WHILE_TRANSMITTING, evidence)

        holds: list[_Hold] = []
        if family is TxFamily.TUNER and is_tune_start(context):
            holds.append(_Hold("tune"))
        return TxAdmission(family, TxWriteClass.HAZARD, evidence=evidence, holds=holds)

    def _own_transmit_hold(self, now: float) -> str | None:
        if self._lease_active is not None and self._lease_active():
            return "lease"
        active = self._active_holds(now)
        return active[0].kind if active else None

    def _active_holds(self, now: float) -> list[_Hold]:
        self._holds = [
            hold
            for hold in self._holds
            if hold.expires_at is None or now < hold.expires_at
        ]
        return self._holds

    def _commit(self, method: str, ticket: TxAdmission) -> None:
        """Called once the caller's write has been handed off."""
        if ticket.holds:
            # `cw` keeps its computed duration; `key` and `tune` inherit the
            # key-down bound, so `_active_holds` clears them on the injected
            # clock even where no driver ever calls `poll()`.
            deadline = self._clock() + self._max_key_down_seconds
            for hold in ticket.holds:
                hold.expires_at = hold.expires_at or deadline
                self._holds.append(hold)
            self._deadline = deadline
        family = str(ticket.family)
        self._record(method, family, ticket.write_class, "sent", None, ticket.evidence)

    def _refuse(
        self,
        method: str,
        family: TxFamily | str,
        write_class: TxWriteClass,
        code: TxRefusalCode,
        evidence: TxEvidence,
    ) -> NoReturn:
        self._record(method, family, write_class, "refused", code, evidence)
        _LOGGER.warning(
            "transmit authority refused write",
            extra={
                "method": method,
                "family": str(family),
                "writeClass": str(write_class),
                "code": str(code),
                "evidence": asdict(evidence),
            },
        )
        raise TxRefusal(code, evidence)

    def _record(
        self,
        method: str,
        family: TxFamily | str,
        write_class: TxWriteClass,
        action: Literal["sent", "refused"],
        code: TxRefusalCode | None,
        evidence: TxEvidence | None,
    ) -> None:
        self._records.append(
            TxDecisionRecord(
                monotonic=self._clock(),
                provider_generation=(
                    self._provider_generation() if self._provider_generation else 0
                ),
                method=method,
                family=str(family),
                write_class=write_class,
                action=action,
                code=code,
                evidence=evidence,
            )
        )
