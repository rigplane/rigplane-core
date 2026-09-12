#!/usr/bin/env python3
"""Render the authoritative face map and the selected-region comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


COLOURS = ("#ff4d4d", "#4dd2ff", "#ffdc4d", "#75ff75", "#d68cff")


def pixel_box(bounds: dict[str, float], width: int, height: int) -> tuple[int, int, int, int]:
    left = round(bounds["x"] * width)
    top = round(bounds["y"] * height)
    right = round((bounds["x"] + bounds["width"]) * width)
    bottom = round((bounds["y"] + bounds["height"]) * height)
    return left, top, right, bottom


def labelled(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, colour: str) -> None:
    font = ImageFont.load_default(size=18)
    box = draw.textbbox(xy, text, font=font, stroke_width=2)
    draw.rectangle((box[0] - 4, box[1] - 3, box[2] + 4, box[3] + 3), fill="#000000cc")
    draw.text(xy, text, fill=colour, font=font, stroke_fill="#000000", stroke_width=2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("--annotated", required=True, type=Path)
    parser.add_argument("--crop", required=True, type=Path)
    parser.add_argument("--render", type=Path)
    parser.add_argument("--comparison", type=Path)
    args = parser.parse_args()

    face_map = json.loads(args.map.read_text(encoding="utf-8"))
    source = Image.open(face_map["source"]["file"]).convert("RGB")
    if source.size != (face_map["source"]["width"], face_map["source"]["height"]):
        raise SystemExit(f"source dimensions changed: {source.size}")

    annotated = source.convert("RGBA")
    draw = ImageDraw.Draw(annotated, "RGBA")
    selected_id = face_map["selectedRegion"]
    selected = None
    for index, region in enumerate(face_map["regions"]):
        box = pixel_box(region["bounds"], *source.size)
        colour = COLOURS[index % len(COLOURS)]
        width = 6 if region["id"] == selected_id else 3
        draw.rectangle(box, outline=colour, width=width)
        labelled(draw, (box[0] + 5, box[1] + 5), region["id"], colour)
        if region["id"] == selected_id:
            selected = region
    if selected is None:
        raise SystemExit(f"selected region not found: {selected_id}")

    args.annotated.parent.mkdir(parents=True, exist_ok=True)
    annotated.convert("RGB").save(args.annotated)

    selected_crop = source.crop(pixel_box(selected["bounds"], *source.size))
    args.crop.parent.mkdir(parents=True, exist_ok=True)
    selected_crop.save(args.crop)

    if args.render is None:
        return
    if args.comparison is None:
        raise SystemExit("--comparison is required with --render")

    current = Image.open(args.render).convert("RGB")
    if current.size != selected_crop.size:
        current = current.resize(selected_crop.size, Image.Resampling.LANCZOS)
    overlay = Image.blend(selected_crop, current, 0.5)
    gap = 18
    title_height = 34
    sheet = Image.new(
        "RGB",
        (selected_crop.width * 3 + gap * 4, selected_crop.height + title_height + gap * 2),
        "#16191d",
    )
    sheet_draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=20)
    for index, (title, panel) in enumerate(
        (("REFERENCE CROP", selected_crop), ("CURRENT RENDER", current), ("50% OVERLAY", overlay))
    ):
        left = gap + index * (selected_crop.width + gap)
        sheet_draw.text((left, gap), title, fill="#f4f6f8", font=font)
        sheet.paste(panel, (left, gap + title_height))
    args.comparison.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.comparison)


if __name__ == "__main__":
    main()
