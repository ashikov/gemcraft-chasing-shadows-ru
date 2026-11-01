"""Opt-in offline pause/outcome cumulative checks; never launches the game."""
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCInventory
from abc_patch import patch_pushstrings
from addressed_localization import check_layout
from alegreya_localization import build_alegreya_localization
from build_prototype import ink_bounds
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from inventory_strings import parse_edit_text
from localization_plan import merge_localization_plans, resolve_plan_font_bindings
from swf_geometry import Reader, parse_font, parse_text, read_swf, read_tags
from translation_catalog import load_catalog

pytestmark = pytest.mark.local

VARIABLES = [
    'GEMCRAFT_ORIGINAL', 'GEMCRAFT_PR4_WORKSPACE', 'GEMCRAFT_PR4_PLANS',
    'GEMCRAFT_PR5_TUTOR_PLAN', 'GEMCRAFT_PR6_OUTCOME_PLAN',
    'GEMCRAFT_ALEGREYA_FONT',
]
# Current canonical first four plans include later layout/binding repairs.
PRE_OUTCOME_SHA256 = 'c7bfb5e30dd0ca6bfe5df5b94aca88eabaef3e558e920b1300840a238931f504'
TOOLTIP_METHODS = (880, 881, 882, 887, 888, 889, 890, 891)
NEW_METHODS = set(TOOLTIP_METHODS) | {895, 3451}
NEW_BINDING_COUNTS = {
    880: 48, 881: 34, 882: 18, 887: 6, 888: 56,
    889: 50, 890: 30, 891: 12, 895: 3, 3451: 3,
}
NUMERIC_EDITS = {f'edit.{number}' for number in (
    3738, 3739, 3740, 3741, 3743, 3746, 3748, 3750, 3752,
    3754, 3756, 3758, 3760, 3762, 3764, 3766, 3768,
)}


def abc_bytes(tags):
    tag = next(tag for tag in tags if tag.code == 82)
    return tag.payload[tag.payload.index(b'\0', 4) + 1:]


def abc_inventory(data):
    inventory = ABCInventory(data, {'tag_path': [4175], 'tag_code': 82})
    inventory.parse()
    return inventory


def resolved_static(tag, fonts):
    text = parse_text(tag.payload)
    rows = []
    active_font = active_height = None
    for record in text['records']:
        active_font = record.get('font_id', active_font)
        active_height = record.get('height', active_height)
        rows.append((
            ''.join(chr(fonts[active_font]['codes'][index]) for index, _ in record['entries']),
            tuple(advance for _, advance in record['entries']),
            record.get('x'), record.get('y'), active_height, record.get('color'),
        ))
    return text['bounds'], text['matrix_raw'], rows


def placed_text_frames(tags, sprite_id):
    sprite = next(tag for tag in tags if tag.code == 39
                  and int.from_bytes(tag.payload[:2], 'little') == sprite_id)
    reader = Reader(sprite.payload)
    assert reader.u16() == sprite_id
    declared = reader.u16()
    children = read_tags(reader.take(len(reader.data) - reader.pos))
    text_ids = {int.from_bytes(tag.payload[:2], 'little')
                for tag in tags if tag.code in (11, 33, 37)}
    frames, current = [], set()
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
                character = inner.u16()
                if character in text_ids:
                    current.add(character)
        elif child.code == 1:
            frames.append(current)
            current = set()
    assert len(frames) == declared
    return frames


def resolved_operations(inventory, operations):
    return [
        (offset, opcode, inventory.strings[args[0]] if opcode == 0x2c else args)
        for offset, opcode, args in operations
    ]


def test_outcome_cumulative_candidate(tmp_path):
    if not all(os.environ.get(name) for name in VARIABLES):
        pytest.skip('Set private original, workspace, PR4-PR6 plans and Alegreya source')
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    workspace = load_catalog(os.environ['GEMCRAFT_PR4_WORKSPACE'])
    entries = {entry['id']: entry for entry in workspace['entries']}
    plans = [load_catalog(path) for path in json.loads(os.environ['GEMCRAFT_PR4_PLANS'])]
    tutor = load_catalog(os.environ['GEMCRAFT_PR5_TUTOR_PLAN'])
    outcome = load_catalog(os.environ['GEMCRAFT_PR6_OUTCOME_PLAN'])
    font = Path(os.environ['GEMCRAFT_ALEGREYA_FONT']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    baseline_plans = plans + [tutor]
    baseline, base_report = build_alegreya_localization(original, workspace, baseline_plans, font)
    assert sha(baseline) == PRE_OUTCOME_SHA256
    candidate, report = build_alegreya_localization(
        original, workspace, baseline_plans + [outcome], font)
    again, repeated = build_alegreya_localization(
        original, workspace, baseline_plans + [outcome], font)
    assert candidate == again and report == repeated
    assert base_report['applied_translations'] == 440
    assert report['applied_translations'] == 602
    assert len(outcome['entry_ids']) == 162
    assert set(base_report['applied_entry_ids']) <= set(report['applied_entry_ids'])
    assert all(report['layout_checks'][identifier] == base_report['layout_checks'][identifier]
               for identifier in base_report['layout_checks'])
    assert Counter(item['tag_code'] for item in report['changed_original_tags']) == {11: 93, 22: 1, 37: 24, 39: 1, 82: 1}
    inherited = merge_localization_plans(baseline_plans, workspace)['coverage_blockers']
    merged = merge_localization_plans(baseline_plans + [outcome], workspace)
    assert merged['coverage_blockers'][:len(inherited)] == inherited
    assert len(merged['coverage_blockers']) == len(inherited) + 1

    original_tags = read_swf(original)[1]
    old_tags, new_tags = read_swf(baseline)[1], read_swf(candidate)[1]

    # Independently re-derive all direct text placements in the pause and outcome sprites.
    assert placed_text_frames(original_tags, 591) == [{589, 590}]
    outcome_frames = placed_text_frames(original_tags, 3787)
    assert len(outcome_frames) == 1 and len(outcome_frames[0]) == 40
    placed_outcome = outcome_frames[0]
    selected_display = {identifier for identifier in outcome['entry_ids']
                        if identifier.startswith(('static.', 'edit.'))}
    assert len(selected_display) == 23
    covered_outcome = {
        *(int(identifier.split('.')[1]) for identifier in selected_display
          if identifier not in ('edit.589', 'edit.590')),
        3744, 3733, *(int(identifier.split('.')[1]) for identifier in NUMERIC_EDITS),
    }
    assert covered_outcome == placed_outcome
    assert 'static.3744' not in outcome['entry_ids']
    assert 'edit.3733' not in outcome['entry_ids']
    assert 'edit.565' not in outcome['entry_ids']

    # Every pushstring in the eight outcome tooltip methods is covered; the
    # technical X5 comparison and no-op separator in endGame are not patched.
    original_abc = abc_bytes(original_tags)
    original_inventory = abc_inventory(original_abc)
    original_codes = {method: code for method, _, code in original_inventory.bodies}
    selected_abc = {identifier for identifier in outcome['entry_ids']
                    if identifier.startswith('abc.')}
    assert len(selected_abc) == 139
    for method in TOOLTIP_METHODS + (3451,):
        operations = original_inventory.instructions(original_codes[method], method)
        expected = {f'abc.4175.{method}.{offset}'
                    for offset, opcode, _ in operations if opcode == 0x2c}
        assert expected == {identifier for identifier in selected_abc
                            if entries[identifier]['location']['method_index'] == method}
    assert {identifier for identifier in selected_abc
            if entries[identifier]['location']['method_index'] == 895} == {
        f'abc.4175.895.{offset}'
        for offset in (243, 258, 304, 339, 358, 361, 364, 1270, 1343)
    }
    assert 'abc.4175.895.1096' not in selected_abc
    assert 'abc.4175.895.354' not in selected_abc

    new_abc = abc_bytes(new_tags)
    new_inventory = abc_inventory(new_abc)
    old_bodies, new_bodies = _bodies(abc_bytes(old_tags)), _bodies(new_abc)
    new_codes = {method: code for method, _, code in new_inventory.bodies}

    # Verify every selected ABC literal, including all earlier stages, through
    # the deterministic appended-pool ordinal and exact relocated addresses.
    selected_entries = [entries[identifier] for identifier in report['applied_entry_ids']
                        if entries[identifier]['location'].get('method_index') is not None]
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
        method_snippets = resolved_snippets[method]
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

    x5_index = entries['abc.4175.895.1096']['location']['string_pool_index']
    operations895 = new_inventory.instructions(new_codes[895], 895)
    assert any(opcode == 0x2c and args == [x5_index]
               for _, opcode, args in operations895)
    separator_index = entries['abc.4175.895.354']['location']['string_pool_index']
    assert any(opcode == 0x2c and args == [separator_index]
               for _, opcode, args in operations895)
    assert new_inventory.strings[x5_index] == 'X5'
    assert new_inventory.strings[separator_index] == ' - '

    # RUNTIME-001 and every unrelated method survive. Resolved literals are
    # compared because new strings shift the family index in earlier bindings.
    from alegreya_localization import tooltip_layout_repair
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
        assert report['abc_font_binding_sites'][str(method)] == count

    def by_character(tags):
        return {int.from_bytes(tag.payload[:2], 'little'): tag for tag in tags
                if tag.code in (11, 33, 37, 75)}

    original_chars = by_character(original_tags)
    old_chars, new_chars = by_character(old_tags), by_character(new_tags)
    old_fonts = {identifier: parse_font(tag.payload)
                 for identifier, tag in old_chars.items() if tag.code == 75}
    new_fonts = {identifier: parse_font(tag.payload)
                 for identifier, tag in new_chars.items() if tag.code == 75}
    new_statics = {int(identifier.split('.')[1]) for identifier in selected_display
                   if identifier.startswith('static.')}
    new_edits = {int(identifier.split('.')[1]) for identifier in selected_display
                 if identifier.startswith('edit.')}

    # New static labels keep their own ink centre, baseline, colour and height.
    for character in sorted(new_statics):
        identifier = f'static.{character}'
        old_text = parse_text(original_chars[character].payload)
        new_text = parse_text(new_chars[character].payload)
        old_row, new_row = old_text['records'][0], new_text['records'][0]
        old_font = parse_font(original_chars[255].payload)
        new_font = new_fonts[6002]
        old_ink = ink_bounds(old_font, old_row['entries'], old_row['height'],
                             old_row.get('x', 0), old_row.get('y', 0))
        new_ink = ink_bounds(new_font, new_row['entries'], new_row['height'],
                             new_row.get('x', 0), new_row.get('y', 0))
        assert abs(sum(old_ink[:2]) - sum(new_ink[:2])) <= 2
        assert new_row.get('y') == old_row.get('y')
        assert new_row.get('color') == old_row.get('color')
        assert new_row['height'] == old_row['height']
        assert new_text['matrix_raw'] == old_text['matrix_raw']
        assert resolved_static(new_chars[character], new_fonts)[2][0][0] == entries[identifier]['translation']

    for character in sorted(new_edits):
        identifier = f'edit.{character}'
        _, source, formatting = parse_edit_text(new_chars[character].payload)
        expected = re.sub(r' face="[^"]+"', ' face="GCCS Alegreya"',
                          entries[identifier]['translation'], count=1)
        assert source == expected
        assert formatting['font_id'] == (6001 if character in (589, 590) else 6002)
        _, _, original_formatting = parse_edit_text(original_chars[character].payload)
        assert formatting['bounds'] == original_formatting['bounds']
        assert formatting['word_wrap'] == original_formatting['word_wrap']

    # Untouched character resources stay identical to PR5; previous static
    # records are compared by Unicode because repertoire growth may reindex them.
    for character, tag in old_chars.items():
        if character in new_statics or character in new_edits or character in (6001, 6002):
            continue
        if tag.code in (11, 33):
            assert resolved_static(tag, old_fonts) == resolved_static(new_chars[character], new_fonts)
        else:
            assert new_chars[character] == tag
    for identifier in ('static.3744', 'edit.3733', 'edit.565', *NUMERIC_EDITS):
        character = int(identifier.split('.')[1])
        assert new_chars[character] == original_chars[character]

    for identifier in (6001, 6002):
        old, new = old_fonts[identifier], new_fonts[identifier]
        assert len(new['codes']) >= len(old['codes'])
        for code in old['codes']:
            old_index, new_index = old['codes'].index(code), new['codes'].index(code)
            assert old['glyphs'][old_index] == new['glyphs'][new_index]
            assert old['advances'][old_index] == new['advances'][new_index]
            assert old['bounds'][old_index] == new['bounds'][new_index]
    for tag in original_tags:
        if tag.code == 75:
            identifier = int.from_bytes(tag.payload[:2], 'little')
            assert new_chars[identifier].payload == tag.payload

    # Assemble every numeric tooltip with a large formatted value and measure
    # the actual candidate faces without the reserved suffix budget.
    dynamic = [identifier for identifier in selected_abc
               if outcome['layouts'][identifier].get('dynamic_suffix_budget') == 3000]
    assert len(dynamic) == 102
    for identifier in dynamic:
        layout = dict(outcome['layouts'][identifier], dynamic_suffix_budget=0)
        translation = entries[identifier]['translation']
        assert translation.endswith(': ')
        text = translation + '2,147,483,647'
        for original_font, embedded_font in ((1, 6001), (255, 6002)):
            assert original_font in layout['alternate_font_ids'] or original_font == layout['font_id']
            check_layout(new_fonts[embedded_font], text, layout)

    field_layout = {'height': 240, 'max_width': 17919, 'wrap': False, 'max_lines': 1}
    field_texts = [
        entries['abc.4175.895.304']['translation'] + 'A1',
        entries['abc.4175.895.243']['translation'] + 'X5'
        + entries['abc.4175.895.258']['translation'],
    ]
    for offset in (358, 361, 364):
        field_texts.append(
            entries['abc.4175.895.339']['translation'] + 'F7 - '
            + entries[f'abc.4175.895.{offset}']['translation'])
    for text in field_texts:
        check_layout(new_fonts[6002], text, field_layout)

    # Install/restore transition rehearsal on temp copies only.
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
