"""Evaluate complete, bounded shrine expressions from actual original ABC.

Formatter results are injected verbatim. This verifies assembly/format arguments,
not a Python reimplementation of Flash numeric formatting or game mechanics.
"""
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from install_patch import ORIGINAL_SHA256, sha
from shrine_phrase_model import COMPASS_DIRECTIONS, NUMBERS, PHRASES, TYPES, catalog_literals, joined, phrase_values
from swf_geometry import read_swf
from test_battle_shrines import EXPECTED_IDS
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


def evaluate(inventory, operations, phrase, number, singular=False, mode='NORMAL'):
    """Interpret only the expression opcodes, failing closed on any new opcode."""
    rows = [(at, op, args) for at, op, args in operations
            if phrase.start <= at < phrase.end]
    positions = {at: index for index, (at, _, _) in enumerate(rows)}
    stack, formats, index, outputs = [], [], 0, iter((number, '21'))
    while index < len(rows):
        at, op, args = rows[index]
        index += 1
        if op in (0xf0, 0xf1, 0xef, 0x82):
            continue
        if op == 0x2c:
            stack.append(inventory.strings[args[0]])
        elif op == 0x24:
            stack.append(args[0])
        elif op == 0x2f:
            stack.append(('double', args[0]))
        elif op in (0x26, 0x27):
            stack.append(op == 0x26)
        elif 0xd0 <= op <= 0xd3:
            stack.append(('local', op - 0xd0))
        elif op == 0x62:
            stack.append(int(number) if phrase.raw_number and args[0] == 27 else ('local', args[0]))
        elif op == 0x60:
            stack.append(('global', inventory.multiname(args[0])))
        elif op == 0x66:
            name = inventory.multiname(args[0])
            key = stack.pop() if inventory.multinames[args[0]]['kind'] in (0x1b, 0x1c) else None
            receiver = stack.pop()
            if name in ('targetsHit', 'targetsKilled'):
                value = 1 if singular else 2
            elif phrase.raw_number and name in ('length', 'orbletsLostNum'):
                value = int(number)
            elif phrase.raw_number and name in ('gameMode', 'NORMAL'):
                value = mode if name == 'gameMode' else 'NORMAL'
            else:
                value = ('index', receiver, key) if key is not None else ('get', receiver, name)
            stack.append(value)
        elif op == 0x46:
            name, count = inventory.multiname(args[0]), args[1]
            values = stack[-count:] if count else []
            if count:
                del stack[-count:]
            receiver = stack.pop()
            if name == 'format':
                assert receiver == ('global', 'NumberFormatter')
                formats.append(tuple(values[1:]))
                stack.append(next(outputs))
            elif name == 'floor':
                assert receiver == ('global', 'Math') and count == 1
                stack.append(next(outputs))
            else:
                assert name in ('g', 'ceil', 'getEffectValue', 'getDamageToSpread')
                stack.append(receiver if phrase.raw_number and name == 'g'
                             else ('call', receiver, name, tuple(values)))
        elif op in (0xa0, 0xa1, 0xa2, 0xa3):
            right, left = stack.pop(), stack.pop()
            if op == 0xa0 and (isinstance(left, str) or isinstance(right, str)):
                assert not isinstance(left, tuple) and not isinstance(right, tuple)
                value = str(left) + str(right)
            elif isinstance(left, (int, float)) and isinstance(right, (int, float)):
                value = {0xa0: lambda: left + right, 0xa1: lambda: left - right,
                         0xa2: lambda: left * right, 0xa3: lambda: left / right}[op]()
            else:
                value = ('arithmetic', op, left, right)
            stack.append(value)
        elif op == 0xab:
            right, left = stack.pop(), stack.pop()
            stack.append(left == right)
        elif op == 0xaf:
            right, left = stack.pop(), stack.pop()
            stack.append(left > right)
        elif op == 0x12:
            if not stack.pop():
                next_at = rows[index][0]
                index = positions[next_at + args[0]]
        elif op == 0x10:
            next_at = rows[index][0]
            index = positions[next_at + args[0]]
        else:
            raise AssertionError((phrase, at, hex(op), args))
    assert len(stack) == 1 and isinstance(stack[0], str), (phrase, stack)
    assert tuple(formats) == phrase.precision
    return stack[0]


@pytest.fixture(scope='module')
def shrine_expressions():
    source = os.environ.get('GEMCRAFT_ORIGINAL')
    if not source:
        pytest.skip('Set GEMCRAFT_ORIGINAL')
    original = Path(source).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    abc = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(abc)
    rows = {r['id']: r for r in load_catalog('translations/catalog.json')['entries']}
    selected = []
    for method in (630, 791, 784, 786):
        for at, op, args in inv.instructions(_bodies(abc)[method][3], method):
            key = f'abc.4175.{method}.{at}'
            phrase_site = any(p.method == method and p.start <= at < p.end for p in PHRASES)
            compass_site = method == 630 and at in {*COMPASS_DIRECTIONS, 13609}
            if op == 0x2c and key in rows and (key in EXPECTED_IDS or phrase_site or compass_site):
                row = rows[key]
                selected.append({**row, 'source': inv.strings[args[0]],
                                 'translation': row['manual']['translation']})
    patched = patch_pushstrings(abc, selected)
    after = abc_inventory(patched)
    operations = {method: after.instructions(_bodies(patched)[method][3], method)
                  for method in (630, 791, 784, 786)}
    return inv, after, operations


def test_actual_abc_complete_phrases_and_plural_paths(shrine_expressions):
    _, inv, operations = shrine_expressions
    literals = catalog_literals()
    for phrase in PHRASES:
        numbers = ('0', '1', '2', '5', '21', '1000') if phrase.raw_number else (*NUMBERS, '1.00')
        for number in numbers:
            for singular in (False, True):
                for mode in ('NORMAL', 'IRON') if phrase.raw_number else ('NORMAL',):
                    actual = evaluate(inv, operations[phrase.method], phrase, number, singular, mode)
                    assert actual == joined(phrase, literals, number, singular=singular, mode=mode)
                    assert actual == phrase.expected.format(**phrase_values(phrase, number, mode=mode))


def test_actual_nine_type_dispatch_and_both_gem_routes(shrine_expressions):
    _, inv, operations = shrine_expressions
    generic = [(at, op, args) for at, op, args in operations[630]
               if 11933 <= at < 12271]
    detailed = [(at, op, args) for at, op, args in operations[791]
                if 262 <= at < 805]
    for rows, calls in ((generic, (11963, 12001, 12039, 12077, 12115, 12153, 12191, 12229, 12267)),
                        (detailed, (315, 376, 437, 498, 559, 620, 681, 740, 801))):
        assert tuple(inv.multiname(args[0]) for _, op, args in rows
                     if op == 0x66 and inv.multiname(args[0]) in TYPES) == TYPES
        assert tuple(at for at, op, args in rows if op == 0x4f and args[0] == 3729) == calls
        assert sum(op == 0x14 for _, op, _ in rows) == 9
    by_at = {at: (op, args) for at, op, args in operations[630]}
    assert by_at[11746] == (0x46, [4945, 0])  # dragged gem predicate
    assert by_at[11773] == by_at[11860] == (0x46, [1410, 2])
    assert by_at[11780] == (0x10, [487])
    assert by_at[11867] == (0x10, [400])
    # Both successful detail calls skip the generic descriptions to 12271.
    assert 11784 + 487 == 11871 + 400 == 12271
    assert by_at[13087] == (0x0c, [90])  # charge < 1 / >= 1
    assert [at for at, op, args in operations[786] if op == 0x61 and args == [5329]] == [
        119, 239, 340, 426, 753, 1007, 1117]


def test_actual_compass_array_and_word_boundaries(shrine_expressions):
    _, inv, operations = shrine_expressions
    rows = [(at, op, args) for at, op, args in operations[630]
            if 13548 <= at < 13588 and op not in (0xf0, 0xf1, 0xef)]
    assert [at for at, op, _ in rows if op == 0x2c] == list(COMPASS_DIRECTIONS)
    assert [(op, args) for _, op, args in rows[8:]] == [
        (0x56, [8]), (0x62, [25]), (0x66, [1411]), (0x66, [5732]),
        (0x85, []), (0x63, [26])]
    expression = [(at, op, args) for at, op, args in operations[630]
                  if 13609 <= at < 13615]
    assert [(op, args) for _, op, args in expression[1:]] == [(0x62, [26]), (0xa0, [])]
    prefix = inv.strings[expression[0][2][0]]
    for (_, _, args), direction in zip(rows[:8], COMPASS_DIRECTIONS.values()):
        assert prefix + inv.strings[args[0]] == 'Указывает на ' + direction


def test_cumulative_candidate_contains_exact_audited_expressions(shrine_expressions):
    _, inv, operations = shrine_expressions
    root = os.environ.get('GEMCRAFT_PR38_ROOT')
    if not root:
        pytest.skip('Set GEMCRAFT_PR38_ROOT for the built cumulative candidate')
    candidate = (Path(root) / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    assert sha(candidate) == load_catalog('translations/build-manifest.json')['expected_candidate_sha256']
    abc = abc_bytes(read_swf(candidate)[1])
    actual = abc_inventory(abc)
    bodies = _bodies(abc)

    def normalized(inventory, rows):
        return [(op, inventory.strings[args[0]] if op == 0x2c else args)
                for _, op, args in rows if op not in (0xf0, 0xf1, 0xef)]

    for phrase in PHRASES:
        expected = normalized(inv, [row for row in operations[phrase.method]
                                    if phrase.start <= row[0] < phrase.end])
        rows = normalized(actual, actual.instructions(bodies[phrase.method][3], phrase.method))
        assert any(rows[index:index + len(expected)] == expected
                   for index in range(len(rows) - len(expected) + 1)), phrase
