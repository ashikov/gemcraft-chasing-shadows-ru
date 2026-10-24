"""Owned-source map event relocation proof; no game launch or installed writes."""
import json
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from install_patch import ORIGINAL_SHA256, sha
from localization_plan import apply_plan_font_bindings
from swf_geometry import read_swf
from test_talisman_local import abc_bytes, abc_inventory

pytestmark = pytest.mark.local


def test_map_events_skip_typography_when_the_panel_creation_was_skipped():
    source = os.environ.get('GEMCRAFT_ORIGINAL')
    candidates = ([Path(source)] if source else []) + list(Path('backups').glob('*.original'))
    if not candidates:
        pytest.skip('Set GEMCRAFT_ORIGINAL or provide an owned original backup')
    data = candidates[0].read_bytes()
    assert sha(data) == ORIGINAL_SHA256
    raw = abc_bytes(read_swf(data)[1])
    original_inv = abc_inventory(raw)
    sites = [row for row in json.loads(Path('translations/plans/pr04-map.json').read_text())[
        'abc_font_bindings'] if row['method_index'] == 341]
    assert len(sites) == 4 and all(row['kind'] == 'floater' for row in sites)
    result, counts = apply_plan_font_bindings(raw, raw, sites)
    assert counts == {'341': 4}
    inv = abc_inventory(result)
    old_bodies, new_bodies = _bodies(raw), _bodies(result)
    original = original_inv.instructions(old_bodies[341][3], 341)
    modified = inv.instructions(new_bodies[341][3], 341)
    # Remove only the four complete typography spans, each immediately after
    # addFloaterPanel and ending with its final plateHeight write. Retain every
    # original instruction as a semantic correspondence for branch targets.
    retained = []
    snippets = []
    index = 0
    while index < len(modified):
        operation = modified[index]
        retained.append(operation)
        index += 1
        if operation[1:] == (0x4f, [3841, 8]):
            first = index
            while modified[index][1:] != (0x61, [3367]):
                index += 1
            index += 1
            snippets.append(modified[first:index])
    assert len(snippets) == 4
    assert len(retained) == len(original)
    source_indices = {at: i for i, (at, _, _) in enumerate(original)}
    target_indices = {at: i for i, (at, _, _) in enumerate(retained)}
    affected = []
    boundaries = {original[row['instruction_index']][0] for row in sites}
    for i, ((at, opcode, args), (new_at, new_opcode, new_args)) in enumerate(zip(original, retained)):
        assert opcode == new_opcode
        if 0x0c <= opcode <= 0x1a:
            destination = original[i + 1][0] + args[0]
            new_destination = retained[i + 1][0] + new_args[0]
            assert new_destination in target_indices
            assert source_indices[destination] == target_indices[new_destination]
            if destination in boundaries:
                affected.append(at)
        elif opcode == 0x2c:
            assert original_inv.strings[args[0]] == inv.strings[new_args[0]]
        else:
            assert args == new_args
    # Includes SC event frames before step30, all previous event exits, and
    # the separate map-hextile popup guard. Neither may read an absent panel.
    assert affected == [516, 1579, 2227, 3323, 4009, 4089, 4120, 4294]
    for method, body in old_bodies.items():
        if method != 341:
            assert body[2:] == new_bodies[method][2:]
    assert old_bodies[341][4:] == new_bodies[341][4:]


def assert_actual_map_floater_branch_repair(original_raw, baseline_raw, candidate_raw):
    """Prove the actual cumulative method changes only its eight unsafe edges."""
    original_inv, baseline_inv, candidate_inv = map(
        abc_inventory, (original_raw, baseline_raw, candidate_raw))
    source_body, old_body, new_body = (
        _bodies(raw)[341] for raw in (original_raw, baseline_raw, candidate_raw))
    source = original_inv.instructions(source_body[3], 341)
    baseline = baseline_inv.instructions(old_body[3], 341)
    candidate = candidate_inv.instructions(new_body[3], 341)
    sites = [row for row in json.loads(Path('translations/plans/pr04-map.json').read_text())[
        'abc_font_bindings'] if row['method_index'] == 341]
    assert len(sites) == 4 and all(row['kind'] == 'floater' for row in sites)

    def without_typography(operations):
        retained, spans = [], []
        index = 0
        while index < len(operations):
            operation = operations[index]
            retained.append(operation)
            index += 1
            if operation[1:] == (0x4f, [3841, 8]):
                first = index
                while operations[index][1:] != (0x61, [3367]):
                    index += 1
                index += 1
                spans.append((operations[first][0], operations[index][0]))
        assert len(spans) == 4
        assert len(retained) == len(source)
        assert [op for _, op, _ in retained] == [op for _, op, _ in source]
        return retained, spans

    previous, old_spans = without_typography(baseline)
    current, new_spans = without_typography(candidate)
    assert old_spans == new_spans
    boundaries = {}
    for site, old_span, new_span in zip(sites, old_spans, new_spans):
        position = site['instruction_index']
        assert source[position - 1][1:] == (0x4f, [3841, 8])
        assert previous[position][0] == old_span[1]
        assert current[position][0] == new_span[1]
        boundaries[source[position][0]] = (old_span, new_span)
    source_indices = {at: i for i, (at, _, _) in enumerate(source)}
    repairs = {}
    affected = []
    for index, (at, opcode, args) in enumerate(source):
        if not 0x0c <= opcode <= 0x1a:
            continue
        source_target = source[index + 1][0] + args[0]
        if source_target not in boundaries:
            continue
        old_span, new_span = boundaries[source_target]
        old_branch, new_branch = previous[index], current[index]
        old_target = previous[index + 1][0] + old_branch[2][0]
        new_target = current[index + 1][0] + new_branch[2][0]
        assert old_target == old_span[0]  # Baseline entered post-call reads.
        assert new_target == new_span[1]  # Candidate skips those reads.
        assert new_target == current[source_indices[source_target]][0]
        assert old_branch[:2] == new_branch[:2]
        assert old_branch[2] != new_branch[2]
        repairs[old_branch[0]] = new_branch[2]
        affected.append(at)
    assert affected == [516, 1579, 2227, 3323, 4009, 4089, 4120, 4294]
    assert len(repairs) == 8
    assert len(old_body[3]) == len(new_body[3])
    assert old_body[2] == new_body[2] and old_body[4:] == new_body[4:]
    assert len(baseline) == len(candidate)
    for (at, opcode, args), (new_at, new_opcode, new_args) in zip(baseline, candidate):
        assert (at, opcode) == (new_at, new_opcode)
        if at in repairs:
            assert new_args == repairs[at]
        elif opcode == 0x2c:
            assert baseline_inv.strings[args[0]] == candidate_inv.strings[new_args[0]]
        else:
            assert args == new_args
    return affected
