"""Opt-in Spark ABC and outline checks; no installation or game launch."""
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
from test_battle_sparks import EXPECTED_IDS, SPARK_CALLS, spark_plan
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


@pytest.fixture(scope='module')
def spark_artifacts():
    if not os.environ.get('GEMCRAFT_PR22_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR22_ROOT and GEMCRAFT_ORIGINAL for offline Spark checks')
    root = Path(os.environ['GEMCRAFT_PR22_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    workspace = load_catalog(root / 'candidate/workspace.json')
    return root, original, candidate, {row['id']: row for row in workspace['entries']}


def test_sparks_complete_abc_scope_guards_states_and_preservation(spark_artifacts):
    root, original, candidate, entries = spark_artifacts
    original_abc = abc_bytes(read_swf(original)[1])
    candidate_abc = abc_bytes(read_swf(candidate)[1])
    before, after = abc_inventory(original_abc), abc_inventory(candidate_abc)
    bodies = _bodies(original_abc)
    ops = before.instructions(bodies[547][3], 547)
    branch = [row for row in ops if 3946 <= row[0] <= 6046 and row[1] == 44]
    visible = {f'abc.4175.547.{offset}' for offset, _, args in branch
               if any(char.isalpha() for char in before.strings[args[0]])}
    assert visible == EXPECTED_IDS
    assert len(branch) == 66
    selected_pool_indices = {args[0] for _, _, args in branch
                             if any(char.isalpha() for char in before.strings[args[0]])}
    external_consumers = {}
    for method, body in bodies.items():
        for offset, opcode, args in before.instructions(body[3], method):
            if opcode == 44 and args[0] in selected_pool_indices and not (
                    method == 547 and 3946 <= offset <= 6046):
                external_consumers.setdefault(args[0], set()).add((method, offset))
    # No hidden recipient receives Russian text through shared pool mutation.
    # Spark in m220 is the original technical achievement filter key.
    assert external_consumers == {
        500: {(220, 746)}, 10278: {(547, 2629)}, 10279: {(547, 2649)},
        10280: {(547, 2657)}, 10290: {(547, 3787)}, 10292: {(547, 3825)},
    }
    assert {before.strings[args[0]] for _, _, args in branch
            if not any(char.isalpha() for char in before.strings[args[0]])} == {'+', '-'}
    calls = {index for index, (_, opcode, args) in enumerate(ops)
             if index > 1580 and opcode == 79 and args[0] == 3729}
    assert calls == SPARK_CALLS
    assert [before.multiname(ops[index][2][0]) for index in
            (1609, 1649, 1678, 1707, 1731, 1760)] == [
                'BEACON_LOCK', 'SPELL_AND_SHRINE_CHARGE', 'BUILDINGS_PRICE_CUT',
                'SHARD_DROP', 'XP_BOOST', 'SHRINE_DROP']
    assert [before.multiname(ops[index][2][0]) for index in
            (2071, 2080, 2089, 2098)] == [
                'CAST_GEMBOMB_INITIATED', 'DRAGGING_GEM_FROM_SLOT_TO_THROW',
                'DRAGGING_GEM_FROM_TOWER_TO_THROW', 'DRAGGING_GEM_FROM_TRAP_TO_THROW']
    # Preview clones the Spark and heats the clone, showing cumulative modifiers.
    assert ops[2189][1:] == (0x46, [819, 0])
    assert ops[2195][1:] == (0x4f, [2327, 1])
    assert ops[2351][1] == 0x14
    assert ops[2387][1] == 0x14
    assert ops[2398][0] == 5991
    # Original XP preview has no '+' pushstring, unlike the existing-heat branch.
    assert all(opcode != 44 for _, opcode, _ in ops[2294:2303])
    assert ops[1956][1:] == (44, [8559])
    for site in spark_plan()['abc_font_bindings']:
        if site['method_index'] != 547:
            continue
        assert ops[site['instruction_index']][1:] == (
            site['expected_opcode'], site['expected_operands'])
        for guard in site['receiver_guards']:
            assert ops[guard['instruction_index']][1:] == (guard['opcode'], guard['operands'])
    assert ops[219][1:] == (0x25, [420])
    merged = load_catalog(root / 'candidate/plan.json')
    inventory = {row['id']: row for row in load_catalog(root / 'candidate/inventory.json')['entries']}
    selected = [entries[key] for key in merged['entry_ids'] if key.startswith('abc.')]
    selected += [{**inventory[key], 'translation': ''}
                 for key in merged.get('empty_abc_strings', [])]
    translated = patch_pushstrings(original_abc, selected)
    translated_inventory = abc_inventory(translated)
    make_snippets, increments, _ = resolve_plan_font_bindings(
        original_abc, translated, merged['abc_font_bindings'])
    snippets = make_snippets(len(translated_inventory.strings), len(translated_inventory.multinames))
    # Checks each original numeric operand, branch target, exception and operation.
    preserve_method(translated_inventory, after, 547,
                    snippets.get(547, []), increments.get(547, 0))
    assert after.strings[:len(before.strings)] == before.strings
    assert not [row for row in after.unsupported + after.control_flow_anomalies
                if row['method_index'] == 547]
    # Neither game calculations nor the shared panel renderer changes.
    for method in (220, 598, 666, 1401, 1451, 1452, 3503, 3504):
        assert _bodies(candidate_abc)[method][2:] == bodies[method][2:]


def test_sparks_assembled_phrases_and_outline_geometry(spark_artifacts):
    root, original, candidate, entries = spark_artifacts
    fonts = [{int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
              for tag in read_swf(data)[1]
              if tag.code == 75 and int.from_bytes(tag.payload[:2], 'little') in (255, 6002)}
             for data in (original, candidate)]
    old_font, new_font = fonts[0][255], fonts[1][6002]
    for key in EXPECTED_IDS:
        check_layout(new_font, entries[key]['translation'], spark_plan()['layouts'][key])

    def text(offset, translated):
        return entries[f'abc.4175.547.{offset}']['translation' if translated else 'source']

    @lru_cache(maxsize=None)
    def measured(value, size, translated):
        return measure_text(new_font if translated else old_font, value, size * 20)

    @lru_cache(maxsize=None)
    def field(value, size, translated):
        font = new_font if translated else old_font
        layout = check_layout(font, value, {
            'height':size * 20, 'max_width':396 * 20, 'wrap':True, 'max_lines':40})
        wrapped = []
        for paragraph in value.split('\n'):
            line = ''
            for word in paragraph.split(' '):
                joined = line + (' ' if line else '') + word
                if measured(joined, size, translated)['widths'][0] > 396 * 20:
                    wrapped.append(line)
                    line = word
                else:
                    line = joined
            wrapped.append(line)
        assert layout['lines'] == len(wrapped)
        result = measured('\n'.join(wrapped), size, translated)
        assert max(result['widths']) <= 396 * 20
        return result, len(wrapped)

    def panel(rows, translated):
        y, previous_bottom = 10., None
        font = new_font if translated else old_font
        for value, size, compact, extra in rows:
            if value is None:
                y += extra
                continue
            result, lines = field(value, size, translated)
            bounds = [bound for bound in result['bounds'] if bound]
            if bounds:
                ascent = font['ascent'] * size / 20480
                top = y + 2 + ascent + min(bound[2] for bound in bounds) / 20
                bottom = y + 2 + ascent + max(bound[3] for bound in bounds) / 20
                assert previous_bottom is None or previous_bottom <= top, value
                previous_bottom = bottom
            y += (lines * result['line_height'] + 80) / 20 + (-3 if compact else 9) + extra
        return y + 8

    def effects(kind, n, translated, mode):
        def t(offset):
            return text(offset, translated)
        if mode == 'total':
            return (
                t(3964) + n + t(3994) + n + t(4022),
                '+' + n + t(4097), '-' + n + t(4172), n + t(4236),
                '+' + n + t(4311), n + t(4382) + n + t(4410) + n + t(4438),
            )[kind]
        preview = mode == 'preview'
        at = (5505, 5533, 5590, 5647, 5697, 5750, 5803, 5831, 5859) if preview else (
            4625, 4653, 4710, 4767, 4817, 4874, 4931, 4959, 4987)
        return (
            '+' + n + t(at[0]) + n + t(at[1]), '+' + n + t(at[2]),
            '-' + n + t(at[3]), '+' + n + t(at[4]),
            ('' if preview else '+') + n + t(at[5]),
            ('' if preview else '+') + n + t(at[6]) + n + t(at[7]) + n + t(at[8]),
        )[kind]

    evidence = []
    for kind, number, heated, selected, first in product(
            range(6), ('1', '2', '5', '21', '1,000', '1.000 e12', '1.0000000 e12'),
            (False, True), (False, True), (False, True)):
        heights, phrases = [], []
        for translated in (False, True):
            def t(offset):
                return text(offset, translated)
            rows = [(t((3946, 4049, 4124, 4199, 4263, 4338)[kind]), 14, True, 0),
                    (t(4467), 10, False, 0),
                    (t((3955, 4058, 4133, 4208, 4272, 4347)[kind]), 12, False, 5),
                    (effects(kind, number, translated, 'total'), 11, False, 5)]
            if heated:
                rows += [(t(5000), 12, True, 0),
                         (effects(kind, number, translated, 'heat'), 12, True, 0),
                         (number + t(5042) + t(5066 if number == '1' else 5058) + t(5071),
                          10, False, 5)]
            if selected:
                rows += [(t(5907 if heated else 5886), 12, True, 0),
                         (effects(kind, number, translated, 'preview'), 12, False, 5)]
            rows += [(t(offset), 10, offset != 6046, 0)
                     for offset in ((5974, 5991) if first else (6012,)) + (6029, 6046)]
            heights.append(panel(rows, translated))
            if translated:
                phrases = [value for value, _, _, _ in rows]
        comparisons = []
        for zoom in (1, .75, .5):
            factor = 1 if zoom >= 1 else (1 + zoom) / (2 * zoom)
            for threshold in (544, 602):
                old, new = (height * factor for height in heights)
                assert not old <= threshold < new, (kind, number, heated, selected,
                                                   first, zoom, threshold, old, new)
                comparisons.append({'zoom':zoom, 'threshold':threshold,
                                    'original':old, 'candidate':new})
        evidence.append({'kind':kind, 'number':number, 'heated':heated, 'selected':selected,
                         'first':first, 'height_comparisons':comparisons,
                         'assembled_translations':phrases})
    (root / 'sparks-geometry-evidence.json').write_text(json.dumps({
        'candidate_sha256':sha(candidate), 'models':evidence,
        'limitations':'Outline and line-box model; Flash autoSize/rendering unverified. '
                      'Formatted numbers are representative samples, not game limits.'
    }, ensure_ascii=False, indent=2) + '\n')
