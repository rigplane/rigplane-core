"""SDR IQ contract types — data carriers for the SDR panadapter (MOR-3151).

This module holds the frozen data half of the ``rigplane.sdr`` contract:
:class:`IqBlock` (one block of complex baseband samples) and
:class:`SdrConfig` (how to open an IQ source). The behavioural protocols
(:class:`~rigplane.sdr.protocol.IqSource`,
:class:`~rigplane.sdr.protocol.IqScopeSink`) live in
:mod:`rigplane.sdr.protocol`; the SoapySDR adapter and the FFT scope are
separate issues and must not grow here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
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
        samples: Complex baseband samples as a 1-D ``numpy.complex64``
            array, one sample per sampling instant at ``sample_rate_hz``,
            baseband to ``center_freq_hz`` (positive frequency axis = +Hz
            from center).
        center_freq_hz: Center (LO) frequency the samples are baseband
            to, in Hz.
        sample_rate_hz: Complex sample rate in Hz.
        timestamp_s: ``time.monotonic()`` capture time of the first
            sample. Monotonic — use for ordering/latency only, never as
            a wall-clock time.
        overflow: Samples were dropped by the source before this block;
            the stream has a gap starting at this block.
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
        gain_db: Hardware gain in dB; ``None`` lets the device run AGC.
        ppm: Frequency error correction in ppm.
        freq_offset_hz: IF-tap offset in Hz; ``0`` for an antenna tap
            such as rig-iq.
        invert_spectrum: Flip the frequency axis (conjugate the samples)
            when the tap inverts sidebands.
        span_hz: Requested display span in Hz; ``None`` = full sample
            rate.
        extra_settings: Driver settings, e.g. ``{"direct_samp": "2"}``
            for RTL-SDR V3 HF direct sampling.
    """

    device_args: str
    sample_rate_hz: int = 2_400_000
    gain_db: float | None = None
    ppm: float = 0.0
    freq_offset_hz: int = 0
    invert_spectrum: bool = False
    span_hz: int | None = None
    extra_settings: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> SdrConfig:
        """Build an :class:`SdrConfig` from a plain mapping.

        Accepts a TOML table / JSON object keyed by field name. Every
        rejected value raises ``ValueError`` naming the offending field.

        Args:
            data: Mapping of field name to value; only :class:`SdrConfig`
                field names are accepted.

        Returns:
            The validated config.

        Raises:
            ValueError: ``device_args`` missing, an unknown key, or a
                value of the wrong type/range — the message names the
                field.
        """
        known = {f.name for f in fields(cls)}
        for key in data:
            if key not in known:
                raise ValueError(
                    f"device config field {key!r} is unknown; "
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

        # Optional fields: only keys that carry a value are validated;
        # the rest fall through to the dataclass defaults.
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
            values["extra_settings"] = dict(value)

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
