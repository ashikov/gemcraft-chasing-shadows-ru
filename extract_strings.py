"""Read-only selected static extraction or a full local structural inventory."""
import argparse
import hashlib
import json
from pathlib import Path

from swf_geometry import parse_font, parse_text, read_swf
from translation_catalog import SHA256, source_hash, validate_catalog


def extract_static(raw, character_ids, expected_sha256):
    digest = hashlib.sha256(raw).hexdigest()
    if not isinstance(expected_sha256, str) or not SHA256.fullmatch(expected_sha256) or digest != expected_sha256:
        raise ValueError("Input SHA-256 does not match the explicitly selected original SWF")
    if not character_ids:
        raise ValueError("Select at least one static character ID")
    if len(set(character_ids)) != len(character_ids):
        raise ValueError("Selection contains duplicate static IDs")
    if any(type(identifier) is not int or not 0 <= identifier <= 65535 for identifier in character_ids):
        raise ValueError("Static IDs must be unsigned 16-bit integers")
    _, tags = read_swf(raw)
    fonts = {}
    for tag in tags:
        if tag.code in (48, 75):
            font = parse_font(tag.payload)
            if font["id"] in fonts:
                raise ValueError(f"Duplicate font ID {font['id']}")
            fonts[font["id"]] = font["codes"]
    selected = set(character_ids)
    entries = {}
    for tag in tags:
        if tag.code not in (11, 33) or int.from_bytes(tag.payload[:2], "little") not in selected:
            continue
        text = parse_text(tag.payload, tag.code)
        identifier = text["id"]
        if identifier in entries:
            raise ValueError(f"Duplicate static character ID {identifier}")
        font_id, chars, font_ids = None, [], set()
        for record in text["records"]:
            font_id = record.get("font_id", font_id)
            if record["entries"] and font_id not in fonts:
                raise ValueError(f"Static ID {identifier}: missing supported font {font_id}")
            for glyph_index, _ in record["entries"]:
                if glyph_index >= len(fonts[font_id]):
                    raise ValueError(f"Static ID {identifier}: invalid glyph index {glyph_index}")
                chars.append(chr(fonts[font_id][glyph_index]))
                font_ids.add(font_id)
        source = "".join(chars)
        entries[identifier] = {
            "id": f"static.{identifier}", "category": "static",
            "location": {"character_id": identifier, "tag_code": tag.code,
                         "font_ids": sorted(font_ids)},
            "source": source, "source_sha256": source_hash(source),
            "context": "Selected static SWF text; inspect display placement before translation",
            "translation": None, "mandatory_placeholders": [],
        }
    missing = selected - entries.keys()
    if missing:
        raise ValueError(f"Selected static IDs not found at top level: {sorted(missing)}")
    result = {"schema_version": 1, "game_sha256": digest,
              "entries": [entries[identifier] for identifier in character_ids]}
    errors = validate_catalog(result, allow_missing=True)
    if errors:
        raise ValueError("\n".join(errors))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--original-sha256", required=True)
    parser.add_argument("--static-id", type=int, action="append")
    parser.add_argument("--inventory", action="store_true",
                        help="Inventory recursive static/edit text and ABC into local JSON")
    parser.add_argument("--report", type=Path, help="New local aggregate report (inventory only)")
    parser.add_argument("--output", type=Path, required=True,
                        help="Local ignored JSON file; must not already exist")
    args = parser.parse_args(argv)
    if args.inventory and args.static_id or not args.inventory and not args.static_id:
        parser.error("Select either --inventory or at least one --static-id")
    if args.report and not args.inventory:
        parser.error("--report requires --inventory")
    try:
        if args.inventory:
            from inventory_strings import extract_inventory, inventory_report
            from install_patch import ORIGINAL_SHA256
            if args.original_sha256 != ORIGINAL_SHA256:
                raise ValueError("Inventory CLI requires the supported original SHA-256")
            catalog = extract_inventory(args.input.read_bytes(), args.original_sha256)
        else:
            catalog = extract_static(args.input.read_bytes(), args.static_id, args.original_sha256)
        paths = [args.output] + ([args.report] if args.report else [])
        if len({path.resolve() for path in paths}) != len(paths):
            raise ValueError("Output and report must be distinct files")
        if any(path.exists() or path.is_symlink() for path in paths):
            raise ValueError("Output/report already exists; choose new files")
        # Exclusive creation prevents overwriting the source, a catalog, or a local backup.
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(catalog, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        if args.report:
            with args.report.open("x", encoding="utf-8") as handle:
                json.dump(inventory_report(catalog), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Extraction error: {error}\n")
    print(f"Extracted {len(catalog['entries'])} entries to {args.output}")


if __name__ == "__main__":
    main()
