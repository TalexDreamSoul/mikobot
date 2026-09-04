# Extension cutover hardening

## Goal

Delete every temporary compatibility presenter and duplicate lifecycle control that the adapter-first migration deliberately left behind, so the canonical registry is the single inventory and control path, then verify cross-family identity, authorization, redaction, failure isolation, hot-reload/restart truth, and desktop/mobile behavior end to end.

## User Value

- One extension identity and one lifecycle vocabulary survive; operators stop seeing the same package described two different ways by two different pages.
- Removing the lossy legacy status projection means `changed`, `restart_required`, `unavailable`, `disabled`, and `disabling` stop collapsing into a coarser four-value string.
- A single mutation path per family removes the class of bug where one page's action succeeds and another page's cached state disagrees.
- The migration ends with a diff that contains no permanent shim, matching the project constraint that extensions live at the edges without a second framework.

## Confirmed Facts

- `nanobot/webui/nanobot_features_api.py` is already a pure compatibility presenter over the canonical snapshot: its module docstring states so (`nanobot/webui/nanobot_features_api.py:1`), and `nanobot_features_payload` reads only `extension_snapshot.packages` (`nanobot/webui/nanobot_features_api.py:116-158`) before re-rendering canonical descriptors into the legacy `features[]` shape through `_feature_base` (`:230`), `_channel_lifecycle_fields` (`:253`), `_channel_status` (`:272`), `_legacy_status` (`:287`), `_action_target_fields` (`:297`), and `_decorate_channel_instances` (`:305`).
- `_legacy_status` (`nanobot/webui/nanobot_features_api.py:287-294`) maps the ten canonical lifecycle values onto four strings (`enabled`, `starting`, `failed`, `stopped`), discarding `changed`, `restart_required`, `unavailable`, `disabled`, and `disabling`.
- `nanobot/webui/mcp_presets_api.py` injects Agent Plugin rows into the MCP preset catalog in two places: `_with_agent_plugin_rows` (`nanobot/webui/mcp_presets_api.py:961-991`) and an inline projection inside `mcp_presets_payload` (`nanobot/webui/mcp_presets_api.py:1009-1017`). The action path calls `_with_agent_plugin_rows` again at `nanobot/webui/mcp_presets_api.py:1775` and `:1803`, and `_agent_plugin_payload` (`:924-959`) is the row builder.
- Both presenters are explicitly sanctioned as temporary, not permanent: the core-adapters child declares "The existing Apps/MCP page is a temporary compatibility presenter, not a second lifecycle owner" (`.trellis/tasks/09-02-extension-core-adapters/prd.md:130`), and the channel/optional child requires `/api/settings/nanobot-features` to "temporarily render canonical channel/optional descriptors into current payload shapes" (`.trellis/tasks/09-03-channel-optional-extension-adapters/prd.md:65`).
- The parent forbids retaining them: R8 requires clean cutover with no permanent compatibility shim (`.trellis/tasks/09-02-unified-extension-platform/prd.md:87`), and the review gate requires the final diff to contain no permanent compatibility alias or dual mutation path (`.trellis/tasks/09-02-unified-extension-platform/implement.md:121`).
- Earlier cutovers already landed and leave no residue to re-remove: `discover_agent_plugins` now has exactly one production caller, the canonical adapter (`nanobot/extensions/adapters/agent_plugins.py:38`), and neither `discover_all` nor `channel_feature_action` has any remaining reference under `nanobot/`.
- Generic optional-feature mechanics are domain logic that must survive: `install_extra` (`nanobot/optional_features.py:216`), `prepare_channel_dependencies` (`:276`), `ensure_enabled_channel_dependencies` (`:307`), and `optional_feature_requires_restart` (`:336`) are dependency machinery, not duplicate inventory.
- The client contract is duplicated across two shapes: `NanobotExtensionDescriptor` mixed into `NanobotFeatureInfo` uses closed union types (`webui/src/lib/types.ts:1290-1308`), while the MCP preset row redeclares the same field names as loose `string` (`webui/src/lib/types.ts:1463-1469`).
- Channel-owned WebUI modules are explicitly retained by the channel child's key decision (`.trellis/tasks/09-03-channel-optional-extension-adapters/prd.md:116`); Feishu and Weixin panels and generic channel forms are not cutover targets.
- Existing WS routes that carry these payloads are `settings.feature.enable`/`disable`, `settings.channel.*`, `settings.cli_app.*`, and `settings.mcp.*` (`nanobot/webui/ws_http.py:237-258`), each mapped to an HTTP path in `nanobot/webui/settings_routes.py:98-142`.

### Measured dead vocabulary (2026-09-04)

The architecture audit counted 11 of 46 enum members with no producer anywhere in
`nanobot/`, as evidence that the contract was designed for nine families while only some
were wired. After the provider, hook, and channel adapters landed, 5 remain:

| Member | Expected resolution |
|---|---|
| `ExtensionSource.CLI_APP` | `09-03-cli-app-extension-adapter` |
| `ExtensionComponentKind.CLI_APP` | same |
| `ExtensionAction.UNINSTALL` | same — uninstalling an installed CLI app |
| `ExtensionLifecycle.DISABLING` | **no adapter emits it.** Delete unless a producer appears. |
| `ExtensionLifecycle.CHANGED` | **no adapter emits it.** Same. |

The first three are expected to gain producers. The last two are transient states nothing
reports, and this task should delete them rather than leave vocabulary that reads as
supported. Re-run the count before closing; a member that is still unused at cutover is
not "reserved for later", it is a claim the contract cannot back.

## Requirements

### R1 — Delete the compatibility presenters

- Remove the canonical-to-legacy projection in `nanobot/webui/nanobot_features_api.py`, including the legacy `features[]` shape, `_legacy_status`, `_channel_status`, `_channel_lifecycle_fields`, `_feature_base`, `_action_target_fields`, and `_decorate_channel_instances`, once every caller consumes canonical descriptors.
- Remove the Agent Plugin row injection from `nanobot/webui/mcp_presets_api.py`, including `_agent_plugin_payload`, `_with_agent_plugin_rows`, the inline plugin projection in `mcp_presets_payload`, and the `installed_count` adjustment that compensates for injected rows.
- Retain the domain payloads these modules legitimately own: configured and preset MCP server rows, MCP runtime status overlay, MCP hot-reload result attachment, and channel setup contracts.
- Delete the corresponding legacy client types and adapters rather than leaving unused optional fields on `NanobotFeatureInfo` and the MCP preset row.
- Any route or mutation name that exists only to carry a deleted projection is removed together with its client caller; no route is left accepting requests that no client sends.

### R2 — Remove migrated duplicate lifecycle controls

- Every lifecycle control whose action now dispatches through `ExtensionRegistry.execute` is removed from its family page and replaced, where useful, by a link to the owning extension package.
- Family pages retain configuration, catalog, credential, connector, pairing, and OAuth workflows, which are domain concerns and not extension lifecycle.
- Channel-owned Vite modules, Feishu and Weixin custom panels, generic channel setup forms, translations, and instance UX are preserved unchanged.
- After removal, each family has exactly one code path that mutates its lifecycle; a grep-level check for a second mutation entry point is part of the deliverable.

### R3 — One lifecycle vocabulary on the wire

- The four-value legacy status vocabulary is deleted rather than extended; consumers read canonical lifecycle values.
- The duplicated client extension-field declarations collapse into one shared TypeScript contract with closed union types; the loose `string` variant is removed.
- Any UI that previously inferred state from the legacy vocabulary is updated to read the canonical value, including the previously unrepresentable `changed`, `restart_required`, `unavailable`, `disabled`, and `disabling` states.
- No client-side mapping table reintroduces a second vocabulary.

### R4 — Cross-family identity and ownership verification

- Verify that every in-scope family produces stable, namespaced, collision-free package and component IDs and that no package appears under two owners after all adapters are composed together.
- Verify that families whose members share a name across registries or directories resolve to exactly one canonical owner, and that the documented ownership rule is the one actually implemented.
- Verify that registration, reload, and unregistration are idempotent and leave no duplicate tool, hook, MCP capability, channel instance, provider, CLI app, or optional-feature row.
- Verify ID stability across a process restart for unchanged installed extensions.

### R5 — Authorization and redaction sweep

- Every global extension mutation across every migrated family rejects ordinary authenticated non-admin actors before any config, package-manager, connector, credential, or runtime side effect, using server-derived identity only.
- Every snapshot, diagnostic, compatibility payload that survives, and action result is asserted free of secrets, environment values, absolute host paths, raw command lines, raw signatures, and unbounded exception text.
- Failures remain bounded, attributable to one owner, and never convert into a false enabled state.
- Redaction findings that belong to a family's own domain code are fixed in that family's task; this task verifies the control-plane surface and fixes only leaks introduced or exposed by the cutover itself.

### R6 — Lifecycle truth and failure isolation verification

- Verify Agent Plugin and MCP actions hot reload, that Python entry-point tools and startup-bound hooks report restart-required, and that channel and provider actions preserve their existing transactional behavior.
- Verify that a marker or configuration mutation that succeeds while its reload fails, times out, or is unavailable reports the truthful restart or failure result and never claims live success.
- Verify that a raising, malformed, or colliding adapter rejects only its own snapshot, emits one bounded diagnostic, and leaves every unrelated package present and actionable.
- Verify that stale revisions and missing acknowledgements fail before the owning adapter is reached.

### R7 — No permanent shim gate

- The final diff contains no permanent compatibility alias, no dual mutation path, and no route retained solely for a removed client.
- Canonical extension IDs do not replace session, bot, channel-instance, provider-config, or MCP tool names; those remain domain identities.
- No extension is installed, enabled, disabled, or granted a capability as a side effect of this cutover.
- Existing config JSON shape, Agent Plugin package format and activation marker, channel credentials and instances, provider selection, Skill precedence, and MCP names remain unchanged.

### R8 — Verification

- Run the full Python suite, Ruff, strict BasedPyright, the full WebUI suite, ESLint, and the production build.
- Add or retain regression tests proving each deleted projection has no remaining caller and each family has exactly one mutation path.
- Browser smoke on desktop and mobile covers the unified Extensions surface for every family, the family pages after control removal, an executable warning cancel and confirm, a stale-revision conflict, an Agent Plugin hot enable and disable, a restart-required row, configuration deep links, and `zh-CN` with no English literal on migrated surfaces.
- Exercise SDK, CLI, API, and Gateway startup and shutdown to confirm compatibility after deletion.

## Acceptance Criteria

- [x] AC1: `nanobot/webui/nanobot_features_api.py` no longer contains a canonical-to-legacy projection, and no caller depends on the legacy `features[]` shape (parent AC11, AC12).
- [x] AC2: `nanobot/webui/mcp_presets_api.py` no longer injects Agent Plugin rows in either the payload path or the action path, and `installed_count` reflects only configured MCP servers (parent AC11).
- [x] AC3: The four-value legacy status vocabulary and the duplicated loose client extension-field declaration are deleted; consumers read one closed canonical contract (parent AC11).
- [x] AC4: Each migrated family exposes exactly one lifecycle mutation path, and every duplicate control has been removed from its family page while configuration, catalog, connector, pairing, and OAuth workflows still function (parent AC11).
- [x] AC5: Channel-owned WebUI modules, Feishu and Weixin panels, generic channel forms, and their translations remain intact and passing (parent AC12).
- [x] AC6: One composed snapshot across every family contains no duplicate or cross-owned package or component ID, and IDs are stable across restart for unchanged extensions (parent AC1, AC4).
- [x] AC7: Registration, reload, and unregistration are idempotent with no duplicate rows in any family (parent AC4).
- [x] AC8: Every global extension mutation rejects non-admin actors before side effects, and missing identity fails closed (parent AC9).
- [x] AC9: No surviving payload, diagnostic, or action result exposes a secret, environment value, absolute host path, raw command line, or unbounded traceback (parent AC10).
- [x] AC10: Hot-reload families hot reload, restart-bound families report restart-required, and a failed reload after a successful authoritative mutation reports the truthful state rather than success (parent AC6).
- [x] AC11: A failing adapter rejects only its own snapshot with one bounded diagnostic while unrelated packages stay listed and actionable (parent AC3).
- [x] AC12: The final diff contains no permanent compatibility alias, dual mutation path, or orphaned route, and no extension changed state as a result of the cutover (parent AC12).
- [x] AC13: Existing Agent Plugins, custom MCP servers, tool entry points, channel manifests and configuration, provider selection, hooks, Skill precedence, CLI apps, and optional features preserve observable runtime behavior (parent AC12).
- [x] AC14: Full backend and frontend suites, Ruff, strict BasedPyright, ESLint, production build, and desktop and mobile browser smoke pass (parent AC12).

## Verification status (2026-09-04)

### R2 landed as one dispatcher, not one route

The requirement asked that each family keep exactly one lifecycle mutation path, and the
plan was to reroute the family switches through the extension action. That is not a call
-site swap: the two routes gate differently and the difference is deliberate.
`ws_http.py` raises only `system_admin` for a member acting on a channel instance they
claimed, never `host_admin`, so rerouting would refuse that member, and widening the host
route to `system_admin` would let anyone who claimed one instance act on every target.
The second cannot be re-scoped from the target id, because `canonical_extension_name`
hashes non-conforming names one way, so the raw channel and instance a per-instance check
needs are unrecoverable.

Both entries already funnel into `ExtensionRegistry.execute`, which is the sole validator
and sole adapter dispatcher. The genuine duplicate was a third path — Agent Plugin
dispatching through the MCP preset route — and that is deleted. Acceptance is therefore
"one dispatcher with declared surfaces", pinned by an AST guard over the four production
construction sites, rather than "one route".

### The dead-vocabulary table in this PRD was stale, and is now a property

It listed five members. By the time this task ran, three had gained producers from the
CLI app adapter, so only two were dead. Counting by hand produced a number that aged
badly within a day. `test_every_contract_vocabulary_member_has_a_producer` asserts the
property across all six enums instead, so nobody re-counts. Adding a producer-less member
reddens it — verified by injecting one.

### The legacy vocabulary was hiding a live defect

It mapped `ENABLED` to `"enabled"` while every consumer compares
`runtime_status === "running"`, so a running channel instance rendered as off. Both sides
were green because each asserted against its own vocabulary. The replacement parses the
TypeScript union and compares it against the Python output pointwise over all eight
lifecycles; set equality alone would still have passed if two states swapped, which is
the exact shape the defect had.

### Two failures escaped the scoped verification

The implementer was told to run `tests/webui/ tests/extensions/`, which is not what CI
runs: `testpaths` also covers `nanobot/channels`, where two assertions still read the
deleted `status` key. This is the second time in this effort that scoping a test run
below `testpaths` hid a real failure. Both were fixed and now read the canonical
lifecycle.

### A mutation check can report a false result in either direction

Restoring a mutant whose file size is unchanged can leave stale bytecode in
`__pycache__`, so the suite runs the mutant while the source reads correct. Separately, a
mutation that fails to apply reads as "the test did not catch it". Both occurred during
this task. A mutation result is only evidence once the mutant is shown to be live and the
caches are cleared.

## Out of Scope

- Adding new adapters, new families, new endpoints, or new UI surfaces; this child only deletes and verifies.
- Rewriting channel-owned WebUI modules or replacing family configuration workflows.
- Changing any family's runtime owner, process ownership, config schema, credential format, Agent Plugin package layout, or activation-marker format.
- Broad refactors of the domain modules that merely happen to sit next to a deleted projection.
- Durable audit storage, telemetry, persistent caching, marketplace, package download, publisher verification, signatures, KMS, billing, and quotas.
- Sandboxing, resource isolation, permission enforcement, or any third-party extension safety guarantee.

## Key Decisions

- The presenters in `nanobot/webui/nanobot_features_api.py` and `nanobot/webui/mcp_presets_api.py` are correct today and prohibited tomorrow; this child is the point where that changes, so the deletion is scoped here rather than distributed across earlier children.
- Deletion happens only after the unified Extensions surface is live, so no family loses its control before a replacement exists.
- The lossy legacy status vocabulary is deleted rather than widened, because widening it would create the second vocabulary the parent forbids.
- Domain dependency machinery in `nanobot/optional_features.py` is not duplicate inventory and stays where it is.
- Redaction defects rooted in a family's own domain code stay with that family's task; this child owns only the control-plane surface it is deleting through.
