"""Contracts for all four first-battle tutorial steps."""
from translation_catalog import load_catalog

PLAN = 'translations/plans/pr24-battle-tutorial.json'
STEPS = ((87,104,121,138),(292,309,326),(480,497,514),(679,696,713))
CALLS = (38,45,52,59,125,132,139,205,212,219,291,298,305)
EXPECTED_IDS = {f'abc.4175.557.{at}' for step in STEPS for at in step}


def test_tutorial_complete_steps_and_font_lifetime():
    plan = load_catalog(PLAN)
    assert set(plan['entry_ids']) == EXPECTED_IDS
    assert plan['fonts'] == [255]
    assert not plan['unconfirmed'] and not plan['coverage_blockers']
    assert len(plan['abc_font_bindings']) == 26
    for call in CALLS:
        before = next(s for s in plan['abc_font_bindings'] if s['instruction_index'] == call)
        after = next(s for s in plan['abc_font_bindings'] if s['instruction_index'] == call+1)
        assert before['kind'] == 'panel_format_before'
        assert after['kind'] == 'panel_format_after'
        assert before['expected_opcode'] == 79 and before['expected_operands'] == [3729,4]
        assert before['receiver_hex'] == after['receiver_hex'] == 'd166c606'
        assert before['receiver_guards']
    assert all(row['max_width'] == 5920 and row['wrap'] for row in plan['layouts'].values())


def test_tutorial_complete_player_instructions():
    rows = {r['id']:r['manual'].get('translation') for r in load_catalog('translations/catalog.json')['entries']}
    phrases = [' '.join(rows[f'abc.4175.557.{at}'] for at in step) for step in STEPS]
    assert phrases == [
        'Нажмите на кнопку выбора типа первого самоцвета',
        'Нажмите на ряд инвентаря, чтобы создать самоцвет',
        'Перетащите самоцвет в башню, чтобы установить его',
        'Нажмите на стрелку, чтобы начать бой!']
