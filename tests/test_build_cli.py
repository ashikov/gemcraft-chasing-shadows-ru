"""CLI file safety with synthetic bytes; no original SWF required."""
import sys

import pytest

import build_prototype
from install_patch import sha


@pytest.mark.parametrize("filename", ["gc-cs-steam.ru.swf", "static-validation.json"])
@pytest.mark.parametrize("link_type", ["hardlink", "symlink"])
def test_build_refuses_output_linked_to_original(tmp_path, monkeypatch, filename, link_type):
    source = tmp_path / "original.swf"
    source.write_bytes(b"synthetic original")
    output = tmp_path / "output"
    output.mkdir()
    candidate = output / filename
    if link_type == "hardlink":
        candidate.hardlink_to(source)
    else:
        candidate.symlink_to(source)
    monkeypatch.setattr(sys, "argv", ["build", "--source", str(source), "--output-dir", str(output)])
    monkeypatch.setattr(build_prototype, "build_patch", lambda raw: b"synthetic patch")
    monkeypatch.setattr(build_prototype, "validate_patch", lambda raw, patch: {"patched_sha256": sha(patch)})
    with pytest.raises(ValueError, match="overwrite"):
        build_prototype.main()
    assert source.read_bytes() == b"synthetic original"
    if filename == "static-validation.json":
        assert not (output / "gc-cs-steam.ru.swf").exists()
