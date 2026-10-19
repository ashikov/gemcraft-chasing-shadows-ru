"""Offline acceptance of the encoded Help timeline; no game launch or installation.

Glyph boxes use actual embedded outline control points. Illustration bounds use
SWF declared ShapeBounds, so their overlaps are conservative evidence, not pixels.
"""
import hashlib
import itertools
import json
import os
import re
from functools import lru_cache
from pathlib import Path

import pytest

from swf_geometry import (
    Reader, decode_matrix, decode_rect, parse_font, parse_shape, parse_text,
    read_swf, read_tags, shape_bounds,
)

from versioned_catalog import restore_workspace

pytestmark = pytest.mark.local
ORIGINAL = 'c8eadc03a7e607086998695f7696ec22dd90d7b7859e3d10cdfabf40fc7bbad5'
BASELINE = '72e655aca3189e89a44cc0b629a53fa6d5c2c25f54642496447dce68bca68290'
IDS = {251, 252, 256, 269, 270, 283, 284, 288, 289, 290, 291, 332, 333,
       350, 351, 379, 380, 381, 382, 383, 384, 385, 386, 405, 406, 407,
       408, 409, 410, 414, 415, 416, 419, 420, 437, 438, 441, 442, 458,
       459, 473, 474, 491, 492, 493, 494, 495, 518, 519, 532, 533}
IDENTITY = (1, 0, 0, 1, 0, 0)
# Original paper/background shapes, verified in all sixteen timeline frames.
BACKGROUNDS = {
    **{n: [0, 14160, 0, 8640] for n in (250, 287, 316, 353, 389, 413, 421, 475)},
    **{n: [0, 14180, 0, 8680] for n in (254, 334, 443)},
    **{n: [0, 14180, 0, 8640] for n in (418, 496)},
    460: [0, 14160, 0, 8680],
}


def matrix(raw):
    item, size = decode_matrix(raw)
    sx, sy = item['scale']
    r0, r1 = item['rotate']
    tx, ty = item['translate']
    return (sx / 65536, r0 / 65536, r1 / 65536, sy / 65536, tx, ty), size


def compose(a, b):
    aa, ab, ac, ad, ax, ay = a
    ba, bb, bc, bd, bx, by = b
    return (aa*ba+ac*bb, ab*ba+ad*bb, aa*bc+ac*bd, ab*bc+ad*bd,
            aa*bx+ac*by+ax, ab*bx+ad*by+ay)


def transformed(rect, m):
    a, b, c, d, x, y = m
    points = [(a*u+c*v+x, b*u+d*v+y)
              for u in rect[:2] for v in rect[2:]]
    return [min(p[0] for p in points), max(p[0] for p in points),
            min(p[1] for p in points), max(p[1] for p in points)]


def union(boxes):
    boxes = [b for b in boxes if b is not None]
    return ([min(b[0] for b in boxes), max(b[1] for b in boxes),
             min(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None)


def intersects(a, b):
    return min(a[1], b[1]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[2], b[2])


def inside(a, b):
    return a[0] >= b[0] and a[1] <= b[1] and a[2] >= b[2] and a[3] <= b[3]


def frames(payload):
    """Simulate display-list persistence, replacement, moves and removals."""
    reader = Reader(payload)
    reader.u16()
    expected = reader.u16()
    state, result = {}, []
    for tag in read_tags(payload[reader.pos:]):
        r = Reader(tag.payload)
        if tag.code in (26, 70):
            flags = r.u8()
            flags2 = r.u8() if tag.code == 70 else 0
            depth = r.u16()
            if tag.code == 70 and (flags2 & 8 or flags2 & 16 and flags & 2):
                while r.u8():
                    pass
            item = dict(state.get(depth, {})) if flags & 1 else {}
            if flags & 2:
                item['id'] = r.u16()
            if flags & 4:
                item['matrix'], size = matrix(r.data[r.pos:])
                r.take(size)
            assert 'id' in item, ('move at empty depth', depth)
            item.setdefault('matrix', IDENTITY)
            state[depth] = item
        elif tag.code == 4:
            identifier, depth = r.u16(), r.u16()
            placement, _ = matrix(r.data[r.pos:])
            state[depth] = {'id': identifier, 'matrix': placement}
        elif tag.code == 28:
            state.pop(r.u16(), None)
        elif tag.code == 5:
            r.u16()
            state.pop(r.u16(), None)
        elif tag.code == 1:
            result.append({depth: dict(item) for depth, item in state.items()})
    assert len(result) == expected
    return result


def resources(tags):
    return {int.from_bytes(t.payload[:2], 'little'): t for t in tags
            if t.code in (2, 7, 11, 22, 32, 33, 34, 37, 39, 48, 75, 83)}


class Scene:
    def __init__(self, tags):
        self.resources = resources(tags)
        self.fonts = {n: parse_font(t.payload) for n, t in self.resources.items()
                      if t.code in (48, 75)}

    @lru_cache(maxsize=None)
    def text(self, identifier):
        tag = self.resources[identifier]
        parsed = parse_text(tag.payload, tag.code)
        x = y = 0
        font = height = None
        glyphs, runs, baselines = [], [], []
        tm, _ = matrix(parsed['matrix_raw'])
        for row in parsed['records']:
            font, height = row.get('font_id', font), row.get('height', height)
            x, y = row.get('x', x), row.get('y', y)
            baselines.append(y)
            embedded = self.fonts[font]
            runs.append(''.join(chr(embedded['codes'][i]) for i, _ in row['entries']))
            em = 20480 if self.resources[font].code == 75 else 1024
            for index, advance in row['entries']:
                contours = parse_shape(embedded['glyphs'][index])
                if contours:
                    b = shape_bounds(contours)
                    glyphs.append((y, transformed(
                        [x+b[0]*height/em, x+b[1]*height/em,
                         y+b[2]*height/em, y+b[3]*height/em], tm)))
                x += advance
        return parsed, runs, glyphs, baselines

    @lru_cache(maxsize=None)
    def illustration(self, identifier):
        tag = self.resources[identifier]
        if tag.code in (2, 22, 32, 83):
            return decode_rect(tag.payload[2:])[0]
        if tag.code in (11, 33):
            return union(b for _, b in self.text(identifier)[2])
        assert tag.code == 39, ('unsupported Help illustration', identifier, tag.code)
        return union(transformed(b, item['matrix'])
                     for frame in frames(tag.payload) for item in frame.values()
                     if (b := self.illustration(item['id'])) is not None)

    def nested_texts(self, identifier, ancestors=()):
        assert identifier not in ancestors
        tag = self.resources[identifier]
        if tag.code in (11, 33, 37):
            return {identifier}
        if tag.code != 39:
            return set()
        return set().union(*(self.nested_texts(item['id'], (*ancestors, identifier))
                             for frame in frames(tag.payload) for item in frame.values()))


@pytest.fixture(scope='module')
def inputs():
    defaults = {
        'ORIGINAL': f'backups/gc-cs-steam.swf.{ORIGINAL}.original',
        'BASELINE': 'local/pr18/workspace/gc-cs-steam.ru.swf',
        'CANDIDATE': 'local/pr18/candidate/gc-cs-steam.ru.swf',
        'WORKSPACE': 'local/pr18/workspace/workspace.json',
        'PLAN': 'translations/plans/pr18-battle-help.json',
    }
    paths = {k: Path(os.environ.get(f'GEMCRAFT_PR18_{k}', v)) for k, v in defaults.items()}
    if any(not p.is_file() for p in paths.values()):
        pytest.skip('Set GEMCRAFT_PR18_ORIGINAL/BASELINE/CANDIDATE/WORKSPACE/PLAN')
    raw = {k: paths[k].read_bytes() for k in ('ORIGINAL', 'BASELINE', 'CANDIDATE')}
    assert hashlib.sha256(raw['ORIGINAL']).hexdigest() == ORIGINAL
    assert hashlib.sha256(raw['BASELINE']).hexdigest() == BASELINE
    tags = {k: read_swf(v)[1] for k, v in raw.items()}
    return (paths, tags, {k: Scene(v) for k, v in tags.items()},
            json.loads(paths['WORKSPACE'].read_text()), json.loads(paths['PLAN'].read_text()))


def test_help_scope_styles_and_previous_resources(inputs):
    paths, tags, scenes, workspace, plan = inputs
    before, after = scenes['ORIGINAL'], scenes['CANDIDATE']
    assert set(plan['entry_ids']) == {f'static.{n}' for n in IDS}
    assert len(plan['entry_ids']) == len(IDS) == 51
    entries = {e['id']: e for e in workspace['entries']}
    assert after.nested_texts(534) == IDS
    assert len(frames(after.resources[534].payload)) == 16
    for n in IDS:
        old, _, _, old_lines = before.text(n)
        new, runs, _, new_lines = after.text(n)
        layout = plan['layouts'][f'static.{n}']
        decoded = ' '.join(runs) if 'lines' in layout else ''.join(runs)
        assert decoded == entries[f'static.{n}']['translation']
        assert len(old['records']) == len(new['records'])
        assert old['matrix_raw'] == new['matrix_raw']
        assert old_lines == new_lines
        centered = plan['layouts'][f'static.{n}'].get('center', False)
        if centered:
            old_box = union(b for _, b in before.text(n)[2])
            new_box = union(b for _, b in after.text(n)[2])
            assert abs(sum(old_box[:2])-sum(new_box[:2])) <= 4
        for a, b in zip(old['records'], new['records']):
            for attr in ('flags', 'color', 'height', 'y', *(() if centered else ('x',))):
                assert a.get(attr) == b.get(attr), (n, attr)
            if 'font_id' in a:
                assert b['font_id'] == {1: 6001, 255: 6002}[a['font_id']]
    expected_keys = {
        251: ['T', 'R', 'A', 'W', 'Shift', 'G', 'Q', 'N', 'D', 'U', 'X', 'Tab',
              'Numpad', '1..9', '1..3', '4..6'],
        252: ['B', 'Shift', 'Up/Down', 'Alt', 'Delete', 'Shift', 'Alt', 'Ctrl',
              'Esc', 'WASD', 'PageUp/PageDown'],
        283: ['T', 'Shift'], 351: ['Shift'], 380: ['U'], 381: ['G'], 382: ['D'],
        384: ['N', 'Ctrl'], 386: ['X'], 407: ['Shift', 'B'], 410: ['B', 'Shift'],
        438: ['Shift', '8'], 494: ['7'], 533: ['Alt'],
    }
    for n, keys in expected_keys.items():
        text = ''.join(after.text(n)[1])
        at = 0
        for key in keys:
            match = re.search(r'(?<![A-Za-z])'+re.escape(key)+r'(?![A-Za-z])', text[at:])
            assert match, (n, key, text)
            at += match.end()
    assert '70%' in ''.join(after.text(386)[1])
    assert '5' in ''.join(after.text(420)[1])
    # No other resource, ABC method, timeline, or prior embedded font may change.
    old_tags, new_tags = tags['BASELINE'], tags['CANDIDATE']
    assert len(old_tags) == len(new_tags)
    changed = set()
    for a, b in zip(old_tags, new_tags):
        assert a.code == b.code
        if a.payload != b.payload:
            assert a.code in (11, 33)
            changed.add(int.from_bytes(a.payload[:2], 'little'))
    assert changed == IDS
    base_plan = json.loads((paths['BASELINE'].parent / 'plan.json').read_text())
    assert len(base_plan['entry_ids']) == 1809
    assert set(base_plan['entry_ids']).isdisjoint(plan['entry_ids'])
    receipt = json.loads(Path('local/pr18/pre-backup-receipt.json').read_text())
    manifest_data = (Path(receipt['backup']) / 'manifest.json').read_bytes()
    assert hashlib.sha256(manifest_data).hexdigest() == receipt['manifest_sha256']
    manifest = json.loads(manifest_data)
    row = next(r for r in manifest if r['source'] == 'translations/catalog.json')
    catalog_data = Path(row['backup']).read_bytes()
    assert hashlib.sha256(catalog_data).hexdigest() == row['sha256']
    inventory = json.loads((paths['BASELINE'].parent / 'inventory.json').read_text())
    restored = restore_workspace(json.loads(catalog_data), inventory)
    canonical = {e['id']: e for e in restored['entries']}
    for identifier in base_plan['entry_ids']:
        for attr in ('translation', 'notes', 'status'):
            assert entries[identifier].get(attr) == canonical[identifier].get(attr)


def test_help_actual_contours_neighbors_and_charge_icons(inputs):
    _, _, scenes, _, _ = inputs
    old, new = scenes['ORIGINAL'], scenes['CANDIDATE']
    original_frames = frames(old.resources[534].payload)
    candidate_frames = frames(new.resources[534].payload)
    assert original_frames == candidate_frames
    report = []
    new_collisions = []
    for frame_number, frame in enumerate(candidate_frames):
        text_boxes, illustrations = {}, {}
        for depth, item in frame.items():
            n, placement = item['id'], item['matrix']
            if n in IDS:
                _, _, glyphs, _ = new.text(n)
                boxes = [(baseline, transformed(b, placement)) for baseline, b in glyphs]
                text_boxes[n] = boxes
                for _, b in boxes:
                    assert inside(b, [0, 14160, 0, 8640]), (frame_number, n, b)
                # Separate encoded physical lines must not intersect each other.
                lines = {}
                for baseline, box in boxes:
                    lines.setdefault(baseline, []).append(box)
                for (_, a), (_, b) in itertools.combinations(lines.items(), 2):
                    assert not intersects(union(a), union(b)), (frame_number, n, a, b)
            else:
                box = new.illustration(n)
                if box is not None:
                    box = transformed(box, placement)
                    if n in BACKGROUNDS:
                        assert box == BACKGROUNDS[n], (frame_number, n, box)
                    else:
                        illustrations[depth] = (n, box)
        for (an, a), (bn, b) in itertools.combinations(text_boxes.items(), 2):
            assert not any(intersects(x, y) for _, x in a for _, y in b), (
                'neighbor text overlap', frame_number, an, bn)
        for n, boxes in text_boxes.items():
            old_item = next(i for i in original_frames[frame_number].values() if i['id'] == n)
            old_boxes = [transformed(b, old_item['matrix']) for _, b in old.text(n)[2]]
            for depth, (illustration, box) in illustrations.items():
                was = any(intersects(b, box) for b in old_boxes)
                now = any(intersects(b, box) for _, b in boxes)
                if was or now:
                    report.append({'frame': frame_number, 'text': n, 'illustration': illustration,
                                   'depth': depth, 'original': was, 'candidate': now,
                                   'bounds_model': 'declared ShapeBounds union of all sprite frames'})
                if now and not was:
                    new_collisions.append((frame_number, n, illustration, box))
                # The charge sign must be inside the literal blank gap, not merely
                # disjoint from the text resource's broad rectangle.
                if illustration == 457 and n in (459, 474):
                    assert not now, ('charge icon intersects text', frame_number, n, box)
                    _, runs, _, _ = new.text(n)
                    assert '   ' in runs[1]
                    row = new.text(n)[0]['records'][1]
                    font_id = next(r['font_id'] for r in new.text(n)[0]['records'] if 'font_id' in r)
                    assert font_id in (6001, 6002)
                    prefix = runs[1].index('   ')
                    x = row['x']
                    lo = x + sum(a for _, a in row['entries'][:prefix])
                    hi = lo + sum(a for _, a in row['entries'][prefix:prefix+3])
                    tm, _ = matrix(new.text(n)[0]['matrix_raw'])
                    original_icon = original_frames[frame_number][depth]
                    original_box = transformed(old.illustration(illustration), original_icon['matrix'])
                    assert box == original_box
                    # The original sign extends below the text baseline and
                    # above a midpoint-based line cell. Only its horizontal
                    # blank span is meaningful; glyph disjointness is checked
                    # above and both the sign and text baselines are preserved.
                    gap = transformed([lo, hi, row['y'], row['y']], compose(old_item['matrix'], tm))
                    assert gap[0] <= box[0] and box[1] <= gap[1], (
                        'charge icon outside anchored whitespace', n, box, gap)
    destination = Path(os.environ.get('GEMCRAFT_PR18_GEOMETRY_REPORT', 'local/pr18/scene-overlaps.json'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2)+'\n')
    assert not new_collisions, ('new conservative illustration overlaps', new_collisions)
