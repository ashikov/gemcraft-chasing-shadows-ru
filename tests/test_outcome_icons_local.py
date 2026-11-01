"""Actual SWF regression; never launches AIR or writes installed resources."""
import os
from pathlib import Path

import pytest

from build_prototype import ink_bounds
from swf_geometry import decode_matrix, parse_font, parse_text, read_swf


def left(tags, character):
    text = parse_text(next(t.payload for t in tags if t.code == 11
                           and int.from_bytes(t.payload[:2], 'little') == character))
    row = text['records'][0]
    font = parse_font(next(t.payload for t in tags if t.code == 75
                           and int.from_bytes(t.payload[:2], 'little') == row['font_id']))
    return ink_bounds(font, row['entries'], row['height'], row.get('x', 0),
                      row.get('y', 0))[0] + decode_matrix(text['matrix_raw'])[0]['translate'][0]


@pytest.mark.local
def test_actual_outcome_icon_gaps():
    paths = [os.environ.get(k) for k in ('GEMCRAFT_ORIGINAL', 'GEMCRAFT_PR36_CANDIDATE')]
    if not all(paths):
        pytest.skip('requires original and PR36 candidate')
    original, candidate = [read_swf(Path(p).read_bytes())[1] for p in paths]
    from outcome_info_icons import icon_rectangles
    shapes = [next(t.payload for t in tags if t.code == 22
                   and int.from_bytes(t.payload[:2], 'little') == 3770)
              for tags in (original, candidate)]
    for character, old, new in zip((3735, 3751, 3753, 3759, 3761, 3767, 3734),
                                    *(icon_rectangles(p) for p in shapes)):
        shift = (left(candidate, character) - left(original, character)) * 65464 / 65536
        assert abs((new[0] - old[0]) - shift) <= 1


def placements(payload):
    from swf_geometry import read_tags
    result = []
    for index, tag in enumerate(read_tags(payload[4:])):
        if tag.code not in (26, 70):
            continue
        flags = tag.payload[0]
        if flags & 6 != 6:
            continue
        start = 5 if tag.code == 26 else 6
        matrix, size = decode_matrix(tag.payload[start:])
        result.append((index, int.from_bytes(tag.payload[start - 2:start], 'little'), matrix,
                       tag.payload[start + size:], tag))
    return result


@pytest.mark.local
def test_outcome_scope_hover_and_scaling():
    paths = [os.environ.get(k) for k in ('GEMCRAFT_ORIGINAL', 'GEMCRAFT_PR36_CANDIDATE',
                                        'GEMCRAFT_PR36_BASELINE')]
    if not all(paths):
        pytest.skip('requires original, cumulative baseline and PR36 candidate')
    raw_original, raw_candidate, raw_baseline = [Path(p).read_bytes() for p in paths]
    assert raw_original[3] == raw_candidate[3] == raw_baseline[3]
    original, candidate, baseline = [read_swf(p)[1]
                                      for p in (raw_original, raw_candidate, raw_baseline)]
    from outcome_info_icons import icon_rectangles
    def resource(tags, code, cid):
        return next(t for t in tags if t.code == code
                    and int.from_bytes(t.payload[:2], 'little') == cid)
    op, cp = [placements(resource(tags, 39, 3787).payload) for tags in (original, candidate)]
    oi, ci = [icon_rectangles(resource(tags, 22, 3770).payload) for tags in (original, candidate)]
    labels = (3735, 3751, 3753, 3759, 3761, 3767, 3734)
    # Inventory actual bitmap rectangles and named hover instances, excluding Purple1/2.
    plates = [p for p in op if p[1] == 3779 and b'Purple' not in p[3]]
    assert len(plates) == len(oi) == len(ci) == 7
    plate_by_label = dict(zip((3734, 3735, 3751, 3753, 3759, 3761, 3767), plates))
    columns = {3734: (0, 7200), 3735: (0, 7200), 3751: (0, 7200),
               3753: (7200, 14400), 3759: (7200, 14400),
               3761: (14400, 23040), 3767: (14400, 23040)}
    for cid, before, after in zip(labels, oi, ci):
        label = next(p for p in op if p[1] == cid)
        assert next(p for p in cp if p[1] == cid)[4] == label[4]
        matrix = label[2]
        factor, tx = matrix['scale'][0] / 65536, matrix['translate'][0]
        en_left, ru_left = [left(tags, cid) * factor + tx for tags in (original, candidate)]
        old_plate = plate_by_label[cid]
        new_plate = next(p for p in cp if p[0] == old_plate[0])
        assert old_plate[3] == new_plate[3]  # instance names / handlers unchanged
        for scale in (1, .75, .5):
            old_gap = (en_left - before[1]) * scale
            new_gap = (ru_left - after[1]) * scale
            assert old_gap > 0 and new_gap > 0
            assert abs(old_gap - new_gap) <= scale
            lo, hi = columns[cid]
            assert lo * scale < after[0] * scale < ru_left * scale < hi * scale
            pm = new_plate[2]
            assert pm['translate'][0] <= after[0]
            assert after[1] <= pm['translate'][0] + 1000 * pm['scale'][0] / 65536
            assert pm['translate'][1] <= after[2]
            assert after[3] <= pm['translate'][1] + 1000 * pm['scale'][1] / 65536
        old_ink, new_ink = [], []
        for tags, output in ((original, old_ink), (candidate, new_ink)):
            text = parse_text(resource(tags, 11, cid).payload)
            assert len(text['records']) == 1
            row = text['records'][0]
            font = parse_font(resource(tags, 75, row['font_id']).payload)
            output.extend(ink_bounds(font, row['entries'], row['height'], row.get('x', 0),
                                     row.get('y', 0))[:2])
        assert abs(sum(old_ink) - sum(new_ink)) <= 2
        # Whole new label retains the original hover padding on its right.
        old_right = (old_ink[1] + decode_matrix(parse_text(resource(original,11,cid).payload)
                                              ['matrix_raw'])[0]['translate'][0]) * factor + tx
        new_right = (new_ink[1] + decode_matrix(parse_text(resource(candidate,11,cid).payload)
                                              ['matrix_raw'])[0]['translate'][0]) * factor + tx
        assert columns[cid][0] < ru_left < new_right < columns[cid][1]
        def plate_right(p):
            return p[2]['translate'][0] + p[2]['scale'][0] * 1000 / 65536
        assert abs((plate_right(old_plate) - old_right)
                   - (plate_right(new_plate) - new_right)) < 1
    changed_children = {p[0] for p in plates}
    for old, new in zip(op, cp):
        if old[0] not in changed_children:
            assert old[4] == new[4]
    # Only two tags differ from the actual cumulative PR35 baseline, including ABC,
    # numbers, columns, shared artwork, fonts and all other screens.
    assert len(candidate) == len(baseline)
    differences = [(a.code, int.from_bytes(a.payload[:2], 'little'))
                   for a, b in zip(candidate, baseline) if a != b]
    assert differences == [(22, 3770), (39, 3787)]
    # Single consumers prove that these two changed resources cannot leak to other screens.
    usages = {3770: [], 3779: [], 3787: []}
    from swf_geometry import read_tags
    for tag in original:
        if tag.code != 39:
            continue
        owner = int.from_bytes(tag.payload[:2], 'little')
        for child in read_tags(tag.payload[4:]):
            if child.code == 4:
                cid = int.from_bytes(child.payload[:2], 'little')
            elif child.code in (26, 70) and child.payload[0] & 2:
                start = 3 if child.code == 26 else 4
                if child.code == 70 and child.payload[1] & 8:
                    start = child.payload.index(b'\0', start) + 1
                cid = int.from_bytes(child.payload[start:start + 2], 'little')
            else:
                continue
            if cid in usages:
                usages[cid].append(owner)
    assert usages == {3770: [3787], 3779: [3787] * 9, 3787: [3932]}


@pytest.mark.local
def test_original_hover_bindings_and_shared_outcome_states():
    """Fresh ABC proof that all four outcomes share the repaired geometry."""
    source = os.environ.get('GEMCRAFT_ORIGINAL')
    if not source:
        pytest.skip('requires original SWF')
    from abc_inventory import ABCInventory
    from install_patch import ORIGINAL_SHA256, sha
    from swf_geometry import read_tags
    raw = Path(source).read_bytes()
    assert sha(raw) == ORIGINAL_SHA256
    tags = read_swf(raw)[1]
    abc_tag = next(t for t in tags if t.code == 82)
    abc = ABCInventory(abc_tag.payload[abc_tag.payload.index(b'\0', 4) + 1:],
                       {'tag_path': [4175], 'tag_code': 82})
    abc.parse()
    codes = {method: code for method, _, code in abc.bodies}
    expected = {
        'mcPlateRawBattleXp': 'renderInfoRawXp',
        'mcPlateManaGathered': 'renderInfoManaGathered',
        'mcPlateManaSpent': 'renderInfoManaSpent',
        'mcPlateKills': 'renderInfoKills',
        'mcPlateSpellsCast': 'renderInfoSpellsCast',
        'mcPlateMonstersSummoned': 'renderInfoMonstersSummoned',
        'mcPlateGemWaspStings': 'renderInfoGemWaspStings',
    }
    # Resolve actual receiver/event/handler names at addEventListener calls.
    names, registrations = [], []
    for _, opcode, args in abc.instructions(codes[877], 877):
        if opcode in (0x60, 0x66):
            names.append(abc.multiname(args[0]))
        elif opcode == 0x4f and abc.multiname(args[0]) == 'addEventListener':
            receiver = names[-6:]
            if len(receiver) == 6 and receiver[2] in expected:
                assert receiver[:2] == ['cnt', 'mcOutcomePanel']
                assert receiver[3] == 'MouseEvent'
                assert args[1] == 5
                registrations.append((receiver[2], receiver[4], receiver[5]))
    assert sorted(registrations) == sorted(
        [(plate, 'MOUSE_OVER', handler) for plate, handler in expected.items()]
        + [(plate, 'MOUSE_OUT', 'ehOutcomePanelInfoPlateOut') for plate in expected])

    switches = {}
    for method in (892, 893):
        operations = abc.instructions(codes[method], method)
        property_uses = [abc.multiname(args[0]) for _, opcode, args in operations
                         if opcode in (0x60, 0x66, 0x61, 0x68)]
        assert 'mcOutcomePanel' in property_uses
        assert not set(expected) & set(property_uses)
        assert not {'x', 'y', 'scaleX', 'scaleY'} & {
            abc.multiname(args[0]) for _, opcode, args in operations if opcode in (0x61, 0x68)}
        switches[method] = []
        for index, (_, opcode, args) in enumerate(operations):
            if opcode != 0x61 or abc.multiname(args[0]) != 'visible':
                continue
            chain = operations[index - 4:index - 1]
            assert [op for _, op, _ in chain] == [0x66] * 3
            receiver = [abc.multiname(operands[0]) for _, _, operands in chain]
            assert receiver[:2] == ['cnt', 'mcOutcomePanel']
            boolean = operations[index - 1][1]
            assert boolean in (0x26, 0x27)
            switches[method].append((receiver[2], boolean == 0x26))
    victory, defeat = 'tfTitleVictory', 'tfTitleDefeat'
    endurance, endurance_victory = 'tfTitleEnduranceOutcome', 'tfTitleEnduranceVictory'
    assert switches[892] == [(victory, False), (defeat, True),
                             (endurance, False), (endurance_victory, False)]
    assert switches[893] == [
        (victory, False), (defeat, False),
        (endurance, True), (endurance_victory, False),
        (endurance, False), (endurance_victory, True),
        (victory, True), (defeat, False), (endurance, False), (endurance_victory, False),
    ]
    parent = next(t for t in tags if t.code == 39
                  and int.from_bytes(t.payload[:2], 'little') == 3932)
    child = read_tags(parent.payload[4:])[9]
    assert child.code == 26
    assert int.from_bytes(child.payload[1:3], 'little') == 30
    assert int.from_bytes(child.payload[3:5], 'little') == 3787
    matrix, _ = decode_matrix(child.payload[5:])
    assert matrix['scale'] == [65536, 65536]
    assert matrix['rotate'] == [0, 0]
