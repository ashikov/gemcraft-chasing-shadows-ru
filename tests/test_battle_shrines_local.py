"""Private original shrine consumer audit; does not start the game."""
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from install_patch import ORIGINAL_SHA256, sha
from localization_plan import resolve_plan_font_bindings
from swf_geometry import read_swf
from test_battle_shrines import CALLS, EXPECTED_IDS, OFFSETS, PLAN
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


def test_original_shrine_consumers_and_all_technical_exclusions():
    source = os.environ.get('GEMCRAFT_ORIGINAL')
    if not source:
        pytest.skip('Set GEMCRAFT_ORIGINAL')
    original = Path(source).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    abc = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(abc)
    bodies = _bodies(abc)
    ops = {m: inv.instructions(bodies[m][3], m) for m in
           (630, 695, 779, 780, 781, 782, 784, 786, 788, 789, 790, 791, 1438)}
    technical = {630: {'%'}, 791: {'-'}, 784: {'sndshrinecharged'}, 786: {'+'}}
    entries = {}
    for method in (630, 791, 784, 786):
        literals = {at: inv.strings[args[0]] for at, op, args in ops[method]
                    if op == 0x2c and (method != 630 or 10990 <= at < 13466)}
        assert {at for at, value in literals.items() if value not in technical[method]} == OFFSETS[method]
        for at, value in literals.items():
            if at in OFFSETS[method]:
                entries[f'abc.4175.{method}.{at}'] = value
    assert [inv.strings[a[0]] for _, op, a in ops[779] if op == 0x2c] == list('ABCDEFGH')
    assert {inv.strings[a[0]] for _, op, a in ops[780] if op == 0x2c} == {
        '', 'Celtic Garamond for GemCraft'}
    assert not any(op == 0x2c for m in (781, 789, 790) for _, op, _ in ops[m])
    assert next(inv.strings[a[0]] for at, op, a in ops[1438] if at == 116) == 'SHRINE'
    entries['abc.4175.695.1400'] = next(inv.strings[a[0]] for at, op, a in ops[695] if at == 1400)
    assert set(entries) == EXPECTED_IDS
    plan = load_catalog(PLAN)
    rows = {r['id']: r for r in load_catalog('translations/catalog.json')['entries']}
    # The actual source hash and instruction guard are verified on the original,
    # then the binding resolver checks every receiver/call-site guard.
    selected = []
    for key, value in entries.items():
        row = rows[key]
        assert sha(value.encode()) == row['source_sha256']
        selected.append({**row, 'source': value, 'translation': row['manual']['translation']})
    translated = patch_pushstrings(abc, selected)
    builder, _, counts = resolve_plan_font_bindings(abc, translated, plan['abc_font_bindings'])
    patched_inventory = abc_inventory(translated)
    assert set(builder(len(patched_inventory.strings), len(patched_inventory.multinames))) == {630, 695, 780, 791}
    assert counts == {'630': 58, '695': 1, '780': 4, '791': 24}
    for method, expected_calls in CALLS.items():
        actual = {i for i, (at, op, args) in enumerate(ops[method])
                  if op == 0x4f and args[0] == 3729
                  and (method != 630 or 10990 <= at < 13466)}
        assert actual == expected_calls
    dispatch = dict((at, (op, args)) for at, op, args in ops[630])
    assert dispatch[10990] == (0x60, [378])  # Shrine type check
    assert dispatch[11746] == (0x46, [4945, 0])  # drag details
    assert dispatch[11773] == dispatch[11860] == (0x46, [1410, 2])
    assert dispatch[13272] == (0x46, [1401, 0])
    # canActivate checks targets; it does not gate on charge or game mode.
    read_names = {inv.multiname(a[0]) for _, op, a in ops[781] if op == 0x66}
    assert 'currentCharge' not in read_names and 'gameMode' not in read_names
    assert {'monstersOnScene', 'shadows', 'apparitions', 'guardians', 'spires', 'specters'} <= read_names
    assert any(op == 0x66 and inv.multiname(a[0]) == 'displayShrinePerformanceInfo'
               for _, op, a in ops[782])
    assert [at for at, op, a in ops[786] if op == 0x61 and a == [5329]] == [119, 239, 340, 426, 753, 1007, 1117]
