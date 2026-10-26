"""Synthetic contracts for the single optional Journey map graphics recipe."""
import copy
import hashlib

import pytest

from journey_map_localization import jpeg_metadata, patch_map_tags, restore_masked_rows
from swf_geometry import Tag


def segment(marker, payload):
    return bytes((255, marker)) + (len(payload) + 2).to_bytes(2, 'big') + payload


def synthetic_jpeg(width=1440, height=868):
    # Header fixture, deliberately not an entropy-decoding test or a game image.
    frame = bytes((8,)) + height.to_bytes(2, 'big') + width.to_bytes(2, 'big')
    frame += bytes((3, 1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1))
    return (b'\xff\xd8' + segment(0xdb, b'\0' * 65) + segment(0xc0, frame)
            + segment(0xc4, b'\0' * 18) + segment(0xda, b'\x03\x01\0\x02\x11\x03\x11\0\x3f\0')
            + b'\x01\xff\0\x02\xff\xd9')


def recipe_for(tags, jpeg):
    return {'character_id': 678, 'tag_code': 6, 'size': [1440, 868],
            'source_payload_sha256': hashlib.sha256(tags[1].payload).hexdigest(),
            'jpeg_tables': [{'tag_path': [0], 'payload_sha256': hashlib.sha256(b'').hexdigest()}],
            'resource_guards': [],
            'result': {'jpeg_sha256': hashlib.sha256(jpeg).hexdigest()}}


def test_full_baseline_jpeg_header_has_own_tables_and_exact_dimensions():
    result = jpeg_metadata(synthetic_jpeg())
    assert result == {'width': 1440, 'height': 868, 'precision': 8,
                      'components': 3, 'frame_marker': 0xc0, 'self_contained': True}


@pytest.mark.parametrize('damaged', [b'', b'\xff\xd8\xff\xd9',
    synthetic_jpeg()[:-1], synthetic_jpeg() + b'x',
    synthetic_jpeg().replace(b'\xff\xdb', b'\xff\xfe', 1),
    b'\xff\xd8\xff\xdb\0\x01\xff\xd9',
    synthetic_jpeg().replace(b'\xff\xc0', b'\xff\xc2', 1)])
def test_damaged_incomplete_or_progressive_jpeg_is_rejected(damaged):
    with pytest.raises(ValueError):
        jpeg_metadata(damaged)


def test_graphics_patch_replaces_only_the_guarded_bitmap_keeps_tag6_and_tables():
    jpeg = synthetic_jpeg()
    tags = [Tag(8, b'', b'tables'), Tag(6, b'\xa6\x02original', b'bitmap'),
            Tag(2, b'\xa7\x02shape', b'shape'), Tag(0, b'', b'end')]
    recipe = recipe_for(tags, jpeg)
    new, report = patch_map_tags(tags, jpeg, recipe)
    assert new[0] is tags[0] and new[2] is tags[2] and new[3] is tags[3]
    assert new[1] == Tag(6, b'\xa6\x02' + jpeg)
    assert tags[1].payload == b'\xa6\x02original'
    assert report['changed_character_ids'] == [678]
    assert report['jpeg_sha256'] == hashlib.sha256(jpeg).hexdigest()


def test_graphics_patch_rejects_unexpected_source_duplicate_tables_or_result():
    jpeg = synthetic_jpeg()
    tags = [Tag(8, b''), Tag(6, b'\xa6\x02original'), Tag(0, b'')]
    recipe = recipe_for(tags, jpeg)
    for changed in ([tags[0], Tag(6, b'\xa6\x02different'), tags[2]],
                    [*tags[:2], tags[1], tags[2]],
                    [Tag(8, b'not empty'), *tags[1:]],
                    [Tag(8, b''), *tags],
                    [tags[0], Tag(21, tags[1].payload), tags[2]]):
        with pytest.raises(ValueError):
            patch_map_tags(changed, jpeg, recipe)
    bad = copy.deepcopy(recipe)
    bad['result']['jpeg_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        patch_map_tags(tags, jpeg, bad)
    wrong_size = synthetic_jpeg(width=1439)
    bad['result']['jpeg_sha256'] = hashlib.sha256(wrong_size).hexdigest()
    with pytest.raises(ValueError):
        patch_map_tags(tags, wrong_size, bad)


def test_restoration_changes_only_exact_mask_and_interpolates_neighbors():
    pixels = bytes([10, 20, 30, 0, 0, 0, 0, 0, 0, 70, 80, 90,
                    100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210])
    mask = bytes([0, 255, 255, 0, 0, 0, 0, 0])
    restored = restore_masked_rows(pixels, mask, 4, 2)
    assert restored == bytes([10, 20, 30, 30, 40, 50, 50, 60, 70, 70, 80, 90,
                              100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210])
    assert restore_masked_rows(pixels, bytes(8), 4, 2) == pixels
    with pytest.raises(ValueError):
        restore_masked_rows(pixels, bytes([255, 0, 0, 0, 0, 0, 0, 0]), 4, 2)
    with pytest.raises(ValueError):
        restore_masked_rows(pixels, mask[:-1], 4, 2)


def test_authored_recipe_scope_and_typography_validate_without_pillow():
    import json
    from journey_map_localization import DEFAULT_RECIPE, validate_map_recipe
    plan = json.loads(DEFAULT_RECIPE.read_text())
    recipe = plan['journey_map']
    assert plan['entry_ids'] == [] and plan['fonts'] == []
    assert validate_map_recipe(recipe)
    for key, value in [('tag_path', [751]), ('character_id', 679), ('size', [1439, 868]),
                       ('font_weight', 700), ('source_payload_sha256', 'wrong')]:
        changed = {**recipe, key: value}
        with pytest.raises(ValueError):
            validate_map_recipe(changed)
    changed = copy.deepcopy(recipe)
    changed['labels'][0]['lines'] = ['Строка\nс переносом']
    with pytest.raises(ValueError):
        validate_map_recipe(changed)


def test_source_tag_path_guard_is_checked_before_patching():
    jpeg = synthetic_jpeg()
    tags = [Tag(8, b''), Tag(6, b'\xa6\x02original'), Tag(0, b'')]
    recipe = recipe_for(tags, jpeg)
    recipe['tag_path'] = [2]
    with pytest.raises(ValueError, match='tag path'):
        patch_map_tags(tags, jpeg, recipe)
