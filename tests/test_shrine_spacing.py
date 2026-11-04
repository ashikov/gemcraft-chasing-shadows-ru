"""Synthetic checks for the sole shrine multiplier spacing adaptation."""
from abc_font_binding import extend_font_pool
from tests.test_abc_font_binding import abc, inventory


def test_spacing_pool_is_opt_in_and_preserves_bodies():
    raw = abc(b'\x47')
    plain, _, _ = extend_font_pool(raw)
    spaced, _, font = extend_font_pool(raw, shrine_spacing=True)
    before, after = inventory(plain), inventory(spaced)
    assert after.multiname(font + 1) == 'letterSpacing'
    assert [row[2] for row in before.bodies] == [row[2] for row in after.bodies]
    assert after.strings[:len(before.strings)] == before.strings
    assert after.multinames[:len(before.multinames)] == before.multinames


def execute_spacing(text):
    from abc_inventory import ABCInventory
    from localization_plan import shrine_spacing_code
    field = {'text': text, 'y': 14, 'height': 31.2}
    panel = {'textfields': [field], 'nextTfPos': -100, 'h': -100}
    calls, stack = [], []
    names = {3355: 'textfields', 5241: 'length', 5329: 'text',
             1303: 'y', 5360: 'height', 3708: 'nextTfPos', 3710: 'h',
             9000: 'letterSpacing'}
    inv = ABCInventory(b'', {'tag_path': [0], 'tag_code': 82})
    for _, opcode, args in inv.instructions(shrine_spacing_code(9000), 0):
        if opcode == 0xd1:
            stack.append(panel)
        elif opcode == 0x24:
            stack.append(args[0])
        elif opcode == 0x2a:
            stack.append(stack[-1])
        elif opcode == 0x5d:
            stack.append('scope')
        elif opcode == 0x4a:
            assert args == [839, 0]
            assert stack.pop() == 'scope'
            stack.append({})
        elif opcode == 0x66:
            if args[0] == 6922:
                index, obj = stack.pop(), stack.pop()
                stack.append(obj[index])
            else:
                obj = stack.pop()
                stack.append(len(obj) if args[0] == 5241 else obj[names[args[0]]])
        elif opcode == 0x61:
            value, obj = stack.pop(), stack.pop()
            obj[names[args[0]]] = value
        elif opcode in (0xa0, 0xa1, 0xa3):
            b, a = stack.pop(), stack.pop()
            stack.append(a + b if opcode == 0xa0 else a - b if opcode == 0xa1 else a / b)
        elif opcode == 0x4f:
            assert args == [6921, 3]
            end, start, fmt, obj = stack.pop(), stack.pop(), stack.pop(), stack.pop()
            assert obj is field
            calls.append((start, end, fmt))
        else:
            raise AssertionError(hex(opcode))
    assert stack == []
    return field, panel, calls


def test_only_existing_spaces_are_formatted_and_following_layout_uses_final_height():
    from localization_plan import SHRINE_PREFIX, SHRINE_SUFFIX
    for number in ('1.00', '1.00 e12', '9.876 e123'):
        text = SHRINE_PREFIX + number + SHRINE_SUFFIX
        field, panel, calls = execute_spacing(text)
        expected = [len(SHRINE_PREFIX) - 1, len(SHRINE_PREFIX + number) + 1]
        assert [(start, end) for start, end, _ in calls] == [(i, i + 1) for i in expected]
        assert all(text[start:end] == ' ' for start, end, _ in calls)
        assert [fmt for _, _, fmt in calls] == [{'letterSpacing': 1.5}] * 2
        assert field['text'] == text
        assert panel['nextTfPos'] == field['y'] + field['height'] + 9
        assert panel['h'] == panel['nextTfPos'] + 8


def test_shrine_guard_rejects_other_consumers_and_changed_text():
    from types import SimpleNamespace
    import pytest
    from localization_plan import SHRINE_PREFIX, SHRINE_SUFFIX, validate_shrine_spacing
    names = {3355: 'textfields', 5241: 'length', 5329: 'text',
             839: 'TextFormat', 6921: 'setTextFormat', 1303: 'y',
             5360: 'height', 3708: 'nextTfPos', 3710: 'h'}
    before = SimpleNamespace(strings=['Deals gem specials and ',
                            'x gem damage to all enemies in range, ignores armor'],
                             multiname=names.get)
    after = SimpleNamespace(strings=[SHRINE_PREFIX, SHRINE_SUFFIX], multiname=names.get)
    old = [(0, 2, [])] * 16 + [(11878, 0x2c, [0]), (11901, 0x2c, [1])]
    old += [(11905, 0x27, []), (11906, 0x24, [10]), (11908, 0x20, []),
            (11909, 0x2d, [168]), (11912, 0x4f, [3729, 6]), (11916, 0xf0, [872])]
    site = {'method_index': 630, 'instruction_index': len(old) - 1,
            'receiver_hex': 'd166c606', 'extra_stack': 10}
    validate_shrine_spacing(before, after, old, old, site)
    with pytest.raises(ValueError, match='call'):
        validate_shrine_spacing(before, after, old, old, {**site, 'method_index': 791})
    after.strings[0] = SHRINE_PREFIX.rstrip()
    with pytest.raises(ValueError, match='translated phrase'):
        validate_shrine_spacing(before, after, old, old, site)


def test_combined_spacing_keeps_bitmap_indices_and_code_unchanged():
    from bitmap_font_binding import bitmap_font_selector, bitmap_selector_pool_indices
    raw = abc(b'\x47')
    indices = bitmap_selector_pool_indices(raw)
    baseline, family, _ = extend_font_pool(raw, bitmap_selector=True)
    combined, combined_family, _ = extend_font_pool(raw, bitmap_selector=True, shrine_spacing=True)
    before, after = inventory(baseline), inventory(combined)
    assert after.strings[:len(before.strings)] == before.strings
    assert after.multinames[:len(before.multinames)] == before.multinames
    assert after.multiname(len(after.multinames) - 1) == 'letterSpacing'
    assert after.multiname(indices['regexp_index']) == 'RegExp'
    assert after.multiname(indices['test_index']) == 'test'
    assert after.strings[indices['pattern_index']] == before.strings[indices['pattern_index']]
    assert bitmap_font_selector(family, **indices) == bitmap_font_selector(combined_family, **indices)
