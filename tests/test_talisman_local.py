"""Opt-in offline Talisman cumulative checks; never launches the game."""
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCInventory
from abc_patch import patch_pushstrings
from addressed_localization import check_layout
from alegreya_localization import build_alegreya_localization, tooltip_layout_repair
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from inventory_strings import extract_inventory
from localization_plan import merge_localization_plans, resolve_plan_font_bindings
from swf_geometry import Reader, parse_font, read_swf, read_tags
from translation_catalog import load_catalog

pytestmark = pytest.mark.local

VARIABLES = [
    'GEMCRAFT_ORIGINAL', 'GEMCRAFT_PR4_WORKSPACE', 'GEMCRAFT_PR4_PLANS',
    'GEMCRAFT_PR5_TUTOR_PLAN', 'GEMCRAFT_PR6_OUTCOME_PLAN',
    'GEMCRAFT_PR7_SKILLS_PLAN', 'GEMCRAFT_PR8_TALISMAN_PLAN',
    'GEMCRAFT_ALEGREYA_FONT',
]
PR7_SHA256 = 'a8c06b90aefad99b3050ff6d751389522cbadafe21f9c0bde5fbf54362518951'
EMPTY_SUFFIX = 'abc.4175.957.1117'
NEW_METHODS = {1104, 1125, 1126, 1127, 1131, 1132, 1133, 1147, 1148}
NEW_BINDING_COUNTS = {
    1104: 2, 1125: 12, 1126: 8, 1127: 4, 1131: 12,
    1132: 8, 1133: 0, 1147: 6, 1148: 6,
}
PANEL_METHOD_CALLS = {1125: 6, 1126: 4, 1127: 2, 1131: 6, 1132: 4, 1147: 3, 1148: 3}
METHOD_ENTRY_COUNTS = {1104: 2, 1125: 5, 1126: 4, 1127: 2, 1131: 7,
                       1132: 2, 1133: 36, 1147: 3, 1148: 3}
PROPERTY_PARTS = {
    0: ('abc.4175.1133.45',), 1: ('abc.4175.1133.66',),
    2: ('abc.4175.1133.87',), 3: ('abc.4175.1133.108',),
    4: ('abc.4175.1133.129',), 5: ('abc.4175.1133.150',),
    6: ('abc.4175.1133.171',), 7: ('abc.4175.1133.199',),
    8: ('abc.4175.1133.227',), 9: ('abc.4175.1133.255',),
    10: ('abc.4175.1133.283',), 11: ('abc.4175.1133.311',),
    12: ('abc.4175.1133.339',), 13: ('abc.4175.1133.367',),
    14: ('abc.4175.1133.395',), 15: ('abc.4175.1133.423',),
    16: ('abc.4175.1133.451',), 17: ('abc.4175.1133.479',),
    18: ('abc.4175.1133.507',), 19: ('abc.4175.1133.535',),
    20: ('abc.4175.1133.563',), 21: ('abc.4175.1133.591',),
    22: ('abc.4175.1133.619',), 23: ('abc.4175.1133.647',),
    24: ('abc.4175.1133.675',), 25: ('abc.4175.1133.703',),
    26: ('abc.4175.1133.731',), 27: ('abc.4175.1133.759',),
    28: ('abc.4175.1133.787',), 29: ('abc.4175.1133.814',),
    30: ('abc.4175.1133.842',), 31: ('abc.4175.1133.870',),
    32: ('abc.4175.1133.898',), 33: ('abc.4175.1133.926',),
    34: ('abc.4175.1133.939', 'abc.4175.1133.954'),
}


def abc_bytes(tags):
    tag = next(tag for tag in tags if tag.code == 82)
    return tag.payload[tag.payload.index(b'\0', 4) + 1:]


def abc_inventory(data):
    inventory = ABCInventory(data, {'tag_path': [4175], 'tag_code': 82})
    inventory.parse()
    return inventory


def resolved_operations(inventory, operations):
    return [
        (offset, opcode, inventory.strings[args[0]] if opcode == 0x2c else args)
        for offset, opcode, args in operations
    ]


def sprite_characters(tags, sprite_id):
    sprites = {int.from_bytes(tag.payload[:2], 'little'): tag for tag in tags if tag.code == 39}
    text_ids = {int.from_bytes(tag.payload[:2], 'little')
                for tag in tags if tag.code in (11, 33, 37)}

    def direct(character):
        sprite = sprites[character]
        reader = Reader(sprite.payload)
        assert reader.u16() == character
        declared = reader.u16()
        children = read_tags(reader.take(len(reader.data) - reader.pos))
        result = set()
        frames = 0
        for child in children:
            if child.code in (26, 70):
                inner = Reader(child.payload)
                flags = inner.u8()
                flags2 = inner.u8() if child.code == 70 else 0
                inner.u16()
                if child.code == 70 and (flags2 & 8 or flags2 & 16 and not flags & 2):
                    end = inner.data.find(b'\0', inner.pos)
                    assert end >= 0
                    inner.pos = end + 1
                if flags & 2:
                    result.add(inner.u16())
            elif child.code == 1:
                frames += 1
        assert frames == declared
        return result

    characters = set()
    visited_sprites = set()

    def visit(character):
        if character in visited_sprites:
            return
        visited_sprites.add(character)
        characters.add(character)
        for placed in direct(character):
            characters.add(placed)
            if placed in sprites:
                visit(placed)

    visit(sprite_id)
    return characters, characters & text_ids


def symbol_class(tag):
    reader = Reader(tag.payload)
    result = {}
    for _ in range(reader.u16()):
        character = reader.u16()
        end = reader.data.find(b'\0', reader.pos)
        assert end >= 0
        result[character] = reader.data[reader.pos:end].decode('utf-8')
        reader.pos = end + 1
    reader.finish()
    return result


def by_character(tags):
    return {int.from_bytes(tag.payload[:2], 'little'): tag for tag in tags
            if tag.code in (11, 33, 37, 75)}


def format_number(value):
    return f'{value:,}'


def test_talisman_cumulative_candidate(tmp_path):
    if not all(os.environ.get(name) for name in VARIABLES):
        pytest.skip('Set private original, workspace, PR4-PR8 plans and Alegreya source')
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    workspace = load_catalog(os.environ['GEMCRAFT_PR4_WORKSPACE'])
    entries = {entry['id']: entry for entry in workspace['entries']}
    plans = [load_catalog(path) for path in json.loads(os.environ['GEMCRAFT_PR4_PLANS'])]
    tutor = load_catalog(os.environ['GEMCRAFT_PR5_TUTOR_PLAN'])
    outcome = load_catalog(os.environ['GEMCRAFT_PR6_OUTCOME_PLAN'])
    skills = load_catalog(os.environ['GEMCRAFT_PR7_SKILLS_PLAN'])
    talisman_path = Path(os.environ['GEMCRAFT_PR8_TALISMAN_PLAN'])
    talisman = load_catalog(talisman_path)
    font = Path(os.environ['GEMCRAFT_ALEGREYA_FONT']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    baseline_plans = plans + [tutor, outcome, skills]
    baseline, base_report = build_alegreya_localization(original, workspace, baseline_plans, font)
    assert sha(baseline) == PR7_SHA256
    candidate, report = build_alegreya_localization(
        original, workspace, baseline_plans + [talisman], font)
    again, repeated = build_alegreya_localization(
        original, workspace, baseline_plans + [talisman], font)
    assert candidate == again and report == repeated
    assert sha(candidate) != PR7_SHA256
    assert base_report['applied_translations'] == 719
    assert report['applied_translations'] == 783
    assert len(talisman['entry_ids']) == 64
    assert talisman.get('empty_abc_strings', []) == []
    assert report['empty_abc_strings'] == [EMPTY_SUFFIX]
    assert set(base_report['applied_entry_ids']) <= set(report['applied_entry_ids'])
    assert all(report['layout_checks'][identifier] == base_report['layout_checks'][identifier]
               for identifier in base_report['layout_checks'])
    assert Counter(item['tag_code'] for item in report['changed_original_tags']) == {11: 96, 37: 24, 82: 1}
    inherited = merge_localization_plans(baseline_plans, workspace)
    merged = merge_localization_plans(baseline_plans + [talisman], workspace)
    assert merged['coverage_blockers'][:len(inherited['coverage_blockers'])] == inherited['coverage_blockers']
    assert len(merged['coverage_blockers']) == len(inherited['coverage_blockers']) + 1
    assert talisman['coverage_blockers'] == [merged['coverage_blockers'][-1]]
    assert 'abc.4175.1144.70/.89' in merged['coverage_blockers'][-1]

    original_tags = read_swf(original)[1]
    old_tags, new_tags = read_swf(baseline)[1], read_swf(candidate)[1]
    assert [(tag.code, tag.payload) for tag in old_tags if tag.code != 82] == [
        (tag.code, tag.payload) for tag in new_tags if tag.code != 82]
    original_inventory_data = extract_inventory(original, ORIGINAL_SHA256)
    original_entries = {entry['id']: entry for entry in original_inventory_data['entries']}
    placements = original_inventory_data['placements']
    assert [item for item in placements if item['character_id'] == 565] == [{
        'tag_code': 70, 'tag_path': [629, 2], 'sprite_path': [566],
        'character_id': 565, 'depth': 3, 'frame': 0,
    }]
    assert [item for item in placements if item['character_id'] == 3088] == [{
        'tag_code': 70, 'tag_path': [3235, 2], 'sprite_path': [3089],
        'character_id': 3088, 'depth': 2, 'frame': 0,
    }]
    talisman_buttons = [item for item in placements if item['character_id'] == 566
                        and item['sprite_path'] == [3011]]
    assert {(tuple(item['tag_path']), item['depth']) for item in talisman_buttons} == {
        ((3144, 5), 8), ((3144, 6), 12)}
    characters, text_characters = sprite_characters(original_tags, 3011)
    assert 3011 in characters and text_characters == {565}
    assert symbol_class(original_tags[4176])[3011] == (
        'com.giab.games.gccs.steam.mcStat.McPnlTalisman')
    assert entries['edit.565']['translation'] is None
    assert entries['edit.3088']['translation'] is None
    assert '9.999.999' in entries['edit.3088']['source']
    assert 'edit.565' not in talisman['entry_ids'] and 'edit.3088' not in talisman['entry_ids']
    assert 'edit.3077' in base_report['applied_entry_ids']
    assert 'edit.3077' not in talisman['entry_ids']

    selected_abc = set(talisman['entry_ids'])
    assert Counter(entries[identifier]['location']['method_index']
                   for identifier in selected_abc) == METHOD_ENTRY_COUNTS
    for identifier in selected_abc:
        source, translation = entries[identifier]['source'], entries[identifier]['translation']
        assert source.count('%') == translation.count('%')
        assert source.count('\n') == translation.count('\n')
        assert source.startswith(' ') == translation.startswith(' ')
        assert source.endswith(' ') == translation.endswith(' ')

    original_abc = abc_bytes(original_tags)
    original_inventory = abc_inventory(original_abc)
    original_codes = {method: code for method, _, code in original_inventory.bodies}
    new_abc = abc_bytes(new_tags)
    new_inventory = abc_inventory(new_abc)
    old_bodies, new_bodies = _bodies(abc_bytes(old_tags)), _bodies(new_abc)
    new_codes = {method: code for method, _, code in new_inventory.bodies}
    for method, expected_calls in PANEL_METHOD_CALLS.items():
        operations = original_inventory.instructions(original_codes[method], method)
        calls = [operation for operation in operations
                 if operation[1] == 0x4f and operation[2][0] == 3729]
        assert len(calls) == expected_calls

    selected_entries = [entries[identifier] for identifier in report['applied_entry_ids']
                        if entries[identifier]['location'].get('method_index') is not None]
    selected_entries.append({**original_entries[EMPTY_SUFFIX], 'translation': ''})
    by_site = {
        (entry['location']['method_index'], entry['location']['code_offset']): entry
        for entry in selected_entries
    }
    expected_by_method = defaultdict(set)
    expected_indices = {}
    original_string_count = len(original_inventory.strings)
    for ordinal, site in enumerate(sorted(by_site)):
        method, _offset = site
        index = original_string_count + ordinal
        assert new_inventory.strings[index] == by_site[site]['translation']
        expected_by_method[method].add(index)
        expected_indices[site] = index
    for method, expected in expected_by_method.items():
        operations = new_inventory.instructions(new_codes[method], method)
        actual = {args[0] for _, opcode, args in operations
                  if opcode == 0x2c and args[0] >= original_string_count}
        assert expected <= actual

    translated_abc = patch_pushstrings(original_abc, selected_entries)
    translated_inventory = abc_inventory(translated_abc)
    family_index = len(translated_inventory.strings)
    font_index = len(translated_inventory.multinames)
    assert new_inventory.strings[family_index] == 'GCCS Alegreya'
    assert new_inventory.strings[family_index + 1] == 'font'
    assert new_inventory.multinames[font_index] == {'kind': 7, 'name': family_index + 1}
    snippets, _, _ = resolve_plan_font_bindings(
        original_abc, translated_abc, merged['abc_font_bindings'])
    resolved_snippets = snippets(family_index, font_index)
    for method in sorted(NEW_METHODS):
        method_snippets = resolved_snippets.get(method, [])
        assert len(method_snippets) == NEW_BINDING_COUNTS[method]
        assert len(new_codes[method]) == len(original_codes[method]) + sum(
            len(snippet) for _, snippet in method_snippets)
        operations = {
            offset: (opcode, args)
            for offset, opcode, args in new_inventory.instructions(new_codes[method], method)
        }
        for offset, snippet in method_snippets:
            relocated = offset + sum(
                len(other) for at, other in method_snippets if at < offset)
            assert new_codes[method][relocated:relocated + len(snippet)] == snippet
        for (candidate_method, offset), index in expected_indices.items():
            if candidate_method != method:
                continue
            relocated = offset + sum(
                len(snippet) for at, snippet in method_snippets if at <= offset)
            assert operations[relocated] == (0x2c, [index])

    old_inventory = abc_inventory(abc_bytes(old_tags))
    assert old_bodies[79][3].count(tooltip_layout_repair()) == 1
    assert new_bodies[79][3].count(tooltip_layout_repair()) == 1
    for method, old in old_bodies.items():
        if method in NEW_METHODS:
            continue
        new = new_bodies[method]
        old_operations = old_inventory.instructions(old[3], method)
        new_operations = new_inventory.instructions(new[3], method)
        assert resolved_operations(old_inventory, old_operations) == resolved_operations(
            new_inventory, new_operations)
        assert old[2] == new[2] and old[4:] == new[4:]
    for method, count in NEW_BINDING_COUNTS.items():
        assert report['abc_font_binding_sites'].get(str(method), 0) == count
    for method in (1128, 1144, 353, 1732):
        assert new_bodies[method][2:] == old_bodies[method][2:]

    matrix = json.loads(talisman_path.with_name('coverage-matrix.json').read_text())
    preserved_ids = {*matrix['preserved']['no_op'], matrix['preserved']['diagnostic_error'],
                     *matrix['preserved']['refund']}
    assert len(preserved_ids) == 37
    for identifier in preserved_ids:
        entry = entries[identifier]
        method, offset = entry['location']['method_index'], entry['location']['code_offset']
        method_snippets = resolved_snippets.get(method, [])
        relocated = offset + sum(len(snippet) for at, snippet in method_snippets if at <= offset)
        old_operation = next((opcode, args) for at, opcode, args in original_inventory.instructions(
            original_codes[method], method) if at == offset)
        new_operation = next((opcode, args) for at, opcode, args in new_inventory.instructions(
            new_codes[method], method) if at == relocated)
        assert old_operation == new_operation == (0x2c, old_operation[1])
        assert new_inventory.strings[new_operation[1][0]] == entry['source']
    empty_original = next(args for at, opcode, args in original_inventory.instructions(
        original_codes[1133], 1133) if at == 800 and opcode == 0x2c)
    empty_new = next(args for at, opcode, args in new_inventory.instructions(
        new_codes[1133], 1133) if at == 800 and opcode == 0x2c)
    assert empty_original == empty_new and new_inventory.strings[empty_new[0]] == ''
    drop_original = next((opcode, args) for at, opcode, args in original_inventory.instructions(
        original_codes[556], 556) if at == 485)
    assert drop_original[0] == 0x4f
    assert original_inventory.multiname(drop_original[1][0]) == 'renderInfoPanelFragment'

    old_chars, new_chars = by_character(old_tags), by_character(new_tags)
    assert set(old_chars) == set(new_chars)
    for character, tag in old_chars.items():
        assert new_chars[character].payload == tag.payload
    for tag in original_tags:
        if tag.code == 75:
            identifier = int.from_bytes(tag.payload[:2], 'little')
            assert new_chars[identifier].payload == tag.payload
    new_fonts = {identifier: parse_font(tag.payload)
                 for identifier, tag in new_chars.items() if tag.code == 75}

    operations1133 = original_inventory.instructions(original_codes[1133], 1133)
    for property_id, parts in PROPERTY_PARTS.items():
        suffix = entries[parts[-1]]
        index = next(i for i, operation in enumerate(operations1133)
                     if operation[0] == suffix['location']['code_offset'])
        window = operations1133[max(0, index - 7):index]
        has_formatter = any(opcode == 0x46 and args == [4755, 1]
                            for _, opcode, args in window)
        assert has_formatter == (property_id >= 7)
        if property_id <= 6:
            assert any(opcode == 0xd2 for _, opcode, _ in window)

    def property_text(property_id, value):
        parts = PROPERTY_PARTS[property_id]
        formatted = str(value) if property_id <= 6 else format_number(value)
        if property_id == 34:
            return entries[parts[0]]['translation'] + formatted + entries[parts[1]]['translation']
        prefix = '' if property_id == 29 else '+'
        return prefix + formatted + entries[parts[0]]['translation']

    validation = json.loads(talisman_path.with_name('layout-validation.json').read_text())
    assert validation['property_sample_count'] == 420
    assert validation['concatenated_sample_count'] == 446
    assert validation['text_widths_twips']['m1125'] == 7600
    assert validation['text_widths_twips']['m1126'] == 6800
    assert validation['text_widths_twips']['m1127'] == 6000
    assert validation['text_widths_twips']['m1131/m1132/m1133'] == 7600
    assert validation['text_widths_twips']['m1147/m1148'] == 8600
    samples = validation['concatenated_samples']
    expected_property_samples = []
    for property_id in PROPERTY_PARTS:
        for value in (1, 2, 5, 21, 1000, 1234567):
            text = property_text(property_id, value)
            for face in (6001, 6002):
                expected_property_samples.append((text, face))
    assert [(sample['text'], sample['font']) for sample in samples[:420]] == expected_property_samples
    assert property_text(0, 1000).startswith('+1000')
    assert property_text(7, 1000).startswith('+1,000')
    assert not property_text(29, 1000).startswith('+')
    assert property_text(34, 1000).startswith(entries['abc.4175.1133.939']['translation'])
    for sample in samples:
        check_layout(new_fonts[sample['font']], sample['text'], {
            'height': sample['height'], 'max_width': sample['max_width'],
            'wrap': True, 'max_lines': sample['max_lines'],
        })
    for identifier in ('abc.4175.1147.99', 'abc.4175.1148.99'):
        layout = talisman['layouts'][identifier]
        assert layout['height'] == 240 and layout['max_width'] == 8600
        check_layout(new_fonts[6002], entries[identifier]['translation'], layout)
    for identifier in ('abc.4175.1104.36', 'abc.4175.1104.53'):
        assert talisman['layouts'][identifier]['max_width'] == 3124
        check_layout(new_fonts[6002], entries[identifier]['translation'], talisman['layouts'][identifier])

    game = tmp_path / 'game' / 'gc-cs-steam.swf'
    game.parent.mkdir()
    game.write_bytes(original)
    old_path, new_path = tmp_path / 'old.swf', tmp_path / 'new.swf'
    old_path.write_bytes(baseline)
    new_path.write_bytes(candidate)
    old_manifest = apply_patch_file(game, old_path, tmp_path / 'backup', ORIGINAL_SHA256, sha(baseline))
    restore_patch_file(old_manifest)
    new_manifest = apply_patch_file(game, new_path, tmp_path / 'backup', ORIGINAL_SHA256, sha(candidate))
    assert game.read_bytes() == candidate
    restore_patch_file(new_manifest)
    assert game.read_bytes() == original
    apply_patch_file(game, old_path, tmp_path / 'backup', ORIGINAL_SHA256, sha(baseline))
    assert game.read_bytes() == baseline
    assert Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes() == original
