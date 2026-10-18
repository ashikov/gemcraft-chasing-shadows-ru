"""Private, source-free manual translation data bound to the original inventory."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

from install_patch import ORIGINAL_SHA256
from localization_workspace import (
    CLASSIFICATIONS, CONFIDENCES, MANUAL_FIELDS, NUMERIC_LIST_FIELDS,
    NUMERIC_LOCATION_FIELDS, initialize_workspace, validate_workspace, write_json,
)
from translation_catalog import SHA256, _html_error, load_catalog

FORMAT = "gemcraft-private-translations"
STATUSES = {"untranslated", "draft", "verified", "needs_review"}
ENTRY_FIELDS = {"id", "category", "source_sha256", "location", "manual"}
SOURCE_REFERENCE_FIELDS = {"context", "notes"}
SOURCE_REFERENCE_MIN_LENGTH = 24


def _valid_source_reference(value):
    if (not isinstance(value, dict) or set(value) != {"source_parts"}
            or not isinstance(value["source_parts"], list)):
        return False
    references = 0
    for part in value["source_parts"]:
        if isinstance(part, str):
            continue
        if (not isinstance(part, dict) or set(part) != {"id", "source_sha256"}
                or not isinstance(part["id"], str) or not part["id"].strip()
                or not isinstance(part["source_sha256"], str)
                or not SHA256.fullmatch(part["source_sha256"])):
            return False
        references += 1
    return references > 0


def _source_encoder(inventory):
    references = {}
    for entry in sorted(inventory["entries"], key=lambda item: item["id"]):
        source = entry["source"]
        textual_label = (len(source) >= 3 and entry["classification"] != "technical"
                         and any(char.isascii() and char.isalpha() for char in source))
        if len(source) >= SOURCE_REFERENCE_MIN_LENGTH or textual_label:
            references.setdefault(entry["source"], {
                "id": entry["id"], "source_sha256": entry["source_sha256"],
            })
    sources = sorted(references, key=lambda source: (-len(source), source))
    pattern = re.compile("|".join(re.escape(source) for source in sources)) if sources else None
    return pattern, references


def _encode_manual(field, value, pattern, references):
    if field not in SOURCE_REFERENCE_FIELDS or pattern is None:
        return copy.deepcopy(value)
    parts, offset = [], 0
    for match in pattern.finditer(value):
        parts.extend((value[offset:match.start()], copy.deepcopy(references[match.group()])))
        offset = match.end()
    if not parts:
        return value
    parts.append(value[offset:])
    return {"source_parts": parts}


def _numeric_location(location):
    return {key: copy.deepcopy(value) for key, value in location.items()
            if key in NUMERIC_LOCATION_FIELDS or key in NUMERIC_LIST_FIELDS}


def _address(location):
    """Identify the binding site independently of ancillary numeric guards."""
    path = tuple(location.get("tag_path", []))
    if "character_id" in location:
        return ("text", path) if path else ("character", location["character_id"])
    if path and "method_index" in location and "code_offset" in location:
        return ("instruction", path, location["method_index"], location["code_offset"])
    if path and "string_pool_index" in location:
        return ("pool", path, location["string_pool_index"])
    raise ValueError("location has no complete binding address")


def validate_catalog_data(catalog):
    """Validate the closed private schema without requiring proprietary inputs."""
    errors = []
    try:
        json.dumps(catalog, ensure_ascii=False).encode("utf-8")
    except (UnicodeError, TypeError, ValueError):
        errors.append("catalog contains invalid Unicode or non-JSON data")
    if not isinstance(catalog, dict):
        return errors + ["catalog must be an object"]
    if set(catalog) != {"schema_version", "format", "game_sha256", "entries"}:
        errors.append("catalog has missing or unknown fields")
    if type(catalog.get("schema_version")) is not int or catalog["schema_version"] != 1:
        errors.append("schema_version must be 1")
    if catalog.get("format") != FORMAT:
        errors.append("unsupported private catalog format")
    if catalog.get("game_sha256") != ORIGINAL_SHA256:
        errors.append("catalog game SHA is not the supported original")
    if not isinstance(catalog.get("entries"), list):
        return errors + ["entries must be an array"]
    ids, addresses = set(), set()
    for index, entry in enumerate(catalog["entries"]):
        label = f"entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label}: entry must be an object")
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append(f"{label}: invalid id")
        else:
            label = identifier
            if identifier in ids:
                errors.append(f"{label}: duplicate id")
            ids.add(identifier)
        if set(entry) != ENTRY_FIELDS:
            errors.append(f"{label}: missing or unknown entry fields")
        if entry.get("category") not in ("static", "actionscript", "html"):
            errors.append(f"{label}: unsupported category")
        digest = entry.get("source_sha256")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            errors.append(f"{label}: invalid source SHA")
        location = entry.get("location")
        valid_location = isinstance(location, dict) and bool(location)
        if valid_location:
            for key, value in location.items():
                if key in NUMERIC_LOCATION_FIELDS:
                    valid = type(value) is int and value >= 0
                elif key in NUMERIC_LIST_FIELDS:
                    valid = isinstance(value, list) and all(
                        type(item) is int and item >= 0 for item in value)
                else:
                    valid = False
                if not valid:
                    valid_location = False
        if not valid_location:
            errors.append(f"{label}: location must contain only safe numeric address fields")
        else:
            try:
                address = _address(location)
                if address in addresses:
                    errors.append(f"{label}: duplicate binding address")
                addresses.add(address)
            except ValueError as error:
                errors.append(f"{label}: {error}")
        manual = entry.get("manual")
        if not isinstance(manual, dict) or not manual:
            errors.append(f"{label}: manual must contain at least one override")
            continue
        if set(manual) - set(MANUAL_FIELDS):
            errors.append(f"{label}: unknown manual fields")
        for field, value in manual.items():
            if field == "translation":
                valid = value is None or isinstance(value, str)
            elif field in ("context", "notes"):
                valid = isinstance(value, str) or _valid_source_reference(value)
            elif field == "classification":
                valid = isinstance(value, str) and value in CLASSIFICATIONS - {"technical"}
            elif field == "context_confidence":
                valid = isinstance(value, str) and value in CONFIDENCES
            elif field == "status":
                valid = isinstance(value, str) and value in STATUSES
            elif field == "mandatory_placeholders":
                valid = (isinstance(value, list)
                         and all(isinstance(item, str) and item for item in value))
                if valid:
                    valid = len(value) == len(set(value))
            else:
                continue
            if not valid:
                errors.append(f"{label}: invalid manual {field}")
        translation = manual.get("translation")
        if entry.get("category") == "html" and isinstance(translation, str):
            issue = _html_error(translation)
            if issue:
                errors.append(f"{label}: translation HTML: {issue}")
    return errors


def _require_catalog(catalog):
    errors = validate_catalog_data(catalog)
    if errors:
        raise ValueError("; ".join(errors))


def catalog_digest(catalog):
    """Hash the exact deterministic UTF-8 representation used by write_json."""
    _require_catalog(catalog)
    serialized = json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _baseline(inventory):
    if not isinstance(inventory, dict) or inventory.get("game_sha256") != ORIGINAL_SHA256:
        raise ValueError("inventory game SHA is not the supported original")
    baseline = initialize_workspace(inventory)
    addresses = {}
    for entry in baseline["entries"]:
        try:
            address = _address(_numeric_location(entry["location"]))
        except ValueError as error:
            raise ValueError(f"{entry['id']}: {error}") from error
        if address in addresses:
            raise ValueError(f"duplicate inventory binding address: {addresses[address]}, {entry['id']}")
        addresses[address] = entry["id"]
    return baseline


def _same_json(left, right):
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def restore_workspace(catalog, inventory):
    """Overlay only manual data onto a freshly verified original inventory."""
    _require_catalog(catalog)
    workspace = _baseline(inventory)
    originals = {entry["id"]: entry for entry in workspace["entries"]}
    reference_sources = {entry["id"]: entry for entry in inventory["entries"]}
    for saved in catalog["entries"]:
        identifier = saved["id"]
        entry = originals.get(identifier)
        if entry is None:
            raise ValueError(f"missing inventory origin: {identifier}")
        expected = {"id": identifier, "category": entry["category"],
                    "source_sha256": entry["source_sha256"],
                    "location": _numeric_location(entry["location"])}
        actual = {key: saved[key] for key in expected}
        if not _same_json(actual, expected):
            raise ValueError(f"inventory origin mismatch: {identifier}")
        manual = copy.deepcopy(saved["manual"])
        for field in SOURCE_REFERENCE_FIELDS & manual.keys():
            if isinstance(manual[field], dict):
                parts = []
                for part in manual[field]["source_parts"]:
                    if isinstance(part, str):
                        parts.append(part)
                        continue
                    source_entry = reference_sources.get(part["id"])
                    if source_entry is None:
                        raise ValueError(f"{identifier}: missing source reference {part['id']}")
                    if source_entry["source_sha256"] != part["source_sha256"]:
                        raise ValueError(f"{identifier}: source reference SHA mismatch: {part['id']}")
                    parts.append(source_entry["source"])
                manual[field] = "".join(parts)
        entry.update(manual)
    errors = validate_workspace(workspace, inventory)
    if errors:
        raise ValueError("; ".join(errors))
    return workspace


def export_catalog(workspace, inventory, previous=None, allow_removals=(),
                   expected_previous_sha256=None):
    """Export sparse overrides; refuse implicit loss relative to a prior snapshot."""
    if previous is not None:
        if expected_previous_sha256 is None:
            raise ValueError("previous catalog baseline receipt is required")
        if expected_previous_sha256 != catalog_digest(previous):
            raise ValueError("previous catalog baseline changed; reconcile workspace before export")
    elif expected_previous_sha256 is not None:
        raise ValueError("baseline receipt requires a previous catalog")
    baseline = _baseline(inventory)
    errors = validate_workspace(workspace, inventory)
    if errors:
        raise ValueError("; ".join(errors))
    if workspace["conflicts"]:
        raise ValueError("unresolved workspace conflicts must be resolved before export")
    if set(workspace) != set(baseline):
        raise ValueError("workspace contains unknown machine fields")
    originals = {entry["id"]: entry for entry in baseline["entries"]}
    pattern, references = _source_encoder(inventory)
    result = {"schema_version": 1, "format": FORMAT,
              "game_sha256": ORIGINAL_SHA256, "entries": []}
    for entry in sorted(workspace["entries"], key=lambda item: item["id"]):
        original = originals[entry["id"]]
        immutable = {key: value for key, value in entry.items() if key not in MANUAL_FIELDS}
        expected = {key: value for key, value in original.items() if key not in MANUAL_FIELDS}
        if not _same_json(immutable, expected):
            raise ValueError(f"immutable machine data changed: {entry['id']}")
        manual = {field: _encode_manual(field, entry[field], pattern, references)
                  for field in MANUAL_FIELDS
                  if not _same_json(entry[field], original[field])}
        if manual:
            result["entries"].append({
                "id": entry["id"], "category": entry["category"],
                "source_sha256": entry["source_sha256"],
                "location": _numeric_location(entry["location"]), "manual": manual,
            })
    _require_catalog(result)
    if previous is not None:
        old_workspace = restore_workspace(previous, inventory)
        new_by_id = {entry["id"]: entry for entry in workspace["entries"]}
        new_manual = {entry["id"]: entry["manual"] for entry in result["entries"]}
        old_by_id = {entry["id"]: entry for entry in old_workspace["entries"]}
        allowed = set(allow_removals)
        previous_manual = {entry["id"]: entry["manual"] for entry in previous["entries"]}
        previous_ids = set(previous_manual)
        if allowed - previous_ids:
            raise ValueError("allow_removals contains unknown previous IDs: "
                             + ", ".join(sorted(allowed - previous_ids)))
        losses = []
        for identifier in sorted(previous_ids - allowed):
            old_translation = old_by_id[identifier]["translation"]
            new_translation = new_by_id[identifier]["translation"]
            if (previous_manual[identifier].keys() - new_manual.get(identifier, {}).keys()
                    or (old_translation is not None and new_translation is None)
                    or (old_translation not in (None, "") and new_translation == "")):
                losses.append(identifier)
        if losses:
            raise ValueError("manual data removal requires explicit IDs: " + ", ".join(losses))
    return result


def check_sync(workspace, inventory, catalog):
    """Report exact differing IDs without writing either representation."""
    try:
        restored = restore_workspace(catalog, inventory)
    except ValueError as error:
        return [str(error)]
    expected = {entry["id"]: entry for entry in restored["entries"]}
    actual = {}
    if isinstance(workspace, dict) and isinstance(workspace.get("entries"), list):
        actual = {entry["id"]: entry for entry in workspace["entries"]
                  if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
    errors = [f"workspace/catalog mismatch: {identifier}"
              for identifier in sorted(set(expected) | set(actual))
              if not _same_json(actual.get(identifier), expected.get(identifier))]
    try:
        export_catalog(workspace, inventory)
    except ValueError as error:
        errors.append(str(error))
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "restore", "check-sync", "validate"):
        command = commands.add_parser(name)
        if name != "validate":
            command.add_argument("--inventory", type=Path, required=True)
        if name in ("export", "check-sync"):
            command.add_argument("--workspace", type=Path, required=True)
        if name != "export":
            command.add_argument("--catalog", type=Path, required=True)
        if name in ("export", "restore"):
            command.add_argument("--output", type=Path, required=True)
        if name == "export":
            command.add_argument("--previous", type=Path)
            command.add_argument("--base-receipt", type=Path)
            command.add_argument("--allow-removal", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            errors = validate_catalog_data(load_catalog(args.catalog))
        elif args.command == "check-sync":
            errors = check_sync(load_catalog(args.workspace), load_catalog(args.inventory),
                                load_catalog(args.catalog))
        else:
            inventory = load_catalog(args.inventory)
            if args.command == "restore":
                result = restore_workspace(load_catalog(args.catalog), inventory)
            else:
                baseline_digest = None
                if bool(args.previous) != bool(args.base_receipt):
                    raise ValueError("--previous and --base-receipt must be supplied together")
                if args.base_receipt:
                    receipt = load_catalog(args.base_receipt)
                    if (not isinstance(receipt, dict) or set(receipt) != {"catalog_sha256"}
                            or not isinstance(receipt["catalog_sha256"], str)
                            or not SHA256.fullmatch(receipt["catalog_sha256"])):
                        raise ValueError("invalid baseline receipt")
                    baseline_digest = receipt["catalog_sha256"]
                result = export_catalog(load_catalog(args.workspace), inventory,
                                        load_catalog(args.previous) if args.previous else None,
                                        args.allow_removal, baseline_digest)
            write_json(args.output, result)
            print(f"Created {args.output}")
            return 0
        if errors:
            raise ValueError("; ".join(errors))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Private catalog error: {error}\n")
    print("Private catalog valid" if args.command == "validate" else "Workspace/catalog synchronized")
    return 0


if __name__ == "__main__":
    main()
