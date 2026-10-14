"""Extend embedded DefineFont3 fonts using recipes from private local JSON.

Recipes contain donor characters, affine parts, optional additional contours and
an advance in font units. This module contains no game-specific glyph recipes.
Original glyph blocks and layout are retained by character code. Sorted insertion
may shift glyph indices: callers must audit/remap existing static TEXTRECORDs.
Ink bounds are conservative control-point bounds, not a runtime rendering proof.
"""

import copy
import math
import struct

from build_prototype import EM, transform
from swf_geometry import build_font, build_shape, parse_font, parse_shape, shape_bounds


def _number(value, label):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"Invalid {label}")
    return value


def _contours(value):
    if not isinstance(value, list):
        raise ValueError("Contours must be a list")
    result = []
    for contour in value:
        if not isinstance(contour, list) or not contour:
            raise ValueError("Invalid contour")
        converted = []
        for index, segment in enumerate(contour):
            if not isinstance(segment, (list, tuple)) or not segment:
                raise ValueError("Invalid contour segment")
            kind = segment[0]
            if kind not in {"M", "L", "Q"} or len(segment) != (5 if kind == "Q" else 3):
                raise ValueError("Unsupported contour segment")
            if (index == 0) != (kind == "M"):
                raise ValueError("Contour must start with exactly one move")
            if any(isinstance(n, bool) or not isinstance(n, int) for n in segment[1:]):
                raise ValueError("Contour coordinates must be integers")
            converted.append(tuple(segment))
        result.append(converted)
    return result


def _layout(source):
    if not source["flags"] & 128:
        raise ValueError("Font must include layout metrics")
    # The codec validates sorted codes, signed metrics and table lengths.
    try:
        return parse_font(build_font(source))
    except (KeyError, TypeError, OverflowError, struct.error) as exc:
        raise ValueError("Invalid font layout") from exc


def extend_font(source, recipes):
    """Return a new font; never mutate or replace existing character outlines.

    A recipe may use ``donor`` plus sx/sy/tx/ty, or ``parts`` with those same
    fields. ``contours`` adds locally authored outlines. Composite/custom recipes
    require an explicit advance; a single donor inherits its advance by default.
    Unicode BMP characters are supported, matching DefineFont3's UI16 code table.
    """
    font = copy.deepcopy(source)
    _layout(font)
    if not isinstance(recipes, dict):
        raise ValueError("Font recipes must be a character mapping")
    original = {code: index for index, code in enumerate(source["codes"])}
    additions = {}
    for character, recipe in recipes.items():
        if not isinstance(character, str) or len(character) != 1:
            raise ValueError("Recipe key must be one character")
        code = ord(character)
        if code in original or code > 65535 or 0xD800 <= code <= 0xDFFF:
            raise ValueError("Recipe cannot replace existing or unsupported character")
        if not isinstance(recipe, dict) or set(recipe) - {
            "donor",
            "sx",
            "sy",
            "tx",
            "ty",
            "parts",
            "contours",
            "advance",
        }:
            raise ValueError("Invalid glyph recipe fields")
        if "parts" in recipe:
            if any(key in recipe for key in ("donor", "sx", "sy", "tx", "ty")):
                raise ValueError("Choose donor or parts")
            parts = recipe["parts"]
            if not isinstance(parts, list):
                raise ValueError("Parts must be a list")
        elif "donor" in recipe:
            parts = [
                {
                    key: value
                    for key, value in recipe.items()
                    if key not in {"advance", "contours"}
                }
            ]
        else:
            if any(key in recipe for key in ("sx", "sy", "tx", "ty")):
                raise ValueError("Transforms require a donor")
            parts = []
        contours, donor_index = [], None
        raw_copy = None
        for part in parts:
            if not isinstance(part, dict) or set(part) - {
                "donor",
                "sx",
                "sy",
                "tx",
                "ty",
            }:
                raise ValueError("Invalid donor part")
            donor = part.get("donor")
            if (
                not isinstance(donor, str)
                or len(donor) != 1
                or ord(donor) not in original
            ):
                raise ValueError(f"Missing donor glyph: {donor!r}")
            donor_index = original[ord(donor)]
            raw = source["glyphs"][donor_index]
            affine = {
                key: _number(part.get(key, default), key)
                for key, default in (("sx", 1), ("sy", 1), ("tx", 0), ("ty", 0))
            }
            contours.extend(transform(parse_shape(raw), **affine))
            if len(parts) == 1 and affine == {"sx": 1, "sy": 1, "tx": 0, "ty": 0}:
                raw_copy = raw
        if "contours" in recipe:
            contours.extend(_contours(recipe["contours"]))
            raw_copy = None
        if not parts and "contours" not in recipe:
            raise ValueError("Recipe needs donor parts or contours")
        advance = recipe.get("advance")
        if advance is None and len(parts) == 1:
            advance = source["advances"][donor_index]
        if (
            isinstance(advance, bool)
            or not isinstance(advance, int)
            or not 0 <= advance <= 32767
        ):
            raise ValueError("Invalid glyph advance")
        raw = raw_copy if raw_copy is not None else build_shape(contours)
        if parse_shape(raw) != contours:
            raise ValueError("Glyph outline roundtrip mismatch")
        bounds = (
            source["bounds"][donor_index]
            if raw_copy is not None
            else shape_bounds(contours)
        )
        additions[code] = (raw, advance, bounds)
    font["codes"] = sorted(set(original) | set(additions))
    for field in ("glyphs", "advances", "bounds"):
        position = ("glyphs", "advances", "bounds").index(field)
        font[field] = [
            copy.deepcopy(source[field][original[code]])
            if code in original
            else additions[code][position]
            for code in font["codes"]
        ]
    # Return actual encoded flags, including wide-code/offset promotion if needed.
    return _layout(font)


def measure_text(font, text, height):
    """Measure plain text in TWIPS at a DefineFont3 text height in TWIPS.

    Each line's bounds are relative to the first line's baseline, or None when
    there is no ink. Newlines advance by ascent+descent+leading. This does not
    interpret HTML or model Flash field wrapping; callers must provide plain text.
    """
    _layout(font)
    if not isinstance(text, str) or _number(height, "text height") <= 0:
        raise ValueError("Invalid text or height")
    scale = height / EM
    indices = {code: index for index, code in enumerate(font["codes"])}
    kern = {(a, b): adjustment for a, b, adjustment in font["kerning"]}
    line_height = (font["ascent"] + font["descent"] + font["leading"]) * scale
    widths, bounds = [], []
    for line_number, line in enumerate(text.split("\n")):
        cursor, ink, previous = 0, None, None
        baseline = line_number * line_height
        for character in line:
            code = ord(character)
            if code not in indices:
                raise ValueError(f"Missing glyph U+{code:04X}")
            index = indices[code]
            cursor += kern.get((previous, code), 0) * scale
            if parse_shape(font["glyphs"][index]):
                xmin, xmax, ymin, ymax = font["bounds"][index]
                box = [
                    cursor + xmin * scale,
                    cursor + xmax * scale,
                    baseline + ymin * scale,
                    baseline + ymax * scale,
                ]
                ink = (
                    box
                    if ink is None
                    else [
                        min(ink[0], box[0]),
                        max(ink[1], box[1]),
                        min(ink[2], box[2]),
                        max(ink[3], box[3]),
                    ]
                )
            cursor += font["advances"][index] * scale
            previous = code
        widths.append(cursor)
        bounds.append(ink)
    return {"widths": widths, "bounds": bounds, "line_height": line_height}
