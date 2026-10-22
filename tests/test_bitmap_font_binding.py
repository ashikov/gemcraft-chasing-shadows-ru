"""Synthetic selector checks; no extracted game resources."""
import re

import pytest

from abc_font_binding import extend_font_pool
from bitmap_font_binding import (
    CYRILLIC_PATTERN, bitmap_font_selector, bitmap_selector_pool_indices,
    validate_bitmap_font_site,
)
from tests.test_abc_font_binding import abc, inventory


def execute(code, inv, text, original='Original decorative family'):
    stack = ['TextFormat', original]
    locals_ = {4: text, 6: 24, 15: {'color': 0xFFAB34}}
    before = locals_.copy()
    initial_depth = peak = len(stack)

    def take(count):
        assert len(stack) >= count + 1, 'Operand stack underflow or prefix consumed'
        values = stack[-count:]
        del stack[-count:]
        return values

    for _, opcode, operands in inv.instructions(code, 0):
        if opcode == 0x2c:
            stack.append(inv.strings[operands[0]])
        elif opcode == 0x56:
            count = operands[0]
            stack.append(take(count))
        elif opcode == 0x60:
            assert inv.multiname(operands[0]) == 'RegExp'
            stack.append(re.compile)
        elif opcode == 0x42:
            assert operands == [2]
            constructor, pattern, flags = take(3)
            assert flags == ''
            stack.append(constructor(pattern))
        elif opcode == 0x62:
            stack.append(locals_[operands[0]])
        elif opcode == 0x46:
            assert inv.multiname(operands[0]) == 'test' and operands[1] == 1
            regexp, value = take(2)
            stack.append(bool(regexp.search(value)))
        elif opcode == 0x73:
            stack.append(int(take(1)[0]))
        elif opcode == 0x66:
            assert inv.multinames[operands[0]]['kind'] == 27, 'Runtime array index requires MultinameL'
            array, index = take(2)
            stack.append(array[index])
        else:
            raise AssertionError(f'Unexpected opcode {opcode}')
        peak = max(peak, len(stack))
        assert stack[0] == 'TextFormat', 'Preserved stack prefix changed'
    assert len(stack) == initial_depth, 'Selector must have zero net stack effect'
    assert peak - initial_depth == 3, 'Selector stack peak must be exactly +3'
    assert locals_ == before
    return stack


def selector_abc():
    # Synthetic QName plus public MultinameL, unlike a static QName lookup the
    # runtime form consumes the integer property name from the operand stack.
    pool = b'\1\1\1\3\1x\0\2\x16\2\2\1\1\3\7\1\1\x1b\1'
    raw = abc(b'\x47').replace(b'\1\1\1\2\1x\2\x16\0\1\2\x07\1\1', pool, 1)
    return raw, 2


@pytest.mark.parametrize('text,russian', [
    ('Warning', False), ('123.5 +10%', False), ('', False),
    ('Ёё', True), ('aБ 12', True), ('Мана: 0', True),
])
def test_selector_preserves_prefix_and_chooses_per_call(text, russian):
    raw, property_index = selector_abc()
    expanded, family, _ = extend_font_pool(raw, bitmap_selector=True)
    indices = bitmap_selector_pool_indices(raw)
    inv = inventory(expanded)
    assert inv.multinames[property_index] == {'kind': 27}
    code = bitmap_font_selector(family, **indices, property_index=property_index)
    assert execute(code, inv, text) == ['TextFormat', 'GCCS Alegreya' if russian else 'Original decorative family']
    assert execute(code, inv, 'Latin again') == ['TextFormat', 'Original decorative family']


def test_selector_pool_opt_in_is_deterministic_and_default_unchanged():
    raw = abc(b'\x47')
    default = extend_font_pool(raw)
    assert default == extend_font_pool(raw, bitmap_selector=False)
    expanded = extend_font_pool(raw, bitmap_selector=True)
    assert expanded == extend_font_pool(raw, bitmap_selector=True)
    inv = inventory(expanded[0])
    indices = bitmap_selector_pool_indices(raw)
    assert inv.strings[indices['pattern_index']] == CYRILLIC_PATTERN
    assert inv.strings[indices['empty_index']] == ''
    assert inv.multiname(indices['regexp_index']) == 'RegExp'
    assert inv.multiname(indices['test_index']) == 'test'
    assert inv.strings[:len(inventory(raw).strings)] == inventory(raw).strings


def test_bitmap_selector_rejects_an_unembedded_target_family():
    with pytest.raises(ValueError, match='Alegreya'):
        extend_font_pool(abc(b'\x47'), family='Unembedded font', bitmap_selector=True)


def test_incorrect_bitmap_site_fails_before_context_access():
    for site in ({'method_index': 0}, {'method_index': 1732, 'instruction_index': 64, 'receiver_hex': 'd0'}):
        with pytest.raises(ValueError, match='bitmap'):
            validate_bitmap_font_site(None, None, [], [], site)


def test_selector_qnames_use_public_and_builtin_namespaces():
    from abc_font_binding import _pool_sections
    from abc_inventory import ABCReader
    from bitmap_font_binding import AS3_NAMESPACE

    raw = abc(b'\x47')
    expanded, _, _ = extend_font_pool(raw, bitmap_selector=True)
    sections, namespaces = _pool_sections(expanded)
    inv = inventory(expanded)
    reader = ABCReader(expanded)
    reader.pos = sections['multiname'][1]
    qnames = []
    for _ in range(1, sections['multiname'][3]):
        assert reader.u8() == 7
        qnames.append((reader.u30(), reader.u30()))
    regexp, test = qnames[-2:]
    assert inv.strings[regexp[1]] == 'RegExp'
    assert namespaces[regexp[0]][0] == 0x16
    assert inv.strings[namespaces[regexp[0]][1]] == ''
    assert inv.strings[test[1]] == 'test'
    assert namespaces[test[0]][0] == 0x08
    assert inv.strings[namespaces[test[0]][1]] == AS3_NAMESPACE


@pytest.mark.local
def test_original_lifecycle_and_negative_context_guards():
    import copy
    import hashlib
    import os
    from pathlib import Path

    from abc_font_binding import insert_method_code
    from install_patch import ORIGINAL_SHA256
    from swf_geometry import read_swf

    path = os.environ.get('GEMCRAFT_ORIGINAL')
    if not path:
        pytest.skip('Set GEMCRAFT_ORIGINAL for the offline bitmap lifecycle check')
    original = Path(path).read_bytes()
    assert hashlib.sha256(original).hexdigest() == ORIGINAL_SHA256
    _, tags = read_swf(original)
    payload = tags[4175].payload
    raw = payload[payload.index(b'\0', 4) + 1:]
    inv = inventory(raw)
    code = next(code for method, _, code in inv.bodies if method == 1732)
    ops = inv.instructions(code, 1732)
    site = {'method_index': 1732, 'instruction_index': 64, 'receiver_hex': '6204',
            'expected_opcode': 0x62, 'expected_operands': [6]}
    validate_bitmap_font_site(inv, inv, ops, ops, site)
    assert ops[64][0] == 214
    for key, value in (('method_index', 1731), ('instruction_index', 63),
                       ('receiver_hex', '6205'), ('expected_operands', [5])):
        with pytest.raises(ValueError, match='bitmap'):
            validate_bitmap_font_site(inv, inv, ops, ops, {**site, key: value})
    changed_ops = copy.deepcopy(ops)
    changed_ops[37] = (ops[37][0], 0x27, [])
    with pytest.raises(ValueError, match='context'):
        validate_bitmap_font_site(inv, inv, changed_ops, changed_ops, site)
    changed_inv = copy.deepcopy(inv)
    changed_inv.strings[10038] = 'Another font'
    with pytest.raises(ValueError, match='font'):
        validate_bitmap_font_site(changed_inv, changed_inv, ops, ops, site)
    changed_raw = insert_method_code(raw, {1732: [(214, b'\x02')]})
    with pytest.raises(ValueError, match='changed body'):
        validate_bitmap_font_site(inv, inventory(changed_raw), ops, ops, site)
    # Selector adds no branches, locals, scope changes or exception handlers;
    # only its stack budget changes the header of the selected constructor.
    from abc_font_binding import _bodies, patch_abc_font_bindings

    indices = bitmap_selector_pool_indices(raw)
    patched = patch_abc_font_bindings(
        raw, lambda family, font: {1732: [(214, bitmap_font_selector(family, **indices))]},
        extra_stack={1732: 4}, bitmap_selector=True,
    )
    before_body, after_body = _bodies(raw)[1732], _bodies(patched)[1732]
    assert after_body[2] == [10, 18, 4, 5]
    assert before_body[4:] == after_body[4:]
    expanded_inv = inventory(patched)
    family = expanded_inv.strings.index('GCCS Alegreya')
    snippet = bitmap_font_selector(family, **indices)
    assert after_body[3] == code[:214] + snippet + code[214:]
    assert Path(path).read_bytes() == original


def test_existing_builtin_and_public_namespaces_are_reused():
    from abc_font_binding import _pool_sections
    from abc_patch import _u30
    from bitmap_font_binding import AS3_NAMESPACE

    strings = ['x', '', AS3_NAMESPACE]
    pool = b'\1\1\1' + _u30(len(strings) + 1)
    pool += b''.join(_u30(len(value.encode())) + value.encode() for value in strings)
    pool += b'\3\x16\2\x08\3\1\2\7\1\1'
    raw = abc(b'\x47').replace(b'\1\1\1\2\1x\2\x16\0\1\2\x07\1\1', pool, 1)
    expanded, _, _ = extend_font_pool(raw, bitmap_selector=True)
    assert _pool_sections(expanded)[1] == _pool_sections(raw)[1]
    assert inventory(expanded).strings.count(AS3_NAMESPACE) == 1
    assert [body[2] for body in inventory(expanded).bodies] == [body[2] for body in inventory(raw).bodies]


def test_interpreter_rejects_static_qname_as_runtime_array_index():
    raw = abc(b'\x47')
    expanded, family, _ = extend_font_pool(raw, bitmap_selector=True)
    code = bitmap_font_selector(family, **bitmap_selector_pool_indices(raw), property_index=1)
    with pytest.raises(AssertionError, match='MultinameL'):
        execute(code, inventory(expanded), 'Русский текст')


def test_interpreter_rejects_operand_stack_underflow():
    raw, property_index = selector_abc()
    with pytest.raises(AssertionError, match='underflow'):
        execute(bytes([0x66, property_index]), inventory(raw), 'text')
