#!/usr/bin/env python3
"""Consumer-Driven Contract (CDC) runner for rigplane-core (MOR-883).

Each consumer (pro, station) authors a static JSON *expectation* declaring the
producer surface it relies on. Those files are vendored byte-identical into this
repo under ``contracts/consumer-expectations/<consumer>/<contract>.json``. This
runner is executed by core's own CI: it builds the REAL producer artifact from
the PUBLIC ``rigplane.*`` surface in-process (no network, no private imports),
then asserts every consumer expectation is still satisfied. A core change that
breaks a fielded consumer therefore turns core's own PR red.

Public-only: imports nothing but ``rigplane.*`` and stdlib + the dev/CI-only
``jsonschema`` dependency. Builds artifacts with no radio attached and a
loopback peer address, so it never touches the network.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Callable

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTATIONS_DIR = REPO_ROOT / "contracts" / "consumer-expectations"


class _CaptureWriter:
    """Minimal asyncio.StreamWriter stand-in that captures written bytes.

    Lets us invoke an HTTP handler in-process and recover the real response
    body without opening a socket.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def write(self, data: bytes) -> None:
        self._buf.extend(data)

    async def drain(self) -> None:
        return None

    def get_extra_info(self, *_args: object, **_kwargs: object) -> None:
        return None

    def is_closing(self) -> bool:
        return False

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None

    def body_json(self) -> Any:
        raw = bytes(self._buf)
        body = raw.split(b"\r\n\r\n", 1)[-1] if b"\r\n\r\n" in raw else raw
        return json.loads(body.decode("utf-8"))


def _build_info() -> dict[str, Any]:
    """Build the real GET /api/v1/info body from the public web server."""
    from rigplane.web.server import WebServer

    async def _run() -> dict[str, Any]:
        server = WebServer(radio=None)
        writer = _CaptureWriter()
        await server._serve_info(writer)
        return writer.body_json()

    return asyncio.run(_run())


def _build_discovery() -> dict[str, Any]:
    """Build the real LAN discovery datagram from the public responder."""
    from rigplane.web.discovery import DiscoveryResponder, RadioInfo

    responder = DiscoveryResponder(
        web_port=8080,
        radio_provider=lambda: RadioInfo(model="IC-7300", connected=True),
    )
    # Loopback peer: build_response only resolves a local route, never sends.
    return json.loads(responder.build_response("127.0.0.1").decode("utf-8"))


ARTIFACT_BUILDERS: dict[str, Callable[[], dict[str, Any]]] = {
    "info": _build_info,
    "discovery": _build_discovery,
}


def _check_speaks_version(artifact: dict[str, Any], spec: dict[str, Any]) -> str | None:
    field = spec["field"]
    expected = spec["value"]
    actual = artifact.get(field)
    if actual != expected:
        return f"version field {field!r} is {actual!r}, consumer speaks {expected!r}"
    return None


def main() -> int:
    expectation_files = sorted(EXPECTATIONS_DIR.glob("*/*.json"))
    if not expectation_files:
        print(f"FAIL: no consumer expectations found under {EXPECTATIONS_DIR}")
        return 1

    built: dict[str, dict[str, Any]] = {}
    failures: list[str] = []

    for path in expectation_files:
        rel = path.relative_to(REPO_ROOT)
        try:
            expectation = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            failures.append(f"{rel}: cannot read/parse expectation: {exc}")
            continue

        artifact_name = expectation.get("producer_artifact")
        builder = ARTIFACT_BUILDERS.get(artifact_name)
        if builder is None:
            failures.append(
                f"{rel}: unknown producer_artifact {artifact_name!r} "
                f"(known: {sorted(ARTIFACT_BUILDERS)})"
            )
            continue

        if artifact_name not in built:
            try:
                built[artifact_name] = builder()
            except Exception as exc:  # noqa: BLE001 — surface any build failure
                failures.append(f"{rel}: failed to build producer artifact: {exc}")
                continue
        artifact = built[artifact_name]

        try:
            jsonschema.validate(instance=artifact, schema=expectation["schema"])
        except jsonschema.ValidationError as exc:
            failures.append(f"{rel}: expectation violated: {exc.message}")
            continue

        version_spec = expectation.get("speaks_version")
        if version_spec:
            problem = _check_speaks_version(artifact, version_spec)
            if problem:
                failures.append(f"{rel}: {problem}")
                continue

        source = expectation.get("source", "?")
        print(f"PASS  {rel}  (consumer={source}, artifact={artifact_name})")

    if failures:
        print()
        for failure in failures:
            print(f"FAIL  {failure}")
        print(f"\n{len(failures)} consumer expectation(s) violated.")
        return 1

    print(f"\nAll {len(expectation_files)} consumer expectation(s) satisfied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
