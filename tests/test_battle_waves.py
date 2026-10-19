"""Resource-free contracts for wave panels and shared monster attributes."""
from collections import Counter

from translation_catalog import load_catalog

PLAN = 'translations/plans/pr21-battle-waves-mana.json'
WAVE_OFFSETS = {
    683, 713, 801, 817, 824, 860, 875, 895, 906, 926, 941, 961, 972,
    1014, 1079, 1144, 1218, 1351, 1476, 1519, 1634, 2401, 2429, 2462,
    2495, 2552, 2587, 2629, 2649, 2657, 2662, 3104, 3125, 3313, 3327,
    3431, 3531, 3643, 3652, 3719, 3770, 3787, 3808, 3825, 3842,
}
ATTRIBUTE_OFFSETS = {50, 78, 104, 132, 154, 166, 194, 206, 225,
                     237, 251, 263, 277, 289, 303}
WAVE_IDS = {f'abc.4175.547.{offset}' for offset in WAVE_OFFSETS}
ATTRIBUTE_IDS = {f'abc.4175.1553.{offset}' for offset in ATTRIBUTE_OFFSETS}
EXPECTED_IDS = WAVE_IDS | ATTRIBUTE_IDS
WAVE_CALLS = {318, 424, 462, 488, 512, 574, 619, 644, 691, 710, 959,
              984, 1069, 1096, 1268, 1276, 1511, 1532, 1551, 1558,
              1566, 1573, 1580}
ATTRIBUTE_CALLS = {689, 708, 727, 746}


def wave_plan():
    return load_catalog(PLAN)


def test_wave_scope_and_guarded_font_pairs():
    plan = wave_plan()
    assert EXPECTED_IDS <= set(plan['entry_ids'])
    assert not any(key.startswith('abc.4175.547.') and int(key.rsplit('.', 1)[1]) > 3842
                   for key in plan['entry_ids'])
    sites = [row for row in plan['abc_font_bindings'] if row['method_index'] in (547, 549)]
    assert Counter(row['kind'] for row in sites) == {
        'panel_format_before': 27, 'panel_format_after': 27}
    for method, calls, receiver in ((547, WAVE_CALLS, 'd166c606'),
                                    (549, ATTRIBUTE_CALLS, 'd266c606')):
        selected = [row for row in sites if row['method_index'] == method]
        assert {row['instruction_index'] for row in selected
                if row['kind'] == 'panel_format_before'} == calls
        assert all(row['receiver_hex'] == receiver for row in selected)
        for before in (row for row in selected if row['kind'] == 'panel_format_before'):
            assert before['expected_opcode'] == 79
            assert before['expected_operands'][0] == 3729
            assert before['receiver_guards']
            assert any(row['instruction_index'] == before['instruction_index'] + 1
                       and row['kind'] == 'panel_format_after' for row in selected)
    assert all(plan['layouts'][key]['wrap'] for key in EXPECTED_IDS)


def test_wave_neutral_count_fragments_and_translation_contract():
    catalog = {row['id']: row['manual'] for row in load_catalog(
        'translations/catalog.json')['entries']}
    assert all(catalog[key]['translation'] for key in EXPECTED_IDS)
    assert all(catalog[key]['classification'] == catalog[key]['context_confidence'] == 'confirmed'
               for key in EXPECTED_IDS)

    def translated(offset):
        return catalog[f'abc.4175.547.{offset}']['translation']

    for number in ('1', '2', '5', '21'):
        # Count and type are separate labels, avoiding the English >1 split
        # choosing an incorrect Russian plural after 2/21/101 monsters.
        for singular, plural in ((941, 875), (961, 895), (972, 906)):
            assert translated(singular) == translated(plural)
            assert number + translated(860) + translated(801) + translated(plural) == (
                number + ' — меченый ' + translated(singular))
        assert translated(2649) == translated(2657) == 'самоцветов-бомб'
        complete = number + translated(2629) + translated(2649) + translated(2662)
        assert complete == number + ' — брошено самоцветов-бомб для разъярения волны'
        assert '+' + number + translated(2429) == '+' + number + ' к числу монстров\n'


def test_banishment_explicitly_labels_a_cost():
    catalog = {row['id']: row['manual'] for row in load_catalog(
        'translations/catalog.json')['entries']}
    for offset in (1476, 1634):
        assert catalog[f'abc.4175.547.{offset}']['translation'] == 'Стоимость изгнания в мане: '
