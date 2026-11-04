"""Synthetic frozen-runtime probe; never discovers or changes a game/profile."""
import importlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
import tkinter as tk

import installer_gui
import localization_project
import windows_installer
from PIL import Image, ImageFont, features
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer


RUNTIME_MODULES = (
    'abc_font_binding', 'abc_inventory', 'abc_patch', 'achievement_category_binding',
    'addressed_localization', 'advice_oracle', 'alegreya_fonts', 'alegreya_localization',
    'bitmap_font_binding', 'bitmap_notification_binding', 'bitmap_notification_placement',
    'build_prototype', 'installer_gui', 'installer_safety', 'install_patch',
    'inventory_strings', 'journey_map_localization', 'localization_bundle',
    'localization_fonts', 'localization_plan', 'localization_project',
    'localization_workspace', 'outcome_info_icons', 'pr34_ui_binding', 'preview_typography',
    'swf_geometry',
    'translation_catalog', 'tutor_highlights', 'versioned_catalog', 'windows_installer',
)


def synthetic_font():
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(['.notdef', 'A'])
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((250, 700))
    pen.lineTo((500, 0))
    pen.closePath()
    builder.setupGlyf({'.notdef': TTGlyphPen(None).glyph(), 'A': pen.glyph()})
    builder.setupHorizontalMetrics({name: (600, 0) for name in ('.notdef', 'A')})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupCharacterMap({65: 'A'})
    builder.setupNameTable({'familyName': 'Synthetic Smoke', 'styleName': 'Regular',
                            'psName': 'SyntheticSmoke', 'copyright': 'Invented fixture'})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200,
                     usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    stream = BytesIO()
    builder.save(stream)
    return stream.getvalue()


def gui_smoke():
    # A non-interactive window station is an explicit limitation, not a GUI pass.
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        class UserObjectFlags(ctypes.Structure):
            _fields_ = [('inherit', wintypes.BOOL), ('reserved', wintypes.BOOL),
                        ('flags', wintypes.DWORD)]
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.GetProcessWindowStation.restype = wintypes.HANDLE
        user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                    ctypes.c_void_p, wintypes.DWORD,
                                                    ctypes.POINTER(wintypes.DWORD)]
        flags, needed = UserObjectFlags(), wintypes.DWORD()
        if not user32.GetUserObjectInformationW(user32.GetProcessWindowStation(), 1,
                                               ctypes.byref(flags), ctypes.sizeof(flags),
                                               ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not flags.flags & 1:
            return {'status': 'skipped', 'reason': 'Windows window station is not interactive'}
    root = tk.Tk()
    class SyntheticInstaller:
        version = 'synthetic-smoke'
        def __init__(self, resources, data_dir):
            pass
    installer_gui.Installer = SyntheticInstaller
    installer_gui.default_data_dir = lambda: Path('unused-synthetic-profile')
    try:
        window = installer_gui.Window(root)
        root.update_idletasks()
        root.update()
        assert root.winfo_ismapped()
        assert len(window.buttons) == 5
        return {'status': 'passed', 'scope': 'actual Window with synthetic backend, no operations'}
    finally:
        root.destroy()


def main():
    assert getattr(sys, 'frozen', False), 'Probe must run from a frozen executable'
    root = Path(sys._MEIPASS).resolve()
    assert Path(__file__).resolve().parent == root
    assert Path.cwd().resolve() != root
    marker = json.loads((root / 'marker.json').read_text(encoding='utf-8'))
    assert marker == {'synthetic': 'ресурс без игровых данных'}
    assert importlib.util.find_spec('secret') is None
    for name in RUNTIME_MODULES:
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve().is_relative_to(root), name
    assert localization_project.build_project and windows_installer.Installer
    data = synthetic_font()
    font = TTFont(BytesIO(data))
    assert font.getBestCmap()[65] == 'A'
    assert callable(instancer.instantiateVariableFont)
    face = ImageFont.truetype(BytesIO(data), 16)
    assert face.getbbox('A')[2] > 0
    stream = BytesIO()
    Image.new('RGB', (8, 8), 'white').save(stream, format='JPEG')
    stream.seek(0)
    assert Image.open(stream).size == (8, 8)
    assert features.version('freetype2')
    report = {'frozen': True, 'resources_from_unrelated_cwd': True,
              'runtime_modules': len(RUNTIME_MODULES), 'secret_absent': True,
              'pillow_freetype_jpeg': True, 'fonttools_synthetic_font': True,
              'gui': gui_smoke()}
    Path(sys.argv[1]).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding='utf-8')


if __name__ == '__main__':
    main()
