"""Real JPEG/geometry/resource checks; offline only, never starts GemCraft."""
import io
import json
import os
from pathlib import Path

import pytest

from journey_map_localization import (DEFAULT_RECIPE, _render_map, digest,
    extract_map_jpeg, generate_map_jpeg, jpeg_metadata, patch_map_tags)
from swf_geometry import read_swf, write_swf

pytestmark = pytest.mark.local


@pytest.fixture(scope='module')
def map_artifacts():
    if not os.environ.get('GEMCRAFT_ORIGINAL'):
        pytest.skip('Set GEMCRAFT_ORIGINAL to the supported private original')
    from PIL import Image
    source = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    recipe = json.loads(DEFAULT_RECIPE.read_text())['journey_map']
    assert digest(source) == recipe['game_sha256']
    font = Path(os.environ.get('GEMCRAFT_ALEGREYA_FONT',
                              'reference-fonts/alegreya/Alegreya[wght].ttf')).read_bytes()
    prefix, tags = read_swf(source)
    original_jpeg = extract_map_jpeg(tags, recipe)
    jpeg, report = generate_map_jpeg(original_jpeg, font, recipe)
    with Image.open(io.BytesIO(jpeg)) as image:
        image.load()
        assert image.format == 'JPEG' and image.size == (1440, 868)
        assert image.mode == 'RGB'
    return source, prefix, tags, recipe, font, original_jpeg, jpeg, report


def test_actual_full_jpeg_empty_tables_and_original_chain_guards(map_artifacts):
    _, _, tags, recipe, _, jpeg, _, _ = map_artifacts
    assert tags[750].code == 6 and tags[750].payload[:2] == b'\xa6\x02'
    assert [(index, tag.payload) for index, tag in enumerate(tags) if tag.code == 8] == [(286, b'')]
    assert jpeg.startswith(b'\xff\xd8\xff\xe0')
    assert jpeg_metadata(jpeg)['self_contained']
    assert len(recipe['resource_guards']) == 4
    assert {row['character_id'] for row in recipe['resource_guards']} == {679, 680, 813}
    assert recipe['source_jpeg_sha256'] == '3fdb11eacb6b7ac71c75f1ca2a66621dd6882e5c46bf4b87174db1e3d2afcd0d'


def test_map_render_is_deterministic_and_has_ten_regions_twelve_lines(map_artifacts):
    _, _, _, recipe, font, original_jpeg, jpeg, report = map_artifacts
    repeated, repeated_report = generate_map_jpeg(original_jpeg, font, recipe)
    assert repeated == jpeg and repeated_report == report
    assert report['jpeg_sha256'] == recipe['result']['jpeg_sha256']
    assert report['label_regions'] == 10 and report['text_lines'] == 12
    assert {row['id'] for row in recipe['labels']} == {
        'tomb', 'frozen', 'labyrinth', 'scythe', 'spiritforge', 'tower',
        'waiting', 'possessed', 'wilderness', 'inhabited'}
    assert all(any('\u0400' <= char <= '\u04ff' for char in line)
               for label in recipe['labels'] for line in label['lines'])
    assert report['runtime'] == 'NOT TESTED'


def test_pixels_outside_letter_masks_routes_and_landmarks_are_preserved_before_jpeg(map_artifacts):
    from PIL import Image
    _, _, _, recipe, font, original_jpeg, _, report = map_artifacts
    rendered, _, union, _, pixel_report = _render_map(original_jpeg, font, recipe)
    original = Image.open(io.BytesIO(original_jpeg)).convert('RGB')
    source_pixels, actual, mask = original.tobytes(), rendered.tobytes(), union.tobytes()
    assert all(source_pixels[at * 3:at * 3 + 3] == actual[at * 3:at * 3 + 3]
               for at, value in enumerate(mask) if value == 0)
    for rectangle in recipe['protected_landmarks']:
        assert original.crop(rectangle).tobytes() == rendered.crop(rectangle).tobytes()
    assert report['preencode_outside_edit_mask_equal']
    assert pixel_report['preencode_route_core_pixels_preserved'] > 1000
    assert report['jpeg_is_lossy']  # Pixel equality is deliberately before encoding.


def test_serialized_replacement_preserves_every_other_raw_tag_and_is_decodable(map_artifacts):
    from PIL import Image
    source, prefix, tags, recipe, _, _, jpeg, _ = map_artifacts
    patched, report = patch_map_tags(tags, jpeg, recipe)
    encoded = write_swf(prefix, patched, source[3])
    prefix_after, reread = read_swf(encoded)
    assert prefix_after[:4] == prefix[:4]
    assert len(reread) == len(tags)
    assert report['changed_character_ids'] == [678]
    for before, after in zip(tags, reread):
        if before is tags[750]:
            assert after.code == 6
            assert after.payload == b'\xa6\x02' + jpeg
            Image.open(io.BytesIO(after.payload[2:])).load()
        else:
            assert before.raw == after.raw
    assert encoded != source


def test_reviewed_label_bounds_at_all_scales_keep_landmark_clearance(map_artifacts):
    _, _, _, recipe, _, _, _, report = map_artifacts
    for label in report['labels']:
        bounds = label['bounds']
        for scale in (1, 0.75, 0.5):
            factor = scale / 2  # Original shape maps two bitmap pixels to one UI pixel.
            assert label['scaled_bounds'][str(scale)] == [value * factor for value in bounds]
            assert 0 <= bounds[0] < bounds[2] <= 1440
            assert 0 <= bounds[1] < bounds[3] <= 868
            for landmark in recipe['protected_landmarks']:
                b, p = [value * factor for value in bounds], [value * factor for value in landmark]
                assert b[2] <= p[0] or p[2] <= b[0] or b[3] <= p[1] or p[3] <= b[1]


def test_generation_rejects_font_source_layout_and_reproduced_landmark_damage(map_artifacts):
    import copy
    _, _, tags, recipe, font, original_jpeg, _, _ = map_artifacts
    with pytest.raises(ValueError, match='font SHA'):
        generate_map_jpeg(original_jpeg, font + b'x', recipe)
    with pytest.raises(ValueError, match='JPEG SHA'):
        generate_map_jpeg(original_jpeg + b'x', font, recipe)
    bad = copy.deepcopy(recipe)
    bad['labels'][2]['center'] = [561, 319]  # Historical prototype touched the square.
    with pytest.raises(ValueError, match='reviewed bounds|landmark'):
        generate_map_jpeg(original_jpeg, font, bad)
    bad = copy.deepcopy(recipe)
    next(row for row in bad['labels'] if row['id'] == 'possessed')['source_bounds'][1] = 583
    with pytest.raises(ValueError, match='protected landmark'):
        generate_map_jpeg(original_jpeg, font, bad)
    broken = list(tags)
    broken[751] = type(tags[751])(tags[751].code, tags[751].payload + b'x')
    with pytest.raises(ValueError, match='resource guard'):
        extract_map_jpeg(broken, recipe)
