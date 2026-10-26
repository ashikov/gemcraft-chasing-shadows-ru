"""Synthetic contracts for PR34's bounded floater font and recovery grammar."""
from copy import deepcopy

import pytest

from abc_inventory import ABCInventory
from pr34_ui_binding import floater_font_selector, validate_match_selection
from translation_catalog import load_catalog

PLAN = 'translations/plans/pr34-small-ui.json'
MOD_OFFSETS = (51, 215, 289, 363, 437, 511, 585, 659, 733, 807)
RECOVERY_OFFSETS = (244, 247, 252, 265, 339, 677, 702)
EXPECTED_IDS = {'static.3431', 'abc.4175.242.647', 'abc.4175.3490.21', 'abc.4175.139.181',
                *(f'abc.4175.334.{at}' for at in RECOVERY_OFFSETS),
                *(f'abc.4175.1537.{at}' for at in MOD_OFFSETS)}


def selected_font(text, matches):
    """Execute the emitted straight-line AVM2 against concrete string arguments."""
    strings = ['', 'Original', 'Alegreya', *(value for value, _ in matches)]
    snippet = floater_font_selector(2, 9, 1,
                                   [(i + 3, mode) for i, (_, mode) in enumerate(matches)], 8, 7)
    inv = ABCInventory(b'', {'tag_path': [0], 'tag_code': 82})
    operations = inv.instructions(snippet, 0)
    assert not inv.unsupported
    stack, local = [], {2: text, 5: {}}
    for _, opcode, args in operations:
        if opcode == 0xd2:
            stack.append(local[2])
        elif opcode == 0x62:
            stack.append(local[args[0]])
        elif opcode == 0x2c:
            stack.append(strings[args[0]])
        elif opcode == 0x56:
            values = stack[-args[0]:]
            del stack[-args[0]:]
            stack.append(values)
        elif opcode == 0x27:
            stack.append(False)
        elif opcode == 0x24:
            stack.append(args[0])
        elif opcode == 0x46:
            assert args == [8, 1]
            value, receiver = stack.pop(), stack.pop()
            stack.append(receiver.find(value))
        elif opcode == 0xab:
            b, a = stack.pop(), stack.pop()
            stack.append(a == b)
        elif opcode == 0xa9:
            b, a = stack.pop(), stack.pop()
            stack.append(int(a) | int(b))
        elif opcode == 0x73:
            stack.append(int(stack.pop()))
        elif opcode == 0x66:
            assert args == [7]
            key, receiver = stack.pop(), stack.pop()
            stack.append(receiver[key])
        elif opcode == 0x61:
            assert args == [9]
            value, receiver = stack.pop(), stack.pop()
            receiver['font'] = value
        else:
            raise AssertionError(hex(opcode))
    assert stack == []
    return local[5]['font']


def test_selector_changes_only_exact_messages_and_explicit_dynamic_prefixes():
    matches = [('Опыт выносливости сохранён.', 'exact'),
               ('Прежний рекорд опыта поля: ', 'prefix')]
    for text in ('Опыт выносливости сохранён.', 'Прежний рекорд опыта поля: 0',
                 'Прежний рекорд опыта поля: 1.0000000 e12'):
        assert selected_font(text, matches) == 'Alegreya'
    for text in ('Original text', '', '1', 'Другое русское сообщение',
                 'Опыт выносливости сохранён. Далее', ' Прежний рекорд опыта поля: 1',
                 'Прежний рекорд опыта поля:'):
        assert selected_font(text, matches) == 'Original'


def test_plan_selects_only_twenty_one_reviewed_ui_fragments():
    plan = load_catalog(PLAN)
    assert set(plan['entry_ids']) == EXPECTED_IDS
    assert len(EXPECTED_IDS) == 21
    assert plan['empty_abc_strings'] == ['abc.4175.334.690']
    assert set(plan['layouts']) == EXPECTED_IDS
    assert plan['layouts']['static.3431'] == {
        'font_id': 255, 'height': 120, 'max_width': 300, 'wrap': False}
    assert {site['kind'] for site in plan['abc_font_bindings']} == {
        'small_ui_floater_font', 'recovery_prefix', 'field'}
    match = next(site for site in plan['abc_font_bindings']
                 if site['kind'] == 'small_ui_floater_font')
    validate_match_selection(match['matches'])


@pytest.mark.parametrize('change', [
    lambda matches: matches.pop(),
    lambda matches: matches.append(deepcopy(matches[0])),
    lambda matches: matches[0].update(mode='prefix'),
    lambda matches: matches[0].update(source_sha256='bad'),
    lambda matches: matches[0].update(entry_id='abc.4175.1537.876'),
])
def test_selector_rejects_widened_or_stale_match_selection(change):
    plan = load_catalog(PLAN)
    matches = deepcopy(next(site['matches'] for site in plan['abc_font_bindings']
                            if site['kind'] == 'small_ui_floater_font'))
    change(matches)
    with pytest.raises(ValueError):
        validate_match_selection(matches)
