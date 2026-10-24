"""Final cumulative PR33 proof on owned resources; no game launch or installation."""
import copy
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from localization_workspace import validate_workspace
from addressed_localization import _display_text
from swf_geometry import parse_font, read_swf
from test_floater_control_flow_local import assert_actual_map_floater_branch_repair
from test_talisman_local import abc_bytes, abc_inventory, resolved_operations
from translation_catalog import load_catalog
from versioned_catalog import export_catalog, restore_workspace

pytestmark = pytest.mark.local
CHANGED_METHODS = {2769, 630, 519, 520, 521, 522, 523, 539}


@pytest.fixture(scope='module')
def final_resources():
    if not os.environ.get('GEMCRAFT_PR33_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR33_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR33_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    baseline = (root / 'baseline/gc-cs-steam.ru.swf').read_bytes()
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    assert sha(baseline) == '563bfe2b620d25ba018480d626342779de66af8a17d27627a90a77729595005e'
    return root, original, baseline, candidate


def test_independent_disk_candidates_and_reports_match(final_resources):
    root, _, _, candidate = final_resources
    assert candidate == (root / 'independent/gc-cs-steam.ru.swf').read_bytes()
    assert sha(candidate) == load_catalog('translations/build-manifest.json')['expected_candidate_sha256']
    first = load_catalog(root / 'candidate/static-validation.json')
    assert first == load_catalog(root / 'independent/static-validation.json')
    assert first['applied_translations'] == 3007 and first['status'] == 'PARTIAL'
    assert first['achievement_category_labels']['technical_keys_preserved']


def test_previous_translations_manual_meanings_and_plans_survive(final_resources):
    root, _, _, _ = final_resources
    inventory = load_catalog(root / 'candidate/inventory.json')
    workspace = load_catalog(root / 'candidate/workspace.json')
    previous = load_catalog(root / 'catalog-before.json')
    current = load_catalog('translations/catalog.json')
    old = {r['id']: r for r in restore_workspace(previous, inventory)['entries']}
    new = {r['id']: r for r in workspace['entries']}
    selected = {key for name in ('pr33-achievement-filters.json', 'pr33-residual-ui.json')
                for key in load_catalog(Path('translations/plans') / name)['entry_ids']}
    assert len(selected) == 110
    for key in old:
        if key == 'static.3767':
            assert old[key]['translation'] == 'Жалящие удары самоцветных ос:'
            expected = copy.deepcopy(old[key])
            expected['translation'] = 'Удары самоцветных ос:'
            assert new[key] == expected
            continue
        if key not in selected:
            assert old[key] == new[key]
        if old[key]['translation'] is not None:
            assert old[key]['translation'] == new[key]['translation']
    assert not validate_workspace(workspace, inventory)
    assert export_catalog(workspace, inventory) == current
    manifest = load_catalog('translations/build-manifest.json')
    previous_plans = load_catalog(root / 'manifest-before.json')['plans']
    expected_plans = copy.deepcopy(previous_plans)
    outcome = next(row for row in expected_plans if row['path'] == 'plans/pr06-outcome.json')
    assert outcome['sha256'] == '1323d0b151178780d3ce39c326f3c0da3f7aef8ada005153ca28e2f00faf10cb'
    outcome['sha256'] = '501ae6cb5a294984375bebd4c617b47755b0341884d92231a9f3a208045fe503'
    assert manifest['plans'][:-2] == expected_plans
    previous_outcome = load_catalog(root / 'plans/pr06-outcome.json')
    expected_outcome = copy.deepcopy(previous_outcome)
    assert expected_outcome['layouts']['static.3767']['max_width'] == 3600
    expected_outcome['layouts']['static.3767']['max_width'] = 2589
    outcome_path = Path('translations/plans/pr06-outcome.json')
    assert load_catalog(outcome_path) == expected_outcome
    assert sha(outcome_path.read_bytes()) == outcome['sha256']
    assert len(manifest['plans']) == 30
    assert sum(isinstance(r['manual'].get('translation'), str) for r in current['entries']) == 3079
    assert len(set(load_catalog(root / 'candidate/plan.json')['entry_ids'])) == 3007


def test_only_reviewed_methods_change_and_original_keys_and_resources_survive(final_resources):
    root, original, baseline, candidate = final_resources
    tags = [read_swf(data)[1] for data in (original, baseline, candidate)]
    assert len(tags[1]) == len(tags[2])
    changed_static = []
    for left, right in zip(tags[1], tags[2]):
        assert left.code == right.code
        if left.code == 82:
            continue
        if left.code == 11 and int.from_bytes(left.payload[:2], 'little') == 3767:
            assert int.from_bytes(right.payload[:2], 'little') == 3767
            assert left != right
            changed_static.append(right)
        else:
            # This includes every font, alignment zone, other text and sprite.
            assert left == right
    assert len(changed_static) == 1
    original_caption = next(tag for tag in tags[0] if tag.code == 11
                            and int.from_bytes(tag.payload[:2], 'little') == 3767)
    source_fonts = {int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
                    for tag in tags[0] if tag.code == 75}
    font = next(parse_font(tag.payload) for tag in tags[2] if tag.code == 75
                and int.from_bytes(tag.payload[:2], 'little') == 6002)
    entry = next(row for row in load_catalog(root / 'candidate/workspace.json')['entries']
                 if row['id'] == 'static.3767')
    assert entry['translation'] == 'Удары самоцветных ос:'
    layout = load_catalog('translations/plans/pr06-outcome.json')['layouts'][entry['id']]
    expected_caption, _ = _display_text(original_caption, entry, font, layout, source_fonts)
    assert (changed_static[0].code, changed_static[0].payload) == (
        expected_caption.code, expected_caption.payload)
    source, before, after = map(abc_bytes, tags)
    origin, old, new = map(abc_inventory, (source, before, after))
    assert new.strings[:len(origin.strings)] == origin.strings
    assert new.multinames[:len(origin.multinames)] == origin.multinames
    bodies = list(map(_bodies, (source, before, after)))
    assert bodies[0].keys() == bodies[1].keys() == bodies[2].keys()
    for method in bodies[1]:
        if method in CHANGED_METHODS:
            continue
        if method == 341:
            assert_actual_map_floater_branch_repair(source, before, after)
            continue
        left = old.instructions(bodies[1][method][3], method)
        right = new.instructions(bodies[2][method][3], method)
        expected = resolved_operations(old, left)
        actual = resolved_operations(new, right)
        if method == 1732:
            # The runtime fix changes only the injected pattern, leaving the
            # renderer's instructions, fonts, measurements and movement intact.
            escaped = r'[\u0400-\u04FF]'
            literal = '[Ѐ-ӿ]'
            assert sum(op == 0x2c and value == escaped for _, op, value in expected) == 1
            assert sum(op == 0x2c and value == literal for _, op, value in actual) == 1
            expected = [(at, op, literal if op == 0x2c and value == escaped else value)
                        for at, op, value in expected]
        assert expected == actual, method
        assert bodies[1][method][2] == bodies[2][method][2]
        assert bodies[1][method][4:] == bodies[2][method][4:]
    # Category keys/order, achievement tags/filtering, save format and progress,
    # and Steam-API achievement reporting retain their original bytecode.
    for method in (139, 220, 221, 310, 443, 450, 451, 452, 1074, 1078, 1079, 1080, 1082):
        assert bodies[0][method][2:] == bodies[2][method][2:], method
    assert not [row for row in new.unsupported + new.control_flow_anomalies
                if row['method_index'] in CHANGED_METHODS]


def test_hash_guarded_apply_restore_on_temporary_files(final_resources, tmp_path):
    root, original, _, candidate = final_resources
    target = tmp_path / 'game/gc-cs-steam.swf'
    target.parent.mkdir()
    target.write_bytes(original)
    receipt = apply_patch_file(target, root / 'candidate/gc-cs-steam.ru.swf',
                               tmp_path / 'backup', ORIGINAL_SHA256, sha(candidate))
    assert target.read_bytes() == candidate
    restore_patch_file(receipt)
    assert target.read_bytes() == original
