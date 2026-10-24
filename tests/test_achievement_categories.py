"""Authored AVM2 display-selector contracts; no game resources."""
from types import SimpleNamespace

import pytest

from achievement_category_binding import category_entry_ids, category_label_selector
from abc_inventory import ABCInventory
from localization_plan import merge_localization_plans


def execute_selector(code, identifier, source, labels):
    inv = ABCInventory(b'', {'tag_path': [0], 'tag_code': 82})
    stack, locals_ = ['text field', source], {2: identifier}
    peak = len(stack)
    for _, opcode, args in inv.instructions(code, 0):
        if opcode == 0x2c:
            stack.append(labels[args[0]])
        elif opcode == 0x56:
            values = stack[-args[0]:]
            del stack[-args[0]:]
            stack.append(values)
        elif opcode == 0x62:
            stack.append(locals_[args[0]])
        elif opcode == 0x24:
            stack.append(args[0])
        elif opcode == 0xa1:
            right, left = stack.pop(), stack.pop()
            stack.append(left - right)
        elif opcode == 0x66:
            index, values = stack.pop(), stack.pop()
            stack.append(values[index] if 0 <= index < len(values) else None)
        elif opcode == 0x2a:
            stack.append(stack[-1])
        elif opcode == 0x63:
            locals_[args[0]] = stack.pop()
        elif opcode in (0x76, 0x73):
            stack[-1] = int(bool(stack[-1]))
        elif opcode == 0x08:
            del locals_[args[0]]
        else:
            pytest.fail(f'Unexpected selector opcode {opcode:x}')
        peak = max(peak, len(stack))
    return stack, peak, locals_


@pytest.mark.parametrize('identifier', range(6, 72))
def test_selector_changes_only_display_for_every_category(identifier):
    labels = {100 + i: f'Категория {i}' for i in range(66)}
    code = category_label_selector(range(100, 166), property_index=6111, temporary_local=4)
    stack, peak, locals_ = execute_selector(code, identifier, 'technical key', labels)
    assert stack == ['text field', f'Категория {identifier - 6}']
    assert peak == 68
    assert locals_ == {2: identifier}


@pytest.mark.parametrize('identifier', [-100, -1, 0, 1, 2, 3, 4, 5, 72, 10000])
def test_selector_preserves_status_and_unknown_labels(identifier):
    code = category_label_selector(range(100, 166), property_index=6111, temporary_local=4)
    labels = {100 + i: f'Категория {i}' for i in range(66)}
    assert execute_selector(code, identifier, 'Original', labels)[0] == ['text field', 'Original']


def test_plan_retains_complete_display_selection_and_rejects_partial_or_duplicate():
    ids = category_entry_ids()
    workspace = {'entries': [{'id': identifier, 'category': 'actionscript',
                             'location': {'tag_path': [4175], 'method_index': 220,
                                          'code_offset': 11 + 15 * i}}
                            for i, identifier in enumerate(ids)]}
    plan = {'schema_version': 1, 'game_sha256': 'a' * 64, 'entry_ids': ids,
            'layouts': {identifier: {} for identifier in ids},
            'achievement_category_labels': ids}
    assert merge_localization_plans([plan], workspace)['achievement_category_labels'] == ids
    with pytest.raises(ValueError, match='achievement categor'):
        merge_localization_plans([{**plan, 'achievement_category_labels': ids[:-1]}], workspace)
    with pytest.raises(ValueError, match='achievement categor'):
        merge_localization_plans([{**plan, 'achievement_category_labels': ids + ids[:1]}], workspace)


def test_inventory_only_exposes_reviewed_category_initializer():
    from achievement_category_binding import is_category_label_literal
    inv = SimpleNamespace(method_labels={220: [{'owner': 'AchiCollection',
                                               'method': 'class initializer'}]})
    ops = [(8, 0x5e, [4550]), (11, 0x2c, [123]), (14, 0x61, [4550])]
    assert is_category_label_literal(inv, ops, 220, 1)
    assert not is_category_label_literal(inv, ops, 221, 1)
    ops[2] = (14, 0x61, [4551])
    assert not is_category_label_literal(inv, ops, 220, 1)


@pytest.mark.parametrize('selection', [None, 'abc.4175.220.11', [], True])
def test_plan_rejects_invalid_category_display_selection(selection):
    ids = category_entry_ids()
    workspace = {'entries': [{'id': identifier, 'category': 'actionscript',
                             'location': {'tag_path': [4175], 'method_index': 220,
                                          'code_offset': 11 + 15 * index}}
                            for index, identifier in enumerate(ids)]}
    plan = {'schema_version': 1, 'game_sha256': 'a' * 64, 'entry_ids': ids,
            'layouts': {identifier: {} for identifier in ids},
            'achievement_category_labels': selection}
    with pytest.raises(ValueError, match='achievement categor'):
        merge_localization_plans([plan], workspace)


@pytest.mark.parametrize('problem', ['owner', 'method', 'offset', 'previous', 'next', 'opcode'])
def test_category_inventory_rule_rejects_unrelated_uses(problem):
    from achievement_category_binding import is_category_label_literal
    inv = SimpleNamespace(method_labels={220: [{'owner': 'AchiCollection',
                                               'method': 'class initializer'}]})
    ops = [(8, 0x5e, [4550]), (11, 0x2c, [123]), (14, 0x61, [4550])]
    if problem in ('owner', 'method'):
        inv.method_labels[220][0][problem] = 'Other'
    elif problem == 'offset':
        ops[1] = (12, 0x2c, [123])
    elif problem == 'previous':
        ops[0] = (8, 0x60, [4550])
    elif problem == 'next':
        ops[2] = (14, 0x61, [4551])
    else:
        ops[1] = (11, 0x2d, [123])
    assert not is_category_label_literal(inv, ops, 220, 1)


def test_legacy_builder_rejects_display_adapter_before_reporting_translations():
    from addressed_localization import build_localization
    with pytest.raises(ValueError, match='Alegreya builder'):
        build_localization(b'', {}, {'achievement_category_labels': category_entry_ids()}, {})
