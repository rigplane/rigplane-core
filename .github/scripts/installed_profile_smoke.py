#!/usr/bin/env python3
"""Hermetic smoke for radio profiles installed from a RigPlane wheel.

Run this copied outside the source checkout with the candidate wheel installed
into the active virtual environment.  It never opens serial, audio, or radio
hardware.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import tomllib
from importlib import metadata, resources
from pathlib import Path
from typing import Iterable


EXPECTED_PROFILES: dict[str, tuple[str, str]] = {
    "ftx1.toml": ("yaesu_ftx1", "FTX-1"),
    "ic705.toml": ("icom_ic705", "IC-705"),
    "ic7300.toml": ("icom_ic7300", "IC-7300"),
    "ic7610.toml": ("icom_ic7610", "IC-7610"),
    "ic9700.toml": ("icom_ic9700", "IC-9700"),
    "tx500.toml": ("lab599_tx500", "TX-500"),
    "x6100.toml": ("xiegu_x6100", "X6100"),
    "x6200.toml": ("xiegu_x6200", "X6200"),
}
REQUIRED_RESOURCES = ("_keyboard-default.toml",)


class SmokeFailure(RuntimeError):
    """The installed artifact does not satisfy the packaged-profile contract."""


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def validate_catalog(names: Iterable[str]) -> tuple[str, ...]:
    """Require an exact, reviewed catalog so missing profiles cannot vanish."""
    actual = tuple(sorted(names))
    expected = tuple(EXPECTED_PROFILES)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        raise SmokeFailure(
            f"packaged profile catalog mismatch: missing={missing}, "
            f"unexpected={unexpected}"
        )
    return actual


def _package_rigs():
    return resources.files("rigplane").joinpath("rigs")


def _profile_resource_names(rig_resources) -> tuple[str, ...]:
    if not rig_resources.is_dir():
        raise SmokeFailure("installed package resource rigplane/rigs is missing")
    names = (
        entry.name
        for entry in rig_resources.iterdir()
        if entry.is_file()
        and entry.name.endswith(".toml")
        and not entry.name.startswith("_")
        and not entry.name.endswith(".draft.toml")
    )
    return validate_catalog(names)


def _copy_toml_resources(rig_resources, destination: Path) -> None:
    for entry in rig_resources.iterdir():
        if entry.is_file() and entry.name.endswith(".toml"):
            (destination / entry.name).write_bytes(entry.read_bytes())


def prove_corrupt_profile_fails_closed(rig_resources=None) -> str:
    """Corrupt a disposable resource copy and require an actionable failure."""
    from rigplane.profiles.rig_loader import RigLoadError, discover_rigs

    source = rig_resources if rig_resources is not None else _package_rigs()
    with tempfile.TemporaryDirectory(prefix="rigplane-corrupt-profile-") as raw_dir:
        directory = Path(raw_dir)
        _copy_toml_resources(source, directory)
        corrupt = directory / "ftx1.toml"
        if not corrupt.exists():
            raise SmokeFailure("negative proof cannot find packaged ftx1.toml")
        corrupt.write_text("[radio\n", encoding="utf-8")
        try:
            discover_rigs(directory)
        except RigLoadError as exc:
            diagnostic = str(exc)
            if (
                "ftx1.toml" not in diagnostic
                or "failed to parse TOML" not in diagnostic
            ):
                raise SmokeFailure(
                    f"corrupt profile diagnostic is not actionable: {diagnostic}"
                ) from exc
            return diagnostic
    raise SmokeFailure("corrupt packaged profile was accepted")


def exercise_ftx1_construction(rigs=None) -> dict[str, object]:
    """Construct the packaged Yaesu backend without connecting any device."""
    from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
    from rigplane.profiles.rig_loader import discover_available_rigs

    catalog = rigs if rigs is not None else discover_available_rigs()
    try:
        config = catalog["FTX-1"]
    except KeyError as exc:
        raise SmokeFailure("public packaged loader did not resolve FTX-1") from exc
    radio = YaesuCatRadio(
        "/dev/rigplane-installed-profile-smoke-never-open",
        profile=config,
        audio_driver=object(),
    )
    result = {
        "backend": radio.backend_id,
        "connected": radio.connected,
        "model": radio.model,
        "profile_id": radio.profile.id,
    }
    if result != {
        "backend": "yaesu_cat",
        "connected": False,
        "model": "FTX-1",
        "profile_id": "yaesu_ftx1",
    }:
        raise SmokeFailure(f"unexpected FTX-1 construction result: {result}")
    return result


def _assert_isolated_install(forbid_root: Path | None) -> Path:
    import rigplane

    package_root = Path(rigplane.__file__).resolve().parent
    distribution_root = Path(metadata.distribution("rigplane").locate_file(""))
    distribution_root = distribution_root.resolve()
    if not _is_relative_to(package_root, distribution_root):
        raise SmokeFailure(
            f"rigplane imported outside installed distribution: {package_root}"
        )
    if forbid_root is not None:
        forbidden = forbid_root.resolve()
        inspected = [package_root, Path.cwd().resolve()]
        inspected.extend(
            Path(entry).resolve()
            for entry in sys.path
            if entry and Path(entry).exists()
        )
        leaked = [str(path) for path in inspected if _is_relative_to(path, forbidden)]
        if leaked:
            raise SmokeFailure(f"source-checkout path leaked into smoke: {leaked}")
    return package_root


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_smoke(
    *, artifact: Path, candidate_sha: str, forbid_root: Path | None
) -> dict[str, object]:
    """Run the installed-artifact proof and return deterministic evidence."""
    if not re.fullmatch(r"[0-9a-f]{40}", candidate_sha):
        raise SmokeFailure("candidate SHA must be exactly 40 lowercase hex characters")
    if not artifact.is_file() or artifact.suffix != ".whl":
        raise SmokeFailure(f"candidate wheel not found: {artifact}")

    package_root = _assert_isolated_install(forbid_root)
    rig_resources = _package_rigs()
    names = _profile_resource_names(rig_resources)
    resource_names = {
        entry.name for entry in rig_resources.iterdir() if entry.is_file()
    }
    missing_resources = sorted(set(REQUIRED_RESOURCES) - resource_names)
    if missing_resources:
        raise SmokeFailure(f"required packaged resources missing: {missing_resources}")

    from rigplane.profiles.rig_loader import discover_available_rigs

    rigs = discover_available_rigs()
    if len(rigs) != len(names):
        raise SmokeFailure(
            f"loader/catalog count mismatch: loaded={len(rigs)}, catalog={len(names)}"
        )
    profiles: list[dict[str, str]] = []
    for filename in names:
        resource = rig_resources.joinpath(filename)
        identity = tomllib.loads(resource.read_text(encoding="utf-8"))["radio"]
        expected_id, expected_model = EXPECTED_PROFILES[filename]
        if (identity.get("id"), identity.get("model")) != (
            expected_id,
            expected_model,
        ):
            raise SmokeFailure(
                f"unexpected identity in packaged {filename}: {identity}"
            )
        config = rigs.get(expected_model)
        if config is None or config.id != expected_id or config.keyboard is None:
            raise SmokeFailure(
                f"packaged loader failed identity/include resolution for {filename}"
            )
        profiles.append({"filename": filename, "id": config.id, "model": config.model})

    negative_diagnostic = prove_corrupt_profile_fails_closed(rig_resources)
    ftx1 = exercise_ftx1_construction(rigs)
    return {
        "artifact": artifact.name,
        "artifact_sha256": _sha256(artifact),
        "candidate_sha": candidate_sha,
        "ftx1": ftx1,
        "negative_diagnostic": negative_diagnostic,
        "package_root": str(package_root),
        "package_version": metadata.version("rigplane"),
        "profile_count": len(profiles),
        "profiles": profiles,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--forbid-root", type=Path)
    args = parser.parse_args()
    try:
        evidence = run_smoke(
            artifact=args.artifact.resolve(),
            candidate_sha=args.candidate_sha,
            forbid_root=args.forbid_root,
        )
    except Exception as exc:
        print(f"installed profile smoke FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
