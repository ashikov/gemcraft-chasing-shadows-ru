"""Synthetic contracts for the single kill-chain caption word-order repair."""
import copy
from types import SimpleNamespace

import pytest

from bitmap_notification_binding import validate_kill_chain_prefix
from localization_plan import resolve_plan_font_bindings


def site():
    return {'tag_path': [4175], 'method_index': 243, 'instruction_index': 117,
            'expected_opcode': 0xa0, 'expected_operands': [],
            'kind': 'kill_chain_prefix', 'extra_stack': 0}


def context():
    # Authored fixture: the number is already a formatted string. The caption
    # ends immediately before add; no numeric arithmetic belongs to this repair.
    old_ops = [(at, 0x02, []) for at in range(105)] + [
        (249, 0x60, [230]), (252, 0x66, [4156]), (255, 0x25, [778]),
        (258, 0x25, [170]), (261, 0xa0, []), (262, 0x24, [74]),
        (264, 0x60, [2133]), (267, 0xd0, []), (268, 0x66, [312]),
        (271, 0x46, [863, 0]), (275, 0x46, [4755, 1]),
        (279, 0x2c, [9585]), (282, 0xa0, []), (283, 0x2d, [206]),
        (286, 0x24, [12]), (288, 0x2c, [9586]), (291, 0x24, [0]),
        (293, 0x2f, [103]), (295, 0x24, [0]), (297, 0x2f, [24]),
        (299, 0x24, [45]), (301, 0x24, [0]), (303, 0x24, [10]),
        (305, 0x4f, [694, 13]), (309, 0x47, []),
    ]
    names = {230: 'GV', 4156: 'vfxEngine', 2133: 'NumberFormatter',
             312: 'monsterKillChainCounter', 863: 'g', 4755: 'format',
             694: 'createFloatingText', 5573: 'defaultTextFormat',
             5569: 'embedFonts', 839: 'TextFormat', 6921: 'setTextFormat'}
    strings = [''] * 9587
    strings[9585], strings[9586] = ' kills in a row', 'right'
    before = SimpleNamespace(
        strings=strings, method_labels={243: [{'owner': 'IngameCore', 'method': 'endKillChain'}]},
        multiname=lambda index: names[index], bodies=[(243, None, b'original')],
        instructions=lambda code, method: old_ops)
    new_ops = copy.deepcopy(old_ops)
    new_ops[116] = (279, 0x2c, [9587])
    after = SimpleNamespace(
        strings=strings + ['Убийств подряд: '], method_labels=copy.deepcopy(before.method_labels),
        multiname=lambda index: names[index], bodies=[(243, None, b'translated')],
        instructions=lambda code, method: new_ops)
    return before, after, old_ops, new_ops


def test_kill_chain_prefix_resolves_one_stack_neutral_swap(monkeypatch):
    before, after, old_ops, new_ops = context()
    validate_kill_chain_prefix(before, after, old_ops, new_ops, site())
    monkeypatch.setattr('localization_plan._inventory',
                        lambda raw: before if raw == b'original' else after)
    snippets, increments, counts = resolve_plan_font_bindings(b'original', b'translated', [site()])
    assert snippets(123, 456) == {243: [(282, b'\x2b')]}
    assert increments == {243: 0}
    assert counts == {'243': 1}
    # The swap acts only on two strings, keeping all 13 call arguments in order.
    for formatted in ('0', '1', '2', '4', '5', '20', '21', '22', '25', '30',
                      '99', '100', '101', '199', '200', '201', '1,000', '1.000 e12'):
        stack = ['receiver', 'x', 'y', formatted, after.strings[new_ops[116][2][0]]]
        initial_depth = len(stack)
        stack[-2], stack[-1] = stack[-1], stack[-2]
        assert len(stack) == initial_depth
        right, left = stack.pop(), stack.pop()
        stack.append(left + right)
        assert stack == ['receiver', 'x', 'y', f'Убийств подряд: {formatted}']


@pytest.mark.parametrize('change', [
    {'tag_path': [4174]}, {'method_index': 242}, {'instruction_index': 116},
    {'expected_opcode': 0xa1}, {'expected_operands': [1]}, {'extra_stack': 1},
    {'extra_stack': False}, {'receiver_hex': 'd0'}, {'receivers_hex': ['d0']},
])
def test_kill_chain_prefix_rejects_another_site_or_resources(change):
    before, after, old_ops, new_ops = context()
    with pytest.raises(ValueError, match='kill-chain'):
        validate_kill_chain_prefix(before, after, old_ops, new_ops, {**site(), **change})


@pytest.mark.parametrize('which', ['before', 'after'])
@pytest.mark.parametrize('offset', [249, 252, 255, 258, 261, 262, 264, 267, 268,
                                  271, 275, 279, 282, 283, 286, 288, 291, 293,
                                  295, 297, 299, 301, 303, 305])
def test_kill_chain_prefix_rejects_expression_or_call_drift(which, offset):
    before, after, old_ops, new_ops = context()
    ops = old_ops if which == 'before' else new_ops
    index = next(i for i, row in enumerate(ops) if row[0] == offset)
    at, opcode, operands = ops[index]
    ops[index] = (at, opcode ^ 1, operands)
    with pytest.raises(ValueError, match='kill-chain'):
        validate_kill_chain_prefix(before, after, old_ops, new_ops, site())


@pytest.mark.parametrize('which', ['before', 'after'])
@pytest.mark.parametrize('problem', ['owner', 'method', 'formatter', 'source', 'alignment', 'offset'])
def test_kill_chain_prefix_rejects_semantic_or_address_drift(which, problem):
    before, after, old_ops, new_ops = context()
    inv = before if which == 'before' else after
    if problem in ('owner', 'method'):
        inv.method_labels[243][0][problem] = 'Another'
    elif problem == 'formatter':
        original_name = inv.multiname
        inv.multiname = lambda index: 'Another' if index == 4755 else original_name(index)
    elif problem == 'source':
        inv.strings = list(inv.strings)
        inv.strings[9585 if which == 'before' else 9587] = 'Another caption'
    elif problem == 'alignment':
        inv.strings = list(inv.strings)
        inv.strings[9586] = 'center'
    else:
        ops = old_ops if which == 'before' else new_ops
        ops[117] = (281, 0xa0, [])
    with pytest.raises(ValueError, match='kill-chain'):
        validate_kill_chain_prefix(before, after, old_ops, new_ops, site())
