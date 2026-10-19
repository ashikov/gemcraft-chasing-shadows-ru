"""Resource-free contracts for the complete Spark hover branch."""
from collections import Counter

from translation_catalog import load_catalog

PLAN = 'translations/plans/pr22-battle-sparks.json'
SPARK_OFFSETS = {
    3946, 3955, 3964, 3994, 4022, 4049, 4058, 4097, 4124, 4133,
    4172, 4199, 4208, 4236, 4263, 4272, 4311, 4338, 4347, 4382,
    4410, 4438, 4467, 4625, 4653, 4710, 4767, 4817, 4874, 4931,
    4959, 4987, 5000, 5042, 5058, 5066, 5071, 5505, 5533, 5590,
    5647, 5697, 5750, 5803, 5831, 5859, 5886, 5907, 5974, 5991,
    6012, 6029, 6046,
}
EXPECTED_IDS = {f'abc.4175.547.{offset}' for offset in SPARK_OFFSETS}
SPARK_CALLS = {1810, 1817, 1826, 1843, 2019, 2028, 2053, 2358,
               2366, 2375, 2394, 2401, 2409, 2416, 2423}


def spark_plan():
    return load_catalog(PLAN)


def test_complete_spark_scope_and_font_pairs():
    plan = spark_plan()
    assert EXPECTED_IDS <= set(plan['entry_ids'])
    assert {key for key in plan['entry_ids'] if key.startswith('abc.4175.547.')} == EXPECTED_IDS
    assert len(EXPECTED_IDS) == 53
    sites = [row for row in plan['abc_font_bindings'] if row['method_index'] == 547]
    assert Counter(row['kind'] for row in sites) == {
        'panel_format_before': 15, 'panel_format_after': 15}
    assert {row['instruction_index'] for row in sites
            if row['kind'] == 'panel_format_before'} == SPARK_CALLS
    for row in sites:
        assert row['method_index'] == 547
        assert row['receiver_hex'] == 'd166c606'
        assert row['receiver_guards']
        if row['kind'] == 'panel_format_before':
            assert row['expected_opcode'] == 79
            assert row['expected_operands'][0] == 3729
            assert any(after['instruction_index'] == row['instruction_index'] + 1
                       and after['kind'] == 'panel_format_after' for after in sites)
    assert all(plan['layouts'][key]['wrap'] for key in EXPECTED_IDS)
    assert not plan.get('abc_numeric_replacements')
    assert not plan['coverage_blockers']


def test_spark_count_and_shared_literal_contracts():
    rows = {row['id']: row['manual'] for row in load_catalog(
        'translations/catalog.json')['entries']}
    assert all(rows[key]['classification'] == rows[key]['context_confidence'] == 'confirmed'
               and rows[key]['translation'] for key in EXPECTED_IDS)

    def text(offset):
        return rows[f'abc.4175.547.{offset}']['translation']

    for number in ('1', '2', '5', '21', '101'):
        for bomb in (5058, 5066):
            assert number + text(5042) + text(bomb) + text(5071) == (
                number + ' — число брошенных самоцветов-бомб для разогрева искры')
    for offsets in ((4022, 4653, 5533), (4311, 4874, 5750),
                    (4382, 4931, 5803), (4410, 4959, 5831),
                    (4438, 4987, 5859), (4625, 5505), (4710, 5590),
                    (4767, 5647), (4817, 5697)):
        assert len({text(offset) for offset in offsets}) == 1
    assert text(5991) == rows['abc.4175.547.3787']['translation']
    assert text(6029) == rows['abc.4175.547.3825']['translation']
    assert text(4236).count('105%') == 1
