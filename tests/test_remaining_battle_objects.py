"""Remaining special-object localization contracts without private resources."""

from collections import Counter

from translation_catalog import load_catalog


PLAN = 'translations/plans/pr29-remaining-battle-objects.json'
PANEL_OFFSETS = {
    13548, 13551, 13554, 13557, 13560, 13563, 13566, 13569, 13595,
    13609, 13753, 13807, 13927, 13949, 14009, 14053, 14247, 14255,
    14260, 14309, 14372, 14380, 14385, 14433, 14441, 14446, 14530,
    14538, 14543, 14593, 14625, 14726, 14759, 14836, 14869, 14933,
    14967, 15096, 15130, 15173, 15219, 15255, 15314, 15358, 15459,
    15583, 15616, 15669, 15785, 15831, 15867, 15926, 15966, 16007,
    16066, 16145, 16269, 16302, 16344, 16485, 16510, 16547, 16564,
    16599, 16617, 16693, 16709, 16735, 16756, 16773, 16798, 16928,
    16931, 16934, 16937, 16940, 16943, 17003, 17038, 17064, 17123,
    17126, 17131, 17134, 17137, 17154, 17157, 17162, 17165, 17168,
    17182, 17246, 17448, 17494, 17533, 17590, 17603, 17628, 17656,
    17671, 17763, 17766, 17769, 17772, 17793, 17796, 17799, 17802,
    17831, 17928, 18007, 18384, 18466, 18547, 18581, 19004, 19022,
    19090, 19138, 19488, 19536, 19607, 19816, 19834, 19902, 19950,
    20300, 20348, 20419, 20625, 20643, 20658, 20675, 20712, 20825,
    20843, 20858, 20875, 20909,
}
EXPECTED_IDS = {f'abc.4175.630.{at}' for at in PANEL_OFFSETS}
TECHNICAL_OFFSETS = {
    13900, 14026, 14223, 14348, 14420, 14505, 15275, 15378, 15887, 16027,
    17094, 17129, 17160, 17558, 17641, 17710, 19581, 19692, 20393, 20504,
}


def test_remaining_object_plan_has_exact_scope_without_prior_conflicts():
    plan = load_catalog(PLAN)
    assert len(PANEL_OFFSETS) == 139
    assert len(plan['entry_ids']) == len(set(plan['entry_ids'])) == 139
    assert set(plan['entry_ids']) == EXPECTED_IDS
    assert plan['fonts'] == [255]
    assert not plan['unconfirmed'] and not plan['coverage_blockers']
    assert set(plan['layouts']) == EXPECTED_IDS
    for at, size in ((13548, 11), (13595, 12), (13753, 13), (14625, 11)):
        assert plan['layouts'][f'abc.4175.630.{at}']['height'] == size * 20
    for item in load_catalog('translations/build-manifest.json')['plans']:
        path = 'translations/' + item['path']
        if path != PLAN:
            assert EXPECTED_IDS.isdisjoint(load_catalog(path)['entry_ids'])


def test_remaining_object_formats_bind_before_measurement_and_restore():
    sites = load_catalog(PLAN)['abc_font_bindings']
    assert len(sites) == 180
    assert len({(site['method_index'], site['instruction_index']) for site in sites}) == 180
    assert {site['method_index'] for site in sites} == {630}
    before = [site for site in sites if site['kind'] == 'panel_format_before']
    after = [site for site in sites if site['kind'] == 'panel_format_after']
    assert len(before) == len(after) == 90
    assert {site['instruction_index'] + 1 for site in before} == {
        site['instruction_index'] for site in after}
    assert Counter(site['expected_operands'][1] for site in before) == {
        2: 33, 3: 7, 4: 10, 5: 2, 6: 38}
    for site in before:
        assert site['expected_opcode'] == 0x4f
        assert site['expected_operands'][0] == 3729
        assert site['receiver_hex'] == 'd166c606'
        assert site['original_font_string_index'] == 10038
        assert site['receiver_guards']


def test_remaining_object_count_labels_handle_russian_number_forms():
    rows = {row['id']: row['manual'].get('translation')
            for row in load_catalog('translations/catalog.json')['entries']}

    def translated(at):
        return rows[f'abc.4175.630.{at}']

    for singular, plural, suffix, label in (
            (14247, 14255, 14260, '— пристыковано малых сфер'),
            (14372, 14380, 14385, '— пристыковано малых сфер'),
            (14433, 14441, 14446, '— можно вернуть малых сфер'),
            (14530, 14538, 14543, '— потеряно малых сфер')):
        assert translated(singular) == translated(plural) == label
        assert translated(suffix) in ('', ': +', ': -')

    for count in (1, 2, 5, 11, 21, 22, 25):
        noun = 14247 if count == 1 else 14255
        docked = f'{count} {translated(noun)}{translated(14260)}{count}{translated(14309)}'
        assert docked == f'{count} — пристыковано малых сфер: +{count}% к получению маны'
        noun = 14530 if count == 1 else 14538
        for penalty in (count * 10, count * 2):
            lost = f'{count} {translated(noun)}{translated(14543)}{penalty}{translated(14593)}'
            assert lost == f'{count} — потеряно малых сфер: -{penalty}% к получению маны'
    assert 'H4' in translated(17928)
    assert 'G2' in translated(18007)
    assert translated(17671) == 'Осталось убить: '
    assert translated(17831) == 'В радиусе: '
    for at in (17763, 17766, 17769, 17772, 17793, 17796, 17799, 17802):
        assert translated(at).startswith('Цель: ')
    for at in (17590, 17603, 17628):
        assert translated(at).startswith('Состояние: ')
