"""Radio contributor — model, FW, backend, capabilities, audio codec; IPs masked."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from rigplane.core.radio_protocol import (
    AudioCapable,
    RigctldRoutable,
    StatePollable,
    UsbAudioCapable,
)
from rigplane.diagnostics.redaction import (
    redact_credentials,
    redact_hostnames,
    redact_ips,
    redact_paths,
)

if TYPE_CHECKING:
    from rigplane.diagnostics.contributor import BundleContext


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _redact(s: str | None) -> str | None:
    if s is None:
        return None
    return redact_credentials(redact_ips(redact_paths(s)))


def _redact_host(s: str | None) -> str | None:
    """Host-field redactor: full chain plus hostname scrubbing.

    ``redact_hostnames`` is intentionally NOT in the generic ``_redact`` chain
    because it would over-redact common identifiers (``radio.json``,
    ``IC-7610.fw``, etc.) on other fields. Apply only where DNS-shape values
    are expected — the radio's ``host``/``_host`` connection field.
    """
    if s is None:
        return None
    return redact_credentials(redact_hostnames(redact_ips(redact_paths(s))))


class RadioConnectionFacts(TypedDict):
    """Link facts for triage, serialized as ``radio.json#/connection``.

    The producer-owned contract for the connection block (MOR-4654):
    consumers (support tooling, tests) read these exact fields through
    this type, never re-guessed dict keys. Every radio contributes what
    it has — ``connected``/``conn_state`` from the radio's public
    surface, the identity status naming a silent-but-present port
    (``no_response``/``checking``/``identity_mismatch``), ``last_error``
    carrying the backend's plain-words no-answer sentence, and
    ``link_loss_episodes`` counting serial link-down edges (LAN
    watchdogs count their own reconnects elsewhere). Missing surfaces
    are ``None``, never a per-radio branch.
    """

    connected: bool
    conn_state: str | None
    identity_status: str | None
    last_error: str | None
    link_loss_episodes: int | None


def _connection_facts(radio: Any) -> RadioConnectionFacts:
    """Link facts for triage: state, identity answer, latest error.

    ``connected``/``conn_state`` come from the radio's public surface;
    the identity status names a silent-but-present port
    (``no_response``/``checking``/``identity_mismatch``); ``last_error``
    carries the backend's plain-words no-answer sentence;
    ``link_loss_episodes`` counts serial link-down edges (LAN watchdogs
    count their own reconnects elsewhere).
    """
    conn_state = _safe_attr(radio, "conn_state")
    conn_state_value = getattr(conn_state, "value", conn_state)
    identity = _safe_attr(radio, "connection_identity")
    identity_status = getattr(getattr(identity, "status", None), "value", None)
    episodes = _safe_attr(radio, "serial_reconnects_total")
    last_error = _safe_attr(radio, "last_error")
    return {
        "connected": bool(_safe_attr(radio, "connected", False)),
        "conn_state": (conn_state_value if isinstance(conn_state_value, str) else None),
        "identity_status": (
            identity_status if isinstance(identity_status, str) else None
        ),
        "last_error": _redact(last_error) if isinstance(last_error, str) else None,
        "link_loss_episodes": episodes if type(episodes) is int else None,
    }


def _capabilities(radio: Any) -> list[str]:
    caps: list[str] = []
    if isinstance(radio, AudioCapable):
        caps.append("AudioCapable")
    if isinstance(radio, StatePollable):
        caps.append("StatePollable")
    if isinstance(radio, RigctldRoutable):
        caps.append("RigctldRoutable")
    if isinstance(radio, UsbAudioCapable):
        caps.append("UsbAudioCapable")
    return caps


class RadioContributor:
    """Emits ``radio/radio.json`` with radio model, FW, backend, capabilities."""

    name = "radio"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        radio = ctx.radio
        if radio is None:
            payload: dict[str, Any] = {
                "available": False,
                "note": "ctx.radio is None — invocation context has no live session",
            }
        else:
            payload = {
                "available": True,
                "model": _redact(
                    _safe_attr(radio, "model") or _safe_attr(radio, "radio_model")
                ),
                "firmware_version": _redact(
                    _safe_attr(radio, "firmware_version")
                    or _safe_attr(radio, "fw_version")
                ),
                "backend": _redact(
                    _safe_attr(radio, "backend_id")
                    or _safe_attr(radio, "backend_name")
                    or radio.__class__.__name__
                ),
                "capabilities": _capabilities(radio),
                "audio_codec": str(_safe_attr(radio, "audio_codec") or "unknown"),
                # Live runtimes store connection info as ``_host``/``_port``
                # (private attrs). Try public first for stub-friendliness,
                # then fall back so an active LAN session reports its real
                # endpoint.
                "host": _redact_host(
                    _safe_attr(radio, "host") or _safe_attr(radio, "_host")
                ),
                "port": _safe_attr(radio, "port") or _safe_attr(radio, "_port"),
                # MOR-4654: the link facts that say plainly what is wrong
                # while the radio is silent — all duck-typed so LAN, serial
                # and stub radios contribute what they have (``None``
                # otherwise), never a per-radio branch.
                "connection": _connection_facts(radio),
            }
        text = json.dumps(payload, indent=2, sort_keys=True)
        (output_dir / "radio.json").write_text(text + "\n", encoding="utf-8")
