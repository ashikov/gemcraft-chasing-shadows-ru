import json

import pytest

import build_windows_installer as packaging
from install_patch import sha


def fixture_project(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    (project / 'translations/plans').mkdir(parents=True)
    (project / 'fonts/alegreya').mkdir(parents=True)
    font = tmp_path / 'font.ttf'
    font.write_bytes(b'font')
    manifest = {'font_sha256': sha(b'font'), 'plans': [{'path': 'plans/test.json'}]}
    (project / 'translations/build-manifest.json').write_text(json.dumps(manifest))
    for name in ('translations/catalog.json', 'translations/plans/test.json',
                 'fonts/alegreya/OFL.txt', 'fonts/alegreya/provenance.json'):
        (project / name).write_text('{}')
    (project / 'tool.py').write_text('pass\n')
    (project / 'secret.swf').write_bytes(b'game')
    (project / 'local').mkdir()
    (project / 'local/private.json').write_text('private')
    monkeypatch.setattr(packaging, 'validate_project', lambda *args: None)
    return project, font


def test_staging_reproducible_and_excludes_game(tmp_path, monkeypatch):
    project, font = fixture_project(tmp_path, monkeypatch)
    first = packaging.stage_resources(tmp_path / 'a', font, project)
    second = packaging.stage_resources(tmp_path / 'b', font, project)
    assert first == second
    assert 'secret.swf' not in first['files']
    assert not any(name.startswith('local/') for name in first['files'])
    assert (tmp_path / 'a/installer-resources.json').read_bytes() == (
        tmp_path / 'b/installer-resources.json').read_bytes()
    for name, digest in first['files'].items():
        assert sha((tmp_path / 'a' / name).read_bytes()) == digest


def test_bad_font_and_missing_input_fail_before_staging(tmp_path, monkeypatch):
    project, font = fixture_project(tmp_path, monkeypatch)
    font.write_bytes(b'bad')
    with pytest.raises(ValueError, match='SHA'):
        packaging.stage_resources(tmp_path / 'a', font, project)
    assert not (tmp_path / 'a').exists()
    font.write_bytes(b'font')
    (project / 'translations/plans/test.json').unlink()
    with pytest.raises(FileNotFoundError):
        packaging.stage_resources(tmp_path / 'b', font, project)
    assert not (tmp_path / 'b').exists()
