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


def test_local_cli_requires_all_recipe_inputs_without_mutation(tmp_path, monkeypatch):
    import install_patch
    import sys

    target = tmp_path / "gc-cs-steam.swf"
    target.write_bytes(b"synthetic original")
    candidate = tmp_path / "candidate.swf"
    candidate.write_bytes(b"synthetic candidate")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install_patch.py",
            "apply",
            "--game-file",
            str(target),
            "--candidate",
            str(candidate),
            "--workspace",
            str(tmp_path / "data.json"),
        ],
    )
    with pytest.raises(ValueError, match="require workspace"):
        install_patch.main()
    assert target.read_bytes() == b"synthetic original"
    assert not (tmp_path / "backups").exists()


def test_local_cli_reproduces_recipe_before_install(tmp_path, monkeypatch):
    import addressed_localization
    import install_patch
    import sys

    target = tmp_path / "gc-cs-steam.swf"
    target.write_bytes(b"synthetic original")
    candidate = tmp_path / "candidate.swf"
    candidate.write_bytes(b"synthetic candidate")
    inputs = [
        tmp_path / name for name in ("workspace.json", "plan.json", "recipes.json")
    ]
    for number, path in enumerate(inputs):
        path.write_text(json.dumps({"synthetic_input": number}))
    args = [
        "install_patch.py",
        "apply",
        "--game-file",
        str(target),
        "--candidate",
        str(candidate),
    ]
    for flag, path in zip(("--workspace", "--plan", "--font-recipes"), inputs):
        args += [flag, str(path)]
    monkeypatch.setattr(sys, "argv", args)
    calls = []

    def validate(original, patched, *documents):
        calls.append((original, patched, documents))
        raise ValueError("Synthetic recipe mismatch")

    monkeypatch.setattr(addressed_localization, "validate_localization", validate)
    with pytest.raises(ValueError, match="recipe mismatch"):
        install_patch.main()
    assert calls == [
        (
            b"synthetic original",
            b"synthetic candidate",
            ({"synthetic_input": 0}, {"synthetic_input": 1}, {"synthetic_input": 2}),
        )
    ]
    assert target.read_bytes() == b"synthetic original"


def test_alegreya_cli_validates_before_atomic_apply_and_restore(tmp_path, monkeypatch):
    import install_patch
    import sys
    from types import SimpleNamespace

    target = tmp_path / "game" / "gc-cs-steam.swf"
    target.parent.mkdir()
    target.write_bytes(b"synthetic original")
    candidate = tmp_path / "candidate.swf"
    candidate.write_bytes(b"synthetic candidate")
    workspace, plan = [tmp_path / name for name in ("workspace.json", "plan.json")]
    workspace.write_text('{"workspace": true}')
    plan.write_text('{"plan": true}')
    font = tmp_path / "font.ttf"
    font.write_bytes(b"synthetic TTF")
    backup_dir = tmp_path / "backups"
    calls = []

    def validate(*inputs):
        calls.append(inputs)
        if len(calls) == 1:
            raise ValueError("Synthetic invalid font")
        return {"valid": True}

    monkeypatch.setitem(
        sys.modules, "alegreya_localization",
        SimpleNamespace(validate_alegreya_localization=validate),
    )
    monkeypatch.setattr(install_patch, "ORIGINAL_SHA256", sha(b"synthetic original"))
    monkeypatch.setattr(sys, "argv", [
        "install_patch.py", "apply", "--game-file", str(target),
        "--candidate", str(candidate), "--backup-dir", str(backup_dir),
        "--workspace", str(workspace), "--plan", str(plan),
        "--alegreya-font", str(font),
    ])
    with pytest.raises(ValueError, match="invalid font"):
        install_patch.main()
    assert target.read_bytes() == b"synthetic original"
    assert not backup_dir.exists()
    install_patch.main()
    assert calls == [(b"synthetic original", b"synthetic candidate",
                      {"workspace": True}, {"plan": True}, b"synthetic TTF")] * 2
    assert target.read_bytes() == b"synthetic candidate"
    restore_patch_file(next(backup_dir.glob("manifest-*.json")))
    assert target.read_bytes() == b"synthetic original"


@pytest.mark.parametrize("extra, error", [
    (["--alegreya-font", "font.ttf"], ValueError),
    (["--alegreya-font", "font.ttf", "--font-recipes", "recipes.json"], SystemExit),
    (["--workspace", "workspace.json", "--plan", "plan.json"], ValueError),
])
def test_font_mode_cli_rejects_incomplete_or_conflicting_inputs(
    tmp_path, monkeypatch, extra, error
):
    import install_patch
    import sys

    target = tmp_path / "gc-cs-steam.swf"
    target.write_bytes(b"synthetic original")
    candidate = tmp_path / "candidate.swf"
    candidate.write_bytes(b"synthetic candidate")
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(sys, "argv", [
        "install_patch.py", "apply", "--game-file", str(target),
        "--candidate", str(candidate), "--backup-dir", str(backup_dir), *extra,
    ])
    with pytest.raises(error):
        install_patch.main()
    assert target.read_bytes() == b"synthetic original"
    assert not backup_dir.exists()


def test_verifier_alegreya_mode_is_read_only(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    import verify_experiment

    target = tmp_path / "gc-cs-steam.swf"
    target.write_bytes(b"synthetic candidate")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    original_hash = sha(b"synthetic original")
    backup = backup_dir / (target.name + "." + original_hash + ".original")
    backup.write_bytes(b"synthetic original")
    monkeypatch.setattr(verify_experiment, "ORIGINAL_SHA256", original_hash)
    calls = []

    def validate(*inputs):
        calls.append(inputs)
        return {"valid": True}

    monkeypatch.setitem(sys.modules, "alegreya_localization",
                        SimpleNamespace(validate_alegreya_localization=validate))
    inputs = ({"workspace": True}, {"plan": True}, b"synthetic TTF")
    report = verify_experiment.verify(target, backup_dir, alegreya_inputs=inputs)
    assert calls == [(b"synthetic original", b"synthetic candidate", *inputs)]
    assert report["static_validation"] == {"valid": True}
    assert target.read_bytes() == b"synthetic candidate"
    assert backup.read_bytes() == b"synthetic original"
    with pytest.raises(ValueError, match="mutually exclusive"):
        verify_experiment.verify(target, backup_dir, local_inputs=inputs,
                                 alegreya_inputs=inputs)
    with pytest.raises(ValueError, match="workspace, plan and Alegreya"):
        verify_experiment.verify(target, backup_dir, alegreya_inputs=({}, None, b"font"))


@pytest.mark.parametrize("font_flag", ["--alegreya-font", "--font-recipes"])
def test_verifier_cli_routes_font_mode(tmp_path, monkeypatch, font_flag):
    import sys
    import verify_experiment

    workspace, plan = [tmp_path / name for name in ("workspace.json", "plan.json")]
    workspace.write_text('{"workspace": true}')
    plan.write_text('{"plan": true}')
    font = tmp_path / "font-input"
    font.write_bytes(b"synthetic TTF" if font_flag == "--alegreya-font" else b'{"recipe": true}')
    target = tmp_path / "game" / "gc-cs-steam.swf"
    backup_dir = tmp_path / "backups"
    calls = []

    def verify(*args, **kwargs):
        calls.append((args, kwargs))
        return {"files": []}

    monkeypatch.setattr(verify_experiment, "verify", verify)
    monkeypatch.setattr(verify_experiment, "PROJECT", tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "verify_experiment.py", "--game-file", str(target),
        "--backup-dir", str(backup_dir), "--workspace", str(workspace),
        "--plan", str(plan), font_flag, str(font),
    ])
    verify_experiment.main()
    documents = [{"workspace": True}, {"plan": True}, {"recipe": True}]
    if font_flag == "--alegreya-font":
        assert calls == [((target.resolve(), backup_dir.resolve(), None, None),
                          {"alegreya_inputs": ({"workspace": True}, {"plan": True},
                                                b"synthetic TTF")})]
    else:
        assert calls == [((target.resolve(), backup_dir.resolve(), None, documents),
                          {"alegreya_inputs": None})]
    assert (tmp_path / "artifacts" / "installation-verification.json").is_file()
    assert not target.exists()
    assert not backup_dir.exists()


def test_verifier_cli_preserves_prior_report(tmp_path, monkeypatch):
    import sys
    import verify_experiment

    target = tmp_path / "gc-cs-steam.swf"
    report_path = tmp_path / "previous-verification.json"
    report_path.write_text('historical evidence')
    monkeypatch.setattr(verify_experiment, "verify", lambda *args, **kwargs: {"files": []})
    monkeypatch.setattr(sys, "argv", [
        "verify_experiment.py", "--game-file", str(target),
        "--report", str(report_path),
    ])
    with pytest.raises(FileExistsError):
        verify_experiment.main()
    assert report_path.read_text() == 'historical evidence'


@pytest.mark.parametrize("extra, error", [
    (["--alegreya-font", "font.ttf"], ValueError),
    (["--alegreya-font", "font.ttf", "--font-recipes", "recipes.json"], SystemExit),
    (["--workspace", "workspace.json", "--plan", "plan.json"], ValueError),
])
def test_verifier_cli_rejects_incomplete_or_conflicting_inputs(
    tmp_path, monkeypatch, extra, error
):
    import sys
    import verify_experiment

    target = tmp_path / "gc-cs-steam.swf"
    target.write_bytes(b"synthetic candidate")
    monkeypatch.setattr(verify_experiment, "PROJECT", tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "verify_experiment.py", "--game-file", str(target), *extra,
    ])
    with pytest.raises(error):
        verify_experiment.main()
    assert target.read_bytes() == b"synthetic candidate"
    assert not (tmp_path / "artifacts").exists()
