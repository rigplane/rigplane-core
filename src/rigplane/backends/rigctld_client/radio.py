"""RigPlane Radio adapter for an external Hamlib ``rigctld`` server."""

from __future__ import annotations

from typing import Any

from ...exceptions import CommandError
from ...radio_state import RadioState
from .transport import RigctldTransport

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
