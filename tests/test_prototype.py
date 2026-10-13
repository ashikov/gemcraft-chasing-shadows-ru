import os
import struct
from pathlib import Path

import pytest

from build_prototype import NEW_FONT_ID, TRANSLATION, build_patch, validate_patch
from install_patch import (
    GAME_FILE,
    ORIGINAL_SHA256,
    PROJECT,
    apply_patch_file,
    restore_patch_file,
    sha,
)
from swf_geometry import Tag, parse_font, parse_shape, parse_text, read_swf, write_swf

pytestmark = pytest.mark.local


@pytest.fixture(scope="module")
def prototype():
    backup = (
        PROJECT / "backups" / (GAME_FILE.name + "." + ORIGINAL_SHA256 + ".original")
    )
    source = os.environ.get("GEMCRAFT_ORIGINAL")
    if not source and not backup.exists():
        pytest.skip("Local original required: set GEMCRAFT_ORIGINAL")
    original = (Path(source) if source else backup).read_bytes()
    return original, build_patch(original)


def test_translation_and_original_latin_resources_preserved(prototype):
    original, patched = prototype
    report = validate_patch(original, patched)
    assert sha(patched) == "41bb1a40cf95f8b48a9829df130ee492f07c8dcb2539070b11c07fc181f592e9"
    assert report["decoded_translation"] == TRANSLATION
    assert report["original_tags_unchanged_except_title"] == 4225
    _, tags = read_swf(patched)
    new_font = parse_font(
        next(
            t.payload
            for t in tags
            if t.code == 75 and struct.unpack_from("<H", t.payload)[0] == NEW_FONT_ID
        )
    )
    assert new_font["codes"] == sorted(map(ord, set(TRANSLATION)))
    assert all(parse_shape(g) for g in new_font["glyphs"])
    text = parse_text(
        next(
            t.payload
            for t in tags
            if t.code == 11 and struct.unpack_from("<H", t.payload)[0] == 3016
        )
    )
    assert len(text["records"][0]["entries"]) == 9
    assert text["records"][0]["height"] == 480


def test_unknown_version_rejected_without_output():
    with pytest.raises(ValueError, match="SHA"):
        build_patch(b"unknown game version")


def test_extra_mutation_is_rejected(prototype):
    original, patched = prototype
    prefix, tags = read_swf(patched)
    index = next(i for i, t in enumerate(tags) if t.code == 24)
    t = tags[index]
    tags[index] = Tag(t.code, t.payload[:-1] + bytes([t.payload[-1] ^ 1]))
    with pytest.raises(ValueError):
        validate_patch(original, write_swf(prefix, tags, patched[3]))


def test_native_homographs_are_exact_copies(prototype):
    original, patched = prototype
    _, source_tags = read_swf(original)
    _, next_tags = read_swf(patched)
    source = parse_font(
        next(
            t.payload
            for t in source_tags
            if t.code == 75 and int.from_bytes(t.payload[:2], "little") == 1
        )
    )
    new = parse_font(
        next(
            t.payload
            for t in next_tags
            if t.code == 75 and int.from_bytes(t.payload[:2], "little") == NEW_FONT_ID
        )
    )
    for ru, latin in [("Н", "H"), ("а", "a"), ("с", "c"), ("р", "p"), ("о", "o")]:
        assert (
            new["glyphs"][new["codes"].index(ord(ru))]
            == source["glyphs"][source["codes"].index(ord(latin))]
        )


def test_actual_swf_can_be_restored_byte_for_byte_in_isolated_directory(
    prototype, tmp_path
):
    original, patched = prototype
    game = tmp_path / "game"
    game.mkdir()
    target = game / "gc-cs-steam.swf"
    target.write_bytes(original)
    candidate = tmp_path / "candidate.swf"
    candidate.write_bytes(patched)
    manifest = apply_patch_file(
        target, candidate, tmp_path / "backups", ORIGINAL_SHA256
    )
    assert target.read_bytes() == patched
    restore_patch_file(manifest)
    assert target.read_bytes() == original
