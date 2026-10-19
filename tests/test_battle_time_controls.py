"""Addressed contract for the complete, small battle time-control scope."""
from translation_catalog import load_catalog

PLAN = 'translations/plans/pr24-battle-time-controls.json'
OFFSETS = {25142,25160,25174,25299,25313,25350,25364,25385,25399,
           25524,25538,25575,25589,25610,25624,25727,25741,25778,
           25792,25813,25827}
EXPECTED_IDS = {f'abc.4175.630.{offset}' for offset in OFFSETS}
CALLS = {9845,9851,9858,9907,9914,9926,9933,9939,9946,9995,10002,
         10014,10021,10027,10034,10074,10081,10093,10100,10106,10113}
KEY_OFFSETS = {25174,25313,25364,25399,25538,25589,25624,25741,25792,25827}


def test_time_control_complete_scope_and_scoped_font_lifetime():
    plan = load_catalog(PLAN)
    assert set(plan['entry_ids']) == EXPECTED_IDS
    assert plan['fonts'] == [255]
    assert not plan['unconfirmed'] and not plan['coverage_blockers']
    assert len(plan['abc_font_bindings']) == 42
    for call in CALLS:
        before = next(s for s in plan['abc_font_bindings'] if s['instruction_index'] == call)
        after = next(s for s in plan['abc_font_bindings'] if s['instruction_index'] == call + 1)
        assert before['kind'] == 'panel_format_before'
        assert after['kind'] == 'panel_format_after'
        assert before['expected_opcode'] == 79
        assert before['expected_operands'][0] == 3729
        assert before['expected_operands'][1] in (2,4)
        assert before['receiver_hex'] == after['receiver_hex'] == 'd166c606'
        assert before['receiver_guards']
    for offset in OFFSETS:
        layout = plan['layouts'][f'abc.4175.630.{offset}']
        assert layout == {'font_id':255,'height':200 if offset in KEY_OFFSETS else 240,
                          'max_width':5120,'wrap':True,'max_lines':3}
    previous = load_catalog('translations/plans/pr23-battle-gem-controls.json')
    assert EXPECTED_IDS.isdisjoint(previous['entry_ids'])


def test_time_control_translations_keep_actions_speed_numbers_and_hotkeys():
    manual = {r['id']:r['manual'].get('translation') for r in load_catalog('translations/catalog.json')['entries']}
    text = {offset:manual[f'abc.4175.630.{offset}'] for offset in OFFSETS}
    assert text[25142] == 'Клик — продвинуть время на один кадр'
    assert 'приостановить' in text[25160]
    assert 'возобновить' in text[25350]
    for offset in (25174,25364,25589,25792):
        assert text[offset] == 'Клавиша: Пробел'
    for offset in KEY_OFFSETS - {25174,25364,25589,25792}:
        assert text[offset] == 'Клавиша: Q'
    for speed, offsets in ((1,(25385,25524)),(3,(25299,25610,25727,25575)),(9,(25778,25813))):
        for offset in offsets:
            assert f'{speed}x' in text[offset]
    assert 'возобновить' in text[25575] and 'возобновить' in text[25778]
