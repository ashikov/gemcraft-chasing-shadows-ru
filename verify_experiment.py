"""Read-only verification of the installed patch and immutable original backup."""

from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path

from build_prototype import build_patch, validate_patch
from install_patch import GAME_FILE, ORIGINAL_SHA256, PROJECT


def verify(
    game_file=GAME_FILE,
    backup_dir=PROJECT / "backups",
    inventory_path=None,
    local_inputs=None,
    alegreya_inputs=None,
):
    if local_inputs is not None and alegreya_inputs is not None:
        raise ValueError("Local recipes and Alegreya inputs are mutually exclusive")
    backup = backup_dir / (game_file.name + "." + ORIGINAL_SHA256 + ".original")
    original = backup.read_bytes()
    if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("Original backup SHA-256 mismatch")
    installed = game_file.read_bytes()
    if alegreya_inputs is not None:
        if len(alegreya_inputs) != 3 or any(value is None for value in alegreya_inputs):
            raise ValueError("Local verification requires workspace, plan and Alegreya font")
        from alegreya_localization import validate_alegreya_localization

        validation = validate_alegreya_localization(original, installed, *alegreya_inputs)
    elif local_inputs is not None:
        if len(local_inputs) != 3 or any(value is None for value in local_inputs):
            raise ValueError(
                "Local verification requires workspace, plan and font recipes"
            )
        from addressed_localization import validate_localization

        validation = validate_localization(original, installed, *local_inputs)
    else:
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-file", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, default=PROJECT / "backups")
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--report", type=Path,
                        help="New verification report path; existing files are preserved")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--plan", type=Path)
    fonts = parser.add_mutually_exclusive_group()
    fonts.add_argument("--font-recipes", type=Path)
    fonts.add_argument("--alegreya-font", type=Path)
    args = parser.parse_args()
    paths = (args.workspace, args.plan, args.font_recipes)
    inputs = None
    alegreya_inputs = None
    if args.alegreya_font is not None:
        if args.workspace is None or args.plan is None:
            raise ValueError("Local verification requires workspace, plan and Alegreya font")
        from translation_catalog import load_catalog

        alegreya_inputs = (
            load_catalog(args.workspace), load_catalog(args.plan),
            args.alegreya_font.read_bytes(),
        )
    elif any(paths):
        if not all(paths):
            raise ValueError(
                "Local verification requires workspace, plan and font recipes"
            )
        from translation_catalog import load_catalog

        inputs = [load_catalog(path) for path in paths]
    report = verify(
        args.game_file.resolve(), args.backup_dir.resolve(), args.inventory, inputs,
        alegreya_inputs=alegreya_inputs,
    )
    destination = args.report or PROJECT / "artifacts" / "installation-verification.json"
    destination.parent.mkdir(exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        "Verified installed SWF and original backup; "
        + str(len(report["files"]))
        + " inventory files checked. No game launch."
    )


if __name__ == "__main__":
    main()
