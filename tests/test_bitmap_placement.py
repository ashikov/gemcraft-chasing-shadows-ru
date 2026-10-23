"""Synthetic contracts for PR32's bounded post-call placement correction."""
import copy
import hashlib
from types import SimpleNamespace

import pytest

from abc_inventory import ABCInventory
from bitmap_notification_placement import (
    PLACEMENT_CALLS, bitmap_placement_code, placement_sites,
    validate_bitmap_placement_site,
)
from translation_catalog import load_catalog


def execute_placement(code, mc):
    """Small independent stack executor; no game resources or display API."""
    names = {4156: 'vfxEngine', 680: 'vfxes', 5241: 'length', 105: 'mc',
             1302: 'x', 1303: 'y', 5359: 'width'}
    previous = {'x': 999, 'y': 999, 'width': 999}
    engine = {'vfxes': [{'mc': previous}, {'mc': mc}]}
    stack, peak = ['preserved prefix'], 1
    inv = ABCInventory(b'', {'tag_path': [0], 'tag_code': 82})
    for _, op, args in inv.instructions(code, 0):
        if op == 0x60:
            stack.append({'vfxEngine': engine} if args == [230] else 'Math')
            assert args in ([230], [5237])
        elif op == 0x66:
            if args == [5492]:
                index, receiver = stack.pop(), stack.pop()
                stack.append(receiver[int(index)])
            else:
                receiver = stack.pop()
                stack.append(len(receiver) if args == [5241] else receiver[names[args[0]]])
        elif op in (0x24, 0x25):
            stack.append(args[0] - 256 if op == 0x24 and args[0] > 127 else args[0])
        elif op == 0x2a:
            stack.append(stack[-1])
        elif op == 0x2b:
            stack[-2:] = reversed(stack[-2:])
        elif op == 0xa1:
            right, left = stack.pop(), stack.pop()
            stack.append(left - right)
        elif op == 0x46:
            assert args in ([5240, 2], [5268, 2])
            right, left, receiver = stack.pop(), stack.pop(), stack.pop()
            assert receiver == 'Math'
            stack.append((max if args[0] == 5240 else min)(left, right))
        elif op == 0x61:
            value, receiver = stack.pop(), stack.pop()
            assert receiver is mc
            receiver[names[args[0]]] = value
        else:
            raise AssertionError(f'Unexpected placement opcode {op:x}')
        peak = max(peak, len(stack))
        assert stack[0] == 'preserved prefix'
    assert stack == ['preserved prefix']
    assert previous == {'x': 999, 'y': 999, 'width': 999}
    assert peak - 1 <= 7
    return mc


def without_placement_ops(inv, method, ops, *, require=False):
    """Remove only the exact reviewed snippets, retaining real branch targets."""
    from test_bitmap_completion import CALLS

    if method not in {m for m, _ in PLACEMENT_CALLS}:
        return list(ops)
    calls = [i for i, (_, op, args) in enumerate(ops)
             if op == 0x4f and inv.multiname(args[0]) in (
                 'createFloatingText', 'createFloatingTextOutgame')]
    assert len(calls) == len(CALLS[method])
    removed = set()
    for original_call, index in zip(CALLS[method], calls):
        key = (method, original_call)
        if key not in PLACEMENT_CALLS:
            continue
        expected = inv.instructions(bitmap_placement_code(*PLACEMENT_CALLS[key]), method)
        actual = ops[index + 1:index + 1 + len(expected)]
        matches = [(op, args) for _, op, args in actual] == [(op, args) for _, op, args in expected]
        if require:
            assert matches, key
        if matches:
            removed.update(range(index + 1, index + 1 + len(expected)))
    return [row for index, row in enumerate(ops) if index not in removed]


def test_exact_seventeen_sites_have_no_text_number_or_renderer_changes():
    expected = {(566, at) for at in (1841, 2162, 2495, 2887)}
    expected |= {(567, at) for at in (1960, 2270, 2606, 2988)}
    expected |= {(695, at) for at in (6275, 6408, 7124, 8017, 8986, 10165)}
    expected |= {(675, 1098), (675, 1208), (1144, 138)}
    assert set(PLACEMENT_CALLS) == expected
    sites = placement_sites()
    assert len(sites) == 17
    assert all(s['extra_stack'] == 7 and s['kind'] == 'bitmap_placement' for s in sites)
    plan = load_catalog('translations/plans/pr32-bitmap-completion.json')
    assert [s for s in plan['abc_font_bindings'] if s['kind'] == 'bitmap_placement'] == sites


@pytest.mark.parametrize('margin,shift', [(23, 0), (32, 0), (32, 4)])
def test_actual_width_clamp_preserves_inside_positions_and_all_other_fields(margin, shift):
    code = bitmap_placement_code(margin, shift)
    for width in (50, 73.5, 100, 138.5, 183, 194, 240, 400):
        for x in (-100, 0, 10, margin, 200, 544, 1000, 1088):
            mc = {'x': x, 'y': 573, 'width': width, 'height': 25, 'alpha': 1}
            assert execute_placement(code, mc) == {
                'x': min(1088 - margin - width, max(margin, x)),
                'y': 573 - shift, 'width': width, 'height': 25, 'alpha': 1}


@pytest.mark.parametrize('margin,shift', [(0, 0), (24, 0), (32, 3), (23, 4), (True, 0)])
def test_unreviewed_motion_budget_or_vertical_change_is_rejected(margin, shift):
    with pytest.raises(ValueError, match='placement'):
        bitmap_placement_code(margin, shift)


@pytest.mark.parametrize('y', [-100, -20, -13, -5, 0, 10, 11, 15, 20, 573])
def test_automatic_refund_shift_cannot_introduce_an_upper_exit_outside_the_window(y):
    mc = {'x': 544, 'y': y, 'width': 100}
    assert execute_placement(bitmap_placement_code(32, 4), mc)['y'] == max(11, y - 4)


@pytest.mark.parametrize('change', [
    {'tag_path': [4174]}, {'method_index': 1732}, {'call_offset': 5996},
    {'instruction_index': 0}, {'extra_stack': 0}, {'extra_stack': True},
    {'expected_opcode': 0}, {'kind': 'field'},
])
def test_wrong_address_fails_before_accessing_abc(change):
    site = {**placement_sites()[0], **change}
    with pytest.raises(ValueError, match='placement'):
        validate_bitmap_placement_site(None, None, [], [], site)


def guarded_context(monkeypatch):
    import bitmap_notification_placement as placement

    names = {230: 'GV', 4156: 'vfxEngine', 680: 'vfxes', 5241: 'length',
             105: 'mc', 1302: 'x', 1303: 'y', 5359: 'width',
             5237: 'Math', 5240: 'max', 5268: 'min', 694: 'createFloatingText'}
    old_ops = [(1732, 0x60, [230]), (1735, 0x66, [4156]),
               (1793, 0x2c, [1]), (1841, 0x4f, [694, 13]), (1845, 0xf0, [401])]
    before = SimpleNamespace(
        strings=['', 'Authored fixture'], multiname=lambda index: names[index],
        method_labels={566: [{'owner': 'IngameCalculator', 'method': 'isNew2x2BuildingBlocking'}]},
        bodies=[(352, 0, b'first wrapper'), (353, 0, b'second wrapper')])
    after = copy.copy(before)
    after.strings = ['', 'Authored fixture', 'Русский пример']
    after.method_labels = copy.deepcopy(before.method_labels)
    after.bodies = list(before.bodies)
    new_ops = copy.deepcopy(old_ops)
    new_ops[2] = (1793, 0x2c, [2])
    digest = placement._context_digest(before, old_ops[:4])
    monkeypatch.setattr(placement, '_SITES', {(566, 1841): (4, 240, [401], 0, digest)})
    monkeypatch.setattr(placement, '_WRAPPERS', {
        m: hashlib.sha256(code).hexdigest() for m, _, code in before.bodies})
    monkeypatch.setattr(placement, '_public_bitmap_access', lambda inv: None)
    return before, after, old_ops, new_ops, placement.placement_sites()[0]


def test_validated_context_returns_only_the_reviewed_placement(monkeypatch):
    context = guarded_context(monkeypatch)
    assert validate_bitmap_placement_site(*context) == bitmap_placement_code(23, 0)


@pytest.mark.parametrize('which', ['before', 'after'])
@pytest.mark.parametrize('problem', ['call', 'coordinate', 'continuation', 'owner', 'wrapper', 'public'])
def test_context_wrapper_and_public_access_drift_fail_closed(monkeypatch, which, problem):
    import bitmap_notification_placement as placement

    before, after, old_ops, new_ops, site = guarded_context(monkeypatch)
    inv = before if which == 'before' else after
    ops = old_ops if which == 'before' else new_ops
    if problem == 'call':
        ops[3] = (1841, 0x4f, [694, 12])
    elif problem == 'coordinate':
        ops[0] = (1732, 0x60, [5237])
    elif problem == 'continuation':
        ops[4] = (1845, 0xf0, [402])
    elif problem == 'owner':
        inv.method_labels = {566: [{'owner': 'Another', 'method': 'isNew2x2BuildingBlocking'}]}
    elif problem == 'wrapper':
        inv.bodies = [(352, 0, b'changed'), (353, 0, b'second wrapper')]
    else:
        def require_public(value):
            if value is inv:
                raise ValueError('Bitmap placement requires public properties')
        monkeypatch.setattr(placement, '_public_bitmap_access', require_public)
    with pytest.raises(ValueError, match='placement'):
        validate_bitmap_placement_site(before, after, old_ops, new_ops, site)
