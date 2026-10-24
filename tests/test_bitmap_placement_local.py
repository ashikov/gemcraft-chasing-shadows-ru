"""Addressed PR32 placement proofs on owned files; no AIR or installed writes."""
from copy import deepcopy
from functools import lru_cache
from itertools import product
import json
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from install_patch import ORIGINAL_SHA256, sha
from swf_geometry import parse_font, read_swf
from test_bitmap_completion import CALLS, COUNTERS, translations
from test_bitmap_completion_layout_local import (
    GeometryExpressions, call_arguments, envelope_at, motion, phrases,
)
from test_bitmap_completion_semantics_local import Expressions
from test_bitmap_layout_local import bitmap_envelope, outline_font

pytestmark = pytest.mark.local
BEFORE_SHA = '90a2efa578212e76aea472991ac22049a2b620a224f6178fe4aa4d3db2f2723d'
PLACEMENTS = {(method, call): (23, 0) for method in (566, 567) for call in CALLS[method]}
PLACEMENTS.update({(695, call): (23, 0) for call in (6275, 6408, 7124, 8017, 8986, 10165)})
PLACEMENTS.update({(675, 1098): (32, 4), (675, 1208): (32, 0), (1144, 138): (32, 0)})


@pytest.fixture(scope='module')
def placement_resources():
    if not os.environ.get('GEMCRAFT_PR32_FIX_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR32_FIX_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR32_FIX_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    before = Path('local/pr32/candidate/gc-cs-steam.ru.swf').read_bytes()
    pr31 = Path('local/pr32/baseline/gc-cs-steam.ru.swf').read_bytes()
    candidate_path = Path(os.environ.get(
        'GEMCRAFT_PR32_FIX_CANDIDATE', str(root / 'candidate/gc-cs-steam.ru.swf')))
    candidate = candidate_path.read_bytes()
    assert sha(original) == ORIGINAL_SHA256 and sha(before) == BEFORE_SHA
    programs = {'source': GeometryExpressions(original), 'before': Expressions(before),
                'candidate': Expressions(candidate)}
    fonts = {}
    for label, data, identifier in (('source', original, 255), ('candidate', candidate, 6002)):
        tag = next(t for t in read_swf(data)[1]
                   if t.code == 75 and int.from_bytes(t.payload[:2], 'little') == identifier)
        fonts[label] = outline_font(parse_font(tag.payload))
    return {'root': root, 'programs': programs, 'fonts': fonts,
            'before': before, 'candidate': candidate, 'pr31': pr31}


def notification_indices(program, method):
    return [i for i, (_, op, args) in enumerate(program.operations[method])
            if op == 0x4f and program.inv.multiname(args[0]) in (
                'createFloatingText', 'createFloatingTextOutgame')]


@lru_cache(maxsize=None)
def placement_span(source, candidate, method, original_call):
    """Extract actual post-call instructions, before the original debug/jump."""
    original_calls = notification_indices(source, method)
    ordinal = next(i for i, index in enumerate(original_calls)
                   if source.operations[method][index][0] == original_call)
    calls = notification_indices(candidate, method)
    assert len(calls) == len(original_calls)
    first = calls[ordinal] + 1
    operations = candidate.operations[method]
    end = next(i for i in range(first, len(operations))
               if operations[i][1] in (0xf0, 0x10, 0x47, 0x48))
    original_next = source.operations[method][original_calls[ordinal] + 1][1]
    assert operations[end][1] == original_next
    return operations[first:end]


def execute_placement(operations, inv, rectangle):
    """Execute only public latest-bitmap reads, Math clamps and x/y writes."""
    mc = deepcopy(rectangle)
    older = {'x': 1, 'y': 2, 'width': 3, 'height': 4}
    gv = {'vfxEngine': {'vfxes': [{'mc': older}, {'mc': mc}]}}
    stack = ['preserved caller stack']
    for _, opcode, args in operations:
        if opcode == 0x60:
            name = inv.multiname(args[0])
            assert name in ('GV', 'Math')
            stack.append(gv if name == 'GV' else 'Math')
        elif opcode == 0x66:
            if inv.multinames[args[0]]['kind'] == 27:
                index, receiver = stack.pop(), stack.pop()
                stack.append(receiver[int(index)])
            else:
                name, receiver = inv.multiname(args[0]), stack.pop()
                stack.append(len(receiver) if name == 'length' else receiver[name])
        elif opcode in (0x24, 0x25):
            stack.append(args[0] - 256 if opcode == 0x24 and args[0] > 127 else args[0])
        elif opcode == 0x2a:
            stack.append(stack[-1])
        elif opcode == 0x2b:
            stack[-2], stack[-1] = stack[-1], stack[-2]
        elif opcode in (0xa0, 0xa1):
            right, left = stack.pop(), stack.pop()
            stack.append(left + right if opcode == 0xa0 else left - right)
        elif opcode == 0x46:
            assert args[1] == 2 and inv.multiname(args[0]) in ('min', 'max')
            right, left, receiver = stack.pop(), stack.pop(), stack.pop()
            assert receiver == 'Math'
            stack.append({'min': min, 'max': max}[inv.multiname(args[0])](left, right))
        elif opcode == 0x61:
            value, receiver = stack.pop(), stack.pop()
            name = inv.multiname(args[0])
            assert receiver is mc and name in ('x', 'y')
            receiver[name] = value
        else:
            raise AssertionError(f'Unexpected placement instruction {opcode:x}')
    assert stack == ['preserved caller stack']
    assert older == {'x': 1, 'y': 2, 'width': 3, 'height': 4}
    assert mc['width'] == rectangle['width'] and mc['height'] == rectangle['height']
    return mc


def corrected_ink(resources, method, call, envelope, args):
    initial_ink = envelope_at(envelope, args[0], args[1], args[5])
    rectangle = {'x': initial_ink[0] - (envelope['ink'][0] - envelope['bitmap'][0]),
                 'y': args[1], 'width': envelope['width'], 'height': envelope['height']}
    programs = resources['programs']
    span = placement_span(programs['source'], programs['candidate'], method, call)
    placed = execute_placement(span, programs['candidate'].inv, rectangle)
    return [initial_ink[0] + placed['x'] - rectangle['x'],
            initial_ink[1] + placed['x'] - rectangle['x'],
            initial_ink[2] + placed['y'] - rectangle['y'],
            initial_ink[3] + placed['y'] - rectangle['y']], placed


def test_known_refund_regression_is_removed_by_actual_candidate_code(placement_resources):
    resources = placement_resources
    source = resources['programs']['source']
    args = call_arguments(source, 675, 1098, mouse=(70, 553), random=(0, 0))
    envelopes = [bitmap_envelope(resources['fonts'][label], value, 14, 544, 306, kerning=False)
                 for label, value in (('source', '+21 mana'), ('candidate', 'Возврат маны: +21'))]
    original = envelope_at(envelopes[0], args[0], args[1], args[5])
    fixed, _ = corrected_ink(resources, 675, 1098, envelopes[1], args)
    for tick, (dx, dy) in motion(args).items():
        for label, box in (('PR31', original), ('candidate', fixed)):
            moving = [box[0] + dx, box[1] + dx, box[2] + dy, box[3] + dy]
            assert 0 <= moving[0] < moving[1] <= 1088, (label, tick, moving)
            assert 0 <= moving[2] < moving[3] <= 612, (label, tick, moving)


@pytest.mark.parametrize('formatted,mouse_y', [
    ('21', -13), ('21', -13.9), ('21', -13.5),
    ('70', -14), ('70', -13), ('70', -14.3),
])
def test_negative_pointer_does_not_introduce_a_new_pr31_top_exit(
        placement_resources, formatted, mouse_y):
    resources = placement_resources
    source = resources['programs']['source']
    args = call_arguments(source, 675, 1098, mouse=(544, mouse_y), random=(0, 0))
    envelopes = [bitmap_envelope(resources['fonts'][label], text, 14, 544, 306, kerning=False)
                 for label, text in (('source', f'+{formatted} mana'),
                                     ('candidate', f'Возврат маны: +{formatted}'))]
    original = envelope_at(envelopes[0], args[0], args[1], args[5])
    fixed, rectangle = corrected_ink(resources, 675, 1098, envelopes[1], args)
    frames = motion(args)
    assert all(0 <= original[2] + dy < original[3] + dy <= 612 for dx, dy in frames.values())
    for tick, (dx, dy) in frames.items():
        for scale in (1, .75, .5):
            assert 0 <= (fixed[2] + dy) * scale < (fixed[3] + dy) * scale <= 612 * scale, (
                formatted, mouse_y, tick, fixed, dy)
    assert rectangle['y'] == max(11, args[1] - 4)


@pytest.mark.parametrize('method,call,mouse_x', [
    *((m, call, 70) for m in (566, 567) for call in CALLS[m]),
    *((695, call, 65) for call in (6275, 6408, 7124, 8017, 8986, 10165)),
    (675, 1098, 70), (675, 1208, 70), (1144, 138, 105),
])
def test_each_address_removes_its_original_safe_new_unsafe_x_case(
        placement_resources, method, call, mouse_x):
    resources = placement_resources
    program = resources['programs']['source']
    args = call_arguments(program, method, call, mouse=(mouse_x, 306), random=(0, 0))
    texts = phrases(program, translations(), method, call, '21')
    envelopes = [bitmap_envelope(resources['fonts'][label], text, args[4], 544, 306, kerning=False)
                 for label, text in zip(('source', 'candidate'), texts)]
    original, uncorrected = [envelope_at(box, args[0], args[1], args[5]) for box in envelopes]
    corrected, _ = corrected_ink(resources, method, call, envelopes[1], args)
    dxs = [dx for dx, _ in motion(args).values()]
    assert all(0 <= original[0] + dx < original[1] + dx <= 1088 for dx in dxs)
    assert any(uncorrected[0] + dx < 0 or uncorrected[1] + dx > 1088 for dx in dxs)
    for scale in (1, .75, .5):
        assert all(0 <= (corrected[0] + dx) * scale < (corrected[1] + dx) * scale <= 1088 * scale
                   for dx in dxs)


def test_only_selected_post_call_results_change_and_branches_bypass_them(placement_resources):
    resources = placement_resources
    programs = resources['programs']
    before, after = programs['before'], programs['candidate']
    raw_before, raw_after = before.inv.reader.data, after.inv.reader.data
    old_bodies, new_bodies = _bodies(raw_before), _bodies(raw_after)
    assert old_bodies.keys() == new_bodies.keys()
    assert raw_before[:min(b[0] for b in old_bodies.values())] == (
        raw_after[:min(b[0] for b in new_bodies.values())])
    assert before.inv.strings == after.inv.strings
    assert before.inv.multinames == after.inv.multinames
    changed = {566, 567, 675, 695, 1144}
    for method, old_body in old_bodies.items():
        new_body = new_bodies[method]
        if method not in changed:
            assert old_body[2:] == new_body[2:]
            continue
        removed = set()
        for (selected_method, call), _ in PLACEMENTS.items():
            if selected_method == method:
                span = placement_span(programs['source'], after, method, call)
                assert span
                removed.update(at for at, _, _ in span)
        old = before.operations[method]
        new = [row for row in after.operations[method] if row[0] not in removed]
        assert len(old) == len(new)
        boundaries = {at: row[0] for (at, _, _), row in zip(old, new)}
        boundaries[len(old_body[3])] = len(new_body[3])
        for i, ((at, op, args), (new_at, new_op, new_args)) in enumerate(zip(old, new)):
            assert op == new_op
            if 0x0c <= op <= 0x1a:
                assert new[i + 1][0] + new_args[0] == boundaries[old[i + 1][0] + args[0]]
            elif op == 0x1b:
                assert args[1] == new_args[1]
                assert [new_at + d for d in (new_args[0], *new_args[2:])] == [
                    boundaries[at + d] for d in (args[0], *args[2:])]
            else:
                assert args == new_args
        assert new_body[2] == [old_body[2][0] + (0 if method == 695 else 7), *old_body[2][1:]]
        assert new_body[4] == [[*(boundaries[at] for at in handler[:3]), *handler[3:]]
                               for handler in old_body[4]]
        assert new_body[5] == old_body[5]
    for method in (1732, 1733):
        assert old_bodies[method][2:] == new_bodies[method][2:]
    before_tags = read_swf(resources['before'])[1]
    after_tags = read_swf(resources['candidate'])[1]
    assert [(t.code, t.payload) for t in before_tags if t.code != 82] == [
        (t.code, t.payload) for t in after_tags if t.code != 82]
    pr31_fonts = {t.payload[:2]: t.payload for t in read_swf(resources['pr31'])[1] if t.code == 75}
    assert all(pr31_fonts[t.payload[:2]] == t.payload for t in after_tags if t.code == 75)


def glyph_vertical(font, value, edge, size=14):
    return [2 + (font['ascent'] + font['bounds'][font['codes'].index(ord(c))][edge]) * size / 20480
            for c in set(value) if c != ' ']


def test_vertical_shift_has_no_new_pr31_risk_for_any_formatter_glyph(placement_resources):
    source, target = (placement_resources['fonts'][key] for key in ('source', 'candidate'))
    alphabet = '0123456789,. e+-InfinityNaN'
    source_bottom = max(glyph_vertical(source, ' mana', 3))
    target_bottom = max(glyph_vertical(target, 'Возврат маны: +' + alphabet, 3))
    target_top = min(glyph_vertical(target, 'Возврат маны: +' + alphabet, 2))
    assert target_bottom - source_bottom == pytest.approx(3.78779296875)
    assert target_bottom - 4 < source_bottom
    # Every displayed Number contains a digit or a nonfinite I/N. Its source
    # top cannot be lower than all of those glyphs. Kerning affects only X.
    source_number_top = max(glyph_vertical(source, '0123456789IN', 2))
    assert target_top > source_number_top
    # The automatic caller has no cursor bound, including negative Y. The
    # addressed floor protects the whole bitmap at every real cursor Y.
    # Below the floor both top and bottom are absolutely safe; above it the
    # -4 delta preserves the old lower-edge bound proved above.
    assert 11 - 10.5 == .5
    assert 11 - 10.5 + target_top > 0
    assert 11 + 58 + target_bottom < 612
    # Keyboard and talisman lower-edge stress coordinates are excluded by
    # the actual caller guards checked in the semantic suite.
    assert 597 - 20 + target_bottom + 10.5 < 612
    assert 479 - 20 + target_bottom + 10.5 < 612


def test_full_phrase_trajectories_fit_after_addressed_x_correction(placement_resources):
    resources = placement_resources
    program = resources['programs']['source']
    rows = translations()
    numbers = [*range(26), 29, 30, 31, 99, 100, 101, 199, 200, 201,
               .49, .5, 1.5, 21.5, 999.5, 1000, 4294967295,
               999999999999, 999999999999.5, 1e12, 1.2345e12,
               1.23456789e12, 1e308, 1.7976931348623157e308]
    assert program.format(999999999999.5) == '1,000,000,000,000'
    assert program.format(-999999999999.5) == '-1,000,000,000,000'
    # With the one-argument formatter, the longest fixed spelling is the
    # 10^12 rounding carry above (18 chars including a possible minus).
    # Precision8 scientific and nonfinite spellings are shorter. Bound every
    # numeric glyph and positive kerning pair, rather than guessing a largest
    # gameplay number or relying on just the sampled widest string.
    font = resources['fonts']['candidate']
    alphabet = '0123456789,. e+-InfinityNaN'
    advances = dict(zip(font['codes'], font['advances']))
    maximum_number_advance = max(advances[ord(char)] for char in alphabet)
    maximum_kerning = max(0, *(adjustment for _, _, adjustment in font['kerning']))
    for prefix in ('Возврат маны: +', 'Возврат теневых ядер: +'):
        upper_advance = (sum(advances[ord(c)] for c in prefix) + 18 * maximum_number_advance
                         + (len(prefix) + 17) * maximum_kerning)
        upper_width = upper_advance * 14 / 20480 + 6.5
        assert upper_width + 2 * 32 < 1088
    cache = {}
    checked, maximum_width = 0, 0
    for (method, call), (margin, shift) in PLACEMENTS.items():
        args = call_arguments(program, method, call)
        dynamic = (method, call) in COUNTERS
        values = range(1501) if method == 1144 else numbers if dynamic else (21,)
        formatted = {program.format(value, long) for value in values for long in (False, True)}
        frame_count = 21 if margin == 32 else 15
        for value, kerning in product(sorted(formatted), (False, True)):
            texts = phrases(program, rows, method, call, value)
            key = (texts[1], args[4], kerning)
            if key not in cache:
                cache[key] = bitmap_envelope(resources['fonts']['candidate'], texts[1], args[4],
                                              544, 306, kerning=kerning)
            envelope = cache[key]
            maximum_width = max(maximum_width, envelope['width'])
            assert envelope['width'] + 2 * margin < 1088
            for x in (0, 10, 70, 544, 1000, 1078, 1088):
                sampled = [x, *args[1:]]
                fixed, rectangle = corrected_ink(resources, method, call, envelope, sampled)
                assert rectangle['x'] == min(1088 - margin - rectangle['width'],
                                              max(margin, envelope_at(envelope, x, args[1], args[5])[0]
                                                  - (envelope['ink'][0] - envelope['bitmap'][0])))
                expected_y = max(11, args[1] - shift) if shift else args[1]
                assert rectangle['y'] == expected_y
                # Actual x velocity is 3*r-1.5 for every real r in [0,1).
                # At every visible frame n<=15/21, |dx|<=1.5*n; therefore
                # these rectangle bounds prove all random values, not a grid.
                for scale in (1, .75, .5):
                    assert (rectangle['x'] - 1.5 * frame_count) * scale >= 0
                    assert (rectangle['x'] + rectangle['width'] + 1.5 * frame_count) * scale <= 1088 * scale
                    assert rectangle['x'] <= fixed[0] < fixed[1] <= rectangle['x'] + rectangle['width']
            checked += 1
    resources['root'].mkdir(exist_ok=True)
    (resources['root'] / 'placement-proof.json').write_text(json.dumps({
        'calls': len(PLACEMENTS), 'phrase_kerning_variants': checked, 'max_width': maximum_width,
        'x_proof': 'Actual candidate snippet; whole bitmap plus 15/21-frame drift bound for all real randoms',
        'y_proof': 'Actual glyph alphabet; max(11,y-4) only false refund; caller bounds for true/talisman',
        'scales': [1, .75, .5], 'runtime': 'AIR not executed'}, ensure_ascii=False, indent=2) + '\n')
