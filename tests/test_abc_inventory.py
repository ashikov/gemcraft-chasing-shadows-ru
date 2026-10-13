"""ABC fixtures authored here, without game resources."""
import hashlib
import struct

import pytest

from abc_inventory import extract_abc


def u30(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


def abc(strings, codes, *, property_name="text", symbol=None):
    strings = [*strings, property_name, "SyntheticMethod"]
    if symbol:
        strings.append(symbol)
    pool = b"\1\1\1" + u30(len(strings) + 1)
    for value in strings:
        raw = value.encode("utf-8")
        pool += u30(len(raw)) + raw
    pool += b"\2\x16\0\1"  # public namespace and empty namespace-set pool
    pool += b"\2\x07\1" + u30(len(strings) - (1 if symbol else 0) - 1)
    methods = u30(len(codes))
    for _ in codes:
        methods += b"\0\0" + u30(len(strings) - (1 if symbol else 0)) + b"\0"
    bodies = u30(len(codes))
    for index, code in enumerate(codes):
        bodies += u30(index) + b"\2\1\0\1" + u30(len(code)) + code + b"\0\0"
    return struct.pack("<HH", 16, 46) + pool + methods + b"\0\0\0" + bodies


def extract(raw):
    return extract_abc(raw, {"tag_path": [3, 2], "tag_code": 82})


def test_direct_text_assignment_and_distinct_repeated_uses():
    raw = abc(["Hello player"], [b"\xd0\x2c\1\x61\1\x47", b"\xd0\x2c\1\x61\1\x47"])
    result = extract(raw)
    uses = [entry for entry in result["entries"] if "method_index" in entry["location"]]
    assert len(uses) == 2
    assert [entry["id"] for entry in uses] == ["abc.3.2.0.1", "abc.3.2.1.1"]
    assert all(entry["classification"] == "probable" for entry in uses)
    assert all(entry["source_sha256"] == hashlib.sha256(b"Hello player").hexdigest() for entry in uses)
    assert all(entry["translation"] is None for entry in uses)
    assert result["coverage"]["instruction_coverage"] == "complete"
    assert result == extract(raw)


def test_html_and_non_ui_pushes_are_conservative():
    raw = abc(["<b>Hello</b>\nworld", "Likely display message", "opaque"],
              [b"\xd0\x2c\1\x61\1\x2c\2\x29\x2c\3\x29\x47"], property_name="htmlText")
    uses = [entry for entry in extract(raw)["entries"] if "method_index" in entry["location"]]
    assert [entry["classification"] for entry in uses] == ["probable", "probable", "unknown"]
    assert uses[0]["location"]["text_property"] == "htmlText"
    assert uses[0]["source"] == "<b>Hello</b>\nworld"


def test_symbol_pool_and_unused_pool_entries():
    entries = extract(abc(["Unreferenced words", "opaque"], [b"\x47"]))["entries"]
    by_source = {entry["source"]: entry for entry in entries}
    assert by_source["SyntheticMethod"]["classification"] == "technical"
    assert by_source["text"]["classification"] == "technical"
    assert by_source["Unreferenced words"]["classification"] == "unknown"
    assert by_source["opaque"]["id"] == "abc.3.2.pool.2"


def test_unknown_opcode_stops_body_without_guessing():
    result = extract(abc(["Hidden words"], [b"\xff\x2c\1\x47"]))
    assert result["coverage"]["instruction_coverage"] == "partial"
    assert result["coverage"]["unsupported_instructions"][0]["code_offset"] == 0
    assert all("method_index" not in entry["location"] for entry in result["entries"])
    assert next(entry for entry in result["entries"] if entry["source"] == "Hidden words")["classification"] == "unknown"


@pytest.mark.parametrize("code", [b"\x2c", b"\x2c\x7f\x47", b"\x1b\0\0\0\x01"])
def test_corrupt_instruction_operands_fail(code):
    with pytest.raises(ValueError):
        extract(abc(["Hello"], [code]))


def test_truncated_and_trailing_abc_fail():
    raw = abc(["Hello"], [b"\x47"])
    with pytest.raises(ValueError):
        extract(raw[:-1])
    with pytest.raises(ValueError):
        extract(raw + b"\0")


def rich_abc():
    strings = ["Hello synthetic player", "text", "Component", "show", "argument", "Annotation", "key", "value", "Default words"]
    pool = b"\1\1\1" + u30(len(strings) + 1)
    for value in strings:
        encoded = value.encode("utf-8")
        pool += u30(len(encoded)) + encoded
    pool += b"\2\x16\0\1\5\x07\1\2\x07\1\3\x07\1\4\x1d\2\1\2"
    methods = b"\2\1\0\2\4\x88\1\x09\x01\5\0\0\0\0"
    metadata = b"\1\6\1\7\x08"
    classes = b"\1\2\0\0\0\1\1\3\1\0\0\1\0"
    scripts = b"\1\1\0"
    code = b"\xd0\x2c\1\x61\1\x47"
    bodies = b"\1\0\2\2\0\1" + u30(len(code)) + code + b"\1\0\6\5\2\3\0"
    return struct.pack("<HH", 16, 46) + pool + methods + metadata + classes + scripts + bodies


def test_class_traits_optional_parameters_metadata_and_exception_multinames():
    result = extract(rich_abc())
    displayed = next(entry for entry in result["entries"] if entry["source"] == "Hello synthetic player")
    assert {"owner": "Component", "method": "show"} in displayed["location"]["method_labels"]
    assert displayed["context_confidence"] != "confirmed"  # screen and receiver remain unresolved
    assert displayed["location"]["abc_byte_offset"] > displayed["location"]["code_offset"]
    by_source = {entry["source"]: entry for entry in result["entries"]}
    assert by_source["argument"]["classification"] == "technical"
    assert by_source["Annotation"]["classification"] == "technical"
    assert by_source["Default words"]["classification"] == "unknown"


def test_debug_and_variable_scope_operands_keep_alignment():
    code = b"\x65\x80\x01\xef\1\2\1\0\xf1\2\x2c\1\x29\x47"
    result = extract(abc(["Synthetic message", "synthetic.as"], [code]))
    entry = next(entry for entry in result["entries"] if entry["source"] == "Synthetic message")
    assert entry["location"]["code_offset"] == 10
    assert next(entry for entry in result["entries"] if entry["source"] == "synthetic.as")["classification"] == "technical"


def test_lookupswitch_skips_complete_case_array():
    code = b"\x1b\x0b\0\0\1\x0b\0\0\x0b\0\0\x2c\1\x29\x47"
    result = extract(abc(["Synthetic message"], [code]))
    entry = next(entry for entry in result["entries"] if entry["source"] == "Synthetic message")
    assert entry["location"]["code_offset"] == 11


def test_multiple_uses_in_one_body_remain_distinct():
    uses = [entry for entry in extract(abc(["Same source"], [b"\x2c\1\x29\x2c\1\x29\x47"]))["entries"]
            if "method_index" in entry["location"]]
    assert [entry["id"] for entry in uses] == ["abc.3.2.0.0", "abc.3.2.0.3"]


@pytest.mark.parametrize("raw", [b"\x11\0\x2e\0", b"\x10\0\x2f\0", b"\x10\0\x2e\0\x80\x80\x80\x80\x04"])
def test_unsupported_version_and_integer_overflow_fail(raw):
    with pytest.raises(ValueError):
        extract(raw)


def test_negative_pushshort_keeps_instruction_alignment():
    code = b"\x25\xff\xff\xff\xff\x0f\x29\x2c\1\x29\x47"
    result = extract(abc(["Synthetic message"], [code]))
    entry = next(entry for entry in result["entries"] if entry["source"] == "Synthetic message")
    assert entry["location"]["code_offset"] == 7
    assert result["coverage"]["instruction_coverage"] == "complete"


@pytest.mark.parametrize("code", [b"\x10\xff\xff\x7f\x47", b"\x10\1\0\0\x2c\1\x29\x47"])
def test_unverified_branch_targets_are_reported_without_invented_control_flow(code):
    result = extract(abc(["Synthetic message"], [code]))
    assert result["coverage"]["control_flow_anomalies"]
    assert result["coverage"]["instruction_coverage"] == "complete"


def test_empty_constant_is_not_reported_as_a_translatable_record():
    result = extract(abc([""], [b"\x2c\1\x29\x47"]))
    assert all(entry["source"] for entry in result["entries"])
