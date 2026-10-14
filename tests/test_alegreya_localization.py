"""Synthetic binding and guarded-source checks, plus private offline integration."""
from pathlib import Path

import pytest

from alegreya_localization import (
    RUSSIAN_ALPHABET, build_alegreya_localization, field_binding, latest_tooltip_field,
    validate_alegreya_localization,
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
