"""Offline source, input dispatch and glyph geometry for time controls."""
import json
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCReader
from addressed_localization import check_layout
from install_patch import ORIGINAL_SHA256, sha
from localization_fonts import measure_text
from swf_geometry import parse_font, read_swf
from test_battle_time_controls import CALLS, EXPECTED_IDS, KEY_OFFSETS, PLAN
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


@pytest.fixture(scope='module')
def time_artifacts():
    if not os.environ.get('GEMCRAFT_PR24_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR24_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR24_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    entries = {r['id']:r for r in load_catalog(root / 'candidate/workspace.json')['entries']}
    return root, original, candidate, entries


def test_time_controls_complete_original_branches_consumers_and_inputs(time_artifacts):
    _, original, _, entries = time_artifacts
    abc = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(abc)
    bodies = _bodies(abc)
    ops = inv.instructions(bodies[630][3],630)
    selected = [(at,args[0]) for at,op,args in ops if op == 44 and 24966 <= at <= 25873]
    assert {f'abc.4175.630.{at}' for at,_ in selected} == EXPECTED_IDS
    assert {i for i,(at,op,args) in enumerate(ops) if 25142 <= at <= 25833 and op == 79 and args[0] == 3729} == CALLS
    for at,pool in selected:
        assert entries[f'abc.4175.630.{at}']['source'] == inv.strings[pool]
    pool = {pool for _,pool in selected}
    consumers = {(m,at) for m,body in bodies.items() for at,op,args in inv.instructions(body[3],m) if op == 44 and args[0] in pool}
    assert consumers == {(630,at) for at,_ in selected}
    for site in load_catalog(PLAN)['abc_font_bindings']:
        assert ops[site['instruction_index']][1:] == (site['expected_opcode'],site['expected_operands'])
        for guard in site['receiver_guards']:
            assert ops[guard['instruction_index']][1:] == (guard['opcode'],guard['operands'])
    # Every visible variant depends on the current multiplier, not gem state.
    for index,value in ((9838,0),(9900,1),(9919,0),(9988,3),(10007,0),(10067,9),(10086,0)):
        assert inv.multiname(ops[index][2][0]) == 'speedMultiplier'
        assert ops[index+1][1:] == (0x24,[value])
        assert ops[index+2][1] == 0x14
    keyboard = {at:(op,args) for at,op,args in inv.instructions(bodies[703][3],703)}
    for at,name,call,action in ((512,'Q',529,'switchSpeedByKeyboard'),(547,'SPACE',564,'freezeTimeByKeyboard')):
        assert inv.multiname(keyboard[at][1][0]) == name
        assert keyboard[call][0] == 0x4f
        assert inv.multiname(keyboard[call][1][0]) == action
    # Space and Q resume the stored pre-pause speed; the mouse speed buttons
    # set their own indicated speed. Preserve that existing distinction.
    for method in (752,753):
        actions = {at:(op,args) for at,op,args in inv.instructions(bodies[method][3],method)}
        assert actions[55] == (0x66,[407]) and actions[58] == (0x61,[351])
        assert inv.multiname(407) == 'speedMultiplierBeforeFreeze'
    # The first button advances exactly one core/vfx frame while paused.
    step = {at:(op,args) for at,op,args in inv.instructions(bodies[754][3],754)}
    assert step[76] == (0x24,[1]) and step[155] == (0x24,[0])
    assert step[78] == step[157] == (0x61,[351])
    assert step[88] == (0x4f,[177,0]) and step[103] == (0x4f,[177,1])
    assert inv.multiname(177) == 'doEnterFrame'
    for method,current,at,other in ((755,1,89,3),(757,3,76,1),(758,9,76,3)):
        actions = {at:(op,args) for at,op,args in inv.instructions(bodies[method][3],method)}
        assert actions[60] == (0x66,[351]) and actions[63] == (0x24,[current])
        assert actions[at] == (0x24,[other]) and actions[at+2] == (0x61,[351])
    for method,value in ((757,3),(758,9)):
        actions = {at:(op,args) for at,op,args in inv.instructions(bodies[method][3],method)}
        assert actions[105] == (0x24,[value]) and actions[107] == (0x61,[351])
    assert inv.multiname(ops[100][2][0]) == 'isFirstStageTutorialRunning'
    assert ops[106][1:] == (0x4f,[940,0]) and ops[108][1:] == (0x47,[])


def test_time_control_all_complete_panels_outline_spacing_and_scales(time_artifacts):
    root, original, candidate, entries = time_artifacts
    abc = abc_bytes(read_swf(original)[1])
    inv = abc_inventory(abc)
    bodies = _bodies(abc)
    # Default reset width is 280; field width subtracts 20, then reserve 4px
    # for the Flash field inset. These panels do not call reset(width).
    reader = ABCReader(abc)
    reader.take(4)
    ints = [0] + [reader.encoded(32) for _ in range(1,reader.count())]
    assert ints[7] == 280
    assert method_metadata(abc)[3506][4] == [(7,3),(3,6)]
    assert method_metadata(abc)[3504][4] == [(10,10),(6,3),(12,12),(2,3)]
    renderer = {at:(op,args) for at,op,args in inv.instructions(bodies[3504][3],3504)}
    assert renderer[199] == (0x24,[20]) and renderer[853] == (0x24,[9])
    assert renderer[869] == (0x24,[8])
    position = {at:(op,args) for at,op,args in inv.instructions(bodies[3507][3],3507)}
    assert position[295] == (0x24,[58])
    assert position[430] == (0x25,[612]) and position[438] == (0x24,[10])
    bitmap = {at:(op,args) for at,op,args in inv.instructions(bodies[3508][3],3508)}
    assert bitmap[97] == (0x24,[1]) and bitmap[105] == (0x93,[])
    assert bitmap[108] == (0xa2,[]) and bitmap[1001] == (0xa3,[])
    fonts = [{int.from_bytes(t.payload[:2],'little'):parse_font(t.payload) for t in read_swf(data)[1] if t.code == 75} for data in (original,candidate)]
    source_font, localized_font = fonts[0][255],fonts[1][6002]
    plan = load_catalog(PLAN)
    for key in EXPECTED_IDS:
        check_layout(localized_font,entries[key]['translation'],plan['layouts'][key])
    panels = ((25142,), (25160,25174), (25299,25313), (25350,25364),
              (25385,25399), (25524,25538), (25575,25589), (25610,25624),
              (25727,25741), (25778,25792), (25813,25827))
    evidence = []
    for offsets in panels:
        heights = []
        for translated,font in ((False,source_font),(True,localized_font)):
            y,previous_bottom = 10.,None
            for offset in offsets:
                size = 10 if offset in KEY_OFFSETS else 12
                entry = entries[f'abc.4175.630.{offset}']
                value = entry['translation' if translated else 'source']
                layout = check_layout(font,value,{'height':size*20,'max_width':5120,'wrap':True,'max_lines':3})
                lines = []
                for paragraph in value.split('\n'):
                    line = ''
                    for word in paragraph.split(' '):
                        joined = line + (' ' if line else '') + word
                        if measure_text(font,joined,size*20)['widths'][0] > 5120:
                            lines.append(line)
                            line = word
                        else:
                            line = joined
                    lines.append(line)
                assert len(lines) == layout['lines']
                measured = measure_text(font,'\n'.join(lines),size*20)
                assert max(measured['widths']) <= 5120
                bounds = [b for b in measured['bounds'] if b]
                baseline = y + 2 + font['ascent'] * size / 20480
                top = baseline + min(b[2] for b in bounds)/20
                bottom = baseline + max(b[3] for b in bounds)/20
                assert previous_bottom is None or previous_bottom <= top
                previous_bottom = bottom
                y += (len(lines)*measured['line_height']+80)/20 + 9
            heights.append(y+8)
        for zoom in (1,.75,.5):
            factor = (1+zoom)/(2*zoom)
            old,new = [height*factor for height in heights]
            # updatePosition keeps ingame panels between y58 and y602.
            assert new <= 602-58
            assert 280*factor <= 1088-20
            evidence.append({'offsets':offsets,'zoom':zoom,'original_height':old,'candidate_height':new,'available_height':544})
    (root/'time-geometry.json').write_text(json.dumps({'candidate_sha256':sha(candidate),'states':len(panels),'comparisons':evidence,'limitations':'Offline source-guarded outline and wrapping model; AIR rasterization and runtime hover placement untested.'},indent=2)+'\n')
