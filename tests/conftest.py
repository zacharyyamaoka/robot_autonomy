"""Fixtures shared by the acceptance tests.

This file never imports robot_autonomy, so pytest can load it (and collect every test file) before the package exists.
Plain builders live in `helpers.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fake_heavy_modules(tmp_path: Path) -> Path:
    """A folder of importable stand-ins for wandb, matplotlib, robot_activity and bam_interventions.

    Put it first on a subprocess's PYTHONPATH: any import of those names then succeeds and shows up in sys.modules, so
    a test can tell "never imported" from "imported but not installed here".
    """
    folder = tmp_path / "fake_site"
    for name in ("wandb", "matplotlib", "robot_activity", "bam_interventions"):
        package = folder / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(f"STAND_IN = {name!r}\n")
    return folder


@pytest.fixture
def autonomy_root(tmp_path: Path) -> Path:
    """An empty folder for one ledger + backlog + agent state."""
    root = tmp_path / "autonomy"
    root.mkdir()
    return root


@pytest.fixture
def notifier():
    from helpers import RecordingNotifier

    return RecordingNotifier()
