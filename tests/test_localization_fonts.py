"""Font extension fixtures are invented shapes, never game contours."""

import copy

import pytest

from swf_geometry import build_font, build_shape, parse_font
from localization_fonts import extend_font, measure_text


def font_fixture():
    contours = [[("M", 0, 0), ("L", 100, -200), ("L", 0, 0)]]
    glyph = build_shape(contours)
    return {
        "id": 3,
        "flags": 0x84,
        "language": 0,
        "name": b"Synthetic",
        "codes": [65, 66],
        "glyphs": [glyph, glyph],
        "advances": [200, 300],
        "bounds": [[0, 100, -200, 0]] * 2,
        "ascent": 300,
        "descent": 30,
        "leading": 5,
        "kerning": [(65, 66, -20)],
    }


def test_copy_preserves_original_indices_bytes_and_layout():
    source = font_fixture()
    before = copy.deepcopy(source)
    result = extend_font(source, {"\u0401": {"donor": "A"}})
    assert source == before
    for field in ("codes", "glyphs", "advances", "bounds"):
        assert result[field][:2] == source[field]
    for field in (
        "id",
        "name",
        "flags",
        "language",
        "ascent",
        "descent",
        "leading",
        "kerning",
    ):
        assert result[field] == source[field]
    assert result["glyphs"][2] == source["glyphs"][0]
    assert result["bounds"][2] == source["bounds"][0]
    assert build_font(parse_font(build_font(result))) == build_font(result)
    assert build_font(result) == build_font(
        extend_font(source, {"\u0401": {"donor": "A"}})
    )


def test_composite_affine_and_local_contours():
    result = extend_font(
        font_fixture(),
        {
            "\u0401": {
                "parts": [
                    {"donor": "A", "sx": -1, "tx": 200},
                    {"donor": "B", "sy": 0.5},
                ],
                "contours": [[["M", 10, -250], ["L", 20, -240], ["L", 10, -250]]],
                "advance": 250,
            },
        },
    )
    assert result["bounds"][-1] == [0, 200, -250, 0]
    assert result["advances"][-1] == 250
    assert len(result["glyphs"]) == 3


@pytest.mark.parametrize(
    "recipe",
    [
        {"donor": "Z"},
        {"donor": "A", "advance": -1},
        {"donor": "A", "sx": float("nan")},
        {"donor": "A", "advance": 40000},
        {"donor": "A", "unexpected": 1},
        {"contours": [[["M", 0, 0], ["X", 1, 2]]], "advance": 10},
    ],
)
def test_reject_invalid_recipes(recipe):
    with pytest.raises(ValueError):
        extend_font(font_fixture(), {"\u0401": recipe})


def test_reject_replacement_and_no_layout():
    with pytest.raises(ValueError):
        extend_font(font_fixture(), {"A": {"donor": "B"}})
    source = font_fixture()
    source["flags"] &= ~128
    with pytest.raises(ValueError, match="layout"):
        extend_font(source, {"\u0401": {"donor": "A"}})


def test_measure_in_twips_with_kerning_and_line_baselines():
    metrics = measure_text(font_fixture(), "AB\nA", 2048)
    assert metrics["widths"] == [48.0, 20.0]
    assert metrics["bounds"] == [[0.0, 28.0, -20.0, 0.0], [0.0, 10.0, 13.5, 33.5]]
    assert metrics["line_height"] == 33.5
    with pytest.raises(ValueError, match="Missing glyph"):
        measure_text(font_fixture(), "Z", 2048)
    with pytest.raises(ValueError):
        measure_text(font_fixture(), "A", 0)


def test_empty_text_has_no_ink():
    assert measure_text(font_fixture(), "", 100)["bounds"] == [None]


def test_sorted_insertion_preserves_high_punctuation_by_code():
    source = font_fixture()
    source["codes"].append(8208)
    source["glyphs"].append(source["glyphs"][0])
    source["advances"].append(500)
    source["bounds"].append([0, 100, -200, 0])
    result = extend_font(source, {"\u0401": {"donor": "A"}})
    assert result["codes"] == [65, 66, 1025, 8208]
    for old_index, code in enumerate(source["codes"]):
        new_index = result["codes"].index(code)
        for field in ("glyphs", "advances", "bounds"):
            assert result[field][new_index] == source[field][old_index]


def test_wide_code_flag_is_promoted_without_changing_style_flags():
    source = font_fixture()
    source["flags"] = 0x81
    result = extend_font(source, {"\u0401": {"donor": "A"}})
    assert result["flags"] == 0x85


def test_malformed_source_metrics_fail_closed():
    source = font_fixture()
    source["advances"][0] = 40000
    with pytest.raises(ValueError, match="layout"):
        extend_font(source, {"\u0401": {"donor": "A"}})
