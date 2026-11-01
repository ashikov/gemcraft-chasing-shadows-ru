"""Synthetic glyphs and timelines; no game artwork in CI fixtures."""
import copy
import struct

import pytest

from localization_plan import merge_localization_plans
from swf_geometry import Bits, Tag, build_shape, build_text, encode_matrix
from translation_catalog import load_catalog
from tutor_highlights import (
    _placements, _style_table, rectangle_records, repair_tutor_highlights,
    retarget_rectangle, span_ink, text_rows, validate_recipe,
    separate_rectangles,
)


def fixture():
    # Font-table bounds deliberately disagree with the outlines. The pen must
    # follow TEXTRECORD advances, including a whitespace-only inherited record.
    glyph = build_shape([[('M', 0, -1000), ('L', 800, -1000),
                          ('L', 800, 0), ('L', 0, 0), ('L', 0, -1000)]])
    font = {'codes': [32, 65, 66], 'glyphs': [build_shape([]), glyph, glyph],
            'bounds': [[-9999, 9999, -9999, 9999]] * 3}
    text = {'id': 7, 'bounds': [0, 1000, 0, 1000], 'gb': 2, 'ab': 10,
            'matrix_raw': encode_matrix({'translate': [30, 50]}), 'records': [
                {'flags': 0x8f, 'font_id': 8, 'height': 2048, 'x': 10, 'y': 200,
                 'color': b'\0\0\0', 'entries': [(1, 130)]},
                {'flags': 0x80, 'entries': [(0, 70)]},
                {'flags': 0x80, 'entries': [(2, 140)]}]}
    placement = {'scale': [32768, 49152], 'rotate': [0, 0], 'translate': [100, 300]}
    return Tag(11, build_text(text)), {8: font}, placement


def test_real_outlines_encoded_advances_and_nested_transforms():
    rows = text_rows(*fixture())
    assert rows == [[('A', [120, 160, 412.5, 487.5])], [(' ', None)],
                    [('B', [220, 260, 412.5, 487.5])]]
    assert span_ink(rows, [2, 'B']) == ([220, 260, 412.5, 487.5], [])


@pytest.mark.parametrize('span', [[0, 'B'], [3, 'A'], [0, ''], [1, ' '], [0, 'AA']])
def test_missing_ambiguous_or_blank_spans_fail_closed(span):
    with pytest.raises(ValueError):
        span_ink(text_rows(*fixture()), span)


def test_padding_follows_ink_and_leaves_room_for_neighbour_and_stroke():
    # Original padding is 40/80/30/30. On the right, the next word now starts
    # only 60 twips away, so the path takes half that gap, leaving 20 after stroke.
    rect = retarget_rectangle([0, 200, 0, 140], [40, 120, 30, 110],
                              [300, 500, 200, 300], [[560, 600, 200, 300]], 20)
    assert rect == [260, 530, 170, 330]
    for scale in (1, .75, .5):
        assert (560 - rect[1] - 10) * scale > 0
        assert (300 - rect[0] - 10) * scale > 0


def test_descenders_keep_internal_padding_at_least_stroke_width():
    assert retarget_rectangle([0, 200, 0, 140], [40, 120, 30, 139],
                               [300, 500, 200, 300], [], 20) == [260, 580, 170, 320]


def test_adjacent_backgrounds_leave_positive_clearance_including_borders():
    regions = [(1, [0, 200, 0, 200], [20, 180, 30, 140]),
               (2, [0, 200, 190, 390], [20, 180, 260, 360])]
    separate_rectangles(regions, 20)
    first, second = [r[1] for r in regions]
    assert first[3] + 10 < second[2] - 10
    assert first[3] - 10 > 140 and second[2] + 10 < 260


@pytest.mark.parametrize('neighbour', [[499, 600, 200, 300], [510, 600, 200, 300],
                                       [350, 400, 305, 360]])
def test_insufficient_clearance_fails_instead_of_covering_other_text(neighbour):
    with pytest.raises(ValueError):
        retarget_rectangle([0, 200, 0, 140], [40, 120, 30, 110],
                            [300, 500, 200, 300], [neighbour], 20)


def test_timeline_replacement_inherits_matrix_at_depth():
    def place(flags, cid, matrix=b''):
        return Tag(26, bytes([flags]) + struct.pack('<HH', 4, cid) + matrix)
    from swf_geometry import write_swf
    # Reuse the tag serializer to avoid encoding children differently in tests.
    children = [place(6, 20, encode_matrix({'translate': [70, 80]})),
                Tag(1, b''), place(3, 21)] + [Tag(1, b'')] * 29 + [Tag(0, b'')]
    prefix = b'FWS\x0a\0\0\0\0' + b'\x08\0\0\x0c\x1e\0'
    from swf_geometry import read_swf
    raw = read_swf(write_swf(prefix, children, 10))[1]
    sprite = Tag(39, struct.pack('<HH', 3608, 30) + b''.join(t.raw for t in raw))
    result = _placements(sprite, {20, 21})
    assert result[20][0] == 1 and result[21][0] == 2
    assert result[21][1]['translate'] == [70, 80]


def test_unknown_original_guard_does_not_mutate_candidate():
    recipe = load_catalog('translations/plans/pr05-tutor.json')['tutor_highlights']
    original = [Tag(0, b'')] * 3849
    candidate = original[:]
    with pytest.raises(ValueError, match='guard mismatch'):
        repair_tutor_highlights(original, candidate, {}, recipe)
    assert candidate == original


def test_merge_preserves_complete_recipe_and_rejects_duplicate():
    recipe = load_catalog('translations/plans/pr05-tutor.json')['tutor_highlights']
    plan = {'schema_version': 1, 'game_sha256': 'a' * 64,
            'entry_ids': ['one'], 'tutor_highlights': recipe}
    work = {'entries': [{'id': 'one', 'category': 'static', 'location': {'tag_path': [1]}}]}
    before = copy.deepcopy(plan)
    merged = merge_localization_plans([plan], work)
    assert merged['tutor_highlights'] == recipe and before == plan
    with pytest.raises(ValueError, match='Duplicate tutor'):
        merge_localization_plans([plan, plan], work)


@pytest.mark.parametrize('value', [None, {}, True, {'sprite_tag': 3848, 'shapes': []}])
def test_incomplete_recipe_rejected(value):
    with pytest.raises(ValueError, match='Invalid tutor'):
        validate_recipe(value)


@pytest.mark.parametrize('key,value', [
    ('target', []), ('target', None), ('target', [[True, 'A']]),
    ('target', [[0, '']]), ('target', [[0, ' A']]), ('source', [0, '']),
    ('fill', 0), ('source_rect', [0, 0, 0, 20]), ('text_sha256', 'b' * 63),
])
def test_bad_area_cannot_drop_or_redirect_highlights(key, value):
    recipe = copy.deepcopy(load_catalog('translations/plans/pr05-tutor.json')['tutor_highlights'])
    recipe['shapes'][0]['areas'][0][key] = value
    candidate = [Tag(0, b'')]
    with pytest.raises(ValueError, match='Invalid tutor'):
        repair_tutor_highlights([], candidate, {}, recipe)
    assert candidate == [Tag(0, b'')]


def test_unknown_style_and_unrepresentable_rectangles_rejected():
    with pytest.raises(ValueError, match='fill style'):
        _style_table(Tag(32, b'\x01\0\0\0\0\0'), 0)
    with pytest.raises(ValueError, match='edge range'):
        rectangle_records([(1, [0, 200000, 0, 400])], 1, 20)


@pytest.mark.parametrize('stroke', [0, 20])
def test_serialized_highlights_have_the_requested_fills_outlines_and_line_style(stroke):
    rectangles = [(2, [-40, 280, 50, 350]), (1, [400, 800, -100, 200])]
    bits = Bits(rectangle_records(rectangles, 2, stroke))
    fb, lb = bits.get(4), bits.get(4)
    for fill, rect in rectangles:
        assert bits.get(1) == 0
        flags = bits.get(5)
        assert flags & 7 == 7 and bool(flags & 8) == bool(stroke)
        count = bits.get(5)
        x, y = bits.signed(count), bits.signed(count)
        assert (bits.get(fb), bits.get(fb)) == (0, fill)
        if lb:
            assert bits.get(lb) == 1
        points = [(x, y)]
        for _ in range(4):
            assert bits.get(1) == bits.get(1) == 1
            count = bits.get(4) + 2
            assert bits.get(1) == 1
            x, y = x + bits.signed(count), y + bits.signed(count)
            points.append((x, y))
        left, right, top, bottom = rect
        assert points == [(left, top), (right, top), (right, bottom),
                          (left, bottom), (left, top)]
    assert bits.get(6) == 0
    assert bits.get(len(bits.data) * 8 - bits.pos) == 0
