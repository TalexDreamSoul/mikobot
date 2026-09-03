# Extension Registry Contracts

## Scenario: Canonical extension package and component registry

### 1. Scope / Trigger

Use this contract whenever a tool, Skill, MCP server, channel, provider, hook, CLI app, or optional feature is projected into the unified extension inventory or receives a lifecycle action.

The registry is an edge service. Runtime families remain authoritative for their configuration and lifecycle. `AgentLoop` and `AgentRunner` do not discover extensions or own this registry.

Third-party trust labels are disclosure only. The registry does not claim sandboxing, plugin verification, permission enforcement, or safety.

### 2. Signatures

Canonical IDs:

```python
extension_package_id(source: ExtensionSource, name: str) -> str
extension_component_id(
    package_id: str,
    kind: ExtensionComponentKind,
    name: str,
) -> str
```

Registry:

```python
class ExtensionRegistry:
    def register(self, adapter: ExtensionAdapter) -> Callable[[], None]: ...
    def snapshot(self) -> ExtensionSnapshot: ...
    async def execute(
        self,
        request: ExtensionActionRequest,
    ) -> ExtensionActionResult: ...
```

Adapter:

```python
class ExtensionAdapter(Protocol):
    @property
    def name(self) -> str: ...
    def snapshot(self) -> ExtensionAdapterSnapshot: ...
    async def execute(
        self,
        request: ExtensionActionRequest,
    ) -> ExtensionActionResult: ...
```

### 3. Contracts

Package IDs use `ext:<source>:<name>`. Component IDs use `<package-id>/<kind>:<name>`.

Name segments are lowercase ASCII, 1–128 characters, and contain only letters, digits, `.`, `_`, or `-`. They cannot contain `..`, path separators, whitespace, controls, or non-ASCII characters. Callers canonicalize their own domain names; ID helpers never silently lowercase them.

Descriptors are frozen slot dataclasses. Package/component ownership is exact:

- package `id == extension_package_id(source, name)`
- component `package_id == package.id`
- component `id == extension_component_id(package.id, kind, name)`
- one package cannot contain duplicate component IDs

Trust and execution are separate. External in-process or child-process packages require risk acknowledgement for `enable` and `install`; permissions remain self-declared and unenforced.

Public descriptor metadata rejects obvious absolute POSIX, Windows, `~/`, and `~user/` host paths while allowing normal HTTP(S) URLs. Diagnostics remove controls, redact common absolute paths, and are capped at 1,000 characters.

Bounded collections:

- permissions: 128
- capabilities: 256
- actions: closed enum size
- components/package: 512
- packages/adapter: 1,024
- diagnostics/adapter: 256
- packages or diagnostics/global snapshot: 4,096 each
- registered adapters: 256
- action JSON: 64 keys/object, depth 8, 1,024 nodes/scalars, 2,000 characters/string

All untrusted iterables are consumed with a `limit + 1` traversal; never materialize an unbounded iterable before checking its limit.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Duplicate adapter name | Reject registration; keep original adapter |
| 257th live adapter | Reject before mutation |
| Adapter exception or malformed tree | Drop that adapter's complete snapshot; preserve unrelated adapters |
| Local or cross-adapter target collision | Lexically later adapter fails atomically; never overwrite |
| Diagnostic names another adapter/target | Reject owning adapter snapshot |
| Non-system-admin action | Reject before adapter callback |
| Missing target | Return `target_not_found`; no callback |
| Undeclared action | Return `action_not_supported`; no callback |
| Executable external enable/install without acknowledgement | Reject; no callback |
| Missing/stale expected revision | Reject; no callback |
| Malformed action values | Reject before callback |
| Adapter result changes action/package/target | Reject as invalid adapter result |

The registry catches ordinary adapter exceptions only. `BaseException` and cancellation/system-exit conditions propagate.

### 5. Good / Base / Bad Cases

**Good**: an adapter returns one canonical package with bounded components; the registry publishes it and dispatches only declared actions to that adapter.

**Base**: an adapter reports no packages. It remains registered and contributes an empty atomic snapshot.

**Bad**: an adapter returns a component owned by another package, an unbounded generator, an absolute host path in public metadata, or a result for another target. Reject it at the relevant boundary without affecting unrelated adapters.

### 6. Tests Required

- Every enum value and canonical ID boundary.
- Frozen/deep-frozen descriptor and action values.
- Package/component owner and duplicate invariants.
- Registration-specific idempotent disposer.
- Registration-order-independent snapshot ordering and collision attribution.
- Adapter exception, malformed row, oversized row, and diagnostic-owner isolation.
- Bounded iterable tests that assert at most `limit + 1` pulls.
- Authorization, supported-action, warning acknowledgement, revision, and no-dispatch assertions.
- Exact owner dispatch and cross-target result rejection.
- Diagnostic path/control/length redaction and HTTP(S) URL preservation.

### 7. Wrong vs Correct

#### Wrong

```python
packages = tuple(adapter.packages())  # infinite/malicious iterable can exhaust memory
index[package.id] = package           # silently overwrites another owner
```

#### Correct

```python
snapshot = ExtensionAdapterSnapshot(
    adapter_name=adapter.name,
    packages=adapter.packages(),  # constructor performs bounded limit+1 consumption
)
# Validate the complete local tree and all collisions before merging any row.
```

Never create a module-global registry, persist derived lifecycle state, or import optional runtime SDKs solely to render inventory metadata.

## Scenario: Core capability adapters and MCP Settings cutover

### 1. Scope / Trigger

Apply this scenario when exposing built-in/entry-point tools, Agent Plugins, effective Skills, or configured MCP servers through the canonical registry, or when mutating Agent Plugin state from Settings.

Existing owners stay authoritative: ToolLoader/ToolRegistry for live tools, SkillsLoader for precedence, Agent Plugin markers for enablement, and MCPProvider for connections/reload/shutdown.

### 2. Signatures

```python
ToolRegistry.register(
    tool: Tool,
    *,
    metadata: ToolRegistrationMetadata | None = None,
) -> None

ToolRegistry.registration_snapshot() -> tuple[RegisteredTool, ...]

build_core_extension_registry(
    config: Config,
    tools: ToolRegistry,
    *,
    skills_loader: SkillsLoader | None = None,
    config_loader: Callable[[], Config] | None = None,
    mcp_runtime_status: Callable[[], Mapping[str, str]] | None = None,
) -> ExtensionRegistry
```

Agent Plugin marker mutation accepts an optional `expected_revision`; enable compares and revalidates that package revision before retaining the marker.

MCP OAuth user operations (`start`, `status`, pasted callback completion, `cancel`) require a server-derived administrator actor and remain bound to the actor that created the flow. Provider callbacks remain state-bound.

### 3. Contracts

- Tool provenance is captured only at the existing successful ToolLoader registration point. Never rescan/import entry points for inventory.
- Metadata-free overwrite or unregister removes stale provenance. MCP/image/dynamic tools are not attributed to the core-tool adapter.
- Agent Plugin packages expose a full content digest as revision, but no root, marker path, command, argv, environment, or plugin-data path.
- Package content is revalidated after Skill/MCP declaration materialization and after marker writes. A changed revision revokes the marker and publishes no changed declarations as approved.
- Noncanonical component/package names use a deterministic 128-bit SHA-256 suffix (`[:32]`), not a short collision-prone slug alone.
- Effective Skill projection reuses workspace → enabled Agent Plugin → built-in precedence. Plugin Skills remain owned by their Agent Plugin.
- Configured MCP revisions hash only structural, non-secret facts. Compatibility payloads never return command, argv, cwd, URL/userinfo/query, headers, env values, OAuth tokens, raw reload errors, or absolute paths.
- Agent Plugin compatibility rows use canonical package ID as `name`; configured legacy `plugin-*` MCP names can coexist without identity collision.
- Every global MCP mutation, including OAuth start/status/complete/cancel, requires server-derived system admin and actor. Client actor/admin fields are ignored.
- Executable plugin enable requires current revision plus explicit warning acknowledgement. Data-only Agent Plugins use revision-bound enable without false executable warning. Disable requires admin but no acknowledgement.
- MCP reload remains single-owner. Marker state remains authoritative when live reload fails; the response reports bounded restart/failure truth.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Entry point collides with live built-in tool | Built-in remains; no entry-point provenance row |
| Metadata-free tool replaces attributed tool | Remove stale provenance |
| Package changes during Skill/MCP materialization | Omit changed declarations, revoke marker |
| Enable revision is stale | Conflict before marker mutation/reload |
| Executable enable lacks acknowledgement | Conflict before marker mutation/reload |
| Data-only enable | Dispatch revision-bound action without executable warning |
| MCP reload fails after marker change | Keep marker, report bounded restart/failure |
| Ordinary authenticated user mutates MCP/OAuth | 403 before config/credential/flow side effects |
| Another admin actor reads/completes/cancels flow | Unknown/expired response; no flow side effect |
| Configured MCP named `plugin-*` | Stay configured-MCP owned; never extension-prefix dispatch |
| One malformed Agent Plugin projection | Keep unrelated packages; emit safe local diagnostic |

### 5. Good / Base / Bad Cases

**Good**: an administrator opens an executable Agent Plugin row, acknowledges the exact displayed revision, and the registry changes the marker before the existing MCP owner performs one reload.

**Base**: a Skills-only Agent Plugin is data-only. It receives no executable-code warning but still sends its canonical ID and current revision.

**Bad**: infer tool provenance from module names, dispatch by a `plugin-` string prefix, expose connection strings for convenience, accept actor/admin from the browser, or keep a marker after materialized declarations no longer match its fingerprint.

### 6. Tests Required

- Tool registration order, scope, collision, wrapper, overwrite/unregister, and no second entry-point load.
- Agent Plugin Skill/MCP ownership, content revision, expected-revision enable, replacement and materialization races.
- Effective Skill precedence and bounded content revisions.
- Configured MCP transport/status mapping and complete secret/path redaction.
- Canonical Agent Plugin/configured `plugin-*` coexistence and exact dispatch ownership.
- System-admin/actor no-side-effect tests for all MCP and OAuth mutations; cross-actor flow rejection.
- Executable warning cancel/confirm, data-only direct path, refreshed-revision snapshot, focus restoration, and mobile layout.
- Full backend/frontend suites, strict type/lint, production build, and actual browser smoke.

### 7. Wrong vs Correct

#### Wrong

```python
if name.startswith("plugin-"):
    set_agent_plugin_enabled(workspace, name.removeprefix("plugin-"), True)
```

#### Correct

```python
await extensions.execute(ExtensionActionRequest(
    context=server_derived_admin_context,
    target_id=canonical_extension_id,
    action=ExtensionAction.ENABLE,
    expected_revision=current_revision,
    risk_acknowledged=True,
))
```

The browser supplies only opaque target/revision/acknowledgement fields; the server supplies actor and administrator authority.
