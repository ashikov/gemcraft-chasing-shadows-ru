"""Private offline loot bitmap contracts; no game launch or installed-file writes."""
import os
from pathlib import Path
import struct

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCReader
from abc_patch import _string_pool_bounds
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from swf_geometry import parse_font, read_swf
from test_bitmap_font_binding import execute
from test_bitmap_layout_local import bitmap_envelope, outline_font
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory, resolved_operations
from translation_catalog import load_catalog
from versioned_catalog import restore_workspace

pytestmark = pytest.mark.local
BASE_SHA = 'df17091c83e959984d3694e112d9127a48457da53b2fbb0f3b89a5c0ee84c4c3'
OFFSETS = {204, 216, 336, 349, 438, 519, 622, 635, 648, 718,
           876, 959, 1074, 1125, 1208, 1291, 1374, 1457}
IDS = {f'abc.4175.623.{at}' for at in OFFSETS}
CALLS = {243, 376, 464, 545, 684, 744, 902, 985, 1100, 1151, 1234, 1317, 1400, 1483}


@pytest.fixture(scope='module')
def loot_resources():
    if not os.environ.get('GEMCRAFT_PR31_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR31_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR31_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    baseline = (root / 'baseline/gc-cs-steam.ru.swf').read_bytes()
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    assert sha(baseline) == BASE_SHA
    assert sha(candidate) == load_catalog('translations/build-manifest.json')['expected_candidate_sha256']
    return root, original, baseline, candidate


def test_loot_preserves_previous_catalog_plans_and_unrelated_resources(loot_resources):
    root, original, baseline, candidate = loot_resources
    old_rows = {row['id']: row for row in load_catalog(root / 'catalog-before.json')['entries']}
    rows = {row['id']: row for row in load_catalog('translations/catalog.json')['entries']}
    assert all(rows[key]['manual'].get('translation') == value['manual'].get('translation')
               for key, value in old_rows.items())
    inventory = load_catalog(root / 'candidate/inventory.json')
    old_workspace = restore_workspace(load_catalog(root / 'catalog-before.json'), inventory)
    restored = {row['id']: row for row in old_workspace['entries']}
    workspace = {row['id']: row for row in load_catalog(root / 'candidate/workspace.json')['entries']}
    # Source-reference encoding may change after newly confirmed UI fragments
    # enter the inventory. Compare fully restored manual semantics instead.
    assert all(workspace[key] == restored[key] for key in old_rows)
    assert set(rows) - set(old_rows) == IDS
    assert sum(isinstance(row['manual'].get('translation'), str) for row in rows.values()) == 2923
    manifest = load_catalog('translations/build-manifest.json')
    old_manifest = load_catalog(root / 'manifest-before.json')
    assert manifest['plans'][:-1] == old_manifest['plans']
    assert len(manifest['plans']) == 27
    assert manifest['plans'][-1]['path'] == 'plans/pr31-bitmap-loot.json'
    assert all(sha((Path('translations') / row['path']).read_bytes()) == row['sha256']
               for row in manifest['plans'])
    old_plan = load_catalog(root / 'baseline/plan.json')
    plan = load_catalog(root / 'candidate/plan.json')
    assert len(old_plan['entry_ids']) == 2831 and len(plan['entry_ids']) == 2849
    assert set(plan['entry_ids']) == set(old_plan['entry_ids']) | IDS
    supplemental = {key for key, row in old_rows.items()
                    if isinstance(row['manual'].get('translation'), str)
                    and key not in old_plan['entry_ids']}
    assert len(supplemental) == 74 and supplemental.isdisjoint(plan['entry_ids'])
    tags = [read_swf(data)[1] for data in (original, baseline, candidate)]
    original_abc, base_abc, new_abc = map(abc_bytes, tags)
    orig, base, new = map(abc_inventory, (original_abc, base_abc, new_abc))
    assert method_metadata(original_abc) == method_metadata(new_abc)
    assert new.strings[:len(orig.strings)] == orig.strings
    assert new.multinames[:len(orig.multinames)] == orig.multinames
    assert len(new.multinames) == len(base.multinames)
    for index in range(len(orig.multinames), len(new.multinames)):
        assert {k: v for k, v in new.multinames[index].items() if k != 'name'} == {
            k: v for k, v in base.multinames[index].items() if k != 'name'}
        assert new.multiname(index) == base.multiname(index)
    assert original_abc[:_string_pool_bounds(original_abc)[0]] == (
        new_abc[:_string_pool_bounds(new_abc)[0]])
    # Includes every font payload, static field, alignment zone and sprite.
    assert [(tag.code, tag.payload) for tag in tags[1] if tag.code != 82] == [
        (tag.code, tag.payload) for tag in tags[2] if tag.code != 82]
    original_bodies = _bodies(original_abc)
    old_bodies, new_bodies = _bodies(base_abc), _bodies(new_abc)
    assert original_bodies.keys() == old_bodies.keys() == new_bodies.keys()
    previous_changes = {method for method in original_bodies
                        if original_bodies[method][2:] != old_bodies[method][2:]}
    assert 623 not in previous_changes
    for method in original_bodies.keys() - previous_changes - {623}:
        assert original_bodies[method][2:] == new_bodies[method][2:]
    for method, body in old_bodies.items():
        assert body[2] == new_bodies[method][2]
        assert body[4:] == new_bodies[method][4:]
        if method != 623:
            # Added strings can renumber old translations in the appended pool.
            assert resolved_operations(base, base.instructions(body[3], method)) == (
                resolved_operations(new, new.instructions(new_bodies[method][3], method)))
    assert not [row for row in new.unsupported if row['method_index'] == 623]
    assert not [row for row in new.control_flow_anomalies if row['method_index'] == 623]


def loot_operations(data):
    raw = abc_bytes(read_swf(data)[1])
    inv = abc_inventory(raw)
    return inv, inv.instructions(_bodies(raw)[623][3], 623)


def test_loot_changes_only_selected_literals_and_preserves_all_parameters(loot_resources):
    root, original, _, candidate = loot_resources
    before, old_ops = loot_operations(original)
    after, new_ops = loot_operations(candidate)
    assert {at for at, op, args in old_ops
            if op == 0x4f and before.multiname(args[0]) == 'createFloatingText'} == CALLS
    rows = {row['id']: row for row in load_catalog(root / 'candidate/workspace.json')['entries']}
    # The conservative 1..104 core-count suite includes all values under the
    # producer's five redistribution passes; it does not claim each is reachable.
    producer = {at: (op, args) for at, op, args in before.instructions(
        _bodies(before.reader.data)[135][3], 135)}
    assert producer[294] == (0x24, [5])
    assert producer[296] == (0x15, [-118])
    assert len(old_ops) == len(new_ops)
    old_indices = {at: index for index, (at, _, _) in enumerate(old_ops)}
    new_indices = {at: index for index, (at, _, _) in enumerate(new_ops)}
    changed = set()
    for index, ((at, op, args), (new_at, new_op, new_args)) in enumerate(zip(old_ops, new_ops)):
        assert op == new_op
        if op == 0x2c:
            old_value, new_value = before.strings[args[0]], after.strings[new_args[0]]
            if at == 656:
                assert new_value == ''
                changed.add(at)
            elif at in OFFSETS:
                assert new_value == rows[f'abc.4175.623.{at}']['translation']
                changed.add(at)
            else:
                assert old_value == new_value  # sounds, alignments, data keys
        elif 0x0c <= op <= 0x1a:
            old_end, new_end = old_ops[index + 1][0], new_ops[index + 1][0]
            assert old_indices[old_end + args[0]] == new_indices[new_end + new_args[0]]
        elif op == 0x1b:
            assert args[1] == new_args[1]
            assert [old_indices[at + delta] for delta in (args[0], *args[2:])] == [
                new_indices[new_at + delta] for delta in (new_args[0], *new_args[2:])]
        else:
            assert args == new_args
    assert changed == OFFSETS | {656}
    # Every call retains size 12, center alignment and identical motion/fade args.
    for at in CALLS:
        index = old_indices[at]
        suffix = old_ops[index - 10:index]
        assert suffix[1][1:] == (0x24, [12])
        assert suffix[2][1] == 0x2c and before.strings[suffix[2][2][0]] == 'center'
        assert [row[1:] for row in suffix[3:]] == [
            (0x24, [0]), (0x2f, [190]), (0x24, [0]), (0x2f, [11]),
            (0x24, [18]), (0x24, [0]), (0x24, [10])]


def phrase_expression(inv, ops, first, last, number, formatted):
    """Execute the bounded real concat/conditional expression, not a mock template."""
    stack = []
    position = first
    by_offset = {at: index for index, (at, _, _) in enumerate(ops)}
    while position < last:
        _, opcode, args = ops[position]
        position += 1
        if opcode == 0x2c:
            stack.append(inv.strings[args[0]])
        elif opcode == 0x60:
            assert inv.multiname(args[0]) == 'NumberFormatter'
            stack.append('formatter')
        elif opcode in (0xd3, 0x62):
            assert opcode == 0xd3 or args[0] in (4, 6)
            stack.append(number)
        elif opcode == 0x46:
            assert inv.multiname(args[0]) == 'format' and args[1] == 1
            assert stack.pop() == number and stack.pop() == 'formatter'
            stack.append(formatted)
        elif opcode == 0xa0:
            right, left = stack.pop(), stack.pop()
            stack.append(left + right)
        elif opcode == 0x24:
            stack.append(args[0])
        elif opcode == 0xaf:
            right, left = stack.pop(), stack.pop()
            stack.append(left > right)
        elif opcode == 0x12:
            if not stack.pop():
                position = by_offset[ops[position][0] + args[0]]
        elif opcode == 0x10:
            position = by_offset[ops[position][0] + args[0]]
        else:
            assert opcode == 0x82  # coerce_a leaves the selected string intact
    assert len(stack) == 1
    return stack[0]


def test_loot_full_runtime_concatenations_and_reused_selector(loot_resources):
    _, original, _, candidate = loot_resources
    before, old_ops = loot_operations(original)
    after, ops = loot_operations(candidate)
    indices = {at: index for index, (at, _, _) in enumerate(old_ops)}
    original_code = _bodies(before.reader.data)[1732][3]
    candidate_code = _bodies(after.reader.data)[1732][3]
    selector = candidate_code[214:214 + len(candidate_code) - len(original_code)]
    assert candidate_code == original_code[:214] + selector + original_code[214:]
    cases = [(number, str(number)) for number in range(1, 105)]
    cases += [(0, '0'), (.5, '1'), (1.5, '2'), (111, '111'),
              (1000, '1,000'), (4294967295, '4,294,967,295'),
              (999999999999, '999,999,999,999')]
    cases += [(number, formatted) for number, short, long in (
        (1e12, '1.000 e12', '1.0000000 e12'),
        (1.23456789e12, '1.235 e12', '1.2345679 e12'),
        (1e13, '1.000 e13', '1.0000000 e13'),
        (1e308, '1.000 e308', '1.0000000 e308'))
        for formatted in (short, long)]
    for number, formatted in cases:
        for first, last, label in ((204, 220, 'Опыт'), (336, 353, 'Мана'),
                                   (622, 661, 'Теневые ядра')):
            phrase = phrase_expression(after, ops, indices[first], indices[last], number, formatted)
            assert phrase == f'{label}: +{formatted}'
            assert execute(selector, after, phrase)[-1] == 'GCCS Alegreya'
            assert execute(selector, after, 'Latin after Russian')[-1] == 'Original decorative family'
            assert execute(selector, after, '123.5 +10%')[-1] == 'Original decorative family'
    for at in OFFSETS - {204, 216, 336, 349, 622, 635, 648, 656}:
        value = after.strings[ops[indices[at]][2][0]]
        assert value and execute(selector, after, value)[-1] == 'GCCS Alegreya'
    constructor = after.instructions(_bodies(after.reader.data)[1732][3], 1732)
    named = [(at, after.multiname(args[0])) for at, op, args in constructor
             if op in (0x61, 0x66, 0x46, 0x4f, 0x4a)]
    at = {name: next(offset for offset, value in named if value == name)
          for name in ('test', 'defaultTextFormat', 'width', 'height', 'BitmapData', 'draw')}
    text_write = next(offset for offset, name in named
                      if name == 'text' and offset > at['defaultTextFormat'])
    assert at['test'] < at['defaultTextFormat'] < text_write < at['width']
    assert at['width'] < at['height'] < at['BitmapData'] < at['draw']


def test_loot_full_phrase_outlines_scales_edges_and_overlap_risks(loot_resources):
    root, original, _, candidate = loot_resources
    before, _ = loot_operations(original)
    reader = ABCReader(before.reader.data)
    reader.take(4)
    for _ in range(2):
        for _ in range(1, reader.count()):
            reader.encoded(32)
    doubles = [None] + [struct.unpack('<d', reader.take(8))[0] for _ in range(1, reader.count())]
    assert doubles[190] == -1.4 and doubles[11] == 0.1
    assert doubles[25] == .25
    fonts = {int.from_bytes(tag.payload[:2], 'little'): outline_font(parse_font(tag.payload))
             for tag in read_swf(candidate)[1] if tag.code == 75}
    rows = {row['id']: row['translation']
            for row in load_catalog(root / 'candidate/workspace.json')['entries']}
    phrases = {rows[f'abc.4175.623.{at}'] for at in OFFSETS
               if at not in (204, 216, 336, 349, 622, 635, 648, 656)}
    counts = [str(number) for number in range(1, 105)]
    counts += ['0', '111', '4,294,967,295', '999,999,999,999',
               '1.000 e12', '1.0000000 e12', '1.235 e12', '1.2345679 e12',
               '1.000 e13', '1.0000000 e13', '1.000 e308', '1.0000000 e308']
    phrases.update(f'{label}: +{number}' for label in ('Опыт', 'Мана', 'Теневые ядра')
                   for number in counts)
    edge_risks = []
    max_width = 0
    for phrase in sorted(phrases):
        for kerning in (False, True):
            measured = bitmap_envelope(fonts[6002], phrase, 12, 544, 306, kerning=kerning)
            max_width = max(max_width, measured['width'])
            for x in (0, 10, 544, 1078, 1088):
                left = max(10, min(1078 - measured['width'], x - measured['width'] / 2))
                dx = left - measured['bitmap'][0]
                for y in (0, 10, 53, 306, 597, 612):
                    # Translation/clamp does not remeasure or reshape the glyphs.
                    box = {'ink': [measured['ink'][0] + dx, measured['ink'][1] + dx,
                                   measured['ink'][2] + y - 306, measured['ink'][3] + y - 306]}
                    for scale in (1, .75, .5):
                        assert 0 <= box['ink'][0] * scale < box['ink'][1] * scale <= 1088 * scale
                        # 18 pre-fade frames plus four 0.25-alpha fade frames;
                        # only the first ten frames move (stopMovingAtTime=10).
                        for tick in range(22):
                            moving = min(tick, 10)
                            dy = moving * -1.4 + .1 * moving * (moving - 1) / 2
                            low, high = box['ink'][2] + dy, box['ink'][3] + dy
                            if low < 0 or high > 612:
                                edge_risks.append((phrase, y, tick, scale))
    assert edge_risks  # no vertical clamp exists in the original renderer
    # Same endpoint and identical delay/motion make simultaneous notifications
    # overlap. The test records this inherited condition rather than inventing
    # a collision manager or declaring arbitrary placements safe.
    one = bitmap_envelope(fonts[6002], rows['abc.4175.623.1291'], 12, 544, 306, kerning=False)
    two = bitmap_envelope(fonts[6002], rows['abc.4175.623.1374'], 12, 544, 306, kerning=False)
    assert max(one['ink'][0], two['ink'][0]) < min(one['ink'][1], two['ink'][1])
    assert max(one['ink'][2], two['ink'][2]) < min(one['ink'][3], two['ink'][3])
    assert max_width < 1068


def test_loot_independent_build_disk_validation_and_temporary_apply_restore(loot_resources, tmp_path):
    root, original, baseline, candidate = loot_resources
    assert (root / 'independent/gc-cs-steam.ru.swf').read_bytes() == candidate
    report = load_catalog(root / 'candidate/static-validation.json')
    assert report == load_catalog(root / 'candidate/on-disk-validation.json')
    assert report == load_catalog(root / 'independent/static-validation.json')
    assert report['applied_translations'] == 2849
    assert report['patched_sha256'] == sha(candidate)
    old_report = load_catalog(root / 'baseline/static-validation.json')
    assert all(report['layout_checks'][key] == value for key, value in old_report['layout_checks'].items())
    game = tmp_path / 'game/gc-cs-steam.swf'
    game.parent.mkdir()
    game.write_bytes(original)
    for directory, expected in (('candidate', candidate), ('baseline', baseline)):
        manifest = apply_patch_file(game, root / directory / 'gc-cs-steam.ru.swf',
                                     tmp_path / 'backup', ORIGINAL_SHA256, sha(expected))
        assert game.read_bytes() == expected
        backup = Path(load_catalog(manifest)['backup_path'])
        assert backup.read_bytes() == original
        with pytest.raises(ValueError):
            apply_patch_file(game, root / directory / 'gc-cs-steam.ru.swf', tmp_path / 'backup',
                             ORIGINAL_SHA256, sha(expected))
        restore_patch_file(manifest)
        assert game.read_bytes() == original and backup.read_bytes() == original
    assert Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes() == original
