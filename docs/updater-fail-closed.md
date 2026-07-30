# Updater fail-closed contract

This document defines how `hermes update` and the installer scripts
(`scripts/install.sh`, `scripts/install.ps1`) treat the managed checkout at
`~/.hermes/hermes-agent`. It exists because of a real incident: on 2026-07-19
the updater destroyed a local-only commit by running `git reset --hard
origin/main` after a fast-forward pull failed on diverged history. The
pre-update stash never covers commits, so that work was unrecoverable from the
stash. The updater now **fails closed** instead.

## Core rule

> An updater must never destroy local work. When it cannot safely fast-forward,
> it stops, explains, preserves everything, and lets the human decide.

`git fetch` may update remote-tracking refs at any time — that is read-only
with respect to your working tree and local branches. Nothing else about the
checkout is mutated unless a fast-forward is provably possible.

## State machine

| State | Updater behavior |
|-------|------------------|
| Clean, remote ahead, fast-forward possible | Update proceeds (`merge --ff-only`). |
| Dirty working tree (tracked edits) | Tracked changes are auto-stashed with a descriptive `hermes-update-autostash-*` message, the update runs, then the stash is restored. The stash is **never** popped after a failed update, and **never** reset over. |
| Untracked files | Left in place. Never `git clean`. |
| **Local-only commits / diverged history** | **Update refused.** No reset, no rebase, no merge commit. HEAD, branches, working tree, and stash are left exactly as found. Non-zero exit with the recovery command. |
| Detached HEAD | Handled by the existing checkout logic; never rewrites branch refs to force a fast-forward. |
| Fetch failure / remote unreachable | Repository untouched. Clear error. Non-zero exit. |
| Update step failure after pull | Auto-rollback to the pre-pull SHA (your *own* prior commit — non-destructive) only for the post-pull syntax guard; diagnostics preserved. |

## Divergence recovery (the 2026-07-19 case)

When you see:

```
✗ Update refused: fast-forward not possible (local history has diverged).
  Nothing was changed: local commits, branches, and files are untouched.
  See what diverged:
    git -C ~/.hermes/hermes-agent log --oneline origin/main..HEAD
```

your local branch has commits the remote does not. Nothing was lost. Choose one:

1. **Keep the commits on a branch** (recommended):
   ```bash
   cd ~/.hermes/hermes-agent
   git branch my-local-work        # label the commits
   git log --oneline origin/main..HEAD   # confirm what they are
   # then either rebase them onto the remote:
   git rebase origin/main
   # or move main to the remote and keep your work on the branch:
   #   git branch -f main origin/main   (only after my-local-work exists)
   hermes update
   ```
2. **Discard the commits intentionally** (only if you are sure they are worthless):
   ```bash
   cd ~/.hermes/hermes-agent
   git log --oneline origin/main..HEAD   # READ THESE FIRST
   git reset --hard origin/main          # a deliberate, human-typed reset
   hermes update
   ```

The updater will never run that reset for you.

## Autostash policy

- Dirty tracked work blocks nothing by itself, but is auto-stashed with a
  descriptive message and reported by SHA before the update runs.
- The updater never creates a second equivalent stash, never pops a stash after
  a failed update, and never stashes untracked files unless explicitly told to.
- If the update fails, your changes remain in the stash; restore with
  `git stash apply` (or `git stash list` to find the `hermes-update-autostash-*`
  entry). The updater prints the stash ref it created.
- Historical autostashes are never deleted by the updater. Inspect them with
  `git stash list` / `git stash show -p <ref>`; drop one only after you have
  confirmed its content is committed elsewhere or preserved.

## Concurrency

A lock prevents two updater/launcher processes from mutating the checkout at
once. A second process exits rather than racing refs. Locks are cleaned up on
failure; if a stale lock remains after a crash, remove it per the diagnostic the
next run prints.

## Inspecting current state

```bash
cd ~/.hermes/hermes-agent
git status                      # dirty? untracked?
git log --oneline origin/main..HEAD   # local-only commits (divergence)
git stash list                  # autostashes and their dates
git reflog -20                  # recent HEAD movement (resets, pulls)
```

## Regression tests

- `tests/hermes_cli/test_update_diverged_failclosed.py` — real-git fixture:
  proves a failed ff-only merge preserves HEAD, the local commit, and the tree,
  and that the old `reset --hard` fallback would have destroyed them.
- `tests/hermes_cli/test_update_autostash.py::test_cmd_update_refuses_reset_over_local_commits`
  — the CLI path refuses divergence and issues no `reset --hard`.
- `tests/test_install_diverged_update.py` — both installer scripts fail closed
  and contain no `reset --hard origin/$BRANCH` fallback.
