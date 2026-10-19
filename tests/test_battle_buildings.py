"""Private-resource-free contracts for the addressed building panels."""
from collections import Counter

from translation_catalog import load_catalog

PLAN = 'translations/plans/pr20-battle-buildings.json'
HOVER_OFFSETS = {
    4116, 4134, 4239, 4273, 4320, 4406, 4476, 5044, 5078, 5173, 5207,
    5261, 5295, 5353, 5613, 5699, 5717, 5783, 6477, 6511, 6555, 6703,
    6789, 6807, 6873, 7033, 7067, 7111, 7298, 7316, 7382,
}
BUTTON_OFFSETS = {
    21236, 21286, 21300, 21314, 21354, 21383, 21402,
    21495, 21545, 21559, 21573, 21613, 21642, 21661,
    21754, 21804, 21818, 21832, 21872, 21901, 21920,
    22196, 22246, 22260, 22274, 22314, 22343, 22362,
}
HEADINGS = {21236, 21495, 21754, 22196}
EXPECTED_IDS = {f'abc.4175.630.{offset}' for offset in HOVER_OFFSETS | BUTTON_OFFSETS}


def test_building_plan_scope_and_font_pair_contract():
    plan = load_catalog(PLAN)
    catalog = {row['id']: row for row in load_catalog('translations/catalog.json')['entries']}
    assert set(plan['entry_ids']) == EXPECTED_IDS
    assert len(plan['entry_ids']) == 59
    assert plan['fonts'] == [255]
    assert not plan.get('empty_abc_strings')
    assert set(plan['layouts']) == EXPECTED_IDS
    for identifier in EXPECTED_IDS:
        offset = int(identifier.rsplit('.', 1)[1])
        row = catalog[identifier]['manual']
        assert row['translation']
        assert row['classification'] == row['context_confidence'] == 'confirmed'
        layout = plan['layouts'][identifier]
        assert layout['font_id'] == 255 and layout['wrap']
        assert layout['height'] == (280 if offset in HEADINGS else 240)
        assert layout['max_width'] == (5120 if offset in HOVER_OFFSETS else 8520)
    sites = plan['abc_font_bindings']
    assert Counter(site['kind'] for site in sites) == {
        'panel_format_before': 55, 'panel_format_after': 55}
    assert {site['method_index'] for site in sites} == {630}
    for before in (site for site in sites if site['kind'] == 'panel_format_before'):
        following = [site for site in sites
                     if site['instruction_index'] == before['instruction_index'] + 1]
        assert len(following) == 1
        after = following[0]
        assert after['kind'] == 'panel_format_after'
        assert before['expected_opcode'] == 0x4f
        assert before['expected_operands'][0] == 3729
        assert before['receiver_hex'] == after['receiver_hex'] == 'd166c606'
        assert before['original_font_string_index'] == after['original_font_string_index'] == 10038
        assert before['extra_stack'] >= 7 and after['extra_stack'] >= 7
