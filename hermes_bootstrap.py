"""Shared bootstrap helpers for early Hermes startup."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence


def resolve_profile_override(argv: Sequence[str]) -> tuple[Optional[str], int]:
    """Return (profile_name, consumed_arg_count) from argv.

    consumed_arg_count is:
    - 2 for ``--profile NAME`` or ``-p NAME``
    - 1 for ``--profile=NAME``
    - 0 when falling back to ``active_profile``
    """
    profile_name = None
    consume = 0

    for i, arg in enumerate(argv):
        if arg in ("--profile", "-p") and i + 1 < len(argv):
            profile_name = argv[i + 1]
            consume = 2
            break
        if arg.startswith("--profile="):
            profile_name = arg.split("=", 1)[1]
            consume = 1
            break

    if profile_name is None:
        try:
            from hermes_constants import get_default_hermes_root

            active_path = get_default_hermes_root() / "active_profile"
            if active_path.exists():
                name = active_path.read_text().strip()
                if name and name != "default":
                    profile_name = name
                    consume = 0
        except (UnicodeDecodeError, OSError):
            pass

    return profile_name, consume


def resolve_profile_home(profile_name: str) -> str:
    """Resolve a profile name to its HERMES_HOME path."""
    from hermes_cli.profiles import resolve_profile_env

    return resolve_profile_env(profile_name)


def load_cli_dotenv(project_root: Path) -> None:
    """Load CLI dotenv files in the existing user-then-project order."""
    from hermes_cli.env_loader import load_hermes_dotenv

    load_hermes_dotenv(project_env=project_root / ".env")


def load_runtime_dotenv(hermes_home: Path, project_env: Path) -> list[Path]:
    """Load runtime dotenv files with explicit home + project paths."""
    from hermes_cli.env_loader import load_hermes_dotenv

    return load_hermes_dotenv(hermes_home=hermes_home, project_env=project_env)


def setup_cli_logging_early() -> None:
    """Initialize centralized logging for CLI entrypoints."""
    try:
        from hermes_logging import setup_logging as _setup_logging

        _setup_logging(mode="cli")
    except Exception:
        pass


def apply_cli_ipv4_preference_early() -> None:
    """Apply IPv4 preference before any HTTP clients are created."""
    try:
        from hermes_cli.config import load_config as _load_config_early
        from hermes_constants import apply_ipv4_preference as _apply_ipv4

        _early_cfg = _load_config_early()
        _net = _early_cfg.get("network", {})
        if isinstance(_net, dict) and _net.get("force_ipv4"):
            _apply_ipv4(force=True)
        del _early_cfg, _net
    except Exception:
        pass
