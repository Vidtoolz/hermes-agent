# Runtime Map

This document is a repository-local map of Hermes Agent runtime behavior.
It is meant to answer one question quickly: when Hermes starts, what code
runs, which config wins, which files are touched, and how does model/runtime
selection change over the life of a turn?

For subsystem deep dives, see the public developer docs under
`website/docs/developer-guide/`.

## Core Conventions

- `HERMES_HOME` is the runtime root for nearly all persistent state.
- Default `HERMES_HOME` is `~/.hermes`.
- In profile mode, `HERMES_HOME` is typically `~/.hermes/profiles/<name>`.
- `hermes_cli.main._apply_profile_override()` runs before other Hermes module
  imports so profile-scoped paths are correct from the start of the process.
- `hermes_constants.get_hermes_home()` is the canonical path helper used across
  the codebase.

## Entry Points

### Packaged entry points

| Command | Target | Purpose |
| --- | --- | --- |
| `hermes` | `hermes_cli.main:main` | Main CLI entry point and subcommand router |
| `hermes-agent` | `run_agent:main` | Standalone Fire CLI around `AIAgent` |
| `hermes-acp` | `acp_adapter.entry:main` | ACP server for editor integrations |

### Repository-local launchers and secondary entry points

| Command | Target | Purpose |
| --- | --- | --- |
| `python hermes` | `hermes_cli.main.main()` | Local wrapper that behaves like installed `hermes` |
| `python -m gateway.run` | `gateway/run.py` | Gateway process without going through CLI wrapper |
| `hermes gateway run` | `hermes_cli.main` -> `hermes_cli.gateway` -> `gateway.run.start_gateway()` | Messaging gateway foreground run |
| `hermes web` | `hermes_cli.main` -> `hermes_cli.web_server.start_server()` | Web dashboard server |
| `hermes mcp serve` | `mcp_serve.py` | MCP bridge over stdio |
| `python batch_runner.py ...` | `batch_runner.py` | Batch trajectory generation |
| `python mini_swe_runner.py ...` | `mini_swe_runner.py` | SWE runner using Hermes environments |
| `python rl_cli.py ...` | `rl_cli.py` | RL-focused CLI runner |

### Container entry point

- `Dockerfile` sets `ENTRYPOINT ["/opt/hermes/docker/entrypoint.sh"]`.
- `docker/entrypoint.sh` bootstraps `HERMES_HOME`, seeds `config.yaml`,
  `.env`, and `SOUL.md`, syncs bundled skills, then executes `hermes "$@"`.

## Startup Chains

### Interactive CLI

```text
hermes
  -> hermes_cli.main:main
  -> _apply_profile_override()
  -> load ~/.hermes/.env first, then repo .env fallback
  -> setup_logging(mode="cli")
  -> load_cli_config()
  -> HermesCLI(...)
  -> resolve_runtime_provider(...)
  -> HermesCLI._resolve_turn_agent_config(...) per turn
  -> HermesCLI._init_agent(...)
  -> AIAgent(...)
  -> AIAgent.run_conversation(...)
```

Important side paths:

- `cli.py` creates `SessionDB()` for session persistence.
- `AIAgent` loads tool definitions via `model_tools.get_tool_definitions()`.
- `model_tools.py` triggers built-in tool discovery, MCP discovery, and plugin
  discovery at import time.

### Gateway

```text
hermes gateway run
  -> hermes_cli.main.cmd_gateway(...)
  -> hermes_cli.gateway.gateway_command(...)
  -> gateway.run.start_gateway(...)
  -> load ~/.hermes/.env
  -> bridge config.yaml values into env vars
  -> setup_logging(mode="gateway")
  -> GatewayRunner(...)
  -> load_gateway_config()
  -> SessionStore(...) + SessionDB(...)
  -> resolve_runtime_provider(...) for session agents
  -> _resolve_turn_agent_config(...) per user message
  -> AIAgent.run_conversation(...)
```

Important side paths:

- Gateway keeps an in-memory agent cache for long-lived sessions.
- Gateway also stores session-key metadata separately in `sessions/sessions.json`.
- Background cron ticker starts inside the gateway process after startup.

### ACP

```text
hermes acp
  or hermes-acp
  -> acp_adapter.entry.main()
  -> load ~/.hermes/.env
  -> logging to stderr only
  -> HermesACPAgent
  -> ACP stdio server
```

### Web dashboard

```text
hermes web
  -> hermes_cli.main
  -> build web/ frontend if needed
  -> hermes_cli.web_server.start_server()
  -> FastAPI app + static web_dist assets
```

### Standalone agent CLI

```text
hermes-agent
  or python run_agent.py
  -> run_agent.main(...) via Fire
  -> AIAgent(...)
  -> AIAgent.run_conversation(...)
```

## Config Precedence

Config precedence is not one global rule. It varies by subsystem.

### Process bootstrap precedence

1. Explicit profile flag `-p/--profile` in `hermes_cli.main`
2. Sticky `active_profile` file under the Hermes root
3. `HERMES_HOME` default

After profile selection:

1. `~/.hermes/.env` or profile-local `.env`
2. Repository `.env` as a development fallback

### Canonical persistent config

- Persistent settings live in `HERMES_HOME/config.yaml`.
- `hermes_cli.config.load_config()` deep-merges `DEFAULT_CONFIG` with
  `config.yaml`.
- `hermes_cli.config.migrate_config()` performs config-version migrations and
  some cleanup of dead environment variables.

### CLI config lookup

`cli.py.load_cli_config()` uses:

1. `HERMES_HOME/config.yaml`
2. `./cli-config.yaml` in the repository as a fallback only if no user config exists
3. Hardcoded defaults in `cli.py`

Then it bridges selected sections into environment variables so older tools and
runtime helpers still work.

### Gateway config lookup

`gateway/run.py` reads `HERMES_HOME/config.yaml` directly and bridges selected
values into env vars. The gateway treats config-backed terminal settings as the
authoritative source for those keys.

### Provider/runtime resolution precedence

`hermes_cli.runtime_provider.resolve_runtime_provider()` effectively follows
this order:

1. Explicit runtime request from the caller
2. Named custom provider match from `config.yaml`
3. Persisted model/provider config in `config.yaml`
4. `HERMES_INFERENCE_PROVIDER` env var
5. Credential pool entries for the chosen provider
6. Provider-specific auth resolution from `auth.json`, CLI/OAuth stores, and env vars
7. Provider defaults and auto resolution

Notes:

- The saved provider/model choice in `config.yaml` is preferred over stale shell
  exports during normal runs.
- `auth.json` is the central Hermes auth store for provider state and many
  OAuth credentials.
- Direct custom endpoints can resolve without an API key when the endpoint is
  local or otherwise ignores auth.

### Per-turn routing precedence

Before a turn creates or reuses an `AIAgent`, CLI and gateway call
`agent.smart_model_routing.resolve_turn_route(...)`.

That function chooses between:

1. The primary runtime from the current session
2. A cheap-model override from `smart_model_routing` if the message looks simple

This is applied outside `AIAgent`, not inside the main loop.

## Runtime File Map

All paths below are relative to `HERMES_HOME` unless noted otherwise.

| Path | Writer(s) | Reader(s) | Purpose |
| --- | --- | --- | --- |
| `config.yaml` | setup flows, `hermes config`, updater migrations | CLI, gateway, web UI, provider/runtime helpers | Main persistent configuration |
| `.env` | setup flows, `hermes config`, memory/provider setup | bootstrap env loader, tools, provider auth | Secrets and env-style config |
| `auth.json` | `hermes auth`, provider auth flows, credential pool sync | runtime provider resolver, auxiliary client, web UI, tools | Provider auth state and credential pools |
| `SOUL.md` | setup/bootstrap, user edits | prompt builder | Agent identity/persona file |
| `memories/MEMORY.md` | built-in memory tool | prompt builder, memory store | Agent-curated environment/project memory |
| `memories/USER.md` | built-in memory tool | prompt builder, memory store | User profile memory |
| `state.db` | `SessionDB` from CLI, gateway, ACP, API server | session search, MCP bridge, logs/debug flows | SQLite session/message store with FTS5 |
| `sessions/sessions.json` | `gateway.session.SessionStore` | gateway, MCP bridge, channel directory | Gateway session-key index and session metadata |
| `logs/agent.log` | centralized logger | `hermes logs`, debug tools | Main runtime log |
| `logs/errors.log` | centralized logger | `hermes logs errors`, debug tools | Warning/error triage log |
| `logs/gateway.log` | centralized logger in gateway mode | `hermes logs gateway`, debug tools | Gateway-only runtime log |
| `channel_directory.json` | gateway channel directory builder | `send_message`, MCP bridge | Cached routing targets for outbound messaging |
| `active_profile` | profile management commands | `hermes_cli.main` bootstrap | Sticky default profile selection |
| `.update_check` | update-banner cache, updater invalidation | banner and update commands | Cached “commits behind” info |
| `.update_prompt.json` | `hermes update --gateway` | gateway update watcher | File-based prompt IPC during gateway-managed updates |
| `.update_exit_code` | `hermes update --gateway` | gateway update watcher | Gateway-visible update result marker |
| `.anthropic_oauth.json` | Anthropic OAuth flows | Anthropic runtime helpers, web UI | Hermes-managed Anthropic OAuth store |
| `auth/google_oauth.json` | Google OAuth helpers | Gemini runtime helpers | Google OAuth credential store |
| `cron/` | cron job commands | scheduler and gateway cron ticker | Scheduled job definitions/state |
| `skills/` | bundled skill sync, user skill installs | prompt builder, skills tools | Installed skills |
| `home/` | bootstrap/container or user-created | subprocess environment helpers | Per-profile subprocess `HOME` |

Paths outside `HERMES_HOME` that matter:

| Path | Purpose |
| --- | --- |
| Repository `cli-config.yaml` | Fallback CLI config when user config does not exist |
| Repository `.env` | Development fallback env file |
| Current working directory `trajectory_samples.jsonl` | Successful trajectory output when trajectory saving is enabled |
| Current working directory `failed_trajectories.jsonl` | Failed trajectory output when trajectory saving is enabled |
| `~/.codex/auth.json` | External Codex CLI credential source and sync target |

## Memory Files And Context Injection

### Built-in memory

Built-in memory is always file-backed:

- `memories/MEMORY.md`
- `memories/USER.md`

`tools/memory_tool.py` loads these files into `MemoryStore`, snapshots them for
system-prompt injection at session start, and writes changes to disk
immediately. Mid-session writes do not mutate the already-built system prompt,
which preserves prompt caching behavior.

### External memory providers

`agent.memory_manager.MemoryManager` can activate exactly one external memory
provider alongside the built-in store. The active external provider is selected
by `memory.provider` in `config.yaml`.

Provider plugins live under `plugins/memory/<name>/`.

### Prompt context files

Prompt assembly in `agent/prompt_builder.py` injects context in this order:

1. `SOUL.md` from `HERMES_HOME` as the primary identity slot
2. Built-in memory blocks
3. External memory-provider prompt block, if active
4. Skills guidance and tool-use guidance
5. Project context from the first matching source:
   - `.hermes.md` or `HERMES.md`
   - `AGENTS.md`
   - `CLAUDE.md`
   - `.cursorrules` and `.cursor/rules/*.mdc`

Project context is chosen by priority, not merged across all file types.

## Routing And Failover Flow

### 1. Provider identity and normalization

- `hermes_cli.providers.py` defines canonical provider IDs, aliases, transport
  overlays, and base URL overrides.
- `hermes_cli.model_switch.py` handles shared `/model` parsing, alias expansion,
  metadata lookup, and live model switching.

### 2. Runtime provider resolution

`resolve_runtime_provider()` returns a runtime dict containing:

- `provider`
- `api_mode`
- `base_url`
- `api_key`
- optional process-backed runtime info such as `command` and `args`
- optional `credential_pool`

### 3. Per-turn smart routing

CLI and gateway resolve the effective turn runtime before constructing an agent
turn config:

```text
primary runtime
  -> smart_model_routing.resolve_turn_route(...)
  -> optional cheap model for simple turns
  -> optional fast-mode request overrides
  -> AIAgent(...)
```

The smart-route decision is conservative. Messages that look code-heavy,
tool-heavy, or long stay on the primary model.

### 4. AIAgent runtime mode selection

Inside `AIAgent.__init__`, the resolved provider/base URL/model determines the
transport mode:

- `chat_completions`
- `codex_responses`
- `anthropic_messages`
- `bedrock_converse`

The agent then builds the correct client stack:

- OpenAI-compatible client
- native Anthropic client
- Bedrock client
- ACP / external process-backed client

### 5. OpenRouter provider routing

`provider_routing` from config is threaded from CLI and gateway into `AIAgent`.
The agent turns it into OpenRouter `provider` request preferences:

- `only`
- `ignore`
- `order`
- `sort`
- `require_parameters`
- `data_collection`

These preferences are only attached to OpenRouter-compatible requests.

### 6. Retry, credential-pool recovery, fallback, and restoration

Within `run_agent.py`, the recovery path is:

1. retry transient API/transport failures
2. attempt credential-pool recovery where available
3. compress context for payload/context-size failures
4. activate configured fallback provider/model from `fallback_providers` or legacy `fallback_model`
5. at the next turn, restore the original primary runtime

Important details:

- Fallback is turn-scoped, not permanent.
- `AIAgent` snapshots the primary runtime in `_primary_runtime`.
- `run_conversation()` starts by calling `_restore_primary_runtime()`.
- `_try_activate_fallback()` swaps the agent in place to the next configured
  fallback runtime and updates context-compressor limits for the fallback model.

## Update Flow

### User-facing updater: `hermes update`

`hermes_cli.main.cmd_update()` is the main updater.

Normal flow:

1. Refuse in managed installs and show the package-manager-specific upgrade command.
2. Check that the install is a git checkout, unless using the Windows ZIP fallback.
3. `git fetch origin`
4. Detect current branch and switch to `main` for updates
5. Auto-stash local changes if needed
6. Compare `HEAD` to `origin/main`
7. If behind, `git pull --ff-only origin main`
8. If fast-forward fails because history diverged, hard reset to `origin/main`
9. Restore stashed local changes if possible
10. Invalidate update cache and clear stale `__pycache__`
11. Reinstall Python dependencies
12. Optionally run `npm install` and rebuild the web UI
13. Sync bundled skills, other profiles, and Honcho profile data
14. Offer config migrations
15. In gateway mode, write `.update_exit_code`
16. Restart running gateways

Important operational notes:

- The divergent-history path is forceful after stashing.
- Gateway-managed updates use `.update_prompt.json` and `.update_exit_code`
  for file-based IPC because stdin is not interactive.
- `hermes update` restarts all running gateways because the code checkout is
  shared across profiles.

### Installer update path

`scripts/install.sh` has its own “existing installation found, updating”
branch. It also stashes local changes, fetches, checks out the target branch,
pulls, and optionally reapplies the stash.

### Maintainer release flow

`scripts/release.py` is the maintainer-facing publish script.

Its publish flow:

1. Compute next semver and CalVer tag
2. Generate changelog from git history
3. Update `hermes_cli/__init__.py` and `pyproject.toml`
4. Commit the version bump
5. Create an annotated git tag
6. Push commits and tags
7. Build Python release artifacts when possible
8. Create the GitHub release with `gh release create`

## Related Files

If you need to extend or debug one part of the runtime map, these are the
highest-value files to open next:

- `hermes_cli/main.py`
- `cli.py`
- `run_agent.py`
- `hermes_cli/config.py`
- `hermes_cli/runtime_provider.py`
- `hermes_cli/providers.py`
- `hermes_cli/model_switch.py`
- `agent/smart_model_routing.py`
- `agent/prompt_builder.py`
- `tools/memory_tool.py`
- `agent/memory_manager.py`
- `hermes_state.py`
- `gateway/run.py`
- `gateway/session.py`
- `hermes_logging.py`
- `scripts/install.sh`
- `scripts/release.py`
