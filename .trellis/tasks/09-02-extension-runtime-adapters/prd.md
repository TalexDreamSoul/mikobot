# Runtime extension adapters

## Goal

Connect channels, standalone optional features, LLM/image/transcription providers, installed long-lived hooks, and installed CLI apps to the canonical extension registry, then migrate their existing lifecycle callers without importing disabled runtimes for inventory, changing provider/channel selection, duplicating process ownership, or exposing executable/configuration secrets.

## User Value

- Operators see every remaining installed runtime capability under the same stable package/component ownership and lifecycle vocabulary.
- Channel instances, provider capabilities, installed hooks, and installed CLI apps report truthful availability, restart/hot-reload behavior, trust, and configuration destinations.
- Existing Channels, Models/Image/Voice, Apps, onboarding, and CLI surfaces retain their domain workflows but stop owning duplicate inventory/lifecycle state.
- External executables receive explicit risk disclosure without a false sandbox or permission-enforcement claim.

## Confirmed Facts

- `ChannelPlugin` is already a dependency-light immutable manifest; disabled channel inventory does not need its optional runtime SDK.
- `ChannelManager` owns live channel instances/tasks/status, while optional-feature code owns dependency installation and persisted channel enablement.
- LLM provider order is routing priority. Provider factory/model runtime, image tool reload, and per-request transcription loading have different lifecycle semantics and must remain independently owned.
- Persistent hooks are currently anonymous `AgentHook`/factory lists stored by AgentLoop; per-run/ephemeral SDK, API, subagent, and progress hooks are not installed extensions.
- `CliAppManager` owns catalog, installed state, controlled package-manager argv, generated Agent Plugin Skills, execution, update/uninstall/test, and runtime context.
- Installed CLI-app generated Skills currently also appear as Agent Plugin packages, creating duplicate future canonical ownership unless explicitly marked/excluded.
- Current channel/feature, provider, and CLI app Settings mutations are global host changes and require server-derived system administrator authority.

## Requirements

### R1 — Channel package and instance adapters

- One dependency-free `ChannelPlugin` manifest maps to one channel package; each configured instance maps to one channel component with stable `(channel_type, instance_id)` identity.
- Inventory reads manifests/setup/management/dependencies/capabilities and safe configured-field facts without importing disabled runtime SDKs or exposing secrets/paths.
- Desired/configured/runtime/pairing states map truthfully to canonical lifecycle and bounded diagnostics.
- Channel actions delegate to existing config transactions, dependency installer, connector/pairing owner, and `ChannelManager` start/stop/restart methods. The adapter never creates a second runtime task/process owner.
- Preserve multi-instance runtime names, state-directory isolation, Pair-Code ownership, legacy default-instance migration, and channel-specific validation.

### R2 — Standalone optional features

- Channel-owned dependency extras are components of their channel package and are not duplicated as standalone features.
- Only non-channel optional extras receive standalone `OPTIONAL_FEATURE` packages.
- Install/enable/disable/uninstall/restart requirements remain owned by existing optional-feature configuration and package-manager functions.
- Package installation requires system-admin authorization and the existing local/remote installation policy; no ordinary tenant package mutation is introduced.

### R3 — Provider registry adapter

- One canonical provider package groups only capability components it actually implements: LLM, image, and/or transcription.
- Preserve ordered LLM provider matching, model-prefix routing, gateway/local fallback, model presets/fallbacks, transcription-only exclusions, image registration order, and transcription aliases/default models.
- Provider inventory reports structural source/capability/configured/availability facts only; it excludes API keys/hints, endpoints, proxy, headers/body/query, models selected by users, OAuth account/expiry/tokens/flows, and paths.
- Provider extension revisions derive only from non-secret structural facts, never `provider_signature` or raw configuration.
- OAuth login/logout and credential persistence remain provider-specific Settings actions, not generic extension lifecycle.

### R4 — Provider lifecycle semantics

- LLM provider settings trigger existing next-turn `ModelRuntimeResolver` invalidation; the adapter does not claim immediate reload.
- The selected enabled image provider may expose `RELOAD` only by delegating once to the existing image runtime-control/tool replacement owner.
- Transcription exposes no reload because current channels/WebUI resolve and construct the provider on each request.
- Image reload failures preserve truthful restart-required results without creating a second tool/process owner.

### R5 — Installed long-lived hook inventory

- Introduce metadata-bearing wrappers for persistent hook factories and direct hooks with stable ID, source, trust, execution, and lifecycle facts.
- Preserve exact observable hook order: progress, registered factories, registered hooks, turn factories, turn hooks; preserve ephemeral suppression and SDK opt-in semantics.
- Internal Gateway/SDK/CLI/API persistent hooks migrate to explicit metadata at composition. Raw external callables remain accepted for API compatibility and report restart-bound operator-trusted fallback identity when inventoried.
- Per-turn caller hooks, SDK capture/stream hooks, API usage capture, subagent hooks, and progress hooks remain excluded.
- AgentLoop executes supplied hook values only; it does not become the canonical registry owner.

### R6 — Installed CLI app projection

- Canonical CLI app packages are derived from durable installed state, not marketplace/catalog availability or network refresh.
- Each installed app owns its CLI executable component and generated Skill component, with operator-trusted child-process/unisolated disclosure and a safe structural revision.
- Runtime execution remains exclusively `CliAppsTool`/`CliAppManager.run`; registry actions never execute arbitrary catalog commands directly.
- Installed update/uninstall/test lifecycle delegates to `CliAppManager`. Catalog-only install remains on the existing catalog workflow until a separately approved marketplace/install contract can represent non-installed targets.
- Generated CLI-app Agent Plugin manifests carry durable manager ownership metadata; AgentPlugin adapter excludes those roots from separate canonical package projection while SkillsLoader behavior remains compatible.

### R7 — Authorization, warnings, and compatibility cutover

- Every global channel/feature/provider/CLI-app lifecycle mutation requires server-derived local-owner/system-admin identity before config, package-manager, connector, credential, or runtime side effects.
- External executable enable/update/test actions use the canonical package revision and explicit warning acknowledgement where the fixed registry contract requires it.
- Existing family pages/routes remain temporary compatibility transports, consume canonical rows/actions after migration, and delete their duplicate inventory/action branches in the same child/grandchild.
- Catalog/search candidates and provider OAuth status may remain family-specific presentation data but cannot become a second installed lifecycle source.

### R8 — Composition and safe failure

- `build_core_extension_registry` accepts explicit runtime-family adapters/callbacks and remains non-global. Family owners provide status/actions; registry stores no derived persistent state.
- One malformed package/instance/provider/hook/app yields a bounded local diagnostic and cannot hide unrelated packages.
- All projections exclude raw secrets, absolute paths, commands/argv/env, URL credentials/query, unbounded traces, and raw package-manager/runtime errors.
- No sandbox, permission enforcement, publisher verification, or safety guarantee is added.

### R9 — Verification

- Adapter tests cover discovery without runtime imports, ownership/IDs, state/action mapping, per-item failure isolation, redaction, and no duplicate packages.
- Compatibility tests cover every migrated route/CLI caller, system-admin no-side-effect behavior, lifecycle single-owner invocation, reload/restart truth, provider/channel selection invariants, hook order, and generated Skill ownership.
- Run full backend/frontend tests, strict type/lint, production build, and browser/CLI smoke for migrated surfaces.

## Acceptance Criteria

- [ ] AC1: Every ChannelPlugin and configured instance appears exactly once without importing disabled runtime SDKs or exposing channel secrets/paths.
- [ ] AC2: Channel actions use existing config/dependency/connector/manager owners exactly once and preserve multi-instance, Pair-Code, and restart behavior.
- [ ] AC3: Channel-owned extras are not duplicated; standalone extras retain truthful package/install/restart state.
- [ ] AC4: Provider packages group exactly their real LLM/image/transcription capabilities without changing provider order, matching, presets, aliases, or fallback behavior.
- [ ] AC5: Provider descriptors/revisions contain no credentials, endpoints, proxy, nested request configuration, OAuth identity/token/flow data, or paths.
- [ ] AC6: Only selected image capability delegates one reload; LLM reports next-turn refresh and transcription remains per-request without false hot reload.
- [ ] AC7: Persistent hooks have stable truthful metadata while hook ordering, errors, ephemeral suppression, and per-run caller hooks remain unchanged.
- [ ] AC8: Installed CLI apps appear once with executable/Skill ownership; generated Agent Plugin packages no longer duplicate them.
- [ ] AC9: CLI app update/uninstall/test delegates only to CliAppManager; catalog install remains explicitly family-owned and out of canonical installed actions.
- [ ] AC10: Every migrated global mutation rejects non-admin/missing actor before side effects and external executable actions show revision-bound warning disclosure.
- [ ] AC11: Existing family compatibility routes/pages use canonical state/actions and contain no permanent duplicate lifecycle implementation after cutover.
- [ ] AC12: One malformed family item cannot suppress unrelated extension packages and all errors are bounded/redacted.
- [ ] AC13: Full backend/frontend/type/lint/build plus actual migrated UI/CLI runtime smoke pass.

## Out of Scope

- Final unified Extensions page/API and cross-family filters/detail UX.
- Marketplace/search/catalog candidates as canonical installed packages; arbitrary remote package installation.
- Generic provider OAuth/credential lifecycle, model routing refactor, transcription cache/reload, or channel protocol changes.
- Per-turn/ephemeral hooks and subagent-only capabilities.
- Sandbox, permission enforcement, signatures, KMS, durable audit storage, billing, and quotas.

## Key Decisions

- This task is decomposed into channel/optional, provider/hook, and installed CLI-app grandchildren with independent verification.
- Domain owners remain authoritative; adapters project and delegate but never recreate processes, clients, package-manager state, or persisted lifecycle.
- Provider OAuth and catalog-only CLI install remain family-specific because the current canonical registry addresses installed/configured targets only.
- Generated CLI app Skills have one canonical CLI-app owner even while their existing Agent Plugin marker remains a runtime implementation detail.
