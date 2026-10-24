"""Post-call placement for the seventeen reviewed PR32 bitmap notifications."""
import hashlib
import json

from abc_font_binding import _pool_sections
from abc_inventory import ABCReader
from abc_patch import _u30


# Original after-call instruction, context start, and complete call-expression
# digest. Keep guards source-free rather than versioning extracted bytecode.
_SITES = {
    (566, 1841): (803, 240, [401], 757, '0d71bcee8b9ccfd154473a5e3289ce8b48277c29899b7133295e4e2452af8a3d'),
    (566, 2162): (939, 240, [414], 893, '0ad4f6967a43e135fb1546aeec33c33c4de0ab2d0ab775704883ec39428a87ce'),
    (566, 2495): (1080, 240, [427], 1034, 'f182d7a2ad0ed17e8149e0f894ac7e085ab42e66bbe517bc79bb9331ffe935d0'),
    (566, 2887): (1245, 240, [442], 1199, 'a8f1a8ed7f3b7baa56dd4b8c25103a90855b6fd06649fd0cda1efe8aa9d907c0'),
    (567, 1960): (821, 240, [581], 775, '941704d1ea035869f8a4adc2468319dff765427bcc5c7a46c19bd389de1d3955'),
    (567, 2270): (953, 240, [593], 907, '765e5c46256c8728ac897b11d99872fbaa8eff21c1920c11aa8e58671d7cee44'),
    (567, 2606): (1094, 240, [606], 1048, '9c78a99fa685dd7cbce0f5b60fcaf15333e53fc82c63a76d095df15d157bc156'),
    (567, 2988): (1255, 240, [620], 1209, '3118fa8448870f92f40f9c4422cf73e3de5338f189192035fef4a57f6ae9359c'),
    (675, 1098): (432, 16, [106], 394, 'f22706ff92cdd52c2fc6d2b76318961e576798f34a8fffca57dbcb5537352eaa'),
    (675, 1208): (475, 240, [1437], 437, '4b3ee48f1f70a9f52dbefc4252b0d90031b910c08b3f400609dad2686734c188'),
    (695, 6275): (2601, 16, [129], 2555, '61b68ed44a85343bab410724569e5e7f0ca5321f8ab6e0e5bf39fefc19c05aea'),
    (695, 6408): (2653, 240, [432], 2607, '4972943cb4bfa1abaa162a57878e4c70d6ec9f5fcdc2d14f7aaff4fbbe32764b'),
    (695, 7124): (2932, 240, [466], 2886, 'd578bbb988ede960895ca9c2fddffc5de9296812c39cd8357faa0e29e5a5a301'),
    (695, 8017): (3293, 240, [513], 3247, 'd8e369e11c23e28cc6da5722ee007d708d65febc55f9a24ae6e77ffbcbc384c4'),
    (695, 8986): (3688, 240, [563], 3642, '1e3020c8dcb1dcbe87d4ec64e777432eecf73b251b904a6951e8718707fd02d8'),
    (695, 10165): (4153, 240, [630], 4107, '4d562260ed2d0fdebeae4209bcf1b22e1c2fda5938863153bfd7be0f1ae04887'),
    (1144, 138): (51, 240, [926], 11, '7c7617f5def25168e73570c1cb082e71ca6e9f7ab752e02ae6196e4e2f55d0f0'),
}
PLACEMENT_CALLS = {key: (32 if key[0] in (675, 1144) else 23,
                         4 if key == (675, 1098) else 0) for key in _SITES}
_OWNERS = {566: ('IngameCalculator', 'isNew2x2BuildingBlocking'),
           567: ('IngameCalculator', 'isNewWallBlocking'),
           675: ('IngameSpellCaster', 'castRefundGem'),
           695: ('IngameInputHandler2', 'clickOnScene'),
           1144: ('PnlTalisman', 'refundFragment')}
_WRAPPERS = {352: '7efa1c6d2675f2af5b53eac3d63d200da8cd0d2638875506e135eeca4ca34d65',
             353: '02e0e86092f2f6c9396683397187acbebfe7a06c54835027eb9a8fd63fd04223'}


def placement_sites():
    return [{'tag_path': [4175], 'method_index': method,
             'instruction_index': row[0], 'expected_opcode': row[1],
             'expected_operands': list(row[2]), 'kind': 'bitmap_placement',
             'extra_stack': 7, 'call_offset': call}
            for (method, call), row in _SITES.items()]


def _op(opcode, *operands):
    return bytes([opcode]) + b''.join(_u30(value) for value in operands)


def bitmap_placement_code(margin, shift_y):
    """Move only the latest synchronous call's public bitmap, stack-neutrally.

    The ceil motion budgets are 15*1.5=22.5 and 21*1.5=31.5 pixels.
    Use the real rendered width, including padding, for every numeric format.
    The sole -4px Y adjustment bounds the reviewed ink-metric difference;
    its floor also handles the automatic caller's unbounded cursor position.
    """
    if (type(margin) is not int or type(shift_y) is not int
            or (margin, shift_y) not in ((23, 0), (32, 0), (32, 4))):
        raise ValueError('Unreviewed bitmap placement budget')
    latest = (_op(0x60, 230) + _op(0x66, 4156) + _op(0x66, 680)
              + b'\x2a' + _op(0x66, 5241) + b'\x24\x01\xa1'
              + _op(0x66, 5492) + _op(0x66, 105))
    result = (latest + b'\x2a' + _op(0x66, 1302)
              + _op(0x60, 5237) + b'\x2b' + _op(0x24, margin) + _op(0x46, 5240, 2)
              + _op(0x60, 5237) + b'\x2b' + _op(0x25, 1088 - margin)
              + latest + _op(0x66, 5359) + b'\xa1' + _op(0x46, 5268, 2)
              + _op(0x61, 1302))
    if shift_y:
        result += (latest + b'\x2a' + _op(0x66, 1303) + b'\x24\x04\xa1'
                   + _op(0x60, 5237) + b'\x2b\x24\x0b' + _op(0x46, 5240, 2)
                   + _op(0x61, 1303))
    return result


def _context_digest(inv, ops):
    rows = [(at, op, args, inv.strings[args[0]] if op == 0x2c else
             inv.multiname(args[0]) if op in (0x60, 0x66, 0x46, 0x4f) else None)
            for at, op, args in ops]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _public_bitmap_access(inv):
    # ABCInventory intentionally omits namespaces. Pin the raw QNames before
    # accessing the latest VFX from another class; private access is not allowed.
    sections, namespaces = _pool_sections(inv.reader.data)
    reader = ABCReader(inv.reader.data)
    reader.pos = sections['namespace'][2]
    sets = [None] + [[reader.u30() for _ in range(reader.count())]
                     for _ in range(1, reader.count())]
    reader.pos = sections['multiname'][1]
    public_properties = {4156, 680, 5241, 105, 1302, 1303, 5359, 5240, 5268}
    for index in range(1, sections['multiname'][3]):
        kind = reader.u8()
        if kind in (7, 13, 9, 14):
            first, _ = reader.u30(), reader.u30()
        elif kind in (15, 16, 27, 28):
            first = reader.u30()
        elif kind == 29:
            reader.u30()
            for _ in range(reader.count()):
                reader.u30()
        if index in public_properties:
            if (kind != 7 or namespaces[first][0] != 0x16
                    or inv.strings[namespaces[first][1]] != ''):
                raise ValueError('Bitmap placement requires public properties')
        if index == 5492:
            if kind != 27 or not any(namespaces[ns][0] == 0x16
                                    and inv.strings[namespaces[ns][1]] == '' for ns in sets[first]):
                raise ValueError('Bitmap placement requires a public array index')


def validate_bitmap_placement_site(before, after, old_ops, new_ops, site):
    key = (site.get('method_index'), site.get('call_offset'))
    row = _SITES.get(key)
    expected = next((s for s in placement_sites()
                     if (s['method_index'], s['call_offset']) == key), None)
    if (row is None or site != expected or type(site.get('extra_stack')) is not int):
        raise ValueError('Unexpected bitmap placement site')
    position, _, _, start, digest = row
    if len(old_ops) != len(new_ops) or position >= len(old_ops):
        raise ValueError('Unexpected bitmap placement instruction structure')
    if old_ops[position][1:] != (row[1], row[2]):
        raise ValueError('Unexpected bitmap placement continuation')
    if _context_digest(before, old_ops[start:position]) != digest:
        raise ValueError('Unexpected bitmap placement call context')
    for old, new in zip(old_ops[start:position + 1], new_ops[start:position + 1]):
        if old[:2] != new[:2] or old[1] != 0x2c and old[2] != new[2]:
            raise ValueError('Unexpected translated bitmap placement call')
    method, call = key
    invoke = 695 if method == 1144 else 694
    if old_ops[position - 1] != (call, 0x4f, [invoke, 13]):
        raise ValueError('Unexpected bitmap placement invocation')
    names = {230: 'GV', 4156: 'vfxEngine', 680: 'vfxes', 5241: 'length',
             105: 'mc', 1302: 'x', 1303: 'y', 5359: 'width',
             5237: 'Math', 5240: 'max', 5268: 'min'}
    owner, label = _OWNERS[method]
    for inv in (before, after):
        if ({'owner': owner, 'method': label} not in inv.method_labels.get(method, [])
                or any(inv.multiname(index) != name for index, name in names.items())):
            raise ValueError('Unexpected bitmap placement owner or properties')
        _public_bitmap_access(inv)
        codes = {m: code for m, _, code in inv.bodies}
        if any(hashlib.sha256(codes[m]).hexdigest() != value for m, value in _WRAPPERS.items()):
            raise ValueError('Bitmap placement requires synchronous VFX append wrappers')
    return bitmap_placement_code(*PLACEMENT_CALLS[key])
