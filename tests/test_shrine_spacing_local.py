"""Read-only cumulative scope and real-font geometry for the shrine adaptation."""
import os
from pathlib import Path

import pytest

from abc_font_binding import _bodies, _inventory
from localization_fonts import measure_text
from localization_plan import SHRINE_PREFIX, SHRINE_SUFFIX
from swf_geometry import parse_font, read_swf

pytestmark = pytest.mark.local


def resources():
    paths = [os.environ.get(key) for key in ('GEMCRAFT_PR37_CANDIDATE', 'GEMCRAFT_PR38_CANDIDATE')]
    if not all(paths):
        pytest.skip('requires PR37 baseline and PR38 candidate')
    return [read_swf(Path(path).read_bytes()) for path in paths]


def abc(tag):
    return tag.payload[tag.payload.index(b'\0', 4) + 1:]


def test_only_shrine_behavior_changes_and_all_artwork_is_preserved():
    (old_prefix, old), (new_prefix, new) = resources()
    assert old_prefix[8:] == new_prefix[8:]
    assert len(old) == len(new)
    changed = [(a, b) for a, b in zip(old, new) if a != b]
    assert len(changed) == 1 and changed[0][0].code == changed[0][1].code == 82
    a, b = [abc(tag) for tag in changed[0]]
    before, after = _inventory(a), _inventory(b)
    spacing_string = after.strings.index('letterSpacing')
    assert after.strings[:spacing_string] + after.strings[spacing_string + 1:] == before.strings
    assert after.multinames[:-1] == before.multinames
    assert after.multiname(len(after.multinames) - 1) == 'letterSpacing'
    old_bodies, new_bodies = _bodies(a), _bodies(b)
    assert old_bodies.keys() == new_bodies.keys()
    assert [m for m in old_bodies if old_bodies[m][2:] != new_bodies[m][2:]] == [630, 2769]
    # Achievement labels are appended after font bindings: only their pool
    # operands move by one. Resolve every literal to prove identical behavior.
    assert old_bodies[2769][2] == new_bodies[2769][2]
    assert old_bodies[2769][4:] == new_bodies[2769][4:]

    def resolved(inv, body):
        return [(at, opcode, [inv.strings[args[0]]] if opcode == 0x2c else args)
                for at, opcode, args in inv.instructions(body[3], 2769)]

    assert resolved(before, old_bodies[2769]) == resolved(after, new_bodies[2769])
    from localization_plan import shrine_spacing_code
    snippet = shrine_spacing_code(len(after.multinames) - 1)
    assert new_bodies[630][3].count(snippet) == 1


@pytest.mark.parametrize('number', ['1.00', '1.00 e12', '9.876 e123'])
def test_real_font_spacing_preserves_two_line_panel_envelope(number):
    _, (_, tags) = resources()
    font = next(parse_font(tag.payload) for tag in tags
                if tag.code == 75 and int.from_bytes(tag.payload[:2], 'little') == 6002)
    # This common description wraps at the same existing word boundary in AIR.
    first = SHRINE_PREFIX + number + 'x урона самоцвета всем'
    second = 'врагам в радиусе, игнорируя броню'
    assert first + ' ' + second == SHRINE_PREFIX + number + SHRINE_SUFFIX
    for line, extra in ((first, 3), (second, 0)):
        width = measure_text(font, line, 200)['widths'][0] / 20 + extra
        for scale in (1, .75, .5):
            assert width * scale < 336 * scale
    assert font['codes'].count(ord(' ')) == 1
