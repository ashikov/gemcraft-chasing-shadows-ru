"""Small, version-bound JSON translation catalog validator (stdlib only)."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re

SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def source_hash(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_catalog(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


class _HTMLBalance(HTMLParser):
    """Check nesting, not the full Flash HTML dialect or renderer behavior."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in {"br", "img", "hr", "input", "meta", "link", "wbr"}:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise ValueError(f"unbalanced closing tag {tag}")

    def handle_startendtag(self, tag, attrs):
        pass


def _html_error(value):
    parser = _HTMLBalance()
    try:
        parser.feed(value)
        parser.close()
        if parser.stack:
            return "unclosed tags"
    except ValueError as error:
        return str(error)
    return None


def _unicode_errors(value, path="catalog"):
    errors = []
    if isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            errors.append(f"{path}: Unicode surrogate is not a scalar value")
    elif isinstance(value, dict):
        for key, item in value.items():
            errors.extend(_unicode_errors(key, path))
            errors.extend(_unicode_errors(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_unicode_errors(item, f"{path}[{index}]"))
    return errors


def validate_catalog(catalog, allow_missing=False):
    errors = _unicode_errors(catalog)
    if not isinstance(catalog, dict):
        return errors + ["catalog must be an object"]
    if type(catalog.get("schema_version")) is not int or catalog["schema_version"] != 1:
        errors.append("schema_version must be 1")
    if not isinstance(catalog.get("game_sha256"), str) or not SHA256.fullmatch(catalog["game_sha256"]):
        errors.append("game_sha256 must be a lowercase SHA-256")
    if not isinstance(catalog.get("entries"), list):
        return errors + ["entries must be an array"]
    ids = set()
    for index, entry in enumerate(catalog["entries"]):
        label = f"entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label}: must be an object")
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append(f"{label}: id must be a nonempty string")
        elif identifier in ids:
            errors.append(f"{label}: duplicate id {identifier}")
        else:
            ids.add(identifier)
        if entry.get("category") not in ("static", "actionscript", "html"):
            errors.append(f"{label}: unsupported category")
        if not isinstance(entry.get("location"), dict) or not entry["location"]:
            errors.append(f"{label}: location must be a nonempty object")
        if not isinstance(entry.get("context"), str):
            errors.append(f"{label}: context must be a string")
        source = entry.get("source")
        translation = entry.get("translation")
        if not isinstance(source, str):
            errors.append(f"{label}: source must be a string")
        elif not any(0xD800 <= ord(c) <= 0xDFFF for c in source):
            if entry.get("source_sha256") != source_hash(source):
                errors.append(f"{label}: source hash mismatch")
        if "translation" not in entry or (translation is not None and not isinstance(translation, str)):
            errors.append(f"{label}: translation must be a string or null")
        elif translation is None and not allow_missing:
            errors.append(f"{label}: missing translation")
        placeholders = entry.get("mandatory_placeholders")
        valid_placeholders = (
            isinstance(placeholders, list)
            and all(isinstance(p, str) and p for p in placeholders)
            and len(placeholders) == len(set(placeholders))
        ) if isinstance(placeholders, list) and all(isinstance(p, str) for p in placeholders) else False
        if not valid_placeholders:
            errors.append(f"{label}: mandatory_placeholders must contain unique nonempty strings")
        elif isinstance(source, str):
            for token in placeholders:
                if not source.count(token):
                    errors.append(f"{label}: mandatory placeholder {token!r} absent from source")
                elif isinstance(translation, str) and source.count(token) != translation.count(token):
                    errors.append(f"{label}: placeholder {token!r} occurrence count differs")
        if entry.get("category") == "html":
            for field in ("source", "translation"):
                value = entry.get(field)
                if isinstance(value, str) and (issue := _html_error(value)):
                    errors.append(f"{label}: {field} HTML: {issue}")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args(argv)
    try:
        errors = validate_catalog(load_catalog(args.catalog), args.allow_missing)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Catalog error: {error}\n")
    if errors:
        parser.exit(1, "\n".join(errors) + "\n")
    print("Catalog valid")


if __name__ == "__main__":
    main()
