"""Optional read-only integration against a private supported original."""
import os
from pathlib import Path

import pytest

from install_patch import GAME_FILE, ORIGINAL_SHA256, PROJECT, sha
from inventory_strings import extract_inventory
from localization_workspace import (
    initialize_workspace, minimal_projection, reconcile_workspace, validate_minimal,
    validate_workspace,
)
from translation_catalog import load_catalog

pytestmark = pytest.mark.local


def test_original_inventory_workspace_and_projection_round_trip():
    source = os.environ.get('GEMCRAFT_ORIGINAL')
    backup = PROJECT / 'backups' / (GAME_FILE.name + '.' + ORIGINAL_SHA256 + '.original')
    if not source and not backup.exists():
        pytest.skip('Local original required: set GEMCRAFT_ORIGINAL')
    path = Path(source) if source else backup
    raw = path.read_bytes()
    assert sha(raw) == ORIGINAL_SHA256
    inventory = extract_inventory(raw, ORIGINAL_SHA256)
    assert inventory == extract_inventory(raw, ORIGINAL_SHA256)
    seed = load_catalog(PROJECT / 'translations' / 'ru.json')
    workspace = initialize_workspace(inventory, seed)
    assert validate_workspace(workspace, inventory) == []
    assert reconcile_workspace(workspace, inventory) == workspace
    projection = minimal_projection(workspace, seed)
    assert len(projection['entries']) == 1
    assert validate_minimal(projection, workspace, seed) == []
    assert all('source' not in e for e in projection['entries'])
    assert sha(path.read_bytes()) == ORIGINAL_SHA256
