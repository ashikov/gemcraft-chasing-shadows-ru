"""Hash-guarded local install/restore. Never launches applications."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

ORIGINAL_SHA256 = "c8eadc03a7e607086998695f7696ec22dd90d7b7859e3d10cdfabf40fc7bbad5"
PROJECT = Path(__file__).resolve().parent
GAME_FILE = Path(
    "/mnt/c/Program Files (x86)/Steam/steamapps/common/GemCraft Chasing Shadows/gc-cs-steam.swf"
)


def configured_game_file(value=None):
    """Mutations require an explicit path, supplied via CLI or environment."""
    value = value or os.environ.get("GEMCRAFT_GAME_FILE")
    if not value:
        raise ValueError("Specify --game-file or GEMCRAFT_GAME_FILE (path to gc-cs-steam.swf)")
    path = Path(value).expanduser().resolve()
    if path.name != "gc-cs-steam.swf" or not path.is_file():
        raise ValueError("Game path must identify an existing gc-cs-steam.swf")
    return path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic_replace(target, data, expected_current):
    """Stage next to destination; recheck current bytes immediately before replace."""
    fd, name = tempfile.mkstemp(prefix=target.name + ".ru-", dir=target.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if staged.read_bytes() != data:
            raise ValueError("Staged file verification failed")
        os.chmod(staged, target.stat().st_mode & 0o777)
        if sha(target.read_bytes()) != expected_current:
            raise ValueError("Target changed during operation; refusing overwrite")
        os.replace(staged, target)
        if target.read_bytes() != data:
            raise ValueError("Installed file verification failed")
    finally:
        staged.unlink(missing_ok=True)


def apply_patch_file(
    target, candidate, backup_dir, expected_original, expected_candidate_sha=None
):
    target, candidate, backup_dir = map(
        lambda p: Path(p).resolve(), (target, candidate, backup_dir)
    )
    if backup_dir == target.parent or target.parent in backup_dir.parents:
        raise ValueError("Backup must be outside the game directory")
    original = target.read_bytes()
    if sha(original) != expected_original:
        raise ValueError("Unexpected original SHA-256; refusing patch")
    patched = candidate.read_bytes()
    if expected_candidate_sha is not None and sha(patched) != expected_candidate_sha:
        raise ValueError("Candidate changed after validation; refusing patch")
    if patched == original:
        raise ValueError("Candidate makes no change")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / (target.name + "." + expected_original + ".original")
    try:
        with backup.open("xb") as out:
            out.write(original)
            out.flush()
            os.fsync(out.fileno())
    except FileExistsError:
        pass  # Never overwrite the first copy, including a damaged one.
    if backup.read_bytes() != original or sha(backup.read_bytes()) != expected_original:
        raise ValueError("Original backup differs; refusing patch")
    backup.chmod(0o400)
    patched_hash = sha(patched)
    record = {
        "schema": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "original_path": str(target),
        "original_size": len(original),
        "original_sha256": expected_original,
        "backup_path": str(backup),
        "patched_size": len(patched),
        "patched_sha256": patched_hash,
        "candidate_path": str(candidate),
        "runtime": "not run; explicit permission required",
    }
    manifest = backup_dir / ("manifest-" + patched_hash + ".json")
    if manifest.exists():
        saved = json.loads(manifest.read_text())
        for key in (
            "original_path",
            "original_sha256",
            "original_size",
            "backup_path",
            "patched_sha256",
            "patched_size",
        ):
            if saved[key] != record[key]:
                raise ValueError("Conflicting existing manifest")
    else:
        with manifest.open("x", encoding="utf-8") as out:
            json.dump(record, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
    atomic_replace(target, patched, expected_original)
    return manifest


def restore_patch_file(manifest):
    record = json.loads(Path(manifest).read_text())
    target, backup = Path(record["original_path"]), Path(record["backup_path"])
    original = backup.read_bytes()
    if (
        len(original) != record["original_size"]
        or sha(original) != record["original_sha256"]
    ):
        raise ValueError("Backup verification failed; refusing restore")
    current_hash = sha(target.read_bytes())
    if current_hash == record["original_sha256"]:
        return
    if current_hash != record["patched_sha256"]:
        raise ValueError("Unknown current SHA-256 (possible update); refusing restore")
    atomic_replace(target, original, record["patched_sha256"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--game-file", type=Path)
    apply.add_argument("--backup-dir", type=Path, default=PROJECT / "backups")
    apply.add_argument(
        "--candidate", type=Path, default=PROJECT / "output" / "gc-cs-steam.ru.swf"
    )
    apply.add_argument("--workspace", type=Path)
    apply.add_argument("--plan", type=Path)
    apply.add_argument("--font-recipes", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("--game-file", type=Path)
    restore.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    game_file = configured_game_file(args.game_file)
    if args.command == "restore":
        manifest = json.loads(args.manifest.read_text())
        if (
            Path(manifest["original_path"]).resolve() != game_file
            or manifest["original_sha256"] != ORIGINAL_SHA256
        ):
            raise ValueError("Manifest is not for this known game installation")
        restore_patch_file(args.manifest)
        print("Original restored and SHA-256 verified")
    else:
        # Revalidate resource structure against the actual original, not a sidecar claim.
        from build_prototype import validate_patch

        candidate = args.candidate.read_bytes()
        local_inputs = (args.workspace, args.plan, args.font_recipes)
        if any(local_inputs):
            if not all(local_inputs):
                raise ValueError("Local candidates require workspace, plan and font recipes")
            from addressed_localization import validate_localization
            from translation_catalog import load_catalog

            validate_localization(
                game_file.read_bytes(), candidate, *(load_catalog(p) for p in local_inputs)
            )
        else:
            validate_patch(game_file.read_bytes(), candidate)
        print(
            apply_patch_file(
                game_file,
                args.candidate,
                args.backup_dir,
                ORIGINAL_SHA256,
                sha(candidate),
            )
        )


if __name__ == "__main__":
    main()
