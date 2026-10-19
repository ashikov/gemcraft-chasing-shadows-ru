"""Original ABC and complete outline states for battle gem controls; offline only."""
import json
import os
from functools import lru_cache
from itertools import product
from pathlib import Path

import pytest

from abc_font_binding import _bodies
from abc_patch import patch_pushstrings
from addressed_localization import check_layout
from install_patch import ORIGINAL_SHA256, sha
from localization_fonts import measure_text
from localization_plan import resolve_plan_font_bindings
from swf_geometry import parse_font, read_swf
from test_battle_gem_controls import CALLS, EXPECTED_IDS, PLAN, RANGES, TYPES
from test_battle_hud_local import preserve_method
from test_pr13_stats_local import method_metadata
from test_talisman_local import abc_bytes, abc_inventory
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


@pytest.fixture(scope='module')
def controls_artifacts():
    if not os.environ.get('GEMCRAFT_PR23_ROOT') or not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_PR23_ROOT and GEMCRAFT_ORIGINAL')
    root = Path(os.environ['GEMCRAFT_PR23_ROOT'])
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    candidate = (root / 'candidate/gc-cs-steam.ru.swf').read_bytes()
    entries = {r['id']:r for r in load_catalog(root / 'candidate/workspace.json')['entries']}
    return root, original, candidate, entries


def test_gem_controls_original_scope_consumers_and_logic(controls_artifacts):
    root, original, candidate, entries = controls_artifacts
    before_abc, after_abc = [abc_bytes(read_swf(data)[1]) for data in (original,candidate)]
    before, after = abc_inventory(before_abc), abc_inventory(after_abc)
    bodies = _bodies(before_abc)
    operations = before.instructions(bodies[630][3],630)
    selected = [(at,args[0]) for at,op,args in operations
                if op == 44 and any(lo <= at < hi for lo,hi in RANGES)]
    assert {f'abc.4175.630.{at}' for at,_ in selected} == EXPECTED_IDS
    assert len(selected) == 46
    assert all(any(c.isalpha() for c in before.strings[pool]) for _,pool in selected)
    assert {i for i,(at,op,args) in enumerate(operations)
            if op == 79 and args[0] == 3729 and any(lo <= at <= hi for lo,hi in (
                (1498,1650),(2118,2203),(22448,22625),(23204,24622),(24900,24927)))} == CALLS
    for at,pool in selected:
        assert entries[f'abc.4175.630.{at}']['source'] == before.strings[pool]
    for site in load_catalog(PLAN)['abc_font_bindings']:
        assert operations[site['instruction_index']][1:] == (site['expected_opcode'],site['expected_operands'])
        for guard in site['receiver_guards']:
            assert operations[guard['instruction_index']][1:] == (guard['opcode'],guard['operands'])
    # These original gates select creation, duplicate, combine and nine visible types.
    assert before.multiname(operations[568][2][0]) == 'CAST_CREATEGEM_INITIATED'
    assert before.multiname(operations[842][2][0]) == 'DRAGGING_GEM_FROM_SLOT_TO_COMBINE'
    assert before.multiname(operations[642][2][0]) == 'gemCreatingBaseManaCosts'
    assert before.multiname(operations[895][2][0]) == 'cost'
    assert before.multiname(operations[8855][2][0]) == 'gemCombiningManaCost'
    assert operations[655][1] == 0x0e and operations[908][1] == 0x0f
    assert operations[8860][1] == 0x0e
    # Combine price concatenates the raw .g() Number, unlike the formatted
    # creation/duplicate price. Commas and custom e12 forms do not occur here.
    assert operations[8868][1:] == operations[8881][1:] == (0x46,[863,0])
    assert operations[8869][1:] == operations[8882][1:] == (0xa0,[])
    assert not any(op == 0x46 and args[0] == 4755
                   for _,op,args in operations[8864:8884])
    type_gates = (9103,9166,9229,9292,9355,9418,9481,9548,9611)
    assert [before.multiname(operations[i][2][0]) for i in type_gates] == [
        'SUPPRESSING','CRITHIT','POOLBOUND','CHAIN_HIT','POISON','SLOWING',
        'BLOODBOUND','MANA_LEECHING','ARMOR_TEARING']
    tutorial_gates = (8835,9149,9212,9275,9338,9401,9464,9527,9590,9653,9742)
    assert all(before.multiname(operations[i][2][0]) == 'showTutorialnfoForButtonsBuildings'
               for i in tutorial_gates)
    # A hidden combine/type button never attaches its panel. Every type checks
    # its visibility slot before setting local8, and the common tail requires it.
    assert before.multiname(operations[8807][2][0]) == 'arrIsSpellBtnVisible'
    assert operations[8808][1:] == (0x24,[14])
    assert operations[8810][1:] == (0x12,[195])
    for index in type_gates:
        assert before.multiname(operations[index-2][2][0]) == 'arrIsSpellBtnVisible'
        assert operations[index+2][1] == 0x12
        assert operations[index+4][1:] == (0x26,[])
        assert operations[index+5][1:] == (0x63,[8])
    assert operations[9755][1:] == (0x62,[8])
    assert operations[9756][1:] == (0x12,[25])
    assert operations[9762][1:] == (0x4f,[5252,1])
    # An occupied combine-drag slot delegates to the existing detailed gem
    # panel; only the empty-slot path shows the new duplicate/cost prompt.
    assert before.multiname(operations[873][2][0]) == 'inventorySlots'
    assert operations[876][1:] == (0x20,[])
    assert operations[877][1:] == (0x13,[29])
    assert before.multiname(operations[887][2][0]) == 'renderInfoPanelGem'
    assert operations[887][1:] == (0x4f,[931,1])
    assert operations[8816][1:] == (0x25,[450])
    assert operations[574][1:] == operations[901][1:] == (0x25,[320])
    # Actual text sizes, extra spacing and renderer width are source-guarded.
    assert method_metadata(before_abc)[3504][4] == [(10,10),(6,3),(12,12),(2,3)]
    assert operations[661][1:] == operations[698][1:] == (0x24,[13])
    assert operations[680][1:] == (0x24,[11])
    assert operations[8823][1:] == (0x24,[14])
    assert operations[8827][1:] == (0x24,[5])
    for title,_,key,_,_ in TYPES:
        title_index = next(i for i,row in enumerate(operations) if row[0] == title)
        key_index = next(i for i,row in enumerate(operations) if row[0] == key)
        assert operations[title_index+2][1:] == (0x24,[13])
        assert operations[key_index+2][1:] == (0x24,[10])
        assert operations[title_index+19][1:] == (0x24,[5])
    renderer = before.instructions(bodies[3504][3],3504)
    assert renderer[77][1:] == (0x24,[20])
    assert before.multiname(renderer[79][2][0]) == 'width'
    # The conservative426/296 widths reserve an additional4px field inset.
    formatter = {at:(op,args) for at,op,args in before.instructions(bodies[1245][3],1245)}
    assert before.strings[formatter[724][1][0]] == ','
    assert before.strings[formatter[331][1][0]] == '.0000000 e'
    assert before.strings[formatter[389][1][0]] == '.000 e'
    assert formatter[148] == (0x24,[8]) and formatter[155] == (0x24,[4])
    assert before.multiname(formatter[141][1][0]) == 'lotsOfDigitsForExpNums'
    pool = {value for _,value in selected}
    consumers = {(m,at) for m,body in bodies.items()
                 for at,op,args in before.instructions(body[3],m) if op == 44 and args[0] in pool}
    selected_consumers = {(630,at) for at,_ in selected}
    external = consumers - selected_consumers
    # Common price/warning pool strings have pre-existing, independently localized
    # consumers. Addressed replacement leaves those consumers and pool bytes alone.
    assert external == {(630,21383),(630,21642),(630,21901),(630,22343)}
    merged = load_catalog(root / 'candidate/plan.json')
    inventory = {r['id']:r for r in load_catalog(root / 'candidate/inventory.json')['entries']}
    chosen = [entries[key] for key in merged['entry_ids'] if key.startswith('abc.')]
    chosen += [{**inventory[key], 'translation':''} for key in merged.get('empty_abc_strings',[])]
    translated = patch_pushstrings(before_abc,chosen)
    translated_inventory = abc_inventory(translated)
    make_snippets,increments,_ = resolve_plan_font_bindings(before_abc,translated,merged['abc_font_bindings'])
    snippets = make_snippets(len(translated_inventory.strings),len(translated_inventory.multinames))
    preserve_method(translated_inventory,after,630,snippets.get(630,[]),increments.get(630,0))
    assert after.strings[:len(before.strings)] == before.strings
    assert not [r for r in after.unsupported + after.control_flow_anomalies if r['method_index'] == 630]
    for method in (598,666,812,1245,1401,1451,1452,3503,3504):
        assert _bodies(after_abc)[method][2:] == bodies[method][2:]


def test_gem_controls_full_panels_outlines_neighbors_and_thresholds(controls_artifacts):
    root, original, candidate, entries = controls_artifacts
    fonts = [{int.from_bytes(t.payload[:2],'little'):parse_font(t.payload)
              for t in read_swf(data)[1] if t.code == 75 and int.from_bytes(t.payload[:2],'little') in (255,6002)}
             for data in (original,candidate)]
    original_font, localized_font = fonts[0][255],fonts[1][6002]
    plan = load_catalog(PLAN)
    for key in EXPECTED_IDS:
        check_layout(localized_font,entries[key]['translation'],plan['layouts'][key])

    def text(offset, translated):
        return entries[f'abc.4175.630.{offset}']['translation' if translated else 'source']

    @lru_cache(maxsize=None)
    def field(value,size,width,translated):
        font = localized_font if translated else original_font
        layout = check_layout(font,value,{'height':size*20,'max_width':width*20,'wrap':True,'max_lines':40})
        lines = []
        for paragraph in value.split('\n'):
            line = ''
            for word in paragraph.split(' '):
                joined = line + (' ' if line else '') + word
                if measure_text(font,joined,size*20)['widths'][0] > width*20:
                    lines.append(line)
                    line = word
                else:
                    line = joined
            lines.append(line)
        assert len(lines) == layout['lines']
        measured = measure_text(font,'\n'.join(lines),size*20)
        assert max(measured['widths']) <= width*20
        return measured,len(lines)

    def panel(rows,width,translated):
        font = localized_font if translated else original_font
        y,previous_bottom = 10.,None
        for value,size,extra in rows:
            measured,count = field(value,size,width,translated)
            bounds = [b for b in measured['bounds'] if b]
            if bounds:
                baseline = y + 2 + font['ascent'] * size / 20480
                top = baseline + min(b[2] for b in bounds)/20
                bottom = baseline + max(b[3] for b in bounds)/20
                assert previous_bottom is None or previous_bottom <= top
                previous_bottom = bottom
            y += (count*measured['line_height']+80)/20 + 9 + extra
        return y+8

    models = []
    for affordable,grade,price in product((False,True),(1,2,5,12),('0','1','21','1,000','1.000 e12','1.0000000 e12')):
        panels = []
        for translated in (False,True):
            title,prefix,cost,size = (1600,1617,1633,12) if affordable else (1505,1522,1538,11)
            rows = [(text(title,translated),13,0),
                    (text(prefix,translated)+str(grade)+text(cost,translated)+price,size,0)]
            panels.append(panel(rows,296,translated))
        models.append(({'kind':'create','affordable':affordable,'grade':grade,'price':price},panels))
    for affordable,price in product((False,True),('0','1','21','1,000','1.000 e12','1.0000000 e12')):
        panels = []
        for translated in (False,True):
            title,cost = (2125,2142) if affordable else (2170,2187)
            panels.append(panel([(text(title,translated),13,0),(text(cost,translated)+price,12,0)],296,translated))
        models.append(({'kind':'duplicate','affordable':affordable,'price':price},panels))
    for tutorial,affordable,price in product((False,True),(False,True),('0','1','21','1000')):
        panels = []
        for translated in (False,True):
            rows = [(text(22455,translated),14,5)]
            if tutorial:
                rows += [(text(at,translated),12,0) for at in (22505,22519,22533)]
            rows += [(text(22607 if affordable else 22573,translated)+price+
                      ('' if affordable else text(22588,translated)),12,0)]
            panels.append(panel(rows,426,translated))
        models.append(({'kind':'combine','tutorial':tutorial,'affordable':affordable,'price':price},panels))
    for tutorial,(title,effect,key,numpad,_) in product((False,True),TYPES):
        panels = []
        for translated in (False,True):
            rows = [(text(title,translated),13,5),(text(effect,translated),12,0)]
            if tutorial:
                rows += [(text(key,translated),10,0)]
                rows += [(text(at,translated),12,0) for at in (24907,24921)]
            panels.append(panel(rows,426,translated))
        models.append(({'kind':'type','numpad':numpad,'tutorial':tutorial},panels))
    evidence = []
    for state,heights in models:
        for zoom,threshold in product((1,.75,.5),(544,602)):
            factor = (1+zoom)/(2*zoom)
            old,new = [height*factor for height in heights]
            assert not old <= threshold < new,(state,zoom,old,new,threshold)
            evidence.append({**state,'zoom':zoom,'threshold':threshold,'original':old,'candidate':new})
    (root / 'controls-geometry.json').write_text(json.dumps({
        'candidate_sha256':sha(candidate),'models':len(models),'comparisons':evidence,
        'limitations':'Offline outline model, not AIR. Representative prices and grades are not gameplay limits. Invisible type buttons create no attached panel; tutorial off/on is modeled for every visible type.'
    },indent=2)+'\n')
