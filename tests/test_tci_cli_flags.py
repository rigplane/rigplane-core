"""C4 RED: CLI flags for the TCI listener (MOR-3821, design section 9).

``web`` and ``station`` gain ``--tci`` / ``--no-tci``, ``--tci-port``
and the ONE shared exposure setting read by both the ``rigctld`` and
TCI listeners. TCI stays off unless requested; ``serve`` has no TCI
support. The rigctld host rule without the new flag is pinned here
before the wiring changes it.
"""

from __future__ import annotations

import pytest

from rigplane.cli import _build_parser


def test_web_tci_off_by_default() -> None:
    args = _build_parser().parse_args(["--host", "1.2.3.4", "web"])
    assert args.web_tci is False
    assert args.web_tci_port == 50001
    assert args.ingress_host is None


def test_web_tci_enable_and_no_tci() -> None:
    p = _build_parser()
    assert p.parse_args(["--host", "1.2.3.4", "web", "--tci"]).web_tci is True
    assert p.parse_args(["--host", "1.2.3.4", "web", "--no-tci"]).web_tci is False
    assert (
        p.parse_args(["--host", "1.2.3.4", "web", "--tci-port", "5002"]).web_tci_port
        == 5002
    )
    assert (
        p.parse_args(
            ["--host", "1.2.3.4", "web", "--ingress-host", "0.0.0.0"]
        ).ingress_host
        == "0.0.0.0"
    )


def test_station_tci_flags_default_off() -> None:
    p = _build_parser()
    args = p.parse_args(["--host", "1.2.3.4", "station"])
    assert args.web_tci is False
    assert args.web_tci_port == 50001
    assert args.ingress_host is None
    assert p.parse_args(["--host", "1.2.3.4", "station", "--tci"]).web_tci is True


def test_serve_has_no_tci_flag() -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["serve", "--tci"])


async def test_pin_rigctld_host_rule_without_ingress_flag() -> None:
    """Pin: no flag, no behaviour change (managed 127.0.0.1, else 0.0.0.0)."""
    from unittest.mock import AsyncMock, patch

    from rigplane.cli import _cmd_web

    captured: list[object] = []

    class FakeRigctldServer:
        def __init__(self, _radio, cfg, **_kwargs: object) -> None:
            captured.append(cfg.host)

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    class FakeWebServer:
        def __init__(self, _radio, _cfg: object) -> None:
            pass

        async def serve_forever(self, *, on_started=None):  # type: ignore[no-untyped-def]
            raise asyncio.CancelledError

    import asyncio

    async def run(command: list[str]) -> None:
        args = _build_parser().parse_args(command)
        args.web_bridge = None
        with (
            patch("rigplane.web.server.WebServer", FakeWebServer),
            patch("rigplane.rigctld.server.RigctldServer", FakeRigctldServer),
            patch("rigplane.cli._detect_loopback_hint", return_value=None),
        ):
            assert await _cmd_web(AsyncMock(), args) == 0

    await run(["--host", "1.2.3.4", "web", "--managed", "--no-tci"])
    await run(["--host", "1.2.3.4", "web", "--no-tci"])
    assert captured == ["127.0.0.1", "0.0.0.0"]


async def test_tci_upgrade_predicate_receives_allowed_hosts() -> None:
    """P2 follow-up: ``--allowed-host`` must reach the TCI upgrade predicate."""
    import asyncio
    from typing import Any
    from unittest.mock import AsyncMock, patch

    from rigplane.cli import _cmd_web
    from rigplane.core.state_store import StateStore

    captured: dict[str, Any] = {}

    class FakeTciServer:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    class FakeWebServer:
        def __init__(self, _radio: Any, _cfg: Any) -> None:
            self.command_state_store = StateStore()

        async def serve_forever(self, *, on_started=None):  # type: ignore[no-untyped-def]
            raise asyncio.CancelledError

    args = _build_parser().parse_args(
        [
            "--host",
            "1.2.3.4",
            "web",
            "--no-rigctld",
            "--tci",
            "--allowed-host",
            "radio.example",
        ]
    )
    args.web_bridge = None
    with (
        patch("rigplane.web.server.WebServer", FakeWebServer),
        patch("rigplane.tci_adapter.server.TciAdapterServer", FakeTciServer),
        patch("rigplane.cli._detect_loopback_hint", return_value=None),
    ):
        assert await _cmd_web(AsyncMock(), args) == 0
    assert "allow" in captured, "TCI wiring must forward allowed_hosts"
    allow = captured["allow"]
    assert callable(allow)
    assert allow({"host": "radio.example"}) is None
    refused = allow({"host": "other.example"})
    assert refused is not None and refused.status == 421
