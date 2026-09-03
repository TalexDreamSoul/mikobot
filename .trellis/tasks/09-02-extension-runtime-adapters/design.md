# Runtime extension adapters — technical design

## Boundary

This parent child groups three independently verifiable deliveries:

1. Channel packages, instances, and standalone optional features.
2. LLM/image/transcription providers plus persistent hook metadata.
3. Installed CLI apps and generated Skill ownership.

They share the fixed extension contracts and runtime composition but do not share domain lifecycle state. The final generic Extensions control plane remains a later child.

## Shared adapter rules

- Register each adapter explicitly in the runtime-local `ExtensionRegistry`.
- Use stable source/name IDs and deterministic 128-bit canonical suffixes for arbitrary external names.
- Snapshot existing manifests/config/status; do not import disabled runtimes, fetch catalogs, construct provider clients, or start processes merely for inventory.
- Delegate actions to current owners once. No adapter directly accesses another owner's private mutable state.
- Preserve canonical actor/admin/revision/ack validation before side effects.
- Return safe structural facts only and isolate failures per package/item.

## Channel and optional-feature architecture

### Package model

One `ChannelPlugin` is one `CHANNEL_PACKAGE` package. Each configured/default instance is a `CHANNEL` component with canonical name derived from channel and instance ID. A package may also own an `OPTIONAL_FEATURE` dependency component.

Package metadata derives from the manifest:

- display name, capabilities, dependencies-present boolean
- setup/management availability and safe official docs URL only where already public
- first-party/in-process trust for bundled channel packages
- desired/configured lifecycle and supported actions

Instance metadata derives through existing `channel_instance_specs`/management contracts and `ChannelManager` status callbacks. Never project setup secret values, state directories, runtime paths, Pair Codes, sender IDs, or raw errors.

### Actions

A `ChannelExtensionAdapter` receives explicit callables for:

- serialized config update/validation
- dependency preparation through existing optional-feature installer
- ChannelManager start/stop/restart/status
- connector/pairing operation where supported

Enable flow remains ordered: validate setup → install required dependencies if policy allows → persist desired state → ask manager to start/reconcile. Failure payload reports authoritative saved/runtime state and existing restart requirement. Disable persists desired state then manager stops. Instance operations preserve exact instance target.

### Standalone features

`OptionalFeatureExtensionAdapter` projects only extras not owned by a ChannelPlugin. It reuses optional dependency metadata/install state and current package policy. It never duplicates a channel extra.

Legacy `nanobot_features`/channel Settings and CLI/onboarding surfaces become compatibility projections/actions over these adapters, then delete direct channel inventory/action branches. Runtime class loading remains manager/selected-workflow-only.

## Provider and hook architecture

### Provider packages

`ProviderRegistryExtensionAdapter` joins metadata by canonical provider name:

- `LLM_PROVIDER` component from ordered `PROVIDERS` unless transcription-only
- `IMAGE_PROVIDER` component only for registered image provider names
- `TRANSCRIPTION_PROVIDER` component only from transcription specs

Aliases never create packages. Components carry their domain configuration target. Trust/execution are truthful first-party client integration/remote-service facts.

Revision hashes only canonical name, component kinds, selected/enabled/configured booleans, auth mode, and other non-secret structural state. Never reuse `provider_signature` or copy legacy Settings rows.

Only the selected enabled image component may declare `RELOAD`, delegated once to the existing runtime-control image reload callable. LLM components report configured/selected and next-turn refresh semantics but no reload action. Transcription is per-request and read-only.

Provider Settings continue validating/persisting config and OAuth; compatibility callers translate existing mutation results and only route the image runtime action through the adapter.

### Persistent hooks

Introduce immutable metadata wrappers:

```python
@dataclass(frozen=True, slots=True)
class RegisteredAgentHook:
    id: str
    hook: AgentHook
    source: ExtensionSource
    trust: ExtensionTrust

@dataclass(frozen=True, slots=True)
class RegisteredAgentHookFactory:
    id: str
    factory: AgentTurnHookFactory
    source: ExtensionSource
    trust: ExtensionTrust
```

A composition helper returns both the unchanged ordered raw hook/factory sequences for `AgentLoop.from_config` and a `HookExtensionAdapter` snapshot. Internal Gateway/SDK/CLI/API composition uses explicit stable IDs. Public AgentLoop constructor still accepts raw hooks/factories; raw long-lived callables can be wrapped with deterministic module/qualname identity for inventory but execution order remains input order.

The adapter is read-only/restart-bound. AgentLoop never discovers or registers it. Per-run/ephemeral hooks remain excluded.

## Installed CLI app architecture

### Package projection

`CliAppExtensionAdapter` consumes `CliAppManager.installed_payload()`/durable installed state only—never remote catalog. One `CLI_APP` package owns:

- executable CLI-app component
- generated Skill component
- safe source/strategy/configured/verification/lifecycle facts
- operator-trusted child-process/unisolated disclosure
- non-secret structural revision

Do not project entry-point command, package-manager argv, managed filesystem paths, registry URLs, artifact paths, or raw verification output.

### Generated Skill ownership

`CliAppManager._record_installed` writes durable manager ownership metadata into the generated Agent Plugin manifest. `AgentPluginExtensionAdapter` skips these managed packages. Existing enable marker and SkillsLoader behavior remain until runtime generation is separately simplified; only canonical inventory ownership changes.

Existing generated packages without the marker are recognized through installed state + exact managed Skill identity and upgraded on the next manager record/update, without deleting user-authored lookalikes.

### Actions and compatibility

Installed update/uninstall/test actions delegate once to `CliAppManager` after admin/revision/ack checks. Test is execution and requires the external-code warning. Uninstall removes installed state/generated managed Skill through the existing manager.

Catalog-only install remains on the existing Apps catalog action because no installed canonical target exists. It retains current remote-install authorization and warning behavior and must not be presented as a registry-owned installed action.

Apps/ThreadShell installed-only payloads and events remain temporary compatibility projections from the adapter while runtime `CliAppsTool` continues using installed state directly.

## Composition and migration

`build_core_extension_registry` grows explicit optional adapter dependencies/status/action callables; no module globals. Each grandchild lands adapters and composition, migrates its own callers, then deletes duplicate lifecycle branches before completion.

Cross-grandchild ordering:

1. Channel/optional may land independently.
2. Provider/hook may land independently after fixed registry.
3. CLI app adapter must coordinate its AgentPlugin exclusion with already-landed Agent Plugin adapter.

## Failure and rollback

- Read-only projection is additive until caller cutover.
- Domain config/state formats remain unchanged except additive managed-CLI ownership metadata.
- A failed action returns current domain state and safe restart/failure truth; no fabricated transactional rollback of external processes/package managers.
- Rollback restores the old caller in the same grandchild and removes its adapter action; no cross-family state migration is required.
