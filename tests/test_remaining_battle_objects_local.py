"""Audit original object consumers and guards without starting GemCraft."""

import os
from collections import Counter
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from install_patch import ORIGINAL_SHA256, sha
from localization_plan import resolve_plan_font_bindings
from swf_geometry import read_swf
from test_remaining_battle_objects import EXPECTED_IDS, PANEL_OFFSETS, PLAN, TECHNICAL_OFFSETS
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


def test_original_remaining_object_inventory_calls_and_address_guards():
    source = os.environ.get('GEMCRAFT_ORIGINAL')
    if not source:
        pytest.skip('Set GEMCRAFT_ORIGINAL')
    original = Path(source).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    abc = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(abc)
    panel = inv.instructions(_bodies(abc)[630][3], 630)
    remaining_types = {inv.multiname(panel[index - 1][2][0])
                       for index, (at, opcode, _) in enumerate(panel)
                       if at >= 13470 and opcode == 0xb3 and index
                       and panel[index - 1][1] == 0x60}
    assert remaining_types == {
        'SecretSwitch', 'Orb', 'MonsterNest', 'Tomb', 'WizTower', 'WizLock',
        'TomeChamber', 'DeadWizard', 'BrokenOrbBase', 'SealedGoE',
        'SummoningNode', 'EnsnaringNode', 'SummoningSocket', 'EnsnaringSocket',
    }
    literals = {at: inv.strings[args[0]] for at, opcode, args in panel
                if opcode == 0x2c and 13548 <= at <= 20909}
    assert set(literals) == PANEL_OFFSETS | TECHNICAL_OFFSETS
    assert all(not any(c.isalpha() for c in literals[at]) for at in TECHNICAL_OFFSETS)
    plan = load_catalog(PLAN)
    rows = {row['id']: row for row in load_catalog('translations/catalog.json')['entries']}
    selected = []
    for at in PANEL_OFFSETS:
        key = f'abc.4175.630.{at}'
        row = rows[key]
        value = literals[at]
        translation = row['manual']['translation']
        assert sha(value.encode()) == row['source_sha256']
        assert Counter(c for c in value if ord(c) < 32) == Counter(
            c for c in translation if ord(c) < 32)
        selected.append({**row, 'source': value, 'translation': translation})
    assert {row['id'] for row in selected} == EXPECTED_IDS
    translated = patch_pushstrings(abc, selected)
    builder, _, counts = resolve_plan_font_bindings(abc, translated, plan['abc_font_bindings'])
    assert set(builder(len(inv.strings), len(inv.multinames))) == {630}
    assert counts == {'630': 180}
    calls = {index for index, (at, opcode, args) in enumerate(panel)
             if opcode == 0x4f and args[0] == 3729 and 13548 <= at <= 20912}
    assert calls == {site['instruction_index'] for site in plan['abc_font_bindings']
                     if site['kind'] == 'panel_format_before'}
    assert len(calls) == 90


def test_warning_bitmap_is_drawn_before_returning_to_the_caller():
    source = os.environ.get('GEMCRAFT_ORIGINAL')
    if not source:
        pytest.skip('Set GEMCRAFT_ORIGINAL')
    original = Path(source).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    abc = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(abc)
    bodies = _bodies(abc)
    ops = {m: {at: (op, args) for at, op, args in inv.instructions(bodies[m][3], m)}
           for m in (597, 713, 352, 1732)}
    for at in (465, 562, 759, 856):
        assert ops[597][at] == (0x4f, [694, 13])
    assert ops[713][285] == (0x4f, [694, 13])
    assert inv.multiname(694) == 'createFloatingText'
    assert ops[352][141] == (0x4a, [2595, 14])
    assert inv.multiname(2595) == 'VfxFloatingText'
    assert ops[1732][211][0] == 0x2c
    assert inv.strings[ops[1732][211][1][0]] == 'Celtic Garamond for GemCraft'
    for at, name in ((243, 'defaultTextFormat'), (253, 'text'),
                     (273, 'width'), (282, 'height'), (291, 'BitmapData'), (320, 'draw')):
        assert inv.multiname(ops[1732][at][1][0]) == name


def test_orblet_creation_count_bounds_from_original_traits_and_iron_stages():
    source = os.environ.get('GEMCRAFT_ORIGINAL')
    if not source:
        pytest.skip('Set GEMCRAFT_ORIGINAL')
    original = Path(source).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    abc = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(abc)
    bodies = _bodies(abc)
    ops = {m: {at: (op, args) for at, op, args in inv.instructions(bodies[m][3], m)}
           for m in (448, 1047, 1069, 1070, 1227)}
    assert ops[1047][33] == (0x24, [7])
    assert inv.multiname(ops[1047][46][1][0]) == 'HATRED'
    assert ops[1069][33] == (0x24, [1])
    assert ops[1069][42] == (0x24, [0])
    assert inv.multiname(ops[1070][200][1][0]) == 'ORBLETS'
    assert ops[1070][208] == (0x24, [2])
    assert ops[1070][231] == (0x24, [1])
    assert ops[448][28] == (0x24, [5])
    stages = {at for at, (op, args) in ops[1227].items()
              if op == 0x66 and inv.multiname(args[0]) == 'WIZTOWER_LOCATION'}
    assert stages == {1570, 1840, 2434, 2812, 3033, 3395, 4129, 4969, 5647, 6387}
