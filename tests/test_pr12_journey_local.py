"""Opt-in offline Journey Notes checks; private inputs never enter CI or Git."""
import json
import os
import re
from html import unescape
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

PR11_SHA256 = '6fe4b753aae014c6b962681936140731a1efcace6727a8dad72d35b6be19a407'
VARIABLES = ['GEMCRAFT_ORIGINAL', 'GEMCRAFT_PR4_WORKSPACE', 'GEMCRAFT_PR12_BASE_PLANS',
             'GEMCRAFT_PR12_JOURNEY_PLAN', 'GEMCRAFT_ALEGREYA_FONT']
NEW_METHODS = {975, 3478}
NEW_BINDING_COUNTS = {975: 12, 3478: 1}
NEW_STACK_BUDGETS = {975: 5, 3478: 8}
# Unlocalized shared consumers and the common renderer stay byte-identical.
# Previously localized consumers are checked below with appended-pool indices
# resolved: inserting earlier-method translations renumbers those indices.
PRESERVED_METHODS = (623, 630, 3484, 3490, 3504)
TAG_RE = re.compile(r'<[^>]*>')
PARAGRAPH_RE = re.compile(r'<p[^>]*>(.*?)</p>')
FACE_RE = re.compile(r'face="[^"]*"')


def visible_lines(html):
    return [unescape(TAG_RE.sub('', inner)) for inner in PARAGRAPH_RE.findall(html)]


def layout_font(fonts, layout):
    return fonts[6001 if layout['font_id'] == 1 else 6002]


def test_journey_cumulative_candidate(tmp_path):
    if not all(os.environ.get(name) for name in VARIABLES):
        pytest.skip('Set private original, workspace, PR1–PR11 plans and Alegreya source')
    original_path = Path(os.environ['GEMCRAFT_ORIGINAL'])
    original = original_path.read_bytes()
    workspace = load_catalog(os.environ['GEMCRAFT_PR4_WORKSPACE'])
    entries = {e['id']: e for e in workspace['entries']}
    bases = [load_catalog(p) for p in json.loads(os.environ['GEMCRAFT_PR12_BASE_PLANS'])]
    plan_path = Path(os.environ['GEMCRAFT_PR12_JOURNEY_PLAN'])
    plan = load_catalog(plan_path)
    inventory = load_catalog(plan_path.with_name('journey-inventory.json'))
    scope = load_catalog(plan_path.with_name('scope-inventory.json'))
    font = Path(os.environ['GEMCRAFT_ALEGREYA_FONT']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    artifact_dir = Path(os.environ.get('GEMCRAFT_PR12_OUTPUT', tmp_path / 'evidence'))
    artifact_dir.mkdir(parents=True, exist_ok=False)
    old_path, new_path, again_path = [
        artifact_dir / name for name in ('baseline.swf', 'candidate.swf', 'reproduction.swf')
    ]
    baseline, base_report = build_alegreya_localization(original, workspace, bases, font)
    old_path.write_bytes(baseline)
    assert sha(baseline) == PR11_SHA256 and base_report['applied_translations'] == 1706

    merged = merge_localization_plans([*bases, plan], workspace)
    assert len(merged['entry_ids']) == 1706 + 38 == 1744
    assert merged['coverage_blockers'] == base_report['coverage_blockers']
    # PR12 neither adds nor resolves blockers: the only filtered entries stay
    # the two already resolved by PR10/PR11, leaving the four known blockers.
    resolved = [b for b in merged['coverage_blockers'] if b.startswith(
        ('Battle-trait names/descriptions not integrated:',
         'Outcome drop-icon tooltips remain outside PR6:'))]
    remaining = [b for b in merged['coverage_blockers'] if b not in resolved]
    assert len(resolved) == 2 and len(remaining) == 4

    candidate, report = build_alegreya_localization(original, workspace, merged, font)
    new_path.write_bytes(candidate)
    again, repeated = build_alegreya_localization(original, workspace, merged, font)
    again_path.write_bytes(again)
    (artifact_dir / 'static-validation.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    )
    assert candidate == again and report == repeated
    assert len(plan['entry_ids']) == 38
    assert report['applied_translations'] == 1744
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
            old_ops = old_inv.instructions(old[3], method)
            new_ops = after.instructions(new[3], method)
            assert resolved_operations(old_inv, old_ops) == resolved_operations(after, new_ops)
            assert old[2] == new[2] and old[4:] == new[4:]
            assert len(old[3]) == len(new[3]) and len(old_ops) == len(new_ops)
            old_code, new_code = bytearray(old[3]), bytearray(new[3])
            for index, ((offset, opcode, args), (new_offset, new_opcode, new_args)) in enumerate(
                    zip(old_ops, new_ops)):
                assert (offset, opcode) == (new_offset, new_opcode)
                if opcode == 0x2C and args[0] >= len(before.strings):
                    assert new_args[0] >= len(before.strings)
                    assert old_inv.strings[args[0]] == after.strings[new_args[0]]
                    end = old_ops[index + 1][0] if index + 1 < len(old_ops) else len(old_code)
                    old_code[offset + 1:end] = new_code[offset + 1:end] = bytes(end - offset - 1)
                else:
                    assert args == new_args
            assert old_code == new_code
    for method in NEW_METHODS:
        old_limits = old_bodies[method][2]
        new_limits = new_bodies[method][2]
        assert new_limits[0] == old_limits[0] + NEW_STACK_BUDGETS[method]
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

    scope_entries = {e['id']: e for e in scope['entries']}
    assert set(scope_entries) == set(plan['entry_ids'])
    from inventory_strings import extract_inventory
    fresh = {e['id']: e for e in extract_inventory(original, ORIGINAL_SHA256)['entries']}
    for identifier in plan['entry_ids']:
        entry = entries[identifier]
        original_entry = fresh[identifier]
        assert entry['translation'] and entry['status'] in ('draft', 'verified')
        assert entry['classification'] == entry['context_confidence'] == 'confirmed'
        assert not entry['mandatory_placeholders']
        assert entry['source'] == original_entry['source']
        assert entry['source_sha256'] == original_entry['source_sha256']
        assert entry['location'] == original_entry['location']
        assert scope_entries[identifier]['location'] == entry['location']
        assert scope_entries[identifier]['source_sha256'] == entry['source_sha256']
        assert re.findall(r'\d+', TAG_RE.sub('', entry['source'])) == re.findall(
            r'\d+', TAG_RE.sub('', entry['translation']))
    selected_abc = [entries[i] for i in merged['entry_ids'] if i.startswith('abc.')]
    selected_abc += [{**fresh[i], 'translation': ''} for i in merged['empty_abc_strings']]
    translated = patch_pushstrings(abc_bytes(original_tags), selected_abc)
    translated_inv = abc_inventory(translated)
    translated_codes = {m: code for m, _, code in translated_inv.bodies}
    for method in NEW_METHODS:
        expected_strings = {
            entries[i]['location']['code_offset']: entries[i]['translation']
            for i in merged['entry_ids']
            if entries[i]['location'].get('method_index') == method
        }
        assert len(expected_strings) == {975: 6, 3478: 1}[method]
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
    assert {method: counts[str(method)] for method in NEW_BINDING_COUNTS} == NEW_BINDING_COUNTS
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

    abc_index = next(index for index, tag in enumerate(old_tags) if tag.code == 82)
    assert new_tags[abc_index].code == 82
    edit_ids = {entries[i]['location']['character_id']
                for i in plan['entry_ids'] if i.startswith('edit.')}

    def preserved_tags(tags):
        result = []
        for index, tag in enumerate(tags):
            character = int.from_bytes(tag.payload[:2], 'little') if len(tag.payload) >= 2 else None
            if index == abc_index or tag.code == 75 and character in (6001, 6002):
                continue
            if tag.code == 37 and character in edit_ids:
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

    from inventory_strings import parse_edit_text
    old_edit_tags = {int.from_bytes(tag.payload[:2], 'little'): tag
                     for tag in old_tags if tag.code == 37}
    new_edit_tags = {int.from_bytes(tag.payload[:2], 'little'): tag
                     for tag in new_tags if tag.code == 37}
    layout_evidence = []
    for row in inventory['entries']:
        identifier = row['id']
        entry = entries[identifier]
        layout = plan['layouts'][identifier]
        text = ('\n'.join(visible_lines(entry['translation']))
                if identifier.startswith('edit.') else entry['translation'])
        result = check_layout(layout_font(new_fonts, layout), text, layout)
        layout_evidence.append({'id': identifier, 'layout': layout, 'result': result})
        if not identifier.startswith('edit.'):
            continue
        character = entry['location']['character_id']
        old_id, _, old_format = parse_edit_text(old_edit_tags[character].payload)
        new_id, patched, new_format = parse_edit_text(new_edit_tags[character].payload)
        assert old_id == new_id
        assert {k: v for k, v in old_format.items() if k != 'font_id'} == {
            k: v for k, v in new_format.items() if k != 'font_id'}
        assert new_format['font_id'] == 6001
        width = old_format['bounds'][1] - old_format['bounds'][0]
        height = old_format['bounds'][3] - old_format['bounds'][2]
        face = layout_font(new_fonts, layout)
        pitch = (face['ascent'] + face['descent'] + face['leading']) * layout['height'] / 20480
        pitch = max(old_format['font_height'] + old_format['leading'],
                    pitch + old_format['leading'])
        assert layout['max_width'] == width - 80
        assert layout['max_height'] == height - 80
        assert layout['max_lines'] == int((height - 80) // pitch)
        assert result['lines'] * pitch <= height - 80
        assert TAG_RE.findall(entry['source']) == TAG_RE.findall(entry['translation'])
        # The recipe replaces only inner text and the font face; the rest of the
        # HTML skeleton (paragraphs, justify, size, colour, spacing) is pinned.
        assert FACE_RE.sub('face="#"', patched) == FACE_RE.sub('face="#"', entry['translation'])
        assert 'GCCS Alegreya' in patched

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
        'baseline_count': 1706,
        'new_count': 38,
        'cumulative_count': 1744,
        'runtime': 'NOT VERIFIED — ACCEPTED RISK',
        'on_disk_validator': True,
        'temporary_apply_restore': True,
        'layout_check_count': len(layout_evidence),
        'new_binding_counts': NEW_BINDING_COUNTS,
    }
    for name, data in (
            ('reproduction.json', evidence),
            ('layout-validation.json', {'checks': layout_evidence}),
            ('static-validation.json', report)):
        (artifact_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
