"""Synthetic end-to-end installer transactions; no game or Windows user data."""
import json
from pathlib import Path

import pytest

import windows_installer as wi
from install_patch import sha


@pytest.fixture
def setup(tmp_path, monkeypatch):
    resources = tmp_path / 'resources'
    resources.mkdir()
    (resources / 'installer-resources.json').write_text('{}')
    (resources / 'translations').mkdir()
    manifest = resources / 'translations/build-manifest.json'
    manifest.write_text(json.dumps({'game_sha256': sha(b'original'),
                                    'expected_candidate_sha256': sha(b'A')}))
    monkeypatch.setattr(wi, 'verify_resources', lambda root: None)
    monkeypatch.setattr(wi, 'assert_game_stopped', lambda: None)
    saves = tmp_path / 'saves'
    saves.mkdir()
    (saves / 'progress.sol').write_bytes(b'latest progress')
    (saves / 'remotecache.vdf').write_bytes(b'cloud metadata')
    monkeypatch.setattr(wi, 'discover_protected_roots', lambda target: [saves])
    builds = []

    def build(source, font, output, **kwargs):
        assert Path(source).read_bytes() == b'original'
        builds.append(source)
        output.mkdir()
        current = json.loads(manifest.read_text())['expected_candidate_sha256']
        (output / 'gc-cs-steam.ru.swf').write_bytes(b'A' if current == sha(b'A') else b'B')
    monkeypatch.setattr(wi, 'build_project', build)
    target = tmp_path / 'игра с пробелами/gc-cs-steam.swf'
    target.parent.mkdir()
    target.write_bytes(b'original')
    (target.parent / 'unrelated.txt').write_text('keep')
    return wi.Installer(resources, tmp_path / 'data'), target, manifest, builds, saves


def test_install_update_idempotent_restore(setup):
    app, target, manifest, builds, saves = setup
    before = {p.name: p.read_bytes() for p in saves.iterdir()}
    assert app.inspect(target)['state'] == 'original'
    app.perform(target, 'install', sha(b'original'))
    assert target.read_bytes() == b'A'
    app.perform(target, 'install', sha(b'A'))
    assert len(builds) == 1
    contents = json.loads(manifest.read_text())
    contents['expected_candidate_sha256'] = sha(b'B')
    manifest.write_text(json.dumps(contents))
    app = wi.Installer(app.resources, app.data_dir)
    app.perform(target, 'install', sha(b'A'))
    assert target.read_bytes() == b'B'
    app.perform(target, 'restore', sha(b'B'))
    assert target.read_bytes() == b'original'
    assert len(list(app.data_dir.rglob('manifest-*.json'))) == 2
    assert (target.parent / 'unrelated.txt').read_text() == 'keep'
    assert before == {p.name: p.read_bytes() for p in saves.iterdir()}


@pytest.mark.parametrize('damage', ['unknown', 'backup', 'manifest', 'confirmation'])
def test_fail_closed(setup, damage):
    app, target, _, _, _ = setup
    app.perform(target, 'install', sha(b'original'))
    if damage == 'unknown':
        target.write_bytes(b'unknown')
    elif damage == 'backup':
        backup = next(app.data_dir.rglob('*.original'))
        backup.chmod(0o600)
        backup.write_bytes(b'bad')
    elif damage == 'manifest':
        next(app.data_dir.rglob('manifest-*.json')).write_text('{}')
    before = target.read_bytes()
    with pytest.raises((ValueError, KeyError)):
        app.perform(target, 'restore', sha(b'wrong') if damage == 'confirmation' else sha(before))
    assert target.read_bytes() == before


def test_build_failure_never_mutates_target(setup, monkeypatch):
    app, target, _, _, _ = setup
    def broken(*args, **kwargs):
        raise ValueError('build failed')
    monkeypatch.setattr(wi, 'build_project', broken)
    with pytest.raises(ValueError, match='build failed'):
        app.perform(target, 'install', sha(b'original'))
    assert target.read_bytes() == b'original'


def test_saves_changed_during_build_stops_write(setup, monkeypatch):
    app, target, _, _, saves = setup
    original = wi.build_project
    def changing(*args, **kwargs):
        original(*args, **kwargs)
        (saves / 'progress.sol').write_bytes(b'new progress')
    monkeypatch.setattr(wi, 'build_project', changing)
    with pytest.raises(ValueError):
        app.perform(target, 'install', sha(b'original'))
    assert target.read_bytes() == b'original'
    assert (saves / 'progress.sol').read_bytes() == b'new progress'


def test_denied_data_directory_stops_before_target_write(setup):
    app, target, _, _, _ = setup
    app.data_dir.write_text('occupied')
    with pytest.raises(OSError):
        app.perform(target, 'install', sha(b'original'))
    assert target.read_bytes() == b'original'


def test_target_changed_during_build_is_preserved(setup, monkeypatch):
    app, target, _, _, _ = setup
    original = wi.build_project
    def changing(*args, **kwargs):
        original(*args, **kwargs)
        target.write_bytes(b'external update')
    monkeypatch.setattr(wi, 'build_project', changing)
    with pytest.raises(ValueError):
        app.perform(target, 'install', sha(b'original'))
    assert target.read_bytes() == b'external update'


def test_bad_candidate_sha_refused(setup, monkeypatch):
    app, target, _, _, _ = setup
    original = wi.build_project
    def changing(*args, **kwargs):
        original(*args, **kwargs)
        (args[2] / 'gc-cs-steam.ru.swf').write_bytes(b'bad candidate')
    monkeypatch.setattr(wi, 'build_project', changing)
    with pytest.raises(ValueError, match='Candidate changed'):
        app.perform(target, 'install', sha(b'original'))
    assert target.read_bytes() == b'original'


def test_resource_inventory_detects_catalog_and_plan_drift(tmp_path, monkeypatch):
    root = tmp_path / 'resources'
    names = ['translations/catalog.json', 'translations/build-manifest.json',
             'translations/plans/test.json', 'fonts/alegreya/provenance.json',
             'fonts/alegreya/OFL.txt', 'fonts/alegreya/Alegreya[wght].ttf']
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'font' if name.endswith('.ttf') else b'{}')
    (root / names[1]).write_text(json.dumps({'font_sha256': sha(b'font'),
                                          'plans': [{'path': 'plans/test.json'}]}))
    inventory = {'schema': 1, 'files': {name: sha((root / name).read_bytes()) for name in names}}
    (root / 'installer-resources.json').write_text(json.dumps(inventory))
    monkeypatch.setattr(wi, 'validate_project', lambda *args: None)
    wi.verify_resources(root)
    for name in ('translations/catalog.json', 'translations/plans/test.json'):
        path = root / name
        previous = path.read_bytes()
        path.write_bytes(b'changed')
        with pytest.raises(ValueError, match='ресурс'):
            wi.verify_resources(root)
        path.write_bytes(previous)
    del inventory['files']['translations/catalog.json']
    (root / 'installer-resources.json').write_text(json.dumps(inventory))
    with pytest.raises(ValueError, match='Неполный'):
        wi.verify_resources(root)


def test_identical_target_replacement_during_build_stops_install(setup, monkeypatch):
    app, target, _, _, _ = setup
    original = wi.build_project
    def changing(*args, **kwargs):
        original(*args, **kwargs)
        replacement = target.parent / 'replacement'
        replacement.write_bytes(b'original')
        replacement.replace(target)
    monkeypatch.setattr(wi, 'build_project', changing)
    with pytest.raises(ValueError, match='Файл игры изменился'):
        app.perform(target, 'install', sha(b'original'))
    assert target.read_bytes() == b'original'


def test_dotdot_path_uses_one_canonical_installation(setup):
    app, target, _, _, _ = setup
    subdirectory = target.parent / 'subdirectory'
    subdirectory.mkdir()
    alias = subdirectory / '..' / target.name
    app.perform(alias, 'install', sha(b'original'))
    assert app.inspect(target)['state'] == 'localized'
    app.perform(target, 'restore', sha(b'A'))
    assert target.read_bytes() == b'original'
