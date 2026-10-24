"""PR32's exact bitmap call/fragment contract; no proprietary resources."""
from bitmap_notification_placement import placement_sites
from translation_catalog import load_catalog

PLAN = 'translations/plans/pr32-bitmap-completion.json'
CALLS = {
    242: {3807: (3760, 3773), 3907: (3856, 3869), 4260: (4226,)},
    243: {305: (279,), 397: (359, 371)},
    539: {2053: (1991, 2004)},
    566: {1841: (1793,), 2162: (2114,), 2495: (2447,), 2887: (2839,)},
    567: {1960: (1912,), 2270: (2222,), 2606: (2558,), 2988: (2940,)},
    615: {536: (488,)}, 663: {291: (243,)}, 664: {436: (388,)},
    665: {471: (423,), 1114: (1066,)}, 666: {421: (373,), 1091: (1043,)},
    669: {815: (767,)}, 670: {1041: (993,)},
    675: {1098: (1035, 1048), 1208: (1146, 1159)}, 676: {208: (160,)},
    695: {5996: (5948,), 6275: (6227,), 6408: (6360,), 6656: (6608,),
          7124: (7076,), 7757: (7709,), 8017: (7969,), 8726: (8678,),
          8986: (8938,), 9697: (9649,), 10165: (10117,)},
    1144: {138: (70, 89)}, 1596: {4688: (4661,), 4783: (4744, 4757)},
}
IDS = {f'abc.4175.{method}.{offset}' for method, calls in CALLS.items()
       for fragments in calls.values() for offset in fragments}
PROMOTED = {'abc.4175.1144.70', 'abc.4175.1144.89'}
NUMERIC_CALLS = {341: {391, 4533}, 954: {356}}
COUNTERS = {
    (242, 3807): 'Мана: +', (242, 3907): 'Опыт: +',
    (243, 397): 'Опыт: +', (539, 2053): 'Опыт: +',
    (675, 1098): 'Возврат маны: +', (675, 1208): 'Возврат маны: +',
    (1144, 138): 'Возврат теневых ядер: +', (1596, 4783): 'Опыт: +',
}


def translations():
    return {row['id']: row['manual']['translation']
            for row in load_catalog('translations/catalog.json')['entries'] if row['id'] in IDS}


def test_exact_forty_calls_and_all_fragments_in_one_cumulative_plan():
    plan = load_catalog(PLAN)
    assert sum(map(len, CALLS.values())) == 40
    assert len(IDS) == 48
    assert set(plan['entry_ids']) == set(plan['layouts']) == IDS
    assert plan['fonts'] == [255]
    assert plan['empty_abc_strings'] == plan['unconfirmed'] == plan['coverage_blockers'] == []
    assert [site['kind'] for site in plan['abc_font_bindings']] == [
        'kill_chain_prefix', *(['bitmap_placement'] * 17)]
    assert plan['abc_font_bindings'][0]['method_index'] == 243
    assert plan['abc_font_bindings'][1:] == placement_sites()
    assert all(layout == {'font_id': 255, 'height': 560,
                          'max_width': 41600, 'wrap': False}
               for layout in plan['layouts'].values())
    assert set(CALLS).isdisjoint(NUMERIC_CALLS)


def test_full_counter_phrases_preserve_return_semantics_and_all_number_forms():
    rows = translations()
    assert set(rows) == IDS
    numbers = ['0', '1', '2', '3', '4', '5', '11', '14', '20', '21', '22', '25',
               '30', '31', '99', '100', '101', '199', '200', '201', '1,000',
               '4,294,967,295', '999,999,999,999', '1.000 e12', '1.2345679 e12',
               '1.000 e308', '-1', '0.5', '1.5']
    for (method, call), prefix in COUNTERS.items():
        first, last = CALLS[method][call]
        for value in numbers:
            assert rows[f'abc.4175.{method}.{first}'] + value + rows[
                f'abc.4175.{method}.{last}'] == prefix + value
    assert rows['abc.4175.243.279'] == 'Убийств подряд: '
    assert rows['abc.4175.1596.4661'] == ' попад.'
    assert rows['abc.4175.242.4226'] == '+1 снос'


def test_static_errors_retain_the_specific_action_and_no_english_suffix():
    rows = translations()
    assert {rows[f'abc.4175.{method}.{at}'] for method in (566, 567)
            for fragments in CALLS[method].values() for at in fragments} == {'Перекрывает путь!'}
    assert rows['abc.4175.665.1066'] == 'Не хватает маны для выбранного числа бомб'
    assert rows['abc.4175.666.1043'] == 'Не хватает маны для выбранного числа бомб'
    for method in (615, 663, 664, 665, 666, 669, 670, 676):
        first = next(iter(CALLS[method].values()))[0]
        assert rows[f'abc.4175.{method}.{first}'] == 'Не хватает маны'
    for call, (at,) in CALLS[695].items():
        expected = 'Не хватает маны' if call in (5996, 6656, 7757, 8726, 9697) else 'Нельзя построить'
        assert rows[f'abc.4175.695.{at}'] == expected
