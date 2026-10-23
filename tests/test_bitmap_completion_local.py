"""PR32 cumulative invariants on owned files; never touches the installation."""
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import _string_pool_bounds
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from localization_project import check_workspace
from localization_workspace import validate_workspace
from swf_geometry import read_swf
from test_bitmap_completion import CALLS, IDS, NUMERIC_CALLS, PROMOTED
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog
from versioned_catalog import catalog_digest, export_catalog, restore_workspace

pytestmark = pytest.mark.local
BASE_SHA = 'e2b0b4dd4e83f18b7c2c608d7789623f604eb310c25561ea6b7703183466b248'


@pytest.fixture(scope='module')
def completion_resources():
    if not os.environ.get('GEMCRAFT_PR32_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR32_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR32_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    baseline = (root / 'baseline/gc-cs-steam.ru.swf').read_bytes()
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    assert sha(original) == ORIGINAL_SHA256 and sha(baseline) == BASE_SHA
    assert sha(candidate) == load_catalog('translations/build-manifest.json')['expected_candidate_sha256']
    return root, original, baseline, candidate


def operations(data, method):
    raw = abc_bytes(read_swf(data)[1])
    inv = abc_inventory(raw)
    return inv, inv.instructions(_bodies(raw)[method][3], method)


def test_previous_manual_semantics_promoted_refunds_and_export_roundtrip(completion_resources):
    root, _, _, _ = completion_resources
    old = load_catalog(root / 'catalog-before.json')
    catalog = load_catalog('translations/catalog.json')
    inventory = load_catalog(root / 'candidate/inventory.json')
    old_rows = {r['id']: r for r in old['entries']}
    new_rows = {r['id']: r for r in catalog['entries']}
    assert set(new_rows) - set(old_rows) == IDS - PROMOTED
    previous = {r['id']: r for r in restore_workspace(old, inventory)['entries']}
    workspace = load_catalog(root / 'candidate/workspace.json')
    current = {r['id']: r for r in workspace['entries']}
    for key in old_rows:
        assert current[key]['context'] == previous[key]['context']
        assert current[key]['notes'] == previous[key]['notes']
        if key not in PROMOTED:
            assert current[key] == previous[key]
        else:
            assert {k: v for k, v in current[key].items() if k != 'translation'} == {
                k: v for k, v in previous[key].items() if k != 'translation'}
    assert current['abc.4175.1144.70']['translation'] == 'Возврат теневых ядер: +'
    assert current['abc.4175.1144.89']['translation'] == ''
    assert not validate_workspace(workspace, inventory)
    assert export_catalog(workspace, inventory) == catalog
    receipt = load_catalog(root / 'candidate/catalog-origin.json')
    assert receipt == {'catalog_sha256': catalog_digest(catalog)}
    assert export_catalog(workspace, inventory, catalog,
                          expected_previous_sha256=receipt['catalog_sha256']) == catalog
    stale = load_catalog(root / 'baseline/catalog-origin.json')['catalog_sha256']
    with pytest.raises(ValueError, match='baseline changed'):
        export_catalog(workspace, inventory, catalog, expected_previous_sha256=stale)
    assert check_workspace(root / 'candidate/workspace.json',
                           root / 'candidate/inventory.json')['synchronized']
    old_plan = load_catalog(root / 'baseline/plan.json')
    plan = load_catalog(root / 'candidate/plan.json')
    assert len(plan['entry_ids']) == 2897
    assert set(plan['entry_ids']) == set(old_plan['entry_ids']) | IDS
    supplemental = {key for key, row in new_rows.items()
                    if isinstance(row['manual'].get('translation'), str)
                    and key not in plan['entry_ids']}
    old_supplemental = {key for key, row in old_rows.items()
                        if isinstance(row['manual'].get('translation'), str)
                        and key not in old_plan['entry_ids']}
    assert supplemental == old_supplemental - PROMOTED
    assert len(supplemental) == 72
    assert sum(isinstance(r['manual'].get('translation'), str) for r in new_rows.values()) == 2969
    old_manifest = load_catalog(root / 'manifest-before.json')
    manifest = load_catalog('translations/build-manifest.json')
    assert len(manifest['plans']) == 28
    assert manifest['plans'][:-1] == old_manifest['plans']
    assert all(sha((Path('translations') / row['path']).read_bytes()) == row['sha256']
               for row in manifest['plans'])


def test_exact_calls_only_addressed_literals_one_swap_and_unchanged_arguments(completion_resources):
    root, original, baseline, candidate = completion_resources
    source = abc_bytes(read_swf(original)[1])
    base = abc_bytes(read_swf(baseline)[1])
    target = abc_bytes(read_swf(candidate)[1])
    origin, before, after = map(abc_inventory, (source, base, target))
    source_bodies, old_bodies, new_bodies = map(_bodies, (source, base, target))
    rows = {row['id']: row for row in load_catalog(root / 'candidate/workspace.json')['entries']}
    seen = set()
    for method, calls in CALLS.items():
        source_ops = origin.instructions(source_bodies[method][3], method)
        old = before.instructions(old_bodies[method][3], method)
        new = after.instructions(new_bodies[method][3], method)
        assert {at for at, op, args in source_ops if op == 0x4f and origin.multiname(args[0]) in (
            'createFloatingText', 'createFloatingTextOutgame')} == set(calls)
        # PR27 already translated m695@1400 and inserted one floater binding
        # before instruction 575. Original source IDs retain original offsets;
        # comparing PR31 -> PR32 must account for that existing insertion.
        prior_bytes = len(old_bodies[method][3]) - len(source_bodies[method][3])
        if method == 695:
            assert prior_bytes > 0
            boundary = source_ops[575][0]
        else:
            assert prior_bytes == 0
            boundary = len(source_bodies[method][3])
        def baseline_offset(offset):
            return offset + (prior_bytes if offset >= boundary else 0)
        addressed = {}
        for at, op, args in source_ops:
            key = f'abc.4175.{method}.{at}'
            if key in IDS:
                assert op == 0x2c
                assert rows[key]['source'] == origin.strings[args[0]]
                assert rows[key]['source_sha256'] == sha(origin.strings[args[0]].encode())
                addressed[baseline_offset(at)] = key
        assert {at for at, op, args in old if op == 0x4f and before.multiname(args[0]) in (
            'createFloatingText', 'createFloatingTextOutgame')} == {baseline_offset(at) for at in calls}
        assert old_bodies[method][2] == new_bodies[method][2]
        assert old_bodies[method][4:] == new_bodies[method][4:]
        swap = method == 243
        if swap:
            assert new[117] == (282, 0x2b, [])
            assert len(new) == len(old) + 1
            del new[117]
        assert len(old) == len(new)
        old_index = {at: i for i, (at, _, _) in enumerate(old)}
        new_index = {at: i for i, (at, _, _) in enumerate(new)}
        if swap:
            new_index[282] = 117  # a branch landing at the inserted swap still reaches add
        for i, ((at, op, args), (new_at, new_op, new_args)) in enumerate(zip(old, new)):
            assert op == new_op
            key = addressed.get(at)
            if op == 0x2c:
                old_text, text = before.strings[args[0]], after.strings[new_args[0]]
                if key is not None:
                    assert text == rows[key]['translation']
                    seen.add(key)
                else:
                    assert text == old_text  # alignments, sounds, symbols and data keys
            elif 0x0c <= op <= 0x1a:
                assert old_index[old[i + 1][0] + args[0]] == new_index[new[i + 1][0] + new_args[0]]
            elif op == 0x1b:
                assert args[1] == new_args[1]
                assert [old_index[at + d] for d in (args[0], *args[2:])] == [
                    new_index[new_at + d] for d in (new_args[0], *new_args[2:])]
            else:
                assert args == new_args  # all game calculations and every call argument
        assert not [row for row in after.unsupported + after.control_flow_anomalies
                    if row['method_index'] == method]
    assert seen == IDS
    # There are no technical origins among these 48 fragments. A string shared
    # with a symbol is never rewritten in the pool or at an unselected use.
    inventory = {r['id']: r for r in load_catalog(root / 'candidate/inventory.json')['entries']}
    assert all(inventory[key]['classification'] != 'technical' for key in IDS)


def test_all_other_methods_font_renderer_pools_and_non_abc_tags_preserved(completion_resources):
    root, original, baseline, candidate = completion_resources
    tags = [read_swf(data)[1] for data in (original, baseline, candidate)]
    source, base, target = map(abc_bytes, tags)
    original_inv, before, after = map(abc_inventory, (source, base, target))
    assert method_metadata(source) == method_metadata(target)
    assert after.strings[:len(original_inv.strings)] == original_inv.strings
    assert after.multinames[:len(original_inv.multinames)] == original_inv.multinames
    assert source[:_string_pool_bounds(source)[0]] == target[:_string_pool_bounds(target)[0]]
    assert len(before.multinames) == len(after.multinames)
    for index, item in enumerate(before.multinames):
        if item:
            assert before.multiname(index) == after.multiname(index)
            assert {k: v for k, v in item.items() if k != 'name'} == {
                k: v for k, v in after.multinames[index].items() if k != 'name'}
    assert [(t.code, t.payload) for t in tags[1] if t.code != 82] == [
        (t.code, t.payload) for t in tags[2] if t.code != 82]
    original_bodies, old_bodies, new_bodies = map(_bodies, (source, base, target))
    assert original_bodies.keys() == old_bodies.keys() == new_bodies.keys()
    changed = set()
    byte_identical = 0
    for method, body in old_bodies.items():
        if method in CALLS:
            continue
        assert body[2] == new_bodies[method][2] and body[4:] == new_bodies[method][4:]
        old = before.instructions(body[3], method)
        new = after.instructions(new_bodies[method][3], method)
        assert len(old) == len(new)
        assert [(at, op, before.strings[a[0]] if op == 0x2c else a) for at, op, a in old] == [
            (at, op, after.strings[a[0]] if op == 0x2c else a) for at, op, a in new]
        if original_bodies[method][2:] == body[2:]:
            assert original_bodies[method][2:] == new_bodies[method][2:]
            byte_identical += 1
    for method in CALLS:
        changed.add(method)
    assert len(changed) == 17
    # The shared bitmap constructor and original numeric consumers do not change.
    # The constructor was compared semantically above. Its appended string
    # indices may move as new translations enter the pool; its code stays the
    # same after resolving those indices, with unchanged limits and handlers.
    for method, calls in NUMERIC_CALLS.items():
        original_ops = original_inv.instructions(original_bodies[method][3], method)
        target_ops = after.instructions(new_bodies[method][3], method)
        sites = [[i for i, (_, op, a) in enumerate(ops)
                  if op == 0x4f and inv.multiname(a[0]) == 'createFloatingTextOutgame']
                 for inv, ops in ((original_inv, original_ops), (after, target_ops))]
        assert {original_ops[i][0] for i in sites[0]} == calls
        assert len(sites[0]) == len(sites[1])
        for old_call, new_call in zip(*sites):
            spans = []
            for inv, ops, call in ((original_inv, original_ops, old_call),
                                   (after, target_ops, new_call)):
                first = max(i for i in range(call) if ops[i][1:] == (0x66, [4156])) - 1
                spans.append([(op, inv.strings[a[0]] if op == 0x2c else a)
                              for _, op, a in ops[first:call + 1]])
            # Other UI text in these methods has prior font bindings; the
            # complete numeric notification expressions and all args survive.
            assert spans[0] == spans[1]
    assert byte_identical > 3400
    print(f'{byte_identical} untouched methods match original bytes; 17 methods addressed')


def test_two_independent_builds_disk_validation_and_hash_guarded_copy_restore(completion_resources, tmp_path):
    root, original, baseline, candidate = completion_resources
    assert (root / 'independent/gc-cs-steam.ru.swf').read_bytes() == candidate
    report = load_catalog(root / 'candidate/static-validation.json')
    assert report == load_catalog(root / 'candidate/on-disk-validation.json')
    assert report == load_catalog(root / 'independent/static-validation.json')
    assert report == load_catalog(root / 'independent/on-disk-validation.json')
    assert report['applied_translations'] == 2897 and report['patched_sha256'] == sha(candidate)
    old_report = load_catalog(root / 'baseline/static-validation.json')
    assert old_report == load_catalog(root / 'baseline/on-disk-validation.json')
    assert old_report['patched_sha256'] == BASE_SHA
    assert all(report['layout_checks'][key] == value for key, value in old_report['layout_checks'].items())
    game = tmp_path / 'game/gc-cs-steam.swf'
    game.parent.mkdir()
    game.write_bytes(original)
    for directory, expected in (('candidate', candidate), ('baseline', baseline)):
        receipt = apply_patch_file(game, root / directory / 'gc-cs-steam.ru.swf',
                                   tmp_path / 'backup', ORIGINAL_SHA256, sha(expected))
        assert game.read_bytes() == expected
        backup = Path(load_catalog(receipt)['backup_path'])
        assert backup.read_bytes() == original
        with pytest.raises(ValueError):
            apply_patch_file(game, root / directory / 'gc-cs-steam.ru.swf', tmp_path / 'backup',
                             ORIGINAL_SHA256, sha(expected))
        restore_patch_file(receipt)
        assert game.read_bytes() == backup.read_bytes() == original
    assert Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes() == original
