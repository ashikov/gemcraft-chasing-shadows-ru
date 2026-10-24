"""Regression for the runtime-confirmed clipped wasp statistic in battle results."""
import os
from pathlib import Path

import pytest

from addressed_localization import _display_text, check_layout
from install_patch import ORIGINAL_SHA256, sha
from localization_fonts import measure_text
from swf_geometry import parse_font, parse_text, read_swf
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


def test_wasp_result_caption_fits_original_slot_with_effect_margin():
    required = ('GEMCRAFT_ORIGINAL', 'GEMCRAFT_PR33_CAPTION_WORKSPACE',
                'GEMCRAFT_PR33_CAPTION_CANDIDATE')
    if not all(os.environ.get(name) for name in required):
        pytest.skip('Set original, restored caption workspace and candidate for the pinned font')
    source = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    assert sha(source) == ORIGINAL_SHA256
    workspace = load_catalog(os.environ['GEMCRAFT_PR33_CAPTION_WORKSPACE'])
    entry = next(row for row in workspace['entries'] if row['id'] == 'static.3767')
    original_tags = read_swf(source)[1]
    original = next(tag for tag in original_tags if tag.code == 11
                    and int.from_bytes(tag.payload[:2], 'little') == 3767)
    original_text = parse_text(original.payload)
    original_fonts = {int.from_bytes(tag.payload[:2], 'little'): parse_font(tag.payload)
                      for tag in original_tags if tag.code == 75}
    candidate = Path(os.environ['GEMCRAFT_PR33_CAPTION_CANDIDATE']).read_bytes()
    font = next(parse_font(tag.payload) for tag in read_swf(candidate)[1]
                if tag.code == 75 and int.from_bytes(tag.payload[:2], 'little') == 6002)
    layout = load_catalog('translations/plans/pr06-outcome.json')['layouts'][entry['id']]
    slot_width = original_text['bounds'][1] - original_text['bounds'][0]
    assert layout['max_width'] <= slot_width
    # The former translation really exceeds this source-derived slot; a larger
    # generic text budget concealed its runtime clipping at the panel boundary.
    with pytest.raises(ValueError, match='field width'):
        check_layout(font, 'Жалящие удары самоцветных ос:', layout)
    assert entry['translation'] == 'Удары самоцветных ос:'
    check_layout(font, entry['translation'], layout)
    translated, _ = _display_text(original, entry, font, layout, original_fonts)
    text = parse_text(translated.payload)
    for key in ('height', 'color', 'y'):
        assert text['records'][0][key] == original_text['records'][0][key]
    assert text['matrix_raw'] == original_text['matrix_raw']
    assert text['bounds'][0] >= original_text['bounds'][0]
    assert text['bounds'][1] <= original_text['bounds'][1]
    # Reserve two source pixels for the existing glow on either side.
    assert text['bounds'][0] - original_text['bounds'][0] >= 40
    assert original_text['bounds'][1] - text['bounds'][1] >= 40
    for scale in (1, 0.75, 0.5):
        assert measure_text(font, entry['translation'], 240)['widths'][0] * scale <= slot_width * scale
