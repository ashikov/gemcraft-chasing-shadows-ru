"""Versioned first group of bitmap notifications; no game resources."""
from translation_catalog import load_catalog

PLAN = 'translations/plans/pr30-bitmap-floating-texts.json'
OFFSETS = {597: {431, 498, 503, 527, 725, 792, 797, 821}, 713: {237}}
IDS = {f'abc.4175.{method}.{at}' for method, offsets in OFFSETS.items() for at in offsets}


def test_bitmap_plan_selects_first_group_and_preserves_technical_suffix_guards():
    plan = load_catalog(PLAN)
    assert set(plan['entry_ids']) == IDS
    assert plan['fonts'] == [255]
    assert set(plan['layouts']) == IDS
    assert plan['empty_abc_strings'] == ['abc.4175.597.522', 'abc.4175.597.816']
    assert not plan['coverage_blockers'] and not plan['unconfirmed']
    assert len(plan['abc_font_bindings']) == 1
    site = plan['abc_font_bindings'][0]
    assert (site['method_index'], site['instruction_index'], site['kind']) == (
        1732, 64, 'bitmap_cyrillic')
    assert site['receiver_hex'] == '6204'
    assert site['expected_opcode'] == 0x62 and site['expected_operands'] == [6]


def test_bitmap_full_countdown_phrases_keep_counts_and_neutral_russian_grammar():
    rows = {row['id']: row['manual'].get('translation')
            for row in load_catalog('translations/catalog.json')['entries']}
    for shift in (0, 294):
        for count in (0, 1, 2, 3, 4, 5, 6, 21):
            # Only 1..5 reach the renderer; the string form itself remains valid
            # at either boundary without changing the game's guards or numbers.
            result = (rows[f'abc.4175.597.{498 + shift}'] + str(count)
                      + rows[f'abc.4175.597.{503 + shift}']
                      + rows[f'abc.4175.597.{527 + shift}'])
            assert result == f'...Осталось волн: {count}...'
    assert rows['abc.4175.597.431'] == 'Уничтожьте замки башни мага!'
    assert rows['abc.4175.597.725'] == 'Зарядите все узлы!'
    assert rows['abc.4175.713.237'] == 'Не хватает маны'
