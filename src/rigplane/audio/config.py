"""Optional audio.toml configuration — persists device selection across runs.

Precedence: CLI flags > audio.toml > defaults.

Example ``audio.toml``::

    [bridge]
    device = "BlackHole 2ch"
    tx_device = "BlackHole 16ch"
    rx_only = false

    [bridge.reconnect]
    max_retries = 5
    retry_delay = 1.0

    [usb]
    rx_device = "USB Audio CODEC"
    tx_device = "USB Audio CODEC"
    last_rx_uid = "AppleUSBAudioEngine:Burr-Brown:USB Audio CODEC:1.0"
    last_tx_uid = "AppleUSBAudioEngine:Burr-Brown:USB Audio CODEC:1.0"
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["AudioConfig", "load_audio_config", "save_audio_config"]

_DEFAULT_PATH = Path("audio.toml")


@dataclass
class BridgeReconnectConfig:
    max_retries: int = 5
    retry_delay: float = 1.0


@dataclass
class BridgeConfig:
    device: str | None = None
    tx_device: str | None = None
    rx_only: bool = False
    label: str | None = None
    reconnect: BridgeReconnectConfig = field(default_factory=BridgeReconnectConfig)


@dataclass
class UsbConfig:
    rx_device: str | None = None
    tx_device: str | None = None
    last_rx_uid: str | None = None
    last_tx_uid: str | None = None


@dataclass
class AudioConfig:
    """Parsed audio.toml configuration."""

    bridge: BridgeConfig = field(default_factory=BridgeConfig)
    usb: UsbConfig = field(default_factory=UsbConfig)

    def merge_cli(
        self,
        *,
        bridge_device: str | None = None,
        bridge_tx_device: str | None = None,
        bridge_rx_only: bool | None = None,
        bridge_label: str | None = None,
        bridge_max_retries: int | None = None,
        bridge_retry_delay: float | None = None,
        usb_rx_device: str | None = None,
        usb_tx_device: str | None = None,
    ) -> "AudioConfig":
        """Return a new config with CLI overrides applied (CLI > config > defaults)."""
        b = BridgeConfig(
            device=bridge_device if bridge_device is not None else self.bridge.device,
            tx_device=bridge_tx_device
            if bridge_tx_device is not None
            else self.bridge.tx_device,
            rx_only=bridge_rx_only
            if bridge_rx_only is not None
            else self.bridge.rx_only,
            label=bridge_label if bridge_label is not None else self.bridge.label,
            reconnect=BridgeReconnectConfig(
                max_retries=(
                    bridge_max_retries
                    if bridge_max_retries is not None
                    else self.bridge.reconnect.max_retries
                ),
                retry_delay=(
                    bridge_retry_delay
                    if bridge_retry_delay is not None
                    else self.bridge.reconnect.retry_delay
                ),
            ),
        )
        u = UsbConfig(
            rx_device=usb_rx_device
            if usb_rx_device is not None
            else self.usb.rx_device,
            tx_device=usb_tx_device
            if usb_tx_device is not None
            else self.usb.tx_device,
            last_rx_uid=self.usb.last_rx_uid,
            last_tx_uid=self.usb.last_tx_uid,
        )
        return AudioConfig(bridge=b, usb=u)


def _find_config_path() -> Path | None:
    """Search for audio.toml in cwd, then XDG config, then home."""
    candidates = [
        Path.cwd() / "audio.toml",
    ]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(Path(xdg) / "icom-lan" / "audio.toml")
    candidates.append(Path.home() / ".config" / "icom-lan" / "audio.toml")

    for p in candidates:
        if p.is_file():
            return p
    return None


def load_audio_config(path: Path | str | None = None) -> AudioConfig:
    """Load audio config from a TOML file.

    Args:
        path: Explicit path. If ``None``, searches standard locations.

    Returns:
        Parsed :class:`AudioConfig` (defaults if file not found).
    """
    if path is not None:
        config_path = Path(path)
    else:
        config_path = _find_config_path()  # type: ignore[assignment]

    if config_path is None or not config_path.is_file():
        return AudioConfig()

    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef,import-not-found]

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        import sys as _sys

        logger.warning("Failed to parse %s", config_path, exc_info=True)
        print(
            f"Warning: failed to parse {config_path}: {exc} — using defaults",
            file=_sys.stderr,
        )
        return AudioConfig()

    logger.info("Loaded audio config from %s", config_path)
    return _parse(data)


def _parse(data: dict[str, Any]) -> AudioConfig:
    bridge_data = data.get("bridge", {})
    reconnect_data = bridge_data.get("reconnect", {})
    usb_data = data.get("usb", {})

    return AudioConfig(
        bridge=BridgeConfig(
            device=bridge_data.get("device"),
            tx_device=bridge_data.get("tx_device"),
            rx_only=bridge_data.get("rx_only", False),
            label=bridge_data.get("label"),
            reconnect=BridgeReconnectConfig(
                max_retries=reconnect_data.get("max_retries", 5),
                retry_delay=reconnect_data.get("retry_delay", 1.0),
            ),
        ),
        usb=UsbConfig(
            rx_device=usb_data.get("rx_device"),
            tx_device=usb_data.get("tx_device"),
            last_rx_uid=usb_data.get("last_rx_uid"),
            last_tx_uid=usb_data.get("last_tx_uid"),
        ),
    )


def save_audio_config(config: AudioConfig, path: Path | str) -> None:
    """Save audio config to a TOML file.

    Used to persist last-used device UIDs after successful bridge runs.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def _esc(s: str) -> str:
        """Escape a string for TOML double-quoted value."""
        return s.replace("\\", "\\\\").replace('"', '\\"')

    lines: list[str] = []
    lines.append("[bridge]")
    if config.bridge.device:
        lines.append(f'device = "{_esc(config.bridge.device)}"')
    if config.bridge.tx_device:
        lines.append(f'tx_device = "{_esc(config.bridge.tx_device)}"')
    if config.bridge.rx_only:
        lines.append("rx_only = true")
    if config.bridge.label:
        lines.append(f'label = "{_esc(config.bridge.label)}"')
    lines.append("")
    lines.append("[bridge.reconnect]")
    lines.append(f"max_retries = {config.bridge.reconnect.max_retries}")
    lines.append(f"retry_delay = {config.bridge.reconnect.retry_delay}")
    lines.append("")
    lines.append("[usb]")
    if config.usb.rx_device:
        lines.append(f'rx_device = "{_esc(config.usb.rx_device)}"')
    if config.usb.tx_device:
        lines.append(f'tx_device = "{_esc(config.usb.tx_device)}"')
    if config.usb.last_rx_uid:
        lines.append(f'last_rx_uid = "{_esc(config.usb.last_rx_uid)}"')
    if config.usb.last_tx_uid:
        lines.append(f'last_tx_uid = "{_esc(config.usb.last_tx_uid)}"')
    lines.append("")

    path.write_text("\n".join(lines))
    logger.info("Saved audio config to %s", path)
