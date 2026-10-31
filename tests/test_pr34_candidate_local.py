"""Current cumulative disk candidate, source preservation and isolated rollback."""
import json
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCInventory
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from localization_project import check_workspace
from localization_workspace import validate_workspace
from swf_geometry import read_swf
from translation_catalog import load_catalog
from versioned_catalog import export_catalog, restore_workspace

pytestmark = pytest.mark.local
CHANGED_METHODS = {139, 242, 334, 465, 1441, 1537, 3442, 3490}


def abc(tags):
    tag = next(tag for tag in tags if tag.code == 82)
    return tag.payload[tag.payload.index(b'\0', 4) + 1:]


def inventory(raw):
    inv = ABCInventory(raw, {'tag_path': [4175], 'tag_code': 82})
    inv.parse()
    return inv


def semantic_operations(inv, body, method):
    result = []
    named = {0x04, 0x05, 0x59, 0x5D, 0x5E, 0x5F, 0x60, 0x61, 0x66, 0x68,
             0x6A, 0x80, 0x86, 0xB2, 0x45, 0x46, 0x4A, 0x4C, 0x4E, 0x4F}
    for at, opcode, args in inv.instructions(body[3], method):
        if opcode == 0x2c:
            args = [inv.strings[args[0]]]
        elif opcode in named:
            args = [inv.multiname(args[0]), *args[1:]]
        result.append((at, opcode, args))
    return result


@pytest.fixture(scope='module')
def artifacts():
    root = Path(os.environ.get('GEMCRAFT_PR34_ROOT', 'local/pr34'))
    original_path = os.environ.get('GEMCRAFT_ORIGINAL')
    if not original_path or not (root / 'independent/gc-cs-steam.ru.swf').exists():
        pytest.skip('Set GEMCRAFT_ORIGINAL and provide both fresh PR34 builds')
    original = Path(original_path).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    return root, original, candidate


def test_two_fresh_disk_builds_reports_and_canonical_roundtrip(artifacts):
    root, original, candidate = artifacts
    assert candidate == (root / 'independent/gc-cs-steam.ru.swf').read_bytes()
    manifest = load_catalog('translations/build-manifest.json')
    assert sha(candidate) == manifest['expected_candidate_sha256']
    report = load_catalog(root / 'candidate/static-validation.json')
    assert report == load_catalog(root / 'independent/static-validation.json')
    assert report['patched_sha256'] == sha(candidate)
    assert report['applied_translations'] == 3029
    assert report['journey_map']['label_regions'] == 10
    assert report['journey_map']['text_lines'] == 12
    assert not report['runtime_verified']
    assert report['original_sha256'] == sha(original)
    for name in ('candidate', 'independent'):
        work = load_catalog(root / name / 'workspace.json')
        inv = load_catalog(root / name / 'inventory.json')
        assert not validate_workspace(work, inv)
        assert check_workspace(root / name / 'workspace.json', root / name / 'inventory.json')['synchronized']
        assert export_catalog(work, inv) == load_catalog('translations/catalog.json')
        assert load_catalog(root / name / 'disk-reread.json')['reread_equal']


def test_all_previous_plans_and_manual_data_survive_and_old_receipt_is_rejected(artifacts):
    root, _, _ = artifacts
    current = load_catalog('translations/catalog.json')
    previous = load_catalog(root / 'catalog-before.json')
    inv = load_catalog(root / 'candidate/inventory.json')
    old = {r['id']: r for r in restore_workspace(previous, inv)['entries']}
    work = load_catalog(root / 'candidate/workspace.json')
    new = {r['id']: r for r in work['entries']}
    changed = set(load_catalog('translations/plans/pr34-small-ui.json')['entry_ids'])
    changed.add('abc.4175.1441.39')
    for key in old:
        if key not in changed:
            assert old[key] == new[key], key
    before = load_catalog(root / 'manifest-before.json')
    after = load_catalog('translations/build-manifest.json')
    assert after['plans'][:30] == before['plans']
    assert len(after['plans']) == 33
    assert sum(isinstance(r['manual'].get('translation'), str) for r in current['entries']) == 3093
    receipt = load_catalog(root / 'integration-origin/catalog-origin.json')
    with pytest.raises(ValueError, match='baseline changed'):
        export_catalog(work, inv, previous=current,
                       expected_previous_sha256=receipt['catalog_sha256'])


def test_unrelated_resources_methods_and_technical_pool_are_preserved(artifacts):
    root, original, candidate = artifacts
    baseline = (root / 'baseline.swf').read_bytes()
    source_tags, old_tags, new_tags = [read_swf(data)[1] for data in (original, baseline, candidate)]
    assert len(old_tags) == len(new_tags)
    for old, new in zip(old_tags, new_tags):
        assert old.code == new.code
        if old.code == 82:
            continue
        identifier = int.from_bytes(old.payload[:2], 'little')
        if old.code == 6 and identifier == 678:
            assert old != new
        elif old.code == 11 and identifier == 3431:
            assert old != new
        else:
            assert old == new, (old.code, identifier)
    raw = list(map(abc, (source_tags, old_tags, new_tags)))
    source, before, after = list(map(inventory, raw))
    assert after.strings[:len(source.strings)] == source.strings
    assert after.multinames[:len(source.multinames)] == source.multinames
    bodies = list(map(_bodies, raw))
    assert bodies[0].keys() == bodies[1].keys() == bodies[2].keys()
    changed = set()
    for method in bodies[1]:
        left = semantic_operations(before, bodies[1][method], method)
        right = semantic_operations(after, bodies[2][method], method)
        if method in CHANGED_METHODS:
            assert left != right
            changed.add(method)
            continue
        assert left == right, method
        assert bodies[1][method][2] == bodies[2][method][2]
        assert bodies[1][method][4:] == bodies[2][method][4:]
    assert changed == CHANGED_METHODS
    # Advice decoding/selection/settings and the map shapes/sprites remain
    # byte-identical; only their single literal/bitmap payloads were replaced.
    assert bodies[1][1442][2:] == bodies[2][1442][2:]
    assert bodies[1][1284][2:] == bodies[2][1284][2:]


def test_hash_guarded_apply_restore_changes_only_a_temporary_copy(artifacts, tmp_path):
    root, original, candidate = artifacts
    game = tmp_path / 'game' / 'gc-cs-steam.swf'
    game.parent.mkdir()
    game.write_bytes(original)
    path = root / 'candidate/gc-cs-steam.ru.swf'
    receipt = apply_patch_file(game, path, tmp_path / 'backup', ORIGINAL_SHA256, sha(candidate))
    assert game.read_bytes() == candidate
    restore_patch_file(receipt)
    assert game.read_bytes() == original
    (tmp_path / 'receipt.json').write_text(json.dumps({
        'original_sha256': sha(original), 'candidate_sha256': sha(candidate),
        'temporary_apply_restore': True}))
