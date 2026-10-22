"""Opt-in shrine outline/viewport models; never launches the game.

These models use SWF glyph outlines and the original ABC layout constants.
They cannot establish AIR wrapping, clipping, filters or final screen placement.
"""
import json
import os
from functools import lru_cache
from itertools import product
from math import ceil
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from addressed_localization import check_layout
from install_patch import ORIGINAL_SHA256, sha
from localization_fonts import measure_text
from swf_geometry import parse_font, read_swf
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local
PLAN = 'translations/plans/pr27-battle-shrines.json'
BASE_SHA = 'ce17870436b5842a9754fc18addeb538b2b45cc4d3ea47afa766b3130a51cf7c'
TYPES = ('BLADES', 'CORROSION', 'ENCHANTMENT', 'ENERGY', 'FOCUS',
         'INFECTION', 'TIME', 'VENOM', 'WISDOM')
TITLE_CALLS = (11136, 11207, 11278, 11349, 11420, 11491, 11562, 11633, 11704)
TYPE_CALLS = (11963, 12001, 12039, 12077, 12115, 12153, 12191, 12229, 12267)
DETAIL_CALLS = (315, 376, 437, 498, 559, 620, 681, 740, 801)
STATUS_CALLS = (13069, 13132, 13173, 13222, 13263, 13322, 13343, 13387, 13433)


@pytest.fixture(scope='module')
def shrine_layout():
    if not os.environ.get('GEMCRAFT_PR27_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR27_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR27_ROOT'])
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
    ops = {method: inv.instructions(body[3], method) for method, body in _bodies(abc).items()
           if method in (630, 695, 780, 782, 784, 786, 791, 3441, 3442, 3445,
                         3446, 3503, 3504, 3506, 3508, 3532, 3535)}
    entries = {row['id']: row for row in load_catalog(root / 'candidate/workspace.json')['entries']}
    return root, candidate, fonts, abc, inv, ops, entries


def test_original_shrine_layout_and_renderer_constants(shrine_layout):
    _, _, _, abc, inv, ops, _ = shrine_layout
    by_at = {method: {at: (op, args) for at, op, args in rows} for method, rows in ops.items()}
    assert by_at[630][11034] == (0x25, [360])
    assert by_at[630][11037] == (0x4f, [3730, 1])
    calls = {at for at, op, args in ops[630]
             if 10990 <= at < 13466 and op == 0x4f and args[0] == 3729}
    assert calls == set(TITLE_CALLS + TYPE_CALLS + STATUS_CALLS + (11912, 11929))
    assert {at for at, op, args in ops[791] if op == 0x4f and args[0] == 3729} == set(
        DETAIL_CALLS + (219, 258, 859))
    # The detailed drag/selected-gem branch replaces the generic description.
    assert by_at[630][11773] == by_at[630][11860] == (0x46, [1410, 2])
    assert by_at[630][11780] == (0x10, [487])
    assert by_at[630][11867] == (0x10, [400])
    assert by_at[630][13087] == (0x0c, [90])  # charge < 1 / >= 1
    assert by_at[630][13272] == (0x46, [1401, 0])  # canActivate
    assert by_at[791][809] == (0x46, [1401, 0])
    # Original shared renderer metrics, compact spacing and raster zoom formula.
    assert method_metadata(abc)[3504][4] == [(10, 10), (6, 3), (12, 12), (2, 3)]
    assert method_metadata(abc)[3506][4] == [(7, 3), (3, 6)]
    for at, operands in ((82, [10]), (95, [8])):
        assert by_at[3506][at] == (0x24, operands)
    for at, operands in ((199, [20]), (846, [253]), (853, [9]), (869, [8])):
        assert by_at[3504][at] == (0x24, operands)
    assert by_at[3508][97] == (0x24, [1])
    assert by_at[3508][105] == (0x93, [])
    assert by_at[3508][108] == (0xa2, [])
    assert by_at[3508][1001] == (0xa3, [])
    # addSeparator records a line location; its -2 argument adds no height.
    assert not [args for _, op, args in ops[3503] if op == 0x61]
    # Four centered, wrapping performance fields: 300px wide, fixed vertical
    # offsets max(90, shrine.y + 10) - (74, 58, 39, 20).
    for width_at in (199, 503, 894, 1186):
        assert by_at[780][width_at] == (0x25, [300])
    for at, size in ((54, 13), (350, 14), (741, 13), (1033, 13)):
        assert by_at[780][at] == (0x24, [size])
    for at, offset in ((228, 74), (532, 58), (923, 39), (1215, 20)):
        assert by_at[780][at] == (0x24, [offset])
    assert {inv.multiname(args[0]) for _, op, args in ops[780] if op == 0x61} >= {
        'wordWrap', 'multiline', 'autoSize', 'embedFonts'}
    # These four TextFields belong directly to Main's overlay, not the
    # zoom-dependent McInfoPanel bitmap used by hover information.
    for at in (1541, 1561, 1581, 1601):
        assert inv.multiname(by_at[782][at][1][0]) == 'cntFloaterPanels'
    assert inv.multiname(by_at[3532][704][1][0]) == 'cntFloaterPanels'
    assert by_at[3532][707] == (0x4f, [5252, 1])


class OutlineModel:
    def __init__(self, font):
        self.font = font

    @lru_cache(maxsize=None)
    def field(self, value, size, width):
        layout = check_layout(self.font, value, {'height': size * 20, 'max_width': width * 20,
                                               'wrap': True, 'max_lines': 100})
        lines = []
        for paragraph in value.split('\n'):
            line = ''
            for word in paragraph.split(' '):
                joined = line + (' ' if line else '') + word
                if measure_text(self.font, joined, size * 20)['widths'][0] > width * 20:
                    assert line, ('unbreakable word', word)
                    lines.append(line)
                    line = word
                else:
                    line = joined
            lines.append(line)
        assert len(lines) == layout['lines']
        measured = measure_text(self.font, '\n'.join(lines), size * 20)
        assert max(measured['widths']) <= width * 20
        bounds = [bound for bound in measured['bounds'] if bound]
        baseline = 2 + self.font['ascent'] * size / 20480
        return {'lines': len(lines), 'height': (len(lines) * measured['line_height'] + 80) / 20,
                'top': baseline + min(bound[2] for bound in bounds) / 20 if bounds else 0,
                'bottom': baseline + max(bound[3] for bound in bounds) / 20 if bounds else 0}

    def panel(self, rows):
        y, previous_bottom, fields = 10., None, []
        for row in rows:
            if isinstance(row, (float, int)):
                y += row
                continue
            value, size, compact = row
            measured = self.field(value, size, 336)
            top, bottom = y + measured['top'], y + measured['bottom']
            assert previous_bottom is None or top >= previous_bottom, (value, previous_bottom, top)
            fields.append({'text': value, 'y': y, **measured})
            previous_bottom = bottom
            y += measured['height'] + (-3 if compact else 9)
        return {'height': y + 8, 'fields': fields}


def test_complete_phrases_exclusive_states_and_scaled_panels(shrine_layout):
    root, candidate, fonts, _, inv, ops, entries = shrine_layout
    # This new scope is still English in PR26, with the unchanged source font.
    # Share the outline cache after proving that baseline premise.
    assert fonts[0][255] == fonts[1][255]
    source_model = OutlineModel(fonts[0][255])
    models = [source_model, source_model, OutlineModel(fonts[2][6002])]
    plan = load_catalog(PLAN)
    for key, layout in plan['layouts'].items():
        check_layout(fonts[2][6002], entries[key]['translation'], layout)

    def text(method, at, model):
        row = entries[f'abc.4175.{method}.{at}']
        return row['translation'] if model == 2 and row['translation'] is not None else row['source']

    # Build complete phrases from actual literal/formatter consumers between
    # calls. No cross-branch concatenation: each renderer site is separate.
    @lru_cache(maxsize=None)
    def rendered(method, model, number):
        result, tokens = {}, []
        for at, op, args in ops[method]:
            if method == 630 and not 11090 <= at < 13466:
                continue
            if op == 0x2c:
                tokens.append(text(method, at, model))
            elif op == 0x46 and inv.multiname(args[0]) in ('format', 'floor'):
                tokens.append(number)
            elif op == 0x4f and args[0] == 3729:
                size = 12 if at in TITLE_CALLS else 10 if at in (11912, 11929, 13173, 13263, 219, 258) else 12 if at in TYPE_CALLS + DETAIL_CALLS else 11
                result[at] = (''.join(tokens), size, at in (13069, 13132, 13222))
                tokens = []
        return result

    evidence = []
    # Includes singular/plural counts, pre-ready / ready / overcharge values,
    # decimals and representative NumberFormatter scientific notation.
    numbers = ('0', '1', '2', '21', '99', '100', '101', '999.9', '1,000', '1.0000000 e12')
    for type_index, number, action, ready, can_activate in product(
            range(9), numbers, ('normal', 'drag', 'cast_selected'), (False, True), (False, True)):
        panels = []
        for model in range(3):
            generic, detailed = rendered(630, model, number), rendered(791, model, number)
            rows = [generic[TITLE_CALLS[type_index]], 5]
            if action == 'normal':
                rows += [generic[11912], generic[11929], generic[TYPE_CALLS[type_index]]]
            else:
                rows += [detailed[219], detailed[258], detailed[DETAIL_CALLS[type_index]]]
                if not can_activate:
                    rows += [5, detailed[859]]
            # m630 only adds this separator if renderInfo returned true.
            if action == 'normal' or can_activate:
                rows += [5]
            rows += [generic[13069], generic[13222 if ready else 13132], generic[13263 if ready else 13173]]
            if ready and can_activate:
                rows += [5, generic[13343 if action == 'normal' else 13322]]
                if TYPES[type_index] != 'ENCHANTMENT':
                    rows += [-10, generic[13387]]
            if action == 'normal':
                rows += [5, generic[13433]]
            panels.append(models[model].panel(rows))
        scales = []
        for zoom in (1, .75, .5):
            factor = (1 + zoom) / (2 * zoom)
            heights = [panel['height'] * factor for panel in panels]
            assert 360 * factor <= 1068
            # A historical overflow is recorded, while a newly crossed
            # preferred-HUD or viewport boundary fails this regression gate.
            for threshold in (544, 602):
                assert not heights[1] <= threshold < heights[2], (
                    TYPES[type_index], number, action, ready, can_activate, zoom, heights, threshold)
            scales.append({'zoom': zoom, 'heights': heights, 'preferred_space': 544, 'viewport': 602,
                           'candidate_exceeds_preferred': heights[2] > 544,
                           'candidate_exceeds_viewport': heights[2] > 602})
        evidence.append({'type': TYPES[type_index], 'number': number, 'action': action,
                         'ready': ready, 'can_activate': can_activate, 'models': panels, 'scales': scales})
    (root / 'shrines-panel-geometry.json').write_text(json.dumps({
        'candidate_sha256': sha(candidate), 'panels': evidence,
        'limitations': 'SWF outline model; numeric samples are not gameplay bounds. '
        'Independent readiness/target states form a conservative envelope, not a reachability proof. '
        'AIR wrapping, rasterization, filters, hover placement and HUD clipping remain unverified.'
    }, ensure_ascii=False, indent=2) + '\n')


def test_performance_neighbors_and_upper_hud_risks(shrine_layout):
    root, candidate, fonts, _, _, _, entries = shrine_layout
    model = OutlineModel(fonts[2][6002])

    def translated(method, offset):
        return entries[f'abc.4175.{method}.{offset}']['translation']

    evidence = []
    # Performance text is enabled by Prefs.displayShrinePerformanceInfo;
    # m786 only updates tf2 for seven shrine types. The other two retain the
    # intentionally empty field; monster effect labels belong to PR26.
    for number, singular in product(('0', '1', '2', '21', '999.9', '1,000', '1.0000000 e12'), (False, True)):
        common = {
            'tf1': (number + translated(784, 622), 13, -74),
            'tf3': (number + translated(784, 513 if singular else 505), 13, -39),
            'tf4': (number + translated(784, 558 if singular else 550), 13, -20),
        }
        for effect, plus in ((115, True), (235, False), (336, True), (422, False), (749, True), (1003, False), (1113, False), (None, False)):
            value = ('+' if plus else '') + number + translated(786, effect) if effect is not None else ''
            fields = {**common, 'tf2': (value, 14, -58)}
            measured = {name: {**model.field(value, size, 296), 'offset': offset, 'text': value}
                        for name, (value, size, offset) in fields.items()}
            overlaps = []
            ordered = ('tf1', 'tf2', 'tf3', 'tf4')
            ordered = tuple(name for name in ordered if measured[name]['text'])
            for left, right in zip(ordered, ordered[1:]):
                bottom = measured[left]['offset'] + measured[left]['bottom']
                top = measured[right]['offset'] + measured[right]['top']
                if bottom > top:
                    overlaps.append({'fields': [left, right], 'overlap': bottom - top})
            assert not overlaps, (number, effect, overlaps)
            # m780's max(90, y+10) floor allows text above the preferred 58px
            # upper HUD even in the original layout. These root-overlay
            # TextFields do not use McInfoPanel's raster/inverse-zoom factor.
            # Do not label this source-preserved risk a visual pass.
            scales = [{'zoom': zoom,
                       'minimum_top': min(90 + row['offset'] + row['top'] for row in measured.values()),
                       'width': 300,
                       'upper_hud': 58, 'viewport_height': 602}
                      for zoom in (1, .75, .5)]
            assert all(row['minimum_top'] >= 0 and row['width'] <= 1068 for row in scales)
            evidence.append({'number': number, 'singular': singular, 'effect_offset': effect,
                             'empty_effect_types': ['INFECTION', 'TIME'] if effect is None else [],
                             'fields': measured, 'scales': scales})
    (root / 'shrines-performance-geometry.json').write_text(json.dumps({
        'candidate_sha256': sha(candidate), 'cases': evidence,
        'limitations': 'Performance fields retain original fixed offsets and may occupy the upper HUD. '
        'No claim of final AIR position, centering, autoSize direction, clipping or filter bounds.'
    }, ensure_ascii=False, indent=2) + '\n')


def test_dragged_bomb_alert_floater_geometry(shrine_layout):
    root, candidate, fonts, _, inv, ops, entries = shrine_layout
    by_at = {method: {at: (op, args) for at, op, args in rows} for method, rows in ops.items()}
    for at, value in ((1410, 12), (1415, 45), (1417, 58)):
        assert by_at[695][at] == (0x24, [value])
    assert by_at[695][1422] == (0x4f, [3841, 8])
    assert inv.strings[by_at[695][1419][1][0]] == 'left'
    # Single-line autoSize floater: 10px left inset, 20px horizontal plate
    # allowance, initial nextTfPos2; remeasure binding uses field.height+5.
    assert by_at[3441][136] == (0x24, [2])
    assert by_at[3441][382] == (0x24, [20])
    for at, value in ((166, 0x27), (174, 0x27)):
        assert by_at[3442][at] == (value, [])
    assert by_at[3442][195] == (0x24, [10])
    assert by_at[3442][237] == (0x24, [20])
    assert by_at[3442][271] == (0x24, [3])
    assert by_at[3442][285] == (0x24, [6])
    # McFloaterPanel raster scale is (1+zoom)/2 and its display scale1/zoom.
    assert by_at[3445][74] == (0x24, [1])
    assert by_at[3445][82] == (0x93, [])
    assert by_at[3445][85] == (0xa2, [])
    assert by_at[3445][755] == (0xa3, [])
    assert by_at[3446][214] == (0x24, [4])
    assert by_at[3446][218] == (0x24, [5])
    assert inv.multiname(by_at[3535][299][1][0]) == 'cntFloaterPanels'
    row = entries['abc.4175.695.1400']
    evidence = []
    for model, (font, value) in enumerate(((fonts[0][255], row['source']),
                                         (fonts[1][255], row['source']),
                                         (fonts[2][6002], row['translation']))):
        check_layout(font, value, {'height': 240, 'max_width': (1068 - 45 - 24) * 20,
                                  'max_lines': 1, 'wrap': False})
        measured = measure_text(font, value, 240)
        width = ceil(measured['widths'][0] / 20 + 4 + 20)
        height = measured['line_height'] / 20 + 4 + 5
        scales = []
        for zoom in (1, .75, .5):
            factor = (1 + zoom) / (2 * zoom)
            assert 45 + width * factor <= 1068
            assert 58 + 20 + height * factor <= 602
            scales.append({'zoom': zoom, 'left': 45, 'settled_top': 58,
                           'initial_top': 78, 'right': 45 + width * factor,
                           'initial_bottom': 78 + height * factor, 'upper_hud': 58})
        evidence.append({'model': model, 'text': value, 'width': width, 'height': height, 'scales': scales})
    (root / 'shrines-alert-geometry.json').write_text(json.dumps({
        'candidate_sha256': sha(candidate), 'models': evidence,
        'limitations': 'Isolated alert only. Existing alerts can stack through Main.addFloaterPanel; '
        'an unbounded alert queue cannot be proved to fit. AIR autoSize, filters, animation and '
        'raster clipping remain unverified.'
    }, ensure_ascii=False, indent=2) + '\n')
