"""Opt-in offline tutor-panel (sprite 3608) candidate checks on temp copies.

Never launches the game. Requires the private original, the reviewed workspace,
the three accepted PR4 plans, the tutor plan and the Alegreya source:

GEMCRAFT_ORIGINAL GEMCRAFT_PR4_WORKSPACE GEMCRAFT_PR4_PLANS
GEMCRAFT_PR5_TUTOR_PLAN GEMCRAFT_ALEGREYA_FONT
"""
import json
import os
import re
from pathlib import Path

import pytest

from alegreya_localization import build_alegreya_localization
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from inventory_strings import parse_edit_text
from localization_plan import merge_localization_plans
from swf_geometry import Reader, parse_font, parse_text, read_swf, read_tags
from translation_catalog import load_catalog

pytestmark = pytest.mark.local

VARIABLES = ['GEMCRAFT_ORIGINAL', 'GEMCRAFT_PR4_WORKSPACE', 'GEMCRAFT_PR4_PLANS',
             'GEMCRAFT_PR5_TUTOR_PLAN', 'GEMCRAFT_ALEGREYA_FONT']


def tutor_frames(tags):
    """Independently re-derive McTutorPanel's per-frame text characters."""
    sprite = next(t for t in tags if t.code == 39
                  and int.from_bytes(t.payload[:2], 'little') == 3608)
    reader = Reader(sprite.payload)
    assert reader.u16() == 3608
    declared = reader.u16()
    children = read_tags(reader.take(len(reader.data) - reader.pos))
    text_ids = {int.from_bytes(t.payload[:2], 'little')
                for t in tags if t.code in (11, 33, 37)}
    frames, frame = [], 0
    current = []
    for child in children:
        if child.code == 26:
            inner = Reader(child.payload)
            flags = inner.u8()
            inner.u16()
            if flags & 2:
                cid = inner.u16()
                if cid in text_ids:
                    current.append(cid)
        elif child.code == 1:
            frames.append(current)
            current = []
            frame += 1
    assert frame == declared == 30
    return frames


def resolved_static(tag, fonts):
    """Unicode text, advances and geometry of a static text, index-free."""
    text = parse_text(tag.payload)
    rows = []
    active_font = active_height = None
    for record in text['records']:
        active_font = record.get('font_id', active_font)
        active_height = record.get('height', active_height)
        rows.append((''.join(chr(fonts[active_font]['codes'][i]) for i, _ in record['entries']),
                     tuple(a for _, a in record['entries']),
                     record.get('x'), record.get('y'), active_height,
                     record.get('color')))
    return text['bounds'], text['matrix_raw'], rows


def test_tutor_cumulative_candidate(tmp_path):
    if not all(os.environ.get(v) for v in VARIABLES):
        pytest.skip('Set private original, workspace, plan lists and Alegreya source')
    original = Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes()
    workspace = load_catalog(os.environ['GEMCRAFT_PR4_WORKSPACE'])
    entries = {e['id']: e for e in workspace['entries']}
    plans = [load_catalog(p) for p in json.loads(os.environ['GEMCRAFT_PR4_PLANS'])]
    tutor = load_catalog(os.environ['GEMCRAFT_PR5_TUTOR_PLAN'])
    font = Path(os.environ['GEMCRAFT_ALEGREYA_FONT']).read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    baseline, base_report = build_alegreya_localization(original, workspace, plans, font)
    candidate, report = build_alegreya_localization(original, workspace, plans + [tutor], font)
    again, repeated = build_alegreya_localization(original, workspace, plans + [tutor], font)
    assert candidate == again and report == repeated

    # Frame coverage: every text character placed in sprite 3608 is selected.
    original_tags = read_swf(original)[1]
    edit_ids = {int.from_bytes(t.payload[:2], 'little') for t in original_tags if t.code == 37}
    frames = tutor_frames(original_tags)
    expected = {f"{'edit' if cid in edit_ids else 'static'}.{cid}"
                for frame in frames for cid in frame}
    assert set(tutor['entry_ids']) == expected
    assert len(tutor['entry_ids']) == 73
    # No tutor element is silently uncovered: the blockers list is exactly the
    # inherited PR4 one.
    merged = merge_localization_plans(plans + [tutor], workspace)
    assert merged['coverage_blockers'] == merge_localization_plans(plans, workspace)['coverage_blockers']

    # All earlier translations keep their checked geometry.
    assert report['applied_translations'] == base_report['applied_translations'] + 73
    assert all(report['layout_checks'][i] == base_report['layout_checks'][i]
               for i in base_report['layout_checks'])

    old_tags, new_tags = read_swf(baseline)[1], read_swf(candidate)[1]

    def by_character(tags):
        return {int.from_bytes(t.payload[:2], 'little'): t for t in tags
                if t.code in (11, 33, 37, 75)}

    old_chars, new_chars = by_character(old_tags), by_character(new_tags)
    old_fonts = {i: parse_font(t.payload) for i, t in old_chars.items() if t.code == 75}
    new_fonts = {i: parse_font(t.payload) for i, t in new_chars.items() if t.code == 75}
    tutor_statics = {int(i.split('.')[1]) for i in tutor['entry_ids'] if i.startswith('static.')}
    tutor_edits = {int(i.split('.')[1]) for i in tutor['entry_ids'] if i.startswith('edit.')}

    # Every tutor text now carries its reviewed Russian lines.
    for cid in sorted(tutor_statics):
        _, _, rows = resolved_static(new_chars[cid], new_fonts)
        layout = tutor['layouts'][f'static.{cid}']
        wanted = layout.get('lines') or [entries[f'static.{cid}']['translation']]
        assert [row[0] for row in rows] == wanted
    for cid in sorted(tutor_edits):
        _, source, formatting = parse_edit_text(new_chars[cid].payload)
        visible = re.sub(r'<[^>]+>', '', entries[f'edit.{cid}']['translation'])
        assert visible and visible in source
        assert 'GCCS Alegreya' in source
        assert formatting['font_id'] == 6002

    # Untouched character resources are byte-identical to the PR4 baseline;
    # PR4 static texts may reference shifted glyph indices and are compared
    # resolved by Unicode, advances, offsets and styling.
    for cid, tag in old_chars.items():
        if cid in tutor_statics or cid in tutor_edits or cid in (6001, 6002):
            continue
        if tag.code in (11, 33):
            assert resolved_static(tag, old_fonts) == resolved_static(new_chars[cid], new_fonts)
        else:
            assert new_chars[cid] == tag
    # The shared ABC tag is byte-identical: RUNTIME-001 repair, all earlier
    # translations and font bindings are intact, and no tutor ABC was added.
    assert next(t for t in old_tags if t.code == 82) == next(t for t in new_tags if t.code == 82)
    # Non-character tag streams stay aligned except for the four inserted font
    # tags; the build report records both insertion and changed positions.
    changed_ids = {int.from_bytes(t.payload[:2], 'little')
                   for t in new_tags if t.code in (11, 33, 37)} & (tutor_statics | tutor_edits)
    assert changed_ids == tutor_statics | tutor_edits

    # New fonts only extend the repertoire: shared contours keep outlines,
    # advances and bounds per Unicode code.
    for identifier in (6001, 6002):
        old, new = old_fonts[identifier], new_fonts[identifier]
        assert len(new['codes']) >= len(old['codes'])
        for code in old['codes']:
            a, b = old['codes'].index(code), new['codes'].index(code)
            assert old['glyphs'][a] == new['glyphs'][b]
            assert old['advances'][a] == new['advances'][b]
            assert old['bounds'][a] == new['bounds'][b]
    # Original game fonts are byte-preserved.
    for tag in original_tags:
        if tag.code == 75:
            identifier = int.from_bytes(tag.payload[:2], 'little')
            assert new_chars[identifier].payload == tag.payload

    # Install/restore transition rehearsal on temp copies only.
    game = tmp_path / 'game' / 'gc-cs-steam.swf'
    game.parent.mkdir()
    game.write_bytes(original)
    old_path, new_path = tmp_path / 'old.swf', tmp_path / 'new.swf'
    old_path.write_bytes(baseline)
    new_path.write_bytes(candidate)
    old_manifest = apply_patch_file(game, old_path, tmp_path / 'backup', ORIGINAL_SHA256, sha(baseline))
    restore_patch_file(old_manifest)
    new_manifest = apply_patch_file(game, new_path, tmp_path / 'backup', ORIGINAL_SHA256, sha(candidate))
    assert game.read_bytes() == candidate
    restore_patch_file(new_manifest)
    assert game.read_bytes() == original
    apply_patch_file(game, old_path, tmp_path / 'backup', ORIGINAL_SHA256, sha(baseline))
    assert game.read_bytes() == baseline
    assert Path(os.environ['GEMCRAFT_ORIGINAL']).read_bytes() == original
