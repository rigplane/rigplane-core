"""Frozen data carriers for the ``rigplane.sdr`` IQ contract (MOR-3151)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

__all__ = ["IqBlock", "SdrConfig"]


def _is_set(data: Mapping[str, Any], name: str) -> bool:
    """Whether ``name`` is present in ``data`` with a non-``None`` value."""
    return data.get(name) is not None


@dataclass(frozen=True)
class IqBlock:
    """One block of complex baseband IQ samples from an IQ source.

    Attributes:
        samples: 1-D ``numpy.complex64`` baseband samples; positive
            frequency axis = +Hz from ``center_freq_hz``.
        center_freq_hz: Center (LO) frequency the samples are baseband to.
        sample_rate_hz: Complex sample rate in Hz.
        timestamp_s: ``time.monotonic()`` capture time of the first
            sample; ordering/latency only, never wall-clock.
        overflow: Samples were dropped by the source before this block.
    """

    samples: np.ndarray
    center_freq_hz: int
    sample_rate_hz: int
    timestamp_s: float
    overflow: bool = False


@dataclass(frozen=True)
class SdrConfig:
    """How to open a SoapySDR-backed IQ source.

    Attributes:
        device_args: SoapySDR kwargs string, e.g.
            ``"driver=remote,remote=127.0.0.1,remote:driver=rtlsdr"``.
        sample_rate_hz: Complex sample rate in Hz.
        gain_db: Hardware gain in dB; ``None`` = device AGC.
        ppm: Frequency error correction in ppm.
        freq_offset_hz: IF-tap offset in Hz; ``0`` = antenna tap.
        invert_spectrum: Flip the frequency axis (conjugate the samples).
        span_hz: Display span in Hz; ``None`` = full sample rate.
        extra_settings: Driver settings (e.g. ``direct_samp=2`` for
            RTL-SDR V3 HF); stored read-only.
    """

    device_args: str
    sample_rate_hz: int = 2_400_000
    gain_db: float | None = None
    ppm: float = 0.0
    freq_offset_hz: int = 0
    invert_spectrum: bool = False
    span_hz: int | None = None
    extra_settings: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType[str, str]({})
    )

    def __post_init__(self) -> None:
        # Wrap any plain mapping so every construction path stores a
        # read-only mapping, not just from_mapping().
        if not isinstance(self.extra_settings, MappingProxyType):
            object.__setattr__(
                self, "extra_settings", MappingProxyType(dict(self.extra_settings))
            )

    def __hash__(self) -> int:
        """Hash the field values (``extra_settings`` by sorted items).

        ``MappingProxyType`` delegates ``hash()`` to its wrapped ``dict``
        (unhashable), so the frozen-dataclass default hash would raise.
        """
        return hash(
            (
                self.device_args,
                self.sample_rate_hz,
                self.gain_db,
                self.ppm,
                self.freq_offset_hz,
                self.invert_spectrum,
                self.span_hz,
                tuple(sorted(self.extra_settings.items())),
            )
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> SdrConfig:
        """Build an :class:`SdrConfig` from a plain mapping (TOML/JSON table).

        Args:
            data: Mapping of field name to value; only :class:`SdrConfig`
                field names are accepted.

        Raises:
            ValueError: ``device_args`` missing, an unknown key, or a
                wrong-typed/ranged value — the message names the field.
        """
        known = {f.name for f in fields(cls)}
        for key in data:
            if key not in known:
                raise ValueError(
                    f"SdrConfig field {key!r} is unknown; "
                    f"known fields: {', '.join(sorted(known))}"
                )

        if "device_args" not in data:
            raise ValueError(
                "device_args: required (SoapySDR kwargs string, "
                "e.g. 'driver=remote,remote=127.0.0.1,remote:driver=rtlsdr')"
            )
        device_args = data["device_args"]
        if not isinstance(device_args, str):
            raise ValueError(
                f"device_args: must be a string, got {type(device_args).__name__}"
            )

        # Only keys that carry a value are validated; the rest keep defaults.
        values: dict[str, Any] = {"device_args": device_args}

        if _is_set(data, "sample_rate_hz"):
            values["sample_rate_hz"] = cls._positive_int(data, "sample_rate_hz")

        if _is_set(data, "gain_db"):
            values["gain_db"] = cls._real_number(data, "gain_db")

        if _is_set(data, "ppm"):
            values["ppm"] = cls._real_number(data, "ppm")

        if _is_set(data, "freq_offset_hz"):
            values["freq_offset_hz"] = cls._plain_int(data, "freq_offset_hz")

        if _is_set(data, "invert_spectrum"):
            value = data["invert_spectrum"]
            if not isinstance(value, bool):
                raise ValueError(
                    f"invert_spectrum: must be a bool, got {type(value).__name__}"
                )
            values["invert_spectrum"] = value

        if _is_set(data, "span_hz"):
            values["span_hz"] = cls._positive_int(data, "span_hz")

        if _is_set(data, "extra_settings"):
            value = data["extra_settings"]
            if not isinstance(value, Mapping):
                raise ValueError(
                    f"extra_settings: must be a mapping of str to str, "
                    f"got {type(value).__name__}"
                )
            for key, item in value.items():
                if not isinstance(key, str) or not isinstance(item, str):
                    raise ValueError(
                        f"extra_settings: keys and values must be strings, "
                        f"got {key!r} -> {item!r}"
                    )
            values["extra_settings"] = MappingProxyType(dict(value))

        return cls(**values)

    @staticmethod
    def _plain_int(data: Mapping[str, Any], name: str) -> int:
        """Read ``name`` as an exact ``int`` (bool rejected)."""
        value = data[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name}: must be an integer, got {type(value).__name__}")
        return int(value)

    @staticmethod
    def _positive_int(data: Mapping[str, Any], name: str) -> int:
        """Read ``name`` as a strictly positive ``int`` (bool rejected)."""
        value = SdrConfig._plain_int(data, name)
        if value <= 0:
            raise ValueError(f"{name}: must be positive, got {value}")
        return value

    @staticmethod
    def _real_number(data: Mapping[str, Any], name: str) -> float:
        """Read ``name`` as an ``int`` or ``float`` (bool rejected)."""
        value = data[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name}: must be a number, got {type(value).__name__}")
        return float(value)
