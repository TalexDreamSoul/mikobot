# Channel and optional extension adapters

## Goal

Project every dependency-free `ChannelPlugin` and configured channel instance into the canonical extension registry, split standalone optional features from channel-owned dependencies, and migrate Channel Settings/CLI/onboarding lifecycle callers while preserving channel-owned configuration, multi-instance isolation, Pair-Code authorization, deferred runtime imports, and the existing ChannelManager runtime owner.

## User Value

- Operators can inspect channel packages and exact instances without importing disabled optional SDKs or exposing credentials/state paths.
- Enable, disable, configure, dependency installation, reconnect/pairing, and runtime failure all report one canonical instance lifecycle.
- Existing channel-specific WebUI panels and connectors keep working while duplicate legacy feature lifecycle code is removed.
- Non-channel optional extras remain manageable without being duplicated when a channel owns the same dependency group.

## Confirmed Facts

- `ChannelPlugin` manifests already expose runtime/connector import strings, setup/management contracts, capabilities, dependencies, default enablement, and channel-owned WebUI entry without importing the runtime SDK.
- `ChannelManagementSpec` and instance helpers own configuration shape, instance expansion, runtime naming, local-state checks, and updates. Feishu/Weixin depend on this for multi-instance and legacy migration.
- `ChannelManager` owns live runtime objects/tasks, owner/instance mappings, start/stop/replacement, pairing-only states, errors, and status.
- `optional_features.py` currently mixes channel inventory, dependency installation, desired state, metadata refresh, and standalone extras; Settings then invokes ChannelManager separately and overlays status.
- Channel configure/connect routes, CLI plugin commands, and onboarding still use separate direct discovery/action paths.
- Current remote package-install policy and Pair-Code/collaboration authorization are server-owned boundaries and remain required in addition to canonical system-admin actions.

## Requirements

### R1 — Dependency-free package and instance projection

- Discover packages only through `discover_plugins`/`ChannelPlugin` manifests; snapshot must not call `load_channel_class`, `load_connector`, runtime validators, network identity refresh, or optional SDK imports.
- One manifest maps to one `CHANNEL_PACKAGE` package. Each configured/default instance maps to one `CHANNEL` component keyed by canonicalized raw `(channel_type, instance_id)`, never runtime display name.
- Preserve raw names only as bounded labels. Mixed-case/noncanonical names use deterministic 128-bit canonical suffixes without silent lowercase merging.
- Package/component revisions derive from public structural manifest facts plus safe desired/configured instance state; never include token, app secret, state/database directory, connector value, Pair Code, sender identity, or raw config/error.
- Bound third-party instance iterables before materialization and isolate malformed package/config projection from unrelated channels.

### R2 — Truthful lifecycle and status

- Map desired enabled, setup configured, dependencies installed, local state, and manager `(owner, instance_id)` status into canonical disabled/unavailable/enabling/enabled/reloading/failed states.
- A desired-enabled instance missing from the live manager is failed, not silently disabled. One running sibling never marks another instance enabled.
- Pairing-only runtime is a transient reconnect/reloading state, not a second enabled instance.
- Runtime errors are bounded/redacted and never expose paths, credentials, or raw SDK exceptions.

### R3 — Channel lifecycle actions

- Declare only supported component actions: inspect/configure/enable/disable, reconnect for connector-capable instances, and install where manifest dependencies are absent.
- Every action is exact-instance, system-admin, current-revision checked, and warning-acknowledged where executable installation requires it before side effects.
- Configure uses existing channel setup coercion/validation and serialized config update, preserving unrelated fields/instances and empty secret semantics.
- Enable sequence is single-owner and ordered: validate current setup → enforce install policy/install missing manifest dependencies → persist desired exact-instance state → request one ChannelManager reconcile/start → refresh channel metadata only after explicit successful enable/connect.
- Disable persists desired state then asks manager to stop/remove only the exact instance. Reconnect/pairing delegates to the channel-owned connector and manager, preserving server actor/Pair-Code scope.
- Adapter never creates a second runtime object/task or directly manipulates channel private state.

### R4 — ChannelManager port and startup

- Expose a narrow lifecycle/status port from ChannelManager for adapter use; do not let the adapter inspect manager private dictionaries.
- Startup dependency preparation uses the same channel dependency service as registry actions exactly once before enabled runtime import.
- Preserve runtime-name/credential/state-directory collision checks, pairing-only listeners, start/stop cancellation, outbound routing, and shutdown.
- Remove duplicate manager persistence/hot-action behavior only after the adapter owns the complete exact-instance sequence.

### R5 — Standalone optional features

- Channel manifest dependencies belong only to their channel package. A matching optional extra never creates a second standalone package.
- `OptionalFeatureExtensionAdapter` projects only non-channel extras with installed/unavailable state and truthful install/restart behavior.
- Generic requirement inspection/install mechanics remain the optional-feature domain owner. Install failures return bounded safe output; raw subprocess/package-manager details stay in logs.
- Remote package-install policy remains an additional server restriction and is never represented as sandboxing or package verification.

### R6 — Settings compatibility cutover

- Existing `/api/settings/nanobot-features` and channel routes temporarily render canonical channel/optional descriptors into current payload shapes.
- Every global mutation uses server-derived system-admin/actor and canonical target/revision/ack fields; client name/actor/admin cannot select authority.
- Channel configure/connect/pairing routes call registry actions or explicit connector flow tied to the same canonical target; successful connector completion reconciles exactly once.
- Existing channel-owned Vite modules, Feishu/Weixin custom panels, generic forms, translations, and instance UX remain intact.
- After all Settings callers migrate, delete direct channel discovery/enable/disable/runtime-status overlay branches and generic `channel_feature_action` plumbing.

### R7 — CLI and onboarding cutover

- CLI plugin/channel list/status reads canonical manifest/instance descriptors without eager runtime imports.
- CLI enable/disable uses canonical registry actions with local-owner system-admin context and existing installation policy.
- Explicit channel login/onboarding may import only the selected channel runtime/connector after target selection; catalog/list paths remain dependency-light.
- Remove `discover_all` only after every eager-runtime caller is migrated; retain `discover_plugins` and exact selected `load_channel_plugin` behavior.

### R8 — Compatibility and security

- Preserve existing config JSON shape, channel credentials, instances, default enablement, Feishu/Weixin legacy migration, Pair-Code assignment, session routing, and channel runtime keys.
- Preserve configured-but-failed desired state and restart-required truth; do not fabricate rollback of external installation/runtime failures.
- All descriptors, compatibility payloads, action results, and diagnostics exclude secrets, paths, raw install/runtime errors, and unbounded text.
- No sandbox, permission enforcement, signature verification, marketplace, or safety guarantee is introduced.

### R9 — Verification

- Tests cover disabled inventory without SDK import, multi-instance IDs/status/actions, dependency ownership/install policy, exact-instance config/reconnect, malformed isolation, redaction, manager call counts, Settings auth/no-side-effect, CLI/onboarding deferred imports, and existing custom panels.
- Run complete backend/frontend/type/lint/build and actual desktop/mobile channel management smoke.

## Acceptance Criteria

- [ ] AC1: Every channel manifest/default/configured instance appears exactly once with canonical IDs without importing its disabled runtime SDK.
- [ ] AC2: Instance revisions/lifecycle reflect only that instance's safe desired/configured/dependency/manager state and expose no secret/path.
- [ ] AC3: Feishu/Weixin default and named instances remain distinct; state/runtime/credential collision protections and legacy migration still pass.
- [ ] AC4: Non-admin, stale revision, missing acknowledgement, unknown/unsupported target, malformed values, and blocked remote install fail before config/install/manager/connector effects.
- [ ] AC5: Valid configure/enable/disable/reconnect changes one exact instance and calls each existing owner at most once in the documented order.
- [ ] AC6: Pairing-only and connector flows remain actor/instance scoped and never become duplicate enabled components.
- [ ] AC7: Channel dependencies are owned only by channel packages; standalone extras exclude channel names and retain truthful install/restart lifecycle.
- [ ] AC8: Startup and hot lifecycle share one dependency/reconcile path and keep ChannelManager as sole runtime owner.
- [ ] AC9: Existing Settings/channel-specific UIs use canonical state/actions and contain no duplicate direct channel lifecycle branch after cutover.
- [ ] AC10: CLI list/status do not import runtimes; explicit selected login/onboarding preserves current behavior and diagnostics.
- [ ] AC11: One malformed channel/instance cannot suppress unrelated descriptors and all public errors are bounded/redacted.
- [ ] AC12: Full backend/frontend/type/lint/build and actual desktop/mobile smoke pass.

## Out of Scope

- Provider, hook, CLI-app adapters and the final generic Extensions page.
- New channel protocols, credential formats, Pair-Code semantics, native mobile UI, or channel marketplace installation.
- Sandbox, permission enforcement, publisher signatures, KMS, durable audit storage, billing, and quotas.

## Key Decisions

- ChannelPlugin manifest/management contracts remain package owners; ChannelManager remains the sole live runtime owner.
- Channel manifest always wins an optional-extra name collision, yielding one canonical package.
- Inventory is strictly dependency-light; runtime/connector imports occur only after exact user action selects a target.
- Existing channel-specific WebUI modules are retained and receive opaque canonical target/revision fields rather than being rewritten.
