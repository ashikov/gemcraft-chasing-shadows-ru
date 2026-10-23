"""Outline/bitmap envelope model, not Flash/AIR visual acceptance."""
import json
from math import ceil

import pytest

from abc_font_binding import _bodies
from localization_fonts import measure_text
from swf_geometry import parse_font, parse_shape, read_swf, shape_bounds
from test_bitmap_floating_texts_local import bitmap_resources as bitmap_resources
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


def outline_font(font):
    # Original ShapeBounds are zero for this decorative face. Decode actual
    # contours rather than treating the font's metadata as visible ink.
    return {**font, 'bounds': [shape_bounds(parse_shape(glyph)) if parse_shape(glyph)
                              else [0, 0, 0, 0] for glyph in font['glyphs']]}


def bitmap_envelope(font, value, size, x, y, *, kerning):
    font = {**font, 'kerning': font['kerning'] if kerning else []}
    measured = measure_text(font, value, size * 40)  # constructor size * 2
    assert len(measured['widths']) == 1 and len(measured['bounds']) == 1
    width = (ceil(measured['widths'][0] / 20 + 4) + 8) / 2
    height = (ceil(measured['line_height'] / 20 + 4) + 8) / 2
    left = max(10, min(1088 - 10 - width, x - width / 2))
    bound = measured['bounds'][0]
    assert bound is not None
    # TextField inset 2 + draw matrix translation 2; then bitmap scale .5.
    baseline = 2 + font['ascent'] * size / 20480
    ink = [left + 2 + bound[0] / 40, left + 2 + bound[1] / 40,
           y + baseline + bound[2] / 40, y + baseline + bound[3] / 40]
    assert left <= ink[0] < ink[1] <= left + width
    assert y <= ink[2] < ink[3] <= y + height
    assert 10 <= left and left + width <= 1078
    return {'text': value, 'size': size, 'bitmap': [left, left + width, y, y + height],
            'ink': ink, 'width': width, 'height': height}


def test_original_bitmap_constants_and_warning_bounds(bitmap_resources):
    _, original, _, _ = bitmap_resources
    abc = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(abc)
    ops = {m: {at: (op, args) for at, op, args in inv.instructions(_bodies(abc)[m][3], m)}
           for m in (597, 713, 1731, 1732, 1733)}
    # Both countdowns are gated by 0<n<6 and use size 20+3*(5-n).
    for first, second, branch in ((373, 383, 386), (667, 677, 680)):
        assert ops[597][first] == (0x24, [0])
        assert ops[597][second] == (0x24, [6])
        assert ops[597][branch] == (0x12, [180])
    for at in (437, 534, 731, 828):
        assert ops[597][at] == (0x24, [20])
    for at in (439, 536, 733, 830):
        assert ops[597][at] == (0x24, [3])
    assert ops[597][428] == ops[597][495] == ops[597][722] == ops[597][789] == (0x24, [15])
    assert ops[597][430] == ops[597][724] == (0xa1, [])
    assert ops[597][497] == ops[597][791] == (0xa0, [])
    # Center 39+918/2=498, y=53+544/2 +/-15. Exclusive branches
    # bypass the second warning pair after handling the wizard tower.
    assert ops[597][412] == (0x25, [918])
    assert ops[597][421] == (0x25, [544])
    assert ops[597][604] == (0x10, [537])
    assert ops[713][200] == (0x24, [60])
    assert ops[713][202] == (0xad, [])  # strictly mouseY < 60
    assert ops[713][203] == (0x12, [17])
    assert ops[713][216] == (0x24, [30])
    assert ops[713][233] == (0x24, [20])
    # Bitmap padding, half scale, horizontal clamp; draw matrix translates 2px.
    assert ops[1732][276] == ops[1732][285] == (0x24, [8])
    assert ops[1732][477] == ops[1732][490] == (0x24, [10])
    assert ops[1732][474] == (0x25, [1088])
    assert ops[1731][20] == (0x24, [2])
    assert ops[1731][90] == (0x24, [6])  # glow blur, raster pixels
    # Motion, fade and stop-time code remains the original doEnterFrame body.
    assert ops[1733][189] == (0x2f, [25])
    assert ops[1733][203] == (0x2f, [14])


def test_complete_bitmap_phrases_actual_outlines_scales_and_neighbor_risks(bitmap_resources):
    root, original, baseline, candidate = bitmap_resources
    source_abc = abc_bytes(read_swf(original)[1])
    source_inv = abc_inventory(source_abc)
    source_literals = {method: {at: source_inv.strings[args[0]]
                               for at, op, args in source_inv.instructions(
                                   _bodies(source_abc)[method][3], method) if op == 0x2c}
                       for method in (597, 713)}
    fonts = [{int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
              for tag in read_swf(data)[1] if tag.code == 75} for data in (baseline, candidate)]
    assert fonts[0] == fonts[1]
    source, target = outline_font(fonts[0][255]), outline_font(fonts[1][6002])
    evidence = []
    for kerning in (False, True):
        for count in range(1, 6):
            size = 20 + 3 * (5 - count)
            literals = source_literals[597]
            english_count = (literals[498] + str(count) + literals[503]
                             + literals[515 if count == 1 else 522] + literals[527])
            russian_count = f'...Осталось волн: {count}...'
            for label, english, russian in (
                    ('locks', literals[431], 'Уничтожьте замки башни мага!'),
                    ('nodes', literals[725], 'Зарядите все узлы!')):
                old = [bitmap_envelope(source, value, size, 498, y, kerning=kerning)
                       for value, y in ((english, 310), (english_count, 340))]
                new = [bitmap_envelope(target, value, size, 498, y, kerning=kerning)
                       for value, y in ((russian, 310), (russian_count, 340))]
                gap = new[1]['ink'][2] - new[0]['ink'][3]
                old_gap = old[1]['ink'][2] - old[0]['ink'][3]
                assert gap > 0 and gap >= old_gap  # ink separation improves
                assert gap - 3 >= -1.7  # two blur radii, modelled conservatively
                for scale in (1, .75, .5):
                    for tick in range(58):  # frame58 reaches alpha=0 and is removed
                        moving_ticks = min(tick, 15)
                        dy = moving_ticks * -1.2 + .1 * moving_ticks * (moving_ticks - 1) / 2
                        for row in new:
                            ink = row['ink']
                            assert 0 <= ink[0] * scale < ink[1] * scale <= 1088 * scale
                            assert 0 <= (ink[2] + dy) * scale < (ink[3] + dy) * scale <= 612 * scale
                    evidence.append({'case': label, 'count': count, 'size': size,
                                     'scale': scale, 'kerning': kerning, 'fields': new,
                                     'ink_gap': gap * scale, 'glow_gap': (gap - 3) * scale,
                                     'baseline_ink_gap': old_gap * scale})
    # Mouse notifications retain the source's lack of a vertical motion clamp.
    # Test all viewport edges and fast/slow initial drift while visible; record
    # an existing top-edge risk instead of declaring all placements safe.
    edge_risks = []
    for mouse_y in (0, 30, 59, 60, 61, 306, 590, 612):
        y = mouse_y + 30 if mouse_y < 60 else mouse_y - 20
        for mouse_x in (0, 10, 544, 1078, 1088):
            for speed_y in (-7, -4):
                new = bitmap_envelope(target, 'Не хватает маны', 12, mouse_x, y, kerning=False)
                old = bitmap_envelope(source, source_literals[713][237], 12, mouse_x, y, kerning=False)
                for tick in range(15):  # frame15 reaches alpha=0 and is removed
                    dy = speed_y * tick + .55 * tick * (tick - 1) / 2
                    new_min, new_max = new['ink'][2] + dy, new['ink'][3] + dy
                    old_min, old_max = old['ink'][2] + dy, old['ink'][3] + dy
                    assert new_min >= old_min and new_max <= old_max
                    if new_min < 0 or new_max > 612:
                        edge_risks.append({'mouse': [mouse_x, mouse_y], 'tick': tick,
                                           'new_vertical': [new_min, new_max],
                                           'original_vertical': [old_min, old_max]})
    assert edge_risks  # inherited renderer risk is explicit, not hidden
    (root / 'bitmap-geometry.json').write_text(json.dumps({
        'model': 'SWF outlines; both kerning states; 2x raster; .5 bitmap scale; no AIR',
        'warnings': evidence, 'inherited_mouse_edge_risks': edge_risks,
        'runtime_risks': ['possible glow blending at nodes n=1; max 1.7px at scale1',
                          'subpixel rasterization at .75/.5 and shared TextField switching',
                          'independent notifications may overlap; no global collision manager']},
        ensure_ascii=False, indent=2) + '\n')
    # Empty fragments and boundaries remain valid in the actual embedded face.
    rows = {row['id']: row['translation'] for row in load_catalog(root / 'candidate/workspace.json')['entries']}
    assert all(ord(char) in target['codes'] for count in (0, 1, 2, 5, 6, 21)
               for char in f'...Осталось волн: {count}...')
    assert rows['abc.4175.597.503'] == rows['abc.4175.597.797'] == ''
