"""Private offline bitmap-path contracts; never launches or alters the game."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import _string_pool_bounds, patch_pushstrings
from bitmap_font_binding import bitmap_font_selector, bitmap_selector_pool_indices
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from localization_plan import resolve_plan_font_bindings
from swf_geometry import read_swf
from test_battle_hud_local import preserve_method
from test_bitmap_floating_texts import IDS, PLAN
from test_bitmap_font_binding import execute
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory, resolved_operations
from translation_catalog import load_catalog

pytestmark = pytest.mark.local
BASE_COMMIT = 'edb391bf3263a5c97d596111cd894931fa959df8'
BASE_SHA = '544d2836f5d7206c8d7ece958064cdee0919f1c3f301ddbcebc4eaab93ca0c7b'


@pytest.fixture(scope='module')
def bitmap_resources():
    if not os.environ.get('GEMCRAFT_PR30_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR30_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR30_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    baseline = (root / 'baseline/gc-cs-steam.ru.swf').read_bytes()
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    assert sha(baseline) == BASE_SHA
    assert sha(candidate) == load_catalog('translations/build-manifest.json')['expected_candidate_sha256']
    return root, original, baseline, candidate


def previous_json(path):
    return json.loads(subprocess.check_output(['git', 'show', f'{BASE_COMMIT}:{path}']))


def test_bitmap_preserves_catalog_manifest_resources_and_all_other_consumers(bitmap_resources):
    root, original, baseline, candidate = bitmap_resources
    old_catalog = previous_json('translations/catalog.json')
    catalog = load_catalog('translations/catalog.json')
    old_rows = {row['id']: row for row in old_catalog['entries']}
    rows = {row['id']: row for row in catalog['entries']}
    assert all(rows[key] == row for key, row in old_rows.items())
    assert set(rows) - set(old_rows) == IDS
    assert sum(isinstance(row['manual'].get('translation'), str) for row in rows.values()) == 2905
    manifest = load_catalog('translations/build-manifest.json')
    old_manifest = previous_json('translations/build-manifest.json')
    assert manifest['plans'][:25] == old_manifest['plans']
    assert len(manifest['plans']) == 26
    assert manifest['plans'][-1]['path'] == 'plans/pr30-bitmap-floating-texts.json'
    assert all(sha((Path('translations') / row['path']).read_bytes()) == row['sha256']
               for row in manifest['plans'])
    old_plan = load_catalog(root / 'baseline/plan.json')
    plan = load_catalog(root / 'candidate/plan.json')
    assert len(old_plan['entry_ids']) == 2822
    assert len(plan['entry_ids']) == 2831
    assert set(plan['entry_ids']) == set(old_plan['entry_ids']) | IDS
    supplemental = {key for key, row in old_rows.items()
                    if isinstance(row['manual'].get('translation'), str)
                    and key not in old_plan['entry_ids']}
    assert len(supplemental) == 74 and supplemental.isdisjoint(plan['entry_ids'])
    tags = [read_swf(data)[1] for data in (original, baseline, candidate)]
    original_abc, base_abc, new_abc = map(abc_bytes, tags)
    orig, base, new = map(abc_inventory, (original_abc, base_abc, new_abc))
    assert method_metadata(original_abc) == method_metadata(new_abc)
    assert new.strings[:len(orig.strings)] == orig.strings
    assert new.multinames[:len(orig.multinames)] == orig.multinames
    assert original_abc[:_string_pool_bounds(original_abc)[0]] == (
        new_abc[:_string_pool_bounds(new_abc)[0]])  # every int/uint/double pool
    # No SWF changes beyond the ABC tag, including all five font payloads,
    # their outlines/metrics/alignment zones and every old text resource.
    assert [(t.code, t.payload) for t in tags[1] if t.code != 82] == [
        (t.code, t.payload) for t in tags[2] if t.code != 82]
    old_bodies, new_bodies = _bodies(base_abc), _bodies(new_abc)
    assert old_bodies.keys() == new_bodies.keys()
    for method, body in old_bodies.items():
        if method in (597, 713, 1732):
            continue
        assert body[2] == new_bodies[method][2]
        assert body[4:] == new_bodies[method][4:]
        assert resolved_operations(base, base.instructions(body[3], method)) == (
            resolved_operations(new, new.instructions(new_bodies[method][3], method)))
    # The PR29 code and its family choices remain semantically identical.
    for method in (352, 353, 1731, 1733):
        assert old_bodies[method][2:] == new_bodies[method][2:]
    assert not [row for row in new.unsupported if row['method_index'] in (597, 713, 1732)]
    # Preserve the original unreachable compiler tail after returnvalue@510.
    assert [row for row in new.control_flow_anomalies
            if row['method_index'] in (597, 713, 1732)] == [
                {'method_index': 713, 'target_offset': 520}]


def test_bitmap_translation_widths_call_parameters_and_selector_order(bitmap_resources):
    root, original, _, candidate = bitmap_resources
    original_abc = abc_bytes(read_swf(original)[1])
    new_abc = abc_bytes(read_swf(candidate)[1])
    before, after = map(abc_inventory, (original_abc, new_abc))
    original_bodies, new_bodies = _bodies(original_abc), _bodies(new_abc)
    plan = load_catalog(root / 'candidate/plan.json')
    workspace = load_catalog(root / 'candidate/workspace.json')
    inventory = load_catalog(root / 'candidate/inventory.json')
    source_rows = {row['id']: row for row in inventory['entries']}
    selected = [row for row in workspace['entries'] if row['id'] in plan['entry_ids']
                and row['category'] == 'actionscript']
    selected += [{**source_rows[key], 'translation': ''} for key in plan['empty_abc_strings']]
    translated = patch_pushstrings(original_abc, selected)
    translated_inv = abc_inventory(translated)
    builder, increments, counts = resolve_plan_font_bindings(
        original_abc, translated, load_catalog(PLAN)['abc_font_bindings'])
    snippets = builder(len(translated_inv.strings), len(translated_inv.multinames))
    assert counts == {'1732': 1} and increments == {1732: 4}
    assert snippets[1732][0][0] == 214
    preserve_method(translated_inv, after, 1732, snippets[1732], 4)
    for method in (597, 713):
        old_ops = before.instructions(original_bodies[method][3], method)
        new_ops = after.instructions(new_bodies[method][3], method)
        assert len(old_ops) == len(new_ops)
        assert original_bodies[method][2] == new_bodies[method][2]
        assert original_bodies[method][4:] == new_bodies[method][4:]
        for old, new in zip(old_ops, new_ops):
            assert old[:2] == new[:2]  # widths, addresses, opcodes, branch targets
            if old[1] != 0x2c:
                assert old == new  # numbers, coordinates, colour, delays, effects
    code = new_bodies[1732][3]
    ops = after.instructions(code, 1732)
    names = [(at, after.multiname(args[0])) for at, op, args in ops
             if op in (0x61, 0x66, 0x46, 0x4f, 0x4a)]
    at = {name: next(offset for offset, n in names if n == name)
          for name in ('test', 'defaultTextFormat', 'text', 'width', 'height', 'BitmapData', 'draw')}
    # The earlier text='' clears the reused field before font selection.
    text_write = next(offset for offset, op, args in ops
                      if op == 0x61 and after.multiname(args[0]) == 'text'
                      and offset > at['defaultTextFormat'])
    assert 214 < at['test'] < at['defaultTextFormat'] < text_write < at['width']
    assert at['width'] < at['height'] < at['BitmapData'] < at['draw']
    indices = bitmap_selector_pool_indices(translated)
    selector = bitmap_font_selector(after.strings.index('GCCS Alegreya'), **indices)
    # Every unmodified call site's literal fragments take the original family;
    # dynamic numbers do not introduce Cyrillic. A preceding Russian call has
    # no persistent state in the selector or fresh per-call TextFormat.
    original_calls = []
    for method, body in original_bodies.items():
        calls = [offset for offset, op, args in before.instructions(body[3], method)
                 if op in (0x46, 0x4f) and before.multiname(args[0]) in (
                     'createFloatingText', 'createFloatingTextOutgame')]
        original_calls.extend((method, offset) for offset in calls)
    assert len(original_calls) == 62
    matrix = load_catalog(root / 'bitmap-consumers.json')
    assert {(row['method'], row['call_offset']) for row in matrix['calls']} == set(original_calls)
    for row in matrix['calls']:
        value = '1'.join(fragment['text'] for fragment in row['source_fragments'])
        assert execute(selector, after, value)[-1] == 'Original decorative family'
    assert execute(selector, after, 'Не хватает маны')[-1] == 'GCCS Alegreya'
    assert execute(selector, after, 'Latin after Russian')[-1] == 'Original decorative family'


def test_bitmap_independent_builds_disk_validation_and_temp_transition(bitmap_resources, tmp_path):
    root, original, baseline, candidate = bitmap_resources
    assert (root / 'independent/gc-cs-steam.ru.swf').read_bytes() == candidate
    report = load_catalog(root / 'candidate/static-validation.json')
    assert report == load_catalog(root / 'candidate/on-disk-validation.json')
    assert report == load_catalog(root / 'independent/static-validation.json')
    assert report['applied_translations'] == 2831
    assert report['patched_sha256'] == sha(candidate)
    old_report = load_catalog(root / 'baseline/static-validation.json')
    assert all(report['layout_checks'][key] == value for key, value in old_report['layout_checks'].items())
    game = tmp_path / 'game/gc-cs-steam.swf'
    game.parent.mkdir()
    game.write_bytes(original)
    backup_dir = tmp_path / 'backup'
    # Candidate -> restore -> baseline -> restore, all on temporary owned files.
    for directory, expected in (('candidate', candidate), ('baseline', baseline)):
        manifest = apply_patch_file(game, root / directory / 'gc-cs-steam.ru.swf',
                                     backup_dir, ORIGINAL_SHA256, sha(expected))
        assert game.read_bytes() == expected
        backup = Path(load_catalog(manifest)['backup_path'])
        assert backup.read_bytes() == original
        with pytest.raises(ValueError):
            apply_patch_file(game, root / directory / 'gc-cs-steam.ru.swf', backup_dir,
                             ORIGINAL_SHA256, sha(expected))
        restore_patch_file(manifest)
        assert game.read_bytes() == original and backup.read_bytes() == original
    assert Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes() == original
