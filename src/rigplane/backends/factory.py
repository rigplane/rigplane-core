"""Backend factory for assembling radio implementations from typed config."""

from __future__ import annotations

from collections.abc import Callable

from ..radio import IcomRadio  # noqa: TID251
from ..radio_protocol import Radio
from .config import (
    BackendConfig,
    LanBackendConfig,
    RigctldBackendConfig,
    SerialBackendConfig,
    YaesuCatBackendConfig,
)
from ._icom_serial_base import _IcomSerialRadioBase
from .ic7300.serial import Ic7300SerialRadio
from .ic705.serial import Ic705SerialRadio, XieguSerialRadio
from .ic9700.serial import Ic9700SerialRadio
from .icom7610.serial import Icom7610SerialRadio
from .rigctld_client.radio import RigctldClientRadio
from .yaesu_cat.radio import YaesuCatRadio

#: Yaesu CAT models routed to the YaesuCatRadio serial backend. Any model
#: starting with ``FT`` is treated as Yaesu CAT too (:func:`create_radio`).
_YAESU_MODELS = frozenset({"FTX-1", "FT-710", "FT-991A", "FT-991", "FTDX101", "FTDX10"})


def _is_yaesu_serial_model(model: str) -> bool:
    """Return whether *model* (upper-cased) routes to the Yaesu CAT backend."""
    return model in _YAESU_MODELS or model.startswith("FT")


#: Single model -> serial backend dispatch table, the one source shared by
#: :func:`create_radio` and :func:`supports_serial_model` (MOR-3777): no
#: second allowlist may grow beside it. Values are deferred constructors:
#: the class symbol is read from module globals only when the lambda runs,
#: so ``monkeypatch.setattr(factory, "Ic7300SerialRadio", fake)`` keeps
#: reaching the dispatch (review R1). X6100 has no serial backend and
#: stays refused.
_SERIAL_MODEL_CLASSES: dict[str, Callable[[], type[_IcomSerialRadioBase]]] = {
    "IC-705": lambda: Ic705SerialRadio,
    "IC-7300": lambda: Ic7300SerialRadio,
    # X6200 shares the IC-705 CI-V session (same default address 0xA4 —
    # see MOR-170, Hamlib's x6100_priv_caps reused by both x6100_caps and
    # x6200_caps). Its own subclass keeps the unmeasured 50 ms gap; the
    # command set still comes from the loaded rig via ``model``.
    "X6200": lambda: XieguSerialRadio,
    "IC-7300MK2": lambda: Ic7300SerialRadio,
    "IC-9700": lambda: Ic9700SerialRadio,
    "IC-7610": lambda: Icom7610SerialRadio,
}


def supports_serial_model(model: str | None) -> bool:
    """Return whether *model* routes to a native serial backend.

    Answers from the same dispatch table and Yaesu prefix rule that
    :func:`create_radio` uses, so route catalogs (setup wizards,
    higher-level products) follow the factory instead of keeping a second
    model allowlist in sync. ``None`` or blank — refused by
    :func:`create_radio` — is ``False`` here. X6100 stays unsupported.
    """
    if model is None or not model.strip():
        return False
    normalized = model.upper()
    return _is_yaesu_serial_model(normalized) or normalized in _SERIAL_MODEL_CLASSES


def create_radio(config: BackendConfig) -> Radio:
    """Create a radio instance for the selected backend config.

    Routes to model-specific backends for serial connections.
    For LAN, uses profile-driven routing (model parameter handled by IcomRadio).
    """
    if isinstance(config, LanBackendConfig):
        return IcomRadio(
            host=config.host,
            port=config.port,
            username=config.username,
            password=config.password,
            radio_addr=config.radio_addr,
            timeout=config.timeout,
            audio_codec=config.audio_codec,
            audio_sample_rate=config.audio_sample_rate,
            audio_codec_explicit=config.audio_codec_explicit,
            audio_sample_rate_explicit=config.audio_sample_rate_explicit,
            auto_reconnect=config.auto_reconnect,
            reconnect_delay=config.reconnect_delay,
            reconnect_max_delay=config.reconnect_max_delay,
            watchdog_timeout=config.watchdog_timeout,
            auto_recover_audio=config.auto_recover_audio,
            cache_ttl_s=config.cache_ttl_s,
            profile=config.profile,
            model=config.model,
        )
    if isinstance(config, YaesuCatBackendConfig):
        return YaesuCatRadio(
            device=config.device,
            baudrate=config.baudrate,
            rx_device=config.rx_device,
            tx_device=config.tx_device,
            audio_sample_rate=config.audio_sample_rate,
        )
    if isinstance(config, RigctldBackendConfig):
        return RigctldClientRadio(
            host=config.host,
            port=config.port,
            timeout=config.timeout,
            model=config.model,
        )
    if isinstance(config, SerialBackendConfig):
        # Route to model-specific serial backend
        if config.model is None or not config.model.strip():
            # R59/MOR-2425: the default this replaced drove an IC-7300 as an
            # IC-7610. Not resolved from ``radio_addr`` either.
            raise ValueError(
                "Serial backend needs an explicit radio model: pass --model "
                "(e.g. --model IC-7300); rigplane does not guess a default rig."
            )
        model = config.model.upper()

        if _is_yaesu_serial_model(model):
            return YaesuCatRadio(
                device=config.device,
                baudrate=config.baudrate or 38400,
                rx_device=config.rx_device,
                tx_device=config.tx_device,
                audio_sample_rate=config.audio_sample_rate or 48000,
            )

        constructor = _SERIAL_MODEL_CLASSES.get(model)
        if constructor is None:
            # MOR-174: never silently impersonate an IC-7610 — driving real
            # hardware with the wrong CI-V personality is a foot-gun.
            raise ValueError(
                f"Unsupported serial model {model!r}; supported: "
                f"{', '.join(sorted(_SERIAL_MODEL_CLASSES))}, "
                "and Yaesu FT-series (FTX-1, FT-710, ...)."
            )

        return constructor()(
            device=config.device,
            baudrate=config.baudrate,
            radio_addr=config.radio_addr,
            timeout=config.timeout,
            audio_codec=config.audio_codec,
            audio_sample_rate=config.audio_sample_rate,
            rx_device=config.rx_device,
            tx_device=config.tx_device,
            ptt_mode=config.ptt_mode,
            allow_low_baud_scope=config.allow_low_baud_scope,
            profile=config.profile,
            model=config.model,
        )

    backend = getattr(config, "backend", None)
    if backend in {"lan", "serial", "yaesu-cat", "rigctld"}:
        raise TypeError(
            "Unsupported config instance for backend "
            f"{backend!r}; use typed backend config dataclasses."
        )
    raise ValueError(
        "Unsupported backend. Expected backend 'lan', 'serial', "
        "'yaesu-cat', or 'rigctld'."
    )


__all__ = ["create_radio", "supports_serial_model"]
