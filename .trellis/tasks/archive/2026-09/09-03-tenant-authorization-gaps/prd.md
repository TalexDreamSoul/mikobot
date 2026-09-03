# Tenant authorization gaps

## Goal

Close the cross-tenant authorization gaps that the archived `09-01-bot-channel-management` task left open when it turned single-owner channels into multi-tenant, per-instance owned resources, so that ordinary members can no longer read or mutate state belonging to another tenant's bot, channel instance, or host configuration.

## User Value

- A member of one organization cannot approve an external DM sender onto another organization's bot.
- An ordinary member cannot write attacker-controlled credentials into host-wide configuration that every tenant then routes through.
- Uploads sent to one tenant's channel instance cannot overwrite or shadow another tenant's media of the same name.
- Every privileged Settings route fails closed by default, so adding a route cannot silently ship without authorization.

## Confirmed Facts

- `09-01-bot-channel-management` is archived with `status: completed` and `completedAt: 2026-09-02`, but three of its authorization invariants are not enforced in the shipped code. The findings below were reproduced by running the code, not inferred.
- `nanobot/pairing/` is the **DM sender approval** store, a separate mechanism from the R5 Pair Code that claims a channel instance for a bot (`consume_pairing_challenge`). Its store keys are per runtime instance (`weixin.tenant-b`), because `nanobot/channels/manager.py:218` sets `channel.name` to the runtime name — so the **storage layer already isolates tenants correctly**.
- `nanobot/pairing/store.py:139` `approve_code(code)` takes no scope argument, and `list_pending()` (`:189`) returns every non-expired request across all instances. Neither is wrong on its own: they are the pre-multi-tenant contract.
- `nanobot/webui/settings_system.py:781` `_pairing_action` and the `pairing-list` dispatch branch at `:319` perform **no authorization check at all**. The sibling `_mcp_presets` handler in the same file (`:835-843`) does perform one, so the omission is local, not architectural.
- Reproduced: an authenticated non-admin passes `check_api_token`, `_is_system_admin` (`nanobot/webui/ws_http.py:630`) returns `False`, `GET /api/settings/pairing` returns `200` with every tenant's pending `code`/`channel`/`sender_id`, and `settings.pairing.approve` with that code returns `200` and flips `is_approved("weixin.<other tenant>", "<attacker sender>")` to `True`.
- `nanobot/webui/settings_capabilities.py` `CapabilitySettingsHandler.handle` checks `system_admin` only for `api-start` (`:731`) and `api-stop` (`:818`). `api-update`, `web-search-update`, `transcription-update`, `network-update`, and `image-update` reach `self.settings.mutate(...)` with no check.
- Reproduced: a non-admin `settings.web_search.update` with `{"provider": "tavily", "api_key": "attacker-supplied-key"}` returns `200` and both values are persisted to `config.json`. `network-safety/update` also returns `200` and can widen `webui_default_access_mode` to `full` via `nanobot/webui/settings_api.py:396-400`.
- `nanobot/webui/settings_system.py:359` `_cli_apps_action` has the same omission: a non-admin `cli-install` returns `404 "not found"` rather than `403`, which shows authorization already passed.
- `nanobot/channels/feishu/runtime.py:1851` and `nanobot/channels/weixin/runtime.py:1555` call `get_media_dir("feishu")` / `get_media_dir("weixin")` with the hardcoded base channel type, not the per-instance runtime name. `_safe_media_filename` (`feishu/runtime.py:1829-1839`) blocks traversal but preserves the sender-supplied basename verbatim.
- Weixin **credential and state** isolation is correct and must not be disturbed: `nanobot/channels/weixin/instances.py:95-105` folds the instance id into `stateDir`, `:160` rejects duplicate state directories, and `:169-177` rejects instances sharing credentials. Only the media directory is shared.
- `nanobot/webui/settings_routes.py:145` `_SETTINGS_MUTATION_PATHS` is **not** an authorization set; it is consumed at `:322` to decide `needs_local_browser` (`:288`). It must not be repurposed as the privileged-route list without separating the two concerns.
- `nanobot/webui/ws_http.py:991-1000` deliberately grants a non-admin elevated rights for self-service connect (`poll`/`cancel`, and `start` with `mode=create`), bounded by the connector's own `_require_session_actor` (`weixin/connect.py:173`) and `_can_manage_channel_instance`. This is intended product behavior and is out of scope here.

## Requirements

### R1 — Tenant-scoped DM sender approval

- `pairing-list`, `pairing-approve`, and `pairing-deny` must reject callers who are not a system administrator with a stable, non-disclosing `403`.
- `pairing-list` must be gated in the same change as approve/deny: it is what hands an attacker the code that approve consumes.
- Rejection must not reveal whether a code exists, which instance it belongs to, or how many are pending.
- The pre-existing local-owner (no OIDC configured) path must keep working unchanged; this task adds no new obstacle for single-user deployments.

### R2 — Privileged capability mutations fail closed

- `api-update`, `web-search-update`, `transcription-update`, `network-update`, `image-update`, and the `cli-*` install/uninstall/update actions must require a system administrator.
- Read-only capability projections keep their current audience; this task does not narrow reads it has not shown to be sensitive.
- Unauthorized privileged actions must return `403`, never a `404` that leaks whether a target exists.

### R3 — Authorization that a new route inherits by default

- Privileged Settings routes must be enumerated in one place that a reviewer can read, rather than depending on each handler remembering its own check.
- The chosen mechanism must not repurpose `_SETTINGS_MUTATION_PATHS`, whose meaning is local-browser scoping.
- It must not break the deliberate self-service connect elevation at `ws_http.py:991-1000`.
- If a central gate would be riskier than per-handler checks, the per-handler route is acceptable, but every privileged action must be covered and the decision recorded.
- Recorded outcome: per-handler gates were chosen, because a central privileged-route set is still a blocklist — a new route omitted from it ships unprotected. Each domain is instead fail-closed internally: `_PUBLIC_CAPABILITY_ACTIONS` and `_PUBLIC_MODEL_ACTIONS` are whitelists, and the system domain uses prefix predicates rather than per-action enumeration.
- Reviewability is carried by a canary test rather than a constant: `test_no_settings_route_reaches_a_member_without_a_recorded_decision` walks `_MODEL_ROUTES`, `_CAPABILITY_ROUTES`, and `_SYSTEM_ROUTES` and fails unless each route either refuses a member or is declared member-reachable / enforced earlier in the stack. Measured today: 33 refused, 6 member-reachable, 4 enforced in `ws_http`.
- Open defence-in-depth gap: `/api/settings/channels/validate` and `/api/settings/channels/configure` return `200`/`400` for a member at the dispatch layer and are protected only by `ws_http.py` `channel_control_path`. That is the same shape as the test that reached `settings_routes.dispatch` directly and skipped identity derivation. Any second caller of `dispatch` would bypass them.


### R4 — Per-instance media isolation

- Media written on behalf of a channel instance must live under a directory derived from that instance's runtime name, not the base channel type.
- Stored filenames must not be fully predictable from a sender-supplied basename, so that two tenants cannot collide on a common name.
- Existing media written under the current shared layout must remain readable, or the migration must be explicit; silently orphaning prior attachments is not acceptable.
- Credential and state isolation for Weixin and Feishu instances must be left exactly as it is.

### R5 — Regression coverage that proves the break is closed

- Each defect gets a test that exercises the attack path and asserts the non-admin is refused, plus a companion test that the administrator path still succeeds.
- Tests assert the observable outcome (approval not recorded, config unchanged, file not overwritten), not merely that a guard function was called.
- Coverage includes the local-owner deployment shape so the fix does not regress single-user installs.

### R6 — Model and provider routes: decide, then enforce

- `nanobot/webui/settings_models.py` contained **zero** `system_admin` references, yet `provider-update` and `model-configurations/{create,update,delete,migrate}` reach `self.settings.mutate(...)`, which writes the single host-wide `config.json` (`nanobot/webui/settings_services.py:153-167`). Provider settings include API keys — the file has redaction helpers for reading them (`:142-170`) but had no gate on writing them.
- The product question was whether member model selection travels through these routes. It does not: there is no per-user or per-bot model configuration in `nanobot/collaboration/`, no session-level model switch in `webui/src/lib/api.ts`, and every `model-*`/`provider-*` client call is a settings mutation.
- **Decision: gate the domain.** A local owner resolves as an administrator (`nanobot/webui/ws_http.py:616-625` returns `local_owner=True` when OIDC is disabled), so single-user installs are unaffected; the gate only takes effect in the OIDC multi-tenant shape, which is the threat model. This matches the posture already applied to the capability routes.
- Enforcement is a fail-closed whitelist (`_PUBLIC_MODEL_ACTIONS`), so a new model action is protected by default. Only `provider-models`, a read that lists model names for the picker, stays open.
- The WebUI does not filter Settings sections by role (`SettingsPage.tsx` and `webui/src/lib/types.ts` contain no `isSystemAdmin`). The backend is the sole enforcement point, and a follow-up should hide or disable the sections a member cannot use rather than letting them fail at submit.



### R7 — No host inventory for callers without host administration

- `/api/settings/nanobot-features` must not disclose package names, extension IDs, revisions, lifecycles, trust, execution location, adapter diagnostics, `runtime_error`, or counts to a caller who is not a system administrator.
- The inventory is withheld whole rather than sampled. nanobot has no per-member extension visibility model — `ExtensionProfile` (`nanobot/collaboration/models.py:337`) and `BotCapabilityProfile` are opaque settings mappings, not capability inventories — so any partial projection would be a guess about what a member "should" see.
- The refusal must not be a bare `403`. Settings sections are rendered for every member (`webui/src/components/settings/SettingsPage.tsx` and `webui/src/lib/types.ts` contain no `isSystemAdmin`), so an error status breaks page rendering. Return the payload shape with an explicit restricted marker so the UI states that administration is required instead of claiming nothing is installed.
- When a per-member visibility model exists, this requirement is what it replaces; until then, withholding is the honest answer.

**Correction, recorded after archiving.** AC11 was verified against the direct read only,
and the direct read was not the whole surface. Every response that ships a
`nanobot_features` block — feature enable/disable, channel configure, and the connect
pairing branch — rebuilt the full inventory from the registry with no caller argument, so
a member elevated by `ws_http` for a channel instance they own still received it. The
audience was narrower than before the task, not closed. `SettingsRequest` now carries
`host_admin`, which the transport records before elevation overwrites `system_admin`, and
the inventory is built in exactly one place that gates on it. Recording the miss rather
than the corrected end state, because the lesson is that "verified" meant one entry point
when the requirement covered a payload.

### R8 — One credential redaction implementation

- Error text reaching an API payload passes through one shared redaction implementation. Two copies drift, and a drift means one surface masks while the other prints — with both surfaces' own tests still green.
- Redaction runs before truncation. Truncating first can cut a credential mid-value and ship its head.
- Unbounded third-party exception text is length-capped at the boundary that emits it.
- Redaction must not damage legible diagnostics. `token expired` and `Secret Santa Bot` stay intact; host paths in an administrator-only MCP diagnostic stay intact because they are the useful part and the reader can already read the config file.

## Acceptance Criteria

- [x] AC1: An authenticated non-administrator receives `403` from `pairing-list`, `pairing-approve`, and `pairing-deny`, and no approval is recorded in the pairing store.
- [x] AC2: A system administrator, and a local owner in a deployment with no OIDC configured, can still list, approve, and deny pairing requests.
- [x] AC3: A non-administrator `web-search-update` carrying an `api_key` returns `403` and `config.json` is byte-identical afterwards.
- [x] AC4: `api-update`, `transcription-update`, `network-update`, `image-update`, and `cli-install`/`cli-uninstall` reject non-administrators with `403` rather than `404`.
- [x] AC5: The set of privileged Settings routes is readable in one place, and a test fails if a privileged action is added without authorization.
- [x] AC6: Two instances of the same channel type receiving an upload with an identical filename produce two distinct stored files, and neither tenant can read or overwrite the other's.
- [x] AC7: Weixin and Feishu per-instance credential and state isolation is unchanged, proven by the existing instance tests continuing to pass untouched.
- [x] AC8: The deliberate self-service connect elevation still works for a non-administrator claiming their own instance.
- [x] AC9: Full `pytest` (both `testpaths`), `ruff`, `basedpyright`, and WebUI tests pass.
- [x] AC10: The model/provider routes have an explicit, recorded authorization posture — either gated, or split so that member model selection and host-wide credential writes are different routes with different requirements. An undecided route is not acceptable as a closing state.
- [x] AC11: A non-administrator reading `/api/settings/nanobot-features` receives no package name, extension ID, revision, lifecycle, trust, execution location, diagnostic, or count, and the Settings UI renders an administrator-required state rather than erroring or claiming nothing is installed.
- [x] AC12: One redaction implementation serves every API error path; `AWS_SECRET_ACCESS_KEY=…`, `sk-…`, and `password: …` are masked, a credential at the truncation boundary cannot ship its head, and `token expired` / `Secret Santa Bot` are byte-identical.

## Out of Scope

- Sandboxing or otherwise constraining third-party extension code; the unified extension platform task already declares this out of scope and this task does not reopen it.
- Reworking the Pair Code channel-claim flow (`consume_pairing_challenge`), which is a different mechanism and is not implicated in these findings.
- Changing the extension revision digest. That belongs with the unified extension platform.
- Durable audit storage for authorization decisions.
- Adding a postgres service to CI so the skipped RLS suite runs. Worth doing, but it is a separate infrastructure change.

### Scope pulled in after the task was written

`/api/settings/nanobot-features` read scope was originally deferred to the unified extension platform. It was pulled in here instead: it is the same tenant-isolation defect as the rest of this task, the extension platform task is not scheduled, and leaving a member-readable host inventory in place while closing every other member-readable surface would have been an arbitrary line. See R7.

## Key Decisions

- These are treated as unmet acceptance work from `09-01-bot-channel-management`, not as new features. `09-01` is left archived; this task carries the remediation and cites the original invariants.
- Attribution is deliberately precise. The capability-route gap directly contradicts `09-01` R8 ("Only the local owner or an OIDC subject explicitly listed as a system admin may read or mutate global login/channel configuration") and AC8. The media-directory gap sits against R4's per-instance isolation intent. The DM-approval gap is **not** named by any single `09-01` acceptance criterion — `nanobot/pairing/` predates that task — but `09-01` R4 is what made channel instances per-tenant property, which is exactly what turned a previously safe global approval list into a cross-tenant break. It is remediation of a gap that task created, and is recorded as such rather than being forced onto an AC that does not fit.
- The storage layers are already correct in all three cases. Every fix belongs at the authorization or path-derivation boundary; none of them should change `nanobot/pairing/store.py`, the collaboration store, or the channel instance contracts.

## Risks and Operational Constraints

- Gating `pairing-list` changes behavior for any deployment where a non-admin operator relied on the pairing screen. That reliance was the vulnerability, but it should be called out in release notes.
- A central authorization gate touches every Settings route at once. It must be introduced with the self-service connect path explicitly excluded and tested, or it will break user onboarding.
- Changing the media directory layout affects paths already handed to the agent via `msg.media`. Existing files must stay reachable or the change must be explicit.
