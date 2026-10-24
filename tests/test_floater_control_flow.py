"""Post-call typography must not run when a branch skips panel creation."""
from abc_font_binding import _bodies
from abc_patch import _u30
from localization_plan import apply_plan_font_bindings
from test_localization_plan import abc, inventory, site


def test_conditional_floater_skip_does_not_dereference_a_missing_panel():
    # Synthetic analogue of the SC notification's `if screenStep != 30`.
    creation = b'\xd0' * 9 + b'\x4f' + _u30(3841) + b'\x08'
    code = b'\x26\x11' + len(creation).to_bytes(3, 'little') + creation + b'\x47'
    raw = abc().replace(b'\0\2\1\0\1\4\x2c\1\x29\x47',
                        b'\0\x09\1\0\1' + _u30(len(code)) + code)
    binding = {**site(), 'kind': 'floater', 'expected_opcode': 0x47,
               'instruction_index': 12,
               'receiver_guards': [{'instruction_index': 11, 'opcode': 0x4f,
                                    'operands': [3841, 8]}],
               'panel_receiver_hex': 'd0'}
    result, counts = apply_plan_font_bindings(raw, raw, [binding])
    inv = inventory(result)
    operations = inv.instructions(_bodies(result)[0][3], 0)
    by_offset = {offset: (opcode, args) for offset, opcode, args in operations}
    assert counts == {'0': 1}
    branch, next_instruction = operations[1], operations[2]
    target = next_instruction[0] + branch[2][0]
    # Skipping creation also skips all field/panel reads; an empty panel list
    # remains valid. The fallthrough still applies the requested typography.
    assert by_offset[target] == (0x47, [])
    call_index = next(i for i, (_, opcode, args) in enumerate(operations)
                      if opcode == 0x4f and args == [3841, 8])
    assert operations[call_index + 1][0] < target
    assert operations[call_index + 1][1] == 0xd0
    assert sum(opcode == 0x4f and args == [3841, 8]
               for _, opcode, args in operations) == 1
    assert _bodies(result)[1][2:] == _bodies(raw)[1][2:]
