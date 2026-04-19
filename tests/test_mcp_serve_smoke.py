"""Startup smoke tests for Hermes MCP server entrypoints."""

import importlib
import sys

import pytest


def _fresh_import_mcp_serve():
    for name in [
        "mcp_serve",
        "hermes_constants",
    ]:
        sys.modules.pop(name, None)
    return importlib.import_module("mcp_serve")


@pytest.fixture()
def mcp_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes_mcp"
    home.mkdir()
    (home / "sessions").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def test_get_sessions_dir_uses_temp_home(mcp_home):
    mcp_serve = _fresh_import_mcp_serve()

    assert mcp_serve._get_sessions_dir() == mcp_home / "sessions"


def test_run_mcp_server_starts_bridge_and_stdio_server(mcp_home, monkeypatch):
    mcp_serve = _fresh_import_mcp_serve()
    monkeypatch.setattr(mcp_serve, "_MCP_SERVER_AVAILABLE", True)

    events = []

    class FakeBridge:
        def start(self):
            events.append("bridge_start")

        def stop(self):
            events.append("bridge_stop")

    class FakeServer:
        async def run_stdio_async(self):
            events.append("server_run")

    monkeypatch.setattr(mcp_serve, "EventBridge", FakeBridge)
    monkeypatch.setattr(mcp_serve, "create_mcp_server", lambda event_bridge: FakeServer())

    mcp_serve.run_mcp_server(verbose=False)

    assert events == ["bridge_start", "server_run", "bridge_stop"]


def test_run_mcp_server_missing_sdk_exits_1_on_stderr(mcp_home, monkeypatch, capsys):
    mcp_serve = _fresh_import_mcp_serve()
    monkeypatch.setattr(mcp_serve, "_MCP_SERVER_AVAILABLE", False)

    with pytest.raises(SystemExit) as exc:
        mcp_serve.run_mcp_server()

    assert exc.value.code == 1
    assert "requires the 'mcp' package" in capsys.readouterr().err
