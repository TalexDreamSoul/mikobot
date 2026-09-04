# Unified extension platform — technical design

## Boundary

This change introduces one canonical extension package/component registry and control contract. It does not replace the existing tool, channel, provider, hook, Skill, MCP, CLI-app, or optional-feature runtimes with a second dependency-injection framework. Existing runtime owners remain responsible for domain work and are attached through narrow adapters.

`AgentLoop` and `AgentRunner` remain consumers of resolved tools, hooks, and providers. They do not discover packages, authorize administrative actions, render inventory, or own plugin lifecycle.

Third-party extension safety is explicitly out of scope. External executable code remains operator-trusted and unisolated; the platform reports that fact and requires a risk acknowledgement before supported install/enable actions.

## Domain contract

### Extension package

An `ExtensionPackageDescriptor` is the stable owner shown to users and automation. It contains:

- canonical `id`
- display name and bounded description
- source and optional safe version/fingerprint label
- trust classification and execution classification
- lifecycle state and supported actions
- safe configuration destination
- zero or more component descriptors
- self-declared, explicitly unenforced permissions
- bounded safe diagnostic

A package may be first-party or external. Package identity is independent of whether it is currently enabled.

### Extension component

An `ExtensionComponentDescriptor` represents one contributed capability:

- stable canonical `id`
- owning package ID
- kind: `skill`, `mcp_server`, `tool`, `channel`, `llm_provider`, `image_provider`, `transcription_provider`, `hook`, `cli_app`, or `optional_feature`
- component-local name, label, capabilities, lifecycle, and supported actions
- execution classification: `data`, `in_process`, `child_process`, or `remote`
- optional safe configuration destination and diagnostic

Components cannot outlive or identify a different owner in one snapshot.

### Closed vocabularies

Use string enums for source, component kind, trust, execution, lifecycle, and action. Unknown values are adapter failures, not pass-through strings. Initial sources are:

- `builtin`
- `agent_plugin`
- `python_entry_point`
- `workspace`
- `configured`
- `channel_package`
- `provider_registry`
- `cli_app`
- `optional_feature`

Initial trust values are `first_party`, `operator_trusted`, `workspace_content`, and `remote_service`. Trust and execution remain separate axes: a remote provider is not in-process code; a static Skill is data but can still influence prompts.

### Stable identity

IDs are constructed only through validated helpers, never ad hoc in adapters:

- package: `ext:<source>:<name>`
- component: `<package-id>/<kind>:<name>`

Segments are lowercase ASCII, 1–128 characters, and limited to letters, digits, `.`, `_`, and `-`. Inputs that cannot be represented fail at the owning adapter. Duplicate package IDs or component IDs make that adapter snapshot fail visibly; they never overwrite another row.

The visible fingerprint is a bounded prefix used only to identify the package revision a user acknowledged. Absolute package roots never enter the descriptor.

## Registry contract

### Adapter

Each family implements one `ExtensionAdapter` protocol:

```python
class ExtensionAdapter(Protocol):
    @property
    def name(self) -> str: ...

    def snapshot(self) -> ExtensionAdapterSnapshot: ...

    async def execute(self, request: ExtensionActionRequest) -> ExtensionActionResult: ...
```

`snapshot()` is dependency-light and side-effect-free. It returns complete packages owned by that adapter plus bounded adapter diagnostics. `execute()` receives a system-admin-validated action request and delegates to the family's real lifecycle owner. An adapter declares no action it cannot perform.

### Registry

`ExtensionRegistry` owns:

- explicit adapter registration/unregistration
- deterministic snapshot assembly
- package/component identity and ownership validation
- duplicate rejection
- action-target lookup
- supported-action validation
- stale revision/risk-acknowledgement validation
- bounded safe result normalization

The registry does not own provider clients, channel runtimes, MCP processes, tool instances, hook objects, config files, or package installation.

Adapter registration returns an idempotent disposer. Re-registering the same adapter name is rejected rather than silently replacing live behavior.

### Failure isolation

Snapshot assembly calls every adapter independently. One adapter exception becomes one redacted `ExtensionAdapterDiagnostic`; packages from other adapters remain available. A failing adapter contributes no partial package tree, avoiding mixed revisions and orphan components.

Diagnostics are capped, strip control characters and absolute paths where recognizable, and never include tracebacks. Full exceptions remain server logs under existing logging policy.

### Action request

`ExtensionActionRequest` carries:

- actor/system-admin fact supplied by the authenticated HTTP/WS boundary
- target package or component ID
- closed action enum
- expected package revision when the action can execute external code
- `risk_acknowledged` for external executable install/enable actions
- bounded JSON values owned by the family adapter

The registry rejects unsupported actions, stale revisions, missing risk acknowledgement, non-admin mutation, and package/component ownership mismatches before dispatch.

Risk acknowledgement is disclosure, not authorization or sandboxing. Binding it to the current revision prevents a package changing between warning display and enablement.

## Adapter map

### Agent Plugins, Skills, MCP, and tools

- Agent Plugin adapter uses `discover_agent_plugins`, existing content fingerprints, enable markers, and MCP hot reload.
- One Agent Plugin package owns its static Skill and MCP-server components.
- Plugin permissions remain bounded self-declarations with `enforced=False`.
- Built-in/workspace Skills without a package owner receive stable synthetic package owners based on their existing source roots, without exposing those roots.
- `ToolLoader` exposes already-discovered built-in and Python entry-point classes to an adapter; inventory must not run a second entry-point scan or import.
- Python entry-point tool packages are `operator_trusted`, `in_process`, enabled at startup, and `restart_required` for lifecycle changes.
- Configured MCP servers retain their current owner/configuration and appear as configured remote or child-process packages. Agent Plugin MCP rows are not duplicated as standalone packages.

### Channels and optional features

- `ChannelPlugin` is already a dependency-free descriptor and becomes the package owner for its runtime, connector, setup, capabilities, and optional WebUI component.
- Runtime status comes from `ChannelManager`; configuration/install actions continue through existing channel/optional-feature transactions.
- Optional feature rows that exist only to install dependencies become components of their real channel/provider package where ownership is known. Standalone optional extras keep an `optional_feature` package.

### Providers

- LLM `ProviderSpec`, image-generation provider registrations, and transcription provider specs map to provider-registry packages/components.
- Availability and configured/enabled state derive from existing settings projections; secrets and endpoint headers never enter the registry.
- Configuration remains on Models/Image/Voice pages. The extension package carries a stable route key, not a URL containing state.

### Hooks

- Introduce a metadata-bearing registration wrapper for long-lived hook factories. Existing callables remain accepted by public constructors, but internal registrations migrate to explicit stable IDs.
- A raw external callable is represented as operator-trusted in-process code using a validated module/qualified-name identity and restart-required lifecycle; collisions fail visibly.
- Per-turn ephemeral hook objects are runtime values, not installed extension packages, and are not inventoried.

### CLI apps

- Installed CLI apps map to external executable packages owning one CLI-app component and the generated Skill component.
- Catalog-only candidates are not installed extension packages; they remain in the Apps catalog until installation succeeds.
- Install/update/uninstall/test actions remain owned by `CliAppManager` and require the external-code warning acknowledgement where applicable.

## Runtime and API data flow

### Snapshot

```text
GET /api/settings/extensions
  -> authenticate system admin
  -> ExtensionRegistry.snapshot()
  -> each adapter snapshots its existing source
  -> registry validates and normalizes
  -> safe JSON payload
  -> Extensions settings page
```

A separate user-safe projection may later filter by effective bot/project capabilities. The global inventory endpoint remains system-admin-only in this task.

### Action

```text
user opens package detail
  -> UI displays current revision + trust warning
  -> user acknowledges and chooses action
  -> mutation sends target, action, expected revision, acknowledgement
  -> HTTP/WS boundary verifies system admin
  -> registry validates state/action/revision
  -> owning adapter executes existing transaction
  -> registry returns canonical result + refreshed package row
```

### Lifecycle refresh

Family owners remain authoritative. Registry snapshots are derived on demand after actions; no second persisted state database is introduced. Live runtime callbacks may overlay current status, as MCP and channel settings already do.

## WebUI

Add an `extensions` Settings section with:

- package list grouped by owner
- family/source/trust/lifecycle filters
- text search
- package detail drawer/dialog
- component list and configuration links
- lifecycle actions declared by the adapter
- explicit warning confirmation for external executable install/enable
- self-declared/unenforced permission label
- safe errors and restart-required state

Existing Models, Image, Voice, Channels, MCP, Skills, and Apps pages remain configuration/catalog specialists. Once an action is migrated, duplicate lifecycle buttons are removed from those pages and replaced by a link to the owning extension package where useful.

## Adapter-first migration

1. Land contracts and registry with fixture adapters only; no runtime behavior changes.
2. Add Agent Plugin/Skill/MCP/tool adapters and switch Agent Plugin lifecycle API to the registry.
3. Add channel, provider, hook, CLI-app, and optional-feature adapters; switch each lifecycle action after family tests pass.
4. Add the unified WebUI and warning flow.
5. Remove migrated duplicate inventory/action code and run full integration verification.

During a family cutover, its existing runtime owner remains unchanged. The old control endpoint and new registry action are not kept as permanent alternatives: migrate all callers in the same child task, then remove the old action path.

## Compatibility

- Existing config schemas and domain IDs remain sources for adapters.
- Agent Plugin package layout and enable marker format remain unchanged unless a later child explicitly migrates them.
- Provider model selection, channel routing, MCP names, Skill precedence, and hook execution order remain unchanged.
- Canonical extension IDs are additive control-plane identities and do not replace session, bot, channel-instance, provider-config, or MCP tool names.
- No migration auto-enables, disables, installs, or removes anything.

## Performance

- Metadata discovery must remain dependency-light and avoid network calls.
- Static family metadata may reuse existing caches. Runtime status is overlaid from current owners.
- Snapshot construction is bounded by installed/configured extensions; marketplace candidates are excluded.
- Do not add persistent cache invalidation until profiling shows snapshot construction is material.

## Failure and rollback

- Contract/foundation rollback removes an unused registry package with no runtime effect.
- A family adapter can be removed before its caller cutover without changing that family's runtime.
- After caller cutover, rollback restores the old caller in the same commit; no data migration is involved.
- A failed action returns the previous authoritative family state and a bounded diagnostic.
- A stale warning revision fails with conflict and requires the user to reopen the package detail.
- Registry or adapter failure must never disable unrelated runtime capabilities automatically.
