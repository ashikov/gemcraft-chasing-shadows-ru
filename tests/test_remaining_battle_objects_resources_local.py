"""Cumulative object patch isolation against private offline artifacts."""

import json
import os
import subprocess
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
BASE_SHA = '4fe24607db3aa9039dd7aa6d54708764c428d3035c8dd5c5c24fa73a30ef9034'
BASE_COMMIT = '0655ecbda041d1cb0151bce5a8ff0083a5c1bc82'


@pytest.fixture(scope='module')
def object_resources():
    if not os.environ.get('GEMCRAFT_PR29_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR29_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR29_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    baseline = (root / 'baseline/gc-cs-steam.ru.swf').read_bytes()
    assert sha(baseline) == BASE_SHA
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    assert sha(candidate) == load_catalog('translations/build-manifest.json')['expected_candidate_sha256']
    return root, original, baseline, candidate


def test_object_patch_preserves_prior_catalog_methods_resources_and_glyphs(object_resources):
    root, original, baseline, candidate = object_resources
    original_tags, base_tags, candidate_tags = [read_swf(data)[1]
                                                  for data in (original, baseline, candidate)]
    original_abc, base_abc, candidate_abc = map(abc_bytes,
                                                (original_tags, base_tags, candidate_tags))
    original_inv, base_inv, candidate_inv = map(abc_inventory,
                                                (original_abc, base_abc, candidate_abc))
    plan = load_catalog(root / 'candidate/plan.json')
    new_plan = load_catalog('translations/plans/pr29-remaining-battle-objects.json')
    baseline_plan = load_catalog(root / 'baseline/plan.json')
    assert len(baseline_plan['entry_ids']) == 2683
    assert len(plan['entry_ids']) == 2822
    assert set(baseline_plan['entry_ids']).isdisjoint(new_plan['entry_ids'])
    assert set(plan['entry_ids']) == set(baseline_plan['entry_ids']) | set(new_plan['entry_ids'])
    manifest = load_catalog('translations/build-manifest.json')
    previous = json.loads(subprocess.check_output([
        'git', 'show', f'{BASE_COMMIT}:translations/build-manifest.json']))
    assert manifest['plans'][:24] == previous['plans']
    assert len(manifest['plans']) == 25
    assert manifest['plans'][-1]['path'] == 'plans/pr29-remaining-battle-objects.json'
    for item in manifest['plans']:
        assert sha((Path('translations') / item['path']).read_bytes()) == item['sha256']
    old_rows = {row['id']: row for row in json.loads(subprocess.check_output([
        'git', 'show', f'{BASE_COMMIT}:translations/catalog.json']))['entries']}
    new_rows = {row['id']: row for row in load_catalog('translations/catalog.json')['entries']}
    assert all(new_rows[key] == row for key, row in old_rows.items()
               if isinstance(row['manual'].get('translation'), str))
    assert all(new_rows[key] == row for key, row in old_rows.items()
               if key not in new_plan['entry_ids'])
    assert sum(isinstance(row['manual'].get('translation'), str)
               for row in old_rows.values()) == 2757
    assert sum(isinstance(row['manual'].get('translation'), str)
               for row in new_rows.values()) == 2896
    assert set(old_rows) <= set(new_rows)
    supplemental = {key for key, row in old_rows.items()
                    if isinstance(row['manual'].get('translation'), str)
                    and key not in baseline_plan['entry_ids']}
    assert len(supplemental) == 74
    assert supplemental.isdisjoint(plan['entry_ids'])
    assert len(set(plan['entry_ids'])) == 2822
    workspace = load_catalog(root / 'candidate/workspace.json')
    entries = {row['id']: row for row in workspace['entries']}
    prior_entries = {row['id']: row for row in load_catalog(root / 'baseline/workspace.json')['entries']}
    assert all(row == entries[key] for key, row in prior_entries.items()
               if key not in new_plan['entry_ids'])
    selected = [row for row in workspace['entries']
                if row['id'] in plan['entry_ids'] and row['id'].startswith('abc.')]
    inventory_rows = {row['id']: row for row in
                      load_catalog(root / 'candidate/inventory.json')['entries']}
    selected += [{**inventory_rows[key], 'translation': ''} for key in plan['empty_abc_strings']]
    translated = patch_pushstrings(original_abc, selected)
    translated_inv = abc_inventory(translated)
    builder, increments, _ = resolve_plan_font_bindings(
        original_abc, translated, plan['abc_font_bindings'])
    snippets = builder(len(translated_inv.strings), len(translated_inv.multinames))
    preserve_method(translated_inv, candidate_inv, 630, snippets.get(630, []),
                    increments.get(630, 0))
    assert method_metadata(base_abc) == method_metadata(candidate_abc)
    old_bodies, new_bodies = _bodies(base_abc), _bodies(candidate_abc)
    assert old_bodies.keys() == new_bodies.keys()
    # Warning texts and bitmap drawing stay outside this addressed panel plan.
    original_bodies = _bodies(original_abc)
    for method in (597, 713, 352, 1732):
        assert original_bodies[method][2:] == old_bodies[method][2:]
        assert old_bodies[method][2:] == new_bodies[method][2:]
    for method, body in old_bodies.items():
        if method == 630:
            continue
        assert resolved_operations(base_inv, base_inv.instructions(body[3], method)) == (
            resolved_operations(candidate_inv,
                                candidate_inv.instructions(new_bodies[method][3], method)))
        assert body[2] == new_bodies[method][2]
        assert body[4:] == new_bodies[method][4:]
    assert candidate_inv.strings[:len(original_inv.strings)] == original_inv.strings
    assert candidate_inv.multinames[:len(original_inv.multinames)] == original_inv.multinames
    assert not [row for row in candidate_inv.unsupported + candidate_inv.control_flow_anomalies
                if row['method_index'] == 630]

    def unchanged(tags):
        return [(tag.code, tag.payload) for tag in tags if tag.code != 82 and not (
            tag.code == 75 and int.from_bytes(tag.payload[:2], 'little') in (6001, 6002))]

    assert unchanged(base_tags) == unchanged(candidate_tags)
    fonts = [{int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
              for tag in tags if tag.code == 75} for tags in (base_tags, candidate_tags)]
    for font_id in (6001, 6002):
        old, new = [font_set[font_id] for font_set in fonts]
        positions = {code: index for index, code in enumerate(new['codes'])}
        for index, code in enumerate(old['codes']):
            for key in ('glyphs', 'advances', 'bounds'):
                assert old[key][index] == new[key][positions[code]]
        assert set(map(tuple, old['kerning'])) <= set(map(tuple, new['kerning']))


def test_object_candidate_independent_rebuild_and_disk_reports(object_resources):
    root, _, _, candidate = object_resources
    plan = load_catalog(root / 'candidate/plan.json')
    report = load_catalog(root / 'candidate/static-validation.json')
    assert (root / 'independent/gc-cs-steam.ru.swf').read_bytes() == candidate
    assert report == load_catalog(root / 'candidate/on-disk-validation.json')
    assert report == load_catalog(root / 'independent/on-disk-validation.json')
    assert report['patched_sha256'] == sha(candidate)
    assert set(report['applied_entry_ids']) == set(plan['entry_ids'])


def test_object_candidate_apply_restore_only_on_temporary_copy(object_resources, tmp_path):
    root, original, baseline, candidate = object_resources
    game = tmp_path / 'game/gc-cs-steam.swf'
    game.parent.mkdir()
    game.write_bytes(original)
    backup_dir = tmp_path / 'backup'
    for directory, expected in (('baseline', baseline), ('candidate', candidate)):
        manifest_path = apply_patch_file(game, root / directory / 'gc-cs-steam.ru.swf',
                                         backup_dir, ORIGINAL_SHA256, sha(expected))
        assert game.read_bytes() == expected
        backup = Path(load_catalog(manifest_path)['backup_path'])
        assert backup.read_bytes() == original
        restore_patch_file(manifest_path)
        assert game.read_bytes() == original
        assert backup.read_bytes() == original
    assert Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes() == original
