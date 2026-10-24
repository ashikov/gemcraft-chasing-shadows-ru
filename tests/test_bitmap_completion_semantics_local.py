"""Execute bounded real bitmap expressions offline; this is not an AIR VM."""
from decimal import Decimal, ROUND_HALF_UP, localcontext
from functools import lru_cache
import math
import os
from pathlib import Path
import struct

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCReader
from install_patch import ORIGINAL_SHA256, sha
from swf_geometry import read_swf
from test_bitmap_completion import COUNTERS
from test_bitmap_placement import without_placement_ops
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


def number_string(value):
    if isinstance(value, str):
        return value
    if isinstance(value, (float, int)):
        return str(int(value)) if value == int(value) else str(value)
    raise AssertionError(f'Unsupported string conversion: {value!r}')


class Expressions:
    """Only the reviewed arithmetic, concat and formatter instructions.

    Property reads require explicit supplied objects. Unsupported instructions
    fail rather than returning an invented value. Python supplies primitive
    operations; the game's actual branches, pool constants and loops execute.
    """

    def __init__(self, data):
        raw = abc_bytes(read_swf(data)[1])
        self.inv = abc_inventory(raw)
        self.operations = {m: self.inv.instructions(body[3], m)
                           for m, body in _bodies(raw).items()}
        reader = ABCReader(raw)
        reader.take(4)
        self.ints = [None] + [reader.encoded(32) for _ in range(1, reader.count())]
        self.ints[1:] = [value - 2 ** 32 if value >= 2 ** 31 else value
                         for value in self.ints[1:]]
        self.uints = [None] + [reader.encoded(32) for _ in range(1, reader.count())]
        self.doubles = [None] + [struct.unpack('<d', reader.take(8))[0]
                                 for _ in range(1, reader.count())]

    @lru_cache(maxsize=None)
    def format(self, value, long=False):
        stack, _, _, _ = self.run(1245, 0, None, {0: {}, 1: value, 2: 0, 3: False}, long=long)
        assert len(stack) == 1 and isinstance(stack[0], str)
        return stack[0]

    def call(self, receiver, name, arguments, long):
        if receiver == 'NumberFormatter':
            assert name == 'format' and len(arguments) == 1
            return self.format(arguments[0], long)
        if receiver == 'Math':
            operations = {'round': lambda v: float(math.floor(v + .5)),
                          'floor': math.floor, 'pow': pow, 'sqrt': math.sqrt,
                          'max': max, 'min': min}
            assert name in operations
            return operations[name](*arguments)
        if name == 'random':
            assert not arguments and isinstance(receiver, dict)
            return receiver['random']
        if name == 'g':
            assert not arguments
            return receiver['value']
        if name == 'getMana':
            assert not arguments
            return receiver['mana']
        if name == 's':
            assert len(arguments) == 1
            receiver['value'] = arguments[0]
            return None
        if name == 'getRnd':
            assert not arguments
            return receiver['random']
        if name == 'toString':
            assert not arguments
            return number_string(receiver)
        if name == 'toPrecision':
            assert receiver >= 1e12 and arguments[0] in (4, 8)
            # Positive Number.toPrecision ties choose the larger decimal value;
            # Python's float formatter otherwise applies ties-to-even.
            with localcontext() as context:
                context.rounding = ROUND_HALF_UP
                return f'{Decimal.from_float(float(receiver)):.{arguments[0] - 1}e}'
        if name == 'indexOf':
            return receiver.find(*arguments)
        if name == 'substring':
            start = max(0, int(arguments[0]))
            end = max(0, int(arguments[1])) if len(arguments) == 2 else len(receiver)
            return receiver[min(start, end):max(start, end)]
        if name == 'substr':
            assert len(arguments) == 2 and min(arguments) >= 0
            return receiver[int(arguments[0]):int(arguments[0] + arguments[1])]
        if name == 'split':
            assert arguments == ['']
            return list(receiver)
        if name == 'charAt':
            index = int(arguments[0])
            return receiver[index] if 0 <= index < len(receiver) else ''
        if name == 'pop':
            assert not arguments and receiver
            return receiver.pop()
        if name == 'splice':
            start, delete, *insert = arguments
            assert delete == 0
            receiver[int(start):int(start)] = insert
            return []
        if name == 'concat':
            return receiver + ''.join(map(number_string, arguments))
        raise AssertionError(f'Unsupported call: {name}')

    def run(self, method, start, end, locals_, *, long=False, globals_extra=None):
        ops = self.operations[method]
        positions = {at: index for index, (at, _, _) in enumerate(ops)}
        locals_ = dict(locals_)
        globals_ = {'Math': 'Math', 'NumberFormatter': 'NumberFormatter',
                    'Prefs': {'lotsOfDigitsForExpNums': long}}
        globals_.update(globals_extra or {})
        stack, visited = [], set()
        index = positions[start]
        for _ in range(10000):
            at, opcode, args = ops[index]
            if end is not None and at >= end:
                return stack, locals_, at, visited
            visited.add(at)
            index += 1
            if opcode in (0x09, 0xef, 0xf0, 0xf1):
                continue
            if opcode == 0x30:
                stack.pop()
            elif opcode == 0x20:
                stack.append(None)
            elif opcode == 0x28:
                stack.append(float('nan'))
            elif opcode in (0x26, 0x27):
                stack.append(opcode == 0x26)
            elif opcode in (0x24, 0x25):
                stack.append(args[0] - 256 if opcode == 0x24 and args[0] > 127 else args[0])
            elif opcode == 0x2f:
                stack.append(self.doubles[args[0]])
            elif opcode in (0x2d, 0x2e):
                stack.append((self.ints if opcode == 0x2d else self.uints)[args[0]])
            elif opcode == 0x2c:
                stack.append(self.inv.strings[args[0]])
            elif opcode == 0x60:
                stack.append(globals_[self.inv.multiname(args[0])])
            elif opcode == 0x62 or 0xd0 <= opcode <= 0xd3:
                stack.append(locals_[args[0] if opcode == 0x62 else opcode - 0xd0])
            elif opcode == 0x63 or 0xd4 <= opcode <= 0xd7:
                locals_[args[0] if opcode == 0x63 else opcode - 0xd4] = stack.pop()
            elif opcode == 0x66:
                if self.inv.multinames[args[0]]['kind'] == 27:
                    key, receiver = stack.pop(), stack.pop()
                    stack.append(receiver[int(key)])
                else:
                    name, receiver = self.inv.multiname(args[0]), stack.pop()
                    stack.append(len(receiver) if name == 'length' else receiver[name])
            elif opcode == 0x2a:
                stack.append(stack[-1])
            elif opcode == 0x29:
                stack.pop()
            elif opcode == 0x2b:
                stack[-2:] = reversed(stack[-2:])
            elif opcode == 0x56:
                count = args[0]
                values = stack[-count:] if count else []
                if count:
                    del stack[-count:]
                stack.append(values)
            elif opcode in (0x46, 0x4f):
                name, count = self.inv.multiname(args[0]), args[1]
                values = stack[-count:] if count else []
                if count:
                    del stack[-count:]
                value = self.call(stack.pop(), name, values, long)
                if opcode == 0x46:
                    stack.append(value)
            elif opcode in (0xa0, 0xa1, 0xa2, 0xa3):
                right, left = stack.pop(), stack.pop()
                if opcode == 0xa0:
                    value = (number_string(left) + number_string(right)
                             if isinstance(left, str) or isinstance(right, str) else left + right)
                else:
                    value = {0xa1: lambda: left - right, 0xa2: lambda: left * right,
                             0xa3: lambda: left / right}[opcode]()
                stack.append(value)
            elif opcode in (0xab, 0xad, 0xae, 0xaf, 0xb0):
                right, left = stack.pop(), stack.pop()
                stack.append({0xab: lambda: left == right, 0xad: lambda: left < right,
                              0xae: lambda: left <= right, 0xaf: lambda: left > right,
                              0xb0: lambda: left >= right}[opcode]())
            elif opcode in (0x73, 0x75, 0x85):
                value = stack.pop()
                stack.append({0x73: int, 0x75: float,
                              0x85: lambda x: x if x is None else number_string(x)}[opcode](value))
            elif opcode in (0x80, 0x82):
                pass
            elif opcode in (0x91, 0x93, 0xc1):
                stack.append(stack.pop() + (1 if opcode == 0x91 else -1))
            elif opcode == 0x90:
                stack.append(-stack.pop())
            elif opcode == 0xc2:
                locals_[args[0]] += 1
            elif opcode == 0x10:
                index = positions[ops[index][0] + args[0]]
            elif opcode in (0x11, 0x12):
                if bool(stack.pop()) == (opcode == 0x11):
                    index = positions[ops[index][0] + args[0]]
            elif 0x0c <= opcode <= 0x1a:
                right, left = stack.pop(), stack.pop()
                take = {0x0c: lambda: not left < right, 0x0d: lambda: not left <= right,
                        0x0e: lambda: not left > right, 0x0f: lambda: not left >= right,
                        0x13: lambda: left == right, 0x14: lambda: left != right,
                        0x15: lambda: left < right, 0x16: lambda: left <= right,
                        0x17: lambda: left > right, 0x18: lambda: left >= right}[opcode]()
                if take:
                    index = positions[ops[index][0] + args[0]]
            elif opcode == 0x48:
                return stack, locals_, at, visited
            else:
                raise AssertionError(f'Unsupported m{method}@{at}: {opcode:x}')
        raise AssertionError('Bounded expression did not terminate')


@pytest.fixture(scope='module')
def semantic_programs():
    if not os.environ.get('GEMCRAFT_PR32_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR32_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR32_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    assert sha(candidate) == load_catalog('translations/build-manifest.json')['expected_candidate_sha256']
    return Expressions(original), Expressions(candidate)


def original_instruction_pairs(before, after, method):
    """Keep actual offsets while excluding only the exact reviewed snippets."""
    old_ops = before.operations[method]
    new_ops = without_placement_ops(after.inv, method, after.operations[method])
    if method == 243 and len(new_ops) == len(old_ops) + 1:
        assert new_ops[117][1:] == (0x2b, [])
        new_ops = new_ops[:117] + new_ops[118:]
    assert len(old_ops) == len(new_ops)
    return list(zip(old_ops, new_ops))


def translated_span(before, after, method, start, end):
    """Map source expressions across the word-order and post-call insertions."""
    offsets = {old[0]: new[0] for old, new in original_instruction_pairs(before, after, method)}
    return offsets[start], offsets[end]


def source_addressed_ops(before, after, method):
    """Index candidate instructions by original addresses without hiding operands."""
    return {old[0]: new[1:] for old, new in original_instruction_pairs(before, after, method)}


def test_actual_formatter_rounding_grouping_sign_and_precision_branches(semantic_programs):
    before, after = semantic_programs
    cases = [(0, '0'), (.49, '0'), (.5, '1'), (1.49, '1'), (1.5, '2'),
             (21.5, '22'), (999.5, '1,000'), (1000, '1,000'),
             (-1.5, '-2'), (-.49, '-0'), (-1000, '-1,000'),
             (4294967295, '4,294,967,295'), (999999999999, '999,999,999,999'),
             (999999999999.49, '999,999,999,999'),
             (999999999999.5, '1,000,000,000,000'),
             (999999999999.9, '1,000,000,000,000')]
    for program in (before, after):
        for number, expected in cases:
            assert program.format(number) == expected
        for long, precision in ((False, 3), (True, 7)):
            for number, short, full in ((1e12, '1.000 e12', '1.0000000 e12'),
                                        (1.2345e12, '1.235 e12', '1.2345000 e12'),
                                        (1.23456789e12, '1.235 e12', '1.2345679 e12'),
                                        (1e308, '1.000 e308', '1.0000000 e308')):
                assert program.format(number, long) == (full if precision == 7 else short)


def test_full_actual_dynamic_concatenations_all_number_forms(semantic_programs):
    before, after = semantic_programs
    # Every numeric input remains the real expression's Number/local/property.
    # Negative and fractional core/hit inputs are robustness probes, not gameplay claims.
    numbers = [*range(26), 29, 30, 31, 32, 35, 99, 100, 101, 199, 200, 201,
               .49, .5, 1.5, 21.5, 999.5, 1000, 4294967295,
               999999999999, 999999999999.5, 1e12, 1.23456789e12, 1e308, -1, -1.5]
    specs = {(242, 3807): (3760, 3777, 15), (242, 3907): (3856, 3873, 16),
             (243, 397): (359, 375, 1), (539, 2053): (1991, 2008, 12),
             (675, 1098): (1035, 1052, 6), (675, 1208): (1146, 1163, 6),
             (1144, 138): (70, 93, 1), (1596, 4783): (4744, 4761, 21)}
    assert set(specs) == set(COUNTERS)
    for key, (start, end, local) in specs.items():
        method, _ = key
        start, end = translated_span(before, after, method, start, end)
        for long in (False, True):
            for number in numbers:
                value = {'sellValue': {'value': number}} if method == 1144 else number
                stack, _, _, _ = after.run(method, start, end, {local: value}, long=long)
                assert stack == [COUNTERS[key] + after.format(number, long)]
    start, end = translated_span(before, after, 243, 264, 283)
    for long in (False, True):
        for number in numbers:
            stack, _, _, _ = after.run(243, start, end,
                                      {0: {'monsterKillChainCounter': {'value': number}}}, long=long)
            assert stack == ['Убийств подряд: ' + after.format(number, long)]
    start, end = translated_span(before, after, 1596, 4649, 4665)
    for count in (0, 1, 2, 4, 5, 9, 10, 11, 20, 21, 22, 25, 1000):
        stack, _, _, _ = after.run(1596, start, end, {10: [None] * count})
        assert stack == [after.format(count) + ' попад.']


def test_reachable_counter_gates_and_real_piecewise_rewards(semantic_programs):
    before, after = semantic_programs
    for program in (before, after):
        for count in (0, 1, 21, 29, 30, 31, 32, 35, 99, 100, 101, 199, 200, 201, 1000):
            core = {0: {'monsterKillChainCounter': {'value': count}}}
            _, _, destination, _ = program.run(243, 45, 59, core)
            assert (destination == 59) == (count > 29)
            _, state, _, visits = program.run(243, 82, 216, core)
            expected = (2 + (count - 30) * .357 if count <= 100 else
                        29 + (count - 100) * .47 if count <= 200 else
                        76 + (count - 200) * .56)
            assert state[1] == pytest.approx(expected)
            assert (99 in visits, 150 in visits, 186 in visits) == (
                count <= 100, 100 < count <= 200, count > 200)
        for count in (0, 1, 9, 10, 11, 21, 22, 25, 1000):
            _, _, destination, _ = program.run(1596, 4538, 4549, {10: [None] * count})
            assert (destination == 4549) == (count > 9)
            _, state, _, _ = program.run(1596, 4559, 4588, {10: [None] * count})
            assert state[21] == pytest.approx(1.24 * (count * 1.14 + 2) ** .83)


def test_real_display_preferences_modes_and_resource_producers(semantic_programs):
    before, after = semantic_programs
    for program in (before, after):
        for enabled in (False, True):
            for normal in (False, True):
                globals_ = {'GV': {'ppd': {'gameMode': int(normal)}},
                            'GameMode': {'NORMAL': 1},
                            'Prefs': {'showXpRewardFloatingNumbers': enabled,
                                      'displaySpellPerformanceInfo': enabled}}
                for method, start, end in ((243, 312, 344), (539, 1912, 1944)):
                    start, end = translated_span(before, program, method, start, end)
                    _, _, destination, _ = program.run(method, start, end, {}, globals_extra=globals_)
                    assert (destination == end) == (enabled and normal)
                _, _, destination, _ = program.run(1596, 4609, 4619, {}, globals_extra=globals_)
                assert (destination == 4619) == enabled
                _, _, destination, _ = program.run(1596, 4695, 4714, {}, globals_extra=globals_)
                assert (destination == 4714) == normal
        for count in (0, .5, 1, 2, 4, 20, 21, 1000):
            core = {0: {'core': {'orb': {'numIntactWaves': {'value': count}}}}}
            _, state, _, _ = program.run(539, 1828, 1873, core)
            assert state[12] == math.floor((1.13 * (1 + count)) ** 1.32 * 1.41 + .5)
        for cost in (0, 1, 2, 5, 21, 99.5, 1000, 10000):
            _, state, _, _ = program.run(675, 68, 92, {1: {'cost': {'value': cost}}})
            assert state[6] == math.floor(cost * .7)
        for method, start, end, gem_local, count_local in (
                (665, 965, 989, 10, 12), (666, 942, 966, 7, 9)):
            for count in (1, 2, 4, 5, 20, 21, 22, 25, 999):
                for cost in (1, 2.5, 21, 1000):
                    for mana in (0, cost * count - .5, cost * count, cost * count + .5):
                        state = {0: {'core': {'mana': mana}},
                                 gem_local: {'cost': {'value': cost}}, count_local: count}
                        _, _, destination, _ = program.run(method, start, end, state)
                        assert (destination == end) == (mana < cost * count)


def test_refund_amount_same_property_and_conservative_rarity_upgrade_envelope(semantic_programs):
    before, after = semantic_programs
    for program in (before, after):
        wheels = program.operations[680]
        assert {args[0] for _, op, args in wheels if op == 0x24} <= set(range(10))
        digit = program.operations[684]
        assert [(op, args) for _, op, args in digit[-3:]] == [
            (0x24, [10]), (0xa4, []), (0x48, [])]
        random = program.operations[683]
        assert [args for _, op, args in random if op == 0x46] == [[1269, 0]] * 3
        coefficients = [program.doubles[args[0]] for _, op, args in random if op == 0x2f]
        assert all(value > 0 for value in coefficients) and sum(coefficients) * 9 < 1
        ops = source_addressed_ops(before, program, 1144)
        assert ops[77] == (0x66, [2086])  # displayed sellValue
        assert ops[84] == (0x46, [4755, 1])
        assert ops[366] == ops[375] == (0x66, [806])  # shadowCoreAmount destination/source
        assert ops[382] == (0xd1, [])
        assert ops[383] == (0x66, [2086])  # the same fragment.sellValue is added
        assert ops[386] == (0x46, [863, 0])
        assert ops[390] == (0xa0, [])
        assert ops[391] == (0x4f, [864, 1])
        # Pure source instructions evaluate all rarity1..100 and both random
        # endpoints. rnd=1 is a conservative upper bound, not a reachable seed.
        maximum = 0
        for rarity in range(1, 101):
            for random in (0, 1):
                fragment = {'rarity': {'value': rarity}, 'sellValue': {'value': 0},
                            'upgradeCost': {'value': 0}}
                locals_ = {0: fragment, 2: {'random': random}}
                program.run(1207, 2174, 2474, locals_)
                for level in range(10):
                    program.run(1207, 2759, 2845, locals_)
                    value = fragment['sellValue']['value']
                    assert value == int(value) and value >= 0
                    maximum = max(maximum, value)
        assert maximum == 1500
        loop = {at: (op, args) for at, op, args in program.operations[1207]}
        assert loop[2477] == (0x24, [0])
        assert loop[2859] == (0x16, [-379])  # i<=upgradeLevel, up to ten passes


def test_refund_callers_and_reachable_mouse_domains(semantic_programs):
    before, after = semantic_programs
    for program in (before, after):
        inv = program.inv
        for name, expected in (
                ('castRefundGem', {521: [707], 612: [147], 708: [1019]}),
                ('refundFragment', {1137: [78], 1152: [471], 1153: [463]})):
            callers = {}
            for method, ops in program.operations.items():
                selected = [at for at, op, args in ops
                            if op in (0x46, 0x4f) and inv.multiname(args[0]) == name]
                if selected:
                    callers[method] = selected
            assert callers == expected
        ops = {method: source_addressed_ops(before, program, method)
               for method in (521, 612, 616, 675, 708, 1136, 1137, 1152, 1153)}
        assert inv.strings[11170:11172] == ['pDeselectEverything', 'pRefundByKeyboard']
        assert method_metadata(inv.reader.data)[675][4] == [(11, 11), (10, 10)]
        # Keyboard mode is the third parameter. It does not test mouseY=60.
        assert ops[675][880] == (0xd3, [])
        assert ops[675][881][0] == 0x11
        for keyboard in (False, True):
            start, end = translated_span(before, program, 675, 880, 885)
            _, _, destination, _ = program.run(675, start, end, {3: keyboard})
            expected, _ = translated_span(before, program, 675, 1106 if keyboard else 885, 1212)
            assert destination == expected
        assert [ops[612][at] for at in (117, 118, 144, 145, 146, 147)] == [
            (0x26, []), (0x46, [1156, 1]), (0xd1, []),
            (0x26, []), (0x26, []), (0x4f, [1253, 3])]
        assert [ops[521][at] for at in (701, 702, 705, 706, 707)] == [
            (0xd1, []), (0x66, [2257]), (0x27, []), (0x27, []), (0x4f, [1253, 3])]
        assert ops[708][1019] == (0x4f, [1253, 1])  # defaults: deselect=true, keyboard=false
        # True keyboard calls first select a gem under the cursor: either the
        # battlefield's open interval or the inventory's stricter y interval.
        for y in (0, 53, 53.01, 59, 60, 143, 143.01, 478.99, 479, 596.99, 597, 612):
            _, state, _, _ = program.run(
                616, 63, 77, {}, globals_extra={'GV': {'main': {'mouseY': y}}})
            assert state[3] == y - 53
            _, _, destination, _ = program.run(616, 103, 124, state)
            assert (destination == 124) == (53 < y < 597)
            _, _, destination, _ = program.run(
                616, 566, 608, {0: {'cnt': {'root': {'mouseY': y}}}})
            assert (destination == 608) == (143 < y < 479)
        # Mouse release refunds occupy 4<y<46. The specter caller supplies
        # false,false and has no pointer lookup, so it receives no such bound.
        assert not any(op in (0x46, 0x4f) and inv.multiname(args[0]) == 'getGemUnderPointer'
                       for _, op, args in program.operations[521])
        assert not any(op == 0x66 and inv.multiname(args[0]) == 'mouseY'
                       for _, op, args in program.operations[521])
        for y in (0, 4, 4.01, 20, 45.99, 46, 59, 60, 597, 612):
            _, state, _, _ = program.run(708, 82, 95, {0: {'cnt': {'root': {'mouseY': y}}}})
            assert state[4] == y
            _, _, destination, _ = program.run(708, 965, 988, state)
            assert (destination == 988) == (4 < y < 46)
        for method, first, invoke in ((1137, 54, 55), (1152, 444, 445), (1153, 439, 440)):
            assert ops[method][first] == (0x26, [])
            assert ops[method][invoke] == (0x46, [1843, 1])
        # Inventory fragment lookup is 132<=y<438. Talisman lookup instead
        # uses a radius40 circle around each actual slot center, max y439.
        for y in (0, 131.99, 132, 143, 437.99, 438, 478.99, 479, 612):
            _, _, destination, _ = program.run(
                1136, 120, 161, {0: {'mc': {'root': {'mouseY': y}}}})
            assert (destination == 161) == (132 <= y < 438)
        stack, _, _, _ = program.run(1103, 111, 157, {})
        assert len(stack) == 1 and len(stack[0]) == 15
        slot_ys = stack[0]
        assert min(slot_ys) == 66 and max(slot_ys) == 439
        assert ops[1136][345] == (0x24, [40])
        assert ops[1136][349] == (0x0c, [33])
        for y in (398.99, 399, 399.01, 439, 478.99, 479, 479.01):
            state = {0: {'slotXs': [500], 'slotYs': [max(slot_ys)]},
                     2: 0, 3: 500, 4: y}
            _, _, destination, _ = program.run(1136, 305, 353, state)
            assert (destination == 353) == (399 < y < 479)
