"""SDR source construction and CLI configuration (MOR-3157).

Builds the production :class:`~rigplane.sdr.soapy_source.SoapyIqSource`
and validates the ``--sdr-*`` flags into an :class:`SdrConfig`. Shared
scope composition lives in :mod:`rigplane.sdr.scope_source`.

GPL boundary: SoapySDR loads every module library it finds at import
time, and distro builds ship GPL-licensed driver modules alongside the
BSD-licensed ``remote`` client. Before this process first imports
SoapySDR, :func:`default_source_factory` restricts the module search so
only the ``remote`` module can load when ``driver=remote`` is used:
``SOAPY_SDR_ROOT`` points at an empty directory and
``SOAPY_SDR_PLUGIN_PATH`` at the Debian ``modules0.8`` directory
holding ``libremoteSupport.so`` (``/usr/lib/<arch>/SoapySDR/``). An
existing ``SOAPY_SDR_PLUGIN_PATH`` is honoured untouched; when the
module is not found there, a warning is logged and no restriction is
applied.
"""

from __future__ import annotations

import glob
import logging
import os
import tempfile
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import Any

from .protocol import IqSource
from .types import IqSourceSpec, SdrConfig

__all__ = [
    "SPAN_LIMIT_RATIO",
    "apply_remote_only_env",
    "build_source",
    "default_source_factory",
    "register_source_kind",
    "remote_only_env_updates",
    "resolve_sdr_config",
]

logger = logging.getLogger(__name__)

#: Max display span / sample rate: the controller's per-edge guard band
#: cannot cover a wider span.
SPAN_LIMIT_RATIO = 0.6


def resolve_sdr_config(
    *,
    device_args: str | None = None,
    sample_rate_hz: int | None = None,
    gain: str | None = None,
    ppm: float | None = None,
    freq_offset_hz: int | None = None,
    span_hz: int | None = None,
    invert_spectrum: bool | None = None,
    settings: Sequence[str] | None = None,
) -> SdrConfig | None:
    """Validate the ``--sdr-*`` CLI values into an :class:`SdrConfig`
    (``None`` = flag not given; no device → ``None``; ``settings`` are
    repeatable ``KEY=VAL`` → ``extra_settings``). Raises
    :class:`ValueError` naming the flag for a bad value or an
    over-wide span."""
    if not device_args:
        return None

    values: dict[str, Any] = {"device_args": device_args}
    if sample_rate_hz is not None:
        values["sample_rate_hz"] = int(sample_rate_hz)
    if freq_offset_hz is not None:
        values["freq_offset_hz"] = int(freq_offset_hz)
    if span_hz is not None:
        values["span_hz"] = int(span_hz)
    if ppm is not None:
        values["ppm"] = float(ppm)
    if gain is not None and gain.strip().lower() != "auto":
        try:
            values["gain_db"] = float(gain)
        except ValueError:
            raise ValueError(
                f"--sdr-gain {gain!r} is neither a number nor 'auto'"
            ) from None
    if invert_spectrum is not None:
        values["invert_spectrum"] = invert_spectrum
    if settings:
        extra: dict[str, str] = {}
        for item in settings:
            key, sep, value = item.partition("=")
            if not sep or not key:
                raise ValueError(f"--sdr-setting {item!r} must be KEY=VAL")
            extra[key] = value
        values["extra_settings"] = extra
    config = SdrConfig.from_mapping(values)
    if config.span_hz is not None and config.span_hz > SPAN_LIMIT_RATIO * (
        config.sample_rate_hz
    ):
        raise ValueError(
            f"--sdr-span-hz: span {config.span_hz} Hz exceeds "
            f"{SPAN_LIMIT_RATIO} × --sdr-sample-rate "
            f"({config.sample_rate_hz} Hz); reduce the span or raise the rate"
        )
    return config


# ---------------------------------------------------------------------------
# GPL boundary: restrict SoapySDR to the remote module
# ---------------------------------------------------------------------------

_DEBIAN_MODULES_GLOB = "/usr/lib/*/SoapySDR/modules0.8"
_REMOTE_MODULE_LIB = "libremoteSupport.so"


def uses_remote_driver(device_args: str) -> bool:
    """Whether a SoapySDR kwargs string selects ``driver=remote``."""
    for part in device_args.split(","):
        key, sep, value = part.partition("=")
        if sep and key.strip() == "driver" and value.strip() == "remote":
            return True
    return False


def remote_only_env_updates(
    device_args: str,
    env: Mapping[str, str],
    remote_module_dir: str | None,
    *,
    empty_root: str,
) -> dict[str, str]:
    """Pure GPL-boundary decision: the env updates restricting SoapySDR.
    Empty (no restriction) unless ``driver=remote``, no operator-set
    ``SOAPY_SDR_PLUGIN_PATH``, and the module directory was found."""
    if not uses_remote_driver(device_args):
        return {}
    if env.get("SOAPY_SDR_PLUGIN_PATH"):
        return {}
    if remote_module_dir is None:
        return {}
    return {
        "SOAPY_SDR_ROOT": empty_root,
        "SOAPY_SDR_PLUGIN_PATH": remote_module_dir,
    }


def apply_remote_only_env(
    device_args: str,
    *,
    env: MutableMapping[str, str] | None = None,
    find_dir: Callable[[], str | None] | None = None,
    create_root: Callable[[], str] | None = None,
) -> bool:
    """Apply the remote-only restriction to ``env`` (default
    ``os.environ``). Returns whether the module search is restricted
    (already restricted counts); warns and leaves ``env`` untouched when
    the module is missing. ``find_dir``/``create_root`` are injectable
    for tests."""
    if env is None:
        env = os.environ
    if not uses_remote_driver(device_args) or env.get("SOAPY_SDR_PLUGIN_PATH"):
        return bool(env.get("SOAPY_SDR_PLUGIN_PATH"))
    if find_dir is None:

        def find_dir() -> str | None:
            for d in sorted(glob.glob(_DEBIAN_MODULES_GLOB)):
                if os.path.exists(os.path.join(d, _REMOTE_MODULE_LIB)):
                    return d
            return None

    module_dir = find_dir()
    if module_dir is None:
        logger.warning(
            "SoapySDR remote module not found under %s; cannot restrict the "
            "module search, GPL-licensed SoapySDR plugins may be imported",
            _DEBIAN_MODULES_GLOB,
        )
        return False
    empty_root = (
        tempfile.mkdtemp(prefix="rigplane-soapy-root-")
        if create_root is None
        else create_root()
    )
    env["SOAPY_SDR_ROOT"] = empty_root
    env["SOAPY_SDR_PLUGIN_PATH"] = module_dir
    logger.info(
        "SoapySDR module search restricted to the remote module (%s)", module_dir
    )
    return True


def default_source_factory(config: SdrConfig) -> IqSource:
    """Build the production :class:`SoapyIqSource`; the GPL module-search
    restriction is applied before SoapySDR is first imported here."""
    apply_remote_only_env(config.device_args)
    from .soapy_source import SoapyIqSource

    return SoapyIqSource(config)


_SOURCE_FACTORIES: dict[str, Callable[[Any], IqSource]] = {}


def register_source_kind(kind: str, factory: Callable[[Any], IqSource]) -> None:
    """Register the factory that builds sources for one spec ``kind``."""
    if kind in _SOURCE_FACTORIES:
        raise ValueError(f"IQ source kind {kind!r} is already registered")
    _SOURCE_FACTORIES[kind] = factory


def build_source(spec: IqSourceSpec) -> IqSource:
    """Build an IQ source through the factory registered for ``spec.kind``."""
    kind = spec.kind
    try:
        factory = _SOURCE_FACTORIES[kind]
    except KeyError:
        known = ", ".join(sorted(_SOURCE_FACTORIES))
        raise ValueError(
            f"Unknown IQ source kind {kind!r}; known kinds: {known}"
        ) from None
    return factory(spec)


register_source_kind("soapy", lambda spec: default_source_factory(spec.config))
