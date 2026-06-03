"""RigPlane Radio adapter for an external Hamlib ``rigctld`` server."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

from ...exceptions import CommandError
from ...core.state_pipeline_contracts import (
    CommandSource,
    FieldPath,
    Observation,
    SourceMetadata,
)
from ...radio_state import RadioState
from .transport import RigctldTransport

if TYPE_CHECKING:
    from ..._poller_types import CommandQueue, CommandQueueEntry
    from ...core.command_service import CommandService
    from .observations import RigctldClientObservationAdapter

logger = logging.getLogger(__name__)

_SUPPORTED_COMMANDS = {
    "get_freq",
    "set_freq",
    "get_mode",
    "set_mode",
    "get_ptt",
    "set_ptt",
    "get_vfo_slot",
    "set_vfo_slot",
    "get_rf_gain",
    "set_rf_gain",
    "get_af_level",
    "set_af_level",
    "get_preamp",
    "set_preamp",
    "get_attenuator",
    "set_attenuator",
    "get_attenuator_level",
    "set_attenuator_level",
    "get_nb",
    "set_nb",
    "get_nr",
    "set_nr",
}


@dataclass(frozen=True, slots=True)
class _ReadbackCorrelation:
    command_id: str
    source: CommandSource
    session_id: str | None
    path: FieldPath
    value: Any
    command_service: "CommandService"


class RigctldClientObservationPoller:
    """Production observation poller for external Hamlib rigctld reads."""

    def __init__(
        self,
        radio: "RigctldClientRadio",
        callback: Callable[[Sequence["Observation"]], None],
        *,
        medium_interval: float = 2.0,
        slow_interval: float = 30.0,
        command_queue: "CommandQueue | None" = None,
    ) -> None:
        self._radio = radio
        self._callback = callback
        self._command_queue = command_queue
        self._medium_interval = medium_interval
        self._slow_interval = slow_interval
        self._tasks: list[asyncio.Task[None]] = []
        self._pending_readback_entries: list[_ReadbackCorrelation] = []

    async def start(self) -> None:
        if self._tasks:
            return
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._run_loop(self._poll_medium, self._medium_interval)),
            loop.create_task(self._run_loop(self._poll_slow, self._slow_interval)),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run_loop(
        self,
        poll: Callable[[], Awaitable[None]],
        interval: float,
    ) -> None:
        while True:
            try:
                await poll()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("rigctld-client observation poll failed", exc_info=True)
            await asyncio.sleep(interval)

    async def _poll_medium(self) -> None:
        from .observations import RigctldClientObservationAdapter

        drained_entries = await self._drain_commands()
        adapter = RigctldClientObservationAdapter(self._radio)
        observations: list["Observation"] = list(
            await adapter.read_freq_mode_controls()
        )
        observations.append(await adapter.read_ptt())
        active_vfo = await adapter.read_active_vfo()
        if active_vfo is not None:
            observations.append(active_vfo)
        observations.extend(
            await _read_drained_slow_control_readbacks(adapter, drained_entries)
        )
        self._callback(self._annotate_readback_observations(observations))

    async def _poll_slow(self) -> None:
        from .observations import RigctldClientObservationAdapter

        adapter = RigctldClientObservationAdapter(self._radio)
        self._callback(
            self._annotate_readback_observations(await adapter.read_slow_controls())
        )

    async def _drain_commands(self) -> tuple["CommandQueueEntry", ...]:
        """Process pending Web command queue entries before rigctld readback."""
        if self._command_queue is None or not self._command_queue.has_commands:
            return ()

        successful: list["CommandQueueEntry"] = []
        for entry in self._command_queue.drain_entries():
            cmd = entry.command
            if entry.future is not None and entry.future.cancelled():
                logger.debug(
                    "rigctld-client observation poller: skipping cancelled command %s",
                    type(cmd).__name__,
                )
                continue
            try:
                await self._execute_command(cmd)
                successful.append(entry)
                self._track_readback_entry(entry)
                if entry.future is not None and not entry.future.done():
                    entry.future.set_result(None)
            except Exception as exc:
                self._mark_queued_command_failed(entry, exc)
                if entry.future is not None and not entry.future.done():
                    entry.future.set_exception(exc)
                logger.warning(
                    "rigctld-client observation poller: command %s failed",
                    type(cmd).__name__,
                    exc_info=True,
                )
        return tuple(successful)

    def _track_readback_entry(self, entry: "CommandQueueEntry") -> None:
        correlation = _readback_correlation_for_entry(entry)
        if correlation is None:
            return
        self._pending_readback_entries.append(correlation)

    def _annotate_readback_observations(
        self,
        observations: Sequence[Observation],
    ) -> tuple[Observation, ...]:
        if not self._pending_readback_entries:
            return tuple(observations)

        unmatched = list(self._pending_readback_entries)
        annotated: list[Observation] = []
        for observation in observations:
            match_index = _matching_readback_entry_index(observation, unmatched)
            if match_index is None:
                annotated.append(observation)
                continue
            entry = unmatched.pop(match_index)
            annotated.append(_with_command_metadata(observation, entry))
        self._pending_readback_entries = []
        _discard_readback_correlations(unmatched)
        return tuple(annotated)

    async def _execute_command(self, cmd: Any) -> None:
        from ..._poller_types import (
            PttOff,
            PttOn,
            SelectVfo,
            SetAfLevel,
            SetAttenuator,
            SetFreq,
            SetMode,
            SetNB,
            SetNR,
            SetPreamp,
            SetRfGain,
        )

        match cmd:
            case SetFreq(freq=freq, receiver=rx):
                await self._radio.set_freq(freq, receiver=rx)
            case SetMode(mode=mode, filter_width=filter_width, receiver=rx):
                await self._radio.set_mode(
                    mode,
                    filter_width=filter_width,
                    receiver=rx,
                )
            case PttOn():
                await self._radio.set_ptt(True)
            case PttOff():
                await self._radio.set_ptt(False)
            case SelectVfo(vfo=vfo):
                await self._radio.set_vfo_slot(vfo)
            case SetRfGain(level=level, receiver=rx):
                await self._radio.set_rf_gain(level, receiver=rx)
            case SetAfLevel(level=level, receiver=rx):
                await self._radio.set_af_level(level, receiver=rx)
            case SetPreamp(level=level, receiver=rx):
                await self._radio.set_preamp(level, receiver=rx)
            case SetAttenuator(db=db, receiver=rx):
                await self._radio.set_attenuator_level(db, receiver=rx)
            case SetNB(on=on, receiver=rx):
                await self._radio.set_nb(on, receiver=rx)
            case SetNR(on=on, receiver=rx):
                await self._radio.set_nr(on, receiver=rx)
            case _:
                raise CommandError(
                    f"{type(cmd).__name__} is not supported by external rigctld"
                )

    @staticmethod
    def _mark_queued_command_failed(
        entry: "CommandQueueEntry",
        exc: BaseException,
    ) -> None:
        if entry.command_service is None or entry.command_id is None:
            return
        params: dict[str, Any] = {
            "message": str(exc) or None,
            "session_id": entry.session_id,
        }
        if entry.source is not None:
            params["source"] = entry.source
        entry.command_service.fail_command(entry.command_id, **params)


async def _read_drained_slow_control_readbacks(
    adapter: "RigctldClientObservationAdapter",
    entries: Sequence["CommandQueueEntry"],
) -> tuple[Observation, ...]:
    from ..._poller_types import SetAttenuator, SetNB, SetNR, SetPreamp

    commands = tuple(entry.command for entry in entries)
    observations: list[Observation] = []
    if any(isinstance(command, SetPreamp) for command in commands):
        observations.append(await adapter.read_preamp())
    if any(isinstance(command, SetAttenuator) for command in commands):
        observations.append(await adapter.read_attenuator())
    if any(isinstance(command, SetNB) for command in commands):
        observations.append(await adapter.read_nb())
    if any(isinstance(command, SetNR) for command in commands):
        observations.append(await adapter.read_nr())
    return tuple(observations)


def _readback_correlation_for_entry(
    entry: "CommandQueueEntry",
) -> _ReadbackCorrelation | None:
    if entry.command_service is None or entry.command_id is None or entry.source is None:
        return None
    expectations = entry.command_service.readback_expectations(
        source=entry.source,
        session_id=entry.session_id,
        command_id=entry.command_id,
    )
    if not expectations:
        return None
    expectation = expectations[0]
    return _ReadbackCorrelation(
        command_id=entry.command_id,
        source=entry.source,
        session_id=entry.session_id,
        path=expectation.path,
        value=expectation.value,
        command_service=entry.command_service,
    )


def _discard_readback_correlations(entries: Sequence[_ReadbackCorrelation]) -> None:
    for entry in entries:
        entry.command_service.discard_readback_expectations(
            source=entry.source,
            session_id=entry.session_id,
            command_id=entry.command_id,
            path=entry.path,
        )


def _matching_readback_entry_index(
    observation: Observation,
    entries: Sequence[_ReadbackCorrelation],
) -> int | None:
    for index, entry in enumerate(entries):
        if _entry_matches_observation(entry, observation):
            return index
    return None


def _entry_matches_observation(
    entry: _ReadbackCorrelation,
    observation: Observation,
) -> bool:
    if not _is_external_rigctld_readback(observation.source):
        return False
    return (
        entry.value == observation.value
        and _readback_paths_match(observation.path, entry.path)
    )


def _with_command_metadata(
    observation: Observation,
    entry: _ReadbackCorrelation,
) -> Observation:
    source = observation.source
    return Observation(
        path=observation.path,
        value=observation.value,
        source=SourceMetadata(
            source=source.source,
            provider=source.provider,
            transport=source.transport,
            native_id=source.native_id,
            capability_id=source.capability_id,
            command_source=entry.source,
            session_id=entry.session_id,
        ),
        timestamp_monotonic=observation.timestamp_monotonic,
        quality=observation.quality,
        correlation_id=entry.command_id,
        max_age=observation.max_age,
    )


def _readback_paths_match(readback_path: FieldPath, overlay_path: FieldPath) -> bool:
    if readback_path == overlay_path:
        return True
    return _external_rigctld_main_alias(readback_path) == (
        _external_rigctld_main_alias(overlay_path)
    )


def _external_rigctld_main_alias(path: FieldPath) -> FieldPath:
    if path.scope.value != "receiver" or path.receiver_id != "0":
        return path
    if path.family.value == "freq_mode" and path.slot is None:
        return FieldPath.active("main", path.family.value, path.name)
    return FieldPath.receiver("main", path.family.value, path.name)


def _is_external_rigctld_readback(source: SourceMetadata) -> bool:
    return (
        source.source == "hamlib_response"
        and source.provider == "external_rigctld"
        and source.transport == "rigctld"
    )


class RigctldClientRadio:
    """Minimal Radio implementation backed by external Hamlib ``rigctld``."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 4532,
        timeout: float = 5.0,
        model: str | None = None,
        transport: RigctldTransport | None = None,
    ) -> None:
        self._transport = transport or RigctldTransport(
            host=host,
            port=port,
            timeout=timeout,
        )
        self._model = model or "External rigctld"
        self._state = RadioState()
        self._vfo_supported = False

    async def connect(self) -> None:
        await self._transport.connect()
        await self._probe_vfo_support()

    async def disconnect(self) -> None:
        await self._transport.close()

    async def __aenter__(self) -> "RigctldClientRadio":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.disconnect()

    @property
    def connected(self) -> bool:
        return self._transport.connected

    @property
    def radio_ready(self) -> bool:
        return self.connected

    @property
    def radio_state(self) -> RadioState:
        return self._state

    @property
    def model(self) -> str:
        return self._model

    @property
    def backend_id(self) -> str:
        return "rigctld"

    @property
    def capabilities(self) -> set[str]:
        caps = {"tx", "rf_gain", "af_level", "preamp", "attenuator", "nb", "nr"}
        if self._vfo_supported:
            caps.add("vfo")
        return caps

    def supports_command(self, command: str) -> bool:
        if command in {"get_vfo_slot", "set_vfo_slot"}:
            return self._vfo_supported
        return command in _SUPPORTED_COMMANDS

    def create_observation_poller(
        self,
        *,
        callback: Callable[[Sequence["Observation"]], None],
        command_queue: "CommandQueue | None" = None,
    ) -> RigctldClientObservationPoller:
        """Construct a backend-neutral observation poller for Web startup."""
        return RigctldClientObservationPoller(
            self,
            callback=callback,
            command_queue=command_queue,
        )

    async def get_freq(self, receiver: int = 0) -> int:
        self._require_main_receiver(receiver, "get_freq")
        line = (await self._transport.query("f", response_lines=1))[0]
        try:
            freq = int(line)
        except ValueError as exc:
            raise CommandError(
                f"External rigctld returned malformed frequency: {line!r}."
            ) from exc
        if freq < 0:
            raise CommandError(
                f"External rigctld returned invalid negative frequency: {freq}."
            )
        self._state.main.freq = freq
        return freq

    async def set_freq(self, freq: int, receiver: int = 0) -> None:
        self._require_main_receiver(receiver, "set_freq")
        if freq <= 0:
            raise ValueError("freq must be > 0 Hz")
        await self._transport.command(f"F {int(freq)}")
        self._state.main.freq = int(freq)

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        self._require_main_receiver(receiver, "get_mode")
        mode, passband = await self._transport.query("m", response_lines=2)
        mode = mode.strip().upper()
        if not mode:
            raise CommandError("External rigctld returned an empty mode.")
        try:
            passband_hz = int(passband)
        except ValueError as exc:
            raise CommandError(
                f"External rigctld returned malformed passband: {passband!r}."
            ) from exc
        self._state.main.mode = mode
        self._state.main.filter_width = passband_hz
        return mode, passband_hz

    async def set_mode(
        self,
        mode: str,
        filter_width: int | None = None,
        receiver: int = 0,
    ) -> None:
        self._require_main_receiver(receiver, "set_mode")
        normalized = mode.strip().upper()
        if not normalized:
            raise ValueError("mode must be non-empty")
        command = f"M {normalized}"
        if filter_width is not None:
            if filter_width < 0:
                raise ValueError("filter_width must be >= 0")
            command = f"{command} {int(filter_width)}"
        await self._transport.command(command)
        self._state.main.mode = normalized
        self._state.main.filter_width = filter_width

    async def get_data_mode(self) -> bool:
        return False

    async def set_data_mode(self, on: int | bool, receiver: int = 0) -> None:
        self._require_main_receiver(receiver, "set_data_mode")
        if on:
            raise CommandError(
                "External rigctld backend does not support RigPlane data mode."
            )

    async def get_ptt(self) -> bool:
        line = (await self._transport.query("t", response_lines=1))[0]
        if line not in {"0", "1"}:
            raise CommandError(f"External rigctld returned malformed PTT: {line!r}.")
        ptt = line == "1"
        self._state.ptt = ptt
        return ptt

    async def set_ptt(self, on: bool) -> None:
        await self._transport.command(f"T {1 if on else 0}")
        self._state.ptt = bool(on)

    async def get_vfo_slot(self, receiver: int = 0) -> str:
        self._require_main_receiver(receiver, "get_vfo_slot")
        line = (await self._transport.query("v", response_lines=1))[0]
        slot = _normalize_vfo_slot(line)
        self._state.main.active_slot = slot
        return slot

    async def set_vfo_slot(self, slot: str, receiver: int = 0) -> None:
        self._require_main_receiver(receiver, "set_vfo_slot")
        normalized = _normalize_vfo_slot(slot)
        await self._transport.command(f"V VFO{normalized}")
        self._state.main.active_slot = normalized

    async def get_rf_gain(self, receiver: int = 0) -> int:
        self._require_main_receiver(receiver, "get_rf_gain")
        line = (await self._transport.query("l RF", response_lines=1))[0]
        return _parse_level_255(line, "RF gain")

    async def set_rf_gain(self, level: int, receiver: int = 0) -> None:
        self._require_main_receiver(receiver, "set_rf_gain")
        await self._transport.command(f"L RF {_level_255_to_float(level)}")

    async def get_af_level(self, receiver: int = 0) -> int:
        self._require_main_receiver(receiver, "get_af_level")
        line = (await self._transport.query("l AF", response_lines=1))[0]
        return _parse_level_255(line, "AF level")

    async def set_af_level(self, level: int, receiver: int = 0) -> None:
        self._require_main_receiver(receiver, "set_af_level")
        await self._transport.command(f"L AF {_level_255_to_float(level)}")

    async def get_preamp(self, receiver: int = 0) -> int:
        self._require_main_receiver(receiver, "get_preamp")
        line = (await self._transport.query("l PREAMP", response_lines=1))[0]
        return _preamp_db_to_level(_parse_int_level(line, "preamp"))

    async def set_preamp(self, level: int, receiver: int = 0) -> None:
        self._require_main_receiver(receiver, "set_preamp")
        await self._transport.command(f"L PREAMP {_preamp_level_to_db(level)}")

    async def get_attenuator(self, receiver: int = 0) -> bool:
        self._require_main_receiver(receiver, "get_attenuator")
        return await self.get_attenuator_level(receiver) > 0

    async def set_attenuator(self, on: bool, receiver: int = 0) -> None:
        self._require_main_receiver(receiver, "set_attenuator")
        await self._transport.command(f"L ATT {'6' if on else '0'}")

    async def get_attenuator_level(self, receiver: int = 0) -> int:
        self._require_main_receiver(receiver, "get_attenuator_level")
        line = (await self._transport.query("l ATT", response_lines=1))[0]
        return _parse_int_level(line, "attenuator")

    async def set_attenuator_level(self, db: int, receiver: int = 0) -> None:
        self._require_main_receiver(receiver, "set_attenuator_level")
        await self._transport.command(f"L ATT {int(db)}")

    async def get_nb(self) -> bool:
        line = (await self._transport.query("u NB", response_lines=1))[0]
        return _parse_func(line, "noise blanker")

    async def set_nb(self, on: bool, receiver: int = 0) -> None:
        self._require_main_receiver(receiver, "set_nb")
        await self._transport.command(f"U NB {1 if on else 0}")

    async def get_nr(self) -> bool:
        line = (await self._transport.query("u NR", response_lines=1))[0]
        return _parse_func(line, "noise reduction")

    async def set_nr(self, on: bool, receiver: int = 0) -> None:
        self._require_main_receiver(receiver, "set_nr")
        await self._transport.command(f"U NR {1 if on else 0}")

    async def _probe_vfo_support(self) -> None:
        try:
            await self.get_vfo_slot()
        except CommandError:
            self._vfo_supported = False
        else:
            self._vfo_supported = True

    @staticmethod
    def _require_main_receiver(receiver: int, operation: str) -> None:
        if receiver != 0:
            raise ValueError(
                f"{operation}: external rigctld backend only supports receiver 0"
            )


def _normalize_vfo_slot(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"A", "VFOA"}:
        return "A"
    if normalized in {"B", "VFOB"}:
        return "B"
    raise CommandError(f"External rigctld returned unsupported VFO: {value!r}.")


def _level_255_to_float(level: int) -> str:
    """Format a RigPlane 0..255 level as a rigctl 0.0..1.0 string (3 decimals)."""
    clamped = max(0, min(255, int(level)))
    return f"{clamped / 255:.3f}"


def _float_to_level_255(value: float) -> int:
    """Convert a rigctl 0.0..1.0 level to a clamped RigPlane 0..255 integer."""
    return max(0, min(255, round(value * 255)))


def _parse_level_255(line: str, name: str) -> int:
    try:
        value = float(line)
    except ValueError as exc:
        raise CommandError(
            f"External rigctld returned malformed {name}: {line!r}."
        ) from exc
    return _float_to_level_255(value)


def _parse_int_level(line: str, name: str) -> int:
    try:
        return int(float(line))
    except ValueError as exc:
        raise CommandError(
            f"External rigctld returned malformed {name}: {line!r}."
        ) from exc


def _parse_func(line: str, name: str) -> bool:
    stripped = line.strip()
    if stripped not in {"0", "1"}:
        raise CommandError(f"External rigctld returned malformed {name}: {line!r}.")
    return stripped == "1"


def _preamp_level_to_db(level: int) -> str:
    """Map a RigPlane preamp level (0/1/2) to a rigctl dB string."""
    return {0: "0", 1: "10", 2: "20"}.get(int(level), "0")


def _preamp_db_to_level(db: int) -> int:
    """Map a rigctl preamp dB reading to a RigPlane preamp level (0/1/2)."""
    if db <= 0:
        return 0
    if db <= 15:
        return 1
    return 2
