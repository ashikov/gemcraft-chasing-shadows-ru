"""Opt-in original ABC and four complete tutorial outline checks; offline only."""
import json
import os
import struct
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_inventory import ABCReader
from abc_patch import patch_pushstrings
from addressed_localization import check_layout
from install_patch import ORIGINAL_SHA256, sha
from localization_fonts import measure_text
from localization_plan import resolve_plan_font_bindings
from swf_geometry import parse_font, read_swf
from test_battle_hud_local import preserve_method
from test_battle_tutorial import CALLS, EXPECTED_IDS, PLAN, STEPS
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


@pytest.fixture(scope='module')
def tutorial_artifacts():
    if not os.environ.get('GEMCRAFT_PR24_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR24_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR24_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    candidate = (root/'candidate/gc-cs-steam.ru.swf').read_bytes()
    entries = {r['id']:r for r in load_catalog(root/'candidate/workspace.json')['entries']}
    return root, original, candidate, entries


def test_tutorial_original_scope_guards_and_control_flow(tutorial_artifacts):
    root, original, candidate, entries = tutorial_artifacts
    original_abc, candidate_abc = [abc_bytes(read_swf(data)[1]) for data in (original,candidate)]
    before, after = abc_inventory(original_abc),abc_inventory(candidate_abc)
    bodies = _bodies(original_abc)
    ops = before.instructions(bodies[557][3],557)
    literals = [(at,args[0]) for at,op,args in ops if op == 44]
    assert {f'abc.4175.557.{at}' for at,_ in literals} == EXPECTED_IDS
    assert tuple(i for i,(_,op,args) in enumerate(ops) if op == 79 and args[0] == 3729) == CALLS
    for at,pool in literals:
        assert entries[f'abc.4175.557.{at}']['source'] == before.strings[pool]
    for binding in load_catalog(PLAN)['abc_font_bindings']:
        assert ops[binding['instruction_index']][1:] == (binding['expected_opcode'],binding['expected_operands'])
        for guard in binding['receiver_guards']:
            assert ops[guard['instruction_index']][1:] == (guard['opcode'],guard['operands'])
    assert [before.multiname(ops[i][2][0]) for i in (12,19,25,112,192,278)] == [
        'stayAliveUntilExplicitDismiss','firstStageTutorIsWaitingForInput',
        *['firstStageTutorStep']*4]
    assert [ops[i][1:] for i in (26,113,193,279)] == [(36,[n]) for n in range(1,5)]
    assert [ops[i][1] for i in (27,114,194,280)] == [20]*4
    assert all(ops[i][1:] == (37,[320]) for i in (30,117,197,283))
    assert all(ops[i][1:] == (79,[5252,1]) for i in (107,187,273,337))
    assert before.multiname(838) == 'tFormat'
    renderer = before.instructions(bodies[3504][3],3504)
    assert renderer[54][1:] == (102,[838])
    assert renderer[55][1:] == (97,[5573])  # defaultTextFormat before text/height
    assert renderer[77][1:] == (36,[20])
    assert renderer[79][1:] == (97,[5359])  # reset320 minus20 =300px
    assert renderer[351][1:] == (36,[253])  # pushbyte -3 for adjacent fields
    assert renderer[354][1:] == (36,[9])
    constructor = before.instructions(bodies[3502][3],3502)
    assert constructor[37][1:] == (38,[])
    assert constructor[38][1:] == (97,[5568])  # all panel fields bold
    bitmap = before.instructions(bodies[3508][3],3508)
    reader = ABCReader(original_abc)
    reader.take(4)
    for _ in range(2):
        for _ in range(1,reader.count()):
            reader.encoded(32)
    doubles = [None]+[struct.unpack('<d',reader.take(8))[0] for _ in range(1,reader.count())]
    assert doubles[9] == .5
    assert bitmap[29][1:] == (147,[]) and bitmap[30][1:] == (47,[9])
    assert bitmap[97][1:] == bitmap[104][1:] == (162,[])
    viewport = {at:(op,args) for at,op,args in before.instructions(bodies[3540][3],3540)}
    assert viewport[46] == (37,[1088]) and viewport[54] == (37,[612])
    assert bitmap[443][1:] == (163,[])  # inverse zoom after bitmap rendering
    pool = {p for _,p in literals}
    consumers = {(m,at) for m,body in bodies.items()
                 for at,op,args in before.instructions(body[3],m) if op == 44 and args[0] in pool}
    (root/'tutorial-pool-consumers.json').write_text(json.dumps(sorted(consumers),indent=2)+'\n')
    assert consumers == {(557,at) for at,_ in literals} | {(220,191)}
    merged = load_catalog(root/'candidate/plan.json')
    inventory = {r['id']:r for r in load_catalog(root/'candidate/inventory.json')['entries']}
    chosen = [entries[key] for key in merged['entry_ids'] if key.startswith('abc.')]
    chosen += [{**inventory[key],'translation':''} for key in merged.get('empty_abc_strings',[])]
    translated = patch_pushstrings(original_abc,chosen)
    translated_inventory = abc_inventory(translated)
    make_snippets,increments,_ = resolve_plan_font_bindings(original_abc,translated,merged['abc_font_bindings'])
    snippets = make_snippets(len(translated_inventory.strings),len(translated_inventory.multinames))
    preserve_method(translated_inventory,after,557,snippets.get(557,[]),increments.get(557,0))
    assert after.strings[:len(before.strings)] == before.strings
    assert not [r for r in after.unsupported+after.control_flow_anomalies if r['method_index'] == 557]
    timer = {at:(op,args) for at,op,args in before.instructions(bodies[240][3],240)}
    assert timer[275] == (37,[135])
    assert [timer[at] for at in (294,392,410,495,513,598,616)] == [
        (36,[1]),(36,[15]),(36,[2]),(36,[15]),(36,[3]),(36,[30]),(36,[4])]
    assert [timer[at] for at in (296,412,515,618)] == [(97,[495])]*4
    assert timer[756] == (97,[500]) and timer[768] == (79,[3730,0])
    click = {at:(op,args) for at,op,args in before.instructions(bodies[705][3],705)}
    drop = {at:(op,args) for at,op,args in before.instructions(bodies[708][3],708)}
    assert click[178] == (37,[1010]) and click[204] == (37,[527])
    assert click[613] == (36,[33]) and click[637] == (36,[15])
    assert click[732] == (79,[1347,1]) and before.multiname(1347) == 'speedPressedNormal'
    assert drop[154] == (37,[464]) and drop[177] == (37,[478])
    assert drop[214] == (102,[356]) and before.multiname(356) == 'draggedGem'
    for method in (240,589,705,708,3502,3503,3504,3506,3508):
        assert _bodies(candidate_abc)[method][2:] == bodies[method][2:]


def test_tutorial_full_outline_panels_and_real_locked_positions(tutorial_artifacts):
    root, original, candidate, entries = tutorial_artifacts
    fonts = [{int.from_bytes(t.payload[:2],'little'):parse_font(t.payload)
              for t in read_swf(data)[1] if t.code == 75 and int.from_bytes(t.payload[:2],'little') in (255,6002)}
             for data in (original,candidate)]
    plan = load_catalog(PLAN)
    evidence = []
    for step,offsets in enumerate(STEPS,1):
        heights = []
        for translated,font in ((False,fonts[0][255]),(True,fonts[1][6002])):
            y, previous_bottom = 10.,None
            for at in offsets:
                identifier = f'abc.4175.557.{at}'
                value = entries[identifier]['translation' if translated else 'source']
                size = plan['layouts'][identifier]['height']
                layout = check_layout(font,value,plan['layouts'][identifier])
                # Every actual field fits one line: no speculative wrapping model.
                assert layout['lines'] == 1
                measured = measure_text(font,value,size)
                assert max(measured['widths']) <= 5920
                bounds = [b for b in measured['bounds'] if b]
                baseline = y+2+font['ascent']*size/409600
                top = baseline+min(b[2] for b in bounds)/20
                bottom = baseline+max(b[3] for b in bounds)/20
                assert previous_bottom is None or previous_bottom <= top
                previous_bottom = bottom
                y += (measured['line_height']+80)/20 + (-3 if at != offsets[-1] else 9)
            heights.append(y+8)
        for zoom in (1,.75,.5):
            # m3508 rasterizes at(1+zoom)/2, then applies inverse zoom.
            factor = (1+zoom)/(2*zoom)
            old,new = [height*factor for height in heights]
            # With the same anchors/width, a shorter panel stays inside the
            # original rectangle, so localization adds no panel overlap.
            assert new <= old
            width = 320*factor
            if step == 1:
                left,top = 990-width,(533-new/2)
            elif step == 2:
                left,top = 959-width,(461-new/2)
            elif step == 3:
                left,top = 719-width/2,551-new
            else:
                left,top = 73,25
            assert left >= 0 and top >= 0
            #990 is the step1 right anchor, not an assumed stage width.
            assert left+width <= 990
            assert top+new <= 612
            evidence.append({'step':step,'zoom':zoom,'original_height':old,'localized_height':new,
                             'bounds':[left,top,left+width,top+new]})
    (root/'tutorial-geometry.json').write_text(json.dumps({
        'candidate_sha256':sha(candidate),'states':evidence,
        'limitations':'Offline glyph contours and line boxes, no AIR/glow/pointer hit testing. m557-specific locked positions; no floating-panel thresholds copied.'},indent=2)+'\n')
