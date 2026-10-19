"""Opt-in HUD checks against existing private build artifacts; never starts the game."""
import json
import os
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from addressed_localization import check_layout
from install_patch import ORIGINAL_SHA256, sha
from inventory_strings import extract_inventory
from localization_fonts import measure_text
from localization_plan import resolve_plan_font_bindings
from swf_geometry import parse_font, read_swf
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory, resolved_operations
from translation_catalog import load_catalog
from versioned_catalog import restore_workspace

pytestmark = pytest.mark.local
BASE_COMMIT = 'c823d8420029bd04b95927e14a759b26e2d7a272'
BASE_SHA = 'ef901fc15fee8f27c8d74519a61fc1550a9dce11e2f8b630636c17a24f672d76'
METHODS = {545, 548, 555}


def previous_json(path):
    return json.loads(subprocess.check_output(['git', 'show', f'{BASE_COMMIT}:{path}']))


def preserve_method(before, after, method, snippets, extra_stack):
    """Every old operation, numeric operand, exception and branch survives insertion."""
    old, new = _bodies(before.reader.data)[method], _bodies(after.reader.data)[method]
    old_ops = before.instructions(old[3], method)
    new_ops = {at: (op, args) for at, op, args in after.instructions(new[3], method)}

    def boundary(at):
        return at + sum(len(data) for offset, data in snippets if offset < at)

    covered = set()
    for at, data in snippets:
        start = boundary(at)
        assert new[3][start:start + len(data)] == data
        covered.update(start + offset for offset, _, _ in after.instructions(data, method))
    for index, (at, opcode, args) in enumerate(old_ops):
        relocated = boundary(at) + sum(len(data) for offset, data in snippets if offset == at)
        assert relocated not in covered
        covered.add(relocated)
        new_opcode, new_args = new_ops[relocated]
        assert opcode == new_opcode
        end = old_ops[index + 1][0] if index + 1 < len(old_ops) else len(old[3])
        if 0x0c <= opcode <= 0x1a:
            assert relocated + end - at + new_args[0] == boundary(end + args[0])
        elif opcode == 0x1b:
            assert args[1] == new_args[1]
            assert [relocated + delta for delta in (new_args[0], *new_args[2:])] == [
                boundary(at + delta) for delta in (args[0], *args[2:])]
        elif opcode == 0x2c:
            assert before.strings[args[0]] == after.strings[new_args[0]]
        else:
            assert args == new_args
    assert covered == set(new_ops)
    assert len(new[3]) == len(old[3]) + sum(len(data) for _, data in snippets)
    assert new[2] == [old[2][0] + extra_stack, *old[2][1:]]
    assert new[4] == [[*(boundary(at) for at in row[:3]), *row[3:]] for row in old[4]]
    assert new[5] == old[5]


@pytest.fixture(scope='module')
def hud_artifacts():
    if not os.environ.get('GEMCRAFT_PR19_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR19_ROOT and GEMCRAFT_ORIGINAL for private offline HUD checks')
    root = Path(os.environ['GEMCRAFT_PR19_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    candidate_dir = root / 'candidate'
    candidate = (candidate_dir / 'gc-cs-steam.ru.swf').read_bytes()
    workspace = load_catalog(candidate_dir / 'workspace.json')
    return root, original, candidate, workspace


def test_hud_artifacts_preserve_catalog_guards_resources_and_logic(hud_artifacts):
    root, original, candidate, workspace = hud_artifacts
    manifest = load_catalog('translations/build-manifest.json')
    previous_manifest = previous_json('translations/build-manifest.json')
    assert len(previous_manifest['plans']) == 13
    assert manifest['plans'][:13] == previous_manifest['plans']
    assert len(manifest['plans']) == 14
    for row in manifest['plans']:
        assert sha((Path('translations') / row['path']).read_bytes()) == row['sha256']
    old_manual = {row['id']: row for row in previous_json('translations/catalog.json')['entries']}
    manual = {row['id']: row for row in load_catalog('translations/catalog.json')['entries']}
    assert sum(isinstance(row['manual'].get('translation'), str) for row in old_manual.values()) == 1934
    original_inventory = extract_inventory(original, ORIGINAL_SHA256)
    restored_old = {row['id']: row for row in restore_workspace(
        previous_json('translations/catalog.json'), original_inventory)['entries']}
    restored_new = {row['id']: row for row in restore_workspace(
        load_catalog('translations/catalog.json'), original_inventory)['entries']}
    assert set(old_manual) <= set(manual)
    plan = load_catalog('translations/plans/pr19-battle-hud.json')
    assert all(restored_old[key] == restored_new[key]
               for key in old_manual if key not in plan['entry_ids'])
    merged = load_catalog(root / 'candidate/plan.json')
    base_plan = load_catalog(root / 'baseline/plan.json')
    assert len(base_plan['entry_ids']) == 1860
    assert set(merged['entry_ids']) == set(base_plan['entry_ids']) | set(plan['entry_ids'])
    assert set(base_plan['entry_ids']).isdisjoint(plan['entry_ids'])
    assert manifest['applied_translations'] == 1860 + len(plan['entry_ids'])
    reports = [load_catalog(root / 'candidate' / name) for name in (
        'static-validation.json', 'on-disk-validation.json')]
    assert reports[0] == reports[1]
    report = reports[0]
    assert report['patched_sha256'] == sha(candidate) == manifest['expected_candidate_sha256']
    assert set(report['applied_entry_ids']) == set(merged['entry_ids'])
    baseline_paths = list((root / 'baseline').glob('*.swf'))
    assert len(baseline_paths) == 1 and sha(baseline_paths[0].read_bytes()) == BASE_SHA
    entries = {row['id']: row for row in workspace['entries']}
    fresh = {row['id']: row for row in original_inventory['entries']}
    visible = {identifier for identifier, row in fresh.items()
               if row['location'].get('method_index') in METHODS
               and any('a' <= char.lower() <= 'z' for char in row['source'])}
    separators = {f'abc.4175.548.{offset}' for offset in (408, 473, 492)}
    assert len(visible) == 120
    assert set(plan['entry_ids']) == visible | separators
    for identifier in plan['entry_ids']:
        entry = entries[identifier]
        assert entry['translation'] and entry['classification'] == 'confirmed'
        assert entry['context_confidence'] == 'confirmed'
        assert Counter(char for char in entry['source'] if ord(char) < 32) == Counter(
            char for char in entry['translation'] if ord(char) < 32), identifier
        for key in ('source', 'source_sha256', 'location'):
            assert entry[key] == fresh[identifier][key]
    assert {entries[i]['location']['method_index'] for i in plan['entry_ids']} == METHODS
    tags = [read_swf(data)[1] for data in (original, baseline_paths[0].read_bytes(), candidate)]
    original_abc, base_abc, candidate_abc = [abc_bytes(items) for items in tags]
    before, base, after = [abc_inventory(data) for data in (original_abc, base_abc, candidate_abc)]
    metadata = method_metadata(original_abc)
    assert metadata == method_metadata(candidate_abc)
    assert metadata[3504][1] == [7, 2, 8, 7, 11, 7]
    assert metadata[3504][4] == [(10, 10), (6, 3), (12, 12), (2, 3)]
    assert metadata[3506][4] == [(7, 3), (3, 6)]
    assert after.strings[:len(before.strings)] == before.strings
    assert after.multinames[:len(before.multinames)] == before.multinames
    old_bodies, new_bodies = _bodies(base_abc), _bodies(candidate_abc)
    assert old_bodies.keys() == new_bodies.keys()
    for method, body in old_bodies.items():
        if method not in METHODS:
            assert resolved_operations(base, base.instructions(body[3], method)) == (
                resolved_operations(after, after.instructions(new_bodies[method][3], method)))
            assert body[2] == new_bodies[method][2]
            assert body[4:] == new_bodies[method][4:]
    selected = [entries[i] for i in merged['entry_ids'] if i.startswith('abc.')]
    selected += [{**fresh[i], 'translation': ''} for i in merged.get('empty_abc_strings', [])]
    translated = patch_pushstrings(original_abc, selected)
    translated_inventory = abc_inventory(translated)
    make_snippets, increments, _ = resolve_plan_font_bindings(
        original_abc, translated, merged['abc_font_bindings'])
    snippets = make_snippets(len(translated_inventory.strings), len(translated_inventory.multinames))
    for method in METHODS:
        preserve_method(translated_inventory, after, method,
                        snippets.get(method, []), increments.get(method, 0))
    sites = plan['abc_font_bindings']
    for method, count, receiver in ((548, 63, '620666c606'), (555, 2, 'd266c606')):
        calls = [(index, args) for index, (_, opcode, args) in enumerate(before.instructions(
            _bodies(original_abc)[method][3], method)) if opcode == 0x4f and args[0] == 3729]
        assert len(calls) == count
        for index, _ in calls:
            pair = [site for site in sites if site['method_index'] == method
                    and site['instruction_index'] in (index, index + 1)]
            assert len(pair) == 2
            assert {(site['instruction_index'], site['kind']) for site in pair} == {
                (index, 'panel_format_before'), (index + 1, 'panel_format_after')}
            assert all(site['receiver_hex'] == receiver and site['extra_stack'] >= 7 for site in pair)
    assert not [row for row in after.unsupported + after.control_flow_anomalies
                if row['method_index'] in METHODS]
    def preserved(items):
        return [(tag.code, tag.payload) for tag in items
                if tag.code != 82 and not (tag.code == 75
                    and int.from_bytes(tag.payload[:2], 'little') in (6001, 6002))]
    assert preserved(tags[1]) == preserved(tags[2])
    fonts = [{int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
              for tag in items if tag.code == 75} for items in tags[1:]]
    for font_id in (6001, 6002):
        old, new = [font_set[font_id] for font_set in fonts]
        indices = {code: index for index, code in enumerate(new['codes'])}
        for index, code in enumerate(old['codes']):
            for key in ('glyphs', 'advances', 'bounds'):
                assert old[key][index] == new[key][indices[code]]
        assert set(map(tuple, old['kerning'])) <= set(map(tuple, new['kerning']))


def dynamic_samples(entries):
    """Exercise the actual fragment order with representative formatter outputs."""
    def t(offset):
        return entries[f'abc.4175.548.{offset}']['translation']

    samples = []
    for n, percentage, duration, count, trap_special, trap_speed in zip(
            ('1', '2', '5', '1,000', '1,234,567'),
            ('1', '2', '5', '12.5', '50'),
            ('1', '2', '5', '1.5', '12.5'),
            ('1', '2', '5', '21', '100'),
            ('80', '90', '100', '112.5', '130'),
            ('60', '70', '80', '90', '100')):
        pieces = [
            ('trap', [671, '-80', 677, trap_special, 720, trap_speed, 765]),
            ('amplifier', [951, percentage, 966, percentage, 982, percentage, 998]),
            ('damage', [1025, n, '-', n]),
            ('speed', [duration, 1113]),
            ('range', [1219, duration, ' = ', percentage, '%']),
            ('poison', ['+', n, 1646]),
            ('healing', [1692, count, 1711]),
            ('critical', [percentage, 1796, duration, 1822]),
            ('slow', [1868, percentage, 1894, duration, 1920]),
            ('chain', [count, 1984]),
            ('mana', [2030, duration, 2052]),
            ('armor', [2098, duration, 2120]),
            ('bloodbound', [2167, duration, 2199]),
            ('blood-increment', ['(+', duration, 2241]),
            ('blood-threshold', [2260, count, 2279]),
            ('poolbound', [2336, duration, 2373]),
            ('pool-increment', ['(+', duration, 2415]),
            ('bomb', [2538, n, 2610, 2623, duration, 2644]),
            ('structure', [2681, n, 2754]),
            ('wasp-count', [2769, '\n(', percentage, 2843]),
            ('wasp-limits', [2868, duration, 2892, count, 2958, duration, 3004, '\n+', percentage, 3075]),
            ('pure', [3191, '+', percentage, 3241, '+', percentage, 3292]),
            ('dual', [3402, '+', percentage, 3539, '+', percentage, 3593]),
            ('triple', [3433, '+', percentage, 3539, '+', percentage, 3593]),
            ('quad', [3448, '+', percentage, 3539, '+', percentage, 3593]),
            ('enhancement-time', [duration, 5135]),
            ('enhancement-ammo', [count, 5215]),
            ('hits-kills', [5256, count, 5275, count]),
            ('mana-value', [5369, n]),
            ('upgrade-cost', [5445, n]),
        ]
        pieces += [(f'amplifier-bonus-{offset}', ['+', percentage if offset == 4651 else duration, offset]) for offset in (
            3881, 3946, 4006, 4053, 4127, 4182, 4293, 4392, 4456, 4517,
            4581, 4651, 4715, 4776, 4840)]
        for label, parts in pieces:
            samples.append((label, ''.join(t(p) if isinstance(p, int) else p for p in parts)))
    for n in ('1', '2', '5', '21', '1,000'):
        samples.append(('grade', t(213) + n + t(228)))
    colors = [t(offset) for offset in (247, 250, 253, 256, 259, 262, 265, 268, 271)]
    samples += [('pure-color', t(340) + color) for color in colors]
    samples += [('dual-color', t(390) + a + t(408) + b)
                for a in colors for b in colors if a != b]
    samples += [('triple-color', a + t(473) + b + t(492) + c)
                for a, b, c in (colors[:3], colors[3:6], colors[6:])]
    samples += [('component-count', str(n) + t(534)) for n in range(4, 10)]
    samples += [('component-modifier', '\n' + str(n) + t(3693) + '50' + t(3729))
                for n in range(5, 10)]
    for prefix, offsets in ((4906, (4909, 4912, 4915)), (5060, (5063, 5066, 5069))):
        samples += [('enhancement', t(prefix) + t(offset)) for offset in offsets]
    for offset in (4960, 4999, 5038, 5175):
        samples.append(('enhancement-state', t(offset)))
    priorities = [entries[f'abc.4175.545.{offset}']['translation'] for offset in range(11, 33, 3)]
    samples += [('target-priority', t(5310) + priority) for priority in priorities]
    samples += [('priority-selection', priority) for priority in priorities]
    samples.append(('priority-drag', entries['abc.4175.555.59']['translation']))
    return samples


def test_hud_full_dynamic_strings_and_outline_geometry(hud_artifacts):
    root, _, candidate, workspace = hud_artifacts
    entries = {row['id']: row for row in workspace['entries']}
    plan = load_catalog('translations/plans/pr19-battle-hud.json')
    fonts = {int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
             for tag in read_swf(candidate)[1] if tag.code == 75}
    bold = fonts[6002]
    evidence = []
    # Measure every selected fragment, including explicit newlines, in its recipe.
    for identifier in plan['entry_ids']:
        layout = plan['layouts'][identifier]
        face = fonts[6001 if layout['font_id'] == 1 else 6002]
        check_layout(face, entries[identifier]['translation'], layout)
    samples = dynamic_samples(entries)
    assert ('grade', 'Самоцвет 1-го уровня') in samples
    assert ('slow', 'Замедляет цель на 2% на 2 с') in samples
    assert ('chain', '5 — длина цепного удара') in samples
    assert ('enhancement-state', 'Остался 1 выстрел') in samples
    for label, text in samples:
        # m3504: w-20 field width, x=10, Flash's 2px gutters on both sides;
        # default size12, inherited bold=true. Samples use this largest usual size.
        width = 256 if label.startswith('priority-') else 326
        result = check_layout(bold, text, {
            'height': 240, 'max_width': width * 20, 'max_lines': 30, 'wrap': True})
        assert all(ord(char) in bold['codes'] for char in text if char not in '\n\r\t')
        words = []
        for paragraph in text.split('\n'):
            line = ''
            for word in paragraph.split(' '):
                joined = line + (' ' if line else '') + word
                if measure_text(bold, joined, 240)['widths'][0] > width * 20:
                    words.append(line)
                    line = word
                else:
                    line = joined
            words.append(line)
        measured = measure_text(bold, '\n'.join(words), 240)
        assert len(words) == result['lines']
        # Conservative field-height model uses full font line boxes plus gutters.
        # The compact successor starts height-3 below this field; verify its ink
        # cannot collide even with those three overlapping pixels of field boxes.
        ascent = bold['ascent'] * 240 / 20480
        field_height = len(words) * measured['line_height'] + 80
        bounds = [box for box in measured['bounds'] if box]
        if bounds:
            bottom = 40 + ascent + max(box[3] for box in bounds)
            next_top = field_height - 60 + 40 + ascent + min(box[2] for box in bounds)
            assert bottom <= next_top
        evidence.append({'state': label, 'text': text, 'lines': result['lines'],
                         'width_twips': width * 20, 'model_field_height_twips': field_height,
                         'ink_bounds_twips': measured['bounds']})
    (root / 'test-evidence.json').write_text(json.dumps({
        'candidate_sha256': sha(candidate), 'samples': evidence,
        'limitations': 'Conservative outline/line-box model; Flash runtime layout, '
                       'complete panel viewport height and positioning still need visual acceptance.'
    }, ensure_ascii=False, indent=2) + '\n')


def panel_rows(entries, component_count, location, modifiers, amplified, enhancement, bomb):
    """Reachable m548 branches, represented as (parts, size, compact) or extra px."""
    def row(parts, size=11, compact=True):
        return (''.join(entries[f'abc.4175.548.{p}'].get('translation') or
                        entries[f'abc.4175.548.{p}']['source']
                        if isinstance(p, int) else p for p in parts), size, compact)

    colors = (247, 250, 253, 256, 259, 262, 265, 268, 271)[:component_count]
    rows = [row([213, '5', 228], 12)]
    if component_count == 1:
        rows.append(row([340, colors[0]], 10, False))
    elif component_count == 2:
        rows.append(row([390, colors[0], 408, colors[1]], 10, False))
    elif component_count == 3:
        rows.append(row([colors[0], 473, colors[1], 492, colors[2]], 10, False))
    else:
        rows.append(row([str(component_count), 534], 10, False))
    if location == 'trap':
        # pushbyte 176 is signed -80 in AVM2, not an unsigned damage bonus.
        rows.append(row([671, '-80', 677, '80', 720, '60', 765], 12, False))
    elif location == 'amplifier':
        rows += [row([933]), row([951, '10.0', 966, '10.0', 982, '10.0', 998], 11, False)]
    rows.append(row([1025, '100', '-', '200'], 12))
    if location != 'trap':
        rows.append(row([1219, '5'], 12))
    rows += [row(['2.50', 1113], 12, False), 5]
    effects = [
        [2030, '5', 2052], ['10', 1796, '2', 1822], [2336, '1.25', 2373],
        ['3', 1984], ['+', '100', 1646], [1692, '100', 1711],
        [2167, '1.25', 2199], [1868, '20', 1894, '3', 1920], [2098, '5', 2120]]
    for index in range(component_count):
        rows.append(row(effects[index]))
        if index == 2:
            rows.append(row(['(+', '0.025', 2415], 10))
        if index == 6:
            rows += [row(['(+', '0.025', 2241], 10), row([2260, '1,000', 2279], 10)]
    rows.append(16)
    if bomb:
        rows += [row([2483], 13, False),
                 row([2538, '100', 2610, 2623, '3', 2644], 11, True),
                 row([2681, '100', 2754], 11, False),
                 row([2769, '\n(', '20', 2843], 11, False),
                 row([2868, '10', 2892, '5', 2958, '1.5', 3004,
                      '\n+', '10', 3075], 11, False)]
        return rows
    if modifiers:
        if component_count < 5:
            heading = {1: 3191, 2: 3402, 3: 3433, 4: 3448}[component_count]
            rows.append(row([heading, '+', '20', 3241, '+', '20', 3292], 10, False))
        else:
            rows.append(row(['\n', str(component_count), 3693, '20', 3729], 10, False))
        rows.append(5)
    if amplified:
        rows += [row([3788], 10), row(['+', '100', 3881], 10), row(['+', '100', 4006], 10)]
        if location != 'trap':
            rows += [row(['+', '1', 4127], 10), row(['+', '0.50', 4182], 10)]
        bonus_offsets = (4581, 4392, 4840, 4293, 4517, 4776, 4715, 4651, 4456)
        rows += [row(['+', '5', offset], 10) for offset in bonus_offsets[:component_count]]
        rows.append(17)
    if enhancement is not None:
        if location == 'trap':
            rows += [row([4906, (4909, 4912, 4915)[enhancement]]),
                     row([(4999, 4960, 5038)[enhancement]], 11, False)]
        else:
            rows += [row([5060, (5063, 5066, 5069)[enhancement]]),
                     row(['5', 5135 if enhancement == 1 else 5215], 11, False)]
        rows.append(5)
    rows += [row([5256, '100', 5275, '5'], 10),
             row([5310, entries['abc.4175.545.11'].get('translation') or
                  entries['abc.4175.545.11']['source']], 10),
             row([5369, '1,000'], 10), row([5445, '2,000'], 10, False)]
    return rows


def test_hud_reachable_whole_panel_height_comparison(hud_artifacts):
    from functools import lru_cache

    root, original, candidate, workspace = hud_artifacts
    entries = {row['id']: row for row in workspace['entries']}
    source_entries = {key: {**row, 'translation': None} for key, row in entries.items()}
    original_abc = abc_bytes(read_swf(original)[1])
    original_inventory = abc_inventory(original_abc)
    operations = original_inventory.instructions(_bodies(original_abc)[548][3], 548)
    by_offset = {offset: index for index, (offset, _, _) in enumerate(operations)}
    constants = original_inventory.instructions(_bodies(original_abc)[1533][3], 1533)
    constants = {offset: (opcode, args) for offset, opcode, args in constants}
    assert constants[81] == (0x24, [5]) and constants[83] == (0x61, [5015])
    assert original_inventory.multiname(5015) == 'SUPPRESSING'
    assert constants[109] == (0x24, [7]) and constants[111] == (0x61, [5018])
    assert original_inventory.multiname(5018) == 'SLOWING'
    assert original_inventory.strings[operations[by_offset[262]][2][0]] == 'cyan'
    assert original_inventory.strings[operations[by_offset[268]][2][0]] == 'blue'
    # Guard the actual field parameter order used by this independent model.
    specs = {
        235: (12, True), 582: (10, False), 619: (10, False), 640: (10, False),
        939: (11, True), 1005: (11, False), 1087: (12, True), 1124: (12, False),
        1207: (12, True), 1298: (12, True), 1331: (12, False),
        1409: (12, True), 1566: (12, False),
        1654: (11, True), 1720: (11, True), 1830: (11, True), 1928: (11, True),
        1992: (11, True), 2060: (11, True), 2128: (11, True), 2207: (11, True),
        2248: (10, True), 2286: (10, True), 2381: (11, True), 2422: (10, True),
        2669: (11, True), 2765: (11, False), 2864: (11, False), 3100: (11, False),
        3328: (10, False), 3629: (10, False), 3736: (10, False),
        3794: (10, True), 3892: (10, True), 3957: (10, True), 4017: (10, True),
        4064: (10, True), 4138: (10, True), 4193: (10, True), 4304: (10, True),
        4403: (10, True), 4467: (10, True), 4528: (10, True), 4592: (10, True),
        4662: (10, True), 4726: (10, True), 4787: (10, True), 4851: (10, True),
        4931: (11, True), 4966: (11, False), 5005: (11, False), 5044: (11, False),
        5085: (11, True), 5142: (11, False), 5181: (11, False), 5222: (11, False),
        5298: (10, True), 5327: (10, True), 5391: (10, True), 5479: (10, False),
    }
    for offset, (size, compact) in specs.items():
        index = by_offset[offset]
        _, opcode, args = operations[index]
        assert opcode == 0x4f and args[0] == 3729
        tail = {4: 2, 5: 3, 6: 4}[args[1]]
        assert operations[index - tail][1:] == (0x26 if compact else 0x27, []), (
            offset, operations[index - tail], size, compact)
        assert operations[index - tail + 1][1:] == (0x24, [size])
    assert operations[by_offset[1497]][1:] == (0x26, [])
    assert operations[by_offset[1498]][1:] == (0x24, [12])
    assert operations[by_offset[1529]][1:] == (0x4f, [3729, 6])
    assert operations[by_offset[2521] - 15][1:] == (0x27, [])
    assert operations[by_offset[2521] - 14][1:] == (0x24, [13])
    for offset, extra in ((1577, 5), (2433, 16), (3339, 5), (3640, 5),
                          (3747, 5), (4862, 17), (5233, 5)):
        index = by_offset[offset]
        assert operations[index][1:] == (0x4f, [3372, 1])
        assert operations[index - 1][1:] == (0x24, [extra])
    fonts = {}
    for key, data, font_id in (('original', original, 255), ('candidate', candidate, 6002)):
        fonts[key] = next(parse_font(tag.payload) for tag in read_swf(data)[1]
                          if tag.code == 75 and int.from_bytes(tag.payload[:2], 'little') == font_id)

    @lru_cache(maxsize=None)
    def height(face, text, size):
        font = fonts[face]
        rows = []
        for paragraph in text.split('\n'):
            line = ''
            for word in paragraph.split(' '):
                joined = line + (' ' if line else '') + word
                if measure_text(font, joined, size * 20)['widths'][0] > 6520:
                    rows.append(line)
                    line = word
                else:
                    line = joined
            rows.append(line)
        return (len(rows) * measure_text(font, '', size * 20)['line_height'] + 80) / 20

    evidence = []
    regressions = []
    for count in (1, 2, 3, 4, 9):
        for location in ('tower', 'trap', 'amplifier'):
            for modifiers in (False, True):
                for amplified in ((False,) if location == 'amplifier' else (False, True)):
                    for enhancement in ((None,) if location == 'amplifier' else (None, 0, 1, 2)):
                        for bomb in (False, True):
                            # Bomb branch skips all later modifiers/amp/enhancement/footer.
                            if bomb and (modifiers or amplified or enhancement is not None):
                                continue
                            descriptor = {'components': count, 'location': location,
                                          'modifiers': modifiers, 'amplified': amplified,
                                          'enhancement': enhancement, 'bomb': bomb}
                            heights = {}
                            for face, texts in (('original', source_entries), ('candidate', entries)):
                                next_y = 10
                                for row in panel_rows(texts, count, location, modifiers,
                                                      amplified, enhancement, bomb):
                                    if isinstance(row, int):
                                        next_y += row
                                    else:
                                        text, size, compact = row
                                        next_y += height(face, text, size) + (-3 if compact else 9)
                                heights[face] = next_y + 8
                            record = {**descriptor, 'height_px_at_zoom_1': heights,
                                      'new_viewport_overflow': heights['original'] <= 544 < heights['candidate']}
                            evidence.append(record)
                            if record['new_viewport_overflow']:
                                regressions.append(record)
    (root / 'panel-height-evidence.json').write_text(json.dumps({
        'candidate_sha256': sha(candidate), 'samples': evidence, 'new_overflows': regressions,
        'model': 'Font ascent/descent/leading line boxes plus 4px gutters; m3504 compact -3 '
                 'or normal +9 and exact m548 extra-height branches. Representative moderate '
                 'numeric values; not upper limits. viewport 1088x612, usable y58..602. '
                 'zoom<1 scales height by (1+zoom)/(2*zoom); zoom>=1 unchanged. '
                 'Flash autoSize can differ; visual acceptance remains required.'
    }, ensure_ascii=False, indent=2) + '\n')
    assert not regressions, regressions[:5]
