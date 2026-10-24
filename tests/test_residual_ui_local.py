"""Offline real-consumer and geometry regressions for PR33's small HUD remainder."""
import json
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from addressed_localization import check_layout
from install_patch import ORIGINAL_SHA256, sha
from inventory_strings import parse_edit_text, walk_tags
from localization_fonts import measure_text
from localization_plan import apply_plan_font_bindings, resolve_plan_font_bindings
from swf_geometry import parse_font, read_swf
from test_battle_hud_local import preserve_method
from test_bitmap_completion_semantics_local import Expressions
from test_floater_control_flow_local import assert_actual_map_floater_branch_repair
from test_residual_ui import EXPECTED_IDS, KILL_OFFSETS, PANEL_OFFSETS, PLAN
from test_talisman_local import abc_bytes, abc_inventory, resolved_operations
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


@pytest.fixture(scope='module')
def residual_artifacts():
    if not os.environ.get('GEMCRAFT_PR33_RESIDUAL_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR33_RESIDUAL_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR33_RESIDUAL_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    candidate = Path(os.environ.get('GEMCRAFT_PR33_RESIDUAL_CANDIDATE', root / 'candidate/gc-cs-steam.ru.swf')).read_bytes()
    baseline = (root / 'baseline/gc-cs-steam.ru.swf').read_bytes()
    assert sha(baseline) == '563bfe2b620d25ba018480d626342779de66af8a17d27627a90a77729595005e'
    workspace = load_catalog(root / 'candidate/workspace.json')
    rows = {row['id']: row for row in workspace['entries']}
    return root, original, baseline, candidate, rows, load_catalog(PLAN)


def test_all_original_consumers_and_address_guards_are_covered(residual_artifacts):
    _, original, _, _, rows, plan = residual_artifacts
    raw = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(raw)
    bodies = _bodies(raw)
    ops = inv.instructions(bodies[630][3], 630)
    selected = {at for at, opcode, args in ops if opcode == 0x2c
                and ((26052 <= at <= 27780) or (33714 <= at <= 34002))
                and any(c.isalpha() for c in inv.strings[args[0]])}
    assert selected == PANEL_OFFSETS
    calls = set()
    for index, (at, _, _) in enumerate(ops):
        if at in selected:
            calls.add(next(i for i in range(index + 1, len(ops))
                           if ops[i][1] == 0x4f and ops[i][2][0] == 3729))
    assert calls == {s['instruction_index'] for s in plan['abc_font_bindings']
                     if s['method_index'] == 630 and s['kind'] == 'panel_format_before'}
    for method, offsets in KILL_OFFSETS.items():
        operations = inv.instructions(bodies[method][3], method)
        assert {at for at, opcode, args in operations if opcode == 0x2c
                and inv.strings[args[0]] in ('\nkill', '\nkills')} == set(offsets)
        for at in offsets:
            row = rows[f'abc.4175.{method}.{at}']
            assert row['translation'] == '\nубито'
            assert row['source'].startswith('\n')
    for site in plan['abc_font_bindings']:
        operations = inv.instructions(bodies[site['method_index']][3], site['method_index'])
        assert operations[site['instruction_index']][1:] == (
            site['expected_opcode'], site['expected_operands'])
        for guard in site['receiver_guards']:
            assert operations[guard['instruction_index']][1:] == (guard['opcode'], guard['operands'])
    # The authored placeholder's English word is cleared both at battle reset
    # and when a series ends; only the six reviewed live writers add a label.
    for method in (243, 501):
        operations = inv.instructions(bodies[method][3], method)
        field = next(i for i, (_, opcode, args) in enumerate(operations)
                     if opcode == 0x66 and args == [3427])
        assert operations[field + 1][1:] == (0x2c, [1])
        assert inv.strings[1] == ''
        assert operations[field + 2][1:] == (0x61, [5329])
    assert set(plan['entry_ids']) == EXPECTED_IDS


def test_font_only_insertions_preserve_every_numeric_operation_and_branch(residual_artifacts):
    _, original, _, _, rows, plan = residual_artifacts
    raw = abc_bytes(read_swf(original)[1])
    translated = patch_pushstrings(raw, [rows[key] for key in plan['entry_ids']])
    before = abc_inventory(translated)
    make, increments, _ = resolve_plan_font_bindings(raw, translated, plan['abc_font_bindings'])
    # The public helper appends the same family/QName and resolves all guards.
    bound, _ = apply_plan_font_bindings(raw, translated, plan['abc_font_bindings'])
    after = abc_inventory(bound)
    family = after.strings.index('GCCS Alegreya')
    font = next(i for i in range(1, len(after.multinames)) if after.multiname(i) == 'font')
    snippets = make(family, font)
    for method in {630, *KILL_OFFSETS}:
        preserve_method(before, after, method, snippets[method], increments[method])
    for method in (220, 221, 450, 451, 452, 1079, 1080):
        old = _bodies(translated)[method]
        new = _bodies(bound)[method]
        assert old[2:] == new[2:]


def test_actual_candidate_retains_baseline_and_translates_only_new_sites(residual_artifacts):
    _, original, baseline, candidate, rows, plan = residual_artifacts
    old_tags, new_tags = read_swf(baseline)[1], read_swf(candidate)[1]
    def untouched(tags):
        return [(tag.code, tag.payload) for tag in tags if tag.code != 82
                and not (tag.code == 11 and int.from_bytes(tag.payload[:2], 'little') == 3767)]
    assert untouched(old_tags) == untouched(new_tags)
    old_raw, new_raw = abc_bytes(old_tags), abc_bytes(new_tags)
    old, new = abc_inventory(old_raw), abc_inventory(new_raw)
    original_raw = abc_bytes(read_swf(original)[1])
    source = abc_inventory(original_raw)
    assert new.strings[:len(source.strings)] == old.strings[:len(source.strings)]
    old_bodies, new_bodies = _bodies(old_raw), _bodies(new_raw)
    for method in old_bodies:
        if method in {630, 2769, *KILL_OFFSETS}:
            continue
        if method == 341:
            assert_actual_map_floater_branch_repair(original_raw, old_raw, new_raw)
            continue
        expected = resolved_operations(old, old.instructions(old_bodies[method][3], method))
        actual = resolved_operations(new, new.instructions(new_bodies[method][3], method))
        if method == 1732:
            # The separately verified native bitmap fix replaces only its
            # injected raw pattern; all renderer operations stay the same.
            escaped, literal = r'[\u0400-\u04FF]', '[Ѐ-ӿ]'
            assert sum(op == 0x2c and value == escaped for _, op, value in expected) == 1
            assert sum(op == 0x2c and value == literal for _, op, value in actual) == 1
            expected = [(at, op, literal if op == 0x2c and value == escaped else value)
                        for at, op, value in expected]
        assert expected == actual, method
        assert old_bodies[method][2] == new_bodies[method][2]
        assert old_bodies[method][4:] == new_bodies[method][4:]
    for row in (rows[key] for key in plan['entry_ids']):
        method = row['location']['method_index']
        assert row['translation'] in [new.strings[args[0]] for _, opcode, args in
                                     new.instructions(new_bodies[method][3], method) if opcode == 0x2c]


def test_real_kill_expression_both_branches_keep_formatter_and_numbers(residual_artifacts):
    root, original, _, candidate, _, _ = residual_artifacts
    results = []
    for raw, translated in ((original, False), (candidate, True)):
        program = Expressions(raw)
        for method in KILL_OFFSETS:
            ops = program.operations[method]
            field = next(i for i, (_, opcode, args) in enumerate(ops)
                         if opcode == 0x66 and program.inv.multiname(args[0]) == 'tfKillChain')
            start = ops[field + 1][0]
            end = next(at for at, opcode, args in ops[field + 1:]
                       if opcode == 0x61 and program.inv.multiname(args[0]) == 'text')
            for number in (1, 2, 5, 21, 1000, 999999999, 1e13):
                for long in (False, True):
                    stack, _, _, _ = program.run(method, start, end,
                        {0: {'core': {'monsterKillChainCounter': {'value': number}}}}, long=long)
                    suffix = '\nубито' if translated else '\nkill' if number == 1 else '\nkills'
                    assert stack == [program.format(number, long) + suffix]
                    results.append({'method': method, 'number': number, 'long': long,
                                    'translated': translated, 'text': stack[0]})
    (root / 'residual-ui-kill-semantics.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))


def test_real_fields_full_dynamic_values_geometry_and_scales(residual_artifacts):
    root, original, _, candidate, rows, plan = residual_artifacts
    original_tags, new_tags = read_swf(original)[1], read_swf(candidate)[1]
    fonts = {int.from_bytes(t.payload[:2], 'little'): parse_font(t.payload)
             for t in new_tags if t.code == 75}
    localized = fonts[6002]
    field_tag = next(t for t, _, _ in walk_tags(original_tags) if t.code == 37
                     and int.from_bytes(t.payload[:2], 'little') == 3456)
    _, _, field = parse_edit_text(field_tag.payload)
    assert field['bounds'] == [-40, 1040, -40, 588]
    assert field['font_id'] == 255 and field['font_height'] == 200 and field['leading'] == 40
    assert field['word_wrap'] and field['multiline'] and not field['auto_size']
    for key in plan['entry_ids']:
        check_layout(localized, rows[key]['translation'], plan['layouts'][key])
    program = Expressions(original)
    evidence = []
    values = [program.format(value, long) for value in (0, 1, 2, 5, 21, 1000, 999999999999, 1e13, 1e100, 1e300)
              for long in (False, True)]
    # The stock field can already wrap large 12-digit counters. The word label
    # remains two lines for values fitting the original numerical row.
    for number in values:
        label = number + '\nубито'
        if measure_text(localized, number, 200)['widths'][0] > 1000:
            assert measure_text(fonts[255], number, 200)['widths'][0] > 1000
            evidence.append({'kind': 'kill', 'value': number,
                             'status': 'ORIGINAL_NUMERIC_LIMITATION',
                             'note': 'Both fonts exceed the stock numerical row; runtime wrapping untested.'})
            continue
        check = check_layout(localized, label, {'height': 200, 'max_width': 1000,
                                              'max_height': 588, 'max_lines': 2})
        assert max(check['widths_twips']) <= 1000
        assert 2 * measure_text(localized, label, 200)['line_height'] + field['leading'] <= 588
        for zoom in (1, .75, .5):
            assert max(check['widths_twips']) / 20 * zoom <= 50 * zoom
        evidence.append({'kind': 'kill', 'value': number, 'check': check})
    for at in PANEL_OFFSETS:
        row = rows[f'abc.4175.630.{at}']
        layout = plan['layouts'][row['id']]
        samples = [row['translation']]
        if at not in (26052, 26066, 26268):
            samples = [row['translation'] + value for value in values]
        for text in samples:
            complete = {k: v for k, v in layout.items() if k != 'dynamic_suffix_budget'}
            checked = check_layout(localized, text, complete)
            assert checked['lines'] <= 2
            evidence.append({'kind': 'panel', 'id': row['id'], 'text': text, 'check': checked})
    for difficulty in (33856, 33859, 33862):
        text = rows['abc.4175.630.33839']['translation'] + 'V23 - ' + rows[f'abc.4175.630.{difficulty}']['translation']
        checked = check_layout(localized, text, {'height': 240, 'max_width': 6720, 'wrap': True, 'max_lines': 2})
        evidence.append({'kind': 'field-difficulty', 'text': text, 'check': checked})
    (root / 'residual-ui-geometry.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
