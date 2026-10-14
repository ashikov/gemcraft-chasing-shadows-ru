"""Private whole-menu integration; no game launch or installation changes."""

import os
from pathlib import Path

import pytest

from addressed_localization import build_localization, validate_localization
from install_patch import ORIGINAL_SHA256, apply_patch_file, restore_patch_file, sha
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


def test_private_menu_candidate_reproducibility_and_restore(tmp_path):
    variables = (
        "GEMCRAFT_ORIGINAL",
        "GEMCRAFT_OPTIONS_WORKSPACE",
        "GEMCRAFT_OPTIONS_PLAN",
        "GEMCRAFT_OPTIONS_RECIPES",
    )
    if not all(os.environ.get(name) for name in variables):
        pytest.skip(
            "Set original, options workspace, plan and recipes for private menu integration"
        )
    source, workspace, plan, recipes = [Path(os.environ[name]) for name in variables]
    original = source.read_bytes()
    assert sha(original) == ORIGINAL_SHA256
    inputs = [load_catalog(p) for p in (workspace, plan, recipes)]
    first, report = build_localization(original, *inputs)
    second, again = build_localization(original, *inputs)
    assert first == second
    assert report == again == validate_localization(original, first, *inputs)
    assert report["applied_translations"] == len(inputs[1]["entry_ids"])
    with pytest.raises(ValueError):
        validate_localization(original, first[:-1] + bytes([first[-1] ^ 1]), *inputs)
    target = tmp_path / "game" / "gc-cs-steam.swf"
    target.parent.mkdir()
    target.write_bytes(original)
    candidate = tmp_path / "candidate.swf"
    candidate.write_bytes(first)
    manifest = apply_patch_file(
        target, candidate, tmp_path / "backups", ORIGINAL_SHA256, sha(first)
    )
    assert target.read_bytes() == first
    from verify_experiment import verify

    checked = verify(target, tmp_path / "backups", local_inputs=inputs)
    assert checked["static_validation"]["applied_translations"] == len(
        inputs[1]["entry_ids"]
    )
    restore_patch_file(manifest)
    assert target.read_bytes() == original
    assert source.read_bytes() == original
