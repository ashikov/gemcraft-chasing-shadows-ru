"""Early font selection for the reviewed PR34 floaters and recovery prefix."""
import hashlib

from abc_patch import _u30

MOD_OFFSETS = (51, 215, 289, 363, 437, 511, 585, 659, 733, 807)
MATCH_MODES = {
    **{f'abc.4175.1537.{at}': 'exact' for at in MOD_OFFSETS},
    'abc.4175.242.647': 'exact',
    **{f'abc.4175.334.{at}': mode for at, mode in
       ((244, 'exact'), (247, 'prefix'), (252, 'prefix'), (265, 'prefix'),
        (339, 'exact'), (677, 'prefix'))},
}
FLOATER_BODY_SHA = 'cc61970e8c8c12acb9936d37634a3cb553889d2aa71428aed06f6bf258f4419a'
RECOVERY_BODY_SHA = 'a0b619bb5ec4af391dff167c7f64e9cd64c2e950590490cc2ebc6f2554d21a0d'


def _op(opcode, *operands):
    return bytes([opcode]) + b''.join(_u30(value) for value in operands)


def validate_match_selection(matches):
    """Forbid broad Cyrillic tests and accidental inclusion of technical codes."""
    if not isinstance(matches, list) or len(matches) != len(MATCH_MODES):
        raise ValueError('Unexpected selected floater font messages')
    seen = set()
    for match in matches:
        if (not isinstance(match, dict) or set(match) != {'entry_id', 'mode', 'source_sha256'}
                or match['entry_id'] in seen
                or MATCH_MODES.get(match['entry_id']) != match['mode']
                or not isinstance(match['source_sha256'], str)
                or len(match['source_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in match['source_sha256'])):
            raise ValueError('Unexpected selected floater font message or guard')
        seen.add(match['entry_id'])


def floater_font_selector(family_index, font_index, original_family_index,
                          matches, indexof_index=5251, array_index=6922):
    """Set local5.font before text assignment, without branches or new state.

    Match only the selected full messages or the four reviewed recovery prefixes.
    The original TextFormat retains size, bold, alignment and every other field.
    This stack-neutral snippet peaks at five slots, independent of match count.
    """
    code = (_op(0x62, 5) + _op(0x2c, original_family_index)
            + _op(0x2c, family_index) + _op(0x56, 2) + b'\x27')
    for index, mode in matches:
        code += b'\xd2' + _op(0x2c, index)
        if mode == 'prefix':
            code += _op(0x46, indexof_index, 1) + b'\x24\x00'
        elif mode != 'exact':
            raise ValueError('Unsupported floater message match')
        code += b'\xab\xa9'
    return code + b'\x73' + _op(0x66, array_index) + _op(0x61, font_index)


def _operations(inv, method):
    code = next(code for current, _, code in inv.bodies if current == method)
    return inv.instructions(code, method)


def validate_small_ui_site(before, after, old_ops, new_ops, site):
    """Resolve only the supported original callsites and fresh translated pool."""
    kind = site.get('kind')
    expected = ({'method_index': 3442, 'instruction_index': 17,
                 'expected_opcode': 0xf0, 'expected_operands': [87], 'extra_stack': 5}
                if kind == 'small_ui_floater_font' else
                {'method_index': 334, 'instruction_index': 252,
                 'expected_opcode': 0xa0, 'expected_operands': [], 'extra_stack': 0}
                if kind == 'recovery_prefix' else None)
    if (expected is None or site.get('tag_path') != [4175]
            or any(site.get(key) != value for key, value in expected.items())
            or type(site.get('extra_stack')) is not int
            or 'receiver_hex' in site or 'receivers_hex' in site):
        raise ValueError('Unexpected PR34 UI binding site or resources')
    method = expected['method_index']
    before_code = next(code for current, _, code in before.bodies if current == method)
    digest = FLOATER_BODY_SHA if method == 3442 else RECOVERY_BODY_SHA
    if hashlib.sha256(before_code).hexdigest() != digest:
        raise ValueError('Unexpected original PR34 UI method body')
    if len(old_ops) != len(new_ops) or any(
            a[1] != b[1] or (a[1] != 0x2c and a[2] != b[2])
            for a, b in zip(old_ops, new_ops)):
        raise ValueError('PR34 UI translation changed original instructions')
    if kind == 'recovery_prefix':
        for index in (251, 257, 263):
            if new_ops[index][1] != 0x2c:
                raise ValueError('Unexpected recovery string operand')
        if (after.strings[new_ops[257][2][0]] != ''
                or after.strings[new_ops[263][2][0]] != '.'
                or not after.strings[new_ops[251][2][0]].endswith(': ')):
            raise ValueError('Recovery prefix requires neutral count and empty plural suffix')
        return []
    if old_ops != new_ops:
        raise ValueError('Shared floater constructor must retain original body')
    names = {839: 'TextFormat', 5251: 'indexOf', 5573: 'defaultTextFormat',
             5329: 'text', 5359: 'width', 5360: 'height'}
    for inv in (before, after):
        if (any(inv.multiname(index) != value for index, value in names.items())
                or inv.strings[10038] != 'Celtic Garamond for GemCraft'
                or inv.multinames[6922]['kind'] != 27):
            raise ValueError('Unexpected floater font or measurement properties')
    matches = site.get('matches')
    validate_match_selection(matches)
    resolved, values = [], set()
    for match in matches:
        _, _, method, offset = match['entry_id'].split('.')
        method, offset = int(method), int(offset)
        source_ops, translated_ops = _operations(before, method), _operations(after, method)
        index = next(i for i, op in enumerate(source_ops) if op[0] == offset)
        source_op, translated_op = source_ops[index], translated_ops[index]
        if source_op[1] != 0x2c or translated_op[1] != 0x2c:
            raise ValueError('Unexpected selected floater message instruction')
        source = before.strings[source_op[2][0]]
        value = after.strings[translated_op[2][0]]
        if (hashlib.sha256(source.encode()).hexdigest() != match['source_sha256']
                or not value or value == source or value in values
                or not any('А' <= c <= 'я' or c in 'Ёё' for c in value)
                or (match['mode'] == 'prefix' and not value.endswith(' '))):
            raise ValueError('Stale or ambiguous selected floater translation')
        values.add(value)
        resolved.append((translated_op[2][0], match['mode']))
    return resolved
