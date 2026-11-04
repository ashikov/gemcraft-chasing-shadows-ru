"""Build/run a disposable synthetic onefile; never upload its binary or resources."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from build_windows_installer import packaging_command  # noqa: E402
from install_patch import sha  # noqa: E402


def main():
    if sys.platform != 'win32':
        raise SystemExit('Synthetic onefile smoke requires native Windows Python')
    with tempfile.TemporaryDirectory(prefix='installer-synthetic-smoke-') as temporary:
        temporary = Path(temporary)
        resources = temporary / 'resources'
        resources.mkdir()
        marker = json.dumps({'synthetic': 'ресурс без игровых данных'}, ensure_ascii=False)
        (resources / 'marker.json').write_text(marker, encoding='utf-8')
        (resources / 'installer-resources.json').write_text(json.dumps({
            'schema': 1, 'files': {'marker.json': sha(marker.encode('utf-8'))}}), encoding='utf-8')
        command = packaging_command(PROJECT / 'tests/packaging_smoke_entry.py', resources,
                                    temporary / 'dist', temporary / 'build',
                                    'Synthetic-Installer-Smoke', windowed=False)
        # A discoverable, unreferenced root module must remain outside the executable.
        extra = temporary / 'unreferenced'
        extra.mkdir()
        (extra / 'secret.py').write_text("raise RuntimeError('must never be bundled')\n")
        command[-1:-1] = ['--paths', str(extra)]
        environment = dict(os.environ, PYTHONHASHSEED='0', SOURCE_DATE_EPOCH='1704067200')
        subprocess.run(command, check=True, timeout=300, env=environment)
        unrelated = temporary / 'другой рабочий каталог'
        unrelated.mkdir()
        report_path = temporary / 'report.json'
        executable = temporary / 'dist/Synthetic-Installer-Smoke.exe'
        subprocess.run([str(executable), str(report_path)], cwd=unrelated, check=True,
                       timeout=60, env=environment)
        report = json.loads(report_path.read_text(encoding='utf-8'))
        assert report['frozen'] and report['secret_absent']
        assert report['resources_from_unrelated_cwd']
        assert report['pillow_freetype_jpeg'] and report['fonttools_synthetic_font']
        assert report['runtime_modules'] >= 25
        assert report['gui']['status'] in ('passed', 'skipped')
        print('SYNTHETIC PACKAGING SMOKE: ' + json.dumps(report, ensure_ascii=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
