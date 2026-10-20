"""Cumulative tutorial resource isolation against private offline artifacts."""
import os
from collections import Counter
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from install_patch import ORIGINAL_SHA256, sha
from localization_plan import resolve_plan_font_bindings
from swf_geometry import parse_font, read_swf
from test_battle_hud_local import preserve_method
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory, resolved_operations
from translation_catalog import load_catalog

pytestmark = pytest.mark.local
PLAN = 'translations/plans/pr24-battle-tutorial.json'
BASE_SHA = '6de5d9ecda0c9fb747d10c184b6cf05c8a59c7ac125c5fc55feef6a6bb3bbdcd'


@pytest.fixture(scope='module')
def tutorial_resources():
    if not os.environ.get('GEMCRAFT_PR24_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR24_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR24_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    base = (root / 'baseline/gc-cs-steam.ru.swf').read_bytes()
    assert sha(base) == BASE_SHA
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    assert sha(candidate) == load_catalog('translations/build-manifest.json')['expected_candidate_sha256']
    workspace = load_catalog(root / 'candidate/workspace.json')
    return root, original, base, candidate, workspace


def test_full_plan_preserves_resources_and_abc(tutorial_resources):
    root, original, base, candidate, workspace = tutorial_resources
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
    time_plan = load_catalog('translations/plans/pr24-battle-time-controls.json')
    new_plan['entry_ids'] += time_plan['entry_ids']
    new_plan['abc_font_bindings'] += time_plan['abc_font_bindings']
    changed = {site['method_index'] for site in new_plan['abc_font_bindings']}
    changed |= {entries[key]['location']['method_index'] for key in new_plan['entry_ids']}
    assert changed == {557, 630}
    old_plan = load_catalog(root / 'baseline/plan.json')
    old_entries = {e['id']: e for e in load_catalog(root / 'baseline/workspace.json')['entries']}
    assert len(old_plan['entry_ids']) == 2228
    assert len(merged['entry_ids']) == 2228 + len(new_plan['entry_ids'])
    assert set(old_plan['entry_ids']).isdisjoint(new_plan['entry_ids'])
    assert set(merged['entry_ids']) == set(old_plan['entry_ids']) | set(new_plan['entry_ids'])
    assert all(e == entries[key] for key, e in old_entries.items()
               if key not in new_plan['entry_ids'])
    assert sum(e['translation'] is not None for e in workspace['entries']) == 2302 + len(new_plan['entry_ids'])
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
    # This scope only changes ABC and potentially extends Alegreya's repertoire.
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
