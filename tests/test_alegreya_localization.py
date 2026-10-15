"""Synthetic binding and guarded-source checks, plus private offline integration."""
from pathlib import Path

import pytest

from alegreya_localization import (
    RUSSIAN_ALPHABET, build_alegreya_localization, field_binding, latest_tooltip_field,
    tooltip_layout_repair, validate_alegreya_localization,
)
from abc_inventory import ABCInventory
from test_abc_inventory import abc


def test_field_snippet_balanced_and_preserves_format_attributes():
    inventory = ABCInventory(abc([], []), {'tag_path': [0], 'tag_code': 82})
    inventory.parse()
    inventory.pool_sizes.update(multiname=7000, string=100)
    operations = inventory.instructions(field_binding(b'\xd0', 20, 21), 0)
    assert not inventory.unsupported
    assert [(op, args) for _, op, args in operations].count((0x61, [21])) == 1
    assert (0x61, [5573]) in [(op, args) for _, op, args in operations]
    assert (0x4f, [6921, 1]) in [(op, args) for _, op, args in operations]
    assert not any(op in (0x30, 0x47, 0x48) for _, op, _ in operations)


def test_latest_tooltip_receiver_indexes_last_added_field():
    inventory = ABCInventory(abc([], []), {'tag_path': [0], 'tag_code': 82})
    inventory.parse()
    inventory.pool_sizes.update(multiname=7000, string=100)
    assert inventory.instructions(latest_tooltip_field(), 0) == [
        (0, 0xd2, []), (1, 0x66, [3355]), (4, 0x2a, []),
        (5, 0x66, [5241]), (8, 0x24, [1]), (10, 0xa1, []),
        (11, 0x66, [6922]),
    ]


def _stack_effect(opcode, args):
    if opcode in (0xd0, 0xd1, 0xd2, 0xd3, 0x24, 0x25, 0x26, 0x2c, 0x60):
        return 1
    if opcode == 0x2a:
        return 1
    if opcode == 0x2b:
        return 0
    if opcode == 0x66:
        # MultinameL array access pops the key; QName property reads are neutral.
        return -1 if args[0] == 6922 else 0
    if opcode in (0xa0, 0xa1):
        return -1
    if opcode == 0x61:
        return -2
    if opcode == 0x42:
        return -args[0]
    if opcode == 0x4f:
        return -(1 + args[1])  # setTextFormat returns void: nothing pushed
    raise AssertionError(f'unexpected opcode {opcode:#x}')


def test_tooltip_layout_repair_recomputes_panel_height_after_font_swap():
    inventory = ABCInventory(abc([], []), {'tag_path': [0], 'tag_code': 82})
    inventory.parse()
    inventory.pool_sizes.update(multiname=7000, string=100)
    snippet = field_binding(latest_tooltip_field(), 20, 21) + tooltip_layout_repair()
    operations = inventory.instructions(snippet, 0)
    assert not inventory.unsupported
    assert not any(op in (*range(0x0c, 0x20), 0x03, 0x30, 0x47, 0x48)
                   for _, op, _ in operations)
    decoded = [(op, args) for _, op, args in operations]
    font_swap = decoded.index((0x4f, [6921, 1]))
    position_fix = decoded.index((0x61, [3708]))
    height_fix = decoded.index((0x61, [3710]))
    assert font_swap < position_fix < height_fix
    assert (0x66, [1303]) in decoded  # field y
    assert (0x66, [5360]) in decoded  # field height after the font swap
    depth = peak = 0
    for _, opcode, args in operations:
        depth += _stack_effect(opcode, args)
        assert depth >= 0
        peak = max(peak, depth)
    assert depth == 0
    assert peak <= 5


def test_unknown_original_fails_before_font_conversion():
    with pytest.raises(ValueError, match='SHA-256'):
        build_alegreya_localization(b'wrong', {}, {}, b'wrong')


def test_russian_repertoire_includes_every_letter_in_both_cases():
    assert set('АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ') <= RUSSIAN_ALPHABET
    assert set('абвгдеёжзийклмнопрстуфхцчшщъыьэюя') <= RUSSIAN_ALPHABET


@pytest.mark.local
def test_private_alegreya_build_deterministic_and_preserves_original_resources():
    from translation_catalog import load_catalog
    from swf_geometry import read_swf
    originals = list(Path('backups').glob('*.original'))
    fonts = list(Path('reference-fonts/alegreya').glob('*.ttf'))
    if not originals or not fonts:
        pytest.skip('Private original and official local Alegreya are required')
    original = originals[0].read_bytes()
    workspace = load_catalog(Path('local/extraction/translation-workspace.json'))
    plan = load_catalog(Path('local/extraction/options-plan.json'))
    ttf = fonts[0].read_bytes()
    candidate, report = build_alegreya_localization(original, workspace, plan, ttf)
    assert validate_alegreya_localization(original, candidate, workspace, plan, ttf) == report
    before_prefix, before = read_swf(original)
    after_prefix, after = read_swf(candidate)
    assert before_prefix[8:] == after_prefix[8:]
    added = set(report['added_tag_positions'])
    retained = [tag for index, tag in enumerate(after) if index not in added]
    assert len(retained) == len(before)
    assert [i for i, (a, b) in enumerate(zip(before, retained)) if a != b] == sorted(item['tag_index'] for item in report['changed_original_tags'])
    assert all(a == b for a, b in zip(before, retained) if a.code in (75, 73))
    from swf_geometry import parse_text
    title = next(parse_text(tag.payload) for tag in retained
                 if tag.code == 11 and int.from_bytes(tag.payload[:2], 'little') == 3016)
    assert title['records'][0]['height'] == 560
    assert {ord(letter) for letter in RUSSIAN_ALPHABET} <= set(report['font_character_codes'])
    assert report['runtime_verified'] is False


def _wrap_lines(text, advance, width):
    """Greedy Flash-style word wrap; advance returns 0 for glyphs missing in the font."""
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split(' '):
            trial = word if not line else line + ' ' + word
            if not line or sum(advance(ch) for ch in trial) <= width:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


@pytest.mark.local
def test_adaptive_tooltip_bottom_gap_matches_real_font_metrics():
    from translation_catalog import load_catalog
    from swf_geometry import parse_font, read_swf
    originals = list(Path('backups').glob('*.original'))
    fonts = list(Path('reference-fonts/alegreya').glob('*.ttf'))
    if not originals or not fonts:
        pytest.skip('Private original and official local Alegreya are required')
    original = originals[0].read_bytes()
    workspace = load_catalog(Path('local/extraction/translation-workspace.json'))
    plan = load_catalog(Path('local/extraction/options-plan.json'))
    candidate, _ = build_alegreya_localization(
        original, workspace, plan, fonts[0].read_bytes())
    entry = next(e for e in workspace['entries'] if e['id'] == 'abc.4175.79.184')
    text = entry['translation']

    _, original_tags = read_swf(original)
    _, candidate_tags = read_swf(candidate)
    def font_by_id(tags, fid):
        return parse_font(next(
            t.payload for t in tags
            if t.code == 75 and int.from_bytes(t.payload[:2], 'little') == fid))
    celtic = font_by_id(original_tags, 255)
    alegreya = font_by_id(candidate_tags, 6002)

    tag = next(t for t in candidate_tags if t.code == 82)
    inventory = ABCInventory(
        tag.payload[tag.payload.index(b'\0', 4) + 1:], {'tag_path': [4175], 'tag_code': 82})
    inventory.parse()
    code = next(body for method, _, body in inventory.bodies if method == 79)
    operations = inventory.instructions(code, 79)
    site = next(i for i, (_, opcode, args) in enumerate(operations)
                if opcode == 0x4f and args == [3729, 6]
                and operations[i - 5][1] == 0x2c
                and inventory.strings[operations[i - 5][2][0]] == text)
    # Between the addTextfield call and the branch to the shared tail, the inserted
    # snippet must swap the font and then recompute the panel's accumulated height.
    segment = []
    for _, opcode, args in operations[site + 1:]:
        if opcode in (0x10, 0x47):
            break
        segment.append((opcode, args))
    font_swap = segment.index((0x4f, [6921, 1]))
    assert segment.index((0x61, [3708])) > font_swap  # nextTfPos from the bound height
    assert segment.index((0x61, [3710])) > font_swap  # panel h follows nextTfPos
    assert sum(1 for _, opcode, args in operations if (opcode, args) == (0x61, [3708])) == 1
    reset = max(i for i in range(site)
                if operations[i][1:] == (0x4f, [3730, 2]))
    width = operations[reset - 2][2][0] - 20 - 4  # reset(w); tf.width = w - 20; 2px gutters

    def advance(font):
        def measure(char):
            code_point = ord(char)
            if code_point not in font['codes']:
                return 0.0  # embedded Celtic has no Cyrillic glyphs to measure
            return font['advances'][font['codes'].index(code_point)] / 20480 * 12
        return measure

    observed_layout = [
        'При частоте ниже 10 кадров/с включает минимум графических',
        'эффектов.',
        '',
        'При частоте выше 20 кадров/с восстанавливает настройки.',
    ]
    alegreya_lines = _wrap_lines(text, advance(alegreya), width)
    assert alegreya_lines == observed_layout  # runtime screenshot 031/034 layout
    celtic_lines = _wrap_lines(text, advance(celtic), width)
    def line_height(font):
        return (font['ascent'] + font['descent'] + font['leading']) / 20480 * 12
    actual_height = len(alegreya_lines) * line_height(alegreya) + 4
    measured_height = len(celtic_lines) * line_height(celtic) + 4
    assert actual_height - measured_height > 6  # the pre-binding capture undercounts
    designed_bottom_gap = 9 + 8  # addTextfield spacing plus panel bottom inset
    assert designed_bottom_gap - (actual_height - measured_height) < 6
