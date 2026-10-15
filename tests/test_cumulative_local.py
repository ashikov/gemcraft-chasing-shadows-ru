"""Opt-in offline cumulative candidate, comparison and transition on temp copies."""
import os
from pathlib import Path

import pytest

from alegreya_localization import build_alegreya_localization
from abc_font_binding import _bodies
from abc_inventory import ABCInventory
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from localization_plan import merge_localization_plans
from swf_geometry import parse_font, parse_text, read_swf
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


def abc_bytes(tags):
    tag = next(t for t in tags if t.code == 82)
    return tag.payload[tag.payload.index(b'\0', 4) + 1:]


def test_private_cumulative_candidate_preserves_settings_and_transition(tmp_path):
    variables = ['GEMCRAFT_ORIGINAL', 'GEMCRAFT_PR4_WORKSPACE', 'GEMCRAFT_PR4_PLANS', 'GEMCRAFT_ALEGREYA_FONT']
    if not all(os.environ.get(v) for v in variables):
        pytest.skip('Set private original, workspace, JSON plan-path list and Alegreya source')
    import json
    original = Path(os.environ[variables[0]]).read_bytes()
    workspace = load_catalog(os.environ[variables[1]])
    plans = [load_catalog(p) for p in json.loads(os.environ[variables[2]])]
    font = Path(os.environ[variables[3]]).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    merged = merge_localization_plans(plans, workspace)
    assert set(plans[0]['entry_ids']) <= set(merged['entry_ids'])
    previous, old_report = build_alegreya_localization(original, workspace, plans[0], font)
    candidate, report = build_alegreya_localization(original, workspace, plans, font)
    again, repeated = build_alegreya_localization(original, workspace, plans, font)
    assert candidate == again and report == repeated
    assert report['applied_translations'] == sum(len(p['entry_ids']) for p in plans)
    assert all(report['layout_checks'][i] == old_report['layout_checks'][i]
               for i in old_report['layout_checks'])
    old_tags = read_swf(previous)[1]
    new_tags = read_swf(candidate)[1]
    # Settings geometry, prior font insertions and resource declarations survive;
    # appended pool indices are compared through their resolved literals.
    old_title = next(t for t in old_tags if t.code == 11 and int.from_bytes(t.payload[:2], 'little') == 3016)
    new_title = next(t for t in new_tags if t.code == 11 and int.from_bytes(t.payload[:2], 'little') == 3016)
    old_fonts = {int.from_bytes(t.payload[:2], 'little'): parse_font(t.payload) for t in old_tags if t.code == 75}
    new_fonts = {int.from_bytes(t.payload[:2], 'little'): parse_font(t.payload) for t in new_tags if t.code == 75}
    def resolved_title(tag, fonts):
        text = parse_text(tag.payload)
        row = text['records'][0]
        row['entries'] = [(fonts[row['font_id']]['codes'][i], advance) for i, advance in row['entries']]
        return text['bounds'], text['matrix_raw'], row
    assert resolved_title(old_title, old_fonts) == resolved_title(new_title, new_fonts)
    for identifier in (6001, 6002):
        old, new = old_fonts[identifier], new_fonts[identifier]
        for code in old['codes']:
            a, b = old['codes'].index(code), new['codes'].index(code)
            assert old['glyphs'][a] == new['glyphs'][b]
            assert old['advances'][a] == new['advances'][b]
            assert old['bounds'][a] == new['bounds'][b]
    before_abc, after_abc = abc_bytes(old_tags), abc_bytes(new_tags)
    old_bodies, new_bodies = _bodies(before_abc), _bodies(after_abc)
    new_methods = {s['method_index'] for p in plans[1:] for s in p.get('abc_font_bindings', [])}
    new_methods.update(e['location']['method_index'] for e in workspace['entries']
                       if e['id'] in set(merged['entry_ids']) - set(plans[0]['entry_ids'])
                       and 'method_index' in e['location'])
    # Pool operands for existing settings translations remain stable: sorted
    # addressed sites for new screens may change appended indices, so compare
    # resolved instruction literals while retaining all other instructions.
    inventories = []
    for raw in (before_abc, after_abc):
        inv = ABCInventory(raw, {'tag_path': [4175], 'tag_code': 82})
        inv.parse()
        inventories.append(inv)
    for method, old in old_bodies.items():
        if method in new_methods:
            continue
        new = new_bodies[method]
        old_ops = inventories[0].instructions(old[3], method)
        new_ops = inventories[1].instructions(new[3], method)
        def resolved(inv, operations):
            return [(offset, opcode, inv.strings[args[0]] if opcode == 0x2c else args)
                    for offset, opcode, args in operations]
        assert resolved(inventories[0], old_ops) == resolved(inventories[1], new_ops)
        assert old[2] == new[2] and old[4:] == new[4:]
    game = tmp_path / 'game' / 'candidate.swf'
    game.parent.mkdir()
    game.write_bytes(original)
    old_path, new_path = tmp_path / 'old.swf', tmp_path / 'new.swf'
    old_path.write_bytes(previous)
    new_path.write_bytes(candidate)
    old_manifest = apply_patch_file(game, old_path, tmp_path / 'backup', ORIGINAL_SHA256, sha(previous))
    restore_patch_file(old_manifest)
    new_manifest = apply_patch_file(game, new_path, tmp_path / 'backup', ORIGINAL_SHA256, sha(candidate))
    assert game.read_bytes() == candidate
    restore_patch_file(new_manifest)
    assert game.read_bytes() == original
    apply_patch_file(game, old_path, tmp_path / 'backup', ORIGINAL_SHA256, sha(previous))
    assert game.read_bytes() == previous
    assert Path(os.environ[variables[0]]).read_bytes() == original
