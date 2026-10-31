"""Read-only ABC inventory, with bounded instruction decoding and no decompilation.

The caller removes the DoABC wrapper. Unsupported instructions terminate decoding
of their body; constant-pool strings remain available without invented uses.
Names and method labels in this result are private extracted data.
"""
import hashlib
import re
import struct


class ABCReader:
    def __init__(self, data):
        self.data = bytes(data)
        self.pos = 0

    def take(self, size):
        if size < 0 or size > len(self.data) - self.pos:
            raise ValueError("Truncated ABC structure")
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value

    def u8(self):
        return self.take(1)[0]

    def encoded(self, bits=30):
        value = 0
        for shift in range(0, 35, 7):
            byte = self.u8()
            value |= (byte & 127) << shift
            if not byte & 128:
                if value >= 1 << bits:
                    raise ValueError("ABC encoded integer overflow")
                return value
        raise ValueError("Invalid ABC encoded integer")

    def u30(self):
        return self.encoded()

    def s24(self):
        return int.from_bytes(self.take(3), "little", signed=True)

    def count(self):
        value = self.u30()
        if value > len(self.data) - self.pos:
            raise ValueError("Impossible ABC count")
        return value

    def finish(self):
        if self.pos != len(self.data):
            raise ValueError("Unexpected trailing ABC bytes")


# Standard AVM2 instructions only. VM-internal and FLOAT extension opcodes are
# intentionally absent. Unknown opcodes are never assumed to be zero-operand.
_NO_OPERANDS = {
    0x01, 0x02, 0x03, 0x07, 0x09, 0x1C, 0x1D, 0x1E, 0x1F,
    0x20, 0x21, 0x23, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x2B,
    0x30, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x3B, 0x3C,
    0x3D, 0x3E, 0x47, 0x48, 0x50, 0x51, 0x52, 0x57,
    0x64, 0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77,
    0x78, 0x81, 0x82, 0x83, 0x84, 0x85, 0x87, 0x88, 0x89, 0x90, 0x91, 0x93, 0x95,
    0x96, 0x97, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6,
    0xA7, 0xA8, 0xA9, 0xAA, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF,
    0xB0, 0xB1, 0xB3, 0xB4, 0xC0, 0xC1, 0xC4, 0xC5, 0xC6,
    0xC7, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xF3,
}
_OPERANDS = {opcode: () for opcode in _NO_OPERANDS}
_OPERANDS.update({opcode: ("s24",) for opcode in range(0x0C, 0x1B)})
for _opcode in (0x04, 0x05, 0x59, 0x5D, 0x5E, 0x5F, 0x60, 0x61,
                0x66, 0x68, 0x6A, 0x80, 0x86, 0xB2):
    _OPERANDS[_opcode] = ("multiname",)
for _opcode in (0x08, 0x41, 0x42, 0x49, 0x53, 0x55, 0x56,
                0x5A, 0x62, 0x63, 0x67, 0x6C, 0x6D, 0x6E, 0x6F,
                0x92, 0x94, 0xC2, 0xC3, 0xF0, 0xF2):
    _OPERANDS[_opcode] = ("u30",)
for _opcode in (0x45, 0x46, 0x4A, 0x4C, 0x4E, 0x4F):
    _OPERANDS[_opcode] = ("multiname", "u30")
_OPERANDS.update({
    0x06: ("string",), 0x24: ("u8",), 0x25: ("encoded32",), 0x2C: ("string",),
    0x2D: ("int",), 0x2E: ("uint",), 0x2F: ("double",),
    0x31: ("namespace",), 0x32: ("u30", "u30"),
    0x40: ("method",), 0x43: ("u30", "u30"),
    0x44: ("method", "u30"), 0x58: ("class",),
    0x65: ("u30",), 0xEF: ("u8", "string", "u8", "u30"),
    0xF1: ("string",),
})


class ABCInventory:
    def __init__(self, data, location):
        self.reader = ABCReader(data)
        self.location = dict(location)
        self.strings = [""]
        self.multinames = [None]
        self.symbols = {}
        self.method_labels = {}
        self.pool_sizes = {}
        self.entries = []
        self.used_strings = set()
        self.unsupported = []
        self.control_flow_anomalies = []
        self.bodies = []

    def index(self, value, size, label, allow_zero=True):
        if value >= size or (not allow_zero and not value):
            raise ValueError(f"Invalid ABC {label} index")
        return value

    def string(self, index, role=None):
        self.index(index, len(self.strings), "string")
        if role and index:
            self.symbols.setdefault(index, set()).add(role)
        return self.strings[index]

    def multiname(self, index):
        self.index(index, len(self.multinames), "multiname")
        item = self.multinames[index]
        return self.string(item.get("name", 0)) if item else ""

    def pools(self):
        r = self.reader
        for kind in ("int", "uint", "double"):
            count = r.count()
            self.pool_sizes[kind] = max(1, count)
            for _ in range(1, count):
                if kind == "double":
                    r.take(8)
                else:
                    r.encoded(32)
        for _ in range(1, r.count()):
            raw = r.take(r.u30())
            try:
                self.strings.append(raw.decode("utf-8"))
            except UnicodeDecodeError as error:
                raise ValueError("Invalid ABC UTF-8 string") from error
        self.pool_sizes["string"] = len(self.strings)
        ns_count = r.count()
        self.pool_sizes["namespace"] = max(1, ns_count)
        for _ in range(1, ns_count):
            if r.u8() not in (0x05, 0x08, 0x16, 0x17, 0x18, 0x19, 0x1A):
                raise ValueError("Unsupported ABC namespace kind")
            self.string(r.u30(), "namespace")
        sets = r.count()
        for _ in range(1, sets):
            for _ in range(r.count()):
                self.index(r.u30(), max(1, ns_count), "namespace", False)
        for _ in range(1, r.count()):
            kind = r.u8()
            item = {"kind": kind}
            if kind in (0x07, 0x0D):
                self.index(r.u30(), max(1, ns_count), "namespace", False)
                item["name"] = r.u30()
            elif kind in (0x0F, 0x10):
                item["name"] = r.u30()
            elif kind in (0x11, 0x12):
                pass
            elif kind in (0x09, 0x0E):
                item["name"] = r.u30()
                self.index(r.u30(), max(1, sets), "namespace-set", False)
            elif kind in (0x1B, 0x1C):
                self.index(r.u30(), max(1, sets), "namespace-set", False)
            elif kind == 0x1D:
                item["base"] = r.u30()
                item["parameters"] = [r.u30() for _ in range(r.count())]
                if not item["parameters"]:
                    raise ValueError("Empty ABC typename parameters")
            else:
                raise ValueError("Unsupported ABC multiname kind")
            if "name" in item:
                self.string(item["name"], "multiname")
            self.multinames.append(item)
        for item in self.multinames[1:]:
            if "base" in item:
                self.index(item["base"], len(self.multinames), "typename", False)
                for index in item["parameters"]:
                    self.index(index, len(self.multinames), "typename")
        self.pool_sizes["multiname"] = len(self.multinames)

    def constant(self, index, kind):
        pools = {0x03: "int", 0x04: "uint", 0x06: "double", 0x01: "string",
                 0x05: "namespace", 0x08: "namespace", 0x16: "namespace",
                 0x17: "namespace", 0x18: "namespace", 0x19: "namespace", 0x1A: "namespace"}
        if kind in pools:
            self.index(index, self.pool_sizes[pools[kind]], "constant")
        elif kind not in (0x00, 0x0A, 0x0B, 0x0C):
            raise ValueError("Unsupported ABC constant kind")

    def methods(self):
        r = self.reader
        self.method_count = r.count()
        self.pool_sizes["method"] = self.method_count
        for index in range(self.method_count):
            count = r.count()
            self.multiname(r.u30())
            for _ in range(count):
                self.multiname(r.u30())
            name = self.string(r.u30(), "method-name")
            if name:
                self.method_labels.setdefault(index, []).append({"method": name})
            flags = r.u8()
            if flags & ~0xEF:
                raise ValueError("Unsupported ABC method flags")
            if flags & 0x08:
                optional = r.count()
                if optional > count:
                    raise ValueError("Invalid ABC optional parameter count")
                for _ in range(optional):
                    self.constant(r.u30(), r.u8())
            if flags & 0x80:
                for _ in range(count):
                    self.string(r.u30(), "parameter-name")

    def traits(self, owner):
        r = self.reader
        for _ in range(r.count()):
            name = self.multiname(r.u30())
            flags = r.u8()
            if flags & 0x80:
                raise ValueError("Unsupported ABC trait attributes")
            kind = flags & 15
            r.u30()  # slot, dispatch ID or class slot ID
            if kind in (0, 6):
                self.multiname(r.u30())
                value = r.u30()
                if value:
                    self.constant(value, r.u8())
            elif kind in (1, 2, 3, 5):
                method = self.index(r.u30(), self.method_count, "trait method")
                self.method_labels.setdefault(method, []).append({"owner": owner, "method": name})
            elif kind == 4:
                self.index(r.u30(), self.class_count, "trait class")
            else:
                raise ValueError("Unsupported ABC trait kind")
            if flags & 0x40:
                for _ in range(r.count()):
                    self.index(r.u30(), self.metadata_count, "metadata")

    def structures(self):
        r = self.reader
        self.metadata_count = r.count()
        for _ in range(self.metadata_count):
            self.string(r.u30(), "metadata-name")
            count = r.count()
            for _ in range(count):
                self.string(r.u30(), "metadata-key")
            for _ in range(count):
                self.string(r.u30(), "metadata-value")
        self.class_count = r.count()
        self.pool_sizes["class"] = self.class_count
        owners = []
        for _ in range(self.class_count):
            owner = self.multiname(r.u30())
            owners.append(owner)
            self.multiname(r.u30())
            flags = r.u8()
            if flags & ~15:
                raise ValueError("Unsupported ABC instance flags")
            if flags & 8:
                self.index(r.u30(), self.pool_sizes["namespace"], "protected namespace", False)
            for _ in range(r.count()):
                self.multiname(r.u30())
            method = self.index(r.u30(), self.method_count, "instance initializer")
            self.method_labels.setdefault(method, []).append({"owner": owner, "method": "instance initializer"})
            self.traits(owner)
        for owner in owners:
            method = self.index(r.u30(), self.method_count, "class initializer")
            self.method_labels.setdefault(method, []).append({"owner": owner, "method": "class initializer"})
            self.traits(owner)
        for index in range(r.count()):
            owner = f"script {index}"
            method = self.index(r.u30(), self.method_count, "script initializer")
            self.method_labels.setdefault(method, []).append({"owner": owner, "method": "script initializer"})
            self.traits(owner)

    def instructions(self, code, method):
        r = ABCReader(code)
        instructions = []
        targets = []
        complete = True
        while r.pos < len(code):
            offset = r.pos
            opcode = r.u8()
            if opcode == 0x1B:
                default = r.s24()
                count = r.count()
                if count + 1 > (len(code) - r.pos) // 3:
                    raise ValueError("Truncated ABC lookupswitch")
                operands = [default, count, *[r.s24() for _ in range(count + 1)]]
            elif opcode in _OPERANDS:
                operands = []
                for kind in _OPERANDS[opcode]:
                    if kind == "u8":
                        value = r.u8()
                    elif kind == "s24":
                        value = r.s24()
                    elif kind == "encoded32":
                        # AVM2 pushshort permits sign-extended, five-byte values.
                        value = r.encoded(32)
                    else:
                        value = r.u30()
                    if kind in self.pool_sizes:
                        self.index(value, self.pool_sizes[kind], kind)
                    operands.append(value)
            else:
                self.unsupported.append({"method_index": method, "code_offset": offset, "opcode": opcode})
                complete = False
                break
            if opcode == 0x1B:
                targets.extend(offset + delta for delta in [operands[0], *operands[2:]])
            elif 0x0C <= opcode <= 0x1A:
                targets.append(r.pos + operands[0])
            if opcode in (0x06, 0xF1):
                self.string(operands[0], "namespace-literal" if opcode == 0x06 else "debug-filename")
            elif opcode == 0xEF:
                self.string(operands[1], "debug-name")
            instructions.append((offset, opcode, operands))
        boundaries = {offset for offset, _, _ in instructions}
        for target in targets:
            if not 0 <= target < len(code) or complete and target not in boundaries:
                # This inventory is not the AVM2 verifier: an unreachable branch
                # can target outside its body. Retain the evidence for analysis.
                self.control_flow_anomalies.append({"method_index": method, "target_offset": target})
        return instructions

    def entry(self, source_index, suffix, extra, classification, context):
        source = self.string(source_index)
        if not source:
            return
        path = ".".join(str(index) for index in self.location["tag_path"])
        location = {**self.location, "string_pool_index": source_index, **extra}
        self.entries.append({
            "id": f"abc.{path}.{suffix}", "category": "actionscript",
            "source": source, "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "location": location, "context": context,
            "context_confidence": "inferred" if extra.get("text_property") else "unknown",
            "classification": classification, "translation": None, "mandatory_placeholders": [],
        })

    def gem_color_offsets(self, instructions, method):
        # These display literals share pool indices with technical color symbols.
        # Promote only the reviewed table, never other uses of those indices.
        if {"owner": "IngameInfoPanelRenderer2", "method": "renderInfoPanelGem"} not in self.method_labels.get(method, []):
            return set()
        colors = ("orange", "yellow", "white", "red", "green", "cyan", "black", "blue", "purple")
        offsets = set()
        for position in range(len(instructions) - 10):
            table = instructions[position:position + 11]
            if (any(opcode != 0x2C for _, opcode, _ in table[:9])
                    or tuple(self.string(operands[0]) for _, _, operands in table[:9]) != colors
                    or table[9][1] != 0xF0
                    or table[10][1:] != (0x56, [9])):
                continue
            offsets.update(offset for offset, _, _ in table[:9])
        return offsets

    def body_entries(self, code, method, code_start):
        from achievement_category_binding import is_category_label_literal
        instructions = self.instructions(code, method)
        gem_colors = self.gem_color_offsets(instructions, method)
        building_captions = {}
        orblet_suffixes = {}
        if {"owner": "IngameInfoPanelRenderer", "method": "renderInfoPanel"} in self.method_labels.get(method, []):
            building_captions = {4239: ("Wall", 203), 5044: ("Tower", 203),
                                 5173: ("Tower", 203), 5261: ("Tower", 203),
                                 6477: ("Amplifier", 203), 7033: ("Trap", 203),
                                 15785: ("Tomb", 177)}
            orblet_suffixes = {14255: " docked: ", 14380: " docked",
                              14441: " can still be retrieved", 14538: " lost: -"}
        for position, (offset, opcode, operands) in enumerate(instructions):
            if opcode != 0x2C:
                continue
            index = operands[0]
            self.used_strings.add(index)
            source = self.string(index)
            classification = "unknown"
            context = "pushstring; display destination is not statically established"
            extra = {"method_index": method, "code_offset": offset,
                     "abc_byte_offset": code_start + offset,
                     "method_labels": self.method_labels.get(method, []),
                     "neighbor_instructions": [
                         {"code_offset": other_offset, "opcode": other_opcode, "operands": other_operands}
                         for other_offset, other_opcode, other_operands in instructions[max(0, position - 2):position + 4]
                     ]}
            if position + 1 < len(instructions):
                _, next_opcode, next_operands = instructions[position + 1]
                if next_opcode in (0x61, 0x68):
                    multiname_index = next_operands[0]
                    item = self.multinames[multiname_index]
                    name = self.multiname(multiname_index)
                    if item and item["kind"] == 0x07 and name in ("text", "htmlText"):
                        classification = "probable"
                        extra.update({"text_property": name, "property_multiname_index": multiname_index})
                        context = f"Literal assigned directly to {name}; receiver type and screen are unresolved"
            if not extra.get("text_property"):
                if re.search(r"\s", source) and re.search(r"[^\W\d_]", source) or re.search(r"<[A-Za-z][^>]*>", source):
                    classification = "probable"
                elif index in self.symbols:
                    classification = "technical"
            # A pool symbol can also be a caption at a different addressed use.
            # Recognize only the helper's literal tail, without crossing branches.
            tail = instructions[position + 1:position + 6]
            if (len(tail) >= 3 and tail[0][1] in (0x26, 0x27)
                    and tail[1][1] == 0x24):
                call = tail[2]
                count = 4
                if (len(tail) == 5 and tail[2][1] == 0x20
                        and tail[3][1] == 0x2D):
                    call, count = tail[4], 6
                if (call[1] == 0x4F and call[2][1] == count
                        and self.multiname(call[2][0]) == "addTextfield"):
                    classification = "probable"
                    context = "Literal passed to addTextfield; receiver and screen are unresolved"
            if offset in gem_colors:
                classification = "probable"
                context = "Reviewed gem color table in renderInfoPanelGem; runtime display is unverified"
            # These headings share symbols; other two-argument calls and
            # technical uses of the same pool entries remain outside this rule.
            caption, color = building_captions.get(offset, (None, None))
            if (source == caption and position >= 2
                    and [item[1:] for item in instructions[position - 2:position]] == [
                        (0xD1, []), (0x2D, [color])]
                    and tail and tail[0][1] == 0x4F and tail[0][2][1] == 2
                    and self.multiname(tail[0][2][0]) == "addTextfield"):
                classification = "probable"
                context = "Reviewed building caption in renderInfoPanel; runtime display is unverified"
            # Only the reviewed plural arms concatenate this symbol with a
            # visible state label; Orb's technical orblets property stays intact.
            if (source == "orblets" and offset in orblet_suffixes and len(tail) >= 4
                    and [item[1:] for item in tail[:2]] == [(0x82, []), (0xA0, [])]
                    and tail[2][1] == 0x2C and tail[3][1:] == (0xA0, [])
                    and self.string(tail[2][2][0]) == orblet_suffixes[offset]):
                classification = "probable"
                context = "Reviewed orblet count label in renderInfoPanel; runtime display is unverified"
            # Gem also names a class. Only these two reviewed pickup captions
            # feed the bitmap wrapper; keep every technical use excluded.
            if (source == "Gem" and offset in (438, 519)
                    and {"owner": "IngameController", "method": "pickUpDrop"}
                    in self.method_labels.get(method, [])):
                bitmap_tail = instructions[position + 1:position + 12]
                expected = [(0x2D, [342 if offset == 438 else 270]),
                            (0x24, [12]), (0x2C, None), (0x24, [0]),
                            (0x2F, [190]), (0x24, [0]), (0x2F, [11]),
                            (0x24, [18]), (0x24, [0]), (0x24, [10])]
                if (len(bitmap_tail) == 11
                        and all(op == wanted_op and (wanted_args is None or args == wanted_args)
                                for (_, op, args), (wanted_op, wanted_args)
                                in zip(bitmap_tail, expected))
                        and self.string(bitmap_tail[2][2][0]) == "center"
                        and bitmap_tail[10][1] == 0x4F
                        and bitmap_tail[10][2][1] == 13
                        and self.multiname(bitmap_tail[10][2][0]) == "createFloatingText"):
                    classification = "probable"
                    context = "Reviewed loot gem bitmap caption in pickUpDrop; runtime display is unverified"
            if is_category_label_literal(self, instructions, method, position):
                classification = "probable"
                context = ("Achievement category shared key; translate only the button display "
                           "through the guarded category-label adapter, never the key initializer")
            if (method == 139 and offset == 181 and source == 'w'
                    and {'owner': 'Achievement', 'method': 'instance initializer'}
                    in self.method_labels.get(method, []) and position >= 3
                    and [item[1:] for item in instructions[position - 3:position]] == [
                        (0xD0, []), (0x66, [105]), (0x66, [2923])]
                    and [item[1:] for item in tail[:3]] == [
                        (0xD3, []), (0xA0, []), (0x61, [5329])]
                    and self.multiname(105) == 'mc'
                    and self.multiname(2923) == 'tfWaveReq'
                    and self.multiname(5329) == 'text'):
                classification = 'probable'
                context = 'Reviewed achievement wave requirement prefix; number and empty branch preserved'
            self.entry(index, f"{method}.{offset}", extra, classification, context)
            if source and (extra.get("text_property") == "htmlText" or re.search(r"<[A-Za-z][^>]*>", source)):
                self.entries[-1]["content_type"] = "html_fragment"

    def parse(self):
        r = self.reader
        minor, major = struct.unpack("<HH", r.take(4))
        if (major, minor) != (46, 16):
            raise ValueError(f"Unsupported ABC version {major}.{minor}")
        self.pools()
        self.methods()
        self.structures()
        body_count = r.count()
        seen = set()
        for _ in range(body_count):
            method = self.index(r.u30(), self.method_count, "body method")
            if method in seen:
                raise ValueError("Duplicate ABC method body")
            seen.add(method)
            for _ in range(4):
                r.u30()  # stack, locals, initial and maximum scope depth
            code_length = r.u30()
            code_start = r.pos
            code = r.take(code_length)
            for _ in range(r.count()):
                start, end, target = r.u30(), r.u30(), r.u30()
                if not 0 <= start < end <= len(code) or target >= len(code):
                    raise ValueError("Invalid ABC exception range")
                self.multiname(r.u30())
                self.multiname(r.u30())
            self.traits(f"body {method}")
            self.bodies.append((method, code_start, code))
        r.finish()
        for method, code_start, code in self.bodies:
            self.body_entries(code, method, code_start)
        for index, source in enumerate(self.strings[1:], 1):
            if index in self.used_strings or not source:
                continue
            roles = sorted(self.symbols.get(index, []))
            self.entry(index, f"pool.{index}", {"symbol_roles": roles},
                       "technical" if roles else "unknown", "Constant-pool entry without a decoded pushstring use")
        return {"entries": self.entries, "coverage": {
            "abc_version": f"{major}.{minor}", "string_pool_entries": len(self.strings) - 1,
            "method_bodies": body_count, "decoded_method_bodies": body_count - len(self.unsupported),
            "instruction_coverage": "partial" if self.unsupported else "complete",
            "unsupported_instructions": self.unsupported,
            "control_flow_anomalies": self.control_flow_anomalies,
            "display_context": "partial; direct text/htmlText assignments only; no dataflow or runtime analysis",
        }}


def extract_abc(data, location):
    """Inventory raw ABC bytes; location must identify the containing SWF tag."""
    if not isinstance(location.get("tag_path"), list) or not location["tag_path"]:
        raise ValueError("ABC location requires a nonempty tag_path")
    if any(type(index) is not int or index < 0 for index in location["tag_path"]):
        raise ValueError("Invalid ABC tag_path")
    if location.get("tag_code") not in (72, 82):
        raise ValueError("ABC location requires tag_code 72 or 82")
    result = ABCInventory(data, location).parse()
    from advice_oracle import ADVICE_ID, unpack_advice_entry
    result['entries'] = [unpack_advice_entry(entry) if entry['id'] == ADVICE_ID else entry
                         for entry in result['entries']]
    return result
