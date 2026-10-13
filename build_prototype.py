"""Reproducible, offline Options title patch for one verified Steam SWF."""

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

from install_patch import ORIGINAL_SHA256, PROJECT
from swf_geometry import (
    Tag,
    build_font,
    build_shape,
    build_text,
    parse_font,
    parse_shape,
    parse_text,
    read_swf,
    shape_bounds,
    signed_bits,
    write_swf,
)

NEW_FONT_ID = 6000
TITLE_ID = 3016
TRANSLATION = "Настройки"
EM = 20480
# Reduced capitals suit this upright, inscription-like face. No system fallback.
XHEIGHT = 10360
RECIPES = {
    "Н": ("H", "copy"),
    "а": ("a", "copy"),
    "с": ("c", "copy"),
    "т": ("T", "small-cap"),
    "р": ("p", "copy"),
    "о": ("o", "copy"),
    "й": ("N", "mirror-small-cap-breve"),
    "к": ("K", "small-cap"),
    "и": ("N", "mirror-small-cap"),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def transform(contours, sx=1, sy=1, tx=0, ty=0):
    result = []
    for contour in contours:
        transformed = []
        for segment in contour:
            coords = []
            for i in range(1, len(segment), 2):
                coords.extend(
                    (round(segment[i] * sx + tx), round(segment[i + 1] * sy + ty))
                )
            transformed.append((segment[0], *coords))
        result.append(transformed)
    return result


def make_font(source):
    font = copy.deepcopy(source)
    font.update(
        id=NEW_FONT_ID,
        name=b"GCCS RU title experiment\x00",
        language=0,
        codes=[],
        glyphs=[],
        advances=[],
        bounds=[],
        kerning=[],
    )
    for ch in sorted(RECIPES):
        donor, recipe = RECIPES[ch]
        index = source["codes"].index(ord(donor))
        raw = source["glyphs"][index]
        advance = source["advances"][index]
        contours = parse_shape(raw)
        if recipe != "copy":
            ymin = shape_bounds(contours)[2]
            lower_donor = "n" if donor == "N" else donor.lower()
            lower_advance = source["advances"][source["codes"].index(ord(lower_donor))]
            old_advance = advance
            advance = lower_advance

            def resize_point(x, y):
                # Preserve top/baseline serif bands while shortening the middle.
                # Preserve the N/K outer stems, or the T central vertical stroke.
                band = 1000
                if y < ymin + band:
                    ny = -XHEIGHT + y - ymin
                elif y > -band:
                    ny = y
                else:
                    ny = (
                        -XHEIGHT
                        + band
                        + (y - ymin - band) * (XHEIGHT - 2 * band) / (-ymin - 2 * band)
                    )
                if donor == "T":
                    center, half = old_advance / 2, 1900
                    ratio = (advance / 2 - half) / (old_advance / 2 - half)
                    if x < center - half:
                        nx = (x - (center - half)) * ratio + advance / 2 - half
                    elif x > center + half:
                        nx = (x - (center + half)) * ratio + advance / 2 + half
                    else:
                        nx = x - center + advance / 2
                else:
                    protected = 5000
                    if x < protected:
                        nx = x
                    elif x > old_advance - protected:
                        nx = x - old_advance + advance
                    else:
                        nx = protected + (x - protected) * (advance - 2 * protected) / (
                            old_advance - 2 * protected
                        )
                return round(nx), round(ny)

            resized = []
            for contour in contours:
                new_contour = []
                for segment in contour:
                    coords = []
                    for i in range(1, len(segment), 2):
                        coords.extend(resize_point(segment[i], segment[i + 1]))
                    new_contour.append((segment[0], *coords))
                resized.append(new_contour)
            contours = resized
            if recipe.startswith("mirror"):
                contours = transform(contours, sx=-1, tx=advance)
            if recipe.endswith("breve"):
                # Reconstruct the original u's lower bowl, remove stems/serifs,
                # symmetrize and thicken the inner arc for small UI rendering.
                bowl = [
                    [
                        ("M", 2810, -4550),
                        ("Q", 2810, 350, 8420, 350),
                        ("Q", 14030, 350, 14030, -4550),
                        ("L", 11530, -4550),
                        ("Q", 11530, -1400, 8420, -1400),
                        ("Q", 5230, -1400, 5230, -4550),
                        ("L", 2810, -4550),
                    ]
                ]
                breve = transform(bowl, sx=0.50, sy=0.50)
                bounds = shape_bounds(breve)
                breve = transform(
                    breve,
                    tx=round(advance / 2 - (bounds[0] + bounds[1]) / 2),
                    ty=-12350,
                )
                contours += breve
            raw = build_shape(contours)
        font["codes"].append(ord(ch))
        font["glyphs"].append(raw)
        font["advances"].append(advance)
        font["bounds"].append(shape_bounds(contours))
    return font


def ink_bounds(font, entries, height, x=0, y=0):
    rects = []
    for index, advance in entries:
        bounds = shape_bounds(parse_shape(font["glyphs"][index]))
        rects.append(
            [
                x + bounds[0] * height / EM,
                x + bounds[1] * height / EM,
                y + bounds[2] * height / EM,
                y + bounds[3] * height / EM,
            ]
        )
        x += advance
    return [
        math.floor(min(r[0] for r in rects)),
        math.ceil(max(r[1] for r in rects)),
        math.floor(min(r[2] for r in rects)),
        math.ceil(max(r[3] for r in rects)),
    ]


def patch_parts(original):
    if sha(original) != ORIGINAL_SHA256:
        raise ValueError("Unknown original SHA-256; refusing this version")
    prefix, tags = read_swf(original)
    # This ID is greater than any character ID in this known version. Never infer
    # applicability to another SWF from this check alone.
    if any(
        len(t.payload) >= 2 and int.from_bytes(t.payload[:2], "little") == NEW_FONT_ID
        for t in tags
    ):
        raise ValueError("Potential character ID collision")
    source = parse_font(
        next(
            t.payload
            for t in tags
            if t.code == 75 and int.from_bytes(t.payload[:2], "little") == 1
        )
    )
    font = make_font(source)
    position = next(
        i
        for i, t in enumerate(tags)
        if t.code == 11 and int.from_bytes(t.payload[:2], "little") == TITLE_ID
    )
    old = parse_text(tags[position].payload)
    if len(old["records"]) != 1:
        raise ValueError("Unexpected title records")
    record = old["records"][0]
    if record["font_id"] != 1 or record["height"] != 480:
        raise ValueError("Unexpected original font or height")
    if "".join(chr(source["codes"][i]) for i, _ in record["entries"]) != "Options":
        raise ValueError("Unexpected original title")
    title = copy.deepcopy(old)
    new_record = title["records"][0]
    new_record["font_id"] = NEW_FONT_ID
    new_record["entries"] = [
        (
            font["codes"].index(ord(ch)),
            round(
                font["advances"][font["codes"].index(ord(ch))] * record["height"] / EM
            ),
        )
        for ch in TRANSLATION
    ]
    local = ink_bounds(font, new_record["entries"], record["height"])
    center = (old["bounds"][0] + old["bounds"][1]) / 2
    new_record["x"] = round(center - (local[0] + local[1]) / 2)
    title["bounds"] = ink_bounds(
        font, new_record["entries"], record["height"], new_record["x"], record["y"]
    )
    title["gb"] = (len(font["codes"]) - 1).bit_length()
    title["ab"] = signed_bits([a for _, a in new_record["entries"]])
    # The title can grow horizontally without distorting glyphs. Keep it inside
    # a conservative 300..600 local-pixel band, with the old optical center.
    if title["bounds"][0] < 6000 or title["bounds"][1] > 12000:
        raise ValueError("Title exceeds the selected safe horizontal band")
    return prefix, tags, position, font, title


def build_patch(original):
    prefix, tags, position, font, title = patch_parts(original)
    tags[position : position + 1] = [
        Tag(75, build_font(font)),
        Tag(11, build_text(title)),
    ]
    return write_swf(prefix, tags, original[3])


def validate_patch(original, patched):
    prefix, before, position, expected_font, expected_title = patch_parts(original)
    next_prefix, after = read_swf(patched)
    if patched[:4] != original[:4] or prefix[8:] != next_prefix[8:]:
        raise ValueError("SWF version or stage/frame header changed")
    if len(after) != len(before) + 1:
        raise ValueError("Unexpected tag count")
    if after[position].code != 75 or after[position].payload != build_font(
        expected_font
    ):
        raise ValueError("New font differs from the reproducible recipe")
    if after[position + 1].code != 11 or after[position + 1].payload != build_text(
        expected_title
    ):
        raise ValueError("Title differs from the reproducible recipe")
    aligned = after[:position] + after[position + 1 :]
    for i, (a, b) in enumerate(zip(before, aligned)):
        if i != position and (a.code != b.code or a.raw != b.raw):
            raise ValueError(f"Unrelated tag changed at position {i}, code {a.code}")
    fonts = {
        int.from_bytes(t.payload[:2], "little"): parse_font(t.payload)
        for t in after
        if t.code == 75
    }
    glyph_count = 0
    for f in fonts.values():
        if f["codes"] != sorted(set(f["codes"])):
            raise ValueError("Unsorted or duplicated code table")
        for glyph in f["glyphs"]:
            contours = parse_shape(glyph)
            if parse_shape(build_shape(contours)) != contours:
                raise ValueError("Glyph geometry roundtrip failed")
            glyph_count += 1
    title = parse_text(after[position + 1].payload)
    record = title["records"][0]
    translated = "".join(
        chr(fonts[record["font_id"]]["codes"][i]) for i, _ in record["entries"]
    )
    if translated != TRANSLATION:
        raise ValueError("Decoded translation differs")
    # Check all other static glyph indices against preserved font definitions.
    static_count = 0
    for tag in after:
        if tag.code in (11, 33):
            text = parse_text(tag.payload, tag.code)
            active_font = None
            for r in text["records"]:
                active_font = r.get("font_id", active_font)
                if active_font not in fonts or any(
                    i >= len(fonts[active_font]["glyphs"]) for i, _ in r["entries"]
                ):
                    raise ValueError("Invalid static text glyph reference")
            static_count += 1
    original_rect = parse_text(before[position].payload)["bounds"]
    return {
        "original_sha256": sha(original),
        "patched_sha256": sha(patched),
        "original_bytes": len(original),
        "patched_bytes": len(patched),
        "original_tag_count": len(before),
        "patched_tag_count": len(after),
        "original_tags_unchanged_except_title": len(before) - 1,
        "changed_original_resources": [{"tag": 11, "id": TITLE_ID}],
        "added_resources": [{"tag": 75, "id": NEW_FONT_ID, "glyphs": 9}],
        "decoded_translation": translated,
        "glyph_shapes_validated": glyph_count,
        "static_texts_validated": static_count,
        "original_fonts_byte_identical": True,
        "abc_and_other_tags_byte_identical": True,
        "font_size_px": record["height"] / 20,
        "color_rgb": record["color"].hex(),
        "old_bounds_twips": original_rect,
        "new_bounds_twips": title["bounds"],
        "optical_center_delta_px": (
            (title["bounds"][0] + title["bounds"][1])
            - (original_rect[0] + original_rect[1])
        )
        / 40,
        "runtime_verified": False,
        "preview_limit": "Offline outline rendering does not prove AIR antialiasing, filters, or startup.",
        "new_font_alignment_zones": "Not supplied: optional; existing CSM settings and placement filters are preserved. Runtime comparison required.",
        "glyph_recipes": {
            ch: {"donor": v[0], "method": v[1]} for ch, v in RECIPES.items()
        },
    }


def create_previews(original, patched):
    from PIL import Image, ImageDraw, ImageFont
    from preview_typography import draw_run, samples, text_entries

    _, before = read_swf(original)
    _, after = read_swf(patched)
    originals = [parse_font(t.payload) for t in before if t.code == 75]
    new_font = parse_font(
        next(
            t.payload
            for t in after
            if t.code == 75 and int.from_bytes(t.payload[:2], "little") == NEW_FONT_ID
        )
    )
    old_text = parse_text(
        next(
            t.payload
            for t in before
            if t.code == 11 and int.from_bytes(t.payload[:2], "little") == TITLE_ID
        )
    )
    new_text = parse_text(
        next(
            t.payload
            for t in after
            if t.code == 11 and int.from_bytes(t.payload[:2], "little") == TITLE_ID
        )
    )
    dest = PROJECT / "artifacts"
    dest.mkdir(exist_ok=True)
    samples(originals, dest / "original-fonts.png")
    image = Image.new("RGB", (1000, 760), (23, 20, 18))
    draw = ImageDraw.Draw(image)
    draw.text(
        (20, 15),
        "Offline outline comparison - not a game screenshot",
        fill=(190, 180, 170),
    )
    for row, (label, font, text) in enumerate(
        [("Original", originals[0], old_text), ("RU prototype", new_font, new_text)]
    ):
        y = 40 + row * 120
        record = text["records"][0]
        entries = [(i, a / 20) for i, a in record["entries"]]
        draw.text((20, y), label + " / 24 px", fill=(190, 180, 170))
        draw_run(image, font, entries, 24, (20, y + 50))
        draw_run(image, font, entries, 24, (350, y + 90), magnify=3)
    for row, (directory, filename) in enumerate(
        [
            ("cormorantunicase", "CormorantUnicase-Regular.ttf"),
            ("kurale", "Kurale-Regular.ttf"),
            ("alice", "Alice-Regular.ttf"),
        ]
    ):
        y = 335 + row * 100
        draw.text((20, y), directory + " / OFL reference / 24 px", fill=(190, 180, 170))
        path = PROJECT / "reference-fonts" / directory / filename
        reference = ImageFont.truetype(str(path), 24)
        draw.text(
            (20, y + 20),
            "Options  " + TRANSLATION,
            font=reference,
            fill=(253, 229, 202),
        )
        reference_large = ImageFont.truetype(str(path), 52)
        draw.text((350, y), TRANSLATION, font=reference_large, fill=(253, 229, 202))
    draw.text(
        (20, 680),
        "Mixed original Latin and RU at the same 24 px em size",
        fill=(190, 180, 170),
    )
    draw_run(
        image, originals[0], text_entries(originals[0], "Options", 24), 24, (20, 735)
    )
    draw_run(image, new_font, text_entries(new_font, TRANSLATION, 24), 24, (170, 735))
    image.save(dest / "typography-comparison.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "output")
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    original = args.source.read_bytes()
    patched = build_patch(original)
    report = validate_patch(original, patched)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "gc-cs-steam.ru.swf"
    validation_path = output / "static-validation.json"
    for path in (destination, validation_path):
        if (
            path.is_symlink()
            or path.resolve() == args.source.resolve()
            or (path.exists() and (path.samefile(args.source) or path.stat().st_nlink > 1))
        ):
            raise ValueError("Build output must not overwrite a source or linked file")
    destination.write_bytes(patched)
    if sha(destination.read_bytes()) != report["patched_sha256"]:
        raise ValueError("Output verification failed")
    validation_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    if args.preview:
        create_previews(original, patched)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
