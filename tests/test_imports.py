"""Import hygiene (SPEC "Package layout"): stdlib + robot-msgs; wandb only inside wandb_log; never robot_activity.

Each check runs in a fresh interpreter with stand-ins for wandb, matplotlib, robot_activity and bam_interventions first on
PYTHONPATH, so an import of one of them shows up in sys.modules even where the real library is not installed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPORT_MODULES = """
import importlib, json, sys
for name in sys.argv[1:]:
    importlib.import_module(name)
print(json.dumps(sorted({module.split('.')[0] for module in sys.modules})))
"""

# Modules that need nothing beyond the stdlib: importing them must not pull robot_msgs (only sources.diagnostics
# uses it) or numpy.
STDLIB_ONLY = [
    "robot_autonomy",
    "robot_autonomy.schema",
    "robot_autonomy.ledger",
    "robot_autonomy.episodes",
    "robot_autonomy.hours",
    "robot_autonomy.metrics",
    "robot_autonomy.verify",
    "robot_autonomy.backlog",
]

EVERY_MODULE = STDLIB_ONLY + [
    "robot_autonomy.sources.labelled",
    "robot_autonomy.sources.robot_activity",
    "robot_autonomy.sources.diagnostics",
    "robot_autonomy.sources.field_service",
    "robot_autonomy.ingest",
    "robot_autonomy.agent",
    "robot_autonomy.team",
    "robot_autonomy.notify",
    "robot_autonomy.activity_link",
    "robot_autonomy.views.terminal",
    "robot_autonomy.views.html",
    "robot_autonomy.demo",
    "robot_autonomy.cli",
    "robot_autonomy.wandb_log",
]


def top_level_modules_after_import(fake_site, *module_names) -> set[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(fake_site), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    completed = subprocess.run(
        [sys.executable, "-c", REPORT_MODULES, *module_names], capture_output=True, text=True, env=env, timeout=120
    )
    assert completed.returncode == 0, completed.stderr
    return set(json.loads(completed.stdout.strip().splitlines()[-1]))


@pytest.mark.parametrize("module_name", STDLIB_ONLY)
def test_core_modules_import_no_heavy_or_robot_msgs(fake_heavy_modules, module_name):
    loaded = top_level_modules_after_import(fake_heavy_modules, module_name)
    for forbidden in ("wandb", "numpy", "matplotlib", "robot_msgs", "robot_activity", "bam_interventions"):
        assert forbidden not in loaded, f"import {module_name} pulled in {forbidden}"


@pytest.mark.parametrize("module_name", EVERY_MODULE)
def test_no_module_imports_wandb_matplotlib_or_robot_activity(fake_heavy_modules, module_name):
    loaded = top_level_modules_after_import(fake_heavy_modules, module_name)
    for forbidden in ("wandb", "matplotlib", "robot_activity", "bam_interventions"):
        assert forbidden not in loaded, f"import {module_name} pulled in {forbidden}"


def test_the_stand_ins_are_detectable(fake_heavy_modules):
    """Guards the ruler: an import of a stand-in really does show up."""
    loaded = top_level_modules_after_import(fake_heavy_modules, "wandb", "robot_activity")
    assert {"wandb", "robot_activity"} <= loaded
