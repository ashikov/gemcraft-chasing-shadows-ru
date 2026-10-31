"""Recognize only the reviewed achievement wave label, never technical w keys."""
import pytest

from abc_inventory import ABCInventory
from abc_patch import _u30


def wave_entry(*, offset=181, receiver=2923, value=1, tail=b'\xd3\xa0\x61\xd1\x29',
               owner='Achievement', method='instance initializer'):
    inventory = ABCInventory(b'', {'tag_path': [4175], 'tag_code': 82})
    inventory.strings = ['', 'w', 'text', 'mc', 'tfWaveReq', 'other']
    inventory.multinames = [None] + [{'kind': 7, 'name': 5} for _ in range(5329)]
    for index, name in ((105, 3), (2923, 4), (5329, 2)):
        inventory.multinames[index] = {'kind': 7, 'name': name}
    inventory.symbols = {1: {'multiname'}, 5: {'multiname'}}
    inventory.method_labels[139] = [{'owner': owner, 'method': method}]
    prefix = b'\xd0\x66' + _u30(105) + b'\x66' + _u30(receiver)
    code = b'\x02' * (offset - len(prefix)) + prefix + b'\x2c' + _u30(value) + tail + b'\x47'
    inventory.body_entries(code, 139, 0)
    return next(entry for entry in inventory.entries
                if entry['location']['code_offset'] == offset)


def test_reviewed_wave_prefix_is_a_display_translation_candidate():
    assert wave_entry()['classification'] == 'probable'


@pytest.mark.parametrize('change', [
    {'offset': 182}, {'receiver': 2922}, {'value': 5}, {'owner': 'Other'},
    {'method': 'other'}, {'tail': b'\xd2\xa0\x61\xd1\x29'},
    {'tail': b'\xd3\xa1\x61\xd1\x29'},
])
def test_other_symbol_uses_stay_technical(change):
    assert wave_entry(**change)['classification'] == 'technical'
