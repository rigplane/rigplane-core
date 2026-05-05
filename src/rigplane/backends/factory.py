"""Backend factory for assembling radio implementations from typed config."""

from __future__ import annotations

import logging

from ..radio import IcomRadio  # noqa: TID251
from ..radio_protocol import Radio
from .config import (
    BackendConfig,
    LanBackendConfig,
    SerialBackendConfig,
    YaesuCatBackendConfig,
)
from .ic7300.serial import Ic7300SerialRadio
from .ic705.serial import Ic705SerialRadio
from .ic9700.serial import Ic9700SerialRadio
from .icom7610.serial import Icom7610SerialRadio
from .yaesu_cat.radio import YaesuCatRadio


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
    if isinstance(config, SerialBackendConfig):
        # Route to model-specific serial backend
        model = (config.model or "IC-7610").upper()

        # Yaesu CAT radios (FTX-1, FT-710, FT-991A, etc.)
        _YAESU_MODELS = {"FTX-1", "FT-710", "FT-991A", "FT-991", "FTDX101", "FTDX10"}
        if model in _YAESU_MODELS or model.startswith("FT"):
            return YaesuCatRadio(
                device=config.device,
                baudrate=config.baudrate or 38400,
                rx_device=config.rx_device,
                tx_device=config.tx_device,
                audio_sample_rate=config.audio_sample_rate or 48000,
            )

        serial_class: (
            type[Ic705SerialRadio]
            | type[Ic7300SerialRadio]
            | type[Ic9700SerialRadio]
            | type[Icom7610SerialRadio]
        )
        if model == "IC-705":
            serial_class = Ic705SerialRadio
        elif model == "IC-7300":
            serial_class = Ic7300SerialRadio
        elif model == "IC-9700":
            serial_class = Ic9700SerialRadio
        else:
            # Default to IC-7610 for compatibility
            logging.getLogger(__name__).warning(
                "Unknown model %r, defaulting to IC-7610",
                model,
            )
            serial_class = Icom7610SerialRadio

        return serial_class(
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
    if backend in {"lan", "serial", "yaesu-cat"}:
        raise TypeError(
            "Unsupported config instance for backend "
            f"{backend!r}; use typed backend config dataclasses."
        )
    raise ValueError(
        "Unsupported backend. Expected backend 'lan', 'serial', or 'yaesu-cat'."
    )


__all__ = ["create_radio"]
