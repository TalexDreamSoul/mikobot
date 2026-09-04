# Unified extension platform

## Goal

Create one canonical, DeepSeek-Harness-inspired extension tree for every extension mechanism nanobot already exposes, so operators can inspect ownership, components, trust, configuration links, and lifecycle from one place without rewriting the agent core or claiming that third-party code is safe.

## User Value

- Operators see which package owns each tool, Skill, MCP server, channel, model provider, hook, and optional application capability instead of navigating unrelated registries.
- Every extension reports a truthful lifecycle and supported actions; enable, disable, reload, and restart requirements no longer depend on hidden family-specific conventions.
- External executable code carries an explicit risk warning, while first-party and data-only components remain clearly distinguishable.
- Existing configuration screens remain focused on domain details and link back to one shared extension identity rather than duplicating lifecycle state.

## Confirmed Facts

- Agent Plugins v1 already group static Skills and stdio MCP servers under one fingerprint-bound package with explicit enable/disable.
- Built-in and Python entry-point tools are discovered by `ToolLoader`; external entry points are imported directly into the gateway process.
- Channel packages already expose dependency-free `ChannelPlugin` manifests and lazily import runtime classes through `nanobot/channels/registry.py`.
- LLM providers use static `ProviderSpec` metadata plus backend construction in `nanobot/providers/factory.py`; image and transcription providers have separate registries.
- Agent hooks are currently passed as hook objects or factories to `AgentLoop` and have no stable descriptor or independent lifecycle identity.
- Skills, MCP servers, CLI apps, and optional feature packages each maintain separate inventories and status projections.
- Existing MCP hot reload already owns reversible tool registration and process cleanup. Channel runtime management and provider reload have separate owners.
- The project architecture requires a small AgentLoop/AgentRunner and extensions at edges. A second runtime framework beside the current loaders would violate that constraint.
- DeepSeek Harness provides useful patterns—stable plugin identity, explicit component ownership, capability seams, inspectable composition, and reversible effects—but explicitly treats dynamic plugins as bash-equivalent trust rather than a security boundary.

## Requirements

### R1 — Complete extension-family coverage

- The unified tree covers every current plugin, registry, or installable-extension mechanism: Agent Plugin packages, Skills, MCP servers, built-in and entry-point tools, channel packages, LLM/image/transcription providers, registered hook factories, CLI apps, and optional feature packages.
- Built-in extensions appear in the same query model as external extensions, with different source and trust classifications.
- Components remain owned by their real package or registration source; the tree must not fabricate one global package that obscures ownership.
- Mechanisms that are ordinary application commands or internal callbacks, not current extension points, are not relabelled as plugins merely for completeness.

### R2 — Canonical package and component contract

- Define one bounded descriptor vocabulary for extension package identity, source, version/fingerprint, display metadata, components, capabilities, trust, execution location, lifecycle, supported actions, configuration destination, and safe diagnostics.
- Package IDs and component IDs are stable, namespaced, and collision-resistant across families.
- Parent/child relationships express package-to-component ownership; references use IDs rather than Python objects or filesystem paths.
- Snapshot ordering is deterministic and one broken adapter cannot suppress unrelated rows.
- Inventory discovery must reuse existing dependency-free manifests and registries; it must not import optional runtime SDKs solely to render metadata.

### R3 — Registry and adapter boundary

- One extension registry is the canonical read/control API used by CLI, WebUI, and future automation.
- Each existing family supplies a narrow adapter that maps its real metadata and lifecycle owner into the canonical contract.
- The registry validates identity, ownership, lifecycle transitions, action support, and safe output; family adapters retain domain-specific configuration and runtime work.
- Registration and unregistration are reversible and idempotent so reloads do not leak duplicate components.
- `AgentLoop` and `AgentRunner` consume only the tool/hook/provider results they already need; they do not become plugin registries.

### R4 — Truthful lifecycle

- Canonical lifecycle states cover discovered, unavailable, disabled, enabling, enabled, reloading, disabling, failed, changed, and restart-required.
- Supported actions are explicit per package/component: inspect, configure, enable, disable, reload, reconnect, install, uninstall, or restart-required. Unsupported actions fail before reaching an adapter.
- Agent Plugin changes continue to invalidate fingerprint-bound enablement; MCP components reconcile through the existing hot-reload owner.
- Python entry-point tools and startup-bound hooks report process-restart lifecycle rather than pretending to hot reload.
- Channels and providers keep their existing runtime/config transactions but report results against the same stable extension identity.
- Failures are bounded, redacted, attributable to one owner, and never converted into a false enabled state.

### R5 — Trust disclosure without safety enforcement

- Nanobot does not sandbox, audit, verify, or guarantee the safety of third-party extensions in this task.
- Executable external packages report `isolated: false`, operator-trusted status, execution location, and a concise warning that they may access process-visible files, credentials, network, and resources.
- Manifest permissions are displayed only as self-declared, unenforced metadata.
- Data-only Skills and metadata-only descriptors are not mislabeled as executable, though prompt-content risk may still be disclosed.
- Enabling or installing executable third-party code requires an explicit warning acknowledgement; cancellation performs no action.

### R6 — Authorization and safe projection

- Only the local owner or explicit system administrator may install, uninstall, enable, disable, reload, reconnect, or globally configure extensions.
- Ordinary users may inspect only the safe capabilities already visible through their assigned bot/project; they cannot enumerate host-only packages or global lifecycle failures.
- API payloads exclude secrets, environment values, absolute host paths, raw command lines, credential references that reveal values, and unbounded exception text.
- Audit-ready action results include actor, stable extension ID, action, outcome, and bounded reason for later durable audit storage; this task does not implement that storage.

### R7 — Unified management experience

- Add one Extensions management surface grouped by owning package, with filters for component family, source, trust, and lifecycle.
- Package detail lists components, capabilities, declared permissions, execution/trust warning, current state, supported actions, safe error, and links to existing channel/provider/MCP configuration pages.
- Existing family-specific pages stop presenting duplicate package lifecycle controls after their action migrates to the unified surface.
- English and Simplified Chinese text is complete, accessible, and usable on desktop and mobile.

### R8 — Compatibility and migration

- Existing Agent Plugins, custom MCP servers, tool entry points, channel manifests/configuration, provider selection, hooks, Skills precedence, CLI apps, and optional features preserve observable runtime behavior during migration.
- Existing IDs exposed to users remain accepted only where they are current domain IDs; canonical extension IDs are additive until each caller is migrated.
- No extension is automatically installed, enabled, disabled, or granted a capability by migration.
- Clean cutover removes duplicate inventory/action code after every caller uses the canonical registry; permanent compatibility shims are not retained.

### R9 — Verification

- Contract tests cover ID stability, collisions, ownership, deterministic ordering, adapter failure isolation, state transitions, unsupported actions, redaction, and trust classification.
- Family tests cover Agent Plugin fingerprint replacement, MCP hot reload, tool entry-point restart state, channel lifecycle, provider availability, hook registration, Skills, CLI apps, and optional features.
- Authorization tests prove system-admin mutation and tenant-safe projection boundaries.
- WebUI tests and browser smoke cover warning acknowledgement, filtering, detail inspection, configuration links, actions, mobile layout, and Chinese localization.

## Acceptance Criteria

- [ ] AC1: One registry query returns every in-scope extension package and component with stable namespaced IDs and correct ownership.
- [ ] AC2: Built-in, workspace, configured, Agent Plugin, Python entry-point, channel-package, and provider sources receive truthful, deterministic classifications.
- [ ] AC3: Every existing extension family registers through one adapter contract; a failing adapter leaves all other family snapshots intact and visibly reports its bounded failure.
- [ ] AC4: Registration, reload, and unregistration are idempotent and leave no duplicate tools, hooks, MCP capabilities, channels, or provider rows.
- [ ] AC5: Lifecycle actions are rejected unless declared by the owning adapter and successful actions report the resulting canonical state.
- [ ] AC6: Agent Plugin/MCP actions hot reload; Python entry-point tools and startup hooks report restart-required; channel and provider actions preserve their existing transactional behavior.
- [ ] AC7: External executable extensions are labelled operator-trusted and unisolated; declared permissions are labelled self-declared and unenforced.
- [ ] AC8: Enabling or installing executable third-party code requires explicit warning acknowledgement, and cancelling causes no mutation.
- [ ] AC9: Only system administrators can mutate global extension state; ordinary users receive only bot/project-visible safe capability projections.
- [ ] AC10: Extension inventory, diagnostics, and action results expose no raw secrets, environment values, absolute host paths, raw command lines, or unbounded tracebacks.
- [ ] AC11: The unified Extensions UI covers every in-scope family and removes migrated duplicate lifecycle controls while retaining links to domain configuration.
- [ ] AC12: Existing extension behavior remains compatible, and focused/full Python, strict type, lint, WebUI, production-build, desktop, and mobile checks pass.

## Out of Scope

- OS sandboxing, containers, microVMs, resource enforcement, network policy, secret brokering, plugin auditing, or any plugin safety guarantee.
- Enforcing self-declared plugin permissions.
- Marketplace/search, arbitrary package download, publisher verification, signatures, revocation lists, dependency installation, or automatic upgrades.
- Tenant KMS/Vault, durable audit storage, billing, and quotas.
- Replacing Python with Cordis or moving the agent loop, persistence, and transports into a new dependency-injection framework.
- Relabelling non-extension internal functions as plugins.

## Key Decisions

- The operator explicitly chose warnings instead of plugin isolation or safety responsibility.
- Legacy `nanobot.tools` Python entry points remain available in SaaS deployments as operator-installed code with gateway-equivalent trust.
- The first-class registry covers all existing extension families, not only Agent Plugins or tools.
- Trust warnings are disclosure, not a security control.
- Migration is adapter-first: the canonical registry is introduced as the single metadata/control contract, existing family owners feed it through adapters, and each family then cuts lifecycle callers over in bounded stages before duplicate paths are removed.

