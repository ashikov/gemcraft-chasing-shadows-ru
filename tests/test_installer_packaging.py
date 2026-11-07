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


def test_unrelated_python_is_not_staged_or_packaging_input(tmp_path, monkeypatch):
    project, font = fixture_project(tmp_path, monkeypatch)
    (project / 'secret.py').write_text('raise RuntimeError("must not ship")\n')
    stage = tmp_path / 'resources'
    inventory = packaging.stage_resources(stage, font, project)
    assert not any(name.endswith('.py') for name in inventory['files'])
    assert not (stage / 'secret.py').exists()
    command = packaging.packaging_command(project / 'installer_gui.py', stage,
                                           tmp_path / 'dist', tmp_path / 'work', 'preview')
    assert not any('secret.py' in argument for argument in command)
    assert str(project / 'installer_gui.py') == command[-1]


def fixture_fribidi(tmp_path, monkeypatch):
    directory = tmp_path / 'fribidi'
    directory.mkdir()
    payload = {'fribidi.dll': b'synthetic DLL',
               'fribidi-1.0.17-COPYING': b'synthetic license'}
    for name, data in payload.items():
        (directory / name).write_bytes(data)
    monkeypatch.setattr(packaging, 'FRIBIDI_FILES',
                        {name: sha(data) for name, data in payload.items()}, raising=False)
    return directory, payload


def test_pinned_fribidi_and_license_are_staged_without_unrelated_dll(tmp_path, monkeypatch):
    project, font = fixture_project(tmp_path, monkeypatch)
    directory, payload = fixture_fribidi(tmp_path, monkeypatch)
    (directory / 'unrelated.dll').write_bytes(b'not approved')
    stage = tmp_path / 'resources'
    inventory = packaging.stage_resources(stage, font, project, fribidi_dir=directory)
    for name, data in payload.items():
        assert inventory['files'][name] == sha(data)
        assert (stage / name).read_bytes() == data
    assert 'unrelated.dll' not in inventory['files']
    assert not (stage / 'unrelated.dll').exists()
    command = packaging.packaging_command(project / 'installer_gui.py', stage,
                                           tmp_path / 'dist', tmp_path / 'work', 'preview')
    binary = str(stage / 'fribidi.dll') + packaging.os.pathsep + '.'
    license_data = str(stage / 'fribidi-1.0.17-COPYING') + packaging.os.pathsep + '.'
    assert command[command.index(binary) - 1] == '--add-binary'
    assert command[command.index(license_data) - 1] == '--add-data'
    assert not any('unrelated.dll' in argument for argument in command)


@pytest.mark.parametrize('name', ['fribidi.dll', 'fribidi-1.0.17-COPYING'])
def test_changed_fribidi_input_is_rejected_before_staging(tmp_path, monkeypatch, name):
    project, font = fixture_project(tmp_path, monkeypatch)
    directory, _ = fixture_fribidi(tmp_path, monkeypatch)
    (directory / name).write_bytes(b'changed')
    with pytest.raises(ValueError, match='FriBidi.*SHA'):
        packaging.stage_resources(tmp_path / 'resources', font, project, fribidi_dir=directory)
    assert not (tmp_path / 'resources').exists()


def test_missing_fribidi_license_is_rejected_before_staging(tmp_path, monkeypatch):
    project, font = fixture_project(tmp_path, monkeypatch)
    directory, _ = fixture_fribidi(tmp_path, monkeypatch)
    (directory / 'fribidi-1.0.17-COPYING').unlink()
    with pytest.raises(FileNotFoundError):
        packaging.stage_resources(tmp_path / 'resources', font, project, fribidi_dir=directory)
    assert not (tmp_path / 'resources').exists()


def test_packaging_rejects_an_unapproved_dll_in_inventory(tmp_path, monkeypatch):
    project, font = fixture_project(tmp_path, monkeypatch)
    stage = tmp_path / 'resources'
    inventory = packaging.stage_resources(stage, font, project)
    (stage / 'unrelated.dll').write_bytes(b'not approved')
    inventory['files']['unrelated.dll'] = sha(b'not approved')
    (stage / 'installer-resources.json').write_text(json.dumps(inventory))
    with pytest.raises(ValueError, match='Unapproved.*DLL'):
        packaging.packaging_command(project / 'installer_gui.py', stage,
                                    tmp_path / 'dist', tmp_path / 'work', 'preview')


@pytest.mark.parametrize('change_after_staging', [False, True])
def test_build_loads_only_verified_persistent_dll(tmp_path, monkeypatch, change_after_staging):
    directory, payload = fixture_fribidi(tmp_path, monkeypatch)
    loaded = []

    def stage(destination, font, *, fribidi_dir):
        destination.mkdir()
        (destination / 'fribidi.dll').write_bytes(payload['fribidi.dll'])
        if change_after_staging:
            (directory / 'fribidi.dll').write_bytes(b'changed')

    def build(*args, **kwargs):
        raise RuntimeError('stop after preload')

    monkeypatch.setattr(packaging, 'stage_resources', stage)
    monkeypatch.setattr(packaging.ctypes, 'WinDLL', loaded.append, raising=False)
    monkeypatch.setattr(packaging, 'build_project', build)
    monkeypatch.setattr(packaging.sys, 'platform', 'win32')
    args = ['--source', str(tmp_path / 'owned.swf'), '--font', str(tmp_path / 'font.ttf'),
            '--fribidi-dir', str(directory), '--output', str(tmp_path / 'output')]
    if change_after_staging:
        with pytest.raises(ValueError, match='SHA mismatch before loading'):
            packaging.main(args)
        assert loaded == []
    else:
        with pytest.raises(RuntimeError, match='stop after preload'):
            packaging.main(args)
        assert loaded == [str(directory.resolve() / 'fribidi.dll')]
