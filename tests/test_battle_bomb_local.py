"""Full bomb-button states against real glyphs; no AIR or installation writes."""
import json
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from addressed_localization import check_layout
from install_patch import ORIGINAL_SHA256, sha
from localization_fonts import measure_text
from swf_geometry import parse_font, read_swf
from test_battle_bomb import BOMB_CALLS, BOMB_IDS, PLAN
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


@pytest.fixture(scope='module')
def bomb_artifacts():
    if not os.environ.get('GEMCRAFT_PR22_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR22_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR22_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    entries = {e['id']: e for e in load_catalog(root / 'candidate/workspace.json')['entries']}
    return root, original, candidate, entries


def test_bomb_original_scope_numeric_and_button_guards(bomb_artifacts):
    _, original, _, entries = bomb_artifacts
    abc = abc_bytes(read_swf(original)[1])
    inventory = abc_inventory(abc)
    operations = inventory.instructions(_bodies(abc)[630][3], 630)
    selected = operations[8638:8709]
    visible = {f'abc.4175.630.{at}' for at, op, args in selected if op == 0x2c}
    assert visible == BOMB_IDS
    assert {i for i in range(8638, 8709) if operations[i][1] == 0x4f
            and operations[i][2][0] == 3729} == BOMB_CALLS
    for site in load_catalog(PLAN)['abc_font_bindings']:
        if site['method_index'] != 630:
            continue
        assert operations[site['instruction_index']][1:] == (
            site['expected_opcode'], site['expected_operands'])
        for guard in site['receiver_guards']:
            assert operations[guard['instruction_index']][1:] == (guard['opcode'], guard['operands'])
    assert inventory.multiname(operations[8642][2][0]) == 'arrIsSpellBtnVisible'
    assert operations[8643][1:] == (0x24, [12])
    assert operations[8651][1:] == (0x25, [450])
    assert inventory.multiname(operations[8670][2][0]) == 'showTutorialnfoForButtonsBuildings'
    assert inventory.multiname(operations[8683][2][0]) == 'gemBombPrototypeModeAmount'
    assert operations[8684][1:] == (0xa0, [])
    assert operations[8686][1:] == (0xa0, [])
    assert operations[8657][1:] == (0x27, [])
    assert operations[8658][1:] == (0x24, [14])
    assert operations[8687][1:] == (0x27, [])
    assert operations[8688][1:] == (0x24, [12])
    assert method_metadata(abc)[3504][4] == [(10, 10), (6, 3), (12, 12), (2, 3)]
    # All seven literals are unique pushstring consumers across this ABC.
    pool = {operations[i][2][0] for i in range(8638, 8709) if operations[i][1] == 0x2c}
    consumers = {(m, at) for m, body in _bodies(abc).items()
                 for at, op, args in inventory.instructions(body[3], m)
                 if op == 0x2c and args[0] in pool}
    assert consumers == {(630, int(key.rsplit('.', 1)[1])) for key in BOMB_IDS}
    assert all(entries[key]['source'] == inventory.strings[args[0]]
               for at, op, args in selected if op == 0x2c
               for key in [f'abc.4175.630.{at}'])


def test_bomb_complete_panels_outlines_neighbors_and_thresholds(bomb_artifacts):
    root, original, candidate, entries = bomb_artifacts
    fonts = [{int.from_bytes(t.payload[:2], 'little'): parse_font(t.payload)
              for t in read_swf(data)[1] if t.code == 75} for data in (original, candidate)]
    original_font, localized_font = fonts[0][255], fonts[1][6002]
    evidence = []
    for tutorial in (False, True):
        for count in ('1', '2', '5', '21', '1000'):
            heights = []
            for translated, font in ((False, original_font), (True, localized_font)):
                def text(offset):
                    return entries[f'abc.4175.630.{offset}']['translation' if translated else 'source']
                rows = [(text(22013), 14, 5)]
                if tutorial:
                    rows += [(text(22063), 12, 0),
                             (text(22077) + count + text(22088), 12, 0)]
                    rows += [(text(offset), 12, 0) for offset in (22110, 22124, 22138)]
                y, previous_bottom = 10., None
                for value, size, extra in rows:
                    layout = {'height': size * 20, 'max_width': 8520,
                              'wrap': True, 'max_lines': 40}
                    result = check_layout(font, value, layout)
                    lines = []
                    for paragraph in value.split('\n'):
                        line = ''
                        for word in paragraph.split(' '):
                            joined = line + (' ' if line else '') + word
                            if measure_text(font, joined, size * 20)['widths'][0] > 8520:
                                lines.append(line)
                                line = word
                            else:
                                line = joined
                        lines.append(line)
                    assert result['lines'] == len(lines)
                    measured = measure_text(font, '\n'.join(lines), size * 20)
                    assert max(measured['widths']) <= 8520
                    bounds = [b for b in measured['bounds'] if b]
                    if bounds:
                        baseline = y + 2 + font['ascent'] * size / 20480
                        top = baseline + min(b[2] for b in bounds) / 20
                        bottom = baseline + max(b[3] for b in bounds) / 20
                        assert previous_bottom is None or previous_bottom <= top
                        previous_bottom = bottom
                    y += (len(lines) * measured['line_height'] + 80) / 20 + 9 + extra
                heights.append(y + 8)
            for zoom in (1, .75, .5):
                factor = (1 + zoom) / (2 * zoom)
                for threshold in (544, 602):
                    old, new = [h * factor for h in heights]
                    assert not old <= threshold < new, (tutorial, count, zoom, old, new)
                    evidence.append({'tutorial': tutorial, 'count': count, 'zoom': zoom,
                                     'threshold': threshold, 'original': old, 'candidate': new})
    (root / 'bomb-geometry.json').write_text(json.dumps({
        'candidate_sha256': sha(candidate), 'comparisons': evidence,
        'limitations': 'Offline outline model, not AIR. Representative counts are not gameplay limits.'
    }, indent=2) + '\n')
