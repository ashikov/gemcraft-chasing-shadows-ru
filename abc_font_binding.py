"""Bounded AVM2 pool extension and straight-line method injection.

Offsets address the already translated ABC. Branches and exception handlers land
before ordinary insertions, but skip explicitly marked after-call code.
Exception ends exclude code inserted at their end boundary.
This is structural relocation, not an AVM2 stack/type verifier; callers own the
snippet's receiver types, stack balance, scope and declared resource increments.
"""
from abc_inventory import ABCInventory, ABCReader
from abc_patch import _u30


def _inventory(data):
    inventory = ABCInventory(data, {'tag_path': [0], 'tag_code': 82})
    inventory.parse()
    return inventory


def _pool_sections(data):
    r = ABCReader(data)
    r.take(4)
    for kind in ('int', 'uint', 'double'):
        for _ in range(1, r.count()):
            r.take(8) if kind == 'double' else r.encoded(32)
    sections = {}
    start = r.pos
    count = r.count()
    entries = r.pos
    for _ in range(1, count):
        r.take(r.u30())
    sections['string'] = (start, entries, r.pos, max(1, count))
    start = r.pos
    count = r.count()
    entries = r.pos
    namespaces = [None]
    for _ in range(1, count):
        namespaces.append((r.u8(), r.u30()))
    sections['namespace'] = (start, entries, r.pos, max(1, count))
    for _ in range(1, r.count()):
        for _ in range(r.count()):
            r.u30()
    start = r.pos
    count = r.count()
    entries = r.pos
    for _ in range(1, count):
        kind = r.u8()
        if kind in (7, 13, 9, 14):
            r.u30()
            r.u30()
        elif kind in (15, 16, 27, 28):
            r.u30()
        elif kind == 29:
            r.u30()
            for _ in range(r.count()):
                r.u30()
        elif kind not in (17, 18):
            raise ValueError('Unsupported ABC multiname kind')
    sections['multiname'] = (start, entries, r.pos, max(1, count))
    return sections, namespaces


def extend_font_pool(data, family='GCCS Alegreya', *, bitmap_selector=False):
    """Append family/name strings and public QName; return bytes and two indices.

    Reuse an existing empty public PackageNamespace, or append one. Existing pool
    entries retain their indices and exact bytes; existing font QNames fail closed.
    """
    data = bytes(data)
    if bitmap_selector and family != 'GCCS Alegreya':
        raise ValueError('Bitmap selector requires the embedded GCCS Alegreya family')
    before = _inventory(data)
    if not isinstance(family, str) or not family or family in before.strings:
        raise ValueError('Font family is empty or already present')
    if any(before.multiname(i) == 'font' and item['kind'] == 7
           for i, item in enumerate(before.multinames) if item):
        raise ValueError('Font QName already present')
    sections, namespaces = _pool_sections(data)
    family_index = len(before.strings)
    font_string = family_index + 1
    font_index = len(before.multinames)
    public = next((
        i for i, ns in enumerate(namespaces)
        if ns and ns[0] == 0x16 and ns[1] > 0 and before.strings[ns[1]] == ''
    ), None)
    changes = []
    additions = {'string': _u30(len(family.encode('utf-8'))) + family.encode('utf-8') + b'\4font'}
    counts = {'string': 2, 'multiname': 1}
    if public is None:
        empty = next((i for i, value in enumerate(before.strings)
                      if i > 0 and value == ''), None)
        if empty is None:
            empty = font_string + 1
            additions['string'] += b'\0'
            counts['string'] += 1
        public = sections['namespace'][3]
        additions['namespace'] = b'\x16' + _u30(empty)
        counts['namespace'] = 1
    additions['multiname'] = b'\7' + _u30(public) + _u30(font_string)
    for kind, added in additions.items():
        start, entries, end, count = sections[kind]
        changes.append((start, end, _u30(count + counts[kind]) + data[entries:end] + added))
    result = data
    for start, end, replacement in sorted(changes, reverse=True):
        result = result[:start] + replacement + result[end:]
    after = _inventory(result)
    if (after.strings[:len(before.strings)] != before.strings
            or after.multinames[:len(before.multinames)] != before.multinames
            or [b[2] for b in before.bodies] != [b[2] for b in after.bodies]):
        raise ValueError('ABC font pool preservation check failed')
    if bitmap_selector:
        result = _extend_bitmap_selector_pool(result, public)
    return result, family_index, font_index


def _extend_bitmap_selector_pool(data, public):
    """Append only the two QName dependencies of the opt-in bitmap selector."""
    from bitmap_font_binding import CYRILLIC_PATTERN, AS3_NAMESPACE

    before = _inventory(data)
    sections, namespaces = _pool_sections(data)
    strings = list(before.strings)
    string_bytes = bytearray()

    def string_index(value):
        existing = next((i for i, item in enumerate(strings) if i > 0 and item == value), None)
        if existing is not None:
            return existing
        index = len(strings)
        strings.append(value)
        encoded = value.encode('utf-8')
        string_bytes.extend(_u30(len(encoded)) + encoded)
        return index

    string_index(CYRILLIC_PATTERN)
    regexp = string_index('RegExp')
    test = string_index('test')
    uri = string_index(AS3_NAMESPACE)
    builtin = next((i for i, ns in enumerate(namespaces) if ns == (0x08, uri)), None)
    changes = []
    if builtin is None:
        builtin = len(namespaces)
        start, entries, end, count = sections['namespace']
        changes.append((start, end, _u30(count + 1) + data[entries:end] + b'\x08' + _u30(uri)))
    start, entries, end, count = sections['string']
    changes.append((start, end, _u30(len(strings)) + data[entries:end] + string_bytes))
    start, entries, end, count = sections['multiname']
    qnames = b'\x07' + _u30(public) + _u30(regexp) + b'\x07' + _u30(builtin) + _u30(test)
    changes.append((start, end, _u30(count + 2) + data[entries:end] + qnames))
    result = data
    for start, end, replacement in sorted(changes, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


def _bodies(data):
    inv = ABCInventory(data, {'tag_path': [0], 'tag_code': 82})
    r = inv.reader
    r.take(4)
    inv.pools()
    inv.methods()
    inv.structures()
    bodies = {}
    for _ in range(r.count()):
        start = r.pos
        method = r.u30()
        limits = [r.u30() for _ in range(4)]
        code = r.take(r.u30())
        exceptions = [[r.u30() for _ in range(5)] for _ in range(r.count())]
        trait_start = r.pos
        inv.traits(f'body {method}')
        bodies[method] = (start, r.pos, limits, code, exceptions, data[trait_start:r.pos])
    r.finish()
    return bodies


def _s24(value):
    if not -(1 << 23) <= value < 1 << 23:
        raise ValueError('Relocated ABC branch overflow')
    return value.to_bytes(3, 'little', signed=True)


def insert_method_code(data, insertions, *, extra_stack=None, extra_locals=None):
    """Insert known straight-line snippets at instruction boundaries.

    Explicit nonnegative increments update max_stack/local_count. No new scope is
    permitted by the snippets. Unselected method bodies remain byte-identical.
    A third site item, 'after_call', requires a preceding callpropvoid and makes
    the snippet run only on its fallthrough, not on branches or handler entries.
    """
    data = bytes(data)
    before = _inventory(data)
    bodies = _bodies(data)
    extra_stack, extra_locals = extra_stack or {}, extra_locals or {}
    if any(method not in insertions for method in (*extra_stack, *extra_locals)):
        raise ValueError('Resource increment targets an unselected method')
    snippet_checker = ABCInventory(data, {'tag_path': [0], 'tag_code': 82})
    snippet_checker.parse()
    replacements = []
    for method, sites in sorted(insertions.items()):
        if type(method) is not int or method not in bodies:
            raise ValueError('Selected ABC method has no body')
        if any(x['method_index'] == method for x in before.unsupported + before.control_flow_anomalies):
            raise ValueError('Unsupported or unverified selected ABC method')
        start, end, limits, code, exceptions, traits = bodies[method]
        instructions = before.instructions(code, method)
        boundaries = {offset for offset, _, _ in instructions} | {len(code)}
        positions = {offset: index for index, (offset, _, _) in enumerate(instructions)}
        snippets, after_calls = {}, set()
        for site in sites:
            if not isinstance(site, (list, tuple)) or len(site) not in (2, 3):
                raise ValueError('Insertion needs offset, code and optional after_call mode')
            offset, snippet = site[:2]
            if type(offset) is not int or offset not in boundaries or offset in snippets:
                raise ValueError('Insertion is not a unique instruction boundary')
            if len(site) == 3:
                position = positions.get(offset, 0)
                if (site[2] != 'after_call' or offset not in positions or position == 0
                        or instructions[position - 1][1] != 0x4f):
                    raise ValueError('after_call insertion must follow callpropvoid')
                after_calls.add(offset)
            snippet = bytes(snippet)
            if not snippet:
                raise ValueError('Empty ABC insertion')
            unsupported_before = len(snippet_checker.unsupported)
            decoded = snippet_checker.instructions(snippet, method)
            if len(snippet_checker.unsupported) != unsupported_before:
                raise ValueError('Unsupported inserted instruction')
            if any(opcode in (*range(0x0C, 0x20), 0x03, 0x30, 0x47, 0x48) for _, opcode, _ in decoded):
                raise ValueError('Inserted snippet must be straight-line without scope changes')
            snippets[offset] = snippet
        def relocated(offset):
            return offset + sum(len(s) for at, s in snippets.items() if at < offset)
        def entry_target(offset):
            return relocated(offset) + (len(snippets[offset]) if offset in after_calls else 0)
        output = bytearray()
        for index, (offset, opcode, operands) in enumerate(instructions):
            output.extend(snippets.get(offset, b''))
            next_offset = instructions[index + 1][0] if index + 1 < len(instructions) else len(code)
            raw = bytearray(code[offset:next_offset])
            instruction_start = relocated(offset) + len(snippets.get(offset, b''))
            if 0x0C <= opcode <= 0x1A:
                raw[1:4] = _s24(entry_target(next_offset + operands[0]) - (instruction_start + len(raw)))
            elif opcode == 0x1B:
                raw[1:4] = _s24(entry_target(offset + operands[0]) - instruction_start)
                reader = ABCReader(raw)
                reader.pos = 4
                reader.u30()
                for delta in operands[2:]:
                    raw[reader.pos:reader.pos + 3] = _s24(entry_target(offset + delta) - instruction_start)
                    reader.pos += 3
            output.extend(raw)
        output.extend(snippets.get(len(code), b''))
        for exception in exceptions:
            if any(at not in boundaries for at in exception[:3]):
                raise ValueError('Exception is not an instruction boundary')
            begin, exception_end, target = exception[:3]
            exception[:3] = [entry_target(begin), relocated(exception_end), entry_target(target)]
        for position, increments in ((0, extra_stack), (1, extra_locals)):
            increment = increments.get(method, 0)
            if type(increment) is not int or increment < 0:
                raise ValueError('Invalid ABC resource increment')
            limits[position] += increment
        replacement = _u30(method) + b''.join(_u30(x) for x in limits)
        replacement += _u30(len(output)) + output + _u30(len(exceptions))
        replacement += b''.join(_u30(x) for record in exceptions for x in record) + traits
        replacements.append((start, end, replacement))
    result = data
    for start, end, replacement in sorted(replacements, reverse=True):
        result = result[:start] + replacement + result[end:]
    after = _inventory(result)
    changed_bodies = _bodies(result)
    for method, body in bodies.items():
        if method not in insertions:
            new = changed_bodies[method]
            if data[body[0]:body[1]] != result[new[0]:new[1]]:
                raise ValueError('Unselected ABC body changed')
    if after.strings != before.strings or after.multinames != before.multinames:
        raise ValueError('ABC pools changed during insertion')
    if any(x['method_index'] in insertions for x in after.unsupported + after.control_flow_anomalies):
        raise ValueError('Relocated ABC verification failed')
    return result


def patch_abc_font_bindings(data, insertions, *, family='GCCS Alegreya', extra_stack=None, extra_locals=None,
                            bitmap_selector=False):
    """Extend font pools then insert snippets, optionally built from new indices."""
    data, family_index, font_index = extend_font_pool(data, family, bitmap_selector=bitmap_selector)
    if callable(insertions):
        insertions = insertions(family_index, font_index)
    return insert_method_code(data, insertions, extra_stack=extra_stack, extra_locals=extra_locals)
