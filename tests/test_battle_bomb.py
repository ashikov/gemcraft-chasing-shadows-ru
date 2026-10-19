"""Contracts for the complete bomb-button tooltip adjacent to Sparks."""
from translation_catalog import load_catalog

PLAN = 'translations/plans/pr22-battle-sparks.json'
BOMB_OFFSETS = {22013, 22063, 22077, 22088, 22110, 22124, 22138}
BOMB_IDS = {f'abc.4175.630.{offset}' for offset in BOMB_OFFSETS}
BOMB_CALLS = {8659, 8676, 8691, 8696, 8701, 8706}


def test_bomb_tooltip_complete_scope_and_format_pairs():
    plan = load_catalog(PLAN)
    assert BOMB_IDS <= set(plan['entry_ids'])
    sites = [s for s in plan['abc_font_bindings'] if s['method_index'] == 630]
    assert len(sites) == 12
    for call in BOMB_CALLS:
        before = next(s for s in sites if s['instruction_index'] == call)
        after = next(s for s in sites if s['instruction_index'] == call + 1)
        assert before['kind'] == 'panel_format_before'
        assert after['kind'] == 'panel_format_after'
        assert before['expected_opcode'] == 79
        assert before['receiver_hex'] == after['receiver_hex'] == 'd166c606'
        assert before['receiver_guards']
    assert all(plan['layouts'][key]['max_width'] == 8520 for key in BOMB_IDS)


def test_bomb_full_dynamic_phrase_and_distinct_shift_sequences():
    catalog = {r['id']: r['manual'] for r in load_catalog('translations/catalog.json')['entries']}
    def text(offset):
        return catalog[f'abc.4175.630.{offset}']['translation']
    assert text(22063) == 'Клавиша: B'
    for count in ('1', '2', '5', '21', '1000'):
        complete = text(22077) + count + text(22088)
        assert complete == ('Число бомб на волну или искру\nв режиме прототипа (' + count
                            + '):\nклавиши Up/Down / колесо мыши')
    assert text(22124).startswith('Удерживая Shift, нажмите кнопку')
    assert 'пуст' in text(22124) and 'инвентар' in text(22124)
    assert text(22138).startswith('Либо нажмите кнопку, затем удерживайте Shift')
    assert 'ман' in text(22138) and 'копи' in text(22138)
