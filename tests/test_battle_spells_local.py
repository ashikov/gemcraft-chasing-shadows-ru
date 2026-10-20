"""Original-ABC scope and real-glyph geometry for the six spell panels."""
import json
import os
from functools import lru_cache
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from addressed_localization import check_layout
from install_patch import ORIGINAL_SHA256, sha
from localization_fonts import measure_text
from swf_geometry import parse_font, read_swf
from test_battle_spells import CALLS, EXPECTED_IDS, OFFSETS, PLAN, RANGES, UNCHANGED
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


@pytest.fixture(scope='module')
def spell_artifacts():
    if not os.environ.get('GEMCRAFT_PR25_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR25_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR25_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    baseline = (root / 'baseline/gc-cs-steam.ru.swf').read_bytes()
    entries = {r['id']: r for r in load_catalog(root / 'candidate/workspace.json')['entries']}
    abc = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(abc)
    ops = inv.instructions(_bodies(abc)[630][3], 630)
    return root, original, baseline, candidate, entries, abc, inv, ops


def test_spell_full_original_scope_consumers_and_panel_guards(spell_artifacts):
    _, _, _, _, entries, _, inv, ops = spell_artifacts
    literals = {(at, args[0]) for at, op, args in ops if op == 44
                and any(lo <= at <= hi for lo, hi in RANGES)}
    assert {at for at, _ in literals} == OFFSETS | UNCHANGED
    assert len(literals) == 114
    for at, pool in literals:
        if at in OFFSETS:
            assert entries[f'abc.4175.630.{at}']['source'] == inv.strings[pool]
        else:
            assert inv.strings[pool] in ('+', '-', '%')
    # Shared pool values also feed skills/gem panels and independent objects.
    # Every one of those consumers must retain its own addressed behavior.
    pools = {pool for at, pool in literals if at in OFFSETS}
    consumers = {(method, at) for method, _, code in inv.bodies
                 for at, op, args in inv.instructions(code, method)
                 if op == 44 and args[0] in pools}
    external = consumers - {(630, at) for at in OFFSETS}
    assert len(pools) == 57 and len(consumers) == 120
    assert external == {(220,326),(950,226),(220,221),(950,241),(950,256),
                        (220,131),(548,5063),(950,214),(220,101),(548,5066),
                        (950,229),(220,71),(548,5069),(950,244),(548,1920),
                        (630,13036),(630,13143),(630,13233),(630,13188),
                        (630,19536),(630,19607),(630,20348),(630,20419),
                        (548,4999),(548,4960),(548,5038)}
    actual_calls = {i for i, (at, op, args) in enumerate(ops) if op == 79
                    and args[0] == 3729 and any(lo <= at <= hi for lo, hi in RANGES)}
    assert actual_calls == CALLS
    for site in load_catalog(PLAN)['abc_font_bindings']:
        assert ops[site['instruction_index']][1:] == (
            site['expected_opcode'], site['expected_operands'])
        for guard in site['receiver_guards']:
            assert ops[guard['instruction_index']][1:] == (guard['opcode'], guard['operands'])
    for number, (first, _) in enumerate(RANGES, 15):
        start = next(i for i, (at, _, _) in enumerate(ops) if at == first)
        assert inv.multiname(ops[start - 11][2][0]) == 'arrIsSpellBtnVisible'
        assert ops[start - 10][1:] == (0x24, [number])
        assert ops[start - 8][1] == 0x12
        reset = ops[start - 4]
        assert reset[1:] == (0x4f, [3730, 1])
        assert inv.multiname(3730) == 'reset'
        assert ops[start - 5][1:] == (0x25, [420])
    # All six panels refresh real charge values rather than static substitutions.
    names = [inv.multiname(args[0]) for at, op, args in ops if op == 0x66
             and 29559 <= at <= 33479]
    for prefix in ('Freeze', 'Curse', 'Woe', 'Bolt', 'Beam', 'Barrage'):
        for suffix in ('ChargingTime', 'CurrentCharge', 'MaxCharge'):
            assert 'sp' + prefix + suffix in names


def test_spell_real_input_dispatch_and_charge_gates(spell_artifacts):
    _, _, _, _, _, abc, inv, _ = spell_artifacts
    bodies = _bodies(abc)
    keyboard = {at: (op, args) for at, op, args in inv.instructions(bodies[703][3], 703)}
    for number in range(1, 7):
        at = 1460 + (number - 1) * 61
        assert inv.multiname(keyboard[at][1][0]) == f'NUM_{number}'
        assert inv.multiname(keyboard[at + 14][1][0]) == 'arrIsSpellBtnVisible'
        assert keyboard[at + 17] == (0x24, [14 + number])
        call = keyboard[at + 43]
        assert call[0] == 0x4f and call[1][1] == 2
        assert inv.multiname(call[1][0]) == (
            'initiateCastStrikeSpell' if number <= 3 else 'initiateCastGemEnhancement')
    for method, gates, names in (
            (739, (337,371,405), ('Freeze','Curse','Woe')),
            (738, (555,589,623), ('Bolt','Beam','Barrage'))):
        actions = {at: (op, args) for at, op, args in inv.instructions(bodies[method][3], method)}
        for at, name in zip(gates, names):
            assert actions[at] == (0xad, [])  # less-than one full charge
            assert actions[at - 2] == (0x24, [1])
            assert inv.multiname(actions[at - 9][1][0]) == f'sp{name}CurrentCharge'
        # These methods contain no visible insufficient-mana/active prompt:
        # the only literal is the internal sound asset key.
        visible = [inv.strings[args[0]] for _, op, args in inv.instructions(bodies[method][3], method)
                   if op == 0x2c]
        assert visible == ['sndspellinitiate']


def test_spell_complete_dynamic_panels_outline_neighbors_and_scales(spell_artifacts):
    root, original, baseline, candidate, entries, abc, inv, ops = spell_artifacts
    bodies = _bodies(abc)
    # These are actual McInfoPanel field, separator, padding and position rules.
    renderer = {at: (op, args) for at, op, args in inv.instructions(bodies[3504][3], 3504)}
    assert method_metadata(abc)[3504][4] == [(10, 10), (6, 3), (12, 12), (2, 3)]
    assert renderer[199] == (0x24, [20])
    assert renderer[846] == (0x24, [253]) and renderer[853] == (0x24, [9])
    assert renderer[869] == (0x24, [8])
    separator = {at: (op, args) for at, op, args in inv.instructions(bodies[3503][3], 3503)}
    assert separator[20] == (0x66, [3708]) and separator[24] == (0xa0, [])
    assert separator[25] == (0x4f, [5282, 1])
    extra = {at: (op, args) for at, op, args in inv.instructions(bodies[3505][3], 3505)}
    assert extra[25] == (0x61, [3708]) and extra[38] == (0x61, [3710])
    position = {at: (op, args) for at, op, args in inv.instructions(bodies[3507][3], 3507)}
    assert position[295] == (0x24, [58])
    assert position[430] == (0x25, [612]) and position[438] == (0x24, [10])
    fonts = [{int.from_bytes(t.payload[:2], 'little'): parse_font(t.payload)
              for t in read_swf(data)[1] if t.code == 75}
             for data in (original, baseline, candidate)]
    models = (fonts[0][255], fonts[1][255], fonts[2][6002])
    plan = load_catalog(PLAN)
    for key in EXPECTED_IDS:
        check_layout(models[2], entries[key]['translation'], plan['layouts'][key])

    @lru_cache(maxsize=None)
    def field(font_number, text, size):
        font = models[font_number]
        layout = check_layout(font, text, {'height': size * 20, 'max_width': 7920,
                                           'wrap': True, 'max_lines': 40})
        lines = []
        for paragraph in text.split('\n'):
            line = ''
            for word in paragraph.split(' '):
                joined = line + (' ' if line else '') + word
                if measure_text(font, joined, size * 20)['widths'][0] > 7920:
                    assert line, ('unbreakable word', word)
                    lines.append(line)
                    line = word
                else:
                    line = joined
            lines.append(line)
        assert len(lines) == layout['lines']
        measured = measure_text(font, '\n'.join(lines), size * 20)
        assert max(measured['widths']) <= 7920
        return measured, lines

    numeric_events = {29679,29703,29746,29789,29833,29899,29946,29993,
                      30302,30326,30369,30412,30456,30500,30544,30631,
                      30678,30725,31008,31048,31088,31128,31235,31282,
                      31329,31770,31835,32013,32060,32107,32436,32506,
                      32688,32735,32782,33092,33178,33356,33403,33450}
    assert {at for at, op, args in ops if op == 0x46
            and any(lo <= at <= hi for lo, hi in RANGES)
            and inv.multiname(args[0]) in ('format', 'round')} == numeric_events
    bitmap = {at: (op, args) for at, op, args in inv.instructions(bodies[3508][3], 3508)}
    assert bitmap[97] == (0x24, [1]) and bitmap[105] == (0x93, [])
    assert bitmap[108] == (0xa2, []) and bitmap[1001] == (0xa3, [])
    evidence = []
    # Representative formatting values include zero, fractional values,
    # comma grouping and the long scientific form used by the formatter.
    for number in ('0', '1', '21', '100', '999.9', '1,000', '1.0000000 e12'):
        for lo, hi in RANGES:
            heights = []
            for model_number, font in enumerate(models):
                y, previous_bottom, tokens, fields = 10., None, [], []
                for i, (at, op, args) in enumerate(ops):
                    if not lo <= at <= hi:
                        continue
                    if op == 0x2c:
                        key = f'abc.4175.630.{at}'
                        tokens.append(entries[key]['translation'] if model_number == 2 and at in OFFSETS
                                      else inv.strings[args[0]])
                    elif op == 0x46 and inv.multiname(args[0]) in ('format', 'round'):
                        tokens.append(number)
                    elif op == 0x4f and args[0] == 3372:
                        assert ops[i - 1][1] == 0x24
                        y += ops[i - 1][2][0]
                    elif op == 0x4f and args[0] == 3729:
                        size_index = next(j for j in range(i - 1, i - 25, -1)
                                          if ops[j][1] == 0x24 and ops[j - 1][1] in (0x26, 0x27))
                        size = ops[size_index][2][0]
                        compact = ops[size_index - 1][1] == 0x26
                        value = ''.join(tokens)
                        tokens = []
                        measured, lines = field(model_number, value, size)
                        bounds = [b for b in measured['bounds'] if b]
                        if bounds:
                            origin = y + 2 + font['ascent'] * size / 20480
                            top = origin + min(b[2] for b in bounds) / 20
                            bottom = origin + max(b[3] for b in bounds) / 20
                            assert previous_bottom is None or previous_bottom <= top, (lo, value, top, previous_bottom)
                            previous_bottom = bottom
                        field_ops = [a for a, _, _ in ops if (fields[-1]['call_offset'] if fields else lo - 1) < a <= at]
                        expected_numbers = len(numeric_events.intersection(field_ops))
                        assert value.count(number) >= expected_numbers, (at, value, expected_numbers)
                        fields.append({'call_offset': at, 'text': value, 'size': size, 'compact': compact,
                                       'y': y, 'lines': len(lines)})
                        y += (len(lines) * measured['line_height'] + 80) / 20 + (-3 if compact else 9)
                heights.append(y + 8)
            for zoom in (1, .75, .5):
                factor = (1 + zoom) / (2 * zoom)
                old, base, new = [h * factor for h in heights]
                assert 420 * factor <= 1068
                for threshold in (544, 602):
                    for reference in (old, base):
                        assert not reference <= threshold < new, (lo, number, zoom, heights, threshold)
                # Toolbar hover is in y8..41. The final bottom clamp can move
                # panels above preferred y58; y0 is the absolute viewport edge.
                placement = []
                for mouse_y in (8, 24, 41):
                    top = min(max(58, mouse_y - 7 - new), 602 - new)
                    assert top >= 0 and top + new <= 602
                    placement.append({'mouse_y': mouse_y, 'top': top, 'bottom': top + new})
                evidence.append({'range': [lo, hi], 'value': number, 'zoom': zoom,
                                 'original_height': old, 'baseline_height': base,
                                 'candidate_height': new, 'available_height': 544,
                                 'candidate_margin': 544 - new, 'absolute_viewport_margin': 602 - new,
                                 'placement': placement, 'fields': fields})
    (root / 'spells-geometry.json').write_text(json.dumps({
        'candidate_sha256': sha(candidate), 'panels': 6, 'models': len(evidence),
        'comparisons': evidence,
        'limitations': 'Source-guarded offline outline model; representative values are not gameplay limits. AIR rasterization, separator raster and hover placement require runtime acceptance.'
    }, ensure_ascii=False, indent=2) + '\n')
