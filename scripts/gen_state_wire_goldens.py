#!/usr/bin/env python3
"""Regenerate the golden wire payloads the frontend client chain is tested on.

For every profile in ``rigs/*.toml``, a ``WebServer`` built with no radio and
``WebConfig(radio_model=<model>)`` produces:

- ``capabilities``: the body ``WebServer._serve_capabilities`` writes;
- ``allUnobserved``: ``WebServer.build_state_update_envelope(force_full=True)``
  over an empty StateStore;
- ``allObserved``: the same call after every field path the profile's
  ``[state_acquisition]`` declares has been observed once. A profile with no
  ``[state_acquisition]`` block declares nothing, so it has no ``allObserved``.
- ``polledDcd*`` (MOR-3630): for every Icom CI-V profile that polls the
  receiver ``dcd`` leaf, the capabilities body and the ``full`` envelope the
  web server builds around a real ``IcomRadio`` after one polled CI-V
  0x15 0x01 SQL-status reply ("Open": 0x01, "Closed": 0x00) or none at all
  ("Unanswered"). The radio ingests the reply through its own CI-V
  state-cache path, so the BUSY lamp's wire inputs (the ``dcd`` value, its
  ``fieldStatus`` row, the ``squelch`` capability tag only a radio-backed
  server publishes) come from the producers, not from this script.
  ``tests/test_dcd_busy_wire_e2e.py`` proves the same fields arrive over
  the real HTTP and WebSocket API from a polled simulator stack.

``_EXCLUDED_MODELS`` profiles contribute a ``capabilities`` entry only: their
state payloads are left out to keep the committed file small.

Observed values are fixed per field type, not calibrated readings. A declared
str or object field this script has no value for raises instead of getting a
placeholder. ``updatedAt``, every ``lastObservedMonotonic``,
``radioHealth.sinceMs``, ``webrtc.available`` and the audio-jitter environment
variables are pinned, so a run does not vary with the clock, with aiortc being
installed, or with those variables.

Usage:
    python scripts/gen_state_wire_goldens.py           # write the golden
    python scripts/gen_state_wire_goldens.py --check   # exit 1 if it is stale
    python scripts/gen_state_wire_goldens.py --stdout  # print it only
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import os
import sys
from pathlib import Path
from typing import Any, cast
from unittest import mock

# Repo layout: scripts/ -> repo root; the package lives under src/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from rigplane.commands._frame import (  # noqa: E402
    CONTROLLER_ADDR,
    build_civ_frame,
    parse_civ_frame,
)
from rigplane.core.state_pipeline_contracts import (  # noqa: E402
    DEFAULT_FIELD_REGISTRY,
    FieldFamily,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.types import ToneSquelchType  # noqa: E402
from rigplane.profiles import RadioProfile, get_radio_profile  # noqa: E402
from rigplane.profiles.rig_loader import discover_rigs  # noqa: E402
from rigplane.radio import IcomRadio  # noqa: E402
from rigplane.runtime._state_queries import (  # noqa: E402
    build_state_queries,
    wire_parts_for_query,
)
from rigplane.web.runtime_helpers import declared_field_paths  # noqa: E402
from rigplane.web.server import WebConfig, WebServer  # noqa: E402

GOLDEN_PATH = (
    _REPO_ROOT
    / "frontend"
    / "src"
    / "lib"
    / "transport"
    / "__tests__"
    / "fixtures"
    / "state-wire-goldens.json"
)

_PINNED_UPDATED_AT = "2026-01-01T00:00:00+00:00"
_PINNED_MONOTONIC = 1000.0
_PINNED_SINCE_MS = 0
# Read by ``get_audio_rx_jitter_floor_ms`` / ``get_audio_rx_jitter_ceiling_ms``.
_JITTER_ENV_VARS = (
    "ICOM_AUDIO_RX_JITTER_FLOOR_MS",
    "ICOM_AUDIO_RX_JITTER_CEILING_MS",
)

# State payloads for these models stay out of the golden to keep the committed
# file small; their capabilities entries are still written.
_EXCLUDED_MODELS = frozenset({"IC-7300MK2", "X6100"})

# RFC 5737 documentation address: the radios below are never connected.
_UNCONNECTED_RADIO_HOST = "192.0.2.1"
# The single-byte CI-V 0x15 0x01 SQL-status payload of a polled reply.
_SQL_OPEN = 0x01
_SQL_CLOSED = 0x00
_POLLED_DCD_CASES = (
    ("polledDcdOpen", _SQL_OPEN),
    ("polledDcdClosed", _SQL_CLOSED),
    ("polledDcdUnanswered", None),
)
_DCD_PATH = FieldPath.receiver("main", "operator_toggles", "dcd")

_INT_BY_NAME = {"filter_width": 2400, "receiver": 0}
_BOOL_BY_NAME = {"ptt": False}


class _CaptureWriter:
    """The two ``StreamWriter`` methods ``_send_response`` calls."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None


def _golden_value(path: FieldPath, value_type: str, profile: RadioProfile) -> object:
    """The observed value for one declared field path.

    ``active`` is MAIN, the TX target is MAIN and the scope receiver is 0,
    which the client's receiver-topology check accepts for a single-receiver
    profile.
    """
    if value_type == "bool":
        return _BOOL_BY_NAME.get(path.name, True)
    if value_type == "float":
        # ``ServerStatePublic`` types the global meter leaves as int.
        return 1 if path.family is FieldFamily.METERS else 0.5
    if value_type == "int":
        if path.name == "freq_hz":
            return profile.freq_ranges[0].bands[0].default
        return _INT_BY_NAME.get(path.name, 1)
    band = profile.freq_ranges[0].bands[0]
    if value_type == "str":
        by_name = {
            "active": "MAIN",
            "mode": profile.modes[0],
            "tone_squelch_type": ToneSquelchType.OFF.value,
        }
    elif value_type == "object":
        by_name = {
            "tx_target": {
                "status": "known",
                "receiver": "MAIN",
                "slot": None,
                "frequencyHz": None,
            },
            "fixed_edge": {
                "range_index": 1,
                "edge": 1,
                "start_hz": band.start,
                "end_hz": band.end,
            },
        }
    else:
        raise ValueError(f"{path}: unknown value type {value_type!r}")
    if path.name not in by_name:
        raise ValueError(
            f"{path}: no golden value for {value_type} field {path.name!r}"
        )
    return by_name[path.name]


def _observe_declared(server: WebServer, profile: RadioProfile) -> None:
    acquisition = profile.state_acquisition
    if acquisition is None:
        raise ValueError(f"{profile.id} declares no state_acquisition")
    source = SourceMetadata(source="poll_response", provider=acquisition.provider)
    for path in sorted(declared_field_paths(acquisition), key=str):
        spec = DEFAULT_FIELD_REGISTRY.require(path)
        server.command_state_store.apply_current(
            Observation(
                path=path,
                value=_golden_value(path, spec.value_type, profile),
                source=source,
                timestamp_monotonic=_PINNED_MONOTONIC,
            )
        )


def _full_envelope(server: WebServer) -> dict[str, Any]:
    envelope = server.build_state_update_envelope(force_full=True)
    data = envelope["data"]
    data["updatedAt"] = _PINNED_UPDATED_AT
    # The server derives the health age from the monotonic clock on every
    # build, so pin it or the golden varies with machine speed.
    radio_health = data.get("radioHealth")
    if radio_health is not None and "sinceMs" in radio_health:
        radio_health["sinceMs"] = _PINNED_SINCE_MS
    for status in data["fieldStatus"].values():
        if "lastObservedMonotonic" in status:
            status["lastObservedMonotonic"] = _PINNED_MONOTONIC
    return cast("dict[str, Any]", envelope)


def _capabilities(server: WebServer) -> dict[str, Any]:
    writer = _CaptureWriter()
    asyncio.run(server._serve_capabilities(cast("asyncio.StreamWriter", writer)))
    head, _, body = bytes(writer.buffer).partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0]
    if status_line != b"HTTP/1.1 200 OK":
        raise RuntimeError(f"capabilities request failed: {status_line!r}")
    capabilities = cast("dict[str, Any]", json.loads(body))
    capabilities["webrtc"]["available"] = False
    return capabilities


@functools.cache
def polled_dcd_models() -> tuple[str, ...]:
    """The golden models whose profile polls the main receiver ``dcd`` leaf.

    Icom CI-V profiles only: the polled reply is a CI-V frame. The
    capabilities-only ``_EXCLUDED_MODELS`` contribute no state payloads.
    """
    models = []
    for model in sorted(discover_rigs(_REPO_ROOT / "rigs")):
        acquisition = get_radio_profile(model).state_acquisition
        if (
            model not in _EXCLUDED_MODELS
            and acquisition is not None
            and acquisition.provider == "icom_civ"
            and _DCD_PATH in acquisition.pollable_paths()
        ):
            models.append(model)
    return tuple(models)


def _polled_dcd_reply(profile: RadioProfile, sql_byte: int) -> bytes:
    """The radio's answer to the profile's own polled main-receiver 0x15 0x01.

    The wire form (plain, or cmd29-wrapped on dual-receiver Icoms) comes from
    the profile's built state queries, the same ones the scheduler sends.
    """
    query = next(
        query
        for query in build_state_queries(profile)
        if query.command == 0x15 and query.sub == 0x01 and query.receiver in (None, 0)
    )
    command, sub, data = wire_parts_for_query(query, 0)
    return build_civ_frame(
        CONTROLLER_ADDR,
        profile.civ_addr,
        command,
        sub=sub,
        data=data + bytes([sql_byte]),
    )


def _polled_dcd_server(model: str, sql_byte: int | None) -> WebServer:
    """A web server around a real, never-connected radio after one polled reply."""
    radio = IcomRadio(_UNCONNECTED_RADIO_HOST, model=model)
    if sql_byte is not None:
        reply = _polled_dcd_reply(get_radio_profile(model), sql_byte)
        radio._update_state_cache_from_frame(parse_civ_frame(reply))
    return WebServer(radio, WebConfig(radio_model=model))


def _build_polled_dcd_goldens(model: str) -> dict[str, Any]:
    golden: dict[str, Any] = {
        case: _full_envelope(_polled_dcd_server(model, sql_byte))
        for case, sql_byte in _POLLED_DCD_CASES
    }
    golden["polledDcdCapabilities"] = _capabilities(_polled_dcd_server(model, None))
    return golden


def _build_profile_goldens(model: str) -> dict[str, Any]:
    profile = get_radio_profile(model)
    config = WebConfig(radio_model=model)
    empty = WebServer(None, config)
    if model in _EXCLUDED_MODELS:
        return {"capabilities": _capabilities(empty)}
    golden: dict[str, Any] = {
        "allUnobserved": _full_envelope(empty),
        "capabilities": _capabilities(empty),
    }
    if profile.state_acquisition is not None:
        observed = WebServer(None, config)
        _observe_declared(observed, profile)
        golden["allObserved"] = _full_envelope(observed)
    if model in polled_dcd_models():
        golden.update(_build_polled_dcd_goldens(model))
    return golden


def _build_goldens() -> dict[str, dict[str, Any]]:
    models = sorted(discover_rigs(_REPO_ROOT / "rigs"))
    with mock.patch.dict(os.environ):
        for name in _JITTER_ENV_VARS:
            os.environ.pop(name, None)
        return {model: _build_profile_goldens(model) for model in models}


def _line(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def render_goldens() -> str:
    """Canonical JSON: sorted keys, one payload per line."""
    goldens = _build_goldens()
    blocks = []
    for model in sorted(goldens):
        entry = goldens[model]
        lines = ",\n".join(
            f"  {_line(key)}: {_line(entry[key])}" for key in sorted(entry)
        )
        blocks.append(f" {_line(model)}: {{\n{lines}\n }}")
    return "{\n" + ",\n".join(blocks) + "\n}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on drift")
    parser.add_argument("--stdout", action="store_true", help="print golden only")
    args = parser.parse_args()

    rendered = render_goldens()
    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    existing = GOLDEN_PATH.read_text(encoding="utf-8") if GOLDEN_PATH.exists() else None

    if args.check:
        if existing != rendered:
            sys.stderr.write(
                f"ERROR: {GOLDEN_PATH.relative_to(_REPO_ROOT)} is stale.\n"
                "Run `uv run python scripts/gen_state_wire_goldens.py` "
                "and commit the result.\n"
            )
            return 1
        sys.stdout.write("State wire goldens are up to date.\n")
        return 0

    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"Wrote {GOLDEN_PATH}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
