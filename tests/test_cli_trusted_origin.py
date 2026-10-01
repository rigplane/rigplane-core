"""CLI wiring for ``web --trusted-origin`` (MOR-3108 b11).

Pins the parser surface (repeatable, validated at parse time so an
invalid configuration never reaches the server) and the handoff into
the real :class:`rigplane.web.server.WebConfig` dataclass.
"""

from __future__ import annotations

import asyncio
import io
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.cli import _build_parser, _cmd_web

#: The operator's fictional public HTTPS origin (the reverse-proxy shape).
_TRUSTED = "https://station.example"

_INVALID_ORIGINS = [
    "",
    "https://*.example",
    "https://user:pass@station.example",
    "https://station.example/",
    "https://station.example?q=1",
    "https://station.example#frag",
    "https://station.example?",
    "https://station.example#",
    "\x00https://station.example",
    "https://station.example\x00",
    "https://station.example\x7f",
    "https://station.example:0",
    "https://station.example%2f",
    "https://station..example",
    "https://-station.example",
    "https://999.999.999.999",
    " https://station.example",
    "https://station.example ",
    "https://station.example:99999",
    "https://station.example:abc",
    "https://station.example:",
    "ftp://station.example",
    "//station.example",
    "https://",
    "https://:443",
]

_INVALID_CONFIG_ORIGINS = [None, *_INVALID_ORIGINS]


class TestParser:
    def test_repeated_flags_collect_in_order(self) -> None:
        p = _build_parser()
        with patch("sys.stderr", new_callable=io.StringIO):
            args = p.parse_args(
                [
                    "web",
                    "--listen",
                    "127.0.0.1",
                    "--trusted-origin",
                    _TRUSTED,
                    "--trusted-origin",
                    "https://ops.example",
                ]
            )
        assert args.trusted_origins == [_TRUSTED, "https://ops.example"]

    def test_absent_flag_defaults_to_none(self) -> None:
        p = _build_parser()
        with patch("sys.stderr", new_callable=io.StringIO):
            args = p.parse_args(["web"])
        assert args.trusted_origins is None

    @pytest.mark.parametrize("bad", _INVALID_ORIGINS)
    def test_invalid_origin_rejected_at_parse_time(
        self, bad: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The parser rejects an invalid serialized origin before any
        runtime starts (SystemExit 2, never a partial startup)."""
        p = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            p.parse_args(["web", "--trusted-origin", bad])
        assert exc_info.value.code == 2
        assert "--trusted-origin" in capsys.readouterr().err

    def test_help_documents_the_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        p = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            p.parse_args(["web", "--help"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "--trusted-origin" in out
        assert _TRUSTED in out


class TestConfigWiring:
    @pytest.mark.asyncio
    async def test_parsed_origins_reach_the_webconfig(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The parser's repeatable list lands in the real WebConfig the
        server is built from."""
        monkeypatch.setenv("ICOM_LOG_FILE", "off")
        p = _build_parser()
        with patch("sys.stderr", new_callable=io.StringIO):
            args = p.parse_args(
                [
                    "web",
                    "--trusted-origin",
                    _TRUSTED,
                    "--trusted-origin",
                    "https://ops.example",
                ]
            )
        args.web_rigctld = False
        captured: dict[str, Any] = {}

        class FakeWebServer:
            def __init__(self, _radio: Any, cfg: Any) -> None:
                captured["config"] = cfg
                self._runtime_log_path = None

            async def serve_forever(self, *, on_started: Any = None) -> None:
                on_started()
                raise asyncio.CancelledError

        radio = AsyncMock()
        with patch("rigplane.web.server.WebServer", FakeWebServer):
            assert await _cmd_web(radio, args) == 0
        cfg = captured["config"]
        assert cfg.trusted_origins == (_TRUSTED, "https://ops.example")

    @pytest.mark.asyncio
    async def test_absent_flag_leaves_the_default_empty_tuple(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ICOM_LOG_FILE", "off")
        p = _build_parser()
        with patch("sys.stderr", new_callable=io.StringIO):
            args = p.parse_args(["web"])
        args.web_rigctld = False
        captured: dict[str, Any] = {}

        class FakeWebServer:
            def __init__(self, _radio: Any, cfg: Any) -> None:
                captured["config"] = cfg
                self._runtime_log_path = None

            async def serve_forever(self, *, on_started: Any = None) -> None:
                on_started()
                raise asyncio.CancelledError

        with patch("rigplane.web.server.WebServer", FakeWebServer):
            assert await _cmd_web(AsyncMock(), args) == 0
        assert captured["config"].trusted_origins == ()

    @pytest.mark.parametrize("bad", _INVALID_CONFIG_ORIGINS)
    def test_direct_webconfig_rejects_what_the_parser_rejects(self, bad: Any) -> None:
        """The same values the parser rejects raise from a direct
        WebConfig construction too (embedders without the CLI)."""
        from rigplane.web.server import WebConfig

        with pytest.raises(ValueError):
            WebConfig(trusted_origins=(bad,))  # type: ignore[arg-type]
