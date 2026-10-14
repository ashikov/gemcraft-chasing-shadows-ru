"""Build a local menu candidate from guarded workspace sites and private recipes.

No translations or game-derived outlines are stored in this tool. Only selected
pushstring operands, extended font tables and their alignment tables may change.
The existing title experiment remains the base recipe, including its exact font.
"""

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from abc_patch import patch_pushstrings
from build_prototype import TRANSLATION, TITLE_ID, patch_parts, sha
from install_patch import ORIGINAL_SHA256
from inventory_strings import extract_inventory, walk_tags
from localization_fonts import extend_font, measure_text
from localization_workspace import validate_workspace, write_json
from swf_geometry import (
    Tag,
    build_font,
    build_text,
    parse_font,
    parse_text,
    read_swf,
    write_swf,
)
from translation_catalog import load_catalog


def extend_alignment_zones(payload, before_codes, after_codes):
    """Preserve old glyph gridfit records; new outlines have disabled zones.

    SWF19 mandates NumZoneData=2. Each record is two FLOAT16 pairs and a mask.
    Zero masks disable new glyph zones; original Latin gridfit is unchanged.
    """
    if len(payload) != 3 + 10 * len(before_codes) or payload[2] & 63:
        raise ValueError("Unsupported font alignment table")
    records = {}
    for index, code in enumerate(before_codes):
        raw = payload[3 + index * 10 : 3 + (index + 1) * 10]
        if raw[0] != 2 or raw[-1] & 252:
            raise ValueError("Unsupported font zone record")
        records[code] = raw
    if not set(before_codes) <= set(after_codes):
        raise ValueError("Original font codes removed")
    return payload[:3] + b"".join(
        records.get(code, b"\2" + b"\0" * 9) for code in after_codes
    )


def check_layout(font, translation, layout):
    height, limit = layout["height"], layout["max_width"]
    if (
        type(height) is not int
        or height <= 0
        or isinstance(limit, bool)
        or not isinstance(limit, (int, float))
        or not math.isfinite(limit)
        or limit <= 0
    ):
        raise ValueError("Invalid layout dimensions")
    if "max_height" in layout:
        maximum = layout["max_height"]
        if (
            isinstance(maximum, bool)
            or not isinstance(maximum, (int, float))
            or not math.isfinite(maximum)
            or maximum <= 0
        ):
            raise ValueError("Invalid layout dimensions")
    budget = layout.get("dynamic_suffix_budget", 0)
    if type(budget) is not int or budget < 0 or budget >= limit:
        raise ValueError("Invalid dynamic value budget")
    available = limit - budget
    # Conservative word wrapping; Flash metrics, padding and effects still need
    # runtime checking. Never shrink the font silently to make a caption fit.
    lines = []
    for line in translation.split("\n"):
        if not layout.get("wrap"):
            lines.append(line)
            continue
        current = ""
        for word in line.split(" "):
            if measure_text(font, word, height)["widths"][0] > available:
                raise ValueError("Word exceeds available field width")
            candidate = current + (" " if current else "") + word
            if measure_text(font, candidate, height)["widths"][0] > available:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    measured = measure_text(font, "\n".join(lines), height)
    scale = height / 20480
    ascent, descent = font["ascent"] * scale, font["descent"] * scale
    for row, (width, bounds) in enumerate(zip(measured["widths"], measured["bounds"])):
        baseline = row * measured["line_height"]
        if bounds and (bounds[2] < baseline - ascent or bounds[3] > baseline + descent):
            raise ValueError("Caption exceeds vertical font metrics")
        ink_width = bounds[1] - min(0, bounds[0]) if bounds else 0
        if max(width, ink_width) > available:
            raise ValueError("Caption exceeds available field width")
    if len(lines) > layout.get("max_lines", 1):
        raise ValueError("Caption exceeds available line count")
    if (
        "max_height" in layout
        and max(
            len(lines) * measured["line_height"],
            max((b[3] for b in measured["bounds"] if b), default=0)
            - min((b[2] for b in measured["bounds"] if b), default=0),
        )
        > layout["max_height"]
    ):
        raise ValueError("Caption exceeds available field height")
    return {
        "widths_twips": measured["widths"],
        "ink_bounds_twips": measured["bounds"],
        "lines": len(lines),
        "available_width_twips": available,
    }


def patch_selected_tags(
    original,
    workspace,
    plan,
    recipes,
    *,
    expected_sha256=ORIGINAL_SHA256,
    replacement_fonts=None,
):
    if sha(original) != expected_sha256 or plan.get("game_sha256") != expected_sha256:
        raise ValueError("Original or plan SHA-256 mismatch")
    if plan.get("schema_version") != 1:
        raise ValueError("Unsupported local plan schema")
    inventory = extract_inventory(original, expected_sha256)
    errors = validate_workspace(workspace, inventory)
    if errors:
        raise ValueError("; ".join(errors))
    selected = plan.get("entry_ids")
    if (
        not isinstance(selected, list)
        or not selected
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("Selection must contain unique entry IDs")
    entries = {entry["id"]: entry for entry in workspace["entries"]}
    conflicts = {conflict["old"]["id"] for conflict in workspace["conflicts"]}
    prefix, tags = read_swf(original)
    source_fonts = {}
    for tag in tags:
        if tag.code == 75:
            font = parse_font(tag.payload)
            source_fonts[font["id"]] = font
    font_ids = plan.get("fonts", [])
    if not isinstance(font_ids, list) or len(set(font_ids)) != len(font_ids):
        raise ValueError("Invalid or duplicate font selection")
    if replacement_fonts is None:
        if set(recipes) != {str(identifier) for identifier in font_ids}:
            raise ValueError("Recipe fonts differ from selected fonts")
    elif set(replacement_fonts) != set(font_ids):
        raise ValueError("Independent font selection differs from selected fonts")
    fonts = dict(source_fonts)
    for identifier in font_ids:
        if identifier not in source_fonts:
            raise ValueError("Selected font not found")
        if replacement_fonts is None:
            fonts[identifier] = extend_font(
                source_fonts[identifier], recipes[str(identifier)]
            )
        else:
            font = replacement_fonts[identifier]
            if font["id"] in source_fonts or font["id"] == 6000:
                raise ValueError("Independent font ID collides with original resources")
            fonts[identifier] = parse_font(build_font(font))
    # Sorted insertion must not alter any static reference, even in nested sprites.
    for tag, _, _ in walk_tags(tags):
        if replacement_fonts is None and tag.code in (11, 33):
            text = parse_text(tag.payload, tag.code)
            active_font = None
            for record in text["records"]:
                active_font = record.get("font_id", active_font)
                if active_font not in font_ids:
                    continue
                for index, _ in record["entries"]:
                    if (
                        source_fonts[active_font]["codes"][index]
                        != fonts[active_font]["codes"][index]
                    ):
                        raise ValueError(
                            "Font extension would change an unrelated static glyph reference"
                        )
    groups, metrics = defaultdict(list), {}
    for identifier in selected:
        entry = entries.get(identifier)
        if (
            entry is None
            or identifier in conflicts
            or entry["translation"] is None
            or entry["classification"] != "confirmed"
            or entry["context_confidence"] != "confirmed"
            or entry["status"] not in ("draft", "verified")
        ):
            raise ValueError(
                f"Unconfirmed, unresolved or missing translation: {identifier}"
            )
        if identifier == f"static.{TITLE_ID}":
            if entry["translation"] != TRANSLATION:
                raise ValueError("The verified title recipe must be preserved")
            continue
        if Counter(
            c for c in entry["translation"] if ord(c) < 32 or ord(c) == 127
        ) != Counter(c for c in entry["source"] if ord(c) < 32 or ord(c) == 127):
            raise ValueError(f"Control sequence count differs: {identifier}")
        location = entry["location"]
        if (
            entry["category"] not in ("actionscript", "html")
            or "method_index" not in location
        ):
            raise ValueError("Only addressed ABC pushstring uses are supported")
        path = location["tag_path"]
        if (
            len(path) != 1
            or tags[path[0]].code != location["tag_code"]
            or location["tag_code"] not in (72, 82)
        ):
            raise ValueError("Unsupported ABC tag address")
        layout = plan.get("layouts", {}).get(identifier)
        if not isinstance(layout, dict) or layout.get("font_id") not in font_ids:
            raise ValueError("Each ABC translation needs a selected font and layout")
        alternate_ids = layout.get("alternate_font_ids", [])
        if (
            not isinstance(alternate_ids, list)
            or any(type(face) is not int for face in alternate_ids)
            or len(set(alternate_ids)) != len(alternate_ids)
            or any(face not in font_ids for face in alternate_ids)
            or layout["font_id"] in alternate_ids
        ):
            raise ValueError("Alternate runtime faces must be unique selected fonts")
        if entry.get("content_type") == "html_fragment" or entry["category"] == "html":
            raise ValueError("HTML rendering needs an explicit supported layout recipe")
        try:
            metrics[identifier] = check_layout(
                fonts[layout["font_id"]], entry["translation"], layout
            )
            if alternate_ids:
                metrics[identifier]["alternate_faces"] = {
                    str(face): check_layout(fonts[face], entry["translation"], layout)
                    for face in alternate_ids
                }
        except ValueError as error:
            raise ValueError(f"{identifier}: {error}") from error
        groups[path[0]].append(entry)
    changed = []
    for index, tag in enumerate(tags):
        payload = tag.payload
        if index in groups:
            start = payload.index(b"\0", 4) + 1 if tag.code == 82 else 0
            payload = payload[:start] + patch_pushstrings(
                payload[start:], groups[index]
            )
        elif tag.code == 75:
            identifier = int.from_bytes(payload[:2], "little")
            if replacement_fonts is None and identifier in font_ids:
                payload = build_font(fonts[identifier])
        elif tag.code == 73:
            identifier = int.from_bytes(payload[:2], "little")
            if replacement_fonts is None and identifier in font_ids:
                payload = extend_alignment_zones(
                    payload,
                    source_fonts[identifier]["codes"],
                    fonts[identifier]["codes"],
                )
        if payload != tag.payload:
            tags[index] = Tag(tag.code, payload)
            changed.append({"tag_index": index, "tag_code": tag.code})
    return (
        prefix,
        tags,
        {
            "applied_translations": len(selected),
            "changed_original_tags": changed,
            "layout_checks": metrics,
            "runtime_verified": False,
            "added_glyphs": (
                {str(i): len(fonts[i]["codes"]) - len(source_fonts[i]["codes"])
                 for i in font_ids}
                if replacement_fonts is None else {}
            ),
        },
    )


def build_localization(original, workspace, plan, recipes):
    if f"static.{TITLE_ID}" not in plan.get("entry_ids", []):
        raise ValueError("Local menu candidate must include the verified title")
    prefix, tags, report = patch_selected_tags(original, workspace, plan, recipes)
    _, _, position, title_font, title = patch_parts(original)
    tags[position : position + 1] = [
        Tag(75, build_font(title_font)),
        Tag(11, build_text(title)),
    ]
    candidate = write_swf(prefix, tags, original[3])
    # Reparse full candidate; preserves requested SWF header and every raw tag
    # outside the explicitly changed resources, including nested resources.
    after_prefix, after = read_swf(candidate)
    if prefix[8:] != after_prefix[8:] or len(after) != len(tags):
        raise ValueError("Candidate header or tag structure differs")
    if any(a.code != b.code or a.payload != b.payload for a, b in zip(tags, after)):
        raise ValueError("Candidate resource roundtrip differs")
    report.update(
        original_sha256=sha(original),
        patched_sha256=sha(candidate),
        original_bytes=len(original),
        patched_bytes=len(candidate),
        title_recipe_preserved=True,
        added_resources=[{"tag_code": 75, "character_id": title_font["id"]}],
        unconfirmed_reachability=plan.get("unconfirmed", []),
        status="READY FOR RUNTIME VERIFICATION",
    )
    report["changed_original_tags"].append(
        {"tag_index": position, "tag_code": 11, "character_id": TITLE_ID}
    )
    return candidate, report


def validate_localization(original, candidate, workspace, plan, recipes):
    expected, report = build_localization(original, workspace, plan, recipes)
    if candidate != expected:
        raise ValueError("Candidate differs from reproducible local recipe")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--font-recipes", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    candidate, report = build_localization(
        args.source.read_bytes(),
        load_catalog(args.workspace),
        load_catalog(args.plan),
        load_catalog(args.font_recipes),
    )
    output = args.output_dir
    if output.is_symlink():
        raise ValueError("Output directory must not be a symlink")
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive output protects originals, previous candidates and evidence.
    destination = output / "gc-cs-steam.ru.swf"
    if any(
        (output / name).exists() or (output / name).is_symlink()
        for name in ("gc-cs-steam.ru.swf", "static-validation.json")
    ):
        raise ValueError("Use a fresh output directory")
    with destination.open("xb") as handle:
        handle.write(candidate)
    if destination.read_bytes() != candidate:
        raise ValueError("Candidate reread differs")
    write_json(output / "static-validation.json", report)
    print(
        json.dumps({k: v for k, v in report.items() if k != "layout_checks"}, indent=2)
    )


if __name__ == "__main__":
    main()
