"""Integrity-checked ordered localization plans for a private Git checkout."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from localization_plan import merge_localization_plans


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_unique_object)


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def _digest(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('Invalid bundle SHA-256')
    return value


def _plan_path(root, name):
    if (not isinstance(name, str) or re.fullmatch(r'plans/[a-z0-9-]+\.json', name) is None
            or PurePosixPath(name).is_absolute()):
        raise ValueError('Invalid confined plan filename')
    path = root
    for part in PurePosixPath(name).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('Symlink plan paths are forbidden')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Plan path escapes bundle')
    return path


def read_bundle(manifest_path):
    """Check manifest and exact plan file hashes, returning manifest and plans."""
    manifest_path = Path(manifest_path)
    if manifest_path.is_symlink():
        raise ValueError('Symlink manifest is forbidden')
    manifest = _read_json(manifest_path)
    required = {'schema_version', 'game_sha256', 'font_sha256',
                'expected_candidate_sha256', 'applied_translations', 'plans'}
    if (not isinstance(manifest, dict) or set(manifest) != required
            or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1):
        raise ValueError('Invalid bundle manifest schema')
    for key in ('game_sha256', 'font_sha256', 'expected_candidate_sha256'):
        _digest(manifest[key])
    count = manifest['applied_translations']
    if type(count) is not int or count < 1:
        raise ValueError('Invalid applied translation count')
    if not isinstance(manifest['plans'], list) or not manifest['plans']:
        raise ValueError('Missing ordered plans')
    seen, plans = set(), []
    for item in manifest['plans']:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256'}:
            raise ValueError('Invalid plan manifest item')
        path = _plan_path(manifest_path.parent, item['path'])
        if item['path'] in seen:
            raise ValueError('Duplicate plan file')
        seen.add(item['path'])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != _digest(item['sha256']):
            raise ValueError(f'Plan SHA-256 mismatch: {item["path"]}')
        plan = _read_json(path)
        if (not isinstance(plan, dict) or type(plan.get('schema_version')) is not int
                or plan['schema_version'] != 1
                or plan.get('game_sha256') != manifest['game_sha256']):
            raise ValueError(f'Plan schema or original SHA mismatch: {item["path"]}')
        plans.append(plan)
    return manifest, plans


def _merge_bundle(manifest_path, workspace, *, check_context):
    manifest, plans = read_bundle(manifest_path)
    if workspace.get('game_sha256') != manifest['game_sha256']:
        raise ValueError('Workspace original SHA-256 mismatch')
    if workspace.get('conflicts'):
        raise ValueError('Workspace contains unresolved conflicts')
    merged = merge_localization_plans(plans, workspace)
    if len(merged['entry_ids']) != manifest['applied_translations']:
        raise ValueError('Applied translation count differs from manifest')
    entries = {entry['id']: entry for entry in workspace['entries']}
    for identifier in merged['entry_ids']:
        entry = entries[identifier]
        if (entry.get('status') not in ('draft', 'verified')
                or not isinstance(entry.get('translation'), str)
                or (check_context and (entry.get('classification') != 'confirmed'
                                       or entry.get('context_confidence') != 'confirmed'))):
            raise ValueError(f'Selected translation is not ready: {identifier}')
    return merged


def load_bundle(manifest_path, workspace):
    """Merge unchanged plans and require every selected translation fully ready."""
    return _merge_bundle(manifest_path, workspace, check_context=True)


def validate_bundle_structure(manifest_path, catalog_workspace):
    """Git-only integrity check using sparse catalog entries without source context.

    This verifies selections, addresses, statuses and plan hashes. It cannot
    establish original-source provenance or replace restoration/build validation.
    """
    return _merge_bundle(manifest_path, catalog_workspace, check_context=False)


def migrate_plans(source_paths, plan_names, destination, workspace, *,
                  font_sha256, expected_sha256, applied_count):
    """Losslessly serialize complete plans into a new, exclusively created bundle.

    Input order is preserved. Existing destinations are never overwritten, and
    all plan semantics are checked before any destination is created.
    """
    source_paths, plan_names = list(source_paths), list(plan_names)
    if not source_paths or len(source_paths) != len(plan_names) or len(set(plan_names)) != len(plan_names):
        raise ValueError('Source plans need unique destination names')
    plans = [_read_json(path) for path in source_paths]
    merged = merge_localization_plans(plans, workspace)
    if workspace.get('conflicts'):
        raise ValueError('Workspace contains unresolved conflicts')
    if merged['game_sha256'] != workspace.get('game_sha256'):
        raise ValueError('Workspace original SHA-256 mismatch')
    if type(applied_count) is not int or applied_count < 1 or len(merged['entry_ids']) != applied_count:
        raise ValueError('Applied translation count differs from migration')
    entries = {entry['id']: entry for entry in workspace['entries']}
    for identifier in merged['entry_ids']:
        entry = entries[identifier]
        if (entry.get('status') not in ('draft', 'verified')
                or entry.get('classification') != 'confirmed'
                or entry.get('context_confidence') != 'confirmed'
                or not isinstance(entry.get('translation'), str)):
            raise ValueError(f'Selected translation is not ready: {identifier}')
    destination = Path(destination)
    manifest = {'schema_version': 1, 'game_sha256': _digest(merged['game_sha256']),
                'font_sha256': _digest(font_sha256),
                'expected_candidate_sha256': _digest(expected_sha256),
                'applied_translations': applied_count, 'plans': []}
    files = []
    for name, plan in zip(plan_names, plans):
        relative = f'plans/{name}'
        _plan_path(destination, relative)
        raw = _encoded(plan)
        files.append((relative, raw))
        manifest['plans'].append({'path': relative, 'sha256': hashlib.sha256(raw).hexdigest()})
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'plans').mkdir()
    for name, raw in files:
        with (destination / name).open('xb') as stream:
            stream.write(raw)
    manifest_path = destination / 'build-manifest.json'
    with manifest_path.open('xb') as stream:
        stream.write(_encoded(manifest))
    load_bundle(manifest_path, workspace)
    return manifest_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('validate')
    check.add_argument('manifest', type=Path)
    check.add_argument('--workspace', required=True, type=Path)
    migrate = commands.add_parser('migrate')
    migrate.add_argument('--plan', action='append', required=True, metavar='NAME=SOURCE')
    migrate.add_argument('--workspace', required=True, type=Path)
    migrate.add_argument('--output', required=True, type=Path)
    migrate.add_argument('--font-sha256', required=True)
    migrate.add_argument('--expected-sha256', required=True)
    migrate.add_argument('--applied-count', required=True, type=int)
    args = parser.parse_args(argv)
    workspace = _read_json(args.workspace)
    if args.command == 'validate':
        merged = load_bundle(args.manifest, workspace)
        print(f'Valid bundle: {len(merged["entry_ids"])} applied translations')
    else:
        pairs = [value.split('=', 1) for value in args.plan]
        if any(len(pair) != 2 for pair in pairs):
            parser.error('--plan requires NAME=SOURCE')
        path = migrate_plans([pair[1] for pair in pairs], [pair[0] for pair in pairs],
                             args.output, workspace, font_sha256=args.font_sha256,
                             expected_sha256=args.expected_sha256, applied_count=args.applied_count)
        print(path)


if __name__ == '__main__':
    main()
