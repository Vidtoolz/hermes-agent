"""Startup smoke tests for gateway bootstrap and CLI handoff."""

import asyncio
import importlib
import os
import sys
import types


class _NamedThreadRef:
    def __init__(self, name):
        self.name = name


def _fresh_import_gateway_run():
    for name in [
        "gateway.run",
        "gateway.config",
        "hermes_constants",
    ]:
        sys.modules.pop(name, None)
    return importlib.import_module("gateway.run")


def test_gateway_import_time_bootstrap_reads_env_from_temp_home(monkeypatch, tmp_path):
    home = tmp_path / "gateway_home"
    home.mkdir()
    for subdir in ("sessions", "logs", "cron"):
        (home / subdir).mkdir()
    (home / ".env").write_text(
        "OPENAI_BASE_URL=https://gateway.example/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENAI_BASE_URL", "https://stale.example/v1")

    gateway_run = _fresh_import_gateway_run()

    assert gateway_run._hermes_home == home
    assert os.getenv("OPENAI_BASE_URL") == "https://gateway.example/v1"


def test_run_gateway_cli_handoff_calls_start_gateway_with_expected_verbosity(monkeypatch):
    import hermes_cli.gateway as gateway_cli
    import gateway.run as gateway_run

    calls = {}

    async def fake_start_gateway(*, replace=False, verbosity=0):
        calls["replace"] = replace
        calls["verbosity"] = verbosity
        return True

    monkeypatch.setattr(gateway_run, "start_gateway", fake_start_gateway)

    gateway_cli.run_gateway(verbose=1, quiet=False, replace=True)

    assert calls == {"replace": True, "verbosity": 1}


def test_start_gateway_writes_pid_only_after_successful_start(monkeypatch, tmp_path):
    home = tmp_path / "gateway_runner_home"
    home.mkdir()
    for subdir in ("sessions", "logs", "cron"):
        (home / subdir).mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    gateway_run = _fresh_import_gateway_run()

    fake_skills_sync = types.ModuleType("tools.skills_sync")
    fake_skills_sync.sync_skills = lambda quiet=True: None
    monkeypatch.setitem(sys.modules, "tools.skills_sync", fake_skills_sync)

    import hermes_logging
    monkeypatch.setattr(hermes_logging, "setup_logging", lambda **kwargs: None)

    import gateway.status as gateway_status
    pid_calls = []
    monkeypatch.setattr(gateway_status, "get_running_pid", lambda: None)
    monkeypatch.setattr(gateway_status, "write_pid_file", lambda: pid_calls.append("write"))
    monkeypatch.setattr(gateway_status, "remove_pid_file", lambda: pid_calls.append("remove"))

    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.started = False

        def start(self):
            self.started = True

        def join(self, timeout=None):
            return None

    class FakeRunner:
        def __init__(self, config):
            self.adapters = {}
            self.should_exit_cleanly = False
            self.exit_reason = None
            self.should_exit_with_failure = False
            self.exit_code = None
            self._restart_requested = False

        async def start(self):
            return True

        async def wait_for_shutdown(self):
            return None

    monkeypatch.setattr(gateway_run, "GatewayRunner", FakeRunner)
    monkeypatch.setattr(gateway_run.threading, "Thread", FakeThread)
    worker_thread = _NamedThreadRef("gateway-test-worker")
    main_thread = _NamedThreadRef("MainThread")
    monkeypatch.setattr(gateway_run.threading, "current_thread", lambda: worker_thread)
    monkeypatch.setattr(gateway_run.threading, "main_thread", lambda: main_thread)

    assert asyncio.run(gateway_run.start_gateway(replace=False, verbosity=0)) is True
    assert pid_calls == ["write"]


def test_start_gateway_refuses_duplicate_instance_in_same_temp_home(monkeypatch, tmp_path):
    home = tmp_path / "gateway_duplicate_home"
    home.mkdir()
    for subdir in ("sessions", "logs", "cron"):
        (home / subdir).mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    gateway_run = _fresh_import_gateway_run()

    import gateway.status as gateway_status
    monkeypatch.setattr(gateway_status, "get_running_pid", lambda: 12345)

    class UnexpectedRunner:
        def __init__(self, config):
            raise AssertionError("GatewayRunner should not be constructed")

    monkeypatch.setattr(gateway_run, "GatewayRunner", UnexpectedRunner)

    assert asyncio.run(gateway_run.start_gateway(replace=False, verbosity=None)) is False


def test_start_gateway_replace_mode_terminates_existing_pid_then_continues(monkeypatch, tmp_path):
    home = tmp_path / "gateway_replace_home"
    home.mkdir()
    for subdir in ("sessions", "logs", "cron"):
        (home / subdir).mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    gateway_run = _fresh_import_gateway_run()

    fake_skills_sync = types.ModuleType("tools.skills_sync")
    fake_skills_sync.sync_skills = lambda quiet=True: None
    monkeypatch.setitem(sys.modules, "tools.skills_sync", fake_skills_sync)

    import hermes_logging
    monkeypatch.setattr(hermes_logging, "setup_logging", lambda **kwargs: None)

    import gateway.status as gateway_status
    calls = []
    monkeypatch.setattr(gateway_status, "get_running_pid", lambda: 12345)
    monkeypatch.setattr(gateway_status, "terminate_pid", lambda pid, force=False: calls.append((pid, force)))
    monkeypatch.setattr(gateway_status, "remove_pid_file", lambda: calls.append("remove_pid"))
    monkeypatch.setattr(gateway_status, "release_all_scoped_locks", lambda: 0)
    monkeypatch.setattr(gateway_status, "write_pid_file", lambda: calls.append("write_pid"))

    monkeypatch.setattr(gateway_run.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))

    class FakeThread:
        def __init__(self, *args, **kwargs):
            return None

        def start(self):
            return None

        def join(self, timeout=None):
            return None

    class FakeRunner:
        def __init__(self, config):
            self.adapters = {}
            self.should_exit_cleanly = False
            self.exit_reason = None
            self.should_exit_with_failure = False
            self.exit_code = None
            self._restart_requested = False

        async def start(self):
            return True

        async def wait_for_shutdown(self):
            return None

    monkeypatch.setattr(gateway_run, "GatewayRunner", FakeRunner)
    monkeypatch.setattr(gateway_run.threading, "Thread", FakeThread)
    worker_thread = _NamedThreadRef("gateway-test-worker")
    main_thread = _NamedThreadRef("MainThread")
    monkeypatch.setattr(gateway_run.threading, "current_thread", lambda: worker_thread)
    monkeypatch.setattr(gateway_run.threading, "main_thread", lambda: main_thread)

    assert asyncio.run(gateway_run.start_gateway(replace=True, verbosity=0)) is True
    assert (12345, False) in calls
    assert "remove_pid" in calls
    assert "write_pid" in calls
