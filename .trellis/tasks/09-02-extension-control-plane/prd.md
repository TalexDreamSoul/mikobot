# Extensions control plane

## Goal

Expose the canonical extension snapshot and lifecycle actions through system-admin-only gateway endpoints and one bilingual, responsive Extensions Settings surface that shows real ownership, trust, execution, lifecycle, self-declared permissions, and revision-bound executable-risk warnings, without adding a second lifecycle owner or changing any family runtime.

## User Value

- Operators inspect every installed extension package and component, and its real owner, from one screen instead of reconstructing ownership from Apps, MCP, Channels, Models, Image, and Voice pages.
- Trust facts the backend already computes — unisolated external execution, operator-trusted origin, self-declared and unenforced permissions — become visible instead of dying inside Python.
- Lifecycle actions are offered only where the owning adapter declares them, so an unsupported control is never rendered and never silently fails.
- Enabling or installing external executable code always shows the same explicit warning bound to the exact revision being enabled, in English and Simplified Chinese, on desktop and mobile.

## Confirmed Facts

- `ExtensionRegistry.snapshot()`/`execute()` already exist (`nanobot/extensions/registry.py`) and one non-global registry is already composed per runtime by `build_core_extension_registry` (`nanobot/extensions/runtime.py:30`) at `nanobot/nanobot.py:146`, `nanobot/cli/gateway_runtime.py:550`, `nanobot/cli/commands.py:358`, and `nanobot/cli/agent.py:185`.
- The Settings boundary already holds that registry: `WebUISettingsServices.extensions: ExtensionRegistry | None` (`nanobot/webui/settings_services.py:124`) is populated through `create(..., extension_registry=...)` (`nanobot/webui/settings_services.py:140`).
- No generic control-plane transport exists. `_MODEL_ROUTES`, `_CAPABILITY_ROUTES`, and `_SYSTEM_ROUTES` (`nanobot/webui/settings_routes.py:98-142`) contain no `/api/settings/extensions*` path, and the WS mutation table (`nanobot/webui/ws_http.py:237-258`) contains no `settings.extension.*` name.
- No Settings section exists. `SettingsSectionKey` (`webui/src/components/settings/contracts.ts:3-16`) declares thirteen keys with no `extensions`; `SETTINGS_NAV_ITEMS` (`webui/src/components/settings/SettingsSidebar.tsx:35-46`) renders ten of them.
- Canonical fields are already produced by the compatibility presenters: `_feature_base` emits `extension_id`, `extension_revision`, `extension_actions`, `extension_lifecycle`, `extension_trust`, `extension_execution` (`nanobot/webui/nanobot_features_api.py:230-250`); `_action_target_fields` emits `action_target_id/revision/actions` (`nanobot/webui/nanobot_features_api.py:297-302`); `_agent_plugin_payload` additionally emits `risk_acknowledgement_required` and `permissions_enforced` (`nanobot/webui/mcp_presets_api.py:924-959`).
- The WebUI consumes only the identity half of that payload. `extension_id`/`extension_revision`/`action_target_id`/`action_target_revision` are read by `webui/src/components/settings/system/RuntimeSettings.tsx:113-119`, `webui/src/components/settings/system/AppsSettings.tsx:593-626`, `webui/src/components/settings/system/createSystemSettingsActions.ts:48-53`, `webui/src/components/settings/channels/ChannelInstancesPanel.tsx:127-161`, and `webui/src/components/settings/channels/ChannelSetupPanel.tsx:377-378`.
- `extension_trust`, `extension_execution`, `extension_lifecycle`, `extension_actions`, `action_target_actions`, and `permissions_enforced` are declared in `webui/src/lib/types.ts:1290-1308` and `webui/src/lib/types.ts:1463-1469` but are read by no component or hook; only test fixtures reference them.
- `ExtensionPackageDescriptor.isolated` exists (`nanobot/extensions/contracts.py:457`) and adapters already set it (`nanobot/extensions/adapters/agent_plugins.py:107,136`; `nanobot/extensions/adapters/tools.py:79`; `nanobot/extensions/adapters/capabilities.py:242`), but it is serialized into no HTTP payload and has zero references anywhere under `webui/src`.
- The only executable-risk warning shipping today is Agent-Plugin-specific, gated on `risk_acknowledgement_required` (`webui/src/components/settings/system/AppsSettings.tsx:557`), and carries a hardcoded English default string (`webui/src/components/settings/system/AppsSettings.tsx:959`).
- A different component already owns the name `ExtensionsPanel`: `webui/src/components/projects/ExtensionsPanel.tsx` edits the per-project `CollaborationExtensionProfile` Skill/MCP allowlist (`webui/src/lib/types.ts:560-571`) and is covered by `webui/src/tests/extensions-panel.test.tsx`. It is a tenant capability editor, not host inventory.
- Settings requests already carry server-derived actor and admin facts (`nanobot/webui/settings_routes.py:361-365`), and the client action payload convention is `extension_id` / `expected_revision` / `risk_acknowledged` built by `nanobotFeatureActionPayload` (`webui/src/lib/api.ts:1066-1077`).
- Ten locales exist under `webui/src/i18n/locales/`, including `en` and `zh-CN`.

## Requirements

### R1 — Canonical snapshot endpoint

- Add one read endpoint that returns the whole `ExtensionRegistry.snapshot()` for the running gateway, plus adapter diagnostics, in the canonical vocabulary and in the registry's deterministic order.
- The endpoint reuses `WebUISettingsServices.extensions`; it does not build a second registry, import family runtimes, or trigger discovery side effects.
- When no registry is composed, the endpoint returns an explicit empty-inventory result rather than a partial reconstruction from family payloads.
- Snapshot reads perform no mutation, no package installation, no network call, and no optional-SDK import.

### R2 — Canonical action endpoint

- Add one mutation endpoint that accepts target ID, closed action value, expected revision, risk acknowledgement, and bounded adapter values, and dispatches through `ExtensionRegistry.execute`.
- Actor identity and system-admin status come only from the existing server-derived request facts; the client payload cannot select actor, admin, or install-policy authority.
- The endpoint returns the canonical action result together with a freshly derived snapshot row for the affected package, so the client never patches lifecycle state locally.
- Unsupported action, unknown target, stale revision, missing acknowledgement, non-admin actor, and malformed values are rejected before any adapter call, each with its own distinguishable status.

### R3 — Complete and safe payload contract

- The serialized package carries: canonical ID, name, display name, description, source, trust, execution, `isolated`, lifecycle, revision, supported actions, self-declared permissions with the explicit unenforced fact, configuration destination key, bounded diagnostic, and its components.
- The serialized component carries: canonical ID, owning package ID, kind, name, label, capabilities, execution, lifecycle, supported actions, revision, configuration destination key, and bounded diagnostic.
- `isolated` and `permissions_enforced` must reach the client; the surface must render them rather than restate a hardcoded assumption.
- Payloads exclude secrets, environment values, absolute host paths, raw command lines, raw provider/channel signatures, and unbounded error text; the existing bounded-message normalization is the only error channel.
- Add matching TypeScript contracts and API functions; the new snapshot types are defined once and are not a copy of the legacy `NanobotFeatureInfo`/`McpPreset` extension fields.

### R4 — Extensions Settings surface

- Add an `extensions` key to `SettingsSectionKey` and a navigation entry, routed by the existing settings-section URL parameter so the section is deep-linkable.
- Provide a package list grouped by owning package with a detail view listing components, capabilities, declared permissions, execution/trust disclosure, lifecycle, supported actions, revision, and bounded error.
- Render only actions the adapter declared for that exact package or component; never synthesize an action from lifecycle state.
- The surface is responsive: usable list, detail, filters, and confirmation flows on mobile widths as well as desktop.
- The new surface must not be named `ExtensionsPanel` and must not reuse or modify `webui/src/components/projects/ExtensionsPanel.tsx` or the collaboration extension profile.

### R5 — Filters, search, and grouping

- Provide filters for component family (kind), source, trust, and lifecycle, plus free-text search over display name, package name, and component name.
- Filters compose, are reflected in the visible result count, and have an explicit empty state distinguishable from a failed snapshot.
- Adapter diagnostics are surfaced as a visible, attributable, bounded failure region so a broken adapter is never mistaken for an empty inventory.
- Ordering follows the registry's deterministic snapshot order; the client does not re-sort into a different stable order.

### R6 — Executable risk disclosure and revision-bound acknowledgement

- External executable packages display `isolated: false`, operator-trusted origin, execution location, and a concise warning that the code may access files, credentials, network, and other resources visible to the nanobot process.
- Declared permissions are labelled self-declared and unenforced wherever they appear; the surface makes no isolation, verification, signing, or safety claim.
- Enable and install for external executable packages require an explicit acknowledgement dialog and submit the exact revision shown in the dialog; cancelling sends no mutation.
- A stale revision returns a conflict that instructs the operator to reopen the package detail; the client does not silently retry with a refreshed revision.
- Data-only packages and components are not labelled executable; prompt-content influence may still be disclosed for Skill-kind components.

### R7 — Configuration destinations

- Component and package configuration destinations link to the existing Models, Image, Voice, Channels, MCP, Skills, and Apps sections using a stable route key resolved on the client.
- Links carry no state, credentials, host path, or URL-embedded secret; an unknown or unmapped destination degrades to no link rather than a broken navigation.
- This task adds links from the Extensions surface to family pages; it does not remove any control from those pages.

### R8 — Authorization and tenant separation

- Global snapshot and action endpoints require the local owner or an explicit system administrator through the existing authorization boundary and fail closed on missing identity.
- Ordinary authenticated non-admin users cannot enumerate host packages, adapter diagnostics, or global lifecycle failures through this endpoint.
- The tenant-facing per-project capability projection remains the separate collaboration extension profile; this task neither extends nor replaces it.
- Action results record actor, stable extension ID, action, outcome, and bounded reason in the response shape so a later durable audit store can consume them; this task adds no audit storage.

### R9 — Localization and accessibility

- Every new string has complete `en` and `zh-CN` entries; the new surface contains no untranslated literal, including trust warnings, permission labels, lifecycle names, action names, filter labels, and empty states.
- Canonical enum values are rendered through translated labels, not by displaying the raw enum string.
- Dialogs, filters, and list/detail navigation are keyboard reachable and screen-reader labelled, matching the conventions of the existing settings sections.

### R10 — Verification

- Backend tests cover snapshot shape and ordering, absent-registry behavior, redaction bounds, admin-only access, non-admin rejection, and each action rejection reason with no adapter call.
- Frontend tests cover filter composition, search, detail rendering of trust/`isolated`/unenforced permissions, declared-action-only controls, diagnostics region, stale-revision conflict, and warning cancel/confirm.
- Run the full Python and WebUI suites, Ruff, strict BasedPyright, ESLint, and the production build.
- Browser smoke covers desktop and mobile Extensions navigation, every family filter, a data-only package, an executable package with warning cancel and confirm, a restart-required row, a configuration deep link, and the `zh-CN` locale with no English literal on the new surface.

## Acceptance Criteria

- [ ] AC1: One authenticated system-admin snapshot request returns every package and component the composed registry reports, with canonical IDs, correct ownership, and the registry's deterministic order (parent AC1, AC2).
- [ ] AC2: The serialized payload includes `isolated`, trust, execution, lifecycle, supported actions, revision, permissions, and the unenforced fact, and the UI renders each of them (parent AC7).
- [ ] AC3: A package or component renders exactly the actions its adapter declared; an action absent from the descriptor is neither rendered nor submittable (parent AC5).
- [ ] AC4: Enable or install of an external executable package requires the acknowledgement dialog and the displayed revision; cancelling issues no request and mutates nothing (parent AC8).
- [ ] AC5: Submitting a revision that no longer matches returns a conflict, performs no adapter call, and instructs the operator to reopen the detail (parent AC5, AC8).
- [ ] AC6: A failing adapter appears as a bounded, attributable diagnostic while every unrelated package remains listed and actionable (parent AC3).
- [ ] AC7: Non-admin authenticated users receive no host inventory, no diagnostics, and no successful mutation from either endpoint; missing identity fails closed (parent AC9).
- [ ] AC8: No snapshot, diagnostic, or action response contains a secret, environment value, absolute host path, raw command line, or unbounded traceback (parent AC10).
- [ ] AC9: The Extensions section is reachable from navigation and by section deep link, and its list, filters, detail, and warning dialog are usable at mobile and desktop widths (parent AC11).
- [ ] AC10: Filters for family, source, trust, and lifecycle plus text search compose correctly and distinguish an empty result from a failed snapshot (parent AC11).
- [ ] AC11: Component configuration links open the correct existing Models, Image, Voice, Channels, MCP, Skills, or Apps section and carry no state or credential (parent AC11).
- [ ] AC12: `en` and `zh-CN` are complete for the new surface, and the `zh-CN` browser smoke shows no English literal (parent AC11, AC12).
- [ ] AC13: Full backend/frontend tests, Ruff, strict BasedPyright, ESLint, production build, and desktop/mobile browser smoke pass (parent AC12).

## Out of Scope

- Removing duplicate lifecycle controls from Apps, MCP, Channels, Models, Image, or Voice pages, and deleting the compatibility presenters in `nanobot/webui/nanobot_features_api.py` and `nanobot/webui/mcp_presets_api.py`. That deletion belongs to the cutover-hardening child, which runs after this surface is live.
- Adding, changing, or fixing any family adapter, including provider, hook, and CLI-app adapters.
- A tenant-visible extension inventory endpoint, changes to the collaboration extension profile, or bot/project capability projection.
- Durable audit storage, telemetry, persistent snapshot caching, and background refresh.
- Marketplace, search over uninstalled packages, package download, publisher verification, signatures, dependency installation, or automatic upgrades.
- Sandboxing, resource isolation, permission enforcement, or any safety guarantee for third-party extensions.

## Key Decisions

- The control plane is a transport and a presenter only. Every lifecycle decision stays with the owning adapter, and the client never derives enabled state locally.
- The new surface is additive: family pages keep their controls throughout this child so the two paths can be compared during verification, and the duplicates are deleted in the following child under one review.
- `isolated` and `permissions_enforced` are rendered from the payload rather than hardcoded, because the current hardcoded Agent Plugin warning is exactly the pattern that made the fields invisible.
- Risk acknowledgement is disclosure bound to a revision, not authorization and not a security control.
- The name `Extensions` is already taken in the project scope by the tenant capability editor; the host surface takes a distinct component name to keep the two boundaries separable.
