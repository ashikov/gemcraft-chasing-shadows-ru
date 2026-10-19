"""Synthetic per-TEXTRECORD fragments, including inline pen inheritance."""

import copy
import hashlib

import pytest

from addressed_localization import _display_text
from inventory_strings import extract_inventory
from swf_geometry import (
    Tag, build_font, build_shape, build_text, encode_matrix, parse_text,
)
from test_localization_fonts import font_fixture
from test_string_inventory import swf


def inputs(runs=('А ', ' ', 'Б', 'А', 'Б '), source_id=1):
    source = font_fixture()
    source['id'] = source_id
    rows = [
        {'flags': 0x8f, 'font_id': source_id, 'height': 2048,
         'x': 100, 'y': 200, 'color': b'\x01\x02\x03', 'entries': [(0, 20)]},
        {'flags': 0x80, 'entries': [(1, 30)]},
        {'flags': 0x80, 'entries': [(0, 20)]},
        {'flags': 0x82, 'y': 400, 'entries': [(1, 30)]},
        {'flags': 0x85, 'x': 200, 'color': b'\x04\x05\x06', 'entries': [(0, 20)]},
    ][:len(runs)]
    text = {'id': 7, 'bounds': [1100, 3000, 680, 900],
            'matrix_raw': encode_matrix({'translate': [1000, 500]}),
            'gb': 1, 'ab': 8, 'records': rows}
    tag = Tag(11, build_text(text))
    raw = swf([Tag(75, build_font(source)), tag])
    entry = extract_inventory(raw, hashlib.sha256(raw).hexdigest())['entries'][0]
    entry['translation'] = ''.join(runs)
    target = copy.deepcopy(source)
    target.update(id=6001, codes=[32, 65, 66, ord('А'), ord('Б')],
                  glyphs=[build_shape([])] + source['glyphs'] * 2,
                  bounds=[[0, 0, 0, 0]] + source['bounds'] * 2,
                  advances=[100, 200, 300, 200, 300], kerning=[])
    layout = {'font_id': source_id, 'height': 2048, 'max_width': 1800,
              'runs': list(runs)}
    return tag, entry, target, layout, {source_id: source}


def patch(values):
    return _display_text(*values)


@pytest.mark.parametrize('source_id', [1, 255])
def test_runs_keep_inline_fragments_positions_style_and_matrix(source_id):
    values = inputs(source_id=source_id)
    before = parse_text(values[0].payload)
    changed, _ = patch(values)
    after = parse_text(changed.payload)
    assert after['matrix_raw'] == before['matrix_raw']
    assert len(after['records']) == len(before['records']) == 5
    for old, new, fragment in zip(before['records'], after['records'], values[3]['runs']):
        assert {k: v for k, v in new.items() if k not in ('entries', 'font_id')} == {
            k: v for k, v in old.items() if k not in ('entries', 'font_id')}
        assert ('font_id' in new) == ('font_id' in old)
        if 'font_id' in new:
            assert new['font_id'] == 6001
        assert ''.join(chr(values[2]['codes'][i]) for i, _ in new['entries']) == fragment
    # Encoded advances are 20+10, 10, 30, 20, 30+10. A whitespace-only
    # record advances the inherited pen but contributes no ink at the origin.
    assert after['bounds'] == [1100, 1210, 680, 900]
    assert patch(values)[0].payload == changed.payload


def test_runs_advance_pen_by_encoded_integers_not_fractional_font_metrics():
    values = inputs(runs=('ААА', 'Б'))
    values[2]['advances'][values[2]['codes'].index(ord('А'))] = 106
    changed, _ = patch(values)
    after = parse_text(changed.payload)
    assert [a for _, a in after['records'][0]['entries']] == [11, 11, 11]
    # Second fragment starts at 100+33, not 100+31.8.
    assert after['bounds'][1] == 1143


@pytest.mark.parametrize('constraint', ['right_edge', 'max_width'])
def test_combined_inline_width_is_guarded_even_when_each_fragment_fits(constraint):
    values = inputs(runs=('АА', 'ББ'))
    # Individual run advances are 40 and 60; together they consume 100.
    if constraint == 'max_width':
        values[3]['max_width'] = 70
    else:
        text = parse_text(values[0].payload)
        text['bounds'][1] = 1170
        values = (Tag(11, build_text(text)), *values[1:])
    with pytest.raises(ValueError, match='width|edge'):
        patch(values)


@pytest.mark.parametrize('runs', [None, [], ['А'], ['А ', '', 'Б', 'А', 'Б '],
                                  ['А ', 2, 'Б', 'А', 'Б '],
                                  ['А ', '\n', 'Б', 'А', 'Б '],
                                  ['А ', '\r', 'Б', 'А', 'Б '],
                                  ['А ', ' ', 'Б', 'А', 'А ']])
def test_runs_require_exact_nonempty_fragments_reproducing_canonical_translation(runs):
    values = inputs()
    values[3]['runs'] = runs
    with pytest.raises(ValueError):
        patch(values)


@pytest.mark.parametrize('other', [{'lines': ['А', 'Б']}, {'center': True}, {'wrap': True}])
def test_runs_reject_conflicting_layout_modes(other):
    values = inputs()
    values[3].update(other)
    with pytest.raises(ValueError):
        patch(values)


@pytest.mark.parametrize('drift', ['count', 'source', 'height', 'mixed_font', 'missing_font'])
def test_runs_guard_original_records_fonts_and_heights(drift):
    values = inputs()
    if drift == 'count':
        values[1]['text_records'].pop()
    elif drift == 'source':
        values[1]['text_records'][1]['source'] = 'A'
    elif drift == 'missing_font':
        values[4].clear()
    else:
        text = parse_text(values[0].payload)
        row = text['records'][1]
        row.update(flags=0x88, font_id=255 if drift == 'mixed_font' else 1,
                   height=2048 if drift == 'mixed_font' else 1024)
        if drift == 'mixed_font':
            values[4][255] = dict(values[4][1], id=255)
        values = (Tag(11, build_text(text)), *values[1:])
    with pytest.raises(ValueError):
        patch(values)


def test_single_record_runs_take_explicit_recipe_path():
    values = inputs(runs=(' А ',))
    changed, _ = patch(values)
    text = parse_text(changed.payload)
    assert text['bounds'] == [1110, 1120, 680, 700]
    values[3]['runs'] = ['А']
    with pytest.raises(ValueError):
        patch(values)


def test_runs_reject_scaled_matrix():
    values = inputs()
    text = parse_text(values[0].payload)
    text['matrix_raw'] = encode_matrix({'scale': [131072, 65536]})
    values = (Tag(11, build_text(text)), *values[1:])
    with pytest.raises(ValueError, match='matrix'):
        patch(values)


def anchored_inputs(count=1, inherited=False):
    """An invented icon occupies a source gap from local x=120 to x=190."""
    source = font_fixture()
    source.update(id=1, codes=[32, 65, 66],
                  glyphs=[build_shape([])] + source['glyphs'],
                  bounds=[[0, 0, 0, 0]] + source['bounds'],
                  advances=[100, 200, 300])
    spaces = [(0, 70)] if count == 1 else [(0, 40), (0, 30)]
    first = {'flags': 0x8f, 'font_id': 1, 'height': 2048,
             'x': 100, 'y': 200, 'color': b'\x01\x02\x03',
             'entries': [(1, 20)] + spaces + [(2, 30)]}
    rows = [first]
    runs = ['А' + ' ' * count + 'Б']
    record_index = 0
    source_offset = translation_offset = 1
    if inherited:
        first['entries'] = [(1, 20)]
        rows.append({'flags': 0x80, 'entries': spaces + [(2, 30)]})
        runs = ['А', ' ' * count + 'Б']
        record_index, source_offset, translation_offset = 1, 0, 0
    text = {'id': 7, 'bounds': [1100, 1400, 680, 700],
            'matrix_raw': encode_matrix({'translate': [1000, 500]}),
            'gb': 2, 'ab': 8, 'records': rows}
    tag = Tag(11, build_text(text))
    raw = swf([Tag(75, build_font(source)), tag])
    entry = extract_inventory(raw, hashlib.sha256(raw).hexdigest())['entries'][0]
    entry['translation'] = ''.join(runs)
    target = inputs()[2]
    layout = {'font_id': 1, 'height': 2048, 'max_width': 300,
              'runs': runs, 'space_anchors': [{
                  'record_index': record_index, 'source_offset': source_offset,
                  'translation_offset': translation_offset, 'count': count}]}
    return tag, entry, target, layout, {1: source}


@pytest.mark.parametrize('count', [1, 2])
@pytest.mark.parametrize('inherited', [False, True])
def test_space_anchor_keeps_original_icon_gap_end(count, inherited):
    values = anchored_inputs(count=count, inherited=inherited)
    changed, _ = patch(values)
    after = parse_text(changed.payload)
    row = after['records'][int(inherited)]
    expected = [70, 30] if count == 1 else [60, 10, 30]
    if not inherited:
        expected.insert(0, 20)
    assert [advance for _, advance in row['entries']] == expected
    assert after['bounds'] == [1100, 1200, 680, 700]
    assert ''.join(chr(values[2]['codes'][index])
                   for record in after['records'] for index, _ in record['entries']) == values[1]['translation']
    assert patch(values)[0].payload == changed.payload


@pytest.mark.parametrize(('field', 'value'), [
    ('record_index', True), ('record_index', -1), ('record_index', 4),
    ('record_index', '0'), ('source_offset', True), ('source_offset', -1),
    ('source_offset', 20), ('translation_offset', True),
    ('translation_offset', -1), ('translation_offset', 20),
    ('count', True), ('count', 0), ('count', -1), ('count', 3),
])
def test_space_anchor_rejects_invalid_indices_and_counts(field, value):
    values = anchored_inputs()
    values[3]['space_anchors'][0][field] = value
    with pytest.raises(ValueError):
        patch(values)


@pytest.mark.parametrize('side', ['source_offset', 'translation_offset'])
def test_space_anchor_requires_literal_spaces_on_both_sides(side):
    values = anchored_inputs()
    values[3]['space_anchors'][0][side] = 0
    with pytest.raises(ValueError):
        patch(values)


@pytest.mark.parametrize('side', ['source', 'target'])
def test_space_anchor_requires_outline_free_space_glyphs(side):
    values = anchored_inputs()
    font = values[4][1] if side == 'source' else values[2]
    font['glyphs'][font['codes'].index(32)] = font['glyphs'][font['codes'].index(65)]
    with pytest.raises(ValueError):
        patch(values)


@pytest.mark.parametrize('offset', [0, 1])
def test_space_anchor_rejects_duplicate_and_overlapping_ranges(offset):
    values = anchored_inputs(count=2)
    anchor = values[3]['space_anchors'][0]
    values[3]['space_anchors'].append(dict(anchor, source_offset=1 + offset,
                                          translation_offset=1 + offset, count=1))
    with pytest.raises(ValueError):
        patch(values)


def test_space_anchor_rejects_translated_prefix_advance_into_icon_gap():
    values = anchored_inputs()
    values[2]['advances'][values[2]['codes'].index(ord('А'))] = 300
    with pytest.raises(ValueError):
        patch(values)


def test_space_anchor_rejects_translated_prefix_ink_into_icon_gap():
    values = anchored_inputs()
    index = values[2]['codes'].index(ord('А'))
    values[2]['glyphs'][index] = build_shape([
        [('M', 0, 0), ('L', 300, -200), ('L', 0, 0)]])
    with pytest.raises(ValueError):
        patch(values)


def test_space_anchor_rejects_negative_adjusted_first_space_advance():
    values = anchored_inputs(count=2)
    # Source gap ends at190. The remaining second space alone advances100,
    # so no nonnegative first-space advance can preserve that end.
    values[2]['advances'][values[2]['codes'].index(32)] = 1000
    with pytest.raises(ValueError):
        patch(values)


def test_space_anchor_rejects_suffix_left_bearing_into_icon_gap():
    values = anchored_inputs()
    index = values[2]['codes'].index(ord('Б'))
    values[2]['glyphs'][index] = build_shape([
        [('M', -200, -200), ('L', 0, -200), ('L', 0, 0),
         ('L', -200, 0), ('L', -200, -200)]])
    values[2]['bounds'][index] = [-200, 0, -200, 0]
    with pytest.raises(ValueError, match='gap'):
        patch(values)


@pytest.mark.parametrize('side', ['previous', 'following'])
def test_space_anchor_guards_other_inline_records_on_the_same_baseline(side):
    values = anchored_inputs(inherited=side == 'previous')
    if side == 'following':
        parsed = parse_text(values[0].payload)
        parsed['records'][0]['entries'] = parsed['records'][0]['entries'][:-1]
        parsed['records'].append({'flags': 0x80, 'entries': [(2, 30)]})
        tag = Tag(11, build_text(parsed))
        import hashlib
        raw = swf([Tag(75, build_font(values[4][1])), tag])
        entry = extract_inventory(raw, hashlib.sha256(raw).hexdigest())['entries'][0]
        entry['translation'] = values[1]['translation']
        values = (tag, entry, *values[2:])
        values[3]['runs'] = ['А ', 'Б']
    char = 'А' if side == 'previous' else 'Б'
    index = values[2]['codes'].index(ord(char))
    lo, hi = (0, 400) if side == 'previous' else (-200, 0)
    values[2]['glyphs'][index] = build_shape([
        [('M', lo, -200), ('L', hi, -200), ('L', hi, 0),
         ('L', lo, 0), ('L', lo, -200)]])
    values[2]['bounds'][index] = [lo, hi, -200, 0]
    with pytest.raises(ValueError, match='gap'):
        patch(values)


def right_edge_inputs():
    values = inputs(runs=('АА', 'ББ'))
    text = parse_text(values[0].payload)
    text['bounds'][1] = 1170
    return (Tag(11, build_text(text)), *values[1:])


def test_explicit_right_edge_allows_verified_expansion_in_post_matrix_space():
    values = right_edge_inputs()
    values[3]['right_edge'] = 1200
    changed, _ = patch(values)
    after = parse_text(changed.payload)
    assert after['bounds'] == [1100, 1180, 680, 700]
    assert after['matrix_raw'] == parse_text(values[0].payload)['matrix_raw']


def test_explicit_right_edge_does_not_override_cumulative_max_width():
    values = right_edge_inputs()
    values[3].update(right_edge=1200, max_width=70)
    with pytest.raises(ValueError, match='width'):
        patch(values)


@pytest.mark.parametrize('right_edge', [True, '1200', -1, 0, 1169, 1200.0])
def test_explicit_right_edge_rejects_invalid_or_shrinking_limits(right_edge):
    values = right_edge_inputs()
    values[3]['runs'] = ['А', 'Б']
    values[1]['translation'] = 'АБ'
    values[3]['right_edge'] = right_edge
    with pytest.raises(ValueError):
        patch(values)
