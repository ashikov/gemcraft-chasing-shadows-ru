"""Synthetic private-catalog recovery and loss-prevention contracts."""
import copy
import json

import pytest

from install_patch import ORIGINAL_SHA256
from localization_workspace import initialize_workspace, write_json
from translation_catalog import source_hash
from versioned_catalog import (
    catalog_digest, check_sync, export_catalog, main, restore_workspace, validate_catalog_data,
)


def inventory():
    entries = []
    for index in range(4):
        source = f"<b>Invented {index} {{n}}</b>"
        entries.append({
            "id": f"abc.7.1.{index}", "category": "html", "source": source,
            "source_sha256": source_hash(source),
            "location": {"tag_path": [7], "method_index": 1, "code_offset": index,
                         "string_pool_index": index + 1, "class_name": "PrivateClass"},
            "context": "Extracted context", "context_confidence": "unknown",
            "classification": "probable", "translation": None,
            "mandatory_placeholders": ["{n}"],
            "neighbor_instructions": [{"opcode": 44, "operands": [index]}],
        })
    return {"schema_version": 1, "game_sha256": ORIGINAL_SHA256, "entries": entries}


def manual_workspace(data):
    result = initialize_workspace(data)
    for index, status in enumerate(("draft", "verified", "needs_review", "untranslated")):
        result["entries"][index].update(
            translation=f"<b>Ёж №{index} {{n}}</b>" if index < 3 else None,
            status=status, notes=f"Редакторская заметка {index}", context="Ручной контекст",
            context_confidence="confirmed", classification="confirmed",
        )
    return result


def test_roundtrip_all_manual_fields_and_statuses_without_machine_content():
    data = inventory()
    workspace = manual_workspace(data)
    catalog = export_catalog(workspace, data)
    assert validate_catalog_data(catalog) == []
    assert restore_workspace(catalog, data) == workspace
    assert check_sync(workspace, data, catalog) == []
    serialized = json.dumps(catalog, ensure_ascii=False)
    for forbidden in ("Invented", "PrivateClass", "Extracted context", "neighbor_instructions",
                      '"source"', '"opcode"'):
        assert forbidden not in serialized
    assert export_catalog(restore_workspace(catalog, data), data, previous=catalog,
                          expected_previous_sha256=catalog_digest(catalog)) == catalog


def test_determinism_sparse_manual_data_and_exclusive_cli(tmp_path):
    data = inventory()
    workspace = initialize_workspace(data)
    workspace["entries"][1]["notes"] = "Заметка"
    catalog = export_catalog(workspace, data)
    assert len(catalog["entries"]) == 1
    assert catalog["entries"][0]["manual"] == {"notes": "Заметка"}
    shuffled = copy.deepcopy(workspace)
    shuffled["entries"].reverse()
    assert export_catalog(shuffled, data) == catalog
    for name, value in (("inventory", data), ("workspace", workspace), ("catalog", catalog)):
        write_json(tmp_path / f"{name}.json", value)
    output = tmp_path / "restored.json"
    assert main(["restore", "--inventory", str(tmp_path / "inventory.json"),
                 "--catalog", str(tmp_path / "catalog.json"), "--output", str(output)]) == 0
    assert json.loads(output.read_text()) == workspace
    with pytest.raises(SystemExit):
        main(["restore", "--inventory", str(tmp_path / "inventory.json"),
              "--catalog", str(tmp_path / "catalog.json"), "--output", str(output)])


@pytest.mark.parametrize("field,value", [
    ("source_sha256", "f" * 64), ("category", "static"),
    ("location", {"tag_path": [7], "method_index": 1, "code_offset": 99}),
    ("id", "missing"),
])
def test_restore_rejects_changed_origin(field, value):
    data = inventory()
    catalog = export_catalog(manual_workspace(data), data)
    catalog["entries"][0][field] = value
    with pytest.raises(ValueError, match="origin|missing"):
        restore_workspace(catalog, data)


@pytest.mark.parametrize("target", ["catalog", "inventory", "workspace"])
def test_unknown_game_fails_closed(target):
    data = inventory()
    workspace = manual_workspace(data)
    catalog = export_catalog(workspace, data)
    {"catalog": catalog, "inventory": data, "workspace": workspace}[target]["game_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="game|SHA"):
        if target == "catalog":
            restore_workspace(catalog, data)
        else:
            export_catalog(workspace, data)


@pytest.mark.parametrize("duplicate", ["id", "address"])
def test_duplicate_ids_and_binding_addresses_rejected(duplicate):
    data = inventory()
    catalog = export_catalog(manual_workspace(data), data)
    other = copy.deepcopy(catalog["entries"][0])
    if duplicate == "address":
        other["id"] = "other-id"
        other["location"]["string_pool_index"] = 999
    catalog["entries"].append(other)
    assert any("duplicate" in error for error in validate_catalog_data(catalog))
    with pytest.raises(ValueError, match="duplicate"):
        restore_workspace(catalog, data)


@pytest.mark.parametrize("mutation", ["removed", "null", "empty"])
def test_reexport_rejects_unacknowledged_loss_and_allows_explicit_id(mutation):
    data = inventory()
    for entry in data["entries"]:
        entry["mandatory_placeholders"] = []
    workspace = manual_workspace(data)
    catalog = export_catalog(workspace, data)
    identifier = workspace["entries"][0]["id"]
    if mutation == "removed":
        workspace["entries"][0] = initialize_workspace(data)["entries"][0]
    else:
        workspace["entries"][0]["translation"] = None if mutation == "null" else ""
    with pytest.raises(ValueError, match=identifier):
        export_catalog(workspace, data, previous=catalog,
                       expected_previous_sha256=catalog_digest(catalog))
    result = export_catalog(workspace, data, previous=catalog, allow_removals=[identifier],
                            expected_previous_sha256=catalog_digest(catalog))
    assert restore_workspace(result, data) == workspace


def test_empty_translation_distinct_from_null_and_changed_placeholders_roundtrip():
    data = inventory()
    data["entries"][0]["mandatory_placeholders"] = []
    workspace = initialize_workspace(data)
    workspace["entries"][0].update(translation="", status="verified")
    workspace["entries"][1]["mandatory_placeholders"] = []
    catalog = export_catalog(workspace, data)
    restored = restore_workspace(catalog, data)
    assert restored == workspace
    assert restored["entries"][0]["translation"] == ""
    assert restored["entries"][1]["translation"] is None


def test_conflicts_invalid_status_and_modified_machine_data_fail_closed():
    data = inventory()
    workspace = manual_workspace(data)
    workspace["conflicts"] = [{"old": workspace["entries"][0]}]
    with pytest.raises(ValueError, match="conflict"):
        export_catalog(workspace, data)
    workspace["conflicts"] = []
    workspace["entries"][0]["status"] = "ready"
    with pytest.raises(ValueError, match="status"):
        export_catalog(workspace, data)
    workspace["entries"][0]["status"] = "draft"
    workspace["entries"][0]["neighbor_instructions"] = []
    with pytest.raises(ValueError, match="machine|immutable"):
        export_catalog(workspace, data)


@pytest.mark.parametrize("where,key,value", [
    ("root", "source", "Original"), ("entry", "neighbor_instructions", []),
    ("manual", "source", "Original"), ("manual", "status", "ready"),
    ("manual", "translation", 3), ("location", "class_name", "Private"),
    ("location", "code_offset", True), ("manual", "notes", "\ud800"),
])
def test_standalone_validation_rejects_injected_fields_or_invalid_types(where, key, value):
    data = inventory()
    catalog = export_catalog(manual_workspace(data), data)
    targets = {"root": catalog, "entry": catalog["entries"][0],
               "manual": catalog["entries"][0]["manual"],
               "location": catalog["entries"][0]["location"]}
    targets[where][key] = value
    assert validate_catalog_data(catalog)


def test_check_sync_reports_precise_ids_without_mutation():
    data = inventory()
    workspace = manual_workspace(data)
    catalog = export_catalog(workspace, data)
    workspace["entries"][2]["notes"] = "Изменение"
    before = copy.deepcopy(workspace)
    errors = check_sync(workspace, data, catalog)
    assert len(errors) == 1 and workspace["entries"][2]["id"] in errors[0]
    assert workspace == before


def test_bad_placeholder_and_html_fail_on_restore():
    data = inventory()
    catalog = export_catalog(manual_workspace(data), data)
    catalog["entries"][0]["manual"]["translation"] = "<b>Потерян токен</b>"
    with pytest.raises(ValueError, match="placeholder"):
        restore_workspace(catalog, data)
    catalog["entries"][0]["manual"]["translation"] = "<b>{n}"
    assert any("HTML" in error for error in validate_catalog_data(catalog))


def test_check_sync_reports_deleted_workspace_ids_and_invalid_status_ids():
    data = inventory()
    workspace = manual_workspace(data)
    catalog = export_catalog(workspace, data)
    missing = workspace["entries"].pop()["id"]
    assert any(missing in error for error in check_sync(workspace, data, catalog))
    workspace = manual_workspace(data)
    workspace["entries"][0]["status"] = "ready"
    assert any(workspace["entries"][0]["id"] in error
               for error in check_sync(workspace, data, catalog))


def test_export_rejects_inconsistent_source_sha_and_removed_origin():
    data = inventory()
    workspace = manual_workspace(data)
    workspace["entries"][0]["source_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="source hash|origin"):
        export_catalog(workspace, data)
    catalog = export_catalog(manual_workspace(data), data)
    data["entries"].pop(0)
    with pytest.raises(ValueError, match="missing"):
        restore_workspace(catalog, data)


def test_export_serializes_identical_bytes_in_any_input_order(tmp_path):
    data = inventory()
    workspace = manual_workspace(data)
    first = export_catalog(workspace, data)
    workspace["entries"].reverse()
    data["entries"].reverse()
    second = export_catalog(workspace, data)
    one, two = tmp_path / "one.json", tmp_path / "two.json"
    write_json(one, first)
    write_json(two, second)
    assert one.read_bytes() == two.read_bytes()


def test_source_references_in_manual_context_and_notes_are_lossless_and_source_free():
    data = inventory()
    source = "A sufficiently long invented original source {n}"
    data["entries"][0].update(source=source, source_sha256=source_hash(source))
    workspace = manual_workspace(data)
    workspace["entries"][0].update(
        context=f"Перед {source} после {source}", notes=f"{source}: заметка",
    )
    catalog = export_catalog(workspace, data)
    manual = catalog["entries"][0]["manual"]
    reference = {"id": data["entries"][0]["id"], "source_sha256": source_hash(source)}
    assert manual["context"] == {"source_parts": ["Перед ", reference, " после ", reference, ""]}
    assert manual["notes"] == {"source_parts": ["", reference, ": заметка"]}
    assert source not in json.dumps(catalog, ensure_ascii=False)
    assert restore_workspace(catalog, data) == workspace
    assert export_catalog(restore_workspace(catalog, data), data, previous=catalog,
                          expected_previous_sha256=catalog_digest(catalog)) == catalog
    assert check_sync(workspace, data, catalog) == []


@pytest.mark.parametrize("template", [
    {}, {"source_parts": []}, {"source_parts": ["one"]},
    {"source_parts": ["one", 2]}, {"source_parts": "not a list"},
    {"source_parts": ["", ""], "source": "injected"},
])
@pytest.mark.parametrize("field", ["context", "notes"])
def test_malformed_source_reference_rejected(template, field):
    data = inventory()
    catalog = export_catalog(manual_workspace(data), data)
    catalog["entries"][0]["manual"][field] = template
    assert validate_catalog_data(catalog)
    with pytest.raises(ValueError, match=field):
        restore_workspace(catalog, data)


def test_reexport_requires_bound_baseline_even_for_same_ids():
    data = inventory()
    workspace = manual_workspace(data)
    catalog = export_catalog(workspace, data)
    with pytest.raises(ValueError, match="baseline"):
        export_catalog(workspace, data, previous=catalog)


def test_catalog_digest_matches_written_bytes_and_stale_workspace_is_rejected(tmp_path):
    import hashlib

    data = inventory()
    workspace_a = manual_workspace(data)
    catalog_a = export_catalog(workspace_a, data)
    path = tmp_path / "catalog.json"
    write_json(path, catalog_a)
    receipt_a = catalog_digest(catalog_a)
    assert receipt_a == hashlib.sha256(path.read_bytes()).hexdigest()
    workspace_b = copy.deepcopy(workspace_a)
    workspace_b["entries"][0]["translation"] = "<b>Новая редакция {n}</b>"
    catalog_b = export_catalog(workspace_b, data, previous=catalog_a,
                               expected_previous_sha256=receipt_a)
    assert catalog_digest(catalog_b) != receipt_a
    with pytest.raises(ValueError, match="baseline changed"):
        export_catalog(workspace_a, data, previous=catalog_b,
                       expected_previous_sha256=receipt_a)
    # Even an explicit removal acknowledgement cannot bypass an outdated baseline.
    with pytest.raises(ValueError, match="baseline changed"):
        export_catalog(workspace_a, data, previous=catalog_b,
                       allow_removals=[workspace_a["entries"][0]["id"]],
                       expected_previous_sha256=receipt_a)
    assert export_catalog(workspace_b, data, previous=catalog_b,
                          expected_previous_sha256=catalog_digest(catalog_b)) == catalog_b
    with pytest.raises(ValueError, match="previous catalog"):
        export_catalog(workspace_a, data, expected_previous_sha256=receipt_a)


@pytest.mark.parametrize("receipt", [
    {}, {"catalog_sha256": None}, {"catalog_sha256": "bad"},
    {"catalog_sha256": "a" * 64, "extra": True}, [],
])
def test_cli_malformed_baseline_receipt_rejected_without_output(tmp_path, receipt):
    data = inventory()
    workspace = manual_workspace(data)
    catalog = export_catalog(workspace, data)
    for name, value in (("inventory", data), ("workspace", workspace),
                        ("catalog", catalog), ("receipt", receipt)):
        write_json(tmp_path / f"{name}.json", value)
    output = tmp_path / "exported.json"
    with pytest.raises(SystemExit):
        main(["export", "--inventory", str(tmp_path / "inventory.json"),
              "--workspace", str(tmp_path / "workspace.json"),
              "--previous", str(tmp_path / "catalog.json"),
              "--base-receipt", str(tmp_path / "receipt.json"), "--output", str(output)])
    assert not output.exists()


def test_cli_requires_receipt_pair_and_allows_current_baseline_revision(tmp_path):
    data = inventory()
    workspace = manual_workspace(data)
    catalog = export_catalog(workspace, data)
    receipt = {"catalog_sha256": catalog_digest(catalog)}
    workspace["entries"][0]["notes"] = "Новая редакторская заметка"
    for name, value in (("inventory", data), ("workspace", workspace),
                        ("catalog", catalog), ("receipt", receipt)):
        write_json(tmp_path / f"{name}.json", value)
    output = tmp_path / "exported.json"
    base = ["export", "--inventory", str(tmp_path / "inventory.json"),
            "--workspace", str(tmp_path / "workspace.json"), "--output", str(output)]
    previous = ["--previous", str(tmp_path / "catalog.json")]
    receipt_arg = ["--base-receipt", str(tmp_path / "receipt.json")]
    for unpaired in (previous, receipt_arg):
        with pytest.raises(SystemExit):
            main(base + unpaired)
        assert not output.exists()
    assert main(base + previous + receipt_arg) == 0
    assert restore_workspace(json.loads(output.read_text()), data) == workspace


def test_cross_entry_source_references_are_source_free_lossless_and_guarded():
    data = inventory()
    sources = ["A long invented original paragraph for entry zero {n}",
               "Another long invented original paragraph for entry one {n}"]
    for entry, source in zip(data["entries"], sources):
        entry.update(source=source, source_sha256=source_hash(source))
    workspace = manual_workspace(data)
    workspace["entries"][2]["context"] = f"{sources[1]} / {sources[0]} / {sources[1]}"
    catalog = export_catalog(workspace, data)
    serialized = json.dumps(catalog, ensure_ascii=False)
    assert all(source not in serialized for source in sources)
    assert restore_workspace(catalog, data) == workspace
    assert export_catalog(workspace, data) == catalog
    parts = catalog["entries"][2]["manual"]["context"]["source_parts"]
    refs = [part for part in parts if isinstance(part, dict)]
    assert [ref["id"] for ref in refs] == [data["entries"][index]["id"] for index in (1, 0, 1)]
    refs[0]["source_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="reference.*SHA|reference.*origin"):
        restore_workspace(catalog, data)
    refs[0]["id"] = "missing-reference"
    with pytest.raises(ValueError, match="reference.*missing|missing.*reference"):
        restore_workspace(catalog, data)


@pytest.mark.parametrize("part", [
    {"id": "abc.7.1.0", "source_sha256": "bad"},
    {"id": "", "source_sha256": "a" * 64},
    {"id": "abc.7.1.0", "source_sha256": "a" * 64, "source": "injected"},
    {"id": "abc.7.1.0", "source_sha256": True},
    ["nested list"],
])
def test_source_reference_schema_rejects_malformed_nested_parts(part):
    data = inventory()
    catalog = export_catalog(manual_workspace(data), data)
    catalog["entries"][0]["manual"]["notes"] = {"source_parts": ["", part, ""]}
    assert validate_catalog_data(catalog)


def test_source_reference_matching_prefers_longest_and_stable_duplicate_id():
    data = inventory()
    shorter = "A long synthetic source for deterministic matching {n}"
    longer = shorter + " with a longer suffix"
    for entry, source in zip(data["entries"], (shorter, longer, shorter, shorter)):
        entry.update(source=source, source_sha256=source_hash(source))
    workspace = manual_workspace(data)
    workspace["entries"][0]["notes"] = f"{longer} / {shorter}"
    first = export_catalog(workspace, data)
    parts = first["entries"][0]["manual"]["notes"]["source_parts"]
    refs = [part for part in parts if isinstance(part, dict)]
    assert [reference["id"] for reference in refs] == ["abc.7.1.1", "abc.7.1.0"]
    assert restore_workspace(first, data) == workspace
    data["entries"].reverse()
    assert export_catalog(workspace, data) == first


def test_source_references_can_resolve_technical_inventory_entries():
    data = inventory()
    source = "Long synthetic technical source not itself a translation task {n}"
    data["entries"][3].update(source=source, source_sha256=source_hash(source),
                              classification="technical")
    workspace = initialize_workspace(data)
    workspace["entries"][0]["notes"] = "См. " + source
    catalog = export_catalog(workspace, data)
    assert source not in json.dumps(catalog, ensure_ascii=False)
    assert restore_workspace(catalog, data) == workspace


def test_static_binding_address_cannot_be_disguised_by_another_character_id():
    data = inventory()
    workspace = manual_workspace(data)
    catalog = export_catalog(workspace, data)
    row = copy.deepcopy(catalog['entries'][0])
    row['id'] = 'static.alias'
    row['location'] = {'tag_path': [7], 'tag_code': 11, 'character_id': 71}
    catalog['entries'][0]['location'] = {
        'tag_path': [7], 'tag_code': 11, 'character_id': 70}
    catalog['entries'].append(row)
    assert any('duplicate binding address' in error for error in validate_catalog_data(catalog))


def test_short_ui_headings_are_references_too():
    data = inventory()
    source = 'Building towers'
    data['entries'][1].update(source=source, source_sha256=source_hash(source),
                              mandatory_placeholders=[])
    workspace = initialize_workspace(data)
    workspace['entries'][0]['context'] = 'Панель «Building towers»'
    catalog = export_catalog(workspace, data)
    assert source not in json.dumps(catalog, ensure_ascii=False)
    assert restore_workspace(catalog, data) == workspace


def test_reexport_cannot_drop_notes_while_retaining_translation():
    data = inventory()
    workspace = manual_workspace(data)
    previous = export_catalog(workspace, data)
    row = workspace['entries'][0]
    translation = row['translation']
    row['notes'] = ''
    with pytest.raises(ValueError, match=row['id']):
        export_catalog(workspace, data, previous=previous,
                       expected_previous_sha256=catalog_digest(previous))
    assert row['translation'] == translation


@pytest.mark.parametrize('field', [
    'notes', 'context', 'status', 'classification', 'context_confidence',
    'mandatory_placeholders', 'translation',
])
def test_resetting_any_manual_override_requires_explicit_removal(field):
    data = inventory()
    workspace = manual_workspace(data)
    workspace['entries'][0]['mandatory_placeholders'] = []
    previous = export_catalog(workspace, data)
    baseline = initialize_workspace(data)['entries'][0]
    row = workspace['entries'][0]
    row[field] = copy.deepcopy(baseline[field])
    with pytest.raises(ValueError, match=row['id']):
        export_catalog(workspace, data, previous=previous,
                       expected_previous_sha256=catalog_digest(previous))
    allowed = export_catalog(workspace, data, previous=previous,
                             allow_removals=[row['id']],
                             expected_previous_sha256=catalog_digest(previous))
    assert restore_workspace(allowed, data) == workspace
    with pytest.raises(ValueError, match='baseline changed'):
        export_catalog(workspace, data, previous=previous,
                       allow_removals=[row['id']], expected_previous_sha256='0' * 64)


@pytest.mark.parametrize('field,value', [
    ('notes', 'Обновлённая заметка'), ('context', 'Уточнённый контекст'),
    ('status', 'verified'), ('classification', 'unknown'),
    ('context_confidence', 'inferred'), ('mandatory_placeholders', ['</b>']),
    ('translation', '<b>Обновлённый ёж {n}</b>'),
])
def test_ordinary_manual_edits_remain_allowed(field, value):
    data = inventory()
    workspace = manual_workspace(data)
    workspace['entries'][0]['mandatory_placeholders'] = ['<b>']
    previous = export_catalog(workspace, data)
    workspace['entries'][0][field] = value
    exported = export_catalog(workspace, data, previous=previous,
                              expected_previous_sha256=catalog_digest(previous))
    assert restore_workspace(exported, data) == workspace
