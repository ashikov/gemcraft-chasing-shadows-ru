"""Read-only Windows discovery and verified, never-restored user-data snapshots."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import stat
import re


def safe_path(path):
    """Reject links and Windows reparse points before resolving any input path."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
            raise ValueError(f'Небезопасная ссылка или hardlink: {item}')
    return path


def assert_game_stopped():
    """Fail closed if native Windows process enumeration is unavailable."""
    if os.name != 'nt':
        raise ValueError('Проверка процессов требует Windows')
    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    processes = []
    try:
        if not kernel.Process32FirstW(handle, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error())
        while True:
            processes.append((entry.th32ProcessID, entry.szExeFile.lower()))
            if not kernel.Process32NextW(handle, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != 18:  # ERROR_NO_MORE_FILES
                    raise ctypes.WinError(error)
                break
    finally:
        kernel.CloseHandle(handle)
    own_name = next((name for pid, name in processes if pid == os.getpid()), '')
    for pid, name in processes:
        if pid == os.getpid():
            continue
        # PyInstaller onefile's parent bootloader shares our exact executable name.
        if (pid == os.getppid() and name == own_name
                and re.fullmatch(r'gemcraft-ru-[0-9a-f]{12}\.exe', own_name)):
            continue
        if any(token in name for token in ('gemcraft', 'gc-cs', 'gccs', 'flashplayer',
                                            'adl.exe', 'adobe air')):
            raise ValueError(f'Закройте игру/Flash/AIR перед операцией: {name}')


def _steam_root():
    import winreg
    for hive, key, value in (
        (winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Valve\Steam', 'InstallPath'),
    ):
        try:
            with winreg.OpenKey(hive, key) as opened:
                path = safe_path(winreg.QueryValueEx(opened, value)[0])
            if path.is_dir():
                return path
        except FileNotFoundError:
            continue
    raise ValueError('Не найден основной каталог Steam; запись запрещена')


def discover_protected_roots(target):
    """Discover user AIR/Flash storage and all local accounts' game Cloud metadata."""
    if os.name != 'nt':
        raise ValueError('Обнаружение сохранений требует Windows')
    appdata = os.environ.get('APPDATA')
    if not appdata or not Path(appdata).is_absolute() or not Path(appdata).is_dir():
        raise ValueError('Не найден профиль APPDATA; запись запрещена')
    safe_path(target)
    steam = _steam_root()
    userdata = safe_path(steam / 'userdata')
    if not userdata.is_dir():
        raise ValueError('Не найден Steam userdata; запись запрещена')
    roots = [Path(appdata) / 'GCCS',
             Path(appdata) / 'Macromedia/Flash Player/#SharedObjects']
    # Missing game directories are tracked too: Cloud can create one during build.
    for account in sorted(userdata.iterdir()):
        safe_path(account)
        if account.name.isdecimal() and account.is_dir():
            roots.append(account / '296490')
    return [safe_path(path) for path in roots]


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _inventory(root):
    root = safe_path(root)
    if not root.exists():
        return {'missing': True}
    paths = [root]
    result = {}
    while paths:
        path = safe_path(paths.pop())
        info = path.stat()
        relative = str(path.relative_to(root))
        identity = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                    info.st_ctime_ns]
        if path.is_dir():
            result[relative] = {'directory': True, 'identity': identity}
            paths.extend(sorted(path.iterdir(), reverse=True))
        elif path.is_file():
            data = path.read_bytes()
            if path.stat() != info:
                raise ValueError(f'Данные изменились во время чтения: {path}')
            result[relative] = {'size': len(data), 'sha256': _digest(data),
                                'identity': identity}
        else:
            raise ValueError(f'Неподдерживаемый файл: {path}')
    return result


def snapshot(roots, destination):
    """Copy once, verify, and return a source/backup recheck for before SWF swap.

    Missing roots and empty directories are evidence too. No source is written.
    """
    roots = [safe_path(path) for path in roots]
    destination = safe_path(destination)
    for root in roots:
        if destination == root or destination.is_relative_to(root):
            raise ValueError('Backup не должен находиться внутри сохранений')
    before = [_inventory(root) for root in roots]
    destination.mkdir(parents=True, exist_ok=False)
    copies = []
    for index, (root, inventory) in enumerate(zip(roots, before)):
        for relative, record in inventory.items():
            if relative == 'missing' or record.get('directory'):
                continue
            source = safe_path(root / relative) if relative != '.' else root
            data = source.read_bytes()
            if len(data) != record['size'] or _digest(data) != record['sha256']:
                raise ValueError('Сохранения изменились; операция остановлена')
            copy = destination / str(index) / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            with copy.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if copy.read_bytes() != data:
                raise ValueError('Проверка backup сохранений не пройдена')
            copy.chmod(0o400)
            copies.append((copy, record['size'], record['sha256']))
    journal = destination / 'snapshot.json'
    encoded = (json.dumps({'roots': list(map(str, roots)), 'inventory': before},
                          ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    with journal.open('xb') as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    journal.chmod(0o400)

    def recheck():
        safe_path(destination)
        if [_inventory(root) for root in roots] != before:
            raise ValueError('Сохранения или Cloud metadata изменились; повторите проверку')
        if safe_path(journal).read_bytes() != encoded:
            raise ValueError('Manifest backup повреждён')
        for copy, size, digest in copies:
            data = safe_path(copy).read_bytes()
            if len(data) != size or _digest(data) != digest:
                raise ValueError('Backup сохранений повреждён')
    recheck()
    return recheck
