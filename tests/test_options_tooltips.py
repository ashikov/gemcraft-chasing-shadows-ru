"""Settings font/measurement regression; private checks never execute Flash/AIR."""
import json
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import _u30
from install_patch import ORIGINAL_SHA256, sha
from localization_fonts import measure_text
from swf_geometry import parse_font, read_swf
from test_alegreya_localization import _wrap_lines
from test_talisman_local import abc_bytes, abc_inventory

CALLS = (
    39, 60, 77, 89, 98, 110, 119, 131, 143, 152, 164, 173, 185, 194, 206,
    215, 227, 236, 248, 257, 269, 278, 290, 299, 311, 320, 332, 341, 353,
    362, 391, 403, 415, 424, 441, 458, 475, 492, 509, 530, 539, 556, 573,
    585, 597, 609, 621, 638, 657, 666, 675, 687, 696, 708, 717, 729, 741,
    750, 762, 771, 783, 792, 804, 813, 825, 834,
)
BASELINE_SHA = '8222c9127774620e6caed18120772f7aad8c68a46d766192a0213c05e1786fe7'


def test_settings_plan_binds_and_restores_every_panel_measurement():
    plan = json.loads(Path('translations/plans/pr03-options.json').read_text())
    sites = plan.get('abc_font_bindings', [])
    assert len(sites) == 131
    expected = {(i, 'panel_format_before'): (79, [3729, 6]) for i in CALLS}
    expected.update({(i + 2, 'panel_format_after'): (None, None) for i in CALLS if i != 638})
    assert {(s['instruction_index'], s['kind']) for s in sites} == set(expected)
    for site in sites:
        assert site['tag_path'] == [4175] and site['method_index'] == 79
        assert site['receiver_hex'] == 'd266c606'  # local2.tFormat
        assert site['original_font_string_index'] == 10038
        assert site['original_font_family'] == 'Celtic Garamond for GemCraft'
        assert site['extra_stack'] >= 2
        opcode, operands = expected[site['instruction_index'], site['kind']]
        if opcode is not None:
            assert site['expected_opcode'] == opcode
        if operands is not None:
            assert site['expected_operands'] == operands


@pytest.fixture(scope='module')
def private_inputs():
    required = ('GEMCRAFT_PR16_BASELINE', 'GEMCRAFT_PR16_CANDIDATE')
    if not all(os.environ.get(name) for name in required):
        pytest.skip('Set private baseline and PR16 candidate paths')
    baseline_path = Path(os.environ[required[0]])
    original_path = os.environ.get('GEMCRAFT_ORIGINAL')
    if original_path:
        original_path = Path(original_path)
    else:
        originals = list(Path('backups').glob('*.original'))
        if not originals:
            pytest.skip('Private hash-guarded original is required')
        original_path = originals[0]
    original = original_path.read_bytes()
    baseline = baseline_path.read_bytes()
    candidate = Path(os.environ[required[1]]).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    assert sha(baseline) == BASELINE_SHA
    workspace_path = Path(os.environ.get(
        'GEMCRAFT_PR16_WORKSPACE', str(baseline_path.with_name('workspace.json'))))
    workspace = json.loads(workspace_path.read_text())
    return original, baseline, candidate, {e['id']: e for e in workspace['entries']}


@pytest.mark.local
def test_settings_candidate_brackets_real_calls_and_preserves_other_resources(private_inputs):
    original, baseline, candidate, _ = private_inputs
    _, original_tags = read_swf(original)
    before_header, before_tags = read_swf(baseline)
    after_header, after_tags = read_swf(candidate)
    assert before_header[8:] == after_header[8:]
    assert len(before_tags) == len(after_tags)
    assert [i for i, (a, b) in enumerate(zip(before_tags, after_tags)) if a != b] == [
        i for i, tag in enumerate(before_tags) if tag.code == 82]
    old_abc, new_abc = abc_bytes(before_tags), abc_bytes(after_tags)
    old_bodies, new_bodies = _bodies(old_abc), _bodies(new_abc)
    assert old_bodies.keys() == new_bodies.keys()
    assert {m for m in old_bodies if old_bodies[m][2:] != new_bodies[m][2:]} == {79}
    # Removing only m79 leaves identical ABC bytes, including pools and metadata.
    a, b = old_bodies[79][:2]
    c, d = new_bodies[79][:2]
    assert old_abc[:a] + old_abc[b:] == new_abc[:c] + new_abc[d:]
    before = abc_inventory(abc_bytes(original_tags))
    after = abc_inventory(new_abc)
    old_ops = before.instructions(next(c for m, _, c in before.bodies if m == 79), 79)
    new_ops = after.instructions(new_bodies[79][3], 79)
    calls = [i for i, (_, op, args) in enumerate(old_ops) if (op, args) == (79, [3729, 6])]
    assert calls == list(CALLS)
    font_name = next(i for i in range(1, len(after.multinames)) if after.multiname(i) == 'font')
    family = after.strings.index('GCCS Alegreya')
    actual = [i for i, (_, op, args) in enumerate(new_ops) if (op, args) == (79, [3729, 6])]
    assert len(actual) == 66
    inserted = set()
    for old_index, index in zip(calls, actual):
        expected_before = [(0xd2, []), (0x66, [838]), (0x2c, [family]), (0x61, [font_name])]
        expected_after = [(0xd2, []), (0x66, [838]), (0x2c, [10038]), (0x61, [font_name])]
        assert [(op, args) for _, op, args in new_ops[index - 4:index]] == expected_before
        if old_index == 638:
            # Disabled weather jumps past enabled text to the same restoring tail.
            jump = next(i for i in range(index + 1, len(new_ops)) if new_ops[i][1] == 0x10)
            destination = new_ops[jump + 1][0] + new_ops[jump][2][0]
            enabled_call = actual[calls.index(657)]
            # Existing after-field binding at the shared boundary also runs
            # on the disabled path, before its debugline and shared restoration.
            assert destination == new_ops[enabled_call + 1][0]
            assert old_ops[640][0] + old_ops[639][2][0] == old_ops[658][0]
            successor = next(i for i in range(enabled_call + 1, len(new_ops))
                             if new_ops[i][1:] == old_ops[658][1:])
        else:
            successor = next(i for i in range(index + 1, len(new_ops))
                             if new_ops[i][1:] == old_ops[old_index + 1][1:])
        assert [(op, args) for _, op, args in new_ops[successor + 1:successor + 5]] == expected_after
        assert new_ops[successor + 5][1] == old_ops[659 if old_index == 638 else old_index + 2][1]
        inserted.update(range(index - 4, index))
        inserted.update(range(successor + 1, successor + 5))
    retained = [(offset, op, args) for i, (offset, op, args) in enumerate(new_ops)
                if i not in inserted]
    baseline_inv = abc_inventory(old_abc)
    baseline_ops = baseline_inv.instructions(old_bodies[79][3], 79)
    assert len(retained) == len(baseline_ops)
    # Compare every old operation, including relocated control-flow destinations.
    projected = {}
    count = 0
    for i, (offset, _, _) in enumerate(new_ops):
        projected[offset] = count
        count += i not in inserted
    projected[len(new_bodies[79][3])] = count
    old_positions = {offset: i for i, (offset, _, _) in enumerate(baseline_ops)}
    old_positions[len(old_bodies[79][3])] = len(baseline_ops)
    for i, ((offset, op, args), (new_offset, new_op, new_args)) in enumerate(
            zip(baseline_ops, retained)):
        assert op == new_op
        if 0x0c <= op <= 0x1a:
            end = baseline_ops[i + 1][0]
            new_end = next(o for o, _, _ in new_ops if o > new_offset)
            assert old_positions[end + args[0]] == projected[new_end + new_args[0]]
        elif op == 0x1b:
            assert args[1] == new_args[1]
            assert [old_positions[offset + delta] for delta in (args[0], *args[2:])] == [
                projected[new_offset + delta] for delta in (new_args[0], *new_args[2:])]
        else:
            assert args == new_args
    assert new_bodies[79][2][0] == old_bodies[79][2][0]
    assert new_bodies[79][2][1:] == old_bodies[79][2][1:]
    assert new_bodies[79][5] == old_bodies[79][5]
    # Shared measurement logic and every other settings section remain byte-identical.
    assert all(old_bodies[m][2:] == new_bodies[m][2:] for m in (3504, 3506, 3469))
    # Guard the actual width, wrapping, y and normal inter-field spacing mechanism.
    code = next(c for m, _, c in before.bodies if m == 3504)
    ops = [(op, args) for _, op, args in before.instructions(code, 3504)]
    assert ops[52:56] == [(0x62, [7]), (0xd0, []), (0x66, [838]), (0x61, [5573])]
    assert ops[61:64] == [(0x62, [7]), (0x26, []), (0x61, [5883])]
    assert ops[74:80] == [(0x62, [7]), (0xd0, []), (0x66, [3711]),
                          (0x24, [20]), (0xa1, []), (0x61, [5359])]
    assert ops[81:85] == [(0x62, [7]), (0xd0, []), (0x66, [3708]), (0x61, [1303])]
    assert ops[354:359] == [(0x24, [9]), (0x82, []), (0xa0, []), (0xa0, []), (0x61, [3708])]
    assert ops[360:366] == [(0xd0, []), (0xd0, []), (0x66, [3708]),
                            (0x24, [8]), (0xa0, []), (0x61, [3710])]
    assert b'\xd2\x66' + _u30(838) in new_bodies[79][3]


@pytest.mark.local
def test_settings_real_outlines_and_neighbor_spacing_model(private_inputs, tmp_path):
    """Explicit outline/advance model, not an assertion about AIR word wrapping."""
    original, _, candidate, entries = private_inputs
    _, tags = read_swf(candidate)
    fonts = {int.from_bytes(t.payload[:2], 'little'): parse_font(t.payload)
             for t in tags if t.code == 75}
    font, celtic = fonts[6002], fonts[255]
    inv = abc_inventory(abc_bytes(read_swf(original)[1]))
    ops = inv.instructions(next(c for m, _, c in inv.bodies if m == 79), 79)
    advances = {chr(c): a / 20480 * 12 for c, a in zip(font['codes'], font['advances'])}
    celtic_advances = {chr(c): a / 20480 * 12 for c, a in zip(celtic['codes'], celtic['advances'])}
    line_height = (font['ascent'] + font['descent'] + font['leading']) / 20480 * 12
    old_line_height = (celtic['ascent'] + celtic['descent'] + celtic['leading']) / 20480 * 12
    switch_offset, _, switch_args = next(item for item in ops if item[1] == 0x1b)
    starts = sorted({switch_offset + delta for delta in switch_args[2:]})
    multi_field = 0
    rows = []
    for index in CALLS:
        # Constant arguments have exactly five arguments after the string.
        if ops[index - 5][1] != 0x2c:
            continue
        branch_start = max(start for start in starts if start <= ops[index][0])
        resets = [i for i in range(index) if ops[i][0] >= branch_start
                  and ops[i][1:] == (0x4f, [3730, 2])]
        reset = resets[-1] if resets else 16
        assert ops[reset - 2][1] == 0x25
        width = ops[reset - 2][2][0] - 20 - 4  # original width/insets + Flash gutters
        offset = ops[index - 5][0]
        entry = entries[f'abc.4175.79.{offset}']
        text = entry['translation']
        assert text and text == text.strip()
        assert '\r' not in text and all(line == line.strip() for line in text.split('\n'))
        assert ops[index - 4][1] == 0x27  # isNewLine=false: normal +9px spacing
        size = ops[index - 3][2][0]
        assert size in (11, 12)
        scale = size / 12
        lines = _wrap_lines(text, lambda ch: advances[ch] * scale, width)
        measured = measure_text(font, '\n'.join(lines), size * 20)
        assert max(measured['widths']) / 20 <= width
        assert len(measured['bounds']) == len(lines)
        assert all(box is None or box[3] - box[2] <= line_height * scale * 20
                   for box in measured['bounds'])
        # Neighbor's top is based on the same final font and field height (+9).
        field_height = len(lines) * line_height * scale + 4
        next_top = 10 + field_height + 9
        assert next_top - (10 + field_height) == pytest.approx(9)
        assert next_top + 8 > 10 + field_height
        assert field_height + 9 + 18 < 592
        rows.append({'instruction_index': index, 'id': entry['id'], 'rendered_sample': text, 'available_width_px': width, 'size_px': size,
                     'lines': len(lines), 'advance_widths_px': [w / 20 for w in measured['widths']],
                     'height_model_px': field_height, 'neighbor_gap_model_px': 9})
        if offset in (263, 1661):
            # Proven multi-field branches: body then resource-demand field.
            assert ops[index + 9][1:] == (0x4f, [3729, 6])
            old_lines = _wrap_lines(text, lambda ch: celtic_advances.get(ch, 0) * scale, width)
            old_height = len(old_lines) * old_line_height * scale + 4
            assert len(lines) == 3
            assert 9 + old_height - field_height < 0  # prior capture overlaps neighbor
            neighbor_offset = ops[index + 4][0]
            neighbor = entries[f'abc.4175.79.{neighbor_offset}']['translation']
            neighbor_metrics = measure_text(font, neighbor, size * 20)
            baseline = font['ascent'] / 20480 * size
            body_bottom = 10 + baseline + max(b[3] for b in measured['bounds'] if b) / 20
            neighbor_ink_top = next_top + baseline + min(
                b[2] for b in neighbor_metrics['bounds'] if b) / 20
            assert neighbor_ink_top > body_bottom
            rows[-1]['neighbor_ink_gap_model_px'] = neighbor_ink_top - body_bottom
            rows[-1]['old_neighbor_gap_model_px'] = 9 + old_height - field_height
            multi_field += 1
    assert multi_field == 2
    # Exact preserved fragments, representative formatted outputs. These samples
    # impose no invented caps on the game preferences or claim AIR formatting.
    def text(offset):
        return entries[f'abc.4175.79.{offset}']['translation']
    dynamic = {
        39: [text(71) + str(n) + '%' for n in (0, 1, 50, 99, 100)],
        60: [text(121) + str(n) + '%' for n in (0, 1, 50, 99, 100)],
        391: [text(905), *(text(912) + n + text(934) for n in ('0', '0.5', '1', '100'))],
        530: [text(1258) + str(n) + '%' for n in (0, 1, 100, 200)],
        657: [text(1569) + str(n) + text(1589) for n in (0, 1, 50, 100)],
    }
    for index, offset in ((441, 1043), (458, 1086), (475, 1129), (492, 1172), (509, 1215)):
        dynamic[index] = [text(offset) + n for n in ('0', '1', '999', '999999', '999,999')]
    for index, samples in dynamic.items():
        branch_start = max(start for start in starts if start <= ops[index][0])
        resets = [i for i in range(index) if ops[i][0] >= branch_start
                  and ops[i][1:] == (0x4f, [3730, 2])]
        reset = resets[-1] if resets else 16
        width = ops[reset - 2][2][0] - 24
        for sample in samples:
            assert sample == sample.strip() and '\r' not in sample
            lines = _wrap_lines(sample, lambda ch: advances[ch], width)
            metrics = measure_text(font, '\n'.join(lines), 12 * 20)
            assert max(metrics['widths']) / 20 <= width
            height = len(lines) * line_height + 4
            assert height + 18 < 592
            rows.append({'instruction_index': index, 'rendered_sample': sample, 'dynamic': True,
                         'available_width_px': width, 'size_px': 12, 'lines': len(lines),
                         'advance_widths_px': [w / 20 for w in metrics['widths']],
                         'height_model_px': height, 'neighbor_gap_model_px': 9})
    assert {row['instruction_index'] for row in rows} == set(CALLS)

    def ink(row):
        size = row['size_px']
        wrapped = _wrap_lines(row['rendered_sample'],
                              lambda ch: advances[ch] * size / 12,
                              row['available_width_px'])
        measured = measure_text(font, '\n'.join(wrapped), size * 20)
        boxes = [b for b in measured['bounds'] if b]
        baseline = font['ascent'] / 20480 * size
        return baseline + min(b[2] for b in boxes) / 20, baseline + max(
            b[3] for b in boxes) / 20

    grouped = []
    for index in CALLS:
        if grouped and not any(0x0c <= op <= 0x1b for _, op, _ in ops[grouped[-1][-1] + 1:index]):
            grouped[-1].append(index)
        else:
            grouped.append([index])
    grouped.append([638, 666, 675])  # disabled opacity rejoins enabled weather tail
    groups = []
    for group in grouped:
        # All successive fields in a straight-line path use final font heights.
        heights = [max(row['height_model_px'] for row in rows
                       if row['instruction_index'] == index) for index in group]
        panel_height = 10 + sum(h + 9 for h in heights) + 8
        assert panel_height < 592
        groups.append({'call_indices': group, 'max_height_model_px': panel_height})
        for left, right in zip(group, group[1:]):
            for body in (r for r in rows if r['instruction_index'] == left):
                for neighbor in (r for r in rows if r['instruction_index'] == right):
                    _, body_bottom = ink(body)
                    neighbor_top, _ = ink(neighbor)
                    assert body['height_model_px'] + 9 + neighbor_top > body_bottom

    (tmp_path / 'options-geometry-model.json').write_text(json.dumps(
        {'runtime_verified': False, 'model': 'SWF outlines, greedy wrap; AIR acceptance pending',
         'fields': rows, 'straight_line_groups': groups}, ensure_ascii=False, indent=2) + '\n')
