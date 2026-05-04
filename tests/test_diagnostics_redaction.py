"""Golden tests for icom-lan diagnostics redaction utilities (#1388)."""

from __future__ import annotations

import pytest

from icom_lan.diagnostics.redaction import (
    REDACTORS,
    redact_credentials,
    redact_hostnames,
    redact_ips,
    redact_paths,
    redact_tokens,
)


# --- Paths ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/Users/moroz/foo", "/Users/<USER>/foo"),
        ("/Users/jane.doe/Library/x", "/Users/<USER>/Library/x"),
        ("path is /Users/admin/file.log here", "path is /Users/<USER>/file.log here"),
    ],
)
def test_redact_paths_unix_macos(raw: str, expected: str) -> None:
    assert redact_paths(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/home/sergey/bar", "/home/<USER>/bar"),
        ("/home/ubuntu/.config/foo", "/home/<USER>/.config/foo"),
    ],
)
def test_redact_paths_unix_linux(raw: str, expected: str) -> None:
    assert redact_paths(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"C:\Users\Bob\baz", r"C:\Users\<USER>\baz"),
        (r"d:\users\Alice\Desktop", r"d:\users\<USER>\Desktop"),
    ],
)
def test_redact_paths_windows(raw: str, expected: str) -> None:
    assert redact_paths(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "/var/log/foo",
        "/etc/hosts",
        "/opt/icom-lan/bin",
        "no path here at all",
        "https://api.example.com/Users/profile/config",
    ],
)
def test_redact_paths_no_false_positive(raw: str) -> None:
    assert redact_paths(raw) == raw


# --- IPv4 -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8.8.8.8", "<IP>"),
        ("connect to 1.1.1.1 now", "connect to <IP> now"),
        ("203.0.113.5", "<IP>"),
    ],
)
def test_redact_ipv4_public_redacted(raw: str, expected: str) -> None:
    assert redact_ips(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "192.168.55.40",
        "10.0.0.1",
        "172.16.5.1",
        "172.31.255.254",
        "127.0.0.1",
        "radio at 192.168.1.100 listening",
    ],
)
def test_redact_ipv4_private_kept(raw: str) -> None:
    assert redact_ips(raw) == raw


# --- IPv6 -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Compressed (dominant real-world) forms.
        ("2001:db8::1", "<IP>"),
        ("2001:db8::2", "<IP>"),
        ("address 2607:f8b0::200e here", "address <IP> here"),
        # Fully-expanded 8-group forms.
        ("2001:db8:0:0:0:0:0:1", "<IP>"),
        (
            "address 2607:f8b0:4005:80a:0:0:0:200e here",
            "address <IP> here",
        ),
    ],
)
def test_redact_ipv6_public_redacted(raw: str, expected: str) -> None:
    assert redact_ips(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "fe80::1",
        "::1",
        "fd00::1",
        "fd12:3456:789a::1",
    ],
)
def test_redact_ipv6_private_kept(raw: str) -> None:
    assert redact_ips(raw) == raw


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Trailing ``::`` (zero compression at end) — used to slip through.
        # Codex review on PR #1404.
        ("2001:db8::", "<IP>"),
        ("2607:f8b0:4002::", "<IP>"),
        ("address 2001:db8:: here", "address <IP> here"),
        ("foo 2001:db8::", "foo <IP>"),
        ("(2001:db8::,)", "(<IP>,)"),
    ],
)
def test_redact_ipv6_trailing_double_colon(raw: str, expected: str) -> None:
    assert redact_ips(raw) == expected


# --- Credentials ----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("password=secret123", "password=<REDACTED>"),
        ("pwd=hunter2", "pwd=<REDACTED>"),
        ("Passwd=anything", "Passwd=<REDACTED>"),
        ("PASSWORD: topsecret", "PASSWORD=<REDACTED>"),
    ],
)
def test_redact_credentials_password_kv(raw: str, expected: str) -> None:
    assert redact_credentials(raw) == expected


def test_redact_credentials_bearer() -> None:
    raw = "Authorization: Bearer abc123def456ghi"
    assert redact_credentials(raw) == "Authorization: Bearer <REDACTED>"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "aws_access_key_id=AKIAIOSFODNN7EXAMPLE",
            "aws_access_key_id=<REDACTED>",
        ),
        (
            "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "aws_secret_access_key=<REDACTED>",
        ),
    ],
)
def test_redact_credentials_aws_keys(raw: str, expected: str) -> None:
    assert redact_credentials(raw) == expected


def test_redact_credentials_activation_code() -> None:
    raw = "license code_01JZ7F4YV7B2Q6G2K8Q7QY6S3H valid"
    assert redact_credentials(raw) == "license <ACTIVATION_CODE> valid"


def test_redact_credentials_private_key_block() -> None:
    raw = (
        "before\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1234567890abcdef\n"
        "moreLines==\n"
        "-----END RSA PRIVATE KEY-----\n"
        "after"
    )
    expected = "before\n<PRIVATE KEY REDACTED>\nafter"
    assert redact_credentials(raw) == expected


# --- Tokens ---------------------------------------------------------


def test_redact_tokens_kv() -> None:
    raw = "token=AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert redact_tokens(raw) == "token=<REDACTED>"


def test_redact_tokens_bearer() -> None:
    raw = "Bearer AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert redact_tokens(raw) == "Bearer <REDACTED>"


@pytest.mark.parametrize(
    "raw",
    [
        "token=foo",
        "api_key=short",
        "apikey=tiny",
        "Bearer abc",
    ],
)
def test_redact_tokens_no_false_positive_short_value(raw: str) -> None:
    assert redact_tokens(raw) == raw


# --- Hostnames ------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("radio.example.com", "<HOSTNAME>"),
        ("alice-mac.local", "<HOSTNAME>"),
        ("connect to mac.local now", "connect to <HOSTNAME> now"),
        ("foo.bar.baz.com", "<HOSTNAME>"),
    ],
)
def test_redact_hostnames_dns_shape(raw: str, expected: str) -> None:
    assert redact_hostnames(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "localhost",
        "IC-7610",  # bare label, no TLD
        "192.168.55.40",  # IPv4 — last octet is digits-only, not a TLD
        "no host here",
    ],
)
def test_redact_hostnames_no_false_positive(raw: str) -> None:
    assert redact_hostnames(raw) == raw


def test_redact_hostnames_empty_input() -> None:
    assert redact_hostnames("") == ""


# --- REDACTORS catalogue -------------------------------------------


def test_redactors_constant() -> None:
    assert REDACTORS == ("paths", "ips", "credentials", "tokens")


# --- Empty / None / idempotency ------------------------------------


@pytest.mark.parametrize(
    "func",
    [redact_paths, redact_ips, redact_credentials, redact_tokens],
)
def test_empty_input_returns_empty(func) -> None:  # type: ignore[no-untyped-def]
    assert func("") == ""


@pytest.mark.parametrize(
    ("func", "raw"),
    [
        (redact_paths, "/Users/moroz/file"),
        (redact_ips, "public 8.8.8.8 and lan 192.168.1.1"),
        (redact_credentials, "password=secret"),
        (redact_tokens, "token=AbCdEfGhIjKlMnOpQrStUvWxYz012345"),
    ],
)
def test_idempotent(func, raw: str) -> None:  # type: ignore[no-untyped-def]
    once = func(raw)
    twice = func(once)
    assert once == twice


# --- Realistic log line --------------------------------------------


def test_realistic_log_line_no_false_positive() -> None:
    raw = (
        "INFO icom_lan.audio: codec=PCM_2CH_16BIT freq=14074000 mode=USB "
        "callsign=DL9EAC ip=192.168.55.40 device='IC-7610 USB Audio'"
    )
    out = raw
    out = redact_paths(out)
    out = redact_ips(out)
    out = redact_credentials(out)
    out = redact_tokens(out)
    assert out == raw
