"""Regression coverage for collection-time and provider-test log isolation."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def log_fingerprint(log_dir: Path) -> dict[str, tuple[str, int]]:
    if not log_dir.exists():
        return {}
    return {
        path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in sorted(log_dir.glob("*.log*"))
        if path.is_file()
    }


def test_import_has_no_file_logging_side_effect_and_main_logging_is_explicit(tmp_path):
    test_home = tmp_path / "hermes-home"
    test_home.mkdir()
    probe = textwrap.dedent(
        """
        import logging
        from pathlib import Path
        import hermes_cli.main as cli_main

        log_dir = Path(__import__("os").environ["HERMES_HOME"]) / "logs"
        assert not list(log_dir.glob("*.log")), "import created Hermes logs"
        cli_main._initialize_cli_logging()
        logging.getLogger("provider.failure.test").warning(
            "deterministic provider HTTP 400 for isolation proof"
        )
        logging.shutdown()
        assert (log_dir / "errors.log").exists()
        assert "deterministic provider HTTP 400" in (log_dir / "errors.log").read_text()
        """
    )
    env = dict(os.environ)
    env["HERMES_HOME"] = str(test_home)

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (test_home / "logs" / "errors.log").exists()


def test_provider_failure_pytest_run_does_not_touch_production_logs(tmp_path):
    real_logs = Path.home() / ".hermes" / "logs"
    before = log_fingerprint(real_logs)
    isolated_home = tmp_path / "isolated-hermes"
    isolated_home.mkdir()
    basetemp = isolated_home / "pytest"
    env = dict(os.environ)
    env["HERMES_HOME"] = str(isolated_home)

    target = (
        "tests/run_agent/test_run_agent_codex_responses.py::"
        "test_gpt_5_6_sol_deterministic_http_400_is_not_retried"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            target,
            f"--basetemp={basetemp}",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert log_fingerprint(real_logs) == before
    isolated_logs = list(isolated_home.rglob("errors.log"))
    assert isolated_logs, "provider failure test did not create an isolated errors.log"
    assert any("reasoning_effort" in path.read_text(errors="replace") for path in isolated_logs)
