"""Synthetic cumulative plans and guarded font bindings; no game resources."""
import copy
import math

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCInventory
from abc_patch import _u30
from localization_plan import apply_plan_font_bindings, merge_localization_plans


def workspace():
    return {'entries': [
        {'id': 'title', 'category': 'static', 'location': {'tag_path': [2]}},
        {'id': 'first', 'category': 'actionscript', 'location': {
            'tag_path': [4], 'method_index': 0, 'code_offset': 0}},
        {'id': 'second', 'category': 'actionscript', 'location': {
            'tag_path': [4], 'method_index': 0, 'code_offset': 3}},
    ]}


def plan(ids):
    return {'schema_version': 1, 'game_sha256': 'a' * 64, 'entry_ids': ids,
            'fonts': [255, 1], 'layouts': {key: {'font_id': 1} for key in ids if key != 'title'},
            'unconfirmed': []}


def test_merge_preserves_settings_and_orders_resources_without_mutation():
    plans = [plan(['title', 'first']), plan(['second'])]
    old = copy.deepcopy(plans)
    merged = merge_localization_plans(plans, workspace())
    assert merged['entry_ids'] == ['title', 'first', 'second']
    assert merged['fonts'] == [1, 255]
    assert set(merged['layouts']) == {'first', 'second'}
    assert plans == old
    merged['layouts']['first']['font_id'] = 999
    assert plans == old


@pytest.mark.parametrize('problem', ['id', 'address', 'sha', 'layout', 'binding'])
def test_merge_rejects_conflicts(problem):
    plans, work = [plan(['title', 'first']), plan(['second'])], workspace()
    if problem == 'id':
        plans[1] = plan(['first'])
    elif problem == 'address':
        work['entries'][2]['location'] = copy.deepcopy(work['entries'][1]['location'])
    elif problem == 'sha':
        plans[1]['game_sha256'] = 'b' * 64
    elif problem == 'layout':
        plans[1]['layouts']['first'] = {'font_id': 1}
    else:
        plans[0]['abc_font_bindings'] = [site()]
        plans[1]['abc_font_bindings'] = [{**site(), 'expected_opcode': 2}]
    with pytest.raises(ValueError):
        merge_localization_plans(plans, work)


def site():
    return {'tag_path': [4], 'method_index': 0, 'instruction_index': 1,
            'expected_opcode': 0x29, 'expected_operands': [], 'receiver_hex': 'd0',
            'receiver_guards': [{'instruction_index': 0, 'opcode': 0x2c, 'operands': [1]}],
            'kind': 'field', 'extra_stack': 5}


def abc(*, floater_fields=False):
    # Large authored pools exercise multi-byte QName operands used by the helper.
    names = {839: 'TextFormat', 5569: 'embedFonts', 5573: 'defaultTextFormat', 6921: 'setTextFormat',
             3366: 'plateWidth', 3367: 'plateHeight', 5237: 'Math', 5327: 'ceil',
             5359: 'width', 5360: 'height', 91: 'McFloaterPanel', 3355: 'textfields',
             3356: 'nextTfPos', 5240: 'max', 1303: 'y'}
    strings = ['Synthetic'] + [names.get(index, f'name{index}') for index in range(1, 6922)]
    pool = b'\1\1\1' + _u30(len(strings) + 1)
    for text in strings:
        raw = text.encode()
        pool += _u30(len(raw)) + raw
    pool += (b'\2\x16\0\2\1\1' if floater_fields else b'\2\x16\0\1')
    pool += _u30(6923 if floater_fields else 6922)
    for index in range(1, 6922):
        pool += b'\7\1' + _u30(index + 1)
    if floater_fields:
        pool += b'\x1b\1'
    methods = b'\2' + b'\0\0\1\0' * 2
    bodies = b'\2'
    for method, code in enumerate((b'\x2c\1\x29\x47', b'\x47')):
        bodies += _u30(method) + b'\2\1\0\1' + _u30(len(code)) + code + b'\0\0'
    return b'\x10\0\x2e\0' + pool + methods + b'\0\0\0' + bodies


def inventory(raw):
    inv = ABCInventory(raw, {'tag_path': [4], 'tag_code': 82})
    inv.parse()
    return inv


def test_guarded_binding_preserves_other_method_and_is_deterministic():
    raw = abc()
    result, counts = apply_plan_font_bindings(raw, raw, [site()])
    assert counts == {'0': 1}
    assert apply_plan_font_bindings(raw, raw, [site()]) == (result, counts)
    before, after = _bodies(raw), _bodies(result)
    assert raw[before[1][0]:before[1][1]] == result[after[1][0]:after[1][1]]
    assert after[0][2][0] == before[0][2][0] + 5
    assert inventory(result).strings[-3:] == ['GCCS Alegreya', 'font', '']


@pytest.mark.parametrize('change', [
    {'expected_opcode': 2}, {'expected_operands': [1]}, {'instruction_index': 99},
    {'receiver_hex': '10ffffff'}, {'receiver_hex': '30'}, {'extra_stack': -1},
    {'receiver_guards': [{'instruction_index': 0, 'opcode': 0x2c, 'operands': [2]}]},
])
def test_binding_rejects_stale_guards_or_unsafe_receiver(change):
    raw = abc()
    with pytest.raises(ValueError):
        apply_plan_font_bindings(raw, raw, [{**site(), **change}])


def test_binding_handles_translated_pushstring_operand_and_rejects_opcode_changes():
    raw = abc()
    # Existing pool entry, longer encoded operand; instruction position stays stable.
    translated = raw.replace(b'\x2c\1\x29\x47', b'\x2c\x81\0\x29\x47')
    # Update code length in the selected body too.
    translated = translated.replace(b'\0\2\1\0\1\4\x2c', b'\0\2\1\0\1\5\x2c')
    result, _ = apply_plan_font_bindings(raw, translated, [site()])
    assert inventory(result).bodies[0][2].startswith(b'\x2c\x81\0')
    with pytest.raises(ValueError, match='structure'):
        apply_plan_font_bindings(raw, raw.replace(b'\x2c\1\x29\x47', b'\x24\1\x29\x47'), [site()])


def test_empty_binding_selection_leaves_bytes_unchanged():
    raw = abc()
    assert apply_plan_font_bindings(raw, raw, []) == (raw, {})


def test_multiple_receivers_share_one_guarded_site_and_stack_budget():
    raw = abc()
    binding = site()
    del binding['receiver_hex']
    binding['receivers_hex'] = ['d0', 'd1']
    result, counts = apply_plan_font_bindings(raw, raw, [binding])
    single, _ = apply_plan_font_bindings(raw, raw, [site()])
    assert counts == {'0': 1}
    assert len(_bodies(result)[0][3]) > len(_bodies(single)[0][3])
    assert _bodies(result)[0][2] == _bodies(single)[0][2]


def test_merge_combines_unconfirmed_and_sorts_binding_addresses():
    first, second = plan(['first']), plan(['second'])
    first['unconfirmed'], second['unconfirmed'] = ['uncertain-a'], ['uncertain-a', 'uncertain-b']
    first['abc_font_bindings'] = [{**site(), 'instruction_index': 3}]
    second['abc_font_bindings'] = [site()]
    merged = merge_localization_plans([first, second], workspace())
    assert merged['unconfirmed'] == ['uncertain-a', 'uncertain-b']
    assert [binding['instruction_index'] for binding in merged['abc_font_bindings']] == [1, 3]


def test_merge_rejects_static_and_edit_text_address_collision():
    work = workspace()
    work['entries'][2].update(category='editable', location={'tag_path': [2]})
    with pytest.raises(ValueError, match='address'):
        merge_localization_plans([plan(['title']), plan(['second'])], work)


def test_binding_order_is_deterministic_and_resource_budget_uses_maximum():
    raw = abc()
    late = {**site(), 'instruction_index': 2, 'expected_opcode': 0x47,
            'expected_operands': [], 'extra_stack': 7}
    forward = apply_plan_font_bindings(raw, raw, [site(), late])
    assert forward == apply_plan_font_bindings(raw, raw, [late, site()])
    assert forward[1] == {'0': 2}
    assert _bodies(forward[0])[0][2][0] == 9
    assert inventory(forward[0]).strings.count('GCCS Alegreya') == 1


def test_binding_rejects_duplicate_or_mixed_tag_sites():
    raw = abc()
    for sites in ([site(), site()], [site(), {**site(), 'tag_path': [5]}]):
        with pytest.raises(ValueError):
            apply_plan_font_bindings(raw, raw, sites)


def test_binding_requires_expected_resource_names():
    raw = abc()
    wrong = raw.replace(b'defaultTextFormat', b'defaultTextFormaX')
    with pytest.raises(ValueError, match='property'):
        apply_plan_font_bindings(wrong, wrong, [site()])


def test_resolver_can_share_pool_extension_with_an_existing_screen():
    from abc_font_binding import patch_abc_font_bindings
    from localization_plan import resolve_plan_font_bindings

    raw = abc()
    build, increments, counts = resolve_plan_font_bindings(raw, raw, [site()])

    def cumulative(family, font):
        sites = build(family, font)
        sites[1] = [(0, b'\x02')]
        return sites

    result = patch_abc_font_bindings(raw, cumulative, extra_stack=increments)
    assert counts == {'0': 1}
    assert inventory(result).bodies[1][2] == b'\x02\x47'
    assert inventory(result).strings.count('GCCS Alegreya') == 1


def test_merge_accepts_optional_layouts_for_selected_static_resources():
    selected = plan(['title', 'first'])
    selected['layouts']['title'] = {'font_id': 1, 'height': 400, 'max_width': 5000}
    merged = merge_localization_plans([selected, plan(['second'])], workspace())
    assert merged['layouts']['title'] == selected['layouts']['title']


def panel_site(kind, position=1):
    return {**site(), 'kind': kind, 'instruction_index': position,
            'expected_opcode': 0x29 if position == 1 else 0x47,
            'extra_stack': 2, 'original_font_string_index': 1,
            'original_font_family': 'Synthetic'}


def test_panel_font_is_set_before_layout_and_original_family_restored_after():
    from abc_font_binding import extend_font_pool
    raw = abc()
    sites = [panel_site('panel_format_before'), panel_site('panel_format_after', 2)]
    result, counts = apply_plan_font_bindings(raw, raw, sites)
    _, family, font = extend_font_pool(raw)
    code = inventory(result).bodies[0][2]
    assert b'\xd0\x2c' + _u30(family) + b'\x61' + _u30(font) in code
    assert b'\xd0\x2c\1\x61' + _u30(font) in code
    assert counts == {'0': 2}
    assert _bodies(result)[0][2][0] == 4


@pytest.mark.parametrize('change', [
    {'original_font_string_index': 99999}, {'original_font_family': 'Wrong'},
    {'receiver_hex': 'd029'}, {'receiver_hex': 'd0d1'}, {'receiver_hex': 'd06101'},
    {'receiver_hex': '24'}, {'receiver_hex': '2a'}, {'extra_stack': 1},
])
def test_panel_binding_rejects_stale_restoration_or_unsafe_receivers(change):
    raw = abc()
    with pytest.raises(ValueError):
        apply_plan_font_bindings(raw, raw, [{**panel_site('panel_format_after'), **change}])


def test_receiver_runtime_property_consumes_its_index_and_leaves_one_value():
    from types import SimpleNamespace
    from localization_plan import _receiver_peak

    inv = SimpleNamespace(multinames=[None, {'kind': 27}])
    decoded = [(0, 0xD0, []), (1, 0x24, [1]), (3, 0x66, [1])]
    assert _receiver_peak(decoded, inv) == 2
    with pytest.raises(ValueError, match='stack'):
        _receiver_peak([(0, 0xD0, []), (1, 0x66, [1])], inv)
    with pytest.raises(ValueError, match='property'):
        _receiver_peak([(0, 0x60, [1])], inv)


def test_field_receiver_rejects_straight_line_writes_and_extra_stack_values():
    raw = abc()
    for receiver in ('d06101', 'd0d1', 'd02a'):
        with pytest.raises(ValueError):
            apply_plan_font_bindings(raw, raw, [{**site(), 'receiver_hex': receiver}])


def floater_fixture():
    raw = abc()
    code = b'\xd0' * 9 + b'\x4f' + _u30(3841) + b'\x08\x47'
    raw = raw.replace(b'\0\2\1\0\1\4\x2c\1\x29\x47',
                      b'\0\x09\1\0\1' + _u30(len(code)) + code)
    binding = {**site(), 'kind': 'floater', 'expected_opcode': 0x47, 'instruction_index': 10,
               'receiver_guards': [{'instruction_index': 9, 'opcode': 0x4f,
                                    'operands': [3841, 8]}], 'panel_receiver_hex': 'd0'}
    return raw, binding


def test_floater_resizes_private_plate_after_field_binding():
    raw, binding = floater_fixture()
    result, counts = apply_plan_font_bindings(raw, raw, [binding])
    ops = inventory(result).instructions(_bodies(result)[0][3], 0)
    assert (0x61, [3366]) in [op[1:] for op in ops]
    assert (0x61, [3367]) in [op[1:] for op in ops]
    assert (0x46, [5327, 1]) in [op[1:] for op in ops]
    assert (0x24, [20]) in [op[1:] for op in ops]
    assert (0x24, [5]) in [op[1:] for op in ops]
    assert counts == {'0': 1}
    assert _bodies(result)[0][2][0] == 14


def test_floater_binding_rejects_wrong_panel_receiver_names_and_source_call():
    raw, binding = floater_fixture()
    for change in ({'panel_receiver_hex': 'd06101'}, {'panel_receiver_hex': 'd0d1'},
                   {'panel_receiver_hex': ''}, {'extra_stack': 3}):
        with pytest.raises(ValueError):
            apply_plan_font_bindings(raw, raw, [{**binding, **change}])
    wrong = raw.replace(b'plateWidth', b'plateWidtX')
    with pytest.raises(ValueError, match='property'):
        apply_plan_font_bindings(wrong, wrong, [binding])
    wrong = raw.replace(b'\x4f' + _u30(3841) + b'\x08', b'\x4f' + _u30(3841) + b'\x07')
    binding['receiver_guards'] = []
    with pytest.raises(ValueError, match='floater'):
        apply_plan_font_bindings(wrong, wrong, [binding])


def test_merge_keeps_explicit_coverage_blockers_without_duplicate_claims():
    first, second = plan(['title', 'first']), plan(['second'])
    first['coverage_blockers'] = ['Shared content']
    second['coverage_blockers'] = ['Shared content', 'Unknown context']
    assert merge_localization_plans([first, second], workspace())['coverage_blockers'] == ['Shared content', 'Unknown context']
    second['coverage_blockers'] = 'invalid'
    with pytest.raises(ValueError, match='coverage'):
        merge_localization_plans([first, second], workspace())


def test_merge_preserves_empty_abc_strings_and_rejects_duplicates():
    first, second = plan(['first']), plan(['second'])
    first['empty_abc_strings'] = ['suffix']
    merged = merge_localization_plans([first, second], workspace())
    assert merged['empty_abc_strings'] == ['suffix']
    second['empty_abc_strings'] = ['suffix']
    with pytest.raises(ValueError, match='empty ABC'):
        merge_localization_plans([first, second], workspace())


def test_merge_rejects_empty_abc_string_that_is_also_translated():
    selected = plan(['first'])
    selected['empty_abc_strings'] = ['first']
    with pytest.raises(ValueError, match='empty ABC'):
        merge_localization_plans([selected], workspace())


def test_merge_rejects_empty_abc_string_translated_by_a_later_plan():
    first, second = plan(['first']), plan(['second'])
    first['empty_abc_strings'] = ['second']
    with pytest.raises(ValueError, match='empty ABC'):
        merge_localization_plans([first, second], workspace())


def floater_fields_fixture():
    raw = abc(floater_fields=True)
    # Synthetic direct constructor, eleven arguments, and a saved panel.
    code = b'\xd0' * 12 + b'\x4a' + _u30(91) + b'\x0b\x80' + _u30(91) + b'\x63\x06\x47'
    raw = raw.replace(b'\0\2\1\0\1\4\x2c\1\x29\x47',
                      b'\0\x0c\x07\0\1' + _u30(len(code)) + code)
    binding = {**site(), 'kind': 'floater_fields', 'instruction_index': 15,
               'expected_opcode': 0x47, 'expected_operands': [], 'receiver_guards': [],
               'panel_receiver_hex': '6206', 'extra_stack': 8}
    del binding['receiver_hex']
    binding['receivers_hex'] = [(b'\x62\x06\x66' + _u30(3355) + b'\x24' + bytes([row])
                                 + b'\x66' + _u30(6922)).hex() for row in range(3)]
    return raw, binding


def test_floater_fields_reflows_all_rows_after_font_selection():
    raw, binding = floater_fields_fixture()
    result, counts = apply_plan_font_bindings(raw, raw, [binding])
    assert apply_plan_font_bindings(raw, raw, [binding]) == (result, counts)
    assert counts == {'0': 1}
    before, after = _bodies(raw), _bodies(result)
    assert raw[before[1][0]:before[1][1]] == result[after[1][0]:after[1][1]]
    assert after[0][2][1:] == before[0][2][1:]
    ops = [op[1:] for op in inventory(result).instructions(after[0][3], 0)]
    fonts = [index for index, op in enumerate(ops) if op == (0x4f, [6921, 1])]
    assert len(fonts) == 3
    geometry = ops[fonts[-1] + 1:-1]
    assert not any(op in (0x63, 0xd4, 0xd5, 0xd6, 0xd7) for op, _ in geometry)
    assert not any(args == [3356] for _, args in geometry)
    rows = [{5359: width, 5360: height} for width, height in [(31.2, 15), (42, 19), (20, 12)]]
    panel = {3355: rows, 3356: 999, 3366: 999, 3367: 999}
    stack, peak = [], 0
    for opcode, operands in geometry:
        if opcode == 0x62:
            assert operands == [6]
            stack.append(panel)
        elif opcode == 0x24:
            stack.append(operands[0])
        elif opcode == 0x60:
            assert operands == [5237]
            stack.append(math)
        elif opcode == 0x66:
            key = stack.pop() if operands == [6922] else operands[0]
            stack.append(stack.pop()[key])
        elif opcode == 0x61:
            value, receiver = stack.pop(), stack.pop()
            receiver[operands[0]] = value
        elif opcode == 0x46:
            argc = operands[1]
            args = stack[-argc:]
            del stack[-argc:]
            assert stack.pop() is math
            assert operands[0] in (5327, 5240)
            stack.append(math.ceil(*args) if operands[0] == 5327 else max(*args))
        elif opcode in (0xa0, 0xa1):
            right, left = stack.pop(), stack.pop()
            stack.append(left + right if opcode == 0xa0 else left - right)
        else:
            pytest.fail(f'Unexpected geometry opcode {opcode:x}')
        peak = max(peak, len(stack))
    assert stack == []
    assert peak <= binding['extra_stack']
    assert [row[1303] for row in rows] == [2, 14, 30]
    assert (panel[3366], panel[3356], panel[3367]) == (62, 999, 45)


@pytest.mark.parametrize('change', [
    {'panel_receiver_hex': 'd0'}, {'panel_receiver_hex': '6205'},
    {'receivers_hex': ['d0'] * 3}, {'receivers_hex': ['d0'] * 2}, {'extra_stack': 7},
])
def test_floater_fields_rejects_unsafe_binding_contract(change):
    raw, binding = floater_fields_fixture()
    with pytest.raises(ValueError):
        apply_plan_font_bindings(raw, raw, [{**binding, **change}])


@pytest.mark.parametrize(('old', 'new'), [
    (b'\x4a' + _u30(91) + b'\x0b', b'\x4a' + _u30(91) + b'\x0a'),
    (b'\x80' + _u30(91), b'\x80' + _u30(92)),
    (b'\x63\x06', b'\x63\x05'),
    (b'plateHeight', b'plateHeighX'), (b'plateWidth', b'plateWidtX'),
    (b'textfields', b'textfieldX'), (b'McFloaterPanel', b'McFloaterPaneX'),
    (b'\x1b\1', b'\x1c\1'),
])
def test_floater_fields_checks_source_without_optional_guards(old, new):
    raw, binding = floater_fields_fixture()
    wrong = raw.replace(old, new)
    with pytest.raises(ValueError):
        apply_plan_font_bindings(wrong, wrong, [binding])
