"""Offline installer orchestration over the shared localization and file APIs."""
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import uuid

from install_patch import (
    apply_patch_file, restore_patch_file, sha, update_patch_file, verified_manifest,
)
from installer_safety import (
    assert_game_stopped, discover_protected_roots, safe_path, snapshot,
)
from localization_project import build_project, validate_project


def default_data_dir():
    value = os.environ.get('LOCALAPPDATA')
    if os.name != 'nt' or not value:
        raise ValueError('Установщик предназначен для Windows; LOCALAPPDATA недоступен.')
    return Path(value) / 'GemCraftRUInstaller'


def verify_resources(root):
    """The packager pins all private inputs, including the otherwise sparse catalog."""
    root = Path(root)
    inventory = json.loads((root / 'installer-resources.json').read_text(encoding='utf-8'))
    if inventory.get('schema') != 1 or not isinstance(inventory.get('files'), dict):
        raise ValueError('Некорректный manifest ресурсов установщика.')
    files = inventory['files']
    manifest = json.loads((root / 'translations/build-manifest.json').read_text(encoding='utf-8'))
    required = {'translations/catalog.json', 'translations/build-manifest.json',
                'fonts/alegreya/provenance.json', 'fonts/alegreya/OFL.txt',
                'fonts/alegreya/Alegreya[wght].ttf'}
    required.update('translations/' + item['path'] for item in manifest['plans'])
    if not required.issubset(files):
        raise ValueError('Неполный manifest ресурсов установщика.')
    for name, digest in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
            raise ValueError('Небезопасный путь ресурса.')
        path = root / name
        safe_path(path)
        if sha(path.read_bytes()) != digest:
            raise ValueError(f'Изменён ресурс установщика: {name}')
    validate_project(root / 'translations/catalog.json', root / 'translations/build-manifest.json')
    if sha((root / 'fonts/alegreya/Alegreya[wght].ttf').read_bytes()) != manifest['font_sha256']:
        raise ValueError('Alegreya SHA-256 mismatch')


def _write_record(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o400)


class Installer:
    def __init__(self, resources, data_dir):
        self.resources = Path(resources).absolute()
        self.data_dir = Path(data_dir).absolute()
        verify_resources(self.resources)
        self.manifest = json.loads((self.resources / 'translations/build-manifest.json').read_text(
            encoding='utf-8'))
        self.version = self.manifest['expected_candidate_sha256'][:12]

    def _target(self, value):
        path = Path(value).expanduser().absolute()
        safe_path(path)
        if path.is_dir():
            path = path / 'gc-cs-steam.swf'
        safe_path(path)
        if path.name != 'gc-cs-steam.swf' or not path.is_file():
            raise ValueError('Выберите папку игры или существующий gc-cs-steam.swf.')
        return path

    def _store(self, target):
        safe_path(self.data_dir)
        if (self.data_dir == target.parent or self.data_dir.is_relative_to(target.parent)
                or target.is_relative_to(self.data_dir)
                or self.data_dir.is_relative_to(self.resources)):
            raise ValueError('Каталог данных должен быть вне игры и ресурсов установщика.')
        return self.data_dir / sha(os.path.normcase(str(target)).encode('utf-8'))

    def inspect(self, value):
        target = self._target(value)
        store = self._store(target)
        current = sha(target.read_bytes())
        info = {'state': 'unknown', 'sha256': current, 'version': current[:12],
                'target': str(target), 'backup': str(store), 'manifest': None}
        if current == self.manifest['game_sha256']:
            info['state'] = 'original'
            return info
        manifest = store / f'manifest-{current}.json'
        if not manifest.exists():
            return info
        safe_path(manifest)
        record = verified_manifest(manifest, self.manifest['game_sha256'], target)
        backup = store / (target.name + '.' + self.manifest['game_sha256'] + '.original')
        if Path(record['backup_path']) != backup or record['patched_sha256'] != current:
            raise ValueError('Manifest не соответствует этой установке; операция запрещена.')
        info.update(state='localized', manifest=str(manifest))
        return info

    def perform(self, value, action, expected_sha):
        if action not in ('install', 'restore'):
            raise ValueError('Неизвестная операция.')
        verify_resources(self.resources)
        # A caller must confirm this exact inspected state, never just the filename.
        info = self.inspect(value)
        target = Path(info['target'])
        if info['sha256'] != expected_sha or info['state'] == 'unknown':
            raise ValueError('Неизвестный или изменившийся SWF. Повторите проверку.')
        if info['state'] == 'original' and action == 'restore':
            return info
        if info['state'] == 'localized' and expected_sha == self.manifest['expected_candidate_sha256'] and action == 'install':
            return info
        target_stat = target.stat()
        identity = (target_stat.st_dev, target_stat.st_ino, target_stat.st_size,
                    target_stat.st_mtime_ns, target_stat.st_ctime_ns)
        assert_game_stopped()
        roots = discover_protected_roots(target)
        store = self._store(target)
        safe_path(store)
        # Never write backups into any discovered user-data tree.
        if any(self.data_dir.is_relative_to(root) or root.is_relative_to(self.data_dir) for root in roots):
            raise ValueError('Каталог backup пересекается с пользовательскими данными.')
        store.mkdir(parents=True, exist_ok=True)
        lock = store / 'operation.lock'
        # Exclusive ownership across double-clicks and multiple installer versions.
        with lock.open('x', encoding='utf-8') as handle:
            handle.write(str(os.getpid()))
        try:
            operation = store / ('operation-' + uuid.uuid4().hex)
            operation.mkdir()
            _write_record(operation / 'localization.json', {
                'build_manifest': self.manifest,
                'resources_sha256': sha((self.resources / 'installer-resources.json').read_bytes())
                if (self.resources / 'installer-resources.json').exists() else None,
            })
            recheck = snapshot(roots, operation / 'userdata')
            original_info = dict(info)

            def guard():
                safe_path(target)
                current_stat = target.stat()
                if (current_stat.st_dev, current_stat.st_ino, current_stat.st_size,
                        current_stat.st_mtime_ns, current_stat.st_ctime_ns) != identity:
                    raise ValueError('Файл игры изменился во время операции.')
                safe_path(store)
                assert_game_stopped()
                if discover_protected_roots(target) != roots:
                    raise ValueError('Изменился список пользовательских данных.')
                recheck()
                now = self.inspect(target)
                if now != original_info:
                    raise ValueError('Состояние установки изменилось; операция остановлена.')

            try:
                guard()
                if action == 'restore':
                    restore_patch_file(info['manifest'], before_replace=guard,
                                       expected_original=self.manifest['game_sha256'])
                else:
                    # Snapshot the build input so concurrent Steam changes cannot become input.
                    source = target if info['state'] == 'original' else Path(json.loads(
                        Path(info['manifest']).read_text(encoding='utf-8'))['backup_path'])
                    original = source.read_bytes()
                    if sha(original) != self.manifest['game_sha256']:
                        raise ValueError('Original SHA-256 mismatch')
                    with tempfile.TemporaryDirectory(prefix='gemcraft-ru-') as temporary:
                        work = Path(temporary)
                        source_copy = work / 'original.swf'
                        source_copy.write_bytes(original)
                        output = work / 'candidate'
                        build_project(source_copy, self.resources / 'fonts/alegreya/Alegreya[wght].ttf',
                                      output, catalog_path=self.resources / 'translations/catalog.json',
                                      manifest_path=self.resources / 'translations/build-manifest.json')
                        guard()
                        candidate = output / 'gc-cs-steam.ru.swf'
                        if info['state'] == 'original':
                            apply_patch_file(target, candidate, store, self.manifest['game_sha256'],
                                             self.manifest['expected_candidate_sha256'], before_replace=guard)
                        else:
                            update_patch_file(target, candidate, store, info['manifest'],
                                              self.manifest['game_sha256'],
                                              self.manifest['expected_candidate_sha256'], before_replace=guard)
                result = self.inspect(target)
                _write_record(operation / 'result.json', {'action': action, 'before': info,
                                                         'after': result})
                return result
            except Exception as error:
                _write_record(operation / 'error.json', {'action': action, 'error': str(error)})
                raise
        finally:
            lock.unlink()
