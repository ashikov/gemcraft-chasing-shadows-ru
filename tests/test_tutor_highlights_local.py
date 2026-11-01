"""Independent offline checks of the actual McTutorPanel artwork and glyphs.

Requires owned SWFs through GEMCRAFT_ORIGINAL, GEMCRAFT_PR37_BASELINE and
GEMCRAFT_PR37_CANDIDATE. Does not import the repair or launch the game.
"""
import json
import os
from pathlib import Path

import pytest

from install_patch import ORIGINAL_SHA256, sha
from swf_geometry import (
    Bits, Reader, decode_matrix, decode_rect, parse_font, parse_shape, parse_text,
    read_swf, read_tags,
)

pytestmark = pytest.mark.local
RECIPE = json.loads(Path('translations/plans/pr05-tutor.json').read_text())['tutor_highlights']
SHAPES = RECIPE['shapes']
COLORS = {bytes.fromhex(s) for s in ('ffff664c', '00ff964c', '0096ff33', '0095ff33')}


@pytest.fixture(scope='module')
def swfs():
    names = ('GEMCRAFT_ORIGINAL', 'GEMCRAFT_PR37_BASELINE', 'GEMCRAFT_PR37_CANDIDATE')
    if not all(os.environ.get(name) for name in names):
        pytest.skip('requires original, PR36 baseline and PR37 candidate SWFs')
    raw = [Path(os.environ[name]).read_bytes() for name in names]
    assert sha(raw[0]) == ORIGINAL_SHA256
    assert sha(raw[1]) == 'd7644a2d1806eb6ba738f0d011b36000b30b90e7315f028a9013147db78fbc8b'
    return [read_swf(data) for data in raw]


def resources(tags):
    return {int.from_bytes(tag.payload[:2], 'little'): tag for tag in tags
            if tag.code in (11, 32, 39, 75, 83)}


def placement(tag):
    if tag.code == 4:
        return int.from_bytes(tag.payload[:2], 'little'), decode_matrix(tag.payload[4:])[0]
    if tag.code not in (26, 70) or not tag.payload[0] & 2:
        return None
    offset = 3 if tag.code == 26 else 4
    if tag.code == 70 and tag.payload[1] & 8:
        offset = tag.payload.index(b'\0', offset) + 1
    cid = int.from_bytes(tag.payload[offset:offset + 2], 'little')
    if tag.payload[0] & 4:
        matrix = decode_matrix(tag.payload[offset + 2:])[0]
    else:
        matrix = {'scale': [65536, 65536], 'rotate': [0, 0], 'translate': [0, 0]}
    return cid, matrix


def panel_placements(tags):
    panel = resources(tags)[3608]
    result = {}
    for tag in read_tags(panel.payload[4:]):
        placed = placement(tag)
        if placed:
            cid, matrix = placed
            if cid in {s['character_id'] for s in SHAPES} | {
                    area['text_id'] for s in SHAPES for area in s['areas']}:
                assert cid not in result
            result[cid] = matrix
    return result


def transform(point, matrix):
    x, y = point
    sx, sy = matrix['scale']
    r0, r1 = matrix['rotate']
    tx, ty = matrix['translate']
    return ((x * sx + y * r1) / 65536 + tx,
            (x * r0 + y * sy) / 65536 + ty)


def glyph_rows(tags, cid):
    """Resolve encoded pen advances and contour hulls, independently of the repair."""
    chars = resources(tags)
    fonts = {i: parse_font(tag.payload) for i, tag in chars.items() if tag.code == 75}
    text = parse_text(chars[cid].payload)
    tm = decode_matrix(text['matrix_raw'])[0]
    pm = panel_placements(tags)[cid]
    x = y = 0
    active_font = height = None
    rows = []
    for row in text['records']:
        active_font = row.get('font_id', active_font)
        height = row.get('height', height)
        x, y = row.get('x', x), row.get('y', y)
        font = fonts[active_font]
        glyphs = []
        for index, advance in row['entries']:
            outlines = parse_shape(font['glyphs'][index])
            points = [transform(transform((x + segment[j] * height / 20480,
                                          y + segment[j + 1] * height / 20480), tm), pm)
                      for path in outlines for segment in path
                      for j in range(1, len(segment), 2)]
            bounds = None
            if points:
                xs, ys = zip(*points)
                bounds = [min(xs), max(xs), min(ys), max(ys)]
            glyphs.append((chr(font['codes'][index]), bounds))
            x += advance
        rows.append(glyphs)
    return rows


def style_tail(tag, shape):
    """Read final style tables, accounting for a possibly resized header RECT."""
    _, old_size = decode_rect(resources_original_payload(shape)[2:])
    _, new_size = decode_rect(tag.payload[2:])
    offset = shape['style_offset'] + new_size - old_size
    if tag.code == 83:
        _, old_edge = decode_rect(resources_original_payload(shape)[2 + old_size:])
        _, new_edge = decode_rect(tag.payload[2 + new_size:])
        offset += new_edge - old_edge
    reader = Reader(tag.payload)
    reader.pos = offset
    fills = []
    for _ in range(reader.u8()):
        assert reader.u8() == 0
        fills.append(reader.take(4))
    line_count = reader.u8()
    lines = reader.take(line_count * (8 if tag.code == 83 else 6))
    return offset, reader.pos, fills, lines, line_count


_ORIGINAL_PAYLOADS = {}


def resources_original_payload(shape):
    # Populated only after the fixture has verified the original's complete SHA.
    return _ORIGINAL_PAYLOADS[shape['character_id']]


def colored_edges(tag, shape, matrix):
    """Assign fills per edge, including original shared-edge topology."""
    offset, start, fills, lines, line_count = style_tail(tag, shape)
    bits = Bits(tag.payload[start:])
    fill_bits, line_bits = bits.get(4), bits.get(4)
    fill0 = fill1 = line = 0
    x = y = 0
    edges = []
    while True:
        if bits.get(1):
            assert bits.get(1) == 1, 'highlight curves were not expected'
            count = bits.get(4) + 2
            if bits.get(1):
                dx, dy = bits.signed(count), bits.signed(count)
            elif bits.get(1):
                dx, dy = 0, bits.signed(count)
            else:
                dx, dy = bits.signed(count), 0
            a, b = transform((x, y), matrix), transform((x + dx, y + dy), matrix)
            if fill0:
                edges.append((fill0, b, a))
            if fill1:
                edges.append((fill1, a, b))
            x, y = x + dx, y + dy
        else:
            flags = bits.get(5)
            if not flags:
                break
            assert not flags & 16, 'final block must not contain further style tables'
            if flags & 1:
                count = bits.get(5)
                x, y = bits.signed(count), bits.signed(count)
            if flags & 2:
                fill0 = bits.get(fill_bits)
            if flags & 4:
                fill1 = bits.get(fill_bits)
            if flags & 8:
                line = bits.get(line_bits)
            assert 0 <= fill0 <= len(fills) and 0 <= fill1 <= len(fills)
            assert 0 <= line <= line_count
    assert bits.size == len(tag.payload) - start
    assert bits.get(bits.size * 8 - bits.pos) == 0
    return edges, (offset, start, fills, lines)


def filled_intervals(edges, fill, y):
    intersections = []
    for f, (x0, y0), (x1, y1) in edges:
        if f == fill and min(y0, y1) <= y < max(y0, y1):
            intersections.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
    intersections.sort()
    assert len(intersections) % 2 == 0
    return list(zip(intersections[::2], intersections[1::2]))


def covered(edges, fill, rect):
    x0, x1, y0, y1 = rect
    cuts = sorted({y0, y1} | {p[1] for _, a, b in edges for p in (a, b)
                             if y0 < p[1] < y1})
    return all(any(left <= x0 and x1 <= right
                   for left, right in filled_intervals(edges, fill, (lo + hi) / 2))
               for lo, hi in zip(cuts, cuts[1:]))


def intersects(edges, fill, rect):
    x0, x1, y0, y1 = rect
    cuts = sorted({y0, y1} | {p[1] for _, a, b in edges for p in (a, b)
                             if y0 < p[1] < y1})
    return any(any(max(left, x0) < min(right, x1)
                   for left, right in filled_intervals(edges, fill, (lo + hi) / 2))
               for lo, hi in zip(cuts, cuts[1:]))


def selected_glyphs(rows, spec):
    row_index, phrase = spec
    row = rows[row_index]
    text = ''.join(char for char, _ in row)
    assert text.count(phrase) == 1, (row_index, phrase, text)
    start = text.index(phrase)
    return row_index, range(start, start + len(phrase))


def setup_original(swfs):
    tags = swfs[0][1]
    chars = resources(tags)
    for shape in SHAPES:
        _ORIGINAL_PAYLOADS[shape['character_id']] = chars[shape['character_id']].payload
    return tags


@pytest.mark.parametrize('shape', SHAPES, ids=lambda s: str(s['character_id']))
def test_all_actual_tutorial_spans_and_neighbors(swfs, shape):
    setup_original(swfs)
    tags = swfs[2][1]
    chars = resources(tags)
    cid = shape['character_id']
    edges, (_, _, fills, lines) = colored_edges(chars[cid], shape, panel_placements(tags)[cid])
    assert set(fills) <= COLORS
    stroke = int.from_bytes(lines[:2], 'little') / 2 if lines else 0
    selected = {}
    for area in shape['areas']:
        text_id = area['text_id']
        rows = glyph_rows(tags, text_id)
        for spec in area['target']:
            row_index, indices = selected_glyphs(rows, spec)
            for index in indices:
                selected.setdefault((text_id, row_index, index), set()).add(area['fill'])
                bounds = rows[row_index][index][1]
                if bounds is None:
                    continue
                for scale in (1, .75, .5):
                    # A positive inset is checked against filled geometry itself,
                    # not bounds/coordinates reported by the repair.
                    rect = [bounds[0] - .25, bounds[1] + .25,
                            bounds[2] - .25, bounds[3] + .25]
                    scaled_edges = [(f, (a[0] * scale, a[1] * scale),
                                     (b[0] * scale, b[1] * scale)) for f, a, b in edges]
                    assert covered(scaled_edges, area['fill'], [v * scale for v in rect]), (
                        cid, text_id, spec, index, bounds, scale)
    for text_id in {area['text_id'] for area in shape['areas']}:
        for row_index, row in enumerate(glyph_rows(tags, text_id)):
            for index, (char, bounds) in enumerate(row):
                if bounds is None:
                    continue
                allowed = selected.get((text_id, row_index, index), set())
                rect = [bounds[0] - stroke, bounds[1] + stroke,
                        bounds[2] - stroke, bounds[3] + stroke]
                for fill in range(1, len(fills) + 1):
                    if fill not in allowed:
                        assert not intersects(edges, fill, rect), (
                            'unintended glyph overlap', cid, text_id, row_index, char, index, fill)


def test_original_source_regions_styles_and_artwork_are_preserved(swfs):
    original = setup_original(swfs)
    candidate = swfs[2][1]
    old_chars, new_chars = resources(original), resources(candidate)
    old_places, new_places = panel_placements(original), panel_placements(candidate)
    for shape in SHAPES:
        cid = shape['character_id']
        old, new = old_chars[cid], new_chars[cid]
        old_edges, old_style = colored_edges(old, shape, old_places[cid])
        _, new_style = colored_edges(new, shape, new_places[cid])
        old_off, old_start, old_fills, old_lines = old_style
        new_off, new_start, new_fills, new_lines = new_style
        assert old_fills == new_fills and old_lines == new_lines
        assert old.payload[old_off:old_start] == new.payload[new_off:new_start]
        _, old_header = decode_rect(old.payload[2:])
        _, new_header = decode_rect(new.payload[2:])
        if old.code == 83:
            _, size = decode_rect(old.payload[2 + old_header:])
            old_header += size
            _, size = decode_rect(new.payload[2 + new_header:])
            new_header += size
        # Every prior fill/style/edge bit belonging to the illustration remains.
        assert old.payload[2 + old_header:old_off] == new.payload[2 + new_header:new_off]
        assert old_places[cid] == new_places[cid]
        for area in shape['areas']:
            row_index, indices = selected_glyphs(glyph_rows(original, area['text_id']), area['source'])
            assert indices and row_index >= 0
            x0, x1, y0, y1 = area['source_rect']
            # Original Click/N areas overlap. Divide at every source boundary,
            # then allow the original top colour only in that documented overlap.
            xs = sorted({x0, x1} | {a['source_rect'][i] for a in shape['areas']
                                   for i in (0, 1) if x0 < a['source_rect'][i] < x1})
            ys = sorted({y0, y1} | {a['source_rect'][i] for a in shape['areas']
                                   for i in (2, 3) if y0 < a['source_rect'][i] < y1})
            for left, right in zip(xs, xs[1:]):
                for top, bottom in zip(ys, ys[1:]):
                    x, y = (left + right) / 2, (top + bottom) / 2
                    allowed = {a['fill'] for a in shape['areas']
                               if a['source_rect'][0] <= x <= a['source_rect'][1]
                               and a['source_rect'][2] <= y <= a['source_rect'][3]}
                    assert any(covered(old_edges, fill,
                                       [left + .25, right - .25, top + .25, bottom - .25])
                               for fill in allowed), (cid, area['source'], left, top)


def test_only_eleven_graphic_resources_change_from_pr36(swfs):
    original = setup_original(swfs)
    base_prefix, baseline = swfs[1]
    new_prefix, candidate = swfs[2]
    assert base_prefix[:4] == new_prefix[:4]
    assert base_prefix[8:] == new_prefix[8:]
    assert len(baseline) == len(candidate)
    changed = [(a.code, int.from_bytes(a.payload[:2], 'little'))
               for a, b in zip(baseline, candidate) if a != b]
    assert changed == [(shape['tag_code'], shape['character_id']) for shape in SHAPES]
    old_panel = resources(original)[3608]
    new_panel = resources(candidate)[3608]
    assert old_panel == new_panel
    assert int.from_bytes(new_panel.payload[2:4], 'little') == 30
    assert sum(t.code == 1 for t in read_tags(new_panel.payload[4:])) == 30


def test_complete_highlight_coverage_and_exclusive_panel_consumers(swfs):
    original = setup_original(swfs)
    wanted = {shape['character_id'] for shape in SHAPES}
    discovered = {int.from_bytes(t.payload[:2], 'little') for t in original
                  if t.code in (2, 22, 32, 83)
                  and any(color in t.payload for color in COLORS)}
    assert discovered == wanted
    consumers = {cid: [] for cid in wanted}
    frames = {}

    def inspect(tags, owner=None):
        frame = 1
        for tag in tags:
            if tag.code == 1:
                frame += 1
            if tag.code in (56, 76):
                # No linkage/export can instantiate these raw shapes from ABC.
                reader = Reader(tag.payload)
                for _ in range(reader.u16()):
                    assert reader.u16() not in wanted
                    end = reader.data.index(b'\0', reader.pos)
                    reader.take(end + 1 - reader.pos)
                reader.finish()
            placed = placement(tag)
            if placed and placed[0] in wanted:
                consumers[placed[0]].append(owner)
                frames[placed[0]] = frame
            if tag.code == 39:
                inspect(read_tags(tag.payload[4:]), int.from_bytes(tag.payload[:2], 'little'))

    inspect(original)
    assert consumers == {cid: [3608] for cid in wanted}
    assert frames == {shape['character_id']: shape['frame'] for shape in SHAPES}
    assert len(set(frames.values())) == 11
    assert sum(len(shape['areas']) for shape in SHAPES) == 31
    assert sum(len(area['target']) for shape in SHAPES for area in shape['areas']) == 34


def test_pr36_defect_classification_uses_actual_russian_ink(swfs):
    """Containment alone misses five old plates crossing neighbouring ink."""
    setup_original(swfs)
    tags = swfs[1][1]
    outside, spill = [], []
    for shape in SHAPES:
        stroke = 0 if shape['character_id'] == 3583 else 10
        for area in shape['areas']:
            rows = glyph_rows(tags, area['text_id'])
            selected, intended = set(), []
            for spec in area['target']:
                row, indices = selected_glyphs(rows, spec)
                selected.update((row, i) for i in indices)
                # Attached punctuation was included for clean new borders. It
                # must not be the reason for calling the old command defective.
                row, indices = selected_glyphs(rows, [spec[0], spec[1].rstrip('.,')])
                intended.extend(rows[row][i][1] for i in indices if rows[row][i][1] is not None)
            left, right, top, bottom = area['source_rect']
            key = (area['text_id'], tuple(area['source']))
            if any(not (left < b[0] and b[1] < right and top < b[2] and b[3] < bottom)
                   for b in intended):
                outside.append(key)
            elif any(b is not None and (r, i) not in selected
                     and left - stroke < b[1] and b[0] < right + stroke
                     and top - stroke < b[3] and b[2] < bottom + stroke
                     for r, row in enumerate(rows) for i, (_, b) in enumerate(row)):
                spill.append(key)
            else:
                pytest.fail(f'Previously correct plate must be preserved: {key}')
    assert len(outside) == 26
    assert spill == [(3558, (0, 'U')), (3566, (6, 'N')), (3566, (8, 'Ctrl-click')),
                     (3570, (1, 'Gem bomb button')), (3584, (0, 'Drag and drop'))]


def rectangle_paths(edges):
    """Recover separately painted candidate regions from their closed edge loops."""
    result = []
    points = []
    active_fill = None
    for fill, a, b in edges:
        if not points:
            active_fill = fill
            points = [a, b]
        else:
            assert fill == active_fill
            if points[-1] == a:
                points.append(b)
            else:
                assert points[-1] == b
                points.append(a)
        if points[-1] == points[0]:
            xs, ys = zip(*points)
            bounds = [min(xs), max(xs), min(ys), max(ys)]
            x0, x1, y0, y1 = bounds
            assert set(points) == {(x0, y0), (x1, y0), (x1, y1), (x0, y1)}
            result.append((active_fill, bounds))
            points = []
    assert not points
    return result


@pytest.mark.parametrize('shape', SHAPES, ids=lambda s: str(s['character_id']))
def test_candidate_backgrounds_do_not_overlap_each_other(swfs, shape):
    setup_original(swfs)
    tags = swfs[2][1]
    cid = shape['character_id']
    edges, (_, _, _, lines) = colored_edges(
        resources(tags)[cid], shape, panel_placements(tags)[cid])
    radius = int.from_bytes(lines[:2], 'little') / 2 if lines else 0
    boxes = [rect for _, rect in rectangle_paths(edges)]
    for index, first in enumerate(boxes):
        for second in boxes[index + 1:]:
            for scale in (1, .75, .5):
                left = max(first[0] - radius, second[0] - radius) * scale
                right = min(first[1] + radius, second[1] + radius) * scale
                top = max(first[2] - radius, second[2] - radius) * scale
                bottom = min(first[3] + radius, second[3] + radius) * scale
                assert left >= right or top >= bottom, (
                    'background overlap including stroke', cid, first, second, scale)
