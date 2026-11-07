import pytest

from installer_safety import safe_path, snapshot


@pytest.mark.parametrize('changed_field', [None, 'st_dev', 'st_ino', 'st_size',
                                         'st_mtime_ns', 'st_ctime_ns', 'st_mode',
                                         'st_nlink', 'st_uid', 'st_gid'])
def test_snapshot_ignores_only_access_time_changes_during_read(
        tmp_path, monkeypatch, changed_field):
    from pathlib import Path
    from types import SimpleNamespace

    root = tmp_path / 'root'
    root.mkdir()
    source = root / 'save.dat'
    source.write_bytes(b'progress')
    real_stat, real_read = Path.stat, Path.read_bytes
    reads = 0

    def stat(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if path != source:
            return info
        fields = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
        fields['st_atime'] += reads
        fields['st_atime_ns'] += reads * 1_000_000_000
        if changed_field is not None:
            fields[changed_field] += bool(reads)
        return SimpleNamespace(**fields)

    def read(path):
        nonlocal reads
        data = real_read(path)
        if path == source:
            reads += 1
        return data

    monkeypatch.setattr(Path, 'stat', stat)
    monkeypatch.setattr(Path, 'read_bytes', read)
    if changed_field is None:
        recheck = snapshot([root], tmp_path / 'backup')
        recheck()
        assert real_read(source) == b'progress'
        assert real_read(tmp_path / 'backup/0/save.dat') == b'progress'
    else:
        with pytest.raises(ValueError, match='изменились во время чтения|hardlink'):
            snapshot([root], tmp_path / 'backup')


def test_snapshot_rejects_hardlink_created_during_read(tmp_path, monkeypatch):
    from pathlib import Path

    root = tmp_path / 'root'
    root.mkdir()
    source = root / 'save.dat'
    source.write_bytes(b'progress')
    outside = tmp_path / 'outside.dat'
    real_read = Path.read_bytes

    def read(path):
        data = real_read(path)
        if path == source and not outside.exists():
            outside.hardlink_to(source)
        return data

    monkeypatch.setattr(Path, 'read_bytes', read)
    with pytest.raises(ValueError, match='hardlink|изменились во время чтения'):
        snapshot([root], tmp_path / 'backup')


def test_snapshot_preserves_sources_and_detects_changes(tmp_path):
    root = tmp_path / 'профиль'
    root.mkdir()
    source = root / 'save.dat'
    source.write_bytes(b'progress')
    cloud = root / 'remotecache.vdf'
    cloud.write_bytes(b'cloud')
    recheck = snapshot([root], tmp_path / 'backup')
    recheck()
    assert source.read_bytes() == b'progress'
    assert cloud.read_bytes() == b'cloud'
    source.write_bytes(b'new progress')
    with pytest.raises(ValueError, match='изменились'):
        recheck()
    assert source.read_bytes() == b'new progress'


def test_missing_root_appearing_is_change(tmp_path):
    root = tmp_path / 'absent'
    recheck = snapshot([root], tmp_path / 'backup')
    root.mkdir()
    with pytest.raises(ValueError):
        recheck()


def test_new_file_and_backup_corruption_rejected(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'a').write_bytes(b'a')
    recheck = snapshot([root], tmp_path / 'backup')
    copied = tmp_path / 'backup/0/a'
    copied.chmod(0o600)
    copied.write_bytes(b'b')
    with pytest.raises(ValueError, match='повреждён'):
        recheck()
    (root / 'b').write_bytes(b'b')
    with pytest.raises(ValueError, match='изменились'):
        recheck()


def test_unsafe_paths_and_backup_location(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'a').write_bytes(b'a')
    (tmp_path / 'linked').symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        safe_path(tmp_path / 'linked/a')
    (tmp_path / 'hard').hardlink_to(root / 'a')
    with pytest.raises(ValueError):
        safe_path(root / 'a')
    with pytest.raises(ValueError):
        snapshot([root], root / 'backup')


def test_snapshot_never_overwrites_existing_destination(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    destination = tmp_path / 'backup'
    destination.mkdir()
    with pytest.raises(FileExistsError):
        snapshot([root], destination)
    assert list(destination.iterdir()) == []


def test_replaced_identical_file_detected(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    source = root / 'a'
    source.write_bytes(b'a')
    recheck = snapshot([root], tmp_path / 'backup')
    other = root / 'replacement'
    other.write_bytes(b'a')
    other.replace(source)
    with pytest.raises(ValueError):
        recheck()


@pytest.mark.parametrize('processes,blocked', [
    ([(10, 'GemCraft-RU-012345abcdef.exe'), (9, 'GemCraft-RU-012345abcdef.exe')], False),
    ([(10, 'python.exe'), (9, 'GemCraft-RU-012345abcdef.exe')], True),
    ([(10, 'GemCraft-RU-012345abcdef.exe'), (9, 'GemCraft Chasing Shadows.exe')], True),
    ([(10, 'GemCraft-RU-012345abcdef.exe'), (11, 'GemCraft-RU-012345abcdef.exe')], True),
    ([(10, 'GemCraft-RU-012345abcdef.exe'), (9, 'GemCraft-RU-fedcba543210.exe')], True),
    ([(10, 'GemCraft-RU-012345abcdef.exe'), (11, 'adl.exe')], True),
])
def test_native_process_enumeration_excludes_only_self_and_exact_bootloader(
        monkeypatch, processes, blocked):
    from types import SimpleNamespace
    import installer_safety as safety

    class Call:
        def __init__(self, action):
            self.action = action

        def __call__(self, *args):
            return self.action(*args)

    pending = iter(processes)

    def advance(handle, pointer):
        try:
            pid, name = next(pending)
        except StopIteration:
            return 0
        pointer._obj.th32ProcessID = pid
        pointer._obj.szExeFile = name
        return 1

    closed = []
    kernel = SimpleNamespace(CreateToolhelp32Snapshot=Call(lambda *args: 123),
                             Process32FirstW=Call(advance), Process32NextW=Call(advance),
                             CloseHandle=Call(lambda handle: closed.append(handle)))
    monkeypatch.setattr(safety, 'os', SimpleNamespace(name='nt', getpid=lambda: 10,
                                                    getppid=lambda: 9))
    monkeypatch.setattr(safety.ctypes, 'WinDLL', lambda *args, **kw: kernel, raising=False)
    monkeypatch.setattr(safety.ctypes, 'get_last_error', lambda: 18, raising=False)
    if blocked:
        with pytest.raises(ValueError, match='Закройте'):
            safety.assert_game_stopped()
    else:
        safety.assert_game_stopped()
    assert closed == [123]


def test_native_windows_process_api_smoke():
    import os
    from installer_safety import assert_game_stopped
    if os.name != 'nt':
        pytest.skip('Native Windows process API smoke only')
    assert_game_stopped()
