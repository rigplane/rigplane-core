"""Spec'd fakes for the TCI adapter tests (MOR-3821, no radio hardware).

No bare ``MagicMock`` (``tests/test_meta_mock_specs.py``): the radio fakes
are hand-rolled classes implementing the real capability protocols, the
store is the real :class:`StateStore` fed with :class:`Observation`, and
the command pipeline is a recording stub.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore


def _source() -> SourceMetadata:
    return SourceMetadata(
        source="poll_response", provider="test", transport="fake", native_id="tci"
    )


def observe(
    store: StateStore,
    path: FieldPath,
    value: Any,
    *,
    at: float = 1.0,
    max_age: float | None = 60.0,
) -> None:
    """Apply one confirmed observation to a real store."""
    store.apply(
        Observation(
            path=path,
            value=value,
            source=_source(),
            timestamp_monotonic=at,
            max_age=max_age,
        )
    )


def build_store(entries: Mapping[FieldPath, Any], *, at: float = 1.0) -> StateStore:
    """A real store preloaded with confirmed observations."""
    store = StateStore()
    for path, value in entries.items():
        observe(store, path, value, at=at)
    return store


@dataclass
class StubProfile:
    """The profile surface the adapter reads (modes, ranges, power)."""

    modes: tuple[str, ...] = ("AM", "LSB", "USB", "CW", "FM")
    freq_ranges: tuple[Any, ...] = field(
        default_factory=lambda: (_hf_range(),),
    )
    data_mode_count: int = 1
    max_watts: int | float | None = None
    min_watts: int | float | None = None


def _hf_range() -> Any:
    from rigplane.profiles import FreqRangeInfo

    return FreqRangeInfo(start=30_000, end=54_000_000, label="hf")


@dataclass
class StubIcomRadio:
    """Full-capability Icom-like fake (IC-7610 shape, LAN DATA2 route).

    Set ``observe_store`` to mirror every write into a real store (the
    acquisition the conformance tests stand in for); unit tests leave it
    ``None`` and assert on the recorded calls instead.
    """

    model: str = "IC-7610"
    profile: StubProfile = field(default_factory=StubProfile)
    native_power_unit: str = "raw_255"
    freq_a: int = 14_074_000
    freq_b: int = 14_074_000
    mode_a: str = "USB"
    mode_b: str = "USB"
    data_a: int = 0
    data_b: int = 0
    split: bool = False
    rit_freq: int = 0
    rit_on: bool = False
    rit_tx: bool = False
    rf_power: int = 128
    key_speed: int = 20
    backend_id: str = "rigplane"
    observe_store: StateStore | None = None
    fail_on: set[str] = field(default_factory=set)
    #: Ack frequency writes but keep the frequency (the C0 capture shape:
    #: the radio accepts the set yet stays where it was). Unlike ``fail_on``,
    #: which refuses the write, this models a silent ignore.
    ignore_freq_writes: bool = False
    b_mode_writes: list[dict[str, Any]] = field(default_factory=list)
    mod_inputs: dict[int, int] = field(default_factory=dict)

    def _fail(self, name: str) -> None:
        if name in self.fail_on:
            from rigplane.core.exceptions import CommandError

            raise CommandError(f"stub refuses {name}")

    def _applied(self, path: FieldPath, value: Any) -> None:
        if self.observe_store is not None:
            observe(self.observe_store, path, value)

    async def get_freq(self, receiver: int = 0) -> int:
        return self.freq_a

    async def set_freq(self, freq_hz: int, receiver: int = 0) -> None:
        self._fail("set_freq")
        if self.ignore_freq_writes:
            return
        self.freq_a = int(freq_hz)
        self._applied(FieldPath.active("main", "freq_mode", "freq_hz"), self.freq_a)

    async def set_mode(
        self, mode: str, filter_width: int | None = None, receiver: int = 0
    ) -> None:
        self._fail("set_mode")
        self.mode_a = str(mode)
        self._applied(FieldPath.active("main", "freq_mode", "mode"), self.mode_a)

    async def set_data_mode(self, data: int | bool, receiver: int = 0) -> None:
        self._fail("set_data_mode")
        self.data_a = int(bool(data)) if isinstance(data, bool) else int(data)
        self._applied(FieldPath.active("main", "freq_mode", "data_mode"), self.data_a)

    async def get_data_mode(self, receiver: int = 0) -> int:
        return self.data_a

    async def get_split(self) -> bool:
        return self.split

    async def set_split(self, on: bool) -> None:
        self._fail("set_split")
        self.split = bool(on)
        self._applied(FieldPath.global_("tx_state", "split"), self.split)

    async def get_rit_frequency(self) -> int:
        return self.rit_freq

    async def set_rit_frequency(self, freq_hz: int) -> None:
        self._fail("set_rit_frequency")
        self.rit_freq = int(freq_hz)
        self._applied(FieldPath.global_("operator_controls", "rit_freq"), self.rit_freq)

    async def get_rit_status(self) -> bool:
        return self.rit_on

    async def set_rit_status(self, on: bool) -> None:
        self._fail("set_rit_status")
        self.rit_on = bool(on)
        self._applied(FieldPath.global_("tx_state", "rit_on"), self.rit_on)

    async def get_rit_tx_status(self) -> bool:
        return self.rit_tx

    async def set_rit_tx_status(self, on: bool) -> None:
        self._fail("set_rit_tx_status")
        self.rit_tx = bool(on)
        self._applied(FieldPath.global_("tx_state", "rit_tx"), self.rit_tx)

    async def get_rf_power(self) -> int:
        return self.rf_power

    def _normalized_power(self) -> float:
        """Mirror the store contract: ``power_level`` is normalized 0..1."""
        native = float(self.rf_power)
        max_watts = getattr(self.profile, "max_watts", None)
        if (
            self.native_power_unit == "watts"
            and isinstance(max_watts, (int, float))
            and not isinstance(max_watts, bool)
            and max_watts > 0
        ):
            return max(0.0, min(1.0, native / float(max_watts)))
        return max(0.0, min(1.0, native / 255.0))

    async def set_rf_power(self, level: int) -> None:
        self._fail("set_rf_power")
        self.rf_power = int(level)
        self._applied(
            FieldPath.global_("operator_controls", "power_level"),
            self._normalized_power(),
        )

    async def get_key_speed(self) -> int:
        return self.key_speed

    async def set_key_speed(self, speed: int) -> None:
        self._fail("set_key_speed")
        self.key_speed = int(speed)

    async def read_relative_vfo(self, *, selected: bool) -> Any:
        from rigplane.core.radio_protocol import RelativeVfoState

        return RelativeVfoState(
            freq_hz=self.freq_b, mode=self.mode_b, data_mode=self.data_b
        )

    async def write_relative_vfo_frequency(
        self,
        freq_hz: int,
        *,
        selected: bool,
        before_set: Callable[[], None] | None = None,
        after_ack: Callable[[], None] | None = None,
        after_readback: Callable[[], None] | None = None,
    ) -> int:
        self._fail("write_relative_vfo_frequency")
        if self.ignore_freq_writes:
            return self.freq_b
        self.freq_b = int(freq_hz)
        return self.freq_b

    async def write_relative_vfo_mode(
        self,
        mode: int,
        *,
        selected: bool,
        data: bool | int | None = None,
        filter_width: int | None = None,
        before_set: Callable[[], None] | None = None,
    ) -> Any:
        from rigplane.core.radio_protocol import RelativeVfoState
        from rigplane.core.types import Mode

        self._fail("write_relative_vfo_mode")
        self.b_mode_writes.append({"mode": mode, "data": data, "selected": selected})
        try:
            self.mode_b = Mode(int(mode)).name
        except ValueError:
            self.mode_b = str(mode)
        self.data_b = (
            int(data)
            if isinstance(data, int) and not isinstance(data, bool)
            else (1 if data else 0)
        )
        return RelativeVfoState(
            freq_hz=self.freq_b, mode=self.mode_b, data_mode=self.data_b
        )

    async def get_data1_mod_input(self) -> int:
        return self.mod_inputs.get(1, 0)

    async def set_data1_mod_input(self, source: int) -> None:
        self._fail("set_data1_mod_input")
        self.mod_inputs[1] = int(source)

    async def get_data2_mod_input(self) -> int:
        return self.mod_inputs.get(2, 5)

    async def set_data2_mod_input(self, source: int) -> None:
        self._fail("set_data2_mod_input")
        self.mod_inputs[2] = int(source)

    def supports_command(self, name: str) -> bool:
        return name == "set_data_mode"


@dataclass
class StubWattsRadio(StubIcomRadio):
    """Watts-native serial-like fake (Yaesu CAT shape, bool DATA fallback)."""

    model: str = "FT-710"
    native_power_unit: str = "watts"
    backend_id: str = "icom_serial"
    profile: StubProfile = field(
        default_factory=lambda: StubProfile(
            modes=("AM", "LSB", "USB", "CW", "FM"),
            max_watts=100,
            min_watts=0,
        )
    )


class StubMinimalRadio:
    """A radio with no split/RIT/relative-VFO/DATA surface (capability gaps)."""

    model: str = "FT-817"
    native_power_unit: str = "raw_255"

    def __init__(self) -> None:
        self.profile = StubProfile(modes=("AM", "LSB", "USB", "CW", "FM"))
        self.freq_a = 14_074_000
        self.mode_a = "USB"

    async def get_freq(self, receiver: int = 0) -> int:
        return self.freq_a

    async def set_freq(self, freq_hz: int, receiver: int = 0) -> None:
        self.freq_a = int(freq_hz)

    async def set_mode(
        self, mode: str, filter_width: int | None = None, receiver: int = 0
    ) -> None:
        self.mode_a = str(mode)

    async def get_rf_power(self) -> int:
        return 128

    async def set_rf_power(self, level: int) -> None:
        pass

    def supports_command(self, name: str) -> bool:
        return False


@dataclass
class RecordedIntent:
    name: str
    params: dict[str, Any]


class StubCommandQueue:
    """An ordering stub: records entries, completes turns immediately.

    ``hold_ready`` defers the queue turn (the ``positive_tx_ready``
    signal) so a managed key pends at admission: the cancellation race
    needs a deterministic in-flight point. :meth:`release_ready`
    completes every held turn.
    """

    def __init__(self, execute: Callable[[Any], None] | None = None) -> None:
        self.entries: list[Any] = []
        self.sessions: set[str] = set()
        self.hold_ready: bool = False
        self._held_ready: list[Any] = []
        self._generations = itertools.count(1)
        self._execute = execute

    def register_session(self, session_id: str) -> None:
        self.sessions.add(session_id)

    def unregister_session(self, session_id: str) -> None:
        self.sessions.discard(session_id)

    def capture_connection_generation(self) -> object:
        return next(self._generations)

    def put_ordered(self, intent: Any, **kwargs: Any) -> None:
        self.entries.append(intent)
        future = kwargs.get("future")
        ready = kwargs.get("positive_tx_ready")
        if self._execute is not None and intent is not None:
            self._execute(intent)
        if ready is not None and not ready.done():
            if self.hold_ready:
                self._held_ready.append(ready)
            else:
                try:
                    ready.set_result(None)
                except Exception:
                    pass
        if future is not None and not future.done():
            try:
                future.set_result(None)
            except Exception:
                pass

    def release_ready(self) -> None:
        """Complete every held queue turn (test teardown)."""
        held, self._held_ready = self._held_ready, []
        for ready in held:
            if not ready.done():
                try:
                    ready.set_result(None)
                except Exception:
                    pass


class StubCommandService:
    """Records intents; the adapter executes them against the fake radio."""

    def __init__(self) -> None:
        self.intents: list[RecordedIntent] = []

    def record(self, name: str, params: Mapping[str, Any]) -> None:
        self.intents.append(RecordedIntent(name=name, params=dict(params)))


class StubAuthority:
    """A minimal managed-TX authority double with an inspectable lease."""

    def __init__(self) -> None:
        self.owner: str | None = None
        self.submit_calls: list[tuple[bool, str]] = []
        self.disconnect_calls: list[str] = []
        self.other_owner: str | None = None
        self.radio_tx: str | None = None
        self.remote = False

    def held_by_other(self, owner: str | None) -> bool:
        return self.other_owner is not None and self.other_owner != owner

    def radio_tx_refusal_nowait(self) -> str | None:
        return self.radio_tx

    async def snapshot(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            state=SimpleNamespace(intent=self.owner),
            owner=self.owner,
        )

    def snapshot_nowait(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            state=SimpleNamespace(intent=self.owner),
            owner=self.owner,
        )

    async def submit_ptt(self, on: bool, owner: str, **kwargs: Any) -> Any:
        from types import SimpleNamespace

        self.submit_calls.append((on, owner))
        if on:
            if self.held_by_other(owner) or self.radio_tx is not None:
                return SimpleNamespace(outcome="REJECTED")
            self.owner = owner
            return SimpleNamespace(outcome="ACCEPTED")
        if self.owner == owner:
            self.owner = None
        return SimpleNamespace(outcome="ACCEPTED")

    async def owner_disconnect(self, owner: str) -> Any:
        self.disconnect_calls.append(owner)
        if self.owner == owner:
            self.owner = None
        return "ACCEPTED"


def make_handler(
    radio: Any = None,
    store: StateStore | None = None,
    *,
    owner: str = "tci-client-1",
    authority: Any = None,
    queue: StubCommandQueue | None = None,
    service: StubCommandService | None = None,
    controller_authority: Any = None,
    config: Any = None,
) -> Any:
    """Build the adapter handler over fakes (signature mirrors the factory)."""
    from rigplane.tci_adapter.handler import TciAdapterHandler

    return TciAdapterHandler(
        radio=radio if radio is not None else StubIcomRadio(),
        state_store=store if store is not None else StateStore(),
        owner=owner,
        managed_tx_authority=authority,
        command_queue=queue,
        command_service=service,
        controller_authority=controller_authority,
        config=config,
    )


async def collect_burst(handler: Any) -> list[str]:
    return list(await handler.on_connect())
