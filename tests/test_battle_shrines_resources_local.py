"""Cumulative shrine resource isolation against private offline artifacts."""
import json
import os
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from localization_plan import resolve_plan_font_bindings
from swf_geometry import parse_font, read_swf
from test_battle_hud_local import preserve_method
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory, resolved_operations
from translation_catalog import load_catalog

pytestmark = pytest.mark.local
PLAN = 'translations/plans/pr27-battle-shrines.json'
BASE_COMMIT = '0ea4f9862cb5a32ee6bf6131d0d406d552f3ee36'
BASE_SHA = 'ce17870436b5842a9754fc18addeb538b2b45cc4d3ea47afa766b3130a51cf7c'


@pytest.fixture(scope='module')
def shrine_resources():
    if not os.environ.get('GEMCRAFT_PR27_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR27_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR27_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    base = (root / 'baseline/gc-cs-steam.ru.swf').read_bytes()
    assert sha(base) == BASE_SHA
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    assert sha(candidate) == load_catalog('translations/build-manifest.json')['expected_candidate_sha256']
    workspace = load_catalog(root / 'candidate/workspace.json')
    return root, original, base, candidate, workspace


def test_full_plan_preserves_resources_and_abc(shrine_resources):
    root, original, base, candidate, workspace = shrine_resources
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
    manifest = load_catalog('translations/build-manifest.json')
    previous_manifest = json.loads(subprocess.check_output([
        'git', 'show', f'{BASE_COMMIT}:translations/build-manifest.json']))
    assert len(previous_manifest['plans']) == 22
    assert len(manifest['plans']) == 23
    assert manifest['plans'][:22] == previous_manifest['plans']
    assert manifest['plans'][22]['path'] == 'plans/pr27-battle-shrines.json'
    previous_manual = json.loads(subprocess.check_output([
        'git', 'show', f'{BASE_COMMIT}:translations/catalog.json']))
    old_manual = {row['id']: row for row in previous_manual['entries']}
    new_manual = {row['id']: row for row in load_catalog('translations/catalog.json')['entries']}
    assert len(new_manual) == len(load_catalog('translations/catalog.json')['entries'])
    assert all(new_manual[key] == row for key, row in old_manual.items()
               if key not in new_plan['entry_ids'] or isinstance(row['manual'].get('translation'), str))
    assert len(new_plan['entry_ids']) == len(set(new_plan['entry_ids']))
    for item in manifest['plans']:
        assert sha((Path('translations') / item['path']).read_bytes()) == item['sha256']
    changed = {site['method_index'] for site in new_plan['abc_font_bindings']}
    changed |= {entries[key]['location']['method_index'] for key in new_plan['entry_ids']}
    assert changed == {630, 695, 780, 784, 786, 791}
    old_plan = load_catalog(root / 'baseline/plan.json')
    old_entries = {e['id']: e for e in load_catalog(root / 'baseline/workspace.json')['entries']}
    assert len(old_plan['entry_ids']) == 2524
    assert len(merged['entry_ids']) == 2524 + len(new_plan['entry_ids'])
    assert set(old_plan['entry_ids']).isdisjoint(new_plan['entry_ids'])
    assert set(merged['entry_ids']) == set(old_plan['entry_ids']) | set(new_plan['entry_ids'])
    assert all(e == entries[key] for key, e in old_entries.items()
               if key not in new_plan['entry_ids'])
    assert sum(e['translation'] is not None for e in workspace['entries']) == 2598 + len(new_plan['entry_ids'])
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
    # Original resources and every existing glyph remain unchanged.
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


def test_hash_guarded_apply_restore_in_temporary_game(shrine_resources, tmp_path):
    root, original, baseline, candidate, _ = shrine_resources
    game = tmp_path / 'game' / 'gc-cs-steam.swf'
    game.parent.mkdir()
    game.write_bytes(original)
    backup_dir = tmp_path / 'backup'
    for directory, expected in (('baseline', baseline), ('candidate', candidate)):
        manifest_path = apply_patch_file(
            game, root / directory / 'gc-cs-steam.ru.swf', backup_dir,
            ORIGINAL_SHA256, sha(expected))
        assert game.read_bytes() == expected
        manifest = load_catalog(manifest_path)
        backup = Path(manifest['backup_path'])
        assert backup.read_bytes() == original
        assert sha(backup.read_bytes()) == ORIGINAL_SHA256
        restore_patch_file(manifest_path)
        assert game.read_bytes() == original
        assert backup.read_bytes() == original
    assert Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes() == original
    assert (root / 'baseline/gc-cs-steam.ru.swf').read_bytes() == baseline
    assert (root / 'candidate/gc-cs-steam.ru.swf').read_bytes() == candidate
