"""Invented text exercises private workspaces and publication boundaries."""
import copy
import json

import pytest

from localization_workspace import (
    initialize_workspace, main, minimal_projection, reconcile_workspace,
    validate_workspace, write_json,
)
from translation_catalog import source_hash


def inventory():
    return {"schema_version": 1, "game_sha256": "a" * 64, "entries": [{
        "id": "static.7", "category": "static", "source": "Invented {n}",
        "source_sha256": source_hash("Invented {n}"),
        "location": {"character_id": 7, "tag_code": 11, "font_ids": [1]},
        "context": "Synthetic location", "context_confidence": "inferred",
        "classification": "probable", "translation": None,
        "mandatory_placeholders": ["{n}"], "metadata": {"symbol": "private symbol"},
    }]}


def seed(data):
    result = copy.deepcopy(data)
    result["entries"][0]["translation"] = "Пример {n}"
    return result


def test_init_excludes_technical_preserves_distinct_context_and_seed():
    data = inventory()
    duplicate = copy.deepcopy(data["entries"][0])
    duplicate.update(id="static.8", location={"character_id": 8, "tag_code": 11})
    technical = copy.deepcopy(duplicate)
    technical.update(id="symbol.9", classification="technical")
    data["entries"] += [duplicate, technical]
    workspace = initialize_workspace(data, seed(inventory()))
    assert len(workspace["entries"]) == 2
    assert workspace["entries"][0]["translation"] == "Пример {n}"
    assert workspace["entries"][0]["status"] == "verified"
    assert workspace["entries"][1]["translation"] is None
    assert validate_workspace(workspace, data) == []
    assert data["entries"][0]["translation"] is None


@pytest.mark.parametrize("change", ["source", "location", "game"])
def test_reconcile_conflict_retains_complete_snapshots(change):
    original = inventory()
    old = initialize_workspace(original, seed(original))
    old["entries"][0].update(notes="Translator note", context="Manual context")
    new = copy.deepcopy(original)
    if change == "source":
        new["entries"][0].update(source="Changed {n}", source_sha256=source_hash("Changed {n}"))
    elif change == "location":
        new["entries"][0]["location"]["character_id"] = 9
    else:
        new["game_sha256"] = "b" * 64
    updated = reconcile_workspace(old, new)
    assert updated["entries"][0]["translation"] is None
    assert updated["entries"][0]["status"] == "needs_review"
    assert updated["conflicts"][0]["old"] == old["entries"][0]
    assert updated["conflicts"][0]["new"] == new["entries"][0]
    assert reconcile_workspace(updated, new)["conflicts"] == updated["conflicts"]


def test_reconcile_preserves_manual_fields_empty_and_archives_removed():
    data = inventory()
    data["entries"][0]["mandatory_placeholders"] = []
    old = initialize_workspace(data)
    old["entries"][0].update(translation="", notes="note", context="manual", status="draft")
    assert reconcile_workspace(old, data)["entries"] == old["entries"]
    removed = copy.deepcopy(data)
    removed["entries"] = []
    result = reconcile_workspace(old, removed)
    assert result["entries"] == []
    assert result["conflicts"][0]["old"] == old["entries"][0]
    assert result["conflicts"][0]["new"] is None


def test_validation_hash_tokens_duplicate_and_html_fragments():
    workspace = initialize_workspace(inventory())
    workspace["entries"][0]["translation"] = "Lost token"
    assert any("placeholder" in error for error in validate_workspace(workspace))
    workspace["entries"][0]["translation"] = None
    workspace["entries"].append(copy.deepcopy(workspace["entries"][0]))
    assert any("duplicate id" in error for error in validate_workspace(workspace))
    data = inventory()
    data["entries"][0].update(source="<b>Fragment", source_sha256=source_hash("<b>Fragment"), mandatory_placeholders=[])
    assert validate_workspace(initialize_workspace(data)) == []
    data["entries"][0]["category"] = "html"
    with pytest.raises(ValueError, match="HTML"):
        initialize_workspace(data)


def test_minimal_allowlist_only_numeric_addresses_and_no_private_fields():
    data = inventory()
    allowlist = seed(data)
    workspace = initialize_workspace(data, allowlist)
    result = minimal_projection(workspace, allowlist)
    entry = result["entries"][0]
    assert set(entry) == {"id", "category", "location", "source_sha256", "translation"}
    assert entry["location"] == data["entries"][0]["location"]
    serialized = json.dumps(result)
    assert "Invented" not in serialized and "private symbol" not in serialized
    workspace["entries"][0]["translation"] = "Иное {n}"
    with pytest.raises(ValueError, match="allowlist"):
        minimal_projection(workspace, allowlist)


@pytest.mark.parametrize("classification", ["unknown", "technical"])
def test_export_rejects_unsafe_classification(classification):
    data = inventory()
    workspace = initialize_workspace(data, seed(data))
    workspace["entries"][0]["classification"] = classification
    with pytest.raises(ValueError):
        minimal_projection(workspace, seed(data))


def test_seed_mismatch_and_extra_translation_refused():
    data = inventory()
    bad = seed(data)
    bad["entries"][0]["location"]["character_id"] = 8
    with pytest.raises(ValueError, match="seed"):
        initialize_workspace(data, bad)
    workspace = initialize_workspace(data, seed(data))
    with pytest.raises(ValueError, match="allowlist"):
        minimal_projection(workspace, {**data, "entries": []})


def test_deterministic_exclusive_json_and_cli(tmp_path):
    data = inventory()
    path = tmp_path / "inventory.json"
    write_json(path, data)
    with pytest.raises(FileExistsError):
        write_json(path, data)
    workspace_path = tmp_path / "workspace.json"
    assert main(["init", str(path), "--output", str(workspace_path)]) == 0
    assert main(["validate", str(workspace_path), "--inventory", str(path)]) == 0
    second = tmp_path / "second.json"
    write_json(second, json.loads(workspace_path.read_text()))
    assert second.read_bytes() == workspace_path.read_bytes()


def test_validation_rejects_corrupt_archived_source_and_invalid_inventory():
    data = inventory()
    old = initialize_workspace(data)
    changed = copy.deepcopy(data)
    changed["entries"] = []
    workspace = reconcile_workspace(old, changed)
    workspace["conflicts"][0]["old"]["source_sha256"] = "b" * 64
    assert any("source hash mismatch" in error for error in validate_workspace(workspace))
    assert validate_workspace(old, [])
    assert validate_workspace({"entries": None}, data)


def test_minimal_filters_unapproved_string_addresses():
    data = inventory()
    data["entries"][0]["location"].update(class_name="PrivateClass", method_name="privateMethod")
    workspace = initialize_workspace(data, seed(data))
    minimal = minimal_projection(workspace, seed(data))
    assert "PrivateClass" not in json.dumps(minimal)
    assert "privateMethod" not in json.dumps(minimal)


def test_seed_context_review_and_manual_classification_survive_reconcile():
    data = inventory()
    old = initialize_workspace(data, seed(data))
    assert old["entries"][0]["context_confidence"] == "confirmed"
    assert old["entries"][0]["classification"] == "confirmed"
    updated = reconcile_workspace(old, data)
    assert updated["entries"][0]["context_confidence"] == "confirmed"
    assert updated["entries"][0]["classification"] == "confirmed"


def test_cli_exclusive_output_and_private_error_redaction(tmp_path, capsys):
    data = inventory()
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    write_json(input_path, data)
    main(["init", str(input_path), "--output", str(output_path)])
    before = output_path.read_bytes()
    with pytest.raises(SystemExit):
        main(["init", str(input_path), "--output", str(output_path)])
    assert output_path.read_bytes() == before
    assert "already exists" in capsys.readouterr().err
    private_token = "PRIVATE_SYNTHETIC_TOKEN"
    data["entries"][0]["mandatory_placeholders"] = [private_token]
    bad_path = tmp_path / "bad.json"
    write_json(bad_path, data)
    with pytest.raises(SystemExit):
        main(["init", str(bad_path), "--output", str(tmp_path / "bad-output.json")])
    assert private_token not in capsys.readouterr().err
    assert not (tmp_path / "bad-output.json").exists()


@pytest.mark.parametrize("target,field,value", [
    ("workspace", "source_sha256", "b" * 64),
    ("allowlist", "source_sha256", "b" * 64),
    ("allowlist", "game_sha256", "b" * 64),
])
def test_minimal_rejects_corrupt_inputs(target, field, value):
    data = inventory()
    workspace = initialize_workspace(data, seed(data))
    allowed = seed(data)
    changed = workspace if target == "workspace" else allowed
    if field == "game_sha256":
        changed[field] = value
    else:
        changed["entries"][0][field] = value
    with pytest.raises(ValueError):
        minimal_projection(workspace, allowed)


@pytest.mark.parametrize("field,value", [
    ("source", "Injected private source"),
    ("context", "Private context"),
    ("source_sha256", "b" * 64),
    ("id", "static.changed"),
])
def test_minimal_validation_rejects_injected_or_changed_fields(field, value):
    from localization_workspace import validate_minimal
    data = inventory()
    workspace = initialize_workspace(data, seed(data))
    projection = minimal_projection(workspace, seed(data))
    assert validate_minimal(projection, workspace, seed(data)) == []
    projection["entries"][0][field] = value
    assert validate_minimal(projection, workspace, seed(data))


def test_unresolved_conflicts_block_export_even_when_marked_verified():
    data = inventory()
    workspace = initialize_workspace(data, seed(data))
    changed = copy.deepcopy(data)
    changed["entries"][0]["location"]["character_id"] = 8
    updated = reconcile_workspace(workspace, changed)
    updated["entries"][0].update(translation="Пример {n}", status="verified")
    with pytest.raises(ValueError, match="allowlist"):
        minimal_projection(updated, seed(changed))
    updated["conflicts"] = []
    assert minimal_projection(updated, seed(changed))["entries"]


def test_validate_minimal_cli_and_abc_numeric_locations(tmp_path):
    data = inventory()
    data["entries"][0]["location"].update(string_pool_index=4, code_offset=12, abc_byte_offset=100)
    allowed = seed(data)
    workspace = initialize_workspace(data, allowed)
    projection = minimal_projection(workspace, allowed)
    assert projection["entries"][0]["location"]["string_pool_index"] == 4
    for name, value in [("workspace", workspace), ("allowlist", allowed), ("projection", projection)]:
        write_json(tmp_path / f"{name}.json", value)
    args = ["validate-minimal", str(tmp_path / "projection.json"),
            "--workspace", str(tmp_path / "workspace.json"),
            "--allowlist", str(tmp_path / "allowlist.json")]
    assert main(args) == 0
    projection["entries"][0]["location"]["symbol_name"] = "privateSymbol"
    corrupt = tmp_path / "corrupt.json"
    write_json(corrupt, projection)
    args[1] = str(corrupt)
    with pytest.raises(SystemExit):
        main(args)


@pytest.mark.parametrize("field", ["id", "classification", "context_confidence", "status"])
def test_validation_reports_unhashable_fields_without_crashing(field):
    data = inventory()
    workspace = initialize_workspace(data)
    workspace["entries"][0][field] = []
    assert validate_workspace(workspace, data)
