"""Inventory contracts use generated SWF structures, never game assets."""
import hashlib
import struct

import pytest

from inventory_strings import extract_inventory, inventory_report
from swf_geometry import Tag, build_font, build_shape, build_text, encode_matrix, encode_rect, write_swf


def swf(tags):
    prefix = b'FWS\x0a\0\0\0\0' + encode_rect([0, 100, 0, 100]) + b'\0\x18\1\0'
    return write_swf(prefix, tags + [Tag(0, b'')], 10)


def text_tags():
    glyph = build_shape([[('M', 0, 0), ('L', 1, 1), ('L', 0, 0)]])
    font = {'id': 4, 'flags': 0, 'language': 0, 'name': b'Synthetic',
            'codes': [65, 66], 'glyphs': [glyph, glyph]}
    text = {'id': 7, 'bounds': [0, 100, 0, 100], 'matrix_raw': encode_matrix({}),
            'gb': 2, 'ab': 8, 'records': [
                {'flags': 0x88, 'font_id': 4, 'height': 100, 'entries': [(0, 20)]},
                {'flags': 0x83, 'x': 10, 'y': 20, 'entries': [(1, 30)]}]}
    return [Tag(75, build_font(font)), Tag(33, build_text(text, 33))]


def extract(raw):
    return extract_inventory(raw, hashlib.sha256(raw).hexdigest())


def test_recursive_static_preserves_records_and_placements():
    font, text = text_tags()
    sprite = Tag(39, struct.pack('<HH', 9, 1) + struct.pack('<H', 33 << 6 | 63) +
                 struct.pack('<I', len(text.payload)) + text.payload + b'\0\0')
    raw = swf([font, sprite, Tag(26, b'\x02\x01\x00\x09\x00')])
    data = extract(raw)
    assert data == extract(raw)
    entry = data['entries'][0]
    assert entry['id'] == 'static.7'
    assert entry['source'] == 'AB'
    assert entry['location']['sprite_path'] == [9]
    assert [r['source'] for r in entry['text_records']] == ['A', 'B']
    assert entry['text_records'][1]['y'] == 20
    assert entry['text_records'][1]['advances'] == [30]
    assert entry['location']['font_ids'] == [4]
    assert data['placements'][0]['character_id'] == 9


def test_edit_text_html_and_dynamic_fields():
    # HasText | WordWrap | HasFont | HasColor | HasMaxLength; HTML | HasLayout.
    flags = 0x8000 | 0x4000 | 0x0100 | 0x0400 | 0x0200 | 0x0002 | 0x0020
    payload = struct.pack('<H', 12) + encode_rect([0, 80, 0, 40]) + struct.pack('>H', flags)
    payload += struct.pack('<HH', 4, 200) + b'\x01\x02\x03\xff' + struct.pack('<H', 90)
    payload += b'\x01' + struct.pack('<HHHh', 1, 2, 3, -4) + b'field\0<b>A&amp;B</b>\n\0'
    empty = struct.pack('<H', 13) + encode_rect([0, 80, 0, 40]) + b'\0\0dynamic\0'
    data = extract(swf([Tag(37, payload), Tag(37, empty)]))
    html, dynamic = data['entries']
    assert html['source'] == '<b>A&amp;B</b>\n'
    assert html['category'] == 'html'
    assert html['format']['font_id'] == 4
    assert html['format']['leading'] == -4
    assert html['format']['html'] is True
    assert dynamic['source'] == ''
    assert dynamic['classification'] == 'unknown'
    assert dynamic['format']['variable_name'] == 'dynamic'


@pytest.mark.parametrize('tag', [Tag(37, b'\x01'), Tag(39, b'\x01'), Tag(82, b'\0\0')])
def test_truncated_structures_fail(tag):
    with pytest.raises(ValueError):
        extract(swf([tag]))


def test_duplicate_character_and_missing_font_fail():
    tags = text_tags()
    with pytest.raises(ValueError, match='Duplicate'):
        extract(swf(tags + tags[1:]))
    with pytest.raises(ValueError, match='font'):
        extract(swf(tags[1:]))


def test_hash_checked_before_parsing():
    with pytest.raises(ValueError, match='SHA-256'):
        extract_inventory(b'bad', 'a' * 64)


def test_report_separates_unknown_and_counts_repetitions():
    data = extract(swf(text_tags()))
    copy = dict(data['entries'][0], id='static.8')
    data['entries'].append(copy)
    report = inventory_report(data)
    assert report['total_records'] == 2
    assert report['unique_sources'] == 1
    assert report['repeated_records'] == 1
    assert report['characters_to_translate']['confirmed'] == 4
    assert 'sources' not in report


def test_placeobject3_image_with_character_has_no_class_name():
    # SWF19 erratum: image+character does not add a ClassName STRING.
    raw = swf([Tag(70, b'\x06\x10\x01\x00\x09\x00\0')])
    assert extract(raw)['placements'][0]['character_id'] == 9


def test_font_class_edit_text_includes_height():
    flags = 0x8000 | 0x0080
    payload = struct.pack('<H', 12) + encode_rect([0, 80, 0, 40]) + struct.pack('>H', flags)
    payload += b'SyntheticFont\0' + struct.pack('<H', 240) + b'variable\0A\0'
    entry = extract(swf([Tag(37, payload)]))['entries'][0]
    assert entry['source'] == 'A'
    assert entry['format']['font_height'] == 240


def test_different_character_ids_keep_identical_text_separate():
    font, text = text_tags()
    second = Tag(33, struct.pack('<H', 8) + text.payload[2:])
    data = extract(swf([font, text, second]))
    assert [e['id'] for e in data['entries']] == ['static.7', 'static.8']
    assert [e['source'] for e in data['entries']] == ['AB', 'AB']


def test_unsupported_resources_are_reported_not_invented():
    data = extract(swf([Tag(12, b'\0')]))
    assert data['entries'] == []
    assert data['coverage']['unsupported_resources'] == [{'tag_code': 12, 'tag_path': [0]}]


def test_inventory_cli_rejects_unapproved_original_before_writing(tmp_path):
    from extract_strings import main
    path = tmp_path / 'synthetic.swf'
    raw = swf(text_tags())
    path.write_bytes(raw)
    output = tmp_path / 'inventory.json'
    with pytest.raises(SystemExit) as exc:
        main([str(path), '--original-sha256', hashlib.sha256(raw).hexdigest(),
              '--inventory', '--output', str(output)])
    assert exc.value.code == 1
    assert not output.exists()
    assert path.read_bytes() == raw


def test_report_estimates_html_text_without_counting_tags():
    data = extract(swf(text_tags()))
    entry = data['entries'][0]
    entry.update(category='html', source='<b>A&amp;B</b>', source_sha256=hashlib.sha256(b'<b>A&amp;B</b>').hexdigest())
    report = inventory_report(data)
    assert report['characters_to_translate']['confirmed'] == 3
    assert report['words_to_translate']['confirmed'] == 2
    assert report['raw_characters']['confirmed'] == 14
