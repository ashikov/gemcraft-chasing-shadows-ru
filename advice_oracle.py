"""Version-bound UTF-8/zlib/base64 recipe for AdviceOracle's addressed payload.

The readable source exists only in a private restored inventory. The canonical
catalog owns the Russian CR-separated text; no extracted advice array is stored
in the repository. Selection, shuffling, settings and gameplay are untouched.
"""
import base64
import binascii
import copy
import hashlib
import zlib

ADVICE_ID = 'abc.4175.1441.39'
ADVICE_COUNT = 60
PACKED_SOURCE_SHA256 = '30d8cd9634d5da4673c7d0504bd9890cdd0015c40a636129520c5f9e7e88428e'
DECODED_SOURCE_SHA256 = '7c07a1e2b1118aeaecc5ab64c400e050881d0a25a3748b8467b387e2b6cefb26'
MAX_DECODED_BYTES = 65536
MAX_PACKED_BYTES = 90000


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def split_advices(text):
    """Validate the exact 60-record CR format without normalizing its content."""
    if not isinstance(text, str):
        raise ValueError('Advice text must be a UTF-8 string')
    try:
        raw = text.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError('Invalid advice UTF-8') from error
    if len(raw) > MAX_DECODED_BYTES:
        raise ValueError('Advice decoded size limit exceeded')
    if any((ord(char) < 32 and char != '\r') or ord(char) == 127 for char in text):
        raise ValueError('Advice record contains an unsupported control character')
    records = text.split('\r')
    if len(records) != ADVICE_COUNT or any(not record for record in records):
        raise ValueError('Advice payload must contain exactly 60 nonempty records')
    return records


def decode_advices(packed):
    """Decode a single bounded, canonical base64/zlib stream into ordered rows."""
    if not isinstance(packed, str) or not packed or len(packed) > MAX_PACKED_BYTES:
        raise ValueError('Invalid advice packed size')
    try:
        compressed = base64.b64decode(packed.encode('ascii'), validate=True)
    except (ValueError, UnicodeEncodeError, binascii.Error) as error:
        raise ValueError('Invalid advice base64') from error
    if base64.b64encode(compressed).decode('ascii') != packed:
        raise ValueError('Noncanonical advice base64')
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(compressed, MAX_DECODED_BYTES + 1)
    except zlib.error as error:
        raise ValueError('Invalid advice zlib stream') from error
    if len(raw) > MAX_DECODED_BYTES or decoder.unconsumed_tail:
        raise ValueError('Advice decoded size limit exceeded')
    if not decoder.eof or decoder.unused_data:
        raise ValueError('Truncated or trailing advice zlib data')
    try:
        return split_advices(raw.decode('utf-8'))
    except UnicodeDecodeError as error:
        raise ValueError('Invalid advice UTF-8') from error


def encode_advices(records):
    """Pack the original order using an explicit deterministic zlib recipe."""
    if (not isinstance(records, (list, tuple)) or len(records) != ADVICE_COUNT
            or any(not isinstance(record, str) or '\r' in record for record in records)):
        raise ValueError('Advice encoding needs exactly 60 separate string records')
    text = '\r'.join(records)
    split_advices(text)
    compressor = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=15,
                                  memLevel=8, strategy=zlib.Z_DEFAULT_STRATEGY)
    compressed = compressor.compress(text.encode('utf-8')) + compressor.flush()
    return base64.b64encode(compressed).decode('ascii')


def _guard_address(entry):
    location = entry.get('location', {})
    if (entry.get('id') != ADVICE_ID or location.get('tag_path') != [4175]
            or location.get('tag_code') != 82 or location.get('method_index') != 1441
            or location.get('code_offset') != 39):
        raise ValueError('Unexpected AdviceOracle payload address')


def unpack_advice_entry(entry):
    """Expose one hash-guarded packed literal as readable canonical catalog text.

    Call only for ADVICE_ID during original inventory extraction, after its ABC
    location has been established. Other strings and raw pool entries stay intact.
    """
    _guard_address(entry)
    packed = entry.get('source')
    if not isinstance(packed, str) or _sha(packed) != PACKED_SOURCE_SHA256:
        raise ValueError('AdviceOracle packed source SHA-256 mismatch')
    decoded = '\r'.join(decode_advices(packed))
    if _sha(decoded) != DECODED_SOURCE_SHA256:
        raise ValueError('AdviceOracle decoded source SHA-256 mismatch')
    result = copy.deepcopy(entry)
    result.update(source=decoded, source_sha256=DECODED_SOURCE_SHA256,
                  content_type='packed_advices', classification='probable',
                  context_confidence='inferred',
                  context='AdviceOracle: 60 CR-separated loading tips; m465 writes tfAdvice.text')
    return result


def prepare_advice_replacement(entry):
    """Re-encode the selected readable translation for existing patch_pushstrings.

    The original source must round-trip byte-for-byte to its guarded packed form.
    The caller checks each decoded record's layout before passing this result to
    patch_pushstrings, whose original-address/source guards remain in force.
    """
    _guard_address(entry)
    if (entry.get('content_type') != 'packed_advices'
            or entry.get('source_sha256') != DECODED_SOURCE_SHA256
            or not isinstance(entry.get('source'), str)
            or _sha(entry['source']) != DECODED_SOURCE_SHA256):
        raise ValueError('AdviceOracle decoded source guard mismatch')
    packed = encode_advices(split_advices(entry['source']))
    if _sha(packed) != PACKED_SOURCE_SHA256:
        raise ValueError('AdviceOracle original packing recipe differs')
    translated = encode_advices(split_advices(entry.get('translation')))
    result = copy.deepcopy(entry)
    result.update(source=packed, source_sha256=PACKED_SOURCE_SHA256,
                  translation=translated)
    return result


def check_advice_layout(font, translation, layout):
    """Measure records independently because only one is shown on each load."""
    from addressed_localization import check_layout
    return {'record_count': ADVICE_COUNT,
            'records': [check_layout(font, text, layout) for text in split_advices(translation)]}
