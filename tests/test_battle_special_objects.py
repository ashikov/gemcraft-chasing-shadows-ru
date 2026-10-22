"""Addressed battle object panel contract without private game resources."""

from translation_catalog import load_catalog


PLAN = 'translations/plans/pr28-battle-special-objects.json'
PANEL_OFFSETS = {
    5905, 5957, 5995, 6039, 6119, 6243, 6276, 6318,
    7515, 7567, 7605, 7665, 7789, 7822, 7864, 7882, 7942, 8066, 8099, 8141,
    8292, 8329, 8348, 8361, 8370, 8375,
    8480, 8517, 8531, 8576, 8609, 8651,
    8736, 8773, 8862, 8899, 8920, 9001, 9038, 9162, 9199, 9211,
    9358, 9392, 9421, 9455, 9484, 9518, 9564, 9598, 9627, 9661,
    9690, 9724, 9753, 9787, 10093, 10152, 10196, 10311, 10382,
    10403, 10460, 10584, 10617, 10659,
}
HOLDER_OFFSETS = set(range(149, 225, 3))
EXPECTED_IDS = ({f'abc.4175.630.{at}' for at in PANEL_OFFSETS}
                | {f'abc.4175.1427.{at}' for at in HOLDER_OFFSETS})


def test_special_object_plan_covers_panel_and_holder_names():
    plan = load_catalog(PLAN)
    assert len(PANEL_OFFSETS) == 66
    assert len(HOLDER_OFFSETS) == 26
    assert set(plan['entry_ids']) == EXPECTED_IDS
    assert plan['fonts'] == [255]
    assert not plan['unconfirmed'] and not plan['coverage_blockers']
    for key in EXPECTED_IDS:
        width = 256 if key.startswith('abc.4175.630.') and int(key.rsplit('.', 1)[1]) < 6339 else 296
        assert plan['layouts'][key]['max_width'] == width * 20
    for item in load_catalog('translations/build-manifest.json')['plans']:
        path = 'translations/' + item['path']
        if path != PLAN:
            assert EXPECTED_IDS.isdisjoint(load_catalog(path)['entry_ids'])


def test_special_object_panel_format_lifetime_and_translations():
    plan = load_catalog(PLAN)
    sites = plan['abc_font_bindings']
    assert len(sites) == 112
    assert len({(site['method_index'], site['instruction_index']) for site in sites}) == 112
    assert {site['method_index'] for site in sites} == {630}
    before = [site for site in sites if site['kind'] == 'panel_format_before']
    after = [site for site in sites if site['kind'] == 'panel_format_after']
    assert len(before) == len(after) == 56
    assert {site['instruction_index'] + 1 for site in before} == {
        site['instruction_index'] for site in after}
    for site in before:
        assert site['expected_opcode'] == 0x4f
        assert site['expected_operands'][0] == 3729
        assert site['receiver_hex'] == 'd166c606'
        assert site['original_font_string_index'] == 10038
    rows = {row['id']: row for row in load_catalog('translations/catalog.json')['entries']}
    empty_suffixes = {'abc.4175.630.8361', 'abc.4175.630.8375',
                      'abc.4175.630.9211'}
    for key in EXPECTED_IDS:
        translation = rows[key]['manual']['translation']
        if key in empty_suffixes:
            assert translation == ''
        else:
            assert any('А' <= c <= 'я' or c in 'Ёё' for c in translation)
