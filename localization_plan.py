"""Merge bounded local screen plans and guard their ABC font binding sites."""
import copy

from abc_font_binding import patch_abc_font_bindings
from abc_inventory import ABCInventory
from abc_patch import _u30
from bitmap_font_binding import (
    bitmap_font_selector, bitmap_selector_pool_indices, validate_bitmap_font_site,
)


def _path(value):
    if not isinstance(value, list) or not value or any(type(x) is not int or x < 0 for x in value):
        raise ValueError('Invalid tag path')
    return tuple(value)


def _index(value):
    if type(value) is not int or value < 0:
        raise ValueError('Invalid instruction or resource index')
    return value


def _binding_key(site):
    return (_path(site['tag_path']), _index(site['method_index']), _index(site['instruction_index']))


def merge_localization_plans(plans, workspace):
    """Preserve selections while rejecting duplicate IDs, addresses and bindings."""
    if not isinstance(plans, (list, tuple)) or not plans:
        raise ValueError('At least one localization plan is required')
    entries = {}
    for entry in workspace['entries']:
        identifier = entry['id']
        if identifier in entries:
            raise ValueError('Duplicate workspace entry ID')
        entries[identifier] = entry
    result = {'schema_version': 1, 'game_sha256': plans[0].get('game_sha256'),
              'entry_ids': [], 'fonts': [], 'layouts': {}, 'unconfirmed': [],
              'abc_font_bindings': [], 'coverage_blockers': [], 'empty_abc_strings': []}
    digest = result['game_sha256']
    if not isinstance(digest, str) or len(digest) != 64 or any(x not in '0123456789abcdef' for x in digest):
        raise ValueError('Invalid plan game SHA-256')
    seen_ids, addresses, fonts, bindings, empty_strings = set(), set(), set(), set(), set()
    for plan in plans:
        if plan.get('schema_version') != 1 or plan.get('game_sha256') != digest:
            raise ValueError('Plan schema or game SHA-256 mismatch')
        selected = plan.get('entry_ids')
        if not isinstance(selected, list) or not selected:
            raise ValueError('Plan must select entry IDs')
        expected_layouts = set()
        for identifier in selected:
            if not isinstance(identifier, str) or identifier not in entries or identifier in seen_ids:
                raise ValueError('Missing or duplicate selected entry ID')
            seen_ids.add(identifier)
            entry = entries[identifier]
            location = entry['location']
            address = (_path(location['tag_path']),)
            if 'method_index' in location:
                address += (_index(location['method_index']), _index(location['code_offset']))
            if address in addresses:
                raise ValueError('Duplicate localization address')
            addresses.add(address)
            if entry['category'] != 'static':
                expected_layouts.add(identifier)
            result['entry_ids'].append(identifier)
        layouts = plan.get('layouts', {})
        if (not isinstance(layouts, dict) or not expected_layouts <= set(layouts)
                or not set(layouts) <= set(selected)):
            raise ValueError('Layouts must cover nonstatic entries and only selected IDs')
        if any(not isinstance(layout, dict) for layout in layouts.values()):
            raise ValueError('Invalid layout')
        result['layouts'].update(copy.deepcopy(layouts))
        selected_fonts = plan.get('fonts', [])
        if not isinstance(selected_fonts, list) or len(set(selected_fonts)) != len(selected_fonts):
            raise ValueError('Invalid or duplicate font selection')
        fonts.update(_index(font) for font in selected_fonts)
        unconfirmed = plan.get('unconfirmed', [])
        if not isinstance(unconfirmed, list):
            raise ValueError('Invalid unconfirmed selection')
        for item in unconfirmed:
            if item not in result['unconfirmed']:
                result['unconfirmed'].append(copy.deepcopy(item))
        blockers = plan.get('coverage_blockers', [])
        if not isinstance(blockers, list) or any(not isinstance(item, str) for item in blockers):
            raise ValueError('Invalid coverage blockers')
        result['coverage_blockers'].extend(item for item in blockers if item not in result['coverage_blockers'])
        sites = plan.get('abc_font_bindings', [])
        if not isinstance(sites, list):
            raise ValueError('Invalid ABC font binding selection')
        for site in sites:
            key = _binding_key(site)
            if key in bindings:
                raise ValueError('Duplicate ABC font binding address')
            bindings.add(key)
            result['abc_font_bindings'].append(copy.deepcopy(site))
        empty = plan.get('empty_abc_strings', [])
        if not isinstance(empty, list) or any(not isinstance(identifier, str) for identifier in empty):
            raise ValueError('Invalid empty ABC string selection')
        for identifier in empty:
            if identifier in empty_strings or identifier in seen_ids:
                raise ValueError('Duplicate or translated empty ABC string')
            empty_strings.add(identifier)
            result['empty_abc_strings'].append(identifier)
    if empty_strings & seen_ids:
        raise ValueError('Translated entry cannot be an empty ABC string')
    result['fonts'] = sorted(fonts)
    result['abc_font_bindings'].sort(key=_binding_key)
    result['empty_abc_strings'].sort()
    return result


def _inventory(data):
    inv = ABCInventory(data, {'tag_path': [0], 'tag_code': 82})
    inv.parse()
    return inv


def _receiver_peak(decoded, inventory):
    """Accept only read expressions producing exactly one independent receiver."""
    depth, peak = 0, 0
    for _, opcode, operands in decoded:
        consumed, produced = 0, 1
        if opcode in (0xD0, 0xD1, 0xD2, 0xD3, 0x62, 0x24):
            pass
        elif opcode == 0x2A:
            consumed, produced = 1, 2
        elif opcode in (0x60, 0x66):
            index = operands[0]
            if not 0 < index < len(inventory.multinames):
                raise ValueError('Invalid receiver property')
            kind = inventory.multinames[index]['kind']
            runtime = {7: 0, 13: 0, 9: 0, 14: 0, 15: 1, 16: 1,
                       17: 2, 18: 2, 27: 1, 28: 1}.get(kind)
            if runtime is None or (opcode == 0x60 and runtime):
                raise ValueError('Unsupported receiver property')
            consumed = 1 + runtime if opcode == 0x66 else 0
        elif opcode == 0xA1:
            consumed = 2
        else:
            raise ValueError('Receiver must use read-only instructions')
        if depth < consumed:
            raise ValueError('Receiver depends on the existing operand stack')
        depth += produced - consumed
        peak = max(peak, depth)
    if depth != 1:
        raise ValueError('Receiver must produce exactly one value')
    return peak


def resolve_plan_font_bindings(original, translated, sites):
    """Resolve guarded original instruction positions before one pool extension.

    Returned snippets can be combined with the existing settings insertions, so
    cumulative builds append the shared font QName and family exactly once.
    """
    if not isinstance(sites, list):
        raise ValueError('Invalid ABC font binding selection')
    if not sites:
        return lambda family, font: {}, {}, {}
    before, after = _inventory(original), _inventory(translated)
    names = {5573: 'defaultTextFormat', 5569: 'embedFonts',
             839: 'TextFormat', 6921: 'setTextFormat'}
    if any(before.multiname(index) != name or after.multiname(index) != name
           for index, name in names.items()):
        raise ValueError('Unexpected ABC font property bindings')
    old_codes = {method: code for method, _, code in before.bodies}
    new_codes = {method: code for method, _, code in after.bodies}
    planned, increments, seen = {}, {}, set()
    path = _path(sites[0]['tag_path'])
    checker = _inventory(translated)
    for site in sorted(sites, key=_binding_key):
        key = _binding_key(site)
        if key in seen or key[0] != path:
            raise ValueError('Duplicate or mixed ABC font binding address')
        seen.add(key)
        _, method, position = key
        if method not in old_codes or method not in new_codes:
            raise ValueError('Selected ABC method has no body')
        old_ops = before.instructions(old_codes[method], method)
        new_ops = after.instructions(new_codes[method], method)
        if len(old_ops) != len(new_ops) or any(a[1] != b[1] for a, b in zip(old_ops, new_ops)):
            raise ValueError('Translation changed ABC instruction structure')
        if position >= len(old_ops):
            raise ValueError('Font binding instruction does not exist')
        if (old_ops[position][1] != site.get('expected_opcode')
                or old_ops[position][2] != site.get('expected_operands')):
            raise ValueError('Unexpected original ABC binding instruction')
        guards = site.get('receiver_guards', [])
        if not isinstance(guards, list):
            raise ValueError('Invalid receiver guards')
        for guard in guards:
            index = _index(guard['instruction_index'])
            if index >= len(old_ops) or old_ops[index][1:] != (guard['opcode'], guard['operands']):
                raise ValueError('Unexpected original ABC receiver instruction')
        kind = site.get('kind')
        if kind not in ('field', 'tooltip', 'panel_format_before', 'panel_format_after',
                        'floater', 'floater_fields', 'bitmap_cyrillic'):
            raise ValueError('Unsupported font binding kind')
        if kind == 'bitmap_cyrillic':
            validate_bitmap_font_site(before, after, old_ops, new_ops, site)
        original_family_index = None
        if kind.startswith('panel_format_'):
            original_family_index = _index(site.get('original_font_string_index'))
            if (original_family_index >= len(before.strings)
                    or before.strings[original_family_index] != site.get('original_font_family')
                    or after.strings[original_family_index] != before.strings[original_family_index]):
                raise ValueError('Unexpected original panel font family')
        receiver_values = site.get('receivers_hex')
        if receiver_values is None:
            receiver_values = [site.get('receiver_hex')]
        elif 'receiver_hex' in site:
            raise ValueError('Choose receiver_hex or receivers_hex')
        if not isinstance(receiver_values, list) or not receiver_values:
            raise ValueError('Invalid font binding receivers')
        receivers, receiver_peak = [], 0
        for value in receiver_values:
            try:
                receiver = bytes.fromhex(value)
            except (TypeError, ValueError) as error:
                raise ValueError('Invalid font binding receiver') from error
            unsupported = len(checker.unsupported)
            decoded = checker.instructions(receiver, method)
            if (not decoded or len(checker.unsupported) != unsupported
                    or any(op in (*range(0x0C, 0x20), 0x03, 0x30, 0x47, 0x48)
                           for _, op, _ in decoded)):
                raise ValueError('Receiver must be supported straight-line code without scope changes')
            receiver_peak = max(receiver_peak, _receiver_peak(decoded, checker))
            receivers.append(receiver)
        panel_receiver, panel_peak = None, 0
        if kind == 'floater':
            if position == 0 or old_ops[position - 1][1:] != (0x4f, [3841, 8]):
                raise ValueError('Unexpected original floater creation call')
            floater_names = {3366: 'plateWidth', 3367: 'plateHeight', 5237: 'Math',
                             5327: 'ceil', 5359: 'width', 5360: 'height'}
            if any(before.multiname(index) != name or after.multiname(index) != name
                   for index, name in floater_names.items()):
                raise ValueError('Unexpected floater property bindings')
            if len(receivers) != 1:
                raise ValueError('Floater needs exactly one text receiver')
            try:
                panel_receiver = bytes.fromhex(site.get('panel_receiver_hex'))
            except (TypeError, ValueError) as error:
                raise ValueError('Invalid floater panel receiver') from error
            unsupported = len(checker.unsupported)
            panel_ops = checker.instructions(panel_receiver, method)
            if len(checker.unsupported) != unsupported:
                raise ValueError('Unsupported floater panel receiver')
            panel_peak = _receiver_peak(panel_ops, checker)
        if kind == 'floater_fields':
            source = [op[1:] for op in old_ops[position - 3:position]]
            if (position < 3 or len(source) != 3
                    or source[:2] != [(0x4a, [91, 11]), (0x80, [91])]
                    or source[2][0] != 0x63
                    or [op[1:] for op in new_ops[position - 3:position]] != source):
                raise ValueError('Unexpected original floater fields creation')
            panel_receiver = b'\x62' + _u30(source[2][1][0])
            if site.get('panel_receiver_hex') != panel_receiver.hex():
                raise ValueError('Unexpected floater fields saved panel receiver')
            expected = [panel_receiver + b'\x66' + _u30(3355) + b'\x24' + bytes([row])
                        + b'\x66' + _u30(6922) for row in range(3)]
            if receivers != expected:
                raise ValueError('Floater fields need exactly three ordered text receivers')
            fields_names = {91: 'McFloaterPanel', 3355: 'textfields',
                            3366: 'plateWidth', 3367: 'plateHeight', 5237: 'Math',
                            5327: 'ceil', 5240: 'max', 5359: 'width', 5360: 'height', 1303: 'y'}
            if (any(before.multiname(index) != name or after.multiname(index) != name
                    for index, name in fields_names.items())
                    or before.multinames[6922]['kind'] != 27
                    or after.multinames[6922]['kind'] != 27):
                raise ValueError('Unexpected floater fields property bindings')
        increment = _index(site['extra_stack'])
        minimum = max(receiver_peak, 2 if kind.startswith('panel_format_') else 4)
        if kind == 'floater':
            minimum = max(minimum, panel_peak, receiver_peak + 2)
        if kind == 'floater_fields':
            minimum = max(minimum, receiver_peak + 3, 8)
        if increment < minimum:
            raise ValueError('Font binding stack budget is insufficient')
        increments[method] = max(increments.get(method, 0), increment)
        planned.setdefault(method, []).append((new_ops[position][0], receivers, kind, original_family_index, panel_receiver))

    def snippets(family_index, font_index):
        # Imported only when snippets are materialized, avoiding a builder cycle.
        from alegreya_localization import field_binding
        bitmap_indices = (bitmap_selector_pool_indices(translated)
                          if any(site['kind'] == 'bitmap_cyrillic' for site in sites) else None)
        def binding(receivers, kind, original_family_index, panel_receiver):
            if kind == 'bitmap_cyrillic':
                return bitmap_font_selector(family_index, **bitmap_indices)
            if kind.startswith('panel_format_'):
                family = family_index if kind == 'panel_format_before' else original_family_index
                return b''.join(receiver + b'\x2c' + _u30(family) + b'\x61' + _u30(font_index)
                                for receiver in receivers)
            result = b''.join(field_binding(receiver, family_index, font_index) for receiver in receivers)
            if kind == 'floater':
                receiver = receivers[0]
                result += (panel_receiver + b'\x60' + _u30(5237)
                           + receiver + b'\x66' + _u30(5359) + b'\x24\x14\xa0'
                           + b'\x46' + _u30(5327) + _u30(1) + b'\x61' + _u30(3366)
                           + panel_receiver + receiver + b'\x66' + _u30(5360)
                           + b'\x24\x05\xa0\x61' + _u30(3367))
            if kind == 'floater_fields':
                # Reflow public fields after font selection, leaving the shared
                # constructor's private nextTfPos slot untouched.
                result += panel_receiver + b'\x24\x02\x61' + _u30(3366)
                previous = None
                for receiver in receivers:
                    y = (b'\x24\x02' if previous is None else
                         previous + b'\x66' + _u30(1303)
                         + previous + b'\x66' + _u30(5360) + b'\x24\x03\xa1\xa0')
                    result += (receiver + y
                               + b'\x61' + _u30(1303)
                               + panel_receiver + b'\x60' + _u30(5237)
                               + panel_receiver + b'\x66' + _u30(3366)
                               + b'\x60' + _u30(5237)
                               + receiver + b'\x66' + _u30(5359) + b'\x24\x14\xa0'
                               + b'\x46' + _u30(5327) + _u30(1)
                               + b'\x46' + _u30(5240) + _u30(2) + b'\x61' + _u30(3366))
                    previous = receiver
                result += (panel_receiver + previous + b'\x66' + _u30(1303)
                           + previous + b'\x66' + _u30(5360)
                           + b'\xa0\x24\x03\xa0\x61' + _u30(3367))
            return result
        return {method: [(offset, binding(receivers, kind, original_family_index, panel_receiver))
                         for offset, receivers, kind, original_family_index, panel_receiver in locations]
                for method, locations in planned.items()}

    return snippets, increments, {str(method): len(locations) for method, locations in planned.items()}


def apply_plan_font_bindings(original, translated, sites, family='GCCS Alegreya'):
    """Guard and apply a single tag's bindings; untouched bodies stay identical."""
    snippets, increments, counts = resolve_plan_font_bindings(original, translated, sites)
    if not sites:
        return bytes(translated), counts
    return patch_abc_font_bindings(
        translated, snippets, family=family, extra_stack=increments,
        bitmap_selector=any(site['kind'] == 'bitmap_cyrillic' for site in sites),
    ), counts
