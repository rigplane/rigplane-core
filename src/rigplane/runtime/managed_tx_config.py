"""Durable configuration for the managed-transmit software time-out."""

from __future__ import annotations

import json
import math
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_MANAGED_TX_TOT_SECONDS = 180.0
_FORMAT_VERSION = 1
_Replace = Callable[[Path, Path], None]


@dataclass(frozen=True, slots=True)
class ManagedTxTotConfig:
    """The one app-level software-TOT input supplied to later authority wiring."""

    timeout_seconds: float | None


class ManagedTxTotConfigStore:
    """Load and atomically replace only the app-level software-TOT setting."""

    def __init__(self, path: Path, *, replace: _Replace | None = None) -> None:
        self._path = path
        self._replace = replace or os.replace
        self._config = self._load()

    @property
    def config(self) -> ManagedTxTotConfig:
        return self._config

    def set_timeout_seconds(self, value: object) -> ManagedTxTotConfig:
        config = ManagedTxTotConfig(_normalize_timeout_seconds(value))
        self._persist(config)
        self._config = config
        return config

    def _load(self) -> ManagedTxTotConfig:
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ManagedTxTotConfig(DEFAULT_MANAGED_TX_TOT_SECONDS)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("managed TX TOT config is invalid") from exc
        return _parse_document(document)

    def _persist(self, config: ManagedTxTotConfig) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(_document_for(config), stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            self._replace(temporary, self._path)
        except Exception:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise


def _parse_document(document: Any) -> ManagedTxTotConfig:
    if not isinstance(document, dict) or set(document) != {
        "software_tot_seconds",
        "version",
    }:
        raise ValueError("managed TX TOT config is invalid")
    version = document["version"]
    if type(version) is not int or version != _FORMAT_VERSION:
        raise ValueError("managed TX TOT config is invalid")
    try:
        timeout_seconds = _normalize_timeout_seconds(document["software_tot_seconds"])
    except ValueError as exc:
        raise ValueError("managed TX TOT config is invalid") from exc
    return ManagedTxTotConfig(timeout_seconds)


def _document_for(config: ManagedTxTotConfig) -> dict[str, float | int | None]:
    return {"version": _FORMAT_VERSION, "software_tot_seconds": config.timeout_seconds}


def _normalize_timeout_seconds(value: object) -> float | None:
    if type(value) not in (int, float):
        raise ValueError("timeout seconds must be finite-positive or zero")
    seconds = float(value)
    if seconds == 0:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("timeout seconds must be finite-positive or zero")
    return seconds
