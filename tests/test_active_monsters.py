"""Addressed contracts for the six active-monster panels."""
from translation_catalog import load_catalog

PLAN = 'translations/plans/pr26-active-monsters.json'
METHODS = set(range(549, 555))
PR21_CALLS = {689, 708, 727, 746}
OFFSETS = {
    549: {2305, 1026, 2178, 652, 781, 2062, 271, 2325, 150, 1563, 2207, 1184, 1440, 418, 2080, 174, 943, 2354, 2227, 181, 568, 1338, 2618, 700, 2109, 447, 1473, 2374, 843, 205, 719, 2256, 2129, 341, 2517, 727, 1371, 2013, 350, 2403, 484, 2276, 998, 2158, 2031, 2550, 2423, 1530, 763},
    550: {517, 773, 392, 265, 146, 914, 542, 800, 417, 943, 307, 820, 1078, 442, 963, 324, 75, 847, 467, 213, 341, 600, 92, 990, 867, 358, 492, 109, 753, 1010, 246, 375, 633, 894},
    551: {514, 264, 778, 661, 534, 681, 563, 312, 710, 583, 72, 465, 213, 345, 89, 730, 612, 485, 230, 247, 632, 889, 126},
    552: {514, 264, 778, 661, 534, 681, 563, 312, 710, 583, 72, 465, 213, 345, 89, 730, 612, 485, 230, 247, 632, 889, 126},
    553: {193, 545, 643, 263, 72, 574, 427, 525, 398, 623, 496, 594, 246, 89, 378, 476, 126, 447},
    554: {640, 513, 263, 395, 660, 280, 542, 415, 562, 819, 444, 708, 72, 591, 464, 213, 89, 611, 493, 246, 126},
}
UNCHANGED = {
    549: {2592, 961, 914, 510, 127},
    550: {172},
    551: {152},
    552: {152},
    553: {152},
    554: {152},
}
CALLS = {
    549: {384, 900, 517, 268, 408, 152, 921, 1050, 294, 170, 942, 560, 689, 178, 1080, 315, 963, 708, 837, 1093, 211, 469, 599, 119, 727, 858, 984, 92, 359, 746, 237, 1005, 879, 631},
    550: {130, 137, 397, 144, 151, 158, 31, 417, 38, 168, 45, 178, 188, 316, 198, 78, 208, 336, 218, 91, 474, 356, 108, 377, 123, 253},
    551: {99, 356, 37, 70, 264, 137, 106, 201, 401, 306, 243, 85, 285, 92, 222, 30},
    552: {99, 356, 37, 70, 264, 137, 106, 201, 401, 306, 243, 85, 285, 92, 222, 30},
    553: {98, 37, 70, 232, 105, 169, 274, 83, 211, 190, 253, 30},
    554: {99, 37, 70, 198, 261, 106, 332, 240, 113, 177, 377, 282, 219, 92, 30},
}
EXPECTED_IDS = {f'abc.4175.{method}.{offset}'
                for method, offsets in OFFSETS.items() for offset in offsets}


def test_active_monsters_complete_scope_and_font_lifetime():
    plan = load_catalog(PLAN)
    assert set(plan['entry_ids']) == EXPECTED_IDS
    assert plan['fonts'] == [255]
    assert not plan['unconfirmed'] and not plan['coverage_blockers']
    sites = {(s['method_index'], s['instruction_index']): s
             for s in plan['abc_font_bindings']}
    expected_calls = {(method, call) for method, calls in CALLS.items()
                      for call in calls if method != 549 or call not in PR21_CALLS}
    assert set(sites) == expected_calls | {(m, i + 1) for m, i in expected_calls}
    for method, call in expected_calls:
        before, after = sites[method, call], sites[method, call + 1]
        assert before['kind'] == 'panel_format_before'
        assert after['kind'] == 'panel_format_after'
        assert before['expected_opcode'] == 79
        assert before['expected_operands'][0] == 3729
        assert before['receiver_hex'] == after['receiver_hex'] == 'd266c606'
        assert before['receiver_guards']
    for key in EXPECTED_IDS:
        layout = plan['layouts'][key]
        assert layout['font_id'] == 255
        assert layout['wrap']
    prior = load_catalog('translations/plans/pr21-battle-waves-mana.json')
    prior_sites = {(s['method_index'], s['instruction_index']): s
                   for s in prior['abc_font_bindings']}
    for call in PR21_CALLS:
        assert prior_sites[549, call]['kind'] == 'panel_format_before'
        assert prior_sites[549, call + 1]['kind'] == 'panel_format_after'
    manifest = load_catalog('translations/build-manifest.json')
    for item in manifest['plans']:
        path = 'translations/' + item['path']
        if path != PLAN:
            assert EXPECTED_IDS.isdisjoint(load_catalog(path)['entry_ids'])


def test_all_new_monster_translations_are_prepared_and_preserve_controls():
    rows = {r['id']: r for r in load_catalog('translations/catalog.json')['entries']}
    for key in EXPECTED_IDS:
        assert isinstance(rows[key]['manual']['translation'], str)
        assert rows[key]['manual']['translation']
    for offset in (719, 727, 781):
        assert rows[f'abc.4175.549.{offset}']['manual']['translation'].endswith('\n')
    for method, offsets in OFFSETS.items():
        for offset in offsets:
            value = rows[f'abc.4175.{method}.{offset}']['manual']['translation']
            if method == 549 and offset in (719, 727):
                assert value == '\n'
            else:
                assert any('А' <= c <= 'я' or c in 'Ёё' for c in value)
