"""End-to-end synthetic addressed localization; no game fixtures."""

import copy
import hashlib
import struct

import pytest

from addressed_localization import patch_selected_tags, extend_alignment_zones
from inventory_strings import extract_inventory
from localization_workspace import initialize_workspace
from swf_geometry import Tag, build_font, read_swf
from test_abc_inventory import abc
from test_localization_fonts import font_fixture
from test_string_inventory import swf


def fixture(source="A"):
    raw = swf(
        [
            Tag(75, build_font(font_fixture())),
            Tag(
                82,
                b"\0\0\0\0\0"
                + abc([source], [b"\xd0\x2c\1\x61\1\x47", b"\xd0\x2c\1\x61\1\x47"]),
            ),
            Tag(77, b"Unrelated synthetic metadata"),
        ]
    )
    digest = hashlib.sha256(raw).hexdigest()
    w = initialize_workspace(extract_inventory(raw, digest))
    e = next(e for e in w["entries"] if e["id"] == "abc.1.0.1")
    e.update(
        translation="\u0410",
        classification="confirmed",
        context_confidence="confirmed",
        status="draft",
    )
    plan = {
        "schema_version": 1,
        "game_sha256": digest,
        "entry_ids": [e["id"]],
        "fonts": [3],
        "layouts": {
            e["id"]: {"font_id": 3, "height": 240, "max_width": 50, "wrap": False}
        },
    }
    return raw, w, plan, {"3": {"\u0410": {"donor": "A"}}}, digest


def run(values):
    raw, w, plan, recipes, digest = values
    return patch_selected_tags(raw, w, plan, recipes, expected_sha256=digest)


def test_addressed_build_deterministic_and_unrelated_tags_preserved():
    values = fixture()
    prefix, tags, report = run(values)
    assert run(values) == (prefix, tags, report)
    _, old = read_swf(values[0])
    assert tags[2:] == old[2:]
    data = extract_inventory(swf(tags[:-1]), hashlib.sha256(swf(tags[:-1])).hexdigest())
    by_id = {e["id"]: e for e in data["entries"]}
    assert by_id["abc.1.0.1"]["source"] == "\u0410"
    assert by_id["abc.1.1.1"]["source"] == "A"
    assert report["applied_translations"] == 1


@pytest.mark.parametrize(
    "field",
    [
        "workspace_sha",
        "plan_sha",
        "duplicate",
        "address",
        "conflict",
        "unconfirmed",
        "missing_layout",
        "overflow",
        "missing_glyph",
    ],
)
def test_fail_closed(field):
    raw, w, p, r, sha = fixture()
    e = next(e for e in w["entries"] if e["id"] == p["entry_ids"][0])
    if field == "workspace_sha":
        w["game_sha256"] = "0" * 64
    elif field == "plan_sha":
        p["game_sha256"] = "0" * 64
    elif field == "duplicate":
        p["entry_ids"] *= 2
    elif field == "address":
        e["location"]["abc_byte_offset"] += 1
    elif field == "conflict":
        w["conflicts"] = [
            {
                "old": copy.deepcopy(e),
                "new": None,
                "reason": "removed",
                "old_game_sha256": sha,
            }
        ]
    elif field == "unconfirmed":
        e["classification"] = "probable"
    elif field == "missing_layout":
        p["layouts"] = {}
    elif field == "overflow":
        p["layouts"][e["id"]]["max_width"] = 0.5
    elif field == "missing_glyph":
        r = {"3": {}}
    with pytest.raises(ValueError):
        run((raw, w, p, r, sha))


def test_placeholder_failure_before_patching():
    raw, w, p, r, sha = fixture()
    e = next(e for e in w["entries"] if e["id"] == p["entry_ids"][0])
    e["mandatory_placeholders"] = ["A"]
    with pytest.raises(ValueError, match="placeholder"):
        run((raw, w, p, r, sha))


def test_zones_insert_disabled_records_preserving_original_zones():
    before = [65, 66, 8208]
    after = [65, 66, 1040, 8208]
    records = [b"\2" + bytes([i]) * 8 + b"\3" for i in range(3)]
    raw = struct.pack("<HB", 3, 0) + b"".join(records)
    result = extend_alignment_zones(raw, before, after)
    assert result == raw[:3] + records[0] + records[1] + b"\2" + b"\0" * 9 + records[2]
    with pytest.raises(ValueError):
        extend_alignment_zones(raw[:-1], before, after)


def test_layout_rejects_nonfinite_dimensions_and_vertical_ink_overflow():
    from addressed_localization import check_layout
    from localization_fonts import extend_font

    font = font_fixture()
    for field in ("max_width", "max_height"):
        layout = {"height": 240, "max_width": 50, "max_height": 20}
        layout[field] = float("nan")
        with pytest.raises(ValueError, match="dimensions"):
            check_layout(font, "A", layout)
    tall = extend_font(font, {"\u0410": {"donor": "A", "ty": -30000}})
    with pytest.raises(ValueError, match="vertical"):
        check_layout(tall, "\u0410", {"height": 240, "max_width": 50, "max_height": 20})


def test_introduced_control_sequences_and_html_layout_fail_closed():
    for case in ("control", "html"):
        values = fixture()
        _, workspace, plan, _, _ = values
        entry = next(e for e in workspace["entries"] if e["id"] == plan["entry_ids"][0])
        if case == "control":
            entry["translation"] += "\n"
            expected = "Control sequence"
        else:
            entry["content_type"] = "html_fragment"
            expected = "HTML rendering"
        with pytest.raises(ValueError, match=expected):
            run(values)


def test_layout_wraps_words_and_reserves_dynamic_value_width():
    from addressed_localization import check_layout

    font = font_fixture()
    font["codes"].insert(0, 32)
    font["glyphs"].insert(0, font["glyphs"][0])
    font["advances"].insert(0, 100)
    font["bounds"].insert(0, font["bounds"][0])
    result = check_layout(
        font,
        "AB AB",
        {
            "height": 2048,
            "max_width": 100,
            "dynamic_suffix_budget": 40,
            "wrap": True,
            "max_lines": 2,
        },
    )
    assert result["lines"] == 2
    assert result["available_width_twips"] == 60
    with pytest.raises(ValueError, match="width"):
        check_layout(
            font,
            "AB",
            {
                "height": 2048,
                "max_width": 50,
                "dynamic_suffix_budget": 10,
                "wrap": False,
            },
        )


def test_caption_requires_glyphs_and_layout_in_alternate_runtime_font_face():
    raw, _, _, _, _ = fixture()
    _, tags = read_swf(raw)
    alternate = font_fixture()
    alternate["id"] = 4
    # A plain-text assignment may select the other embedded style in a family.
    raw = swf([tags[0], Tag(75, build_font(alternate)), *tags[1:-1]])
    digest = hashlib.sha256(raw).hexdigest()
    workspace = initialize_workspace(extract_inventory(raw, digest))
    entry = next(e for e in workspace["entries"] if e["id"] == "abc.2.0.1")
    entry.update(
        translation="\u0410",
        classification="confirmed",
        context_confidence="confirmed",
        status="draft",
    )
    plan = {
        "schema_version": 1,
        "game_sha256": digest,
        "entry_ids": [entry["id"]],
        "fonts": [3, 4],
        "layouts": {
            entry["id"]: {
                "font_id": 3,
                "alternate_font_ids": [4],
                "height": 240,
                "max_width": 50,
                "wrap": False,
            }
        },
    }
    recipes = {"3": {"\u0410": {"donor": "A"}}, "4": {}}
    with pytest.raises(ValueError, match="Missing glyph"):
        patch_selected_tags(raw, workspace, plan, recipes, expected_sha256=digest)
    recipes["4"] = {"\u0410": {"donor": "A"}}
    _, _, report = patch_selected_tags(
        raw, workspace, plan, recipes, expected_sha256=digest
    )
    assert report["layout_checks"][entry["id"]]["alternate_faces"]["4"]["widths_twips"]
    recipes["4"]["\u0410"]["advance"] = 6000
    with pytest.raises(ValueError, match="field width"):
        patch_selected_tags(raw, workspace, plan, recipes, expected_sha256=digest)


def test_independent_face_validates_translation_without_changing_shared_font():
    from swf_geometry import parse_font

    raw, workspace, plan, _, digest = fixture()
    original_tag = read_swf(raw)[1][0]
    face = parse_font(original_tag.payload)
    face["id"] = 6001
    from localization_fonts import extend_font

    face = extend_font(face, {"\u0410": {"donor": "A"}})
    _, tags, report = patch_selected_tags(
        raw,
        workspace,
        plan,
        None,
        expected_sha256=digest,
        replacement_fonts={3: face},
    )
    assert tags[0].payload == original_tag.payload
    assert report["layout_checks"]
    assert report["changed_original_tags"] == [{"tag_index": 1, "tag_code": 82}]
    with pytest.raises(ValueError, match="Independent font"):
        patch_selected_tags(
            raw,
            workspace,
            plan,
            None,
            expected_sha256=digest,
            replacement_fonts={3: parse_font(original_tag.payload)},
        )


@pytest.mark.parametrize("alternate_ids", [None, [3], [4], [True], [3, 3]])
def test_alternate_runtime_font_selection_fails_closed(alternate_ids):
    values = fixture()
    plan = values[2]
    plan["layouts"][plan["entry_ids"][0]]["alternate_font_ids"] = alternate_ids
    with pytest.raises(ValueError, match="Alternate runtime faces"):
        run(values)


def suppression_fixture():
    raw_abc = abc(["A", "s", "B"], [
        b"\xd0\x2c\1\x61\1\x47", b"\x2c\2\x47", b"\xd0\x2c\3\x61\1\x47",
    ])
    assert raw_abc.count(b"\0\0\5\0") == 3
    raw_abc = raw_abc.replace(b"\0\0\5\0", b"\0\0\2\0")
    raw = swf([
        Tag(75, build_font(font_fixture())),
        Tag(82, b"\0\0\0\0\0" + raw_abc),
        Tag(77, b"Unrelated synthetic metadata"),
    ])
    digest = hashlib.sha256(raw).hexdigest()
    inventory = extract_inventory(raw, digest)
    suffix = "abc.1.1.0"
    original = {entry["id"]: entry for entry in inventory["entries"]}
    assert original[suffix]["classification"] == "technical"
    workspace = initialize_workspace(inventory)
    entry = next(item for item in workspace["entries"] if item["id"] == "abc.1.0.1")
    entry.update(translation="А", classification="confirmed",
                 context_confidence="confirmed", status="draft")
    plan = {
        "schema_version": 1,
        "game_sha256": digest,
        "entry_ids": [entry["id"]],
        "fonts": [3],
        "layouts": {
            entry["id"]: {"font_id": 3, "height": 240, "max_width": 50, "wrap": False}
        },
        "empty_abc_strings": [suffix],
    }
    return raw, workspace, plan, {"3": {"А": {"donor": "A"}}}, digest, suffix


def test_empty_abc_string_is_patched_only_at_guarded_technical_address():
    from abc_inventory import ABCInventory

    raw, workspace, plan, recipes, digest, suffix = suppression_fixture()
    _, tags, report = patch_selected_tags(
        raw, workspace, plan, recipes, expected_sha256=digest)
    assert report["applied_translations"] == 1
    assert report["empty_abc_strings"] == [suffix]
    original_payload = read_swf(raw)[1][1].payload
    original_data = original_payload[original_payload.index(b"\0", 4) + 1:]
    original_inventory = ABCInventory(original_data, {"tag_path": [1], "tag_code": 82})
    original_inventory.parse()
    payload = tags[1].payload
    data = payload[payload.index(b"\0", 4) + 1:]
    inventory = ABCInventory(data, {"tag_path": [1], "tag_code": 82})
    inventory.parse()
    assert inventory.strings[:len(original_inventory.strings)] == original_inventory.strings
    assert inventory.strings[-2:] == ["А", ""]
    operations = inventory.instructions(inventory.bodies[1][2], 1)
    assert any(opcode == 0x2c and inventory.strings[args[0]] == ""
               for _, opcode, args in operations)
    assert inventory.strings[2] == "s"


@pytest.mark.parametrize("bad", ["abc.1.9.0", "abc.1.0.1", "abc.1.2.1"])
def test_empty_abc_string_requires_existing_technical_address(bad):
    raw, workspace, plan, recipes, digest, _ = suppression_fixture()
    plan["empty_abc_strings"] = [bad]
    with pytest.raises(ValueError, match="empty ABC"):
        patch_selected_tags(raw, workspace, plan, recipes, expected_sha256=digest)


def test_empty_abc_string_rejects_duplicate_addresses():
    raw, workspace, plan, recipes, digest, suffix = suppression_fixture()
    plan["empty_abc_strings"] = [suffix, suffix]
    with pytest.raises(ValueError, match="empty ABC"):
        patch_selected_tags(raw, workspace, plan, recipes, expected_sha256=digest)
