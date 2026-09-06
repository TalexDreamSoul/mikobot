# Directory Structure

> How backend code is organized in this project.

---

## Overview

nanobot is a single Python package, `nanobot/`, containing 25 subpackages. There is no
`src/` layout, no `routes/services/repositories` split, and no per-feature vertical slicing.
Organization follows **runtime role**: each subpackage owns one stage of the message
lifecycle or one cross-cutting service.

The critical path is:

```
Channel -> MessageBus -> AgentLoop -> AgentRunner -> Provider / Tools -> MessageBus -> Channel
```

Two rules govern where new code goes, both from [`.agent/design.md`](../../../.agent/design.md):

1. **Core stays small; extend at the edges.** `nanobot/agent/loop.py` (~2.7k lines) and
   `nanobot/agent/runner.py` (~1.5k lines) are the critical core path. Changes there must be
   minimal and justified. If a capability can live in a channel package, a tool, a skill, or an
   MCP server, it does not belong in the loop.
2. **Prefer duplication over premature abstraction.** Channels and providers are allowed to
   repeat similar logic. Do not add a shared base class or helper module purely to remove
   duplication across channel or provider files.

A more detailed source-to-behavior map lives in [`docs/architecture.md`](../../../docs/architecture.md).

---

## Directory Layout

```
nanobot/
├── agent/              Core agent path
│   ├── loop.py         AgentLoop: session keys, workspace scope, context, hooks, outbound
│   ├── runner.py       AgentRunner: provider calls, streaming, tool execution loop
│   ├── context.py      ContextBuilder: system prompt + project/agent instruction assembly
│   ├── memory.py       Long-term memory + Dream two-phase consolidation (atomic writes)
│   ├── subagent.py     Spawned subagent turns
│   ├── hook.py         Hook contracts; hooks/ holds concrete hooks (file-edit activity)
│   └── tools/          Every agent-facing tool (see below)
├── api/                OpenAI-compatible HTTP API (/v1/chat/completions, /v1/models)
├── apps/               CLI Apps protocol + installer service (apps/cli/service.py)
├── audio/              Transcription registry and shared audio helpers
├── bus/                MessageBus, InboundMessage/OutboundMessage, progress + runtime events
├── channels/           One self-contained package per platform (see below)
├── cli/                Typer CLI: commands.py, gateway.py, agent.py, onboard.py, entry.py
├── collaboration/      Users, projects, channel assignments, and Pair Codes (local JSON store)
├── command/            Slash command router + built-in handlers
├── config/             Pydantic schema, loader/saver, path helpers, file watcher
├── cron/               Cron service, job store, scheduled-turn delivery
├── extensions/         Canonical extension inventory + family adapters (edge service)
├── gateway/            Background gateway runtime + service lifecycle
├── llm_usage/          Content-free LLM usage accounting
├── pairing/            DM sender approval store and pairing codes
├── providers/          LLM provider implementations on a common base
├── sdk/                Internals for the public Python SDK facade
├── security/           SSRF guards, workspace access/policy, private media
├── session/            Session history, compaction, goal state, WebUI turn coordination
├── skills/             Built-in skills as markdown + YAML frontmatter (no .py)
├── templates/          Jinja2 prompt templates (identity.md, SOUL.md, HEARTBEAT.md, ...)
├── triggers/           Local trigger store and runner
├── utils/              Small shared helpers (paths, documents, artifacts, logging bridge)
├── web/                Packaging shim for the built WebUI (nanobot/web/dist/)
├── webui/              Gateway HTTP surface: settings, sessions, media, OIDC, transcripts
├── config_base.py      Pydantic `Base` with camelCase alias generator
├── nanobot.py          Public Python SDK facade
├── optional_features.py Optional dependency inspection/installation
└── process_runtime.py  Process lifecycle + lock files
```

Repository root also contains `tests/`, `webui/` (React SPA source), `tui/` (Bun/OpenTUI
terminal UI), `scripts/`, and `docs/`.

---

## Module Organization

### Channels are self-contained packages

`nanobot/channels/<name>/` is the unit of contribution. Packages are discovered by
`pkgutil.iter_modules` scanning in `_channel_package_names` / `discover_plugins`
(`nanobot/channels/registry.py`) — there is no central
registration list to edit. A channel package owns everything about that platform:

```
nanobot/channels/feishu/
├── manifest.py       ChannelPlugin descriptor + ChannelSetupSpec (no runtime imports)
├── runtime.py        FeishuChannel: the BaseChannel implementation
├── connect.py        Interactive connect/QR-login flow; duck-typed, needs `async handle()`
├── validation.py     Field validation called from the manifest
├── config.py         Channel-local config parsing
├── instances.py      Multi-instance management descriptor
├── history.py        Platform history fetch
├── websocket.py      Transport specifics
├── tests/            Package-local pytest suite (16 test files)
└── webui/            Channel-owned React panels (index.tsx, *.tsx, locales/)
```

`manifest.py` must stay dependency-free: it is imported to render Settings even when the
channel's optional dependencies are not installed. Runtime imports belong in `runtime.py`,
which the manifest references as a string (`runtime=f"{__package__}.runtime:FeishuChannel"`).

Shared channel infrastructure lives at `nanobot/channels/` top level: `base.py` (BaseChannel),
`plugin.py` (ChannelPlugin), `contracts.py`, `manager.py` (lifecycle), `registry.py`
(discovery), `connect.py` (`ChannelConnectError`), `_manifest.py` / `_setup.py` (underscore-
prefixed internal helpers). See [`docs/channel-package-guide.md`](../../../docs/channel-package-guide.md).

### Providers build on a common base

`nanobot/providers/base.py` (~1.7k lines) holds the shared `LLMProvider`. Most hosted
providers need **no new file**: add a `ProviderSpec` to `nanobot/providers/registry.py` and a
field to `ProvidersConfig` in `nanobot/config/schema.py`. Write a dedicated module only when
the OpenAI-compatible path genuinely cannot serve the provider — the existing dedicated
implementations are Anthropic, Azure OpenAI, Bedrock, GitHub Copilot, OpenAI Codex, OpenAI
Responses (`openai_responses/`), and xAI Grok. `factory.py` resolves config to a concrete
class; every resolution path must be traceable from the factory.

### Tools

Every agent-facing capability is one module under `nanobot/agent/tools/`, discovered by
`pkgutil` scan plus `nanobot.tools` entry points in `ToolLoader.discover` / `_discover_plugins`
(`nanobot/agent/tools/loader.py`).
`base.py` defines the `Tool` contract, `schema.py` the JSON schema conversion, `registry.py`
the live registry. Path handling must route through `path_utils.py` / `filesystem.py`.

### Extensions are an edge projection, not a core service

`nanobot/extensions/` projects existing families (channels, tools, skills, MCP, optional
features, agent plugins) into one inventory for management surfaces. Adapters live in
`nanobot/extensions/adapters/`, one file per family. Runtime families remain authoritative
for their own configuration and lifecycle; the registry never becomes the owner. See
[`extension-registry.md`](./extension-registry.md).

### WebUI backend

`nanobot/webui/` is the gateway's HTTP surface, served alongside the WebSocket endpoint by
the `websocket` channel. It is organized by concern, not by REST resource:
`ws_http.py` (`GatewayHTTPHandler`, the top-level dispatcher, ~3k lines),
`settings_routes.py` (settings dispatch), `settings_system.py` / `settings_models.py` /
`settings_capabilities.py` (domain handlers), `settings_contracts.py` (transport-neutral
`SettingsRouteResult`), `settings_services.py` (`WebUISettingsConfig` — the config
read-modify-write lock). Dispatch is explicit `if path == ...` chains inside
`_dispatch_*_routes` methods; there is no decorator router.

### Where business logic lives

There is no `services/` layer. Logic lives with the runtime owner:

| Concern | Owner |
|---|---|
| Turn orchestration, session/workspace scope | `nanobot/agent/loop.py` |
| Provider + tool conversation loop | `nanobot/agent/runner.py` |
| Platform I/O, message formatting, retries | the channel package |
| Persistence and authorization for tenancy | `nanobot/collaboration/` |
| HTTP request validation and projection | `nanobot/webui/` |
| Cross-cutting pure helpers | `nanobot/utils/` |

`nanobot/utils/` is for small, dependency-light helpers only. It is not a dumping ground for
logic that belongs to a runtime owner.

---

## Naming Conventions

- Modules and packages: `snake_case`. Channel package directories use the platform's short
  name (`feishu`, `weixin`, `msteams`), no prefix or suffix.
- A leading underscore marks a package-internal module: `channels/_manifest.py`,
  `channels/_setup.py`, `agent/tools/_windows_job.py`. Do not import these from outside their
  package.
- Ruff rule `N` (pep8-naming) is enforced repo-wide, so classes are `PascalCase`, functions
  and variables `snake_case`, constants `UPPER_SNAKE`.
- Module-private names use a single leading underscore (`_migrate_v6`, `_ChannelActionStaleError`).
  This is used heavily and is the normal way to keep a module's surface small.
- Config models subclass `Base` from `nanobot/config_base.py`, which applies a camelCase alias
  generator with `populate_by_name=True`. Python fields stay `snake_case`; JSON is camelCase.
- Test files are `test_<subject>.py`; test functions `test_<behavior>`.

---

## Test Layout

`tests/` mirrors the `nanobot/` package structure: `tests/agent/`, `tests/channels/`,
`tests/collaboration/`, `tests/providers/`, `tests/webui/`, `tests/tools/`, and so on.

**Channel tests are the exception and are easy to miss.** They live inside the channel
package (`nanobot/channels/<name>/tests/`), and `pyproject.toml` sets:

```toml
[tool.pytest.ini_options]
testpaths = ["tests", "nanobot/channels"]
```

CI runs bare `python -m pytest`, so **both** roots are collected. A change to a channel must
consider its in-package suite, not just `tests/channels/`. Wheel and sdist builds exclude
`nanobot/channels/*/tests/**`, so these tests ship with the repo but not with the package.

Shared fixtures live in the root `conftest.py` (log isolation, session-root redirection, CA
bundle caching). Only two other conftest files exist: `tests/agent/conftest.py` and
`nanobot/channels/websocket/tests/conftest.py`. Prefer the root conftest or local fixtures
over adding new conftest layers.

---

## Examples

| Pattern | Read this |
|---|---|
| A complete channel package | `nanobot/channels/feishu/` (manifest, runtime, connect, validation, tests, webui) |
| A minimal channel manifest | `nanobot/channels/feishu/manifest.py` |
| Persistence behind one Protocol | `nanobot/collaboration/repository.py` + `local_repository.py` + `store.py` |
| An edge adapter that isolates failures | `nanobot/extensions/adapters/channels.py` |
| Transport-neutral route results | `nanobot/webui/settings_contracts.py` |
| Discovery by `pkgutil` + entry points | `nanobot/agent/tools/loader.py`, `nanobot/channels/registry.py` |

---

## Anti-Patterns

- **Do not add a new framework layer.** No dependency-injection container, no service locator,
  no generic repository/unit-of-work over the existing stores. `.agent/design.md` calls this
  out explicitly: "Less structure, more intelligence."
- **Do not inline channel- or provider-specific behavior into `agent/loop.py` or
  `agent/runner.py`.** Runtime state fan-out belongs in
  `nanobot/session/webui_turns.py::WebuiTurnCoordinator` or the channel adapter; the loop may
  only publish generic events from `nanobot/bus/runtime_events.py`.
- **Do not create a shared `channels/common/` helper module** to deduplicate send retries,
  media handling, or message splitting across channels. Each channel file stays readable on
  its own.
- **Do not move a channel's tests into `tests/`** or its React panels into `webui/`. The
  package is the unit; splitting it breaks discovery and packaging assumptions.
- **Do not import a channel's `runtime.py` from its `manifest.py`.** The manifest must load
  without the channel's optional dependencies installed.
