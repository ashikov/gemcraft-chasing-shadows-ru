"""Cumulative addressed Alegreya screens for the single verified original SWF.

Original fonts, alignment zones and shared tooltip formats remain byte-identical.
The bounded AVM2 edits bind selected fields to an embedded family, preserving
existing format attributes and applying a font-only format to existing text.
"""
import argparse
import copy
import json
from pathlib import Path

from abc_font_binding import patch_abc_font_bindings
from abc_inventory import ABCInventory
from abc_patch import _u30
from addressed_localization import patch_selected_tags
from alegreya_fonts import convert_font
from build_prototype import EM, TITLE_ID, TRANSLATION, ink_bounds, sha
from install_patch import ORIGINAL_SHA256
from inventory_strings import walk_tags
from localization_workspace import write_json
from translation_catalog import load_catalog
from swf_geometry import (
    Tag, build_font, build_text, parse_font, parse_text, read_swf,
    signed_bits, write_swf,
)

FAMILY = 'GCCS Alegreya'
FONT_IDS = (6001, 6002)
ABC_TAG = 4175
RUSSIAN_ALPHABET = set(
    'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ'
    'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'
)


def _op(opcode, *operands):
    return bytes([opcode]) + b''.join(_u30(value) for value in operands)


def field_binding(receiver, family_index, font_index):
    """Stack-neutral font-only update retaining bold, size, colour and alignment."""
    return (
        receiver + b'\x2a' + _op(0x66, 5573) + b'\x2a'
        + _op(0x2c, family_index) + _op(0x61, font_index)
        + _op(0x61, 5573)
        + receiver + b'\x26' + _op(0x61, 5569)
        + receiver + _op(0x60, 839) + _op(0x2c, family_index)
        + _op(0x42, 1) + _op(0x4f, 6921, 1)
    )


def latest_tooltip_field():
    """Return method79's latest local2.textfields[length - 1]."""
    return (
        b'\xd2' + _op(0x66, 3355) + b'\x2a' + _op(0x66, 5241)
        + b'\x24\x01\xa1' + _op(0x66, 6922)
    )


ADAPTIVE_TOOLTIP_SOURCE = 'Automatically falls back to lowest visual settings'


def tooltip_layout_repair():
    """Replay McInfoPanel's accumulation with the bound field's real height.

    McInfoPanel.addTextfield derives nextTfPos/h from tf.height measured with the
    embedded Celtic font, which has no Cyrillic glyphs: Russian tooltip text wraps
    to fewer lines at creation than it renders after the Alegreya binding, so the
    panel frame ends up shorter than its content. The binding runs after
    addTextfield, so this repair repeats addTextfield's own trailing step
    (spacing 9 above arg3=false, bottom inset 8; pinned by _bindings guards) using
    the post-binding height. Stack-neutral, peak depth 4.
    """
    return (
        latest_tooltip_field() + b'\x2a' + _op(0x66, 1303) + b'\x2b'
        + _op(0x66, 5360) + b'\xa0\x24\x09\xa0'
        + b'\xd2' + b'\x2b' + _op(0x61, 3708)
        + b'\xd2\xd2' + _op(0x66, 3708) + b'\x24\x08\xa0' + _op(0x61, 3710)
    )


def _inventory(data):
    result = ABCInventory(data, {'tag_path': [ABC_TAG], 'tag_code': 82})
    result.parse()
    return result


def _bindings(original, translated, sites=None):
    before, after = _inventory(original), _inventory(translated)
    names = {
        840: 'tf', 6882: 'tf', 5573: 'defaultTextFormat', 5569: 'embedFonts',
        6921: 'setTextFormat', 839: 'TextFormat', 3355: 'textfields',
        5241: 'length', 3729: 'addTextfield', 5329: 'text', 6883: 'text',
        1303: 'y', 5360: 'height', 3708: 'nextTfPos', 3710: 'h',
    }
    if any(before.multiname(index) != name for index, name in names.items()):
        raise ValueError('Unexpected settings ABC property bindings')
    if before.multinames[6922]['kind'] != 27:
        raise ValueError('Unexpected tooltip runtime array property')
    old_codes = {method: code for method, _, code in before.bodies}
    new_codes = {method: code for method, _, code in after.bodies}
    planned = {}
    for method, offset in ((2873, 37), (2876, 43), (2879, 37)):
        operations = before.instructions(old_codes[method], method)
        position = next(i for i, op in enumerate(operations) if op[0] == offset)
        if operations[position][1:] != (0x61, [5329]):
            raise ValueError('Unexpected settings label text assignment')
        if operations[position - 2][1:] != (0x66, [840]):
            raise ValueError('Unexpected settings label receiver')
        planned[method] = [(position, b'\xd0' + _op(0x66, 840))]
    operations = before.instructions(old_codes[3469], 3469)
    buttons = (3381, 3599, 3600, 3601, 3597, 3598, 3602, 3596)
    planned[3469] = []
    for offset, button in zip((36, 51, 81, 96, 126, 141, 171, 186), buttons):
        position = next(i for i, op in enumerate(operations) if op[0] == offset)
        if (operations[position][1] != 0x2c
                or operations[position - 1][1:] != (0x66, [6882])
                or operations[position - 2][1:] != (0x66, [button])):
            raise ValueError('Unexpected settings navigation button receiver')
        receiver = b'\xd0' + _op(0x66, button) + _op(0x66, 6882)
        planned[3469].append((position, receiver))
    operations = before.instructions(old_codes[79], 79)
    planned[79] = [
        (i + 1, latest_tooltip_field()) for i, (_, opcode, args) in enumerate(operations)
        if opcode == 0x4f and args == [3729, 6]
    ]
    if len(planned[79]) != 66:
        raise ValueError('Unexpected settings tooltip field count')
    # The height repair replays addTextfield's trailing accumulation; pin the
    # layout constants it depends on and the single site it is scoped to.
    layout_owner = next(
        method for method, labels in before.method_labels.items()
        if any(label.get('owner') == 'McInfoPanel' and label.get('method') == 'addTextfield'
               for label in labels))
    layout_operations = [
        (opcode, args) for _, opcode, args
        in before.instructions(old_codes[layout_owner], layout_owner)
    ]
    for pin in ((0x66, [5360]), (0x24, [9]), (0x24, [253]), (0x24, [8]),
                (0x61, [3708]), (0x61, [3710])):
        if pin not in layout_operations:
            raise ValueError('Unexpected tooltip panel layout constants')
    repairs = set()
    for i, (_, opcode, args) in enumerate(operations):
        if opcode != 0x4f or args != [3729, 6]:
            continue
        text_operation = operations[i - 5]
        if (text_operation[1] == 0x2c
                and before.strings[text_operation[2][0]].startswith(ADAPTIVE_TOOLTIP_SOURCE)):
            if operations[i - 4][1] != 0x27:
                raise ValueError('Unexpected adaptive tooltip spacing argument')
            repairs.add(i + 1)
    if len(repairs) != 1:
        raise ValueError('Expected exactly one adaptive tooltip site')
    from localization_plan import resolve_plan_font_bindings
    extra, increments, counts = resolve_plan_font_bindings(original, translated, sites or [])

    def snippets(family_index, font_index):
        result = {}
        for method, sites in planned.items():
            old_ops = before.instructions(old_codes[method], method)
            new_ops = after.instructions(new_codes[method], method)
            if len(old_ops) != len(new_ops) or any(
                a[1] != b[1] for a, b in zip(old_ops, new_ops)
            ):
                raise ValueError('Translation changed settings instruction structure')
            result[method] = [
                (new_ops[position][0], field_binding(receiver, family_index, font_index)
                 + (tooltip_layout_repair() if method == 79 and position in repairs else b''))
                for position, receiver in sites
            ]
        for method, insertions in extra(family_index, font_index).items():
            result.setdefault(method, []).extend(insertions)
        return result
    for method in planned:
        increments[method] = max(increments.get(method, 0), 5)
        counts[str(method)] = counts.get(str(method), 0) + len(planned[method])
    return patch_abc_font_bindings(
        translated, snippets, family=FAMILY,
        extra_stack=increments,
    ), counts


def _title(tags, font):
    positions = [i for i, tag in enumerate(tags) if tag.code == 11
                 and int.from_bytes(tag.payload[:2], 'little') == TITLE_ID]
    if len(positions) != 1:
        raise ValueError('Expected one settings title')
    position = positions[0]
    old = parse_text(tags[position].payload)
    source = parse_font(next(tag.payload for tag in tags if tag.code == 75
                             and int.from_bytes(tag.payload[:2], 'little') == 1))
    if len(old['records']) != 1:
        raise ValueError('Unexpected title records')
    record = old['records'][0]
    if (record.get('font_id') != 1 or record.get('height') != 480
            or ''.join(chr(source['codes'][i]) for i, _ in record['entries']) != 'Options'):
        raise ValueError('Unexpected original settings title')
    title = copy.deepcopy(old)
    row = title['records'][0]
    row['font_id'] = font['id']
    # Alegreya's capitals occupy a smaller fraction of the em than the original.
    # 28 px restores the old title's approximate ink height without moving it.
    row['height'] = 560
    kern = {(a, b): value for a, b, value in font['kerning']}
    row['entries'] = []
    for index, char in enumerate(TRANSLATION):
        glyph = font['codes'].index(ord(char))
        following = ord(TRANSLATION[index + 1]) if index + 1 < len(TRANSLATION) else 0
        advance = font['advances'][glyph] + kern.get((ord(char), following), 0)
        row['entries'].append((glyph, round(advance * row['height'] / EM)))
    local = ink_bounds(font, row['entries'], row['height'])
    row['x'] = round(sum(old['bounds'][:2]) / 2 - sum(local[:2]) / 2)
    title['bounds'] = ink_bounds(font, row['entries'], row['height'], row['x'], row['y'])
    title['gb'] = (len(font['codes']) - 1).bit_length()
    title['ab'] = signed_bits([advance for _, advance in row['entries']])
    if title['bounds'][0] < 6000 or title['bounds'][1] > 12000:
        raise ValueError('Alegreya title exceeds safe horizontal band')
    return position, Tag(11, build_text(title)), title['bounds']


def build_alegreya_localization(original, workspace, plan, ttf_bytes):
    if sha(original) != ORIGINAL_SHA256:
        raise ValueError('Original SHA-256 mismatch')
    if isinstance(plan, (list, tuple)):
        from localization_plan import merge_localization_plans
        plan = merge_localization_plans(plan, workspace)
    if any(site.get('tag_path') != [ABC_TAG] for site in plan.get('abc_font_bindings', [])):
        raise ValueError('Unsupported ABC font binding tag address')
    if f'static.{TITLE_ID}' not in plan.get('entry_ids', []):
        raise ValueError('Settings plan must include the title')
    prefix, original_tags = read_swf(original)
    # Check all resource IDs, including nested sprites, before adding resources.
    for tag, _, _ in walk_tags(original_tags):
        if len(tag.payload) >= 2 and int.from_bytes(tag.payload[:2], 'little') in FONT_IDS:
            raise ValueError('Independent Alegreya resource ID collision')
    selected = set(plan['entry_ids'])
    characters = set(chr(code) for code in range(32, 127)) | RUSSIAN_ALPHABET
    characters.update(TRANSLATION)
    for entry in workspace['entries']:
        if entry['id'] in selected and isinstance(entry['translation'], str):
            characters.update(char for char in entry['translation'] if ord(char) >= 32)
    regular = convert_font(ttf_bytes, 400, characters, FONT_IDS[0], FAMILY)
    bold = convert_font(ttf_bytes, 700, characters, FONT_IDS[1], FAMILY)
    prefix, tags, report = patch_selected_tags(
        original, workspace, plan, {}, replacement_fonts={1: regular, 255: bold},
    )
    position, title, title_bounds = _title(tags, regular)
    tags[position] = title
    if tags[ABC_TAG].code != 82:
        raise ValueError('Unexpected settings ABC tag')
    start = tags[ABC_TAG].payload.index(b'\0', 4) + 1
    old_start = original_tags[ABC_TAG].payload.index(b'\0', 4) + 1
    abc, bindings = _bindings(
        original_tags[ABC_TAG].payload[old_start:], tags[ABC_TAG].payload[start:],
        plan.get('abc_font_bindings', []),
    )
    tags[ABC_TAG] = Tag(82, tags[ABC_TAG].payload[:start] + abc)
    additions = []
    for font in (regular, bold):
        additions.extend([
            Tag(75, build_font(font)),
            Tag(73, font['id'].to_bytes(2, 'little') + b'\0'
                + (b'\2' + b'\0' * 9) * len(font['codes'])),
        ])
    tags[position:position] = additions
    candidate = write_swf(prefix, tags, original[3])
    after_prefix, after = read_swf(candidate)
    if (after_prefix[8:] != prefix[8:] or len(after) != len(tags)
            or any(a.code != b.code or a.payload != b.payload for a, b in zip(after, tags))):
        raise ValueError('Alegreya candidate roundtrip differs')
    allowed = {position, ABC_TAG} | {item['tag_index'] for item in report['changed_original_tags']}
    retained = after[:position] + after[position + len(additions):]
    if any(a != b for i, (a, b) in enumerate(zip(original_tags, retained)) if i not in allowed):
        raise ValueError('Unrelated original SWF resource changed')
    report.update(
        original_sha256=sha(original), patched_sha256=sha(candidate),
        original_bytes=len(original), patched_bytes=len(candidate),
        alegreya_source_sha256=sha(ttf_bytes), font_family=FAMILY,
        font_weights=[400, 700], font_character_codes=regular['codes'],
        added_tag_positions=list(range(position, position + len(additions))),
        added_resources=[{'tag_code': 75, 'character_id': identifier} for identifier in FONT_IDS],
        original_fonts_preserved=True, original_alignment_zones_preserved=True,
        title_bounds_twips=title_bounds, abc_font_binding_sites=bindings,
        unconfirmed_reachability=plan.get('unconfirmed', []),
        status='PARTIAL' if plan.get('coverage_blockers') else 'READY FOR RUNTIME VERIFICATION',
        coverage_blockers=plan.get('coverage_blockers', []),
        applied_entry_ids=list(plan['entry_ids']),
    )
    report['changed_original_tags'].append(
        {'tag_index': position, 'tag_code': 11, 'character_id': TITLE_ID})
    return candidate, report


def validate_alegreya_localization(original, candidate, workspace, plan, ttf_bytes):
    expected, report = build_alegreya_localization(original, workspace, plan, ttf_bytes)
    if candidate != expected:
        raise ValueError('Candidate differs from reproducible Alegreya recipe')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True, action='append')
    parser.add_argument('--alegreya-font', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    candidate, report = build_alegreya_localization(
        args.source.read_bytes(), load_catalog(args.workspace),
        [load_catalog(path) for path in args.plan], args.alegreya_font.read_bytes(),
    )
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise ValueError('Use a fresh output directory')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    destination = args.output_dir / 'gc-cs-steam.ru.swf'
    with destination.open('xb') as handle:
        handle.write(candidate)
    if destination.read_bytes() != candidate:
        raise ValueError('Candidate reread differs')
    write_json(args.output_dir / 'static-validation.json', report)
    print(json.dumps({key: report[key] for key in
                     ('status', 'original_sha256', 'patched_sha256')}, indent=2))


if __name__ == '__main__':
    main()
