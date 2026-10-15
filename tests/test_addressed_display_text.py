"""Synthetic static/edit text recipes; no game content."""
import copy
import hashlib
import struct

import pytest

from addressed_localization import patch_selected_tags
from inventory_strings import extract_inventory, parse_edit_text
from localization_workspace import initialize_workspace
from swf_geometry import Tag, build_font, build_text, encode_matrix, encode_rect, parse_text, read_swf
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


@pytest.mark.parametrize('edit', [False, True])
def test_layout_cannot_claim_wrapping_absent_in_resource(edit):
    values = inputs(edit)
    values[2]['layouts'][values[1]['entries'][0]['id']]['wrap'] = True
    with pytest.raises(ValueError, match='wrap|single line'):
        run(values)
