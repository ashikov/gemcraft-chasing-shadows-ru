"""Addressed ABC patching of authored synthetic data only."""

import copy
import hashlib
import struct

import pytest

from abc_inventory import ABCInventory
from abc_patch import patch_pushstrings


def u30(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def abc(strings, codes, *, exceptions=b"\0"):
    pool = b"\1\1\1" + u30(len(strings) + 1)
    for value in strings:
        raw = value.encode("utf-8")
        pool += u30(len(raw)) + raw
    # First literal doubles as a QName and method name: it must stay untouched.
    pool += b"\2\x16\0\1\2\x07\1\1"
    methods = u30(len(codes)) + b"\0\0\1\0" * len(codes)
    bodies = u30(len(codes))
    for method, code in enumerate(codes):
        bodies += u30(method) + b"\2\1\0\1" + u30(len(code)) + code + exceptions + b"\0"
    return struct.pack("<HH", 16, 46) + pool + methods + b"\0\0\0" + bodies


def inventory(raw):
    result = ABCInventory(raw, {"tag_path": [1], "tag_code": 82})
    result.parse()
    return result


def replacement(raw, method=0, offset=0, translation="Синтетика"):
    entry = next(
        entry
        for entry in inventory(raw).entries
        if entry["location"].get("method_index") == method
        and entry["location"].get("code_offset") == offset
    )
    return {**copy.deepcopy(entry), "translation": translation}


def test_isolates_shared_literals_and_preserves_technical_symbols():
    raw = abc(["Synthetic text"], [b"\x2c\1\x29\x2c\1\x29\x47", b"\x2c\1\x29\x47"])
    result = patch_pushstrings(raw, [replacement(raw)])
    before, after = inventory(raw), inventory(result)
    assert after.strings == [*before.strings, "Синтетика"]
    assert after.multinames == before.multinames
    assert after.symbols[1] == before.symbols[1]
    assert after.method_labels == before.method_labels
    assert after.bodies[0][2] == b"\x2c\2\x29\x2c\1\x29\x47"
    assert after.bodies[1][2] == before.bodies[1][2]


def test_deterministic_order_and_distinct_translations_at_identical_source_sites():
    raw = abc(["Synthetic text"], [b"\x2c\1\x29\x47", b"\x2c\1\x29\x47"])
    records = [
        replacement(raw, 1, translation="Второе"),
        replacement(raw, translation="Первое"),
    ]
    assert patch_pushstrings(raw, records) == patch_pushstrings(raw, records[::-1])
    assert inventory(patch_pushstrings(raw, records)).strings[-2:] == [
        "Первое",
        "Второе",
    ]


def test_nonminimal_operand_keeps_width_and_branches_exceptions_unchanged():
    code = b"\x10\0\0\0\x2c\x81\0\x29\x47"
    exceptions = b"\1\0\x08\x08\0\0"
    raw = abc(["Synthetic text"], [code], exceptions=exceptions)
    result = patch_pushstrings(raw, [replacement(raw, offset=4)])
    before, after = inventory(raw), inventory(result)
    assert after.bodies[0][2] == b"\x10\0\0\0\x2c\x82\0\x29\x47"
    before_end = before.bodies[0][1] + len(code)
    after_end = after.bodies[0][1] + len(code)
    assert result[after_end:] == raw[before_end:]


def test_refuses_crossing_operand_width_boundary():
    raw = abc(
        ["Synthetic text", *[f"padding{i}" for i in range(126)]], [b"\x2c\1\x29\x47"]
    )
    with pytest.raises(ValueError, match="width"):
        patch_pushstrings(raw, [replacement(raw)])


@pytest.mark.parametrize(
    "field,value", [("source", "Stale"), ("source_sha256", "0" * 64)]
)
def test_stale_source_fails(field, value):
    raw = abc(["Synthetic text"], [b"\x2c\1\x29\x47"])
    record = replacement(raw)
    record[field] = value
    with pytest.raises(ValueError):
        patch_pushstrings(raw, [record])


@pytest.mark.parametrize(
    "field,value",
    [
        ("abc_byte_offset", 0),
        ("code_offset", 1),
        ("string_pool_index", 0),
        ("method_index", 4),
        ("method_index", True),
    ],
)
def test_stale_or_malformed_address_fails(field, value):
    raw = abc(["Synthetic text"], [b"\x2c\1\x29\x47"])
    record = replacement(raw)
    record["location"][field] = value
    with pytest.raises(ValueError):
        patch_pushstrings(raw, [record])


def test_duplicate_sites_fail_even_if_identical():
    raw = abc(["Synthetic text"], [b"\x2c\1\x29\x47"])
    record = replacement(raw)
    with pytest.raises(ValueError, match="Duplicate"):
        patch_pushstrings(raw, [record, record])


@pytest.mark.parametrize("code", [b"\x2c\1\x29\xff", b"\x2c\1\x29\x10\xff\xff\x7f\x47"])
def test_rejects_unverified_selected_method(code):
    raw = abc(["Synthetic text"], [code])
    with pytest.raises(ValueError, match="Unsupported|control flow"):
        patch_pushstrings(raw, [replacement(raw)])


def test_unselected_unsupported_method_is_preserved():
    raw = abc(["Synthetic text"], [b"\x2c\1\x29\x47", b"\xff"])
    result = patch_pushstrings(raw, [replacement(raw)])
    assert inventory(result).bodies[1][2] == b"\xff"


def test_exception_offsets_must_be_instruction_boundaries():
    raw = abc(["Synthetic text"], [b"\x2c\1\x29\x47"], exceptions=b"\1\1\3\3\0\0")
    with pytest.raises(ValueError, match="exception"):
        patch_pushstrings(raw, [replacement(raw)])


def test_empty_request_preserves_original_bytes():
    raw = abc(["Synthetic text"], [b"\x47"])
    assert patch_pushstrings(raw, []) == raw


def test_empty_translation_is_an_explicit_isolated_literal():
    raw = abc(["Synthetic text"], [b"\x2c\1\x29\x47"])
    result = patch_pushstrings(raw, [replacement(raw, translation="")])
    assert inventory(result).strings == ["", "Synthetic text", ""]


def test_utf8_translation_is_not_normalized():
    raw = abc(["Synthetic text"], [b"\x2c\1\x29\x47"])
    text = "<b>Синтетика {value}</b>\nе\u0308"
    result = patch_pushstrings(raw, [replacement(raw, translation=text)])
    assert inventory(result).strings[-1] == text
    assert (
        hashlib.sha256(inventory(raw).strings[1].encode()).hexdigest()
        == replacement(raw)["source_sha256"]
    )


def test_all_bytes_outside_pool_and_selected_operand_are_identical():
    raw = abc(["Synthetic text", "Untouched"], [b"\x2c\1\x29\x2c\2\x29\x47"])
    result = patch_pushstrings(raw, [replacement(raw)])
    before, after = inventory(raw), inventory(result)
    # The pool grows; align by method code addresses, then mask the sole operand.
    before_start, after_start = before.bodies[0][1], after.bodies[0][1]
    suffix = bytearray(result[after_start:])
    suffix[1] = raw[before_start + 1]
    assert bytes(suffix) == raw[before_start:]
    assert result[:7] == raw[:7]  # header and non-string primitive pools


def test_string_pool_count_growth_does_not_move_code_relative_addresses():
    raw = abc(
        ["Synthetic text", *[f"padding{i}" for i in range(125)]],
        [b"\x2c\x81\0\x29\x47"],
    )
    result = patch_pushstrings(raw, [replacement(raw)])
    checked = inventory(result)
    assert len(checked.strings) == 128
    assert checked.bodies[0][2] == b"\x2c\xff\0\x29\x47"
    assert checked.entries[0]["location"]["code_offset"] == 0


@pytest.mark.parametrize(('padding', 'code', 'offset', 'expected'), [
    (125, b'\x2c\1\x29\x2c\1\x29\x47', 3,
     b'\x2c\x7f\x29\x2c\x7f\x29\x47'),
    (16381, b'\x2c\x81\0\x29\x2c\x81\0\x29\x47', 4,
     b'\x2c\xff\x7f\x29\x2c\xff\x7f\x29\x47'),
])
def test_reuses_identical_translations_only_when_operand_capacity_is_exceeded(
        padding, code, offset, expected):
    raw = abc(['Synthetic text', *[f'padding{i}' for i in range(padding)]], [code])
    records = [replacement(raw, offset=0), replacement(raw, offset=offset)]
    result = patch_pushstrings(raw, records)
    before, after = inventory(raw), inventory(result)
    assert after.strings == [*before.strings, 'Синтетика']
    assert after.bodies[0][2] == expected
    assert result == patch_pushstrings(raw, records[::-1])
    records[1]['translation'] = 'Другой перевод'
    with pytest.raises(ValueError, match='width'):
        patch_pushstrings(raw, records)


def test_retains_separate_identical_literals_while_all_indices_fit():
    raw = abc(['Synthetic text'], [b'\x2c\1\x29\x2c\1\x29\x47'])
    records = [replacement(raw, offset=0), replacement(raw, offset=3)]
    result = patch_pushstrings(raw, records)
    assert inventory(result).strings == ['', 'Synthetic text', 'Синтетика', 'Синтетика']
    assert inventory(result).bodies[0][2] == b'\x2c\2\x29\x2c\3\x29\x47'


def test_preserves_unreachable_off_body_jump_after_return():
    code = b'\x2c\1\x29\x47\x10\0\0\0'
    raw = abc(['Synthetic text'], [code])
    result = patch_pushstrings(raw, [replacement(raw)])
    before, after = inventory(raw), inventory(result)
    assert after.bodies[0][2] == b'\x2c\2\x29\x47\x10\0\0\0'
    assert after.control_flow_anomalies == before.control_flow_anomalies == [
        {'method_index': 0, 'target_offset': len(code)}]


@pytest.mark.parametrize('code,exceptions', [
    # A conditional edge makes the otherwise trailing invalid jump reachable.
    (b'\x2c\1\x29\x26\x11\1\0\0\x47\x10\0\0\0', b'\0'),
    # All handlers are conservatively treated as roots, even after return.
    (b'\x2c\1\x29\x47\x10\0\0\0', b'\1\0\3\4\0\0'),
    # A reachable switch arm cannot leave the body.
    (b'\x2c\1\x29\x24\0\x1b\x08\0\0\0\x08\0\0', b'\0'),
])
def test_rejects_invalid_branches_reachable_from_any_control_flow_root(code, exceptions):
    raw = abc(['Synthetic text'], [code], exceptions=exceptions)
    with pytest.raises(ValueError, match='control flow'):
        patch_pushstrings(raw, [replacement(raw)])


@pytest.mark.parametrize('terminal', [b'\x47', b'\x48', b'\x03'])
def test_terminal_operations_make_only_the_following_tail_unreachable(terminal):
    code = b'\x2c\1' + (b'\x29' if terminal == b'\x47' else b'') + terminal + b'\x10\0\0\0'
    raw = abc(['Synthetic text'], [code])
    result = patch_pushstrings(raw, [replacement(raw)])
    assert inventory(result).bodies[0][2][-4:] == b'\x10\0\0\0'


def test_reachability_terminates_on_a_backward_cycle_without_entering_dead_tail():
    code = b'\x2c\1\x29\x10\xfc\xff\xff\x10\0\0\0'
    raw = abc(['Synthetic text'], [code])
    result = patch_pushstrings(raw, [replacement(raw)])
    assert inventory(result).bodies[0][2] == b'\x2c\2' + code[2:]


def test_switch_case_reaches_a_malformed_tail_even_when_default_returns():
    code = b'\x2c\1\x29\x24\0\x1b\x08\0\0\0\x09\0\0\x47\x10\0\0\0'
    raw = abc(['Synthetic text'], [code])
    with pytest.raises(ValueError, match='control flow'):
        patch_pushstrings(raw, [replacement(raw)])
