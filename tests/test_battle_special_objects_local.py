"""Audit the owned original SWF without starting the game."""

import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from install_patch import ORIGINAL_SHA256, sha
from localization_plan import resolve_plan_font_bindings
from swf_geometry import read_swf
from test_battle_special_objects import EXPECTED_IDS, HOLDER_OFFSETS, PANEL_OFFSETS, PLAN
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


def test_original_special_object_consumers_and_address_guards():
    source = os.environ.get('GEMCRAFT_ORIGINAL')
    if not source:
        pytest.skip('Set GEMCRAFT_ORIGINAL')
    original = Path(source).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    abc = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(abc)
    bodies = _bodies(abc)
    panel = inv.instructions(bodies[630][3], 630)
    holders = inv.instructions(bodies[1427][3], 1427)
    literals = {}
    for at, opcode, args in panel:
        if opcode == 0x2c and (5855 <= at < 6339 or 7454 <= at < 10940):
            if at not in (9832, 10113, 10223):
                literals[f'abc.4175.630.{at}'] = inv.strings[args[0]]
    for at, opcode, args in holders:
        if opcode == 0x2c and at in HOLDER_OFFSETS:
            literals[f'abc.4175.1427.{at}'] = inv.strings[args[0]]
    assert {int(key.rsplit('.', 1)[1]) for key in literals if '.630.' in key} == PANEL_OFFSETS
    assert set(literals) == EXPECTED_IDS
    assert next(inv.strings[args[0]] for at, opcode, args in panel
                if at == 9832 and opcode == 0x2c) == 'unknown beacon type: '
    plan = load_catalog(PLAN)
    rows = {row['id']: row for row in load_catalog('translations/catalog.json')['entries']}
    selected = []
    for key, value in literals.items():
        row = rows[key]
        assert sha(value.encode()) == row['source_sha256']
        selected.append({**row, 'source': value, 'translation': row['manual']['translation']})
    translated = patch_pushstrings(abc, selected)
    builder, _, counts = resolve_plan_font_bindings(abc, translated, plan['abc_font_bindings'])
    assert set(builder(len(inv.strings), len(inv.multinames))) == {630}
    assert counts == {'630': 112}
    calls = {index for index, (at, opcode, args) in enumerate(panel)
             if opcode == 0x4f and args[0] == 3729
             and (5855 <= at < 6339 or 7454 <= at < 10940)}
    assert calls == {site['instruction_index'] for site in plan['abc_font_bindings']
                     if site['kind'] == 'panel_format_before'}
    assert len(calls) == 56
