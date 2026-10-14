"""Synthetic AVM2 relocation fixtures; no game resources."""
import struct

import pytest

from abc_font_binding import extend_font_pool, insert_method_code
from abc_inventory import ABCInventory, ABCReader


def abc(code, exceptions=b'\0', other=b'\x47'):
    pool = b'\1\1\1\2\1x\2\x16\0\1\2\x07\1\1'
    methods = b'\2' + b'\0\0\1\0' * 2
    bodies = b'\2\0\2\1\0\1' + bytes([len(code)]) + code + exceptions + b'\0'
    bodies += b'\1\2\1\0\1' + bytes([len(other)]) + other + b'\0\0'
    return struct.pack('<HH', 16, 46) + pool + methods + b'\0\0\0' + bodies


def inventory(data):
    inv = ABCInventory(data, {'tag_path': [0], 'tag_code': 82})
    inv.parse()
    return inv


def test_pool_extension_preserves_existing_resources():
    raw = abc(b'\x47')
    result, family, font = extend_font_pool(raw)
    before, after = inventory(raw), inventory(result)
    assert after.strings[family] == 'GCCS Alegreya'
    assert after.multinames[font] == {'kind': 7, 'name': after.strings.index('font')}
    assert after.strings[:len(before.strings)] == before.strings
    assert after.multinames[:len(before.multinames)] == before.multinames
    assert [x[2] for x in after.bodies] == [x[2] for x in before.bodies]
    assert extend_font_pool(raw) == (result, family, font)
    with pytest.raises(ValueError, match='already'):
        extend_font_pool(result)


def test_jump_target_runs_inserted_code_and_unselected_body_unchanged():
    raw = abc(b'\x10\0\0\0\x47')
    result = insert_method_code(raw, {0: [(4, b'\x02')]}, extra_stack={0: 1}, extra_locals={0: 1})
    inv = inventory(result)
    assert inv.bodies[0][2] == b'\x10\0\0\0\x02\x47'
    assert inv.bodies[1][2] == inventory(raw).bodies[1][2]


def test_backward_branch_and_forward_branch_rebased():
    code = b'\x02\x10\xfb\xff\xff\x10\0\0\0\x47'
    result = insert_method_code(abc(code), {0: [(1, b'\x02'), (5, b'\x02')]})
    assert inventory(result).bodies[0][2] == b'\x02\x02\x10\xfa\xff\xff\x02\x10\0\0\0\x47'


def test_switch_relative_to_opcode_relocated():
    code = b'\x1b\x08\0\0\0\x08\0\0\x47'
    result = insert_method_code(abc(code), {0: [(0, b'\x02'), (8, b'\x02')]})
    assert inventory(result).bodies[0][2] == b'\x02\x1b\x08\0\0\0\x08\0\0\x02\x47'


def test_exception_boundaries_and_handler_relocated():
    raw = abc(b'\x02\x02\x47', b'\1\0\2\2\0\0')
    result = insert_method_code(raw, {0: [(0, b'\x02'), (1, b'\x02'), (2, b'\x02')]})
    _, start, code = inventory(result).bodies[0]
    reader = ABCReader(result)
    reader.pos = start + len(code)
    assert [reader.u30() for _ in range(6)] == [1, 0, 4, 4, 0, 0]


@pytest.mark.parametrize('code,offset,snippet', [
    (b'\xff', 0, b'\x02'), (b'\x24\1\x47', 1, b'\x02'),
    (b'\x47', 0, b'\xff'), (b'\x47', 0, b'\x10\0\0\0'),
])
def test_unsupported_or_nonboundary_insertions_fail_closed(code, offset, snippet):
    with pytest.raises(ValueError):
        insert_method_code(abc(code), {0: [(offset, snippet)]})


def test_truncated_or_wrong_version_fails():
    with pytest.raises(ValueError):
        extend_font_pool(abc(b'\x47')[:-1])
    with pytest.raises(ValueError):
        extend_font_pool(b'\0\0' + abc(b'\x47')[2:])


def test_resource_increments_and_deterministic_site_order():
    raw = abc(b'\x02\x47')
    sites = [(0, b'\x02'), (1, b'\x02')]
    result = insert_method_code(raw, {0: sites}, extra_stack={0: 2}, extra_locals={0: 1})
    assert result == insert_method_code(raw, {0: sites[::-1]}, extra_stack={0: 2}, extra_locals={0: 1})
    inv = inventory(result)
    _, code_start, _ = inv.bodies[0]
    # Header fields immediately preceding the one-byte code length.
    assert result[code_start - 6:code_start] == b'\0\4\2\0\1\4'
    for kwargs in ({'extra_stack': {0: -1}}, {'extra_locals': {1: 1}}):
        with pytest.raises(ValueError):
            insert_method_code(raw, {0: sites}, **kwargs)


def test_conditional_branch_crossing_insertion():
    code = b'\x26\x11\1\0\0\x02\x47'
    result = insert_method_code(abc(code), {0: [(5, b'\x02')]})
    assert inventory(result).bodies[0][2] == b'\x26\x11\2\0\0\x02\x02\x47'


def test_existing_pools_remain_byte_identical_during_insertion():
    raw = abc(b'\x47')
    result = insert_method_code(raw, {0: [(0, b'\x02')]})
    before = inventory(raw)
    after = inventory(result)
    assert raw[:before.bodies[0][1] - 6] == result[:after.bodies[0][1] - 6]
    assert insert_method_code(raw, {}) == raw


def test_appends_public_namespace_when_absent():
    # No QName/namespace entries, but a valid method-name string.
    raw = abc(b'\x47').replace(b'\2\x16\0\1\2\x07\1\1', b'\1\1\1', 1)
    result, _, font = extend_font_pool(raw)
    inv = inventory(result)
    assert inv.multiname(font) == 'font'
    assert inv.pool_sizes['namespace'] == 2


def test_combined_api_supplies_new_pool_indices():
    from abc_font_binding import patch_abc_font_bindings

    raw = abc(b'\x47')
    result = patch_abc_font_bindings(
        raw, lambda family, font: {0: [(0, bytes([0x2C, family, 0x29]))]},
        extra_stack={0: 1},
    )
    inv = inventory(result)
    assert inv.bodies[0][2] == bytes([0x2C, inv.strings.index('GCCS Alegreya'), 0x29, 0x47])


def test_malformed_exception_boundary_fails_closed():
    raw = abc(b'\x24\1\x47', b'\1\0\1\2\0\0')
    with pytest.raises(ValueError, match='Exception'):
        insert_method_code(raw, {0: [(0, b'\x02')]})
