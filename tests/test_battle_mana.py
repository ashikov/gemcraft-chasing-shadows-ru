"""Resource-free contracts for the mana pool and live monster counters."""
from translation_catalog import load_catalog

PLAN = 'translations/plans/pr21-battle-waves-mana.json'
MANA_OFFSETS = {28538, 28588, 28617, 28639, 28677, 28713, 28772, 28849,
                28938, 29015, 29085, 29162, 29193, 29240, 29253, 29275}
COUNTER_OFFSETS = {28197, 28264}
MANA_IDS = {f'abc.4175.630.{offset}' for offset in MANA_OFFSETS | COUNTER_OFFSETS}


def test_mana_scope_and_translation_contract():
    plan = load_catalog(PLAN)
    catalog = {row['id']: row['manual'] for row in load_catalog('translations/catalog.json')['entries']}
    assert MANA_IDS <= set(plan['entry_ids'])
    assert len(plan['entry_ids']) == 80
    for identifier in ('abc.4175.760.280', 'abc.4175.761.144'):
        assert identifier in plan['entry_ids']
        assert catalog[identifier]['translation'] == 'Вызваны все оставшиеся волны'
    for identifier in MANA_IDS:
        row = catalog[identifier]
        assert row['translation'] and row['context_confidence'] == 'confirmed'
        assert row['classification'] == 'confirmed'
        assert identifier in plan['layouts']
    assert catalog['abc.4175.630.28538']['translation'] == 'Бассейн маны'
    assert catalog['abc.4175.630.28197']['translation'] == ' монстр. в атаке'
    assert catalog['abc.4175.630.28264']['translation'] == ' монстр. на подходе'
    assert catalog['abc.4175.630.28617']['translation'].endswith(' ')
    assert catalog['abc.4175.630.28639']['translation'] == ' маны'
    assert catalog['abc.4175.630.29193']['translation'] == ' маны'
    assert catalog['abc.4175.630.29275']['translation'] == '(Клавиша: X)'
    for offset in (28772, 28849, 28938, 29015, 29085):
        assert catalog[f'abc.4175.630.{offset}']['translation'].endswith(': x')
    bindings = [site for site in plan['abc_font_bindings'] if site['method_index'] == 630]
    before = {site['instruction_index'] for site in bindings if site['kind'] == 'panel_format_before'}
    after = {site['instruction_index'] for site in bindings if site['kind'] == 'panel_format_after'}
    assert len(before) == 14
    assert after == {index + 1 for index in before}
    assert all(site['receiver_hex'] == 'd166c606' for site in bindings)


def test_mana_label_uses_production_glossary():
    terms = {row['en']: row['ru'] for row in load_catalog(
        'translations/editorial/glossary.json')['terms']}
    catalog = {row['id']: row['manual'] for row in load_catalog(
        'translations/catalog.json')['entries']}
    assert catalog['abc.4175.630.28538']['translation'].lower() == terms['mana pool']
    assert terms['skill: Mana Stream'] == 'Поток маны'
    assert 'Потока маны' in catalog['abc.4175.630.28849']['translation']
