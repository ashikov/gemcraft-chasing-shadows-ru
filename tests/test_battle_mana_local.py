"""Offline mana panel, counters and early-call notifications; no game execution."""
import itertools
import json
import os
from collections import Counter
from functools import lru_cache
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
from test_battle_mana import MANA_IDS, PLAN
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory, resolved_operations
from translation_catalog import load_catalog

pytestmark = pytest.mark.local
BASE_SHA = 'c7a8939d4955b4cb0471eec8d7b657a31446c806af97bb4188d6e6fe92414ec7'


@pytest.fixture(scope='module')
def mana_artifacts():
    if not os.environ.get('GEMCRAFT_PR21_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR21_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR21_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    base = (root / 'baseline/gc-cs-steam.ru.swf').read_bytes()
    assert sha(base) == BASE_SHA
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    assert sha(candidate) == load_catalog('translations/build-manifest.json')['expected_candidate_sha256']
    workspace = load_catalog(root / 'candidate/workspace.json')
    return root, original, base, candidate, workspace


def test_full_plan_preserves_resources_and_abc(mana_artifacts):
    root, original, base, candidate, workspace = mana_artifacts
    tags = [read_swf(data)[1] for data in (original, base, candidate)]
    original_abc, baseline_abc, candidate_abc = map(abc_bytes, tags)
    before, baseline, after = map(abc_inventory, (original_abc, baseline_abc, candidate_abc))
    merged = load_catalog(root / 'candidate/plan.json')
    selected_ids = set(merged['entry_ids'])
    selected = [e for e in workspace['entries'] if e['id'] in selected_ids and e['id'].startswith('abc.')]
    entries = {e['id']: e for e in workspace['entries']}
    inventory_entries = {row['id']: row for row in load_catalog(root / 'candidate/inventory.json')['entries']}
    selected += [{**inventory_entries[key], 'translation': ''} for key in merged['empty_abc_strings']]
    translated = patch_pushstrings(original_abc, selected)
    translated_inventory = abc_inventory(translated)
    snippet_builder, increments, _ = resolve_plan_font_bindings(
        original_abc, translated, merged['abc_font_bindings'])
    snippets = snippet_builder(len(translated_inventory.strings), len(translated_inventory.multinames))
    new_plan = load_catalog(PLAN)
    changed = {site['method_index'] for site in new_plan['abc_font_bindings']}
    changed |= {entries[key]['location']['method_index'] for key in new_plan['entry_ids']}
    assert {630, 760, 761} <= changed
    old_plan = load_catalog(root / 'baseline/plan.json')
    old_entries = {e['id']: e for e in load_catalog(root / 'baseline/workspace.json')['entries']}
    assert len(old_plan['entry_ids']) == 2042
    assert len(merged['entry_ids']) == 2122
    assert len(new_plan['entry_ids']) == 80
    assert set(old_plan['entry_ids']).isdisjoint(new_plan['entry_ids'])
    assert set(merged['entry_ids']) == set(old_plan['entry_ids']) | set(new_plan['entry_ids'])
    assert all(e == entries[key] for key, e in old_entries.items()
               if key not in new_plan['entry_ids'])
    assert sum(e['translation'] is not None for e in workspace['entries']) == 2196
    originals = {e['id']: e for e in load_catalog(root / 'baseline/inventory.json')['entries']}
    for key in new_plan['entry_ids']:
        for attribute in ('source', 'source_sha256', 'location'):
            assert entries[key][attribute] == originals[key][attribute]
        assert Counter(c for c in entries[key]['source'] if ord(c) < 32) == Counter(
            c for c in entries[key]['translation'] if ord(c) < 32)
    for method in changed:
        preserve_method(translated_inventory, after, method, snippets.get(method, []),
                        increments.get(method, 0))
    assert method_metadata(baseline_abc) == method_metadata(candidate_abc)
    old_bodies, new_bodies = _bodies(baseline_abc), _bodies(candidate_abc)
    assert old_bodies.keys() == new_bodies.keys()
    for method, body in old_bodies.items():
        if method in changed:
            continue
        assert resolved_operations(baseline, baseline.instructions(body[3], method)) == resolved_operations(
            after, after.instructions(new_bodies[method][3], method))
        assert body[2] == new_bodies[method][2]
        assert body[4:] == new_bodies[method][4:]
    assert after.strings[:len(before.strings)] == before.strings
    assert after.multinames[:len(before.multinames)] == before.multinames
    assert not [row for row in after.unsupported + after.control_flow_anomalies
                if row['method_index'] in changed]
    # This scope only changes ABC and potentially extends Alegreya's repertoire.
    def unchanged(resources):
        return [(tag.code, tag.payload) for tag in resources if tag.code != 82 and not (
            tag.code == 75 and int.from_bytes(tag.payload[:2], 'little') in (6001, 6002))]
    assert unchanged(tags[1]) == unchanged(tags[2])
    fonts = [{int.from_bytes(t.payload[:2], 'little'): parse_font(t.payload)
              for t in resources if t.code == 75} for resources in tags[1:]]
    for font_id in (6001, 6002):
        old, new = [font_set[font_id] for font_set in fonts]
        positions = {code: i for i, code in enumerate(new['codes'])}
        for i, code in enumerate(old['codes']):
            for key in ('glyphs', 'advances', 'bounds'):
                assert old[key][i] == new[key][positions[code]]
        assert set(map(tuple, old['kerning'])) <= set(map(tuple, new['kerning']))
    report = load_catalog(root / 'candidate/static-validation.json')
    assert report == load_catalog(root / 'candidate/on-disk-validation.json')
    assert report['patched_sha256'] == sha(candidate)
    assert set(report['applied_entry_ids']) == set(merged['entry_ids'])


def test_mana_numeric_guards_and_notifications(mana_artifacts):
    _, original, _, _, workspace = mana_artifacts
    inv = abc_inventory(abc_bytes(read_swf(original)[1]))
    operations = inv.instructions(_bodies(inv.reader.data)[630][3], 630)
    ops = {at: (op, args) for at, op, args in operations}
    for at, name in ((28595, 'manaPoolLevel'), (28627, 'maxMana'),
                     (28687, 'manaReplenishMultiplier'), (28697, 'manaReplenishPerSecBase'),
                     (28782, 'manaPoolXpGainMultiplier'), (28821, 'MANA_STREAM'),
                     (28915, 'XP_GAINED'), (29025, 'orbletsManaGainMultiplier'),
                     (29095, 'manaPoolManaGainMultiplier'), (29175, 'cost')):
        assert ops[at][0] == 0x66
        assert inv.multiname(ops[at][1][0]) == name
    assert ops[28705] == (0x24, [60])
    assert ops[28707] == (0xa2, [])
    assert ops[29182] == (0x24, [70])
    assert ops[29184] == (0xa2, [])
    assert ops[29185] == (0x24, [100])
    assert ops[29187] == (0xa3, [])
    entries = {e['id']: e for e in workspace['entries']}
    for method, offset, call in ((760, 280, 124), (761, 144, 68)):
        notify = inv.instructions(_bodies(inv.reader.data)[method][3], method)
        assert notify[call][1:] == (0x4f, [3841, 8])
        assert inv.strings[notify[call - 1][2][0]] == 'left'
        assert entries[f'abc.4175.{method}.{offset}']['translation'] == 'Вызваны все оставшиеся волны'
        sites = [s for s in load_catalog(PLAN)['abc_font_bindings'] if s['method_index'] == method]
        assert len(sites) == 1 and sites[0]['kind'] == 'floater'
        assert sites[0]['instruction_index'] == call + 1


def test_mana_complete_phrases_and_panel_geometry(mana_artifacts):
    root, original, _, candidate, workspace = mana_artifacts
    entries = {e['id']: e for e in workspace['entries']}
    fonts = {int.from_bytes(t.payload[:2], 'little'): parse_font(t.payload)
             for t in read_swf(candidate)[1] if t.code == 75}
    bold = fonts[6002]
    for key in MANA_IDS:
        check_layout(bold, entries[key]['translation'], load_catalog(PLAN)['layouts'][key])
    inv = abc_inventory(abc_bytes(read_swf(original)[1]))
    def text(offset, russian=True):
        row = entries[f'abc.4175.630.{offset}']
        return row['translation'] if russian else row['source']
    # Samples include both NumberFormatter exponential precisions; not gameplay bounds.
    numbers = ('0', '1', '2', '5', '21', '1,234,567', '1.000 e12',
               '1.0000000 e12', '9.999 e99', '9.9999999 e99')
    states = list(itertools.product(numbers, (False, True), (False, True), (False, True), (False, True)))
    # Verify line-box constants against the original method and viewport model.
    assert method_metadata(inv.reader.data)[3504][4] == [(10, 10), (6, 3), (12, 12), (2, 3)]
    method = {at: (op, args) for at, op, args in inv.instructions(_bodies(inv.reader.data)[3504][3], 3504)}
    assert method[846] == (0x24, [253])  # compact spacing is signed -3
    assert method[853] == (0x24, [9]) and method[869] == (0x24, [8])
    source_ops = inv.instructions(_bodies(inv.reader.data)[630][3], 630)
    # Compact flags and actual point sizes determine each following field's y.
    for call, compact, size, tail in ((11286, True, 12, 0),
                                    (11302, False, 11, 0),
                                    (11364, True, 11, 2),
                                    (11401, True, 11, 2),
                                    (11436, True, 11, 2),
                                    (11490, False, 11, 2),
                                    (11525, False, 12, 2),
                                    (11549, False, 12, 2),
                                    (11556, False, 10, 0)):
        assert source_ops[call - 2 - tail][1:] == (0x26 if compact else 0x27, [])
        assert source_ops[call - 1 - tail][1:] == (0x24, [size])
    @lru_cache(maxsize=None)
    def field(face_id, value, size, width):
        face = fonts[face_id]
        layout = check_layout(face, value, {'height': size * 20, 'max_width': width * 20,
                                          'wrap': True, 'max_lines': 30})
        lines = []
        for paragraph in value.split('\n'):
            line = ''
            for word in paragraph.split(' '):
                joined = line + (' ' if line else '') + word
                if measure_text(face, joined, size * 20)['widths'][0] > width * 20:
                    lines.append(line)
                    line = word
                else:
                    line = joined
            lines.append(line)
        assert layout['lines'] == len(lines)
        measured = measure_text(face, '\n'.join(lines), size * 20)
        assert all(w <= width * 20 for w in measured['widths'])
        return measured, len(lines)
    def panel_height(rows, russian):
        y, bottom = 10.0, None
        face_id = 6002 if russian else 255
        for value, size, compact, extra in rows:
            measured, count = field(face_id, value, size, 396)
            bounds = [b for b in measured['bounds'] if b]
            if bounds:
                ascent = fonts[face_id]['ascent'] * size / 20480
                top = y + 2 + ascent + min(b[2] for b in bounds) / 20
                next_bottom = y + 2 + ascent + max(b[3] for b in bounds) / 20
                assert bottom is None or bottom <= top
                bottom = next_bottom
            y += (count * measured['line_height'] + 80) / 20 + (-3 if compact else 9) + extra
        return y + 8
    evidence = []
    for number, normal, stream, talisman, orblets in states:
        heights = []
        for russian in (False, True):
            def t(at):
                return text(at, russian)
            rows = [(t(28538), 14, True, 0), (t(28588) + number, 12, True, 0),
                    (t(28617) + number + t(28639), 11, False, 5),
                    (t(28677) + number + t(28713), 11, normal, 0)]
            if normal:
                rows.append((t(28772) + number, 11, True, 0))
            if stream:
                rows.append((t(28849) + number, 11, True, 0))
            if talisman:
                rows.append((t(28938) + number, 11, True, 0))
            if orblets:
                rows.append((t(29015) + number, 11, True, 0))
            rows.append((t(29085) + number, 11, False, 5))
            for dragged in (False, True):
                tail = [(t(29162) + number + t(29193), 12, False, 0)] if dragged else [
                    (t(29240) + '70' + t(29253), 12, False, 0)]
                tail.append((t(29275), 10, False, 0))
                heights.append(panel_height(rows + tail, russian))
        comparisons = []
        for index, dragged in enumerate((False, True)):
            original_height, candidate_height = heights[index], heights[index + 2]
            for zoom in (1, .75, .5):
                factor = (1 + zoom) / (2 * zoom)
                for threshold in (544, 602):
                    old, new = original_height * factor, candidate_height * factor
                    assert not old <= threshold < new, (number, normal, stream, talisman, orblets, dragged, zoom)
                    comparisons.append({'zoom': zoom, 'threshold': threshold,
                                        'original': old, 'candidate': new})
        evidence.append({'number': number, 'normal': normal, 'stream': stream,
                         'talisman': talisman, 'orblets': orblets, 'heights': heights,
                         'comparisons': comparisons})
    for number in numbers:
        for offset in (28197, 28264):
            for russian in (False, True):
                field(6002 if russian else 255, number + text(offset, russian), 12, 236)
    for method, offset in ((760, 280), (761, 144)):
        check_layout(bold, entries[f'abc.4175.{method}.{offset}']['translation'],
                     load_catalog(PLAN)['layouts'][f'abc.4175.{method}.{offset}'])
    (root / 'mana-geometry.json').write_text(json.dumps({
        'candidate_sha256': sha(candidate), 'states': evidence,
        'panels': len(states) * 2, 'threshold_comparisons': len(states) * 12,
        'runtime_verified': False,
        'limits': 'Conservative glyph/line-box model; samples, not gameplay maxima; AIR unverified.'
    }, ensure_ascii=False, indent=2) + '\n')
