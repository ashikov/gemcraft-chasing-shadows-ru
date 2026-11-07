"""Native Windows private-preview packaging; no game assets enter the bundle."""
import argparse
import ctypes
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
FRIBIDI_FILES = {
    'fribidi.dll': '78b063ae41832d256c5ad8677c2e10fae18fbe7e623dc2ccf279ee3dcbdba1fc',
    'fribidi-1.0.17-COPYING': '20e50fe7aae3e56378ebf0417d9de904f55a0e61e4df315333e632a4d3555d95',
}


def stage_resources(destination, font, project=PROJECT, *, fribidi_dir=None):
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
    if fribidi_dir is not None:
        files.update({name: Path(fribidi_dir) / name for name in FRIBIDI_FILES})
    payload = {relative: source.read_bytes() for relative, source in sorted(files.items())}
    if fribidi_dir is not None:
        for name, digest in FRIBIDI_FILES.items():
            if sha(payload[name]) != digest:
                raise ValueError(f'FriBidi resource SHA mismatch: {name}')
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
        option = '--add-data'
        if path.suffix.lower() == '.dll':
            if relative != 'fribidi.dll':
                raise ValueError('Unapproved packaged DLL')
            if digest != FRIBIDI_FILES[relative]:
                raise ValueError('FriBidi DLL SHA mismatch')
            option = '--add-binary'
        command += [option, str(path) + os.pathsep + str(Path(relative).parent)]
    command += ['--add-data', str(resources / 'installer-resources.json') + os.pathsep + '.',
                str(entry)]
    return command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Owned verified original SWF')
    parser.add_argument('--font', type=Path, required=True)
    parser.add_argument('--fribidi-dir', type=Path, required=True,
                        help='Pinned FriBidi 1.0.17 x64 DLL and COPYING directory')
    parser.add_argument('--output', type=Path, default=PROJECT / 'output/windows-installer')
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        parser.error('Build natively on Windows with Windows Python (cross compilation unsupported)')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='gemcraft-installer-build-') as temporary:
        temporary = Path(temporary)
        stage = temporary / 'resources'
        stage_resources(stage, args.font, fribidi_dir=args.fribidi_dir)
        # Pillow selects optional shaping support when its extension first loads.
        # Loading the persistent input avoids locking our temporary staging directory.
        fribidi = args.fribidi_dir.resolve() / 'fribidi.dll'
        if sha(fribidi.read_bytes()) != FRIBIDI_FILES['fribidi.dll']:
            raise ValueError('FriBidi DLL SHA mismatch before loading')
        ctypes.WinDLL(str(fribidi))
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
