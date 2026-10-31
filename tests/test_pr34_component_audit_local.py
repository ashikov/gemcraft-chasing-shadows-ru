"""Bind the authored 55-component triage to the actual owned original."""
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCInventory
from install_patch import ORIGINAL_SHA256, sha
from inventory_strings import extract_inventory, walk_tags
from swf_geometry import read_swf
from versioned_catalog import restore_workspace

pytestmark = pytest.mark.local


def test_every_residual_component_has_an_original_consumer_and_source_guard():
    source = os.environ.get('GEMCRAFT_ORIGINAL')
    if not source:
        pytest.skip('Set GEMCRAFT_ORIGINAL')
    original = Path(source).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    inventory = extract_inventory(original, ORIGINAL_SHA256)
    workspace = restore_workspace(json.loads(Path('translations/catalog.json').read_text()), inventory)
    expected = {entry['id'] for entry in workspace['entries']
                if entry['classification'] == 'confirmed' and entry['translation'] is None}
    report = json.loads(Path('docs/pr34-component-audit.json').read_text())
    assert report['original_sha256'] == ORIGINAL_SHA256
    rows = report['components']
    assert len(rows) == 55 and {row['id'] for row in rows} == expected
    assert Counter(row['classification'] for row in rows) == report['counts']
    entries = {row['id']: row for row in inventory['entries']}
    tags = read_swf(original)[1]
    by_path = {path: tag for tag, path, _ in walk_tags(tags)}
    tag = tags[4175]
    raw = tag.payload[tag.payload.index(b'\0', 4) + 1:]
    inv = ABCInventory(raw, {'tag_path': [4175], 'tag_code': 82})
    inv.parse()
    bodies = _bodies(raw)
    operations = {method: inv.instructions(body[3], method) for method, body in bodies.items()}
    for row in rows:
        entry = entries[row['id']]
        assert row['source_sha256'] == entry['source_sha256']
        assert row['tag_path'] == entry['location']['tag_path']
        character = entry['location']['character_id']
        for placement in row['placements']:
            assert any(p['tag_path'] == placement['tag_path'] and p['character_id'] == character
                       and p['depth'] == placement['depth'] for p in inventory['placements'])
            assert by_path[tuple(placement['tag_path'])].code in (26, 70)
        for method in row['consumer_methods']:
            assert method in operations
        assert row['classification'] in {'COVERED', 'TECHNICAL', 'UNREACHABLE',
                                         'NEEDS TRANSLATION', 'RUNTIME-UNVERIFIED'}
        assert row['runtime'] == 'NOT TESTED in PR34'
    # The only new prose discovered in these template consumers is the wave
    # prefix. It is now a separate addressed translation, not an XML rewrite.
    marker = next(entry for entry in workspace['entries'] if entry['id'] == 'abc.4175.139.181')
    assert marker['translation'] == 'в.'
    assert marker['classification'] == 'confirmed'
