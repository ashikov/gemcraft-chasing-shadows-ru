"""Opt-in offline Skills cumulative checks; never launches the game."""
import json
import math
import os
import struct
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCInventory, ABCReader
from abc_patch import patch_pushstrings
from addressed_localization import check_layout
from alegreya_localization import build_alegreya_localization, tooltip_layout_repair
from build_prototype import ink_bounds
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from inventory_strings import extract_inventory
from localization_plan import merge_localization_plans, resolve_plan_font_bindings
from swf_geometry import decode_matrix, parse_font, parse_text, read_swf
from translation_catalog import load_catalog

pytestmark = pytest.mark.local

VARIABLES = [
    'GEMCRAFT_ORIGINAL', 'GEMCRAFT_PR4_WORKSPACE', 'GEMCRAFT_PR4_PLANS',
    'GEMCRAFT_PR5_TUTOR_PLAN', 'GEMCRAFT_PR6_OUTCOME_PLAN',
    'GEMCRAFT_PR7_SKILLS_PLAN', 'GEMCRAFT_ALEGREYA_FONT',
]
PR6_SHA256 = '82f9c998faa1d2503f327d997ee2b13a433d0966127a408710b8b08c7b2fa300'
STATIC_IDS = {'static.1555', 'static.1557', 'static.1560'}
EMPTY_SUFFIX = 'abc.4175.957.1117'
NO_OP = 'abc.4175.957.1066'
NEW_METHODS = {950, 955, 956, 957, 556, 3481}
NEW_BINDING_COUNTS = {950: 0, 955: 10, 956: 22, 957: 34, 556: 2, 3481: 3}
EXPECTED_PLACEMENTS = {
    1555: ([1672, 1], [1559]),
    1557: ([1672, 3], [1559]),
    1560: ([1683, 31], [1569]),
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


def format_number(value, decimals=0):
    text = f'{value:,.{decimals}f}'
    return text.rstrip('0').rstrip('.') if decimals else text


def test_skills_cumulative_candidate(tmp_path):
    if not all(os.environ.get(name) for name in VARIABLES):
        pytest.skip('Set private original, workspace, PR4-PR7 plans and Alegreya source')
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    workspace = load_catalog(os.environ['GEMCRAFT_PR4_WORKSPACE'])
    entries = {entry['id']: entry for entry in workspace['entries']}
    plans = [load_catalog(path) for path in json.loads(os.environ['GEMCRAFT_PR4_PLANS'])]
    tutor = load_catalog(os.environ['GEMCRAFT_PR5_TUTOR_PLAN'])
    outcome = load_catalog(os.environ['GEMCRAFT_PR6_OUTCOME_PLAN'])
    skills = load_catalog(os.environ['GEMCRAFT_PR7_SKILLS_PLAN'])
    font = Path(os.environ['GEMCRAFT_ALEGREYA_FONT']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    baseline_plans = plans + [tutor, outcome]
    baseline, base_report = build_alegreya_localization(original, workspace, baseline_plans, font)
    assert sha(baseline) == PR6_SHA256
    candidate, report = build_alegreya_localization(
        original, workspace, baseline_plans + [skills], font)
    again, repeated = build_alegreya_localization(
        original, workspace, baseline_plans + [skills], font)
    assert candidate == again and report == repeated
    assert base_report['applied_translations'] == 602
    assert report['applied_translations'] == 719
    assert len(skills['entry_ids']) == 117
    assert skills['empty_abc_strings'] == [EMPTY_SUFFIX]
    assert report['empty_abc_strings'] == [EMPTY_SUFFIX]
    assert NO_OP not in skills['entry_ids'] and EMPTY_SUFFIX not in skills['entry_ids']
    assert set(base_report['applied_entry_ids']) <= set(report['applied_entry_ids'])
    assert all(report['layout_checks'][identifier] == base_report['layout_checks'][identifier]
               for identifier in base_report['layout_checks'])
    assert Counter(item['tag_code'] for item in report['changed_original_tags']) == {11: 96, 37: 24, 82: 1}
    inherited = merge_localization_plans(baseline_plans, workspace)
    merged = merge_localization_plans(baseline_plans + [skills], workspace)
    assert merged['coverage_blockers'] == inherited['coverage_blockers']
    assert len(report['coverage_blockers']) == len(base_report['coverage_blockers'])

    original_tags = read_swf(original)[1]
    old_tags, new_tags = read_swf(baseline)[1], read_swf(candidate)[1]
    original_inventory_data = extract_inventory(original, ORIGINAL_SHA256)
    original_entries = {entry['id']: entry for entry in original_inventory_data['entries']}
    assert original_entries[EMPTY_SUFFIX]['classification'] == 'technical'
    assert EMPTY_SUFFIX not in entries
    placements = {
        character: [(item['tag_path'], item['sprite_path']) for item in original_inventory_data['placements']
                    if item['character_id'] == character]
        for character in EXPECTED_PLACEMENTS
    }
    assert all(placements[character] == [expected]
               for character, expected in EXPECTED_PLACEMENTS.items())

    selected_static = {identifier for identifier in skills['entry_ids']
                       if identifier.startswith('static.')}
    assert selected_static == STATIC_IDS
    selected_abc = {identifier for identifier in skills['entry_ids']
                    if identifier.startswith('abc.')}
    assert len(selected_abc) == 114
    assert Counter(entries[identifier]['location']['method_index']
                   for identifier in selected_abc) == {950: 75, 955: 6, 956: 13, 957: 17, 3481: 3}

    original_abc = abc_bytes(original_tags)
    original_inventory = abc_inventory(original_abc)
    original_codes = {method: code for method, _, code in original_inventory.bodies}
    new_abc = abc_bytes(new_tags)
    new_inventory = abc_inventory(new_abc)
    old_bodies, new_bodies = _bodies(abc_bytes(old_tags)), _bodies(new_abc)
    new_codes = {method: code for method, _, code in new_inventory.bodies}

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

    operations556 = {
        offset: (opcode, args)
        for offset, opcode, args in new_inventory.instructions(new_codes[556], 556)
    }
    snippets556 = resolved_snippets[556]
    for offset, opcode, args in original_inventory.instructions(original_codes[556], 556):
        if opcode != 0x2c:
            continue
        relocated = offset + sum(len(snippet) for at, snippet in snippets556 if at <= offset)
        assert operations556[relocated] == (0x2c, args)
    operations957 = {
        offset: (opcode, args)
        for offset, opcode, args in new_inventory.instructions(new_codes[957], 957)
    }
    snippets957 = resolved_snippets[957]
    original957 = original_inventory.instructions(original_codes[957], 957)
    for offset in (496, 1066, 1125):
        expected = next((opcode, args) for at, opcode, args in original957 if at == offset)
        relocated = offset + sum(len(snippet) for at, snippet in snippets957 if at <= offset)
        assert operations957[relocated] == expected
    assert new_inventory.strings[expected_indices[(957, 1100)]] == ''
    assert new_inventory.strings[expected_indices[(957, 1117)]] == ''

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

    def by_character(tags):
        return {int.from_bytes(tag.payload[:2], 'little'): tag for tag in tags
                if tag.code in (11, 33, 37, 75)}

    original_chars = by_character(original_tags)
    old_chars, new_chars = by_character(old_tags), by_character(new_tags)
    old_fonts = {identifier: parse_font(tag.payload)
                 for identifier, tag in old_chars.items() if tag.code == 75}
    new_fonts = {identifier: parse_font(tag.payload)
                 for identifier, tag in new_chars.items() if tag.code == 75}
    for identifier in sorted(STATIC_IDS):
        character = int(identifier.split('.')[1])
        old_text = parse_text(original_chars[character].payload)
        new_text = parse_text(new_chars[character].payload)
        old_row, new_row = old_text['records'][0], new_text['records'][0]
        assert new_row.get('y') == old_row.get('y')
        assert new_row.get('color') == old_row.get('color')
        assert new_row['height'] == old_row['height']
        assert new_text['matrix_raw'] == old_text['matrix_raw']
        assert resolved_static(new_chars[character], new_fonts)[2][0][0] == entries[identifier]['translation']
        old_font = parse_font(original_chars[1].payload)
        new_font = new_fonts[6001]
        old_ink = ink_bounds(old_font, old_row['entries'], old_row['height'],
                             old_row.get('x', 0), old_row.get('y', 0))
        new_ink = ink_bounds(new_font, new_row['entries'], new_row['height'],
                             new_row.get('x', 0), new_row.get('y', 0))
        if identifier == 'static.1560':
            assert abs(sum(old_ink[:2]) - sum(new_ink[:2])) <= 2
        else:
            old_matrix, _ = decode_matrix(old_text['matrix_raw'])
            assert new_row.get('x', 0) == old_row.get('x', 0)
            assert new_ink[1] <= old_text['bounds'][1] - old_matrix['translate'][0] + 2
    for character in (1541, 1542, 1556, 1558, 565):
        assert new_chars[character] == original_chars[character]
    for character, tag in old_chars.items():
        if character in {1555, 1557, 1560, 6001, 6002}:
            continue
        if tag.code in (11, 33):
            assert resolved_static(tag, old_fonts) == resolved_static(new_chars[character], new_fonts)
        else:
            assert new_chars[character] == tag
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

    reader = ABCReader(original_abc)
    reader.take(4)
    pools = {}
    for kind in ('int', 'uint', 'double'):
        count = reader.count()
        values = []
        for _ in range(1, count):
            values.append(struct.unpack('<d', reader.take(8))[0]
                          if kind == 'double' else reader.encoded(32))
        pools[kind] = [None, *values]
    operations950 = original_inventory.instructions(original_codes[950], 950)

    def numeric(operation):
        _, opcode, args = operation
        if opcode == 0x24:
            return args[0] - 256 if args[0] >= 128 else args[0]
        if opcode == 0x25:
            return args[0] - 65536 if args[0] >= 32768 else args[0]
        if opcode in (0x2d, 0x2e, 0x2f):
            return pools['int' if opcode == 0x2d else 'uint' if opcode == 0x2e else 'double'][args[0]]
        return None

    effective_pairs = []
    for index in range(len(operations950) - 2):
        first, second = numeric(operations950[index]), numeric(operations950[index + 1])
        if first is not None and second is not None and operations950[index + 2][1:] == (0x56, [2]):
            effective_pairs.append((first, second))
    name_ids = sorted((identifier for identifier in selected_abc
                       if 199 <= entries[identifier]['location']['code_offset'] <= 271
                       and entries[identifier]['location']['method_index'] == 950),
                      key=lambda identifier: entries[identifier]['location']['code_offset'])
    effect_ids = sorted((identifier for identifier in selected_abc
                         if entries[identifier]['location']['method_index'] == 950
                         and entries[identifier]['location']['code_offset'] > 300),
                        key=lambda identifier: entries[identifier]['location']['code_offset'])
    assert len(name_ids) == len(effective_pairs) == 25 and len(effect_ids) == 50
    marker_counts = Counter()
    for identifier in effect_ids:
        source, translation = entries[identifier]['source'], entries[identifier]['translation']
        marker = next(marker for marker in ('#0,1', '#0,2', '#0') if marker in source)
        assert source.count(marker) == translation.count(marker) == 1
        assert source.count('%') == translation.count('%')
        marker_counts[marker] += 1
    assert marker_counts == {'#0': 40, '#0,1': 8, '#0,2': 2}

    effect_samples = []
    for skill_index, pair in enumerate(effective_pairs):
        for description_index in (0, 1):
            identifier = effect_ids[2 * skill_index + description_index]
            template = entries[identifier]['translation']
            marker = next(marker for marker in ('#0,1', '#0,2', '#0') if marker in template)
            decimals = 1 if marker == '#0,1' else 2 if marker == '#0,2' else 0
            for level in ((1, 3, 45) if description_index == 0 else (3, 45)):
                effective_level = math.floor(level / 3) if description_index else level
                value = Decimal(0) if effective_level < 1 else Decimal(str(pair[description_index])) * effective_level
                if marker + '%' in template:
                    value *= 100
                formatted = format_number(value, decimals)
                text = template.replace(marker, formatted, 1)
                for face in (6001, 6002):
                    result = check_layout(new_fonts[face], text, skills['layouts'][identifier])
                    effect_samples.append((identifier, level, face, formatted, text, result['lines']))
    assert len(effect_samples) == 250
    assert Counter(sample[-1] for sample in effect_samples) == {1: 195, 2: 55}
    title_samples = []
    for identifier in name_ids:
        for height, width in ((240, 6800), (260, 5400)):
            for face in (6001, 6002):
                result = check_layout(new_fonts[face], entries[identifier]['translation'], {
                    'height': height, 'max_width': width, 'wrap': True, 'max_lines': 1})
                title_samples.append(result)
    assert len(title_samples) == 100

    validation = json.loads(Path(os.environ['GEMCRAFT_PR7_SKILLS_PLAN']).with_name(
        'layout-validation.json').read_text())
    assert len(validation['concatenated_samples']) == 72
    for sample in validation['concatenated_samples']:
        check_layout(new_fonts[sample['font']], sample['text'], {
            'height': sample['height'], 'max_width': sample['max_width'],
            'wrap': True, 'max_lines': sample['max_lines'],
        })
    for cost in (1, 2, 5, 21, 45):
        text = entries['abc.4175.957.1090']['translation'] + str(cost)
        assert text == f'Стоимость следующего уровня навыка в очках: {cost}'
        check_layout(new_fonts[6002], text, skills['layouts']['abc.4175.957.1090'])
        insufficient = (entries['abc.4175.957.1056']['translation'] + str(cost)
                        + entries['abc.4175.957.1066']['translation'])
        check_layout(new_fonts[6002], insufficient, skills['layouts']['abc.4175.957.1056'])
    for bought in (0, 1, 25):
        text = (entries['abc.4175.955.179']['translation'] + format_number(Decimal(1000 + 100 * bought))
                + entries['abc.4175.955.210']['translation'])
        check_layout(new_fonts[6002], text, skills['layouts']['abc.4175.955.179'])

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
