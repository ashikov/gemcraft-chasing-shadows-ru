"""Isolate addressed pushstring uses without relocating any ABC bytecode.

The SWF caller must guard the containing file's hash and the DoABC tag address.
Existing string entries, names and all unselected bytecode remain byte-identical.
"""

from collections.abc import Mapping
import hashlib

from abc_inventory import ABCInventory, ABCReader


def _u30(value, width=None):
    if not 0 <= value < 1 << 30:
        raise ValueError("ABC u30 value overflow")
    minimum = max(1, (value.bit_length() + 6) // 7)
    if width is None:
        width = minimum
    if not minimum <= width <= 5:
        raise ValueError("New ABC string index exceeds original operand width")
    # Nonminimal u30 is legal; keeping its width preserves every code address.
    return bytes(
        ((value >> (7 * position)) & 127) | (128 if position < width - 1 else 0)
        for position in range(width)
    )


def _string_pool_bounds(data):
    reader = ABCReader(data)
    reader.take(4)
    for kind in ("int", "uint", "double"):
        for _ in range(1, reader.count()):
            if kind == "double":
                reader.take(8)
            else:
                reader.encoded(32)
    start = reader.pos
    count = reader.count()
    entries_start = reader.pos
    for _ in range(1, count):
        reader.take(reader.u30())
    return start, entries_start, reader.pos


def _validate_method(data, inventory, method, start, code):
    if any(item["method_index"] == method for item in inventory.unsupported):
        raise ValueError("Unsupported instruction in selected ABC method")
    if any(item["method_index"] == method for item in inventory.control_flow_anomalies):
        raise ValueError("Unverified control flow in selected ABC method")
    instructions = inventory.instructions(code, method)
    boundaries = {offset for offset, _, _ in instructions}
    reader = ABCReader(data)
    reader.pos = start + len(code)
    for _ in range(reader.count()):
        begin, end, target = reader.u30(), reader.u30(), reader.u30()
        if (
            begin not in boundaries
            or end not in boundaries | {len(code)}
            or target not in boundaries
        ):
            raise ValueError("ABC exception address is not an instruction boundary")
        reader.u30()
        reader.u30()
    return {offset: (opcode, operands) for offset, opcode, operands in instructions}


def patch_pushstrings(data, replacements):
    """Append isolated literals and retarget only individually guarded uses.

    Records use the local workspace shape: source, source_sha256, translation,
    and location containing method_index, code_offset, abc_byte_offset and
    string_pool_index. Content/placeholder/font validation belongs to the caller.
    No existing constant is replaced, even when it has no known technical use.
    """
    data = bytes(data)
    inventory = ABCInventory(data, {"tag_path": [0], "tag_code": 82})
    inventory.parse()
    sites = {}
    for record in replacements:
        if not isinstance(record, Mapping) or not isinstance(
            record.get("location"), Mapping
        ):
            raise ValueError("ABC replacement requires a record and location")
        location = record["location"]
        for name in (
            "method_index",
            "code_offset",
            "abc_byte_offset",
            "string_pool_index",
        ):
            if type(location.get(name)) is not int or location[name] < 0:
                raise ValueError(f"Invalid ABC replacement {name}")
        source, translation = record.get("source"), record.get("translation")
        if not isinstance(source, str) or not isinstance(translation, str):
            raise ValueError("ABC replacement requires source and translation strings")
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != record.get(
            "source_sha256"
        ):
            raise ValueError("ABC replacement source SHA mismatch")
        index = location["string_pool_index"]
        if index >= len(inventory.strings) or inventory.strings[index] != source:
            raise ValueError("ABC replacement source pool mismatch")
        site = (location["method_index"], location["code_offset"])
        if site in sites:
            raise ValueError("Duplicate ABC replacement site")
        sites[site] = record
    if not sites:
        return data

    bodies = {method: (start, code) for method, start, code in inventory.bodies}
    decoded = {}
    modified = bytearray(data)
    appended = bytearray()
    expected = {}
    for ordinal, (site, record) in enumerate(sorted(sites.items())):
        method, offset = site
        if method not in bodies:
            raise ValueError("ABC replacement method has no body")
        start, code = bodies[method]
        if method not in decoded:
            decoded[method] = _validate_method(data, inventory, method, start, code)
        location = record["location"]
        instruction = decoded[method].get(offset)
        if instruction != (0x2C, [location["string_pool_index"]]):
            raise ValueError("ABC replacement does not address the expected pushstring")
        if location["abc_byte_offset"] != start + offset:
            raise ValueError("ABC replacement byte offset mismatch")
        operand_reader = ABCReader(code)
        operand_reader.pos = offset + 1
        operand_reader.u30()
        width = operand_reader.pos - offset - 1
        new_index = len(inventory.strings) + ordinal
        encoded_index = _u30(new_index, width)
        modified[start + offset + 1 : start + offset + 1 + width] = encoded_index
        raw = record["translation"].encode("utf-8")
        appended.extend(_u30(len(raw)) + raw)
        expected[site] = (new_index, record["translation"])

    pool_start, entries_start, pool_end = _string_pool_bounds(data)
    result = (
        bytes(modified[:pool_start])
        + _u30(len(inventory.strings) + len(sites))
        + bytes(modified[entries_start:pool_end])
        + bytes(appended)
        + bytes(modified[pool_end:])
    )
    checked = ABCInventory(result, {"tag_path": [0], "tag_code": 82})
    checked.parse()
    if checked.strings[: len(inventory.strings)] != inventory.strings:
        raise ValueError("Existing ABC strings changed during patching")
    for method, start, code in checked.bodies:
        if method not in decoded:
            continue
        instructions = _validate_method(result, checked, method, start, code)
        for site, (new_index, translation) in expected.items():
            if site[0] == method and (
                instructions.get(site[1]) != (0x2C, [new_index])
                or checked.strings[new_index] != translation
            ):
                raise ValueError("Patched ABC use failed verification")
    return result
