"""Opt-in original-source and actual loading-field checks for all 60 advices."""
import copy
import json
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from advice_oracle import (
    ADVICE_ID, DECODED_SOURCE_SHA256, PACKED_SOURCE_SHA256, check_advice_layout,
    decode_advices, encode_advices, prepare_advice_replacement, unpack_advice_entry,
)
from alegreya_fonts import convert_font
from install_patch import ORIGINAL_SHA256, sha
from inventory_strings import parse_edit_text
from localization_plan import resolve_plan_font_bindings, apply_plan_font_bindings
from swf_geometry import read_swf
from test_battle_hud_local import preserve_method
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local
PLAN = 'translations/plans/pr34-advices.json'


@pytest.fixture(scope='module')
def advice_artifacts():
    if not os.environ.get('GEMCRAFT_ORIGINAL') or not os.environ.get('GEMCRAFT_ALEGREYA_FONT'):
        pytest.skip('Set GEMCRAFT_ORIGINAL and GEMCRAFT_ALEGREYA_FONT')
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    workspace_path = os.environ.get('GEMCRAFT_PR34_WORKSPACE')
    if not workspace_path:
        pytest.skip('Set GEMCRAFT_PR34_WORKSPACE to a fresh translated workspace')
    rows = {row['id']: row for row in load_catalog(workspace_path)['entries']}
    entry = rows[ADVICE_ID]
    font = Path(os.environ['GEMCRAFT_ALEGREYA_FONT']).read_bytes()
    assert sha(font) == 'ba5564634b93a8f8ba57b48cd4f1ae7417d2b4656fbac779028679b00de3cf12'
    return original, entry, font, load_catalog(PLAN)


def test_real_payload_algorithm_order_controls_and_no_original_pool_mutation(advice_artifacts):
    original, entry, _, _ = advice_artifacts
    raw = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(raw)
    original_row = next(row for row in inv.entries if row['id'] == ADVICE_ID)
    # Inventory integration may already expose this literal as readable text.
    if original_row.get('content_type') == 'packed_advices':
        original_row = copy.deepcopy(original_row)
        original_row['source'] = encode_advices(original_row['source'].split('\r'))
    decoded = unpack_advice_entry(original_row)
    assert decoded['source'] == entry['source']
    assert decoded['source_sha256'] == DECODED_SOURCE_SHA256
    prepared = prepare_advice_replacement(entry)
    assert sha(prepared['source'].encode()) == PACKED_SOURCE_SHA256
    before = copy.deepcopy(entry)
    translated = patch_pushstrings(raw, [prepared])
    assert entry == before
    after = abc_inventory(translated)
    old, new = _bodies(raw), _bodies(translated)
    assert all(old[m][2:] == new[m][2:] for m in old if m != 1441)
    assert after.strings[:len(inv.strings)] == inv.strings
    ops = after.instructions(new[1441][3], 1441)
    literal = next(after.strings[a[0]] for _, op, a in ops if op == 0x2c)
    assert decode_advices(literal) == entry['translation'].split('\r')
    assert len(decode_advices(literal)) == 60
    assert entry['source'].count('\r') == entry['translation'].count('\r') == 59
    assert encode_advices(decode_advices(prepared['source'])) == prepared['source']
    # The actual StringToolbox helper uncompresses its ByteArray and reads UTF-8.
    helper = inv.instructions(old[1284][3], 1284)
    assert [(at, inv.multiname(args[0]), args[1]) for at, op, args in helper
            if op in (0x46, 0x4f)] == [(25, 'base64DecodeByteArray', 1),
                                      (35, 'uncompress', 0), (46, 'readUTFBytes', 1)]


def test_all_advice_consumers_and_early_font_binding_keep_selector_unchanged(advice_artifacts):
    original, entry, _, plan = advice_artifacts
    raw = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(raw)
    bodies = _bodies(raw)
    consumers = []
    for method, body in bodies.items():
        for at, opcode, args in inv.instructions(body[3], method):
            if opcode in (0x46, 0x4f) and inv.multiname(args[0]) == 'giveAdvice':
                consumers.append((method, at, args[1]))
    assert consumers == [(465, 1147, 0)]
    decoder = inv.instructions(bodies[1442][3], 1442)
    assert next(inv.strings[a[0]] for at, op, a in decoder if at == 57 and op == 0x2c) == '\r'
    assert any(at == 60 and op == 0x46 and inv.multiname(a[0]) == 'split'
               for at, op, a in decoder)
    translated = patch_pushstrings(raw, [prepare_advice_replacement(entry)])
    bound, _ = apply_plan_font_bindings(raw, translated, plan['abc_font_bindings'])
    after = abc_inventory(bound)
    make, increments, _ = resolve_plan_font_bindings(raw, translated, plan['abc_font_bindings'])
    family = after.strings.index('GCCS Alegreya')
    qname = next(i for i in range(1, len(after.multinames)) if after.multiname(i) == 'font')
    snippets = make(family, qname)
    preserve_method(abc_inventory(translated), after, 465, snippets[465], increments[465])
    assert _bodies(bound)[1442][2:] == bodies[1442][2:]
    assert _bodies(bound)[1284][2:] == bodies[1284][2:]
    assert _bodies(bound)[3533][2:] == bodies[3533][2:]
    site = plan['abc_font_bindings'][0]
    assert site['kind'] == 'field'
    assert inv.instructions(bodies[465][3],465)[site['instruction_index']][0] == 1132
    assert site['receiver_hex'] == '60e60166ac0566f11d66fd18'
    assert len(snippets[465]) == 1


def test_actual_loading_field_wraps_every_advice_at_all_requested_scales(advice_artifacts):
    original, entry, ttf, plan = advice_artifacts
    tags = read_swf(original)[1]
    field = next(parse_edit_text(tag.payload)[2] for tag in tags if tag.code == 37
                 and int.from_bytes(tag.payload[:2], 'little') == 2013)
    assert field['bounds'] == [-3399, 12080, -40, 1092]
    assert field['font_id'] == 1 and field['font_height'] == 320
    assert field['word_wrap'] and field['multiline'] and not field['auto_size']
    assert field['leading'] == 40 and field['left_margin'] == field['right_margin'] == 0
    layout = plan['layouts'][ADVICE_ID]
    assert layout['height'] == field['font_height']
    width = field['bounds'][1] - field['bounds'][0]
    height = field['bounds'][3] - field['bounds'][2]
    assert layout['max_width'] <= width - 80
    assert layout['max_height'] <= height - 80
    characters = {char for char in entry['translation'] if ord(char) >= 32}
    font = convert_font(ttf, 400, characters, 6001, 'GCCS Alegreya')
    metrics = check_advice_layout(font, entry['translation'], layout)
    assert metrics['record_count'] == 60
    assert max(row['lines'] for row in metrics['records']) <= 2
    # Account separately for the authored 2px leading and 2px TextField padding.
    line_height = (font['ascent'] + font['descent'] + font['leading']) * 320 / 20480
    max_height = max(row['lines'] * line_height + (row['lines'] - 1) * field['leading']
                     for row in metrics['records'])
    assert max_height + 80 <= height
    scaled = []
    for scale in (1, .75, .5):
        for index, row in enumerate(metrics['records']):
            widest = max(row['widths_twips'])
            assert widest * scale <= (width - 80) * scale
            assert (max_height + 80) * scale <= height * scale
            scaled.append({'record_index': index, 'scale': scale,
                           'lines': row['lines'], 'width_px': widest / 20 * scale,
                           'field_width_px': width / 20 * scale,
                           'worst_height_px': max_height / 20 * scale,
                           'field_height_px': height / 20 * scale})
    evidence_dir = os.environ.get('GEMCRAFT_PR34_ADVICE_EVIDENCE')
    if evidence_dir:
        Path(evidence_dir, 'geometry.json').write_text(json.dumps(scaled, ensure_ascii=False, indent=2))
