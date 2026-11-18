"""activity_link.py: asking the Robot Activity Agent for robot time (SPEC "activity_link.py")."""

from __future__ import annotations

import os
import subprocess

import pytest

from robot_autonomy.activity_link import submit_verification
from robot_autonomy.backlog import Improvement

IMPROVEMENT = Improvement(
    id="imp-0a1b2c3d",
    title="Remove power_or_replug: powered hub",
    causes=("power_or_replug",),
    owner="dev agent",
    proposed_by="Robot Autonomy Agent",
    proposed_t=1_762_000_000.0,
    updated_t=1_762_000_000.0,
)


class FakeRunner:
    """Stands in for subprocess.run: records the argv, returns a CompletedProcess.

    stdout/stderr come back as str when the caller asked for text, else as bytes, so either calling style works.
    """

    def __init__(self, returncode=0, stdout='{"id": "q-42", "status": "queued"}', stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, *args, **kwargs):
        argv = list(args[0]) if args else list(kwargs["args"])
        self.calls.append(argv)
        as_text = kwargs.get("text") or kwargs.get("universal_newlines") or kwargs.get("encoding")
        stdout = self.stdout if as_text else self.stdout.encode()
        stderr = self.stderr if as_text else self.stderr.encode()
        return subprocess.CompletedProcess(argv, self.returncode, stdout, stderr)


def test_submit_runs_the_robot_activity_cli_with_the_exact_argv(tmp_path):
    runner = FakeRunner()
    root = str(tmp_path / "activity")
    result = submit_verification(
        IMPROVEMENT,
        root=root,
        est_min=20.0,
        policy_repo="https://github.com/example/policies",
        policy_commit="0123456789abcdef",
        policy_path="verify/power_hub.py",
        robot="arm-1",
        runner=runner,
    )
    assert result == {"id": "q-42", "status": "queued"}
    [argv] = runner.calls
    assert os.path.basename(argv[0]) == "robot-activity"
    est_min_index = argv.index("--est-min") + 1
    assert float(argv[est_min_index]) == 20.0
    argv_without_est_min = argv[1:est_min_index] + argv[est_min_index + 1 :]
    assert argv_without_est_min == [
        "queue", "submit",
        "--root", root,
        "--title", "Verify: Remove power_or_replug: powered hub",
        "--owner", "dev agent",
        "--est-min",
        "--source", "agent",
        "--robot", "arm-1",
        "--policy-repo", "https://github.com/example/policies",
        "--policy-commit", "0123456789abcdef",
        "--policy-path", "verify/power_hub.py",
        "--json",
    ]


def test_robot_defaults_to_any(tmp_path):
    runner = FakeRunner()
    submit_verification(
        IMPROVEMENT, root=str(tmp_path), est_min=5, policy_repo="repo", policy_commit="c0ffee", policy_path="p.py",
        runner=runner,
    )
    [argv] = runner.calls
    assert argv[argv.index("--robot") + 1] == "any"


def test_a_non_zero_exit_raises_with_stderr(tmp_path):
    runner = FakeRunner(returncode=2, stdout="", stderr="queue error: est_min must be > 0")
    with pytest.raises(RuntimeError, match="est_min must be > 0"):
        submit_verification(
            IMPROVEMENT, root=str(tmp_path), est_min=20.0, policy_repo="repo", policy_commit="c0ffee",
            policy_path="p.py", runner=runner,
        )
