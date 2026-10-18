"""Restore and reproduce the private translation project from Git and owned inputs."""
import argparse
import json
from pathlib import Path
import tempfile

from install_patch import ORIGINAL_SHA256, sha
from inventory_strings import extract_inventory
from localization_workspace import validate_workspace, write_json
from translation_catalog import load_catalog

PROJECT = Path(__file__).resolve().parent
DEFAULT_CATALOG = PROJECT / 'translations/catalog.json'
DEFAULT_MANIFEST = PROJECT / 'translations/build-manifest.json'
PROVENANCE = PROJECT / 'fonts/alegreya/provenance.json'


def _project_data(catalog_path, manifest_path):
    from versioned_catalog import validate_catalog_data
    catalog = load_catalog(catalog_path)
    errors = validate_catalog_data(catalog)
    if errors:
        raise ValueError('; '.join(errors))
    manifest = load_catalog(manifest_path)
    font = load_catalog(PROVENANCE)['files']['Alegreya[wght].ttf']
    if manifest['game_sha256'] != ORIGINAL_SHA256:
        raise ValueError('Manifest original SHA mismatch')
    if manifest['font_sha256'] != font['sha256']:
        raise ValueError('Manifest font differs from pinned provenance')
    return catalog, manifest


def validate_project(catalog_path=DEFAULT_CATALOG, manifest_path=DEFAULT_MANIFEST):
    """Check Git structure without claiming to verify unavailable English origins."""
    from localization_bundle import validate_bundle_structure
    catalog, manifest = _project_data(catalog_path, manifest_path)
    # Sparse overrides omit defaults supplied by the original inventory. This
    # projection proves plan selection/addresses/status, not original context.
    projection = {'game_sha256': catalog['game_sha256'], 'entries': [
        {**{key: row[key] for key in ('id', 'category', 'location')}, **row['manual']}
        for row in catalog['entries']]}
    merged = validate_bundle_structure(manifest_path, projection)
    return {
        'game_sha256': catalog['game_sha256'],
        'catalog_sha256': sha(Path(catalog_path).read_bytes()),
        'translated_entries': sum(isinstance(row['manual'].get('translation'), str)
                                  for row in catalog['entries']),
        'applied_translations': len(merged['entry_ids']),
        'plans': len(manifest['plans']),
        'expected_candidate_sha256': manifest['expected_candidate_sha256'],
        'validation': 'Git structure only; original SWF not checked',
    }


def _fresh_output(output_dir):
    output_dir = Path(output_dir)
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError('Use a fresh output directory')
    resolved = output_dir.resolve()
    allowed = [PROJECT / name for name in ('local', 'output', 'artifacts')]
    allowed.append(Path(tempfile.gettempdir()).resolve())
    if not any(resolved.is_relative_to(root.resolve()) and resolved != root.resolve()
               for root in allowed):
        raise ValueError('Output must be in local/, output/, artifacts/ or a temporary directory')
    return output_dir


def prepare_workspace(source, output_dir, catalog_path=DEFAULT_CATALOG,
                      manifest_path=DEFAULT_MANIFEST):
    """Create a new derived workspace; never write to an existing user's data."""
    from localization_bundle import load_bundle
    from versioned_catalog import catalog_digest, restore_workspace
    output_dir = _fresh_output(output_dir)
    original = Path(source).read_bytes()
    if sha(original) != ORIGINAL_SHA256:
        raise ValueError('Original SHA-256 mismatch')
    catalog, _ = _project_data(catalog_path, manifest_path)
    inventory = extract_inventory(original, ORIGINAL_SHA256)
    workspace = restore_workspace(catalog, inventory)
    plan = load_bundle(manifest_path, workspace)
    errors = validate_workspace(workspace, inventory)
    if errors:
        raise ValueError('; '.join(errors))
    output_dir.mkdir(parents=True, exist_ok=False)
    for name, data in (('inventory', inventory), ('workspace', workspace), ('plan', plan)):
        write_json(output_dir / f'{name}.json', data)
    write_json(output_dir / 'catalog-origin.json', {'catalog_sha256': catalog_digest(catalog)})
    return workspace, plan


def check_workspace(workspace_path, inventory_path, catalog_path=DEFAULT_CATALOG,
                    manifest_path=DEFAULT_MANIFEST):
    from localization_bundle import load_bundle
    from versioned_catalog import check_sync
    catalog, _ = _project_data(catalog_path, manifest_path)
    workspace, inventory = load_catalog(workspace_path), load_catalog(inventory_path)
    errors = check_sync(workspace, inventory, catalog)
    if errors:
        raise ValueError('; '.join(errors))
    load_bundle(manifest_path, workspace)
    return {'synchronized': True, 'workspace_entries': len(workspace['entries'])}


def build_project(source, font_path, output_dir, catalog_path=DEFAULT_CATALOG,
                  manifest_path=DEFAULT_MANIFEST):
    """Build, reread and independently rebuild using the existing disk validator."""
    from alegreya_localization import build_alegreya_localization, validate_alegreya_localization
    _fresh_output(output_dir)
    _, manifest = _project_data(catalog_path, manifest_path)
    font = Path(font_path).read_bytes()
    if sha(font) != manifest['font_sha256']:
        raise ValueError('Alegreya SHA-256 mismatch')
    workspace, plan = prepare_workspace(source, output_dir, catalog_path, manifest_path)
    original = Path(source).read_bytes()
    print(json.dumps({'phase': 'workspace restored',
                      'selected_translations': len(plan['entry_ids'])}), flush=True)
    candidate, report = build_alegreya_localization(original, workspace, plan, font)
    if report['applied_translations'] != manifest['applied_translations']:
        raise ValueError('Applied translation count differs from manifest')
    if sha(candidate) != manifest['expected_candidate_sha256']:
        raise ValueError('Candidate SHA differs from manifest; investigate recipe drift')
    print(json.dumps({'phase': 'candidate built', 'sha256': sha(candidate)}), flush=True)
    output_dir = Path(output_dir)
    destination = output_dir / 'gc-cs-steam.ru.swf'
    with destination.open('xb') as handle:
        handle.write(candidate)
    on_disk = destination.read_bytes()
    if on_disk != candidate:
        raise ValueError('Candidate reread differs')
    validation = validate_alegreya_localization(original, on_disk, workspace, plan, font)
    if validation != report:
        raise ValueError('On-disk validation report differs')
    write_json(output_dir / 'static-validation.json', report)
    write_json(output_dir / 'on-disk-validation.json', validation)
    print(json.dumps({'phase': 'on-disk validation complete',
                      'sha256': report['patched_sha256'],
                      'applied_translations': report['applied_translations']}), flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=DEFAULT_CATALOG)
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('validate')
    for command in ('restore', 'build'):
        sub = commands.add_parser(command)
        sub.add_argument('--source', type=Path, required=True)
        sub.add_argument('--output-dir', type=Path, required=True)
        if command == 'build':
            sub.add_argument('--alegreya-font', type=Path, required=True)
    check = commands.add_parser('check')
    check.add_argument('--workspace', type=Path, required=True)
    check.add_argument('--inventory', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'validate':
            result = validate_project(args.catalog, args.manifest)
        elif args.command == 'check':
            result = check_workspace(args.workspace, args.inventory, args.catalog, args.manifest)
        elif args.command == 'restore':
            workspace, plan = prepare_workspace(args.source, args.output_dir,
                                                args.catalog, args.manifest)
            result = {'workspace_entries': len(workspace['entries']),
                      'selected_translations': len(plan['entry_ids'])}
        else:
            result = build_project(args.source, args.alegreya_font, args.output_dir,
                                   args.catalog, args.manifest)
            result = {key: result[key] for key in
                      ('status', 'original_sha256', 'patched_sha256', 'applied_translations')}
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'Localization project error: {error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    main()
