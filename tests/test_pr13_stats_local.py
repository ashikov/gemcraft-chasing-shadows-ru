"""Opt-in offline statistics checks; private inputs and artifacts stay outside Git."""
import json
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCInventory, ABCReader
from abc_patch import patch_pushstrings
from addressed_localization import check_layout
from alegreya_localization import build_alegreya_localization, validate_alegreya_localization
from build_prototype import ink_bounds
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from inventory_strings import extract_inventory, parse_edit_text
from localization_fonts import measure_text
from localization_plan import merge_localization_plans, resolve_plan_font_bindings
from swf_geometry import decode_matrix, parse_font, parse_text, read_swf
from test_talisman_local import abc_bytes, abc_inventory, resolved_operations
from translation_catalog import load_catalog

pytestmark = pytest.mark.local

PR12_SHA256 = '7a95c0a5fd73a3a22f9c46f5f8423c7d5064a42d315a473f7171466f3b06e739'
VARIABLES = ['GEMCRAFT_ORIGINAL', 'GEMCRAFT_PR4_WORKSPACE', 'GEMCRAFT_PR13_BASE_PLANS',
             'GEMCRAFT_PR13_STATS_PLAN', 'GEMCRAFT_ALEGREYA_FONT']
NEW_METHODS = {907, 920, 3484}
NEW_BINDING_COUNTS = {920: 1, 3484: 3}
NEW_STACK_BUDGETS = {907: 0, 920: 8, 3484: 8}
EXPECTED_IDS = {'static.573', 'static.574',
                *(f'abc.4175.907.{offset}' for offset in range(10, 188, 3)),
                *(f'abc.4175.3484.{offset}' for offset in (32, 47, 62))}
SORTING_IDS = [0, 56, 26, 27, 25, 16, 33, 55, 58, 39, 15, 57, 5, 6, 7, 8, 9, 10, 11,
               12, 13, 14, 1, 2, 3, 4, 37, 35, 36, 44, 45, 34, 17, 18, 19, 20, 21, 22,
               23, 59, 41, 42, 43, 24, 48, 49, 50, 51, 47, 52, 53, 46, 54, 28, 29, 30,
               31, 32, 38, 40]


def assert_method_preserved(before, after, method, snippets):
    """Check every original operation and branch target after straight-line insertions."""
    old_code = next(code for m, _, code in before.bodies if m == method)
    new_code = next(code for m, _, code in after.bodies if m == method)
    old_ops = before.instructions(old_code, method)
    new_ops = {offset: (opcode, args) for offset, opcode, args in after.instructions(new_code, method)}

    def boundary(offset):
        return offset + sum(len(data) for at, data in snippets if at < offset)

    assert len(new_code) == len(old_code) + sum(len(data) for _, data in snippets)
    occupied = set()
    for at, data in snippets:
        relocated = boundary(at)
        assert new_code[relocated:relocated + len(data)] == data
        occupied.update(relocated + offset for offset, _, _ in after.instructions(data, method))
    for index, (offset, opcode, args) in enumerate(old_ops):
        relocated = boundary(offset) + sum(len(data) for at, data in snippets if at == offset)
        assert relocated not in occupied
        occupied.add(relocated)
        new_opcode, new_args = new_ops[relocated]
        assert opcode == new_opcode
        next_offset = old_ops[index + 1][0] if index + 1 < len(old_ops) else len(old_code)
        if 0x0c <= opcode <= 0x1a:
            assert relocated + next_offset - offset + new_args[0] == boundary(next_offset + args[0])
        elif opcode == 0x1b:
            assert args[1] == new_args[1]
            assert [relocated + delta for delta in (new_args[0], *new_args[2:])] == [
                boundary(offset + delta) for delta in (args[0], *args[2:])]
        elif opcode == 0x2c:
            assert before.strings[args[0]] == after.strings[new_args[0]]
        else:
            assert args == new_args
    assert occupied == set(new_ops)
    old_body, new_body = _bodies(before.reader.data)[method], _bodies(after.reader.data)[method]
    assert new_body[2][0] == old_body[2][0] + NEW_STACK_BUDGETS[method]
    assert old_body[2][1:] == new_body[2][1:]
    assert new_body[4] == [[*(boundary(at) for at in row[:3]), *row[3:]] for row in old_body[4]]
    assert old_body[5] == new_body[5]



def method_metadata(data):
    inventory = ABCInventory(data, {'tag_path': [4175], 'tag_code': 82})
    reader = inventory.reader
    reader.take(4)
    inventory.pools()
    result = []
    for _ in range(reader.count()):
        count = reader.count()
        return_type = reader.u30()
        parameters = [reader.u30() for _ in range(count)]
        name, flags = reader.u30(), reader.u8()
        options = [(reader.u30(), reader.u8()) for _ in range(reader.count())] if flags & 8 else []
        names = [reader.u30() for _ in range(count)] if flags & 128 else []
        result.append((return_type, parameters, name, flags, options, names))
    return result


def test_stats_cumulative_candidate(tmp_path):
    if not all(os.environ.get(name) for name in VARIABLES):
        pytest.skip('Set private original, workspace, PR1–PR12 plans and Alegreya source')
    original_path = Path(os.environ['GEMCRAFT_ORIGINAL'])
    original = original_path.read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    workspace = load_catalog(os.environ['GEMCRAFT_PR4_WORKSPACE'])
    entries = {entry['id']: entry for entry in workspace['entries']}
    bases = [load_catalog(path) for path in json.loads(os.environ['GEMCRAFT_PR13_BASE_PLANS'])]
    plan_path = Path(os.environ['GEMCRAFT_PR13_STATS_PLAN'])
    plan = load_catalog(plan_path)
    scope = load_catalog(plan_path.with_name('scope-inventory.json'))
    inventory = load_catalog(plan_path.with_name('stats-inventory.json'))
    font = Path(os.environ['GEMCRAFT_ALEGREYA_FONT']).read_bytes()
    assert scope['game_sha256'] == ORIGINAL_SHA256
    assert set(plan['entry_ids']) == EXPECTED_IDS and len(plan['entry_ids']) == 65
    assert {row['id'] for row in inventory['entries']} == EXPECTED_IDS
    assert {row['id'] for row in scope['entries']} == EXPECTED_IDS
    assert not plan.get('empty_abc_strings')
    fresh = {entry['id']: entry for entry in extract_inventory(original, ORIGINAL_SHA256)['entries']}
    for row in scope['entries']:
        identifier = row['id']
        entry, source = entries[identifier], fresh[identifier]
        assert entry['translation'] and entry['status'] in ('draft', 'verified')
        assert entry['classification'] == entry['context_confidence'] == 'confirmed'
        assert not entry['mandatory_placeholders']
        for key in ('location', 'source', 'source_sha256'):
            assert row[key] == entry[key] == source[key]

    artifact_dir = Path(os.environ.get('GEMCRAFT_PR13_OUTPUT', tmp_path / 'evidence'))
    artifact_dir.mkdir(parents=True, exist_ok=False)
    old_path, new_path, again_path = [artifact_dir / name for name in (
        'baseline.swf', 'candidate.swf', 'reproduction.swf')]
    baseline, base_report = build_alegreya_localization(original, workspace, bases, font)
    old_path.write_bytes(baseline)
    assert sha(baseline) == PR12_SHA256 and len(baseline) == 45367867
    assert base_report['applied_translations'] == 1744
    merged = merge_localization_plans([*bases, plan], workspace)
    assert len(merged['entry_ids']) == 1744 + 65 == 1809
    assert merged['coverage_blockers'] == base_report['coverage_blockers']
    candidate, report = build_alegreya_localization(original, workspace, merged, font)
    new_path.write_bytes(candidate)
    again, repeated = build_alegreya_localization(original, workspace, merged, font)
    again_path.write_bytes(again)
    assert candidate == again and report == repeated
    assert report['applied_translations'] == 1809 and report['status'] == 'PARTIAL'
    assert set(report['applied_entry_ids']) == set(base_report['applied_entry_ids']) | EXPECTED_IDS
    assert set(base_report['applied_entry_ids']).isdisjoint(EXPECTED_IDS)
    assert report['empty_abc_strings'] == base_report['empty_abc_strings']
    assert all(report['layout_checks'][identifier] == result
               for identifier, result in base_report['layout_checks'].items())

    original_tags, old_tags, new_tags = [read_swf(data)[1] for data in (original, baseline, candidate)]
    original_abc, old_abc, new_abc = [abc_bytes(tags) for tags in (original_tags, old_tags, new_tags)]
    before, old_inv, after = [abc_inventory(data) for data in (original_abc, old_abc, new_abc)]
    original_codes = {method: code for method, _, code in before.bodies}
    old_bodies, new_bodies = _bodies(old_abc), _bodies(new_abc)
    assert method_metadata(original_abc) == method_metadata(new_abc)
    formatter = method_metadata(original_abc)[1245]
    assert formatter[1] == [7, 7, 8] and formatter[3] == 136
    assert formatter[4] == [(1, 3), (10, 10)]  # int-pool[1] = 0; Boolean false.
    integer_reader = ABCReader(original_abc)
    integer_reader.take(4)
    assert integer_reader.count() > 1 and integer_reader.encoded(32) == 0
    assert after.strings[:len(before.strings)] == before.strings
    assert after.multinames[:len(before.multinames)] == before.multinames
    assert set(old_bodies) == set(new_bodies)
    for method, old in old_bodies.items():
        if method in NEW_METHODS:
            continue
        new = new_bodies[method]
        assert resolved_operations(old_inv, old_inv.instructions(old[3], method)) == (
            resolved_operations(after, after.instructions(new[3], method)))
        assert old[2] == new[2] and old[4:] == new[4:]
    assert not [item for item in after.unsupported + after.control_flow_anomalies
                if item['method_index'] in NEW_METHODS]

    selected_abc = [entries[identifier] for identifier in merged['entry_ids']
                    if identifier.startswith('abc.')]
    selected_abc += [{**fresh[identifier], 'translation': ''}
                     for identifier in merged['empty_abc_strings']]
    translated = patch_pushstrings(original_abc, selected_abc)
    translated_inv = abc_inventory(translated)
    for method in NEW_METHODS:
        old_ops = before.instructions(original_codes[method], method)
        code = next(code for m, _, code in translated_inv.bodies if m == method)
        patched_ops = translated_inv.instructions(code, method)
        expected = {entries[i]['location']['code_offset']: entries[i]['translation']
                    for i in EXPECTED_IDS if entries[i]['location'].get('method_index') == method}
        assert len(expected) == {907: 60, 920: 0, 3484: 3}[method]
        assert len(old_ops) == len(patched_ops)
        for (offset, opcode, args), (new_offset, new_opcode, new_args) in zip(old_ops, patched_ops):
            assert (offset, opcode) == (new_offset, new_opcode)
            if offset in expected:
                assert opcode == 0x2c and translated_inv.strings[new_args[0]] == expected[offset]
            else:
                assert args == new_args

    snippets, _, counts = resolve_plan_font_bindings(original_abc, translated, merged['abc_font_bindings'])
    snippets = snippets(len(translated_inv.strings), len(translated_inv.multinames))
    assert {m: counts[str(m)] for m in NEW_BINDING_COUNTS} == NEW_BINDING_COUNTS
    sites = plan['abc_font_bindings']
    assert [(site['method_index'], site['instruction_index']) for site in sites] == [
        (920, 106), (3484, 18), (3484, 24), (3484, 30)]
    expected_receivers = ['d366e018', 'd066921c66f235', 'd066c41c66f235', 'd066c51c66f235']
    for site, receiver in zip(sites, expected_receivers):
        assert site.get('receivers_hex', [site.get('receiver_hex')]) == [receiver]
    assert before.instructions(original_codes[920], 920)[105] == (267, 0x61, [5329])
    assert before.instructions(original_codes[920], 920)[145] == (365, 0x66, [5360])
    for method in NEW_METHODS:
        assert_method_preserved(translated_inv, after, method, snippets.get(method, []))

    ops907 = before.instructions(original_codes[907], 907)
    assert [args[0] for offset, opcode, args in ops907 if 296 <= offset <= 414] == SORTING_IDS
    assert ops907[66] == (190, 0x56, [60])
    assert ops907[195] == (418, 0x68, [1641])
    ops920 = before.instructions(original_codes[920], 920)
    assert [(offset, before.strings[args[0]]) for offset, opcode, args in ops920 if opcode == 0x2c] == [
        (542, 'abcId'), (657, 'xpForSorting')]
    assert [(offset, args) for offset, opcode, args in ops920 if opcode == 0x46 and args[0] == 4755] == [
        (328, [4755, 1]), (817, [4755, 1])]
    assert before.multiname(2133) == 'NumberFormatter' and before.multiname(4755) == 'format'
    formatter_code = original_codes[1245]
    formatter_ops = before.instructions(formatter_code, 1245)
    assert next(args for offset, opcode, args in formatter_ops if offset == 724) == [8458]
    assert before.strings[8458] == ','
    assert (200, 0x46, [5275, 1]) in formatter_ops and before.multiname(5275) == 'round'
    accumulator = before.instructions(original_codes[583], 583)
    assert [(offset, opcode, args) for offset, opcode, args in accumulator if 1296 <= offset <= 1328] == [
        (1296, 0x66, [5146]), (1299, 0x60, [5237]), (1302, 0x60, [230]),
        (1305, 0x66, [4198]), (1308, 0x66, [293]), (1311, 0x60, [2438]),
        (1314, 0x66, [5146]), (1317, 0x66, [5647]), (1320, 0xd0, []),
        (1321, 0x66, [1094]), (1324, 0x46, [5240, 2]), (1328, 0x61, [5647])]
    assert before.multiname(5146) == 'MOST_WAVES_BEATEN_IN_ONE_BATTLE'
    assert before.multiname(1094) == 'wavesBeaten' and before.multiname(5240) == 'max'
    # The ambiguous English label denotes a count per battle, never an ordinal.
    assert entries['abc.4175.907.91']['translation'] == 'Больше всего волн отбито за бой'
    for offset in range(10, 188, 3):
        entry = entries[f'abc.4175.907.{offset}']
        assert entry['source'].startswith(' - ') == entry['translation'].startswith(' - ')
    # Labels and numbers occupy independent fields. No suffix is concatenated,
    # so neutral labels remain grammatical at zero, singular and large values.
    label_value_samples = [(entries[f'abc.4175.907.{offset}']['translation'], value)
                           for offset in range(10, 188, 3)
                           for value in ('0', '1', '2', '5', '21', '1,000', '1,234,567')]
    assert len(label_value_samples) == 420
    assert all('\n' not in label and label.strip() and not label.endswith(' ') and value
               for label, value in label_value_samples)

    def preserved_tags(tags):
        return [(tag.code, tag.payload) for tag in tags
                if not (tag.code == 82 or tag.code == 75
                        and int.from_bytes(tag.payload[:2], 'little') in (6001, 6002)
                        or tag.code in (11, 33)
                        and int.from_bytes(tag.payload[:2], 'little') in (573, 574))]

    assert len(old_tags) == len(new_tags)
    assert preserved_tags(old_tags) == preserved_tags(new_tags)
    old_fonts, new_fonts = [{int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
                             for tag in tags if tag.code == 75} for tags in (old_tags, new_tags)]
    for identifier in (6001, 6002):
        old_font, new_font = old_fonts[identifier], new_fonts[identifier]
        assert {key: value for key, value in old_font.items()
                if key not in ('codes', 'glyphs', 'advances', 'bounds', 'kerning', 'offsets', 'code_offset')} == {
                    key: value for key, value in new_font.items()
                    if key not in ('codes', 'glyphs', 'advances', 'bounds', 'kerning', 'offsets', 'code_offset')}
        indices = {code: index for index, code in enumerate(new_font['codes'])}
        for index, code in enumerate(old_font['codes']):
            for key in ('glyphs', 'advances', 'bounds'):
                assert old_font[key][index] == new_font[key][indices[code]]
        assert set(map(tuple, old_font['kerning'])) <= set(map(tuple, new_font['kerning']))

    edits = {int.from_bytes(tag.payload[:2], 'little'): (index, parse_edit_text(tag.payload)[2])
             for index, tag in enumerate(original_tags) if tag.code == 37}
    index, formatting = edits[585]
    assert index == 650 and formatting['font_id'] == 255
    assert formatting['bounds'] == [-40, 8760, -40, 345]
    assert formatting['font_height'] == 240 and formatting['leading'] == 40
    assert formatting['multiline'] and formatting['word_wrap'] and not formatting['auto_size']
    assert edits[565][1]['bounds'] == [1054, 4258, -40, 345]
    layout_evidence = []
    for identifier in plan['entry_ids']:
        layout = plan['layouts'][identifier]
        face = new_fonts[6001 if layout['font_id'] == 1 else 6002]
        result = check_layout(face, entries[identifier]['translation'], layout)
        assert result['lines'] == 1
        if identifier.startswith('abc.'):
            assert layout['height'] == 240 and layout['max_lines'] == 1
            assert layout['max_width'] == (8720 if '.907.' in identifier else 3124)
            # A single line uses its ink around the original baseline; ascent
            # plus descent is not the height of this fixed-size Flash field.
            # Leading applies only to a subsequent line, which is forbidden.
            assert 'max_height' not in layout
            measured = measure_text(face, entries[identifier]['translation'], 240)
            text_baseline = 40 + face['ascent'] * 240 / 20480
            assert measured['bounds'][0] is not None
            assert text_baseline + measured['bounds'][0][2] >= 40
            assert text_baseline + measured['bounds'][0][3] <= 345
        else:
            character = entries[identifier]['location']['character_id']
            original_tag = next(t for t in original_tags if t.code in (11, 33)
                                and int.from_bytes(t.payload[:2], 'little') == character)
            new_tag = next(t for t in new_tags if t.code in (11, 33)
                           and int.from_bytes(t.payload[:2], 'little') == character)
            old_text, new_text = [parse_text(t.payload, t.code) for t in (original_tag, new_tag)]
            assert new_text['matrix_raw'] == old_text['matrix_raw']
            assert len(old_text['records']) == len(new_text['records']) == 1
            old_row, new_row = old_text['records'][0], new_text['records'][0]
            assert new_row['font_id'] == face['id']
            assert {k: v for k, v in old_row.items() if k not in ('font_id', 'x', 'entries')} == {
                k: v for k, v in new_row.items() if k not in ('font_id', 'x', 'entries')}
            assert ''.join(chr(face['codes'][i]) for i, _ in new_row['entries']) == entries[identifier]['translation']
            old_ink = ink_bounds(old_fonts[old_row['font_id']], old_row['entries'],
                                 old_row['height'], old_row['x'], 0)
            new_ink = ink_bounds(face, new_row['entries'], new_row['height'], new_row['x'], 0)
            assert abs(sum(old_ink[:2]) - sum(new_ink[:2])) <= 1
            assert layout['center'] and layout['max_width'] == 8000
            bounds = ink_bounds(face, new_row['entries'], new_row['height'], new_row['x'], new_row['y'])
            tx, ty = decode_matrix(new_text['matrix_raw'])[0]['translate']
            assert new_text['bounds'] == [bounds[0] + tx, bounds[1] + tx, bounds[2] + ty, bounds[3] + ty]
        layout_evidence.append({'id': identifier, 'layout': layout, 'result': result})

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
    assert game.read_bytes() == baseline and original_path.read_bytes() == original
    evidence = {'baseline_sha256': sha(baseline), 'candidate_sha256': sha(candidate),
                'reproduction_sha256': sha(again), 'baseline_count': 1744, 'new_count': 65,
                'cumulative_count': 1809, 'runtime': 'NOT VERIFIED — ACCEPTED RISK',
                'on_disk_validator': True, 'temporary_apply_restore': True,
                'layout_check_count': len(layout_evidence), 'label_value_sample_count': 420,
                'new_binding_counts': NEW_BINDING_COUNTS}
    for name, data in (('reproduction.json', evidence), ('static-validation.json', report),
                       ('layout-validation.json', {'checks': layout_evidence})):
        (artifact_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
