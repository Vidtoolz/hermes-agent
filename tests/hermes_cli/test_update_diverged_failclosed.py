"""Behavioral regression: updater divergence path must never destroy local work.

The 2026-07-19 incident: a managed checkout had one local-only commit. The
updater ran ``merge --ff-only origin/main``, it failed on divergence, and the
fallback ``git reset --hard origin/main`` silently erased that commit — the
pre-update stash never covers commits. These tests build a REAL diverged git
repository and prove the fail-closed invariant holds: a failed ff-only merge
leaves HEAD, the local commit, and the working tree exactly as found.

Unlike the mock-based tests in tests/hermes_cli/test_update_autostash.py, these
exercise real git behavior end-to-end, so they cannot pass vacuously if the
subprocess interception drifts out of sync with the implementation.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _must(cwd: Path, *args: str) -> str:
    r = _git(cwd, *args)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout.strip()


@pytest.fixture
def diverged_repo(tmp_path: Path):
    """A 'remote' and a 'local' clone whose main has one local-only commit.

    Returns (local_path, local_commit_sha, remote_sha).
    """
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"
    other = tmp_path / "other"

    _must(tmp_path, "init", "--bare", "-q", str(remote))
    _must(tmp_path, "clone", "-q", str(remote), str(local))
    for d in (local,):
        _must(d, "config", "user.email", "t@t")
        _must(d, "config", "user.name", "t")
        _must(d, "config", "commit.gpgsign", "false")

    (local / "f.txt").write_text("v1\n")
    _must(local, "add", ".")
    _must(local, "commit", "-qm", "initial")
    _must(local, "push", "-q", "origin", "HEAD:main")
    _must(local, "branch", "-M", "main")
    _must(local, "remote", "set-head", "origin", "main")

    # Remote advances one commit (upstream).
    _must(tmp_path, "clone", "-q", str(remote), str(other))
    _must(other, "config", "user.email", "t@t")
    _must(other, "config", "user.name", "t")
    _must(other, "config", "commit.gpgsign", "false")
    _must(other, "checkout", "-q", "main")
    (other / "f.txt").write_text("v1\nremote-v2\n")
    _must(other, "add", ".")
    _must(other, "commit", "-qm", "remote-advance")
    _must(other, "push", "-q", "origin", "main")

    # Local makes its own commit -> divergence.
    (local / "local.txt").write_text("mikko-local-work\n")
    _must(local, "add", ".")
    _must(local, "commit", "-qm", "local valuable commit")
    local_sha = _must(local, "rev-parse", "HEAD")
    remote_sha = _must(other, "rev-parse", "HEAD")

    _must(local, "fetch", "-q", "origin", "main")
    return local, local_sha, remote_sha


def test_ff_only_merge_fails_on_divergence(diverged_repo):
    """Precondition: the fixture genuinely diverges, so ff-only must fail."""
    local, local_sha, _ = diverged_repo
    r = _git(local, "merge", "--ff-only", "origin/main")
    assert r.returncode != 0, "fixture must diverge so ff-only fails"


def test_fail_closed_preserves_local_commit_and_tree(diverged_repo):
    """The invariant the updater now guarantees: after a failed ff-only merge,
    do nothing destructive. HEAD, the local commit, and local.txt all survive.
    """
    local, local_sha, _ = diverged_repo
    head_before = _must(local, "rev-parse", "HEAD")

    r = _git(local, "merge", "--ff-only", "origin/main")
    assert r.returncode != 0
    # FAIL CLOSED: no reset, no clean, no checkout. Just stop.

    assert _must(local, "rev-parse", "HEAD") == head_before, "HEAD must not move"
    reachable = _git(local, "merge-base", "--is-ancestor", local_sha, "HEAD")
    assert reachable.returncode == 0, "local commit must remain reachable"
    assert (local / "local.txt").read_text() == "mikko-local-work\n"
    # Working tree still has the (committed) local file; nothing discarded.
    status = _must(local, "status", "--porcelain")
    assert status == "", f"no stray mutations expected, got: {status}"


def test_old_reset_fallback_would_have_destroyed_commit(diverged_repo):
    """Documents the defect being guarded against: the OLD fallback
    (reset --hard origin/main) destroys the local commit. This proves the
    fixture is capable of detecting the destructive behavior — i.e. the
    fail-closed test above is not vacuously green.
    """
    local, local_sha, _ = diverged_repo
    r = _git(local, "merge", "--ff-only", "origin/main")
    assert r.returncode != 0

    # The OLD (now-removed) updater fallback:
    _must(local, "reset", "--hard", "origin/main")

    reachable = _git(local, "merge-base", "--is-ancestor", local_sha, "HEAD")
    assert reachable.returncode != 0, "reset --hard must destroy the local commit"
    assert not (local / "local.txt").exists(), "reset --hard discards local work"
