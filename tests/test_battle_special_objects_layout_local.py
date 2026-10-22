"""Opt-in object panel outline models; Flash/AIR rendering remains unverified."""

import json
import os
from functools import lru_cache
from itertools import product
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCReader
from install_patch import ORIGINAL_SHA256, sha
from swf_geometry import parse_font, read_swf
from test_battle_shrines_layout_local import OutlineModel
from test_battle_special_objects import HOLDER_OFFSETS
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local
BASE_SHA = 'ddf10d3afc7e1ea139f056f8e86c6db585bdbf81af2315fc87f68b9dbeeace13'


@pytest.fixture(scope='module')
def object_layout():
    if not os.environ.get('GEMCRAFT_PR28_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR28_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR28_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    baseline = (root / 'baseline/gc-cs-steam.ru.swf').read_bytes()
    assert sha(baseline) == BASE_SHA
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    assert sha(candidate) == load_catalog('translations/build-manifest.json')['expected_candidate_sha256']
    tags = [read_swf(data)[1] for data in (original, baseline, candidate)]
    fonts = [{int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
              for tag in group if tag.code == 75} for group in tags]
    abc = abc_bytes(tags[0])
    inv = abc_inventory(abc)
    ops = {m: inv.instructions(_bodies(abc)[m][3], m) for m in (630, 3504, 3506, 3508)}
    entries = {row['id']: row for row in load_catalog(root / 'candidate/workspace.json')['entries']}
    # The integer pool establishes reset's default width.
    reader = ABCReader(abc)
    reader.take(4)
    ints = [0] + [reader.encoded(32) for _ in range(reader.u30() - 1)]
    assert ints[method_metadata(abc)[3506][4][0][0]] == 280
    assert method_metadata(abc)[3504][4] == [(10, 10), (6, 3), (12, 12), (2, 3)]
    return root, candidate, fonts, inv, ops, entries


def test_original_panel_dimensions_and_zoom_formula(object_layout):
    _, _, _, _, ops, _ = object_layout
    panel = {at: (op, args) for at, op, args in ops[630]}
    # GemSeal inherits reset() at 2637; every subsequent selected object resets 320.
    assert panel[2637] == (0x4f, [3730, 0])
    for at in (7501, 8259, 8466, 8722, 8848, 8987, 9148):
        assert panel[at] == (0x25, [320])
    renderer = {at: (op, args) for at, op, args in ops[3504]}
    assert renderer[199] == (0x24, [20])
    assert renderer[846] == (0x24, [253])  # compact spacing -3
    assert renderer[853] == (0x24, [9])
    assert renderer[869] == (0x24, [8])
    zoom = {at: (op, args) for at, op, args in ops[3508]}
    assert zoom[97] == (0x24, [1])
    assert zoom[105] == (0x93, [])
    assert zoom[108] == (0xa2, [])
    assert zoom[1001] == (0xa3, [])


def test_complete_object_branches_outlines_neighbors_and_viewport(object_layout):
    root, candidate, fonts, inv, ops, entries = object_layout
    assert fonts[0][255] == fonts[1][255]
    source = OutlineModel(fonts[0][255])
    models = (source, source, OutlineModel(fonts[2][6002]))

    def text(at, model, method=630):
        row = entries[f'abc.4175.{method}.{at}']
        return row['translation'] if model == 2 and row['translation'] is not None else row['source']

    @lru_cache(maxsize=None)
    def rendered(model, number):
        result, tokens, segment = {}, [], []
        for at, op, args in ops[630]:
            if not (5901 <= at <= 6321 or 7511 <= at <= 10662):
                continue
            segment.append((at, op, args))
            if op == 0x2c and at != 9832:
                tokens.append(text(at, model))
            elif op == 0x46 and inv.multiname(args[0]) == 'format':
                tokens.append(number)
            elif op == 0x4f and args[0] == 3729:
                count = args[1]
                size, extra = 12, 9
                if count in (4, 6):
                    tail = segment[-3:] if count == 4 else segment[-5:]
                    assert tail[0][1] in (0x26, 0x27) and tail[1][1] == 0x24
                    compact, size = tail[0][1] == 0x26, tail[1][2][0]
                    extra = -3 if compact else 9
                    if count == 6:
                        assert tail[-2][1] == 0x2d
                        # Sixth argument is a color/highlight parameter, not spacing.
                result[at] = (''.join(tokens), size, extra)
                segment, tokens = [], []
        return result

    def panel(rows, width, model):
        y, bottom, fields = 10., None, []
        for row in rows:
            if isinstance(row, (int, float)):
                y += row
                continue
            value, size, extra = row
            # Original renderer field width w-20, with 4px TextField gutters.
            measured = models[model].field(value, size, width - 24)
            top, next_bottom = y + measured['top'], y + measured['bottom']
            assert bottom is None or top >= bottom, (value, bottom, top)
            fields.append({'text': value, 'size': size, 'y': y, **measured})
            bottom = next_bottom
            y += measured['height'] + extra
        return {'height': y + 8, 'fields': fields}

    evidence, covered = [], set()
    numbers = ('0', '1', '2', '21', '99', '100', '999.9', '1,000', '1.0000000 e12')
    # State flags independently form a conservative envelope. These numeric
    # formatter samples and boolean combinations are not gameplay bounds.
    for number, armor, normal, talisman, disabled in product(numbers, (False, True), (False, True), (False, True), (False, True)):
        cases = {}
        for model in range(3):
            r = rendered(model, number)

            def rows(*calls):
                covered.update(call for call in calls if isinstance(call, int) and call > 100)
                return [r[call] if isinstance(call, int) and call > 100 else call for call in calls]

            tail = rows(6201) if normal else []
            tail += rows(6287) if talisman else []
            cases.setdefault('GemSeal', []).append(panel(
                rows(5908, 5, 5984, 6024) + [5] + tail + rows(5, 6321) if armor else
                rows(5908, 5, 6066, 5) + tail + rows(5, 6321), 280, model))
            for shell in (False, True):
                calls = (7594, 7632) if shell else (7909,)
                mana = rows(7518, 5, *calls)
                mana += rows(7747 if shell else 8024) if normal else []
                mana += rows(7833 if shell else 8110) if talisman else []
                mana += rows(5, 7867 if shell else 8144)
                cases.setdefault(f'ManaShard:shell={shell}', []).append(panel(mana, 320, model))
            corrupted = rows(8486, 5, 8520, 8534)
            corrupted += rows(8620) if talisman else []
            cases.setdefault('CorruptedManaShard', []).append(panel(corrupted + rows(5, 8654), 320, model))
            for name, title, detail in (('PossessionObelisk', 8742, 8783),
                                        ('SleepingHive', 8868, 8909), ('ScytheGate', 9007, 9048)):
                parts = rows(title, 5, detail)
                if name == 'SleepingHive':
                    parts += rows(8923)
                cases.setdefault(name, []).append(panel(parts, 320, model))
            # Two actual ternary arms; never concatenate both instructions.
            for singular in (False, True):
                value = text(8329, model) + (text(8370, model) if singular else
                        text(8348, model) + number + text(8361, model)) + text(8375, model)
                for holder in sorted(HOLDER_OFFSETS):
                    title = (text(holder, model, 1427), 13, -3)
                    cases.setdefault(f'DropHolder:{holder}:single={singular}', []).append(panel(
                        [title] + rows(8298, 5) + [(value, 12, 9)], 320, model))
            dwelling = text(9199, model) + number + text(9211, model)
            cases.setdefault('Dwelling', []).append(panel(rows(9168, 5) +
                                                         [(dwelling, 12, 9)], 320, model))
            for title, detail, no_range in ((9361, 9402, False), (9424, 9465, False),
                    (9487, 9528, True), (9567, 9608, False), (9630, 9671, False),
                    (9693, 9734, False), (9756, 9797, True)):
                beacon = rows(title, 5, detail, 5, 10141, 10181) if armor else rows(title, 5, detail, 5, 10251)
                beacon += [12] if no_range else rows(10339)
                beacon += rows(5, 10407) if disabled else []
                beacon += rows(5, 10542) if normal else []
                beacon += rows(10628) if talisman else []
                beacon += rows(5, 10662)
                cases.setdefault(f'Beacon:{title}', []).append(panel(beacon, 320, model))
        for name, panels in cases.items():
            assert len(panels) == 3
            scales = []
            for zoom in (1, .75, .5):
                factor = (1 + zoom) / (2 * zoom)
                heights = [p['height'] * factor for p in panels]
                width = 280 if name == 'GemSeal' else 320
                assert width * factor <= 1068
                for threshold in (544, 602):
                    assert not heights[1] <= threshold < heights[2], (name, number, heights, threshold)
                scales.append({'zoom': zoom, 'heights': heights,
                               'candidate_above_preferred': heights[2] > 544,
                               'candidate_above_viewport': heights[2] > 602})
            evidence.append({'object': name, 'number': number, 'armor': armor,
                             'normal': normal, 'talisman': talisman, 'disabled': disabled,
                             'models': panels, 'scales': scales})
    # Every literal renderer site plus the dynamic holder name, ternary and
    # dwelling expressions are represented by this model.
    bound_calls = {at for at, op, args in ops[630] if op == 0x4f and args[0] == 3729
                   and (5855 <= at < 6339 or 7454 <= at < 10940)}
    assert covered | {8281, 8386, 9222} == bound_calls
    (root / 'special-objects-geometry.json').write_text(json.dumps({
        'candidate_sha256': sha(candidate), 'panels': evidence,
        'limitations': 'Static SWF outlines and line boxes; samples are not numeric gameplay bounds. '
        'Independent flags conservatively include combinations whose reachability is unproven. '
        'AIR wrapping, autoSize, rasterization, effects, screen placement and clipping remain unverified. '
        '544px is preferred space below HUD; 602px viewport is a height envelope, not placement proof.'
    }, ensure_ascii=False) + '\n')
