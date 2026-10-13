import hashlib
import json
from pathlib import Path

import pytest

from install_patch import apply_patch_file, restore_patch_file


def sha(data):
    return hashlib.sha256(data).hexdigest()


def test_backup_apply_restore_preserves_first_backup(tmp_path):
    target = tmp_path / "game" / "game.swf"
    target.parent.mkdir()
    target.write_bytes(b"original")
    candidate = tmp_path / "candidate.swf"
    candidate.write_bytes(b"patched")
    store = tmp_path / "backups"
    manifest = apply_patch_file(target, candidate, store, sha(b"original"))
    info = json.loads(manifest.read_text())
    backup = Path(info["backup_path"])
    assert backup.read_bytes() == b"original"
    assert target.read_bytes() == b"patched"
    with pytest.raises(ValueError):
        apply_patch_file(target, candidate, store, sha(b"original"))
    restore_patch_file(manifest)
    assert target.read_bytes() == backup.read_bytes() == b"original"
    restore_patch_file(manifest)  # idempotent after successful restore
    apply_patch_file(target, candidate, store, sha(b"original"))
    assert backup.read_bytes() == b"original"


def test_unknown_target_and_corrupt_backup_fail_closed(tmp_path):
    target = tmp_path / "game" / "game.swf"
    target.parent.mkdir()
    target.write_bytes(b"unknown")
    candidate = tmp_path / "candidate.swf"
    candidate.write_bytes(b"patched")
    with pytest.raises(ValueError):
        apply_patch_file(target, candidate, tmp_path / "backup", sha(b"original"))
    assert target.read_bytes() == b"unknown"
    target.write_bytes(b"original")
    manifest = apply_patch_file(
        target, candidate, tmp_path / "backup", sha(b"original")
    )
    target.write_bytes(b"future Steam update")
    with pytest.raises(ValueError):
        restore_patch_file(manifest)
    assert target.read_bytes() == b"future Steam update"
    target.write_bytes(b"patched")
    backup = Path(json.loads(manifest.read_text())["backup_path"])
    backup.chmod(0o600)
    backup.write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        restore_patch_file(manifest)
    assert target.read_bytes() == b"patched"


def test_changed_candidate_rejected_before_install(tmp_path):
    target = tmp_path / "game" / "game.swf"
    target.parent.mkdir()
    target.write_bytes(b"original")
    candidate = tmp_path / "candidate.swf"
    candidate.write_bytes(b"changed after validation")
    store = tmp_path / "backups"
    with pytest.raises(ValueError, match="Candidate changed"):
        apply_patch_file(
            target, candidate, store, sha(b"original"), sha(b"validated candidate")
        )
    assert target.read_bytes() == b"original"
    assert not store.exists()


def test_existing_corrupt_backup_is_never_overwritten(tmp_path):
    target = tmp_path / "game" / "game.swf"
    target.parent.mkdir()
    target.write_bytes(b"original")
    candidate = tmp_path / "candidate.swf"
    candidate.write_bytes(b"patched")
    store = tmp_path / "backups"
    store.mkdir()
    backup = store / (target.name + "." + sha(b"original") + ".original")
    backup.write_bytes(b"first damaged copy")
    with pytest.raises(ValueError, match="Original backup differs"):
        apply_patch_file(target, candidate, store, sha(b"original"))
    assert target.read_bytes() == b"original"
    assert backup.read_bytes() == b"first damaged copy"


def test_target_changed_during_atomic_replace_is_preserved(tmp_path, monkeypatch):
    import install_patch

    target = tmp_path / "game.swf"
    target.write_bytes(b"original")
    real_chmod = install_patch.os.chmod

    def change_target(path, mode):
        real_chmod(path, mode)
        target.write_bytes(b"external update")

    monkeypatch.setattr(install_patch.os, "chmod", change_target)
    with pytest.raises(ValueError, match="Target changed"):
        install_patch.atomic_replace(target, b"patched", sha(b"original"))
    assert target.read_bytes() == b"external update"
    assert list(tmp_path.glob("*.ru-*")) == []


def test_game_path_requires_explicit_existing_swf(tmp_path, monkeypatch):
    from install_patch import configured_game_file

    monkeypatch.delenv("GEMCRAFT_GAME_FILE", raising=False)
    with pytest.raises(ValueError, match="Specify"):
        configured_game_file()
    with pytest.raises(ValueError, match="existing"):
        configured_game_file(tmp_path / "missing")
    target = tmp_path / "gc-cs-steam.swf"
    target.write_bytes(b"synthetic")
    monkeypatch.setenv("GEMCRAFT_GAME_FILE", str(target))
    assert configured_game_file() == target.resolve()
