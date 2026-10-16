"""Build a local menu candidate from guarded workspace sites and private recipes.

No translations or game-derived outlines are stored in this tool. Only selected
pushstring operands, extended font tables and their alignment tables may change.
The existing title experiment remains the base recipe, including its exact font.
"""

import argparse
import copy
from collections import Counter, defaultdict
import json
import math
import re
from html import unescape
from pathlib import Path

from abc_patch import patch_pushstrings
from build_prototype import TRANSLATION, TITLE_ID, patch_parts, sha
from install_patch import ORIGINAL_SHA256
from inventory_strings import extract_inventory, parse_edit_text, walk_tags
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
    decode_matrix,
    decode_rect,
    signed_bits,
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


def _glyph_run(font, kerning, line, height):
    """Encode one line with the target font, keeping pair kerning inside the line."""
    return [(font['codes'].index(ord(char)), round(
        (font['advances'][font['codes'].index(ord(char))]
         + kerning.get((ord(char), ord(line[i + 1]) if i + 1 < len(line) else 0), 0))
        * height / 20480)) for i, char in enumerate(line)]


def _record_lines(entry, records, layout):
    """Resolve and guard the explicit per-record lines of a multi-record static.

    Lines joined in order must reproduce the workspace translation exactly,
    optionally consuming one space at each boundary. This keeps the workspace
    the single source of truth while the plan owns only the line split.
    """
    if len(records) == 1:
        if layout.get('lines') not in (None, [entry['translation']]):
            raise ValueError('Single-record static lines must equal the translation')
        return [entry['translation']]
    lines = layout.get('lines')
    if (not isinstance(lines, list) or len(lines) != len(records)
            or any(type(line) is not str or not line or line != line.strip()
                   or '\n' in line for line in lines)):
        raise ValueError('Static multi-record text needs one explicit trimmed line per record')
    position = 0
    translation = entry['translation']
    for index, line in enumerate(lines):
        if not translation.startswith(line, position):
            raise ValueError('Static record lines do not reproduce the selected translation')
        position += len(line)
        if index + 1 < len(lines) and translation[position:position + 1] == ' ':
            position += 1
    if position != len(translation):
        raise ValueError('Static record lines do not reproduce the selected translation')
    return lines


def _multiline_static(tag, entry, font, layout, source_fonts):
    """Reflow a guarded multi-record static, one explicit line per record.

    Record count, order, colours and heights are retained; a line may
    not pass the original right ink edge. The tag matrix must be a pure
    translation so that bounds stay exact. `center` keeps each record's
    own ink centre by recomputing its x offset; otherwise x is retained.
    """
    from build_prototype import ink_bounds
    text = parse_text(tag.payload, tag.code)
    records = text['records']
    matrix, _ = decode_matrix(text['matrix_raw'])
    if matrix['scale'] != [65536, 65536] or matrix['rotate'] != [0, 0]:
        raise ValueError('Static text recipe supports a translation-only matrix')
    tx, ty = matrix['translate']
    lines = _record_lines(entry, records, layout)
    center = bool(layout.get('center'))
    if layout.get('wrap') or any('\n' in line for line in lines):
        raise ValueError('Static text recipe must retain its single line height')
    if len(entry.get('text_records', [])) != len(records):
        raise ValueError('Static inventory records differ at selected address')
    original_bounds = text['bounds']
    right_edge = original_bounds[1] - tx
    text = copy.deepcopy(text)
    kerning = {(a, b): value for a, b, value in font['kerning']}
    active_font = active_height = None
    metrics = {'widths_twips': [], 'ink_bounds_twips': [], 'lines': len(lines),
               'available_width_twips': []}
    bounds = []
    for row, record, line in zip(text['records'], entry['text_records'], lines):
        previous_font = active_font
        active_font = row.get('font_id', active_font)
        active_height = row.get('height', active_height)
        if previous_font is not None and active_font != previous_font:
            raise ValueError('Static recipe supports a single original font per text')
        source = source_fonts.get(active_font)
        if not row['entries'] or source is None or ''.join(
                chr(source['codes'][i]) for i, _ in row['entries']) != record['source']:
            raise ValueError('Static source differs at selected address')
        if active_height != layout['height']:
            raise ValueError('Static text recipe must retain its single line height')
        budget = layout['max_width'] if center else min(
            layout['max_width'], right_edge - row.get('x', 0))
        check = check_layout(font, line, {'height': active_height, 'max_width': budget})
        metrics['widths_twips'].extend(check['widths_twips'])
        metrics['available_width_twips'].append(budget)
        old_center = None
        if center:
            if 'x' not in row:
                raise ValueError('Centered static record needs an explicit x offset')
            old_ink = ink_bounds(source, row['entries'], active_height, row.get('x', 0), 0)
            old_center = (old_ink[0] + old_ink[1]) / 2
        row['entries'] = _glyph_run(font, kerning, line, active_height)
        if 'font_id' in row:
            row['font_id'] = font['id']
        if center:
            ink = ink_bounds(font, row['entries'], active_height)
            row['x'] = round(old_center - (ink[0] + ink[1]) / 2)
            if not -32768 <= row['x'] <= 32767:
                raise ValueError('Centered static x offset exceeds its field')
        bounds.append(ink_bounds(font, row['entries'], active_height,
                                 row.get('x', 0), row.get('y', 0)))
        metrics['ink_bounds_twips'].append(bounds[-1])
    text['bounds'] = [min(b[0] for b in bounds) + tx, max(b[1] for b in bounds) + tx,
                      min(b[2] for b in bounds) + ty, max(b[3] for b in bounds) + ty]
    text['gb'] = (len(font['codes']) - 1).bit_length()
    text['ab'] = signed_bits([advance for row in text['records'] for _, advance in row['entries']])
    return Tag(tag.code, build_text(text, tag.code)), metrics


def _display_text(tag, entry, font, layout, source_fonts):
    """Patch one guarded display resource, retaining its existing style/position."""
    if tag.code in (11, 33):
        text = parse_text(tag.payload, tag.code)
        if len(text['records']) != 1 or layout.get('center'):
            return _multiline_static(tag, entry, font, layout, source_fonts)
        row = text['records'][0]
        source = source_fonts.get(row.get('font_id'))
        if source is None or ''.join(chr(source['codes'][i]) for i, _ in row['entries']) != entry['source']:
            raise ValueError('Static source differs at selected address')
        metrics = check_layout(font, entry['translation'], layout)
        if layout.get('wrap') or layout['height'] != row['height'] or '\n' in entry['translation']:
            raise ValueError('Static text recipe must retain its single line height')
        text = copy.deepcopy(text)
        row = text['records'][0]
        kerning = {(a, b): value for a, b, value in font['kerning']}
        translation = entry['translation']
        row['entries'] = _glyph_run(font, kerning, translation, row['height'])
        row['font_id'] = font['id']
        from build_prototype import ink_bounds
        text['bounds'] = ink_bounds(font, row['entries'], row['height'], row.get('x', 0), row.get('y', 0))
        text['gb'] = (len(font['codes']) - 1).bit_length()
        text['ab'] = signed_bits([advance for _, advance in row['entries']])
        return Tag(tag.code, build_text(text, tag.code)), metrics
    identifier, source, formatting = parse_edit_text(tag.payload)
    if layout.get('lines') is not None or layout.get('center'):
        raise ValueError('Editable text does not support static-only layout keys')
    if (identifier != entry['location']['character_id'] or source != entry['source']
            or not formatting['has_font'] or formatting['has_font_class']
            or not formatting['has_text'] or not formatting['use_outlines']):
        raise ValueError('Unsupported editable text binding')
    translation = entry['translation']
    if bool(layout.get('wrap')) != formatting['word_wrap']:
        raise ValueError('Editable word wrap differs from layout')
    if formatting['html']:
        # Only the existing one-paragraph Flash HTML recipe is supported. Tags,
        # attributes and sizes are exact guards; only text and local face change.
        pattern = r'<p(?: align="(?:left|center|right)")?><font face="[^"]+" size="\d+"(?: color="#[0-9a-fA-F]{6}")?(?: letterSpacing="[0-9.]+")?(?: kerning="1")?>(?:<b>)?[^<>]*(?:</b>)?</font></p>'
        if not re.fullmatch(pattern, source) or not re.fullmatch(pattern, translation):
            raise ValueError('Unsupported HTML recipe')
        if re.findall(r'<[^>]+>', source) != re.findall(r'<[^>]+>', translation):
            raise ValueError('HTML structure or style differs')
        visible = unescape(re.sub(r'<[^>]+>', '', translation))
        size = int(re.search(r' size="(\d+)"', source)[1]) * 20
        if size != layout['height']:
            raise ValueError('HTML font size differs from layout')
        translation = re.sub(r' face="[^"]+"', lambda _: ' face="' + font['name'].decode('utf-8') + '"', translation, count=1)
    else:
        visible = translation
    if layout['height'] != formatting['font_height']:
        raise ValueError('Editable font height differs from layout')
    metrics = check_layout(font, visible, layout)
    _, rect_bytes = decode_rect(tag.payload[2:])
    font_offset = 2 + rect_bytes + 2
    text_bytes = source.encode('utf-8') + b'\0'
    if not tag.payload.endswith(text_bytes):
        raise ValueError('Editable source byte guard failed')
    payload = bytearray(tag.payload[:-len(text_bytes)])
    payload[font_offset:font_offset + 2] = font['id'].to_bytes(2, 'little')
    payload.extend(translation.encode('utf-8') + b'\0')
    return Tag(37, bytes(payload)), metrics


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
    groups, metrics, display_changes = defaultdict(list), {}, {}
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
        if location['tag_code'] in (11, 33, 37):
            path = location['tag_path']
            if replacement_fonts is None or len(path) != 1 or tags[path[0]].code != location['tag_code']:
                raise ValueError('Unsupported display text address or font recipe')
            layout = plan.get('layouts', {}).get(identifier)
            if not isinstance(layout, dict) or layout.get('font_id') not in font_ids:
                raise ValueError('Display text needs a selected font and layout')
            if path[0] in display_changes:
                raise ValueError('Duplicate display text address')
            try:
                display_changes[path[0]], metrics[identifier] = _display_text(
                    tags[path[0]], entry, fonts[layout['font_id']], layout, source_fonts)
            except ValueError as error:
                raise ValueError(f'{identifier}: {error}') from error
            continue
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
        if index in display_changes:
            payload = display_changes[index].payload
        elif index in groups:
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
