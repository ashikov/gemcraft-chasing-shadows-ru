"""Original ABC scope and offline outline models; never launches GemCraft."""
import json
import os
from functools import lru_cache
from itertools import product
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from addressed_localization import check_layout
from install_patch import ORIGINAL_SHA256, sha
from localization_fonts import measure_text
from swf_geometry import parse_font, read_swf
from test_active_monsters import CALLS, EXPECTED_IDS, OFFSETS, PLAN, PR21_CALLS, UNCHANGED
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


@pytest.fixture(scope='module')
def monster_artifacts():
    if not os.environ.get('GEMCRAFT_PR26_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR26_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR26_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    baseline = (root / 'baseline/gc-cs-steam.ru.swf').read_bytes()
    assert sha(baseline) == 'a10aa184dea912875b0b5c87da2a10f67e0072f7f24cb4963caabba1a543e771'
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    entries = {r['id']: r for r in load_catalog(root / 'candidate/workspace.json')['entries']}
    abc = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(abc)
    return root, original, baseline, candidate, entries, abc, inv


def test_complete_original_scope_and_consumers(monster_artifacts):
    _, _, _, _, entries, abc, inv = monster_artifacts
    bodies = _bodies(abc)
    dispatch = inv.instructions(bodies[630][3], 630)
    for index, pool, argc in ((171, 932, 0), (207, 934, 1), (243, 935, 1),
                              (279, 937, 1), (315, 936, 1), (351, 933, 1)):
        assert dispatch[index][1:] == (0x4f, [pool, argc])
        assert inv.multiname(dispatch[index - (1 if argc == 0 else 6)][2][0]) == 'infoPanelRenderer2'
    plans = [load_catalog(PLAN), load_catalog('translations/plans/pr21-battle-waves-mana.json')]
    for method in OFFSETS:
        ops = inv.instructions(bodies[method][3], method)
        literals = {at: args[0] for at, op, args in ops if op == 0x2c}
        assert set(literals) == OFFSETS[method] | UNCHANGED[method]
        for at, pool in literals.items():
            if inv.strings[pool]:
                assert entries[f'abc.4175.{method}.{at}']['source'] == inv.strings[pool]
            assert any(c.isalpha() for c in inv.strings[pool]) == (at in OFFSETS[method])
        calls = {i for i, (_, op, args) in enumerate(ops) if op == 0x4f and args[0] == 3729}
        assert calls == CALLS[method]
        for call in calls:
            plan = plans[1] if method == 549 and call in PR21_CALLS else plans[0]
            for index in (call, call + 1):
                site = next(s for s in plan['abc_font_bindings']
                            if s['method_index'] == method and s['instruction_index'] == index)
                assert ops[index][1:] == (site['expected_opcode'], site['expected_operands'])
                for guard in site['receiver_guards']:
                    assert ops[guard['instruction_index']][1:] == (guard['opcode'], guard['operands'])
        # No hidden text helpers: the only dynamic visible-text helper is
        # the four descriptions already translated and bound by PR21.
        calls_to_desc = [args for _, op, args in ops if op == 0x46
                         and inv.multiname(args[0]) == 'giveDesc']
        assert len(calls_to_desc) == (4 if method == 549 else 0)
        names = {inv.multiname(args[0]) for _, op, args in ops if op == 0x66}
        assert {'hp', 'hpMax', 'isPoisoned', 'isPoisonedByShrine', 'isCursed',
                'isFrozen', 'isSlowed', 'isSlowedByShrine'} <= names


def test_complete_dynamic_fields_panel_heights_and_scales(monster_artifacts):
    root, original, baseline, candidate, entries, abc, inv = monster_artifacts
    bodies = _bodies(abc)
    fonts = [{int.from_bytes(t.payload[:2], 'little'): parse_font(t.payload)
              for t in read_swf(data)[1] if t.code == 75}
             for data in (original, baseline, candidate)]
    models = (fonts[0][255], fonts[1][255], fonts[2][6002])
    plan = load_catalog(PLAN)
    for key in EXPECTED_IDS:
        check_layout(models[2], entries[key]['translation'], plan['layouts'][key])
    # Lock the common renderer's line height, compact/noncompact spacing,
    # separator padding and zoom transformation used by this offline model.
    assert method_metadata(abc)[3504][4] == [(10, 10), (6, 3), (12, 12), (2, 3)]
    reset = dict((a, (o, p)) for a, o, p in inv.instructions(bodies[3506][3], 3506))
    assert reset[82] == (0x24, [10]) and reset[95] == (0x24, [8])
    assert method_metadata(abc)[3506][4] == [(7, 3), (3, 6)]
    renderer = dict((a, (o, p)) for a, o, p in inv.instructions(bodies[3504][3], 3504))
    assert renderer[199] == (0x24, [20])
    assert renderer[846] == (0x24, [253]) and renderer[853] == (0x24, [9])
    assert renderer[869] == (0x24, [8])
    bitmap = dict((a, (o, p)) for a, o, p in inv.instructions(bodies[3508][3], 3508))
    assert bitmap[97] == (0x24, [1]) and bitmap[105] == (0x93, [])
    assert bitmap[108] == (0xa2, []) and bitmap[1001] == (0xa3, [])

    def text(method, at, model):
        row = entries[f'abc.4175.{method}.{at}']
        return row['translation'] if model == 2 and row['translation'] is not None else row['source']

    def attribute(index, number, model):
        starts_ends = ((206, 225), (237, 251), (263, 277), (289, 303))
        first, last = starts_ends[index]
        # PR21 already changed these descriptions in the baseline.
        return ''.join((text(1553, first, 2 if model else 0), number,
                        text(1553, last, 2 if model else 0)))

    def model_font(model, value):
        if model == 1 and any('А' <= c <= 'я' or c in 'Ёё' for c in value):
            return fonts[1][6002]
        return models[model]

    @lru_cache(maxsize=None)
    def field(model, value, size, width):
        font = model_font(model, value)
        result = check_layout(font, value, {'height': size * 20, 'max_width': width,
                                           'wrap': True, 'max_lines': 100})
        lines = []
        for paragraph in value.split('\n'):
            line = ''
            for word in paragraph.split(' '):
                joined = line + (' ' if line else '') + word
                if measure_text(font, joined, size * 20)['widths'][0] > width:
                    assert line, ('unbreakable word', word)
                    lines.append(line)
                    line = word
                else:
                    line = joined
            lines.append(line)
        assert len(lines) == result['lines']
        measured = measure_text(font, '\n'.join(lines), size * 20)
        assert max(measured['widths']) <= width
        return measured, len(lines)

    evidence = []
    numbers = ('0', '1', '2', '21', '999.9', '1,000', '1.0000000 e12')
    for method, number, optional in product(OFFSETS, numbers, (False, True)):
        ops = inv.instructions(bodies[method][3], method)
        width = {549: 6720, 550: 5920}.get(method, 5120)
        comparisons = []
        for model in range(3):
            y, previous_bottom, fields, overlaps = 10., None, [], []
            last_call = -1
            for index, (at, op, args) in enumerate(ops):
                if op != 0x4f or args[0] != 3729:
                    continue
                segment = ops[last_call + 1:index]
                last_call = index
                call = index
                # Title, wave subtitle and hit-count branches are exclusive.
                if method == 549 and call in (92, 119, 560, 599, 1093):
                    continue
                if method == 549 and call == 178 and optional:
                    continue
                if method == 549 and call == 152:
                    prefix = text(549, 181, model) if optional else ''
                    value = prefix + text(549, 350, model)
                elif method == 549 and call == 268:
                    value = text(549, 652, model) + number
                elif method == 549 and call == 170:
                    value = text(549, 418, model) + number
                elif method == 549 and call == 294:
                    value = text(549, 700, model) + number + text(549, 727 if number == '1' else 719, model)
                elif method == 549 and call in PR21_CALLS:
                    if not optional:
                        continue
                    value = attribute(sorted(PR21_CALLS).index(call), number, model)
                else:
                    tokens = []
                    for offset, opcode, operands in segment:
                        if opcode == 0x2c:
                            tokens.append(text(method, offset, model))
                        elif opcode == 0x46 and inv.multiname(operands[0]) == 'format':
                            tokens.append(number)
                    value = ''.join(tokens)
                if method == 549 and call == 178:
                    value = text(549, 447, model)
                if not value:
                    continue
                # When optional=False retain only always-visible fields.
                if not optional and call not in {
                    549: {152, 178, 211, 237, 268, 359, 469, 517},
                    550: {31, 38, 45, 78, 91, 108, 123, 130, 137, 144, 151, 158, 474},
                    551: {30, 37, 70, 85, 92, 99, 106, 356, 401},
                    552: {30, 37, 70, 85, 92, 99, 106, 356, 401},
                    553: {30, 37, 70, 83, 98, 105},
                    554: {30, 37, 70, 92, 99, 106, 113, 332, 377},
                }[method]:
                    continue
                if args[1] == 3:
                    size, compact = 12, True
                else:
                    size_index = next(j for j in range(index - 1, max(0, index - 45), -1)
                                      if ops[j][1] == 0x24 and ops[j - 1][1] in (0x26, 0x27))
                    size = ops[size_index][2][0]
                    compact = ops[size_index - 1][1] == 0x26
                # Source-reviewed branch-aware spacing. In NORMAL mode the
                # XP else-padding is skipped. Type-dependent talisman fields
                # are exclusive; the reaver branch is selected above.
                padding = {
                    549: {211: 16, 359: 16, 469: 4, 689: 5, 837: 17, 1050: 8},
                    550: {78: 5, 123: 5, 253: 10, 316: 2, 474: 14 if optional else 24},
                    551: {70: 5, 85: 5, 201: 2, 356: 11 if optional else 5},
                    552: {70: 5, 85: 5, 201: 2, 356: 11 if optional else 5},
                    553: {70: 5, 98: 5, 169: 2},
                    554: {70: 5, 92: 5, 177: 2, 332: 11 if optional else 5},
                }[method].get(call, 0)
                y += padding
                measured, lines = field(model, value, size, width)
                bounds = [bound for bound in measured['bounds'] if bound]
                if bounds:
                    origin = y + 2 + model_font(model, value)['ascent'] * size / 20480
                    top = origin + min(bound[2] for bound in bounds) / 20
                    bottom = origin + max(bound[3] for bound in bounds) / 20
                    if previous_bottom is not None and top < previous_bottom:
                        overlaps.append({'call': call, 'overlap': previous_bottom - top})
                    previous_bottom = bottom
                fields.append({'call': call, 'text': value, 'size': size, 'y': y, 'lines': lines})
                y += (lines * measured['line_height'] + 80) / 20 + (-3 if compact else 9)
            y += 18 if method == 549 and not optional else 6 if method == 553 and optional else 0
            comparisons.append({'model': model, 'height': y + 8, 'fields': fields, 'outline_overlaps': overlaps})
        assert not comparisons[2]['outline_overlaps'], (method, number, optional)
        for zoom in (1, .75, .5):
            factor = (1 + zoom) / (2 * zoom)
            for threshold in (544, 602):
                assert not (comparisons[1]['height'] * factor <= threshold
                            < comparisons[2]['height'] * factor), (method, number, zoom)
        evidence.append({'method': method, 'number': number, 'optional_upper_envelope': optional,
                         'models': comparisons,
                         'scales': [{'zoom': zoom, 'heights': [c['height'] * (1 + zoom) / (2 * zoom)
                                                            for c in comparisons],
                                     'preferred_space': 544, 'viewport': 602,
                                     'candidate_exceeds_preferred': comparisons[2]['height'] * (1 + zoom) / (2 * zoom) > 544,
                                     'candidate_exceeds_viewport': comparisons[2]['height'] * (1 + zoom) / (2 * zoom) > 602}
                                    for zoom in (1, .75, .5)]})
    # Every title alternative and every buff combination is measured separately.
    title_cases = []
    for model, prefix, noun in product(range(3), (None, 150, 174, 181), (205, 271, 341, 350)):
        value = (text(549, prefix, model) if prefix else '') + text(549, noun, model)
        _, lines = field(model, value, 12, 6720)
        title_cases.append({'model': model, 'prefix': prefix, 'noun': noun, 'lines': lines})
    hits_cases = []
    for model, number in product(range(3), numbers):
        _, lines = field(model, text(549, 2618, model) + number, 10, 6720)
        hits_cases.append({'model': model, 'number': number, 'lines': lines})
    buff_cases = []
    for model, number, mask in product(range(3), numbers, range(16)):
        line_counts = [field(model, attribute(index, number, model), 10, 6720)[1]
                       for index in range(4) if mask & (1 << index)]
        buff_cases.append({'model': model, 'number': number, 'mask': mask, 'lines': line_counts})
    (root / 'active-monsters-geometry.json').write_text(json.dumps({
        'candidate_sha256': sha(candidate), 'panels': evidence, 'title_cases': title_cases, 'buff_cases': buff_cases, 'hits_cases': hits_cases,
        'limitations': 'Offline outline model with minimum and conservative optional-field upper envelopes. '
        'Optional states are not asserted jointly reachable. Representative numbers are not gameplay bounds. '
        'AIR autoSize, clipping, rasterization and actual hover/HUD placement require runtime acceptance.'
    }, ensure_ascii=False, indent=2) + '\n')
