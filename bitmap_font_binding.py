"""One guarded, branchless font choice before VfxFloatingText rasterization."""
from abc_patch import _u30

# Raw ABC strings bypass AS3 source unescaping; PCRE1 rejects literal \u escapes.
CYRILLIC_PATTERN = '[Ѐ-ӿ]'
AS3_NAMESPACE = 'http://adobe.com/AS3/2006/builtin'


def bitmap_selector_pool_indices(data):
    """Resolve deterministic opt-in indices from the unextended translated ABC."""
    from abc_font_binding import _inventory, extend_font_pool

    expanded, _, _ = extend_font_pool(data, bitmap_selector=True)
    inv = _inventory(expanded)
    return {
        'pattern_index': inv.strings.index(CYRILLIC_PATTERN),
        'empty_index': next(i for i, value in enumerate(inv.strings) if i > 0 and value == ''),
        'regexp_index': len(inv.multinames) - 2,
        'test_index': len(inv.multinames) - 1,
    }


def bitmap_font_selector(family_index, *, pattern_index, empty_index,
                         regexp_index, test_index, property_index=5492):
    """Replace stack top font by [original, Alegreya][int(RegExp.test(text))].

    Both regex arguments are explicit. No global regex flag, branches, locals or
    scopes are introduced. The two original constructor arguments below the
    snippet retain their order; the peak is three slots above its initial depth.
    """
    def op(opcode, *operands):
        return bytes([opcode]) + b''.join(_u30(value) for value in operands)

    return (op(0x2c, family_index) + op(0x56, 2)
            + op(0x60, regexp_index) + op(0x2c, pattern_index)
            + op(0x2c, empty_index) + op(0x42, 2)
            + op(0x62, 4) + op(0x46, test_index, 1)
            + b'\x73' + op(0x66, property_index))


def _dynamic_namespace_set(data, index):
    from abc_font_binding import _pool_sections
    from abc_inventory import ABCReader

    sections, namespaces = _pool_sections(data)
    reader = ABCReader(data)
    reader.pos = sections['namespace'][2]
    sets = [None]
    for _ in range(1, reader.count()):
        sets.append([reader.u30() for _ in range(reader.count())])
    count = reader.count()
    for current in range(1, count):
        kind = reader.u8()
        if kind in (7, 13, 9, 14):
            operands = [reader.u30(), reader.u30()]
        elif kind in (15, 16, 27, 28):
            operands = [reader.u30()]
        elif kind == 29:
            operands = [reader.u30()]
            operands.extend(reader.u30() for _ in range(reader.count()))
        else:
            operands = []
        if current == index:
            if kind != 27:
                raise ValueError('Unsupported bitmap dynamic property')
            return [namespaces[number] for number in sets[operands[0]]]
    raise ValueError('Missing bitmap dynamic property')


def validate_bitmap_font_site(before, after, old_ops, new_ops, site):
    """Fail closed unless the exact supported constructor lifecycle is present."""
    if (site.get('method_index') != 1732 or site.get('instruction_index') != 64
            or site.get('receiver_hex') != '6204'
            or site.get('expected_opcode') != 0x62
            or site.get('expected_operands') != [6]):
        raise ValueError('Unexpected bitmap selector site or text receiver')
    from abc_font_binding import _bodies

    bodies = [_bodies(inv.reader.data).get(1732) for inv in (before, after)]
    if (any(body is None or body[2] != [6, 18, 4, 5] or body[4] for body in bodies)
            or bodies[0][3] != bodies[1][3]):
        raise ValueError('Unsupported bitmap constructor resources or changed body')
    namespace_sets = [_dynamic_namespace_set(inv.reader.data, 5492) for inv in (before, after)]
    if (namespace_sets[0] != namespace_sets[1]
            or not any(kind == 0x16 and before.strings[name] == '' and name > 0
                       for kind, name in namespace_sets[0])):
        raise ValueError('Unsupported bitmap dynamic public namespace')
    # Critical lifecycle from embedFonts to dimensions: no wrapping, fresh bold
    # TextFormat, original size/colour and single-line measurement remain intact.
    context = {
        36: (0x60, [5228]), 37: (0x26, []), 38: (0x61, [5569]),
        45: (0x60, [5228]), 46: (0x27, []), 47: (0x61, [5882]),
        49: (0x60, [5228]), 50: (0x27, []), 51: (0x61, [5883]),
        53: (0x60, [5228]), 54: (0x60, [5574]), 55: (0x66, [4298]),
        56: (0x61, [5575]),
        62: (0x5d, [839]), 63: (0x2c, [10038]), 64: (0x62, [6]),
        65: (0x24, [2]), 66: (0xa2, []), 67: (0x4a, [839, 2]),
        68: (0x80, [839]), 69: (0x63, [15]),
        71: (0x62, [15]), 72: (0x26, []), 73: (0x61, [5568]),
        75: (0x60, [5228]), 76: (0x62, [15]), 77: (0x61, [5573]),
        79: (0x60, [5228]), 80: (0x62, [4]), 81: (0x61, [5329]),
        83: (0x60, [5228]), 84: (0x62, [5]), 85: (0x61, [5434]),
        88: (0x60, [5228]), 89: (0x66, [5359]),
        92: (0x60, [5228]), 93: (0x66, [5360]), 98: (0x4a, [16, 4]),
        108: (0x62, [16]), 109: (0x60, [5228]), 110: (0x60, [5224]),
        111: (0x4f, [3032, 2]),
    }
    if (len(old_ops) != len(new_ops) or len(old_ops) <= max(context)
            or any(old_ops[i][1:] != expected or new_ops[i][1:] != expected
                   for i, expected in context.items())):
        raise ValueError('Unexpected bitmap constructor ABC context')
    names = {5228: 'tf', 839: 'TextFormat', 5569: 'embedFonts',
             5882: 'multiline', 5883: 'wordWrap', 5568: 'bold',
             5573: 'defaultTextFormat', 5329: 'text', 5434: 'textColor',
             5359: 'width', 5360: 'height', 16: 'BitmapData',
             5574: 'TextFieldAutoSize', 4298: 'LEFT', 5575: 'autoSize', 3032: 'draw'}
    for inv in (before, after):
        if (any(inv.multiname(index) != name for index, name in names.items())
                or len(inv.strings) <= 10038
                or inv.strings[1] != ''
                or inv.strings[10038] != 'Celtic Garamond for GemCraft'
                or inv.multinames[5492] != {'kind': 27}):
            raise ValueError('Unsupported bitmap font or property scenario')
