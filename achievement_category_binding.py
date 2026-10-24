"""Translate achievement-button labels while preserving their shared tag keys."""
from abc_font_binding import _bodies, _inventory, _pool_sections, insert_method_code
from abc_patch import _u30
from install_patch import sha

CATEGORY_COUNT = 66
SOURCE_METHOD_SHA256 = {
    220: '3948933ee808831f7950bfa714194a2b0d7a66601286bedb8afae7519c59febe',
    221: 'b7c7226f6a64bf6c581ec2256716118b853195db23cf6f98109d4d68df6f56d7',
    1073: 'a8767b1e8107b69e02e4879474fc5aade1e200a82286079a82a1c73432e94475',
    2769: 'c97b2e92dd231ec82c38bdff7f6f793f37f093dacd5744b224370b94b916ab2b',
}


def category_entry_ids():
    return [f'abc.4175.220.{11 + 15 * index}' for index in range(CATEGORY_COUNT)]


def is_category_label_literal(inventory, instructions, method, position):
    """Expose only the reviewed dual-use literals, never change their key role."""
    if (method != 220 or {'owner': 'AchiCollection', 'method': 'class initializer'}
            not in inventory.method_labels.get(method, [])
            or position < 1 or position + 1 >= len(instructions)):
        return False
    offset, opcode, _ = instructions[position]
    index, remainder = divmod(offset - 11, 15)
    return (opcode == 0x2c and remainder == 0 and 0 <= index < CATEGORY_COUNT
            and instructions[position - 1] == (offset - 3, 0x5e, [4550 + index])
            and instructions[position + 1] == (offset + 3, 0x61, [4550 + index]))


def validate_category_selection(identifiers, selected):
    if (not isinstance(identifiers, list) or identifiers != category_entry_ids()
            or not set(identifiers) <= set(selected)):
        raise ValueError('Invalid achievement category display selection')


def category_label_selector(string_indices, *, property_index, temporary_local):
    """Map id-6 only at text assignment; absent ids retain the incoming label.

    A temporary local keeps the lookup result while [original, translated] is
    indexed by its truth value. Nonempty translations are required by the caller.
    The snippet is branchless, leaves the receiver below the label intact and
    clears its additional local before returning to the original assignment.
    """
    indices = list(string_indices)
    if len(indices) != CATEGORY_COUNT:
        raise ValueError('Expected all achievement category display strings')

    def op(opcode, *operands):
        return bytes([opcode]) + b''.join(_u30(value) for value in operands)

    return (b''.join(op(0x2c, index) for index in indices)
            + op(0x56, CATEGORY_COUNT) + op(0x62, 2) + b'\x24\x06\xa1'
            + op(0x66, property_index) + b'\x2a' + op(0x63, temporary_local)
            + op(0x56, 2) + op(0x62, temporary_local) + b'\x76\x73'
            + op(0x66, property_index) + op(0x08, temporary_local))


def apply_category_labels(original, translated, workspace, identifiers):
    """Append authored labels and adapt only BtnAchiFilter.tfLabel.text."""
    if not identifiers:
        return translated, {}
    validate_category_selection(identifiers, identifiers)
    before, after = _inventory(original), _inventory(translated)
    old_bodies, new_bodies = _bodies(original), _bodies(translated)
    if any(method not in old_bodies or sha(old_bodies[method][3]) != digest
           for method, digest in SOURCE_METHOD_SHA256.items()):
        raise ValueError('Unexpected achievement category source methods')
    if any(old_bodies[method][2:] != new_bodies[method][2:] for method in (220, 221)):
        raise ValueError('Achievement category keys or tag order changed')
    names = {3034: 'tfLabel', 5329: 'text', 95: 'id'}
    if (any(inv.multiname(index) != name for inv in (before, after)
            for index, name in names.items())
            or before.multinames[6111] != after.multinames[6111]
            or after.multinames[6111] != {'kind': 27}):
        raise ValueError('Unexpected achievement category display properties')
    from alegreya_localization import field_binding
    family_indices = [index for index, name in enumerate(after.strings) if name == 'GCCS Alegreya']
    font_indices = [index for index, item in enumerate(after.multinames)
                    if item and item['kind'] == 7 and after.multiname(index) == 'font']
    if len(family_indices) != 1 or len(font_indices) != 1:
        raise ValueError('Unexpected achievement button font binding')
    # PR9 already binds this one field before getlocal1. Preserve that exact
    # binding and every constructor instruction, including the separate id write.
    font_code = field_binding(bytes.fromhex('d066da17'), family_indices[0], font_indices[0])
    source_body = old_bodies[2769]
    expected_code = source_body[3][:36] + font_code + source_body[3][36:]
    expected_limits = [source_body[2][0] + 8, *source_body[2][1:]]
    body = new_bodies[2769]
    if (body[2] != expected_limits or body[3] != expected_code
            or body[4:] != source_body[4:]):
        raise ValueError('Unexpected achievement button resources or constructor')
    operations = after.instructions(body[3], 2769)
    assignments = [index for index, (_, opcode, args) in enumerate(operations)
                   if opcode == 0x61 and args == [5329]]
    if (len(assignments) != 1 or assignments[0] < 1
            or operations[assignments[0] - 1][1:] != (0xd1, [])):
        raise ValueError('Unexpected achievement category display assignment')
    entries = {row['id']: row for row in workspace['entries']}
    literals = {offset: before.strings[args[0]]
                for offset, opcode, args in before.instructions(old_bodies[220][3], 220)
                if opcode == 0x2c}
    labels = []
    for index, identifier in enumerate(identifiers):
        entry = entries[identifier]
        offset = 11 + 15 * index
        location = entry['location']
        if (location.get('tag_path') != [4175] or location.get('method_index') != 220
                or location.get('code_offset') != offset
                or entry['source'] != literals[offset]
                or entry['source_sha256'] != sha(literals[offset].encode('utf-8'))
                or entry.get('classification') != 'confirmed'
                or entry.get('context_confidence') != 'confirmed'
                or entry.get('status') not in ('draft', 'verified')
                or not isinstance(entry.get('translation'), str) or not entry['translation']):
            raise ValueError(f'Unconfirmed achievement category display label: {identifier}')
        labels.append(entry['translation'])
    strings = list(after.strings)
    additions = bytearray()
    indices = []
    for label in labels:
        if label not in strings:
            encoded = label.encode('utf-8')
            additions.extend(_u30(len(encoded)) + encoded)
            strings.append(label)
        indices.append(strings.index(label))
    sections, _ = _pool_sections(translated)
    start, entries_start, end, _ = sections['string']
    expanded = (translated[:start] + _u30(len(strings)) + translated[entries_start:end]
                + additions + translated[end:])
    offset = operations[assignments[0]][0]
    code = category_label_selector(indices, property_index=6111, temporary_local=4)
    result = insert_method_code(expanded, {2769: [(offset, code)]},
                                extra_stack={2769: CATEGORY_COUNT}, extra_locals={2769: 1})
    result_bodies = _bodies(result)
    if any(result_bodies[method][2:] != new_bodies[method][2:]
           for method in new_bodies if method != 2769):
        raise ValueError('Unrelated achievement consumer changed')
    return result, {'method_index': 2769, 'category_count': CATEGORY_COUNT,
                    'technical_keys_preserved': True, 'unknown_ids_keep_source': True}
