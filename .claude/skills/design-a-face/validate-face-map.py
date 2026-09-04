#!/usr/bin/env python3
"""Validate the normalized image map required before radio-face codegen."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, NoReturn


KINDS = {
    "discrete-state", "scalar-meter", "numeric-readout", "trace", "envelope",
    "radio-control", "composite-face", "chrome", "unidentified",
}
CONFIDENCE = {"confirmed", "probable", "ambiguous"}
STATUS = {"identified", "unidentified"}
RENDERERS = {"svg", "canvas", "html-css", "reuse", "unknown"}


def die(message: str) -> NoReturn:
    raise SystemExit(f"validate-face-map: {message}")


def require_object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        die(f"{where} must be an object")
    return value


def require_bounds(value: Any, where: str) -> None:
    bounds = require_object(value, where)
    if set(bounds) != {"x", "y", "width", "height"}:
        die(f"{where} must contain exactly x, y, width, height")
    for key, raw in bounds.items():
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            die(f"{where}.{key} must be numeric")
        if raw < 0 or raw > 1:
            die(f"{where}.{key} must be between 0 and 1")
    if bounds["width"] <= 0 or bounds["height"] <= 0:
        die(f"{where} width and height must be positive")
    if bounds["x"] + bounds["width"] > 1.000001 or bounds["y"] + bounds["height"] > 1.000001:
        die(f"{where} extends outside its normalized parent")


def validate(data: Any, *, check_file: bool = True) -> None:
    root = require_object(data, "root")
    if root.get("version") != 1:
        die("version must be 1")
    if root.get("complexity") not in {"flat", "compound"}:
        die("complexity must be flat or compound")

    source = require_object(root.get("source"), "source")
    for key in ("file", "sha256", "width", "height", "panelCrop"):
        if key not in source:
            die(f"source.{key} is required")
    if not isinstance(source["file"], str) or not source["file"].startswith("/"):
        die("source.file must be an absolute path")
    if not isinstance(source["sha256"], str) or len(source["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in source["sha256"]):
        die("source.sha256 must be 64 lowercase hex characters")
    if not all(isinstance(source[key], int) and not isinstance(source[key], bool) and source[key] > 0 for key in ("width", "height")):
        die("source width and height must be positive integers")
    require_bounds(source["panelCrop"], "source.panelCrop")
    if check_file:
        path = Path(source["file"])
        if not path.is_file():
            die(f"source file does not exist: {path}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != source["sha256"]:
            die(f"source.sha256 mismatch: expected {source['sha256']}, got {actual}")

    regions = root.get("regions")
    if not isinstance(regions, list) or not regions:
        die("regions must be a non-empty array")
    ids: set[str] = set()
    for index, raw in enumerate(regions):
        region = require_object(raw, f"regions[{index}]")
        for key in ("id", "kind", "status", "confidence", "label", "group", "bounds", "renderer", "evidence"):
            if key not in region:
                die(f"regions[{index}].{key} is required")
        region_id = region["id"]
        if not isinstance(region_id, str) or not region_id:
            die(f"regions[{index}].id must be a non-empty string")
        if region_id in ids:
            die(f"duplicate region id: {region_id}")
        ids.add(region_id)
        if region["kind"] not in KINDS:
            die(f"regions[{index}].kind is invalid")
        if region["status"] not in STATUS:
            die(f"regions[{index}].status is invalid")
        if region["confidence"] not in CONFIDENCE:
            die(f"regions[{index}].confidence is invalid")
        if region["renderer"] not in RENDERERS:
            die(f"regions[{index}].renderer is invalid")
        if not isinstance(region["label"], str) or not isinstance(region["group"], str):
            die(f"regions[{index}] label and group must be strings")
        if not isinstance(region["evidence"], list) or not region["evidence"] or not all(isinstance(item, str) and item for item in region["evidence"]):
            die(f"regions[{index}].evidence must be a non-empty string array")
        require_bounds(region["bounds"], f"regions[{index}].bounds")
    for index, region in enumerate(regions):
        repeated = region.get("repeatedFrom")
        if repeated is not None and repeated not in ids:
            die(f"regions[{index}].repeatedFrom names missing region {repeated}")

    selected = root.get("selectedRegion")
    if root["complexity"] == "compound" and selected not in ids:
        die("compound map selectedRegion must name one region")
    if selected is not None and selected not in ids:
        die("selectedRegion must be null or name one region")


def selftest() -> None:
    with tempfile.TemporaryDirectory() as directory:
        image = Path(directory) / "reference.bin"
        image.write_bytes(b"radio-face-reference")
        base = {
            "version": 1,
            "complexity": "compound",
            "source": {
                "file": str(image),
                "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "width": 100,
                "height": 50,
                "panelCrop": {"x": 0, "y": 0, "width": 1, "height": 1},
            },
            "selectedRegion": "meter",
            "regions": [{
                "id": "meter", "kind": "scalar-meter", "status": "identified",
                "confidence": "confirmed", "label": "S", "group": "main",
                "bounds": {"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.3},
                "renderer": "svg", "evidence": ["visible radial scale"],
            }],
        }
        validate(base)
        broken = json.loads(json.dumps(base))
        broken["regions"][0]["bounds"]["width"] = 1
        try:
            validate(broken)
        except SystemExit:
            pass
        else:
            die("selftest failed to reject an out-of-bounds region")
        broken = json.loads(json.dumps(base))
        broken["source"]["sha256"] = "0" * 64
        try:
            validate(broken)
        except SystemExit:
            pass
        else:
            die("selftest failed to reject a source hash mismatch")
    print("validate-face-map: selftest PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selftest")
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("map")
    args = parser.parse_args()
    if args.command == "selftest":
        selftest()
        return
    try:
        data = json.loads(Path(args.map).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read map: {exc}")
    validate(data)
    print("validate-face-map: PASS")


if __name__ == "__main__":
    main()
