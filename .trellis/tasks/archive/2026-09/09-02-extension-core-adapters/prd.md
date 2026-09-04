# Core capability extension adapters

## Goal

Connect Agent Plugins, effective Skills, configured MCP servers, built-in tools, and Python `nanobot.tools` entry points to the canonical extension registry, then migrate the existing Agent Plugin lifecycle caller to that registry without changing tool precedence, Skill precedence, MCP process ownership, or current domain configuration behavior.

## User Value

- Operators can inspect the real owner, source, trust, revision, components, and lifecycle of core agent capabilities from one canonical snapshot.
- Enabling an executable Agent Plugin uses a current revision, an explicit warning acknowledgement, and server-derived administrator identity rather than a synthetic plugin name.
- Existing Apps/MCP Settings continues to work until the unified Extensions page lands, but no longer owns a duplicate Agent Plugin discovery or enable/disable implementation.
- Installed Python entry-point tools are visible as operator-trusted, in-process, restart-bound code without being imported a second time.

## Confirmed Facts

- `ToolLoader.load` is the sole owner of built-in/entry-point discovery order, scope checks, `enabled(ctx)`, construction, legacy wrapping, and built-in collision precedence. It currently discards successful-registration provenance.
- `ToolRegistry.register` currently records only the live tool by name. MCP and image-generation owners can later replace/unregister tools in the same registry.
- Main SDK, CLI, API, and Gateway compositions all share one `ToolRegistry` with `AgentLoop`; temporary MCP-test/OAuth, Dream, per-turn filtered, and subagent registries have different scopes and are not the main installed inventory.
- Agent Plugin enablement is bound to a complete content fingerprint. Package replacement, movement, or content changes invalidate the marker before plugin Skills or MCP config remains effective.
- `SkillsLoader.list_skills` owns effective precedence: workspace, then enabled Agent Plugin, then built-in; disabled/unavailable filtering is layered afterwards.
- `MCPProvider` alone owns desired/live server state, tool registration, connection cleanup, runtime status, reload reconciliation, and shutdown.
- Existing WebUI Apps/MCP Settings independently discovers Agent Plugins and directly toggles their markers through a `plugin-{name}` special case before requesting MCP reload.
- Settings requests already carry server-derived actor ID and system-admin facts. Local-token ownership and configured OIDC administrator subjects resolve through the existing collaboration/OIDC boundary.

## Requirements

### R1 — Live tool provenance without a second discovery pass

- Extend successful `ToolRegistry` registrations with optional immutable provenance while preserving every existing caller and `register(tool)` behavior.
- `ToolLoader` records provenance only after the existing scope, enabled, construction, collision, and registration decisions succeed.
- Built-in records identify first-party built-in ownership; entry-point records retain the original entry-point name and identify operator-trusted in-process ownership.
- Preserve built-ins-first order, sorted built-in class order, existing entry-point iteration order, built-in-over-entry-point collision behavior, plugin overwrite behavior, legacy error wrapping, tool names, and schema-cache invalidation.
- Registry overwrite/unregister removes stale provenance. A core tool adapter reports only provenance whose exact tool object is still the live `ToolRegistry` value.
- MCP wrappers, image-generation replacements, per-turn filtered registries, Dream tools, temporary test/OAuth registries, and subagent-only tools must not be duplicated as main core-tool ownership.

### R2 — Agent Plugin package projection

- One Agent Plugin maps to one `AGENT_PLUGIN` package owning its valid static `SKILL` and MCP declaration components.
- Discovery exposes a safe full content revision, Skill names, MCP member names, and current enablement without exposing package root, activation-marker path, command, argv, environment, or plugin-data path.
- Package execution is `DATA` for Skills-only packages and `CHILD_PROCESS` when MCP executables exist. Trust is `OPERATOR_TRUSTED`, `isolated` is false, and permissions are self-declared/unenforced.
- Lifecycle is enabled only while the current fingerprint marker validates. A changed package is projected as changed or disabled according to one documented rule and receives a new revision.
- Invalid component names receive deterministic collision-safe canonical IDs or bounded package diagnostics; they must not erase unrelated valid plugin packages.

### R3 — Effective Skill projection

- Project only effective Skills selected by the existing workspace → enabled Agent Plugin → built-in precedence.
- Agent Plugin Skills remain components of their owning Agent Plugin package and are not duplicated as standalone Skill packages.
- Effective standalone workspace and built-in Skills receive stable packages/components, data execution, truthful trust, safe content revisions, and configuration links where one already exists.
- Registry snapshots never replace `SkillsLoader` prompt/loading logic or the filesystem tool's fingerprint-sensitive plugin Skill read authorization.
- Standalone Skill lifecycle is read-only in this child; disabled-Skills configuration remains with its current owner.

### R4 — Configured MCP projection and runtime status

- Project each configured MCP server exactly once without exposing URL, headers, environment, command, args, cwd, OAuth tokens, or secrets.
- Classify stdio as operator-trusted child-process execution and HTTP/SSE/streamable transports as remote-service execution.
- Derive a stable revision from a safe hash of the existing server signature, never from raw serialized values returned to the client.
- Overlay only the current `MCPProvider.runtime_status` facts and reuse its existing reload/reconnect owner. The adapter never directly connects, unregisters, closes, or mutates provider internals.
- User-configured MCP continues to win effective host-name collisions with Agent Plugin MCP. Every effective server has one owner in the canonical snapshot.

### R5 — Canonical Agent Plugin lifecycle action

- Agent Plugin enable/disable is dispatched through `ExtensionRegistry.execute` to the owning adapter.
- Enable requires system admin, exact current revision, and explicit risk acknowledgement before marker mutation. Disable requires system admin but no risk acknowledgement.
- Adapter execution only changes the existing fingerprint marker. The existing MCP reload callback runs once after a successful marker change.
- Preserve existing restart semantics: if reload is unavailable, times out, or partially fails, retain the authoritative marker change and report the actual hot-reload/restart-required result; do not claim live connection success and do not invent a best-effort rollback.
- Package replacement between list and enable fails as stale before marker mutation.

### R6 — Existing Settings compatibility cutover

- Existing MCP Settings list consumes Agent Plugin package data from the canonical registry compatibility projection; it no longer calls `discover_agent_plugins` independently.
- Agent Plugin rows carry safe canonical target ID, current revision, risk-acknowledgement requirement, trust/execution/lifecycle facts, and the existing display fields required by Apps Settings.
- The current MCP Settings enable flow shows a bilingual explicit warning and submits only target ID, current revision, and acknowledgement. Cancel sends no mutation. Disable sends the target without acknowledgement.
- Backend uses server-derived `SettingsRequest.actor_user_id` and `system_admin`; client payload cannot choose actor/admin facts.
- Remove the `plugin-{name}` lifecycle special case after all existing callers use canonical target metadata. Configured MCP operations retain their existing domain request shape and owner.
- A configured server whose legacy name begins `plugin-` must never dispatch an Agent Plugin action.

### R7 — Global settings authorization

- Every mutation under the global MCP preset/settings family requires the local owner or explicit system administrator, including configured MCP actions that have not yet migrated into the canonical registry.
- Reads continue to return the existing safe MCP catalog projection to authenticated settings users according to current product policy.
- Local-token owner identity uses the existing server-derived collaboration user ID; missing identity/admin facts fail closed rather than accepting client identity.

### R8 — Composition and lifecycle ownership

- Compose one non-global `ExtensionRegistry` for each real application runtime that owns a main `ToolRegistry`; adapters receive existing ToolRegistry/config/workspace/MCP callbacks explicitly.
- Gateway passes its registry into Settings services. SDK/CLI/API may retain the registry for future CLI/SDK exposure but do not add user-facing commands in this child.
- Main core inventory excludes ephemeral subagent registries and per-conversation filtered views; this exclusion is explicit and tested.
- No adapter owns MCP process shutdown, channel/provider lifecycle, package installation, or persistent extension state.

### R9 — Safe output and compatibility

- Canonical descriptors and compatibility payloads contain no absolute host path, secret, environment value, raw MCP signature, command line, or unbounded error.
- Existing Agent Plugin package format, activation marker, Skill precedence, MCP names, tool registration order/collisions, tool execution, provider reload, and chat MCP attachment behavior remain compatible.
- Do not add sandboxing, permission enforcement, publisher verification, dependency installation, or safety claims.

### R10 — Verification

- Tests cover tool provenance/order/collisions/scope/overwrite, Agent Plugin revisions/components/replacement, effective Skill precedence/revision, configured MCP ownership/status/redaction, registry actions, administrator authorization, warning cancellation/confirmation, reload outcomes, legacy-name collision, and compatibility callers.
- Run full Python/WebUI tests, strict BasedPyright, Ruff, ESLint, production build, and desktop/mobile warning-flow browser smoke.

## Acceptance Criteria

- [ ] AC1: The main live `ToolRegistry` reports exact successful built-in/entry-point provenance without another scan/import and without changing registration order or collisions.
- [ ] AC2: Later replacement/unregister removes stale core-tool ownership; MCP/image/dynamic tools are not duplicated by the core-tool adapter.
- [ ] AC3: Each Agent Plugin package has a safe current revision and exactly its valid Skill/MCP components, with truthful data/child-process execution and warning-only trust fields.
- [ ] AC4: Changed plugin content invalidates prior enablement and makes a previously displayed revision stale before mutation.
- [ ] AC5: Effective standalone Skill projection exactly matches existing workspace → enabled-plugin → built-in precedence and never duplicates Agent Plugin Skills.
- [ ] AC6: Every configured/effective MCP server appears once with correct transport trust/execution and runtime lifecycle, without secret-bearing fields.
- [ ] AC7: Agent Plugin enable/disable reaches only the canonical adapter; enable requires current revision and acknowledgement, disable does not, and each successful mutation requests one MCP reload.
- [ ] AC8: Reload unavailable/timeout/failure preserves the marker mutation while returning truthful restart/failure state; no duplicate connection owner or false enabled-live claim is introduced.
- [ ] AC9: Existing MCP Settings Agent Plugin rows are registry-derived and the old synthetic-name discovery/action branch is removed.
- [ ] AC10: Agent Plugin enable warning cancellation performs no mutation; confirmation submits only safe canonical metadata.
- [ ] AC11: Every global MCP mutation rejects ordinary authenticated non-admin users, while local owner and configured OIDC admins retain access with server-derived actor identity.
- [ ] AC12: A configured MCP server named `plugin-*` remains a configured server and cannot target an Agent Plugin action.
- [ ] AC13: Existing custom MCP, Agent Plugin Skills, tool entry points, MCP chat attachments, SDK/CLI/API/Gateway startup, and shutdown behavior remain compatible.
- [ ] AC14: Focused and full backend/frontend quality gates plus desktop/mobile browser smoke pass.

## Out of Scope

- Final unified Extensions Settings page and generic extension snapshot/action endpoints.
- Channels, providers, hooks, CLI-app, and optional-feature adapters.
- Subagent/ephemeral registry inventory.
- Sandbox, resource isolation, permission enforcement, signatures, marketplace/package installation, KMS, durable audit storage, billing, or quotas.

## Key Decisions

- Tool provenance is captured at the existing successful registration point and stored with the live ToolRegistry; adapters never rediscover tools.
- Agent Plugin marker state remains authoritative even when live MCP reload requires restart; the adapter reports failure instead of attempting an unreliable rollback.
- Effective Skills remain owned by `SkillsLoader`; registry projection does not replace prompt or filesystem authorization logic.
- The existing Apps/MCP page is a temporary compatibility presenter, not a second lifecycle owner.
- All global MCP mutations become system-admin-only in this child because they share the same host-wide configuration boundary.
