"""Synthetic bundle migration, integrity and readiness checks."""
import copy
import hashlib
import json

import pytest

from localization_bundle import load_bundle, migrate_plans


def fixture(tmp_path):
    workspace = {'game_sha256': 'a' * 64, 'conflicts': [], 'entries': [
        {'id': 'title', 'category': 'static', 'location': {'tag_path': [2]},
         'translation': '<b>Ёж {value}</b>', 'status': 'draft',
         'classification': 'confirmed', 'context_confidence': 'confirmed'}]}
    plan = {'schema_version': 1, 'game_sha256': 'a' * 64, 'entry_ids': ['title'],
            'fonts': [1], 'layouts': {}, 'coverage_blockers': ['Historical blocker'],
            'font_recipes': {'1': {'preserve': True}}, 'runtime_verified': ['title']}
    source = tmp_path / 'source.json'
    source.write_text(json.dumps(plan), encoding='utf-8')
    return workspace, plan, source


def migrate(tmp_path, workspace, source):
    return migrate_plans([source], ['pr03-options.json'], tmp_path / 'bundle', workspace,
                         font_sha256='b' * 64, expected_sha256='c' * 64,
                         applied_count=1)


def test_lossless_migration_is_deterministic_and_never_overwrites(tmp_path):
    work, plan, source = fixture(tmp_path)
    manifest = migrate(tmp_path, work, source)
    assert json.loads((manifest.parent / 'plans/pr03-options.json').read_text()) == plan
    assert load_bundle(manifest, work)['entry_ids'] == ['title']
    before = {p.relative_to(manifest.parent): p.read_bytes()
              for p in manifest.parent.rglob('*.json')}
    with pytest.raises(FileExistsError):
        migrate(tmp_path, work, source)
    other = tmp_path / 'other'
    other.mkdir()
    second = migrate(other, work, source)
    assert before == {p.relative_to(second.parent): p.read_bytes()
                      for p in second.parent.rglob('*.json')}


@pytest.mark.parametrize('problem', ['hash', 'traversal', 'symlink', 'duplicate',
                                    'game', 'count', 'status', 'missing', 'unconfirmed',
                                    'conflict', 'empty_translation'])
def test_bundle_fails_closed(tmp_path, problem):
    work, _, source = fixture(tmp_path)
    manifest = migrate(tmp_path, work, source)
    data = json.loads(manifest.read_text())
    path = manifest.parent / data['plans'][0]['path']
    if problem == 'hash':
        path.write_text('{}')
    elif problem == 'traversal':
        data['plans'][0]['path'] = '../source.json'
    elif problem == 'symlink':
        path.unlink()
        path.symlink_to(source)
    elif problem == 'duplicate':
        data['plans'].append(copy.deepcopy(data['plans'][0]))
    elif problem == 'game':
        work['game_sha256'] = 'd' * 64
    elif problem == 'count':
        data['applied_translations'] = 2
    elif problem == 'status':
        work['entries'][0]['status'] = 'needs_review'
    elif problem == 'missing':
        work['entries'] = []
    elif problem == 'unconfirmed':
        work['entries'][0]['classification'] = 'probable'
    elif problem == 'conflict':
        work['conflicts'] = [{'id': 'title'}]
    else:
        work['entries'][0]['translation'] = None
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_bundle(manifest, work)


def test_duplicate_bindings_and_addresses_still_fail(tmp_path):
    work, plan, source = fixture(tmp_path)
    work['entries'].append({**work['entries'][0], 'id': 'other'})
    plan['entry_ids'].append('other')
    source.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='address'):
        migrate(tmp_path, work, source)


def test_manifest_rejects_duplicate_json_keys(tmp_path):
    work, _, source = fixture(tmp_path)
    manifest = migrate(tmp_path, work, source)
    manifest.write_text('{"schema_version": 1, "schema_version": 1}')
    with pytest.raises(ValueError, match='Duplicate JSON'):
        load_bundle(manifest, work)


def test_plan_digest_is_exact_serialized_file(tmp_path):
    work, _, source = fixture(tmp_path)
    manifest = migrate(tmp_path, work, source)
    data = json.loads(manifest.read_text())
    item = data['plans'][0]
    assert hashlib.sha256((manifest.parent / item['path']).read_bytes()).hexdigest() == item['sha256']


def test_structural_check_does_not_claim_original_context(tmp_path):
    from localization_bundle import validate_bundle_structure

    work, _, source = fixture(tmp_path)
    manifest = migrate(tmp_path, work, source)
    del work['entries'][0]['classification']
    del work['entries'][0]['context_confidence']
    assert validate_bundle_structure(manifest, work)['entry_ids'] == ['title']
    with pytest.raises(ValueError, match='not ready'):
        load_bundle(manifest, work)
    work['entries'][0]['status'] = 'needs_review'
    with pytest.raises(ValueError, match='not ready'):
        validate_bundle_structure(manifest, work)


@pytest.mark.parametrize('problem', ['id', 'binding', 'plan_game', 'schema'])
def test_migration_validates_before_creating_destination(tmp_path, problem):
    work, plan, source = fixture(tmp_path)
    if problem == 'id':
        plan['entry_ids'].append('title')
    elif problem == 'binding':
        site = {'tag_path': [4], 'method_index': 0, 'instruction_index': 1}
        plan['abc_font_bindings'] = [site, site]
    elif problem == 'plan_game':
        plan['game_sha256'] = 'e' * 64
    else:
        plan['schema_version'] = 2
    source.write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        migrate(tmp_path, work, source)
    assert not (tmp_path / 'bundle').exists()


def test_nested_symlink_directory_is_rejected(tmp_path):
    work, _, source = fixture(tmp_path)
    manifest = migrate(tmp_path, work, source)
    plans = manifest.parent / 'plans'
    plans.rename(manifest.parent / 'moved-plans')
    plans.symlink_to(manifest.parent / 'moved-plans', target_is_directory=True)
    with pytest.raises(ValueError, match='Symlink'):
        load_bundle(manifest, work)


def test_intentionally_empty_translation_is_still_selected(tmp_path):
    work, _, source = fixture(tmp_path)
    work['entries'][0]['translation'] = ''
    assert load_bundle(migrate(tmp_path, work, source), work)['entry_ids'] == ['title']


def test_boolean_schema_version_is_not_an_integer_version(tmp_path):
    work, _, source = fixture(tmp_path)
    manifest = migrate(tmp_path, work, source)
    data = json.loads(manifest.read_text())
    data['schema_version'] = True
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='schema'):
        load_bundle(manifest, work)
