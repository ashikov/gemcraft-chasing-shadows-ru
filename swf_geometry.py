"""Bounded SWF geometry reader/writer; importing this module performs no I/O.

Glyph coordinates are SWF font units (DefineFont3 has 20,480 units per em).
Unchanged glyph blocks and tags may be retained verbatim. Only simple glyph SHAPE
records are accepted: one implicit fill, no line styles or new style tables.
"""

from dataclasses import dataclass
import struct
import zlib


class Reader:
    def __init__(self, data):
        self.data, self.pos = bytes(data), 0

    def take(self, count):
        if count < 0 or self.pos + count > len(self.data):
            raise ValueError("Truncated SWF structure")
        result = self.data[self.pos : self.pos + count]
        self.pos += count
        return result

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return struct.unpack("<H", self.take(2))[0]

    def s16(self):
        return struct.unpack("<h", self.take(2))[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def finish(self):
        if self.pos != len(self.data):
            raise ValueError("Unexpected trailing bytes")


class Bits:
    def __init__(self, data):
        self.data, self.pos = bytes(data), 0

    def get(self, count):
        if count < 0 or self.pos + count > len(self.data) * 8:
            raise ValueError("Truncated bit structure")
        value = 0
        for _ in range(count):
            value = (value << 1) | (
                (self.data[self.pos // 8] >> (7 - self.pos % 8)) & 1
            )
            self.pos += 1
        return value

    def signed(self, count):
        value = self.get(count)
        return value - (1 << count) if count and value & (1 << (count - 1)) else value

    @property
    def size(self):
        return (self.pos + 7) // 8


class BitWriter:
    def __init__(self):
        self.bits = []

    def put(self, value, count):
        if value < 0 or value >= 1 << count:
            raise ValueError("Unsigned bit field overflow")
        self.bits.extend((value >> shift) & 1 for shift in range(count - 1, -1, -1))

    def signed(self, value, count):
        if count == 0:
            if value:
                raise ValueError("Zero-width signed field")
        elif not -(1 << (count - 1)) <= value < (1 << (count - 1)):
            raise ValueError("Signed bit field overflow")
        self.put(value & ((1 << count) - 1), count)

    def bytes(self):
        bits = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(
            sum(bits[i + j] << (7 - j) for j in range(8))
            for i in range(0, len(bits), 8)
        )


def signed_bits(values):
    return max(
        [1]
        + [
            (v.bit_length() + 1) if v >= 0 else ((-v - 1).bit_length() + 1)
            for v in values
        ]
    )


def _rect(bits):
    count = bits.get(5)
    return [bits.signed(count) for _ in range(4)]


def decode_rect(raw):
    bits = Bits(raw)
    return _rect(bits), bits.size


def encode_rect(rect):
    if len(rect) != 4:
        raise ValueError("RECT requires four coordinates")
    bits = BitWriter()
    count = signed_bits(rect)
    bits.put(count, 5)
    for value in rect:
        bits.signed(value, count)
    return bits.bytes()


def decode_matrix(raw):
    bits = Bits(raw)
    scale = [65536, 65536]
    rotate = [0, 0]
    if bits.get(1):
        count = bits.get(5)
        scale = [bits.signed(count), bits.signed(count)]
    if bits.get(1):
        count = bits.get(5)
        rotate = [bits.signed(count), bits.signed(count)]
    count = bits.get(5)
    translate = [bits.signed(count), bits.signed(count)]
    return {"scale": scale, "rotate": rotate, "translate": translate}, bits.size


def encode_matrix(matrix):
    bits = BitWriter()
    for values, default in (
        (matrix.get("scale", [65536, 65536]), [65536, 65536]),
        (matrix.get("rotate", [0, 0]), [0, 0]),
    ):
        present = values != default
        bits.put(int(present), 1)
        if present:
            count = signed_bits(values)
            bits.put(count, 5)
            for value in values:
                bits.signed(value, count)
    values = matrix.get("translate", [0, 0])
    count = signed_bits(values) if any(values) else 0
    bits.put(count, 5)
    for value in values:
        bits.signed(value, count)
    return bits.bytes()


@dataclass(frozen=True)
class Tag:
    code: int
    payload: bytes
    raw: bytes = b""


def read_tags(raw):
    reader, tags = Reader(raw), []
    while reader.pos < len(reader.data):
        start = reader.pos
        header = reader.u16()
        code, length = header >> 6, header & 63
        if length == 63:
            length = reader.u32()
        payload = reader.take(length)
        tags.append(Tag(code, payload, reader.data[start : reader.pos]))
        if code == 0:
            if payload:
                raise ValueError("Nonempty End tag")
            reader.finish()
            return tags
    raise ValueError("Missing End tag")


def read_swf(raw):
    if len(raw) < 8 or raw[:3] not in (b"CWS", b"FWS"):
        raise ValueError("Expected FWS or CWS")
    length = struct.unpack_from("<I", raw, 4)[0]
    if not 12 <= length <= 256 * 1024 * 1024:
        raise ValueError("SWF size outside bound")
    if raw[:3] == b"CWS":
        decoder = zlib.decompressobj()
        body = decoder.decompress(raw[8:], length - 7)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("Invalid compressed SWF stream")
    else:
        body = raw[8:]
    if len(body) != length - 8:
        raise ValueError("SWF length mismatch")
    _, size = decode_rect(body)
    prefix_size = size + 4
    if prefix_size > len(body):
        raise ValueError("Missing frame header")
    return b"FWS" + raw[3:8] + body[:prefix_size], read_tags(body[prefix_size:])


def write_swf(prefix, tags, version):
    if prefix[:3] not in (b"FWS", b"CWS"):
        raise ValueError("Invalid SWF prefix")
    chunks = []
    for tag in tags:
        if tag.raw:
            parsed = read_tags(tag.raw + (b"\0\0" if tag.code != 0 else b""))
            if parsed[0].code != tag.code or parsed[0].payload != tag.payload:
                raise ValueError("Tag raw disagrees with payload")
            chunks.append(tag.raw)
        else:
            length = len(tag.payload)
            chunks.append(struct.pack("<H", (tag.code << 6) | min(length, 63)))
            if length >= 63:
                chunks.append(struct.pack("<I", length))
            chunks.append(tag.payload)
    body = prefix[8:] + b"".join(chunks)
    read_tags(b"".join(chunks))
    return (
        b"CWS"
        + bytes([version])
        + struct.pack("<I", len(body) + 8)
        + zlib.compress(body)
    )


def parse_font(payload):
    r = Reader(payload)
    result = {"id": r.u16(), "flags": r.u8(), "language": r.u8()}
    result["name"] = r.take(r.u8())
    count = r.u16()
    base = r.pos
    wide = result["flags"] & 8
    get_offset = r.u32 if wide else r.u16
    offsets = [get_offset() for _ in range(count)]
    code_offset = get_offset()
    minimum = (count + 1) * (4 if wide else 2)
    edges = offsets + [code_offset]
    if (count and offsets[0] != minimum) or (not count and code_offset != minimum):
        raise ValueError("Unexpected font offset table gap")
    if edges != sorted(edges) or base + code_offset > len(payload):
        raise ValueError("Invalid font offsets")
    result["offsets"] = offsets
    result["code_offset"] = code_offset
    result["glyphs"] = [
        payload[base + edges[i] : base + edges[i + 1]] for i in range(count)
    ]
    r.pos = base + code_offset
    result["codes"] = [
        (r.u16() if result["flags"] & 4 else r.u8()) for _ in range(count)
    ]
    if result["codes"] != sorted(set(result["codes"])):
        raise ValueError("DefineFont3 codes must be unique and ascending")
    result.update(
        advances=[], bounds=[], kerning=[], ascent=None, descent=None, leading=None
    )
    if result["flags"] & 128:
        result.update(ascent=r.u16(), descent=r.u16(), leading=r.s16())
        result["advances"] = [r.s16() for _ in range(count)]
        for _ in range(count):
            rect, size = decode_rect(r.data[r.pos :])
            r.take(size)
            result["bounds"].append(rect)
        for _ in range(r.u16()):
            code1 = r.u16() if result["flags"] & 4 else r.u8()
            code2 = r.u16() if result["flags"] & 4 else r.u8()
            result["kerning"].append((code1, code2, r.s16()))
    r.finish()
    return result


def build_font(font):
    count, flags = len(font["glyphs"]), font["flags"]
    if count != len(font["codes"]) or count > 65535:
        raise ValueError("Glyph/code count mismatch")
    if font["codes"] != sorted(set(font["codes"])):
        raise ValueError("DefineFont3 codes must be unique and ascending")
    if any(code > 255 for code in font["codes"]):
        flags |= 4
    wide = bool(flags & 8)
    if (count + 1) * 2 + sum(map(len, font["glyphs"])) > 65535:
        wide = True
    if wide:
        flags |= 8
    offset = (count + 1) * (4 if wide else 2)
    offsets = []
    for glyph in font["glyphs"]:
        offsets.append(offset)
        offset += len(glyph)
    name = font["name"]
    out = bytearray(
        struct.pack("<HBBB", font["id"], flags, font["language"], len(name))
        + name
        + struct.pack("<H", count)
    )
    out += b"".join(
        struct.pack("<I" if wide else "<H", value) for value in offsets + [offset]
    )
    out += b"".join(font["glyphs"])
    out += b"".join(
        struct.pack("<H" if flags & 4 else "<B", code) for code in font["codes"]
    )
    if flags & 128:
        if len(font["advances"]) != count or len(font["bounds"]) != count:
            raise ValueError("Layout table count mismatch")
        out += struct.pack("<HHh", font["ascent"], font["descent"], font["leading"])
        out += b"".join(struct.pack("<h", value) for value in font["advances"])
        out += b"".join(encode_rect(rect) for rect in font["bounds"])
        out += struct.pack("<H", len(font["kerning"]))
        for code1, code2, adjustment in font["kerning"]:
            out += struct.pack(
                "<HHh" if flags & 4 else "<BBh", code1, code2, adjustment
            )
    return bytes(out)


def parse_shape(raw):
    bits = Bits(raw)
    fill_bits, line_bits = bits.get(4), bits.get(4)
    if fill_bits != 1 or line_bits != 0:
        raise ValueError("Unexpected glyph style widths")
    contours, contour, x, y = [], None, 0, 0
    while True:
        if bits.get(1):
            straight, count = bits.get(1), bits.get(4) + 2
            if contour is None:
                contour = [("M", x, y)]
                contours.append(contour)
            if straight:
                if bits.get(1):
                    dx, dy = bits.signed(count), bits.signed(count)
                elif bits.get(1):
                    dx, dy = 0, bits.signed(count)
                else:
                    dx, dy = bits.signed(count), 0
                x, y = x + dx, y + dy
                contour.append(("L", x, y))
            else:
                cx, cy = x + bits.signed(count), y + bits.signed(count)
                x, y = cx + bits.signed(count), cy + bits.signed(count)
                contour.append(("Q", cx, cy, x, y))
        else:
            flags = bits.get(5)
            if not flags:
                break
            if flags & 16:
                raise ValueError("New glyph styles are unsupported")
            if flags & 1:
                count = bits.get(5)
                x, y = bits.signed(count), bits.signed(count)
                contour = [("M", x, y)]
                contours.append(contour)
            if flags & 2:
                if bits.get(fill_bits) not in (0, 1):
                    raise ValueError("Unexpected fill0")
            if flags & 4:
                if bits.get(fill_bits) not in (0, 1):
                    raise ValueError("Unexpected fill1")
            if flags & 8:
                raise ValueError("Glyph line style is unsupported")
    if bits.size != len(raw) or bits.get(len(raw) * 8 - bits.pos):
        raise ValueError("Unexpected glyph trailing bits")
    return contours


def build_shape(contours):
    bits = BitWriter()
    bits.put(1, 4)
    bits.put(0, 4)
    x = y = 0
    for contour in contours:
        if not contour or contour[0][0] != "M":
            raise ValueError("Contour must start with M")
        _, x, y = contour[0]
        count = signed_bits([x, y])
        bits.put(0, 1)
        bits.put(3, 5)  # MoveTo + FillStyle0
        bits.put(count, 5)
        bits.signed(x, count)
        bits.signed(y, count)
        bits.put(1, 1)
        for segment in contour[1:]:
            if segment[0] == "L":
                nx, ny = segment[1:]
                values = [nx - x, ny - y]
                count = max(2, signed_bits(values))
                if count > 17:
                    raise ValueError("Glyph edge exceeds SWF delta limit")
                bits.put(1, 1)
                bits.put(1, 1)
                bits.put(count - 2, 4)
                bits.put(1, 1)
            elif segment[0] == "Q":
                cx, cy, nx, ny = segment[1:]
                values = [cx - x, cy - y, nx - cx, ny - cy]
                count = max(2, signed_bits(values))
                if count > 17:
                    raise ValueError("Glyph curve exceeds SWF delta limit")
                bits.put(1, 1)
                bits.put(0, 1)
                bits.put(count - 2, 4)
            else:
                raise ValueError("Unsupported glyph segment")
            for value in values:
                bits.signed(value, count)
            x, y = nx, ny
    bits.put(0, 6)
    return bits.bytes()


def shape_bounds(contours):
    points = [
        (segment[i], segment[i + 1])
        for contour in contours
        for segment in contour
        for i in range(1, len(segment), 2)
    ]
    if not points:
        return [0, 0, 0, 0]
    xs, ys = zip(*points)
    return [min(xs), max(xs), min(ys), max(ys)]


def parse_text(payload, tagcode=11):
    if tagcode not in (11, 33):
        raise ValueError("Expected DefineText or DefineText2")
    r = Reader(payload)
    result = {"id": r.u16()}
    result["bounds"], size = decode_rect(r.data[r.pos :])
    r.take(size)
    _, size = decode_matrix(r.data[r.pos :])
    result["matrix_raw"] = r.take(size)
    result["gb"], result["ab"] = r.u8(), r.u8()
    result["records"] = []
    if result["gb"] > 32 or result["ab"] > 32:
        raise ValueError("Excessive text bit width")
    while True:
        flags = r.u8()
        if not flags:
            break
        if flags & 0x70 or not flags & 0x80:
            raise ValueError("Invalid TEXTRECORD flags")
        record = {"flags": flags}
        if flags & 8:
            record["font_id"] = r.u16()
        if flags & 4:
            record["color"] = r.take(4 if tagcode == 33 else 3)
        if flags & 1:
            record["x"] = r.s16()
        if flags & 2:
            record["y"] = r.s16()
        if flags & 8:
            record["height"] = r.u16()
        count = r.u8()
        bits = Bits(r.data[r.pos :])
        record["entries"] = [
            (bits.get(result["gb"]), bits.signed(result["ab"])) for _ in range(count)
        ]
        r.take(bits.size)
        result["records"].append(record)
    r.finish()
    return result


def build_text(text, tagcode=11):
    if tagcode not in (11, 33):
        raise ValueError("Expected DefineText or DefineText2")
    gb, ab = text["gb"], text["ab"]
    if not 0 <= gb <= 32 or not 0 <= ab <= 32:
        raise ValueError("Invalid text bit width")
    out = bytearray(
        struct.pack("<H", text["id"])
        + encode_rect(text["bounds"])
        + text["matrix_raw"]
        + bytes([gb, ab])
    )
    for record in text["records"]:
        flags = record["flags"]
        if flags & 0x70 or not flags & 0x80:
            raise ValueError("Invalid TEXTRECORD flags")
        out.append(flags)
        if flags & 8:
            out += struct.pack("<H", record["font_id"])
        if flags & 4:
            if len(record["color"]) != (4 if tagcode == 33 else 3):
                raise ValueError("Invalid text color")
            out += record["color"]
        if flags & 1:
            out += struct.pack("<h", record["x"])
        if flags & 2:
            out += struct.pack("<h", record["y"])
        if flags & 8:
            out += struct.pack("<H", record["height"])
        if len(record["entries"]) > 255:
            raise ValueError("Too many text glyphs in one record")
        out.append(len(record["entries"]))
        bits = BitWriter()
        for index, advance in record["entries"]:
            bits.put(index, gb)
            bits.signed(advance, ab)
        out += bits.bytes()
    out.append(0)
    return bytes(out)
