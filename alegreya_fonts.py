"""Convert TrueType quadratic outlines to independent SWF DefineFont3 resources.

Coordinates use DefineFont3's 20 * 1024 units per em, with downward-positive y.
Layout uses hhea metrics, not OS/2 Win clipping metrics. Only horizontal pair
advance kerning is supported; no OpenType shaping or TrueType hinting is retained.
"""

from io import BytesIO
import unicodedata

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

from swf_geometry import build_font, build_shape, parse_font, shape_bounds

SWF_EM = 20480
WEIGHTS = (400, 700)


def _open_font(data):
    try:
        font = TTFont(BytesIO(data), lazy=False, recalcTimestamp=False)
        for table in ("head", "hhea", "hmtx", "maxp", "cmap", "glyf", "name", "OS/2"):
            if table not in font:
                raise ValueError(f"TrueType table {table} is required")
            font[
                table
            ]  # Decode eagerly so a broken table cannot hide until conversion.
        if not 16 <= font["head"].unitsPerEm <= 16384:
            raise ValueError("Invalid TrueType unitsPerEm")
        return font
    except Exception as error:
        raise ValueError(f"Invalid TrueType font: {error}") from error


def instantiate_face(ttf_bytes, weight):
    """Freeze wght at 400/700, rename derivatives, preserve OFL name records.

    Static input must already have the requested OS/2 weight. A variable input
    must expose only wght; accepting additional unfrozen axes would defeat the
    reproducible static-instance contract.
    """
    if weight not in WEIGHTS:
        raise ValueError("Only weights 400 and 700 are supported")
    font = _open_font(ttf_bytes)
    try:
        if "fvar" in font:
            axes = font["fvar"].axes
            if len(axes) != 1 or axes[0].axisTag != "wght":
                raise ValueError("Expected a variable TrueType with only the wght axis")
            if not axes[0].minValue <= weight <= axes[0].maxValue:
                raise ValueError("Requested weight is outside the wght axis")
            font = instantiateVariableFont(font, {"wght": weight}, inplace=True)
        elif font["OS/2"].usWeightClass != weight:
            raise ValueError("Static TrueType weight does not match requested weight")
        style = "Bold" if weight == 700 else "Regular"
        names = {
            1: "GCCS Alegreya",
            2: style,
            3: f"GCCS-Alegreya-{style}",
            4: f"GCCS Alegreya {style}",
            6: f"GCCSAlegreya-{style}",
            16: "GCCS Alegreya",
            17: style,
            18: f"GCCS Alegreya {style}",
            21: "GCCS Alegreya",
            22: style,
            25: "GCCSAlegreya",
        }
        # Explicit derivative names avoid reusing upstream reserved family names.
        # Copyright, licence and licence URL records remain untouched.
        for name_id, value in names.items():
            font["name"].removeNames(nameID=name_id)
            font["name"].setName(value, name_id, 3, 1, 0x409)
        font["OS/2"].usWeightClass = weight
        font["OS/2"].fsSelection &= ~((1 << 0) | (1 << 5) | (1 << 6))
        font["OS/2"].fsSelection |= 1 << (5 if weight == 700 else 6)
        font["head"].macStyle &= ~3
        font["head"].macStyle |= int(weight == 700)
        if "DSIG" in font:
            del font["DSIG"]
        font["head"].created = font["head"].modified = 2082844800
        font.recalcTimestamp = False
        output = BytesIO()
        font.save(output, reorderTables=True)
        return output.getvalue()
    except Exception as error:
        raise ValueError(
            f"Cannot instantiate TrueType weight {weight}: {error}"
        ) from error
    finally:
        font.close()


class _SWFPen(BasePen):
    def __init__(self, glyph_set, scale):
        super().__init__(glyph_set)
        self.scale = scale
        self.contours = []
        self.contour = None

    def _point(self, point):
        x, y = point
        return round(x * self.scale), round(-y * self.scale)

    def _moveTo(self, point):
        self.contour = [("M", *self._point(point))]
        self.contours.append(self.contour)

    def _lineTo(self, point):
        self.contour.append(("L", *self._point(point)))

    def _qCurveToOne(self, control, point):
        self.contour.append(("Q", *self._point(control), *self._point(point)))

    def _curveToOne(self, control1, control2, point):
        raise ValueError("Cubic outlines cannot be represented as TrueType quadratics")

    def _closePath(self):
        start = self.contour[0][1:]
        if self.contour[-1][-2:] != start:
            self.contour.append(("L", *start))
        self.contour = None

    def _endPath(self):
        raise ValueError("Open TrueType contour is unsupported")


def _pair_adjustment(value1, value2):
    for value in (value1, value2):
        if value is None:
            continue
        if any(
            getattr(value, field, 0)
            for field in (
                "XPlacement",
                "YPlacement",
                "YAdvance",
                "XPlaDevice",
                "YPlaDevice",
                "XAdvDevice",
                "YAdvDevice",
            )
        ):
            raise ValueError("GPOS positioning is not representable by SWF kerning")
    if value2 is not None and getattr(value2, "XAdvance", 0):
        raise ValueError("Second-glyph advance is not representable by SWF kerning")
    return getattr(value1, "XAdvance", 0) if value1 is not None else 0


def _kerning(font, code_names, scale):
    pairs = {}
    lookup_indices = set()
    if "GPOS" in font:
        table = font["GPOS"].table
        if table.FeatureList:
            for record in table.FeatureList.FeatureRecord:
                if record.FeatureTag == "kern":
                    lookup_indices.update(record.Feature.LookupListIndex)
        for index in sorted(lookup_indices):
            lookup = table.LookupList.Lookup[index]
            if lookup.LookupFlag & ~8:
                raise ValueError("Filtered GPOS kerning is not supported")
            if lookup.LookupFlag & 8:
                gdef = font["GDEF"].table if "GDEF" in font else None
                classes = (
                    gdef.GlyphClassDef.classDefs if gdef and gdef.GlyphClassDef else {}
                )
                if any(classes.get(name) == 3 for name in code_names.values()):
                    raise ValueError("Mark-skipping GPOS requires unsupported shaping")
            subtables = lookup.SubTable
            for code1, name1 in code_names.items():
                for code2, name2 in code_names.items():
                    for raw_table in subtables:
                        subtable = raw_table
                        lookup_type = lookup.LookupType
                        if lookup_type == 9:
                            lookup_type = subtable.ExtensionLookupType
                            subtable = subtable.ExtSubTable
                        if lookup_type != 2:
                            raise ValueError("Contextual GPOS kerning is not supported")
                        if name1 not in subtable.Coverage.glyphs:
                            continue
                        if subtable.Format == 1:
                            coverage_index = subtable.Coverage.glyphs.index(name1)
                            record = next(
                                (
                                    item
                                    for item in subtable.PairSet[
                                        coverage_index
                                    ].PairValueRecord
                                    if item.SecondGlyph == name2
                                ),
                                None,
                            )
                            if record is None:
                                continue
                        elif subtable.Format == 2:
                            class1 = subtable.ClassDef1.classDefs.get(name1, 0)
                            class2 = subtable.ClassDef2.classDefs.get(name2, 0)
                            record = subtable.Class1Record[class1].Class2Record[class2]
                        else:
                            raise ValueError("Unknown GPOS pair format")
                        adjustment = _pair_adjustment(record.Value1, record.Value2)
                        pair = (code1, code2)
                        pairs[pair] = pairs.get(pair, 0) + adjustment
                        # Alternative subtables in a lookup do not accumulate.
                        break
    if not lookup_indices and "kern" in font:
        name_codes = {}
        for code, name in code_names.items():
            name_codes.setdefault(name, []).append(code)
        for subtable in font["kern"].kernTables:
            if subtable.format != 0 or subtable.coverage != 1:
                raise ValueError("Only horizontal legacy kern format 0 is supported")
            for (left, right), adjustment in subtable.kernTable.items():
                for code1 in name_codes.get(left, []):
                    for code2 in name_codes.get(right, []):
                        pair = (code1, code2)
                        pairs[pair] = pairs.get(pair, 0) + adjustment
    result = [
        (code1, code2, round(value * scale))
        for (code1, code2), value in sorted(pairs.items())
        if round(value * scale)
    ]
    if len(result) > 65535 or any(
        not -32768 <= value <= 32767 for _, _, value in result
    ):
        raise ValueError("SWF kerning table exceeds representable limits")
    return result


def convert_font(ttf_bytes, weight, characters, font_id, family_name="GCCS Alegreya"):
    """Return a build_font-compatible DefineFont3 with a sorted BMP repertoire.

    Composite glyphs and implied quadratic on-curve points are decomposed by
    fontTools BasePen. Bounds conservatively include control points, matching
    existing SWF geometry checks; they never clip the actual quadratic curve.
    Blank glyphs are allowed only for Unicode space separators.
    """
    characters = set(characters)
    if not characters or any(
        not isinstance(char, str)
        or len(char) != 1
        or ord(char) > 0xFFFF
        or unicodedata.category(char).startswith("C")
        for char in characters
    ):
        raise ValueError("Repertoire must contain non-control BMP characters")
    if not isinstance(font_id, int) or not 1 <= font_id <= 65535:
        raise ValueError("Invalid SWF font ID")
    name = family_name.encode("utf-8")
    if not name or len(name) > 255:
        raise ValueError("SWF font family name must fit in 255 UTF-8 bytes")
    static_bytes = instantiate_face(ttf_bytes, weight)
    font = _open_font(static_bytes)
    try:
        cmap = font.getBestCmap()
        codes = sorted(map(ord, characters))
        if cmap is None or any(code not in cmap for code in codes):
            missing = [
                f"U+{code:04X}" for code in codes if cmap is None or code not in cmap
            ]
            raise ValueError(
                f"TrueType is missing requested characters: {', '.join(missing)}"
            )
        glyph_set = font.getGlyphSet()
        scale = SWF_EM / font["head"].unitsPerEm
        glyphs, advances, bounds = [], [], []
        for code in codes:
            pen = _SWFPen(glyph_set, scale)
            glyph_set[cmap[code]].draw(pen)
            if (
                not any(len(contour) > 2 for contour in pen.contours)
                and unicodedata.category(chr(code)) != "Zs"
            ):
                raise ValueError(f"Empty visible glyph U+{code:04X}")
            glyphs.append(build_shape(pen.contours))
            advance = round(font["hmtx"][cmap[code]][0] * scale)
            if not 0 <= advance <= 32767:
                raise ValueError(f"Unrepresentable advance U+{code:04X}")
            advances.append(advance)
            bounds.append(shape_bounds(pen.contours))
        hhea = font["hhea"]
        ascent, descent = round(hhea.ascent * scale), round(-hhea.descent * scale)
        leading = round(hhea.lineGap * scale)
        if not (
            0 <= ascent <= 65535
            and 0 <= descent <= 65535
            and -32768 <= leading <= 32767
        ):
            raise ValueError("Unrepresentable SWF vertical metrics")
        result = dict(
            id=font_id,
            flags=0x84 | int(weight == 700),
            language=0,
            name=name,
            codes=codes,
            glyphs=glyphs,
            advances=advances,
            bounds=bounds,
            ascent=ascent,
            descent=descent,
            leading=leading,
            kerning=_kerning(font, {code: cmap[code] for code in codes}, scale),
        )
        # Serialize now, checking every SWF field and returning actual offset flags.
        return parse_font(build_font(result))
    except Exception as error:
        raise ValueError(f"Cannot convert TrueType to DefineFont3: {error}") from error
    finally:
        font.close()
