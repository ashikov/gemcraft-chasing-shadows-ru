"""Owned-source PR34 consumers, numeric phrases and outline geometry; no launch."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from addressed_localization import check_layout
from install_patch import ORIGINAL_SHA256, sha
from inventory_strings import extract_inventory, parse_edit_text
from localization_fonts import measure_text
from localization_plan import apply_plan_font_bindings, resolve_plan_font_bindings
from pr34_ui_binding import validate_small_ui_site
from swf_geometry import decode_rect, parse_font, parse_text, read_swf
from test_battle_hud_local import preserve_method
from test_bitmap_completion_semantics_local import Expressions
from test_pr34_small_ui import EXPECTED_IDS, PLAN, selected_font
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


@pytest.fixture(scope='module')
def ui_artifacts():
    root = Path(os.environ.get('GEMCRAFT_PR34_SMALL_UI_ROOT', 'local/pr34/small-ui'))
    candidates = ([Path(os.environ['GEMCRAFT_ORIGINAL'])]
                  if os.environ.get('GEMCRAFT_ORIGINAL') else list(Path('backups').glob('*.original')))
    candidate_path = Path(os.environ.get('GEMCRAFT_PR34_CANDIDATE', root / 'candidate.swf'))
    workspace_path = Path(os.environ.get('GEMCRAFT_PR34_WORKSPACE', root / 'workspace.json'))
    if not candidates or not candidate_path.exists() or not workspace_path.exists():
        pytest.skip('Provide an owned original, PR34 candidate and restored workspace')
    original = candidates[0].read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    candidate = candidate_path.read_bytes()
    rows = {row['id']: row for row in load_catalog(workspace_path)['entries']}
    return root, original, candidate, rows, load_catalog(PLAN)


def test_early_selector_and_swapped_prefix_preserve_original_numeric_code(ui_artifacts):
    _, original, _, rows, plan = ui_artifacts
    raw = abc_bytes(read_swf(original)[1])
    inventory = extract_inventory(original, ORIGINAL_SHA256)
    empty = deepcopy(next(row for row in inventory['entries'] if row['id'] == 'abc.4175.334.690'))
    empty['translation'] = ''
    translated = patch_pushstrings(raw, [rows[key] for key in EXPECTED_IDS
                                         if key != 'static.3431'] + [empty])
    source_inv, translated_inv = abc_inventory(raw), abc_inventory(translated)
    source_bodies, translated_bodies = _bodies(raw), _bodies(translated)
    changed_strings = set()
    for method in (139, 242, 334, 1537, 3490):
        old_ops = source_inv.instructions(source_bodies[method][3], method)
        new_ops = translated_inv.instructions(translated_bodies[method][3], method)
        assert len(old_ops) == len(new_ops)
        for (at, opcode, args), (_, new_opcode, new_args) in zip(old_ops, new_ops):
            assert opcode == new_opcode
            if opcode != 0x2c:
                continue
            identifier = f'abc.4175.{method}.{at}'
            old_value, new_value = source_inv.strings[args[0]], translated_inv.strings[new_args[0]]
            expected = '' if identifier == empty['id'] else rows[identifier]['translation'] if identifier in EXPECTED_IDS else old_value
            assert new_value == expected
            if new_value != old_value:
                changed_strings.add(identifier)
    assert changed_strings == (EXPECTED_IDS - {'static.3431'}) | {empty['id']}
    snippets, increments, _ = resolve_plan_font_bindings(raw, translated, plan['abc_font_bindings'])
    bound, _ = apply_plan_font_bindings(raw, translated, plan['abc_font_bindings'])
    before, after = abc_inventory(translated), abc_inventory(bound)
    family = after.strings.index('GCCS Alegreya')
    font = next(i for i in range(1, len(after.multinames)) if after.multiname(i) == 'font')
    emitted = snippets(family, font)
    assert set(emitted) == {139, 334, 3442, 3490}
    for method in emitted:
        preserve_method(before, after, method, emitted[method], increments[method])
    bound_bodies = _bodies(bound)
    for method, body in _bodies(translated).items():
        if method not in emitted:
            assert body[2:] == bound_bodies[method][2:]
    original_inv = abc_inventory(raw)
    old = original_inv.instructions(_bodies(raw)[3442][3], 3442)
    site = next(site for site in plan['abc_font_bindings']
                if site['kind'] == 'small_ui_floater_font')
    resolved = validate_small_ui_site(original_inv, before, old, old, site)
    matches = [(before.strings[index], mode) for index, mode in resolved]
    assert len(matches) == 17
    # Every selected full message and every formatter output selects the font;
    # unrelated Russian/Latin/numeric payloads retain the original font.
    for text, mode in matches:
        for suffix in ('', '0', '1', '2', '5', '21', '1.0000000 e12') if mode == 'prefix' else ('',):
            assert selected_font(text + suffix, matches) == 'Alegreya'
    for text in ('Untranslated diagnostic', '123', 'Другой русский текст',
                 'Опыт выносливости сохранён. Продолжение'):
        assert selected_font(text, matches) == 'Original'
    assert old[17][0] == 61
    assert next(at for at, opcode, args in old if opcode == 0x61 and args == [5573]) > 61
    assert next(at for at, opcode, args in old if opcode == 0x61 and args == [5329]) > 61
    assert next(at for at, opcode, args in old if opcode == 0x66 and args == [5359]) > 61
    for field in ('matches', 'receiver_guards'):
        assert site[field]
    bad = deepcopy(site)
    bad['matches'][0]['source_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='Stale'):
        validate_small_ui_site(original_inv, before, old, old, bad)


def test_actual_recovery_plural_branches_and_full_number_formatter_phrases(ui_artifacts):
    root, original, candidate, rows, _ = ui_artifacts
    old, new = Expressions(original), Expressions(candidate)
    results = []
    for program, localized in ((old, False), (new, True)):
        ops = program.operations[334]
        prefix = rows['abc.4175.334.677']['translation' if localized else 'source']
        start_index = next(i for i, (_, op, args) in enumerate(ops)
                           if op == 0x2c and program.inv.strings[args[0]] == prefix)
        start = ops[start_index - 1][0]
        end = next(at for at, op, args in ops[start_index:] if op == 0x56 and args == [1])
        for count in (1, 2, 5, 11, 21, 101, 1000):
            stack, _, _, visited = program.run(334, start, end, {8: count})
            expected = (f'Восстановлено и добавлено в инвентарь фрагментов талисмана: {count}.'
                        if localized else f'{count} talisman fragment{"s" if count > 1 else ""} recovered and added to the inventory.')
            assert stack == [expected]
            results.append({'count': count, 'localized': localized, 'text': stack[0],
                            'visited': sorted(visited)})
        # The real first four-row expression uses the actual game's formatter.
        header = rows['abc.4175.334.244']['translation' if localized else 'source']
        start = next(at for at, op, args in ops if op == 0x2c
                     and program.inv.strings[args[0]] == header)
        end = next(at for at, op, args in ops if at > start and op == 0x56 and args == [4])
        for value in (0, 1, 2, 999999, 1e13):
            for long in (False, True):
                stack, _, _, _ = program.run(334, start, end, {2: 'C3', 4: value, 5: value + 1}, long=long)
                assert stack[0] == header
                assert stack[1].endswith('C3')
                assert stack[2].endswith(program.format(value, long))
                assert stack[3].endswith(program.format(value + 1, long))
                assert len(stack) == 4
                results.append({'value': value, 'long': long, 'localized': localized, 'text': stack})
    for program, localized in ((old, False), (new, True)):
        ops = program.operations[139]
        prefix = rows['abc.4175.139.181']['translation' if localized else 'source']
        index = next(i for i, (_, op, args) in enumerate(ops)
                     if op == 0x2c and program.inv.strings[args[0]] == prefix)
        # Begin at the original -1 guard, stopping before the selected setter.
        # Parent cumulative plans can insert field typography in this span;
        # the dedicated unbound source and prefix expression stay authoritative.
        start, end = ops[index][0], ops[index + 3][0]
        for wave in (1, 2, 5, 21, 100, 999, 1000, 9999):
            stack, _, _, _ = program.run(139, start, end, {3: wave})
            assert stack == [('в.' if localized else 'w') + str(wave)]
            results.append({'wave_requirement': wave, 'localized': localized, 'text': stack[0]})
    root.mkdir(parents=True, exist_ok=True)
    (root / 'numeric-phrases.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')


def test_xp_actual_outlines_retain_height_and_fit_the_frame_and_scaled_neighbors(ui_artifacts):
    root, original, candidate, rows, plan = ui_artifacts
    old_tags, tags = read_swf(original)[1], read_swf(candidate)[1]
    fonts = {int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
             for tag in tags if tag.code == 75}
    text = next(tag for tag in tags if tag.code == 11 and int.from_bytes(tag.payload[:2], 'little') == 3431)
    source_text = next(tag for tag in old_tags if tag.code == 11 and int.from_bytes(tag.payload[:2], 'little') == 3431)
    old, current = parse_text(source_text.payload, 11), parse_text(text.payload, 11)
    assert current['records'][0]['height'] == old['records'][0]['height'] == 120
    assert current['records'][0]['x'] == old['records'][0]['x'] == 720
    assert current['records'][0]['y'] == old['records'][0]['y'] == 120
    # Source sprite3432 places its label at (+80,+58) over shape3430 [0,1800,0,680].
    bound = [current['bounds'][0] + 80, current['bounds'][1] + 80,
             current['bounds'][2] + 58, current['bounds'][3] + 58]
    assert 0 <= bound[0] < bound[1] <= 1800
    assert 0 <= bound[2] < bound[3] <= 680
    assert current['bounds'][1] > old['bounds'][1]  # Real contours exceed the old slot.
    assert ''.join(chr(fonts[6002]['codes'][i]) for i, _ in current['records'][0]['entries']) == 'Опыт'
    frame = next(tag for tag in old_tags if tag.code == 2 and int.from_bytes(tag.payload[:2], 'little') == 3430)
    assert decode_rect(frame.payload[2:])[0] == [0, 1800, 0, 680]
    for identifier in (3432, 3458, 3463):
        assert next(tag.payload for tag in tags if tag.code == 39 and int.from_bytes(tag.payload[:2], 'little') == identifier) == next(
            tag.payload for tag in old_tags if tag.code == 39 and int.from_bytes(tag.payload[:2], 'little') == identifier)
    # Numeric XP's authored placement is y=160+175 twips. The label's actual
    # ink bottom is above even that origin, before the numeric field's inset.
    assert current['bounds'][3] + 140 + 58 < 160 + 175
    geometry = []
    for identifier in EXPECTED_IDS:
        row, layout = rows[identifier], plan['layouts'][identifier]
        font = fonts[6001 if layout['font_id'] == 1 else 6002]
        measured = check_layout(font, row['translation'], layout)
        width = max(measured['widths_twips']) / 20
        for scale in (1, .75, .5):
            geometry.append({'id': identifier, 'scale': scale, 'width': width * scale,
                             'available': layout['max_width'] / 20 * scale})
            assert width * scale <= layout['max_width'] / 20 * scale
    # McPnlTutorial582.btnClose566.tf565 is the real 12px bold button field.
    tutorial_field = next(tag for tag in old_tags if tag.code == 37 and int.from_bytes(tag.payload[:2], 'little') == 565)
    _, _, tutorial_format = parse_edit_text(tutorial_field.payload)
    assert tutorial_format['bounds'] == [1054, 4258, -40, 345]
    assert tutorial_format['font_height'] == 240 and tutorial_format['font_id'] == 255
    button_width = max(measure_text(fonts[6002], rows['abc.4175.3490.21']['translation'], 240)['widths'])
    assert button_width + 160 < tutorial_format['bounds'][1] - tutorial_format['bounds'][0]
    # Floater constructor does not wrap. Pin complete longest dynamic strings,
    # not only the individual declared fragments, against the 1088px viewport.
    for value in ('1', '2', '21', '1000', '1.0000000 e12'):
        for identifier in ('abc.4175.334.252', 'abc.4175.334.265', 'abc.4175.334.677'):
            full = rows[identifier]['translation'] + value + ('.' if identifier.endswith('.677') else '')
            width = max(measure_text(fonts[6002], full, 220)['widths']) / 20 + 24
            assert width + 10 < 1088
    program = Expressions(original)
    requirements = []
    operations = program.operations[222]
    for index, (at, opcode, args) in enumerate(operations):
        if at >= 20284 or opcode != 0x5d or args != [69]:
            continue
        _, op, operands = operations[index + 3]
        assert op in (0x24, 0x25, 0x2d, 0x2f)
        value = (operands[0] - (256 if op == 0x24 and operands[0] > 127 else 0)
                 if op in (0x24, 0x25) else program.ints[operands[0]] if op == 0x2d
                 else program.doubles[operands[0]])
        requirements.append(value)
    assert len(requirements) == 418 and max(requirements) == 24
    for value in set(requirements) - {-1}:
        wave = rows['abc.4175.139.181']['translation'] + str(value)
        for font in (fonts[6001], fonts[6002]):
            assert max(measure_text(font, wave, 200)['widths']) <= 522
    (root / 'geometry-scales.json').write_text(json.dumps({'xp_bounds': bound, 'layouts': geometry}, ensure_ascii=False, indent=2) + '\n')
