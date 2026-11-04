import pytest

from installer_safety import safe_path, snapshot


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
