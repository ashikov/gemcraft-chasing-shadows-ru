"""Opt-in offline achievements checks; private inputs never enter CI or Git."""
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from addressed_localization import check_layout
from alegreya_localization import build_alegreya_localization, validate_alegreya_localization
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from localization_plan import merge_localization_plans, resolve_plan_font_bindings
from swf_geometry import parse_font, read_swf
from test_skills_local import resolved_static
from test_talisman_local import abc_bytes, abc_inventory, resolved_operations, sprite_characters
from translation_catalog import load_catalog

pytestmark = pytest.mark.local

PR8_SHA256 = '058a09945473a2877fb131bb2c59dd16078a7116d8bcf5f486c558c609a64969'
VARIABLES = ['GEMCRAFT_ORIGINAL', 'GEMCRAFT_PR4_WORKSPACE', 'GEMCRAFT_PR9_BASE_PLANS',
             'GEMCRAFT_PR9_ACHIEVEMENTS_PLAN', 'GEMCRAFT_ALEGREYA_FONT']
NEW_METHODS = {222, 1073, 1085, 1090, 1093, 1094, 2769, 3475, 3536}
BINDING_COUNTS = {1085: 14, 1090: 8, 1093: 4, 1094: 1, 2769: 1, 3475: 3, 3536: 1}
STATIC_IDS = {2659, 2661, 2662, 2663, 2664, 2665, 2666}


def test_achievements_cumulative_candidate(tmp_path):
    if not all(os.environ.get(name) for name in VARIABLES):
        pytest.skip('Set private original, workspace, PR3–PR9 plans and Alegreya source')
    original_path = Path(os.environ['GEMCRAFT_ORIGINAL'])
    original = original_path.read_bytes()
    workspace = load_catalog(os.environ['GEMCRAFT_PR4_WORKSPACE'])
    entries = {e['id']: e for e in workspace['entries']}
    bases = [load_catalog(p) for p in json.loads(os.environ['GEMCRAFT_PR9_BASE_PLANS'])]
    plan_path = Path(os.environ['GEMCRAFT_PR9_ACHIEVEMENTS_PLAN'])
    plan = load_catalog(plan_path)
    merged = load_catalog(plan_path.with_name('cumulative-plan.json'))
    font = Path(os.environ['GEMCRAFT_ALEGREYA_FONT']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    artifact_dir = Path(os.environ.get('GEMCRAFT_PR9_OUTPUT', tmp_path / 'evidence'))
    artifact_dir.mkdir(parents=True, exist_ok=False)
    old_path, new_path, again_path = [artifact_dir / name for name in ('baseline.swf', 'candidate.swf', 'reproduction.swf')]
    baseline, base_report = build_alegreya_localization(original, workspace, bases, font)
    old_path.write_bytes(baseline)
    assert sha(baseline) == PR8_SHA256 and base_report['applied_translations'] == 783
    expected = merge_localization_plans([*bases, plan], workspace)
    # Only the now-covered map achievement-description blocker is retired.
    resolved = [b for b in expected['coverage_blockers']
                if b.startswith('Achievement descriptions dynamically')]
    assert len(resolved) == 1
    expected['coverage_blockers'] = [b for b in expected['coverage_blockers'] if b not in resolved]
    assert merged == expected
    candidate, report = build_alegreya_localization(original, workspace, merged, font)
    new_path.write_bytes(candidate)
    again, repeated = build_alegreya_localization(original, workspace, merged, font)
    again_path.write_bytes(again)
    (artifact_dir / 'static-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    assert candidate == again and report == repeated
    assert len(plan['entry_ids']) == 865
    assert report['applied_translations'] == 783 + 865 == 1648
    assert report['status'] == 'PARTIAL'
    assert set(base_report['applied_entry_ids']) <= set(report['applied_entry_ids'])
    assert all(report['layout_checks'][i] == result
               for i, result in base_report['layout_checks'].items())
    assert report['empty_abc_strings'] == ['abc.4175.1085.154', 'abc.4175.957.1117']
    assert plan['empty_abc_strings'] == ['abc.4175.1085.154']

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
    # Original field keys, numeric constructor arguments, category refs and all
    # conditions survive: only the two display arguments may change per object.
    original_ops = before.instructions(original_codes[222], 222)
    new_ops = after.instructions(new_codes[222], 222)
    constructors = [i for i, (_, opcode, args) in enumerate(original_ops)
                    if opcode == 0x4a and args[0] == 69]
    assert len(constructors) == 418
    translated_sites = {entries[i]['location']['code_offset']: entries[i]['translation']
                        for i in plan['entry_ids'] if i.startswith('abc.4175.222.')}
    assert len(translated_sites) == 835
    assert len(original_ops) == len(new_ops)
    for (offset, opcode, args), (_, new_opcode, new_args) in zip(original_ops, new_ops):
        assert opcode == new_opcode
        if offset in translated_sites:
            assert opcode == 0x2c and after.strings[new_args[0]] == translated_sites[offset]
        else:
            assert args == new_args
    assert Counter(original_ops[i][2][1] for i in constructors) == {11: 402, 12: 16}

    selected_abc = [entries[i] for i in merged['entry_ids'] if i.startswith('abc.')]
    from inventory_strings import extract_inventory
    originals = {e['id']: e for e in extract_inventory(original, ORIGINAL_SHA256)['entries']}
    selected_abc += [{**originals[i], 'translation': ''} for i in merged['empty_abc_strings']]
    translated = patch_pushstrings(abc_bytes(original_tags), selected_abc)
    translated_inv = abc_inventory(translated)
    snippets, _, counts = resolve_plan_font_bindings(
        abc_bytes(original_tags), translated, merged['abc_font_bindings'])
    snippets = snippets(len(translated_inv.strings), len(translated_inv.multinames))
    for method, count in BINDING_COUNTS.items():
        assert counts[str(method)] == count
        for offset, snippet in snippets[method]:
            relocated = offset + sum(len(s) for at, s in snippets[method] if at < offset)
            assert new_codes[method][relocated:relocated + len(snippet)] == snippet
    # Shared constructors, mechanics and category keys remain byte-identical.
    # m556's inherited font binding has a relocated family pool index; its full
    # resolved instruction sequence and metadata were compared above.
    assert 556 not in NEW_METHODS
    for method in (139, 220, 221, 1079, 1083, 3441, 3442, 3445, 1144, 1732):
        assert old_bodies[method][2:] == new_bodies[method][2:]
    for method, calls in ((1085, 7), (1090, 4), (1093, 2)):
        ops = before.instructions(original_codes[method], method)
        actual = [i for i, (_, opcode, args) in enumerate(ops) if opcode == 0x4f and args[0] == 3729]
        assert len(actual) == calls
        sites = [s for s in plan['abc_font_bindings'] if s['method_index'] == method]
        assert {(s['instruction_index'], s['kind']) for s in sites} == {
            pair for i in actual for pair in ((i, 'panel_format_before'), (i + 1, 'panel_format_after'))}
    assert any(opcode == 0x66 and args == [234] for _, opcode, args
               in before.instructions(original_codes[1064], 1064))
    map_sites = [s for s in merged['abc_font_bindings'] if s['method_index'] == 1064]
    assert any(s['instruction_index'] == 1654 and s['kind'] == 'panel_format_before' for s in map_sites)

    def chars(tags):
        return {int.from_bytes(t.payload[:2], 'little'): t for t in tags if t.code in (11, 33, 37, 75)}
    old_chars, new_chars = chars(old_tags), chars(new_tags)
    old_fonts = {i: parse_font(t.payload) for i, t in old_chars.items() if t.code == 75}
    new_fonts = {i: parse_font(t.payload) for i, t in new_chars.items() if t.code == 75}
    for identifier, tag in old_chars.items():
        if identifier in STATIC_IDS or identifier in (6001, 6002):
            continue
        if tag.code in (11, 33):
            assert resolved_static(tag, old_fonts) == resolved_static(new_chars[identifier], new_fonts)
        else:
            assert new_chars[identifier].payload == tag.payload
    for identifier in (6001, 6002):
        a, b = old_fonts[identifier], new_fonts[identifier]
        for code in a['codes']:
            left, right = a['codes'].index(code), b['codes'].index(code)
            assert (a['glyphs'][left], a['advances'][left], a['bounds'][left]) == (
                b['glyphs'][right], b['advances'][right], b['bounds'][right])
    assert sprite_characters(original_tags, 2668)[1] == STATIC_IDS | {2658, 565}

    # Use final on-disk literals, including the suppressed English plural branch.
    translated_codes = {m: c for m, _, c in translated_inv.bodies}
    decoded_literals = {}
    for method in NEW_METHODS:
        old_ops = before.instructions(original_codes[method], method)
        patched_ops = translated_inv.instructions(translated_codes[method], method)
        decoded_literals.update({(method, old[0]): after.strings[new[2][0]]
                                for old, new in zip(old_ops, patched_ops) if old[1] == 0x2c})

    def literal(identifier):
        e = entries.get(identifier, originals.get(identifier))
        method, offset = e['location']['method_index'], e['location']['code_offset']
        return decoded_literals[method, offset]
    samples = []
    for points in (1, 2, 3):
        text = literal('abc.4175.1085.131') + str(points) + literal('abc.4175.1085.139')
        text += literal('abc.4175.1085.154') if points > 1 else ''
        samples.append((text, 200, 7200, 1, 6001))
        assert not literal('abc.4175.1085.139') and not literal('abc.4175.1085.154')
    for current, target in ((0, 1), (1, 3), (21, 418), (999999, 1000000)):
        text = literal('abc.4175.1085.342') + f'{current:,}' + ' / ' + f'{target:,}'
        samples.append((text, 220, 7200, 1, 6001))
    for count in (0, 1, 179, 180, 181, 418):
        text = literal('abc.4175.1090.201') + str(count) + ')'
        samples.append((text, 220, 8000, 1, 6001))
    # Every collection text is exercised at each actual consumer's size and weight.
    layout_checks = []
    inventory = load_catalog(plan_path.with_name('collection-inventory.json'))
    for achievement in inventory['achievements']:
        title, description = achievement['entries'][2], achievement['entries'][1]
        for item, height, width, lines, face, wrap in (
                (title, 280, 7200, 2, 6002, True),
                (description, 240, 7200, 6, 6001, True),
                (description, 220, 6000, 6, 6001, True),
                (title, 240, 17800, 1, 6002, False),
                (description, 200, 17800, 1, 6002, False)):
            text = literal(item['id'])
            layout = {'height': height, 'max_width': width, 'max_lines': lines, 'wrap': wrap}
            result = check_layout(new_fonts[face], text, layout)
            layout_checks.append({'id': item['id'], 'font': face, 'layout': layout, 'result': result})
    for text, height, width, lines, face in samples:
        check_layout(new_fonts[face], text, {'height': height, 'max_width': width,
                                            'max_lines': lines, 'wrap': True})

    # The standard validator reads the finished file and reproduces its full recipe.
    assert validate_alegreya_localization(original, new_path.read_bytes(), workspace, merged, font) == report
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
    evidence = {'baseline_sha256': sha(baseline), 'candidate_sha256': sha(candidate),
                'reproduction_sha256': sha(again), 'baseline_count': 783, 'new_count': 865,
                'cumulative_count': 1648, 'runtime': 'NOT VERIFIED — ACCEPTED RISK',
                'on_disk_validator': True, 'temporary_apply_restore': True,
                'consumer_layout_count': len(layout_checks), 'dynamic_sample_count': len(samples)}
    for name, data in (('reproduction.json', evidence), ('static-validation.json', report),
                       ('layout-validation.json', {'consumer_checks': layout_checks, 'dynamic_samples': samples})):
        (artifact_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
