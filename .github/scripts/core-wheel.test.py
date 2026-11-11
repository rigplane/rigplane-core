"""Pins and unit tests for the Core wheel workflow family (MOR-4408).

Three parts:

- the wheel version rule (``core_wheel_version.py``), including
  ``--write-pyproject``;
- core_ref resolution (``resolve_core_ref.py``): pure parsing against
  fixtures, plus end-to-end runs against a real throwaway git repository
  (every runner pool that executes this file has git);
- the ``core-wheel.json`` typed contract (``core_wheel_manifest.py``),
  including the ``profile_smoke`` skip value, and the profile-smoke
  catalog contract that makes the workflow run the TARGET revision's
  smoke script (P1: revision-specific catalogs);
- text pins on ``core-wheel.yml``, ``core-wheel-nightly.yml``,
  ``publish.yml`` (the build job's delegation to the reusable workflow)
  and the ``quick.yml`` step that runs this file — same text-pin style as
  ``tests/test_publish_workflow_contract.py``, no YAML dependency, because
  the control-test environment is a bare ``python3``.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
WHEEL_YML = WORKFLOWS / "core-wheel.yml"
NIGHTLY_YML = WORKFLOWS / "core-wheel-nightly.yml"
PUBLISH_YML = WORKFLOWS / "publish.yml"
QUICK_YML = WORKFLOWS / "quick.yml"
VERSION_SCRIPT = Path(__file__).with_name("core_wheel_version.py")
RESOLVE_SCRIPT = Path(__file__).with_name("resolve_core_ref.py")

MANIFEST_SCRIPT = Path(__file__).with_name("core_wheel_manifest.py")
SMOKE_SCRIPT = Path(__file__).with_name("installed_profile_smoke.py")
CALLER_FIXTURE = Path(__file__).with_name("core-wheel-caller-context.fixture.json")

SHA = "0123456789abcdef0123456789abcdef01234567"


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERSION = _load(VERSION_SCRIPT)
RESOLVE = _load(RESOLVE_SCRIPT)


# ── Wheel version rule ─────────────────────────────────────────────


class WheelVersionRuleTest(unittest.TestCase):
    def test_tag_match_returns_pyproject_version_unchanged(self) -> None:
        self.assertEqual(
            VERSION.wheel_version("3.2.0", SHA, ["v3.2.0"]),
            "3.2.0",
        )

    def test_tag_match_normalizes_separator_and_case(self) -> None:
        self.assertEqual(
            VERSION.wheel_version("3.2.0RC1", SHA, ["v3.2.0-rc.1"]),
            "3.2.0RC1",
        )

    def test_tag_mismatch_fails(self) -> None:
        with self.assertRaises(VERSION.VersionRuleError):
            VERSION.wheel_version("3.2.0", SHA, ["v3.1.0"])

    def test_no_tag_gets_local_version(self) -> None:
        version = VERSION.wheel_version("3.1.2rc4", SHA, [])
        self.assertEqual(version, f"3.1.2rc4+g{SHA[:12]}")

    def test_two_tags_get_local_version(self) -> None:
        # Even when one tag matches the pyproject version, two release tags
        # at one commit are ambiguous: never ship the clean version.
        self.assertEqual(
            VERSION.wheel_version("3.2.0", SHA, ["v3.2.0", "v3.2.1"]),
            f"3.2.0+g{SHA[:12]}",
        )

    def test_pyproject_with_local_part_is_rejected(self) -> None:
        with self.assertRaises(VERSION.VersionRuleError):
            VERSION.wheel_version("3.2.0+g0123456789ab", SHA, [])

    def test_invalid_pyproject_version_is_rejected(self) -> None:
        for bad in ("3.2.0.0.0x", "abc", "3.2.0 dev", ""):
            with self.subTest(bad=bad), self.assertRaises(VERSION.VersionRuleError):
                VERSION.wheel_version(bad, SHA, [])

    def test_sha_must_be_40_lowercase_hex(self) -> None:
        for bad in (SHA.upper(), SHA[:12], "z" + SHA[1:]):
            with self.subTest(bad=bad), self.assertRaises(VERSION.VersionRuleError):
                VERSION.wheel_version("3.2.0", bad, [])

    def test_non_release_tags_are_ignored(self) -> None:
        self.assertEqual(
            VERSION.wheel_version(
                "3.2.0", SHA, ["release-2026", "visual-v2", "v3.2.0+build", "vlatest"]
            ),
            f"3.2.0+g{SHA[:12]}",
        )

    def test_numeric_pre_post_dev_suffixes_normalize_to_integers(self) -> None:
        # Omitted number = 0; leading zeros collapse: a tag v3.2.0rc01 must
        # release the pyproject version 3.2.0rc1, and a bare rc equals rc0.
        pairs = [
            ("3.2.0rc1", "v3.2.0rc01"),
            ("3.2.0rc0", "v3.2.0rc"),
            ("3.1.0a1", "v3.1.0alpha01"),
            ("3.1.0b2", "v3.1.0.beta.02"),
            ("3.2.0.post1", "v3.2.0-1"),
            ("3.2.0.post0", "v3.2.0.post"),
            ("3.2.0.post3", "v3.2.0.rev03"),
            ("3.2.0.dev1", "v3.2.0.dev01"),
            ("3.2.0.dev0", "v3.2.0.dev"),
        ]
        for pyproject, tag in pairs:
            with self.subTest(pyproject=pyproject, tag=tag):
                self.assertEqual(
                    VERSION.wheel_version(pyproject, SHA, [tag]), pyproject
                )

    def test_normalize_pairs_for_every_suffix_kind(self) -> None:
        equivalent = [
            ("3.2.0rc", "3.2.0rc0"),
            ("3.2.0rc01", "3.2.0rc1"),
            ("3.2.0c2", "3.2.0rc02"),
            ("3.2.0-2", "3.2.0.post2"),
            ("3.2.0.post", "3.2.0.post0"),
            ("3.2.0.rev3", "3.2.0.post3"),
            ("3.2.0.dev", "3.2.0.dev0"),
            ("3.2.0.dev007", "3.2.0.dev7"),
        ]
        for left, right in equivalent:
            with self.subTest(left=left, right=right):
                self.assertEqual(
                    VERSION.normalize_public_version(left),
                    VERSION.normalize_public_version(right),
                )

    def test_epoch_normalizes_as_an_integer_with_zero_omitted(self) -> None:
        # PEP 440: the epoch is numeric, and zero is the default epoch, so
        # the canonical form omits it. A tag v0!3.2.0 releases pyproject
        # 3.2.0, and v01!3.2.0 releases 1!3.2.0.
        pairs = [
            ("3.2.0", "v0!3.2.0"),
            ("1!3.2.0", "v01!3.2.0"),
        ]
        for pyproject, tag in pairs:
            with self.subTest(pyproject=pyproject, tag=tag):
                self.assertEqual(
                    VERSION.wheel_version(pyproject, SHA, [tag]), pyproject
                )
        for left, right in (("0!3.2.0", "3.2.0"), ("01!3.2.0", "1!3.2.0")):
            with self.subTest(left=left, right=right):
                self.assertEqual(
                    VERSION.normalize_public_version(left),
                    VERSION.normalize_public_version(right),
                )

    def test_release_segments_stay_strict(self) -> None:
        with self.assertRaises(VERSION.VersionRuleError):
            VERSION.wheel_version("3.2.0", SHA, ["v3.2"])
        with self.assertRaises(VERSION.VersionRuleError):
            VERSION.wheel_version("3.2", SHA, ["v3.2.0"])


class WheelVersionCliTest(unittest.TestCase):
    def _pyproject(self, directory: Path, version: str = "3.1.2rc4") -> Path:
        path = directory / "pyproject.toml"
        path.write_text(
            '[build-system]\nrequires = ["hatchling"]\n\n'
            f'[project]\nname = "rigplane"\nversion = "{version}"\n',
            encoding="utf-8",
        )
        return path

    def test_computes_and_writes_version(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            path = self._pyproject(directory)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = VERSION.main(
                    [
                        "--pyproject",
                        str(path),
                        "--sha",
                        SHA,
                        "--tags-json",
                        "[]",
                        "--write-pyproject",
                        str(path),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue().strip(), f"3.1.2rc4+g{SHA[:12]}")
            text = path.read_text(encoding="utf-8")
            self.assertIn(f'version = "3.1.2rc4+g{SHA[:12]}"', text)
            self.assertIn('[project]\nname = "rigplane"', text)

    def test_write_refuses_pyproject_without_version_line(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "pyproject.toml"
            path.write_text('[project]\nname = "rigplane"\n', encoding="utf-8")
            stderr = io.StringIO()
            with mock.patch.object(sys, "stderr", stderr):
                code = VERSION.main(
                    [
                        "--pyproject",
                        str(path),
                        "--sha",
                        SHA,
                        "--tags-json",
                        "[]",
                        "--write-pyproject",
                        str(path),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertNotIn("version =", path.read_text(encoding="utf-8"))

    def test_tags_json_must_be_an_array_of_strings(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = self._pyproject(Path(raw))
            for payload in ('{"a": 1}', "[1]", "not json"):
                with self.subTest(payload=payload):
                    code = VERSION.main(
                        ["--pyproject", str(path), "--sha", SHA, "--tags-json", payload]
                    )
                    self.assertEqual(code, 2)

    def test_cli_subprocess_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = self._pyproject(Path(raw))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(VERSION_SCRIPT),
                    "--pyproject",
                    str(path),
                    "--sha",
                    SHA,
                    "--tags-json",
                    '["v3.1.2rc4"]',
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout.strip(), "3.1.2rc4")


# ── core-wheel.json typed contract ─────────────────────────────────


class CoreWheelManifestTest(unittest.TestCase):
    """The producer's real output must parse through its own contract.

    ``core-wheel.json`` crosses a product boundary (Core CI writes it, Pro
    and other consumers read it), so it is written by
    ``core_wheel_manifest.py`` (a JSON encoder) — never by shell
    interpolation of ref text into a heredoc, which a legal ref containing
    a quote would turn into invalid JSON.
    """

    QUOTE_REF = 'feature/"probe"'
    WHEEL = "rigplane-3.2.0+g0123456789ab-py3-none-any.whl"

    def setUp(self) -> None:
        self.manifest = _load(MANIFEST_SCRIPT)

    def _produce(self, directory: Path, *, requested_ref: str) -> Path:
        output = directory / "core-wheel.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = self.manifest.main(
                [
                    "--sha",
                    SHA,
                    "--version",
                    "3.2.0+g0123456789ab",
                    "--requested-ref",
                    requested_ref,
                    "--wheel",
                    self.WHEEL,
                    "--wheel-sha256",
                    "a" * 64,
                    "--profile-smoke",
                    "target",
                    "--output",
                    str(output),
                ]
            )
        self.assertEqual(code, 0)
        return output

    def test_producer_output_parses_with_a_quote_containing_ref(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = self._produce(Path(raw), requested_ref=self.QUOTE_REF)
            text = output.read_text(encoding="utf-8")
        self.assertIn('"', self.QUOTE_REF)  # the fixture really carries a quote
        parsed = json.loads(text)  # the written file is valid JSON, quote and all
        self.assertEqual(
            set(parsed),
            {
                "sha",
                "version",
                "requested_ref",
                "wheel",
                "wheel_sha256",
                "profile_smoke",
            },
        )
        manifest = self.manifest.parse_manifest(text)
        self.assertEqual(manifest["sha"], SHA)
        self.assertEqual(manifest["version"], "3.2.0+g0123456789ab")
        self.assertEqual(manifest["requested_ref"], self.QUOTE_REF)
        self.assertEqual(manifest["wheel"], self.WHEEL)
        self.assertEqual(manifest["wheel_sha256"], "a" * 64)
        self.assertEqual(manifest["profile_smoke"], "target")
        for key, value in manifest.items():
            self.assertIsInstance(value, str, key)

    def test_parser_rejects_invalid_payloads(self) -> None:
        valid = {
            "sha": SHA,
            "version": "3.2.0",
            "requested_ref": "main",
            "wheel": self.WHEEL,
            "wheel_sha256": "a" * 64,
            "profile_smoke": "target",
        }
        bad_payloads = {
            "not json": "{ nope",
            "not an object": "[]",
            "empty object": "{}",
            "unknown key": json.dumps({**valid, "extra": 1}),
            "sha not hex": json.dumps({**valid, "sha": "z" * 40}),
            "sha uppercase": json.dumps({**valid, "sha": SHA.upper()}),
            "sha wrong type": json.dumps({**valid, "sha": 123}),
            "version empty": json.dumps({**valid, "version": ""}),
            "version wrong type": json.dumps({**valid, "version": 3}),
            "ref multiline": json.dumps({**valid, "requested_ref": "a\nb"}),
            "ref empty": json.dumps({**valid, "requested_ref": ""}),
            "ref wrong type": json.dumps({**valid, "requested_ref": ["main"]}),
            "wheel with separator": json.dumps({**valid, "wheel": "dist/x.whl"}),
            "sha256 wrong length": json.dumps({**valid, "wheel_sha256": "a" * 63}),
            "sha256 wrong type": json.dumps({**valid, "wheel_sha256": None}),
            "profile smoke unknown value": json.dumps(
                {**valid, "profile_smoke": "skipped"}
            ),
            "profile smoke wrong type": json.dumps({**valid, "profile_smoke": True}),
        }
        for name, payload in bad_payloads.items():
            with self.subTest(payload=name):
                with self.assertRaises(self.manifest.ManifestError):
                    self.manifest.parse_manifest(payload)

    def test_both_profile_smoke_values_parse(self) -> None:
        """The explicit skip value is part of the contract, not drift.

        A target revision older than the installed-profile smoke script
        yields ``not_available_for_target``: consumers must be able to rely
        on that exact value — never a silent pass or a silent failure.
        """
        valid = {
            "sha": SHA,
            "version": "3.2.0",
            "requested_ref": "v3.0.0",
            "wheel": self.WHEEL,
            "wheel_sha256": "a" * 64,
        }
        for value in ("target", "not_available_for_target"):
            with self.subTest(value=value):
                manifest = self.manifest.parse_manifest(
                    json.dumps({**valid, "profile_smoke": value})
                )
                self.assertEqual(manifest["profile_smoke"], value)

    def test_cli_rejects_invalid_inputs_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "core-wheel.json"
            code = self.manifest.main(
                [
                    "--sha",
                    "short",
                    "--version",
                    "3.2.0",
                    "--requested-ref",
                    "main",
                    "--wheel",
                    self.WHEEL,
                    "--wheel-sha256",
                    "a" * 64,
                    "--profile-smoke",
                    "target",
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(code, 2)
            self.assertFalse(output.exists())

    def test_cli_rejects_unknown_profile_smoke_value_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "core-wheel.json"
            code = self.manifest.main(
                [
                    "--sha",
                    SHA,
                    "--version",
                    "3.2.0",
                    "--requested-ref",
                    "main",
                    "--wheel",
                    self.WHEEL,
                    "--wheel-sha256",
                    "a" * 64,
                    "--profile-smoke",
                    "skipped",
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(code, 2)
            self.assertFalse(output.exists())


# ── Profile-smoke catalog contract (target vs controls) ────────────


class ProfileSmokeCatalogContractTest(unittest.TestCase):
    """Why the profile smoke must run the TARGET revision's script (P1).

    The profile catalog is revision-specific: v3.1.4 shipped nine radio
    profiles, current Core ships eleven (g7250.toml, ic905.toml). Judging a
    historical wheel by the controls' current catalog rejects a valid
    release, so the workflow runs the target's own smoke script — while a
    wheel genuinely missing one of the TARGET's own resources is still
    rejected (the switch must not weaken the smoke into a no-op).
    """

    def setUp(self) -> None:
        self.smoke = _load(SMOKE_SCRIPT)

    def _older_catalog(self) -> dict[str, tuple[str, str]]:
        # The v3.1.4 catalog: everything but g7250.toml and ic905.toml.
        return {
            name: identity
            for name, identity in self.smoke.EXPECTED_PROFILES.items()
            if name not in ("g7250.toml", "ic905.toml")
        }

    def test_an_older_complete_target_catalog_passes_its_own_contract(self) -> None:
        older = self._older_catalog()
        self.assertEqual(len(older), 9)
        older_names = tuple(sorted(older))
        with mock.patch.object(self.smoke, "EXPECTED_PROFILES", older):
            self.assertEqual(self.smoke.validate_catalog(older_names), older_names)
        # The same complete wheel is rejected by the controls' current
        # catalog: running the controls' script would break every
        # historical ref.
        with self.assertRaises(self.smoke.SmokeFailure):
            self.smoke.validate_catalog(older_names)

    def test_a_genuinely_missing_target_profile_is_still_rejected(self) -> None:
        older = self._older_catalog()
        incomplete = tuple(sorted(older))[:-1]  # ic9700.toml missing
        with mock.patch.object(self.smoke, "EXPECTED_PROFILES", older):
            with self.assertRaises(self.smoke.SmokeFailure):
                self.smoke.validate_catalog(incomplete)


# ── core_ref resolution ────────────────────────────────────────────


_FIXTURE_LS_REMOTE = "\n".join(
    [
        f"{SHA}\trefs/heads/main",
        f"{SHA}\trefs/heads/release/3.2",
        f"{'a' * 40}\trefs/heads/release/3.2rc1",
        f"{SHA}\trefs/tags/v3.2.0",
        f"{'b' * 40}\trefs/tags/v3.2.0rc1",
        f"{SHA}\trefs/tags/v3.2.0rc1^{{}}",
        f"{'c' * 40}\trefs/pull/4554/head",
    ]
)


class ResolveParsingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.refs = RESOLVE.parse_ls_remote(_FIXTURE_LS_REMOTE)

    def test_branch_resolution(self) -> None:
        sha, tags = RESOLVE.resolve_ref(self.refs, "release/3.2")
        self.assertEqual(sha, SHA)
        self.assertEqual(tags, ["v3.2.0", "v3.2.0rc1"])

    def test_annotated_tag_peels_to_its_commit(self) -> None:
        sha, tags = RESOLVE.resolve_ref(self.refs, "v3.2.0rc1")
        self.assertEqual(sha, SHA)
        self.assertEqual(tags, ["v3.2.0", "v3.2.0rc1"])

    def test_full_ref_form(self) -> None:
        self.assertEqual(RESOLVE.resolve_ref(self.refs, "refs/heads/main")[0], SHA)

    def test_name_matching_branch_and_tag_with_one_commit_resolves(self) -> None:
        fixture = "\n".join(
            [
                f"{SHA}\trefs/heads/prod",
                f"{SHA}\trefs/tags/prod",
            ]
        )
        sha, _tags = RESOLVE.resolve_ref(RESOLVE.parse_ls_remote(fixture), "prod")
        self.assertEqual(sha, SHA)

    def test_ambiguous_name_fails(self) -> None:
        fixture = "\n".join(
            [
                f"{SHA}\trefs/heads/prod",
                f"{'a' * 40}\trefs/tags/prod",
            ]
        )
        with self.assertRaises(RESOLVE.RefResolutionError):
            RESOLVE.resolve_ref(RESOLVE.parse_ls_remote(fixture), "prod")

    def test_unknown_name_fails(self) -> None:
        with self.assertRaises(RESOLVE.RefResolutionError):
            RESOLVE.resolve_ref(self.refs, "no-such-branch")

    def test_non_ref_names_are_refused(self) -> None:
        for bad in (
            "HEAD",
            "HEAD~2",
            "main..other",
            "-main",
            "a b",
            "main@{yesterday}",
            "refs/tags/v3.lock",
            "/leading",
            "trailing/",
            "releases/*",
        ):
            with self.subTest(bad=bad), self.assertRaises(RESOLVE.RefResolutionError):
                RESOLVE.resolve_ref(self.refs, bad)

    def test_tags_at_sha_excludes_peeled_entries(self) -> None:
        fixture = "\n".join(
            [
                f"{SHA}\trefs/tags/v1.0.0",
                f"{'b' * 40}\trefs/tags/v1.1.0",
                f"{SHA}\trefs/tags/v1.1.0^{{}}",
            ]
        )
        self.assertEqual(
            RESOLVE.tags_at_sha(RESOLVE.parse_ls_remote(fixture), SHA),
            ["v1.0.0", "v1.1.0"],
        )

    def test_remote_token_is_never_shown_in_failures(self) -> None:
        sanitized = RESOLVE._remote_url(  # noqa: SLF001
            "https://github.com/rigplane/rigplane-core.git", "sekrit"
        )
        self.assertIn("x-access-token:sekrit@", sanitized)
        detail = "boom https://x-access-token:sekrit@github.com boom"
        self.assertIn("***", RESOLVE._sanitize(detail, "sekrit"))


class ResolveDiagnosticsTest(unittest.TestCase):
    """A failing git call must never leak the credential-bearing remote.

    ``_run_git`` embeds its argv in the failure message; with the
    token-bearing URL on that argv (``ls-remote``/``fetch``), the exception —
    and ``main()``'s stderr — would publish the credential (GH_TOKEN).
    """

    TOKEN = "ghs_SUPERSECRET123"
    REMOTE = "https://github.com/rigplane/rigplane-core.git"

    def _failing_git(self, stderr: str) -> mock._patch:  # type: ignore[type-arg]
        completed = subprocess.CompletedProcess(
            ["git", "ls-remote", "irrelevant"], returncode=128, stdout="", stderr=stderr
        )
        return mock.patch.object(subprocess, "run", return_value=completed)

    def test_exception_message_never_contains_the_credential(self) -> None:
        url = RESOLVE._remote_url(self.REMOTE, self.TOKEN)  # noqa: SLF001
        self.assertIn(self.TOKEN, url)  # the fixture really is credential-bearing
        stderr = (
            f"fatal: unable to access '{url}': Could not resolve host: github.com\n"
        )
        with self._failing_git(stderr):
            with self.assertRaises(RESOLVE.RefResolutionError) as ctx:
                RESOLVE.resolve("main", remote=self.REMOTE, token=self.TOKEN)
        message = str(ctx.exception)
        self.assertNotIn(self.TOKEN, message)
        self.assertNotIn("x-access-token", message)

    def test_main_stderr_never_contains_the_credential(self) -> None:
        url = RESOLVE._remote_url(self.REMOTE, self.TOKEN)  # noqa: SLF001
        stderr = f"fatal: unable to access '{url}': gnu-family terror\n"
        captured = io.StringIO()
        with self._failing_git(stderr):
            with mock.patch.dict(os.environ, {"GH_TOKEN": self.TOKEN}):
                with mock.patch.object(sys, "stderr", captured):
                    code = RESOLVE.main(["--ref", "main", "--remote", self.REMOTE])
        self.assertEqual(code, 2)
        self.assertNotIn(self.TOKEN, captured.getvalue())
        self.assertNotIn("x-access-token", captured.getvalue())


@unittest.skipIf(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    "git is not available",
)
class ResolveEndToEndTest(unittest.TestCase):
    """The resolver against a real throwaway bare repository."""

    origin: Path
    commit_sha: str

    @classmethod
    def setUpClass(cls) -> None:
        cls._root = Path(tempfile.mkdtemp(prefix="core-wheel-resolve-"))
        cls.origin = cls._root / "origin.git"
        work = cls._root / "work"
        cls._git_work(work, "init", "-q", "-b", "main", str(work))
        for key, value in (
            ("user.email", "core-wheel@test"),
            ("user.name", "core-wheel test"),
        ):
            cls._git_work(work, "config", key, value)
        (work / "file.txt").write_text("content\n", encoding="utf-8")
        cls._git_work(work, "add", "file.txt")
        cls._git_work(work, "commit", "-q", "-m", "one")
        cls.commit_sha = cls._git_work(work, "rev-parse", "HEAD").strip()
        cls._git_work(work, "tag", "v3.2.0")
        cls._git_work(work, "tag", "-a", "v3.2.0rc1", "-m", "rc")
        # The annotated tag's own object ID: not a commit, and a legal raw
        # core_ref input that must peel to the tagged commit.
        cls.tag_object_sha = cls._git_work(work, "rev-parse", "v3.2.0rc1").strip()
        cls._git_work(work, "branch", "release/3.2")
        cls._run(("init", "-q", "--bare", str(cls.origin)))
        cls._git_work(work, "remote", "add", "origin", str(cls.origin))
        cls._git_work(
            work, "push", "-q", str(cls.origin), "main", "release/3.2", "--tags"
        )
        # GitHub serves uploadpack.allowAnySHA1InWant; mirror it so the
        # exact-SHA reachability proof runs the same path locally.
        cls._run(
            ("-C", str(cls.origin), "config", "uploadpack.allowAnySHA1InWant", "true")
        )
        # A blob reachable only by exact-SHA fetch: fetchable, but never a
        # commit — a wheel must not be "built" from it.
        cls.blob_sha = (
            subprocess.run(
                ["git", "-C", str(cls.origin), "hash-object", "-w", "--stdin"],
                input=b"disposable blob\n",
                capture_output=True,
                check=True,
            )
            .stdout.decode()
            .strip()
        )
        # A tree object, written the same way.
        cls.tree_sha = (
            subprocess.run(
                ["git", "-C", str(cls.origin), "mktree"],
                input=b"",
                capture_output=True,
                check=True,
            )
            .stdout.decode()
            .strip()
        )
        # Tags pointing at non-commit objects: a tag NAME must be proven to
        # name a commit exactly like a raw object ID — the peeled entry in
        # ls-remote is not proof of a commit. Annotated tags need an
        # identity in the bare repository.
        for key, value in (
            ("user.email", "core-wheel@test"),
            ("user.name", "core-wheel test"),
        ):
            cls._run(("-C", str(cls.origin), "config", key, value))
        for name, target in (
            ("lightweight-blob-tag", cls.blob_sha),
            ("lightweight-tree-tag", cls.tree_sha),
        ):
            cls._run(("-C", str(cls.origin), "tag", name, target))
        for name, target in (
            ("annotated-blob-tag", cls.blob_sha),
            ("annotated-tree-tag", cls.tree_sha),
        ):
            cls._run(
                ("-C", str(cls.origin), "tag", "-a", name, "-m", "non-commit", target)
            )

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil

        shutil.rmtree(cls._root, ignore_errors=True)

    @staticmethod
    def _run(args: tuple[str, ...]) -> str:
        completed = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, completed.stderr
        return completed.stdout

    @classmethod
    def _git_work(cls, work: Path, *args: str) -> str:
        if args[:2] == ("init", "-q"):
            subprocess.run(["git", *args], capture_output=True, text=True, check=True)
            return ""
        return cls._run(("-C", str(work), *args))

    def _main(self, ref: str, github_output: Path | None) -> tuple[int, dict[str, str]]:
        env = dict(os.environ)
        if github_output is not None:
            env["GITHUB_OUTPUT"] = str(github_output)
        else:
            env.pop("GITHUB_OUTPUT", None)
        with mock.patch.dict(os.environ, env):
            if github_output is not None:
                code = RESOLVE.main(["--ref", ref, "--remote", str(self.origin)])
                if code != 0:
                    return code, {}
                outputs = {}
                for line in github_output.read_text(encoding="utf-8").splitlines():
                    name, _, value = line.partition("=")
                    outputs[name] = value
                return code, outputs
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = RESOLVE.main(["--ref", ref, "--remote", str(self.origin)])
            if code != 0:
                return code, {}
            return code, json.loads(stdout.getvalue())

    def test_branch_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            code, outputs = self._main("release/3.2", Path(raw) / "out")
        self.assertEqual(code, 0)
        self.assertEqual(outputs["sha"], self.commit_sha)
        self.assertEqual(outputs["sha12"], self.commit_sha[:12])
        self.assertEqual(outputs["requested_ref"], "release/3.2")
        self.assertEqual(json.loads(outputs["tags_json"]), ["v3.2.0", "v3.2.0rc1"])

    def test_sha_core_ref_advertised_by_a_ref(self) -> None:
        code, outputs = self._main(self.commit_sha, None)
        self.assertEqual(code, 0)
        self.assertEqual(outputs["sha"], self.commit_sha)
        self.assertEqual(json.loads(outputs["tags_json"]), ["v3.2.0", "v3.2.0rc1"])

    def test_raw_annotated_tag_object_id_peels_to_its_commit(self) -> None:
        """The object ID of an annotated tag resolves to the tagged commit.

        The peeled commit must propagate everywhere: stamp, manifest and
        artifact name all carry outputs["sha"], so returning the tag object
        ID would misreport provenance and drop the tags at that commit.
        """
        self.assertNotEqual(self.tag_object_sha, self.commit_sha)
        with tempfile.TemporaryDirectory() as raw:
            code, outputs = self._main(self.tag_object_sha, Path(raw) / "out")
        self.assertEqual(code, 0)
        self.assertEqual(outputs["sha"], self.commit_sha)
        self.assertEqual(outputs["sha12"], self.commit_sha[:12])
        self.assertEqual(outputs["requested_ref"], self.tag_object_sha)
        self.assertEqual(json.loads(outputs["tags_json"]), ["v3.2.0", "v3.2.0rc1"])

    def test_fetchable_non_commit_object_is_refused(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(sys, "stderr", stderr):
            with redirect_stdout(io.StringIO()):
                code = RESOLVE.main(
                    ["--ref", self.blob_sha, "--remote", str(self.origin)]
                )
        self.assertEqual(code, 2)
        self.assertIn("not a commit", stderr.getvalue())

    def test_named_tags_pointing_at_non_commit_objects_are_refused(self) -> None:
        """A tag name must be proven to name a commit, like a raw object ID.

        A lightweight tag at a blob or a tree, and an annotated tag peeling
        to one, all come out of ls-remote pointing at a non-commit object;
        each is refused with exit 2 and NO success outputs (no stdout JSON,
        no GITHUB_OUTPUT written).
        """
        for tag_name in (
            "lightweight-blob-tag",
            "lightweight-tree-tag",
            "annotated-blob-tag",
            "annotated-tree-tag",
        ):
            with self.subTest(tag=tag_name):
                with tempfile.TemporaryDirectory() as raw:
                    output_path = Path(raw) / "out"
                    env = dict(os.environ, GITHUB_OUTPUT=str(output_path))
                    stdout = io.StringIO()
                    with mock.patch.dict(os.environ, env):
                        with redirect_stdout(stdout):
                            code = RESOLVE.main(
                                ["--ref", tag_name, "--remote", str(self.origin)]
                            )
                    self.assertEqual(code, 2)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertFalse(output_path.exists())

    def test_sha_not_in_repository_fails(self) -> None:
        code, _ = self._main("0" * 40, None)
        self.assertEqual(code, 2)

    def test_unknown_ref_fails(self) -> None:
        code, _ = self._main("no-such-thing", None)
        self.assertEqual(code, 2)


# ── Workflow pins ──────────────────────────────────────────────────


def _job_text(text: str, name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \w[\w-]*:\n|\Z)", text, re.S | re.M)
    assert match is not None, f"workflow has no `{name}` job"
    return match.group(1)


class CallerContextPinsTest(unittest.TestCase):
    """The reusable workflow must select Core regardless of the caller.

    In a called workflow ``github.repository`` and ``github.sha`` belong to
    the CALLER: a private caller (Pro) invoking
    ``rigplane/rigplane-core/.github/workflows/core-wheel.yml@<ref>`` would
    otherwise check out, resolve against and load control files from Pro.
    The fixture models exactly that caller, twice over.
    """

    def setUp(self) -> None:
        self.wheel = WHEEL_YML.read_text(encoding="utf-8")
        self.fixture = json.loads(CALLER_FIXTURE.read_text(encoding="utf-8"))
        self.caller = self.fixture["caller"]

    def _rendered_with_caller_context(self) -> str:
        rendered = self.wheel
        for key, value in self.caller.items():
            rendered = rendered.replace("${{ github.%s }}" % key, value)
        return rendered

    def test_no_caller_context_can_select_core_files(self) -> None:
        # No caller-derived expression remains anywhere in the workflow:
        # after substituting the fixture caller's context there is nothing
        # left to inject, and the caller's own identity appears nowhere.
        for key in self.caller:
            self.assertNotIn("${{ github.%s }}" % key, self.wheel)
        rendered = self._rendered_with_caller_context()
        self.assertNotIn(self.caller["repository"], rendered)
        self.assertNotIn(self.caller["sha"], rendered)

    def test_every_checkout_names_core(self) -> None:
        self.assertEqual(self.wheel.count("uses: actions/checkout@v6"), 3)
        self.assertEqual(
            self.wheel.count("repository: rigplane/rigplane-core"),
            3,
            "controls x2 and the built commit x1 must all name Core",
        )

    def test_controls_follow_the_called_workflows_own_commit(self) -> None:
        # CodeQL cache-poisoning root fix (MOR-4408): the controls used to
        # be checked out from a caller-controlled inputs.controls_ref — a
        # privileged checkout of caller-chosen code (two HIGH alerts).
        # job.workflow_sha is the documented job-context identity (GitHub
        # Actions, April 2026) of the commit of the workflow file that
        # defines this job — exactly the revision the caller invoked with
        # uses: — so no dispatcher or caller can choose different code as
        # controls. github.* cannot express this: in a called workflow
        # github.workflow_sha names the CALLER's workflow file. Not
        # available on GitHub Enterprise Server, which this repository
        # does not use.
        self.assertNotIn("controls_ref", self.wheel)
        self.assertEqual(self.wheel.count("ref: ${{ job.workflow_sha }}"), 2)
        self.assertEqual(self.wheel.count("ref: ${{ needs.resolve.outputs.sha }}"), 1)

    def test_core_remote_is_fixed_to_core(self) -> None:
        self.assertIn(
            "CORE_REMOTE: https://github.com/rigplane/rigplane-core.git", self.wheel
        )

    def test_no_controls_ref_input_remains_on_any_trigger(self) -> None:
        # The input is gone from BOTH triggers — including
        # workflow_dispatch: a dispatcher must not choose which code runs
        # as controls; dispatch uses the dispatched ref's own revision,
        # schedule the default branch's. With the input removed, nothing
        # can override the job.workflow_sha checkout pinned above.
        self.assertNotIn("controls_ref", self.wheel)
        self.assertNotIn("default: 'main'", self.wheel)

    def test_smoke_evidence_artifact_name_is_derived_per_invocation(self) -> None:
        build = _job_text(self.wheel, "build")
        self.assertIn(
            "name: ${{ steps.artifact.outputs.artifact_name }}-installed-profile-smoke",
            build,
        )
        # The artifact step must run before the smoke upload that names it.
        self.assertLess(
            build.index("id: artifact"),
            build.index("Upload installed-profile smoke evidence"),
        )
        names = set()
        for invocation in self.fixture["invocations"]:
            artifact_name = (
                f"{invocation['artifact_name_prefix']}-"
                f"{invocation['resolved_sha'][:12]}"
            )
            names.add(f"{artifact_name}-installed-profile-smoke")
        self.assertEqual(len(names), len(self.fixture["invocations"]))


class CoreWheelWorkflowPinsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.wheel = WHEEL_YML.read_text(encoding="utf-8")
        self.publish = PUBLISH_YML.read_text(encoding="utf-8")
        self.nightly = NIGHTLY_YML.read_text(encoding="utf-8")

    def test_both_triggers_and_inputs(self) -> None:
        for trigger in ("  workflow_call:", "  workflow_dispatch:"):
            self.assertIn(trigger, self.wheel)
        self.assertEqual(self.wheel.count("        required: true"), 2)
        self.assertEqual(self.wheel.count("        default: 'core-wheel'"), 2)
        self.assertEqual(self.wheel.count("        default: 3"), 2)

    def test_call_outputs_are_the_consumer_contract(self) -> None:
        for name in ("sha", "version", "wheel_name", "wheel_sha256", "artifact_name"):
            self.assertIn(f"value: ${{{{ jobs.build.outputs.{name} }}}}", self.wheel)

    def test_resolve_job_shape(self) -> None:
        resolve = _job_text(self.wheel, "resolve")
        self.assertIn("runs-on: [self-hosted, Linux, ARM64, light]", resolve)
        self.assertIn("timeout-minutes: 5", resolve)
        self.assertIn("resolve_core_ref.py", resolve)
        self.assertIn("persist-credentials: false", resolve)
        self.assertIn("outputs.sha", resolve)

    def test_build_job_shape(self) -> None:
        build = _job_text(self.wheel, "build")
        self.assertIn("needs: resolve", build)
        self.assertIn("runs-on: [self-hosted, Linux, ARM64, build]", build)
        # Measured about a minute on the build pool; 15 covers a cold npm ci
        # plus uv build while a hung build still releases the runner.
        self.assertIn("timeout-minutes: 15", build)
        self.assertIn("ref: ${{ needs.resolve.outputs.sha }}", build)
        self.assertIn("persist-credentials: false", build)
        self.assertEqual(build.count("uses: actions/checkout@v6"), 2)

    def test_build_job_uses_the_tested_scripts(self) -> None:
        build = _job_text(self.wheel, "build")
        self.assertEqual(build.count("core_wheel_version.py"), 1)
        self.assertIn("--write-pyproject pyproject.toml", build)
        self.assertIn('--tags-json "$TAGS_JSON"', build)
        self.assertIn("scripts/release/stamp_build_info.py", build)
        self.assertIn('--requested-ref "$CORE_REF"', build)

    def test_build_job_builds_frontend_and_wheel_once(self) -> None:
        build = _job_text(self.wheel, "build")
        self.assertEqual(build.count("npm run build"), 1)
        self.assertEqual(build.count("run: uv build"), 1)

    def test_build_job_runs_without_actions_caches(self) -> None:
        # CodeQL cache-poisoning root fix (MOR-4408): the build job
        # executes code from the checked-out target ref, so no Actions
        # cache may be written or read around it — a shared cache is a
        # poisoning path in the privileged scheduled context. The cost is
        # a cold npm ci and uv install per build; accepted.
        build = _job_text(self.wheel, "build")
        self.assertIn("enable-cache: false", build)
        self.assertNotIn("enable-cache: true", self.wheel)
        self.assertNotIn("cache: 'npm'", build)
        self.assertNotIn("cache-dependency-path", self.wheel)

    def test_installed_profile_smoke_judges_the_target_revision(self) -> None:
        # P1 (MOR-4408): the profile catalog is revision-specific — v3.1.4
        # shipped nine profiles, current Core ships eleven — so the smoke
        # must run the TARGET revision's own script, never the controls'
        # current catalog (see ProfileSmokeCatalogContractTest for why).
        # Provenance (stamp) verification stays with the controls.
        build = _job_text(self.wheel, "build")
        self.assertIn("core/.github/scripts/installed_profile_smoke.py", build)
        self.assertNotIn("control/.github/scripts/installed_profile_smoke.py", build)
        self.assertEqual(build.count("python installed_profile_smoke.py"), 1)
        self.assertIn('--candidate-sha "$RESOLVED_SHA"', build)
        self.assertIn('--forbid-root "$GITHUB_WORKSPACE"', build)
        # The smoke evidence artifact name is derived from the wheel
        # artifact name (see CallerContextPinsTest for the distinctness
        # pin) — never a fixed name two invocations could collide on.
        self.assertIn(
            "name: ${{ steps.artifact.outputs.artifact_name }}-installed-profile-smoke",
            build,
        )

    def test_target_without_the_smoke_script_skips_explicitly(self) -> None:
        # Never fail silently, never pass silently: a target older than
        # the smoke script records profile_smoke=not_available_for_target
        # as the step output that feeds the manifest, and in the job
        # summary; the evidence upload runs only when the smoke ran.
        build = _job_text(self.wheel, "build")
        self.assertIn("profile_smoke=not_available_for_target", build)
        self.assertIn('>> "$GITHUB_STEP_SUMMARY"', build)
        self.assertIn("if: steps.smoke.outputs.profile_smoke == 'target'", build)

    def test_wheel_reports_its_own_provenance(self) -> None:
        build = _job_text(self.wheel, "build")
        # The stamped resource is verified through the controls' contract
        # reader (the stamp script loads build_info.py by file path), so a
        # Core ref older than the contract — no build_info module — still
        # passes; the runtime reader runs only when the target has it.
        self.assertIn("--verify-file", build)
        self.assertIn('--expect-sha "$RESOLVED_SHA"', build)
        self.assertIn('importlib.resources.files("rigplane.core")', build)
        self.assertIn('importlib.util.find_spec("rigplane.core.build_info")', build)
        self.assertIn("load_build_info().git_sha", build)

    def test_core_wheel_json_goes_through_the_typed_contract(self) -> None:
        build = _job_text(self.wheel, "build")
        self.assertIn("../control/.github/scripts/core_wheel_manifest.py", build)
        # The manifest records the profile-smoke outcome, including the
        # explicit not_available_for_target skip, through an env-derived
        # argv value — never ${{ }} text substitution.
        self.assertIn('--profile-smoke "$PROFILE_SMOKE"', build)
        self.assertIn("PROFILE_SMOKE: ${{ steps.smoke.outputs.profile_smoke }}", build)
        # The heredoc with ${{ }} interpolation is gone for good.
        self.assertNotIn("<<'JSON'", build)
        # No ${{ }} text substitution into the manifest-writing step's
        # script: values reach the producer through env-derived argv only.
        artifact_step = build[
            build.index("Write core-wheel.json") : build.index(
                "Upload installed-profile smoke evidence"
            )
        ]
        self.assertNotIn("${{", artifact_step[artifact_step.index("run: |") :])

    def test_artifact_upload_contract(self) -> None:
        build = _job_text(self.wheel, "build")
        self.assertEqual(build.count("uses: actions/upload-artifact@v7"), 2)
        self.assertIn("if-no-files-found: error", build)
        self.assertIn("retention-days: ${{ inputs.retention_days }}", build)
        self.assertIn("core-wheel.json", build)

    def test_publish_build_job_delegates_to_the_reusable_workflow(self) -> None:
        build = _job_text(self.publish, "build")
        self.assertRegex(build, r"(?m)^    needs: validate$")
        self.assertIn("uses: ./.github/workflows/core-wheel.yml", build)
        self.assertIn("core_ref: ${{ github.sha }}", build)
        # controls_ref is no longer an input: core-wheel.yml checks the
        # controls out at its own job.workflow_sha, which for this
        # same-repo call IS the triggering commit. There is nothing to
        # pass (CodeQL cache-poisoning fix, MOR-4408).
        self.assertNotIn("controls_ref", build)
        self.assertIn("artifact_name: dist", build)
        self.assertNotIn("steps:", build)
        self.assertNotIn("npm", build)
        self.assertNotIn("uv build", build)
        self.assertNotIn("upload-artifact", build)

    def test_publish_job_guard_and_upload_unchanged(self) -> None:
        publish = _job_text(self.publish, "publish")
        self.assertIn("if: github.event_name == 'release'", publish)
        self.assertIn("needs: build", publish)
        self.assertIn(
            'run: uv publish --trusted-publishing always "$DIST_DIR"/*.whl "$DIST_DIR"/*.tar.gz',
            publish,
        )

    def test_nightly_shape(self) -> None:
        self.assertIn("- cron: '10 5 * * *'", self.nightly)
        self.assertIn("workflow_dispatch:", self.nightly)
        self.assertIn("group: core-wheel-nightly", self.nightly)
        self.assertIn("uses: ./.github/workflows/core-wheel.yml", self.nightly)
        self.assertIn("core_ref: main", self.nightly)
        self.assertNotIn("controls_ref", self.nightly)
        # The cron slot justification must stay next to the schedule.
        self.assertIn("# 05:10 UTC", self.nightly)

    def test_quick_ci_route_runs_this_file(self) -> None:
        quick = QUICK_YML.read_text(encoding="utf-8")
        self.assertIn(
            "uv run --no-project python3 .github/scripts/core-wheel.test.py",
            quick,
        )


if __name__ == "__main__":
    unittest.main()
