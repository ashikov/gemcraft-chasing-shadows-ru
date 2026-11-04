"""Native Windows private-preview packaging; no game assets enter the bundle."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from install_patch import sha
from localization_project import build_project, validate_project

PROJECT = Path(__file__).resolve().parent


def stage_resources(destination, font, project=PROJECT):
    """Create a deterministic, hashed allowlist; never copy local directories."""
    destination, project, font = map(Path, (destination, project, font))
    validate_project(project / 'translations/catalog.json',
                     project / 'translations/build-manifest.json')
    manifest = json.loads((project / 'translations/build-manifest.json').read_text('utf-8'))
    if sha(font.read_bytes()) != manifest['font_sha256']:
        raise ValueError('Alegreya SHA-256 mismatch')
    files = {f'translations/{name}': project / 'translations' / name
             for name in ('catalog.json', 'build-manifest.json')}
    for plan in manifest['plans']:
        relative = Path(plan['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.suffix != '.json':
            raise ValueError('Unsafe plan path')
        files['translations/' + relative.as_posix()] = project / 'translations' / relative
    for name in ('provenance.json', 'OFL.txt'):
        files['fonts/alegreya/' + name] = project / 'fonts/alegreya' / name
    files['fonts/alegreya/Alegreya[wght].ttf'] = font
    payload = {relative: source.read_bytes() for relative, source in sorted(files.items())}
    destination.mkdir(parents=True, exist_ok=False)
    for relative, data in payload.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    inventory = {'schema': 1, 'files': {name: sha(data) for name, data in payload.items()}}
    (destination / 'installer-resources.json').write_text(
        json.dumps(inventory, ensure_ascii=False, sort_keys=True, indent=2) + '\n', 'utf-8')
    return inventory


def packaging_command(entry, resources, output, work, name, *, windowed=True):
    """Use PyInstaller's import graph for code; only explicit resources are data."""
    entry, resources, output, work = map(Path, (entry, resources, output, work))
    inventory = json.loads((resources / 'installer-resources.json').read_text('utf-8'))
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--onefile', '--windowed' if windowed else '--console', '--noupx',
               '--name', name, '--distpath', str(output), '--workpath', str(work),
               '--specpath', str(work.parent), '--paths', str(PROJECT),
               '--collect-all', 'PIL', '--collect-all', 'fontTools']
    for relative, digest in inventory['files'].items():
        path = resources / relative
        if (Path(relative).is_absolute() or '..' in Path(relative).parts
                or path.suffix == '.py' or not path.resolve().is_relative_to(resources.resolve())):
            raise ValueError('Unsafe packaged data path')
        if sha(path.read_bytes()) != digest:
            raise ValueError('Packaged resource SHA mismatch')
        command += ['--add-data', str(path) + os.pathsep + str(Path(relative).parent)]
    command += ['--add-data', str(resources / 'installer-resources.json') + os.pathsep + '.',
                str(entry)]
    return command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Owned verified original SWF')
    parser.add_argument('--font', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=PROJECT / 'output/windows-installer')
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        parser.error('Build natively on Windows with Windows Python (cross compilation unsupported)')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='gemcraft-installer-build-') as temporary:
        temporary = Path(temporary)
        stage = temporary / 'resources'
        stage_resources(stage, args.font)
        # Independently build twice before packaging; production validation rereads disk.
        for index in range(2):
            build_project(args.source, stage / 'fonts/alegreya/Alegreya[wght].ttf',
                          temporary / f'candidate-{index}',
                          catalog_path=stage / 'translations/catalog.json',
                          manifest_path=stage / 'translations/build-manifest.json')
        manifest = json.loads((stage / 'translations/build-manifest.json').read_text('utf-8'))
        name = 'GemCraft-RU-' + manifest['expected_candidate_sha256'][:12]
        command = packaging_command(PROJECT / 'installer_gui.py', stage, args.output,
                                    temporary / 'work', name)
        environment = dict(os.environ, PYTHONHASHSEED='0', SOURCE_DATE_EPOCH='1704067200')
        subprocess.run(command, check=True, env=environment)
        shutil.copyfile(stage / 'installer-resources.json', args.output / (name + '-inputs.json'))
        executable = args.output / (name + '.exe')
        (args.output / (name + '.sha256')).write_text(sha(executable.read_bytes()) + '\n', 'ascii')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
