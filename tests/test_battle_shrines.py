"""Addressed shrine plan contract, without private game resources."""
from translation_catalog import load_catalog

PLAN = 'translations/plans/pr27-battle-shrines.json'
OFFSETS = {
    630: {11097, 11168, 11239, 11310, 11381, 11452, 11523, 11594, 11665,
          11878, 11901, 11923, 11957, 11995, 12033, 12071, 12109, 12147,
          12185, 12223, 12261, 13036, 13058, 13098, 13143, 13188, 13233,
          13316, 13337, 13381, 13427},
    791: {163, 208, 230, 247, 285, 304, 346, 365, 407, 426, 468, 487,
          529, 548, 590, 609, 651, 670, 712, 729, 771, 790, 853},
    695: {1400},
    784: {505, 513, 550, 558, 622},
    786: {115, 235, 336, 422, 749, 1003, 1113},
}
CALLS = {
    630: {4440, 4467, 4494, 4521, 4548, 4575, 4602, 4629, 4656,
          4743, 4750, 4763, 4777, 4791, 4805, 4819, 4833, 4847, 4861,
          4875, 5186, 5211, 5228, 5248, 5265, 5287, 5295, 5312, 5330},
    791: {81, 98, 122, 147, 172, 197, 222, 247, 272, 296, 321, 345},
}
EXPECTED_IDS = {f'abc.4175.{method}.{offset}'
                for method, offsets in OFFSETS.items() for offset in offsets}


def test_shrine_plan_complete_addressed_scope_and_font_lifetime():
    plan = load_catalog(PLAN)
    assert set(plan['entry_ids']) == EXPECTED_IDS
    assert len(EXPECTED_IDS) == 67
    assert plan['fonts'] == [255]
    assert not plan['unconfirmed'] and not plan['coverage_blockers']
    sites = {(s['method_index'], s['instruction_index']): s
             for s in plan['abc_font_bindings']}
    calls = {(m, i) for m, indexes in CALLS.items() for i in indexes}
    assert set(sites) == calls | {(m, i + 1) for m, i in calls} | {
        (780, 58), (780, 184), (780, 341), (780, 462), (695, 575)}
    for method, call in calls:
        before, after = sites[method, call], sites[method, call + 1]
        assert before['kind'] == 'panel_format_before'
        assert after['kind'] == ('panel_format_after_shrine_spacing'
                                 if (method, call) == (630, 4743) else 'panel_format_after')
        assert before['expected_opcode'] == 79
        assert before['expected_operands'][0] == 3729
        assert before['receiver_hex'] == after['receiver_hex'] == (
            'd166c606' if method == 630 else 'd266c606')
        assert before['original_font_string_index'] == after['original_font_string_index'] == 10038
    for index in (58, 184, 341, 462):
        assert sites[780, index]['kind'] == 'field'
        assert sites[780, index]['receiver_guards'][-1]['opcode'] == 97
        assert sites[780, index]['receiver_guards'][-1]['operands'] == [5573]
    assert sites[695, 575]['kind'] == 'floater'
    assert sites[695, 575]['receiver_guards'][-1]['operands'] == [3841, 8]
    for item in load_catalog('translations/build-manifest.json')['plans']:
        path = 'translations/' + item['path']
        if path != PLAN:
            assert EXPECTED_IDS.isdisjoint(load_catalog(path)['entry_ids'])


def test_shrine_translations_prepared_and_plural_branches_neutral():
    rows = {r['id']: r for r in load_catalog('translations/catalog.json')['entries']}
    for key in EXPECTED_IDS:
        assert any('А' <= c <= 'я' or c in 'Ёё' for c in rows[key]['manual']['translation'])
    for first, second in ((505, 513), (550, 558)):
        assert rows[f'abc.4175.784.{first}']['manual']['translation'] == rows[
            f'abc.4175.784.{second}']['manual']['translation']
    assert rows['abc.4175.791.853']['manual']['translation'].count('\n') == 1
