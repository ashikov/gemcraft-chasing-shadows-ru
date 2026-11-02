"""Retarget McTutorPanel's guarded static highlight layers to translated ink.

Only the final style group of the eleven selected shapes is rewritten. Earlier
shape records contain illustrations and retain their original byte encoding.
"""
import math

from install_patch import sha
from swf_geometry import (
    BitWriter, Tag, decode_matrix, decode_rect, parse_font,
    parse_shape, parse_text, read_tags, shape_bounds, signed_bits,
)

SHAPES = {3525: 3711, 3553: 3758, 3556: 3763, 3565: 3778,
          3568: 3783, 3580: 3803, 3583: 3808, 3587: 3814,
          3592: 3821, 3595: 3826, 3598: 3831}
COLORS = {b'\xff\xff\x66\x4c', b'\x00\xff\x96\x4c',
          b'\x00\x96\xff\x33', b'\x00\x95\xff\x33'}
AREA_COUNTS = (5, 3, 4, 3, 6, 2, 1, 1, 3, 1, 2)
FRAMES = (9, 18, 19, 21, 22, 24, 25, 26, 27, 28, 29)


def validate_recipe(recipe):
    def digest(value):
        return (isinstance(value, str) and len(value) == 64
                and all(c in '0123456789abcdef' for c in value))

    def span(value):
        return (isinstance(value, list) and len(value) == 2
                and type(value[0]) is int and value[0] >= 0
                and isinstance(value[1], str) and bool(value[1])
                and value[1] == value[1].strip())

    if (not isinstance(recipe, dict)
            or set(recipe) != {'sprite_tag', 'sprite_sha256', 'shapes'}
            or recipe.get('sprite_tag') != 3848 or not digest(recipe.get('sprite_sha256'))
            or not isinstance(recipe.get('shapes'), list)
            or len(recipe['shapes']) != len(SHAPES)):
        raise ValueError('Invalid tutor highlight selection')
    sources, targets = set(), set()
    for item, (cid, index), count, frame in zip(recipe['shapes'], SHAPES.items(), AREA_COUNTS, FRAMES):
        if (not isinstance(item, dict) or set(item) != {
                'character_id', 'tag_index', 'tag_code', 'sha256', 'style_offset', 'frame', 'areas'}
                or item['character_id'] != cid or item['tag_index'] != index
                or item['tag_code'] != (83 if cid == 3525 else 32)
                or not digest(item['sha256']) or type(item['style_offset']) is not int
                or item['style_offset'] < 2 or item['frame'] != frame
                or not isinstance(item['areas'], list) or len(item['areas']) != count):
            raise ValueError('Invalid tutor highlight shape recipe')
        for area in item['areas']:
            if (not isinstance(area, dict) or set(area) != {
                    'text_id', 'text_tag', 'text_sha256', 'source', 'fill', 'source_rect', 'target'}
                    or type(area['text_id']) is not int or type(area['text_tag']) is not int
                    or area['text_id'] < 0 or area['text_tag'] < 0
                    or not digest(area['text_sha256']) or not span(area['source'])
                    or type(area['fill']) is not int or not 1 <= area['fill'] <= 4
                    or not isinstance(area['source_rect'], list) or len(area['source_rect']) != 4
                    or any(type(v) is not int for v in area['source_rect'])
                    or area['source_rect'][0] >= area['source_rect'][1]
                    or area['source_rect'][2] >= area['source_rect'][3]
                    or not isinstance(area['target'], list) or not area['target']
                    or any(not span(s) for s in area['target'])):
                raise ValueError('Invalid tutor highlight area recipe')
            key = (area['text_id'], *area['source'])
            if key in sources:
                raise ValueError('Duplicate tutor source span')
            sources.add(key)
            for target in area['target']:
                key = (area['text_id'], *target)
                if key in targets:
                    raise ValueError('Duplicate tutor target span')
                targets.add(key)
    if len(targets) != 34:
        raise ValueError('Incomplete tutor highlight target selection')


def _guard(tags, index, code, character, digest):
    if type(index) is not int or not 0 <= index < len(tags):
        raise ValueError('Tutor highlight address mismatch')
    tag = tags[index]
    if (tag.code != code or int.from_bytes(tag.payload[:2], 'little') != character
            or sha(tag.payload) != digest):
        raise ValueError('Tutor highlight resource guard mismatch')
    return tag


def _placements(sprite, selected):
    result, depths, frame = {}, {}, 1
    for tag in read_tags(sprite.payload[4:]):
        if tag.code == 1:
            frame += 1
        elif tag.code == 28:
            depths.pop(int.from_bytes(tag.payload, 'little'), None)
        elif tag.code == 26:
            flags = tag.payload[0]
            depth = int.from_bytes(tag.payload[1:3], 'little')
            previous = depths.get(depth) if flags & 1 else None
            pos = 3
            cid = previous[0] if previous else None
            matrix = previous[1] if previous else {
                'scale': [65536, 65536], 'rotate': [0, 0], 'translate': [0, 0]}
            if flags & 2:
                cid = int.from_bytes(tag.payload[pos:pos + 2], 'little')
                pos += 2
            if flags & 4:
                matrix, _ = decode_matrix(tag.payload[pos:])
            depths[depth] = (cid, matrix)
            if flags & 2 and cid in selected:
                if cid in result:
                    raise ValueError('Repeated tutor character placement')
                result[cid] = (frame, matrix)
    if frame != 31 or int.from_bytes(sprite.payload[2:4], 'little') != 30:
        raise ValueError('Unexpected tutor frame count')
    return result


def _transform(bounds, matrix):
    if matrix['rotate'] != [0, 0] or min(matrix['scale']) <= 0:
        raise ValueError('Unsupported tutor highlight transform')
    sx, sy = [v / 65536 for v in matrix['scale']]
    tx, ty = matrix['translate']
    return [bounds[0] * sx + tx, bounds[1] * sx + tx,
            bounds[2] * sy + ty, bounds[3] * sy + ty]


def text_rows(tag, fonts, placement):
    """Resolve inherited pen/font state, encoded advances and actual outlines."""
    text = parse_text(tag.payload, tag.code)
    matrix, _ = decode_matrix(text['matrix_raw'])
    font = height = None
    x = y = 0
    rows = []
    for record in text['records']:
        font = record.get('font_id', font)
        height = record.get('height', height)
        x, y = record.get('x', x), record.get('y', y)
        face, glyphs = fonts[font], []
        for index, advance in record['entries']:
            contour = parse_shape(face['glyphs'][index])
            bounds = None
            if contour:
                local = shape_bounds(contour)
                bounds = [x + local[0] * height / 20480,
                          x + local[1] * height / 20480,
                          y + local[2] * height / 20480,
                          y + local[3] * height / 20480]
                bounds = _transform(_transform(bounds, matrix), placement)
            glyphs.append((chr(face['codes'][index]), bounds))
            x += advance
        rows.append(glyphs)
    return rows


def span_ink(rows, span):
    row, phrase = span
    if (type(row) is not int or not 0 <= row < len(rows)
            or not isinstance(phrase, str) or not phrase or phrase != phrase.strip()):
        raise ValueError('Invalid tutor highlight span')
    glyphs = rows[row]
    value = ''.join(g[0] for g in glyphs)
    if value.count(phrase) != 1:
        raise ValueError('Tutor highlight text span mismatch')
    start = value.index(phrase)
    selected = [b for _, b in glyphs[start:start + len(phrase)] if b is not None]
    if not selected:
        raise ValueError('Empty tutor highlight ink')
    bounds = [min(b[0] for b in selected), max(b[1] for b in selected),
              min(b[2] for b in selected), max(b[3] for b in selected)]
    others = [b for i, (_, b) in enumerate(glyphs)
              if b is not None and not start <= i < start + len(phrase)]
    return bounds, others


def retarget_rectangle(source, old_ink, new_ink, others, stroke):
    """Keep source padding, limiting it to half the free gap to adjacent ink.

    Padding follows glyph contours rather than advance cells. Half-gap clipping
    leaves room for both the original stroke and the neighbouring word's ink.
    """
    padding = [old_ink[0] - source[0], source[1] - old_ink[1],
               old_ink[2] - source[2], source[3] - old_ink[3]]
    if min(padding) <= 0:
        raise ValueError('Source tutor highlight does not enclose its span')
    # Alegreya's descenders can approach the old lower edge much more closely
    # than the source font. Keep at least one stroke width of internal padding.
    padding = [max(p, stroke) for p in padding]
    left, right, top, bottom = new_ink
    for other in others:
        if other[2] < bottom and other[3] > top:
            if other[1] <= left:
                padding[0] = min(padding[0], (left - other[1]) / 2)
            elif other[0] >= right:
                padding[1] = min(padding[1], (other[0] - right) / 2)
            else:
                raise ValueError('Overlapping tutor text spans')
    if min(padding) <= stroke / 2 + 1:
        raise ValueError('Insufficient tutor highlight padding')
    rect = [math.floor(left - padding[0]), math.ceil(right + padding[1]),
            math.floor(top - padding[2]), math.ceil(bottom + padding[3])]
    # Include neighbouring rows too: long descenders/accents can reach them.
    for other in others:
        if (other[0] < rect[1] + stroke / 2 and other[1] > rect[0] - stroke / 2
                and other[2] < rect[3] + stroke / 2 and other[3] > rect[2] - stroke / 2):
            raise ValueError('Tutor highlight overlaps neighbouring ink')
    return rect


def _style_table(tag, offset):
    payload = tag.payload
    if type(offset) is not int or not 0 <= offset < len(payload):
        raise ValueError('Unexpected tutor highlight style address')
    count = payload[offset]
    if count not in (1, 2, 4):
        raise ValueError('Unexpected tutor highlight fill count')
    pos = offset + 1
    for _ in range(count):
        if payload[pos] != 0 or payload[pos + 1:pos + 5] not in COLORS:
            raise ValueError('Unexpected tutor highlight fill style')
        pos += 5
    lines = payload[pos]
    pos += 1
    expected = b'\x14\0' + (b'\0\0' if tag.code == 83 else b'') + b'\0\0\0\x33'
    if lines not in (0, 1) or lines and payload[pos:pos + len(expected)] != expected:
        raise ValueError('Unexpected tutor highlight line style')
    pos += lines * len(expected)
    return payload[offset:pos], count, 20 if lines else 0


def rectangle_records(rectangles, fills, stroke):
    """Explicit independent polygons avoid the source's shared-edge topology."""
    bits = BitWriter()
    fb, lb = fills.bit_length(), int(bool(stroke))
    bits.put(fb, 4)
    bits.put(lb, 4)
    for fill, (left, right, top, bottom) in rectangles:
        if not 1 <= fill <= fills or not left < right or not top < bottom:
            raise ValueError('Invalid tutor highlight rectangle')
        bits.put(0, 1)
        bits.put(15 if stroke else 7, 5)
        size = signed_bits([left, top])
        bits.put(size, 5)
        bits.signed(left, size)
        bits.signed(top, size)
        bits.put(0, fb)
        bits.put(fill, fb)
        if stroke:
            bits.put(1, lb)
        for dx, dy in ((right - left, 0), (0, bottom - top),
                       (left - right, 0), (0, top - bottom)):
            size = max(2, signed_bits([dx, dy]))
            if size > 17:
                raise ValueError('Tutor rectangle exceeds edge range')
            bits.put(1, 1)
            bits.put(1, 1)
            bits.put(size - 2, 4)
            bits.put(1, 1)
            bits.signed(dx, size)
            bits.signed(dy, size)
    bits.put(0, 6)
    return bits.bytes()


def separate_rectangles(regions, stroke):
    """Split padding in the free ink gap, keeping even the borders separate."""
    for index, (_, first, ink_a) in enumerate(regions):
        for _, second, ink_b in regions[index + 1:]:
            if not (first[0] < second[1] + stroke and second[0] < first[1] + stroke
                    and first[2] < second[3] + stroke and second[2] < first[3] + stroke):
                continue
            gaps = []
            for axis in (0, 2):
                if ink_a[axis + 1] <= ink_b[axis]:
                    gaps.append((ink_b[axis] - ink_a[axis + 1], axis,
                                 first, second, ink_a[axis + 1], ink_b[axis]))
                elif ink_b[axis + 1] <= ink_a[axis]:
                    gaps.append((ink_a[axis] - ink_b[axis + 1], axis,
                                 second, first, ink_b[axis + 1], ink_a[axis]))
            if not gaps:
                raise ValueError('Intersecting tutor highlight ink')
            _, axis, low, high, end, start = max(gaps, key=lambda item: item[0])
            midpoint = (end + start) / 2
            low[axis + 1] = min(low[axis + 1], math.floor(midpoint - stroke / 2 - 1))
            high[axis] = max(high[axis], math.ceil(midpoint + stroke / 2 + 1))
    for _, rect, ink in regions:
        if min(ink[0] - rect[0], rect[1] - ink[1],
               ink[2] - rect[2], rect[3] - ink[3]) <= stroke / 2:
            raise ValueError('Insufficient clearance between tutor highlights')


def repair_tutor_highlights(original, tags, fonts, recipe):
    validate_recipe(recipe)
    sprite = _guard(original, recipe['sprite_tag'], 39, 3608, recipe['sprite_sha256'])
    if tags[recipe['sprite_tag']] != sprite:
        raise ValueError('Tutor timeline changed before highlight repair')
    selected = set(SHAPES) | {a['text_id'] for s in recipe['shapes'] for a in s['areas']}
    placements = _placements(sprite, selected)
    source_fonts = {int.from_bytes(t.payload[:2], 'little'): parse_font(t.payload)
                    for t in original if t.code == 75}
    pending, report = {}, []
    for item in recipe['shapes']:
        cid, index = item['character_id'], item['tag_index']
        old = _guard(original, index, item['tag_code'], cid, item['sha256'])
        if tags[index] != old or placements[cid] != (item['frame'], {
                'scale': [65536, 65536], 'rotate': [0, 0], 'translate': [0, 0]}):
            raise ValueError('Unexpected tutor background placement')
        table, fills, stroke = _style_table(old, item['style_offset'])
        rectangles, regions, areas = [], [], []
        for area in item['areas']:
            text_id, ti = area['text_id'], area['text_tag']
            text = _guard(original, ti, 11, text_id, area['text_sha256'])
            if placements[text_id][0] != item['frame']:
                raise ValueError('Tutor text/background frame mismatch')
            placement = placements[text_id][1]
            old_rows = text_rows(text, source_fonts, placement)
            new_rows = text_rows(tags[ti], fonts, placement)
            old_ink, _ = span_ink(old_rows, area['source'])
            target_rects = []
            for target in area['target']:
                ink, neighbours = span_ink(new_rows, target)
                neighbours += [b for r, row in enumerate(new_rows) if r != target[0]
                               for _, b in row if b is not None]
                try:
                    rect = retarget_rectangle(area['source_rect'], old_ink, ink, neighbours, stroke)
                except ValueError as error:
                    raise ValueError(f'Tutor {text_id} {target}: {error}') from error
                rectangles.append((area['fill'], rect))
                regions.append((area['fill'], rect, ink))
                target_rects.append(rect)
            areas.append({'text_id': text_id, 'source': area['source'], 'target': area['target'],
                          'source_rect': area['source_rect'], 'rectangles': target_rects})
        separate_rectangles(regions, stroke)
        payload = old.payload[:item['style_offset']] + table + rectangle_records(rectangles, fills, stroke)
        # Shape bounds include the original illustrations as well as the new layer.
        bounds, _ = decode_rect(payload[2:])
        rects = [r for _, r in rectangles]
        expanded = [min(bounds[0], min(r[0] for r in rects) - stroke // 2),
                    max(bounds[1], max(r[1] for r in rects) + stroke // 2),
                    min(bounds[2], min(r[2] for r in rects) - stroke // 2),
                    max(bounds[3], max(r[3] for r in rects) + stroke // 2)]
        if expanded != bounds:
            # DefineShape4 carries a second edge RECT before the style records.
            # These guarded source bounds enclose the whole panel already.
            raise ValueError('Tutor highlight exceeds original shape bounds')
        pending[index] = Tag(old.code, payload)
        report.append({'character_id': cid, 'tag_index': index, 'frame': item['frame'],
                       'areas': areas})
    for index, tag in pending.items():
        tags[index] = tag
    return report
