"""Real-outline bitmap envelopes and inherited placement risks; not an AIR renderer."""
import json
from itertools import product
from math import ceil, floor

import pytest

from abc_font_binding import _bodies
from swf_geometry import parse_font, read_swf
from test_bitmap_completion import CALLS, COUNTERS, translations
from test_bitmap_completion_local import completion_resources as completion_resources
from test_bitmap_completion_semantics_local import Expressions
from test_bitmap_font_binding import execute
from test_bitmap_layout_local import bitmap_envelope, outline_font
from test_talisman_local import abc_bytes, abc_inventory

pytestmark = pytest.mark.local
SCALES = (1, .75, .5)


class GeometryExpressions(Expressions):
    def call(self, receiver, name, arguments, long):
        if receiver == 'Math' and name == 'random':
            assert not arguments
            return next(self.random_values)
        return super().call(receiver, name, arguments, long)


def call_arguments(program, method, call, *, mouse=(544, 306), random=(0, 0), early=True):
    """Evaluate the original thirteen arguments with explicit coordinate inputs."""
    ops = program.operations[method]
    first = CALLS[method][call][0]
    start = max(i for i, (at, op, args) in enumerate(ops)
                if at < first and op == 0x66 and program.inv.multiname(args[0]) == 'vfxEngine')
    assert ops[start - 1][1:] == (0x60, [230])
    locals_ = {key: 21 for key in range(22)}
    locals_.update({0: {'core': {'orb': {'fieldX': 20, 'fieldY': 16}},
                       'monsterKillChainCounter': {'value': 21}},
                    10: [None] * 21, 11: early})
    if method == 1144:
        locals_[1] = {'sellValue': {'value': 21}}
    program.random_values = iter(random)
    values, _, destination, _ = program.run(
        method, ops[start + 1][0], call, locals_,
        globals_extra={'GV': {'main': {'mouseX': mouse[0], 'mouseY': mouse[1]}}})
    assert destination == call and len(values) == 13
    assert values[4] in (11, 12, 14, 16)
    assert values[5] in ('left', 'center', 'right')
    assert values[8] == 0  # the original doEnterFrame does not update speedX
    return values


def phrases(program, rows, method, call, formatted):
    fragments = CALLS[method][call]
    source = {at: program.inv.strings[args[0]]
              for at, op, args in program.operations[method] if op == 0x2c}
    target = [rows[f'abc.4175.{method}.{at}'] for at in fragments]
    if (method, call) in COUNTERS:
        return source[fragments[0]] + formatted + source[fragments[-1]], ''.join(
            (target[0], formatted, target[-1]))
    if (method, call) == (243, 305):
        return formatted + source[fragments[0]], target[0] + formatted
    if (method, call) == (1596, 4688):
        return formatted + source[fragments[0]], formatted + target[0]
    assert len(fragments) == 1
    return source[fragments[0]], target[0]


def envelope_at(envelope, x, y, alignment):
    width = envelope['width']
    initial = {'left': x, 'center': x - width / 2, 'right': x - width}[alignment]
    left = max(10, min(1078 - width, initial))
    dx, dy = left - envelope['bitmap'][0], y - envelope['bitmap'][2]
    return [envelope['ink'][0] + dx, envelope['ink'][1] + dx,
            envelope['ink'][2] + dy, envelope['ink'][3] + dy]


def motion(arguments):
    """Replay postdecrement delay/stop/fade order, retaining only visible frames."""
    speed_x, speed_y, _, acceleration, prefade, delay, stop = arguments[6:]
    dx = dy = 0
    alpha = 1
    result = {0: (dx, dy)} if delay == 0 else {}
    for tick in range(1, 256):
        if delay > 0:
            delay -= 1
            if delay == 0:
                result[tick] = (dx, dy)
            continue
        if stop > 0:
            dx += speed_x
            dy += speed_y
            speed_y += acceleration
        stop -= 1
        if prefade <= 0:
            alpha -= .25
        prefade -= 1
        if alpha < .01:
            return result
        result[tick] = (dx, dy)
    raise AssertionError('Original bitmap lifetime exceeded the bounded model')


def exit_trajectories(program, new_exits):
    """Distinguish a shifted exit tick from a previously safe complete path."""
    results, seen = [], set()
    for example in new_exits:
        key = (example['call'], tuple(example['mouse']), tuple(example['random']))
        if key in seen:
            continue
        seen.add(key)
        method, call = map(int, example['call'][1:].split('@'))
        args = call_arguments(program, method, call, mouse=key[1], random=key[2])
        frames = motion(args)
        origin = frames[example['tick']]
        outside = []
        for field in ('source', 'candidate'):
            box = example[field]
            ticks = []
            for tick, (dx, dy) in frames.items():
                x0, x1 = box[0] + dx - origin[0], box[1] + dx - origin[0]
                y0, y1 = box[2] + dy - origin[1], box[3] + dy - origin[1]
                if x0 < 0 or x1 > 1088 or y0 < 0 or y1 > 612:
                    ticks.append(tick)
            outside.append(ticks)
        results.append({'call': key[0], 'mouse': key[1], 'random': key[2],
                        'original_outside_ticks': outside[0], 'candidate_outside_ticks': outside[1],
                        'new_unsafe_trajectory': not outside[0] and bool(outside[1])})
    return results


def safe_interval(envelope, coordinate, motion_bounds, domain, *, horizontal):
    """Invert the original affine coordinate and clamp for whole-life ink bounds."""
    slope, intercept = coordinate[:2]
    axis = 0 if horizontal else 2
    relative = [envelope['ink'][axis + i] - envelope['bitmap'][axis] for i in (0, 1)]
    low = -relative[0] - motion_bounds[0]
    high = (1088 if horizontal else 612) - relative[1] - motion_bounds[1]
    if horizontal:
        shift = {'left': 0, 'center': envelope['width'] / 2,
                 'right': envelope['width']}[coordinate[2]]
        intercept -= shift
        minimum, maximum = 10, 1078 - envelope['width']
        if maximum < low or minimum > high:
            return []
        low = float('-inf') if minimum >= low else low
        high = float('inf') if maximum <= high else high
    if slope == 0:
        position = max(10, min(1078 - envelope['width'], intercept)) if horizontal else intercept
        return list(domain) if low <= position <= high else []
    assert slope == 1
    first = domain[0] if low == float('-inf') else max(domain[0], ceil(low - intercept))
    last = domain[1] if high == float('inf') else min(domain[1], floor(high - intercept))
    return [first, last] if first <= last else []


def difference(first, second):
    if not first:
        return set()
    return set(range(first[0], first[1] + 1)) - (set(range(second[0], second[1] + 1)) if second else set())


def boundary_paths(program, method, call):
    """Read the two possible mouse-Y coordinate branches directly from ABC."""
    args = {y: call_arguments(program, method, call, mouse=(0, y)) for y in (0, 59, 60, 612)}
    other = call_arguments(program, method, call, mouse=(1088, 306))
    origin = call_arguments(program, method, call, mouse=(0, 306))
    slope_x = (other[0] - origin[0]) / 1088
    assert slope_x in (0, 1)
    bands = []
    for first, last in ((0, 59), (60, 612)):
        slope = (args[last][1] - args[first][1]) / (last - first)
        assert slope in (0, 1)
        bands.append(((slope, args[first][1] - slope * first), (first, last)))
    paths = []
    for random in (0, .01, .05, .1, .2, .25, .5, .75, 1):
        horizontal = motion(call_arguments(program, method, call, random=(random, .5)))
        vertical = motion(call_arguments(program, method, call, random=(.5, random)))
        paths.append({'random': random, 'x': (slope_x, origin[0], origin[5]), 'y': bands,
                      'dx': (min(x for x, y in horizontal.values()), max(x for x, y in horizontal.values())),
                      'dy': (min(y for x, y in vertical.values()), max(y for x, y in vertical.values())),
                      'frames_x': horizontal, 'frames': vertical})
    return paths


def record_boundary_risks(risks, key, formatted, kerning, envelopes, paths):
    for path in paths:
        for axis in ('x', 'y'):
            bands = [(path['x'], (0, 1088))] if axis == 'x' else path['y']
            for coordinate, domain in bands:
                safe = [safe_interval(box, coordinate, path['d' + axis], domain, horizontal=axis == 'x')
                        for box in envelopes]
                points = difference(*safe)
                if not points:
                    continue
                item = risks.setdefault(key, {}).setdefault(axis, {
                    'variant_path_count': 0, 'integer_coordinates': set(), 'examples': []})
                item['variant_path_count'] += 1
                item['integer_coordinates'].update(points)
                if len(item['examples']) < 3 or formatted in ('21', '1,000') and not kerning:
                    example = {'formatted': formatted, 'kerning': kerning, 'random': path['random'],
                               'source_safe_interval': safe[0], 'candidate_safe_interval': safe[1],
                               'new_unsafe_coordinates': sorted(points),
                               'source_text': envelopes[0]['text'], 'candidate_text': envelopes[1]['text']}
                    if axis == 'y':
                        y = min(points)
                        initial_y = coordinate[0] * y + coordinate[1]
                        intervals = [[initial_y + box['ink'][2] - 306,
                                      initial_y + box['ink'][3] - 306] for box in envelopes]
                        example['candidate_only_ticks'] = [tick for tick, (dx, dy) in path['frames'].items()
                                                           if intervals[1][0] + dy < 0
                                                           or intervals[1][1] + dy > 612]
                        example['lifetime_vertical_extents'] = [[low + path['dy'][0], high + path['dy'][1]]
                                                                for low, high in intervals]
                    else:
                        x = coordinate[0] * min(points) + coordinate[1]
                        intervals = [envelope_at(box, x, 306, coordinate[2])[:2] for box in envelopes]
                        example['candidate_only_ticks'] = [tick for tick, (dx, dy) in path['frames_x'].items()
                                                           if intervals[1][0] + dx < 0
                                                           or intervals[1][1] + dx > 1088]
                        example['lifetime_horizontal_extents'] = [[low + path['dx'][0], high + path['dx'][1]]
                                                                  for low, high in intervals]
                    item['examples'].append(example)


@pytest.fixture(scope='module')
def layout_resources(completion_resources):
    root, original, _, candidate = completion_resources
    fonts = []
    for data, identifier in ((original, 255), (candidate, 6002)):
        tag = next(t for t in read_swf(data)[1]
                   if t.code == 75 and int.from_bytes(t.payload[:2], 'little') == identifier)
        fonts.append(outline_font(parse_font(tag.payload)))
    program = GeometryExpressions(original)
    # These source instructions determine the model's movement/fade order.
    operations = {at: (op, args) for at, op, args in program.operations[1733]}
    assert operations[78] == (0x66, [2602]) and operations[82] == (0xc1, [])
    assert operations[111] == (0x66, [2597]) and operations[130] == (0x66, [2598])
    assert operations[145] == (0x66, [2600]) and operations[149] == (0x61, [2598])
    assert operations[157] == (0x66, [2596]) and operations[161] == (0xc1, [])
    assert program.doubles[25] == .25 and program.doubles[14] == .01
    # Blur 6 raster pixels at bitmap scale .5: conservative radius 1.5px.
    glow = {at: (op, args) for at, op, args in program.operations[1731]}
    assert glow[90] == (0x24, [6])
    assert program.doubles[9] == .5
    source_code = _bodies(program.inv.reader.data)[1732][3]
    target_raw = abc_bytes(read_swf(candidate)[1])
    target_code = _bodies(target_raw)[1732][3]
    selector = target_code[214:214 + len(target_code) - len(source_code)]
    assert target_code == source_code[:214] + selector + source_code[214:]
    return root, program, fonts, translations(), selector, abc_inventory(target_raw)


def test_all_forty_complete_phrases_actual_outlines_and_motion_envelopes(layout_resources):
    root, program, fonts, rows, selector, target_inv = layout_resources
    summaries, risk_counts, risk_examples = {}, {'original': 0, 'candidate': 0, 'new': 0}, []
    new_risk_examples = []
    boundary_risks = {}
    numbers = [*range(26), 29, 30, 31, 99, 100, 101, 199, 200, 201,
               .49, .5, 1.5, 21.5, 999.5, 1000, 4294967295,
               999999999999, 1e12, 1.23456789e12, 1e308]
    for method, calls in CALLS.items():
        for call in calls:
            arguments = call_arguments(program, method, call)
            paths = boundary_paths(program, method, call)
            dynamic = (method, call) in COUNTERS or (method, call) in ((243, 305), (1596, 4688))
            values = range(1501) if method == 1144 else numbers if dynamic else [21]
            formatted = {program.format(value, long) for value in values for long in (False, True)}
            assert phrases(program, rows, method, call, '21')[0] == arguments[2]
            widest, maximums = None, [0, 0]
            for value, kerning in product(sorted(formatted), (False, True)):
                texts = phrases(program, rows, method, call, value)
                assert execute(selector, target_inv, texts[1])[-1] == 'GCCS Alegreya'
                envelopes = [bitmap_envelope(font, text, arguments[4], 544, 306, kerning=kerning)
                             for font, text in zip(fonts, texts)]
                for index, (font, text, envelope) in enumerate(zip(fonts, texts, envelopes)):
                    assert all(ord(char) in font['codes'] for char in text)
                    maximums[index] = max(maximums[index], envelope['width'])
                if widest is None or envelopes[1]['width'] > widest[1]['width']:
                    widest = envelopes
                record_boundary_risks(boundary_risks, f'm{method}@{call}', value, kerning, envelopes, paths)
            assert widest and max(maximums) < 1068
            assert execute(selector, target_inv, 'Latin after Russian')[-1] == 'Original decorative family'
            assert execute(selector, target_inv, '123.5 +10%')[-1] == 'Original decorative family'
            key = f'm{method}@{call}'
            summaries[key] = {'variants': len(formatted) * 2, 'size': arguments[4],
                              'alignment': arguments[5], 'max_widths': maximums,
                              'longest_candidate': widest[1]['text'], 'random_envelopes': []}
            # Full widths above include every conservative refund integer, both
            # formatter precisions and grammar boundaries. Placement probes use
            # the widest candidate and its corresponding complete original.
            for random in product((0, 1), repeat=2):
                args = call_arguments(program, method, call, random=random)
                frames = motion(args)
                summaries[key]['random_envelopes'].append({'r': random, 'arguments': args[4:],
                                                           'visible_frames': len(frames)})
                for mouse in product((0, 10, 544, 1078, 1088), (0, 30, 59, 60, 61, 306, 590, 612)):
                    edge_args = call_arguments(program, method, call, mouse=mouse, random=random)
                    initial = [envelope_at(box, edge_args[0], edge_args[1], edge_args[5])
                               for box in widest]
                    for tick, (dx, dy) in frames.items():
                        boxes = [[b[0] + dx, b[1] + dx, b[2] + dy, b[3] + dy] for b in initial]
                        outside = [b[0] < 0 or b[1] > 1088 or b[2] < 0 or b[3] > 612 for b in boxes]
                        risk_counts['original'] += outside[0]
                        risk_counts['candidate'] += outside[1]
                        risk_counts['new'] += outside[1] and not outside[0]
                        if outside[1] and not outside[0]:
                            new_risk_examples.append({'call': key, 'mouse': mouse, 'random': random,
                                                      'tick': tick, 'source': boxes[0], 'candidate': boxes[1]})
                        if outside[1] and len(risk_examples) < 12:
                            risk_examples.append({'call': key, 'mouse': mouse, 'tick': tick,
                                                  'source': boxes[0], 'candidate': boxes[1]})
                        for scale in SCALES:
                            for box in boxes:
                                scaled = [coordinate * scale for coordinate in box]
                                assert scaled[0] < scaled[1] and scaled[2] < scaled[3]
                                # Scaling transforms the same contours; this
                                # checks geometry, not subpixel raster coverage.
                                assert (scaled[0] < 0 or scaled[1] > 1088 * scale
                                        or scaled[2] < 0 or scaled[3] > 612 * scale) == (
                                            box[0] < 0 or box[1] > 1088
                                            or box[2] < 0 or box[3] > 612)
    assert len(summaries) == 40
    assert {row['size'] for row in summaries.values()} == {11, 12, 14, 16}
    assert risk_counts['original'] and risk_counts['candidate']
    trajectories = exit_trajectories(program, new_risk_examples)
    # Preserve the requested coordinates and expose narrower Russian-only edge
    # intervals rather than misclassifying every exit as inherited.
    expected_risks = {f'm{method}@{call}' for method in (566, 567) for call in CALLS[method]}
    expected_risks |= {f'm695@{call}' for call, (at,) in CALLS[695].items()
                       if rows[f'abc.4175.695.{at}'] == 'Нельзя построить'}
    expected_risks |= {'m675@1098', 'm675@1208', 'm1144@138'}
    assert set(boundary_risks) == expected_risks
    assert {key for key, risks in boundary_risks.items() if 'y' in risks} == {
        'm675@1098', 'm675@1208', 'm1144@138'}
    refund = next(row for row in boundary_risks['m675@1098']['y']['examples']
                  if row['formatted'] == '21' and not row['kerning'] and row['random'] == 0)
    assert refund['source_safe_interval'] == [60, 556]
    assert refund['candidate_safe_interval'] == [60, 552]
    assert refund['candidate_only_ticks'] == [9, 10]
    for risks in boundary_risks.values():
        for item in risks.values():
            item['integer_coordinates'] = sorted(item['integer_coordinates'])
    (root / 'bitmap-completion-geometry.json').write_text(json.dumps({
        'model': 'Actual SWF contours; both kerning states; 2x raster/.5 bitmap; not AIR',
        'scales': SCALES, 'calls': summaries, 'screen_exit_probes': risk_counts,
        'examples': risk_examples, 'new_exit_examples': new_risk_examples,
        'affected_trajectories': trajectories,
        'all_variant_boundary_differences': boundary_risks,
        'limits': ['r=1 is a closed upper envelope, not a reachable random seed',
                   'no proven gameplay maximum for XP/mana/kill counters; formatter extremes are probes',
                   'no vertical clamp or motion-time horizontal clamp in the original renderer',
                   'boundary differences are conservative coordinate stresses; caller gates determine reachability',
                   'random interiors are sampled; this is not an exhaustive proof over real random values',
                   'scales preserve geometry; smoothing, glow raster and reading in motion require AIR']},
        ensure_ascii=False, indent=2) + '\n')


def test_real_simultaneous_pairs_ink_glow_gaps_and_inherited_overlap(layout_resources):
    root, program, fonts, rows, _, _ = layout_resources
    pairs = [((243, 305), (243, 397)), ((1596, 4688), (1596, 4783)),
             ((242, 3807), (242, 3907)), ((242, 3807), (242, 4260)),
             ((242, 3907), (242, 4260))]
    results = []
    for first, second in pairs:
        for number, kerning, random in product((31, 1000, 1e308), (False, True), ((0, 0), (1, 1))):
            formatted = program.format(number, True)
            arguments = [call_arguments(program, *key, random=random, early=True) for key in (first, second)]
            frames = [motion(args) for args in arguments]
            pair = {'calls': [f'm{m}@{at}' for m, at in (first, second)],
                    'number': formatted, 'kerning': kerning, 'random': random, 'fonts': []}
            for font_index, font in enumerate(fonts):
                boxes = []
                for key, args in zip((first, second), arguments):
                    text = phrases(program, rows, *key, formatted)[font_index]
                    envelope = bitmap_envelope(font, text, args[4], 544, 306, kerning=kerning)
                    boxes.append(envelope_at(envelope, args[0], args[1], args[5]))
                minimum_gap, ink_overlap, glow_overlap = float('inf'), 0, 0
                for tick in frames[0].keys() & frames[1].keys():
                    moving = [[box[0] + frames[i][tick][0], box[1] + frames[i][tick][0],
                               box[2] + frames[i][tick][1], box[3] + frames[i][tick][1]]
                              for i, box in enumerate(boxes)]
                    a, b = moving
                    horizontal = max(b[0] - a[1], a[0] - b[1])
                    vertical = max(b[2] - a[3], a[2] - b[3])
                    minimum_gap = min(minimum_gap, vertical)
                    ink_overlap += horizontal < 0 and vertical < 0
                    glow_overlap += horizontal < 3 and vertical < 3
                pair['fonts'].append({'font_id': font['id'], 'ink_overlap_frames': ink_overlap,
                                      'glow_envelope_overlap_frames': glow_overlap,
                                      'minimum_scaled_ink_gaps': [minimum_gap * s for s in SCALES],
                                      'minimum_scaled_glow_gaps': [(minimum_gap - 3) * s for s in SCALES]})
            # Existing independently placed effects may intersect. A new ink
            # intersection must be investigated rather than called inherited.
            assert pair['fonts'][1]['ink_overlap_frames'] == 0 or pair['fonts'][0]['ink_overlap_frames'] > 0
            results.append(pair)
    (root / 'bitmap-completion-pairs.json').write_text(json.dumps({
        'model': 'Shared wall-clock ticks include original initial delays; conservative 1.5px glow radius',
        'pairs': results, 'limits': 'No general collision prevention; AIR rasterization remains unverified'},
        ensure_ascii=False, indent=2) + '\n')
