# Core capability extension adapters — technical design

## Boundary

This child connects existing core capability owners to `ExtensionRegistry`. It does not implement the final Extensions page or move domain lifecycle into the registry.

Runtime ownership remains:

- `ToolLoader` and `ToolRegistry`: tool discovery/construction/live-name ownership
- `SkillsLoader`: effective Skill precedence and loading
- `nanobot.agent.plugins`: package validation, fingerprint, marker, Skill/MCP declaration
- `MCPProvider`: desired/live server reconciliation, tool wrappers, connections, status, shutdown
- Settings router/domain: authenticated WebUI transport and compatibility presentation

The registry validates package/component metadata and lifecycle action preconditions, then delegates.

## Tool provenance

### Domain metadata

Add an immutable `ToolRegistrationMetadata` adjacent to `ToolRegistry`:

```python
@dataclass(frozen=True, slots=True)
class ToolRegistrationMetadata:
    source: ExtensionSource  # restricted to BUILTIN or PYTHON_ENTRY_POINT
    owner_name: str
    class_name: str
    scope: str
```

`ToolRegistry.register(tool, *, metadata=None)` remains backward compatible. Registering without metadata removes any previous metadata for that name because the new live owner is unknown to the core adapter. `unregister` removes both tool and metadata. Add a read-only snapshot returning `(tool_name, tool_object, metadata)` for current rows with metadata only.

Do not infer provenance from module names, wrapper classes, or `mcp_` prefixes.

### ToolLoader

Retain entry-point name with its class instead of flattening `_discover_plugins().values()`. At the exact existing successful `registry.register` point:

- built-in: metadata owner `nanobot`, source `ExtensionSource.BUILTIN`
- entry point: metadata owner is the original entry-point name, source `ExtensionSource.PYTHON_ENTRY_POINT`; the adapter applies the same deterministic canonical-name encoder used for arbitrary MCP/member names

Preserve `load() -> list[str]`, all ordering, collision decisions, enabled/scope checks, construction, and `_LegacyErrorPrefixTool`.

The adapter snapshots only the main application registry's current metadata rows. One built-in package owns live built-in tool components. Each entry point owns its currently live tool components. Entry-point packages are operator-trusted, in-process, unisolated, unenforced, and restart-required for lifecycle changes.

## Agent Plugin descriptor source

Extend `AgentPlugin` safe discovery metadata with:

- `revision: str | None`
- `skills: tuple[str, ...]`
- existing `mcp_servers`
- existing `enabled`

Capture a package fingerprint for the public revision and marker comparison, then revalidate after Skill/MCP declaration materialization and after marker writes. Each validation step reuses its captured fingerprint, but correctness takes precedence over a single total hash pass. Never return activation-marker JSON or root, and do not change marker format or legacy migration semantics.

The Agent Plugin adapter maps:

- source: `AGENT_PLUGIN`
- trust: `OPERATOR_TRUSTED`
- execution: `CHILD_PROCESS` when MCP declarations exist, otherwise `DATA`
- isolated: false
- permissions_enforced: false
- revision: current content fingerprint
- lifecycle: `ENABLED` if the exact marker validates; `DISABLED` otherwise
- actions: enable/disable

One package owns Skill and MCP declaration components. A deterministic name encoder maps noncanonical component names to `<safe-prefix>-<sha256(raw)[:32]>`; valid canonical names remain unchanged. Raw member names remain bounded display labels. The 128-bit suffix prevents practical collision-driven owner loss.

Malformed packages remain handled by existing discovery. A component-specific projection failure becomes a package diagnostic while other packages remain.

## Effective Skills adapter

Use `SkillsLoader.list_skills(filter_unavailable=False)` as the only precedence projection.

- source `plugin`: omit standalone row because Agent Plugin owns it
- source `workspace`: one workspace-content/data package per effective Skill
- source `builtin`: one first-party/data package per effective Skill

Revision is SHA-256 of bounded `SKILL.md` bytes read from the already selected path. The descriptor never includes the path or file content. Missing/unreadable/oversized files produce a bounded diagnostic and do not affect unrelated Skills.

Do not alter `load_skill`, prompt summaries, requirement checks, disabled-Skill configuration, or `ReadFileTool._resolve_read`.

## Configured MCP adapter

Configured MCP packages derive from `Config.tools.mcp_servers`, not the merged plugin/config projection. This avoids duplicating Agent Plugin ownership. User-config entries remain the effective winner where host names collide.

For each configured key:

- stable canonical name uses the deterministic encoder
- raw key remains safe bounded display text
- stdio → `OPERATOR_TRUSTED`, `CHILD_PROCESS`, `isolated=False`
- SSE/streamable HTTP → `REMOTE_SERVICE`, `REMOTE`
- revision is SHA-256 of `_server_signature(cfg)` serialized only inside the server; the digest alone is projected
- lifecycle overlays `MCPProvider.runtime_status`: connected→enabled, connecting→reloading, failed→failed, absent→discovered
- configuration target links to Apps/MCP Settings

No URL, auth header, env, command, args, cwd, OAuth data, or raw signature enters descriptors.

Configured MCP lifecycle stays with current MCP Settings operations in this child. The adapter may expose inspect/configure/reconnect metadata only when the existing callback can safely own it; it never closes or mutates `MCPProvider` internals.

## Core registry composition

Add a gateway/runtime composition helper that accepts explicit dependencies:

```python
def build_core_extension_registry(
    config: Config,
    tools: ToolRegistry,
    *,
    mcp_runtime_status: Callable[[], Mapping[str, str]] | None = None,
    mcp_reload: Callable[[], Awaitable[dict[str, Any]]] | None = None,
) -> ExtensionRegistry: ...
```

It creates a non-global registry and registers core-tool, Agent Plugin, effective-Skill, and configured-MCP adapters.

Gateway builds it after `AgentLoop` has registered default tools and before `ChannelManager`/WebUI settings construction. Pass it explicitly through `ChannelManager` → WebSocket runtime → `build_gateway_services` → `WebUISettingsServices`/Settings router. Other application surfaces may construct/retain it without adding commands; no module singleton is introduced.

Temporary subagent, Dream, filtered, OAuth-test, and MCP-test registries are intentionally excluded.

## Agent Plugin action flow

```text
SettingsRequest (server actor/admin)
  -> canonical ExtensionActionRequest
  -> ExtensionRegistry fresh snapshot/revision check
  -> AgentPluginAdapter.execute
  -> set_agent_plugin_enabled(marker owner)
  -> ExtensionActionResult
  -> existing bounded MCP reload callback exactly once
  -> refreshed compatibility payload + real hot_reload/restart result
```

The adapter itself does not call MCP reload; the Settings integration calls the existing callback after registry success. This keeps marker mutation and process reconciliation separately owned and prevents double reload.

If reload is absent, times out, raises, or returns `ok=False`, keep the marker change and use current `attach_mcp_hot_reload_result`/restart-required semantics. The response must not imply a live connected process.

## Settings authorization and compatibility projection

### Authorization

Before every MCP mutation, `SystemSettingsHandler._mcp_presets` requires `request.system_admin`. The actor ID comes only from `request.actor_user_id`; missing actor fails closed. Existing `_collaboration_identity` already ensures a stable local-owner user for token/local mode and explicit OIDC admin subjects for OIDC mode.

Reads remain on the current authenticated settings path.

### Compatibility row

Until the unified Extensions UI child, convert canonical Agent Plugin packages into current `McpPresetInfo` rows. Add optional fields:

- `extension_id`
- `extension_revision`
- `extension_lifecycle`
- `extension_trust`
- `extension_execution`
- `risk_acknowledgement_required`
- `permissions_enforced`

Keep `name` only as a presentation/action-key compatibility field; lifecycle dispatch uses `extension_id`, never `plugin-{name}` matching.

Remove direct Agent Plugin discovery from `mcp_presets_payload` and remove the `name.startswith("plugin-")` action branch after callers migrate.

### Frontend warning

`McpAppsCatalogRow` detects an executable Agent Plugin compatibility row and opens an `AlertDialog` before enable. The dialog states that the plugin is unisolated operator-trusted code that may access process-visible files, credentials, network, and resources. Confirm submits:

```ts
{
  name,
  extension_id,
  expected_revision: extension_revision,
  risk_acknowledged: true,
  ...ordinaryValues
}
```

Cancel sends nothing. Disable sends canonical target metadata without acknowledgement. Configured MCP actions retain their old request shape.

Complete English and Simplified Chinese translations; no raw safety promise.

## Failure handling

- Invalid/changed plugin revision: registry conflict, no marker mutation or reload.
- Non-admin or missing actor: 403, no mutation/reload.
- Marker write failure: bounded adapter error, no reload.
- Reload timeout/error/partial failure: marker remains authoritative; existing response marks restart/failure.
- Invalid adapter row: canonical registry diagnostic; unrelated packages remain.
- Configured `plugin-*` server name: remains configured because dispatch is by canonical extension ID/source, not prefix.

## Compatibility

- No config or marker schema migration.
- Tool registration/execution order and collisions remain unchanged.
- Agent Plugin Skills/MCP merge and config precedence remain unchanged.
- MCPProvider owns all processes and tools.
- Chat MCP attachments continue excluding Agent Plugin rows.
- Existing Apps/MCP route names remain until the final control-plane cutover.

## Rollback

Adapters and provenance are additive until the Settings lifecycle cutover. Rollback before cutover removes them without state changes. After cutover, restore the old caller and remove canonical action fields in the same rollback; marker/config formats remain compatible.
