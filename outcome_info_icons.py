"""Guarded outcome-only repair of seven bitmap icons and their hover plates."""
import copy
import struct

from build_prototype import ink_bounds
from install_patch import sha
from swf_geometry import (
    Bits, BitWriter, Tag, decode_matrix, decode_rect, encode_matrix, encode_rect,
    parse_font, parse_text, read_tags, signed_bits,
)

# Rectangle order in DefineShape2 3770, matched to named hover placements in 3787.
PAIRS = ((3735, 46), (3751, 47), (3753, 48), (3759, 49),
         (3761, 50), (3767, 51), (3734, 45))
GUARDS = {4046: (22, 3770, '08f9dc9e8fbfdf686de4056be129d40ddd364aefd63edf7a0b83a5e565faa3b8'),
          4067: (39, 3787, 'f9adc609afeab5bd234a8a71086e39608ed7c81671278c7f6cc1951d9a406da1')}


def _rectangles(payload):
    """Read only the bitmap-rectangle dialect present in this one shape."""
    _, size = decode_rect(payload[2:])
    pos, rectangles = 2 + size, []
    for ordinal in range(7):
        start = pos
        if payload[pos:pos + 4] != b'\x02\x41\xff\xff':
            raise ValueError('Unexpected outcome icon fill styles')
        _, size = decode_matrix(payload[pos + 4:])
        pos += 4 + size
        dummy = payload[start:pos]
        if payload[pos:pos + 3] != b'\x41\xb9\x0e':
            raise ValueError('Unexpected outcome icon bitmap')
        matrix, size = decode_matrix(payload[pos + 3:])
        pos += 3 + size
        if payload[pos] != 0:
            raise ValueError('Unexpected outcome icon line styles')
        bits = Bits(payload)
        bits.pos = (pos + 1) * 8
        if (bits.get(4), bits.get(4), bits.get(1), bits.get(5)) != (2, 0, 0, 5):
            raise ValueError('Unexpected outcome icon rectangle record')
        count = bits.get(5)
        x, y = bits.signed(count), bits.signed(count)
        if bits.get(2) != 2 or matrix['translate'] != [x, y]:
            raise ValueError('Outcome bitmap and rectangle differ')
        edge_start = bits.pos
        points = [(x, y)]
        for _ in range(4):
            if (bits.get(1), bits.get(1)) != (1, 1):
                raise ValueError('Expected four straight rectangle edges')
            count = bits.get(4) + 2
            if bits.get(1):
                x += bits.signed(count)
                y += bits.signed(count)
            elif bits.get(1):
                y += bits.signed(count)
            else:
                x += bits.signed(count)
            points.append((x, y))
        edge_end = bits.pos
        x0, y0 = points[0]
        if points != [(x0, y0), (x0 + 220, y0), (x0 + 220, y0 + 260),
                      (x0, y0 + 260), (x0, y0)]:
            raise ValueError('Unexpected outcome icon dimensions')
        suffix_start = bits.pos
        if ordinal < 6:
            if (bits.get(1), bits.get(5), bits.get(5), bits.get(2),
                    bits.get(2), bits.get(2)) != (0, 31, 1, 0, 0, 0):
                raise ValueError('Unexpected outcome new-style separator')
        elif bits.get(6) != 0:
            raise ValueError('Missing outcome shape end')
        suffix_end = bits.pos
        if bits.get(bits.size * 8 - bits.pos):
            raise ValueError('Nonzero outcome shape padding')
        pos = bits.size
        rectangles.append((x0, y0, dummy, matrix, edge_start, edge_end,
                           suffix_start, suffix_end))
    if pos != len(payload):
        raise ValueError('Unexpected trailing outcome shape data')
    return rectangles


def icon_rectangles(payload):
    return [[r[0], r[0] + 220, r[1], r[1] + 260] for r in _rectangles(payload)]


def _move_rectangles(payload, shifts):
    rows = _rectangles(payload)
    rects = [[r[0] + dx, r[0] + dx + 220, r[1], r[1] + 260]
             for r, dx in zip(rows, shifts)]
    bounds = [min(r[0] for r in rects), max(r[1] for r in rects),
              min(r[2] for r in rects), max(r[3] for r in rects)]
    result = payload[:2] + encode_rect(bounds)
    for row, dx in zip(rows, shifts):
        x, y, dummy, matrix, edge_start, edge_end, suffix_start, suffix_end = row
        matrix = copy.deepcopy(matrix)
        matrix['translate'][0] += dx
        result += dummy + b'\x41\xb9\x0e' + encode_matrix(matrix) + b'\0'
        bits = BitWriter()
        bits.put(2, 4)
        bits.put(0, 4)
        bits.put(0, 1)
        bits.put(5, 5)
        count = signed_bits([x + dx, y])
        bits.put(count, 5)
        bits.signed(x + dx, count)
        bits.signed(y, count)
        bits.put(2, 2)
        source = Bits(payload)
        for start, end in ((edge_start, edge_end), (suffix_start, suffix_end)):
            source.pos = start
            for _ in range(end - start):
                bits.put(source.get(1), 1)
        result += bits.bytes()
    return result


def _placement(tag):
    if tag.code not in (26, 70) or tag.payload[0] & 0x0f != 6:
        raise ValueError('Unexpected outcome PlaceObject2')
    start = 5 if tag.code == 26 else 6
    matrix, size = decode_matrix(tag.payload[start:])
    return int.from_bytes(tag.payload[start - 2:start], 'little'), matrix, size


def _ink(tags, character, fonts=None):
    text = parse_text(next(t.payload for t in tags if t.code == 11
                           and int.from_bytes(t.payload[:2], 'little') == character))
    row = text['records'][0]
    font = (fonts or {}).get(row['font_id'])
    if font is None:
        font = parse_font(next(t.payload for t in tags if t.code == 75
                               and int.from_bytes(t.payload[:2], 'little') == row['font_id']))
    bounds = ink_bounds(font, row['entries'], row['height'], row.get('x', 0), row.get('y', 0))
    matrix = decode_matrix(text['matrix_raw'])[0]
    if matrix['scale'] != [65536, 65536] or matrix['rotate'] != [0, 0]:
        raise ValueError('Unexpected outcome text matrix')
    return [bounds[i] + matrix['translate'][0] for i in (0, 1)]


def repair_outcome_icons(original_tags, tags, fonts):
    for index, (code, character, digest) in GUARDS.items():
        old = original_tags[index]
        if (old.code != code or int.from_bytes(old.payload[:2], 'little') != character
                or sha(old.payload) != digest or tags[index] != old):
            raise ValueError('Outcome icon resource guard mismatch')
    shape, sprite = tags[4046], tags[4067]
    if _move_rectangles(shape.payload, [0] * 7) != shape.payload:
        raise ValueError('Outcome shape roundtrip differs')
    children = read_tags(sprite.payload[4:])
    shifts, report = [], []
    for character, index in PAIRS:
        placements = [t for t in children if t.code in (26, 70)
                      and _placement(t)[0] == character]
        if len(placements) != 1:
            raise ValueError('Expected one outcome label placement')
        _, label_matrix, _ = _placement(placements[0])
        if label_matrix['rotate'] != [0, 0]:
            raise ValueError('Unexpected outcome label rotation')
        old, new = _ink(original_tags, character), _ink(tags, character, fonts)
        scale = label_matrix['scale'][0] / 65536
        dx = round((new[0] - old[0]) * scale)
        right_shift = (new[1] - old[1]) * scale
        shifts.append(dx)
        plate = children[index]
        cid, matrix, size = _placement(plate)
        if cid != 3779:
            raise ValueError('Unexpected outcome hover resource')
        matrix['translate'][0] += dx
        # Retain the old left/right padding around both icon and label.
        matrix['scale'][0] += round((right_shift - dx) * 65536 / 1000)
        children[index] = Tag(26, plate.payload[:5] + encode_matrix(matrix) + plate.payload[5 + size:])
        report.append({'label_id': character, 'plate_child': index, 'shift_twips': dx})
    tags[4046] = Tag(22, _move_rectangles(shape.payload, shifts))
    # Keep every unaffected child tag's original raw encoding.
    raw_children = b''.join(t.raw or (struct.pack('<HI', (t.code << 6) | 63, len(t.payload))
                                     + t.payload) for t in children)
    tags[4067] = Tag(39, sprite.payload[:4] + raw_children)
    return report
