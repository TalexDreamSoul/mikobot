# Architecture

This page maps nanobot's runtime behavior to source files. Use it when you are debugging internals, reviewing a PR, adding a provider/channel/tool, or trying to understand where a user-visible behavior comes from.

For the product-level mental model, read [`concepts.md`](./concepts.md) first.

## Core Flow

```mermaid
flowchart LR
    Channel["Channel<br/>CLI, WebUI, chat apps"] --> Bus["MessageBus<br/>InboundMessage"]
    Bus --> Loop["AgentLoop<br/>session, workspace, context"]
    Loop --> Runner["AgentRunner<br/>provider/tool loop"]
    Runner --> Provider["Provider<br/>LLM backend"]
    Provider --> Runner
    Runner --> Tools["Tools<br/>files, shell, web, MCP, cron"]
    Tools --> Runner
    Runner --> Loop
    Loop --> Outbound["MessageBus<br/>OutboundMessage"]
    Outbound --> Channel

    Loop -. reads/writes .-> State["Session, memory,<br/>hooks, skills, templates"]
```

Main files:

| Area | Files |
|---|---|
| Message events and queue | `nanobot/bus/events.py`, `nanobot/bus/queue.py` |
| Turn orchestration | `nanobot/agent/loop.py` |
| Provider/tool conversation loop | `nanobot/agent/runner.py` |
| Context construction | `nanobot/agent/context.py` |
| Session storage and compaction | `nanobot/session/manager.py` |
| Long-term memory and Dream | `nanobot/agent/memory.py` |

## Agent Loop vs Agent Runner

`AgentLoop` owns the channel-facing turn:

- receives inbound messages;
- determines the effective session and workspace scope;
- builds context;
- wires hooks, progress, and channel metadata;
- publishes outbound messages.

`AgentRunner` owns the model-facing loop:

- sends messages to the selected provider;
- handles streaming deltas and reasoning blocks;
- executes tool calls;
- feeds tool results back into the model;
- stops when a final answer is produced or runtime limits are hit.

MCP connections are application-owned infrastructure. Composition roots create
an `MCPProvider`, share its `ToolRegistry` with `AgentLoop`, await `connect()`
before use, and guarantee `aclose()` during shutdown; the loop does not manage
that lifecycle. `AgentLoop.from_config()` therefore requires a caller-owned
`ToolRegistry`; callers using MCP share it with their application-owned
`MCPProvider`.

Keep this split in mind when debugging. If a problem is about channel routing, session keys, workspace selection, or outbound delivery, start in `agent/loop.py`. If it is about provider calls, tool calls, streaming, or iteration limits, start in `agent/runner.py`.

## Projects and Channel Assignments

`nanobot/collaboration/` is the small control plane the gateway uses to serve
other people. It stores users, projects, project membership, one
`ChannelAssignment` per chat-channel instance, Pair Codes, and conversation
bindings in one JSON document under the runtime `collaboration/` directory.

| Concept | Meaning |
|---|---|
| Administrator | The local owner, or an OIDC subject listed in `admin_subjects`. Manages every project and may hand any channel instance to any member. |
| Project | A workspace plus members and optional Skill / MCP allowlists. |
| Channel assignment | One connected channel instance routed to one project on behalf of one member. Created by a one-time Pair Code sent from that channel. |
| Conversation binding | An explicit group or thread bound to a project. |

Only the gateway composition root (`nanobot/cli/gateway_runtime.py`) wires a
`CollaborationRepository` into `AgentLoop` and `ChannelManager`. The CLI, the
Python SDK, and the API server pass nothing and run single-user. Inside the
loop, the host owner's own CLI and token-authenticated WebUI turns never touch
the repository; only chat-channel senders and OIDC or proxy principals are
resolved to a project scope, which sets their workspace, session namespace,
and allowed capabilities.

Internal cron, trigger, continuation, recovery, and subagent turns inherit the
persisted user, project, route, assignment requirement, and binding of their source
session. A sender label alone is not proof of an internal turn. Missing or revoked
scope fails closed instead of falling back to the host. Tool execution rechecks the
admitted capability, including queued subagents and policy changes during a model call.

Member chat-channel keys include both user and project. Public WebUI chat IDs stay
stable and pin their initial project in server-owned session metadata. Changing a
default project affects new chats, not the ownership of an existing conversation.
Reads, attachment, recovery, streaming, and context references recheck ownership;
cross-session references additionally require the same user and project. Historical
files are retained rather than guessed into a new namespace.

Two WebUI surfaces manage this. **Channels** lists every assigned instance with
its project, its runtime status, and its Pair Code flow; **Projects** covers
members and capability allowlists.

## Providers

Provider metadata is centralized in `nanobot/providers/registry.py`. Configuration fields live in `nanobot/config/schema.py`.

Provider selection uses:

- explicit `agents.defaults.provider` or preset provider;
- provider registry keywords;
- API key prefixes and API base URL hints;
- local provider fallback when `apiBase` is configured;
- gateway fallback for providers that can route many model families.

Provider implementations live in `nanobot/providers/`. Most hosted providers use the OpenAI-compatible implementation, while Anthropic, Azure OpenAI, AWS Bedrock, OpenAI Codex, and GitHub Copilot have specialized paths.

Useful docs:

- [`providers.md`](./providers.md) for practical setup;
- [`configuration.md#providers`](./configuration.md#providers) for exact provider reference.

## Channels

Channels translate external platforms into `InboundMessage` events and send `OutboundMessage` events back to the platform.

Main files:

| Area | Files |
|---|---|
| Base channel contract | `nanobot/channels/base.py` |
| Channel packages | `nanobot/channels/<channel>/` |
| Discovery and lifecycle | `nanobot/channels/manager.py` |
| WebSocket/WebUI channel | `nanobot/channels/websocket/` |

Channels are discovered by scanning self-contained packages under `nanobot/channels/`. Add a channel by contributing one package that follows [`channel-package-guide.md`](./channel-package-guide.md).

## WebUI and Gateway

`nanobot gateway` starts:

- enabled chat channels;
- the WebSocket channel when configured;
- workspace-scoped cron service;
- system jobs such as Dream and heartbeat;
- the health endpoint on `gateway.port`.

The packaged WebUI is served by the WebSocket channel, not the health endpoint:

| Surface | Default |
|---|---|
| Health endpoint | `http://127.0.0.1:18790/health` |
| WebUI/WebSocket | `http://127.0.0.1:8765` |

WebUI source lives in `webui/`. The production build is written to `nanobot/web/dist/` and bundled into the wheel.

Useful docs:

- [`webui.md`](./webui.md) for the WebUI user guide;
- [`../webui/README.md`](../webui/README.md) for frontend source development;
- [`websocket.md`](./websocket.md) for protocol details.

## Tools

Tools are discovered from `nanobot/agent/tools/` and plugin entry points.

Important files:

| Tool area | Files |
|---|---|
| Tool base and schema | `nanobot/agent/tools/base.py`, `nanobot/agent/tools/schema.py` |
| Discovery | `nanobot/agent/tools/registry.py` |
| Shell execution | `nanobot/agent/tools/shell.py` |
| Filesystem tools | `nanobot/agent/tools/filesystem.py` |
| Web search/fetch | `nanobot/agent/tools/web.py` |
| MCP tools | `nanobot/agent/tools/mcp.py` |
| Cron | `nanobot/agent/tools/cron.py`, `nanobot/cron/` |
| Image generation | `nanobot/agent/tools/image_generation.py` |
| Runtime self-inspection | `nanobot/agent/tools/self.py` |

Tool behavior is part of the model contract. Keep user-visible tool names, schemas, and error messages stable unless a change is intentional.

## Config and Paths

The config schema lives in `nanobot/config/schema.py`. Loading and saving live in `nanobot/config/loader.py`. Runtime path helpers live in `nanobot/config/paths.py`.

Defaults:

| Path | Default |
|---|---|
| Config | `~/.nanobot/config.json` |
| Workspace | `~/.nanobot/workspace/` |
| Sessions | `<config-dir>/sessions/<workspace-id>/*.jsonl` (default: `~/.nanobot/sessions/...`) |
| Memory | `<workspace>/memory/` |
| Cron store | `<workspace>/cron/jobs.json` |
| WebUI/media/log runtime data | config directory subdirectories such as `webui/`, `media/`, and `logs/` |

The schema accepts both camelCase and snake_case keys, but saves config with camelCase aliases.

### Agent-Owned State vs Effective Project Context

The host's selected **working directory** is not the same concept as a managed
collaboration **Project**. Host directory selection preserves the configured agent
profile and memory. A member's project determines shared files and permissions,
while private profile and memory live outside that shared directory.

| Concern | Host owner | Project member |
|---|---|---|
| Project instructions and ordinary file/tool root | Selected working directory | Authorized project workspace |
| Profile and automatic memory/archive/Dream | Configured agent workspace | `<config-dir>/users/<user-id>/projects/<project-id>/` |
| Conversation sharing | Legacy single-user rules | Same user and project only |
| Inbound attachments | Host media store | `<config-dir>/users/<user-id>/media/<project-id>/`, exact authorized files |
| Executable tools | Owner's configured policy | Linux Bubblewrap required for shell; host-managed CLI Apps unavailable |

Filesystem capabilities grant only the member's exact profile/memory files,
authorized attachments, and allowed built-in Skills in addition to shared project
files. Private journals are not writable through ordinary file tools. Member Dream
does not publish private conversation-derived Skills into shared project directories.
Session-bound media signatures require current authorization on fetch and do not
allow Markdown image paths to stage arbitrary host files.

## Memory and Sessions

Session history is the near-term conversation replay. Memory is the longer-term workspace state.

| Store | File area |
|---|---|
| Session JSONL files | `<config-dir>/sessions/<workspace-id>/` |
| Long-term memory | `<workspace>/memory/MEMORY.md` |
| Consolidation source history | `<workspace>/memory/history.jsonl` |
| Bootstrap identity files | `<workspace>/SOUL.md`, `<workspace>/USER.md`, templates under `nanobot/templates/` |

Dream is implemented in `nanobot/agent/memory.py` and scheduled by the runtime when enabled.

The paths above describe the host store. Member stores use the private user/project
root described above. Existing mixed historical memory is not automatically erased
or declassified; operators should review legacy profile/memory files before sharing
an upgraded deployment with unrelated people.

## Security Boundaries

Security-sensitive code paths include:

| Boundary | Files |
|---|---|
| Workspace scope | `nanobot/security/workspace_access.py`, `nanobot/security/workspace_policy.py` |
| Shell sandboxing | `nanobot/agent/tools/shell.py` |
| SSRF/network checks | `nanobot/security/network.py`, `nanobot/agent/tools/web.py` |
| PTH guard and CLI startup security | `nanobot/security/` and CLI entrypoints |
| Channel access control | channel config in `nanobot/channels/*.py` |

When changing tools, channels, file access, WebUI workspace behavior, or network fetching, treat security as part of the functional behavior and update docs if the user-facing boundary changes.

## Extension Points

| Extension | How |
|---|---|
| Provider | Add `ProviderSpec` in `providers/registry.py`, add schema field in `config/schema.py`, implement provider only if the generic backend is not enough |
| Channel | Export a `ChannelPlugin` descriptor, keep its runtime and optional setup surfaces in one package, and follow [`channel-package-guide.md`](./channel-package-guide.md) |
| Tool | Implement a tool under `agent/tools/` or expose a plugin entry point |
| Agent Plugin | Add a v1 package under `<workspace>/plugins/` and enable it from Apps |
| MCP | Add `tools.mcpServers` config or bundle the server in an Agent Plugin |
| Skill | Add workspace skills under `<workspace>/skills/`, bundle them in an Agent Plugin, or add built-in skills under `nanobot/skills/` |
| CLI App | Add it to the CLI Apps catalog; the installer owns its executable lifecycle and writes a skills-only Agent Plugin |

Prefer existing registry/discovery patterns over ad hoc wiring.

## Testing and Verification

Common checks:

```bash
pytest tests/test_openai_api.py::test_function -v
ruff check nanobot/
cd webui && bun run test
cd webui && bun run build
```

Choose tests based on the changed surface:

| Change | Minimum useful verification |
|---|---|
| Provider behavior | Provider unit tests or a mocked API path; `nanobot agent -m "Hello!"` with safe config when possible |
| Channel behavior | Channel tests plus `nanobot gateway` startup path |
| WebUI behavior | WebUI tests/build and, for routing/settings/chat changes, browser-level verification through the gateway |
| Tool behavior | Tool unit tests and an agent-run path when schema or model-facing behavior changes |
| Docs | Link checks, command accuracy against CLI/schema, and `git diff --check` |

For user-facing flows, prefer at least one verification path through the public surface the user actually touches: CLI command, HTTP endpoint, WebSocket/WebUI, chat channel, or packaged import.
