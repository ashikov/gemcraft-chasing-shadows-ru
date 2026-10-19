"""Opt-in private artifact checks for building hover panels and build buttons.

No builds, installation writes or game launches are performed here.
"""
import json
import os
import struct
import subprocess
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCReader
from abc_patch import patch_pushstrings
from addressed_localization import check_layout
from install_patch import ORIGINAL_SHA256, sha
from inventory_strings import extract_inventory
from localization_fonts import measure_text
from localization_plan import resolve_plan_font_bindings
from swf_geometry import parse_font, read_swf
from test_battle_buildings import BUTTON_OFFSETS, EXPECTED_IDS, HOVER_OFFSETS, PLAN
from test_battle_hud_local import preserve_method
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory, resolved_operations
from translation_catalog import load_catalog
from versioned_catalog import restore_workspace

pytestmark = pytest.mark.local
BASE_COMMIT = '0b2a2f4fa0b3776cbd805c2fed54fee71613ba9b'
BASE_SHA = '79a08c177825c972f4f05eea0ace76c855fe9517dadd7e47c23699227311b8e5'
RANGES = ((1700, 2404), (2608, 3017), (8356, 8645), (8708, 8807))


@pytest.fixture(scope='module')
def building_artifacts():
    if not os.environ.get('GEMCRAFT_PR20_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR20_ROOT and GEMCRAFT_ORIGINAL for offline building checks')
    root = Path(os.environ['GEMCRAFT_PR20_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    baseline_paths = list((root / 'baseline').glob('*.swf'))
    assert len(baseline_paths) == 1
    baseline = baseline_paths[0].read_bytes()
    assert sha(baseline) == BASE_SHA
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    workspace = load_catalog(root / 'candidate/workspace.json')
    return root, original, baseline, candidate, workspace


def previous_json(path):
    return json.loads(subprocess.check_output(['git', 'show', f'{BASE_COMMIT}:{path}']))


def test_buildings_catalog_manifest_and_on_disk_report(building_artifacts):
    root, original, _, candidate, workspace = building_artifacts
    plan = load_catalog(PLAN)
    manifest = load_catalog('translations/build-manifest.json')
    old_manifest = previous_json('translations/build-manifest.json')
    assert len(old_manifest['plans']) == 14
    assert manifest['plans'][:14] == old_manifest['plans']
    assert len(manifest['plans']) == 15
    for row in manifest['plans']:
        assert sha((Path('translations') / row['path']).read_bytes()) == row['sha256']
    old_catalog = previous_json('translations/catalog.json')
    catalog = load_catalog('translations/catalog.json')
    old_manual = {row['id']: row for row in old_catalog['entries']}
    manual = {row['id']: row for row in catalog['entries']}
    assert set(old_manual) <= set(manual)
    assert sum(isinstance(row['manual'].get('translation'), str)
               for row in old_manual.values()) == 2057
    assert sum(isinstance(row['manual'].get('translation'), str)
               for row in manual.values()) == 2116
    fresh = extract_inventory(original, ORIGINAL_SHA256)
    restored = restore_workspace(catalog, fresh)
    old_restored = {row['id']: row for row in restore_workspace(old_catalog, fresh)['entries']}
    new_restored = {row['id']: row for row in restored['entries']}
    assert all(row == new_restored[key] for key, row in old_restored.items()
               if key not in EXPECTED_IDS)
    entries = {row['id']: row for row in workspace['entries']}
    assert entries == {row['id']: row for row in restored['entries']}
    originals = {row['id']: row for row in fresh['entries']}
    for identifier in EXPECTED_IDS:
        for key in ('source', 'source_sha256', 'location'):
            assert entries[identifier][key] == originals[identifier][key]
        assert Counter(c for c in entries[identifier]['source'] if ord(c) < 32) == Counter(
            c for c in entries[identifier]['translation'] if ord(c) < 32)
    merged = load_catalog(root / 'candidate/plan.json')
    old_plan = load_catalog(root / 'baseline/plan.json')
    assert len(old_plan['entry_ids']) == 1983
    assert set(old_plan['entry_ids']).isdisjoint(EXPECTED_IDS)
    assert set(merged['entry_ids']) == set(old_plan['entry_ids']) | EXPECTED_IDS
    assert set(plan['entry_ids']) == EXPECTED_IDS
    assert manifest['applied_translations'] == len(merged['entry_ids']) == 2042
    static = load_catalog(root / 'candidate/static-validation.json')
    assert static == load_catalog(root / 'candidate/on-disk-validation.json')
    assert static['patched_sha256'] == sha(candidate) == manifest['expected_candidate_sha256']
    assert set(static['applied_entry_ids']) == set(merged['entry_ids'])


def test_buildings_literal_branches_bindings_and_unrelated_resources(building_artifacts):
    root, original, baseline, candidate, workspace = building_artifacts
    original_tags, baseline_tags, candidate_tags = [read_swf(data)[1]
                                                     for data in (original, baseline, candidate)]
    original_abc, baseline_abc, candidate_abc = [abc_bytes(tags) for tags in (
        original_tags, baseline_tags, candidate_tags)]
    before, base, after = [abc_inventory(data) for data in (
        original_abc, baseline_abc, candidate_abc)]
    # NumberFormatter.format defaults to zero decimal places. Above 1e12 it
    # selects four/eight significant digits via the existing preference, then
    # normalizes mantissa/exponent spacing. This method is also isolated below.
    assert method_metadata(original_abc)[1245][4] == [(1, 3), (10, 10)]
    reader = ABCReader(original_abc)
    reader.take(4)
    ints = [0] + [reader.encoded(32) for _ in range(1, reader.count())]
    assert ints[1] == 0
    for _ in range(1, reader.count()):
        reader.encoded(32)
    doubles = [float('nan')] + [struct.unpack('<d', reader.take(8))[0]
                               for _ in range(1, reader.count())]
    assert doubles[361] == 1e12
    formatter = {at: (opcode, args) for at, opcode, args in before.instructions(
        _bodies(original_abc)[1245][3], 1245)}
    assert formatter[121] == (0x2f, [361])
    assert formatter[124] == (0x0f, [41])
    for offset in (141, 310):
        assert formatter[offset][0] == 0x66
        assert before.multiname(formatter[offset][1][0]) == 'lotsOfDigitsForExpNums'
    assert formatter[148] == (0x24, [8])
    assert formatter[155] == (0x24, [4])
    assert formatter[158][0] == 0x46
    assert before.multiname(formatter[158][1][0]) == 'toPrecision'
    for offset, literal in ((331, '.0000000 e'), (389, '.000 e')):
        assert formatter[offset][0] == 0x2c
        assert before.strings[formatter[offset][1][0]] == literal
    operations = before.instructions(_bodies(original_abc)[630][3], 630)
    chosen = [(index, at, opcode, args) for lo, hi in RANGES
              for index, (at, opcode, args) in enumerate(operations[lo:hi], lo)]
    visible = {f'abc.4175.630.{at}' for _, at, opcode, args in chosen
               if opcode == 0x2c and any(c.isalpha() for c in before.strings[args[0]])}
    assert visible == EXPECTED_IDS
    calls = {index for index, _, opcode, args in chosen if opcode == 0x4f and args[0] == 3729}
    assert len(calls) == 55
    plan = load_catalog(PLAN)
    assert {site['instruction_index'] for site in plan['abc_font_bindings']
            if site['kind'] == 'panel_format_before'} == calls
    for site in plan['abc_font_bindings']:
        index = site['instruction_index']
        assert operations[index][1:] == (site['expected_opcode'], site['expected_operands'])
        for guard in site['receiver_guards']:
            assert operations[guard['instruction_index']][1:] == (guard['opcode'], guard['operands'])
    # Real numeric expressions: enough-mana branch clamps before NumberFormatter;
    # insufficient-mana branch appends two newlines after formatting the rounded cost.
    for start in (8417, 8511, 8605, 8770):
        assert [before.multiname(operations[index][2][0]) for index in (start + 7, start + 8)] == [
            'round', 'format']
        assert operations[start + 9][1] == 0xa0
        assert before.strings[operations[start + 10][2][0]].startswith('\n\n')
        assert operations[start + 11][1] == 0xa0
        enough = start + 17
        assert operations[enough + 3][1:] == (0x24, [0])
        assert [before.multiname(operations[index][2][0]) for index in (
            enough + 9, enough + 10, enough + 11)] == ['round', 'max', 'format']
    for index in (1750, 1874, 2385, 2802, 2998):
        assert [before.multiname(operations[index + delta][2][0]) for delta in (2, 3)] == [
            'core', 'demolitionsLeft']
        assert before.multiname(operations[index + 4][2][0]) == 'g'
        assert operations[index + 5][1:] == (0xa0, [])
    merged = load_catalog(root / 'candidate/plan.json')
    entries = {row['id']: row for row in workspace['entries']}
    fresh = {row['id']: row for row in extract_inventory(original, ORIGINAL_SHA256)['entries']}
    selected = [entries[key] for key in merged['entry_ids'] if key.startswith('abc.')]
    selected += [{**fresh[key], 'translation': ''} for key in merged.get('empty_abc_strings', [])]
    translated = patch_pushstrings(original_abc, selected)
    translated_inventory = abc_inventory(translated)
    make_snippets, increments, _ = resolve_plan_font_bindings(
        original_abc, translated, merged['abc_font_bindings'])
    snippets = make_snippets(len(translated_inventory.strings), len(translated_inventory.multinames))
    preserve_method(translated_inventory, after, 630, snippets[630], increments[630])
    assert method_metadata(baseline_abc) == method_metadata(candidate_abc)
    old_bodies, new_bodies = _bodies(baseline_abc), _bodies(candidate_abc)
    assert old_bodies.keys() == new_bodies.keys()
    for method, body in old_bodies.items():
        if method != 630:
            assert resolved_operations(base, base.instructions(body[3], method)) == resolved_operations(
                after, after.instructions(new_bodies[method][3], method))
            assert body[2] == new_bodies[method][2]
            assert body[4:] == new_bodies[method][4:]
    assert after.strings[:len(before.strings)] == before.strings
    assert after.multinames[:len(before.multinames)] == before.multinames
    assert not [row for row in after.unsupported + after.control_flow_anomalies
                if row['method_index'] == 630]
    def untouched(tags):
        return [(tag.code, tag.payload) for tag in tags if tag.code != 82 and not (
            tag.code == 75 and int.from_bytes(tag.payload[:2], 'little') in (6001, 6002))]
    assert untouched(baseline_tags) == untouched(candidate_tags)
    fonts = [{int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
              for tag in tags if tag.code == 75} for tags in (baseline_tags, candidate_tags)]
    for font_id in (6001, 6002):
        old, new = [font_set[font_id] for font_set in fonts]
        positions = {code: index for index, code in enumerate(new['codes'])}
        for index, code in enumerate(old['codes']):
            for key in ('glyphs', 'advances', 'bounds'):
                assert old[key][index] == new[key][positions[code]]
        assert set(map(tuple, old['kerning'])) <= set(map(tuple, new['kerning']))


def test_buildings_complete_strings_and_outline_geometry(building_artifacts):
    root, original, _, candidate, workspace = building_artifacts
    entries = {row['id']: row for row in workspace['entries']}
    plan = load_catalog(PLAN)
    fonts = {int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
             for tag in read_swf(candidate)[1] if tag.code == 75}
    bold = fonts[6002]
    for identifier in EXPECTED_IDS:
        check_layout(bold, entries[identifier]['translation'], plan['layouts'][identifier])
    def text(offset):
        return entries[f'abc.4175.630.{offset}']['translation']
    # Representative NumberFormatter outputs in both precision preferences;
    # scientific examples are samples, not numeric range limits. Enough-mana
    # arithmetic clamps signed inputs to zero before formatting. Negative costs
    # are not claimed to be reachable game states. The bytecode guards above
    # preserve the actual clamp, rounding and formatter selection.
    formatted_numbers = (
        '0', '1', '2', '5', '21', '1,000', '1,234,567',
        '1.000 e12', '1.0000000 e12', '9.999 e99', '9.9999999 e99')
    panels = []
    for heading, hotkey, description, shift, cost, warning, enough in (
            (21236, 21286, 21300, 21314, 21354, 21383, 21402),
            (21495, 21545, 21559, 21573, 21613, 21642, 21661),
            (21754, 21804, 21818, 21832, 21872, 21901, 21920),
            (22196, 22246, 22260, 22274, 22314, 22343, 22362)):
        for tutorial in (False, True):
            for affordable in (False, True):
                for number in formatted_numbers:
                    rows = [(text(heading), 14)]
                    if tutorial:
                        rows += [(text(offset), 12) for offset in (hotkey, description, shift)]
                    rows.append((text(enough if affordable else cost) + number + (
                        '' if affordable else text(warning)), 12))
                    panels.append((f'button-{heading}-{tutorial}-{affordable}-{number}', 426, rows, 5))
    for offset in (4134, 4476, 5783, 6873, 7382):
        for number in ('1', '2', '5', '21', '1000'):
            complete = text(offset) + number
            assert complete.endswith(' ' + number)
            assert 'сносов' in complete
    # Empty/Spiritforge-owned building panels. Occupied gem panels delegate to
    # m548; their whole-panel geometry is covered by test_battle_hud_local.
    # The Spiritforge branches return before path/demolition rows are considered.
    for heading, description in ((5044, 5078), (5173, 5207)):
        panels.append((f'spiritforge-{heading}', 256,
                       [(text(heading), 12), (text(description), 12)], 5))
    # With the wall-info preference disabled, bombing still exposes a titleless
    # warning/count panel and addExtraHeight(-2); otherwise this branch returns.
    for exhausted in (False, True):
        value = text(4116) if exhausted else text(4134) + '1000'
        panels.append((f'wall-disabled-bombing-{exhausted}', 256, [(value, 12)], -2))
    hover_variants = (
        (4239, (4273,)),
        (5261, (5295,)), (5261, (5295, 5353)),
        (6477, (6511,)), (6477, (6511, 6555)),
        (7033, (7067,)), (7033, (7067, 7111)),
    )
    for heading, details in hover_variants:
        wall = heading == 4239
        trap = heading == 7033
        for path in ((False,) if trap else (False, True)):
            for exhausted in (False, True):
                for bombing in (False, True):
                    rows = [(text(heading), 12)] + [(text(offset), 12) for offset in details]
                    if path:
                        rows.append((text(4320), 12))
                    if exhausted:
                        if bombing:
                            rows.append((text(4116), 12))
                    else:
                        if not wall:
                            rows.append((text(5717), 12))
                        if bombing:
                            rows.append((text(4134) + '1000', 12))
                    label = f'hover-{heading}-{details}-{path}-{exhausted}-{bombing}'
                    panels.append((label, 256, rows, 5))
    inventory = abc_inventory(abc_bytes(read_swf(original)[1]))
    bodies = _bodies(inventory.reader.data)
    # Guard the model's default size, spacing and panel margins against original.
    assert method_metadata(inventory.reader.data)[3504][4] == [
        (10, 10), (6, 3), (12, 12), (2, 3)]
    operations = inventory.instructions(bodies[3504][3], 3504)
    by_offset = {at: (op, args) for at, op, args in operations}
    assert by_offset[846] == (0x24, [253])  # signed -3 compact spacing
    assert by_offset[853] == (0x24, [9])
    assert by_offset[869] == (0x24, [8])
    assert inventory.multiname(by_offset[872][1][0]) == 'h'
    reset = {at: (op, args) for at, op, args in inventory.instructions(bodies[3506][3], 3506)}
    assert reset[82] == (0x24, [10])
    # Separator only records a y coordinate; it consumes no vertical space.
    separator = inventory.instructions(bodies[3503][3], 3503)
    assert not [args for _, opcode, args in separator if opcode == 0x61]
    @lru_cache(maxsize=None)
    def measure(value, height):
        return measure_text(bold, value, height)

    @lru_cache(maxsize=None)
    def field_layout(value, height, width):
        return check_layout(bold, value, {
            'height': height, 'max_width': width,
            'max_lines': 30, 'wrap': True})

    evidence = []
    for label, width, rows, extra in panels:
        y = 10.0
        previous_bottom = None
        for index, (value, size) in enumerate(rows):
            result = field_layout(value, size * 20, width * 20)
            wrapped = []
            for paragraph in value.split('\n'):
                line = ''
                for word in paragraph.split(' '):
                    joined = line + (' ' if line else '') + word
                    if measure(joined, size * 20)['widths'][0] > width * 20:
                        wrapped.append(line)
                        line = word
                    else:
                        line = joined
                wrapped.append(line)
            assert result['lines'] == len(wrapped)
            measured = measure('\n'.join(wrapped), size * 20)
            assert all(w <= width * 20 for w in measured['widths'])
            bounds = [bound for bound in measured['bounds'] if bound]
            ascent = bold['ascent'] * size / 20480
            if bounds:
                top = y + 2 + ascent + min(bound[2] for bound in bounds) / 20
                bottom = y + 2 + ascent + max(bound[3] for bound in bounds) / 20
                assert previous_bottom is None or previous_bottom <= top, label
                previous_bottom = bottom
            y += (len(wrapped) * measured['line_height'] + 80) / 20 + 9
            if index == 0:
                y += extra
        height = y + 8
        comparisons = []
        # m3508's source formula and its guards are tested in
        # test_battle_hud_local; zoom<1 increases logical panel height.
        for zoom in (1, 0.75, 0.5):
            factor = 1 if zoom >= 1 else (1 + zoom) / (2 * zoom)
            logical_height = height * factor
            for threshold in (544, 602):
                assert logical_height < threshold, (label, zoom, threshold, logical_height)
                comparisons.append({'zoom': zoom, 'threshold_px': threshold,
                                    'logical_height_px': logical_height})
        evidence.append({'state': label, 'height_px': height, 'fields': len(rows),
                         'threshold_comparisons': comparisons})
    assert HOVER_OFFSETS | BUTTON_OFFSETS == {int(key.rsplit('.', 1)[1]) for key in EXPECTED_IDS}
    (root / 'buildings-test-evidence.json').write_text(json.dumps({
        'candidate_sha256': sha(candidate), 'panels': evidence,
        'limitations': 'Outline and conservative line-box model. Flash runtime autoSize, '
                       'viewport positioning and actual neighbor rendering remain unverified. '
                       'Empty/Spiritforge and wall-preference branches are represented; '
                       'occupied gem branches are covered separately by PR19 tests.'
    }, ensure_ascii=False, indent=2) + '\n')
