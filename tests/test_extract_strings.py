"""Synthetic SWFs; no extracted game outlines or text."""
import hashlib
import pytest

from extract_strings import extract_static
from swf_geometry import Tag, build_font, build_shape, build_text, encode_matrix, encode_rect, write_swf
from translation_catalog import validate_catalog


def synthetic_swf(tag_code=11, missing_font=False, bad_glyph=False):
    glyph = build_shape([[("M", 0, 0), ("L", 10, 10), ("L", 0, 0)]])
    font = {"id": 4, "flags": 0, "language": 0, "name": b"Synthetic",
            "codes": [65, 66], "glyphs": [glyph, glyph]}
    text = {"id": 7, "bounds": [0, 100, 0, 100], "matrix_raw": encode_matrix({}),
            "gb": 2, "ab": 8, "records": [
                {"flags": 0x88, "font_id": 4, "height": 100, "entries": [(0, 20)]},
                {"flags": 0x80, "entries": [(3 if bad_glyph else 1, 20)]},
            ]}
    prefix = b"FWS\x0a\0\0\0\0" + encode_rect([0, 100, 0, 100]) + b"\0\x18\1\0"
    tags = ([] if missing_font else [Tag(75, build_font(font))])
    tags += [Tag(tag_code, build_text(text, tag_code)), Tag(0, b"")]
    return write_swf(prefix, tags, 10)


@pytest.mark.parametrize("tag_code", [11, 33])
def test_selected_static_and_inherited_font(tag_code):
    raw = synthetic_swf(tag_code)
    data = extract_static(raw, [7], hashlib.sha256(raw).hexdigest())
    entry = data["entries"][0]
    assert entry["source"] == "AB"
    assert entry["id"] == "static.7"
    assert entry["translation"] is None
    assert entry["location"]["font_ids"] == [4]
    assert validate_catalog(data, allow_missing=True) == []


@pytest.mark.parametrize("kwargs,ids,message", [
    ({"missing_font": True}, [7], "font"),
    ({"bad_glyph": True}, [7], "glyph"),
    ({}, [9], "not found"),
    ({}, [], "Select"),
    ({}, [7, 7], "duplicate"),
])
def test_invalid_selection_fails(kwargs, ids, message):
    raw = synthetic_swf(**kwargs)
    with pytest.raises(ValueError, match=message):
        extract_static(raw, ids, hashlib.sha256(raw).hexdigest())


def test_hash_guard_before_parsing():
    with pytest.raises(ValueError, match="SHA-256"):
        extract_static(b"unknown", [7], "a" * 64)


def test_cli_preserves_existing_output_and_requires_selection(tmp_path):
    from extract_strings import main
    raw = synthetic_swf()
    source = tmp_path / "original.swf"
    source.write_bytes(raw)
    output = tmp_path / "selected.json"
    output.write_text("keep existing", encoding="utf-8")
    args = [str(source), "--original-sha256", hashlib.sha256(raw).hexdigest(),
            "--output", str(output)]
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    with pytest.raises(SystemExit) as error:
        main(args + ["--static-id", "7"])
    assert error.value.code == 1
    assert output.read_text(encoding="utf-8") == "keep existing"
    assert source.read_bytes() == raw
