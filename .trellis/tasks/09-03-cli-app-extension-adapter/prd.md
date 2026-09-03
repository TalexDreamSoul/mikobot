# Installed CLI app extension adapter

## Goal

Give each installed CLI app exactly one canonical extension owner covering its executable and its generated Skill, resolve the current double ownership where every installed CLI app also appears as an Agent Plugin package, and migrate update, uninstall, and test to revision-bound canonical actions with bounded, redacted results.

## User Value

- An installed CLI app appears once, under one owner, instead of appearing both as a CLI app and as an enabled `cli-app-*` Agent Plugin that an operator could disable from a different page with no visible connection.
- CLI apps carry honest disclosure: operator-installed, child-process, unisolated executable code fetched from a package manager, with no verification claim.
- Update, uninstall, and test require system-admin identity and the exact current revision, so a package that changed between listing and action fails instead of acting on stale assumptions.
- Action results stop returning raw package-manager output and host filesystem paths to the browser.

## Confirmed Facts

- The enum members this child must produce have zero producers today: `ExtensionSource.CLI_APP` (`nanobot/extensions/contracts.py:75`) and `ExtensionComponentKind.CLI_APP` (`nanobot/extensions/contracts.py:90`) appear only in `contracts.py`, and no adapter under `nanobot/extensions/adapters/` emits them.
- The domain is well isolated. `CliAppManager` (`nanobot/apps/cli/service.py`) owns catalog fetch, durable installed state, package-manager argv, Skill generation, execution, and the update/uninstall/test transactions. The HTTP edge is `nanobot/webui/cli_apps_api.py`, where `cli_apps_payload` (`:106-123`) reads and `cli_apps_action` (`:126-144`) dispatches `install`, `update`, `uninstall`, and `test` by a single `name` query value, with no revision, no acknowledgement, and no admin fact at that layer.
- Double ownership is real and currently unavoidable. `CliAppManager.install_skill` writes a genuine Agent Plugin manifest — `plugin.json` carrying `$schema: AGENT_PLUGIN_SCHEMA` — into `plugins/cli-app-<name>/` (`nanobot/apps/cli/service.py:1137-1155`), and `_record_installed` then calls `set_agent_plugin_enabled(workspace, "cli-app-<name>", True)` (`nanobot/apps/cli/service.py:1165-1174`).
- `_installed_plugins` discovers exactly that layout under `<workspace>/plugins/*` and accepts any directory whose `plugin.json` matches the schema (`nanobot/agent/plugins.py:64-82`, `:188-213`). Consequently `AgentPluginExtensionAdapter` (`nanobot/extensions/adapters/agent_plugins.py:38`) already projects every installed CLI app as an enabled `ext:agent_plugin:cli-app-<name>` package declaring `ENABLE` and `DISABLE` (`nanobot/extensions/adapters/agent_plugins.py:97-113`).
- The generated Skill path is `plugins/cli-app-<name>/skills/cli-app-<name>/SKILL.md` with a legacy fallback at `skills/<legacy-name>/SKILL.md` (`nanobot/apps/cli/service.py:215-231`), and `remove_skill` deletes the whole plugin root (`nanobot/apps/cli/service.py:1157-1163`).
- Durable installed state is the correct inventory source and is separate from the catalog. `_load_installed` reads the persisted apps map (`nanobot/apps/cli/service.py:451-454`), `installed_payload` projects it (`:798-810`), while `_app_payload` mixes catalog candidates and installed apps into one list with a five-value `status` (`:670-707`).
- Durable state records an absolute host path: `_installed_entry` stores `entry_point_path` from `shutil.which` (`nanobot/apps/cli/service.py:1061-1063`), so any revision or descriptor derived naively from that entry would embed a host path.
- Restart honesty has a precedent in this subsystem: the local CLI management registry returns `requires_restart: True` rather than claiming it controlled a running process (`nanobot/extensions/management.py:45-48`).
- Output leaks sit directly on the action paths this child migrates. Install, update, and uninstall each raise `CliAppError(_truncate(result.stderr or result.stdout or ...), status=500)` (`nanobot/apps/cli/service.py:1212`, `:1241`, `:1269`), where `_truncate` bounds at `_MAX_TOOL_OUTPUT_CHARS = 12_000` (`:40`, `:404-408`) — up to twelve thousand characters of raw pip, brew, npm, or uv output in an HTTP error body.
- The uninstall success path returns an absolute host path inside a 200 response: `reason` interpolates `managed_entry_path` into `last_action.message` (`nanobot/apps/cli/service.py:1273-1281`).
- `test` resolves the entry point with `shutil.which` and returns up to 3 000 characters of the executable's own stdout or stderr in `last_action.output` (`nanobot/apps/cli/service.py:1320-1335`).
- The intermediate parent already fixes the ownership and scope rules this child implements: generated CLI-app Agent Plugin manifests must carry durable manager ownership metadata and the Agent Plugin adapter must exclude those roots (`.trellis/tasks/09-02-extension-runtime-adapters/prd.md:70`), and catalog-only install stays on the existing catalog workflow (`.trellis/tasks/09-02-extension-runtime-adapters/prd.md:69`).

## Requirements

### R1 — Installed-only canonical projection

- One `CLI_APP` package per app present in durable installed state, never per catalog candidate, and never dependent on a catalog fetch, cache freshness, or network refresh.
- Each package owns one `CLI_APP` component for the executable and one `SKILL` component for the generated Skill; the Skill component exists only when the generated Skill file is actually present.
- Package and component metadata is limited to safe display facts: app name, display name, category, description, recorded version, install source, package-manager strategy, entry-point command name, and whether the generated Skill is present.
- Snapshot construction performs no package-manager invocation, no catalog HTTP request, and no execution of the app's entry point.
- A malformed or unreadable installed entry yields one bounded package diagnostic and cannot suppress unrelated CLI-app packages.

### R2 — Single canonical owner for the generated Skill

- The generated Skill is a component of its CLI-app package and must not also appear as a standalone `AGENT_PLUGIN` package or a standalone Skill package.
- The generated Agent Plugin manifest carries durable ownership metadata identifying the CLI-app manager as its owner, written at generation time and readable without executing anything.
- The Agent Plugin adapter excludes manager-owned plugin roots from its own package projection based on that metadata, not on a name prefix match.
- A plugin directory that merely happens to be named `cli-app-*` but carries no ownership metadata remains an ordinary Agent Plugin and must not be adopted by the CLI-app adapter.
- `SkillsLoader` precedence, the enablement marker mechanism, the filesystem tool's plugin-Skill read authorization, and prompt assembly are unchanged; the exclusion is a control-plane projection rule only.
- Existing installations without ownership metadata are handled by one documented, tested rule that does not require an operator migration step and does not silently disable an installed app.

### R3 — Truthful trust, execution, and lifecycle

- CLI-app packages are operator-trusted, child-process execution, `isolated: false`, with declared permissions absent or explicitly self-declared and unenforced.
- Disclosure states that the app is third-party executable code installed from a package manager, that nanobot does not verify or sandbox it, and that it can access resources visible to the nanobot process.
- Lifecycle reflects durable installed state and entry-point availability: an installed app whose entry point is no longer resolvable is unavailable or failed, never silently enabled.
- The generated Skill component is data execution, not executable, even though its owning package is executable.
- Revisions derive only from safe structural facts — app name, recorded version, install source, strategy, and generated-Skill presence — and never from `entry_point_path`, resolved executable location, or any absolute path.

### R4 — Canonical installed lifecycle actions

- Update, uninstall, and test dispatch through `ExtensionRegistry.execute` to this adapter, which delegates to the existing `CliAppManager` transactions and creates no second installer, uninstaller, or executor.
- Every action requires server-derived system-admin identity, the exact current package revision, and — for update and test, which run external code — explicit risk acknowledgement; a stale revision fails before any package-manager or executable invocation.
- Catalog install remains on the existing catalog workflow and is not a canonical action in this child, because a not-yet-installed app has no installed package to target.
- Runtime execution of a CLI app remains exclusively `CliAppsTool`/`CliAppManager.run`; registry actions never execute arbitrary catalog commands.
- Successful actions report the resulting canonical state truthfully, including restart-required where the existing owner cannot affect a running process, following the precedent in `nanobot/extensions/management.py:45-48`.
- Uninstall that leaves the entry point resolvable reports a truthful partial outcome rather than claiming removal, and preserves the existing decision not to fabricate a rollback.

### R5 — Bounded, redacted action results

- Action results produced by this child's canonical path contain no raw package-manager stdout or stderr, no absolute host path, no resolved executable location, no argv or command line, and no unbounded text.
- Install, update, and uninstall failures return a bounded, attributable, safe reason; the full package-manager output remains in server logs under the existing logging policy.
- The uninstall partial-outcome message describes the condition without interpolating the recorded entry-point path.
- The test action reports the exit status and a bounded, redacted excerpt of the executable's output, or no excerpt at all, rather than up to three thousand unfiltered characters.
- These redaction requirements apply to the results this child produces and to the legacy edge that produces them for the same operations; broader redaction work in unrelated modules stays with its own owner.

### R6 — Compatibility

- Durable installed state format, generated Skill path and legacy fallback, `@name` mention resolution, CLI-app attachments in chat, `run_cli_app` behavior, and existing Apps Settings catalog browsing remain unchanged.
- No CLI app is installed, uninstalled, updated, enabled, or disabled as a side effect of adding the adapter or the ownership metadata.
- Existing Apps Settings continues to function throughout this child; removal of its duplicate lifecycle controls belongs to the cutover child.
- The Agent Plugin enablement marker for a CLI-app-generated plugin remains an implementation detail of the manager and is not exposed as a separate operator-facing lifecycle.
- No sandboxing, permission enforcement, publisher verification, signature checking, or safety claim is introduced.

### R7 — Verification

- Adapter tests cover installed-only projection with an empty and a stale catalog, component presence and absence for the generated Skill, revision stability and change, absence of any path-bearing field, unavailable entry point, and per-entry failure isolation.
- Ownership tests prove that an installed CLI app produces exactly one package across the composed registry, that the Agent Plugin adapter no longer emits a duplicate for manager-owned roots, that a metadata-less `cli-app-*` directory is still an ordinary Agent Plugin, and that the pre-existing-installation rule behaves as documented.
- Action tests cover non-admin rejection, stale revision, missing acknowledgement, unsupported action, delegation counts to `CliAppManager`, truthful restart-required results, and the uninstall partial outcome.
- Redaction tests assert that a failing install, update, uninstall, and test produce no raw package-manager output, no absolute path, and no unbounded message.
- Compatibility tests cover Skill precedence, `@name` mentions, chat attachments, and `run_cli_app` execution.
- Run focused tests, the full Python suite, Ruff, strict BasedPyright, the WebUI suite, and the production build, plus Apps Settings smoke for install, update, uninstall, and test.

## Acceptance Criteria

- [ ] AC1: Every app in durable installed state appears exactly once as a `CLI_APP` package owning its executable component and, when present, its generated Skill component (parent AC1; runtime-adapters AC8).
- [ ] AC2: An installed CLI app no longer produces a second `ext:agent_plugin:cli-app-*` package, while a `cli-app-*` directory without manager ownership metadata remains an ordinary Agent Plugin (parent AC1; runtime-adapters AC8).
- [ ] AC3: Skill precedence, plugin enablement markers, filesystem plugin-Skill authorization, and prompt assembly are unchanged by the exclusion rule (parent AC12).
- [ ] AC4: Catalog candidates, catalog cache state, and network refresh have no effect on the canonical installed inventory (runtime-adapters AC8).
- [ ] AC5: CLI-app packages report operator-trusted, child-process, `isolated: false` with self-declared unenforced permissions, and their generated Skill components report data execution (parent AC7).
- [ ] AC6: An installed app whose entry point is unresolvable reports unavailable or failed, never enabled (parent AC5).
- [ ] AC7: No descriptor, revision, diagnostic, or action result contains `entry_point_path`, a resolved executable location, any absolute host path, argv, or a raw command line (parent AC10; runtime-adapters AC5).
- [ ] AC8: Update, uninstall, and test dispatch only through the canonical adapter to `CliAppManager`, each existing owner is called at most once, and catalog install remains on its existing workflow (runtime-adapters AC9).
- [ ] AC9: Non-admin actor, stale revision, missing acknowledgement, unknown target, and unsupported action each fail before any package-manager or executable invocation (parent AC5, AC9; runtime-adapters AC10).
- [ ] AC10: A failing install, update, uninstall, or test returns a bounded safe reason with no raw package-manager output, and the full output is present only in server logs (parent AC10).
- [ ] AC11: Uninstall that leaves the entry point resolvable reports a truthful partial outcome without interpolating a host path and without fabricating a rollback (parent AC6, AC10).
- [ ] AC12: Test reports exit status with a bounded redacted excerpt or no excerpt, never unfiltered executable output (parent AC10).
- [ ] AC13: One malformed installed entry yields a bounded diagnostic and suppresses no unrelated CLI-app package (parent AC3; runtime-adapters AC12).
- [ ] AC14: Installed state format, generated Skill paths, `@name` mentions, chat attachments, `run_cli_app`, and Apps Settings catalog browsing are unchanged, and no app changed state as a side effect (parent AC12; runtime-adapters AC11).
- [ ] AC15: Focused and full Python tests, Ruff, strict BasedPyright, WebUI tests, the production build, and Apps Settings smoke pass (parent AC12; runtime-adapters AC13).

## Out of Scope

- Catalog install as a canonical registry action, marketplace or search over uninstalled apps, arbitrary remote package download, publisher verification, signatures, and automatic upgrades.
- Changing `run_cli_app`, the CLI-apps tool's own execution output handling, catalog fetch and caching, or the `@name` mention mechanism.
- Redaction work in modules unrelated to the CLI-app action paths this child migrates.
- Removing duplicate lifecycle controls from Apps Settings, and deleting compatibility presenters; both belong to the cutover child.
- Provider, hook, channel, optional-feature, Agent Plugin, Skill, MCP, and tool adapters, and the unified Extensions page.
- Sandboxing, resource isolation, permission enforcement, KMS, durable audit storage, billing, and quotas.

## Key Decisions

- The CLI-app package is the canonical owner of the generated Skill; the Agent Plugin marker underneath remains a manager implementation detail so that Skill loading, precedence, and authorization stay exactly as they are.
- Exclusion is driven by durable ownership metadata rather than a `cli-app-*` name prefix, because a name prefix is operator-writable and would let an ordinary Agent Plugin be silently adopted or hidden.
- Inventory comes from durable installed state, not the catalog, so a cold cache or an offline host does not make installed apps disappear from the extension tree.
- Catalog install stays outside canonical actions because the fixed registry contract targets installed packages and a not-yet-installed candidate has no package to bind a revision to.
- The raw package-manager output and host-path leaks are treated as in-scope here rather than deferred, because these are precisely the responses this child converts into canonical action results, and shipping them through the new path would violate the parent's safe-projection requirement on day one.
