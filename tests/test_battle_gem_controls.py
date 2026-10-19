"""Contracts for the complete connected battle gem-control tooltips."""
from translation_catalog import load_catalog

PLAN = 'translations/plans/pr23-battle-gem-controls.json'
RANGES = ((1255, 1875), (1943, 2322), (22415, 22626), (23167, 24966))
OFFSETS = {1505,1522,1538,1600,1617,1633,2125,2142,2170,2187,
           22455,22505,22519,22533,22573,22588,22607,
           23211,23281,23309,23374,23444,23472,23537,23607,23635,
           23700,23770,23798,23863,23933,23961,24026,24096,24124,
           24189,24259,24287,24352,24422,24450,24515,24585,24613,24907,24921}
EXPECTED_IDS = {f'abc.4175.630.{offset}' for offset in OFFSETS}
CALLS = {662,681,699,716,915,924,932,941,8824,8841,8846,8851,8872,8883,
         9132,9146,9157,9195,9209,9220,9258,9272,9283,9321,9335,9346,
         9384,9398,9409,9447,9461,9472,9510,9524,9535,9573,9587,9598,
         9636,9650,9661,9748,9753}
TYPES = ((23211,23281,23309,6,'подавления'),
         (23374,23444,23472,8,'критического удара'),
         (23537,23607,23635,9,'связи с бассейном'),
         (23700,23770,23798,4,'цепного удара'),
         (23863,23933,23961,5,'ядовитый'),
         (24026,24096,24124,2,'замедляющий'),
         (24189,24259,24287,1,'кровной связи'),
         (24352,24422,24450,7,'поглощения маны'),
         (24515,24585,24613,3,'разрушения брони'))


def manual_texts():
    rows = {r['id']:r['manual'].get('translation') for r in load_catalog('translations/catalog.json')['entries']}
    return {offset:rows[f'abc.4175.630.{offset}'] for offset in OFFSETS}


def test_gem_controls_complete_scope_and_font_lifetime():
    plan = load_catalog(PLAN)
    assert set(plan['entry_ids']) == EXPECTED_IDS
    assert not plan['unconfirmed'] and not plan['coverage_blockers']
    assert plan['fonts'] == [255]
    assert len(plan['abc_font_bindings']) == 86
    for call in CALLS:
        before = next(s for s in plan['abc_font_bindings'] if s['instruction_index'] == call)
        after = next(s for s in plan['abc_font_bindings'] if s['instruction_index'] == call + 1)
        assert before['kind'] == 'panel_format_before'
        assert after['kind'] == 'panel_format_after'
        assert before['expected_opcode'] == 79
        assert before['expected_operands'][0] == 3729
        assert before['receiver_hex'] == after['receiver_hex'] == 'd166c606'
        assert before['receiver_guards']
    for offset in OFFSETS:
        layout = plan['layouts'][f'abc.4175.630.{offset}']
        assert layout['max_width'] == (5920 if offset < 2200 else 8520)
        assert layout['wrap']
    previous = load_catalog('translations/plans/pr22-battle-sparks.json')
    assert EXPECTED_IDS.isdisjoint(previous['entry_ids'])


def test_gem_controls_complete_dynamic_phrases_and_actions():
    text = manual_texts()
    for grade in (1,2,5,12):
        for price in ('0','1','21','1,000','1.000 e12','1.0000000 e12'):
            for prefix,cost in ((1522,1538),(1617,1633)):
                assert text[prefix] + str(grade) + text[cost] + price == (
                    f'Уровень самоцвета: {grade}\nЗатраты маны: {price}')
            for offset in (2142,2187):
                complete = text[offset] + price
                assert complete == f'Затраты маны: {price}'
    for price in ('0','1','21','1000'):
        assert text[22573] + price + text[22588] == f'Затраты маны: {price}\n\nНедостаточно маны'
        assert text[22607] + price == f'Затраты маны: {price}'
    assert 'создания' in text[1505] and 'создать' in text[1600]
    assert 'дублирования' in text[2125] and 'дублирования' in text[2170]
    assert text[22505] == 'Клавиша: G'
    assert 'Shift' in text[22533] and 'того же уровня' in text[22533]
    assert 'Shift' in text[24921] and 'Ctrl/Cmd' in text[24921]
    assert 'всех пустых ячейках' in text[24921]
    assert 'ряда инвентаря' in text[24907]
    for title,effect,key,numpad,term in TYPES:
        assert term in text[title]
        assert text[key] == f'Клавиша: Num {numpad}'
        assert text[effect]
    assert 'минуя щит' in text[23281]
    assert 'броню и щит' in text[23933]
    assert 'попаданий' in text[24259] and 'убийств' not in text[24259]
