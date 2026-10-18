"""Opt-in offline drop-icon tooltip checks; private inputs never enter CI or Git."""
import json
import math
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from addressed_localization import check_layout
from alegreya_localization import build_alegreya_localization, validate_alegreya_localization
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from localization_plan import merge_localization_plans, resolve_plan_font_bindings
from swf_geometry import parse_font, read_swf
from test_talisman_local import abc_bytes, abc_inventory, resolved_operations
from translation_catalog import load_catalog

pytestmark = pytest.mark.local

PR10_SHA256 = '671fe1de2fc3553f705c18060987caeaef9edcd6ab98d38a534260c04099f5b6'
VARIABLES = ['GEMCRAFT_ORIGINAL', 'GEMCRAFT_PR4_WORKSPACE', 'GEMCRAFT_PR11_BASE_PLANS',
             'GEMCRAFT_PR11_DROP_ICON_PLAN', 'GEMCRAFT_ALEGREYA_FONT']
NEW_METHODS = {556}
NEW_BINDING_COUNTS = {556: 30}
MERGED_BINDING_COUNTS = {556: 48}
PRESERVED_METHODS = (623, 630, 1065, 1068, 1069, 1144, 1732)
SHADOW_CORE_PLURAL = 'abc.4175.556.426'
SHADOW_CORE_SINGULAR = 'abc.4175.556.434'
FIELD_TOKEN = 'abc.4175.556.515'
MAP_HEXTILE = 'abc.4175.556.585'


def format_number(value, decimals=0):
    factor = 10 ** decimals
    rounded = math.floor(value * factor + 0.5)
    if decimals:
        return f'{rounded / factor:,.{decimals}f}'
    return f'{rounded:,}'


def layout_without_budget(layout):
    return {key: value for key, value in layout.items() if key != 'dynamic_suffix_budget'}


def test_drop_icons_cumulative_candidate(tmp_path):
    if not all(os.environ.get(name) for name in VARIABLES):
        pytest.skip('Set private original, workspace, PR3–PR10 plans and Alegreya source')
    original_path = Path(os.environ['GEMCRAFT_ORIGINAL'])
    original = original_path.read_bytes()
    workspace = load_catalog(os.environ['GEMCRAFT_PR4_WORKSPACE'])
    entries = {e['id']: e for e in workspace['entries']}
    bases = [load_catalog(p) for p in json.loads(os.environ['GEMCRAFT_PR11_BASE_PLANS'])]
    plan_path = Path(os.environ['GEMCRAFT_PR11_DROP_ICON_PLAN'])
    plan = load_catalog(plan_path)
    merged = load_catalog(plan_path.with_name('cumulative-plan.json'))
    inventory = load_catalog(plan_path.with_name('drop-icon-inventory.json'))
    font = Path(os.environ['GEMCRAFT_ALEGREYA_FONT']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    artifact_dir = Path(os.environ.get('GEMCRAFT_PR11_OUTPUT', tmp_path / 'evidence'))
    artifact_dir.mkdir(parents=True, exist_ok=False)
    old_path, new_path, again_path = [
        artifact_dir / name for name in ('baseline.swf', 'candidate.swf', 'reproduction.swf')
    ]
    baseline, base_report = build_alegreya_localization(original, workspace, bases, font)
    old_path.write_bytes(baseline)
    assert sha(baseline) == PR10_SHA256 and base_report['applied_translations'] == 1690

    expected = merge_localization_plans([*bases, plan], workspace)
    resolved = []
    remaining = []
    for blocker in expected['coverage_blockers']:
        if blocker.startswith('Battle-trait names/descriptions not integrated:'):
            resolved.append(blocker)
        elif blocker.startswith('Outcome drop-icon tooltips remain outside PR6:'):
            resolved.append(blocker)
        else:
            remaining.append(blocker)
    assert len(resolved) == 2
    expected['coverage_blockers'] = remaining
    assert merged == expected

    candidate, report = build_alegreya_localization(original, workspace, merged, font)
    new_path.write_bytes(candidate)
    again, repeated = build_alegreya_localization(original, workspace, merged, font)
    again_path.write_bytes(again)
    (artifact_dir / 'static-validation.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    )
    assert candidate == again and report == repeated
    assert len(plan['entry_ids']) == 16
    assert report['applied_translations'] == 1690 + 16 == 1706
    assert report['status'] == 'PARTIAL'
    assert set(base_report['applied_entry_ids']) <= set(report['applied_entry_ids'])
    assert all(report['layout_checks'][i] == result
               for i, result in base_report['layout_checks'].items())
    assert report['empty_abc_strings'] == base_report['empty_abc_strings']
    assert not plan.get('empty_abc_strings')

    original_tags, old_tags, new_tags = [read_swf(data)[1] for data in (original, baseline, candidate)]
    before, after = abc_inventory(abc_bytes(original_tags)), abc_inventory(abc_bytes(new_tags))
    old_inv = abc_inventory(abc_bytes(old_tags))
    old_bodies, new_bodies = _bodies(abc_bytes(old_tags)), _bodies(abc_bytes(new_tags))
    original_codes = {m: code for m, _, code in before.bodies}
    new_codes = {m: code for m, _, code in after.bodies}
    assert after.strings[:len(before.strings)] == before.strings
    for method, old in old_bodies.items():
        if method not in NEW_METHODS:
            new = new_bodies[method]
            assert resolved_operations(old_inv, old_inv.instructions(old[3], method)) == (
                resolved_operations(after, after.instructions(new[3], method)))
            assert old[2] == new[2] and old[4:] == new[4:]
    for method in NEW_METHODS:
        old_limits = old_bodies[method][2]
        new_limits = new_bodies[method][2]
        assert new_limits[0] == old_limits[0]
        assert new_limits[1:] == old_limits[1:]
        assert len(new_bodies[method][4]) == len(old_bodies[method][4])
        assert [e[3:] for e in new_bodies[method][4]] == [e[3:] for e in old_bodies[method][4]]
        assert new_bodies[method][5] == old_bodies[method][5]
        operations = after.instructions(new_codes[method], method)
        boundaries = {offset for offset, _, _ in operations} | {len(new_codes[method])}
        assert all(exception[0] in boundaries and exception[1] in boundaries
                   and exception[2] in boundaries for exception in new_bodies[method][4])
    assert not [item for item in after.unsupported + after.control_flow_anomalies
                if item['method_index'] in NEW_METHODS]
    for method in PRESERVED_METHODS:
        assert old_bodies[method][2:] == new_bodies[method][2:]

    from inventory_strings import extract_inventory
    fresh = {e['id']: e for e in extract_inventory(original, ORIGINAL_SHA256)['entries']}
    for identifier in plan['entry_ids']:
        entry = entries[identifier]
        original_entry = fresh[identifier]
        assert entry['source'] == original_entry['source']
        assert entry['source_sha256'] == original_entry['source_sha256']
        assert entry['location'] == original_entry['location']
    selected_abc = [entries[i] for i in merged['entry_ids'] if i.startswith('abc.')]
    selected_abc += [{**fresh[i], 'translation': ''} for i in merged['empty_abc_strings']]
    translated = patch_pushstrings(abc_bytes(original_tags), selected_abc)
    translated_inv = abc_inventory(translated)
    translated_codes = {m: code for m, _, code in translated_inv.bodies}
    for method in NEW_METHODS:
        # m556 also carries the six PR10 scroll-tooltip strings; their operands
        # are remapped to appended pool entries just like the new PR11 ones.
        expected_strings = {
            entries[i]['location']['code_offset']: entries[i]['translation']
            for i in merged['entry_ids']
            if entries[i]['location'].get('method_index') == method
        }
        assert len(expected_strings) == 22
        old_ops = before.instructions(original_codes[method], method)
        patched_ops = translated_inv.instructions(translated_codes[method], method)
        assert len(old_ops) == len(patched_ops)
        for (offset, opcode, args), (_, new_opcode, new_args) in zip(old_ops, patched_ops):
            assert opcode == new_opcode
            if offset in expected_strings:
                assert opcode == 0x2C
                assert translated_inv.strings[new_args[0]] == expected_strings[offset]
            else:
                assert args == new_args

    snippets, _, counts = resolve_plan_font_bindings(
        abc_bytes(original_tags), translated, merged['abc_font_bindings'])
    assert {method: counts[str(method)] for method in MERGED_BINDING_COUNTS} == MERGED_BINDING_COUNTS
    snippets = snippets(len(translated_inv.strings), len(translated_inv.multinames))
    original_operations = {
        method: before.instructions(original_codes[method], method) for method in NEW_METHODS
    }
    for site in merged['abc_font_bindings']:
        method = site['method_index']
        if method not in NEW_METHODS:
            continue
        offset = original_operations[method][site['instruction_index']][0]
        snippet = next(snippet for at, snippet in snippets[method] if at == offset)
        relocated = offset + sum(len(s) for at, s in snippets[method] if at < offset)
        assert new_codes[method][relocated:relocated + len(snippet)] == snippet
    _, _, new_counts = resolve_plan_font_bindings(
        abc_bytes(original_tags), translated, plan['abc_font_bindings'])
    assert {int(method): count for method, count in new_counts.items()} == NEW_BINDING_COUNTS
    assert any(site['method_index'] == 556 and site['instruction_index'] == 328
               for site in merged['abc_font_bindings'])

    abc_index = next(index for index, tag in enumerate(old_tags) if tag.code == 82)
    assert new_tags[abc_index].code == 82

    def preserved_tags(tags):
        result = []
        for index, tag in enumerate(tags):
            character = int.from_bytes(tag.payload[:2], 'little') if len(tag.payload) >= 2 else None
            if index == abc_index or tag.code == 75 and character in (6001, 6002):
                continue
            result.append((tag.code, tag.payload))
        return result
    assert len(old_tags) == len(new_tags)
    assert preserved_tags(old_tags) == preserved_tags(new_tags)
    old_fonts = {int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
                 for tag in old_tags if tag.code == 75}
    new_fonts = {int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
                 for tag in new_tags if tag.code == 75}
    for identifier in (6001, 6002):
        old_font, new_font = old_fonts[identifier], new_fonts[identifier]
        new_indices = {code: index for index, code in enumerate(new_font['codes'])}
        for old_index, code in enumerate(old_font['codes']):
            new_index = new_indices[code]
            assert (old_font['glyphs'][old_index], old_font['advances'][old_index],
                    old_font['bounds'][old_index]) == (
                        new_font['glyphs'][new_index], new_font['advances'][new_index],
                        new_font['bounds'][new_index])

    layout_evidence = []
    for row in inventory['entries']:
        identifier = row['id']
        layout = plan['layouts'][identifier]
        result = check_layout(new_fonts[6001], entries[identifier]['translation'], layout)
        layout_evidence.append({'id': identifier, 'layout': layout, 'result': result})

    def shadow_core_line(value):
        suffix = (entries[SHADOW_CORE_PLURAL]['translation'] if value > 1
                  else entries[SHADOW_CORE_SINGULAR]['translation'])
        return format_number(value) + suffix

    dynamic_samples = []
    for value in (1, 2, 5, 1337, 999999):
        text = shadow_core_line(value)
        dynamic_samples.append({'value': value, 'text': text})
        layout = layout_without_budget(plan['layouts'][SHADOW_CORE_PLURAL])
        result = check_layout(new_fonts[6001], text, layout)
        layout_evidence.append({'id': 'shadow_core', 'value': value,
                                'layout': layout, 'result': result})
    assert shadow_core_line(1) == '1 теневое ядро'
    assert shadow_core_line(2) == '2 теневых ядер'
    assert shadow_core_line(1337) == '1,337 теневых ядер'
    for identifier, samples in ((FIELD_TOKEN, ('A1', 'W13')), (MAP_HEXTILE, ('H3', 'H24'))):
        for str_id in samples:
            text = entries[identifier]['translation'] + str_id
            dynamic_samples.append({'id': identifier, 'strId': str_id, 'text': text})
            layout = layout_without_budget(plan['layouts'][identifier])
            result = check_layout(new_fonts[6001], text, layout)
            layout_evidence.append({'id': identifier, 'strId': str_id,
                                    'layout': layout, 'result': result})

    assert validate_alegreya_localization(
        original, new_path.read_bytes(), workspace, merged, font) == report
    game = tmp_path / 'game' / 'gc-cs-steam.swf'
    game.parent.mkdir()
    game.write_bytes(original)
    old_manifest = apply_patch_file(game, old_path, tmp_path / 'backup', ORIGINAL_SHA256, sha(baseline))
    restore_patch_file(old_manifest)
    new_manifest = apply_patch_file(game, new_path, tmp_path / 'backup', ORIGINAL_SHA256, sha(candidate))
    assert game.read_bytes() == candidate
    restore_patch_file(new_manifest)
    assert game.read_bytes() == original
    apply_patch_file(game, old_path, tmp_path / 'backup', ORIGINAL_SHA256, sha(baseline))
    assert game.read_bytes() == baseline
    assert original_path.read_bytes() == original

    evidence = {
        'baseline_sha256': sha(baseline),
        'candidate_sha256': sha(candidate),
        'reproduction_sha256': sha(again),
        'baseline_count': 1690,
        'new_count': 16,
        'cumulative_count': 1706,
        'runtime': 'NOT VERIFIED — ACCEPTED RISK',
        'on_disk_validator': True,
        'temporary_apply_restore': True,
        'dynamic_sample_count': len(dynamic_samples),
        'layout_check_count': len(layout_evidence),
        'new_binding_counts': NEW_BINDING_COUNTS,
        'merged_binding_counts': MERGED_BINDING_COUNTS,
    }
    for name, data in (
            ('reproduction.json', evidence),
            ('layout-validation.json', {'checks': layout_evidence,
                                        'dynamic_samples': dynamic_samples}),
            ('static-validation.json', report)):
        (artifact_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
