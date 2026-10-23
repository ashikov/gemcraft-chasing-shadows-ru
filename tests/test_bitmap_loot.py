"""Complete loot phrases and bounded plan selection, without game resources."""
from translation_catalog import load_catalog

PLAN = 'translations/plans/pr31-bitmap-loot.json'
OFFSETS = {204, 216, 336, 349, 438, 519, 622, 635, 648, 718,
           876, 959, 1074, 1125, 1208, 1291, 1374, 1457}
IDS = {f'abc.4175.623.{at}' for at in OFFSETS}
CALLS = (243, 376, 464, 545, 684, 744, 902, 985, 1100, 1151, 1234, 1317, 1400, 1483)


def translations():
    return {int(row['id'].rsplit('.', 1)[1]): row['manual']['translation']
            for row in load_catalog('translations/catalog.json')['entries'] if row['id'] in IDS}


def test_loot_plan_covers_exact_user_fragments_and_reuses_existing_selector():
    plan = load_catalog(PLAN)
    assert set(plan['entry_ids']) == set(plan['layouts']) == IDS
    assert plan['fonts'] == [255]
    assert plan['abc_font_bindings'] == []
    assert plan['empty_abc_strings'] == ['abc.4175.623.656']
    assert plan['unconfirmed'] == plan['coverage_blockers'] == []
    assert all(layout == {'font_id': 255, 'height': 480,
                          'max_width': 41600, 'wrap': False}
               for layout in plan['layouts'].values())


def test_complete_counter_phrases_have_no_number_dependent_noun_suffix():
    rows = translations()
    # Conservative core producer envelope and extra grammar probes. Actual
    # formatter spelling and expression branches are checked against private ABC.
    for n in [*range(1, 105), 0, .5, 1.5, 111, 1000, 1e12, 1e308]:
        assert rows[204] + str(n) + rows[216] == f'Опыт: +{n}'
        assert rows[336] + str(n) + rows[349] == f'Мана: +{n}'
        assert rows[622] + str(n) + rows[635] + (rows[648] if n > 1 else '') == (
            f'Теневые ядра: +{n}')


def test_all_static_loot_messages_and_both_scroll_types_are_full_phrases():
    rows = translations()
    assert {at: rows[at] for at in (438, 519, 718, 876, 959, 1074, 1125,
                                  1208, 1291, 1374, 1457)} == {
        438: 'Самоцвет', 519: 'Самоцвет', 718: 'Фрагмент талисмана',
        876: 'Жетон поля', 959: 'Гексагон карты', 1074: 'Самоцвет Вечности',
        1125: 'Фолиант навыка', 1208: 'Железная малая сфера',
        1291: 'Свиток боевой черты', 1374: 'Свиток сложности боя',
        1457: 'Страница журнала'}
