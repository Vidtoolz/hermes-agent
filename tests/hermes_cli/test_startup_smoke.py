"""Startup smoke tests for top-level Hermes CLI entrypoints."""

import importlib
import os
import sys
import types

import pytest


def _fresh_import_main():
    for name in [
        "hermes_cli.main",
        "hermes_cli.config",
        "hermes_constants",
    ]:
        sys.modules.pop(name, None)
    return importlib.import_module("hermes_cli.main")


@pytest.fixture()
def startup_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes_startup"
    home.mkdir()
    for subdir in ("sessions", "logs", "cron", "profiles", "skills"):
        (home / subdir).mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def test_hermes_no_subcommand_defaults_to_chat_from_temp_home(monkeypatch, startup_home):
    (startup_home / ".env").write_text(
        "OPENAI_BASE_URL=https://chat.example/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_BASE_URL", "https://stale.example/v1")
    monkeypatch.setattr(sys, "argv", ["hermes"])

    main_mod = _fresh_import_main()
    monkeypatch.setattr(main_mod, "_has_any_provider_configured", lambda: True)

    called = {}
    fake_cli = types.ModuleType("cli")

    def fake_cli_main(**kwargs):
        called["kwargs"] = kwargs

    fake_cli.main = fake_cli_main
    monkeypatch.setitem(sys.modules, "cli", fake_cli)

    main_mod.main()

    assert os.getenv("OPENAI_BASE_URL") == "https://chat.example/v1"
    assert called["kwargs"]["verbose"] is False
    assert called["kwargs"]["quiet"] is False


def test_profile_flag_repoints_home_before_dotenv_load(monkeypatch, tmp_path):
    profile_home = tmp_path / "coder-profile"
    profile_home.mkdir()
    (profile_home / ".env").write_text(
        "OPENAI_BASE_URL=https://profile.example/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_BASE_URL", "https://stale.example/v1")
    monkeypatch.setattr(sys, "argv", ["hermes", "--profile", "coder", "gateway", "status"])

    fake_profiles = types.ModuleType("hermes_cli.profiles")
    fake_profiles.resolve_profile_env = lambda profile_name: str(profile_home)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", fake_profiles)

    _fresh_import_main()

    assert os.environ["HERMES_HOME"] == str(profile_home)
    assert os.getenv("OPENAI_BASE_URL") == "https://profile.example/v1"
    assert "--profile" not in sys.argv
    assert "coder" not in sys.argv


def test_hermes_first_run_guard_exits_cleanly_with_empty_temp_home(monkeypatch, startup_home, capsys):
    monkeypatch.setattr(sys, "argv", ["hermes"])

    main_mod = _fresh_import_main()
    monkeypatch.setattr(main_mod, "_has_any_provider_configured", lambda: False)

    fake_setup = types.ModuleType("hermes_cli.setup")
    fake_setup.is_interactive_stdin = lambda: False
    fake_setup.print_noninteractive_setup_guidance = lambda message: print(message)
    monkeypatch.setitem(sys.modules, "hermes_cli.setup", fake_setup)

    with pytest.raises(SystemExit) as exc:
        main_mod.main()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Run:  hermes setup" in out
    assert "No interactive TTY detected" in out


def test_hermes_gateway_run_dispatches_to_gateway_command(monkeypatch, startup_home):
    monkeypatch.setattr(sys, "argv", ["hermes", "gateway", "run", "--replace", "-v"])

    main_mod = _fresh_import_main()
    called = {}
    monkeypatch.setattr(main_mod, "cmd_gateway", lambda args: called.setdefault("args", args))

    main_mod.main()

    assert called["args"].gateway_command == "run"
    assert called["args"].replace is True
    assert called["args"].verbose == 1


def test_hermes_mcp_serve_dispatches_to_mcp_command(monkeypatch, startup_home):
    monkeypatch.setattr(sys, "argv", ["hermes", "mcp", "serve", "--verbose"])

    main_mod = _fresh_import_main()
    called = {}
    fake_mcp_config = types.ModuleType("hermes_cli.mcp_config")
    fake_mcp_config.mcp_command = lambda args: called.setdefault("args", args)
    monkeypatch.setitem(sys.modules, "hermes_cli.mcp_config", fake_mcp_config)

    main_mod.main()

    assert called["args"].mcp_action == "serve"
    assert called["args"].verbose is True


def test_hermes_acp_dispatches_to_acp_entry(monkeypatch, startup_home):
    monkeypatch.setattr(sys, "argv", ["hermes", "acp"])

    main_mod = _fresh_import_main()
    called = []

    fake_entry = types.ModuleType("acp_adapter.entry")
    fake_entry.main = lambda: called.append("acp")
    fake_pkg = types.ModuleType("acp_adapter")
    fake_pkg.__path__ = []
    fake_pkg.entry = fake_entry
    monkeypatch.setitem(sys.modules, "acp_adapter", fake_pkg)
    monkeypatch.setitem(sys.modules, "acp_adapter.entry", fake_entry)

    main_mod.main()

    assert called == ["acp"]
