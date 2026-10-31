"""Synthetic outcome icon shapes; no extracted game artwork or SWF bytes."""
import copy
import struct

import pytest

from localization_plan import merge_localization_plans
from outcome_info_icons import (
    _move_rectangles, _rectangles, icon_rectangles, repair_outcome_icons,
)
from swf_geometry import Bits, BitWriter, Tag, decode_rect, encode_matrix, encode_rect, signed_bits


def synthetic_icons(*, width=220, bitmap=3769):
    positions = [(40, 80), (900, 80), (1800, 80), (40, 800),
                 (900, 800), (1800, 800), (2800, 1400)]
    payload = struct.pack('<H', 3770) + encode_rect([40, 2800 + width, 80, 1660])
    for ordinal, (x, y) in enumerate(positions):
        unused = {'scale': [1310720, 1310720], 'translate': [0, 0]}
        visible = {'scale': [655360, 655360], 'translate': [x, y]}
        payload += (b'\x02\x41\xff\xff' + encode_matrix(unused)
                    + b'\x41' + struct.pack('<H', bitmap) + encode_matrix(visible) + b'\0')
        bits = BitWriter()
        bits.put(2, 4)
        bits.put(0, 4)
        bits.put(0, 1)
        bits.put(5, 5)
        count = signed_bits([x, y])
        bits.put(count, 5)
        bits.signed(x, count)
        bits.signed(y, count)
        bits.put(2, 2)
        for dx, dy in ((width, 0), (0, 260), (-width, 0), (0, -260)):
            count = max(2, signed_bits([dx, dy]))
            bits.put(1, 1)
            bits.put(1, 1)
            bits.put(count - 2, 4)
            bits.put(1, 1)
            bits.signed(dx, count)
            bits.signed(dy, count)
        if ordinal < 6:
            bits.put(0, 1)
            bits.put(31, 5)
            bits.put(1, 5)
            bits.put(0, 2)
            bits.put(0, 2)
            bits.put(0, 2)
        else:
            bits.put(0, 6)
        payload += bits.bytes()
    return payload


def bit_span(payload, start, end):
    bits = Bits(payload)
    bits.pos = start
    return tuple(bits.get(1) for _ in range(end - start))


def test_zero_move_is_byte_exact_and_inventories_all_seven_rectangles():
    shape = synthetic_icons()
    assert _move_rectangles(shape, [0] * 7) == shape
    assert icon_rectangles(shape) == [
        [40, 260, 80, 340], [900, 1120, 80, 340], [1800, 2020, 80, 340],
        [40, 260, 800, 1060], [900, 1120, 800, 1060],
        [1800, 2020, 800, 1060], [2800, 3020, 1400, 1660],
    ]


def test_move_preserves_dimensions_edge_bits_styles_and_bitmap_registration():
    shape = synthetic_icons()
    shifts = [-130, 12, 1024, -700, 0, 300, -50]
    moved = _move_rectangles(shape, shifts)
    old_rows, new_rows = _rectangles(shape), _rectangles(moved)
    for old, new, dx in zip(old_rows, new_rows, shifts):
        assert new[:2] == (old[0] + dx, old[1])
        assert new[2] == old[2]  # The unused fill is unchanged.
        expected_matrix = copy.deepcopy(old[3])
        expected_matrix['translate'][0] += dx
        assert new[3] == expected_matrix
        for start, end in ((4, 5), (6, 7)):
            assert bit_span(moved, new[start], new[end]) == bit_span(shape, old[start], old[end])
    assert decode_rect(moved[2:])[0] == [-660, 3044, 80, 1660]
    assert _move_rectangles(moved, [-dx for dx in shifts]) == shape


@pytest.mark.parametrize('shape, message', [
    (synthetic_icons(width=221), 'dimensions'),
    (synthetic_icons(bitmap=777), 'bitmap'),
    (synthetic_icons() + b'\0', 'trailing'),
])
def test_rejects_other_shape_dialects(shape, message):
    with pytest.raises(ValueError, match=message):
        icon_rectangles(shape)


def test_unknown_resource_hash_fails_before_mutating_tags():
    original = [Tag(0, b'')] * 4068
    original[4046] = Tag(22, synthetic_icons())
    original[4067] = Tag(39, struct.pack('<HH', 3787, 1) + b'\0\0')
    candidate = list(original)
    with pytest.raises(ValueError, match='resource guard mismatch'):
        repair_outcome_icons(original, candidate, {})
    assert candidate == original


def icon_plan(identifier, selection=True):
    return {'schema_version': 1, 'game_sha256': 'a' * 64,
            'entry_ids': [identifier], 'outcome_info_icons': selection}


def test_plan_merge_retains_explicit_repair_without_mutating_input():
    plan = icon_plan('one')
    work = {'entries': [{'id': 'one', 'category': 'static', 'location': {'tag_path': [1]}}]}
    previous = copy.deepcopy(plan)
    assert merge_localization_plans([plan], work)['outcome_info_icons'] is True
    assert plan == previous


@pytest.mark.parametrize('selection', [False, 1, 'true', {}, None])
def test_plan_rejects_nonboolean_or_disabled_repair(selection):
    with pytest.raises(ValueError, match='Invalid or duplicate outcome icon repair'):
        merge_localization_plans([icon_plan('one', selection)], {'entries': []})


def test_plan_rejects_duplicate_repair_across_plans():
    work = {'entries': [
        {'id': 'one', 'category': 'static', 'location': {'tag_path': [1]}},
        {'id': 'two', 'category': 'static', 'location': {'tag_path': [2]}},
    ]}
    with pytest.raises(ValueError, match='Invalid or duplicate outcome icon repair'):
        merge_localization_plans([icon_plan('one'), icon_plan('two')], work)
