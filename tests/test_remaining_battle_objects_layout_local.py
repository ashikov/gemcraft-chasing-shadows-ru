"""Real SWF outline envelope for PR29; this never establishes AIR rendering."""

import json
import os
from functools import lru_cache
from itertools import product
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCReader
from install_patch import ORIGINAL_SHA256, sha
from swf_geometry import parse_font, read_swf
from test_battle_shrines_layout_local import OutlineModel
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local
BASE_SHA = '4fe24607db3aa9039dd7aa6d54708764c428d3035c8dd5c5c24fa73a30ef9034'


@pytest.fixture(scope='module')
def remaining_layout():
    if not os.environ.get('GEMCRAFT_PR29_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR29_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR29_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    baseline = (root / 'baseline/gc-cs-steam.ru.swf').read_bytes()
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    assert sha(baseline) == BASE_SHA
    assert sha(candidate) == load_catalog('translations/build-manifest.json')['expected_candidate_sha256']
    tags = [read_swf(data)[1] for data in (original, baseline, candidate)]
    fonts = [{int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
              for tag in group if tag.code == 75} for group in tags]
    abc = abc_bytes(tags[0])
    inv = abc_inventory(abc)
    ops = {m: inv.instructions(_bodies(abc)[m][3], m) for m in (630, 3504, 3506, 3508)}
    entries = {row['id']: row for row in load_catalog(root / 'candidate/workspace.json')['entries']}
    return root, candidate, fonts, abc, inv, ops, entries


def test_original_remaining_objects_panel_metrics(remaining_layout):
    _, _, _, abc, _, ops, _ = remaining_layout
    by_at = {m: {at: (op, args) for at, op, args in rows} for m, rows in ops.items()}
    assert by_at[630][13682] == (0x25, [360])
    for at in (13541, 15070, 15771, 16457, 16914, 17434, 18370,
               18452, 18533, 18956, 19768, 20579, 20779):
        assert by_at[630][at] == (0x25, [320])
    assert method_metadata(abc)[3504][4] == [(10, 10), (6, 3), (12, 12), (2, 3)]
    reader = ABCReader(abc)
    reader.take(4)
    integers = [0] + [reader.encoded(32) for _ in range(reader.u30() - 1)]
    assert integers[6] == 12  # addTextfield's default font size
    for at, value in ((199, 20), (846, 253), (853, 9), (869, 8)):
        assert by_at[3504][at] == (0x24, [value])
    assert by_at[3506][82] == (0x24, [10])
    assert by_at[3508][97] == (0x24, [1])
    assert by_at[3508][105] == (0x93, [])
    assert by_at[3508][108] == (0xa2, [])
    assert by_at[3508][1001] == (0xa3, [])


def test_full_remaining_objects_phrases_outlines_and_viewport(remaining_layout):
    root, candidate, fonts, _, inv, ops, entries = remaining_layout
    assert fonts[0][255] == fonts[1][255]
    source = OutlineModel(fonts[0][255])
    models = (source, source, OutlineModel(fonts[2][6002]))
    covered = set()

    literals = {at: inv.strings[args[0]] for at, op, args in ops[630] if op == 0x2c}

    def text(at, model):
        value = literals[at]
        row = entries.get(f'abc.4175.630.{at}')
        if model == 2 and any(char.isalpha() for char in value):
            assert row is not None and row['translation'] is not None, at
        if model == 2 and row is not None and row['translation'] is not None:
            return row['translation']
        return value

    @lru_cache(maxsize=None)
    def rendered(model, number):
        result, tokens, segment = {}, [], []
        for at, op, args in ops[630]:
            if not 13470 <= at <= 20940:
                continue
            segment.append((at, op, args))
            if op == 0x2c:
                tokens.append(text(at, model))
            elif op == 0x46 and inv.multiname(args[0]) == 'format':
                tokens.append(number)
            elif op == 0x4f and args[0] in (3729, 3730):
                if args[0] == 3729:
                    count, size, extra = args[1], 12, 9
                    if count == 3:
                        assert segment[-2][1] in (0x26, 0x27)
                        extra = -3 if segment[-2][1] == 0x26 else 9
                    elif count == 4:
                        assert segment[-3][1] in (0x26, 0x27)
                        size = segment[-2][2][0]
                        extra = -3 if segment[-3][1] == 0x26 else 9
                    elif count == 5:
                        size = 13  # Orb title's filters follow its explicit size.
                    elif count == 6:
                        if at == 14684:
                            size, extra = 11, 9  # conditional color is last argument.
                        elif at == 14794:
                            size, extra = 12, -3  # compact iff rollback field follows.
                        else:
                            assert segment[-5][1] in (0x26, 0x27), at
                            size = segment[-4][2][0]
                            extra = -3 if segment[-5][1] == 0x26 else 9
                    result[at] = (''.join(tokens), size, extra)
                segment, tokens = [], []
        return result

    def panel(rows, model, width):
        y, bottom, fields, overlaps = 10., None, [], []
        for row in rows:
            if isinstance(row, int):
                y += row
                continue
            call, value, size, extra = row
            measured = models[model].field(value, size, width - 24)
            top, next_bottom = y + measured['top'], y + measured['bottom']
            if bottom is not None and top < bottom:
                overlaps.append({'call': call, 'overlap': bottom - top})
            fields.append({'call': call, 'text': value, 'size': size, 'y': y, **measured})
            bottom = next_bottom
            y += measured['height'] + extra
        return {'height': y + 8, 'fields': fields, 'overlaps': overlaps}

    evidence, regressions = [], []
    numbers = ('0', '1', '2', '21', '99', '100', '999.9', '1,000', '1.0000000 e12')
    for number in numbers:
        cases = {}
        for model in range(3):
            r = rendered(model, number)

            def rows(*calls):
                result = []
                for call in calls:
                    if call < 100:
                        result.append(call)
                    else:
                        covered.add(call)
                        result.append((call, *r[call]))
                return result

            def field(call, value):
                covered.add(call)
                return (call, value, *r[call][1:])

            def add(name, parts):
                width = 360 if name.startswith('Orb:') else 320
                cases.setdefault(name, []).append(panel(parts, model, width))

            for direction in range(8):
                add(f'SecretSwitch:{direction}', [field(13598, text(13595, model)), field(13618,
                    text(13609, model) + text(13548 + 3 * direction, model))])
            for normal, discounts, amps, heavy, rollback in product((False, True), repeat=5):
                # Original creation paths bound NORMAL to 0 or 3..9 and IRON
                # to 5..15. Exercise minimum/maximum totals and every count arm.
                for total in ((0, 3, 9) if normal else (0, 5, 15)):
                    states = {(total, 0, 0), (0, total, 0), (0, 0, total)}
                    if total:
                        states.update({(1, total - 1, 0), (total - 1, 0, 1),
                                       (0, 1, total - 1), (1, total - 2, 1),
                                       (total - 2, 1, 1), (1, 1, total - 2)})
                    for docked, lost, retrievable in sorted(states):
                        assert docked + lost + retrievable == total <= 15
                        parts = rows(13792 if normal else 13846, 5)
                        if discounts:
                            parts += rows(13938, 13955, 17)
                        if amps:
                            parts += rows(14015, 14064, 5)
                        if total:
                            if docked:
                                call = 14320 if normal else 14396
                                noun = (14247 if docked == 1 else 14255) if normal else (
                                    14372 if docked == 1 else 14380)
                                value = str(docked) + ' ' + text(noun, model)
                                value += (text(14260, model) + str(docked) + text(14309, model)
                                          if normal else text(14385, model))
                                parts += [field(call, value)]
                            if retrievable:
                                value = str(retrievable) + ' ' + text(
                                    14433 if retrievable == 1 else 14441, model) + text(14446, model)
                                parts += [field(14457, value)]
                            if lost:
                                value = str(lost) + ' ' + text(14530 if lost == 1 else 14538, model)
                                value += text(14543, model) + str(lost * (10 if normal else 2))
                                parts += [field(14604, value + text(14593, model))]
                            parts += rows(12, 14684)
                            if heavy:
                                parts += [(14794, r[14794][0], 12, -3 if rollback else 9)]
                                covered.add(14794)
                            if rollback:
                                parts += rows(14880)
                            parts += rows(5)
                        if normal:
                            parts += rows(14936, 5)
                        name = f'Orb:{normal}:{total}:{docked}:{lost}:{retrievable}'
                        name += f':{discounts}:{amps}:{heavy}:{rollback}'
                        add(name, parts + rows(14971))
            for armor, normal, talisman in product((False, True), repeat=3):
                tail = rows(5, 15541) if normal else []
                tail += rows(15627) if talisman else []
                add(f'MonsterNest:active:{armor}:{normal}:{talisman}',
                    rows(15176, 5) + rows(*( (15303, 15343) if armor else (15406,) )) + tail + rows(5, 15672))
                tail = rows(16227) if normal else []
                tail += rows(16313) if talisman else []
                add(f'Tomb:active:{armor}:{normal}:{talisman}', rows(15788, 5) +
                    rows(*( (15915, 15955, 15992) if armor else (16055, 16092) )) +
                    rows(5) + tail + rows(5, 16347))
            add('MonsterNest:destroyed', rows(15099, 5, 15133))
            add('MonsterNest:invulnerable', rows(15176, 5, 15222))
            add('Tomb:opened', rows(15788, 5, 15834))
            add('WizTower:hideout', rows(16489, 2, 16516, 5, 16553, 16570))
            add('WizTower:destroyed', rows(16602))
            add('WizTower:unlocked', rows(16620, 5, 16808))
            for single in (False, True):
                instruction = rows(16745) if single else [field(16720,
                    text(16693, model) + number + text(16709, model))]
                add(f'WizTower:locked:{single}', rows(16620, 5) + instruction + rows(16762, 16783))
            for kind, single, normal in product(range(6), (False, True), (False, True)):
                title = field(16955, text(16928 + 3 * kind, model))
                if kind == 2:
                    detail = rows(17049)
                else:
                    offsets = (17154, 17157, 17160, 17162, 17165, 17168) if single else (
                        17123, 17126, 17129, 17131, 17134, 17137)
                    detail = [field(17193, text(17064, model) + number + ' ' +
                                    text(offsets[kind], model) + text(17182, model))]
                add(f'WizLock:{kind}:{single}:{normal}', [title, 5] + detail +
                    (rows(5, 17328) if normal else []))
            add('TomeChamber:opened', rows(17451, 5, 17504))
            add('TomeChamber:endurance', rows(17451, 5, 17543))
            for kind, status, single, missing_freeze, missing_curse in product(
                    range(4), (None, 17590, 17603, 17628), (False, True), (False, True), (False, True)):
                # Requirements are independent TextFields, not a single phrase.
                parts = rows(17451, 5) + [field(17660, text(17656, model)),
                    field(17701, text(17671, model) + number)]
                if status is not None:
                    parts += [field(17726, text(status, model))]
                parts += [field(17820, text((17763 if single else 17793) + 3 * kind, model))]
                parts += rows(17859)
                if missing_freeze and status in (17590, 17628):
                    parts += rows(5, 17938)
                if missing_curse and status in (17590, 17603):
                    parts += rows(5, 18017)
                add(f'TomeChamber:{kind}:{status}:{single}:{missing_freeze}:{missing_curse}', parts)
            add('DeadWizard', rows(18387))
            add('BrokenOrbBase', rows(18469))
            add('SealedGoE', rows(18550, 5, 18584))
            for name, offset in (('SummoningNode', 0), ('EnsnaringNode', 812)):
                for ruined, full in product((False, True), repeat=2):
                    parts = rows((19007 if ruined else 19025) + offset, 5)
                    if ruined:
                        parts += rows(19093 + offset)
                    parts += rows(19141 + offset) if full else rows(19491 + offset,
                        (19703 if ruined else 19592) + offset)
                    add(f'{name}:{ruined}:{full}', parts)
            for name, delta in (('SummoningSocket', 0), ('EnsnaringSocket', 200)):
                add(f'{name}:ruined', rows(20628 + delta))
                add(f'{name}:active', rows(20647 + delta, 20664 + delta,
                    (20681 if not delta else 20678) + delta, 5,
                    (20715 if not delta else 20712) + delta))
        for name, panels in cases.items():
            assert len(panels) == 3, name
            scales = []
            previous_overlaps = {row['call']: row['overlap'] for row in panels[1]['overlaps']}
            for overlap in panels[2]['overlaps']:
                if overlap['overlap'] > previous_overlaps.get(overlap['call'], 0) + 1e-6:
                    regressions.append({'object': name, 'number': number, 'overlap': overlap})
            for zoom in (1, .75, .5):
                factor = (1 + zoom) / (2 * zoom)
                heights = [p['height'] * factor for p in panels]
                width = 360 if name.startswith('Orb:') else 320
                assert width * factor <= 1068
                for threshold in (544, 602):
                    if heights[1] <= threshold < heights[2]:
                        regressions.append({'object': name, 'number': number, 'zoom': zoom,
                                            'heights': heights, 'threshold': threshold})
                scales.append({'zoom': zoom, 'heights': heights,
                               'candidate_above_preferred': heights[2] > 544,
                               'candidate_above_viewport': heights[2] > 602})
            evidence.append({'object': name, 'number': number, 'models': panels, 'scales': scales})
    bound_calls = {at for at, op, args in ops[630] if 13470 <= at <= 20940
                   and op == 0x4f and args[0] == 3729}
    assert covered == bound_calls, (sorted(bound_calls - covered), sorted(covered - bound_calls))
    (root / 'remaining-objects-geometry.json').write_text(json.dumps({
        'candidate_sha256': sha(candidate), 'panels': evidence, 'regressions': regressions,
        'limitations': 'Static SWF glyph outlines and line boxes. Numeric samples are not gameplay bounds. '
        'Orb counts obey verified total bounds and conservation; other flags conservatively include unproven combinations. Existing baseline overflow is '
        'recorded, not rejected. AIR wrapping, autoSize, rasterization, effects, positioning and HUD '
        'clipping remain unverified. 544px preferred below-HUD height and 602px viewport are envelopes.'
    }, ensure_ascii=False) + '\n')
    assert not regressions, regressions[:10]
