"""Server-side SDR scope runtime (MOR-3157).

Builds an IQ source through an injectable factory (default
:class:`~rigplane.sdr.soapy_source.SoapyIqSource`), feeds an
:class:`~rigplane.sdr.iq_scope.IqFftScope`, keeps the IQ window on the
radio VFO via :class:`~rigplane.sdr.controller.SdrScopeController`, and
validates the ``--sdr-*`` flags into an :class:`SdrConfig`.

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

import asyncio
import glob
import logging
import os
import tempfile
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import replace
from typing import Any, Literal

from rigplane.scope import ScopeFrame

from .controller import SdrScopeController
from .iq_scope import IqFftScope
from .protocol import IqSource
from .types import IqBlock, SdrConfig

__all__ = [
    "SPAN_LIMIT_RATIO",
    "SdrScopeRuntime",
    "apply_remote_only_env",
    "default_source_factory",
    "remote_only_env_updates",
    "resolve_sdr_config",
]

logger = logging.getLogger(__name__)

#: Max display span / sample rate: the controller's per-edge guard band
#: cannot cover a wider span.
SPAN_LIMIT_RATIO = 0.6

#: Seconds without a frame after which the SDR counts as dropped.
FRAME_STALE_S = 2.0


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


class SdrScopeRuntime:
    """One live SDR scope pipeline for ``/api/v1/scope`` (MOR-3157).

    ``source.on_block`` → :class:`IqFftScope` → frame callback; the
    controller is driven from radio state fed by the server. Frames hop
    to the event loop via :meth:`asyncio.loop.call_soon_threadsafe` only
    — the scope worker emits them while holding its lock. :attr:`active`
    flips ``False`` after ``frame_stale_s`` s without a frame and back
    when frames return. The read-only status surface for the public
    ``sdr`` state object (MOR-3201) is :attr:`state`, :attr:`last_error`,
    :attr:`overflow_count`, :attr:`tx_frozen`, :attr:`span_hz`.
    """

    def __init__(
        self,
        config: SdrConfig,
        *,
        source_factory: Callable[[SdrConfig], IqSource] | None = None,
        scope_factory: Callable[[], IqFftScope] | None = None,
        clock: Callable[[], float] = time.monotonic,
        frame_stale_s: float = FRAME_STALE_S,
    ) -> None:
        self._config = config
        self._clock = clock
        self._frame_stale_s = float(frame_stale_s)
        self._source_factory = source_factory or default_source_factory
        self._scope_factory = scope_factory or (
            lambda: IqFftScope(invert_spectrum=config.invert_spectrum)
        )
        self._source: IqSource | None = None
        self._scope: IqFftScope | None = None
        self._controller: SdrScopeController | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._frame_sink: Callable[[ScopeFrame], None] | None = None
        self._started = False
        self._active = False
        self._last_frame_s: float | None = None
        self._started_at_s = 0.0
        self._last_error: str | None = None
        self._overflow_count = 0

    @property
    def started(self) -> bool:
        """Whether the pipeline is running (between start and stop)."""
        return self._started

    @property
    def active(self) -> bool:
        """Whether SDR frames currently flow (within the stale window)."""
        return self._started and self._active

    @property
    def state(
        self,
    ) -> Literal["disabled", "starting", "streaming", "reconnecting", "error"]:
        """Public status state for the web ``sdr`` object (MOR-3201).

        ``error`` — the last :meth:`start` failed (source factory or
        open); a device lost mid-stream reconnects in the background
        and reads ``reconnecting``, not ``error``. ``disabled`` — not
        started. ``starting`` — started, no frame delivered yet.
        ``streaming`` — a frame delivered within the stale window.
        ``reconnecting`` — started, no frame within ``frame_stale_s``.
        """
        if not self._started:
            return "error" if self._last_error is not None else "disabled"
        if not self._active:
            return "reconnecting"
        return "starting" if self._last_frame_s is None else "streaming"

    @property
    def last_error(self) -> str | None:
        """Message of the last failed :meth:`start`; ``None`` otherwise."""
        return self._last_error

    @property
    def overflow_count(self) -> int:
        """Blocks flagged ``overflow`` since the last successful start."""
        return self._overflow_count

    @property
    def tx_frozen(self) -> bool:
        """Whether the controller currently holds the display TX-freeze
        (set on the TX rising edge, released ``TX_HOLD_S`` after it;
        the flag lives in ``SdrScopeController._tx_sink_active``, which
        has no public accessor)."""
        controller = self._controller
        return controller is not None and controller._tx_sink_active

    @property
    def span_hz(self) -> int:
        """Effective display span the controller configured the sink with."""
        controller = self._controller
        return controller.span_hz if controller is not None else 0

    @property
    def frequency_in_range(self) -> bool:
        """Whether the tracked VFO is inside the source's tunable range."""
        controller = self._controller
        return controller.frequency_in_range if controller is not None else True

    def start(
        self,
        on_frame: Callable[[ScopeFrame], None],
        *,
        freq_hz: int | None = None,
        tx_active: bool = False,
    ) -> None:
        """Build and open the pipeline (running event loop). The seed
        state retunes the source before it opens: already at the VFO.
        A source-factory or open failure is recorded in :attr:`last_error`
        (state ``"error"``) and re-raised after the half-built pipeline
        is closed."""
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        self._frame_sink = on_frame

        try:
            source = self._source_factory(self._config)
            scope = self._scope_factory()
            controller = SdrScopeController(source, scope, self._config, self._clock)
            controller.on_radio_state(freq_hz, tx_active)
        except Exception as exc:
            self._last_error = str(exc)
            raise

        self._source, self._scope, self._controller = source, scope, controller
        self._started = self._active = True
        self._last_frame_s = None
        self._started_at_s = self._clock()
        self._last_error = None
        self._overflow_count = 0

        scope.on_frame(self._on_scope_frame)
        source.on_block(self._on_block)
        try:
            source.open()
        except Exception as exc:
            self.stop()
            self._last_error = str(exc)
            raise
        logger.info(
            "SDR scope started (device=%r, span_hz=%s)",
            self._config.device_args,
            controller.span_hz,
        )

    def stop(self) -> None:
        """Close the pipeline; idempotent, safe from any thread. Clears
        :attr:`last_error` — a deliberate stop is not an error."""
        source, scope = self._source, self._scope
        self._source = self._scope = self._controller = None
        self._loop = self._frame_sink = None
        self._started = False
        self._active = False
        self._last_error = None
        if source is not None:
            source.on_block(None)
            source.close()
        if scope is not None:
            scope.on_frame(None)
            scope.close()
        logger.info("SDR scope stopped")

    def on_radio_state(self, freq_hz: int | None, tx_active: bool) -> None:
        """Feed one radio state snapshot to the controller."""
        if self._controller is not None:
            self._controller.on_radio_state(freq_hz, tx_active)

    def tick(self) -> None:
        """Run liveness tracking and the controller's periodic actions."""
        if not self._started:
            return
        now = self._clock()
        last = (
            self._last_frame_s if self._last_frame_s is not None else self._started_at_s
        )
        self._active = (now - last) <= self._frame_stale_s
        if self._controller is not None:
            self._controller.tick()

    def _on_block(self, block: IqBlock) -> None:
        """Reader thread: enqueue one block; centre-0 blocks carry no
        tuning and are dropped."""
        scope = self._scope
        if not self._started or scope is None:
            return
        if block.overflow:
            self._overflow_count += 1
        if block.center_freq_hz == 0:
            return
        scope.feed(block)

    def _on_scope_frame(self, frame: ScopeFrame) -> None:
        """Scope worker thread (lock held): no I/O — hop to the loop."""
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._deliver_frame, frame)
        except RuntimeError:
            pass  # loop closed during shutdown; the frame is dropped

    def _deliver_frame(self, frame: ScopeFrame) -> None:
        """Event loop: mark out-of-range frames, hand over to the sink."""
        if not self._started:
            return
        self._last_frame_s = self._clock()
        controller = self._controller
        if (
            controller is not None
            and not controller.frequency_in_range
            and not frame.out_of_range
        ):
            frame = replace(frame, out_of_range=True)
        sink = self._frame_sink
        if sink is not None:
            sink(frame)
