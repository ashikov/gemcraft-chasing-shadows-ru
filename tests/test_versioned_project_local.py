"""Opt-in real restoration/build using only a Git checkout, original SWF and TTF.

Run this file from a fresh clone to prove independence from prior ignored files.
No application is launched and apply/restore targets only pytest temporary files.
"""
import json
import os
from pathlib import Path

import pytest

from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from localization_bundle import load_bundle
from localization_project import build_project, check_workspace
from localization_workspace import validate_workspace
from translation_catalog import load_catalog
from versioned_catalog import catalog_digest, export_catalog

pytestmark = pytest.mark.local
EXPECTED = 'a4b77c6e63f525bd24351841d7aabb6b906c20abb1980ec00c76c3dec2626ff2'


def test_restore_build_and_temporary_apply_restore(tmp_path):
    original_path = os.environ.get('GEMCRAFT_ORIGINAL')
    font_path = os.environ.get('GEMCRAFT_ALEGREYA_FONT')
    if not original_path or not font_path:
        pytest.skip('Set GEMCRAFT_ORIGINAL and GEMCRAFT_ALEGREYA_FONT')
    source, font = Path(original_path), Path(font_path)
    original = source.read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    root = Path(__file__).resolve().parents[1]
    catalog = root / 'translations/catalog.json'
    manifest = root / 'translations/build-manifest.json'
    output = tmp_path / 'restored-build'
    report = build_project(source, font, output, catalog, manifest)
    assert report['patched_sha256'] == EXPECTED
    assert report['applied_translations'] == 2262
    assert report['status'] == 'PARTIAL'
    workspace = load_catalog(output / 'workspace.json')
    inventory = load_catalog(output / 'inventory.json')
    assert not validate_workspace(workspace, inventory)
    assert sum(entry['translation'] is not None for entry in workspace['entries']) == 2336
    assert export_catalog(workspace, inventory) == load_catalog(catalog)
    assert load_catalog(output / 'catalog-origin.json') == {
        'catalog_sha256': catalog_digest(load_catalog(catalog))}
    assert check_workspace(output / 'workspace.json', output / 'inventory.json',
                           catalog, manifest)['synchronized']
    plan = load_bundle(manifest, workspace)
    assert len(plan['entry_ids']) == 2262
    assert not set(plan['empty_abc_strings']) & set(plan['entry_ids'])
    assert load_catalog(output / 'on-disk-validation.json') == report
    candidate = output / 'gc-cs-steam.ru.swf'
    assert sha(candidate.read_bytes()) == EXPECTED
    # Existing hash guards, backups, atomic replacement and restoration are real.
    game = tmp_path / 'isolated-game' / 'gc-cs-steam.swf'
    game.parent.mkdir()
    game.write_bytes(original)
    backup = tmp_path / 'isolated-backup'
    receipt = apply_patch_file(game, candidate, backup, ORIGINAL_SHA256, EXPECTED)
    assert game.read_bytes() == candidate.read_bytes()
    restore_patch_file(receipt)
    assert game.read_bytes() == original
    assert source.read_bytes() == original
    evidence = {
        'original_sha256': sha(original), 'font_sha256': sha(font.read_bytes()),
        'candidate_sha256': EXPECTED, 'applied_translations': 2262,
        'prepared_translations': 2336, 'on_disk_validation': True,
        'temporary_apply_restore': True,
        'checkout': str(root), 'output': str(output),
    }
    (tmp_path / 'integration-receipt.json').write_text(
        json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(evidence), flush=True)
