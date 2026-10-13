"""Synthetic checks contain no extracted game resources."""

import unittest
from swf_geometry import (
    Tag,
    build_font,
    build_shape,
    build_text,
    decode_matrix,
    decode_rect,
    encode_matrix,
    encode_rect,
    parse_font,
    parse_shape,
    parse_text,
    read_swf,
    shape_bounds,
    write_swf,
)


class GeometryTests(unittest.TestCase):
    def test_shape_roundtrip_negative_curve_and_multiple_contours(self):
        contours = [
            [
                ("M", -250, 100),
                ("L", 10, -100),
                ("Q", 123, -350, 450, 50),
                ("L", -250, 100),
            ],
            [("M", 0, 0), ("L", 15, 20), ("L", 0, 0)],
        ]
        encoded = build_shape(contours)
        self.assertEqual(parse_shape(encoded), contours)
        self.assertEqual(shape_bounds(contours), [-250, 450, -350, 100])
        self.assertEqual(encoded[0], 0x10)
        # StateMoveTo + StateFillStyle0, not FillStyle1.
        self.assertEqual(encoded[1] >> 2, 3)

    def test_reject_truncated_and_unsupported_shape_styles(self):
        with self.assertRaises(ValueError):
            parse_shape(b"\x11\x00")
        with self.assertRaises(ValueError):
            parse_shape(b"\x10")
        with self.assertRaises(ValueError):
            parse_shape(b"\x10\x40")
        with self.assertRaises(ValueError):
            build_shape([[("M", 0, 0), ("L", 100000, 0)]])

    def test_rect_and_matrix_signed_fixed_point(self):
        rect = [-900, 15000, -12, 430]
        self.assertEqual(decode_rect(encode_rect(rect))[0], rect)
        matrix = {
            "scale": [65536, -32768],
            "rotate": [1024, -2048],
            "translate": [-250, 125],
        }
        self.assertEqual(decode_matrix(encode_matrix(matrix))[0], matrix)

    def test_font_layout_and_original_glyph_blocks_preserved(self):
        glyph = build_shape([[("M", 0, 0), ("L", 10, 20), ("L", 0, 0)]])
        font = {
            "id": 6000,
            "flags": 0x84,
            "language": 0,
            "name": b"Local test",
            "codes": [65, 0x41D],
            "glyphs": [glyph, glyph],
            "advances": [30, 40],
            "bounds": [[0, 10, 0, 20]] * 2,
            "ascent": 100,
            "descent": 20,
            "leading": -3,
            "kerning": [(65, 0x41D, -2)],
        }
        encoded = build_font(font)
        parsed = parse_font(encoded)
        self.assertEqual(parsed["glyphs"], font["glyphs"])
        self.assertEqual(parsed["kerning"], font["kerning"])
        self.assertEqual(build_font(parsed), encoded)
        parsed["codes"].append(0x430)
        parsed["glyphs"].append(glyph)
        parsed["advances"].append(33)
        parsed["bounds"].append([0, 10, 0, 20])
        extended = parse_font(build_font(parsed))
        self.assertEqual(extended["glyphs"][:2], font["glyphs"])
        self.assertEqual(extended["codes"], [65, 0x41D, 0x430])

    def test_define_text2_signed_advances_and_offsets(self):
        text = {
            "id": 30,
            "bounds": [-100, 200, -50, 300],
            "matrix_raw": encode_matrix({"translate": [2, -3]}),
            "gb": 9,
            "ab": 8,
            "records": [
                {
                    "flags": 0x8F,
                    "font_id": 6000,
                    "color": b"\x10\x20\x30\xff",
                    "x": -12,
                    "y": 25,
                    "height": 240,
                    "entries": [(257, -30), (10, 60)],
                }
            ],
        }
        encoded = build_text(text, 33)
        self.assertEqual(parse_text(encoded, 33), text)
        with self.assertRaises(ValueError):
            parse_text(encoded[:-1], 33)

    def test_compressed_swf_and_raw_tag_preservation(self):
        prefix = (
            b"FWS\x0a\x00\x00\x00\x00"
            + encode_rect([0, 100, 0, 100])
            + b"\x00\x18\x01\x00"
        )
        # Deliberately retain a long tag header for a short payload.
        opaque = Tag(9, b"\xff\x00\x11", b"\x7f\x02\x03\x00\x00\x00\xff\x00\x11")
        raw = write_swf(prefix, [opaque, Tag(0, b"")], 10)
        parsed_prefix, tags = read_swf(raw)
        self.assertEqual(tags[0], opaque)
        self.assertEqual(
            read_swf(write_swf(parsed_prefix, tags, 10)), (parsed_prefix, tags)
        )
        with self.assertRaises(ValueError):
            read_swf(raw[:-1])
        with self.assertRaises(ValueError):
            read_swf(raw + b"extra")


if __name__ == "__main__":
    unittest.main()
