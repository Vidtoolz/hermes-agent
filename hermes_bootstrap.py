"""Shared bootstrap helpers for early profile/home resolution."""

from __future__ import annotations

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
