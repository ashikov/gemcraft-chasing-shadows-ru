"""Read-only verification of the installed patch and immutable original backup."""

from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path

from build_prototype import build_patch, validate_patch
from install_patch import GAME_FILE, ORIGINAL_SHA256, PROJECT


def verify(game_file=GAME_FILE, backup_dir=PROJECT / "backups", inventory_path=None):
    backup = (
        backup_dir / (game_file.name + "." + ORIGINAL_SHA256 + ".original")
    )
    original = backup.read_bytes()
    if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("Original backup SHA-256 mismatch")
    installed = game_file.read_bytes()
    expected = build_patch(original)
    if installed != expected:
        raise ValueError("Installed patch differs from reproducible build")
    validation = validate_patch(original, installed)
    inventory = json.loads(inventory_path.read_text()) if inventory_path else []
    expected_paths = {item["path"] for item in inventory}
    actual_paths = {
        str(p.relative_to(game_file.parent))
        for p in game_file.parent.rglob("*")
        if p.is_file()
    }
    if inventory_path and actual_paths != expected_paths:
        raise ValueError("Unexpected game file inventory")
    files = []
    for item in inventory:
        path = game_file.parent / item["path"]
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        unchanged = digest == item["sha256"] and len(data) == item["bytes"]
        if not unchanged and path != game_file:
            raise ValueError("Unrelated installation file changed: " + item["path"])
        files.append(
            {
                "path": item["path"],
                "bytes": len(data),
                "sha256": digest,
                "unchanged": unchanged,
            }
        )
    return {
        "verified_utc": datetime.now(timezone.utc).isoformat(),
        "state": "patch installed; this report verifies static integrity only",
        "runtime_evidence_path": str(PROJECT / "artifacts" / "runtime-session.json"),
        "backup_path": str(backup),
        "backup_sha256": ORIGINAL_SHA256,
        "files": files,
        "static_validation": validation,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-file", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, default=PROJECT / "backups")
    parser.add_argument("--inventory", type=Path)
    args = parser.parse_args()
    report = verify(args.game_file.resolve(), args.backup_dir.resolve(), args.inventory)
    destination = PROJECT / "artifacts" / "installation-verification.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        "Verified installed SWF and original backup; "
        + str(len(report["files"])) + " inventory files checked. No game launch."
    )
