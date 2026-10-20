"""Addressed contracts for all six battle-spell tooltip panels."""
from translation_catalog import load_catalog

PLAN = 'translations/plans/pr25-battle-spells.json'
RANGES = ((29559, 30022), (30182, 30754), (30888, 31358),
          (31621, 32136), (32287, 32811), (32939, 33479))
OFFSETS = {29559,29609,29646,29708,29750,29794,29816,29838,29880,29904,
           29926,29973,30016,30182,30232,30269,30331,30373,30395,30417,
           30461,30505,30527,30549,30571,30612,30636,30658,30705,30748,
           30888,30938,30975,31012,31052,31092,31132,31154,31175,31216,
           31240,31262,31309,31352,31621,31670,31707,31724,31775,31797,
           31818,31878,31895,31916,31957,31994,32018,32040,32087,32130,
           32287,32336,32373,32390,32441,32463,32484,32511,32553,32570,
           32591,32632,32669,32693,32715,32762,32805,32939,32988,33025,
           33042,33097,33119,33140,33161,33221,33238,33259,33300,33337,
           33361,33383,33430,33473}
UNCHANGED = {29683,29772,29951,29998,30306,30439,30483,30683,30730,31287,
           31334,31741,32065,32112,32407,32740,32787,33063,33408,33455}
CALLS = {11696,11703,11718,11745,11762,11780,11798,11825,11845,11863,
           11870,11955,11962,11977,12004,12021,12039,12057,12075,12093,
           12102,12129,12149,12167,12174,12248,12255,12270,12287,12303,
           12319,12335,12344,12353,12380,12400,12418,12425,12555,12562,
           12577,12584,12607,12616,12632,12647,12656,12665,12680,12707,
           12727,12745,12752,12831,12838,12853,12860,12883,12892,12913,
           12928,12937,12946,12961,12988,13008,13026,13033,13103,13110,
           13125,13134,13157,13166,13175,13191,13206,13215,13224,13239,
           13266,13286,13304,13311}
EXPECTED_IDS = {f'abc.4175.630.{offset}' for offset in OFFSETS}


def manual_texts():
    entries = {row['id']: row['manual'].get('translation')
               for row in load_catalog('translations/catalog.json')['entries']}
    return {offset: entries[f'abc.4175.630.{offset}'] for offset in OFFSETS}


def test_six_spells_complete_scope_and_font_lifetime():
    plan = load_catalog(PLAN)
    assert set(plan['entry_ids']) == EXPECTED_IDS
    assert plan['fonts'] == [255]
    assert not plan['unconfirmed'] and not plan['coverage_blockers']
    assert len(plan['abc_font_bindings']) == len(CALLS) * 2
    for call in CALLS:
        before = next(s for s in plan['abc_font_bindings']
                      if s['instruction_index'] == call)
        after = next(s for s in plan['abc_font_bindings']
                     if s['instruction_index'] == call + 1)
        assert before['kind'] == 'panel_format_before'
        assert after['kind'] == 'panel_format_after'
        assert before['method_index'] == after['method_index'] == 630
        assert before['expected_opcode'] == 79
        assert before['expected_operands'][0] == 3729
        assert before['receiver_hex'] == after['receiver_hex'] == 'd166c606'
        assert before['receiver_guards']
    for key in EXPECTED_IDS:
        layout = plan['layouts'][key]
        assert layout['font_id'] == 255 and layout['max_width'] == 7920
        assert layout['height'] in (200, 220, 240, 280)
        assert layout['wrap']
    previous = load_catalog('translations/build-manifest.json')
    paths = ['translations/' + item['path'] for item in previous['plans']
             if 'translations/' + item['path'] != PLAN]
    for path in paths:
        assert EXPECTED_IDS.isdisjoint(load_catalog(path)['entry_ids'])


def test_spell_names_hotkeys_and_complete_dynamic_phrases():
    text = manual_texts()
    assert [text[i] for i in (29559,30182,30888,31621,32287,32939)] == [
        'Заморозка','Проклятие','Пробуждение вечности','Магическая стрела','Луч','Шквал']
    for number, offset in enumerate((30016,30748,31352,32130,32805,33473), 1):
        assert text[offset] == f'Клавиша: {number}'
    for value in ('0','1','21','100','999.9','1,000','1.0000000 e12'):
        for prefix, suffix in ((29880,29904),(30612,30636),(31216,31240),
                               (31994,32018),(32669,32693),(33337,33361)):
            assert text[prefix] + value + text[suffix] == f'Время зарядки: {value} с'
        for current, maximum in ((29926,29973),(30658,30705),(31262,31309),
                                 (32040,32087),(32715,32762),(33383,33430)):
            assert text[current] + value + '%' == f'Текущий заряд: {value}%'
            assert text[maximum] + value + '%' == f'Максимальный заряд: {value}%'
        assert value + '-' + value + text[29708] == f'{value}-{value} с — длительность заморозки'
        assert value + '-' + value + text[30331] == f'{value}-{value} с — длительность проклятия'
        for offset in (29750,30373,31012):
            assert value + text[offset] == f'{value} кл. — радиус действия'
        for offset in (31775,33097):
            assert '+' + value + text[offset] == f'+{value}% к урону'
        assert '+' + value + text[32441] == f'+{value}% урона выстрела за 1 секунду'
        for offset in (31818,33161):
            assert text[offset] + value == f'Всего выстрелов: {value}'
        assert text[29816] + value + text[29838] == f'Урон взрыва трупа: {value}% от максимального здоровья'
        assert text[30395] + value + text[30417] == f'Выстрелы игнорируют {value}% уровня брони'
        assert text[30527] + value + text[30549] == f'Замедление от самоцветов длится на {value}% дольше'
        assert text[32484] + value + text[32511] == f'Общая длительность: {value} с'
    assert text[29646].count('\n') == 2
    assert 'случайному' in text[29646] and 'осколками льда' in text[29646]
    assert '75%' in text[30571] and 'не срабатывают' in text[30571]
    assert 'навсегда' in text[31092] and '5-ю' in text[31175]
    assert 'щит' in text[31154] and 'всех' in text[31154]
    for offset in (31916,32591,33259):
        assert text[offset] == 'В ловушках усиления не расходуются'
    for offset in (31957,32632,33300):
        assert text[offset] == 'Усиленные самоцветы вставляются мгновенно'
    assert '+50%' in text[31797] and '10' in text[32463]
    assert '+35%' in text[33119] and '3' in text[33140]
