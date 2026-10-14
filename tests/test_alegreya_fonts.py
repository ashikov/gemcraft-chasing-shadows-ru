"""Invented TrueType outlines exercise the converter without game resources."""

from io import BytesIO

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.feaLib.builder import addOpenTypeFeaturesFromString

from alegreya_fonts import convert_font, instantiate_face
from swf_geometry import build_font, parse_font, parse_shape, shape_bounds


def synthetic_font(weight=400, empty=False):
    builder = FontBuilder(1000, isTTF=True)
    order = [".notdef", "space", "A", "V", "curve", "composite"]
    builder.setupGlyphOrder(order)
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(None)
        if name not in ("space", "composite") and not empty:
            pen.moveTo((0, 0))
            if name == "curve":
                pen.qCurveTo((100, 500), (300, 500), (400, 0))
            else:
                pen.lineTo((200 + (weight == 700) * 30, 700))
                pen.lineTo((400, 0))
            pen.closePath()
        glyphs[name] = pen.glyph()
    pen = TTGlyphPen(glyphs)
    pen.addComponent("curve", (1, 0, 0, 1, 20, 50))
    glyphs["composite"] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (500, 0) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200, lineGap=50)
    builder.setupCharacterMap(
        {
            32: "space",
            65: "A",
            86: "V",
            48: "A",
            37: "curve",
            46: "curve",
            0x401: "composite",
            0x451: "composite",
            0x419: "composite",
            0x439: "composite",
            0x431: "curve",
            0x411: "A",
        }
    )
    builder.setupNameTable(
        {
            "familyName": "Invented Fixture",
            "styleName": "Regular",
            "copyright": "Synthetic fixture",
            "psName": "InventedFixture",
        }
    )
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
        usWeightClass=weight,
    )
    builder.setupPost()
    addOpenTypeFeaturesFromString(builder.font, "feature kern { pos A V -40; } kern;")
    output = BytesIO()
    builder.save(output)
    return output.getvalue()


def test_coordinates_quadratic_composites_metrics_and_roundtrip():
    source = synthetic_font()
    font = convert_font(source, 400, " AV0%.ЁёЙйбБ", 6001)
    assert font["codes"] == sorted(map(ord, set(" AV0%.ЁёЙйбБ")))
    shapes = dict(zip(font["codes"], map(parse_shape, font["glyphs"])))
    assert shapes[32] == []
    assert shapes[65] == [
        [("M", 0, 0), ("L", 4096, -14336), ("L", 8192, 0), ("L", 0, 0)]
    ]
    assert shapes[37][0][1] == ("Q", 2048, -10240, 4096, -10240)
    assert shapes[0x401][0][0] == ("M", 410, -1024)
    assert font["ascent"] == 16384
    assert font["descent"] == 4096
    assert font["leading"] == 1024
    assert set(font["advances"]) == {10240}
    assert font["kerning"] == [(48, 86, -819), (65, 86, -819), (0x411, 86, -819)]
    assert font["bounds"][font["codes"].index(37)] == [0, 8192, -10240, 0]
    assert build_font(parse_font(build_font(font))) == build_font(font)
    assert build_font(font) == build_font(
        convert_font(source, 400, reversed(" AV0%.ЁёЙйбБ"), 6001)
    )


def test_bold_has_separate_flag_and_outlines_but_same_family():
    regular = convert_font(synthetic_font(), 400, "A", 6001)
    bold = convert_font(synthetic_font(700), 700, "A", 6002)
    assert regular["name"] == bold["name"] == b"GCCS Alegreya"
    assert not regular["flags"] & 1
    assert bold["flags"] & 1
    assert regular["glyphs"] != bold["glyphs"]


def test_static_instance_is_deterministic_renamed_and_preserves_copyright():
    source = synthetic_font()
    first = instantiate_face(source, 400)
    assert first == instantiate_face(source, 400)
    with TTFont(BytesIO(first)) as font:
        assert "fvar" not in font
        assert font["name"].getDebugName(1) == "GCCS Alegreya"
        assert font["name"].getDebugName(0) == "Synthetic fixture"
        assert font["OS/2"].usWeightClass == 400


@pytest.mark.parametrize("characters", ["Z", "\U0001f600", "\n", ""])
def test_rejects_missing_nonbmp_controls_and_empty_repertoire(characters):
    with pytest.raises(ValueError):
        convert_font(synthetic_font(), 400, characters, 6001)


def test_rejects_invalid_font_wrong_weight_and_empty_visible_glyph():
    for source, weight in [
        (b"broken", 400),
        (synthetic_font(), 700),
        (synthetic_font(empty=True), 400),
        (synthetic_font(), 500),
    ]:
        with pytest.raises(ValueError):
            convert_font(source, weight, "A", 6001)


def test_rejects_unrepresentable_gpos_placement():
    font = TTFont(BytesIO(synthetic_font()))
    del font["GPOS"]
    addOpenTypeFeaturesFromString(font, "feature kern { pos A V <10 0 -40 0>; } kern;")
    output = BytesIO()
    font.save(output)
    with pytest.raises(ValueError, match="represent"):
        convert_font(output.getvalue(), 400, "AV", 6001)


def variable_font():
    from fontTools.ttLib.tables.TupleVariation import TupleVariation

    font = TTFont(BytesIO(synthetic_font()))
    builder = FontBuilder(font=font)
    builder.setupFvar([("wght", 400, 400, 900, "Weight")], [])
    # Three invented outline points, followed by the four phantom metric points.
    builder.setupGvar(
        {
            "A": [
                TupleVariation(
                    {"wght": (0, 1, 1)}, [(0, 0), (50, 0), (0, 0)] + [(0, 0)] * 4
                )
            ]
        }
    )
    output = BytesIO()
    font.save(output)
    return output.getvalue()


def test_variable_weights_freeze_axis_and_interpolate_without_timestamp_drift():
    source = variable_font()
    instances = [instantiate_face(source, weight) for weight in (400, 700)]
    assert instances == [instantiate_face(source, weight) for weight in (400, 700)]
    for instance, weight in zip(instances, (400, 700)):
        with TTFont(BytesIO(instance)) as font:
            assert "fvar" not in font
            assert "gvar" not in font
            assert font["OS/2"].usWeightClass == weight
    regular = convert_font(source, 400, "A", 6001)
    bold = convert_font(source, 700, "A", 6002)
    assert parse_shape(regular["glyphs"][0])[0][1] == ("L", 4096, -14336)
    assert parse_shape(bold["glyphs"][0])[0][1] == ("L", 4710, -14336)


def test_gpos_class_pair_and_ignore_marks_without_marks():
    font = TTFont(BytesIO(synthetic_font()))
    del font["GPOS"]
    addOpenTypeFeaturesFromString(
        font,
        """
        @Left = [A curve]; @Right = [V composite];
        feature kern { lookupflag IgnoreMarks; pos @Left @Right -25; } kern;
    """,
    )
    output = BytesIO()
    font.save(output)
    result = convert_font(output.getvalue(), 400, "AV%Ё", 6001)
    assert result["kerning"] == [
        (37, 86, -512),
        (37, 1025, -512),
        (65, 86, -512),
        (65, 1025, -512),
    ]


def test_rejects_bad_swfid_family_and_metrics():
    source = synthetic_font()
    for font_id in (0, 65536):
        with pytest.raises(ValueError, match="ID"):
            convert_font(source, 400, "A", font_id)
    with pytest.raises(ValueError, match="name"):
        convert_font(source, 400, "A", 6001, family_name="a" * 256)
    font = TTFont(BytesIO(source))
    font["hmtx"].metrics["A"] = (2000, 0)
    output = BytesIO()
    font.save(output)
    with pytest.raises(ValueError, match="advance"):
        convert_font(output.getvalue(), 400, "A", 6001)


def test_rejects_extra_variable_axis():
    font = TTFont(BytesIO(variable_font()))
    axis = type(font["fvar"].axes[0])()
    axis.axisTag, axis.minValue, axis.defaultValue, axis.maxValue = "wdth", 75, 100, 125
    axis.flags, axis.axisNameID = 0, 256
    font["fvar"].axes.append(axis)
    output = BytesIO()
    font.save(output)
    with pytest.raises(ValueError, match="only the wght"):
        instantiate_face(output.getvalue(), 400)


@pytest.mark.local
@pytest.mark.parametrize("weight", [400, 700])
def test_official_alegreya_bmp_repertoire_reproducibility(weight):
    import hashlib
    from pathlib import Path

    source_path = Path("reference-fonts/alegreya/Alegreya[wght].ttf")
    if not source_path.exists():
        pytest.skip("Pinned freely licensed local Alegreya source is absent")
    source = source_path.read_bytes()
    assert hashlib.sha256(source).hexdigest() == (
        "ba5564634b93a8f8ba57b48cd4f1ae7417d2b4656fbac779028679b00de3cf12"
    )
    characters = (
        "".join(map(chr, range(32, 127)))
        + "".join(map(chr, range(0x410, 0x450)))
        + "Ёё–—…«»№"
    )
    first = convert_font(source, weight, characters, 6001)
    assert len(first["codes"]) == len(set(characters))
    assert first["ascent"] == 20808
    assert first["descent"] == 7066
    assert first["leading"] == 0
    assert first["kerning"]
    assert build_font(first) == build_font(
        convert_font(source, weight, characters, 6001)
    )
    for code, glyph, bounds in zip(first["codes"], first["glyphs"], first["bounds"]):
        assert bounds == shape_bounds(parse_shape(glyph))
        if code != 32:
            assert parse_shape(glyph)
            assert bounds[0] < bounds[1]
            assert bounds[2] < bounds[3]


def contour_areas(contours):
    from fontTools.pens.areaPen import AreaPen

    areas = []
    for contour in contours:
        pen = AreaPen(None)
        pen.moveTo(contour[0][1:])
        for segment in contour[1:]:
            if segment[0] == "L":
                pen.lineTo(segment[1:])
            else:
                pen.qCurveTo(segment[1:3], segment[3:5])
        pen.closePath()
        areas.append(pen.value)
    return areas


def test_preserves_hole_opposite_winding_after_y_inversion():
    font = TTFont(BytesIO(synthetic_font()))
    pen = TTGlyphPen(None)
    for points in (
        ((0, 0), (0, 600), (600, 600), (600, 0)),
        ((100, 100), (500, 100), (500, 500), (100, 500)),
    ):
        pen.moveTo(points[0])
        for point in points[1:]:
            pen.lineTo(point)
        pen.closePath()
    font["glyf"]["A"] = pen.glyph()
    output = BytesIO()
    font.save(output)
    converted = convert_font(output.getvalue(), 400, "A", 6001)
    areas = contour_areas(parse_shape(converted["glyphs"][0]))
    assert areas == [360000 * 20.48**2, -160000 * 20.48**2]


@pytest.mark.local
@pytest.mark.parametrize("weight", [400, 700])
def test_official_glyph_winding_matches_decomposed_quadratic_source(weight):
    from pathlib import Path
    from fontTools.pens.areaPen import AreaPen
    from fontTools.pens.recordingPen import DecomposingRecordingPen

    path = Path("reference-fonts/alegreya/Alegreya[wght].ttf")
    if not path.exists():
        pytest.skip("Pinned freely licensed local Alegreya source is absent")
    source = path.read_bytes()
    static = TTFont(BytesIO(instantiate_face(source, weight)))
    glyph_set = static.getGlyphSet()
    converted = convert_font(source, weight, "ОобБ8ёЙ%", 6001)
    for code, raw in zip(converted["codes"], converted["glyphs"]):
        recording = DecomposingRecordingPen(glyph_set)
        glyph_set[static.getBestCmap()[code]].draw(recording)
        expected_areas = []
        pen = AreaPen(None)
        previous_area = 0
        for operation, arguments in recording.value:
            getattr(pen, operation)(*arguments)
            if operation == "closePath":
                expected_areas.append(pen.value - previous_area)
                previous_area = pen.value
        actual_areas = contour_areas(parse_shape(raw))
        assert len(actual_areas) == len(expected_areas)
        # SWF reverses y and rounds to 1/20480 em; allow only rounding error.
        for actual, expected in zip(actual_areas, expected_areas):
            assert actual == pytest.approx(-expected * 20.48**2, rel=0.002)
    static.close()
