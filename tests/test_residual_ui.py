"""Addressed coverage contract for the remaining small battle HUD captions."""
import os

from translation_catalog import load_catalog

PLAN = os.environ.get('GEMCRAFT_PR33_RESIDUAL_PLAN',
                      'translations/plans/pr33-residual-ui.json')
PANEL_OFFSETS = {
    26052, 26066, 26268, 26305, 26387, 26469, 26551, 26633, 26715,
    26797, 26879, 26961, 27043, 27125, 27229, 27320, 27392, 27474,
    27556, 27662, 27721, 27780, 33714, 33767, 33798, 33839, 33856,
    33859, 33862, 33900, 33937, 34002,
}
KILL_OFFSETS = {519: (613, 621), 520: (258, 266), 521: (828, 836),
                522: (512, 520), 523: (727, 735), 539: (2134, 2142)}
EXPECTED_IDS = {*(f'abc.4175.630.{at}' for at in PANEL_OFFSETS),
                *(f'abc.4175.{method}.{at}' for method, offsets in KILL_OFFSETS.items()
                  for at in offsets)}


def test_residual_ui_scope_has_every_target_without_shared_keys_or_unrelated_ui():
    plan = load_catalog(PLAN)
    assert len(EXPECTED_IDS) == 44
    assert set(plan['entry_ids']) == EXPECTED_IDS
    assert set(plan['layouts']) == EXPECTED_IDS
    assert plan['fonts'] == [255]
    assert not plan['unconfirmed'] and not plan['coverage_blockers']
    assert not plan.get('empty_abc_strings')
    assert {site['method_index'] for site in plan['abc_font_bindings']} == {630, *KILL_OFFSETS}


def test_panel_formats_are_paired_and_kill_fields_are_font_only():
    sites = load_catalog(PLAN)['abc_font_bindings']
    panels = [site for site in sites if site['method_index'] == 630]
    before = {site['instruction_index']: site for site in panels
              if site['kind'] == 'panel_format_before'}
    after = {site['instruction_index']: site for site in panels
             if site['kind'] == 'panel_format_after'}
    assert len(before) == len(after) == 29
    for index, site in before.items():
        restore = after[index + 1]
        assert site['expected_opcode'] == 0x4f and site['expected_operands'][0] == 3729
        assert site['expected_operands'][1] in (2, 6)
        for row in (site, restore):
            assert row['receiver_hex'] == 'd166c606'
            assert row['original_font_family'] == 'Celtic Garamond for GemCraft'
            assert row['original_font_string_index'] == 10038
            assert row['receiver_guards'] and row['extra_stack'] >= 7
    fields = [site for site in sites if site['method_index'] in KILL_OFFSETS]
    assert len(fields) == 6
    assert {row['method_index'] for row in fields} == set(KILL_OFFSETS)
    for row in fields:
        assert row['kind'] == 'field'
        assert row['receiver_hex'] == 'd0668402669b1d66e31a'
        assert row['receiver_guards'] and row['extra_stack'] >= 5


def test_layouts_reserve_dynamic_values_and_retain_font_sizes():
    layouts = load_catalog(PLAN)['layouts']
    for method, offsets in KILL_OFFSETS.items():
        for at in offsets:
            assert layouts[f'abc.4175.{method}.{at}'] == {
                'font_id': 255, 'height': 200, 'max_width': 1000,
                'max_height': 588, 'max_lines': 2,
            }
    for at in PANEL_OFFSETS:
        row = layouts[f'abc.4175.630.{at}']
        assert row['max_width'] == (4720 if at <= 26066 else 5520 if at < 33714 else 6720)
        assert row['height'] == (240 if at in (26052, 26066, 26268, 33714, 33767,
                                              33798, 33839, 33856, 33859, 33862,
                                              33900, 34002) else 220 if at == 33937 else 200)
        assert row['max_lines'] == 2 and row['wrap']
        if at not in (26052, 26066, 26268):
            assert row['dynamic_suffix_budget'] >= 1600
