"""Synthetic static/edit text recipes; no game content."""
import copy
import hashlib
import struct

import pytest

from addressed_localization import patch_selected_tags
from inventory_strings import extract_inventory, parse_edit_text
from localization_workspace import initialize_workspace
from swf_geometry import Tag, build_font, build_text, encode_matrix, encode_rect, parse_font, parse_text, read_swf
from test_localization_fonts import extend_font, font_fixture
from test_string_inventory import swf


def inputs(edit=False):
    font = font_fixture()
    source = '<p><font face="Synthetic" size="12">A</font></p>' if edit else 'A'
    if edit:
        payload = struct.pack('<H', 7) + encode_rect([0, 2000, -100, 600])
        payload += struct.pack('>H', 0x8103) + struct.pack('<HH', 3, 240)
        payload += b'variable\0' + source.encode() + b'\0'
        tag = Tag(37, payload)
    else:
        tag = Tag(11, build_text({'id': 7, 'bounds': [0, 2000, -100, 600],
            'matrix_raw': encode_matrix({}), 'gb': 1, 'ab': 8,
            'records': [{'flags': 0x8f, 'font_id': 3, 'height': 240,
                         'x': 20, 'y': 200, 'color': b'\x01\x02\x03', 'entries': [(0, 20)]}]}))
    raw = swf([Tag(75, build_font(font)), tag, Tag(77, b'Untouched')])
    digest = hashlib.sha256(raw).hexdigest()
    workspace = initialize_workspace(extract_inventory(raw, digest))
    entry = workspace['entries'][0]
    entry.update(translation=source.replace('>A<', '>А<') if edit else 'А',
                 classification='confirmed', context_confidence='confirmed', status='draft')
    plan = {'schema_version': 1, 'game_sha256': digest, 'entry_ids': [entry['id']],
            'fonts': [3], 'layouts': {entry['id']: {'font_id': 3, 'height': 240,
            'max_width': 1800, 'wrap': False, 'max_lines': 1}}}
    replacement = extend_font(copy.deepcopy(font), {'А': {'donor': 'A'}})
    replacement.update(id=6001, name=b'New Family')
    return raw, workspace, plan, replacement, digest


def run(values):
    raw, workspace, plan, font, digest = values
    return patch_selected_tags(raw, workspace, plan, None,
                               expected_sha256=digest, replacement_fonts={3: font})


def test_static_addressed_glyphs_keep_style_and_unrelated_resources():
    values = inputs()
    _, tags, report = run(values)
    old = read_swf(values[0])[1]
    assert tags[0] == old[0] and tags[2:] == old[2:]
    text = parse_text(tags[1].payload)
    row = text['records'][0]
    assert row['font_id'] == 6001 and row['color'] == b'\x01\x02\x03'
    assert row['height'] == 240 and row['y'] == 200
    assert values[3]['codes'][row['entries'][0][0]] == ord('А')
    assert run(values)[1] == tags
    assert report['applied_translations'] == 1


def test_edit_html_changes_only_selected_font_and_visible_text():
    values = inputs(True)
    _, tags, _ = run(values)
    _, source, formatting = parse_edit_text(tags[1].payload)
    assert source == '<p><font face="New Family" size="12">А</font></p>'
    assert formatting['font_id'] == 6001 and formatting['variable_name'] == 'variable'
    assert tags[0] == read_swf(values[0])[1][0]


def test_html_style_or_structure_changes_fail_closed():
    values = inputs(True)
    values[1]['entries'][0]['translation'] = '<p><font face="Synthetic" size="13">А</font></p>'
    with pytest.raises(ValueError, match='HTML'):
        run(values)


def test_static_multiple_records_need_explicit_run_recipe():
    values = inputs()
    raw, workspace, plan, font, digest = values
    tags = read_swf(raw)[1]
    text = parse_text(tags[1].payload)
    text['records'].append(dict(text['records'][0], y=400))
    tags[1] = Tag(11, build_text(text))
    raw = swf(tags[:-1])
    digest = hashlib.sha256(raw).hexdigest()
    workspace = initialize_workspace(extract_inventory(raw, digest))
    workspace['entries'][0].update(translation='АА', classification='confirmed', context_confidence='confirmed', status='draft')
    plan['game_sha256'] = digest
    with pytest.raises(ValueError, match='record'):
        run((raw, workspace, plan, font, digest))


def multiline_inputs(lines=('А', 'Б'), center=False, matrix=None, bounds=None):
    font = font_fixture()
    records = [
        {'flags': 0x8f, 'font_id': 3, 'height': 240, 'x': 20, 'y': 200,
         'color': b'\x01\x02\x03', 'entries': [(0, 20)]},
        {'flags': 0x83, 'x': 40, 'y': 400, 'entries': [(1, 30)]},
    ]
    if len(lines) == 1:
        records = records[:1]
    text = {'id': 7, 'bounds': bounds or [20, 2000, 100, 600],
            'matrix_raw': encode_matrix(matrix or {}), 'gb': 1, 'ab': 8,
            'records': records}
    raw = swf([Tag(75, build_font(font)), Tag(11, build_text(text)), Tag(77, b'Untouched')])
    digest = hashlib.sha256(raw).hexdigest()
    workspace = initialize_workspace(extract_inventory(raw, digest))
    entry = workspace['entries'][0]
    translation = ' '.join(lines)
    entry.update(translation=translation, classification='confirmed',
                 context_confidence='confirmed', status='draft')
    donors = {char: {'donor': 'A'} for char in dict.fromkeys(translation.replace(' ', ''))}
    replacement = extend_font(copy.deepcopy(font), donors)
    replacement.update(id=6001, name=b'New Family')
    layout = {'font_id': 3, 'height': 240, 'max_width': 1800, 'wrap': False}
    if len(lines) > 1:
        layout['lines'] = list(lines)
    if center:
        layout['center'] = True
    plan = {'schema_version': 1, 'game_sha256': digest, 'entry_ids': [entry['id']],
            'fonts': [3], 'layouts': {entry['id']: layout}}
    return raw, workspace, plan, replacement, digest


def test_static_multi_record_lines_keep_positions_style_and_unrelated():
    values = multiline_inputs()
    _, tags, report = run(values)
    old = read_swf(values[0])[1]
    assert tags[0] == old[0] and tags[2:] == old[2:]
    text = parse_text(tags[1].payload)
    first, second = text['records']
    assert first['font_id'] == 6001 and first['color'] == b'\x01\x02\x03'
    assert (first['x'], first['y']) == (20, 200) and (second['x'], second['y']) == (40, 400)
    assert 'font_id' not in second
    codes = values[3]['codes']
    assert codes[first['entries'][0][0]] == ord('А')
    assert codes[second['entries'][0][0]] == ord('Б')
    assert run(values)[1] == tags
    assert report['layout_checks']['static.7']['lines'] == 2


def test_static_multi_record_lines_must_reproduce_translation():
    values = multiline_inputs()
    values[2]['layouts']['static.7']['lines'] = ['А', 'А']
    with pytest.raises(ValueError, match='lines'):
        run(values)


def test_static_multi_record_reject_blank_or_padded_lines():
    values = multiline_inputs()
    values[2]['layouts']['static.7']['lines'] = ['А ', 'Б']
    with pytest.raises(ValueError, match='record'):
        run(values)


def test_static_line_cannot_pass_original_right_edge():
    values = multiline_inputs(lines=('АБ', 'Б'), bounds=[20, 21, 100, 600])
    values[2]['layouts']['static.7']['max_width'] = 1800
    with pytest.raises(ValueError, match='width'):
        run(values)


def test_static_center_keeps_post_matrix_ink_center():
    from build_prototype import ink_bounds
    values = multiline_inputs(lines=('АБ',), center=True, matrix={'translate': [500, 100]})
    old_text = parse_text(read_swf(values[0])[1][1].payload)
    old_row = old_text['records'][0]
    source_font = parse_font(read_swf(values[0])[1][0].payload)
    old_ink = ink_bounds(source_font, old_row['entries'], 240, old_row.get('x', 0), 0)
    _, tags, _ = run(values)
    new_text = parse_text(tags[1].payload)
    new_row = new_text['records'][0]
    new_ink = ink_bounds(values[3], new_row['entries'], 240,
                         new_row.get('x', 0), new_row.get('y', 0))
    assert abs(sum(new_ink[:2]) - sum(old_ink[:2])) <= 2
    # TextBounds stay in the character definition space: local ink plus the
    # unchanged matrix translation, exactly as the original tags encode it.
    assert new_text['bounds'] == [new_ink[0] + 500, new_ink[1] + 500,
                                  new_ink[2] + 100, new_ink[3] + 100]
    assert new_text['matrix_raw'] == old_text['matrix_raw']


@pytest.mark.parametrize('xmax,ok', [(342, False), (343, True)])
def test_static_right_edge_compared_in_original_bounds_space(xmax, ok):
    from build_prototype import ink_bounds
    values = multiline_inputs(lines=('АБ', 'Б'), bounds=[20, xmax, 100, 600],
                              matrix={'translate': [300, 0]})
    if ok:
        _, tags, _ = run(values)
        text = parse_text(tags[1].payload)
        union = [ink_bounds(values[3], row['entries'], 240, row.get('x', 0), row.get('y', 0))
                 for row in text['records']]
        assert text['bounds'] == [min(b[0] for b in union) + 300, max(b[1] for b in union) + 300,
                                  min(b[2] for b in union), max(b[3] for b in union)]
        assert text['bounds'][1] <= xmax
    else:
        with pytest.raises(ValueError, match='width'):
            run(values)


def test_static_center_keeps_each_record_ink_center():
    values = multiline_inputs(center=True)
    old_text = parse_text(read_swf(values[0])[1][1].payload)
    _, tags, _ = run(values)
    new_text = parse_text(tags[1].payload)
    # Donor glyphs reuse the source contours, so centred records keep their x.
    assert [row.get('x') for row in new_text['records']] == [20, 40]
    assert [row.get('y') for row in new_text['records']] == [200, 400]
    assert new_text['records'][1]['entries'][0][0] != old_text['records'][1]['entries'][0][0]


def test_static_recipe_rejects_scaled_matrix():
    values = multiline_inputs(matrix={'scale': [131072, 65536]})
    with pytest.raises(ValueError, match='matrix'):
        run(values)


def test_static_center_requires_x_offset_flag():
    values = multiline_inputs(lines=('АБ',), center=True)
    raw, workspace, plan, font, digest = values
    tags = read_swf(raw)[1]
    text = parse_text(tags[1].payload)
    record = dict(text['records'][0], flags=0x8e)  # font, color, height: no x
    del record['x']
    text['records'] = [record]
    tags[1] = Tag(11, build_text(text))
    raw = swf(tags[:-1])
    digest = hashlib.sha256(raw).hexdigest()
    workspace = initialize_workspace(extract_inventory(raw, digest))
    workspace['entries'][0].update(translation='АБ', classification='confirmed',
                                   context_confidence='confirmed', status='draft')
    plan['game_sha256'] = digest
    with pytest.raises(ValueError, match='x'):
        run((raw, workspace, plan, font, digest))


def test_static_mixed_record_fonts_fail_closed():
    values = multiline_inputs()
    raw, workspace, plan, font, digest = values
    tags = read_swf(raw)[1]
    other = copy.deepcopy(parse_font(read_swf(raw)[1][0].payload))
    other['id'] = 9
    text = parse_text(tags[1].payload)
    text['records'][1] = dict(text['records'][1], flags=0x8b, font_id=9, height=240)
    tags[1] = Tag(11, build_text(text))
    tags.insert(1, Tag(75, build_font(other)))
    raw = swf(tags[:-1])
    digest = hashlib.sha256(raw).hexdigest()
    workspace = initialize_workspace(extract_inventory(raw, digest))
    workspace['entries'][0].update(translation='А Б', classification='confirmed',
                                   context_confidence='confirmed', status='draft')
    plan['game_sha256'] = digest
    with pytest.raises(ValueError, match='font'):
        run((raw, workspace, plan, font, digest))


def test_edit_layout_rejects_static_only_keys():
    values = inputs(True)
    values[2]['layouts'][values[1]['entries'][0]['id']]['center'] = True
    with pytest.raises(ValueError, match='static-only'):
        run(values)


@pytest.mark.parametrize('edit', [False, True])
def test_layout_cannot_claim_wrapping_absent_in_resource(edit):
    values = inputs(edit)
    values[2]['layouts'][values[1]['entries'][0]['id']]['wrap'] = True
    with pytest.raises(ValueError, match='wrap|single line'):
        run(values)
