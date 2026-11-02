"""Full Russian shrine phrases, with opaque numeric output boundary samples.

Actual bytecode order and formatter arguments are checked by the local companion.
These cases do not emulate NumberFormatter or assert negative gameplay values.
"""
import pytest

from shrine_phrase_model import COMPASS_DIRECTIONS, NUMBERS, PHRASES, catalog_literals, joined, phrase_values


@pytest.fixture(scope='module')
def literals():
    return catalog_literals()


@pytest.mark.parametrize('phrase,number', [
    (phrase, number) for phrase in PHRASES
    for number in (('0', '1', '2', '5', '21', '1000') if phrase.raw_number else NUMBERS)
], ids=lambda value: f'm{value.method}@{value.start}' if hasattr(value, 'method') else value)
def test_complete_shrine_phrase_boundaries(phrase, number, literals):
    expected = phrase.expected.format(**phrase_values(phrase, number))
    for singular in (False, True):
        actual = joined(phrase, literals, number, singular=singular)
        assert actual == expected
        assert '  ' not in actual
        assert actual == actual.strip()


def test_venom_observed_multiplier_and_technical_boundaries(literals):
    assert joined(PHRASES[0], literals, '1.00') == (
        'Применяет эффекты самоцвета и наносит 1.00x урона самоцвета всем врагам в радиусе, игнорируя броню')
    assert '1.00 x' not in joined(PHRASES[0], literals, '1.00')
    assert any(joined(p, literals, '100').startswith('Снимает 100%') for p in PHRASES)


@pytest.mark.parametrize('at,direction', COMPASS_DIRECTIONS.items())
def test_compass_word_boundary(literals, at, direction):
    actual = literals['abc.4175.630.13609'] + literals[f'abc.4175.630.{at}']
    assert actual == 'Указывает на ' + direction
