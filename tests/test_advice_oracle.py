"""Synthetic packed-advice recipe checks; no extracted game prose."""
import base64
import copy
import zlib

import pytest

from advice_oracle import (
    ADVICE_COUNT, MAX_DECODED_BYTES, decode_advices, encode_advices, split_advices,
)


def records():
    return [f'Совет {index:02}: сохраните порядок, число 200% и клавишу Shift.'
            for index in range(ADVICE_COUNT)]


def test_utf8_cr_order_and_deterministic_roundtrip():
    source = records()
    encoded = encode_advices(source)
    assert encoded == encode_advices(copy.deepcopy(source))
    assert decode_advices(encoded) == source
    decoded = zlib.decompress(base64.b64decode(encoded)).decode('utf-8')
    assert decoded == '\r'.join(source)
    assert decoded.count('\r') == 59
    assert not decoded.endswith('\r')
    assert encode_advices(decode_advices(encoded)) == encoded


@pytest.mark.parametrize('bad', ['', 'not base64!', 'eA==', ' eA==', 'eA==\n'])
def test_malformed_base64_and_zlib_are_rejected(bad):
    with pytest.raises(ValueError):
        decode_advices(bad)


def test_truncated_trailing_and_concatenated_streams_are_rejected():
    raw = base64.b64decode(encode_advices(records()))
    for bad in (raw[:-1], raw + b'x', raw + raw):
        with pytest.raises(ValueError):
            decode_advices(base64.b64encode(bad).decode())


def test_bounded_decompression_rejects_a_compression_bomb():
    raw = zlib.compress(b'x' * (MAX_DECODED_BYTES + 1))
    with pytest.raises(ValueError, match='limit'):
        decode_advices(base64.b64encode(raw).decode())


def test_invalid_utf8_and_noncanonical_base64_are_rejected():
    raw = base64.b64encode(zlib.compress(b'\xff')).decode()
    with pytest.raises(ValueError, match='UTF-8'):
        decode_advices(raw)
    encoded = encode_advices(records())
    with pytest.raises(ValueError):
        decode_advices(encoded + '=')


@pytest.mark.parametrize('change', [
    lambda r: r[:-1], lambda r: r + ['лишний'],
    lambda r: ['', *r[1:]], lambda r: [r[0] + '\rновый', *r[1:]],
    lambda r: [r[0] + '\n', *r[1:]], lambda r: [r[0] + '\t', *r[1:]],
    lambda r: [r[0] + '\0', *r[1:]], lambda r: [r[0] + '\x7f', *r[1:]],
])
def test_count_empty_records_and_control_characters_are_rejected(change):
    with pytest.raises(ValueError):
        encode_advices(change(records()))


def test_plaintext_split_preserves_spaces_and_does_not_normalize():
    source = records()
    source[0] = ' Начальный и конечный пробелы сохраняются. '
    assert split_advices('\r'.join(source)) == source
    assert decode_advices(encode_advices(source)) == source


def test_origin_adapter_fails_closed_for_wrong_address_and_source():
    from advice_oracle import ADVICE_ID, unpack_advice_entry, prepare_advice_replacement
    location = {'tag_path': [4175], 'tag_code': 82, 'method_index': 1441, 'code_offset': 39}
    wrong = {'id': ADVICE_ID, 'location': location, 'source': encode_advices(records())}
    with pytest.raises(ValueError, match='source SHA'):
        unpack_advice_entry(wrong)
    wrong['id'] = 'abc.4175.1441.40'
    with pytest.raises(ValueError, match='address'):
        unpack_advice_entry(wrong)
    wrong['id'] = ADVICE_ID
    with pytest.raises(ValueError, match='decoded source guard'):
        prepare_advice_replacement(wrong)


def test_plan_selects_only_the_payload_and_binds_the_field_before_its_text():
    from advice_oracle import ADVICE_ID
    from translation_catalog import load_catalog
    plan = load_catalog('translations/plans/pr34-advices.json')
    assert plan['entry_ids'] == [ADVICE_ID]
    assert set(plan['layouts']) == {ADVICE_ID}
    assert plan['fonts'] == [1]
    assert not plan['unconfirmed'] and not plan['coverage_blockers']
    assert not plan.get('empty_abc_strings')
    assert len(plan['abc_font_bindings']) == 1
    site = plan['abc_font_bindings'][0]
    assert site['method_index'] == 465 and site['instruction_index'] == 461
    assert site['kind'] == 'field' and site['extra_stack'] >= 5
    assert len(site['receiver_guards']) == 7


def test_decoded_entry_guard_rejects_missing_source_as_a_value_error():
    from advice_oracle import ADVICE_ID, DECODED_SOURCE_SHA256, prepare_advice_replacement
    entry = {'id': ADVICE_ID,
             'location': {'tag_path': [4175], 'tag_code': 82,
                          'method_index': 1441, 'code_offset': 39},
             'content_type': 'packed_advices', 'source_sha256': DECODED_SOURCE_SHA256}
    with pytest.raises(ValueError, match='decoded source guard'):
        prepare_advice_replacement(entry)
