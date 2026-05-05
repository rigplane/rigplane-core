"""End-to-end privacy invariant tests for the diagnostic bundle (#1399).

Asserts: no forbidden pattern leaks through the bundle pipeline; manifest
schema invariants hold; opt-in contact fields behave correctly.

Strategy:
- Groups 1 & 2: register the real ``ConfigContributor`` so the redaction
  path is exercised end-to-end (TOML parse → ``_sanitise`` → JSON dump
  → ZIP). Inject the secret as a non-secret-key TOML value, build the
  bundle, then scan every byte of the produced ZIP for the raw secret.
- Groups 3-5: register a minimal stub contributor and inspect ``manifest.json``.

Targets <30s total runtime by avoiding the full set of built-in contributors
(uses the same isolation pattern as ``test_diagnostics_bundle.py``).
"""

from __future__ import annotations

import json
import random
import string
import zipfile
from pathlib import Path
from typing import Any

import pytest

from rigplane.diagnostics import (
    BundleContext,
    build_bundle,
    register,
)
from rigplane.diagnostics import _discovery
from rigplane.diagnostics.contributors.config import ConfigContributor


# --- Fixtures -------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_contributors() -> Any:
    """Isolate from runtime-registered AND built-in contributors.

    Mirrors the pattern in ``tests/test_diagnostics_bundle.py``.
    """
    _discovery._RUNTIME_REGISTERED.clear()
    saved_built_in = list(_discovery._BUILT_IN_CONTRIBUTORS)
    _discovery._BUILT_IN_CONTRIBUTORS.clear()
    yield
    _discovery._RUNTIME_REGISTERED.clear()
    _discovery._BUILT_IN_CONTRIBUTORS.clear()
    _discovery._BUILT_IN_CONTRIBUTORS.extend(saved_built_in)


# --- Helpers --------------------------------------------------------


def _make_ctx(tmp_path: Path, **overrides: Any) -> BundleContext:
    config_dir = tmp_path / "config"
    log_dir = tmp_path / "log"
    config_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    base: dict[str, Any] = {
        "radio": None,
        "config_dir": config_dir,
        "log_dir": log_dir,
        "user_description": None,
        "issue_ref": None,
        "contact_email": None,
        "contact_callsign": None,
        "submission_id": "test-sub-id",
        "generated_at_unix": 1700000000,
    }
    base.update(overrides)
    return BundleContext(**base)


def _bundle_bytes(zip_path: Path) -> bytes:
    """Concatenate every entry's content for substring scanning."""
    chunks: list[bytes] = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            chunks.append(zf.read(name))
    return b"".join(chunks)


def _read_manifest(zip_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path) as zf:
        return json.loads(zf.read("manifest.json"))


def _write_config_with_secret(config_dir: Path, secret: str) -> None:
    """Write ``settings.toml`` containing ``secret`` as a non-secret-key value.

    The ``description`` key is intentionally NOT in ``ConfigContributor``'s
    ``_SECRET_KEYS`` set — it must reach the redaction pipeline rather than
    being key-dropped.
    """
    # Use multi-line basic string so embedded newlines survive (e.g. PEM blocks).
    toml_text = 'description = """\n' + secret + '\n"""\n'
    (config_dir / "settings.toml").write_text(toml_text, encoding="utf-8")


class _StubContributor:
    """Minimal contributor for manifest-shape tests — writes one tiny file."""

    name = "stub"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        (output_dir / "ok.txt").write_text("ok", encoding="utf-8")


class _EmailEmittingContributor:
    """Used in Group 5 inverse case — emits an email-shaped string in its data.

    This must NOT cause ``manifest.contact`` to be populated, because the
    manifest only reads ``BundleContext.contact_*``; it never scans
    contributor output for contact data.
    """

    name = "stub-email"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        (output_dir / "data.txt").write_text(
            "address=alice@example.org\n", encoding="utf-8"
        )


# --- Group 1: Forbidden pattern sweep -------------------------------


_PEM_BLOCK = (
    "-----BEGIN PRIVATE KEY-----\n"
    "ABCDEFGHIJKLMNOP1234567890==\n"
    "-----END PRIVATE KEY-----"
)

# Each entry: (label, raw_secret_string)
# Every secret here MUST be redacted by ``redact_credentials`` (the only
# scrubber ConfigContributor invokes via ``_sanitise``).
_FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    ("aws_access_key_id", "aws_access_key_id=AKIA1234567890ABCDEF"),
    (
        "aws_secret_access_key",
        "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    ),
    (
        "authorization_bearer",
        "Authorization: Bearer eyJabcdefghijklmnopqrstuvwxyz0123456789012",
    ),
    ("password_eq", "password=hunter2supersecret"),
    ("pwd_colon", "pwd: hunter2supersecret"),
    ("passwd_eq", "passwd=hunter2supersecret"),
    ("activation_code", "code_ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    ("pem_private_key", _PEM_BLOCK),
]


@pytest.mark.parametrize(
    ("label", "secret"),
    _FORBIDDEN_PATTERNS,
    ids=[p[0] for p in _FORBIDDEN_PATTERNS],
)
def test_forbidden_pattern_does_not_leak(
    tmp_path: Path, label: str, secret: str
) -> None:
    """Each forbidden secret injected into a TOML config must NOT appear
    anywhere in the resulting ZIP, AND a redaction marker must be present."""
    register(ConfigContributor)
    ctx = _make_ctx(tmp_path)
    _write_config_with_secret(ctx.config_dir, secret)

    out = tmp_path / "report.zip"
    result = build_bundle(ctx, out)

    blob = _bundle_bytes(result)

    # Core invariant: raw secret bytes never appear in the bundle.
    assert secret.encode("utf-8") not in blob, (
        f"forbidden pattern '{label}' leaked into bundle"
    )

    # Also: sanity-check that some redaction marker IS present, so we
    # know the secret was processed (not silently dropped on the floor).
    assert (
        (b"<REDACTED>" in blob)
        or (b"<ACTIVATION_CODE>" in blob)
        or (b"<PRIVATE KEY REDACTED>" in blob)
    ), f"no redaction marker found for pattern '{label}'"


# --- Group 2: Fuzz test ---------------------------------------------


def _random_secret(rng: random.Random, kind: str) -> str:
    """Generate a random secret-shaped string matching one of the credential
    families that ``redact_credentials`` covers."""
    if kind == "password":
        chars = rng.choices(string.ascii_letters + string.digits, k=rng.randint(32, 64))
        return f"password={''.join(chars)}"
    if kind == "pwd":
        chars = rng.choices(string.ascii_letters + string.digits, k=rng.randint(32, 64))
        return f"pwd: {''.join(chars)}"
    if kind == "passwd":
        chars = rng.choices(string.ascii_letters + string.digits, k=rng.randint(32, 64))
        return f"passwd={''.join(chars)}"
    if kind == "bearer":
        chars = rng.choices(string.ascii_letters + string.digits, k=rng.randint(32, 64))
        return f"Authorization: Bearer {''.join(chars)}"
    if kind == "aws_id":
        chars = rng.choices(string.ascii_uppercase + string.digits, k=20)
        return f"aws_access_key_id={''.join(chars)}"
    if kind == "aws_secret":
        chars = rng.choices(
            string.ascii_letters + string.digits + "/+", k=rng.randint(32, 48)
        )
        return f"aws_secret_access_key={''.join(chars)}"
    if kind == "code":
        # Activation-code regex requires exactly 26 [A-Z0-9].
        chars = rng.choices(string.ascii_uppercase + string.digits, k=26)
        return f"code_{''.join(chars)}"
    raise AssertionError(f"unknown kind: {kind}")


_FUZZ_KINDS = (
    "password",
    "pwd",
    "passwd",
    "bearer",
    "aws_id",
    "aws_secret",
    "code",
)


def test_fuzz_random_secrets_do_not_leak(tmp_path: Path) -> None:
    """100 random secret-shaped strings, all credential-family covered.

    All must be fully scrubbed from the bundle output.
    """
    rng = random.Random(42)
    secrets: list[str] = [
        _random_secret(rng, rng.choice(_FUZZ_KINDS)) for _ in range(100)
    ]

    register(ConfigContributor)
    ctx = _make_ctx(tmp_path)

    # Inject all secrets into a single TOML, one per line, inside a
    # multi-line basic string under a non-secret key.
    payload = "\n".join(secrets)
    toml_text = 'description = """\n' + payload + '\n"""\n'
    (ctx.config_dir / "settings.toml").write_text(toml_text, encoding="utf-8")

    out = tmp_path / "report.zip"
    result = build_bundle(ctx, out)
    blob = _bundle_bytes(result)

    leaked: list[str] = [s for s in secrets if s.encode("utf-8") in blob]
    assert not leaked, f"{len(leaked)} fuzz secret(s) leaked; first: {leaked[:3]!r}"


# --- Group 3: Manifest schema invariants ----------------------------


def test_manifest_required_fields_no_nulls(tmp_path: Path) -> None:
    register(_StubContributor)
    out = tmp_path / "report.zip"
    result = build_bundle(_make_ctx(tmp_path), out)

    manifest = _read_manifest(result)

    # Required top-level fields, all non-empty.
    assert manifest["schema_version"] == "rigplane-bundle-v2"
    assert manifest["submission_id"]
    assert manifest["generated_at_unix"]
    assert manifest["app"]["name"] == "rigplane"
    assert manifest["app"]["version"]
    assert manifest["platform"]["os"]
    assert manifest["platform"]["arch"]

    # Recursive: no value anywhere is JSON null.
    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for v in value.values():
                _walk(v)
        elif isinstance(value, list):
            for v in value:
                _walk(v)
        else:
            assert value is not None

    _walk(manifest)


# --- Group 4: Optional fields omitted (no nulls) --------------------


def test_optional_fields_omitted_when_absent(tmp_path: Path) -> None:
    """When ctx fields are None, manifest must OMIT the key entirely
    (not present-with-null)."""
    register(_StubContributor)
    out = tmp_path / "report.zip"
    result = build_bundle(_make_ctx(tmp_path), out)

    manifest = _read_manifest(result)
    assert "user_description" not in manifest
    assert "issue_ref" not in manifest
    assert "contact" not in manifest


# --- Group 5: Contact opt-in correctness ----------------------------


def test_contact_omitted_when_unset_and_no_email_in_bundle(tmp_path: Path) -> None:
    register(_StubContributor)
    ctx = _make_ctx(tmp_path, contact_email=None, contact_callsign=None)
    out = tmp_path / "report.zip"
    result = build_bundle(ctx, out)

    manifest = _read_manifest(result)
    assert "contact" not in manifest

    # And no email-shaped string appears in the bundle (we registered
    # nothing that emits one).
    blob = _bundle_bytes(result)
    assert b"@" not in blob and b"example.com" not in blob


def test_contact_present_when_set(tmp_path: Path) -> None:
    register(_StubContributor)
    ctx = _make_ctx(
        tmp_path,
        contact_email="user@example.com",
        contact_callsign="K1ABC",
    )
    out = tmp_path / "report.zip"
    result = build_bundle(ctx, out)

    manifest = _read_manifest(result)
    assert manifest["contact"] == {"email": "user@example.com", "callsign": "K1ABC"}

    blob = _bundle_bytes(result)
    assert b"user@example.com" in blob
    assert b"K1ABC" in blob


def test_contributor_email_does_not_populate_manifest_contact(tmp_path: Path) -> None:
    """Inverse direction: a contributor emitting an email-shaped value into
    its own data file MUST NOT cause the manifest's ``contact`` key to be
    populated. The manifest's contact comes from ``BundleContext`` only.
    """
    register(_EmailEmittingContributor)
    ctx = _make_ctx(tmp_path, contact_email=None, contact_callsign=None)
    out = tmp_path / "report.zip"
    result = build_bundle(ctx, out)

    manifest = _read_manifest(result)
    assert "contact" not in manifest

    # The contributor's own data file does carry the email — that's
    # expected; the invariant is about the manifest contact field only.
    blob = _bundle_bytes(result)
    assert b"alice@example.org" in blob
