"""Offline PR33 preservation and display checks on private resources."""
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import _string_pool_bounds
from achievement_category_binding import category_entry_ids
from install_patch import ORIGINAL_SHA256, sha
from swf_geometry import read_swf
from test_achievement_categories import execute_selector
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local
BASELINE_SHA = '563bfe2b620d25ba018480d626342779de66af8a17d27627a90a77729595005e'


@pytest.fixture(scope='module')
def category_resources():
    if not os.environ.get('GEMCRAFT_PR33_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR33_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR33_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    baseline_path = Path(os.environ.get('GEMCRAFT_PR33_BASELINE', root / 'baseline/gc-cs-steam.ru.swf'))
    baseline = baseline_path.read_bytes()
    candidate_dir = Path(os.environ.get('GEMCRAFT_PR33_CATEGORY_ONLY', root / 'achievement-candidate'))
    candidate = (candidate_dir / 'gc-cs-steam.ru.swf').read_bytes()
    workspace = load_catalog(root / 'achievement-workspace.json')
    assert sha(original) == ORIGINAL_SHA256
    assert sha(baseline) == BASELINE_SHA
    return original, baseline, candidate, workspace


def test_all_technical_category_keys_consumers_and_existing_translations_are_unchanged(category_resources):
    original, baseline, candidate, _ = category_resources
    old_tags, new_tags = read_swf(baseline)[1], read_swf(candidate)[1]
    assert len(old_tags) == len(new_tags)
    assert [(i, old.code) for i, (old, new) in enumerate(zip(old_tags, new_tags))
            if old != new] == [(4179, 82)]
    source = abc_bytes(read_swf(original)[1])
    old, new = abc_bytes(old_tags), abc_bytes(new_tags)
    old_inv, new_inv = abc_inventory(old), abc_inventory(new)
    old_bodies, new_bodies = _bodies(old), _bodies(new)
    assert old_inv.strings == new_inv.strings[:len(old_inv.strings)]
    assert old_inv.multinames == new_inv.multinames
    _, old_start, old_end = _string_pool_bounds(old)
    _, new_start, _ = _string_pool_bounds(new)
    assert old[old_start:old_end] == new[new_start:new_start + old_end - old_start]
    assert [m for m in old_bodies if old_bodies[m][2:] != new_bodies[m][2:]] == [2769]
    source_bodies = _bodies(source)
    assert all(source_bodies[m][2:] == new_bodies[m][2:] for m in (220, 221, 139, 1074, 1078, 1080, 1082))
    assert new_bodies[2769][2] == [old_bodies[2769][2][0] + 66, 5, *old_bodies[2769][2][2:]]
    assert old_bodies[2769][4:] == new_bodies[2769][4:]


def test_real_selector_produces_all_draft_captions_and_preserves_unknown_statuses(category_resources):
    _, baseline, candidate, workspace = category_resources
    old, new = abc_bytes(read_swf(baseline)[1]), abc_bytes(read_swf(candidate)[1])
    old_code, new_code = _bodies(old)[2769][3], _bodies(new)[2769][3]
    inv = abc_inventory(new)
    ops = inv.instructions(new_code, 2769)
    assignment = next(at for at, op, args in ops if op == 0x61 and args == [5329])
    added = len(new_code) - len(old_code)
    code = new_code[assignment - added:assignment]
    assert new_code[:assignment - added] + new_code[assignment:] == old_code
    entries = {row['id']: row for row in workspace['entries']}
    for identifier, entry_id in enumerate(category_entry_ids(), 6):
        row = entries[entry_id]
        stack, peak, locals_ = execute_selector(code, identifier, row['source'], dict(enumerate(inv.strings)))
        assert stack == ['text field', row['translation']]
        assert peak <= _bodies(new)[2769][2][0]
        assert locals_ == {2: identifier}
    for identifier in (-1, 0, 1, 2, 3, 4, 5, 72, 10000):
        assert execute_selector(code, identifier, 'Original status', dict(enumerate(inv.strings)))[0] == [
            'text field', 'Original status']


def test_original_category_usage_reveals_combine_is_normally_hidden(category_resources):
    original, _, candidate, _ = category_resources
    raw = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(raw)
    operations = inv.instructions(_bodies(raw)[222][3], 222)
    categories, achievements = [], []
    for _, op, args in operations:
        if op == 0x60 and 4550 <= args[0] < 4616:
            categories.append(args[0] - 4550)
        if op == 0x4a and args[0] == 69:
            achievements.append(categories)
            categories = []
    assert len(achievements) == 418
    assert set(range(66)) - {i for tags in achievements for i in tags} == {13}
    assert inv.multiname(4550 + 13) == 'tCombine'
    # The method that generates filterFlags from key membership is unchanged.
    new_raw = abc_bytes(read_swf(candidate)[1])
    # Prior translation operands exist in m222; comparison to the PR32 baseline
    # in the preservation test covers its entire bytecode, including that tail.
    assert _bodies(new_raw)[220][3] == _bodies(raw)[220][3]
