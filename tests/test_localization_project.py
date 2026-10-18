"""Project orchestration boundaries use synthetic inputs only."""
from pathlib import Path

import pytest

from install_patch import ORIGINAL_SHA256, sha
from localization_project import prepare_workspace, validate_project


def test_wrong_original_fails_without_creating_output(tmp_path):
    source = tmp_path / 'original.swf'
    source.write_bytes(b'not a game')
    output = tmp_path / 'result'
    with pytest.raises(ValueError, match='Original SHA'):
        prepare_workspace(source, output)
    assert not output.exists()


def test_existing_output_is_never_overwritten(tmp_path):
    source = tmp_path / 'original.swf'
    source.write_bytes(b'invalid')
    output = tmp_path / 'occupied'
    output.mkdir()
    marker = output / 'workspace.json'
    marker.write_text('user data')
    with pytest.raises(ValueError, match='fresh output'):
        prepare_workspace(source, output)
    assert marker.read_text() == 'user data'


def test_symlink_output_is_rejected_even_when_dangling(tmp_path):
    source = tmp_path / 'original.swf'
    source.write_bytes(b'invalid')
    output = tmp_path / 'link'
    output.symlink_to(tmp_path / 'missing', target_is_directory=True)
    with pytest.raises(ValueError, match='fresh output'):
        prepare_workspace(source, output)
    assert output.is_symlink()


def test_real_git_catalog_and_plan_structure_without_game_inputs():
    result = validate_project()
    assert result['game_sha256'] == ORIGINAL_SHA256
    assert result['applied_translations'] == 1809
    assert result['translated_entries'] == 1934
    assert result['plans'] == 12
    assert result['validation'] == 'Git structure only; original SWF not checked'
    assert result['expected_candidate_sha256'] == (
        '72e655aca3189e89a44cc0b629a53fa6d5c2c25f54642496447dce68bca68290')
    # The private catalog schema deliberately excludes full source strings.
    assert sha(Path('translations/catalog.json').read_bytes()) == result['catalog_sha256']


def test_versioned_translation_tree_contains_only_explicit_text_files():
    import json

    root = Path('translations')
    manifest = json.loads((root / 'build-manifest.json').read_text())
    expected = {'ru.json', 'glossary.json', 'catalog.json', 'build-manifest.json',
                'editorial/glossary.json', 'editorial/style-guide.md'}
    expected.update(item['path'] for item in manifest['plans'])
    actual = {str(path.relative_to(root)) for path in root.rglob('*') if path.is_file()}
    assert actual == expected
    for name in sorted(expected):
        path = root / name
        assert not path.is_symlink()
        text = path.read_text(encoding='utf-8')
        assert '\0' not in text
        if path.suffix == '.json':
            assert isinstance(json.loads(text), dict)


def test_wrong_font_fails_without_output(tmp_path):
    from localization_project import build_project

    source = tmp_path / 'original.swf'
    source.write_bytes(b'unused')
    font = tmp_path / 'font.ttf'
    font.write_bytes(b'wrong release')
    output = tmp_path / 'result'
    with pytest.raises(ValueError, match='Alegreya SHA'):
        build_project(source, font, output)
    assert not output.exists()
