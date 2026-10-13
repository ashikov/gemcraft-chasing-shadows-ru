"""Private translation workspaces and deliberately allowlisted public projections."""
import argparse
import copy
import json
from pathlib import Path

from translation_catalog import load_catalog, validate_catalog

CLASSIFICATIONS = {"confirmed", "probable", "technical", "unknown"}
CONFIDENCES = {"confirmed", "inferred", "unknown"}
MANUAL_FIELDS = (
    "translation", "context", "context_confidence", "classification", "notes",
    "status", "mandatory_placeholders",
)
NUMERIC_LOCATION_FIELDS = {
    "character_id", "tag_code", "tag_index", "tag_offset", "abc_index",
    "string_index", "method_index", "instruction_offset", "body_index", "offset",
    "string_pool_index", "code_offset", "abc_byte_offset",
}
NUMERIC_LIST_FIELDS = {"font_ids", "sprite_path", "tag_path"}


def _validate_data(data, workspace=False):
    errors = validate_catalog(data, allow_missing=True)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return errors
    for index, entry in enumerate(data["entries"]):
        if not isinstance(entry, dict):
            continue
        label = f"entries[{index}]"
        if not isinstance(entry.get("classification"), str) or entry["classification"] not in CLASSIFICATIONS:
            errors.append(f"{label}: invalid classification")
        if not isinstance(entry.get("context_confidence"), str) or entry["context_confidence"] not in CONFIDENCES:
            errors.append(f"{label}: invalid context_confidence")
        if workspace:
            if entry.get("classification") == "technical":
                errors.append(f"{label}: technical entry is not a translation task")
            if not isinstance(entry.get("notes"), str):
                errors.append(f"{label}: notes must be a string")
            if not isinstance(entry.get("status"), str) or entry["status"] not in {
                "untranslated", "draft", "verified", "needs_review",
            }:
                errors.append(f"{label}: invalid status")
    return errors


def _require_valid(data, workspace=False):
    errors = validate_workspace(data) if workspace else _validate_data(data)
    if errors:
        raise ValueError("; ".join(errors))


def _same_origin(left, right):
    return all(left.get(field) == right.get(field) for field in
               ("id", "category", "source", "source_sha256", "location"))


def _allowlist_match(entry, allowed):
    return all(entry.get(field) == allowed.get(field) for field in
               ("id", "category", "source", "source_sha256")) and all(
        entry["location"].get(key) == value for key, value in allowed["location"].items()
    )


def validate_workspace(workspace, inventory=None):
    """Validate manual data, optionally binding every task to a fresh extraction."""
    errors = _validate_data(workspace, workspace=True)
    if not isinstance(workspace, dict):
        return errors
    conflicts = workspace.get("conflicts")
    if not isinstance(conflicts, list):
        errors.append("conflicts must be an array")
    else:
        for index, conflict in enumerate(conflicts):
            if not isinstance(conflict, dict) or not isinstance(conflict.get("old"), dict):
                errors.append(f"conflicts[{index}]: old snapshot must be an object")
                continue
            if conflict.get("new") is not None and not isinstance(conflict["new"], dict):
                errors.append(f"conflicts[{index}]: new snapshot must be an object or null")
            if conflict.get("reason") not in ("changed", "removed"):
                errors.append(f"conflicts[{index}]: invalid reason")
            for field in ("old", "new"):
                snapshot = conflict.get(field)
                if isinstance(snapshot, dict):
                    snapshot_errors = _validate_data({
                        "schema_version": 1,
                        "game_sha256": conflict.get(f"{field}_game_sha256"),
                        "entries": [snapshot],
                    }, workspace=field == "old")
                    errors.extend(f"conflicts[{index}].{field}: {error}"
                                  for error in snapshot_errors)
    if inventory is not None:
        errors.extend(_validate_data(inventory))
        if (not isinstance(inventory, dict) or not isinstance(inventory.get("entries"), list)
                or not isinstance(workspace.get("entries"), list)):
            return errors
        if workspace.get("game_sha256") != inventory.get("game_sha256"):
            errors.append("workspace game SHA differs from inventory")
        originals = {entry["id"]: entry for entry in inventory.get("entries", [])
                     if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
        for entry in workspace.get("entries", []):
            if (isinstance(entry, dict) and isinstance(entry.get("id"), str)
                    and not _same_origin(entry, originals.get(entry["id"], {}))):
                errors.append(f"inventory origin mismatch: {entry.get('id')}")
        expected = {key for key, entry in originals.items() if entry.get("classification") != "technical"}
        actual = {entry["id"] for entry in workspace.get("entries", [])
                  if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
        if actual != expected:
            errors.append("workspace tasks differ from inventory")
    return errors


def initialize_workspace(inventory, seed=None):
    """Create tasks without mutating extraction data; import only matching seed work."""
    _require_valid(inventory)
    result = {"schema_version": 1, "game_sha256": inventory["game_sha256"],
              "entries": [], "conflicts": []}
    for original in inventory["entries"]:
        if original["classification"] == "technical":
            continue
        entry = copy.deepcopy(original)
        entry.update(translation=None, notes="", status="untranslated")
        result["entries"].append(entry)
    if seed is not None:
        errors = validate_catalog(seed)
        if errors:
            raise ValueError("seed: " + "; ".join(errors))
        if seed["game_sha256"] != result["game_sha256"]:
            raise ValueError("seed game SHA mismatch")
        tasks = {entry["id"]: entry for entry in result["entries"]}
        for allowed in seed["entries"]:
            entry = tasks.get(allowed["id"])
            if entry is None or not _allowlist_match(entry, allowed):
                raise ValueError(f"seed origin mismatch: {allowed['id']}")
            entry.update(translation=allowed["translation"], context=allowed["context"],
                         mandatory_placeholders=copy.deepcopy(allowed["mandatory_placeholders"]),
                         status="verified", context_confidence="confirmed",
                         classification="confirmed")
    _require_valid(result, workspace=True)
    return result


def reconcile_workspace(old_workspace, new_inventory):
    """Return a new snapshot; changed or removed origins preserve full old work."""
    _require_valid(old_workspace, workspace=True)
    result = initialize_workspace(new_inventory)
    result["conflicts"] = copy.deepcopy(old_workspace["conflicts"])
    old_entries = {entry["id"]: entry for entry in old_workspace["entries"]}
    new_entries = {entry["id"]: entry for entry in new_inventory["entries"]}
    same_game = old_workspace["game_sha256"] == new_inventory["game_sha256"]
    for entry in result["entries"]:
        old = old_entries.get(entry["id"])
        if old is None:
            continue
        if same_game and _same_origin(old, entry):
            for field in MANUAL_FIELDS:
                entry[field] = copy.deepcopy(old[field])
        else:
            entry["status"] = "needs_review"
    for identifier, old in old_entries.items():
        new = new_entries.get(identifier)
        removed = new is None or new["classification"] == "technical"
        if removed or not same_game or not _same_origin(old, new):
            conflict = {"reason": "removed" if removed else "changed",
                        "old_game_sha256": old_workspace["game_sha256"],
                        "new_game_sha256": new_inventory["game_sha256"],
                        "old": copy.deepcopy(old), "new": copy.deepcopy(new)}
            if conflict not in result["conflicts"]:
                result["conflicts"].append(conflict)
    _require_valid(result, workspace=True)
    return result


def minimal_projection(workspace, allowlist):
    """Export only already-authorized verified translations, never bulk identifiers."""
    _require_valid(workspace, workspace=True)
    errors = validate_catalog(allowlist)
    if errors:
        raise ValueError("allowlist: " + "; ".join(errors))
    if workspace["game_sha256"] != allowlist["game_sha256"]:
        raise ValueError("allowlist game SHA mismatch")
    allowed_entries = {entry["id"]: entry for entry in allowlist["entries"]}
    unresolved_ids = {conflict["old"].get("id") for conflict in workspace["conflicts"]}
    result = {"schema_version": 1, "game_sha256": workspace["game_sha256"], "entries": []}
    for entry in workspace["entries"]:
        if entry["translation"] is None:
            continue
        allowed = allowed_entries.get(entry["id"])
        if (entry["classification"] not in {"confirmed", "probable"}
                or entry["status"] != "verified" or allowed is None
                or entry["id"] in unresolved_ids
                or not _allowlist_match(entry, allowed)
                or entry["translation"] != allowed["translation"]):
            raise ValueError(f"translation outside verified allowlist: {entry['id']}")
        location = {}
        for key, value in entry["location"].items():
            if key in NUMERIC_LOCATION_FIELDS and type(value) is int and value >= 0:
                location[key] = value
            elif key in NUMERIC_LIST_FIELDS and isinstance(value, list) and all(
                type(item) is int and item >= 0 for item in value
            ):
                location[key] = copy.deepcopy(value)
        if not location:
            raise ValueError(f"no safe numeric address: {entry['id']}")
        result["entries"].append({"id": entry["id"], "category": entry["category"],
                                  "location": location, "source_sha256": entry["source_sha256"],
                                  "translation": entry["translation"]})
    return result


def validate_minimal(projection, workspace, allowlist):
    """Require exact derivation: no source text, injected metadata or stale hashes."""
    try:
        expected = minimal_projection(workspace, allowlist)
    except ValueError as error:
        return [str(error)]
    if type(projection) is not dict or projection != expected:
        return ["minimal projection differs from verified allowlist derivation"]
    # Python equality treats booleans and numbers as equal; JSON types must match too.
    if json.dumps(projection, sort_keys=True) != json.dumps(expected, sort_keys=True):
        return ["minimal projection types differ from verified allowlist derivation"]
    return []


def write_json(path, data):
    """Exclusive UTF-8 output; byte-stable serialization without timestamps."""
    serialized = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(serialized)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("inventory", type=Path)
    init.add_argument("--seed", type=Path)
    init.add_argument("--output", type=Path, required=True)
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("workspace", type=Path)
    reconcile.add_argument("inventory", type=Path)
    reconcile.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("workspace", type=Path)
    validate.add_argument("--inventory", type=Path)
    minimal = commands.add_parser("minimal")
    minimal.add_argument("workspace", type=Path)
    minimal.add_argument("--allowlist", type=Path, required=True)
    minimal.add_argument("--output", type=Path, required=True)
    check_minimal = commands.add_parser("validate-minimal")
    check_minimal.add_argument("projection", type=Path)
    check_minimal.add_argument("--workspace", type=Path, required=True)
    check_minimal.add_argument("--allowlist", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize_workspace(load_catalog(args.inventory),
                                          load_catalog(args.seed) if args.seed else None)
        elif args.command == "reconcile":
            result = reconcile_workspace(load_catalog(args.workspace), load_catalog(args.inventory))
        elif args.command == "minimal":
            result = minimal_projection(load_catalog(args.workspace), load_catalog(args.allowlist))
        elif args.command == "validate-minimal":
            errors = validate_minimal(load_catalog(args.projection), load_catalog(args.workspace),
                                      load_catalog(args.allowlist))
            if errors:
                raise ValueError("; ".join(errors))
            print("Minimal projection valid")
            return 0
        else:
            errors = validate_workspace(load_catalog(args.workspace),
                                        load_catalog(args.inventory) if args.inventory else None)
            if errors:
                raise ValueError("; ".join(errors))
            print("Workspace valid")
            return 0
        write_json(args.output, result)
    except FileExistsError:
        parser.exit(1, "Workspace error: output already exists; choose a new snapshot path\n")
    except OSError:
        parser.exit(1, "Workspace error: cannot read input or create output\n")
    except ValueError:
        # Detailed validator messages can contain private placeholders or identifiers.
        parser.exit(1, "Workspace error: invalid data, origin conflict or allowlist mismatch; "
                    "inspect validation details locally with the Python API\n")
    print(f"Created {args.output}")
    return 0


if __name__ == "__main__":
    main()
