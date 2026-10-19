"""Offline wave contracts against explicit private artifacts; never launches AIR."""
import json
import os
from functools import lru_cache
from itertools import product
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from addressed_localization import check_layout
from install_patch import ORIGINAL_SHA256, sha
from localization_fonts import measure_text
from localization_plan import resolve_plan_font_bindings
from swf_geometry import parse_font, read_swf
from test_battle_hud_local import preserve_method
from test_battle_waves import ATTRIBUTE_CALLS, EXPECTED_IDS, WAVE_CALLS, wave_plan
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


@pytest.fixture(scope='module')
def wave_artifacts():
    if not os.environ.get('GEMCRAFT_PR21_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR21_ROOT and GEMCRAFT_ORIGINAL for offline wave checks')
    root = Path(os.environ['GEMCRAFT_PR21_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    workspace = load_catalog(root / 'candidate/workspace.json')
    return root, original, candidate, {row['id']: row for row in workspace['entries']}


def test_waves_literals_guards_shared_consumers_and_abc_isolation(wave_artifacts):
    root, original, candidate, entries = wave_artifacts
    original_abc = abc_bytes(read_swf(original)[1])
    candidate_abc = abc_bytes(read_swf(candidate)[1])
    before, after = abc_inventory(original_abc), abc_inventory(candidate_abc)
    bodies = _bodies(original_abc)
    assert method_metadata(original_abc)[3504][4] == [
        (10, 10), (6, 3), (12, 12), (2, 3)]
    # giveDesc is used by precisely the wave renderer and four monster fields.
    consumers = {method for method, body in bodies.items()
                 if any(opcode in (0x46, 0x4f) and args[0] == 5113
                        for _, opcode, args in before.instructions(body[3], method))}
    assert consumers == {547, 549}
    for method, calls, init, local in ((547, WAVE_CALLS, (65, 66, 67, 68), 0xd5),
                                       (549, ATTRIBUTE_CALLS, (28, 29, 30, 31), 0xd6)):
        ops = before.instructions(bodies[method][3], method)
        assert [ops[index][1:] for index in init] == [
            (0x60, [230]), (0x66, [4200]), (0x80, [64]), (local, [])]
        selected = [site for site in wave_plan()['abc_font_bindings']
                    if site['method_index'] == method]
        assert {site['instruction_index'] for site in selected
                if site['kind'] == 'panel_format_before'} == calls
        for site in selected:
            assert ops[site['instruction_index']][1:] == (
                site['expected_opcode'], site['expected_operands'])
            for guard in site['receiver_guards']:
                assert ops[guard['instruction_index']][1:] == (guard['opcode'], guard['operands'])
    monster_ops = before.instructions(bodies[549][3], 549)
    assert monster_ops[34][1:] == (0x25, [360])
    for call in ATTRIBUTE_CALLS:
        assert monster_ops[call - 4][1:] == (0x26, [])
        assert monster_ops[call - 3][1:] == (0x24, [10])
    ops = before.instructions(bodies[547][3], 547)
    assert ops[219][1:] == (0x25, [420])
    assert before.multiname(ops[220][2][0]) == 'reset'
    for index in (427, 723, 968, 1099, 1514, 1535):
        assert ops[index][1:] == (0x24, [5])
        assert ops[index + 1][1:] == (0x4f, [3372, 1])
    formatting = {
        449: (True, 11), 475: (True, 11), 499: (False, 11),
        570: (True, 11), 615: (True, 11), 640: (True, 11),
        687: (False, 11), 706: (False, 11), 955: (False, 12),
        982: (True, 12), 1065: (True, 12), 1092: (False, 10),
        1266: (True, 12), 1274: (True, 12), 1507: (False, 12),
        1528: (False, 10), 1549: (True, 10), 1556: (True, 10),
        1564: (True, 10), 1571: (True, 10), 1578: (False, 10),
    }
    for index, (compact, size) in formatting.items():
        assert ops[index][1:] == (0x26 if compact else 0x27, [])
        assert ops[index + 1][1:] == (0x24, [size])
    assert before.multiname(ops[306][2][0]) == 'isEndurance'
    assert ops[308][1] == 0x2c and before.strings[ops[308][2][0]] == ''
    for index, literal in ((1351, '+1 monster\n'), (1498, '+1 beacon appears when wave starts')):
        assert ops[index][1] == 0x2c and before.strings[ops[index][2][0]] == literal
    # Exactly all visible wave literals; punctuation/newlines and the exception
    # string are explicitly separate. The Spark branch starts after index 1580.
    visible = {f'abc.4175.547.{offset}' for offset, opcode, args in ops[:1581]
               if opcode == 0x2c and any(char.isalpha() for char in before.strings[args[0]])
               and offset != 1993}
    assert visible == {key for key in EXPECTED_IDS if '.547.' in key} - {
        'abc.4175.547.860', 'abc.4175.547.926'}
    # Addressed replacements and bindings preserve all other bytecode, including
    # every numeric operand, conditional, switch and all Spark consumers.
    merged = load_catalog(root / 'candidate/plan.json')
    selected = [entries[key] for key in merged['entry_ids'] if key.startswith('abc.')]
    inventory_entries = {row['id']: row for row in load_catalog(root / 'candidate/inventory.json')['entries']}
    selected += [{**inventory_entries[key], 'translation': ''}
                 for key in merged.get('empty_abc_strings', [])]
    translated = patch_pushstrings(original_abc, selected)
    translated_inventory = abc_inventory(translated)
    make_snippets, increments, _ = resolve_plan_font_bindings(
        original_abc, translated, merged['abc_font_bindings'])
    snippets = make_snippets(len(translated_inventory.strings), len(translated_inventory.multinames))
    for method in (547, 549, 1553):
        preserve_method(translated_inventory, after, method,
                        snippets.get(method, []), increments.get(method, 0))
    assert after.strings[:len(before.strings)] == before.strings
    for method in (547, 549, 1553):
        assert not [row for row in after.unsupported + after.control_flow_anomalies
                    if row['method_index'] == method]


def test_waves_complete_dynamic_strings_and_outline_geometry(wave_artifacts):
    root, original, candidate, entries = wave_artifacts
    original_fonts, candidate_fonts = [
        {int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
         for tag in read_swf(data)[1] if tag.code == 75}
        for data in (original, candidate)]
    old_font, new_font = original_fonts[255], candidate_fonts[6002]
    for key in EXPECTED_IDS:
        check_layout(new_font, entries[key]['translation'], wave_plan()['layouts'][key])

    def text(method, offset, translated):
        return entries[f'abc.4175.{method}.{offset}'][
            'translation' if translated else 'source']

    def wave(offset, translated):
        return text(547, offset, translated)

    def attribute(index, number, translated):
        starts_ends = ((None, 50), (None, 78), (None, 104), (None, 132),
                       (None, 154), (166, 194), (206, 225), (237, 251),
                       (263, 277), (289, 303))
        start, end = starts_ends[index]
        prefix = '+' if index in (0, 2, 3) else 'x' if index == 1 else ''
        return prefix + (text(1553, start, translated) if start else '') + number + text(
            1553, end, translated)

    @lru_cache(maxsize=None)
    def measure(value, size, translated):
        return measure_text(new_font if translated else old_font, value, size * 20)

    @lru_cache(maxsize=None)
    def geometry(value, size, width, translated):
        font = new_font if translated else old_font
        result = check_layout(font, value, {'height': size * 20, 'max_width': width * 20,
                                          'wrap': True, 'max_lines': 40})
        wrapped = []
        for paragraph in value.split('\n'):
            line = ''
            for word in paragraph.split(' '):
                joined = line + (' ' if line else '') + word
                if measure(joined, size, translated)['widths'][0] > width * 20:
                    wrapped.append(line)
                    line = word
                else:
                    line = joined
            wrapped.append(line)
        assert result['lines'] == len(wrapped)
        measured = measure('\n'.join(wrapped), size, translated)
        assert max(measured['widths']) <= width * 20
        return measured, len(wrapped)

    def panel(rows, translated, width=396):
        y, previous_bottom = 10., None
        font = new_font if translated else old_font
        for value, size, compact, extra in rows:
            if value is None:
                y += extra
                continue
            measured, lines = geometry(value, size, width, translated)
            bounds = [bound for bound in measured['bounds'] if bound]
            if bounds:
                ascent = font['ascent'] * size / 20480
                top = y + 2 + ascent + min(bound[2] for bound in bounds) / 20
                bottom = y + 2 + ascent + max(bound[3] for bound in bounds) / 20
                assert previous_bottom is None or previous_bottom <= top, value
                previous_bottom = bottom
            y += (lines * measured['line_height'] + 80) / 20 + (-3 if compact else 9) + extra
        return y + 8

    evidence = []
    numbers = ('1', '2', '5', '21', '1,000', '1.000 e12', '1.0000000 e12')
    attribute_sets = ((), (0,), (0, 1), (0, 1, 2), (6, 7, 8, 9), tuple(range(10)))
    for number, indices, kind, state, endurance, first in product(
            numbers, attribute_sets, (875, 895, 906),
            ((False, False), (True, False), (False, True), (True, True)),
            (False, True), (False, True)):
        attrs = len(indices)
        raging, selected = state
        heights = []
        for translated in (False, True):
            prefix = '' if not attrs else wave((801, 817, 824)[min(attrs, 3) - 1], translated)
            kind_at = kind if number != '1' else {875: 941, 895: 961, 906: 972}[kind]
            header = number + wave(860 if number != '1' else 926, translated) + prefix + wave(kind_at, translated)
            rows = [(wave(683, translated) + '21' + ('' if endurance else wave(713, translated) + '100'), 12, False, 0),
                    (header, 12, False, 5)]
            rows += [(wave(offset, translated) + number, 11, offset not in (1144, 1519), 0)
                     for offset in (1014, 1079, 1144, 1218, 1476, 1519)]
            if attrs:
                rows.append((None, 0, False, 5))
                rows.append(('\n'.join(attribute(index, number, translated)
                                       for index in indices) + '\n', 12, False, 0))
            if raging:
                rows.append((None, 0, False, 5))
                rows.append((wave(2401, translated), 12, True, 0))
                rows.append(('+' + number + wave(2429, translated) + '+' + number + wave(2462, translated)
                             + '+' + number + wave(2495, translated) + (wave(2587, translated) if number == '1' else '+' + number + wave(2552, translated)), 12, True, 0))
                rows.append((number + wave(2629, translated) + wave(2657 if number == '1' else 2649, translated)
                             + wave(2662, translated), 10, False, 5))
            if selected:
                rows.append((wave(3125 if raging else 3104, translated), 12, True, 0))
                rows.append(((wave(3313, translated) if number == '1' else '+' + number + wave(3327, translated)) + '+' + number + wave(3431, translated)
                             + '+' + number + wave(3531, translated) + (wave(3652, translated) if number == '1' else '+' + number + wave(3643, translated)), 12, False, 5))
            rows.append((wave(3719, translated), 10, False, 5))
            rows += [(wave(offset, translated), 10, offset != 3842, 0)
                     for offset in ((3770, 3787) if first else (3808,)) + (3825, 3842)]
            heights.append(panel(rows, translated))
        comparisons = []
        for zoom in (1, .75, .5):
            factor = 1 if zoom >= 1 else (1 + zoom) / (2 * zoom)
            for threshold in (544, 602):
                old, new = (height * factor for height in heights)
                assert not old <= threshold < new, (number, attrs, kind, raging, selected, zoom, threshold, old, new)
                comparisons.append({'zoom': zoom, 'threshold': threshold,
                                    'original': old, 'candidate': new})
        evidence.append({'number': number, 'attributes': attrs, 'kind_offset': kind,
                         'raging': raging, 'selected': selected, 'attribute_indices': indices,
                         'endurance': endurance, 'first': first,
                         'height_comparisons': comparisons})
    # All ten giveDesc variants, including four shared monster fields, use
    # complete numerical phrases and the narrower monster-panel width.
    for translated in (False, True):
        for index in range(10):
            for number in numbers:
                panel([(attribute(index, number, translated), 10, True, 0)], translated, 336)
    (root / 'waves-geometry-evidence.json').write_text(json.dumps({
        'candidate_sha256': sha(candidate), 'models': evidence,
        'limitations': 'Outline/line-box model; Flash autoSize and actual runtime rendering unverified. '
                       'Representative formatted values are samples, not gameplay limits.'
    }, ensure_ascii=False, indent=2) + '\n')
