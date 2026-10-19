"""Private-resource-free contracts for the scoped battle HUD plan."""
from collections import Counter

from translation_catalog import load_catalog


def test_battle_hud_scope_and_pre_measurement_font_pairs():
    plan = load_catalog('translations/plans/pr19-battle-hud.json')
    catalog = {row['id']: row for row in load_catalog('translations/catalog.json')['entries']}
    assert len(plan['entry_ids']) == len(set(plan['entry_ids'])) == 123
    assert Counter(int(i.split('.')[2]) for i in plan['entry_ids']) == {545: 8, 548: 114, 555: 1}
    assert plan['fonts'] == [255]
    assert not plan.get('empty_abc_strings')
    assert set(plan['layouts']) == set(plan['entry_ids'])
    for identifier in plan['entry_ids']:
        row = catalog[identifier]
        assert row['manual']['translation']
        assert row['manual']['classification'] == row['manual']['context_confidence'] == 'confirmed'
        layout = plan['layouts'][identifier]
        assert layout['font_id'] == 255 and layout['wrap']
        assert layout['max_width'] == (5120 if '.545.' in identifier or '.555.' in identifier else 6520)
    assert '\n' not in catalog['abc.4175.548.2868']['manual']['translation']
    sites = plan['abc_font_bindings']
    assert Counter(site['method_index'] for site in sites) == {548: 126, 555: 4}
    for before in (site for site in sites if site['kind'] == 'panel_format_before'):
        after = [site for site in sites if site['method_index'] == before['method_index']
                 and site['instruction_index'] == before['instruction_index'] + 1]
        assert len(after) == 1 and after[0]['kind'] == 'panel_format_after'
        assert before['expected_opcode'] == 0x4f
        assert before['expected_operands'][0] == 3729
        assert before['extra_stack'] >= 7 and after[0]['extra_stack'] >= 7
        assert before['receiver_hex'] == after[0]['receiver_hex']
        assert before['original_font_string_index'] == after[0]['original_font_string_index'] == 10038
