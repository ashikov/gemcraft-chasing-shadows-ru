"""Catalog checks use invented strings only."""
import copy
import json

import pytest

from translation_catalog import load_catalog, source_hash, validate_catalog


def catalog(source="Synthetic {count}\ntext", translation="Проверка {count}\nстроки"):
    return {
        "schema_version": 1,
        "game_sha256": "a" * 64,
        "entries": [{
            "id": "synthetic.title", "category": "static",
            "location": {"character_id": 7, "tag_code": 11},
            "source": source, "source_sha256": source_hash(source),
            "context": "Synthetic test", "translation": translation,
            "mandatory_placeholders": ["{count}"],
        }],
    }


def test_unicode_newlines_and_empty_vs_missing(tmp_path):
    data = catalog()
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert load_catalog(path) == data
    data["entries"][0]["mandatory_placeholders"] = []
    data["entries"][0]["translation"] = ""
    assert validate_catalog(data) == []
    data["entries"][0]["translation"] = None
    assert "missing translation" in validate_catalog(data)[0]
    assert validate_catalog(data, allow_missing=True) == []


def test_duplicate_keys_and_identifiers(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        load_catalog(path)
    data = catalog()
    data["entries"].append(copy.deepcopy(data["entries"][0]))
    assert any("duplicate id" in error for error in validate_catalog(data))


@pytest.mark.parametrize("field,value,expected", [
    ("source_sha256", "b" * 64, "source hash mismatch"),
    ("translation", "\ud800", "Unicode surrogate"),
    ("category", "invented", "category"),
    ("location", "where", "location"),
    ("translation", "{count} {count}", "placeholder"),
])
def test_invalid_entry(field, value, expected):
    data = catalog()
    data["entries"][0][field] = value
    assert any(expected in error for error in validate_catalog(data))


def test_html_balance_only_for_html_category():
    data = catalog("<b>{count}</b>", "<b>{count}</i>")
    assert validate_catalog(data) == []
    data["entries"][0]["category"] = "html"
    assert any("HTML" in error for error in validate_catalog(data))
    data["entries"][0]["translation"] = '<b>{count}</b><br/> &amp;'
    assert validate_catalog(data) == []


def test_explicit_placeholder_counts_and_invalid_schema():
    data = catalog("{count} {count}", "{count}")
    assert any("placeholder" in error for error in validate_catalog(data))
    data["entries"][0]["mandatory_placeholders"] = [""]
    assert any("mandatory_placeholders" in error for error in validate_catalog(data))
    assert validate_catalog([])


def test_cli_missing_translation_requires_explicit_flag(tmp_path, capsys):
    from translation_catalog import main
    data = catalog()
    data["entries"][0]["translation"] = None
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        main([str(path)])
    assert error.value.code == 1
    assert "missing translation" in capsys.readouterr().err
    main([str(path), "--allow-missing"])
    assert "Catalog valid" in capsys.readouterr().out
